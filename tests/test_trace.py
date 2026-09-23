import io
import json

import pytest
from pydantic import ValidationError

from keel.config import load_config
from keel.kernel.clock import VirtualClock
from keel.protocol.provisional import Cancel, Speak, TextChunk, ToolCall
from keel.trace import TraceWriter, read_trace


def deterministic_config():
    cfg = load_config()
    return cfg.model_copy(update={"trace": cfg.trace.model_copy(update={"record_wall_time": False})})


def write_sample(sink, config):
    clock = VirtualClock()
    with TraceWriter(sink, clock=clock, session_id="s1", config=config, synthetic=True) as tr:
        tr.event_in(TextChunk(event_id="e1", session_id="s1", ts_ms=0, text="book friday"))
        clock.advance_to(5)
        tr.action_out(ToolCall(action_id="a1", session_id="s1", ts_ms=5, call_id="c1",
                               tool="book", arguments={"day": "fri"}))
        clock.advance_to(55)
        tr.note("slot_changed", slot="day", old_version=1, new_version=2)
        tr.action_out(Cancel(action_id="a2", session_id="s1", ts_ms=55, call_id="c1", reason="day v1->v2"))


def test_trace_round_trips_and_carries_header(tmp_path):
    path = tmp_path / "t.jsonl"
    write_sample(path, load_config())
    recs = list(read_trace(path))
    assert [r.kind for r in recs] == ["session_start", "text", "tool_call", "slot_changed", "cancel", "session_end"]
    assert [r.seq for r in recs] == list(range(6))
    assert [r.ts_ms for r in recs] == [0, 0, 5, 55, 55, 55]
    head = recs[0].data
    assert head["synthetic"] is True
    assert head["schema_version"] == load_config().protocol.schema_version
    assert len(head["config_digest"]) == 64
    assert all(r.wall_ns is not None for r in recs)


def test_trace_is_byte_identical_without_wall_time():
    a, b = io.StringIO(), io.StringIO()
    write_sample(a, deterministic_config())
    write_sample(b, deterministic_config())
    assert a.getvalue() == b.getvalue()
    assert all(json.loads(line)["wall_ns"] is None for line in a.getvalue().splitlines())


def test_trace_refuses_unvalidated_actions():
    tr = TraceWriter(io.StringIO(), clock=VirtualClock(), session_id="s1",
                     config=deterministic_config(), synthetic=False)
    bogus = Speak.model_construct(action_id="a1", session_id="s1", ts_ms=0, caused_by=None,
                                  type="speak", text="", purpose="acknowledge")
    with pytest.raises(ValidationError):
        tr.action_out(bogus)


def test_closed_trace_rejects_writes():
    tr = TraceWriter(io.StringIO(), clock=VirtualClock(), session_id="s1",
                     config=deterministic_config(), synthetic=False)
    tr.close()
    tr.close()  # idempotent
    with pytest.raises(RuntimeError):
        tr.note("late")
