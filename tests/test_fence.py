from keel.kernel.fence import CommitFence


def fence(**kw):
    args = dict(quiet_ms=600, stale_turn_ms=1000, require_end_of_turn=True)
    args.update(kw)
    return CommitFence(**args)


def test_opens_quiet_ms_after_the_later_of_turn_end_and_slot_change():
    f = fence()
    f.user_activity(1000, end_of_turn=True)
    f.slot_changed("day", 1300)
    assert f.opens_at({"day": 1}) == 1900
    assert f.opens_at({"other": 1}) == 1600  # unrelated slot: only turn end counts


def test_open_turn_holds_until_it_goes_stale():
    f = fence()
    f.user_activity(1000, end_of_turn=False)
    assert f.opens_at({}) == 1000 + 1000 + 600
    f.user_activity(1500, end_of_turn=False)  # still talking
    assert f.opens_at({}) == 1500 + 1000 + 600
    f.user_activity(1700, end_of_turn=True)
    assert f.opens_at({}) == 1700 + 600


def test_without_end_of_turn_requirement_only_activity_counts():
    f = fence(require_end_of_turn=False)
    f.user_activity(1000, end_of_turn=False)
    assert f.opens_at({}) == 1600


def test_no_activity_means_open_now():
    assert fence().opens_at({}) == 0


def test_while_the_user_speaks_the_fence_has_no_opening_time():
    f = fence()
    f.user_activity(1000, end_of_turn=True)
    f.user_activity(1200, end_of_turn=False, speaking=True)   # an interruption: speaking again
    assert f.opens_at({}) is None
    f.user_activity(4000, end_of_turn=False)                  # that stretch of speech ended
    assert f.opens_at({}) == 4000 + 1000 + 600                # turn still open: stale rule
    f.user_activity(4100, end_of_turn=True)
    assert f.opens_at({}) == 4100 + 600


# ---------------------------------------------------------------- rule 5: words still being transcribed
def test_a_stretch_of_speech_holds_until_its_words_arrive():
    f = fence(transcript_wait_ms=8000)
    f.user_activity(9800, end_of_turn=False)
    f.speech_ended(9800)                       # first stretch ends ...
    f.words_arrived(10200)                     # ... and its words come
    f.speech_ended(22166)                      # the last stretch ends; its words are still coming
    f.user_activity(22611, end_of_turn=True)   # the turn is committed without them
    assert f.opens_at({}) == 22166 + 8000 and f.binding == "transcript"
    f.words_arrived(28507)                     # they arrive: only the ordinary rules remain
    assert f.opens_at({}) == 22611 + 600 and f.binding is None


def test_words_belong_to_the_oldest_stretch_waiting_for_them():
    f = fence(transcript_wait_ms=5000)
    f.speech_ended(1000)
    f.speech_ended(2000)
    f.words_arrived(2500)                      # the first stretch's words: the second still waits
    assert f.opens_at({}) == 2000 + 5000
    f.words_arrived(2600)
    assert f.untranscribed == [] and f.opens_at({}) == 0     # no stretch waits; nothing else recorded here


def test_a_stretch_that_yields_no_words_holds_only_until_the_cap():
    f = fence(transcript_wait_ms=5000)
    f.speech_ended(1000)                       # noise: LiveKit sends no event for an empty transcript
    f.user_activity(1200, end_of_turn=True)
    assert f.opens_at({}) == 6000
    f.words_arrived(7000)                      # a later stretch's words do not revive an expired one
    assert f.untranscribed == []


def test_rule_five_is_off_by_default():
    f = fence()
    f.speech_ended(1000)
    f.user_activity(1200, end_of_turn=True)
    assert f.untranscribed == [] and f.opens_at({}) == 1800
