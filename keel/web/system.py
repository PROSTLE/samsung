"""What the web app's Setup page reports: keys, profiles, the FDB-v3 checkout, tools, checks.

Everything is read from the environment, the config profiles in config/ and the
files on disk at request time. A key's value is never returned, only whether it
is set. The checks make one real request each and report exactly what came back.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Optional

from keel.config import KeelConfig, load_config

# Environment variables the agents read (see .env.example). Only presence is reported.
KEYS = {
    "livekit": ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GOOGLE_API_KEY",),
    "groq": ("GROQ_API_KEY",),
}


def key_status() -> dict[str, bool]:
    return {name: all(os.getenv(k) for k in names) for name, names in KEYS.items()}


def load_profiles(root: Path) -> dict[str, KeelConfig]:
    """Every file in config/ that is a complete LiveKit profile on top of keel.toml
    (config/fdb_v3.toml, config/show_and_fix.toml, ...), by file stem."""
    out: dict[str, KeelConfig] = {}
    base = root / "config" / "keel.toml"
    for path in sorted((root / "config").glob("*.toml")):
        if path.name == base.name:
            continue
        try:
            cfg = load_config(base, path)
        except Exception:  # noqa: BLE001 - an overlay that is not a complete profile
            continue
        if cfg.livekit is not None:
            out[path.stem] = cfg
    return out


def pipeline_models(cfg: KeelConfig) -> dict[str, dict[str, Any]]:
    """The models each pipeline of a profile would use, as configured, and the
    providers it calls (with the Show & Fix display reader for that pipeline)."""
    from keel.config import with_overrides
    from keel.providers import pipeline_providers

    lk = cfg.livekit
    assert lk is not None
    c = lk.cascaded
    out: dict[str, dict[str, Any]] = {
        "cascaded": {"stt": c.stt_model, "llm": c.llm_model, "tts": f"{c.tts_model} ({c.tts_voice})",
                     "turn_detector": c.turn_detector},
        "gpt_realtime": {"model": lk.realtime.model, "voice": lk.realtime.voice},
    }
    if lk.gemini is not None:
        out["gemini_realtime"] = {"model": lk.gemini.model, "voice": lk.gemini.voice}
    if lk.open is not None:
        o = lk.open
        out["open"] = {"stt": f"{o.stt_provider}: {o.stt_model}", "llm": f"{o.llm_provider}: {o.llm_model}",
                       "tts": f"{o.tts_provider}: {o.tts_model} ({o.tts_voice})", "turn_detector": c.turn_detector}
    for name, models in out.items():
        variant = with_overrides(cfg, livekit={"pipeline": name})
        if lk.show_and_fix is not None:
            provider, model = lk.show_and_fix.vision_for(name)
            models["reads the display"] = f"{provider}: {model}"
        models["providers"] = pipeline_providers(variant)
    return out


def provider_table(cfg: KeelConfig) -> list[dict[str, Any]]:
    """[providers]: where each points and whether its key is set (never the key)."""
    return [{"name": name, "base_url": p.base_url or "https://api.openai.com/v1", "key_env": p.key_env or None,
             "key_set": bool(os.getenv(p.key_env)) if p.key_env else None, "local": not p.key_env}
            for name, p in cfg.providers.items()]


def profile_summary(name: str, cfg: KeelConfig) -> dict[str, Any]:
    lk = cfg.livekit
    assert lk is not None
    f = cfg.fence
    return {
        "name": name,
        "pipeline": lk.pipeline,
        "pipelines": pipeline_models(cfg),
        "trace_dir": lk.trace_dir,
        "fence": {"quiet_ms": f.quiet_ms, "hold_reads": f.hold_reads, "repair_wait_ms": f.repair_wait_ms,
                  "transcript_wait_ms": f.transcript_wait_ms, "stale_turn_ms": f.stale_turn_ms,
                  "editing_terms": list(f.editing_terms)},
        "speak_purposes": list(lk.speak_purposes),
        "vision_model": lk.show_and_fix.vision_model if lk.show_and_fix else None,
        "fdb": ({"template": lk.fdb.template, "latency_profile": lk.fdb.latency_profile, "seed": lk.fdb.seed}
                if lk.fdb else None),
    }


def git_commit(repo: Path) -> Optional[str]:
    """HEAD commit of a checkout, read from .git without running git."""
    git = repo / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head
        ref = head[4:].strip()
        if (git / ref).is_file():
            return (git / ref).read_text(encoding="utf-8").strip()
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split()[0]
    except OSError:
        return None
    return None


def fdb_status(fdb_dir: Path, template: Optional[str]) -> dict[str, Any]:
    data = fdb_dir / "fdb_v3_data_released"
    scenarios = [p for p in data.iterdir() if p.is_dir()] if data.is_dir() else []
    return {
        "dir": str(fdb_dir),
        "present": fdb_dir.is_dir(),
        "commit": git_commit(fdb_dir.parent) if fdb_dir.is_dir() else None,
        "template_present": bool(template) and (fdb_dir / str(template)).is_file(),
        "recordings": sum((p / "input.wav").is_file() for p in scenarios),
        "scenario_results": sum(any(p.glob("result_*.json")) for p in scenarios),
    }


def tool_table(specs: list[Any], cfg: KeelConfig) -> list[dict[str, Any]]:
    """Each tool with the label Keel's manifest compiler gives it (the kernel's own compiler)."""
    from keel.compiler.manifest import default_compiler

    policies = default_compiler(cfg.compiler.min_confidence)(list(specs))
    rows = []
    for spec in specs:
        p = policies[spec.name]
        props = (spec.parameters or {}).get("properties") or {}
        rows.append({
            "name": spec.name, "description": spec.description, "parameters": list(props),
            "safety": p.safety, "method": p.method, "confidence": round(p.confidence, 2),
            "evidence": list(p.evidence),
        })
    return rows


def profile_tools(root: Path, cfg: KeelConfig, fdb_dir: Path) -> tuple[list[dict[str, Any]], Optional[str]]:
    """(tools, why not) for one profile: FDB-v3's template tools, or Show & Fix's manifest."""
    lk = cfg.livekit
    assert lk is not None
    try:
        if lk.fdb is not None:
            path = fdb_dir / lk.fdb.template
            if not path.is_file():
                return [], f"FDB-v3's {lk.fdb.template} is not at {fdb_dir} (set KEEL_FDB_DIR)"
            from keel.livekit.template import read_template

            return tool_table(read_template(path).specs(), cfg), None
        if lk.show_and_fix is not None:
            import json

            from keel.protocol.provisional import ToolSpec

            manifest = json.loads((root / lk.show_and_fix.manifest).read_text(encoding="utf-8"))
            return tool_table([ToolSpec(**t) for t in manifest["tools"]], cfg), None
    except Exception as e:  # noqa: BLE001 - reported on the page, not fatal
        return [], f"{type(e).__name__}: {e}"
    return [], "this profile declares no tools"


# ------------------------------------------------------------------ checks
async def check_livekit() -> dict[str, Any]:
    """List the project's rooms with the configured keys: proves the URL, key and secret."""
    url, key, secret = (os.getenv(k) for k in KEYS["livekit"])
    if not (url and key and secret):
        return {"ok": False, "detail": "LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET are not all set"}
    from livekit import api

    http = url.replace("wss://", "https://").replace("ws://", "http://")
    t0 = time.perf_counter()
    lk = api.LiveKitAPI(http, key, secret)
    try:
        rooms = await asyncio.wait_for(lk.room.list_rooms(api.ListRoomsRequest()), timeout=10)
        n = len(rooms.rooms)
        return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000),
                "detail": f"LiveKit accepted the keys; {n} room{'s' if n != 1 else ''} open now"}
    except Exception as e:  # noqa: BLE001 - any refusal is the answer
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    finally:
        await lk.aclose()


async def check_provider(name: str, cfg: KeelConfig) -> dict[str, Any]:
    """One minimal request to a model provider, as the benchmark's preflight makes."""
    from keel.livekit import preflight

    fn = preflight.CHECKS.get(name)
    if fn is None:
        return {"ok": False, "detail": f"no check for {name!r}"}
    t0 = time.perf_counter()
    try:
        detail = await asyncio.wait_for(asyncio.to_thread(fn, cfg), timeout=45)
        return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000), "detail": detail}
    except ModuleNotFoundError as e:
        return {"ok": False, "detail": f"{e.name} is not installed in the environment running this app"}
    except Exception as e:  # noqa: BLE001 - bad key, no credit, no model access, network
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"[:400]}
