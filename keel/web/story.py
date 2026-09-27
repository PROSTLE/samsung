"""A Keel trace told as a conversation: what was said, and what became of each action.

The web app shows this instead of raw trace records, both for a recorded
session (replay) and live (the agent feeds its trace records in as they are
written and publishes the updates to the room). Every event comes from a
trace record; nothing is inferred beyond what the record says.

Events (JSON-able dicts, ``t`` = ms since the session started):
  {"kind": "user",   "t", "text"}                    a finished user turn
  {"kind": "agent",  "t", "text"}                    what the agent said
  {"kind": "keel",   "t", "text", "purpose"}         a line Keel itself spoke while a call waited
  {"kind": "note",   "t", "text"}                    a Keel decision worth a line
  {"kind": "config", "t", "pipeline"}                the agent's pipeline (cascaded, gemini_realtime, ...)
  {"kind": "state",  "t", "who", "state"}            who = "user": speaking | quiet (voice activity);
                                                     who = "agent": listening | thinking | speaking | ...
  {"kind": "action", "t", "id", "tool", "title", "args", "state", "status", "reason", "history"}
      the whole current state of one planned action, sent again on every change.
      state: planned | holding | sent | done | failed | not_sent | reused
"""

from __future__ import annotations

import copy
import json
import statistics
from typing import Any, Iterable, Optional

from pydantic import JsonValue

from keel.trace import TraceRecord

Event = dict[str, Any]

# Participants who joined from the web app (keel/web/server.py signs their
# tokens), and the text-stream topic the agent publishes story events on.
WEB_IDENTITY_PREFIX = "keel-web-"
STORY_TOPIC = "keel.story"

# Keel's own reasons, as the user would put them. Unknown reasons are shown as
# recorded, so a new reason is never hidden.
_NOT_SENT_REASONS = (
    ("the user interrupted the reply this call belonged to", "You started speaking again before it was sent."),
    ("no longer needed by the current goal", "No longer needed after what you said next."),
)
FINAL_STATES = ("done", "failed", "not_sent", "reused")


def title_of(tool: str) -> str:
    """search_flights -> Search flights"""
    words = tool.replace("-", "_").split("_")
    return " ".join([words[0].capitalize(), *words[1:]]) if words and words[0] else tool


def label_of(name: str) -> str:
    return title_of(name)


def value_text(value: JsonValue) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def summarize_result(result: JsonValue) -> str:
    """A one-line description of a tool result for the action card."""
    if not isinstance(result, dict):
        return value_text(result)
    # The first field decides (traces store keys sorted): a list first is the
    # answer (flights, products); otherwise its short scalar fields are.
    items = [(k, v) for k, v in result.items() if k not in ("status", "message", "source")]
    if items and isinstance(items[0][1], list):
        key, n = items[0][0], len(items[0][1])
        return f"{n} {label_of(key).lower() if n != 1 else label_of(key).lower().rstrip('s')} returned"
    scalars = [(k, v) for k, v in items
               if not isinstance(v, (dict, list, bool)) and len(value_text(v)) <= 60]
    if scalars:
        return ", ".join(f"{label_of(k)}: {value_text(v)}" for k, v in scalars[:3])
    return value_text(result.get("status", "done"))


class Story:
    """Feed trace records in order; each call returns the events they produce."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.actions: dict[str, Event] = {}
        self._step_of_call: dict[str, str] = {}
        self._repair_pending = False
        self._state: dict[str, str] = {}
        self.scenario: Optional[str] = None
        self.session_id: Optional[str] = None
        self.synthetic: Optional[bool] = None
        self.pipeline: Optional[str] = None
        # Wall-clock anchor (session_start.unix_ms, when the trace recorded one)
        # minus that record's own ts_ms: the Unix time of t = 0.
        self.unix_ms_at_zero: Optional[int] = None
        self.ended_at: Optional[int] = None
        self.last_t: int = 0

    # ------------------------------------------------------------------ input
    def feed(self, rec: TraceRecord) -> list[Event]:
        out: list[Optional[Event]] = []
        d, t = rec.data, rec.ts_ms
        self.last_t = max(self.last_t, t)
        kind = rec.kind
        if kind == "session_start":
            self.scenario = d.get("scenario")  # type: ignore[assignment]
            self.session_id = d.get("session_id")  # type: ignore[assignment]
            self.synthetic = bool(d.get("synthetic")) if "synthetic" in d else None
            if isinstance(d.get("unix_ms"), int):
                self.unix_ms_at_zero = int(d["unix_ms"]) - t  # type: ignore[arg-type]
        elif kind == "session_end":
            self.ended_at = t
        elif kind == "session_config":
            self.pipeline = d.get("pipeline")  # type: ignore[assignment]
            out.append(self._emit({"kind": "config", "t": t, "pipeline": self.pipeline}))
        elif kind == "interrupt" and rec.dir == "in":
            # The gate records voice activity starting as an interrupt (keel/livekit/gate.py).
            out.append(self._set_state("user", t, "speaking"))
        elif kind == "text" and rec.dir == "in":
            text = str(d.get("text") or "").strip()
            if d.get("end_of_turn"):
                out.append(self._set_state("user", t, "quiet"))
                if text:
                    self._repair_pending = False
                    out.append(self._emit({"kind": "user", "t": t, "text": text}))
            elif not text:
                # An empty chunk that does not end the turn: voice activity stopped.
                out.append(self._set_state("user", t, "quiet"))
        elif kind == "agent_state" and d.get("state"):
            out.append(self._set_state("agent", t, str(d["state"])))
        elif kind == "agent_said" and str(d.get("text") or "").strip():
            out.append(self._emit({"kind": "agent", "t": t, "text": str(d["text"]).strip()}))
        elif kind == "filler_said" and str(d.get("text") or "").strip():
            out.append(self._emit({"kind": "keel", "t": t, "text": str(d["text"]).strip(),
                                   "purpose": d.get("purpose")}))
        elif kind == "repair_announced":
            self._repair_pending = True
            out.append(self._emit({"kind": "note", "t": t, "text": (
                f"“{d.get('text')}” announces a correction. Actions planned now wait for it.")}))
        elif kind == "llm_tool_call":
            step = str(d["step_id"])
            args = d.get("arguments") or {}
            self.actions[step] = {
                "kind": "action", "t": t, "id": step, "tool": d.get("tool"), "title": title_of(str(d.get("tool"))),
                "args": [[label_of(k), value_text(v)] for k, v in (args.items() if isinstance(args, dict) else [])],
                "state": None, "status": None, "reason": None, "history": [],
            }
            out.append(self._update(step, t, "planned", "Planned by the model"))
        elif kind == "call_created":
            step = d.get("step_id")
            if step is not None and str(step) in self.actions:
                self._step_of_call[str(d["call_id"])] = str(step)
        elif kind == "fence_hold":
            step = self._step_of_call.get(str(d.get("call_id")))
            if step:
                if d.get("until") is None:
                    status = "Waiting: you are speaking"
                elif d.get("why") == "speech not yet transcribed":
                    status = "Waiting for the words you just said"
                elif self._repair_pending:
                    status = "Waiting for the correction you announced"
                else:
                    status = "Waiting for you to finish"
                out.append(self._update(step, t, "holding", status))
        elif kind == "tool_call" and rec.dir == "out":
            step = self._step_of_call.get(str(d.get("call_id")))
            if step:
                out.append(self._update(step, t, "sent", "Sent"))
        elif kind == "tool_result" and rec.dir == "in":
            step = self._step_of_call.get(str(d.get("call_id")))
            if step:
                if d.get("error"):
                    out.append(self._update(step, t, "failed", "Failed", reason=str(d["error"])))
                else:
                    out.append(self._update(step, t, "done", "Completed", reason=summarize_result(d.get("result"))))
        elif kind == "call_dropped":
            out.append(self._not_sent(str(d.get("step_id")), t, str(d.get("why") or "")))
        elif kind == "superseded_by_speech":
            for step in d.get("steps") or []:  # type: ignore[union-attr]
                out.append(self._not_sent(str(step), t, "Your next words arrived before it was sent."))
        elif kind == "call_cancelled" and not d.get("was_in_flight"):
            step = self._step_of_call.get(str(d.get("call_id")))
            if step:
                reason = str(d.get("reason") or "")
                if reason.startswith("utterance v"):
                    reason = "What you said changed before it was sent."
                out.append(self._not_sent(step, t, reason))
        elif kind == "result_reused":
            step = str(d.get("step_id"))
            if step in self.actions:
                out.append(self._update(step, t, "reused", "Already done in this conversation",
                                        reason="The same action with the same details ran earlier; its result was reused."))
        elif kind == "arguments_rejected":
            out.append(self._emit({"kind": "note", "t": t, "text": (
                f"{title_of(str(d.get('tool')))} was not run: the model gave invalid details.")}))
        return [e for e in out if e is not None]

    def feed_all(self, records: Iterable[TraceRecord]) -> "Story":
        for rec in records:
            self.feed(rec)
        return self

    # ---------------------------------------------------------------- helpers
    def _emit(self, ev: Event) -> Event:
        self.events.append(ev)
        return ev

    def _set_state(self, who: str, t: int, state: str) -> Optional[Event]:
        if self._state.get(who) == state:
            return None
        self._state[who] = state
        return self._emit({"kind": "state", "t": t, "who": who, "state": state})

    def _update(self, step: str, t: int, state: str, status: str, reason: Optional[str] = None) -> Optional[Event]:
        a = self.actions.get(step)
        if a is None:
            return None
        if a["state"] in FINAL_STATES and state not in FINAL_STATES:
            return None                      # a late hold note after the end changes nothing
        if a["state"] == state and a["status"] == status:
            return None
        a.update(state=state, status=status, reason=reason if reason is not None else a["reason"])
        a["history"].append({"t": t, "state": state, "status": status})
        snapshot = copy.deepcopy(a)
        snapshot["t_updated"] = t
        # Replay order: an action's card sits where it was planned.
        if not any(e is a for e in self.events):
            self.events.append(a)
        return snapshot

    def _not_sent(self, step: str, t: int, why: str) -> Optional[Event]:
        a = self.actions.get(step)
        if a is None or a["state"] in ("sent", "done", "failed", "reused", "not_sent"):
            return None
        for keel_reason, said in _NOT_SENT_REASONS:
            if why.startswith(keel_reason):
                why = said
                break
        return self._update(step, t, "not_sent", "Not sent", reason=why or None)

    # ---------------------------------------------------------------- summary
    def hold_times_ms(self) -> list[int]:
        """For every action that went out: how long Keel held it after the model planned it."""
        out = []
        for a in self.actions.values():
            planned = next((h["t"] for h in a["history"] if h["state"] == "planned"), None)
            sent = next((h["t"] for h in a["history"] if h["state"] == "sent"), None)
            if planned is not None and sent is not None:
                out.append(sent - planned)
        return out

    def summary(self) -> dict[str, Any]:
        states = [a["state"] for a in self.actions.values()]
        users = [e for e in self.events if e["kind"] == "user"]
        holds = self.hold_times_ms()
        return {
            "session_id": self.session_id,
            "scenario": self.scenario,
            "synthetic": self.synthetic,
            "pipeline": self.pipeline,
            "duration_ms": self.ended_at,
            "last_t": self.last_t,
            "turns": len(users),
            "agent_turns": sum(e["kind"] == "agent" for e in self.events),
            "first_words": users[0]["text"] if users else None,
            "actions": len(states),
            "sent": sum(s in ("sent", "done", "failed") for s in states),
            "executed": states.count("done"),
            "failed": states.count("failed"),
            "not_sent": states.count("not_sent"),
            "reused": states.count("reused"),
            "held": sum(any(h["state"] == "holding" for h in a["history"]) for a in self.actions.values()),
            "hold_ms_median": statistics.median(holds) if holds else None,
        }
