"""Replays interpretations recorded in scenario data.

Test/eval infrastructure only: this stands in for the slow path (LLM) so the
kernel can be exercised deterministically with no model. It knows nothing
about any scenario; it looks interpretations up by the input event_id given
in the scenario's own data.
"""

from __future__ import annotations

from typing import Callable, Mapping

from keel.kernel.clock import VirtualClock
from keel.kernel.internal import Interpretation, InternalEvent
from keel.protocol.provisional import EventT


class ScriptedInterpreter:
    def __init__(self, clock: VirtualClock, script: Mapping[str, list[tuple[int, Interpretation]]]):
        """script: input event_id -> [(delay_ms, interpretation), ...]"""
        self.clock = clock
        self.script = script
        self.submitted: list[str] = []

    def submit(self, event: EventT, view: object, post: Callable[[InternalEvent], None]) -> None:
        self.submitted.append(event.event_id)
        for delay, interp in self.script.get(event.event_id, []):
            stamped = interp if interp.caused_by else interp.model_copy(update={"caused_by": event.event_id})
            self.clock.call_later(delay, lambda s=stamped: post(s))
