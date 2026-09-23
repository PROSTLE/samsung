"""Goals: what the agent is trying to do, as data.

A Goal is a list of Steps. Each Step names a tool and says where each argument
comes from: a session slot, a constant, or the result of an earlier step.
Because arguments are *bound* rather than copied, the kernel knows exactly
which slot versions a call read, which is what makes precise invalidation
possible (a call is stale iff something it read has changed).

The slow path (LLM) emits Goals as schema-constrained JSON; these models are
that schema.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SlotRef(_Frozen):
    slot: str


class Const(_Frozen):
    const: JsonValue


class ResultRef(_Frozen):
    """Value at `path` inside the result of step `result`."""

    result: str
    path: tuple[Union[str, int], ...] = ()


Binding = Annotated[Union[SlotRef, Const, ResultRef], Field(union_mode="left_to_right")]


class Step(_Frozen):
    step_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    tool: str
    bindings: dict[str, Binding] = Field(default_factory=dict)
    # Steps that must succeed first even though no argument reads them.
    after: tuple[str, ...] = ()


class Goal(_Frozen):
    intent: str
    steps: tuple[Step, ...] = ()
    # Final-response template. Placeholders: {slot_name} or {step_id.path.to.value}.
    reply: Optional[str] = None


def result_key(step_id: str) -> str:
    """Pseudo-slot name under which a step's result version is tracked."""
    return f"@{step_id}"


def dig(value: Any, path: tuple[Union[str, int], ...]) -> tuple[bool, Any]:
    for part in path:
        if isinstance(value, dict) and isinstance(part, str) and part in value:
            value = value[part]
        elif isinstance(value, list) and isinstance(part, int) and -len(value) <= part < len(value):
            value = value[part]
        else:
            return False, None
    return True, value
