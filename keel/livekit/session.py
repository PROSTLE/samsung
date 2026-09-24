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
# Imported at module level so `download-files` fetches their model weights.
from livekit.plugins import openai, silero, turn_detector  # noqa: F401

from keel.config import KeelConfig
from keel.livekit.gate import KeelGate
from keel.protocol.provisional import ToolSpec


def to_llm(payload: dict[str, Any]) -> str:
    """A successful call returns the tool's own JSON (as the FDB-v3 templates
    do); anything else tells the LLM what happened and why."""
    if payload.get("status") == "ok":
        return json.dumps(payload.get("result"))
    return json.dumps(payload)


def make_tool(spec: ToolSpec, gate: KeelGate, cfg: KeelConfig, fillers: bool):
    """A LiveKit raw-schema tool whose every call goes through the gate."""
    name = spec.name
    schema = {"name": name, "description": spec.description, "parameters": spec.parameters}
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
        async with context.with_filler(lambda n: gate.filler(step_id, n),
                                       delay=floor.ack_after_ms / 1000, interval=floor.progress_after_ms / 1000,
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
    if cfg.livekit is not None and cfg.livekit.pipeline == "cascaded":
        proc.userdata["vad"] = load_vad(cfg)


def build_session(cfg: KeelConfig, *, vad: Any = None, **session_kwargs: Any) -> tuple[AgentSession, bool, bool]:
    """The configured pipeline. Returns (session, has_tts, drop_on_new_speech)."""
    lk = cfg.livekit
    assert lk is not None
    if lk.pipeline == "cascaded":
        c = lk.cascaded
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
        session = AgentSession(
            vad=vad,
            stt=openai.STT(model=c.stt_model, language=c.stt_language),
            llm=openai.LLM(model=c.llm_model, temperature=c.llm_temperature, extra_body={"seed": c.llm_seed}),
            tts=openai.TTS(model=c.tts_model, voice=c.tts_voice),
            turn_handling={
                "turn_detection": detection,
                "endpointing": {"min_delay": c.min_endpointing_s, "max_delay": c.max_endpointing_s},
                "preemptive_generation": {"enabled": c.preemptive_generation},
            },
            **session_kwargs,
        )
        return session, True, c.drop_on_new_speech
    if lk.pipeline == "gpt_realtime":
        r = lk.realtime
        model = openai.realtime.RealtimeModel(model=r.model, voice=r.voice)
        return AgentSession(llm=model, **session_kwargs), False, r.drop_on_new_speech
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
