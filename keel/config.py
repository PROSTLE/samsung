"""Typed loader for config/keel.toml.

Unknown keys are rejected so a typo in the config file fails loudly instead of
silently falling back to a default.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

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


class KeelConfig(_Section):
    protocol: ProtocolConfig
    trace: TraceConfig
    # sha256 of the raw file, written into trace headers.
    digest: str = ""


def load_config(path: Path | str | None = None) -> KeelConfig:
    raw_bytes = Path(path or DEFAULT_PATH).read_bytes()
    data = tomllib.loads(raw_bytes.decode("utf-8"))
    data["digest"] = hashlib.sha256(raw_bytes).hexdigest()
    return KeelConfig.model_validate(data)
