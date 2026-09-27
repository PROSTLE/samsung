"""keel.speech: the local OpenAI-compatible speech server, with fake engines (no model, no network)."""

import asyncio
import io
import wave

import pytest

pytest.importorskip("aiohttp")
np = pytest.importorskip("numpy")

from aiohttp import FormData  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from keel.speech.server import make_app, pcm16, wav_bytes  # noqa: E402


class FakeWhisper:
    name = "Systran/faster-whisper-small.en"

    def __init__(self):
        self.calls = []

    def transcribe(self, audio, language, prompt):
        self.calls.append((audio, language, prompt))
        return "make it the seventh"


class FakeKokoro:
    name = "kokoro-v1.0"
    voices = ["af_heart", "am_adam"]

    def synthesize(self, text, voice, speed):
        n = int(24000 * 0.1 * len(text.split()))            # 0.1 s per word
        return np.full(n, 0.5, dtype=np.float32), 24000


def run(coro):
    return asyncio.run(coro)


def form(model, audio=b"RIFF....WAVE", **fields):
    f = FormData()
    f.add_field("file", audio, filename="a.wav", content_type="audio/wav")
    f.add_field("model", model)
    for k, v in fields.items():
        f.add_field(k, v)
    return f


def test_transcription_answers_in_openais_shape():
    whisper = FakeWhisper()

    async def main():
        async with TestClient(TestServer(make_app(whisper, FakeKokoro()))) as c:
            ok = await c.post("/v1/audio/transcriptions", data=form(whisper.name, language="en"))
            text = await c.post("/v1/audio/transcriptions", data=form(whisper.name, response_format="text"))
            return ok.status, await ok.json(), await text.text()

    status, body, text = run(main())
    assert status == 200 and body == {"text": "make it the seventh"} and text == "make it the seventh"
    assert whisper.calls[0][1] == "en" and whisper.calls[0][0] == b"RIFF....WAVE"


def test_a_model_it_has_not_loaded_is_refused_not_substituted():
    async def main():
        async with TestClient(TestServer(make_app(FakeWhisper(), FakeKokoro()))) as c:
            stt = await c.post("/v1/audio/transcriptions", data=form("whisper-1"))
            tts = await c.post("/v1/audio/speech", json={"model": "tts-1", "input": "hi", "voice": "af_heart"})
            voice = await c.post("/v1/audio/speech", json={"model": "kokoro-v1.0", "input": "hi", "voice": "nova"})
            fmt = await c.post("/v1/audio/speech", json={"model": "kokoro-v1.0", "input": "hi", "voice": "af_heart",
                                                         "response_format": "mp3"})
            empty = await c.post("/v1/audio/speech", json={"model": "kokoro-v1.0", "input": " ", "voice": "af_heart"})
            nofile = await c.post("/v1/audio/transcriptions", data={"model": "Systran/faster-whisper-small.en"})
            return [(r.status, (await r.json())["error"]["message"]) for r in (stt, tts, voice, fmt, empty, nofile)]

    out = run(main())
    assert [s for s, _ in out] == [404, 404, 400, 400, 400, 400]
    assert "faster-whisper-small.en" in out[0][1] and "kokoro-v1.0" in out[1][1] and "af_heart" in out[2][1]


def test_speech_is_16_bit_mono_wav_or_raw_pcm_at_the_models_rate():
    async def main():
        async with TestClient(TestServer(make_app(FakeWhisper(), FakeKokoro()))) as c:
            w = await c.post("/v1/audio/speech", json={"model": "kokoro-v1.0", "input": "one two", "voice": "af_heart",
                                                       "response_format": "wav"})
            p = await c.post("/v1/audio/speech", json={"model": "kokoro-v1.0", "input": "one two", "voice": "am_adam",
                                                       "response_format": "pcm"})
            return w.headers["Content-Type"], await w.read(), p.headers["Content-Type"], p.headers["X-Sample-Rate"], await p.read()

    wtype, wav, ptype, rate, pcm = run(main())
    with wave.open(io.BytesIO(wav)) as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate(), f.getnframes()) == (1, 2, 24000, 4800)
    assert wtype == "audio/wav" and ptype == "audio/pcm" and rate == "24000" and len(pcm) == 4800 * 2


def test_models_and_health_list_only_what_is_loaded():
    async def main():
        async with TestClient(TestServer(make_app(None, FakeKokoro()))) as c:
            models = await (await c.get("/v1/models")).json()
            health = await (await c.get("/health")).json()
            stt = await c.post("/v1/audio/transcriptions", data=form("x"))
            return models, health, stt.status

    models, health, stt = run(main())
    assert [m["id"] for m in models["data"]] == ["kokoro-v1.0"]
    assert health == {"ok": True, "stt": None, "tts": "kokoro-v1.0"} and stt == 404


def test_audio_helpers_clip_and_frame():
    pcm = pcm16(np.array([2.0, -2.0, 0.0], dtype=np.float32))
    assert np.frombuffer(pcm, "<i2").tolist() == [32767, -32767, 0]
    with wave.open(io.BytesIO(wav_bytes(pcm, 16000))) as f:
        assert f.getframerate() == 16000 and f.getnframes() == 3


def test_the_openai_sdk_talks_to_it():
    """The client LiveKit's OpenAI plugin uses, against the real server on a local port."""
    openai = pytest.importorskip("openai")
    from aiohttp import web

    async def main():
        runner = web.AppRunner(make_app(FakeWhisper(), FakeKokoro()))
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        c = openai.AsyncOpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="not-needed", max_retries=0)
        try:
            speech = await c.audio.speech.create(model="kokoro-v1.0", voice="af_heart", input="hello there",
                                                 response_format="wav")
            text = await c.audio.transcriptions.create(model="Systran/faster-whisper-small.en",
                                                       file=("a.wav", speech.content, "audio/wav"), language="en")
            try:
                await c.audio.speech.create(model="kokoro-v1.0", voice="nope", input="x")
                refused = None
            except openai.BadRequestError as e:
                refused = e.status_code
        finally:
            await c.close()
            await runner.cleanup()
        return speech.content, text.text, refused

    wav, text, refused = run(main())
    assert wav[:4] == b"RIFF" and text == "make it the seventh" and refused == 400
