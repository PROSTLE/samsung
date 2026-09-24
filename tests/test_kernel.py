"""End-to-end kernel behaviour on the virtual clock, with no model.

Every test also runs the trace invariants (I1-I5, keel/sim/invariants.py).
Timings in comments follow config/keel.toml: fence quiet 600 ms, ack 300 ms.
"""

import asyncio

import pytest

from keel.config import with_overrides
from keel.kernel.guard import SingleWriterViolation
from keel.kernel.internal import Interpretation
from keel.kernel.plan import Goal, ResultRef, SlotRef, Step
from keel.kernel.reconcile import ProbeCall
from keel.sim.harness import Simulation
from keel.sim.invariants import check_trace
from keel.sim.mock_env import ToolBehaviour
from tests.conftest import goal, interp, interrupt, manifest, prop, step, text, tool

TOOLS = manifest(0, tool("lookup", True), tool("other_lookup", True), tool("book", False),
                 tool("check_booking", True))


def run(cfg, events, script, behaviours=None, probe=None):
    sim = Simulation(config=cfg, behaviours=behaviours or {}, script=script, probe=probe)
    res = sim.run([TOOLS, *events])
    assert check_trace(res.records, res.env.world) == []
    return res


def timeline(res):
    out = []
    for a in res.actions:
        detail = getattr(a, "tool", None) or getattr(a, "call_id", None) or getattr(a, "text", "")
        out.append((a.ts_ms, a.type, detail))
    return out


class BookingProbe:
    """Test probe: asks check_booking whether a booking with these args exists."""

    def plan(self, entry, policies):
        return ProbeCall("check_booking", dict(entry.arguments))

    def verdict(self, entry, result):
        return "executed" if result.get("exists") else "not_executed"


def check_booking(args, world):
    return "ok", {"exists": world.happened("book", **args)}


# ---------------------------------------------------------------------------


def test_reads_start_speculatively_and_writes_wait_for_the_fence(cfg):
    g = goal("repair", step("s1", "lookup", code="code", appliance="appliance"),
             step("s2", "book", appliance="appliance"), reply="Booked for your {appliance}.")
    res = run(cfg, [text("e1", 1000)],
              {"e1": [(200, interp(prop("code", "E21"), prop("appliance", "washer"), goal=g))]},
              {"lookup": ToolBehaviour(latency_ms=300, result={"meaning": "x"}),
               "book": ToolBehaviour(latency_ms=200, result={"ticket": "T1"}, effect=True)})
    assert timeline(res) == [
        (1200, "tool_call", "lookup"),           # speculative read at once
        (1200, "speak", "Let me check that."),   # compiler-built ack for the read (no hold phrase needed)
        (1800, "tool_call", "book"),             # fence: 1200 (last change) + 600
        (2000, "final", "Booked for your washer."),
    ]
    final = res.of("final")[0]
    assert final.snapshot.intent == "repair"
    assert final.snapshot.slots == {"appliance": "washer", "code": "E21"}


def test_slot_change_cancels_exactly_the_calls_that_read_it(cfg):
    g = goal("repair", step("s1", "lookup", appliance="appliance"), step("s2", "other_lookup", zip="zip"))
    res = run(cfg, [text("e1", 1000), text("e2", 1400)],
              {"e1": [(200, interp(prop("appliance", "washer"), prop("zip", "411001"), goal=g))],
               "e2": [(100, interp(prop("appliance", "dryer", correction=True)))]},
              {"lookup": ToolBehaviour(latency_ms=1000), "other_lookup": ToolBehaviour(latency_ms=1000)})
    at_1500 = [t for t in timeline(res) if t[0] == 1500]
    assert at_1500 == [(1500, "cancel", "sim:call-00001"), (1500, "tool_call", "lookup"),
                       (1500, "speak", "Okay, dryer instead.")]
    cancel = res.of("cancel")[0]
    assert cancel.reason == "appliance v1->v2" and cancel.caused_by == "e2"
    assert len(res.of("cancel")) == 1  # other_lookup (reads zip) left alone
    assert [c.arguments for c in res.env.calls if c.tool == "lookup"] == [{"appliance": "washer"},
                                                                         {"appliance": "dryer"}]


def test_an_interrupt_alone_cancels_nothing(cfg):
    g = goal("repair", step("s1", "lookup", appliance="appliance"))
    res = run(cfg, [text("e1", 1000), interrupt("i1", 1400)],
              {"e1": [(200, interp(prop("appliance", "washer"), goal=g))]},
              {"lookup": ToolBehaviour(latency_ms=1000)})
    assert res.of("cancel") == [] and len(res.of("final")) == 1


def test_self_repair_before_the_fence_produces_one_write_with_the_repair(cfg):
    g = goal("book", step("s1", "book", day="day"))
    res = run(cfg, [text("e1", 1000), text("e2", 1500)],
              {"e1": [(200, interp(prop("day", "friday"), goal=g))],
               "e2": [(200, interp(prop("day", "saturday", correction=True)))]},
              {"book": ToolBehaviour(latency_ms=200, effect=True)})
    calls = [c for c in res.env.calls if c.tool == "book"]
    assert [(c.ts_ms, c.arguments) for c in calls] == [(2300, {"day": "saturday"})]  # 1700 + 600
    assert res.of("cancel") == []  # the friday write never left Keel, so nothing to cancel
    assert res.env.world.effects == [(2500, "book", {"day": "saturday"})]


def test_in_flight_write_invalidated_is_probed_before_the_new_write(cfg):
    fast = with_overrides(cfg, fence={"quiet_ms": 0})
    g = goal("book", step("s1", "book", day="day"))
    res = run(fast, [text("e1", 1000), text("e2", 1400)],
              {"e1": [(200, interp(prop("day", "friday"), goal=g))],
               "e2": [(100, interp(prop("day", "saturday", correction=True)))]},
              {"book": ToolBehaviour(latency_ms=1000, effect=True, honors_cancel=True),
               "check_booking": ToolBehaviour(latency_ms=100, respond=check_booking)},
              probe=BookingProbe())
    tl = [t for t in timeline(res) if t[1] in ("tool_call", "cancel", "final")]
    assert tl == [
        (1200, "tool_call", "book"),              # friday goes out (quiet 0)
        (1500, "cancel", "sim:call-00001"),       # invalidated by day v1->v2
        (11200, "tool_call", "check_booking"),    # Keel timeout on the cancelled write -> probe
        (11300, "tool_call", "book"),             # probe: friday never ran -> saturday is safe
        (12300, "final", "All done."),
    ]
    assert res.env.world.effects == [(12300, "book", {"day": "saturday"})]


@pytest.mark.parametrize("executed", [True, False])
def test_did_it_go_through(cfg, executed):
    g = goal("book", step("s1", "book", day="day"))
    res = run(cfg, [text("e1", 1000)],
              {"e1": [(200, interp(prop("day", "friday"), goal=g))]},
              {"book": ToolBehaviour(latency_ms=500, outcome="timeout", effect=executed),
               "check_booking": ToolBehaviour(latency_ms=100, respond=check_booking)},
              probe=BookingProbe())
    assert [c.tool for c in res.env.calls] == ["book", "check_booking"]  # never re-sent
    said = [a.text for a in res.actions if a.type in ("speak", "final")]
    assert "I'm not sure yet whether that went through. Let me check." in said
    if executed:
        assert said[-2:] == ["I've checked: it did go through.", "All done."]
    else:
        assert said[-1] == "I've checked: it did not go through."
        assert "All done." not in said


def test_without_a_probe_keel_says_it_cannot_confirm(cfg):
    # "reserve" shares no object words with any read-only tool, so nothing can check it.
    g = goal("book", step("s1", "reserve", day="day"))
    res = run(cfg, [text("e1", 1000)], {"e1": [(200, interp(prop("day", "friday"), goal=g))]},
              {"reserve": ToolBehaviour(latency_ms=500, outcome="timeout", effect=True)})
    assert [c.tool for c in res.env.calls] == ["reserve"]
    assert res.of("final")[0].text == "I can't confirm whether that went through."


def test_probe_is_derived_from_the_manifest(cfg):
    tools = manifest(0, tool("book", False),
                     {"name": "check_booking", "annotations": {"readOnlyHint": True},
                      "parameters": {"type": "object", "properties": {"day": {"type": "string"}},
                                     "required": ["day"]}})
    g = goal("book", step("s1", "book", day="day"))
    sim = Simulation(config=cfg, script={"e1": [(200, interp(prop("day", "friday"), goal=g))]},
                     behaviours={"book": ToolBehaviour(latency_ms=500, outcome="timeout", effect=True),
                                 "check_booking": ToolBehaviour(latency_ms=100, respond=check_booking)})
    res = sim.run([tools, text("e1", 1000)])
    assert check_trace(res.records, res.env.world) == []
    probe_call = [c for c in res.env.calls if c.tool == "check_booking"]
    assert [c.arguments for c in probe_call] == [{"day": "friday"}]
    said = [a.text for a in res.actions if a.type in ("speak", "final")]
    assert said[-2:] == ["I've checked: it did go through.", "All done."]



def test_write_that_landed_with_old_details_ends_the_goal_truthfully(cfg):
    # friday is cancelled after dispatch but the world runs it anyway; saturday
    # must not also be booked, and the session must still get a final response.
    fast = with_overrides(cfg, fence={"quiet_ms": 0})
    g = goal("book", step("s1", "book", day="day"))
    res = run(fast, [text("e1", 1000), text("e2", 1400)],
              {"e1": [(100, interp(prop("day", "friday"), goal=g))],
               "e2": [(100, interp(prop("day", "saturday", correction=True)))]},
              {"book": ToolBehaviour(latency_ms=1000, effect=True, honors_cancel=False)})
    assert res.env.world.effects == [(2100, "book", {"day": "friday"})]
    finals = res.of("final")
    assert [(f.ts_ms, f.text) for f in finals] == [(2100, cfg.phrases.already_committed)]
    assert finals[0].snapshot.slots == {"day": "saturday"}
    assert "commit_conflict" in [r.kind for r in res.records]


def test_required_argument_the_goal_left_unbound_reads_the_same_named_slot(cfg):
    tools = manifest(0, {"name": "create_ticket", "annotations": {"readOnlyHint": False},
                         "parameters": {"type": "object", "required": ["user_id", "issue"],
                                        "properties": {"user_id": {"type": "string"}, "issue": {"type": "string"}}}})
    g = goal("support", step("s1", "create_ticket", issue="issue"))  # no binding for user_id
    sim = Simulation(config=cfg, behaviours={"create_ticket": ToolBehaviour(effect=True)},
                     script={"e1": [(100, interp(prop("issue", "E21"), prop("user_id", "u7"), goal=g))]})
    res = sim.run([tools, text("e1", 1000)])
    assert check_trace(res.records, res.env.world) == []
    assert [c.arguments for c in res.env.calls] == [{"issue": "E21", "user_id": "u7"}]
    assert res.of("final")[0].text == "All done."


def test_required_argument_with_no_slot_is_asked_for(cfg):
    tools = manifest(0, {"name": "create_ticket", "annotations": {"readOnlyHint": False},
                         "parameters": {"type": "object", "required": ["user_id"],
                                        "properties": {"user_id": {"type": "string"}}}})
    g = goal("support", step("s1", "create_ticket"))
    res = Simulation(config=cfg, script={"e1": [(100, interp(goal=g))]}).run([tools, text("e1", 1000)])
    assert [c.text for c in res.of("clarify")] == ["What user id should I use?"]
    assert res.env.calls == []

def test_chained_step_follows_the_result_it_depends_on(cfg):
    g = Goal(intent="fly", steps=(
        Step(step_id="find", tool="lookup", bindings={"day": SlotRef(slot="day")}),
        Step(step_id="buy", tool="book", bindings={"flight": ResultRef(result="find", path=("id",))}),
    ))
    by_day = {"friday": "F1", "saturday": "F2"}
    res = run(cfg, [text("e1", 1000), text("e2", 1500)],
              {"e1": [(200, interp(prop("day", "friday"), goal=g))],
               "e2": [(100, interp(prop("day", "saturday", correction=True)))]},
              {"lookup": ToolBehaviour(latency_ms=100, respond=lambda a, w: ("ok", {"id": by_day[a["day"]]})),
               "book": ToolBehaviour(latency_ms=100, effect=True)})
    assert [c.arguments for c in res.env.calls if c.tool == "book"] == [{"flight": "F2"}]
    assert res.env.world.effects[-1][2] == {"flight": "F2"}


def test_duplicate_and_late_results_are_ignored(cfg):
    g = goal("repair", step("s1", "lookup", appliance="appliance"))
    res = run(cfg, [text("e1", 1000), text("e2", 1300)],
              {"e1": [(100, interp(prop("appliance", "washer"), goal=g))],
               "e2": [(100, interp(prop("appliance", "dryer", correction=True)))]},
              {"lookup": ToolBehaviour(latency_ms=500, duplicate=True, honors_cancel=False,
                                       respond=lambda a, w: ("ok", {"for": a["appliance"]}))})
    notes = [r.kind for r in res.records if r.dir == "note"]
    assert "result_dropped" in notes     # washer result arrived after its cancel
    assert "result_duplicate" in notes   # dryer result delivered twice
    assert res.of("final")[0].snapshot.slots == {"appliance": "dryer"}


def test_locked_correction_beats_later_perception(cfg):
    g = goal("repair", step("s1", "lookup", appliance="appliance"))
    res = run(cfg, [text("e1", 1000), text("e2", 1100)],
              {"e1": [(50, interp(prop("appliance", "washer", source="frame", conf=0.8), goal=g))],
               "e2": [(50, interp(prop("appliance", "dryer", correction=True))),
                      (80, interp(prop("appliance", "washer", source="frame", conf=0.99)))]},
              {"lookup": ToolBehaviour(latency_ms=500)})
    rejected = [r.data for r in res.records if r.kind == "proposal_rejected"]
    assert rejected and rejected[0]["reason"] == "locked"
    assert res.of("final")[0].snapshot.slots == {"appliance": "dryer"}


def test_low_confidence_perception_asks_instead_of_acting(cfg):
    g = goal("repair", step("s1", "lookup", code="code"))
    res = run(cfg, [text("e1", 1000)],
              {"e1": [(100, interp(prop("code", "E21", source="frame", conf=0.4), goal=g))]})
    clarify = res.of("clarify")
    assert [c.text for c in clarify] == ["Just to check, is the code E21?"]
    assert res.env.calls == [] and res.kernel.slots.get("code") is None


def test_missing_slot_is_asked_once_after_the_slow_path_answers(cfg):
    g = goal("book", step("s1", "book", day="day"))
    res = run(cfg, [text("e1", 1000)], {"e1": [(200, interp(goal=g))]})
    assert [(c.ts_ms, c.text, c.slot) for c in res.of("clarify")] == [(1200, "What day should I use?", "day")]


def test_floor_no_hold_phrase_when_something_substantive_came_first(cfg):
    g = goal("repair", step("s1", "lookup", appliance="appliance"))
    res = run(cfg, [text("e1", 1000), text("e2", 2000)],
              {"e1": [(100, interp(prop("appliance", "washer"), goal=g))],
               "e2": [(100, interp(prop("appliance", "dryer", correction=True)))]},
              {"lookup": ToolBehaviour(latency_ms=5000)})
    speech = [(a.ts_ms, a.text) for a in res.of("speak")]
    assert (2100, "Okay, dryer instead.") in speech
    assert (2300, "One moment.") not in speech
    progress = [s for s in speech if s[1] == "Still working on it."]
    assert 0 < len(progress) <= cfg.floor.max_progress_per_turn


def test_replay_is_byte_identical(cfg):
    g = goal("repair", step("s1", "lookup", appliance="appliance"), step("s2", "book", appliance="appliance"))
    args = (cfg, [text("e1", 1000)], {"e1": [(200, interp(prop("appliance", "washer"), goal=g))]},
            {"book": ToolBehaviour(effect=True)})
    assert run(*args).trace_text == run(*args).trace_text


def test_handle_cannot_be_reentered_from_a_sink(cfg):
    sim = Simulation(config=cfg, script={"e1": [(0, interp(prop("appliance", "washer"),
                                                             goal=goal("r", step("s1", "lookup", appliance="appliance"))))]})
    res = sim.run([TOOLS, text("e1", 10)])
    kernel = res.kernel
    import io
    from keel.trace import TraceWriter
    kernel.trace = TraceWriter(io.StringIO(), clock=kernel.clock, session_id="sim", config=cfg, synthetic=True)
    kernel._sink = lambda a: kernel.handle(Interpretation())
    with pytest.raises(SingleWriterViolation):
        kernel.handle(Interpretation(clear_goal=True, reply="Hello."))  # emits a final -> sink -> re-entry


def test_live_runner_over_asyncio_queues(cfg):
    from keel.kernel.clock import MonotonicClock
    from keel.kernel.loop import AsyncKernelRunner, Kernel
    from keel.protocol.adapter import END_OF_STREAM, QueueAdapter
    from keel.trace import TraceWriter
    import io

    g = goal("repair", step("s1", "lookup", appliance="appliance"))

    class Immediate:
        def submit(self, event, view, post):
            if event.type == "text":
                post(interp(prop("appliance", "washer"), goal=g, caused_by=event.event_id))

    async def main():
        inbox, outbox = asyncio.Queue(), asyncio.Queue()
        adapter = QueueAdapter(inbox, outbox)

        def make(post, sink):
            clock = MonotonicClock()
            tr = TraceWriter(io.StringIO(), clock=clock, session_id="live", config=cfg, synthetic=True)
            return Kernel(session_id="live", clock=clock, config=cfg, trace=tr, sink=sink,
                          interpreter=Immediate(), post=post)

        task = asyncio.create_task(AsyncKernelRunner(make).run(adapter))
        await inbox.put({**TOOLS, "session_id": "live"})
        await inbox.put({**text("e1", 0), "session_id": "live"})
        call = await asyncio.wait_for(outbox.get(), 2)
        while call["type"] != "tool_call":
            call = await asyncio.wait_for(outbox.get(), 2)
        await inbox.put({"type": "tool_result", "event_id": "r1", "session_id": "live", "ts_ms": 0,
                         "call_id": call["call_id"], "status": "ok", "result": {}})
        final = await asyncio.wait_for(outbox.get(), 2)
        while final["type"] != "final":
            final = await asyncio.wait_for(outbox.get(), 2)
        await inbox.put(END_OF_STREAM)
        await asyncio.wait_for(task, 2)
        return call, final

    call, final = asyncio.run(main())
    assert call["tool"] == "lookup" and call["arguments"] == {"appliance": "washer"}
    assert final["snapshot"] == {"intent": "repair", "slots": {"appliance": "washer"}}
