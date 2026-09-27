"""The coordination kernel: one writer, one converge step.

Everything that changes session state happens inside Kernel.handle(), one
event at a time. Workers (ASR, OCR, LLM) and timers never touch state; they
post events, which are handled in order. WriteGuard enforces this.

After every event the kernel *converges*: from the current goal and slot
values it computes the calls the goal needs right now, then

  1. cancels every active call whose idempotency key is no longer needed
     (the reason names the slot versions it read that have since changed),
  2. reuses every call whose key is still needed (in flight or succeeded),
  3. creates the missing ones: read-only calls go out at once
     (speculatively), state-changing calls wait behind the commit fence,
  4. and when every step has succeeded, emits one final response with a
     complete state snapshot.

No model call sits on this path; it is pure bookkeeping, so invalidation
happens in the same tick as the event that caused it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Protocol, Union

from pydantic import JsonValue

from keel.compiler.manifest import ManifestCompiler, ToolPolicy, default_compiler
from keel.config import KeelConfig
from keel.kernel.clock import Clock, TimerHandle
from keel.kernel.fence import CommitFence
from keel.kernel.guard import WriteGuard
from keel.kernel.internal import Interpretation, InternalEvent, SlotProposal, TimerFired
from keel.kernel.ledger import ACTIVE, CallEntry, Ledger, SafetyClass
from keel.kernel.plan import Const, Goal, ResultRef, SlotRef, Step, dig, result_key
from keel.kernel.reconcile import CannotConfirm, Probe, Retry, StatusProbe, plan_reconciliation
from keel.kernel.repair import editing_only
from keel.kernel.slots import Rejection, SlotChange, SlotStore
from keel.paths.fast import humanize, render
from keel.protocol.ids import IdFactory
from keel.protocol.provisional import (
    ActionT,
    AudioClip,
    Cancel,
    Clarify,
    EventT,
    FinalResponse,
    Interrupt,
    Speak,
    StateSnapshot,
    TextChunk,
    ToolCall,
    ToolManifest,
    ToolResult,
    VideoFrame,
)
from keel.trace import TraceWriter

AnyEvent = Union[EventT, InternalEvent]


@dataclass(frozen=True)
class KernelView:
    """Read-only copy of state handed to workers (they run off the kernel thread)."""

    now_ms: int
    slots: Mapping[str, JsonValue]
    locked: frozenset[str]
    goal: Optional[Goal]
    tools: Mapping[str, SafetyClass]


class Interpreter(Protocol):
    """Slow path: turns inputs into Interpretations, posted back later."""

    def submit(self, event: EventT, view: KernelView, post: Callable[[InternalEvent], None]) -> None: ...


@dataclass(frozen=True)
class _Resolved:
    step: Step
    arguments: dict[str, JsonValue]
    reads: dict[str, int]
    key: str


class Kernel:
    def __init__(
        self,
        *,
        session_id: str,
        clock: Clock,
        config: KeelConfig,
        trace: TraceWriter,
        sink: Callable[[ActionT], None],
        interpreter: Optional[Interpreter] = None,
        compiler: Optional[ManifestCompiler] = None,
        probe: Optional[StatusProbe] = None,
        post: Optional[Callable[[AnyEvent], None]] = None,
    ) -> None:
        self.session_id = session_id
        self.clock = clock
        self.cfg = config
        self.trace = trace
        self._sink = sink
        self.interpreter = interpreter
        self.compiler = compiler or default_compiler(config.compiler.min_confidence)
        if probe is None:
            from keel.compiler.probe import ManifestProbe

            probe = ManifestProbe()
        self.probe = probe
        # Default post: schedule on the clock at "now", i.e. after the current
        # handle() returns. Live runs replace this with a queue put.
        self.post: Callable[[AnyEvent], None] = post or (
            lambda ev: self.clock.call_later(0, lambda: self.handle(ev))
        )

        self.guard = WriteGuard()
        self.slots = SlotStore(self.guard)
        self.ledger = Ledger(self.guard, session_id)
        self.fence = CommitFence(
            quiet_ms=config.fence.quiet_ms,
            stale_turn_ms=config.fence.stale_turn_ms,
            require_end_of_turn=config.fence.require_end_of_turn,
            repair_wait_ms=config.fence.repair_wait_ms,
            transcript_wait_ms=config.fence.transcript_wait_ms,
        )
        self.ids = IdFactory(session_id)
        self.policies: dict[str, ToolPolicy] = {}

        self.goal: Optional[Goal] = None
        self.goal_gen = 0
        self.goal_done = False
        self.last_intent: Optional[str] = None
        # (intent, step_id) -> call_id of a write that went through.
        self._commits: dict[tuple[str, str], str] = {}
        self._clarified: set[tuple[str, str]] = set()
        self._call_cause: dict[str, Optional[str]] = {}
        self._call_intent: dict[str, str] = {}
        self._retry_of: dict[str, str] = {}
        self._reconciling: set[str] = set()
        # step_id -> seq of the call whose result the current goal is using.
        self._result_seq: dict[str, int] = {}
        # Only ask for a missing slot after the slow path has had its say.
        self._may_ask_missing = False
        self._deferred_ack: Optional[str] = None
        self._invalid_args: set[str] = set()
        self._held_for_speech: set[str] = set()
        # Writes whose outcome nothing could settle (see _give_up_on).
        self.unresolvable: set[str] = set()

        self._now = clock.now_ms()
        self._cause: Optional[str] = None
        self._timers: dict[tuple[str, Optional[str]], TimerHandle] = {}
        self._timer_gen: dict[tuple[str, Optional[str]], int] = {}
        self._timer_cause: dict[tuple[str, Optional[str]], Optional[str]] = {}

        # Floor management.
        self._turn = 0
        self._spoke_this_turn = False
        self._progress_this_turn = 0
        self._last_spoken_at = -1

    # ======================================================================
    # entry point
    # ======================================================================

    def handle(self, ev: AnyEvent) -> None:
        with self.guard.hold():
            self._now = self.clock.now_ms()
            self._may_ask_missing = isinstance(ev, Interpretation)
            if isinstance(ev, TimerFired):
                key = (ev.purpose, ev.ref)
                if self._timer_gen.get(key) != ev.gen:
                    return  # superseded timer
                self._timers.pop(key, None)
                self._cause = self._timer_cause.get(key)
                self._on_timer(ev)
            elif isinstance(ev, Interpretation):
                self._cause = ev.caused_by
                self.trace.note("interpretation", **ev.model_dump(mode="json"))
                self._on_interpretation(ev)
            else:
                self._cause = ev.event_id
                self.trace.event_in(ev)
                self._on_input(ev)
            self._converge()
            ack, self._deferred_ack = self._deferred_ack, None
            if ack and not self.goal_done:
                self._say("acknowledge", ack)
            self._schedule_progress()

    def view(self) -> KernelView:
        return KernelView(
            now_ms=self._now,
            slots=dict(self.slots.values()),
            locked=frozenset(n for n in self.slots.names() if self.slots.is_locked(n)),
            goal=self.goal,
            tools={n: p.safety for n, p in self.policies.items()},
        )

    # ======================================================================
    # inputs
    # ======================================================================

    def _on_input(self, ev: EventT) -> None:
        if isinstance(ev, ToolManifest):
            for name, pol in self.compiler(list(ev.tools)).items():
                self.policies[name] = pol
                self.trace.note("tool_policy", tool=name, safety=pol.safety, idempotent=pol.idempotent,
                                confidence=pol.confidence, method=pol.method, evidence=list(pol.evidence))
            return
        if isinstance(ev, ToolResult):
            self._on_result(ev)
            return
        if isinstance(ev, (TextChunk, AudioClip)):
            announced_repair = (isinstance(ev, TextChunk) and ev.end_of_turn
                                and editing_only(ev.text, self.cfg.fence.editing_terms))
            self.fence.user_activity(self._now, end_of_turn=ev.end_of_turn, editing_only=announced_repair)
            if isinstance(ev, TextChunk) and not ev.end_of_turn:
                # Rule 5: an empty chunk ends a stretch of speech; a non-empty one brings its words.
                if ev.text.strip():
                    self.fence.words_arrived(self._now)
                else:
                    self.fence.speech_ended(self._now)
            if announced_repair and self.fence.repair_pending:
                self.trace.note("repair_announced", text=ev.text, wait_ms=self.cfg.fence.repair_wait_ms)
            if ev.end_of_turn:
                self._new_turn()
                self._set_timer("ack", None, self._now + self.cfg.floor.ack_after_ms)
        elif isinstance(ev, Interrupt):
            # The user is talking over us: their turn is open. An interrupt by
            # itself cancels nothing; only a change to what a call read does.
            self.fence.user_activity(self._now, end_of_turn=False, speaking=True)
            self._new_turn()
        if isinstance(ev, (TextChunk, AudioClip, VideoFrame, Interrupt)) and self.interpreter is not None:
            self.interpreter.submit(ev, self.view(), self.post)

    def _new_turn(self) -> None:
        self._turn += 1
        self._spoke_this_turn = False
        self._progress_this_turn = 0

    def _on_interpretation(self, it: Interpretation) -> None:
        corrected: Optional[SlotChange] = None
        for p in it.proposals:
            if p.confidence < self.cfg.perception.clarify_below:
                self._clarify_low_confidence(p)
                continue
            out = self.slots.propose(p.name, p.value, source=p.source, confidence=p.confidence,
                                     now=self._now, correction=p.correction)
            if isinstance(out, Rejection):
                self.trace.note("proposal_rejected", slot=p.name, value=p.value, source=p.source,
                                confidence=p.confidence, reason=out.reason)
            elif isinstance(out, SlotChange):
                self.fence.slot_changed(p.name, self._now)
                self.trace.note(
                    "slot_changed", slot=p.name, value=out.new.value, version=out.new.version,
                    old_value=out.old.value if out.old else None,
                    old_version=out.old.version if out.old else 0,
                    source=p.source, confidence=p.confidence, correction=p.correction,
                    locked=self.slots.is_locked(p.name),
                )
                if out.old is not None and corrected is None and p.source in ("text", "audio"):
                    corrected = out
        if it.clear_goal:
            self._set_goal(None)
        if it.goal is not None and it.goal != self.goal:
            self._set_goal(it.goal)
        if corrected is not None:
            # Spoken after converge, so cancellations go out first.
            self._deferred_ack = render(self.cfg.phrases.correction, {"value": corrected.new.value})
        if it.reply and (self.goal is None or not self.goal.steps):
            self._final(it.reply)

    def _clarify_low_confidence(self, p: SlotProposal) -> None:
        key = (p.name, repr(p.value))
        self.trace.note("proposal_held", slot=p.name, value=p.value, source=p.source,
                        confidence=p.confidence, threshold=self.cfg.perception.clarify_below)
        if key in self._clarified:
            return
        self._clarified.add(key)
        text = render(self.cfg.phrases.clarify_low_confidence, {"slot": humanize(p.name), "value": p.value})
        if text:
            self._emit(Clarify(action_id=self.ids.new("act"), session_id=self.session_id, ts_ms=self._now,
                               caused_by=self._cause, text=text, slot=p.name))
            self._spoke()

    def _set_goal(self, goal: Optional[Goal]) -> None:
        self.goal_gen += 1
        self.goal = goal
        self.goal_done = False
        if goal is not None:
            self.last_intent = goal.intent
        self.trace.note("goal_set", gen=self.goal_gen,
                        goal=goal.model_dump(mode="json") if goal else None)

    # ======================================================================
    # results and timers
    # ======================================================================

    def _on_result(self, ev: ToolResult) -> None:
        e = self.ledger.get(ev.call_id)
        if e is None:
            self.trace.note("result_unknown_call", call_id=ev.call_id)
            return
        if e.status == "cancelled" and not e.in_doubt:
            self.trace.note("result_dropped", call_id=e.call_id, why="call was cancelled")
            return
        if e.status in ("succeeded", "failed"):
            self.trace.note("result_duplicate", call_id=e.call_id, status=e.status)
            return
        if e.status == "pending":
            self.trace.note("result_for_undispatched_call", call_id=e.call_id)
            return

        self._clear_timer("call_timeout", e.call_id)
        if ev.status == "ok":
            prior = e.status
            self.ledger.transition(e.call_id, "succeeded", self._now, result=ev.result)
            self.trace.note("call_status", call_id=e.call_id, status="succeeded",
                            late_after=prior if prior in ("cancelled", "unknown") else None)
            if e.writes and e.probe_for is None:
                self._commits.setdefault((self._intent_of(e), e.step_id), e.call_id)
            if e.probe_for:
                self._apply_probe(e)
            elif e.call_id in self._retry_of:
                # An idempotent retry succeeded: the effect exists, exactly once.
                original = self.ledger.get(self._retry_of[e.call_id])
                if original is not None and original.in_doubt:
                    self.ledger.resolve_doubt(original.call_id, True, self._now)
                    self.trace.note("doubt_resolved", call_id=original.call_id, by="idempotent retry",
                                    retry=e.call_id)
            self._reconciling.discard(e.call_id)
            return

        if ev.status == "error":
            if e.status == "cancelled":
                # A cancelled write that reports failure did not run.
                self.ledger.settle_cancelled(e.call_id, self._now)
                self.trace.note("call_status", call_id=e.call_id, status="cancelled(confirmed)")
            elif e.status == "unknown":
                self.ledger.transition(e.call_id, "failed", self._now, error=ev.error)
                self.trace.note("call_status", call_id=e.call_id, status="failed", error=ev.error)
            else:
                self.ledger.transition(e.call_id, "failed", self._now, error=ev.error)
                self.trace.note("call_status", call_id=e.call_id, status="failed", error=ev.error)
            if e.probe_for:
                self._probe_inconclusive(e)
            return

        self._timed_out(e, source="harness")

    def _timed_out(self, e: CallEntry, *, source: str) -> None:
        if e.probe_for:
            self.ledger.transition(e.call_id, "failed", self._now, error=f"timeout ({source})")
            self._probe_inconclusive(e)
            return
        if not e.writes:
            if e.status == "in_flight":
                self.ledger.transition(e.call_id, "failed", self._now, error=f"timeout ({source})")
                self.trace.note("call_status", call_id=e.call_id, status="failed", error=f"timeout ({source})")
            return
        if e.status == "in_flight":
            self.ledger.transition(e.call_id, "unknown", self._now, error=f"timeout ({source})")
            self.trace.note("call_status", call_id=e.call_id, status="unknown", error=f"timeout ({source})")
            self._say("status", self.cfg.phrases.status_unsure)
        self._reconcile(e)

    def _on_timer(self, t: TimerFired) -> None:
        if t.purpose == "call_timeout" and t.ref:
            e = self.ledger.get(t.ref)
            if e is not None and (e.status == "in_flight" or e.in_doubt) and e.call_id not in self._reconciling:
                self._timed_out(e, source="keel")
        elif t.purpose == "ack":
            if not self._spoke_this_turn:
                self._say("acknowledge", self.cfg.phrases.hold)
        elif t.purpose == "progress":
            if (self._in_flight_work() and self._progress_this_turn < self.cfg.floor.max_progress_per_turn
                    and self._now - self._last_spoken_at >= self.cfg.floor.progress_after_ms):
                self._progress_this_turn += 1
                self._say("progress", self.cfg.phrases.progress)
        # "fence": nothing to do here; converge re-checks held writes.

    # ======================================================================
    # reconciliation
    # ======================================================================

    def _reconcile(self, e: CallEntry) -> None:
        if e.call_id in self._reconciling:
            return
        self._reconciling.add(e.call_id)
        plan = plan_reconciliation(e, self.policies, self.probe,
                                   retry_if_idempotent=self.cfg.reconcile.retry_if_idempotent)
        self.trace.note("reconcile", call_id=e.call_id, plan=type(plan).__name__,
                        detail=getattr(plan, "reason", None) or (plan.call.tool if isinstance(plan, Probe) else None))
        if isinstance(plan, Retry):
            retry = self._new_entry(e.step_id, e.tool, e.arguments, e.reads, e.safety, retry_of=e.call_id)
            self._retry_of[retry.call_id] = e.call_id
            self._dispatch(retry)
        elif isinstance(plan, Probe):
            probe = self._new_entry(f"probe.{e.step_id}", plan.call.tool, plan.call.arguments, {},
                                    "read_only", probe_for=e.call_id)
            self._dispatch(probe)
        else:
            assert isinstance(plan, CannotConfirm)
            self._give_up_on(e)

    def _apply_probe(self, probe: CallEntry) -> None:
        target = self.ledger.get(probe.probe_for or "")
        if target is None or not target.in_doubt or self.probe is None:
            return
        verdict = self.probe.verdict(target, probe.result)
        self.trace.note("probe_verdict", call_id=target.call_id, probe=probe.call_id, verdict=verdict)
        was_unknown = target.status == "unknown"
        if verdict == "executed":
            self.ledger.resolve_doubt(target.call_id, True, self._now)
            self._commits.setdefault((self._intent_of(target), target.step_id), target.call_id)
            if was_unknown:
                self._say("status", self.cfg.phrases.status_confirmed)
        elif verdict == "not_executed":
            self.ledger.resolve_doubt(target.call_id, False, self._now)
            # With a goal still open, converge ends it with a final response
            # that carries this same sentence; don't say it twice.
            if was_unknown and (self.goal is None or self.goal_done):
                self._say("status", self.cfg.phrases.status_not_executed)
        else:
            self._probe_inconclusive(probe)

    def _probe_inconclusive(self, probe: CallEntry) -> None:
        target = self.ledger.get(probe.probe_for or "")
        self.trace.note("probe_inconclusive", probe=probe.call_id, call_id=probe.probe_for)
        if target is not None and target.in_doubt:
            self._give_up_on(target)

    def _give_up_on(self, e: CallEntry) -> None:
        """Nothing can settle this write. Say so and end the goal truthfully:
        blocking forever would stall the task, and re-sending could duplicate it."""
        self.trace.note("doubt_unresolvable", call_id=e.call_id)
        self.unresolvable.add(e.call_id)
        if self.goal is not None and not self.goal_done:
            self._final(self.cfg.phrases.status_cannot_confirm)
        else:
            self._say("status", self.cfg.phrases.status_cannot_confirm)

    # ======================================================================
    # converge
    # ======================================================================

    def _converge(self) -> None:
        goal = None if self.goal_done else self.goal
        needed: dict[str, _Resolved] = {}
        results: dict[str, CallEntry] = {}
        all_done = goal is not None
        failure: Optional[CallEntry] = None
        conflict: Optional[tuple[str, CallEntry]] = None
        to_create: list[_Resolved] = []

        for step in (goal.steps if goal else ()):
            r = self._resolve(step, results)
            if r is None:
                all_done = False
                continue
            state, entry = self._step_state(r)
            if state == "satisfied":
                assert entry is not None
                results[step.step_id] = entry
                continue
            all_done = False
            if state == "active":
                needed[r.key] = r
            elif state == "create":
                needed[r.key] = r
                to_create.append(r)
            elif state == "failed" and failure is None:
                failure = entry
            elif state == "conflict" and conflict is None:
                assert entry is not None
                conflict = (step.step_id, entry)

        self._result_seq = {sid: e.seq for sid, e in results.items()}
        # 1. cancel what is no longer needed (never probes; they settle doubt).
        for e in self.ledger.active():
            if e.probe_for is None and e.idem_key not in needed:
                self._cancel(e)
        # 2. create what is missing, then 3. dispatch whatever may go now.
        for r in to_create:
            if self.ledger.blocking(r.key) is None and self._arguments_valid(r):
                self._new_entry(r.step.step_id, r.step.tool, r.arguments, r.reads, self._safety(r.step.tool))
        for e in self.ledger.active():
            if e.status == "pending" and e.probe_for is None:
                self._maybe_dispatch(e)

        if goal is not None and failure is not None:
            self._finish_failed(failure)
        elif goal is not None and conflict is not None:
            self._finish_conflict(*conflict)
        elif goal is not None and all_done:
            self._final(self._reply_text(goal, results))

    def _resolve(self, step: Step, results: Mapping[str, CallEntry]) -> Optional[_Resolved]:
        args: dict[str, JsonValue] = {}
        reads: dict[str, int] = {}
        missing: list[str] = []
        for arg, b in self._bindings(step).items():
            if isinstance(b, SlotRef):
                sv = self.slots.get(b.slot)
                if sv is None or sv.value is None:
                    missing.append(b.slot)
                else:
                    args[arg] = sv.value
                    reads[b.slot] = sv.version
            elif isinstance(b, Const):
                args[arg] = b.const
            elif isinstance(b, ResultRef):
                src = results.get(b.result)
                ok, v = dig(src.result, b.path) if src else (False, None)
                if not ok:
                    missing.append(result_key(b.result))
                else:
                    args[arg] = v
                    reads[result_key(b.result)] = src.seq  # type: ignore[union-attr]
        for slot in step.basis:
            reads[slot] = self.slots.version(slot)
        for dep in step.after:
            if dep not in results:
                missing.append(result_key(dep))
            else:
                reads[result_key(dep)] = results[dep].seq
        if missing:
            self._maybe_ask_missing(step, [m for m in missing if not m.startswith("@")])
            return None
        return _Resolved(step, args, reads, self.ledger.key_for(step.tool, args))

    def _bindings(self, step: Step) -> dict[str, Any]:
        """The step's own bindings, plus the manifest's default slot for every
        required argument the step left unbound (ToolPolicy.slot_map)."""
        pol = self.policies.get(step.tool)
        if pol is None:
            return dict(step.bindings)
        out: dict[str, Any] = dict(step.bindings)
        for arg in pol.required:
            if arg not in out and arg in pol.slot_map:
                out[arg] = SlotRef(slot=pol.slot_map[arg])
        return out

    def _step_state(self, r: _Resolved) -> tuple[str, Optional[CallEntry]]:
        writes = self._safety(r.step.tool) != "read_only"
        if writes:
            commit_id = self._commits.get((self._intent(), r.step.step_id))
            if commit_id is not None:
                committed = self.ledger.get(commit_id)
                assert committed is not None
                if committed.idem_key == r.key:
                    return "satisfied", committed
                return "conflict", committed
        blocker = self.ledger.blocking(r.key)
        if blocker is not None:
            if blocker.status == "succeeded":
                return "satisfied", blocker
            if blocker.status in ACTIVE:
                return "active", blocker
            return "blocked", None
        if writes:
            # Writes to one tool are serialised: a new one waits while another
            # with different arguments is in flight (it may be about to be
            # cancelled in this very converge) or in doubt, so an old and a
            # new write can never both land.
            # ASSUMPTION [K19] What a cancel does to an in-flight call in the
            # mock environment (dropped silently? a final result?). We wait for
            # a result or our timeout before probing, since probing while the
            # write may still be executing could read a misleading "not found".
            if any(e.writes and e.tool == r.step.tool and e.probe_for is None
                   and (e.status == "in_flight" or e.in_doubt) for e in self.ledger):
                return "blocked", None
        tries = [e for e in self.ledger.with_key(r.key) if e.status == "failed" and e.probe_for is None]
        if tries and (writes or len(tries) > self.cfg.calls.read_retries):
            return "failed", tries[-1]
        return "create", None

    def _arguments_valid(self, r: _Resolved) -> bool:
        """Check arguments against the tool's schema before anything is sent.
        On failure, ask about the slot behind the first bad argument (once)."""
        pol = self.policies.get(r.step.tool)
        errors = pol.validate(r.arguments) if pol else []
        if not errors:
            return True
        if r.key not in self._invalid_args:
            self._invalid_args.add(r.key)
            self.trace.note("arguments_invalid", step_id=r.step.step_id, tool=r.step.tool,
                            arguments=r.arguments, errors=errors)
            bad = errors[0].split(":", 1)[0].split("/")[0]
            b = self._bindings(r.step).get(bad)
            if isinstance(b, SlotRef) and self._may_ask_missing:
                key = (b.slot, "<invalid>")
                if key not in self._clarified:
                    self._clarified.add(key)
                    text = render(self.cfg.phrases.clarify_missing, {"slot": humanize(b.slot)})
                    if text:
                        self._emit(Clarify(action_id=self.ids.new("act"), session_id=self.session_id,
                                           ts_ms=self._now, caused_by=self._cause, text=text, slot=b.slot))
                        self._spoke()
        return False

    def _maybe_ask_missing(self, step: Step, slots: list[str]) -> None:
        if not slots or self.fence.turn_open or not self._may_ask_missing:
            return
        slot = slots[0]
        key = (slot, "<missing>")
        if any(k[0] == slot for k in self._clarified):
            return  # already asked about this slot (missing or low-confidence)
        self._clarified.add(key)
        text = render(self.cfg.phrases.clarify_missing, {"slot": humanize(slot)})
        if text:
            self._emit(Clarify(action_id=self.ids.new("act"), session_id=self.session_id, ts_ms=self._now,
                               caused_by=self._cause, text=text, slot=slot))
            self._spoke()

    def _finish_conflict(self, step_id: str, committed: CallEntry) -> None:
        """A write for this step already went through with the old details.
        Re-sending would duplicate it, so end the goal truthfully instead of
        waiting forever; a later goal (e.g. one using a modify tool) can still
        change it."""
        self.trace.note("commit_conflict", step_id=step_id, committed_call=committed.call_id,
                        committed_arguments=committed.arguments)
        self._final(self.cfg.phrases.already_committed)

    # ======================================================================
    # call lifecycle
    # ======================================================================

    def _safety(self, tool: str) -> SafetyClass:
        pol = self.policies.get(tool)
        return pol.safety if pol else "unknown"

    def _intent(self) -> str:
        return self.goal.intent if self.goal else (self.last_intent or "")

    def _intent_of(self, e: CallEntry) -> str:
        return self._call_intent.get(e.call_id, self._intent())

    def _new_entry(self, step_id: str, tool: str, arguments: dict[str, JsonValue], reads: dict[str, int],
                   safety: SafetyClass, *, probe_for: Optional[str] = None,
                   retry_of: Optional[str] = None) -> CallEntry:
        e = self.ledger.add(call_id=self.ids.new("call"), step_id=step_id, goal_gen=self.goal_gen, tool=tool,
                            arguments=arguments, reads=reads, safety=safety, now=self._now,
                            probe_for=probe_for, retry_of=retry_of)
        self._call_cause[e.call_id] = self._cause
        self._call_intent[e.call_id] = self._intent()
        self.trace.note("call_created", call_id=e.call_id, step_id=step_id, tool=tool, arguments=arguments,
                        reads=reads, safety=safety, idem_key=e.idem_key, probe_for=probe_for, retry_of=retry_of)
        return e

    def _maybe_dispatch(self, e: CallEntry) -> None:
        if not e.writes and not self.cfg.fence.hold_reads:
            self._dispatch(e)
            return
        opens = self.fence.opens_at({k: v for k, v in e.reads.items() if not k.startswith("@")})
        if opens is None:
            # The user is speaking; the input that ends it re-runs converge.
            if e.call_id not in self._held_for_speech:
                self._held_for_speech.add(e.call_id)
                self.trace.note("fence_hold", call_id=e.call_id, until=None, why="user is speaking")
            self._clear_timer("fence", e.call_id)
        elif opens <= self._now:
            self._dispatch(e)
        else:
            self._held_for_speech.discard(e.call_id)
            if ("fence", e.call_id) not in self._timers:
                why = {"transcript": "speech not yet transcribed", "repair": "announced repair"}.get(
                    self.fence.binding or "")
                if why:
                    self.trace.note("fence_hold", call_id=e.call_id, until=opens, why=why)
                else:
                    self.trace.note("fence_hold", call_id=e.call_id, until=opens)
            self._set_timer("fence", e.call_id, opens)

    def _dispatch(self, e: CallEntry) -> None:
        self._clear_timer("fence", e.call_id)
        self.ledger.transition(e.call_id, "in_flight", self._now)
        cause = self._call_cause.get(e.call_id)
        self._emit(ToolCall(action_id=self.ids.new("act"), session_id=self.session_id, ts_ms=self._now,
                            caused_by=cause, call_id=e.call_id, tool=e.tool, arguments=e.arguments))
        # ASSUMPTION [K18] Keel-side call timeout; the kit's latency and
        # fault model is unknown, so the value is provisional (config/keel.toml).
        self._set_timer("call_timeout", e.call_id, self._now + self.cfg.calls.timeout_ms)
        pol = self.policies.get(e.tool)
        # One filler per turn: a pending correction acknowledgment wins.
        if (pol and pol.ack_template and not self._spoke_this_turn and e.probe_for is None
                and not self._deferred_ack):
            text = render(pol.ack_template, e.arguments)
            if text:
                self._say("acknowledge", text)

    def _cancel(self, e: CallEntry) -> None:
        stale = []
        for s, v in e.reads.items():
            cur = self._current_version(s)
            if cur != v:
                stale.append({"slot": s, "read": v, "now": cur})
        reason = ("; ".join(f"{d['slot']} v{d['read']}->v{d['now']}" for d in stale)
                  or "no longer needed by the current goal")
        was_in_flight = e.status == "in_flight"
        self._clear_timer("fence", e.call_id)
        self.ledger.transition(e.call_id, "cancelled", self._now, cancel_reason=reason)
        if was_in_flight:
            self._emit(Cancel(action_id=self.ids.new("act"), session_id=self.session_id, ts_ms=self._now,
                              caused_by=self._cause, call_id=e.call_id, reason=reason))
            if not e.writes:
                self._clear_timer("call_timeout", e.call_id)
        dispatched_at = e.dispatched_at if e.dispatched_at is not None else e.created_at
        self.trace.note("call_cancelled", call_id=e.call_id, reason=reason, invalidated_by=stale,
                        was_in_flight=was_in_flight, outcome_in_doubt=e.outcome_in_doubt,
                        cause=self._cause, ms_since_dispatch=self._now - dispatched_at)

    def _current_version(self, key: str) -> int:
        if key.startswith("@"):
            # A step result's version is the seq of the call that produced the
            # result the current goal uses (0 if none is available now).
            return self._result_seq.get(key[1:], 0)
        return self.slots.version(key)

    # ======================================================================
    # speaking
    # ======================================================================

    def _emit(self, action: ActionT) -> None:
        if not self.cfg.floor.speak and isinstance(action, (Speak, Clarify, FinalResponse)):
            # Another component owns the conversation (e.g. a LiveKit LLM). The
            # kernel's own lines are recorded as notes, never as actions, so the
            # trace does not show speech that was never produced.
            self.trace.note(f"unspoken_{action.type}", **action.model_dump(mode="json"))
            return
        self.trace.action_out(action)
        self._sink(action)

    def _spoke(self) -> None:
        self._spoke_this_turn = True
        self._last_spoken_at = self._now
        self._clear_timer("ack", None)

    def _say(self, purpose: str, text: str) -> None:
        self._emit(Speak(action_id=self.ids.new("act"), session_id=self.session_id, ts_ms=self._now,
                         caused_by=self._cause, text=text, purpose=purpose))  # type: ignore[arg-type]
        self._spoke()

    def snapshot(self) -> StateSnapshot:
        return StateSnapshot(intent=self._intent() or None, slots=self.slots.values())

    def _final(self, text: str) -> None:
        self._emit(FinalResponse(action_id=self.ids.new("act"), session_id=self.session_id, ts_ms=self._now,
                                 caused_by=self._cause, text=text, snapshot=self.snapshot()))
        self._spoke()
        self.goal_done = True
        self.slots.release_locks()

    def _finish_failed(self, e: CallEntry) -> None:
        text = (self.cfg.phrases.status_not_executed if e.history and any(s == "unknown" for _, s in e.history)
                else render(self.cfg.phrases.failed, {"error": e.error or "the tool reported an error"}))
        self._final(text or self.cfg.phrases.status_cannot_confirm)

    def _reply_text(self, goal: Goal, results: Mapping[str, CallEntry]) -> str:
        if goal.reply:
            values: dict[str, Any] = dict(self.slots.values())
            values.update({sid: e.result for sid, e in results.items()})
            text = render(goal.reply, values)
            if text:
                return text
        return self.cfg.phrases.final_default

    def _in_flight_work(self) -> bool:
        return any(e.status == "in_flight" for e in self.ledger)

    def _schedule_progress(self) -> None:
        if not self._in_flight_work():
            self._clear_timer("progress", None)
            return
        if ("progress", None) in self._timers:
            return
        anchor = max(self._last_spoken_at, self.fence.last_user_activity, 0)
        self._set_timer("progress", None, max(self._now, anchor) + self.cfg.floor.progress_after_ms)

    # ======================================================================
    # timers
    # ======================================================================

    def _set_timer(self, purpose: str, ref: Optional[str], at: int) -> None:
        key = (purpose, ref)
        old = self._timers.pop(key, None)
        if old is not None:
            old.cancel()
        gen = self._timer_gen.get(key, 0) + 1
        self._timer_gen[key] = gen
        self._timer_cause[key] = self._cause
        ev = TimerFired(purpose=purpose, ref=ref, gen=gen)  # type: ignore[arg-type]
        self._timers[key] = self.clock.call_at(max(at, self._now), lambda: self.post(ev))

    def _clear_timer(self, purpose: str, ref: Optional[str]) -> None:
        key = (purpose, ref)
        old = self._timers.pop(key, None)
        if old is not None:
            old.cancel()
        self._timer_gen[key] = self._timer_gen.get(key, 0) + 1


# ==========================================================================
# live runner
# ==========================================================================

class AsyncKernelRunner:
    """Runs a Kernel on a live asyncio loop behind a KitAdapter.

    One consumer task drains a single inbox, so handle() is only ever called
    from that task: inputs from the adapter, results posted by workers
    (thread-safe), and timer events all go through the same queue.
    """

    def __init__(self, make_kernel: Callable[[Callable[[AnyEvent], None], Callable[[ActionT], None]], Kernel]):
        self._make_kernel = make_kernel

    async def run(self, adapter: Any) -> Kernel:
        loop = asyncio.get_running_loop()
        inbox: asyncio.Queue = asyncio.Queue()
        outbox: list[ActionT] = []
        end = object()

        def post(ev: AnyEvent) -> None:
            loop.call_soon_threadsafe(inbox.put_nowait, ev)

        kernel = self._make_kernel(post, outbox.append)

        async def reader() -> None:
            while (ev := await adapter.receive()) is not None:
                inbox.put_nowait(ev)
            inbox.put_nowait(end)

        reader_task = asyncio.create_task(reader())
        try:
            while (ev := await inbox.get()) is not end:
                try:
                    kernel.handle(ev)
                except Exception as exc:  # noqa: BLE001
                    # One bad event must not end the session: every later
                    # event (and its score) would be lost. Record it and go on;
                    # the simulator calls handle() directly, so tests still fail loudly.
                    kernel.trace.note("kernel_error", event_type=getattr(ev, "type", type(ev).__name__),
                                      error=f"{type(exc).__name__}: {exc}")
                while outbox:
                    await adapter.send(outbox.pop(0))
        finally:
            reader_task.cancel()
        return kernel
