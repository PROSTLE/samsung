"""Fetch the stateful multi-turn API docs from the Berkeley Function Calling
Leaderboard (ShishirPatil/gorilla, berkeley-function-call-leaderboard).

The multi_turn_func_doc/*.json files describe stateful APIs (vehicle control,
travel booking, ticketing, messaging, file system, trading) as JSONL, one
function per line. They mix read-only and state-changing tools, which is what
the manifest compiler needs. BFCL carries no read/write labels.

Schemas use BFCL's own dialect ("type": "dict", "float", ...), stored
verbatim; normalisation to JSON Schema is the compiler's job.

Output: data/tools/bfcl.jsonl
Usage:  python -m data.fetch.bfcl
"""

from __future__ import annotations

import json
import sys

from data.fetch import _common as c
from data.labels.ast_effects import class_method_effects

REPO = "ShishirPatil/gorilla"
DOC_DIR = "berkeley-function-call-leaderboard/bfcl_eval/data/multi_turn_func_doc/"
# Reference implementations of the same APIs, used to derive read/write labels.
SRC_DIR = "berkeley-function-call-leaderboard/bfcl_eval/eval_checker/multi_turn_eval/func_source_code/"


def parse_func_doc(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def main() -> int:
    sha = c.resolve_commit(REPO, "main")
    lic = c.repo_license(REPO)
    if lic != "Apache-2.0":
        print(f"license changed ({lic}); stopping", file=sys.stderr)
        return 1

    tree = c.list_tree(REPO, sha)
    paths = sorted(p for p in tree if p.startswith(DOC_DIR) and p.endswith(".json"))
    rows, per_domain, unlabelled = [], {}, []
    for path in paths:
        domain = path.rsplit("/", 1)[-1].removesuffix(".json")
        url = c.raw_url(REPO, sha, path)
        funcs = parse_func_doc(c.http_get(url).decode("utf-8"))
        per_domain[domain] = len(funcs)
        src_path = f"{SRC_DIR}{domain}.py"
        src_url = c.raw_url(REPO, sha, src_path) if src_path in tree else None
        effects = class_method_effects(c.http_get(src_url).decode("utf-8")) if src_url else {}
        for f in funcs:
            fx = effects.get(f["name"])
            if fx is None:
                unlabelled.append(f"{domain}.{f['name']}")
            rows.append({
                "source": "bfcl",
                "source_commit": sha,
                "source_url": url,
                "domain": domain,
                "name": f["name"],
                "description": f.get("description", ""),
                "parameters": f.get("parameters", {}),
                "response": f.get("response"),
                "schema_dialect": "bfcl",
                "labels": None,
                "impl_url": src_url,
                "impl_effects": None if fx is None else {"mutates": fx.mutates, "evidence": fx.evidence},
            })
    n = c.write_jsonl(c.TOOLS_DIR / "bfcl.jsonl", rows)
    c.save_license(REPO, sha, "LICENSE", "gorilla.LICENSE")
    c.record_source("bfcl", f"""
### Berkeley Function Calling Leaderboard, multi-turn API docs

- Repo: https://github.com/{REPO} @ `{sha}`, path `{DOC_DIR}`
- License: {lic}, per GitHub API (repository root LICENSE; the subdirectory has none of its own); copy in `data/tools/LICENSES/`
- Retrieved: {c.today()} by `python -m data.fetch.bfcl`
- Output: `data/tools/bfcl.jsonl` ({n} functions: {", ".join(f"{k}={v}" for k, v in per_domain.items())})
- No read/write labels upstream. Schema dialect is BFCL's (`"type": "dict"`, `"float"`), stored verbatim.
- `impl_effects`: whether the reference implementation in `{SRC_DIR}` mutates
  its state, by static analysis (`data/labels/ast_effects.py`). Functions with
  no matching implementation method: {len(unlabelled)}{"" if not unlabelled else " — " + ", ".join(unlabelled)}.
""")
    print(f"bfcl: {n} functions across {len(per_domain)} domains")
    return 0


if __name__ == "__main__":
    sys.exit(main())
