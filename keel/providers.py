"""Model endpoints by name ([providers] in config/keel.toml), resolved at run time.

Every provider speaks OpenAI's API (OpenAI itself, Gemini's compatibility
layer, Groq, a local Ollama or Speaches server), so one client serves them all;
only the base URL and the key differ. Keys come from the environment only.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Optional

from keel.config import KeelConfig, with_overrides

# The OpenAI SDK refuses an empty key; a local server ignores whatever it is sent.
NO_KEY = "not-needed"
PIPELINES = ("cascaded", "gpt_realtime", "gemini_realtime", "open")


def apply_env_pipeline(cfg: KeelConfig) -> KeelConfig:
    """KEEL_PIPELINE overrides the profile's pipeline (both agents, preflight, the benchmark script)."""
    name = os.getenv("KEEL_PIPELINE")
    if not name:
        return cfg
    if name not in PIPELINES:
        raise SystemExit(f"KEEL_PIPELINE={name!r}: must be one of {', '.join(PIPELINES)}")
    return with_overrides(cfg, livekit={"pipeline": name})


class ProviderError(RuntimeError):
    pass


def endpoint(cfg: KeelConfig, name: str) -> tuple[Optional[str], str]:
    """(base_url, api_key) of a provider. base_url None = the SDK's default (OpenAI)."""
    p = cfg.providers.get(name)
    if p is None:
        if name == "openai":
            return None, os.environ.get("OPENAI_API_KEY", "")
        raise ProviderError(f"unknown model provider {name!r}: add [providers.{name}] to config/keel.toml")
    if not p.key_env:
        return p.base_url or None, NO_KEY
    key = os.environ.get(p.key_env, "")
    if not key:
        raise ProviderError(f"{p.key_env} is not set; provider {name!r} needs it")
    return p.base_url or None, key


def accepts_seed(cfg: KeelConfig, name: str) -> bool:
    p = cfg.providers.get(name)
    return True if p is None else p.seed


def key_envs(cfg: KeelConfig, names: list[str]) -> list[str]:
    """The environment variables these providers need (for a run's key check)."""
    out: list[str] = []
    for name in names:
        p = cfg.providers.get(name)
        env = "OPENAI_API_KEY" if p is None and name == "openai" else (p.key_env if p else "")
        if env and env not in out:
            out.append(env)
    return out


def describe_open(cfg: KeelConfig, root: Path) -> str:
    """The open pipeline's models, as a run records them (run_info.txt). A model
    served by the local Ollama gets its digest: the sha256 of its manifest, which
    is the ID `ollama list` shows, read from disk so the server need not be running."""
    assert cfg.livekit is not None and cfg.livekit.open is not None, "no [livekit.open] section"
    o = cfg.livekit.open
    parts = [f"stt {o.stt_provider}/{o.stt_model}", f"llm {o.llm_provider}/{o.llm_model}",
             f"tts {o.tts_provider}/{o.tts_model} voice {o.tts_voice}"]
    if o.llm_provider == "ollama":
        name, _, tag = o.llm_model.partition(":")
        manifest = (root / "third_party" / "ollama" / "models" / "manifests" / "registry.ollama.ai"
                    / ("library/" + name if "/" not in name else name) / (tag or "latest"))
        if manifest.is_file():
            parts.append(f"ollama digest {hashlib.sha256(manifest.read_bytes()).hexdigest()[:12]}")
    return "; ".join(parts)


def main(argv: list[str]) -> int:
    from keel.config import load_config

    if argv[:1] != ["describe"]:
        print("usage: python -m keel.providers describe", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    cfg = apply_env_pipeline(load_config(None, os.getenv("KEEL_CONFIG", str(root / "config" / "fdb_v3.toml"))))
    print(describe_open(cfg, root))
    return 0


def pipeline_providers(cfg: KeelConfig) -> list[str]:
    """Providers the configured pipeline calls (the Show & Fix display reader included)."""
    lk = cfg.livekit
    if lk is None:
        return []
    names: list[str]
    if lk.pipeline in ("cascaded", "gpt_realtime"):
        names = ["openai"]
    elif lk.pipeline == "gemini_realtime":
        names = ["gemini"]
    elif lk.pipeline == "open" and lk.open is not None:
        names = [lk.open.stt_provider, lk.open.llm_provider, lk.open.tts_provider]
    else:
        names = []
    if lk.show_and_fix is not None:
        names.append(lk.show_and_fix.vision_for(lk.pipeline)[0])
    return list(dict.fromkeys(names))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
