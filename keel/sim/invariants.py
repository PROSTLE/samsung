"""Invariant checks computed from a trace (plus the mock World's ground truth).

These are the properties the guide scores under Interruption Recovery and
Safety & Protocol (§5), stated so a machine can check them on every run:

  I1 no stale dispatch: every tool_call's reads equal the slot versions
     current at the moment it was sent;
  I2 prompt cancellation: once a slot changes, every active call that read
     an older version of it is cancelled (or finishes) in the same virtual
     millisecond;
  I3 no duplicate state-changing calls: no two tool_call actions for a
     non-read-only tool carry the same arguments (explicit idempotent retries
     excepted);
  I4 exactly-once in the world: no state-changing effect happened twice;
  I5 snapshot accuracy: every final response's slots equal the slot store at
     that moment.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Optional

from keel.kernel.ledger import canonical_json
from keel.sim.mock_env import World
from keel.trace import TraceRecord


def check_trace(records: Iterable[TraceRecord], world: Optional[World] = None) -> list[str]:
    violations: list[str] = []
    versions: dict[str, int] = {}
    values: dict[str, Any] = {}
    created: dict[str, dict] = {}
    safety: dict[str, str] = {}
    active: dict[str, dict[str, int]] = {}
    invalid_since: dict[str, int] = {}
    write_calls: Counter[tuple[str, str]] = Counter()

    def closed(cid: str, ts: int, how: str) -> None:
        active.pop(cid, None)
        since = invalid_since.pop(cid, None)
        if since is not None and how == "cancelled" and ts != since:
            violations.append(f"I2 {cid} cancelled at {ts}, invalidated at {since}")

    for r in records:
        d = r.data
        if r.dir == "note" and r.kind == "tool_policy":
            safety[d["tool"]] = d["safety"]
        elif r.dir == "note" and r.kind == "slot_changed":
            slot, ver = d["slot"], d["version"]
            versions[slot], values[slot] = ver, d["value"]
            for cid, reads in active.items():
                if slot in reads and reads[slot] != ver and cid not in invalid_since:
                    invalid_since[cid] = r.ts_ms
        elif r.dir == "note" and r.kind == "call_created":
            created[d["call_id"]] = d
        elif r.dir == "out" and r.kind == "tool_call":
            cid = d["call_id"]
            meta = created.get(cid, {})
            reads = {k: v for k, v in (meta.get("reads") or {}).items() if not k.startswith("@")}
            for s, v in reads.items():
                if versions.get(s, 0) != v:
                    violations.append(f"I1 {cid} sent with {s} v{v} but current is v{versions.get(s, 0)}")
            active[cid] = reads
            if safety.get(d["tool"], "unknown") != "read_only" and not meta.get("retry_of"):
                write_calls[(d["tool"], canonical_json(d["arguments"]))] += 1
        elif r.dir == "note" and r.kind == "call_cancelled":
            closed(d["call_id"], r.ts_ms, "cancelled")
        elif r.dir == "note" and r.kind == "call_status":
            closed(d["call_id"], r.ts_ms, "finished")
        elif r.dir == "out" and r.kind == "final":
            if d["snapshot"]["slots"] != values:
                violations.append(f"I5 final at {r.ts_ms} snapshot {d['snapshot']['slots']} != store {values}")

    # A call still marked invalid at the end was never cancelled at all.
    for cid, since in invalid_since.items():
        if cid in active:
            violations.append(f"I2 {cid} invalidated at {since} and never cancelled")
    for (tool, args), n in write_calls.items():
        if n > 1:
            violations.append(f"I3 {tool}{args} dispatched {n} times")
    if world is not None:
        for (tool, args), n in Counter((t, canonical_json(a)) for _, t, a in world.effects).items():
            if n > 1:
                violations.append(f"I4 {tool}{args} took effect {n} times")
    return violations
