"""Fast path: spoken templates rendered from confirmed state only.

Nothing here awaits a model. A template references `{slot}` or
`{step_id.path.to.value}`; if any placeholder cannot be filled from confirmed
state, render() returns None and the caller falls back to a safer phrase, so
Keel never speaks a sentence with a hole or a guess in it.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from keel.kernel.plan import dig

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_@.\-]+)\}")


def humanize(name: str) -> str:
    return name.replace("_", " ").strip()


def spoken(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(spoken(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{humanize(str(k))} {spoken(v)}" for k, v in value.items())
    return str(value)


def render(template: str, values: Mapping[str, Any]) -> Optional[str]:
    missing = False

    def sub(m: re.Match[str]) -> str:
        nonlocal missing
        head, *rest = m.group(1).split(".")
        if head not in values:
            missing = True
            return ""
        path = tuple(int(p) if p.lstrip("-").isdigit() else p for p in rest)
        ok, v = dig(values[head], path)
        if not ok or v is None:
            missing = True
            return ""
        return spoken(v)

    out = _PLACEHOLDER.sub(sub, template)
    return None if missing else out
