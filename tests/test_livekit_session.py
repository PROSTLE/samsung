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
import os
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


async def _run_session(tmp_path, plan, turns, fillers=False, tool_delay_s=0.0, history=None):
    config = with_overrides(load_config(None, ROOT / "config" / "fdb_v3.toml"), trace={"record_wall_time": False},
                            fence={"quiet_ms": 50})
    loop = asyncio.get_running_loop()
    clock = MonotonicClock(loop)
    template = read_template(FIXTURE)
    registry = FakeRegistry(delay_s=tool_delay_s)
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
    if history is not None:
        history.extend(getattr(item, "text_content", None) or "" for item in session.history.items)
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


def test_a_filler_is_spoken_but_kept_out_of_the_llms_chat_history(tmp_path):
    # The tool is slow enough (1.2 s) for the filler to fire. What Keel says
    # while waiting must not reach the LLM's context, where it would read as
    # something the assistant itself said.
    plan = {"trains": [("search_trains", {"city": "X", "date": "d"})]}
    history: list = []
    _, logged, records, _ = asyncio.run(_run_session(tmp_path, plan, list(plan), fillers=True,
                                                     tool_delay_s=1.2, history=history))
    assert len(logged) == 1
    said = [r.data["text"] for r in records if r.kind == "filler_said"]
    assert said, "the filler never fired, so this test would prove nothing"
    assert not [h for h in history if any(text in h for text in said)]


def _gemini_config():
    return with_overrides(load_config(None, ROOT / "config" / "fdb_v3.toml"), livekit={"pipeline": "gemini_realtime"})


def test_the_gemini_pipeline_is_a_gemini_live_session_with_the_configured_model(monkeypatch):
    pytest.importorskip("livekit.plugins.google")
    from livekit.plugins import google

    from keel.livekit.session import build_session

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")      # read by the plugin; no request is made
    cfg = _gemini_config()

    async def build():
        return build_session(cfg)

    session, has_tts, drop = asyncio.run(build())
    assert isinstance(session.llm, google.realtime.RealtimeModel)
    assert (session.llm.model, session.llm._opts.voice) == (cfg.livekit.gemini.model, cfg.livekit.gemini.voice)
    # No separate TTS: Keel's fillers (session.say) are off, as for gpt_realtime.
    assert has_tts is False and drop == cfg.livekit.gemini.drop_on_new_speech


def _open_config(**stages):
    base = load_config(None, ROOT / "config" / "fdb_v3.toml")
    # VAD turn detection: the end-of-utterance model needs a LiveKit job context.
    cascaded = {**base.livekit.cascaded.model_dump(), "turn_detector": "vad"}
    return with_overrides(base, livekit={"pipeline": "open", "cascaded": cascaded,
                                         "open": {**base.livekit.open.model_dump(), **stages}})


def test_the_open_pipeline_points_each_stage_at_its_provider():
    from livekit.plugins import openai as lk_openai

    from keel.livekit.session import build_session

    cfg = _open_config()
    o = cfg.livekit.open

    async def build():
        return build_session(cfg)

    session, has_tts, drop = asyncio.run(build())
    assert isinstance(session.stt, lk_openai.STT) and isinstance(session.tts, lk_openai.TTS)
    assert (str(session.stt._client.base_url), session.stt.model) == ("http://127.0.0.1:8000/v1/", o.stt_model)
    assert (str(session.llm._client.base_url), session.llm.model) == ("http://127.0.0.1:11434/v1/", o.llm_model)
    assert (str(session.tts._client.base_url), session.tts.model) == ("http://127.0.0.1:8000/v1/", o.tts_model)
    assert session.stt.capabilities.streaming is False          # the plain transcription endpoint, not OpenAI's realtime one
    assert session.llm._opts.temperature == 0.0 and session.llm._opts.extra_body == {"seed": cfg.livekit.cascaded.llm_seed}
    assert session.tts._opts.response_format == o.tts_format and has_tts is True and drop is o.drop_on_new_speech
    # Local models get longer per-request deadlines than LiveKit's 10 s, and one retry.
    for opts in (session.conn_options.stt_conn_options, session.conn_options.llm_conn_options,
                 session.conn_options.tts_conn_options):
        assert (opts.timeout, opts.max_retry) == (o.request_timeout_s, 1)


def test_an_open_stage_on_gemini_is_sent_no_seed(monkeypatch):
    from keel.livekit.session import build_session

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    cfg = _open_config(llm_provider="gemini", llm_model="gemini-2.5-flash")

    async def build():
        return build_session(cfg)

    session, _, _ = asyncio.run(build())
    assert str(session.llm._client.base_url) == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert not session.llm._opts.extra_body                   # Gemini rejects `seed` with HTTP 400


def test_a_missing_key_for_an_open_stage_stops_with_its_name(monkeypatch):
    from keel.livekit.session import build_session

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cfg = _open_config(llm_provider="groq", llm_model="openai/gpt-oss-20b")

    async def build():
        return build_session(cfg)

    with pytest.raises(SystemExit, match="GROQ_API_KEY"):
        asyncio.run(build())


@pytest.mark.skipif(not os.getenv("KEEL_FDB_DIR"), reason="set KEEL_FDB_DIR to an FDB-v3 checkout's v3/ directory")
def test_the_gemini_model_is_fdb_v3s_own_gemini3_1_provider():
    source = (Path(os.environ["KEEL_FDB_DIR"]) / "lk_agent_tool.py").read_text(encoding="utf-8")
    branch = source.split('provider == "gemini3_1"', 1)[1].split("elif", 1)[0]
    assert f'model="{_gemini_config().livekit.gemini.model}"' in branch


def _live_declarations(template_path):
    # The conversion the Live session itself makes when it connects
    # (livekit/plugins/google/realtime/realtime_api.py, _build_connect_config).
    from livekit.plugins.google.utils import create_tools_config

    template = read_template(template_path)
    tools = [make_tool(t.spec, None, _gemini_config(), False) for t in template.tools]
    gemini_tools, _ = create_tools_config(llm.ToolContext(tools), use_parameters_json_schema=False)
    return template, {d.name: d for d in gemini_tools[0].function_declarations}


def test_keel_tools_convert_to_the_live_sessions_function_declarations():
    pytest.importorskip("livekit.plugins.google")
    template, declared = _live_declarations(FIXTURE)
    assert set(declared) == {t.spec.name for t in template.tools}
    convert = declared["convert"].parameters
    assert convert.properties["note"].nullable is True            # `note: str = None`
    assert convert.properties["note"].type.value == "STRING"
    assert set(convert.required) == {"amount", "to_currency"}


@pytest.mark.skipif(not os.getenv("KEEL_FDB_DIR"), reason="set KEEL_FDB_DIR to an FDB-v3 checkout's v3/ directory")
def test_every_fdb_v3_tool_converts_for_the_live_session():
    pytest.importorskip("livekit.plugins.google")
    template, declared = _live_declarations(Path(os.environ["KEEL_FDB_DIR"]) / "cascaded_agent.py")
    assert set(declared) == {t.spec.name for t in template.tools}
    for t in template.tools:
        props = declared[t.spec.name].parameters.properties if t.spec.parameters["properties"] else {}
        assert set(props or {}) == set(t.spec.parameters["properties"]), t.spec.name


def test_nullable_as_any_of_is_the_same_schema_in_another_form():
    from jsonschema import Draft202012Validator

    from keel.livekit.session import nullable_as_any_of

    before = {"type": "object", "properties": {"note": {"type": ["string", "null"], "description": "d"},
                                               "n": {"type": "integer"}}, "required": ["n"]}
    after = nullable_as_any_of(before)
    assert after["properties"]["note"] == {"description": "d", "anyOf": [{"type": "string"}, {"type": "null"}]}
    assert after["properties"]["n"] == {"type": "integer"} and before["properties"]["note"]["type"] == ["string", "null"]
    for value in ({"n": 1}, {"n": 1, "note": None}, {"n": 1, "note": "x"}, {"n": 1, "note": 3}, {}):
        assert Draft202012Validator(before).is_valid(value) == Draft202012Validator(after).is_valid(value)


@pytest.mark.parametrize("module", ["keel.livekit.agent", "extension.show_and_fix.agent"])
def test_agent_process_hooks_can_be_pickled(module):
    # On Linux LiveKit starts job processes with multiprocessing's forkserver,
    # which pickles these. A closure here meant no job process ever started.
    import importlib
    import pickle

    mod = importlib.import_module(module)
    pickle.dumps(mod.server.setup_fnc)
    pickle.dumps(mod.entrypoint)
