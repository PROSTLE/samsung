"""Deterministic, session-scoped identifier generation.

IDs are counters, not UUIDs, so replaying the same scenario produces a
byte-identical trace. They are unique within a session; uniqueness across
sessions comes from the session_id prefix.
"""

from __future__ import annotations

import itertools
from collections import defaultdict


class IdFactory:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._counters: dict[str, itertools.count] = defaultdict(lambda: itertools.count(1))

    def new(self, prefix: str) -> str:
        return f"{self.session_id}:{prefix}-{next(self._counters[prefix]):05d}"
