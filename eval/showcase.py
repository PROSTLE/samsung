"""Five showcase sessions, one per behaviour a judge asks about, rendered in
the trace console.

    python -m eval.showcase            # writes traces/*.jsonl and traces/console.html

SYNTHETIC: the tools, timings, transcripts and interpretations are written
here (the slow path is scripted, as in the tests); every trace header says
so and the console shows the label. The kernel, compiler and probe are the
real ones. The scenarios follow the guide's use cases (§2).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from keel.config import load_config, with_overrides
from keel.console import build_view, render
from keel.kernel.internal import Interpretation, SlotProposal
from keel.kernel.plan import Goal, SlotRef, Step
from keel.sim.harness import SimResult, Simulation
from keel.sim.invariants import check_trace
from keel.sim.mock_env import ToolBehaviour, World

SID = "demo"


def _obj(required: tuple[str, ...], **props: str) -> dict[str, Any]:
    return {"type": "object", "properties": {k: {"type": v} for k, v in props.items()}, "required": list(required)}


def tool(name: str, description: str, params: dict, read_only: Optional[bool]) -> dict[str, Any]:
    t: dict[str, Any] = {"name": name, "description": description, "parameters": params}
    if read_only is not None:
        t["annotations"] = {"readOnlyHint": read_only}
    return t


def manifest(*tools: dict) -> dict[str, Any]:
    return {"type": "manifest", "event_id": "m0", "session_id": SID, "ts_ms": 0, "tools": list(tools)}


def said(eid: str, ts: int, words: str, eot: bool = True) -> dict[str, Any]:
    return {"type": "text", "event_id": eid, "session_id": SID, "ts_ms": ts, "text": words, "end_of_turn": eot}


def frame(eid: str, ts: int) -> dict[str, Any]:
    return {"type": "frame", "event_id": eid, "session_id": SID, "ts_ms": ts, "media": {"uri": f"{eid}.png"}}


def p(name: str, value: Any, source: str = "text", conf: float = 0.95, correction: bool = False) -> SlotProposal:
    return SlotProposal(name=name, value=value, source=source, confidence=conf, correction=correction)  # type: ignore[arg-type]


def it(*props: SlotProposal, goal: Optional[Goal] = None) -> Interpretation:
    return Interpretation(proposals=props, goal=goal)


def slots(**bind: str) -> dict[str, SlotRef]:
    return {arg: SlotRef(slot=s) for arg, s in bind.items()}


@dataclass
class Showcase:
    key: str
    title: str
    events: list
    script: dict
    behaviours: dict
    config: dict

    def run(self, base) -> SimResult:
        sim = Simulation(config=with_overrides(base, **self.config) if self.config else base,
                         behaviours=self.behaviours, script=self.script, session_id=SID,
                         synthetic=True, scenario=self.title)
        return sim.run(self.events)


def _tickets(args: dict, world: World) -> tuple[str, Any]:
    return "ok", {"tickets": [a for _, t, a in world.effects if t == "create_ticket" and a["user_id"] == args["user_id"]]}


def showcases() -> list[Showcase]:
    visit = tool("book_technician_visit", "Book a technician visit for an appliance on a given day.",
                 _obj(("appliance", "day"), appliance="string", day="string"), False)
    visits = tool("get_technician_visits", "List the technician visits booked for this customer.",
                  _obj((), appliance="string"), True)
    route = tool("plan_route", "Compute a driving route to a destination.", _obj(("destination",), destination="string"), True)
    alerts = tool("get_traffic_alerts", "Current traffic alerts for a region.", _obj(("region",), region="string"), True)
    ticket = tool("create_ticket", "Open a support ticket for a customer.",
                  _obj(("user_id", "issue"), user_id="string", issue="string"), False)
    tickets = tool("get_tickets", "List a customer's support tickets.", _obj(("user_id",), user_id="string"), True)
    code = tool("lookup_error_code", "Look up what an appliance error code means in the service manual.",
                _obj(("appliance", "code"), appliance="string", code="string"), True)

    book = Goal(intent="book_visit", steps=(Step(step_id="book", tool="book_technician_visit",
                                                 bindings=slots(appliance="appliance", day="day")),),
                reply="Your {appliance} visit is booked for {day}.")
    nav = Goal(intent="navigate", steps=(Step(step_id="route", tool="plan_route", bindings=slots(destination="destination")),
                                         Step(step_id="alerts", tool="get_traffic_alerts", bindings=slots(region="region"))),
               reply="Route to {destination} is ready, {route.minutes} minutes.")
    support = Goal(intent="report_fault", steps=(Step(step_id="ticket", tool="create_ticket",
                                                      bindings=slots(user_id="user_id", issue="issue")),))
    fix = Goal(intent="troubleshoot", steps=(Step(step_id="lookup", tool="lookup_error_code",
                                                  bindings=slots(appliance="appliance", code="code")),),
               reply="{code} on a {appliance} means {lookup.meaning}. {lookup.fix}")

    booking = ToolBehaviour(latency_ms=400, effect=True, result={"visit_id": "V-1042"})
    return [
        Showcase(
            "self-repair", "Self-repair before anything is sent",
            [manifest(visit, visits), said("u1", 1000, "Book a technician for my washer on Friday", eot=False),
             said("u2", 1700, "no wait, Saturday.")],
            {"u1": [(180, it(p("appliance", "washer"), p("day", "friday"), goal=book))],
             "u2": [(160, it(p("day", "saturday", correction=True)))]},
            {"book_technician_visit": booking}, {}),
        Showcase(
            "reroute", "Destination changes mid-route",
            [manifest(route, alerts), said("u1", 1000, "Take me to Koramangala."),
             said("u2", 1900, "Actually, make it Indiranagar.")],
            {"u1": [(150, it(p("destination", "Koramangala"), p("region", "Bengaluru East"), goal=nav))],
             "u2": [(140, it(p("destination", "Indiranagar", correction=True)))]},
            {"plan_route": ToolBehaviour(latency_ms=1400, respond=lambda a, w: (
                "ok", {"minutes": {"Koramangala": 24, "Indiranagar": 17}[a["destination"]]})),
             "get_traffic_alerts": ToolBehaviour(latency_ms=1800, result={"alerts": []})}, {}),
        Showcase(
            "did-it-go-through", "A write times out, then gets checked",
            [manifest(ticket, tickets), said("u1", 1000, "My dryer shows a heating error, please raise a ticket.")],
            {"u1": [(200, it(p("user_id", "C-3187"), p("issue", "dryer heating error"), goal=support))]},
            {"create_ticket": ToolBehaviour(latency_ms=1500, outcome="timeout", effect=True),
             "get_tickets": ToolBehaviour(latency_ms=150, respond=_tickets)}, {}),
        Showcase(
            "landed-anyway", "A cancelled write lands with the old details",
            [manifest(visit, visits), said("u1", 1000, "Book the dishwasher technician for Friday."),
             said("u2", 2100, "Sorry, I meant Saturday.")],
            {"u1": [(150, it(p("appliance", "dishwasher"), p("day", "friday"), goal=book))],
             "u2": [(150, it(p("day", "saturday", correction=True)))]},
            {"book_technician_visit": ToolBehaviour(latency_ms=900, effect=True, honors_cancel=False,
                                                    result={"visit_id": "V-1043"})}, {}),
        Showcase(
            "show-and-fix", "An unclear camera frame gets a clarifying question",
            [manifest(code), said("u1", 1000, "What does this error on my washer mean?"), frame("f1", 1100),
             said("u2", 3600, "Yes, 4E.")],
            {"u1": [(120, it(p("appliance", "washer"), goal=fix))],
             "f1": [(450, it(p("code", "4E", source="frame", conf=0.46)))],
             "u2": [(130, it(p("code", "4E", correction=True)))]},
            {"lookup_error_code": ToolBehaviour(latency_ms=500, result={
                "meaning": "a water supply problem",
                "fix": "Check that the tap is open and the inlet hose isn't kinked."})}, {}),
    ]


def run_all(out_dir: Path) -> tuple[list[dict], list[str]]:
    base = with_overrides(load_config(), trace={"record_wall_time": False})
    views, problems = [], []
    out_dir.mkdir(parents=True, exist_ok=True)
    for sc in showcases():
        res = sc.run(base)
        (out_dir / f"{sc.key}.jsonl").write_text(res.trace_text, encoding="utf-8", newline="\n")
        problems += [f"{sc.key}: {v}" for v in check_trace(res.records, res.env.world)]
        views.append(build_view(res.records, name=sc.title, world=res.env.world))
    return views, problems


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.showcase")
    ap.add_argument("--out", type=Path, default=Path("traces"))
    args = ap.parse_args(argv)
    views, problems = run_all(args.out)
    page = args.out / "console.html"
    page.write_text(render(views), encoding="utf-8", newline="\n")
    for v in views:
        s = v["summary"]
        print(f"{v['name']:<52} calls={s['tool_calls']} cancels={s['cancels']} "
              f"first_response_max={s['first_response_max_ms']}ms final={v['finals'][-1]['text'] if v['finals'] else None!r}")
    print(f"wrote {page}  (open it in a browser)  invariant_violations={len(problems)}")
    for pr in problems:
        print("  ", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
