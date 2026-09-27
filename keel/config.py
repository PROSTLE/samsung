"""Typed loader for config/keel.toml.

Unknown keys are rejected so a typo in the config file fails loudly instead of
silently falling back to a default.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "keel.toml"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProtocolConfig(_Section):
    schema_version: str


class TraceConfig(_Section):
    record_wall_time: bool


class FenceConfig(_Section):
    quiet_ms: int = Field(ge=0)
    hold_reads: bool
    require_end_of_turn: bool
    stale_turn_ms: int = Field(ge=0)
    # A turn made only of editing terms ("oh, wait") holds calls until the next
    # turn ends, for at most this long (keel/kernel/fence.py rule 4). 0 = off.
    repair_wait_ms: int = Field(default=0, ge=0)
    editing_terms: list[str] = Field(default_factory=list)
    # A stretch of speech that has ended but whose words have not arrived yet
    # holds calls until they do, for at most this long (keel/kernel/fence.py
    # rule 5). 0 = off. Only meaningful where a transcript follows each stretch
    # of speech (a cascade); keel/livekit/gate.py turns it off otherwise.
    transcript_wait_ms: int = Field(default=0, ge=0)


class CallsConfig(_Section):
    timeout_ms: int = Field(gt=0)
    read_retries: int = Field(ge=0)


class ReconcileConfig(_Section):
    retry_if_idempotent: bool


class PerceptionConfig(_Section):
    clarify_below: float = Field(ge=0.0, le=1.0)


class FloorConfig(_Section):
    speak: bool
    ack_after_ms: int = Field(ge=0)
    progress_after_ms: int = Field(gt=0)
    max_progress_per_turn: int = Field(ge=0)


class CompilerConfig(_Section):
    min_confidence: float = Field(ge=0.5, le=1.0)


class Phrases(_Section):
    hold: str
    correction: str
    progress: str
    status_unsure: str
    status_confirmed: str
    status_not_executed: str
    status_cannot_confirm: str
    already_committed: str
    clarify_low_confidence: str
    clarify_missing: str
    final_default: str
    failed: str


class CascadedConfig(_Section):
    vad_min_speech_s: float = Field(ge=0)
    vad_min_silence_s: float = Field(ge=0)
    stt_model: str
    stt_language: str
    llm_model: str
    llm_temperature: float = Field(ge=0, le=2)
    llm_seed: int
    tts_model: str
    tts_voice: str
    turn_detector: str  # "english" | "multilingual" | "vad"
    min_endpointing_s: float = Field(ge=0)
    max_endpointing_s: float = Field(ge=0)
    preemptive_generation: bool
    drop_on_new_speech: bool


class ProviderConfig(_Section):
    """An OpenAI-compatible endpoint (OpenAI, Gemini's compatibility layer, Groq,
    a local Ollama or Speaches server...)."""
    # "" = the OpenAI SDK's default (https://api.openai.com/v1).
    base_url: str = ""
    # Environment variable holding the key. "" = the endpoint needs no key (a
    # local server); the SDK is then given a placeholder.
    key_env: str = ""
    # Whether it accepts OpenAI's `seed` parameter. Gemini's compatibility layer
    # rejects it (HTTP 400 "Unknown name seed"), so the seed is left out there.
    seed: bool = True


class OpenCascadeConfig(_Section):
    """Pipeline "open": the cascaded pipeline (VAD, end of turn and endpointing
    from [livekit.cascaded]) with each of STT, LLM and TTS served by any
    OpenAI-compatible provider from [providers]."""
    stt_provider: str
    stt_model: str
    stt_language: str = "en"
    llm_provider: str
    llm_model: str
    tts_provider: str
    tts_model: str
    tts_voice: str
    # Audio format asked of the TTS endpoint (mp3 | wav | pcm | flac | opus).
    tts_format: str = "mp3"
    drop_on_new_speech: bool = True
    # Per-request deadline for each stage. LiveKit's default is 10 s with three
    # retries; a local model on a small GPU can take longer to start answering,
    # and a retry only starts the same work again (measured: a 10.0 s timeout
    # and retry cost 10 s in our local travel_10 run). One retry is kept for a
    # dropped connection.
    request_timeout_s: float = Field(default=60.0, gt=0)


class RealtimeConfig(_Section):
    model: str
    voice: str
    drop_on_new_speech: bool


class FdbConfig(_Section):
    fdb_dir: str
    template: str
    tool_log: str
    heartbeat_log: str
    latency_profile: str
    seed: int
    extra_instructions: str


class ShowAndFixConfig(_Section):
    manifest: str
    instructions: str
    manual: str
    vision_model: str
    frame_max_age_s: float = Field(gt=0)
    # Pipeline -> "provider:model" for reading the display, when it should not
    # be vision_model on OpenAI (e.g. gemini_realtime reads with a Gemini model,
    # so that pipeline needs no other key). Split at the first colon.
    vision: dict[str, str] = Field(default_factory=dict)

    def vision_for(self, pipeline: str) -> tuple[str, str]:
        """(provider, model) that reads the display under this pipeline."""
        choice = self.vision.get(pipeline)
        if not choice:
            return "openai", self.vision_model
        provider, _, model = choice.partition(":")
        return provider, model


class LivekitConfig(_Section):
    pipeline: str  # "cascaded" | "gpt_realtime" | "gemini_realtime" | "open"
    trace_dir: str
    speak_purposes: list[str]
    cascaded: CascadedConfig
    realtime: RealtimeConfig
    gemini: Optional[RealtimeConfig] = None
    open: Optional[OpenCascadeConfig] = None
    fdb: Optional[FdbConfig] = None
    show_and_fix: Optional[ShowAndFixConfig] = None


class KeelConfig(_Section):
    protocol: ProtocolConfig
    trace: TraceConfig
    fence: FenceConfig
    calls: CallsConfig
    reconcile: ReconcileConfig
    perception: PerceptionConfig
    floor: FloorConfig
    compiler: CompilerConfig
    phrases: Phrases
    # Model endpoints by name, referred to by [livekit.open] and
    # [livekit.show_and_fix].vision. "openai" works without an entry.
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    # Only present when a LiveKit profile (e.g. config/fdb_v3.toml) is layered on.
    livekit: Optional[LivekitConfig] = None
    # sha256 of the raw file, written into trace headers.
    digest: str = ""


def with_overrides(config: KeelConfig, **sections: dict) -> KeelConfig:
    """Copy of config with some fields replaced, e.g. fence={"quiet_ms": 0}."""
    data = config.model_dump()
    for section, values in sections.items():
        data[section].update(values)
    # The trace header's digest must identify the config actually in force,
    # not the file it started from.
    material = config.digest + json.dumps(sections, sort_keys=True, default=str)
    data["digest"] = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return KeelConfig.model_validate(data)


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Path | str | None = None, *overlays: Path | str) -> KeelConfig:
    """Load config/keel.toml (or `path`), then layer each overlay file on top,
    section by section. The digest covers every file, in order."""
    files = [Path(path or DEFAULT_PATH), *map(Path, overlays)]
    data: dict = {}
    h = hashlib.sha256()
    for f in files:
        raw = f.read_bytes()
        h.update(raw)
        data = _merge(data, tomllib.loads(raw.decode("utf-8")))
    data["digest"] = h.hexdigest() if overlays else hashlib.sha256(files[0].read_bytes()).hexdigest()
    return KeelConfig.model_validate(data)
