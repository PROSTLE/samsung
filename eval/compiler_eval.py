"""Leave-one-domain-out evaluation of the manifest compiler's safety classifier.

Each domain group in turn is held out entirely (its tools are "unseen"), the
model is trained on the rest, and the held-out tools are classified. BFCL's
three memory_* domains share near-identical tool sets, so they form one group.

A tool is treated as read_only only if p(write) <= 1 - t for confidence
threshold t; everything else (including "unknown") is treated as
state_changing, exactly as the kernel does.

  unsafe        gold state_changing, treated read_only (could run speculatively)
  read_recall   gold read_only tools that Keel may run speculatively
  accuracy      treated label == gold

Two ways of choosing t are reported:
  pre-registered  the config value fixed before any evaluation (0.8)
  nested          per outer fold, t is chosen on inner leave-one-domain-out
                  predictions over the *training* domains only: the lowest t
                  with zero unsafe inner errors. Held-out tools never
                  influence their own threshold.

Usage: python -m eval.compiler_eval [--json reports/compiler_eval.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from keel.compiler.classify import split_identifier
from keel.compiler.train import fit, load_gold, predict

GRID = (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95)
PRE_REGISTERED = 0.8
# Baseline only: the "obvious" verb-list heuristic a reviewer might propose.
READ_VERBS = frozenset("get list search find check show view display retrieve fetch lookup estimate "
                       "calculate compute query read count describe verify".split())


def group_of(row: dict) -> str:
    d = row["domain"]
    if row["source"] == "bfcl":
        return "bfcl:memory" if d.startswith("memory") else f"bfcl:{d}"
    return f"tau:{d}"


def treat(p: float, t: float) -> str:
    return "read_only" if p <= 1 - t else "state_changing"


def score(pairs: list[tuple[str, str]]) -> dict:
    """pairs of (gold, treated)"""
    n = len(pairs)
    writes = [t for g, t in pairs if g == "state_changing"]
    reads = [t for g, t in pairs if g == "read_only"]
    return {
        "n": n,
        "accuracy": round(sum(g == t for g, t in pairs) / n, 4) if n else None,
        "unsafe": sum(t == "read_only" for t in writes),
        "read_recall": round(sum(t == "read_only" for t in reads) / len(reads), 4) if reads else None,
    }


def lodo_predictions(rows: list[dict]) -> list[float]:
    p = [0.0] * len(rows)
    for g in sorted({group_of(r) for r in rows}):
        model = fit([r for r in rows if group_of(r) != g])
        for i, r in enumerate(rows):
            if group_of(r) == g:
                p[i] = predict(model, r)
    return p


def choose_threshold(rows: list[dict], p: list[float]) -> float:
    for t in GRID:  # lowest threshold (most speculation) with zero unsafe errors
        if score([(r["label"], treat(pi, t)) for r, pi in zip(rows, p)])["unsafe"] == 0:
            return t
    return GRID[-1]


def outer_fold(args: tuple[list[dict], str]) -> tuple[str, float, list[tuple[int, float]]]:
    rows, g = args
    train_rows = [r for r in rows if group_of(r) != g]
    t = choose_threshold(train_rows, lodo_predictions(train_rows))
    model = fit(train_rows)
    return g, t, [(i, predict(model, r)) for i, r in enumerate(rows) if group_of(r) == g]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    rows = load_gold()
    groups = sorted({group_of(r) for r in rows})
    with ProcessPoolExecutor() as pool:
        folds = list(pool.map(outer_fold, [(rows, g) for g in groups]))
    p = [0.0] * len(rows)
    nested_t: dict[str, float] = {}
    for g, t, preds in folds:
        nested_t[g] = t
        for i, pi in preds:
            p[i] = pi

    def report(pairs_for: dict[int, str]) -> dict:
        pairs = [(r["label"], pairs_for[i]) for i, r in enumerate(rows)]
        by_source = defaultdict(list)
        for (gold, treated), r in zip(pairs, rows):
            by_source[r["source"]].append((gold, treated))
        return {"all": score(pairs), **{s: score(ps) for s, ps in sorted(by_source.items())}}

    results: dict = {"n_tools": len(rows), "groups": groups, "thresholds": {}}
    for t in GRID:
        results["thresholds"][str(t)] = report({i: treat(p[i], t) for i in range(len(rows))})
    results["pre_registered"] = {"t": PRE_REGISTERED, **results["thresholds"][str(PRE_REGISTERED)]}
    results["nested"] = {"t_per_group": nested_t,
                         **report({i: treat(p[i], nested_t[group_of(r)]) for i, r in enumerate(rows)})}
    results["zero_unsafe_rule_on_all_data"] = choose_threshold(rows, p)
    results["baselines"] = {
        "everything_state_changing": score([(r["label"], "state_changing") for r in rows]),
        "verb_list": score([(r["label"], "read_only" if (split_identifier(r["name"]) or [""])[0] in READ_VERBS
                             else "state_changing") for r in rows]),
    }
    results["argmax_errors"] = sorted(
        ({"name": r["name"], "domain": r["domain"], "gold": r["label"], "p_write": round(p[i], 3)}
         for i, r in enumerate(rows) if (p[i] >= 0.5) != (r["label"] == "state_changing")),
        key=lambda e: -abs(e["p_write"] - 0.5))

    print(f"leave-one-domain-out: {len(groups)} domain groups, {len(rows)} real tools")
    print(f"{'threshold':>9} {'accuracy':>9} {'unsafe':>7} {'read_recall':>12} {'tau2 acc':>9} {'bfcl acc':>9}")
    for t in GRID:
        r = results["thresholds"][str(t)]
        print(f"{t:>9} {r['all']['accuracy']:>9.3f} {r['all']['unsafe']:>7d} {r['all']['read_recall']:>12.3f}"
              f" {r['tau2_bench']['accuracy']:>9.3f} {r['bfcl']['accuracy']:>9.3f}")
    for label, r in (("pre-registered t=0.8", results["pre_registered"]), ("nested-CV threshold", results["nested"])):
        print(f"{label:<22} accuracy {r['all']['accuracy']:.3f}  unsafe {r['all']['unsafe']}  "
              f"read_recall {r['all']['read_recall']:.3f}")
    print(f"nested thresholds chosen per group: {sorted(set(nested_t.values()))}")
    print(f"zero-unsafe rule applied to all data: {results['zero_unsafe_rule_on_all_data']} "
          f"(not shipped: forced up by the single get_flight_cost label; see docs/reports/PHASE_3.md)")
    for name, b in results["baselines"].items():
        print(f"baseline {name:<26} accuracy {b['accuracy']:.3f}  unsafe {b['unsafe']}  read_recall {b['read_recall']:.3f}")
    print(f"argmax errors: {len(results['argmax_errors'])}")
    for e in results["argmax_errors"][:10]:
        print(f"   {e['domain']}.{e['name']}: gold={e['gold']} p(write)={e['p_write']}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
