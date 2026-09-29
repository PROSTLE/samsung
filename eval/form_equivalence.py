"""Re-score an FDB-v3 run with argument values compared by form-insensitive equality.

FDB-v3's rule-based check (no gpt-4o judge) fails an argument unless it matches the
expected value exactly, so "2026-07-15" for "July 15" or "PO-999" for "PO999" count
as wrong. Its judge exists to compare by meaning. This script is our stand-in for
that, with a rule we state, and it is an estimate, not a score:

  two values are equal if they are the same calendar day written differently
  ("2026-07-15" and "July 15"), equal numbers ("1800" and 1800), the same boolean
  ("true" and True), or the same words after ignoring case, spaces, punctuation
  and a plural "s".

A scenario is re-counted as passing only if FDB-v3's strict check had the right
tools (none missing, none unexpected) and every argument it failed is equal under
the rule. A missing argument or a different value is never forgiven.

    python -m eval.form_equivalence results/fdb_v3/<run>/<provider>_pass_rate_report.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}


def _day(v) -> tuple[int, int] | None:
    """(month, day) of a date written as ISO or as 'Month D', else None."""
    s = str(v).strip().lower()
    m = re.fullmatch(r"\d{4}-(\d{1,2})-(\d{1,2})", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*\d{4})?", s)
    if m and m.group(1) in MONTHS:
        return MONTHS[m.group(1)], int(m.group(2))
    return None


def _number(v) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def _words(v) -> str:
    return "".join(re.sub(r"s$", "", w) for w in re.findall(r"[a-z0-9]+", str(v).lower()))


def equal(expected, actual) -> bool:
    if expected == actual:
        return True
    if expected is None or actual is None:
        return False
    de, da = _day(expected), _day(actual)
    if de and da:
        return de == da
    ne, na = _number(expected), _number(actual)
    if ne is not None and na is not None:
        return ne == na
    if str(expected).lower() in ("true", "false") and str(actual).lower() in ("true", "false"):
        return str(expected).lower() == str(actual).lower()
    return _words(expected) == _words(actual) and _words(expected) != ""


def rescore(report: dict) -> tuple[int, int, list[str]]:
    strict = flipped = 0
    names = []
    for r in report["scenario_results"]:
        if r["passed"]:
            strict += 1
            continue
        checks = r.get("checks", {})
        ts = checks.get("tool_selection", {})
        if not ts.get("passed"):
            continue
        ok = True
        for d in checks.get("argument_accuracy", {}).get("details", []):
            if d.get("passed"):
                continue
            exp, act = d.get("expected_args") or {}, d.get("actual_args") or {}
            for k, v in exp.items():
                if isinstance(v, str) and v.startswith("$RESULT"):
                    continue
                if k not in act or not equal(v, act[k]):
                    ok = False
        if ok:
            flipped += 1
            names.append(r["scenario_id"])
    return strict, flipped, names


def main() -> None:
    path = Path(sys.argv[1])
    report = json.loads(path.read_text(encoding="utf-8"))
    strict, flipped, names = rescore(report)
    total = len(report["scenario_results"])
    print(f"strict pass (FDB-v3, exact match): {strict}/{total}")
    print(f"also equal under the form rule:    {flipped} ({', '.join(sorted(names))})")
    print(f"estimate with the form rule:       {strict + flipped}/{total}  (an estimate, not a score)")


if __name__ == "__main__":
    main()
