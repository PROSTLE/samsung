import asyncio
import json

import pytest
from pydantic import ValidationError

from keel.protocol.adapter import (
    END_OF_STREAM,
    KitProtocolError,
    ProvisionalCodec,
    QueueAdapter,
    dumps_action,
)
from keel.protocol.ids import IdFactory
from keel.protocol.provisional import (
    ACTION_ADAPTER,
    EVENT_ADAPTER,
    Cancel,
    Clarify,
    FinalResponse,
    Speak,
    StateSnapshot,
    TextChunk,
    ToolCall,
    ToolManifest,
    ToolResult,
)

ENV = {"session_id": "s1", "ts_ms": 10}


def ev(**kw):
    return {"event_id": "e1", **ENV, **kw}


def act(**kw):
    return {"action_id": "a1", **ENV, **kw}


EVENTS = [
    ev(type="text", text="book it for friday", end_of_turn=False),
    ev(type="audio", media={"uri": "clips/x.wav"}, duration_ms=1200),
    ev(type="frame", media={"data_b64": "iVBORw0KGgo="}),
    ev(type="interrupt"),
    ev(type="tool_result", call_id="s1:call-00001", status="timeout"),
    ev(type="manifest", tools=[{"name": "book", "parameters": {"type": "object"}}]),
]

ACTIONS = [
    act(type="speak", text="One moment.", purpose="acknowledge"),
    act(type="tool_call", call_id="c1", tool="search_direct_flight", arguments={"date": "2024-05-01"}),
    act(type="cancel", call_id="c1", reason="slot date changed v1->v2"),
    act(type="clarify", text="Washer or dryer?", slot="appliance"),
    act(type="final", text="Booked.", snapshot={"intent": "book", "slots": {"date": "2024-05-02"}}),
]


@pytest.mark.parametrize("raw", EVENTS, ids=lambda r: r["type"])
def test_every_event_kind_round_trips(raw):
    parsed = EVENT_ADAPTER.validate_python(raw)
    again = EVENT_ADAPTER.validate_json(json.dumps(parsed.model_dump(mode="json")))
    assert again == parsed


@pytest.mark.parametrize("raw", ACTIONS, ids=lambda r: r["type"])
def test_every_action_kind_round_trips(raw):
    parsed = ACTION_ADAPTER.validate_python(raw)
    assert ACTION_ADAPTER.validate_json(dumps_action(parsed)) == parsed


def test_unknown_fields_are_rejected_on_envelopes():
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(ev(type="interrupt", surprise=1))
    with pytest.raises(ValidationError):
        ACTION_ADAPTER.validate_python(act(type="cancel", call_id="c1", extra=True))


def test_tool_spec_keeps_unknown_hints_for_the_compiler():
    m = EVENT_ADAPTER.validate_python(
        ev(type="manifest", tools=[{"name": "reroute", "annotations": {"readOnlyHint": False}}])
    )
    assert isinstance(m, ToolManifest)
    assert m.tools[0].model_extra == {"annotations": {"readOnlyHint": False}}


@pytest.mark.parametrize("bad", ["", " c1", "c 1", "c1\n", "x" * 129, "-leading"])
def test_invalid_identifiers_are_rejected(bad):
    with pytest.raises(ValidationError):
        Cancel(action_id="a1", call_id=bad, **ENV)


def test_negative_timestamps_rejected():
    with pytest.raises(ValidationError):
        TextChunk(event_id="e1", session_id="s1", ts_ms=-1, text="hi")


def test_media_requires_exactly_one_source():
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(ev(type="frame", media={}))
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(ev(type="frame", media={"uri": "a.png", "data_b64": "AA=="}))


def test_final_response_requires_snapshot():
    with pytest.raises(ValidationError):
        ACTION_ADAPTER.validate_python(act(type="final", text="done"))


def test_empty_spoken_text_rejected():
    with pytest.raises(ValidationError):
        Speak(action_id="a1", text="", purpose="acknowledge", **ENV)


def test_codec_refuses_to_encode_an_action_that_skipped_validation():
    bogus = ToolCall.model_construct(
        action_id="a1", session_id="s1", ts_ms=0, caused_by=None,
        type="tool_call", call_id="bad id", tool="t", arguments={},
    )
    with pytest.raises(ValidationError):
        ProvisionalCodec().encode_action(bogus)


def test_codec_wraps_decode_errors():
    with pytest.raises(KitProtocolError):
        ProvisionalCodec().decode_event({"type": "text"})
    with pytest.raises(KitProtocolError):
        ProvisionalCodec().decode_event("{not json")


def test_queue_adapter_moves_models_over_two_queues():
    async def run():
        inbox, outbox = asyncio.Queue(), asyncio.Queue()
        adapter = QueueAdapter(inbox, outbox)
        await inbox.put(json.dumps(EVENTS[0]))
        await inbox.put(EVENTS[4])
        await inbox.put(END_OF_STREAM)
        got = [await adapter.receive() for _ in range(3)]
        await adapter.send(
            FinalResponse(action_id="a9", text="ok", snapshot=StateSnapshot(intent=None, slots={}), **ENV)
        )
        return got, await outbox.get()

    got, wire = asyncio.run(run())
    assert isinstance(got[0], TextChunk) and isinstance(got[1], ToolResult) and got[2] is None
    assert wire["type"] == "final" and wire["snapshot"] == {"intent": None, "slots": {}}


def test_id_factory_is_deterministic_and_valid():
    a, b = IdFactory("s1"), IdFactory("s1")
    seq_a = [a.new("call"), a.new("call"), a.new("act")]
    seq_b = [b.new("call"), b.new("call"), b.new("act")]
    assert seq_a == seq_b == ["s1:call-00001", "s1:call-00002", "s1:act-00001"]
    for i in seq_a:
        Cancel(action_id=i, call_id=i, **ENV)  # validates as Id


def test_clarify_slot_optional():
    assert Clarify(action_id="a1", text="Which one?", **ENV).slot is None
