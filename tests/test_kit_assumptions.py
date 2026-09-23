"""Every TODO(kit) in code has an ID, and code IDs match docs/KIT_ASSUMPTIONS.md."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"TODO\(kit\):\s*(\[K\d{2}\])?")


def code_ids():
    ids, untagged = set(), []
    for path in (ROOT / "keel").rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in TAG.finditer(line):
                if m.group(1):
                    ids.add(m.group(1)[1:-1])
                else:
                    untagged.append(f"{path.relative_to(ROOT)}:{n}")
    return ids, untagged


def doc_ids():
    text = (ROOT / "docs" / "KIT_ASSUMPTIONS.md").read_text(encoding="utf-8")
    return set(re.findall(r"^\| (K\d{2}) \|", text, re.MULTILINE))


def test_every_kit_todo_has_an_id():
    _, untagged = code_ids()
    assert untagged == []


def test_code_and_doc_list_the_same_assumptions():
    ids, _ = code_ids()
    assert ids == doc_ids()
