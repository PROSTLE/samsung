"""Commit fence: when may a state-changing call leave the building?

Read-only calls start speculatively the moment their arguments resolve.
State-changing calls wait until the user has stopped revising what they read:

  1. the user's turn is closed (end-of-turn seen, or the open turn has gone
     quiet for `stale_turn_ms`, so a lost end-of-turn marker cannot block
     writes forever), and
  2. `quiet_ms` has passed since the later of: the end of that turn, and the
     last change to any slot the call reads.

Self-repairs mostly land inside the same turn ("Friday, no, Saturday"), which
rule 1 covers; rule 2 covers the correction that arrives as a fresh turn just
after the first one ended.
"""

from __future__ import annotations

from typing import Optional


class CommitFence:
    def __init__(self, *, quiet_ms: int, stale_turn_ms: int, require_end_of_turn: bool) -> None:
        self.quiet_ms = quiet_ms
        self.stale_turn_ms = stale_turn_ms
        self.require_end_of_turn = require_end_of_turn
        self.turn_open = False
        self.last_user_activity = -1
        self.last_turn_end = -1
        self._last_change: dict[str, int] = {}

    def user_activity(self, now: int, *, end_of_turn: bool) -> None:
        self.last_user_activity = now
        self.turn_open = not end_of_turn
        if end_of_turn:
            self.last_turn_end = now

    def slot_changed(self, name: str, now: int) -> None:
        self._last_change[name] = now

    def _turn_closed_at(self) -> Optional[int]:
        """When the current turn counts as closed; None if never (yet)."""
        if not self.require_end_of_turn:
            return self.last_user_activity
        if self.turn_open:
            return self.last_user_activity + self.stale_turn_ms
        return self.last_turn_end

    def opens_at(self, reads: dict[str, int]) -> int:
        """Earliest virtual time at which a write reading these slots may go."""
        closed = self._turn_closed_at()
        anchors = [closed if closed is not None else -1]
        anchors += [self._last_change[s] for s in reads if s in self._last_change]
        latest = max(anchors)
        return latest + self.quiet_ms if latest >= 0 else 0
