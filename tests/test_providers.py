"""Model providers ([providers] in config/keel.toml) and the pipeline switch, without any network."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from keel.config import load_config, with_overrides
from keel.providers import (NO_KEY, ProviderError, accepts_seed, apply_env_pipeline, describe_open, endpoint,
                            key_envs, pipeline_providers)

ROOT = Path(__file__).resolve().parents[1]


def profile(name="fdb_v3", pipeline=None):
    cfg = load_config(None, ROOT / "config" / f"{name}.toml")
    return with_overrides(cfg, livekit={"pipeline": pipeline}) if pipeline else cfg


def test_hosted_providers_need_their_key_and_local_ones_none(monkeypatch):
    cfg = profile()
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    assert endpoint(cfg, "groq") == ("https://api.groq.com/openai/v1", "gsk-test")
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(ProviderError, match="GROQ_API_KEY is not set"):
        endpoint(cfg, "groq")
    assert endpoint(cfg, "ollama") == ("http://127.0.0.1:11434/v1", NO_KEY)
    assert endpoint(cfg, "speaches") == ("http://127.0.0.1:8000/v1", NO_KEY)
    with pytest.raises(ProviderError, match="unknown model provider"):
        endpoint(cfg, "nope")


def test_openai_works_without_a_providers_entry(monkeypatch):
    cfg = profile().model_copy(update={"providers": {}})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert endpoint(cfg, "openai") == (None, "sk-test")
    assert key_envs(cfg, ["openai"]) == ["OPENAI_API_KEY"]


def test_gemini_is_sent_no_seed():
    # Its compatibility layer answers HTTP 400 "Unknown name seed" (measured).
    cfg = profile()
    assert accepts_seed(cfg, "gemini") is False
    assert accepts_seed(cfg, "openai") and accepts_seed(cfg, "ollama") and accepts_seed(cfg, "groq")


def test_each_pipeline_names_only_the_providers_it_calls():
    assert pipeline_providers(profile(pipeline="cascaded")) == ["openai"]
    assert pipeline_providers(profile(pipeline="gemini_realtime")) == ["gemini"]
    assert set(pipeline_providers(profile(pipeline="open"))) == {"speaches", "ollama"}
    # Show & Fix adds its display reader, which follows the pipeline's provider.
    assert pipeline_providers(profile("show_and_fix", "gemini_realtime")) == ["gemini"]
    assert pipeline_providers(profile("show_and_fix", "cascaded")) == ["openai"]
    assert key_envs(profile(), pipeline_providers(profile(pipeline="open"))) == []
    assert key_envs(profile(), ["gemini", "groq"]) == ["GOOGLE_API_KEY", "GROQ_API_KEY"]


def test_the_display_reader_follows_the_pipeline():
    sf = profile("show_and_fix").livekit.show_and_fix
    assert sf.vision_for("cascaded") == ("openai", sf.vision_model)
    assert sf.vision_for("gemini_realtime") == ("gemini", "gemini-2.5-flash")
    assert sf.vision_for("open") == ("ollama", "qwen3-vl:2b-instruct-q4_K_M")   # model names may contain colons


def test_keel_pipeline_overrides_the_profile(monkeypatch):
    monkeypatch.setenv("KEEL_PIPELINE", "open")
    assert apply_env_pipeline(profile()).livekit.pipeline == "open"
    monkeypatch.setenv("KEEL_PIPELINE", "nope")
    with pytest.raises(SystemExit, match="must be one of"):
        apply_env_pipeline(profile())
    monkeypatch.delenv("KEEL_PIPELINE")
    assert apply_env_pipeline(profile()).livekit.pipeline == "cascaded"


def test_a_run_records_the_open_models_and_the_local_llms_digest(tmp_path):
    cfg = profile(pipeline="open")
    manifest = tmp_path / "third_party/ollama/models/manifests/registry.ollama.ai/library/qwen3/4b-instruct-2507-q4_K_M"
    assert "ollama digest" not in describe_open(cfg, tmp_path)
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b'{"layers": []}')
    text = describe_open(cfg, tmp_path)
    assert "llm ollama/qwen3:4b-instruct-2507-q4_K_M" in text and "tts speaches/kokoro-v1.0 voice af_heart" in text
    assert f"ollama digest {hashlib.sha256(manifest.read_bytes()).hexdigest()[:12]}" in text


def test_the_display_reader_sends_a_seed_only_where_it_is_accepted(monkeypatch):
    """reader_for builds an OpenAI-compatible client for the pipeline's provider;
    the request carries `seed` only if that provider takes it."""
    pytest.importorskip("openai")
    from extension.show_and_fix import backend

    sent = []

    class FakeCompletions:
        async def create(self, **kw):
            sent.append(kw)

            class R:
                choices = [type("C", (), {"message": type("M", (), {"content": json.dumps(
                    {"code": "4C", "confidence": 0.9, "what_i_see": "4C on the panel"})})()})()]
            return R()

    class FakeClient:
        def __init__(self, api_key=None, base_url=None):
            self.base_url, self.api_key = base_url, api_key
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for pipeline in ("gemini_realtime", "cascaded"):
        out = asyncio.run(backend.reader_for(profile("show_and_fix", pipeline))(b"\x89PNG", "image/png"))
        assert out == {"code": "4C", "confidence": 0.9, "what_i_see": "4C on the panel"}
    gemini, openai_call = sent
    assert gemini["model"] == "gemini-2.5-flash" and "seed" not in gemini
    assert openai_call["model"] == "gpt-4o" and openai_call["seed"] == 5
