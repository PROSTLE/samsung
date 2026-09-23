"""Call ledger: one entry per tool call, with a checked status machine.

Status machine (anything else raises IllegalTransition):

    pending ──► in_flight ──► succeeded | failed | unknown | cancelled
       │                        unknown   ──► succeeded | failed
       └──► cancelled           cancelled ──► succeeded   (dispatched state-changing
                                             calls only: the world executed it
                                             despite the cancel request)

The idempotency key is sha256(session_id, tool, canonical JSON arguments).
It is how Keel guarantees exactly-once writes: while any entry with the same
key is pending, in flight, succeeded, unknown, or a dispatched write whose
cancellation is unconfirmed, no new entry with that key may be dispatched.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Optional

from pydantic import JsonValue

from keel.kernel.guard import WriteGuard

SafetyClass = Literal["read_only", "state_changing", "unknown"]
Status = Literal["pending", "in_flight", "succeeded", "failed", "cancelled", "unknown"]

_ALLOWED: dict[str, frozenset[str]] = {
    "pending": frozenset({"in_flight", "cancelled"}),
    "in_flight": frozenset({"succeeded", "failed", "unknown", "cancelled"}),
    "unknown": frozenset({"succeeded", "failed"}),
    "cancelled": frozenset({"succeeded"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
}
ACTIVE: frozenset[str] = frozenset({"pending", "in_flight"})


class IllegalTransition(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def idempotency_key(session_id: str, tool: str, arguments: dict[str, Any]) -> str:
    material = "\x00".join((session_id, tool, canonical_json(arguments)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class CallEntry:
    call_id: str
    seq: int
    step_id: str
    goal_gen: int
    tool: str
    arguments: dict[str, JsonValue]
    reads: dict[str, int]
    safety: SafetyClass
    idem_key: str
    created_at: int
    status: Status = "pending"
    dispatched_at: Optional[int] = None
    finished_at: Optional[int] = None
    result: JsonValue = None
    error: Optional[str] = None
    cancel_reason: Optional[str] = None
    # Set when a *dispatched* write is cancelled: we asked the world to stop,
    # but cannot know it did until a result, a probe, or silence says so.
    outcome_in_doubt: bool = False
    # This entry exists to check the outcome of another call.
    probe_for: Optional[str] = None
    history: list[tuple[int, str]] = field(default_factory=list)

    @property
    def writes(self) -> bool:
        # guide/build rule: `unknown` safety is always treated as state-changing.
        return self.safety != "read_only"

    @property
    def dispatched(self) -> bool:
        return self.dispatched_at is not None

    @property
    def in_doubt(self) -> bool:
        """A write whose effect on the world is not known."""
        return self.writes and (self.status == "unknown" or (self.status == "cancelled" and self.outcome_in_doubt))


class Ledger:
    def __init__(self, guard: WriteGuard, session_id: str) -> None:
        self._guard = guard
        self.session_id = session_id
        self._entries: dict[str, CallEntry] = {}
        self._order: list[str] = []

    # ---- reads -----------------------------------------------------------

    def get(self, call_id: str) -> Optional[CallEntry]:
        return self._entries.get(call_id)

    def __iter__(self) -> Iterator[CallEntry]:
        return (self._entries[c] for c in self._order)

    def __len__(self) -> int:
        return len(self._order)

    def active(self) -> list[CallEntry]:
        return [e for e in self if e.status in ACTIVE]

    def with_key(self, key: str) -> list[CallEntry]:
        return [e for e in self if e.idem_key == key]

    def blocking(self, key: str) -> Optional[CallEntry]:
        """The entry that forbids dispatching another call with this key."""
        for e in reversed([e for e in self if e.idem_key == key]):
            if e.status in ("pending", "in_flight", "succeeded", "unknown"):
                return e
            if e.in_doubt:
                return e
        return None

    def key_for(self, tool: str, arguments: dict[str, Any]) -> str:
        return idempotency_key(self.session_id, tool, arguments)

    # ---- writes ------------------------------------------------------------

    def add(
        self,
        *,
        call_id: str,
        step_id: str,
        goal_gen: int,
        tool: str,
        arguments: dict[str, JsonValue],
        reads: dict[str, int],
        safety: SafetyClass,
        now: int,
        probe_for: Optional[str] = None,
        retry_of: Optional[str] = None,
    ) -> CallEntry:
        """Create a pending entry.

        retry_of: re-send of an `unknown` call to an idempotent tool. It is the
        only way to add an entry whose key is held by another entry.
        """
        self._guard.check()
        if call_id in self._entries:
            raise IllegalTransition(f"duplicate call_id {call_id}")
        key = self.key_for(tool, arguments)
        blocker = self.blocking(key)
        if retry_of is not None:
            if blocker is None or blocker.call_id != retry_of or blocker.status != "unknown":
                raise IllegalTransition(f"retry_of={retry_of} does not name the unknown call holding this key")
        elif blocker is not None and (safety != "read_only" or blocker.status in ACTIVE):
            raise IllegalTransition(f"key already held by {blocker.call_id} ({blocker.status})")
        entry = CallEntry(
            call_id=call_id,
            seq=len(self._order) + 1,
            step_id=step_id,
            goal_gen=goal_gen,
            tool=tool,
            arguments=arguments,
            reads=dict(reads),
            safety=safety,
            idem_key=key,
            created_at=now,
            probe_for=probe_for,
            history=[(now, "pending")],
        )
        self._entries[call_id] = entry
        self._order.append(call_id)
        return entry

    def transition(self, call_id: str, status: Status, now: int, **fields: Any) -> CallEntry:
        self._guard.check()
        e = self._entries[call_id]
        if status not in _ALLOWED[e.status]:
            raise IllegalTransition(f"{call_id}: {e.status} -> {status}")
        if e.status == "cancelled" and not e.in_doubt:
            raise IllegalTransition(f"{call_id}: cancelled call has a known outcome")
        e.status = status
        e.history.append((now, status))
        if status == "in_flight":
            e.dispatched_at = now
        if status in ("succeeded", "failed", "unknown", "cancelled"):
            e.finished_at = now
        if status == "cancelled":
            e.outcome_in_doubt = e.writes and e.dispatched
        if status in ("succeeded", "failed"):
            e.outcome_in_doubt = False
        for k, v in fields.items():
            if not hasattr(e, k):
                raise AttributeError(k)
            setattr(e, k, v)
        return e

    def resolve_doubt(self, call_id: str, executed: bool, now: int) -> CallEntry:
        """Record a confirmed outcome for a write whose outcome was in doubt."""
        e = self._entries[call_id]
        if not e.in_doubt:
            raise IllegalTransition(f"{call_id} is not in doubt")
        if executed:
            return self.transition(call_id, "succeeded", now)
        if e.status == "cancelled":
            return self.settle_cancelled(call_id, now)
        return self.transition(call_id, "failed", now)

    def settle_cancelled(self, call_id: str, now: int) -> CallEntry:
        """A cancelled write is now known not to have run (e.g. probe said so)."""
        self._guard.check()
        e = self._entries[call_id]
        if not (e.status == "cancelled" and e.outcome_in_doubt):
            raise IllegalTransition(f"{call_id} is not a cancelled write in doubt")
        e.outcome_in_doubt = False
        e.history.append((now, "cancelled(confirmed)"))
        return e
