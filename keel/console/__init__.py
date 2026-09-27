"""Trace console: renders Keel traces as one self-contained HTML page.

    python -m keel.console traces/*.jsonl -o traces/console.html

The page is built only from what the trace records (keel/trace.py: "Nothing
downstream is allowed to invent data the trace does not contain"). The view
model below is plain data, computed here in Python so it is testable; the
template only draws it. A trace marked synthetic gets a visible label.
"""

from __future__ import annotations

import argparse
import html
import json
import statistics
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from keel.sim.invariants import check_trace
from keel.sim.mock_env import World
from keel.trace import TraceRecord, read_trace

# Output actions that answer the user. The guide scores "time to first
# substantive spoken action following user input or interruption" (§5); which
# kinds the kit counts as substantive is unknown (ASSUMPTION [K12]), so every
# spoken kind is measured and the kind is shown.
SPOKEN = ("speak", "clarify", "final")

INVARIANTS = (
    ("I1", "No stale dispatch", "Every call went out with the slot versions current at that moment."),
    ("I2", "Same-ms cancellation", "Calls that read a changed slot were cancelled in the same virtual ms."),
    ("I3", "No duplicate writes", "No state-changing call was sent twice with the same arguments."),
    ("I4", "Exactly once in the world", "No state-changing effect happened twice (needs the mock world)."),
    ("I5", "Snapshot matches state", "Every final response's slots equal the slot store at that moment."),
)


def _user_inputs(records: Sequence[TraceRecord], live: bool) -> list[dict[str, Any]]:
    """User-side inputs. In a LiveKit session (`live`) an interrupt only marks
    the user starting to speak, an empty non-final text chunk only marks them
    stopping, and a non-empty one only brings a stretch's words (fence rule 5),
    which the committed turn repeats: none is a turn, so none is shown as one."""
    out = []
    for r in records:
        if r.dir != "in" or r.kind in ("tool_result", "manifest"):
            continue
        d = r.data
        if live and r.kind == "text" and not d.get("end_of_turn"):
            continue
        if live and r.kind == "interrupt":
            continue
        label = {"text": d.get("text") or "", "audio": "audio clip", "frame": "camera frame",
                 "interrupt": "interruption"}.get(r.kind, r.kind)
        out.append({"ts": r.ts_ms, "kind": r.kind, "event_id": d.get("event_id"), "label": label,
                    "end_of_turn": bool(d.get("end_of_turn")) or r.kind == "interrupt"})
    return out


def _live_speech(records: Sequence[TraceRecord]) -> list[dict[str, Any]]:
    """What was actually said in a LiveKit session: the LLM's replies and
    Keel's fillers (the kernel's own lines are only notes there)."""
    out = []
    for r in records:
        if r.dir == "note" and r.kind == "agent_said":
            out.append({"ts": r.ts_ms, "type": "agent", "text": r.data.get("text", ""), "purpose": "reply",
                        "caused_by": None, "slot": None})
        elif r.dir == "note" and r.kind == "filler_said":
            out.append({"ts": r.ts_ms, "type": "speak", "text": r.data.get("text", ""),
                        "purpose": r.data.get("purpose"), "caused_by": None, "slot": None})
    return out


def _calls(records: Sequence[TraceRecord]) -> list[dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    for r in records:
        d = r.data
        cid = d.get("call_id")
        if r.dir == "note" and r.kind == "call_created":
            calls[cid] = {"call_id": cid, "tool": d["tool"], "step_id": d.get("step_id"), "safety": d["safety"],
                          "arguments": d.get("arguments") or {}, "reads": d.get("reads") or {},
                          "probe_for": d.get("probe_for"), "retry_of": d.get("retry_of"),
                          "created": r.ts_ms, "held_until": None, "dispatched": None, "ended": None,
                          "status": "pending", "reason": None, "in_doubt": False, "events": [(r.ts_ms, "created")]}
            continue
        c = calls.get(cid) if cid else None
        if c is None:
            continue
        if r.dir == "note" and r.kind == "fence_hold":
            c["held_until"] = d.get("until")
            c["events"].append((r.ts_ms, "held by the commit fence while the user is speaking"
                                if d.get("until") is None else f"held by the commit fence until {d.get('until')} ms"))
        elif r.dir == "out" and r.kind == "tool_call":
            c["dispatched"] = r.ts_ms
            c["status"] = "in_flight"
            c["events"].append((r.ts_ms, "sent"))
        elif r.dir == "note" and r.kind == "call_cancelled":
            c["status"] = "cancelled"
            c["reason"] = d.get("reason")
            c["in_doubt"] = bool(d.get("outcome_in_doubt"))
            c["cancelled_at"] = r.ts_ms
            c["ended"] = r.ts_ms
            c["events"].append((r.ts_ms, f"cancelled: {d.get('reason')}"
                                + (" (it had already been sent, so its outcome is in doubt)" if c["in_doubt"] else "")))
        elif r.dir == "note" and r.kind == "call_status":
            st = d.get("status")
            c["status"] = "cancelled" if st == "cancelled(confirmed)" else st
            if st in ("succeeded", "failed", "cancelled(confirmed)"):
                c["in_doubt"] = False
            if st == "unknown":
                c["in_doubt"] = True
            c["ended"] = r.ts_ms
            c["events"].append((r.ts_ms, st + (f": {d['error']}" if d.get("error") else "")
                                + (f" (arrived after it was {d['late_after']})" if d.get("late_after") else "")))
        elif r.dir == "note" and r.kind in ("probe_verdict", "doubt_resolved", "doubt_unresolvable", "reconcile"):
            label = {"probe_verdict": f"probe verdict: {str(d.get('verdict', '')).replace('_', ' ')}",
                     "doubt_resolved": f"doubt resolved by {d.get('by')}",
                     "doubt_unresolvable": "nothing can confirm the outcome",
                     "reconcile": f"reconcile: {d.get('plan')}" + (f" via {d['detail']}" if d.get("detail") else "")}
            c["events"].append((r.ts_ms, label[r.kind]))
            if r.kind == "probe_verdict":
                c["in_doubt"] = d.get("verdict") == "unknown"
                c["verdict"] = d.get("verdict")
                c["ended"] = r.ts_ms
    for c in calls.values():
        c["writes"] = c["safety"] != "read_only"
        c["events"] = [{"ts": t, "text": s} for t, s in c["events"]]
    return list(calls.values())


def _slots(records: Sequence[TraceRecord]) -> list[dict[str, Any]]:
    slots: dict[str, dict[str, Any]] = {}
    for r in records:
        if r.dir == "note" and r.kind == "slot_changed":
            d = r.data
            s = slots.setdefault(d["slot"], {"name": d["slot"], "versions": [], "rejected": [], "held": []})
            s["versions"].append({"ts": r.ts_ms, "version": d["version"], "value": d["value"], "source": d["source"],
                                  "confidence": d["confidence"], "correction": d["correction"], "locked": d["locked"]})
        elif r.dir == "note" and r.kind in ("proposal_rejected", "proposal_held"):
            d = r.data
            s = slots.setdefault(d["slot"], {"name": d["slot"], "versions": [], "rejected": [], "held": []})
            key = "rejected" if r.kind == "proposal_rejected" else "held"
            s[key].append({"ts": r.ts_ms, "value": d["value"], "source": d["source"], "confidence": d["confidence"],
                           "why": d.get("reason") or f"below {d.get('threshold')} confidence"})
    return list(slots.values())


def _latency(inputs: list[dict[str, Any]], spoken: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per user turn end (or interruption): ms until Keel next spoke."""
    out = []
    ends = [i for i in inputs if i["end_of_turn"]]
    for n, i in enumerate(ends):
        nxt = ends[n + 1]["ts"] if n + 1 < len(ends) else None
        first = next((s for s in spoken if s["ts"] >= i["ts"] and (nxt is None or s["ts"] < nxt)), None)
        out.append({"ts": i["ts"], "event_id": i["event_id"], "label": i["label"],
                    "ms": first["ts"] - i["ts"] if first else None,
                    "by": f"{first['type']}: {first['text']}" if first else None})
    return out


def build_view(records: Iterable[TraceRecord], name: Optional[str] = None,
               world: Optional[World] = None) -> dict[str, Any]:
    """world: the mock environment's ground truth, when available, so I4 can be checked."""
    recs = list(records)
    header = next((r.data for r in recs if r.dir == "meta" and r.kind == "session_start"), {})
    live = any(r.dir == "note" and r.kind == "session_config" for r in recs)
    inputs = _user_inputs(recs, live)
    spoken = [{"ts": r.ts_ms, "type": r.kind, "text": r.data.get("text", ""), "purpose": r.data.get("purpose"),
               "caused_by": r.data.get("caused_by"), "slot": r.data.get("slot")}
              for r in recs if r.dir == "out" and r.kind in SPOKEN]
    if live:
        spoken = sorted(spoken + _live_speech(recs), key=lambda s: s["ts"])
    finals = [{"ts": r.ts_ms, "text": r.data["text"], "snapshot": r.data["snapshot"]}
              for r in recs if r.dir == "out" and r.kind == "final"]
    calls = _calls(recs)
    # In a LiveKit session the moment the agent's audio starts is recorded as
    # an agent_state note; that, not when the reply text was logged, is when it spoke.
    starts = spoken if not live else sorted(
        spoken + [{"ts": r.ts_ms, "type": "audio", "text": "(agent started speaking)"}
                  for r in recs if r.dir == "note" and r.kind == "agent_state" and r.data.get("state") == "speaking"],
        key=lambda s: s["ts"])
    latency = _latency(inputs, starts)
    measured = [x["ms"] for x in latency if x["ms"] is not None]
    violations = check_trace(recs, world)
    world_checked = world is not None  # a trace file alone carries no ground truth
    policies = [{"tool": r.data["tool"], "safety": r.data["safety"], "method": r.data.get("method"),
                 "confidence": r.data.get("confidence"), "idempotent": r.data.get("idempotent"),
                 "evidence": r.data.get("evidence") or []}
                for r in recs if r.dir == "note" and r.kind == "tool_policy"]
    end = max((r.ts_ms for r in recs), default=0)
    return {
        "name": name or header.get("scenario") or header.get("session_id") or "trace",
        "live": live,
        "header": header,
        "duration_ms": end,
        "inputs": inputs,
        "spoken": spoken,
        "finals": finals,
        "calls": calls,
        "slots": _slots(recs),
        "policies": policies,
        "latency": latency,
        "summary": {
            "tool_calls": sum(1 for c in calls if c["dispatched"] is not None),
            "cancels": sum(1 for r in recs if r.dir == "out" and r.kind == "cancel"),
            "writes_sent": sum(1 for c in calls if c["writes"] and c["dispatched"] is not None),
            "in_doubt": sum(1 for c in calls if c["in_doubt"]),
            "finals": len(finals),
            "first_response_max_ms": max(measured) if measured else None,
            "first_response_median_ms": statistics.median(measured) if measured else None,
        },
        "invariants": [
            {"id": i, "title": t, "detail": dsc,
             "status": ("unchecked" if i == "I4" and not world_checked
                        else "fail" if any(v.startswith(i + " ") for v in violations) else "pass"),
             "violations": [v for v in violations if v.startswith(i + " ")]}
            for i, t, dsc in INVARIANTS
        ],
        "log": [{"seq": r.seq, "ts": r.ts_ms, "dir": r.dir, "kind": r.kind, "data": r.data} for r in recs],
    }


def render(views: Sequence[dict[str, Any]], *, title: str = "Keel trace console") -> str:
    template = resources.files("keel.console").joinpath("template.html").read_text(encoding="utf-8")
    # "</" is escaped so no string inside the data can close the script element.
    payload = json.dumps(list(views), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (template.replace("__KEEL_TITLE__", html.escape(title))
            .replace("/*__KEEL_DATA__*/[]", payload))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m keel.console", description=__doc__.splitlines()[0])
    ap.add_argument("traces", nargs="+", type=Path, help="trace .jsonl files")
    ap.add_argument("-o", "--out", type=Path, default=Path("traces/console.html"))
    args = ap.parse_args(argv)
    views = [build_view(read_trace(p), name=p.stem) for p in args.traces]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(views), encoding="utf-8", newline="\n")
    print(f"wrote {args.out} ({len(views)} trace{'s' if len(views) != 1 else ''})")
    return 0
