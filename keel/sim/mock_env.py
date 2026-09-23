"""Deterministic mock tool environment on the virtual clock.

Mirrors what the guide says the kit's mock environment does (§4:
"deterministic latency and fault injection"). Behaviour comes entirely from
ToolBehaviour data supplied by a scenario, never from code that knows about a
particular scenario.

The World records which state-changing calls actually took effect, so tests
can check exactly-once against ground truth instead of against Keel's own
bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

from pydantic import JsonValue

from keel.kernel.clock import TimerHandle, VirtualClock
from keel.kernel.ledger import canonical_json
from keel.protocol.ids import IdFactory
from keel.protocol.provisional import ActionT, Cancel, ToolCall, ToolResult

Outcome = Literal["ok", "error", "timeout", "drop"]


@dataclass
class World:
    effects: list[tuple[int, str, dict]] = field(default_factory=list)

    def record(self, t: int, tool: str, args: dict) -> None:
        self.effects.append((t, tool, args))

    def count(self, tool: str, args: dict) -> int:
        key = canonical_json(args)
        return sum(1 for _, t, a in self.effects if t == tool and canonical_json(a) == key)

    def happened(self, tool: str, **match: JsonValue) -> bool:
        return any(t == tool and all(a.get(k) == v for k, v in match.items()) for _, t, a in self.effects)


Responder = Callable[[dict, World], tuple[Outcome, JsonValue]]


@dataclass
class ToolBehaviour:
    latency_ms: int = 100
    outcome: Outcome = "ok"
    result: JsonValue = None
    # Does the world change when this tool runs? (state-changing tools)
    effect: bool = False
    # If the call is cancelled before it completes, is the effect prevented?
    honors_cancel: bool = True
    # Deliver the result twice (fault injection).
    duplicate: bool = False
    # Dynamic response, e.g. a status tool that reads the World.
    respond: Optional[Responder] = None


class MockEnv:
    def __init__(
        self,
        clock: VirtualClock,
        deliver: Callable[[ToolResult], None],
        behaviours: dict[str, ToolBehaviour],
        session_id: str,
    ) -> None:
        self.clock = clock
        self.deliver = deliver
        self.behaviours = behaviours
        self.session_id = session_id
        self.world = World()
        self.ids = IdFactory(f"{session_id}-env")
        self._pending: dict[str, TimerHandle] = {}
        self.calls: list[ToolCall] = []
        self.cancels: list[Cancel] = []

    def receive(self, action: ActionT) -> None:
        if isinstance(action, ToolCall):
            self.calls.append(action)
            b = self.behaviours.get(action.tool, ToolBehaviour())
            self._pending[action.call_id] = self.clock.call_later(
                b.latency_ms, lambda: self._complete(action, b)
            )
        elif isinstance(action, Cancel):
            self.cancels.append(action)
            b = next((self.behaviours.get(c.tool, ToolBehaviour()) for c in self.calls
                      if c.call_id == action.call_id), ToolBehaviour())
            h = self._pending.get(action.call_id)
            if h is not None and b.honors_cancel:
                h.cancel()
                del self._pending[action.call_id]

    def _complete(self, call: ToolCall, b: ToolBehaviour) -> None:
        self._pending.pop(call.call_id, None)
        if b.respond is not None:
            outcome, result = b.respond(dict(call.arguments), self.world)
        else:
            outcome, result = b.outcome, b.result
        if b.effect and outcome in ("ok", "timeout", "drop"):
            # timeout/drop model the ambiguous case: it ran, we never heard.
            self.world.record(self.clock.now_ms(), call.tool, dict(call.arguments))
        if outcome == "drop":
            return
        ev = ToolResult(
            event_id=self.ids.new("res"), session_id=self.session_id, ts_ms=self.clock.now_ms(),
            call_id=call.call_id, status=outcome,
            result=result if outcome == "ok" else None,
            error="mock error" if outcome == "error" else None,
        )
        self.deliver(ev)
        if b.duplicate:
            self.clock.call_later(1, lambda: self.deliver(ev.model_copy(update={"event_id": self.ids.new("res")})))
