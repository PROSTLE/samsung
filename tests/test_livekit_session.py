"""A real LiveKit AgentSession (text mode) running Keel's tools, with a scripted LLM.

This drives LiveKit's own tool-execution path (voice/generation.py) through
keel.livekit.agent.make_tool -> KeelGate -> FdbBackend, so the raw-schema tool
signature, the RunContext calls, the tool log and the session event wiring
are exercised against the installed livekit-agents, offline.

Skipped when livekit-agents is not installed (pip install -e ".[livekit]").
"""

import asyncio
import io
import json
from pathlib import Path

import pytest

pytest.importorskip("livekit.agents")

from livekit.agents import Agent, AgentSession, llm  # noqa: E402
from livekit.agents.llm import ChatChunk, ChoiceDelta, FunctionToolCall  # noqa: E402
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS  # noqa: E402

from keel.config import load_config, with_overrides  # noqa: E402
from keel.kernel.clock import MonotonicClock  # noqa: E402
from keel.livekit.session import make_tool  # noqa: E402
from keel.livekit.fdb import FdbBackend  # noqa: E402
from keel.livekit.gate import KeelGate  # noqa: E402
from keel.livekit.template import read_template  # noqa: E402
from keel.trace import TraceRecord, TraceWriter  # noqa: E402
from tests.test_livekit_gate import FIXTURE, FakeRegistry  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class ScriptedLLM(llm.LLM):
    """Answers each user message with the tool calls listed for it, then with
    text once the tool outputs are in the context."""

    def __init__(self, plan):
        super().__init__()
        self.plan = plan          # user text -> list of (tool, args)
        self.requests = 0

    def chat(self, *, chat_ctx, tools=None, conn_options=DEFAULT_API_CONNECT_OPTIONS, **_):
        self.requests += 1
        return _Stream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


class _Stream(llm.LLMStream):
    async def _run(self) -> None:
        items = self._chat_ctx.items
        last = items[-1]
        if getattr(last, "type", None) == "function_call_output":
            outputs = [i.output for i in items if getattr(i, "type", None) == "function_call_output"]
            text = "Done with: " + " | ".join(outputs[-2:])
            self._event_ch.send_nowait(ChatChunk(id="t", delta=ChoiceDelta(role="assistant", content=text)))
            return
        user = [i for i in items if getattr(i, "role", None) == "user"][-1].text_content
        calls = self._llm.plan.get(user, [])
        if not calls:
            self._event_ch.send_nowait(ChatChunk(id="t", delta=ChoiceDelta(role="assistant", content="Okay.")))
            return
        self._event_ch.send_nowait(ChatChunk(id="t", delta=ChoiceDelta(role="assistant", tool_calls=[
            FunctionToolCall(name=tool, arguments=json.dumps(args), call_id=f"fc_{self._llm.requests}_{n}")
            for n, (tool, args) in enumerate(calls)])))


async def _run_session(tmp_path, plan, turns, fillers=False):
    config = with_overrides(load_config(None, ROOT / "config" / "fdb_v3.toml"), trace={"record_wall_time": False},
                            fence={"quiet_ms": 50})
    loop = asyncio.get_running_loop()
    clock = MonotonicClock(loop)
    template = read_template(FIXTURE)
    registry = FakeRegistry()
    backend = FdbBackend(template=template, registry=registry, room="room-lk", tool_log=tmp_path / "tools.log", seed=5)
    buf = io.StringIO()
    trace = TraceWriter(buf, clock=clock, session_id="room-lk", config=config, synthetic=True)
    gate = KeelGate(session_id="room-lk", config=config, tools=template.specs(), execute=backend.execute,
                    trace=trace, clock=clock, loop=loop, drop_on_new_speech=True,
                    complete_arguments=backend.arguments)
    tools = [make_tool(t.spec, gate, config, fillers) for t in template.tools]
    session = AgentSession(llm=ScriptedLLM(plan))

    @session.on("conversation_item_added")
    def _item(ev):
        if getattr(ev.item, "role", None) == "user":
            gate.user_turn_committed(ev.item.text_content or "")

    await session.start(Agent(instructions=template.instructions, tools=tools))
    results = []
    for text in turns:
        results.append(await session.run(user_input=text))
    await session.aclose()
    await gate.aclose()
    logged = [json.loads(line) for line in (tmp_path / "tools.log").read_text(encoding="utf-8").splitlines()] \
        if (tmp_path / "tools.log").exists() else []
    records = [TraceRecord.model_validate_json(line) for line in buf.getvalue().splitlines()]
    return results, logged, records, registry


def test_livekit_runs_a_keel_tool_and_the_benchmark_log_gets_one_line(tmp_path):
    plan = {"trains to Pune on Friday": [("search_trains", {"city": "Pune", "date": "Friday"})]}
    results, logged, records, registry = asyncio.run(_run_session(tmp_path, plan, list(plan)))
    assert registry.calls == [("search_trains", {"city": "Pune", "date": "Friday"})]
    assert [(r["room"], r["call"]["function"], r["call"]["args"]) for r in logged] == [
        ("room-lk", "search_trains", {"city": "Pune", "date": "Friday"})]
    # The LLM received the mock's own JSON, as with the FDB-v3 templates.
    outputs = [e.item.output for e in results[0].events if getattr(e.item, "type", None) == "function_call_output"]
    assert json.loads(outputs[0]) == {"status": "success", "echo": {"city": "Pune", "date": "Friday"}}
    assert "llm_tool_call" in [r.kind for r in records]


def test_a_repeated_call_in_a_later_turn_is_not_executed_again(tmp_path):
    plan = {"reserve T1": [("reserve_seat", {"train_id": "T1"})],
            "reserve T1 again please": [("reserve_seat", {"train_id": "T1", "seats": 1})]}
    _, logged, records, registry = asyncio.run(_run_session(tmp_path, plan, list(plan)))
    assert len(registry.calls) == 1 and len(logged) == 1
    assert "result_reused" in [r.kind for r in records]


def test_parallel_calls_from_one_llm_step_both_run(tmp_path):
    plan = {"two things": [("search_trains", {"city": "A", "date": "d"}),
                           ("convert", {"amount": 5, "to_currency": "INR"})]}
    _, logged, _, _ = asyncio.run(_run_session(tmp_path, plan, list(plan)))
    assert sorted(r["call"]["function"] for r in logged) == ["convert", "search_trains"]


def test_invalid_arguments_never_reach_the_benchmark_log(tmp_path):
    plan = {"reserve": [("reserve_seat", {"seats": "two"})]}
    results, logged, _, _ = asyncio.run(_run_session(tmp_path, plan, list(plan)))
    assert logged == []
    outputs = [e.item.output for e in results[0].events if getattr(e.item, "type", None) == "function_call_output"]
    assert json.loads(outputs[0])["status"] == "error"


def test_fillers_path_runs_without_breaking_the_call(tmp_path):
    plan = {"trains": [("search_trains", {"city": "X", "date": "d"})]}
    _, logged, _, _ = asyncio.run(_run_session(tmp_path, plan, list(plan), fillers=True))
    assert len(logged) == 1


@pytest.mark.parametrize("module", ["keel.livekit.agent", "extension.show_and_fix.agent"])
def test_agent_process_hooks_can_be_pickled(module):
    # On Linux LiveKit starts job processes with multiprocessing's forkserver,
    # which pickles these. A closure here meant no job process ever started.
    import importlib
    import pickle

    mod = importlib.import_module(module)
    pickle.dumps(mod.server.setup_fnc)
    pickle.dumps(mod.entrypoint)
