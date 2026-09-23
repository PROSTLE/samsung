"""Fetch real tool schemas and author-assigned read/write labels from Sierra's
τ-bench (v1) and τ²-bench.

  τ-bench  (sierra-research/tau-bench): each tool file has a static
           get_info() returning an OpenAI-style function schema as a dict
           literal. We extract it with ast.literal_eval.
  τ²-bench (sierra-research/tau2-bench): tools are methods decorated with
           @is_tool(ToolType.READ|WRITE|THINK|GENERIC, mutates_state=...).
           Those decorator arguments are the authors' own labels; we extract
           them, plus the method signature and docstring, with ast.

Outputs:
  data/tools/tau_bench.jsonl   one row per tool, full JSON schema, no labels
  data/tools/tau2_bench.jsonl  one row per tool, author labels, signature

Usage:  python -m data.fetch.tau_bench
"""

from __future__ import annotations

import ast
import sys
from typing import Any, Optional

from data.fetch import _common as c

TAU1 = "sierra-research/tau-bench"
TAU2 = "sierra-research/tau2-bench"


# --------------------------------------------------------------------------
# τ-bench v1: get_info() dict literals
# --------------------------------------------------------------------------

def extract_get_info(source: str) -> Optional[dict]:
    """Return the dict literal returned by a `get_info` function, or None."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "get_info":
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Return) and stmt.value is not None:
                    return ast.literal_eval(stmt.value)
    return None


def fetch_tau1(sha: str) -> tuple[list[dict], list[str]]:
    rows, failures = [], []
    paths = [
        p for p in c.list_tree(TAU1, sha)
        if p.startswith("tau_bench/envs/") and "/tools/" in p
        and p.endswith(".py") and not p.endswith("__init__.py")
    ]
    for path in sorted(paths):
        domain = path.split("/")[2]
        url = c.raw_url(TAU1, sha, path)
        try:
            info = extract_get_info(c.http_get(url).decode("utf-8"))
        except (ValueError, SyntaxError) as exc:
            failures.append(f"{path}: {exc}")
            continue
        if info is None:
            failures.append(f"{path}: no get_info()")
            continue
        fn = info.get("function", info)
        rows.append({
            "source": "tau_bench",
            "source_commit": sha,
            "source_url": url,
            "domain": domain,
            "name": fn["name"],
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {}),
            "schema_dialect": "json-schema",
            "labels": None,
        })
    return rows, failures


# --------------------------------------------------------------------------
# τ²-bench: @is_tool decorator labels
# --------------------------------------------------------------------------

def _is_tool_decorator(dec: ast.expr) -> Optional[ast.Call]:
    if isinstance(dec, ast.Call):
        f = dec.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        if name == "is_tool":
            return dec
    return None


def _tool_type(call: ast.Call) -> str:
    node: Optional[ast.expr] = call.args[0] if call.args else None
    for kw in call.keywords:
        if kw.arg == "tool_type":
            node = kw.value
    if node is None:
        # is_tool's signature defaults tool_type to ToolType.READ
        # (src/tau2/environment/toolkit.py, verified; see SOURCES.md).
        return "READ"
    if isinstance(node, ast.Attribute):
        return node.attr
    raise ValueError(f"unrecognised tool_type expression: {ast.unparse(node)}")


def _mutates_state(call: ast.Call) -> Optional[bool]:
    for kw in call.keywords:
        if kw.arg == "mutates_state":
            return ast.literal_eval(kw.value)
    return None


def extract_is_tool_methods(source: str) -> list[dict[str, Any]]:
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        call = next((d for d in map(_is_tool_decorator, node.decorator_list) if d), None)
        if call is None:
            continue
        args = node.args
        positional = [a for a in args.posonlyargs + args.args if a.arg != "self"]
        n_defaults = len(args.defaults)
        params = []
        for i, a in enumerate(positional):
            has_default = i >= len(positional) - n_defaults
            params.append({
                "name": a.arg,
                "annotation": ast.unparse(a.annotation) if a.annotation else None,
                "required": not has_default,
            })
        for a, d in zip(args.kwonlyargs, args.kw_defaults):
            params.append({
                "name": a.arg,
                "annotation": ast.unparse(a.annotation) if a.annotation else None,
                "required": d is None,
            })
        out.append({
            "name": node.name,
            "tool_type": _tool_type(call),
            "mutates_state_explicit": _mutates_state(call),
            "docstring": ast.get_docstring(node) or "",
            "params": params,
        })
    return out


def fetch_tau2(sha: str) -> tuple[list[dict], list[str]]:
    rows, failures = [], []
    paths = [
        p for p in c.list_tree(TAU2, sha)
        if p.startswith("src/tau2/domains/") and p.rsplit("/", 1)[-1] in ("tools.py", "user_tools.py")
    ]
    for path in sorted(paths):
        domain = path.split("/")[3]
        role = "user" if path.endswith("user_tools.py") else "agent"
        url = c.raw_url(TAU2, sha, path)
        try:
            methods = extract_is_tool_methods(c.http_get(url).decode("utf-8"))
        except (ValueError, SyntaxError) as exc:
            failures.append(f"{path}: {exc}")
            continue
        for m in methods:
            explicit = m.pop("mutates_state_explicit")
            rows.append({
                "source": "tau2_bench",
                "source_commit": sha,
                "source_url": url,
                "domain": domain,
                "role": role,
                "name": m["name"],
                "description": m["docstring"],
                "params": m["params"],
                "schema_dialect": "python-signature",
                "labels": {
                    "tool_type": m["tool_type"],
                    "mutates_state_explicit": explicit,
                    # Upstream rule (toolkit.py is_tool docstring): when
                    # mutates_state is None it is True for WRITE, else False.
                    "mutates_state": explicit if explicit is not None else m["tool_type"] == "WRITE",
                    "label_origin": "upstream @is_tool decorator",
                },
            })
    return rows, failures


def main() -> int:
    sha1, sha2 = c.resolve_commit(TAU1, "main"), c.resolve_commit(TAU2, "main")
    lic1, lic2 = c.repo_license(TAU1), c.repo_license(TAU2)
    if lic1 != "MIT" or lic2 != "MIT":
        print(f"license changed (tau-bench={lic1}, tau2-bench={lic2}); stopping", file=sys.stderr)
        return 1

    rows1, fail1 = fetch_tau1(sha1)
    rows2, fail2 = fetch_tau2(sha2)
    n1 = c.write_jsonl(c.TOOLS_DIR / "tau_bench.jsonl", rows1)
    n2 = c.write_jsonl(c.TOOLS_DIR / "tau2_bench.jsonl", rows2)
    c.save_license(TAU1, sha1, "LICENSE", "tau-bench.LICENSE")
    c.save_license(TAU2, sha2, "LICENSE", "tau2-bench.LICENSE")

    by_type: dict[str, int] = {}
    for r in rows2:
        by_type[r["labels"]["tool_type"]] = by_type.get(r["labels"]["tool_type"], 0) + 1
    failures = fail1 + fail2
    c.record_source("tau_bench", f"""
### τ-bench and τ²-bench tool schemas (Sierra Research)

- Repos: https://github.com/{TAU1} @ `{sha1}`, https://github.com/{TAU2} @ `{sha2}`
- License: {lic1} (tau-bench), {lic2} (tau2-bench), per GitHub API; copies in `data/tools/LICENSES/`
- Retrieved: {c.today()} by `python -m data.fetch.tau_bench`
- Output: `data/tools/tau_bench.jsonl` ({n1} tools with JSON schemas),
  `data/tools/tau2_bench.jsonl` ({n2} tools with author labels: {", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))})
- Labels come from τ²-bench's own `@is_tool(ToolType.…, mutates_state=…)` decorators, not from us.
- Parse failures: {len(failures)}{"" if not failures else " — " + "; ".join(failures)}
""")
    print(f"tau_bench: {n1} tools, tau2_bench: {n2} tools, failures: {len(failures)}")
    for f in failures:
        print("  FAIL", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
