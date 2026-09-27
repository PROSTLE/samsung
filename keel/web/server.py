"""Keel's web app: overview, live conversations, session replays, benchmark results, setup.

    python -m keel.web                 # http://127.0.0.1:8765
    python -m keel.web --port 9000

Binds to 127.0.0.1 only. The Live page joins a LiveKit room from the browser
with a token this server signs (LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET
from .env); the agent, started separately (`python -m keel.livekit.agent dev`),
is dispatched to that room automatically and publishes its Keel events to it.
Sessions are read from each profile's trace directory (config/*.toml,
livekit.trace_dir) and from results/fdb_v3/*/traces; results from results/fdb_v3/.
A benchmark session replays with FDB-v3's own recordings when its checkout
(KEEL_FDB_DIR) still holds them.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import secrets
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Optional

from aiohttp import web

from keel.config import with_overrides
from keel.providers import PIPELINES
from keel.trace import read_trace
from keel.web import system
from keel.web.results import (audio_offset_ms, fdb_recordings, list_runs, run_detail, scenario_titles,
                              scenario_verdict)
from keel.web.story import STORY_TOPIC, WEB_IDENTITY_PREFIX, Story

REPO = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"
EXAMPLES = Path(__file__).resolve().parent / "examples.json"
# A trace without a session_end record that changed this recently is a session
# still running; an older one ended without closing (a killed run).
LIVE_WINDOW_S = 120


class Sessions:
    """Trace files by session id, each parsed once per change."""

    def __init__(self, root: Path, trace_dirs: dict[str, str]) -> None:
        self.root = root
        self.trace_dirs = trace_dirs          # profile name -> trace_dir (relative to root)
        self._cache: dict[Path, tuple[int, int, Story]] = {}

    def files(self) -> dict[str, tuple[Path, str]]:
        """session id -> (newest trace file with that name, profile it came from)."""
        found: dict[str, tuple[Path, str]] = {}
        sources = [(profile, self.root / d, "**/*.jsonl") for profile, d in self.trace_dirs.items()]
        sources.append(("fdb_v3", self.root / "results" / "fdb_v3", "*/traces/*.jsonl"))
        for profile, base, pattern in sources:
            if not base.is_dir():
                continue
            for p in base.glob(pattern):
                if p.stem not in found or p.stat().st_mtime > found[p.stem][0].stat().st_mtime:
                    found[p.stem] = (p, profile)
        return found

    def story(self, path: Path) -> Story:
        st = path.stat()
        hit = self._cache.get(path)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
        story = Story()
        try:
            story.feed_all(read_trace(path))
        except ValueError:
            pass     # a trace being written can end in half a line; show what is complete
        self._cache[path] = (st.st_mtime_ns, st.st_size, story)
        return story


def source_of(sid: str, profile: str, benchmark_rooms: set[str]) -> str:
    """benchmark | live | show_and_fix | other. Rooms come from FDB-v3's runner
    (named in its result files) or from this app (keel-live-, see token())."""
    if profile == "show_and_fix":
        return "show_and_fix"
    if sid in benchmark_rooms or sid.startswith("eval-"):
        return "benchmark"
    if sid.startswith("keel-live-"):
        return "live"
    return "other"


class App:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.results = root / "results" / "fdb_v3"
        # The checkout's own profiles; a --root without config/ (a copy holding
        # only traces and results) uses this package's.
        self.profiles = system.load_profiles(root if (root / "config" / "keel.toml").is_file() else REPO)
        fdb_default = next((c.livekit.fdb.fdb_dir for c in self.profiles.values()
                            if c.livekit and c.livekit.fdb), "third_party/Full-Duplex-Bench/v3")
        fdb_dir = Path(os.getenv("KEEL_FDB_DIR", fdb_default))
        self.fdb_dir = fdb_dir if fdb_dir.is_absolute() else root / fdb_dir
        self.fdb_data = self.fdb_dir / "fdb_v3_data_released"
        trace_dirs = {name: c.livekit.trace_dir for name, c in self.profiles.items() if c.livekit}
        self.sessions = Sessions(root, trace_dirs)

    # ------------------------------------------------------------ helpers
    def _titles(self) -> dict:
        return scenario_titles(self.results, self.fdb_data)

    def _rows(self) -> list[dict[str, Any]]:
        titles = self._titles()
        benchmark_rooms = set(titles)
        now = time.time()
        rows = []
        for sid, (path, profile) in self.sessions.files().items():
            story = self.sessions.story(path)
            s = story.summary()
            mtime = path.stat().st_mtime
            s.update(id=sid, modified=mtime, profile=profile, **titles.get(sid, {}))
            s.setdefault("title", None)
            s["source"] = source_of(sid, profile, benchmark_rooms)
            s["status"] = ("complete" if s["duration_ms"] is not None
                           else "live" if now - mtime < LIVE_WINDOW_S else "incomplete")
            rows.append(s)
        rows.sort(key=lambda r: r["modified"], reverse=True)
        return rows

    # ------------------------------------------------------------ pages
    async def index(self, _: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC / "index.html")

    # -------------------------------------------------------------- api
    async def config(self, _: web.Request) -> web.Response:
        keys = system.key_status()
        return web.json_response({"live_available": keys["livekit"], "keys": keys})

    async def token(self, _: web.Request) -> web.Response:
        url, key, secret = (os.getenv(k) for k in system.KEYS["livekit"])
        if not (url and key and secret):
            return web.json_response({"error": "LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET are not set"},
                                     status=503)
        from livekit import api

        room = f"keel-live-{secrets.token_hex(4)}"
        identity = f"{WEB_IDENTITY_PREFIX}{secrets.token_hex(4)}"
        jwt = (api.AccessToken(key, secret).with_identity(identity).with_name("You")
               .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True,
                                            can_publish_data=True))
               .to_jwt())
        return web.json_response({"url": url, "token": jwt, "room": room, "identity": identity,
                                  "story_topic": STORY_TOPIC})

    async def overview(self, _: web.Request) -> web.Response:
        rows = [r for r in self._rows() if not r.get("synthetic")]
        files = self.sessions.files()
        holds = [ms for r in rows for ms in self.sessions.story(files[r["id"]][0]).hold_times_ms()]
        runs = list_runs(self.results)
        complete = [r for r in runs if r["complete"]]
        latest = max(complete, key=lambda r: ((r["scenarios"] or 0) > 1, r["id"]), default=None)
        by_source: dict[str, int] = {}
        for r in rows:
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        totals = {k: sum(r[k] for r in rows) for k in ("actions", "executed", "failed", "not_sent", "reused", "held")}
        # The session to offer as a replay: one where Keel dropped a call, with
        # a recording if possible, most recent first.
        recorded = set(fdb_recordings(self.fdb_data))
        complete = [r for r in rows if r["status"] == "complete" and r["actions"]]
        demo = max(complete, key=lambda r: (r["not_sent"] > 0, r["id"] in recorded, r["modified"]), default=None)
        return web.json_response({
            "sessions": len(rows), "by_source": by_source, **totals,
            "demo_session": demo["id"] if demo else None,
            "hold_ms_median": statistics.median(holds) if holds else None,
            "latest_run": latest, "runs": len(runs),
            "recent": rows[:5],
        })

    async def session_list(self, _: web.Request) -> web.Response:
        return web.json_response(self._rows())

    def _recording(self, sid: str, story: Story) -> Optional[dict[str, Any]]:
        rec = fdb_recordings(self.fdb_data).get(sid)
        if rec is None:
            return None
        sent = [(str(a["tool"]), h["t"]) for a in story.actions.values() for h in a["history"] if h["state"] == "sent"]
        offset, anchor = audio_offset_ms(rec, story.unix_ms_at_zero, sorted(sent, key=lambda x: x[1]))
        if offset is None:
            return {"available": False, "why": "the trace has no clock anchor and no executed call to line it up by"}
        return {"available": True, "offset_ms": offset, "anchor": anchor,
                "user": f"/api/sessions/{sid}/audio/user" if rec["user"] else None,
                "agent": f"/api/sessions/{sid}/audio/agent" if rec["agent"] else None}

    async def session(self, request: web.Request) -> web.Response:
        sid = request.match_info["sid"]
        found = self.sessions.files().get(sid)
        if found is None:
            raise web.HTTPNotFound()
        path, profile = found
        story = self.sessions.story(path)
        titles = self._titles()
        summary = story.summary()
        summary.update(id=sid, profile=profile, source=source_of(sid, profile, set(titles)), **titles.get(sid, {}))
        verdict = None
        if summary.get("run") and summary.get("scenario"):
            verdict = scenario_verdict(self.results / summary["run"], summary["scenario"])
        return web.json_response({"summary": summary, "events": story.events,
                                  "audio": self._recording(sid, story), "verdict": verdict})

    async def audio(self, request: web.Request) -> web.StreamResponse:
        sid, which = request.match_info["sid"], request.match_info["which"]
        rec = fdb_recordings(self.fdb_data).get(sid)
        path = rec.get(which) if rec and which in ("user", "agent") else None
        if path is None:
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Content-Type": "audio/wav"})

    async def runs(self, _: web.Request) -> web.Response:
        return web.json_response(list_runs(self.results))

    async def run(self, request: web.Request) -> web.Response:
        rid = request.match_info["rid"]
        run = self.results / rid
        if not rid or "/" in rid or "\\" in rid or rid.startswith(".") or not run.is_dir():
            raise web.HTTPNotFound()
        return web.json_response(run_detail(run))

    async def system_info(self, _: web.Request) -> web.Response:
        profiles = []
        for name, cfg in self.profiles.items():
            p = system.profile_summary(name, cfg)
            p["tools"], p["tools_note"] = system.profile_tools(self.root, cfg, self.fdb_dir)
            profiles.append(p)
        fdb_template = next((c.livekit.fdb.template for c in self.profiles.values() if c.livekit and c.livekit.fdb),
                            None)
        base = self.profiles.get("fdb_v3") or next(iter(self.profiles.values()), None)
        return web.json_response({
            "keys": system.key_status(),
            "providers": system.provider_table(base) if base else [],
            "profiles": profiles,
            "fdb": system.fdb_status(self.fdb_dir, fdb_template),
            "keel_commit": system.git_commit(self.root),
            "python": sys.version.split()[0],
        })

    async def check(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name == "livekit":
            return web.json_response(await system.check_livekit())
        # "vision" reads with Show & Fix's display reader for ?pipeline= (default
        # gemini_realtime, the free one); "open" asks every stage of the open pipeline.
        if name == "vision":
            profile = self.profiles.get("show_and_fix")
            pipeline = request.query.get("pipeline", "gemini_realtime")
        else:
            profile = self.profiles.get("fdb_v3") or next(iter(self.profiles.values()), None)
            pipeline = "open" if name == "open" else None
        if profile is None:
            return web.json_response({"ok": False, "detail": "no matching LiveKit profile in config/"})
        if pipeline:
            if pipeline not in PIPELINES:
                return web.json_response({"ok": False, "detail": f"unknown pipeline {pipeline!r}"}, status=400)
            profile = with_overrides(profile, livekit={"pipeline": pipeline})
        return web.json_response(await system.check_provider(name, profile))

    async def examples(self, _: web.Request) -> web.Response:
        try:
            return web.json_response(json.loads(EXAMPLES.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return web.json_response({})


@web.middleware
async def _no_cache(request: web.Request, handler):  # type: ignore[no-untyped-def]
    # A local tool that changes with the checkout: never show a stale page.
    response = await handler(request)
    if "/audio/" not in request.path:          # recordings never change; let the browser keep them
        response.headers["Cache-Control"] = "no-cache"
    return response


def make_app(root: Path = REPO) -> web.Application:
    a = App(root)
    app = web.Application(middlewares=[_no_cache])
    app.router.add_get("/", a.index)
    app.router.add_get("/api/config", a.config)
    app.router.add_post("/api/token", a.token)
    app.router.add_get("/api/overview", a.overview)
    app.router.add_get("/api/sessions", a.session_list)
    app.router.add_get("/api/sessions/{sid}", a.session)
    app.router.add_get("/api/sessions/{sid}/audio/{which}", a.audio)
    app.router.add_get("/api/runs", a.runs)
    app.router.add_get("/api/runs/{rid}", a.run)
    app.router.add_get("/api/system", a.system_info)
    app.router.add_post("/api/check/{name}", a.check)
    app.router.add_get("/api/examples", a.examples)
    app.router.add_static("/static/", STATIC, show_index=False)
    return app


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m keel.web", description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", type=Path, default=REPO, help="repository root (config/, traces/, results/)")
    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(args.root / ".env", override=False)
    except ImportError:
        pass
    try:
        web.run_app(make_app(args.root), host="127.0.0.1", port=args.port,
                    print=lambda _: print(f"Keel web app: http://127.0.0.1:{args.port}"))
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, 10048):
            raise SystemExit(f"port {args.port} is in use by another program; start with --port <another port>")
        raise
