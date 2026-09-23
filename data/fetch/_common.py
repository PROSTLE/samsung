"""Shared helpers for data/fetch scripts.

Every fetch is pinned to a commit SHA so the data is reproducible, and every
script rewrites its own section of SOURCES.md with URL, license, commit and
retrieval date. Nothing here executes third-party code: Python sources are
parsed with `ast`, never imported.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "data" / "tools"
SOURCES_MD = REPO_ROOT / "SOURCES.md"
USER_AGENT = "keel-data-fetch (+https://github.com/)"


def http_get(url: str) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def http_json(url: str) -> Any:
    return json.loads(http_get(url))


def resolve_commit(repo: str, ref: str) -> str:
    return http_json(f"https://api.github.com/repos/{repo}/commits/{ref}")["sha"]


def repo_license(repo: str) -> str:
    lic = http_json(f"https://api.github.com/repos/{repo}").get("license") or {}
    return lic.get("spdx_id") or "UNKNOWN"


def list_tree(repo: str, sha: str) -> list[str]:
    tree = http_json(f"https://api.github.com/repos/{repo}/git/trees/{sha}?recursive=1")
    if tree.get("truncated"):
        raise RuntimeError(f"tree listing for {repo}@{sha} was truncated")
    return [e["path"] for e in tree["tree"] if e["type"] == "blob"]


def raw_url(repo: str, sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{path}"


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            fh.write("\n")
            n += 1
    return n


def save_license(repo: str, sha: str, license_path: str, dest_name: str) -> Path:
    dest = TOOLS_DIR / "LICENSES" / dest_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(http_get(raw_url(repo, sha, license_path)))
    return dest


def today() -> str:
    return _dt.date.today().isoformat()


def record_source(key: str, body: str) -> None:
    """Replace (or append) the SOURCES.md section delimited by markers for key."""
    start, end = f"<!-- source:{key} -->", f"<!-- /source:{key} -->"
    block = f"{start}\n{body.strip()}\n{end}"
    text = SOURCES_MD.read_text(encoding="utf-8") if SOURCES_MD.exists() else "# Sources\n"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if pattern.search(text):
        text = pattern.sub(lambda _m: block, text)
    else:
        text = text.rstrip("\n") + "\n\n" + block + "\n"
    SOURCES_MD.write_text(text, encoding="utf-8", newline="\n")
