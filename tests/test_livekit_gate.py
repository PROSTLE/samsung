"""KeelGate on a real asyncio loop, with no LiveKit and no network.

Timings are shortened with config overrides so the tests run in well under a
second each; the logic is the same as with the shipped values.
"""

import asyncio
import io
import json
from pathlib import Path

from keel.config import load_config, with_overrides
from keel.kernel.clock import MonotonicClock
from keel.livekit.fdb import FdbBackend
from keel.livekit.gate import KeelGate
from keel.livekit.template import read_template
from keel.sim.invariants import check_trace
from keel.trace import TraceRecord, TraceWriter

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "fdb_style_agent.py"


class FakeRegistry:
    """Stands in for FDB-v3's MockAPIRegistry: records calls, returns data."""

    def __init__(self, delay_s=0.0, results=None):
        self.calls = []
        self.delay_s = delay_s
        self.results = results or {}

    def call(self, name, **kwargs):
        import time
        time.sleep(self.delay_s)
        self.calls.append((name, kwargs))
        return self.results.get(name, {"status": "success", "echo": kwargs})


def cfg(**fence):
    base = load_config(None, ROOT / "config" / "fdb_v3.toml")
    return with_overrides(base, trace={"record_wall_time": False},
                          fence={"quiet_ms": 60, "stale_turn_ms": 120, **fence},
                          calls={"timeout_ms": 400})


class Harness:
    def __init__(self, tmp_path, config=None, registry=None, drop=True):
        self.tmp_path = tmp_path
        self.config = config or cfg()
        self.registry = registry or FakeRegistry()
        self.buf = io.StringIO()

    async def __aenter__(self):
        loop = asyncio.get_running_loop()
        clock = MonotonicClock(loop)
        template = read_template(FIXTURE)
        self.backend = FdbBackend(template=template, registry=self.registry, room="room-1",
                                  tool_log=self.tmp_path / "tool_calls.log", seed=5)
        trace = TraceWriter(self.buf, clock=clock, session_id="room-1", config=self.config, synthetic=True)
        self.gate = KeelGate(session_id="room-1", config=self.config, tools=template.specs(),
                             execute=self.backend.execute, trace=trace, clock=clock, loop=loop,
                             drop_on_new_speech=True, complete_arguments=self.backend.arguments)
        return self

    async def __aexit__(self, *exc):
        await self.gate.aclose()

    def logged(self):
        path = self.tmp_path / "tool_calls.log"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def records(self):
        return [TraceRecord.model_validate_json(line) for line in self.buf.getvalue().splitlines()]

    def say(self, text):
        """One complete user turn: speech, transcript, end of turn."""
        self.gate.user_speaking()
        self.gate.user_stopped()
        self.gate.user_transcript(text, final=True)
        self.gate.user_turn_committed(text)


def run(coro):
    return asyncio.run(coro)


def test_a_call_waits_for_the_fence_then_runs_once_and_is_logged(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.say("trains to Pune on Friday")
            t0 = asyncio.get_running_loop().time()
            out = await h.gate.call("search_trains", {"city": "Pune", "date": "Friday"})
            waited = asyncio.get_running_loop().time() - t0
            return h, out, waited

    h, out, waited = run(main())
    assert out["status"] == "ok" and out["result"]["echo"] == {"city": "Pune", "date": "Friday"}
    assert waited >= 0.04  # held for (most of) quiet_ms after the end of turn
    logged = h.logged()
    assert [(r["room"], r["call"]["function"], r["call"]["args"]) for r in logged] == [
        ("room-1", "search_trains", {"city": "Pune", "date": "Friday"})]
    assert logged[0]["call"]["timestamp_end"] >= logged[0]["call"]["timestamp_start"]
    assert check_trace(h.records()) == []


def test_a_call_planned_before_a_self_correction_never_runs(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.say("trains to Pune")
            first = asyncio.ensure_future(h.gate.call("search_trains", {"city": "Pune", "date": "Friday"}))
            await asyncio.sleep(0.01)          # held by the fence (quiet 60 ms)
            h.say("no, actually Nashik")       # the user keeps talking
            superseded = await first
            second = await h.gate.call("search_trains", {"city": "Nashik", "date": "Friday"})
            return h, superseded, second

    h, superseded, second = run(main())
    assert superseded["status"] == "superseded"
    assert second["status"] == "ok"
    assert [r["call"]["args"]["city"] for r in h.logged()] == ["Nashik"]   # Pune never executed
    kinds = [r.kind for r in h.records()]
    assert "superseded_by_speech" in kinds
    cancelled = [r.data for r in h.records() if r.kind == "call_cancelled"]
    assert cancelled and cancelled[0]["reason"].startswith("utterance v1->v2")
    assert not cancelled[0]["was_in_flight"]
    assert check_trace(h.records()) == []


def test_speech_that_is_not_transcribed_drops_nothing(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.say("trains to Pune")
            pending = asyncio.ensure_future(h.gate.call("search_trains", {"city": "Pune", "date": "Friday"}))
            await asyncio.sleep(0.01)
            h.gate.user_speaking()             # e.g. background noise
            h.gate.user_stopped()              # ... that produced no transcript
            return h, await pending

    h, out = run(main())
    assert out["status"] == "ok" and len(h.logged()) == 1


def test_the_fence_stays_shut_while_the_user_is_speaking(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.say("trains to Pune")
            pending = asyncio.ensure_future(h.gate.call("search_trains", {"city": "Pune", "date": "Friday"}))
            h.gate.user_speaking()
            await asyncio.sleep(0.3)           # longer than stale_turn_ms + quiet_ms
            ran_while_speaking = len(h.registry.calls)
            h.gate.user_stopped()
            out = await pending
            return h, ran_while_speaking, out

    h, ran_while_speaking, out = run(main())
    assert ran_while_speaking == 0 and out["status"] == "ok"


def test_an_identical_call_is_answered_from_the_first_one(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.say("reserve train T1")
            a = await h.gate.call("reserve_seat", {"train_id": "T1"})
            b = await h.gate.call("reserve_seat", {"train_id": "T1", "seats": 1})   # default made explicit
            return h, a, b

    h, a, b = run(main())
    assert a["status"] == b["status"] == "ok"
    assert b["note"] == "identical call already made in this session"
    assert [r["call"]["args"] for r in h.logged()] == [{"train_id": "T1", "seats": 1}]


def test_numbers_are_canonicalised_before_the_idempotency_key(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.say("convert")
            await h.gate.call("convert", {"amount": 100, "to_currency": "EUR"})
            await h.gate.call("convert", {"amount": 100.0, "to_currency": "EUR"})
            return h

    h = run(main())
    assert [r["call"]["args"] for r in h.logged()] == [{"amount": 100.0, "to_currency": "EUR", "note": None}]


def test_invalid_arguments_are_returned_to_the_llm_and_never_run(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.say("reserve")
            return h, await h.gate.call("reserve_seat", {"seats": "two"})

    h, out = run(main())
    assert out["status"] == "error" and "train_id" in out["message"]
    assert h.logged() == []


def test_parallel_calls_both_complete_and_neither_is_cancelled(tmp_path):
    async def main():
        async with Harness(tmp_path, registry=FakeRegistry(delay_s=0.05)) as h:
            h.say("two things")
            a, b = await asyncio.gather(h.gate.call("search_trains", {"city": "A", "date": "d"}),
                                        h.gate.call("convert", {"amount": 5, "to_currency": "INR"}))
            return h, a, b

    h, a, b = run(main())
    assert a["status"] == b["status"] == "ok" and len(h.logged()) == 2
    assert not [r for r in h.records() if r.kind == "call_cancelled"]


def test_fillers_are_truthful_and_bounded(tmp_path):
    async def main():
        async with Harness(tmp_path, registry=FakeRegistry(delay_s=0.1)) as h:
            h.say("reserve T9")
            pending = asyncio.ensure_future(h.gate.call("reserve_seat", {"train_id": "T9"}))
            await asyncio.sleep(0.005)
            held = h.gate.filler("c1", 0)            # still behind the fence
            await asyncio.sleep(0.09)                # now dispatched and running
            running = h.gate.filler("c1", 0)
            progress = [h.gate.filler("c1", n) for n in (1, 2, 3)]
            await pending
            return h, held, running, progress

    h, held, running, progress = run(main())
    cfg_ = h.config
    assert held == cfg_.phrases.hold
    assert running and "done" not in running.lower() and "booked" not in running.lower()
    assert progress[:cfg_.floor.max_progress_per_turn] == [cfg_.phrases.progress] * cfg_.floor.max_progress_per_turn
    assert progress[cfg_.floor.max_progress_per_turn:] == [None] * (3 - cfg_.floor.max_progress_per_turn)
    assert h.gate.filler("c1", 0) is None     # resolved calls say nothing more


def test_kernel_speech_is_recorded_as_notes_not_actions(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.say("trains")
            await h.gate.call("search_trains", {"city": "X", "date": "d"})
            await asyncio.sleep(0.4)   # past the hold-phrase timer
            return h

    h = run(main())
    outs = {r.kind for r in h.records() if r.dir == "out"}
    assert outs <= {"tool_call", "cancel"}
    assert any(r.kind.startswith("unspoken_") for r in h.records())


def test_concurrent_executions_write_whole_log_lines(tmp_path):
    # The FDB-v3 runner parses the tool log line by line; parallel calls run in
    # worker threads and must never interleave or lose a line.
    async def main():
        backend = FdbBackend(template=read_template(FIXTURE), registry=FakeRegistry(delay_s=0.01), room="r",
                             tool_log=tmp_path / "tools.log", seed=5)
        await asyncio.gather(*(backend.execute("search_trains", {"city": f"C{n}", "date": "d"}) for n in range(20)))

    run(main())
    lines = (tmp_path / "tools.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 20
    assert sorted(json.loads(line)["call"]["args"]["city"] for line in lines) == sorted(f"C{n}" for n in range(20))


def test_a_number_for_a_string_parameter_is_passed_as_its_string_form(tmp_path):
    async def main():
        async with Harness(tmp_path) as h:
            h.say("trains to 42")
            return h, await h.gate.call("search_trains", {"city": 42, "date": "d"})

    h, out = run(main())
    assert out["status"] == "ok" and h.logged()[0]["call"]["args"]["city"] == "42"


def test_one_parallel_call_failing_does_not_cancel_the_other(tmp_path):
    # A failed step ends the kernel's goal; the gate must re-sync the goal to
    # the calls still outstanding before the kernel cancels the slow one.
    class Registry(FakeRegistry):
        def call(self, name, **kwargs):
            import time
            if name == "convert":
                return {"status": "error", "message": "boom"}
            time.sleep(0.15)
            self.calls.append((name, kwargs))
            return {"status": "success"}

    async def main():
        async with Harness(tmp_path, registry=Registry()) as h:
            h.say("two things")

            async def noise_mid_flight():
                # Any later event makes the kernel converge; that is when a stale
                # goal would cancel the slow call. Background noise is one.
                await asyncio.sleep(0.12)
                h.gate.user_speaking()
                h.gate.user_stopped()

            a, b, _ = await asyncio.gather(h.gate.call("search_trains", {"city": "A", "date": "d"}),
                                           h.gate.call("convert", {"amount": 5, "to_currency": "INR"}),
                                           noise_mid_flight())
            await asyncio.sleep(0.05)
            return h, a, b

    h, a, b = run(main())
    assert b["status"] == "error" and a["status"] == "ok"
    assert not [r for r in h.records() if r.kind == "call_cancelled"]
    assert check_trace(h.records()) == []


def _read_only_gate(config, executed):
    from keel.protocol.provisional import ToolSpec

    spec = ToolSpec(name="get_price", description="Look up a price.", annotations={"readOnlyHint": True},
                    parameters={"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]})

    async def execute(tool, args):
        executed.append((tool, args))
        return "ok", {"price": 1}, None

    loop = asyncio.get_running_loop()
    clock = MonotonicClock(loop)
    trace = TraceWriter(io.StringIO(), clock=clock, session_id="r", config=config, synthetic=True)
    return KeelGate(session_id="r", config=config, tools=[spec], execute=execute, trace=trace, clock=clock,
                    loop=loop, drop_on_new_speech=True)


def _say(gate, text):
    gate.user_speaking(); gate.user_stopped()
    gate.user_transcript(text, final=True); gate.user_turn_committed(text)


def test_with_hold_reads_a_read_waits_for_the_fence_and_is_dropped_by_a_correction():
    # FDB-v3 profile: every executed call is scored, so a read on a retracted
    # value must never run.
    executed = []

    async def main():
        gate = _read_only_gate(cfg(), executed)
        assert gate.kernel.policies["get_price"].safety == "read_only"
        _say(gate, "price of apples")
        first = asyncio.ensure_future(gate.call("get_price", {"item": "apples"}))
        await asyncio.sleep(0.01)
        ran_before_fence = list(executed)
        _say(gate, "no, pears")
        dropped = await first
        second = await gate.call("get_price", {"item": "pears"})
        await gate.aclose()
        return ran_before_fence, dropped, second

    ran_before_fence, dropped, second = run(main())
    assert ran_before_fence == []
    assert dropped["status"] == "superseded" and second["status"] == "ok"
    assert executed == [("get_price", {"item": "pears"})]


def test_without_hold_reads_a_read_starts_at_once():
    executed = []

    async def main():
        gate = _read_only_gate(cfg(hold_reads=False), executed)
        _say(gate, "price of apples")
        pending = asyncio.ensure_future(gate.call("get_price", {"item": "apples"}))
        await asyncio.sleep(0.01)       # well inside the 60 ms fence
        started = list(executed)
        await pending
        await gate.aclose()
        return started

    assert run(main()) == [("get_price", {"item": "apples"})]
