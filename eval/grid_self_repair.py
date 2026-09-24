"""Exhaustive grid over timing and faults around one self-repair ("friday ...
no, saturday") against a booking tool. Prints outcome classes and invariant
violations. Synthetic: the tools, timings and faults are generated here.

Usage: python -m eval.grid_self_repair
"""

from __future__ import annotations

import collections
import itertools
import sys

from keel.config import load_config, with_overrides
from keel.kernel.plan import Goal, SlotRef, Step
from keel.kernel.internal import Interpretation, SlotProposal
from keel.kernel.reconcile import ProbeCall
from keel.sim.harness import Simulation
from keel.sim.invariants import check_trace
from keel.sim.mock_env import ToolBehaviour

TOOLS = {"type": "manifest", "event_id": "m0", "session_id": "sim", "ts_ms": 0, "tools": [
    {"name": "book", "annotations": {"readOnlyHint": False}},
    {"name": "check_booking", "annotations": {"readOnlyHint": True}},
]}


class Probe:
    def plan(self, entry, policies):
        return ProbeCall("check_booking", dict(entry.arguments))

    def verdict(self, entry, result):
        return "executed" if result.get("exists") else "not_executed"


class NoProbe:
    """Nothing can check a write (the manifest-derived probe is disabled)."""

    def plan(self, entry, policies):
        return None

    def verdict(self, entry, result):
        return "unknown"


def text(eid, ts):
    return {"type": "text", "event_id": eid, "session_id": "sim", "ts_ms": ts, "text": "", "end_of_turn": True}


def day(value, correction=False):
    return Interpretation(proposals=(SlotProposal(name="day", value=value, source="text", confidence=0.95,
                                                  correction=correction),),
                          goal=Goal(intent="book", steps=(Step(step_id="s1", tool="book",
                                                               bindings={"day": SlotRef(slot="day")}),)))


def main() -> int:
    base = with_overrides(load_config(), trace={"record_wall_time": False})
    grid = itertools.product([1100, 1500, 2500], [0, 5, 50, 200], [0, 600], [0, 50, 300, 1500],
                             ["ok", "timeout", "error", "drop"], [True, False], [False, True], [True, False])
    tally: collections.Counter = collections.Counter()
    runs = violations = double = false_success = 0
    for corr, delay, quiet, lat, outcome, honors, dup, with_probe in grid:
        sim = Simulation(
            config=with_overrides(base, fence={"quiet_ms": quiet}),
            script={"e1": [(delay, day("friday"))], "e2": [(delay, day("saturday", correction=True))]},
            behaviours={"book": ToolBehaviour(latency_ms=lat, outcome=outcome, effect=True,
                                              honors_cancel=honors, duplicate=dup),
                        "check_booking": ToolBehaviour(
                            latency_ms=100,
                            respond=lambda a, w: ("ok", {"exists": w.happened("book", **a)}))},
            probe=Probe() if with_probe else NoProbe(),
        )
        res = sim.run([TOOLS, text("e1", 1000), text("e2", corr)])
        runs += 1
        violations += bool(check_trace(res.records, res.env.world))
        booked = tuple(a["day"] for _, t, a in res.env.world.effects if t == "book")
        double += len(booked) > 1
        finals = res.of("final")
        if finals and finals[0].text == "All done." and not res.env.world.happened(
                "book", day=finals[0].snapshot.slots["day"]):
            false_success += 1
        tally[(finals[0].text if finals else "<no final: commit conflict awaits replan>", booked)] += 1

    print(f"synthetic grid: runs={runs} invariant_violations={violations} "
          f"double_bookings={double} false_success_claims={false_success}")
    for (final, booked), n in tally.most_common():
        print(f"{n:5d}  booked={list(booked)!s:<14} final={final!r}")
    return 1 if (violations or double or false_success) else 0


if __name__ == "__main__":
    sys.exit(main())
