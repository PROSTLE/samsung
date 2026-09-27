"""Commit fence: when may a state-changing call leave the building?

Read-only calls start speculatively the moment their arguments resolve.
State-changing calls wait until the user has stopped revising what they read:

  1. the user's turn is closed (end-of-turn seen, or the open turn has gone
     quiet for `stale_turn_ms`, so a lost end-of-turn marker cannot block
     writes forever), and
  2. `quiet_ms` has passed since the later of: the end of that turn, and the
     last change to any slot the call reads, and
  3. the user is not speaking right now. An interruption marks the user as
     speaking until the next text/audio input ends that stretch of speech, so
     a long utterance can never outlast the stale-turn rule and let a write
     out mid-sentence.
  4. if the last turn was only an editing term ("oh, wait"; keel/kernel/repair.py),
     `repair_wait_ms` has passed since it ended. The next turn replaces it
     (and clears the wait), so the repair that follows is what calls act on.
  5. every stretch of speech that has ended has had its words arrive, or
     `transcript_wait_ms` has passed since it ended. In a cascade the words of
     a stretch come from speech-to-text after it ends; a turn can be committed
     (and a call planned) from the earlier stretches while the last one is
     still being transcribed, and that one may be the correction. An empty
     non-final text chunk marks a stretch ending, a non-empty one its words.
     Stretches and their words arrive in order; the cap covers a stretch that
     yields no words at all (LiveKit sends nothing for an empty transcript).

Self-repairs mostly land inside the same turn ("Friday, no, Saturday"), which
rule 1 covers; rule 2 covers the correction that arrives as a fresh turn just
after the first one ended; rule 4 covers the repair announced by a turn of its
own ("Oh, wait." ... "make it the 7th") after a pause longer than `quiet_ms`.
"""

from __future__ import annotations

from typing import Optional


class CommitFence:
    def __init__(self, *, quiet_ms: int, stale_turn_ms: int, require_end_of_turn: bool,
                 repair_wait_ms: int = 0, transcript_wait_ms: int = 0) -> None:
        self.quiet_ms = quiet_ms
        self.stale_turn_ms = stale_turn_ms
        self.require_end_of_turn = require_end_of_turn
        self.repair_wait_ms = repair_wait_ms
        self.transcript_wait_ms = transcript_wait_ms
        # End times of stretches of speech still waiting for their words (rule 5).
        self.untranscribed: list[int] = []
        # Which rule set the last opening time, for the trace: "repair", "transcript" or None.
        self.binding: Optional[str] = None
        self.turn_open = False
        self.speaking = False
        self.repair_pending = False
        self.last_user_activity = -1
        self.last_turn_end = -1
        self._last_change: dict[str, int] = {}

    def user_activity(self, now: int, *, end_of_turn: bool, speaking: bool = False,
                      editing_only: bool = False) -> None:
        self.last_user_activity = now
        self.turn_open = not end_of_turn
        self.speaking = speaking
        if end_of_turn:
            self.last_turn_end = now
            self.repair_pending = editing_only and self.repair_wait_ms > 0

    def speech_ended(self, now: int) -> None:
        """A stretch of speech ended; its words are not known yet (rule 5)."""
        if self.transcript_wait_ms > 0:
            self.untranscribed.append(now)

    def words_arrived(self, now: int) -> None:
        """Words arrived: they belong to the oldest stretch still waiting for its own."""
        self.untranscribed = [t for t in self.untranscribed if t + self.transcript_wait_ms > now]
        if self.untranscribed:
            self.untranscribed.pop(0)

    def slot_changed(self, name: str, now: int) -> None:
        self._last_change[name] = now

    def _turn_closed_at(self) -> Optional[int]:
        """When the current turn counts as closed; None if never (yet)."""
        if not self.require_end_of_turn:
            return self.last_user_activity
        if self.turn_open:
            return self.last_user_activity + self.stale_turn_ms
        return self.last_turn_end

    def opens_at(self, reads: dict[str, int]) -> Optional[int]:
        """Earliest virtual time at which a write reading these slots may go;
        None while the user is speaking (no time is known yet)."""
        if self.speaking:
            return None
        closed = self._turn_closed_at()
        anchors = [closed if closed is not None else -1]
        anchors += [self._last_change[s] for s in reads if s in self._last_change]
        latest = max(anchors)
        opens = latest + self.quiet_ms if latest >= 0 else 0
        self.binding = None
        if self.repair_pending and self.last_turn_end + self.repair_wait_ms > opens:
            opens, self.binding = self.last_turn_end + self.repair_wait_ms, "repair"
        if self.untranscribed and max(self.untranscribed) + self.transcript_wait_ms > opens:
            opens, self.binding = max(self.untranscribed) + self.transcript_wait_ms, "transcript"
        return opens
