"""Is a user turn nothing but an editing term, i.e. a self-repair still to come?

Levelt (1983, "Monitoring and self-repair in speech", Cognition 14:41-104)
splits a self-repair into three phases: the speaker interrupts, then comes "a
phase characterized by hesitation, pausing, but especially the use of so-called
editing terms", then "the repair proper". A turn made only of such terms
("Oh, wait.") ends in the middle phase: the repair has not been said yet, so a
call planned from it was planned from what the user is about to revise.

The terms come from config (`fence.editing_terms`). A turn counts only if every
word in it belongs to one of them, so "wait, make it Saturday" (which carries
the repair itself) never does.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_WORD = re.compile(r"[a-z']+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def editing_only(text: str, terms: Iterable[str]) -> bool:
    """True if `text` is one or more editing terms and nothing else."""
    words = _words(text)
    phrases = sorted({tuple(_words(t)) for t in terms if _words(t)}, key=len, reverse=True)
    if not words or not phrases:
        return False
    i = 0
    while i < len(words):
        for p in phrases:
            if tuple(words[i:i + len(p)]) == p:
                i += len(p)
                break
        else:
            return False
    return True
