"""Internal events: things workers and timers post back to the kernel.

Workers (ASR, OCR, vision, the LLM slow path) never touch session state. They
post one of these, and the kernel applies it inside handle(), the only
place state changes.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from keel.kernel.plan import Goal

Source = Literal["text", "audio", "frame", "tool", "system"]


class _Internal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SlotProposal(_Internal):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    value: JsonValue
    source: Source
    confidence: float = Field(ge=0.0, le=1.0)
    # True when the user explicitly corrected this slot ("no, Pune").
    correction: bool = False


class Interpretation(_Internal):
    """What a worker understood from one or more inputs."""

    type: Literal["interpretation"] = "interpretation"
    caused_by: Optional[str] = None
    proposals: tuple[SlotProposal, ...] = ()
    # None means "no change to the goal". clear_goal drops the current goal.
    goal: Optional[Goal] = None
    clear_goal: bool = False
    # A direct answer that needs no tools; becomes a final response.
    reply: Optional[str] = None


class TimerFired(_Internal):
    type: Literal["timer"] = "timer"
    purpose: Literal["fence", "call_timeout", "ack", "progress", "turn_stale"]
    ref: Optional[str] = None
    # Generation counter so a superseded timer can recognise itself.
    gen: int = 0


InternalEvent = Interpretation | TimerFired
