"""Check, before a benchmark run, that each model provider it needs accepts the key.

    python -m keel.livekit.preflight openai gemini
    KEEL_PIPELINE=open python -m keel.livekit.preflight open
    KEEL_CONFIG=config/show_and_fix.toml KEEL_PIPELINE=gemini_realtime python -m keel.livekit.preflight vision

A key that lists models can still have no credit or no access to a model (an
OpenAI account with no credit lists models fine, then refuses every
transcription), and the benchmark would then record a silent agent for every
scenario. One minimal request per provider shows that before the long run.
The models are the configured ones (config/fdb_v3.toml, or KEEL_CONFIG).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from keel.config import KeelConfig, load_config

REPO = Path(__file__).resolve().parents[2]


def _config() -> KeelConfig:
    from keel.providers import apply_env_pipeline

    return apply_env_pipeline(load_config(None, os.getenv("KEEL_CONFIG", str(REPO / "config" / "fdb_v3.toml"))))


def check_openai(cfg: KeelConfig) -> str:
    """One chat completion of one token with the pipeline's LLM (also the judge's model family)."""
    import openai

    assert cfg.livekit is not None
    model = cfg.livekit.cascaded.llm_model
    openai.OpenAI().chat.completions.create(
        model=model, messages=[{"role": "user", "content": "Reply with OK."}], max_tokens=1)
    return f"OpenAI accepts the key ({model})"


def check_gemini(cfg: KeelConfig) -> str:
    """Open and close one Live session with the configured Gemini Live model."""
    from google import genai
    from google.genai import types

    assert cfg.livekit is not None and cfg.livekit.gemini is not None
    model = cfg.livekit.gemini.model

    async def connect() -> None:
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        config = types.LiveConnectConfig(response_modalities=[types.Modality.AUDIO])
        async with client.aio.live.connect(model=model, config=config):
            pass

    asyncio.run(asyncio.wait_for(connect(), timeout=30))
    return f"Gemini accepts the key ({model})"


def _client(cfg: KeelConfig, provider: str):  # type: ignore[no-untyped-def]
    import openai

    from keel.providers import endpoint

    base_url, key = endpoint(cfg, provider)
    # Generous timeout: a local server loads its model on the first request.
    return openai.OpenAI(base_url=base_url, api_key=key, timeout=180, max_retries=0)


def check_open(cfg: KeelConfig) -> str:
    """Pipeline "open": one short transcription, one token, one spoken word, from
    whichever providers [livekit.open] names (local servers or hosted ones)."""
    import io
    import wave

    assert cfg.livekit is not None and cfg.livekit.open is not None, "no [livekit.open] section"
    o = cfg.livekit.open
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:                  # half a second of silence
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 8000)
    _client(cfg, o.stt_provider).audio.transcriptions.create(
        model=o.stt_model, file=("check.wav", buf.getvalue(), "audio/wav"), language=o.stt_language)
    _client(cfg, o.llm_provider).chat.completions.create(
        model=o.llm_model, messages=[{"role": "user", "content": "Reply with OK."}], max_tokens=1)
    audio = _client(cfg, o.tts_provider).audio.speech.create(
        model=o.tts_model, voice=o.tts_voice, input="OK.", response_format=o.tts_format)  # type: ignore[arg-type]
    if not audio.content:
        raise RuntimeError(f"{o.tts_provider} returned no audio")
    return (f"open pipeline answers: stt {o.stt_provider}/{o.stt_model}, llm {o.llm_provider}/{o.llm_model}, "
            f"tts {o.tts_provider}/{o.tts_model}")


def check_vision(cfg: KeelConfig) -> str:
    """Show & Fix: read one small generated image with the display reader the
    pipeline uses ([livekit.show_and_fix].vision), and require its JSON answer."""
    import asyncio as aio

    from extension.show_and_fix.backend import reader_for

    assert cfg.livekit is not None and cfg.livekit.show_and_fix is not None, "not a Show & Fix profile"
    provider, model = cfg.livekit.show_and_fix.vision_for(cfg.livekit.pipeline)
    out = aio.run(aio.wait_for(reader_for(cfg)(_blank_png(), "image/png"), timeout=120))
    if "confidence" not in out:
        raise RuntimeError(f"unexpected answer: {out}")
    return f"{provider} reads images ({model}); a blank test image gave code={out['code']!r}"


def _blank_png(size: int = 32) -> bytes:
    """A grey PNG, made without an imaging library."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x80" * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


CHECKS = {"openai": check_openai, "gemini": check_gemini, "open": check_open, "vision": check_vision}


def main(argv: list[str]) -> int:
    unknown = [name for name in argv if name not in CHECKS]
    if not argv or unknown:
        print(f"usage: python -m keel.livekit.preflight {{{'|'.join(CHECKS)}}}...", file=sys.stderr)
        return 2
    cfg = _config()
    failed = False
    for name in argv:
        try:
            print(CHECKS[name](cfg))
        except Exception as e:  # noqa: BLE001 - any refusal: bad key, no credit, no model access, network
            print(f"{name}: the provider refused a minimal request: {type(e).__name__}: {e}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
