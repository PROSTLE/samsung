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
