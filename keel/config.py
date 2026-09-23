"""Typed loader for config/keel.toml.

Unknown keys are rejected so a typo in the config file fails loudly instead of
silently falling back to a default.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

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
    require_end_of_turn: bool
    stale_turn_ms: int = Field(ge=0)


class CallsConfig(_Section):
    timeout_ms: int = Field(gt=0)
    read_retries: int = Field(ge=0)


class ReconcileConfig(_Section):
    retry_if_idempotent: bool


class PerceptionConfig(_Section):
    clarify_below: float = Field(ge=0.0, le=1.0)


class FloorConfig(_Section):
    ack_after_ms: int = Field(ge=0)
    progress_after_ms: int = Field(gt=0)
    max_progress_per_turn: int = Field(ge=0)


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


class KeelConfig(_Section):
    protocol: ProtocolConfig
    trace: TraceConfig
    fence: FenceConfig
    calls: CallsConfig
    reconcile: ReconcileConfig
    perception: PerceptionConfig
    floor: FloorConfig
    phrases: Phrases
    # sha256 of the raw file, written into trace headers.
    digest: str = ""


def with_overrides(config: KeelConfig, **sections: dict) -> KeelConfig:
    """Copy of config with some fields replaced, e.g. fence={"quiet_ms": 0}."""
    data = config.model_dump()
    for section, values in sections.items():
        data[section].update(values)
    return KeelConfig.model_validate(data)


def load_config(path: Path | str | None = None) -> KeelConfig:
    raw_bytes = Path(path or DEFAULT_PATH).read_bytes()
    data = tomllib.loads(raw_bytes.decode("utf-8"))
    data["digest"] = hashlib.sha256(raw_bytes).hexdigest()
    return KeelConfig.model_validate(data)
