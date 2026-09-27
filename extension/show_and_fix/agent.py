"""Show & Fix: a Samsung washer troubleshooting voice agent that looks through the camera.

EXTENSION USE CASE (Theme 05 guide §3, step 4): beyond FDB-v3's four domains.

    python -m extension.show_and_fix.agent download-files
    python -m extension.show_and_fix.agent dev

Then open Keel's web app (python -m keel.web, Live demo) or your LiveKit Cloud
project's Agent Console, turn on the microphone and the camera, and point the
camera at the washer's display. KEEL_PIPELINE=gemini_realtime runs it on the
Gemini API's free tier alone (Gemini Live, and a Gemini model reading the
display); KEEL_PIPELINE=open on the open-weight models of [livekit.open]. For a run without a camera, KEEL_SHOW_AND_FIX_IMAGE=<path to
a photo> makes the agent use that still image; the trace marks it as a file.

Same pipeline and the same Keel layer as the benchmark agent (keel.livekit):
  * read_error_display: the latest camera frame, read by a vision model; below
    perception.clarify_below it answers "unclear" and the agent asks instead;
  * lookup_error_code: only Samsung's published table (washer_codes.json);
  * book_technician: state-changing, so Keel fences it until the user has
    finished; a correction ("Friday, no, Saturday") drops the held Friday call,
    and an identical request later in the session is not booked twice;
  * list_technician_bookings: read-only; Keel's manifest probe uses it to
    answer "did it go through?".
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from livekit import agents, rtc
from livekit.agents import Agent, AgentServer, room_io
from livekit.agents.utils.images import EncodeOptions, ResizeOptions, encode

from extension.show_and_fix.backend import FrameStore, Manual, ShowAndFixBackend, reader_for
from keel.config import KeelConfig, load_config
from keel.kernel.clock import MonotonicClock
from keel.livekit.agent import load_env, resolve, session_id
from keel.livekit.gate import KeelGate
from keel.livekit.session import build_session, make_tool, prewarm_vad, wire
from keel.providers import apply_env_pipeline
from keel.protocol.provisional import ToolSpec
from keel.trace import TraceWriter
from keel.web import live as live_story

REPO = Path(__file__).resolve().parents[2]
log = logging.getLogger("keel.show_and_fix")
# Downscaled before it is sent to the vision model; a display is legible at this size.
FRAME_ENCODE = EncodeOptions(format="JPEG", quality=85,
                             resize_options=ResizeOptions(width=1024, height=1024, strategy="scale_aspect_fit"))


def load() -> tuple[KeelConfig, list[ToolSpec], str]:
    cfg = apply_env_pipeline(load_config(None, os.getenv("KEEL_CONFIG", str(REPO / "config" / "show_and_fix.toml"))))
    if cfg.livekit is None or cfg.livekit.show_and_fix is None:
        raise SystemExit("the config has no [livekit.show_and_fix] section")
    sf = cfg.livekit.show_and_fix
    tools = [ToolSpec(**t) for t in json.loads(resolve(sf.manifest).read_text(encoding="utf-8"))["tools"]]
    instructions = resolve(sf.instructions).read_text(encoding="utf-8").strip()
    return cfg, tools, instructions


async def _pump_frames(track: rtc.Track, frames: FrameStore) -> None:
    stream = rtc.VideoStream(track)
    try:
        async for ev in stream:
            frames.put(ev.frame, source="camera", encode=lambda f: encode(f, FRAME_ENCODE))
    finally:
        await stream.aclose()


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext) -> None:
    cfg, specs, instructions = load()
    lk = cfg.livekit
    assert lk is not None and lk.show_and_fix is not None
    sf = lk.show_and_fix
    loop = asyncio.get_running_loop()
    clock = MonotonicClock(loop)
    sid = session_id(ctx.room.name)
    trace = TraceWriter(resolve(lk.trace_dir) / f"{sid}.jsonl", clock=clock, session_id=sid, config=cfg,
                        synthetic=False, scenario=f"show_and_fix:{ctx.room.name}")
    live_story.attach(trace, ctx.room, loop)   # the web app's Live page, if one joined
    frames = FrameStore()
    if os.getenv("KEEL_SHOW_AND_FIX_IMAGE"):
        path = Path(os.environ["KEEL_SHOW_AND_FIX_IMAGE"])
        frames.put(path.read_bytes(), source=f"file:{path.name}")

    session, has_tts, drop = build_session(cfg, vad=ctx.proc.userdata.get("vad"))
    backend = ShowAndFixBackend(manual=Manual(resolve(sf.manual)), frames=frames,
                                reader=reader_for(cfg),
                                session_id=sid, clarify_below=cfg.perception.clarify_below,
                                frame_max_age_s=sf.frame_max_age_s)
    gate = KeelGate(session_id=sid, config=cfg, tools=specs, execute=backend.execute, trace=trace,
                    clock=clock, loop=loop, drop_on_new_speech=drop)
    backend.on_perception = lambda rec: gate.note("perception", **rec)
    tools = [make_tool(spec, gate, cfg, has_tts and bool(lk.speak_purposes)) for spec in specs]
    wire(session, gate)

    # asyncio keeps only weak references to tasks: hold the frame pumps here, or
    # one can be garbage-collected mid-stream and the camera silently freezes.
    pumps: set[asyncio.Task] = set()

    @ctx.room.on("track_subscribed")
    def _on_track(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant) -> None:
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            gate.note("camera", participant=participant.identity, event="subscribed")
            task = asyncio.ensure_future(_pump_frames(track, frames))
            pumps.add(task)
            task.add_done_callback(pumps.discard)

    async def _shutdown() -> None:
        for task in list(pumps):
            task.cancel()
        await gate.aclose()

    ctx.add_shutdown_callback(_shutdown)
    await session.start(room=ctx.room, agent=Agent(instructions=instructions, tools=tools),
                        room_options=room_io.RoomOptions(video_input=True))
    log.info("show & fix agent started in room %s", ctx.room.name)


def setup(proc: agents.JobProcess) -> None:
    """Job-process warm-up. Module-level so it can be pickled (see prewarm_vad)."""
    prewarm_vad(proc, load()[0])


server.setup_fnc = setup


def main() -> None:
    load_env()
    load()   # fail fast on a broken config, before the worker starts
    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
