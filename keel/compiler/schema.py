"""Argument validation from a manifest's parameter schema.

Manifests in the wild are not always strict JSON Schema. BFCL, for example,
writes `"type": "dict"` and `"float"`. normalize() maps such Python-style
type names onto JSON Schema types before building a Draft 2020-12 validator,
so arguments are checked before any call leaves Keel.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

# Python / BFCL type names -> JSON Schema types. "any" means no constraint.
_TYPE_MAP = {
    "dict": "object", "object": "object", "float": "number", "number": "number", "int": "integer",
    "integer": "integer", "str": "string", "string": "string", "bool": "boolean", "boolean": "boolean",
    "list": "array", "array": "array", "tuple": "array", "null": "null", "none": "null", "any": None,
}
_SCHEMA_KEYS = ("properties", "items", "additionalProperties")


def normalize(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k == "type":
            types = v if isinstance(v, list) else [v]
            mapped = [_TYPE_MAP.get(str(t).lower(), t) for t in types]
            if None in mapped:
                continue  # "any": leave unconstrained
            out["type"] = mapped[0] if len(mapped) == 1 else mapped
        elif k == "properties" and isinstance(v, dict):
            out[k] = {name: normalize(sub) for name, sub in v.items()}
        elif k in _SCHEMA_KEYS:
            out[k] = normalize(v)
        elif k in ("anyOf", "oneOf", "allOf") and isinstance(v, list):
            out[k] = [normalize(s) for s in v]
        else:
            out[k] = v
    if "properties" in out and "type" not in out:
        out["type"] = "object"
    return out


@lru_cache(maxsize=512)
def _validator(schema_json: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads(schema_json))


def validate_arguments(schema: dict, arguments: dict) -> list[str]:
    """Human-readable violations; empty when the arguments are valid."""
    v = _validator(json.dumps(normalize(schema or {"type": "object"}), sort_keys=True))
    return sorted(
        f"{'/'.join(map(str, e.absolute_path)) or '(arguments)'}: {e.message}"
        for e in v.iter_errors(arguments)
    )
