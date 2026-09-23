"""Provisional wire contract between Keel and the evaluation harness.

Source: docs/Theme_5_Guide.pdf, section 3.1. The guide names the *kinds* of
messages on each queue but not their fields, so every field name below is our
placeholder. Each choice the real kit must confirm carries a TODO(kit) tag
whose ID is listed in docs/KIT_ASSUMPTIONS.md.

Inputs (guide 3.1): transcribed text chunks with end-of-turn markers, raw audio
clips (WAV), video frames (PNG), interruption signals, asynchronous tool
results, scenario tool manifests.

Outputs (guide 3.1): spoken fillers, non-blocking tool calls with explicit
call_id, cancellations, clarification requests, final responses carrying
structured state snapshots (intent and slot values).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

# TODO(kit): [K01] Identifier alphabet and length. The guide only says
# "valid identifiers"; we accept a conservative ASCII set.
Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]

# TODO(kit): [K02] Timestamps are integer milliseconds on the harness's
# virtual clock, measured from scenario start. The kit may use float seconds.
TimeMs = Annotated[int, Field(ge=0)]

# Tool names follow the common function-calling convention (letters, digits,
# underscore, plus '.' and '-' which appear in BFCL names such as
# "math.factorial"; see SOURCES.md, BFCL).
ToolName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_.\-]{0,127}$")]


class _Msg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

class _Event(_Msg):
    # TODO(kit): [K03] Envelope fields shared by every input event.
    event_id: Id
    session_id: Id
    ts_ms: TimeMs


class MediaRef(_Msg):
    """Exactly one of `uri` or `data_b64` must be present."""

    # TODO(kit): [K04] Whether media arrives inline (base64) or by path/URI.
    uri: Optional[str] = None
    data_b64: Optional[str] = None

    @model_validator(mode="after")
    def _one_source(self) -> "MediaRef":
        if (self.uri is None) == (self.data_b64 is None):
            raise ValueError("exactly one of uri or data_b64 is required")
        return self


class TextChunk(_Event):
    type: Literal["text"] = "text"
    text: str
    # TODO(kit): [K05] End-of-turn marker is a boolean on the chunk rather
    # than a separate event.
    end_of_turn: bool = False


class AudioClip(_Event):
    type: Literal["audio"] = "audio"
    media: MediaRef
    # TODO(kit): [K06] Whether the kit supplies clip duration / sample rate.
    duration_ms: Optional[TimeMs] = None
    end_of_turn: bool = False


class VideoFrame(_Event):
    type: Literal["frame"] = "frame"
    media: MediaRef


class Interrupt(_Event):
    # TODO(kit): [K07] Whether an interruption carries a payload (e.g. the
    # interrupting text) or is a bare signal followed by a text/audio event.
    type: Literal["interrupt"] = "interrupt"


class ToolResult(_Event):
    type: Literal["tool_result"] = "tool_result"
    call_id: Id
    # TODO(kit): [K08] Result status vocabulary. "timeout" is what drives the
    # reconciliation path; the kit may signal timeouts differently.
    status: Literal["ok", "error", "timeout"]
    result: JsonValue = None
    error: Optional[str] = None


class ToolSpec(BaseModel):
    """One tool in a scenario manifest.

    Unknown fields are *kept*, not rejected: explicit hints such as MCP's
    `annotations.readOnlyHint` (see SOURCES.md, MCP) are the first thing the
    manifest compiler looks for.
    """

    # TODO(kit): [K09] Manifest tool shape. We assume OpenAI/BFCL-style
    # {name, description, parameters: JSON Schema} plus arbitrary extras.
    model_config = ConfigDict(extra="allow", frozen=True)

    name: ToolName
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolManifest(_Event):
    type: Literal["manifest"] = "manifest"
    tools: list[ToolSpec]


Event = Annotated[
    Union[TextChunk, AudioClip, VideoFrame, Interrupt, ToolResult, ToolManifest],
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------

class _Action(_Msg):
    # TODO(kit): [K10] Envelope fields shared by every output action.
    action_id: Id
    session_id: Id
    ts_ms: TimeMs
    # TODO(kit): [K11] Link from an action to the input event that caused it.
    # The latency score ("time to first substantive spoken action following
    # user input or interruption", guide section 5) needs this or an
    # equivalent; the kit may compute it from timestamps alone.
    caused_by: Optional[Id] = None


class Speak(_Action):
    """A spoken filler: acknowledgment, progress narration, or hold phrase."""

    type: Literal["speak"] = "speak"
    text: str = Field(min_length=1)
    # TODO(kit): [K12] Whether the kit distinguishes filler sub-kinds. The
    # guide penalises "excessive fillers" (3.2.1), so we tag them ourselves.
    purpose: Literal["acknowledge", "progress", "status"]


class ToolCall(_Action):
    type: Literal["tool_call"] = "tool_call"
    call_id: Id
    tool: ToolName
    arguments: dict[str, JsonValue]


class Cancel(_Action):
    type: Literal["cancel"] = "cancel"
    call_id: Id
    reason: str = ""


class Clarify(_Action):
    type: Literal["clarify"] = "clarify"
    text: str = Field(min_length=1)
    slot: Optional[str] = None


class StateSnapshot(_Msg):
    # TODO(kit): [K13] Snapshot shape: a single intent string plus a flat
    # slot->value map. The kit may nest slots or allow multiple intents.
    intent: Optional[str]
    slots: dict[str, JsonValue]


class FinalResponse(_Action):
    type: Literal["final"] = "final"
    text: str = Field(min_length=1)
    snapshot: StateSnapshot


Action = Annotated[
    Union[Speak, ToolCall, Cancel, Clarify, FinalResponse],
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)
ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)

EventT = Union[TextChunk, AudioClip, VideoFrame, Interrupt, ToolResult, ToolManifest]
ActionT = Union[Speak, ToolCall, Cancel, Clarify, FinalResponse]
