"""Train the shipped lexical safety model on all gold labels.

Usage: python -m keel.compiler.train     (writes keel/compiler/model.json)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from keel.compiler.classify import MODEL_PATH, LexicalModel, features, idf_table, train

GOLD = Path(__file__).resolve().parents[2] / "data" / "labels" / "gold.jsonl"


def load_gold(path: Path = GOLD) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def row_features(row: dict, idf: dict[str, float] | None) -> dict[str, float]:
    return features(row["name"], row["description"], [p["name"] for p in row["params"]], idf)


def fit(rows: list[dict], meta: dict | None = None) -> LexicalModel:
    """IDF is computed from the training rows only, then stored with the model."""
    idf = idf_table(r["description"] for r in rows)
    model = train([(row_features(r, idf), int(r["label"] == "state_changing")) for r in rows], meta=meta)
    model.meta["idf"] = idf
    return model


def predict(model: LexicalModel, row: dict) -> float:
    return model.p_write(row_features(row, model.idf))


def main() -> int:
    raw = GOLD.read_bytes()
    rows = load_gold()
    model = fit(rows, meta={"trained_on": "data/labels/gold.jsonl", "gold_sha256": hashlib.sha256(raw).hexdigest(),
                            "rows": len(rows), "algorithm": "L2 logistic regression, full-batch GD from zero"})
    MODEL_PATH.write_text(model.to_json() + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {MODEL_PATH.name}: {len(model.weights)} weights from {len(rows)} tools")
    return 0


if __name__ == "__main__":
    sys.exit(main())
