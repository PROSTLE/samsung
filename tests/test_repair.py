"""Announced self-repairs: an editing-term-only turn holds calls (fence rule 4)."""

from pathlib import Path

from keel.config import load_config
from keel.kernel.fence import CommitFence
from keel.kernel.repair import editing_only

ROOT = Path(__file__).resolve().parents[1]
TERMS = load_config(None, ROOT / "config" / "fdb_v3.toml").fence.editing_terms


def test_a_turn_of_editing_terms_only_announces_a_repair():
    for text in ("Oh, wait.", "wait", "Hold on...", "Um, actually,", "Sorry, let me think.", "hang on one second"):
        assert editing_only(text, TERMS), text


def test_a_turn_with_any_content_does_not():
    for text in ("Wait, make it Saturday.", "Oh, October 7th.", "No.", "Okay.", "Actually Pune", "", "  ...  "):
        assert not editing_only(text, TERMS), text


def test_multi_word_terms_match_whole_words_only():
    assert editing_only("hold on", ["hold on"])
    assert not editing_only("hold", ["hold on"])
    assert not editing_only("waiting", ["wait"])


def fence(**kw):
    return CommitFence(quiet_ms=600, stale_turn_ms=1000, require_end_of_turn=True, **kw)


def test_after_an_announced_repair_calls_wait_up_to_repair_wait_ms():
    f = fence(repair_wait_ms=5000)
    f.user_activity(10_000, end_of_turn=True, editing_only=True)
    assert f.opens_at({}) == 15_000


def test_the_next_turn_replaces_the_announcement():
    f = fence(repair_wait_ms=5000)
    f.user_activity(10_000, end_of_turn=True, editing_only=True)
    f.user_activity(12_000, end_of_turn=False, speaking=True)
    assert f.opens_at({}) is None                     # the repair is being said
    f.user_activity(13_000, end_of_turn=True)
    assert f.opens_at({}) == 13_600                   # back to the quiet period


def test_off_by_default_and_when_zero():
    for f in (fence(), fence(repair_wait_ms=0)):
        f.user_activity(10_000, end_of_turn=True, editing_only=True)
        assert f.opens_at({}) == 10_600
