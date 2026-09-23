import asyncio

import pytest
from hypothesis import given
from hypothesis import strategies as st

from keel.kernel.clock import MonotonicClock, VirtualClock


def test_timers_fire_in_deadline_then_insertion_order():
    clock, fired = VirtualClock(), []
    clock.call_at(50, lambda: fired.append("b"))
    clock.call_at(5, lambda: fired.append("a"))
    clock.call_at(50, lambda: fired.append("c"))
    clock.run_until_idle()
    assert fired == ["a", "b", "c"]
    assert clock.now_ms() == 50


def test_callbacks_observe_their_own_deadline():
    clock, seen = VirtualClock(), []
    for t in (0, 5, 50):
        clock.call_at(t, lambda: seen.append(clock.now_ms()))
    clock.run_until_idle()
    assert seen == [0, 5, 50]


def test_advance_fires_only_due_timers_and_sets_now():
    clock, fired = VirtualClock(), []
    clock.call_at(10, lambda: fired.append(10))
    clock.call_at(30, lambda: fired.append(30))
    clock.advance_to(20)
    assert fired == [10] and clock.now_ms() == 20
    assert clock.next_deadline() == 30


def test_timers_scheduled_during_advance_fire_if_inside_window():
    clock, fired = VirtualClock(), []
    clock.call_at(10, lambda: clock.call_later(5, lambda: fired.append(clock.now_ms())))
    clock.advance_to(15)
    assert fired == [15]


def test_cancelled_timer_never_fires():
    clock, fired = VirtualClock(), []
    h = clock.call_at(10, lambda: fired.append("x"))
    clock.call_at(20, lambda: fired.append("y"))
    h.cancel()
    assert clock.pending() == 1
    clock.run_until_idle()
    assert fired == ["y"]


def test_time_never_moves_backwards():
    clock = VirtualClock()
    clock.advance_to(100)
    with pytest.raises(ValueError):
        clock.advance_to(99)
    with pytest.raises(ValueError):
        clock.call_at(99, lambda: None)
    with pytest.raises(ValueError):
        clock.call_later(-1, lambda: None)


@given(st.lists(st.tuples(st.integers(0, 10_000), st.booleans()), max_size=200))
def test_firing_order_is_sorted_and_skips_cancelled(specs):
    clock, fired = VirtualClock(), []
    expected = []
    for i, (t, keep) in enumerate(specs):
        h = clock.call_at(t, lambda i=i: fired.append(i))
        if keep:
            expected.append((t, i))
        else:
            h.cancel()
    clock.run_until_idle()
    assert fired == [i for _, i in sorted(expected)]


@given(st.lists(st.integers(0, 1000), min_size=1, max_size=50))
def test_replay_is_deterministic(deadlines):
    def run():
        clock, log = VirtualClock(), []
        for i, t in enumerate(deadlines):
            clock.call_at(t, lambda i=i: log.append((clock.now_ms(), i)))
        clock.run_until_idle()
        return log

    assert run() == run()


@pytest.mark.parametrize("delay_ms", [1, 5, 20])
def test_monotonic_clock_never_fires_early(delay_ms):
    # Regression: on Windows, asyncio's loop clock has 15.625 ms resolution and
    # fired a 1 ms timer while now_ms() still read 0.
    async def run():
        clock, fired, observed = MonotonicClock(), asyncio.Event(), []

        def on_fire():
            observed.append(clock.now_ms())
            fired.set()

        clock.call_later(delay_ms, on_fire)
        h = clock.call_later(delay_ms, lambda: observed.append("cancelled timer ran"))
        h.cancel()
        await asyncio.wait_for(fired.wait(), timeout=2)
        await asyncio.sleep(delay_ms / 1000 + 0.02)
        return observed

    observed = asyncio.run(run())
    assert len(observed) == 1 and observed[0] >= delay_ms
