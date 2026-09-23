"""Randomised timing and faults around a self-repair. The phase-5 fuzzer
generalises this to every scenario; here it guards the kernel on its own."""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from keel.config import load_config, with_overrides
from keel.sim.harness import Simulation
from keel.sim.invariants import check_trace
from keel.sim.mock_env import ToolBehaviour
from tests.conftest import goal, interp, prop, step, text
from tests.test_kernel import TOOLS, BookingProbe, check_booking

BASE = with_overrides(load_config(), trace={"record_wall_time": False})


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    correction_at=st.integers(1001, 4000),
    interp_delay=st.sampled_from([0, 5, 50, 200, 900]),
    quiet_ms=st.sampled_from([0, 600]),
    book_latency=st.sampled_from([0, 5, 50, 300, 1500]),
    outcome=st.sampled_from(["ok", "timeout", "error", "drop"]),
    honors_cancel=st.booleans(),
    duplicate=st.booleans(),
    with_probe=st.booleans(),
)
def test_self_repair_never_double_books_or_lies(correction_at, interp_delay, quiet_ms, book_latency,
                                                outcome, honors_cancel, duplicate, with_probe):
    cfg = with_overrides(BASE, fence={"quiet_ms": quiet_ms})
    g = goal("book", step("s1", "book", day="day"))
    sim = Simulation(
        config=cfg,
        script={"e1": [(interp_delay, interp(prop("day", "friday"), goal=g))],
                "e2": [(interp_delay, interp(prop("day", "saturday", correction=True)))]},
        behaviours={"book": ToolBehaviour(latency_ms=book_latency, outcome=outcome, effect=True,
                                          honors_cancel=honors_cancel, duplicate=duplicate),
                    "check_booking": ToolBehaviour(latency_ms=100, respond=check_booking)},
        probe=BookingProbe() if with_probe else None,
    )
    res = sim.run([TOOLS, text("e1", 1000), text("e2", correction_at)])

    assert check_trace(res.records, res.env.world) == []
    bookings = [e for e in res.env.world.effects if e[1] == "book"]
    assert len(bookings) <= 1, bookings                        # never double-booked
    finals = res.of("final")
    assert len(finals) <= 1
    if finals and finals[0].text == "All done.":               # never a false success
        assert res.env.world.happened("book", day=finals[0].snapshot.slots["day"])
