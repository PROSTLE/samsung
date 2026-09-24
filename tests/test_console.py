import json
import re

from keel.console import build_view, render
from keel.sim.harness import Simulation
from keel.sim.mock_env import ToolBehaviour
from tests.conftest import goal, interp, manifest, prop, step, text, tool


def _payload(page):
    m = re.search(r'<script id="keel-data" type="application/json">(.*?)</script>', page, re.S)
    return json.loads(m.group(1))


def _session(cfg):
    g = goal("repair", step("s1", "lookup", appliance="appliance"))
    sim = Simulation(config=cfg, behaviours={"lookup": ToolBehaviour(latency_ms=500)},
                     script={"e1": [(100, interp(prop("appliance", "washer"), goal=g))],
                             "e2": [(100, interp(prop("appliance", "dryer", correction=True)))]})
    return sim.run([manifest(0, tool("lookup", True)), text("e1", 1000, "my washer"), text("e2", 1300, "no, dryer")])


def test_view_is_built_from_the_trace(cfg):
    res = _session(cfg)
    v = build_view(res.records, world=res.env.world)
    assert v["header"]["synthetic"] is True
    assert [c["status"] for c in v["calls"]] == ["cancelled", "succeeded"]
    assert v["calls"][0]["reason"] == "appliance v1->v2"
    assert [x["value"] for x in v["slots"][0]["versions"]] == ["washer", "dryer"]
    assert v["finals"][-1]["snapshot"]["slots"] == {"appliance": "dryer"}
    assert {i["id"]: i["status"] for i in v["invariants"]} == {f"I{n}": "pass" for n in range(1, 6)}
    # Latency is measured per end of turn, to the first thing Keel said.
    assert [(x["ts"], x["ms"]) for x in v["latency"]] == [(1000, 100), (1300, 100)]


def test_world_invariant_is_unchecked_without_ground_truth(cfg):
    v = build_view(_session(cfg).records)
    assert {i["id"]: i["status"] for i in v["invariants"]}["I4"] == "unchecked"


def test_page_embeds_data_safely(cfg):
    res = _session(cfg)
    v = build_view(res.records, name="</script><script>alert(1)</script>")
    page = render([v])
    assert "</script><script>alert(1)" not in page
    assert _payload(page)[0]["name"] == "</script><script>alert(1)</script>"
    assert "__KEEL_DATA__" not in page and "<title>Keel trace console</title>" in page


def test_showcase_sessions_hold_every_invariant(tmp_path):
    from eval.showcase import run_all

    views, problems = run_all(tmp_path)
    assert problems == []
    assert len(views) == 5 and all(v["finals"] for v in views)
    assert all(v["header"]["synthetic"] for v in views)
    assert sorted(p.name for p in tmp_path.glob("*.jsonl")) == sorted(
        f"{k}.jsonl" for k in ("self-repair", "reroute", "did-it-go-through", "landed-anyway", "show-and-fix"))


def test_a_livekit_session_trace_shows_real_speech_and_turns(tmp_path):
    import asyncio

    from tests.test_livekit_gate import Harness

    async def main():
        async with Harness(tmp_path) as h:
            h.gate.note("session_config", pipeline="cascaded")
            h.say("trains to Pune")
            await h.gate.call("search_trains", {"city": "Pune", "date": "Friday"})
            h.gate.note("agent_state", state="speaking")
            h.gate.note("agent_said", text="Found trains to Pune.")
            return h

    h = asyncio.run(main())
    v = build_view(h.records())
    assert v["live"] is True
    assert [i["label"] for i in v["inputs"]] == ["trains to Pune"]          # no speech-start/stop markers
    assert [s["text"] for s in v["spoken"] if s["type"] == "agent"] == ["Found trains to Pune."]
    assert v["latency"] and v["latency"][0]["ms"] is not None
