"""Pieces every Keel LiveKit agent shares: the guarded tool, the session, the events.

Used by the FDB-v3 agent (keel.livekit.agent) and the Show & Fix extension
(extension/show_and_fix/agent.py). Everything here follows the installed
livekit-agents API (pinned in requirements/agent.lock.txt) and is exercised by
tests/test_livekit_session.py through a real AgentSession.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Optional

from livekit import agents
from livekit.agents import AgentSession, RunContext, function_tool
# Imported at module level: LiveKit registers plugins on the main thread,
# `download-files` fetches their model weights, and importing google.genai
# inside a job blocked the agent's event loop for ~2 s (measured).
from livekit.plugins import google, openai, silero, turn_detector  # noqa: F401

from keel.config import KeelConfig
from keel.livekit.gate import KeelGate
from keel.protocol.provisional import ToolSpec
from keel.providers import PIPELINES, ProviderError, accepts_seed, apply_env_pipeline, endpoint  # noqa: F401

# Pipelines built from VAD + STT + LLM + TTS (the others are one realtime model).
CASCADES = ("cascaded", "open")


def to_llm(payload: dict[str, Any]) -> str:
    """A successful call returns the tool's own JSON (as the FDB-v3 templates
    do); anything else tells the LLM what happened and why."""
    if payload.get("status") == "ok":
        return json.dumps(payload.get("result"))
    return json.dumps(payload)


def nullable_as_any_of(schema: Any) -> Any:
    """`"type": [T, "null"]` rewritten as `"anyOf": [{"type": T}, {"type": "null"}]`.

    The same JSON Schema. The Google plugin's Live API converter
    (_GeminiJsonSchema, called with use_parameters_json_schema=False) reads a
    nullable parameter only in the anyOf form; a type list makes it raise
    ("unhashable type: 'list'") and the session never connects."""
    if isinstance(schema, list):
        return [nullable_as_any_of(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: nullable_as_any_of(v) for k, v in schema.items()}
    t = out.get("type")
    if isinstance(t, list) and "null" in t and len(t) == 2:
        (other,) = [x for x in t if x != "null"]
        del out["type"]
        out["anyOf"] = [{"type": other}, {"type": "null"}]
    return out


def make_tool(spec: ToolSpec, gate: KeelGate, cfg: KeelConfig, fillers: bool):
    """A LiveKit raw-schema tool whose every call goes through the gate."""
    name = spec.name
    parameters = spec.parameters
    if cfg.livekit is not None and cfg.livekit.pipeline == "gemini_realtime":
        parameters = nullable_as_any_of(parameters)
    schema = {"name": name, "description": spec.description, "parameters": parameters}
    floor = cfg.floor

    async def wait(step_id: str, fut: asyncio.Future, context: RunContext) -> dict[str, Any]:
        await context.speech_handle.wait_if_not_interrupted([fut])
        if not fut.done():
            # LiveKit interrupted the reply this call belongs to. If the call
            # has not gone out, it never will; if it has, wait for its result.
            gate.drop(step_id, "the user interrupted the reply this call belonged to")
        return await fut

    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str:
        step_id, fut = gate.submit(name, dict(raw_arguments))
        if step_id is None:
            return to_llm(fut.result())
        if not fillers:
            return to_llm(await wait(step_id, fut, context))

        def filler(n: int):
            # Spoken, but kept out of the LLM's chat history (say() adds text to
            # it by default): the LLM must not read "I'm sending the ... request
            # now" back as something it said, or plan from it.
            text = gate.filler(step_id, n)
            return context.session.say(text, add_to_chat_ctx=False) if text else None

        async with context.with_filler(filler, delay=floor.ack_after_ms / 1000,
                                       interval=floor.progress_after_ms / 1000,
                                       max_steps=1 + floor.max_progress_per_turn):
            return to_llm(await wait(step_id, fut, context))

    return function_tool(handler, raw_schema=schema)


def load_vad(cfg: KeelConfig) -> Any:
    c = cfg.livekit.cascaded  # type: ignore[union-attr]
    return silero.VAD.load(min_speech_duration=c.vad_min_speech_s, min_silence_duration=c.vad_min_silence_s)


def prewarm_vad(proc: agents.JobProcess, cfg: KeelConfig) -> None:
    """Load the VAD model once per job process, before any room: loading it
    inside the session blocks the event loop (~0.4 s measured).

    Call it from a MODULE-LEVEL setup function. On Linux LiveKit starts job
    processes with multiprocessing's forkserver, which pickles the setup
    function; a closure cannot be pickled and every job process then fails
    to start ("Can't pickle local object"), so no agent ever joins a room."""
    if cfg.livekit is not None and cfg.livekit.pipeline in CASCADES:
        proc.userdata["vad"] = load_vad(cfg)


def _client_args(cfg: KeelConfig, provider: str) -> dict[str, Any]:
    try:
        base_url, key = endpoint(cfg, provider)
    except ProviderError as e:
        raise SystemExit(str(e)) from None
    return {"api_key": key, **({"base_url": base_url} if base_url else {})}


def _cascade(cfg: KeelConfig, vad: Any, stt: Any, llm: Any, tts: Any, **session_kwargs: Any) -> AgentSession:
    """VAD, end-of-turn model and endpointing from [livekit.cascaded], around any STT/LLM/TTS."""
    c = cfg.livekit.cascaded  # type: ignore[union-attr]
    vad = vad or load_vad(cfg)
    if c.turn_detector == "english":
        from livekit.plugins.turn_detector.english import EnglishModel
        detection: Any = EnglishModel()
    elif c.turn_detector == "multilingual":
        from livekit.plugins.turn_detector.multilingual import MultilingualModel
        detection = MultilingualModel()
    elif c.turn_detector == "vad":
        detection = "vad"
    else:
        raise SystemExit(f"unknown turn_detector {c.turn_detector!r}")
    return AgentSession(
        vad=vad, stt=stt, llm=llm, tts=tts,
        turn_handling={
            "turn_detection": detection,
            "endpointing": {"min_delay": c.min_endpointing_s, "max_delay": c.max_endpointing_s},
            "preemptive_generation": {"enabled": c.preemptive_generation},
        },
        **session_kwargs,
    )


def build_session(cfg: KeelConfig, *, vad: Any = None, **session_kwargs: Any) -> tuple[AgentSession, bool, bool]:
    """The configured pipeline. Returns (session, has_tts, drop_on_new_speech)."""
    lk = cfg.livekit
    assert lk is not None
    if lk.pipeline == "cascaded":
        c = lk.cascaded
        session = _cascade(
            cfg, vad,
            stt=openai.STT(model=c.stt_model, language=c.stt_language),
            llm=openai.LLM(model=c.llm_model, temperature=c.llm_temperature, extra_body={"seed": c.llm_seed}),
            tts=openai.TTS(model=c.tts_model, voice=c.tts_voice),
            **session_kwargs,
        )
        return session, True, c.drop_on_new_speech
    if lk.pipeline == "open":
        o, c = lk.open, lk.cascaded
        if o is None:
            raise SystemExit("pipeline open needs a [livekit.open] section in the config")
        import httpx
        from livekit.agents import APIConnectOptions
        from livekit.agents.voice.agent_session import SessionConnectOptions

        per_request = APIConnectOptions(max_retry=1, retry_interval=0.5, timeout=o.request_timeout_s)
        session_kwargs.setdefault("conn_options", SessionConnectOptions(
            stt_conn_options=per_request, llm_conn_options=per_request, tts_conn_options=per_request))
        session = _cascade(
            cfg, vad,
            stt=openai.STT(model=o.stt_model, language=o.stt_language, use_realtime=False,
                           **_client_args(cfg, o.stt_provider)),
            llm=openai.LLM(model=o.llm_model, temperature=c.llm_temperature,
                           # The plugin's default allows 5 s between bytes; the first token may take longer.
                           timeout=httpx.Timeout(connect=15.0, read=o.request_timeout_s, write=15.0, pool=15.0),
                           **({"extra_body": {"seed": c.llm_seed}} if accepts_seed(cfg, o.llm_provider) else {}),
                           **_client_args(cfg, o.llm_provider)),
            tts=openai.TTS(model=o.tts_model, voice=o.tts_voice, response_format=o.tts_format,  # type: ignore[arg-type]
                           **_client_args(cfg, o.tts_provider)),
            **session_kwargs,
        )
        return session, True, o.drop_on_new_speech
    if lk.pipeline == "gpt_realtime":
        r = lk.realtime
        model = openai.realtime.RealtimeModel(model=r.model, voice=r.voice)
        return AgentSession(llm=model, **session_kwargs), False, r.drop_on_new_speech
    if lk.pipeline == "gemini_realtime":
        g = lk.gemini
        if g is None:
            raise SystemExit("pipeline gemini_realtime needs a [livekit.gemini] section in the config")
        model = google.realtime.RealtimeModel(model=g.model, voice=g.voice)
        return AgentSession(llm=model, **session_kwargs), False, g.drop_on_new_speech
    raise SystemExit(f"unknown pipeline {lk.pipeline!r}")


def wire(session: AgentSession, gate: KeelGate, *,
         on_user_final: Optional[Callable[[], None]] = None,
         on_agent_speaking: Optional[Callable[[], None]] = None) -> None:
    """Feed the session's user/agent events to the gate (and optional hooks)."""

    @session.on("user_state_changed")
    def _user_state(ev: agents.voice.UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            gate.user_speaking()
        elif ev.old_state == "speaking":
            gate.user_stopped()

    @session.on("user_input_transcribed")
    def _transcribed(ev: agents.voice.UserInputTranscribedEvent) -> None:
        if ev.is_final and on_user_final:
            on_user_final()
        gate.user_transcript(ev.transcript, final=ev.is_final)

    @session.on("conversation_item_added")
    def _item(ev: agents.voice.ConversationItemAddedEvent) -> None:
        role = getattr(ev.item, "role", None)
        text = getattr(ev.item, "text_content", None) or ""
        if role == "user":
            gate.user_turn_committed(text)
        elif role == "assistant" and text:
            gate.note("agent_said", text=text)

    @session.on("agent_state_changed")
    def _agent_state(ev: agents.voice.AgentStateChangedEvent) -> None:
        gate.note("agent_state", state=ev.new_state)
        if ev.new_state == "speaking" and on_agent_speaking:
            on_agent_speaking()
