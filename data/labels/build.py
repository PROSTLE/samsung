"""Build data/labels/gold.jsonl from the fetched tool data (see POLICY.md).

Usage: python -m data.labels.build
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "data" / "tools"
OUT = ROOT / "data" / "labels" / "gold.jsonl"

# POLICY.md "Keel overrides": contacting / handing off to a person or third party.
HANDOFF_OVERRIDES = {
    ("tau2_bench", "transfer_to_human_agents"): "hands the conversation to a human (POLICY.md)",
    ("bfcl", "contact_customer_support"): "contacts a third party (POLICY.md)",
}


def _load(name: str) -> list[dict]:
    return [json.loads(line) for line in (TOOLS / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def _params_from_schema(schema: dict) -> list[dict]:
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    return [{"name": k, "type": (v or {}).get("type"), "description": (v or {}).get("description", ""),
             "required": k in required} for k, v in props.items()]


def build() -> tuple[list[dict], dict]:
    tau1 = _load("tau_bench.jsonl")
    tau2 = _load("tau2_bench.jsonl")
    bfcl = _load("bfcl.jsonl")
    tau1_by_key = {(r["domain"], r["name"]): r for r in tau1}

    rows: list[dict] = []
    agree = compared = 0
    for r in tau2:
        lab = r["labels"]
        gold = "state_changing" if lab["mutates_state"] else "read_only"
        origin = f"τ²-bench author label {lab['tool_type']}" + (
            f" mutates_state={lab['mutates_state_explicit']}" if lab["mutates_state_explicit"] is not None else "")
        twin = tau1_by_key.get((r["domain"], r["name"]))
        if twin is not None and twin.get("impl_effects") is not None:
            compared += 1
            agree += twin["impl_effects"]["mutates"] == lab["mutates_state"]
        override = HANDOFF_OVERRIDES.get(("tau2_bench", r["name"]))
        if override:
            gold, origin = "state_changing", f"{origin}; Keel override: {override}"
        rows.append({
            "source": "tau2_bench", "domain": r["domain"], "name": r["name"],
            "description": r["description"],
            "parameters": twin["parameters"] if twin else None,
            "params": _params_from_schema(twin["parameters"]) if twin else
            [{"name": p["name"], "type": p["annotation"], "description": "", "required": p["required"]}
             for p in r["params"]],
            "label": gold, "label_origin": origin,
        })

    tau2_keys = {(r["domain"], r["name"]) for r in tau2}
    for r in tau1:
        if (r["domain"], r["name"]) in tau2_keys or r.get("impl_effects") is None:
            continue
        rows.append({
            "source": "tau_bench", "domain": r["domain"], "name": r["name"], "description": r["description"],
            "parameters": r["parameters"], "params": _params_from_schema(r["parameters"]),
            "label": "state_changing" if r["impl_effects"]["mutates"] else "read_only",
            "label_origin": "reference implementation analysis",
        })

    for r in bfcl:
        fx = r.get("impl_effects")
        if fx is None:
            continue
        gold = "state_changing" if fx["mutates"] else "read_only"
        origin = "reference implementation analysis" + (f": {fx['evidence'][0]}" if fx["evidence"] else "")
        override = HANDOFF_OVERRIDES.get(("bfcl", r["name"]))
        if override:
            gold, origin = "state_changing", f"{origin}; Keel override: {override}"
        rows.append({
            "source": "bfcl", "domain": r["domain"], "name": r["name"], "description": r["description"],
            "parameters": r["parameters"], "params": _params_from_schema(r["parameters"]),
            "label": gold, "label_origin": origin,
        })

    stats = {
        "rows": len(rows),
        "by_source": dict(Counter(r["source"] for r in rows)),
        "by_label": dict(Counter(r["label"] for r in rows)),
        "domains": len({(r["source"], r["domain"]) for r in rows}),
        "labeller_validation": {"agree": agree, "compared": compared},
    }
    return rows, stats


def main() -> int:
    rows, stats = build()
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                   encoding="utf-8", newline="\n")
    v = stats["labeller_validation"]
    print(f"gold: {stats['rows']} tools, {stats['domains']} domains, {stats['by_source']}, {stats['by_label']}")
    print(f"labeller vs tau2-bench authors on shared tools: {v['agree']}/{v['compared']}")
    return 0 if v["agree"] == v["compared"] else 1


if __name__ == "__main__":
    sys.exit(main())
