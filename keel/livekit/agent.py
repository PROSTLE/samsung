"""Keel's LiveKit voice agent for FDB-v3: the benchmark's agent, with Keel under every tool call.

    python -m keel.livekit.agent download-files   # VAD + turn-detector weights (setup/warm-up)
    python -m keel.livekit.agent start            # worker the FDB-v3 runner streams audio to
    python -m keel.livekit.agent start --latency normal   # same flag as the FDB-v3 templates

It is the FDB-v3 cascaded template's pipeline (Silero VAD, Whisper STT, GPT-4o,
OpenAI TTS; config/fdb_v3.toml). Tools and instructions are read from the
template file itself (keel.livekit.template). What differs from the template:

  1. Every tool call goes through KeelGate (keel.livekit.gate): held until the
     user has finished, dropped if they keep talking before it is sent, and run
     at most once per session. Execution and the tool log are the benchmark's
     own (keel.livekit.fdb).
  2. End of turn uses LiveKit's end-of-utterance model instead of VAD silence
     alone.
  3. Four general instructions are appended to the template's.
  4. The LLM runs at temperature 0 with a fixed seed.
  5. While a call is held or running, Keel may say a short truthful line
     through LiveKit's filler mechanism (livekit.speak_purposes).

Environment (e.g. .env.local; see .env.example):
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, and
    OPENAI_API_KEY (cascaded, gpt_realtime), GOOGLE_API_KEY (gemini_realtime), or the keys
    of the providers [livekit.open] names (pipeline open; none for local servers)
    KEEL_CONFIG    overlay on config/keel.toml (default config/fdb_v3.toml)
    KEEL_FDB_DIR   FDB-v3 checkout's v3/ directory (default from the config)
    KEEL_PIPELINE  "cascaded" | "gpt_realtime" | "gemini_realtime" | "open" (default from the config)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from livekit import agents
from livekit.agents import Agent, AgentServer

from keel.config import KeelConfig, load_config, with_overrides
from keel.kernel.clock import MonotonicClock
from keel.livekit.fdb import FdbBackend, import_mock_apis, load_mock_registry
from keel.livekit.gate import KeelGate
from keel.livekit.session import build_session, make_tool, prewarm_vad, wire
from keel.providers import apply_env_pipeline
from keel.livekit.template import Template, read_template
from keel.trace import TraceWriter
from keel.web import live as live_story

REPO = Path(__file__).resolve().parents[2]
log = logging.getLogger("keel.livekit")


def pop_latency_flag(argv: list[str]) -> Optional[str]:
    """`--latency <profile>`, as the FDB-v3 templates accept; removed from argv
    so the LiveKit CLI does not reject it."""
    if "--latency" in argv:
        i = argv.index("--latency")
        if i + 1 < len(argv):
            value = argv[i + 1]
            del argv[i:i + 2]
            return value
    return None


def load_env(*dirs: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    paths = [Path(os.environ["KEEL_ENV_FILE"])] if os.getenv("KEEL_ENV_FILE") else []
    paths += [REPO / ".env", REPO / ".env.local", *(d / ".env.local" for d in dirs)]
    for path in paths:
        if path.exists():
            load_dotenv(path, override=False)


def session_id(room: str) -> str:
    sid = re.sub(r"[^A-Za-z0-9._:-]", "-", room)[:100] or "room"
    return sid if sid[0].isalnum() else f"r{sid}"


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO / p


@dataclass(frozen=True)
class Settings:
    config: KeelConfig
    fdb_dir: Path
    template: Template


def load_settings() -> Settings:
    cfg = load_config(None, os.getenv("KEEL_CONFIG", str(REPO / "config" / "fdb_v3.toml")))
    if cfg.livekit is None or cfg.livekit.fdb is None:
        raise SystemExit("the config has no [livekit.fdb] section; set KEEL_CONFIG to config/fdb_v3.toml")
    cfg = apply_env_pipeline(cfg)
    if os.getenv("KEEL_LATENCY_PROFILE"):
        fdb = {**cfg.livekit.fdb.model_dump(), "latency_profile": os.environ["KEEL_LATENCY_PROFILE"]}
        cfg = with_overrides(cfg, livekit={"fdb": fdb})
    fdb_dir = resolve(os.getenv("KEEL_FDB_DIR", cfg.livekit.fdb.fdb_dir))
    template = read_template(fdb_dir / cfg.livekit.fdb.template)
    if not template.instructions:
        raise SystemExit(f"no Agent instructions found in {template.path}")
    return Settings(config=cfg, fdb_dir=fdb_dir, template=template)


class LatencyTracker:
    """Writes the FDB-v3 templates' LATENCY_TRACK_JSON heartbeat line, which the
    runner turns into `search_latency_breakdown` (run_tool_benchmark.py step 4.6).
    Same fields and the same moments as the templates' tracker."""

    def __init__(self, path: Path, room: str) -> None:
        self.path, self.room = path, room
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.reset()

    def reset(self) -> None:
        self.user_done_at = self.tool_start_at = self.tool_end_at = self.agent_start_at = 0.0
        self.tool = ""
        self.query_received = False

    def executed(self, tool: str, t_start: float, t_end: float) -> None:
        self.tool, self.tool_start_at, self.tool_end_at = tool, t_start, t_end

    def user_final(self) -> None:
        if not self.query_received:
            self.user_done_at, self.query_received = time.time(), True

    def agent_speaking(self) -> None:
        if not self.query_received or self.agent_start_at:
            return
        self.agent_start_at = time.time()
        if self.tool_start_at:
            metrics = {
                "room": self.room, "tool": self.tool,
                "reasoning": round(self.tool_start_at - self.user_done_at, 3),
                "execution": round(self.tool_end_at - self.tool_start_at, 3),
                "synthesis": round(self.agent_start_at - (self.tool_end_at or self.user_done_at), 3),
                "total": round(self.agent_start_at - self.user_done_at, 3),
                "agent_start_at": self.agent_start_at,
            }
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"LATENCY_TRACK_JSON: {json.dumps(metrics)}\n")
        self.reset()


# LiveKit stops giving a worker jobs once the machine's CPU load passes
# load_threshold (0.7 by default in production). FDB-v3 runs its own speech
# recognition on the same machine between scenarios, and this agent handles one
# conversation at a time, so machine-wide load says little about its capacity.
# LiveKit requires a value below 1 in production.
server = AgentServer(load_threshold=0.95)
_SETTINGS: Optional[Settings] = None


def settings() -> Settings:
    """Loaded on first use in each process. On Linux and macOS LiveKit runs jobs
    in separate processes, which inherit the environment but not this module's
    globals (on Windows it runs them in threads, where this is simply cached)."""
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = load_settings()
    return _SETTINGS


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext) -> None:
    s = settings()
    cfg = s.config
    lk = cfg.livekit
    assert lk is not None and lk.fdb is not None
    room = ctx.room.name
    loop = asyncio.get_running_loop()
    clock = MonotonicClock(loop)
    sid = session_id(room)
    trace = TraceWriter(resolve(lk.trace_dir) / f"{sid}.jsonl", clock=clock, session_id=sid, config=cfg,
                        synthetic=False, scenario=room)
    live_story.attach(trace, ctx.room, loop)   # the web app's Live page, if one joined
    tracker = LatencyTracker(Path(lk.fdb.heartbeat_log), room)
    backend = FdbBackend(template=s.template, registry=load_mock_registry(s.fdb_dir, lk.fdb.latency_profile),
                         room=room, tool_log=lk.fdb.tool_log, seed=lk.fdb.seed, on_executed=tracker.executed)
    session, has_tts, drop = build_session(cfg, vad=ctx.proc.userdata.get("vad"))
    gate = KeelGate(session_id=sid, config=cfg, tools=s.template.specs(), execute=backend.execute, trace=trace,
                    clock=clock, loop=loop, drop_on_new_speech=drop, complete_arguments=backend.arguments)
    # Fillers are spoken with session.say, which needs a TTS.
    fillers = has_tts and bool(lk.speak_purposes)
    tools = [make_tool(t.spec, gate, cfg, fillers) for t in s.template.tools]
    gate.note("session_config", pipeline=lk.pipeline, latency_profile=lk.fdb.latency_profile,
              template=s.template.path.name, tools=[t.spec.name for t in s.template.tools],
              fillers=fillers, drop_on_new_speech=drop)
    wire(session, gate, on_user_final=tracker.user_final, on_agent_speaking=tracker.agent_speaking)
    ctx.add_shutdown_callback(gate.aclose)
    instructions = s.template.instructions + "\n" + lk.fdb.extra_instructions.strip()
    await session.start(room=ctx.room, agent=Agent(instructions=instructions, tools=tools), record=lk.record)
    log.info("keel agent started in room %s (pipeline=%s)", room, lk.pipeline)


def setup(proc: agents.JobProcess) -> None:
    """Job-process warm-up. Module-level so it can be pickled (see prewarm_vad)."""
    s = settings()
    prewarm_vad(proc, s.config)
    import_mock_apis(s.fdb_dir)


server.setup_fnc = setup


def main() -> None:
    latency = pop_latency_flag(sys.argv)
    if latency:
        os.environ["KEEL_LATENCY_PROFILE"] = latency   # inherited by job processes
    load_env()                      # the repo's .env first: it may set KEEL_* too
    s = settings()
    load_env(s.fdb_dir)             # then FDB-v3's v3/.env.local
    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
