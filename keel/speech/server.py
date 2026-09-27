"""A local, OpenAI-compatible speech server: faster-whisper STT and Kokoro TTS.

    python -m keel.speech --port 8000 --whisper Systran/faster-whisper-small.en \\
        --kokoro-model third_party/models/kokoro-v1.0.int8.onnx --kokoro-voices third_party/models/voices-v1.0.bin

The "open" pipeline (config/fdb_v3.toml, [livekit.open]) points LiveKit's
OpenAI plugin at it, so speech recognition and synthesis run on open-weight
models on this machine: no key, no account, no per-request limit. It serves the
two endpoints the plugin calls, and refuses a model it has not loaded instead
of silently answering with another:

  POST /v1/audio/transcriptions   multipart: file, model[, language, prompt, response_format]
                                  -> {"text": ...} (response_format json | verbose_json | text)
  POST /v1/audio/speech           JSON: model, input, voice[, response_format wav | pcm, speed]
                                  -> 16-bit mono audio at the model's rate (Kokoro: 24 kHz)
  GET  /v1/models, /health

Binds to 127.0.0.1 only. The engines are injected, so the tests run it without
any model (tests/test_speech.py).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

from aiohttp import web

log = logging.getLogger("keel.speech")


class Transcriber(Protocol):
    name: str

    def transcribe(self, audio: bytes, language: Optional[str], prompt: Optional[str]) -> str: ...


class Synthesizer(Protocol):
    name: str
    voices: list[str]

    def synthesize(self, text: str, voice: str, speed: float) -> tuple[Any, int]:
        """(float samples in [-1, 1], sample rate)"""
        ...


# ------------------------------------------------------------------ engines
class WhisperEngine:
    """faster-whisper (CTranslate2). Greedy decoding and no conditioning on
    earlier text: each request is one utterance, transcribed the same way every time.

    faster-whisper's own voice-activity filter (Silero VAD) runs first: Whisper
    turns non-speech into words (half a second of silence came back as "you",
    measured), and in a live session a phantom transcript is new user speech, which
    makes Keel drop a held call. With the filter, silence transcribes as ""."""

    def __init__(self, model: str, *, device: str = "cpu", compute_type: str = "int8",
                 download_root: Optional[str] = None, revision: Optional[str] = None) -> None:
        from faster_whisper import WhisperModel

        self.name = model
        self._model = WhisperModel(model, device=device, compute_type=compute_type,
                                   download_root=download_root, revision=revision)

    def transcribe(self, audio: bytes, language: Optional[str], prompt: Optional[str]) -> str:
        segments, _info = self._model.transcribe(io.BytesIO(audio), language=language or None, beam_size=1,
                                                 temperature=0.0, condition_on_previous_text=False,
                                                 initial_prompt=prompt or None, vad_filter=True)
        return "".join(s.text for s in segments).strip()


class KokoroEngine:
    """Kokoro-82M through ONNX Runtime (kokoro-onnx)."""

    def __init__(self, model_path: str, voices_path: str, *, lang: str = "en-us") -> None:
        from kokoro_onnx import Kokoro

        self.name = Path(model_path).stem              # e.g. "kokoro-v1.0.int8"
        self.lang = lang
        self._k = Kokoro(model_path, voices_path)
        self.voices = sorted(self._k.get_voices())

    def synthesize(self, text: str, voice: str, speed: float) -> tuple[Any, int]:
        return self._k.create(text, voice=voice, speed=speed, lang=self.lang)


# ------------------------------------------------------------------ audio
def pcm16(samples: Any) -> bytes:
    import numpy as np

    a = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    return (a * 32767.0).astype("<i2").tobytes()


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


# ------------------------------------------------------------------ server
def _error(status: int, message: str, kind: str = "invalid_request_error") -> web.Response:
    # OpenAI's error shape, so the SDK raises a readable APIStatusError.
    return web.json_response({"error": {"message": message, "type": kind}}, status=status)


@dataclass
class Speech:
    stt: Optional[Transcriber]
    tts: Optional[Synthesizer]

    def __post_init__(self) -> None:
        # One request at a time per model: ONNX Runtime and CTranslate2 already
        # use every core for one request, so queueing is faster than interleaving.
        self._stt_lock = asyncio.Lock()
        self._tts_lock = asyncio.Lock()

    async def health(self, _: web.Request) -> web.Response:
        return web.json_response({"ok": True, "stt": self.stt.name if self.stt else None,
                                  "tts": self.tts.name if self.tts else None})

    async def models(self, _: web.Request) -> web.Response:
        now = int(time.time())
        data = [{"id": e.name, "object": "model", "created": now, "owned_by": "local"}
                for e in (self.stt, self.tts) if e is not None]
        return web.json_response({"object": "list", "data": data})

    async def transcriptions(self, request: web.Request) -> web.Response:
        if self.stt is None:
            return _error(404, "this server has no speech-to-text model loaded", "not_found_error")
        if not request.content_type.startswith("multipart/"):
            return _error(400, "send the audio as multipart/form-data (field 'file'), as OpenAI's API expects")
        fields: dict[str, str] = {}
        audio: Optional[bytes] = None
        reader = await request.multipart()
        async for part in reader:
            if part.name == "file":
                audio = await part.read(decode=False)
            elif part.name:
                fields[part.name] = await part.text()
        if not audio:
            return _error(400, "no audio: send it as the multipart field 'file'")
        model = fields.get("model", "")
        if model != self.stt.name:
            return _error(404, f"model {model!r} is not loaded here; this server transcribes with {self.stt.name!r}",
                          "not_found_error")
        t0 = time.perf_counter()
        async with self._stt_lock:
            try:
                text = await asyncio.to_thread(self.stt.transcribe, audio, fields.get("language"), fields.get("prompt"))
            except Exception as e:  # noqa: BLE001 - an undecodable upload, reported to the caller
                return _error(400, f"could not transcribe the audio: {type(e).__name__}: {e}")
        log.info("transcribed %d bytes in %.0f ms: %r", len(audio), (time.perf_counter() - t0) * 1000, text[:80])
        fmt = fields.get("response_format", "json")
        if fmt == "text":
            return web.Response(text=text, content_type="text/plain")
        if fmt not in ("json", "verbose_json"):
            return _error(400, f"response_format {fmt!r} is not supported (json, verbose_json, text)")
        return web.json_response({"text": text} if fmt == "json" else {"text": text, "language": fields.get("language")})

    async def speech(self, request: web.Request) -> web.Response:
        if self.tts is None:
            return _error(404, "this server has no text-to-speech model loaded", "not_found_error")
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return _error(400, "the body must be JSON")
        model, text = body.get("model", ""), str(body.get("input") or "")
        voice, fmt = str(body.get("voice") or ""), body.get("response_format") or "wav"
        if model != self.tts.name:
            return _error(404, f"model {model!r} is not loaded here; this server speaks with {self.tts.name!r}",
                          "not_found_error")
        if voice not in self.tts.voices:
            return _error(400, f"voice {voice!r} is not one of this model's voices: {', '.join(self.tts.voices)}")
        if fmt not in ("wav", "pcm"):
            return _error(400, f"response_format {fmt!r} is not supported here (wav, pcm)")
        if not text.strip():
            return _error(400, "input is empty")
        speed = float(body.get("speed") or 1.0)
        t0 = time.perf_counter()
        async with self._tts_lock:
            samples, rate = await asyncio.to_thread(self.tts.synthesize, text, voice, speed)
        pcm = pcm16(samples)
        log.info("spoke %d chars as %.1f s of audio in %.0f ms", len(text), len(pcm) / 2 / rate,
                 (time.perf_counter() - t0) * 1000)
        if fmt == "pcm":
            return web.Response(body=pcm, content_type="audio/pcm", headers={"X-Sample-Rate": str(rate)})
        return web.Response(body=wav_bytes(pcm, rate), content_type="audio/wav")


def make_app(stt: Optional[Transcriber], tts: Optional[Synthesizer]) -> web.Application:
    s = Speech(stt, tts)
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_get("/health", s.health)
    app.router.add_get("/v1/models", s.models)
    app.router.add_post("/v1/audio/transcriptions", s.transcriptions)
    app.router.add_post("/v1/audio/speech", s.speech)
    return app


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m keel.speech", description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--whisper", help="faster-whisper model (Hugging Face id or local path)")
    ap.add_argument("--whisper-revision", help="pin the Hugging Face revision")
    ap.add_argument("--whisper-device", default="cpu", help="cpu | cuda | auto (default cpu: the GPU is left to the LLM)")
    ap.add_argument("--whisper-compute", default="int8", help="CTranslate2 compute type (default int8)")
    ap.add_argument("--models-dir", default=None, help="where faster-whisper downloads its model")
    ap.add_argument("--kokoro-model", help="kokoro-v1.0*.onnx")
    ap.add_argument("--kokoro-voices", help="voices-v1.0.bin")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if not args.whisper and not args.kokoro_model:
        ap.error("load at least one model: --whisper and/or --kokoro-model with --kokoro-voices")
    stt = (WhisperEngine(args.whisper, device=args.whisper_device, compute_type=args.whisper_compute,
                         download_root=args.models_dir, revision=args.whisper_revision) if args.whisper else None)
    tts = KokoroEngine(args.kokoro_model, args.kokoro_voices) if args.kokoro_model else None
    log.info("speech server on http://127.0.0.1:%d (stt=%s, tts=%s)", args.port,
             stt.name if stt else None, tts.name if tts else None)
    web.run_app(make_app(stt, tts), host="127.0.0.1", port=args.port, print=None)
