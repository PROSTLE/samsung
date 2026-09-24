"""Status probes derived from the manifest, for "did it go through?".

For a write whose outcome is in doubt, look for a read-only tool in the same
manifest that talks about the same thing and can be called with arguments the
write already had:

  * the probe's object words (its name minus the leading verb, stemmed)
    overlap the write's object words, e.g. book_reservation ↔
    get_reservation_details, create_ticket ↔ get_user_tickets;
  * every argument the probe requires is among the write's arguments.

The verdict is read structurally from the probe's result, with no knowledge
of the specific tool:

  * a bare boolean, or an object with exactly one boolean field → that answer;
  * otherwise, look for a record whose fields equal the write's own scalar
    arguments (those not already used to query). Found → executed. The fields
    are visible in some record but none match, or the result is an empty
    collection → not executed. Anything else → unknown (Keel then says it
    cannot confirm).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from pydantic import JsonValue

from keel.compiler.classify import split_identifier, stem
from keel.compiler.manifest import ToolPolicy
from keel.kernel.ledger import CallEntry
from keel.kernel.reconcile import ProbeCall, Verdict


def object_words(name: str) -> set[str]:
    toks = [stem(t) for t in split_identifier(name)]
    return set(toks[1:] if len(toks) > 1 else toks)


class ManifestProbe:
    """Session-scoped: remembers which write arguments each probe used as filters."""

    def __init__(self) -> None:
        self._filters: dict[str, set[str]] = {}

    def plan(self, entry: CallEntry, policies: Mapping[str, ToolPolicy]) -> Optional[ProbeCall]:
        target = object_words(entry.tool)
        best: Optional[tuple[tuple[int, int, str], str]] = None
        for name, pol in policies.items():
            if pol.safety != "read_only" or name == entry.tool:
                continue
            if not set(pol.required) <= set(entry.arguments):
                continue
            overlap = len(target & object_words(name))
            if overlap == 0:
                continue
            # More shared words first, then fewer required args (broader query), then name for determinism.
            rank = (-overlap, len(pol.required), name)
            if best is None or rank < best[0]:
                best = (rank, name)
        if best is None:
            return None
        pol = policies[best[1]]
        props = (pol.parameters or {}).get("properties") or {}
        args = {k: v for k, v in entry.arguments.items() if k in props}
        self._filters[entry.call_id] = set(args)
        return ProbeCall(best[1], args)

    def verdict(self, entry: CallEntry, probe_result: JsonValue) -> Verdict:
        flag = _single_bool(probe_result)
        if flag is not None:
            return "executed" if flag else "not_executed"
        scalars = {k: v for k, v in entry.arguments.items() if isinstance(v, (str, int, float, bool))}
        if not scalars:
            return "unknown"
        # Arguments already used as query filters need not reappear in records.
        must_show = (set(scalars) - self._filters.get(entry.call_id, set())) or set(scalars)
        seen_fields = False
        for rec in _records(probe_result):
            if must_show <= rec.keys():
                seen_fields = True
                if all(rec[k] == v for k, v in scalars.items() if k in rec):
                    return "executed"
        if seen_fields or _is_empty_collection(probe_result):
            return "not_executed"
        return "unknown"


def _single_bool(x: Any) -> Optional[bool]:
    if isinstance(x, bool):
        return x
    if isinstance(x, dict):
        bools = [v for v in x.values() if isinstance(v, bool)]
        if len(bools) == 1 and len(x) == 1:
            return bools[0]
    return None


def _records(x: Any):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from _records(v)
    elif isinstance(x, list):
        for v in x:
            yield from _records(v)


def _is_empty_collection(x: Any) -> bool:
    """An empty list, or an object whose collections are all empty. Scalar
    fields beside them (an envelope's "status": "success") are metadata, so
    {"status": "success", "bookings": []} is an empty result."""
    if isinstance(x, list):
        return len(x) == 0
    if isinstance(x, dict):
        if not x:
            return True
        collections = [v for v in x.values() if isinstance(v, (list, dict))]
        return bool(collections) and all(len(v) == 0 for v in collections)
    return False
