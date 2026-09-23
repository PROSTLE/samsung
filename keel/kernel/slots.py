"""Versioned, session-scoped slot store.

Each slot keeps its full version history. A new version is created only when
the value actually changes, so re-hearing the same value never invalidates
work that read it.

Locking: an explicit user correction ("no, Pune") locks the slot. A locked
slot rejects every later proposal that is not itself an explicit correction,
so a lower-confidence perception (OCR, vision, a mis-heard ASR word) cannot
silently overwrite what the user just said. Locks last until the goal they
were made under completes (release_locks()).

Guide §6: session-scoped memory only. A SlotStore belongs to one session and
is never persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Union

from pydantic import JsonValue

from keel.kernel.guard import WriteGuard
from keel.kernel.internal import Source


@dataclass(frozen=True)
class SlotVersion:
    value: JsonValue
    version: int
    source: Source
    confidence: float
    updated_at: int
    correction: bool


@dataclass
class Slot:
    name: str
    history: list[SlotVersion] = field(default_factory=list)
    locked: bool = False

    @property
    def current(self) -> SlotVersion:
        return self.history[-1]


@dataclass(frozen=True)
class SlotChange:
    name: str
    old: Optional[SlotVersion]
    new: SlotVersion


@dataclass(frozen=True)
class Rejection:
    name: str
    value: JsonValue
    source: Source
    reason: Literal["locked"]


ProposalOutcome = Union[SlotChange, Rejection, None]


class SlotStore:
    def __init__(self, guard: WriteGuard) -> None:
        self._guard = guard
        self._slots: dict[str, Slot] = {}

    # ---- reads (safe from anywhere) -------------------------------------

    def get(self, name: str) -> Optional[SlotVersion]:
        slot = self._slots.get(name)
        return slot.current if slot else None

    def version(self, name: str) -> int:
        slot = self._slots.get(name)
        return slot.current.version if slot else 0

    def is_locked(self, name: str) -> bool:
        slot = self._slots.get(name)
        return bool(slot and slot.locked)

    def history(self, name: str) -> tuple[SlotVersion, ...]:
        slot = self._slots.get(name)
        return tuple(slot.history) if slot else ()

    def names(self) -> list[str]:
        return sorted(self._slots)

    def values(self) -> dict[str, JsonValue]:
        return {n: s.current.value for n, s in sorted(self._slots.items())}

    # ---- writes (kernel only) -------------------------------------------

    def propose(
        self,
        name: str,
        value: JsonValue,
        *,
        source: Source,
        confidence: float,
        now: int,
        correction: bool = False,
    ) -> ProposalOutcome:
        self._guard.check()
        slot = self._slots.get(name)
        if slot is not None and slot.locked and not correction:
            return Rejection(name, value, source, "locked")
        if correction:
            if slot is None:
                slot = self._slots[name] = Slot(name)
            slot.locked = True
        if slot is not None and slot.history and slot.current.value == value:
            return None
        if slot is None:
            slot = self._slots[name] = Slot(name)
        old = slot.current if slot.history else None
        new = SlotVersion(
            value=value,
            version=(old.version + 1) if old else 1,
            source=source,
            confidence=confidence,
            updated_at=now,
            correction=correction,
        )
        slot.history.append(new)
        return SlotChange(name, old, new)

    def release_locks(self) -> None:
        self._guard.check()
        for slot in self._slots.values():
            slot.locked = False
