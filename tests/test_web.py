"""The web app: the story told from real gate traces, results, the live publisher, the server."""

import asyncio
import base64
import json

import pytest

from keel.trace import TraceRecord
from keel.web.results import list_runs, run_detail, summarize_run
from keel.web.story import Story, summarize_result, title_of
from tests.test_livekit_gate import Harness, cfg


def _oh_wait_session(tmp_path):
    """The travel_10 shape through the real gate: a call from "oh, wait" is held and dropped."""
    async def main():
        async with Harness(tmp_path, config=cfg(repair_wait_ms=300)) as h:
            h.say("trains to Pune on Friday")
            h.say("oh, wait.")
            pending = asyncio.ensure_future(h.gate.call("search_trains", {"city": "Pune", "date": "Friday"}))
            await asyncio.sleep(0.15)
            h.say("make it Saturday instead")
            await pending
            await h.gate.call("search_trains", {"city": "Pune", "date": "Saturday"})
            h.gate.note("agent_said", text="There is one train to Pune on Saturday.")
            return h.records()
    return asyncio.run(main())


def test_the_story_says_what_happened_to_each_action(tmp_path):
    story = Story().feed_all(_oh_wait_session(tmp_path))
    said = [(e["kind"], e["text"]) for e in story.events if e["kind"] in ("user", "agent")]
    assert said == [("user", "trains to Pune on Friday"), ("user", "oh, wait."),
                    ("user", "make it Saturday instead"), ("agent", "There is one train to Pune on Saturday.")]
    first, second = story.actions.values()
    assert first["title"] == "Search trains" and first["args"] == [["City", "Pune"], ["Date", "Friday"]]
    assert first["state"] == "not_sent" and first["reason"]
    assert [h["status"] for h in first["history"]][:2] == ["Planned by the model", "Waiting for the correction you announced"]
    assert second["state"] == "done" and [h["state"] for h in second["history"]][-2:] == ["sent", "done"]
    notes = [e["text"] for e in story.events if e["kind"] == "note"]
    assert notes and "oh, wait." in notes[0]
    assert story.summary()["sent"] == 1 and story.summary()["not_sent"] == 1


def test_live_updates_are_snapshots_and_each_change_is_sent_once(tmp_path):
    story = Story()
    updates = [e for rec in _oh_wait_session(tmp_path) for e in story.feed(rec) if e["kind"] == "action"]
    states = [(u["id"], u["state"]) for u in updates]
    collapsed = [s for i, s in enumerate(states) if i == 0 or s != states[i - 1]]
    assert collapsed == [("c1", "planned"), ("c1", "holding"), ("c1", "not_sent"),
                         ("c2", "planned"), ("c2", "holding"), ("c2", "sent"), ("c2", "done")]
    # While held, the reason changes as it happens (the user starts talking, stops).
    c1_holds = [u["status"] for u in updates if u["id"] == "c1" and u["state"] == "holding"]
    assert "Waiting: you are speaking" in c1_holds
    assert len(set(map(json.dumps, updates))) == len(updates)   # no update sent twice
    assert updates[0]["state"] == "planned"          # a snapshot, not the live object


def test_titles_and_result_lines():
    assert title_of("search_flights") == "Search flights"
    assert summarize_result({"flights": [{"id": 1}], "status": "success"}) == "1 flight returned"
    assert summarize_result({"status": "success", "order_id": "A1", "total": 99.0}) == "Order id: A1, Total: 99"


# ---------------------------------------------------------------- results
def _run(tmp_path, complete=True):
    run = tmp_path / "results" / "fdb_v3" / "20260925T142230Z_keel_gemini_realtime"
    (run / "per_scenario").mkdir(parents=True)
    (run / "run_info.txt").write_text("provider_label: keel_gemini_realtime\njudge: none (rule-based)\n", encoding="utf-8")
    (run / "inference.log").write_text("[1/1] Processing ...\n", encoding="utf-8")
    (run / "per_scenario" / "travel_10.json").write_text(json.dumps({"example_id": "travel_10", "room_name": "eval-1",
                                                                     "title": "Date Correction"}), encoding="utf-8")
    if complete:
        (run / "p_pass_rate_report.json").write_text(json.dumps({
            "total_scenarios": 1, "overall_pass_rate": 0.0, "passed": 0, "failed": 1,
            "by_disfluency_feature": {"SELF_CORRECTION": 0.0},
            "scenario_results": [{"scenario_id": "travel_10", "title": "Date Correction", "passed": False,
                                  "failure_reason": "Wrong arguments"}]}), encoding="utf-8")
        (run / "p_evaluation_report.json").write_text(json.dumps({
            "turn_taking": {"turn_take_rate": 1.0}, "by_metric": {"tool_selection_acc": 1.0, "argument_acc": 0.0,
                                                                  "response_qual": None}}), encoding="utf-8")
    return run


def test_a_run_is_read_from_fdb_v3s_own_reports(tmp_path):
    d = run_detail(_run(tmp_path))
    assert (d["pass_rate"], d["tool_selection"], d["turn_take_rate"], d["response_quality"]) == (0.0, 1.0, 1.0, None)
    assert d["judge"].startswith("none") and d["complete"]
    assert d["scenarios_detail"] == [{"scenario": "travel_10", "title": "Date Correction", "domain": None,
                                      "passed": False, "failure": "Wrong arguments", "turn_taken": None,
                                      "latency_s": None, "session": "eval-1"}]


def test_a_run_in_progress_has_no_invented_scores(tmp_path):
    s = summarize_run(_run(tmp_path, complete=False))
    assert s["complete"] is False and s["pass_rate"] is None and s["scenarios"] == 1
    assert s["status"] == "running"                       # its log was just written
    assert [r["id"] for r in list_runs(tmp_path / "results" / "fdb_v3")] == ["20260925T142230Z_keel_gemini_realtime"]


def test_a_run_whose_logs_went_quiet_without_reports_stopped(tmp_path):
    import os

    run = _run(tmp_path, complete=False)
    os.utime(run / "inference.log", (1, 1))
    (run / "NOTE.md").write_text("written later\n", encoding="utf-8")   # a note is not activity
    s = summarize_run(run)
    assert s["status"] == "stopped" and s["pass_rate"] is None
    assert summarize_run(_run(tmp_path / "b"))["status"] == "complete"


# ---------------------------------------------------------------- live publisher
class _Participant:
    def __init__(self, identity):
        self.identity = identity


class _Local:
    def __init__(self):
        self.sent = []

    async def send_text(self, text, *, topic, destination_identities):
        self.sent.append((topic, destination_identities, json.loads(text)))


class _Room:
    def __init__(self, identities):
        self.remote_participants = {i: _Participant(i) for i in identities}
        self.local_participant = _Local()

    def isconnected(self):
        return True


def test_the_live_story_goes_only_to_web_participants(tmp_path):
    from keel.kernel.clock import MonotonicClock
    from keel.trace import TraceWriter
    from keel.web.live import attach
    from keel.web.story import STORY_TOPIC

    async def main():
        loop = asyncio.get_running_loop()
        room = _Room(["wav-file-user", "keel-web-1a2b"])
        trace = TraceWriter(tmp_path / "t.jsonl", clock=MonotonicClock(loop), session_id="s", config=cfg(), synthetic=True)
        attach(trace, room, loop)
        trace.note("llm_tool_call", step_id="c1", tool="search_trains", arguments={"city": "Pune"})
        trace.note("tool_policy", tool="x")                    # not a story event: nothing sent
        await asyncio.sleep(0.05)
        return room.local_participant.sent

    sent = asyncio.run(main())
    assert len(sent) == 1
    topic, to, events = sent[0]
    assert topic == STORY_TOPIC and to == ["keel-web-1a2b"]
    assert events[0]["title"] == "Search trains" and events[0]["state"] == "planned"


def test_a_failing_listener_never_breaks_the_trace(tmp_path):
    from keel.kernel.clock import MonotonicClock
    from keel.trace import TraceWriter, read_trace

    async def main():
        trace = TraceWriter(tmp_path / "t.jsonl", clock=MonotonicClock(asyncio.get_running_loop()), session_id="s",
                            config=cfg(), synthetic=True)
        trace.listeners.append(lambda rec: 1 / 0)
        trace.note("a")
        trace.note("b")
        trace.close()
        return trace

    trace = asyncio.run(main())
    assert trace.listeners == []
    assert [r.kind for r in read_trace(tmp_path / "t.jsonl")] == ["session_start", "a", "b", "session_end"]


# ---------------------------------------------------------------- server
def _jwt_payload(token):
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_the_server_api(tmp_path, monkeypatch):
    pytest.importorskip("aiohttp")
    pytest.importorskip("livekit.api")
    from aiohttp.test_utils import TestClient, TestServer

    from keel.web.server import make_app

    _run(tmp_path)
    traces = tmp_path / "traces" / "livekit"
    traces.mkdir(parents=True)
    records = _oh_wait_session(tmp_path)
    (traces / "eval-1.jsonl").write_text(
        "".join(json.dumps(r.model_dump(mode="json")) + "\n" for r in records), encoding="utf-8")

    async def main():
        async with TestClient(TestServer(make_app(tmp_path))) as c:
            sessions = await (await c.get("/api/sessions")).json()
            one = await (await c.get("/api/sessions/eval-1")).json()
            missing = (await c.get("/api/sessions/nope")).status
            escape = (await c.get("/api/runs/..%2F..%2Fetc")).status
            runs = await (await c.get("/api/runs")).json()
            monkeypatch.delenv("LIVEKIT_URL", raising=False)
            no_keys = (await c.post("/api/token")).status
            monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
            monkeypatch.setenv("LIVEKIT_API_KEY", "key")
            monkeypatch.setenv("LIVEKIT_API_SECRET", "secret-secret-secret-secret-secret")
            token = await (await c.post("/api/token")).json()
            index = await (await c.get("/")).text()
            return sessions, one, missing, escape, runs, no_keys, token, index

    sessions, one, missing, escape, runs, no_keys, token, index = asyncio.run(main())
    assert [s["id"] for s in sessions] == ["eval-1"] and sessions[0]["title"] == "Date Correction"
    assert one["summary"]["not_sent"] == 1 and any(e["kind"] == "action" for e in one["events"])
    assert missing == 404 and escape == 404
    assert runs[0]["provider"] == "keel_gemini_realtime"
    assert no_keys == 503
    claims = _jwt_payload(token["token"])
    assert claims["sub"].startswith("keel-web-") and claims["video"]["room"] == token["room"]
    assert token["room"].startswith("keel-live-") and "<main id=\"view\"" in index


def test_record_model_is_the_traces_own():
    # The story reads TraceRecord objects exactly as the trace writes them.
    rec = TraceRecord(seq=0, ts_ms=5, wall_ns=None, dir="note", kind="agent_said", data={"text": "Hi."})
    assert Story().feed(rec) == [{"kind": "agent", "t": 5, "text": "Hi."}]


# ---------------------------------------------------------------- replay: states, anchors, verdicts
def test_the_story_carries_voice_activity_and_agent_states(tmp_path):
    story = Story().feed_all(_oh_wait_session(tmp_path))
    user = [e["state"] for e in story.events if e["kind"] == "state" and e["who"] == "user"]
    # three turns, each: speaking, then quiet; an unchanged state is not sent again
    assert user == ["speaking", "quiet"] * 3
    rec = TraceRecord(seq=1, ts_ms=7, wall_ns=None, dir="note", kind="agent_state", data={"state": "speaking"})
    s = Story()
    assert s.feed(rec) == [{"kind": "state", "t": 7, "who": "agent", "state": "speaking"}]
    assert s.feed(rec) == []
    keel = TraceRecord(seq=2, ts_ms=9, wall_ns=None, dir="note", kind="filler_said",
                       data={"step_id": "c1", "text": "One moment.", "purpose": "acknowledge"})
    assert s.feed(keel) == [{"kind": "keel", "t": 9, "text": "One moment.", "purpose": "acknowledge"}]


def test_hold_times_are_measured_from_plan_to_dispatch(tmp_path):
    story = Story().feed_all(_oh_wait_session(tmp_path))
    holds = story.hold_times_ms()
    assert len(holds) == 1 and holds[0] >= 0                  # only the call that went out
    s = story.summary()
    assert s["held"] == 2 and s["executed"] == 1 and s["hold_ms_median"] == holds[0]


def test_a_trace_with_wall_time_records_its_unix_anchor(tmp_path):
    import time as _time

    from keel.config import with_overrides
    from keel.kernel.clock import MonotonicClock
    from keel.trace import TraceWriter, read_trace

    async def main():
        c = with_overrides(cfg(), trace={"record_wall_time": True})
        before = _time.time_ns() // 1_000_000
        with TraceWriter(tmp_path / "t.jsonl", clock=MonotonicClock(asyncio.get_running_loop()),
                         session_id="s", config=c, synthetic=True):
            pass
        return before, _time.time_ns() // 1_000_000

    before, after = asyncio.run(main())
    head = next(read_trace(tmp_path / "t.jsonl"))
    assert before <= head.data["unix_ms"] <= after
    assert Story().feed_all(read_trace(tmp_path / "t.jsonl")).unix_ms_at_zero == head.data["unix_ms"] - head.ts_ms


def test_audio_is_lined_up_by_clock_else_by_the_first_executed_call():
    from keel.web.results import audio_offset_ms

    rec = {"stream_start_time": 1790349700.21871,
           "tool_calls": [{"function": "get_card_benefits", "timestamp_start": 21.19}]}
    # clock anchor: the stream start minus the Unix time of the trace's t = 0
    assert audio_offset_ms(rec, 1790349698000, []) == (2219, "clock")
    # tool-call anchor (finance_21 in our Gemini run): dispatched at trace 23262 ms,
    # 21.19 s after the stream started in FDB-v3's log
    assert audio_offset_ms(rec, None, [("get_card_benefits", 23262)]) == (2072, "tool_call")
    assert audio_offset_ms(rec, None, [("other_tool", 100)]) == (None, None)
    assert audio_offset_ms({"tool_calls": []}, None, []) == (None, None)


def test_recordings_and_verdicts_are_read_from_fdb_v3s_files(tmp_path):
    from keel.web.results import fdb_recordings, scenario_verdict

    data = tmp_path / "fdb_v3_data_released" / "travel_10_abc"
    data.mkdir(parents=True)
    (data / "result_keel_gemini_realtime.json").write_text(json.dumps({
        "example_id": "travel_10", "room_name": "eval-1", "stream_start_time": 100.0,
        "actual_tool_calls": [{"function": "search_flights", "timestamp_start": 1.5}]}), encoding="utf-8")
    (data / "input_mono.wav").write_bytes(b"RIFF")
    recs = fdb_recordings(tmp_path / "fdb_v3_data_released")
    assert recs["eval-1"]["user"].name == "input_mono.wav" and recs["eval-1"]["agent"] is None
    assert recs["eval-1"]["provider"] == "keel_gemini_realtime"
    assert fdb_recordings(tmp_path / "missing") == {}

    run = tmp_path / "run"
    run.mkdir()
    (run / "p_pass_rate_report.json").write_text(json.dumps({"scenario_results": [{
        "scenario_id": "travel_10", "passed": False, "failure_reason": "Wrong arguments for: ['search_flights']",
        "disfluency": ["SELF_CORRECTION"],
        "checks": {"tool_selection": {"expected": ["search_flights"], "actual": ["search_flights"],
                                      "missing": [], "unexpected": []},
                   "argument_accuracy": {"details": [{"function": "search_flights", "passed": False,
                                                      "expected_args": {"date": "October 7"},
                                                      "actual_args": {"date": "2026-10-07"}}]}}}]}),
        encoding="utf-8")
    v = scenario_verdict(run, "travel_10")
    assert v["passed"] is False and v["failure"] == "Wrong arguments: Search flights"
    assert v["arguments"][0]["expected"] == {"date": "October 7"}
    assert v["arguments"][0]["actual"] == {"date": "2026-10-07"}
    assert scenario_verdict(run, "nope") is None


def test_sources_and_statuses():
    pytest.importorskip("aiohttp")
    from keel.web.server import source_of

    assert source_of("eval-1a2b", "fdb_v3", set()) == "benchmark"
    assert source_of("room-x", "fdb_v3", {"room-x"}) == "benchmark"
    assert source_of("keel-live-1a2b", "fdb_v3", set()) == "live"
    assert source_of("keel-live-1a2b", "show_and_fix", set()) == "show_and_fix"
    assert source_of("playground-7", "fdb_v3", set()) == "other"


def test_the_server_serves_replays_overview_system_and_audio(tmp_path, monkeypatch):
    pytest.importorskip("aiohttp")
    pytest.importorskip("livekit.api")
    import os

    from aiohttp.test_utils import TestClient, TestServer

    from keel.web.server import make_app

    _run(tmp_path)
    records = _oh_wait_session(tmp_path)
    # recorded as a real session (the harness marks its traces synthetic, which the overview leaves out)
    records = [records[0].model_copy(update={"data": {**records[0].data, "synthetic": False}}), *records[1:]]
    last = records[-1]
    closed = records + [TraceRecord(seq=last.seq + 1, ts_ms=last.ts_ms, wall_ns=None, dir="meta", kind="session_end",
                                    data={"records": last.seq + 2})]
    lines = "".join(json.dumps(r.model_dump(mode="json")) + "\n" for r in closed)
    for d, name in (("traces/livekit", "eval-1"), ("traces/show_and_fix", "keel-live-5")):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
        (tmp_path / d / f"{name}.jsonl").write_text(lines, encoding="utf-8")
    # a killed session: no session_end record, last written long ago
    cut = tmp_path / "traces" / "livekit" / "eval-2.jsonl"
    cut.write_text("".join(json.dumps(r.model_dump(mode="json")) + "\n" for r in records), encoding="utf-8")
    os.utime(cut, (1, 1))
    # FDB-v3's recordings for eval-1, lined up by its executed call
    fdb = tmp_path / "fdb" / "v3"
    scen = fdb / "fdb_v3_data_released" / "travel_10_abc"
    scen.mkdir(parents=True)
    story = Story().feed_all(records)
    sent_t = next(h["t"] for a in story.actions.values() for h in a["history"] if h["state"] == "sent")
    (scen / "result_keel_gemini_realtime.json").write_text(json.dumps({
        "example_id": "travel_10", "room_name": "eval-1", "title": "Date Correction", "stream_start_time": 5.0,
        "actual_tool_calls": [{"function": "search_trains", "timestamp_start": 0.25}]}), encoding="utf-8")
    (scen / "input_mono.wav").write_bytes(b"RIFF-user")
    (scen / "output_keel_gemini_realtime.wav").write_bytes(b"RIFF-agent")
    monkeypatch.setenv("KEEL_FDB_DIR", str(fdb))
    monkeypatch.setenv("GOOGLE_API_KEY", "g-secret-value")

    async def main():
        async with TestClient(TestServer(make_app(tmp_path))) as c:
            rows = await (await c.get("/api/sessions")).json()
            one = await (await c.get("/api/sessions/eval-1")).json()
            user = await (await c.get("/api/sessions/eval-1/audio/user")).read()
            bad = (await c.get("/api/sessions/eval-1/audio/other")).status
            overview = await (await c.get("/api/overview")).json()
            system = await (await c.get("/api/system")).text()
            examples = await (await c.get("/api/examples")).json()
            return rows, one, user, bad, overview, system, examples

    rows, one, user, bad, overview, system, examples = asyncio.run(main())
    by_id = {r["id"]: r for r in rows}
    assert by_id["eval-1"]["source"] == "benchmark" and by_id["eval-1"]["status"] == "complete"
    assert by_id["keel-live-5"]["source"] == "show_and_fix"      # Show & Fix's own trace directory is read
    assert by_id["eval-2"]["status"] == "incomplete" and by_id["eval-2"]["duration_ms"] is None
    audio = one["audio"]
    assert audio["available"] and audio["anchor"] == "tool_call" and audio["offset_ms"] == sent_t - 250
    assert audio["user"] == "/api/sessions/eval-1/audio/user" and user == b"RIFF-user" and bad == 404
    assert overview["not_sent"] >= 1 and overview["demo_session"] in by_id and overview["sessions"] == len(rows)
    assert '"gemini": true' in system and "g-secret-value" not in system   # presence only, never the value
    assert "fdb_v3" in examples and all("say" in x for x in examples["fdb_v3"])
