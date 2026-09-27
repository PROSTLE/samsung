"""KeelGate: the Keel kernel between an LLM's tool calls and the tools.

A LiveKit agent's LLM decides *what* to call. The gate decides *whether and
when* it is safe to run it, using the same kernel, ledger and commit fence
as the rest of Keel. Every call the LLM makes becomes one goal step whose
arguments are constants and whose `basis` is the user's utterance:

  * **Held until the user has finished.** A call is dispatched only once the
    commit fence opens: the user's turn has ended, they are not speaking,
    and `fence.quiet_ms` has passed. With `fence.hold_reads` this applies to
    read-only calls too.
  * **Dropped if the user keeps talking.** When new transcribed speech
    arrives, every call that has not been dispatched yet is removed from the
    goal. The kernel cancels it (reason `utterance vN->vN+1`) and the LLM is
    told it was superseded. It never ran, so it never reaches the tool log.
  * **Run once.** The ledger's idempotency key (session, tool, arguments)
    means an identical call later in the same session returns the first
    result instead of executing again.
  * **Truthful about doubt.** If the kernel cannot settle whether a write
    ran, the LLM is told exactly that, never "done".

Only this class touches the kernel, and only from the event loop's thread:
events from LiveKit callbacks, tool results and kernel timers all enter
through `_handle`, one at a time.

No LiveKit import: the gate works on any asyncio loop, and is tested that way.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

from pydantic import JsonValue

from keel.compiler.manifest import ManifestCompiler
from keel.config import KeelConfig, with_overrides
from keel.kernel.clock import Clock
from keel.kernel.internal import Interpretation, SlotProposal
from keel.kernel.ledger import CallEntry
from keel.kernel.loop import AnyEvent, Kernel
from keel.kernel.plan import Const, Goal, Step
from keel.paths.fast import render
from keel.protocol.ids import IdFactory
from keel.protocol.provisional import ActionT, Interrupt, TextChunk, ToolCall, ToolManifest, ToolResult, ToolSpec
from keel.trace import TraceWriter

# (status, result, error) — Keel's ToolResult vocabulary.
Execute = Callable[[str, dict], Awaitable[tuple[str, Any, Optional[str]]]]

UTTERANCE = "utterance"
INTENT = "llm_tool_calls"


@dataclass
class _Pending:
    step: Step
    future: asyncio.Future
    utterance_version: int


def _payload(status: str, **fields: Any) -> dict[str, Any]:
    return {"status": status, **{k: v for k, v in fields.items() if v is not None}}


class KeelGate:
    def __init__(
        self,
        *,
        session_id: str,
        config: KeelConfig,
        tools: Sequence[ToolSpec],
        execute: Execute,
        trace: TraceWriter,
        clock: Clock,
        loop: asyncio.AbstractEventLoop,
        drop_on_new_speech: bool,
        complete_arguments: Optional[Callable[[str, dict], dict]] = None,
        compiler: Optional[ManifestCompiler] = None,
    ) -> None:
        self.session_id = session_id
        self.cfg = config
        self.loop = loop
        self.clock = clock
        self.trace = trace
        self._execute = execute
        self._drop = drop_on_new_speech
        # Fills defaults and canonicalises types *before* the idempotency key is
        # taken, so `add_to_cart(p)` and `add_to_cart(p, quantity=1)` are one call.
        self._complete = complete_arguments or (lambda tool, args: dict(args))
        self._ids = IdFactory(f"{session_id}-lk")
        if not drop_on_new_speech and config.fence.transcript_wait_ms:
            # A realtime model's input transcript may come after its tool call:
            # there, a transcript is not a stretch of speech's words (fence rule 5).
            config = with_overrides(config, fence={"transcript_wait_ms": 0})
            self.cfg = config
        self.kernel = Kernel(session_id=session_id, clock=clock, config=config, trace=trace,
                             sink=self._on_action, post=self._post, compiler=compiler)
        self._pending: dict[str, _Pending] = {}
        self._n = 0
        self._turn_text = ""
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self._handle(ToolManifest(event_id=self._ids.new("evt"), session_id=session_id,
                                  ts_ms=clock.now_ms(), tools=list(tools)))

    # ------------------------------------------------------------------ in
    def _post(self, ev: AnyEvent) -> None:
        """Kernel timers and tool results: handled later, on the loop, in order."""
        self.loop.call_soon_threadsafe(self._handle, ev)

    def _handle(self, ev: AnyEvent) -> None:
        if self._closed:
            return
        try:
            self.kernel.handle(ev)
        except Exception as exc:  # noqa: BLE001 - one bad event must not end the session
            self.trace.note("kernel_error", event_type=getattr(ev, "type", type(ev).__name__),
                            error=f"{type(exc).__name__}: {exc}")
        self._settle()

    def _now(self) -> int:
        return self.clock.now_ms()

    def user_speaking(self) -> None:
        """VAD: the user started speaking. Their turn is open; held calls wait."""
        self._handle(Interrupt(event_id=self._ids.new("evt"), session_id=self.session_id, ts_ms=self._now()))

    def user_stopped(self) -> None:
        """VAD: the user stopped speaking (the turn may not be over)."""
        self._handle(TextChunk(event_id=self._ids.new("evt"), session_id=self.session_id, ts_ms=self._now(),
                               text="", end_of_turn=False))

    def user_transcript(self, text: str, *, final: bool) -> None:
        """STT output. A new non-empty final transcript is new speech: calls
        planned before it that have not gone out yet are dropped."""
        text = text.strip()
        if not final or not text:
            return
        self._turn_text = f"{self._turn_text} {text}".strip()
        dropped: list[str] = []
        if self._drop:
            for sid, p in list(self._pending.items()):
                if not self._dispatched(p):
                    dropped.append(sid)
                    del self._pending[sid]
                    self._resolve(p, _payload("superseded", message=(
                        "Not run: the user kept speaking after this call was planned, so it may use "
                        "retracted details. Do not repeat it; act on the user's complete request.")))
            if dropped:
                self.trace.note("superseded_by_speech", steps=dropped, transcript=text)
            # The words of the stretch of speech that just ended (fence rule 5).
            self._handle(TextChunk(event_id=self._ids.new("evt"), session_id=self.session_id, ts_ms=self._now(),
                                   text=text, end_of_turn=False))
        self._handle(Interpretation(
            proposals=(SlotProposal(name=UTTERANCE, value=self._turn_text, source="system", confidence=1.0),),
            **self._goal_fields()))

    def user_turn_committed(self, text: str) -> None:
        """The session committed the user's turn (end of turn)."""
        self._handle(TextChunk(event_id=self._ids.new("evt"), session_id=self.session_id, ts_ms=self._now(),
                               text=text, end_of_turn=True))
        self._turn_text = ""

    def note(self, kind: str, **data: JsonValue) -> None:
        """Record something the session did (agent speech, a filler) in the trace."""
        if not self._closed:
            self.trace.note(kind, **data)

    # --------------------------------------------------------------- calls
    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One LLM tool call. Returns the JSON-able payload the LLM should see."""
        _, fut = self.submit(tool, arguments)
        return await fut

    def submit(self, tool: str, arguments: dict[str, Any]) -> tuple[Optional[str], asyncio.Future]:
        """Register one LLM tool call now; the future resolves with its payload.
        Returns (step_id, future); step_id is None if the call was rejected."""
        pol = self.kernel.policies.get(tool)
        if pol is not None:
            arguments = self._complete(tool, arguments)
        errors = [f"unknown tool {tool}"] if pol is None else pol.validate(arguments)
        if errors:
            self.trace.note("arguments_rejected", tool=tool, arguments=arguments, errors=errors)
            done: asyncio.Future = self.loop.create_future()
            done.set_result(_payload("error", message="Invalid arguments: " + "; ".join(errors)))
            return None, done
        self._n += 1
        step = Step(step_id=f"c{self._n}", tool=tool,
                    bindings={k: Const(const=v) for k, v in arguments.items()}, basis=(UTTERANCE,))
        fut: asyncio.Future = self.loop.create_future()
        self._pending[step.step_id] = _Pending(step, fut, self.kernel.slots.version(UTTERANCE))
        self.trace.note("llm_tool_call", step_id=step.step_id, tool=tool, arguments=arguments)
        self._handle(Interpretation(**self._goal_fields()))
        return step.step_id, fut

    def drop(self, step_id: str, why: str) -> bool:
        """Drop a call that has not been dispatched (e.g. its reply was
        interrupted). Returns False if it already went out."""
        p = self._pending.get(step_id)
        if p is None or self._dispatched(p):
            return False
        del self._pending[step_id]
        self.trace.note("call_dropped", step_id=step_id, why=why)
        self._resolve(p, _payload("superseded", message=f"Not run: {why}."))
        self._handle(Interpretation(**self._goal_fields()))
        return True

    def filler(self, step_id: str, n: int) -> Optional[str]:
        """What Keel may truthfully say while this call is held or running.
        n = how many fillers were already spoken for it. None = say nothing."""
        p = self._pending.get(step_id)
        if p is None:
            return None
        purposes = set(self.cfg.livekit.speak_purposes) if self.cfg.livekit else set()
        entry = self._entry(p)
        text, purpose = None, None
        if n == 0 and "acknowledge" in purposes:
            if entry is not None and entry.dispatched:
                pol = self.kernel.policies.get(p.step.tool)
                text = render(pol.ack_template, entry.arguments) if pol and pol.ack_template else None
            text, purpose = text or self.cfg.phrases.hold, "acknowledge"
        elif n >= 1 and "progress" in purposes and n <= self.cfg.floor.max_progress_per_turn:
            text, purpose = self.cfg.phrases.progress, "progress"
        if text:
            self.trace.note("filler_said", step_id=step_id, text=text, purpose=purpose)
        return text

    # ------------------------------------------------------------ internals
    def _goal_fields(self) -> dict[str, Any]:
        steps = tuple(p.step for p in self._pending.values())
        if not steps:
            return {"clear_goal": True}
        return {"goal": Goal(intent=INTENT, steps=steps)}

    def _entry(self, p: _Pending) -> Optional[CallEntry]:
        entries = [e for e in self.kernel.ledger if e.step_id == p.step.step_id and e.probe_for is None]
        return entries[-1] if entries else None

    def _dispatched(self, p: _Pending) -> bool:
        e = self._entry(p)
        return e is not None and e.dispatched

    def _resolve(self, p: _Pending, payload: dict[str, Any]) -> None:
        if not p.future.done():
            p.future.set_result(payload)
        self.trace.note("tool_result_to_llm", step_id=p.step.step_id, tool=p.step.tool,
                        status=payload["status"])

    def _outcome(self, p: _Pending) -> Optional[dict[str, Any]]:
        e = self._entry(p)
        if e is None:
            # No call of its own: the same call already ran in this session.
            args = {k: b.const for k, b in p.step.bindings.items()}  # type: ignore[union-attr]
            prior = self.kernel.ledger.blocking(self.kernel.ledger.key_for(p.step.tool, args))
            if prior is not None and prior.status == "succeeded":
                self.trace.note("result_reused", step_id=p.step.step_id, call_id=prior.call_id)
                return _payload("ok", result=prior.result, note="identical call already made in this session")
            return None
        if e.status == "succeeded":
            return _payload("ok", result=e.result)
        if e.status == "failed":
            return _payload("error", message=e.error or "the tool reported an error")
        if e.status == "cancelled" and not e.dispatched:
            return _payload("superseded", message=f"Not run: {e.cancel_reason}.")
        if e.status == "cancelled" and not e.in_doubt:
            return _payload("error", message="cancelled; the tool confirmed it did not run")
        if e.call_id in self.kernel.unresolvable:
            return _payload("unknown", message=(
                "No confirmation was received and it could not be checked, so it is not known "
                "whether this went through. Say so; do not repeat the call."))
        return None

    def _settle(self) -> None:
        # Bounded: each round either resolves a call or stops.
        for _ in range(len(self._pending) + 1):
            changed = False
            for sid, p in list(self._pending.items()):
                out = self._outcome(p)
                if out is not None:
                    del self._pending[sid]
                    self._resolve(p, out)
                    changed = True
            if not changed or self._closed:
                return
            # Keep the goal equal to the calls still outstanding, so a finished
            # call never leaves the kernel cancelling the ones still running.
            try:
                self.kernel.handle(Interpretation(**self._goal_fields()))
            except Exception as exc:  # noqa: BLE001
                self.trace.note("kernel_error", event_type="interpretation", error=f"{type(exc).__name__}: {exc}")
                return

    # ------------------------------------------------------------------ out
    def _on_action(self, action: ActionT) -> None:
        if isinstance(action, ToolCall):
            task = self.loop.create_task(self._run(action))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        # Cancel: the call has not returned yet and a running mock cannot be
        # stopped; the trace records the cancel and its outcome stays in doubt.
        # Speak/Clarify/Final are not emitted when floor.speak is false.

    async def _run(self, call: ToolCall) -> None:
        status, result, error = await self._execute(call.tool, dict(call.arguments))
        try:
            json.dumps(result)
        except (TypeError, ValueError):
            result = str(result)
        self._post(ToolResult(event_id=self._ids.new("res"), session_id=self.session_id, ts_ms=self._now(),
                              call_id=call.call_id, status=status if status in ("ok", "error", "timeout") else "error",
                              result=result if status == "ok" else None, error=error))

    async def aclose(self) -> None:
        for task in list(self._tasks):
            try:
                await task
            except Exception:  # noqa: BLE001
                pass
        # Let the results those tasks posted be handled before closing.
        await asyncio.sleep(0)
        self._closed = True
        for p in self._pending.values():
            if not p.future.done():
                p.future.set_result(_payload("error", message="session closed"))
        self.trace.close()
