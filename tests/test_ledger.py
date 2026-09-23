import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from keel.kernel.guard import WriteGuard
from keel.kernel.ledger import IllegalTransition, Ledger, idempotency_key


def ledger():
    g = WriteGuard()
    return g, Ledger(g, "s1")


def add(led, cid, tool="book", args=None, safety="state_changing"):
    return led.add(call_id=cid, step_id="s", goal_gen=1, tool=tool, arguments=args or {"d": 1},
                   reads={}, safety=safety, now=0)


def test_key_ignores_argument_order_but_not_session_or_tool():
    k = idempotency_key("s1", "book", {"a": 1, "b": 2})
    assert k == idempotency_key("s1", "book", {"b": 2, "a": 1})
    assert k != idempotency_key("s2", "book", {"a": 1, "b": 2})
    assert k != idempotency_key("s1", "cancel", {"a": 1, "b": 2})


def test_second_write_with_same_key_is_refused_in_every_holding_state():
    for path in ([], ["in_flight"], ["in_flight", "succeeded"], ["in_flight", "unknown"]):
        g, led = ledger()
        with g.hold():
            add(led, "c1")
            for st_ in path:
                led.transition("c1", st_, 1)
            with pytest.raises(IllegalTransition):
                add(led, "c2")


def test_write_may_be_reissued_after_confirmed_failure_or_pending_cancel():
    g, led = ledger()
    with g.hold():
        add(led, "c1")
        led.transition("c1", "cancelled", 1)  # never dispatched: outcome known
        add(led, "c2")
        led.transition("c2", "in_flight", 2)
        led.transition("c2", "failed", 3)
        add(led, "c3")


def test_cancelled_in_flight_write_stays_in_doubt_and_blocks():
    g, led = ledger()
    with g.hold():
        add(led, "c1")
        led.transition("c1", "in_flight", 1)
        e = led.transition("c1", "cancelled", 2)
        assert e.in_doubt and led.blocking(e.idem_key) is e
        with pytest.raises(IllegalTransition):
            add(led, "c2")
        led.settle_cancelled("c1", 3)
        assert not e.in_doubt
        add(led, "c2")


def test_late_success_after_cancel_is_recorded():
    g, led = ledger()
    with g.hold():
        add(led, "c1")
        led.transition("c1", "in_flight", 1)
        led.transition("c1", "cancelled", 2)
        e = led.transition("c1", "succeeded", 3)
    assert e.status == "succeeded" and not e.in_doubt


def test_read_only_calls_never_enter_doubt():
    g, led = ledger()
    with g.hold():
        add(led, "r1", tool="search", safety="read_only")
        led.transition("r1", "in_flight", 1)
        e = led.transition("r1", "cancelled", 2)
        assert not e.in_doubt
        with pytest.raises(IllegalTransition):
            led.transition("r1", "succeeded", 3)


def test_unknown_safety_is_treated_as_state_changing():
    g, led = ledger()
    with g.hold():
        add(led, "u1", safety="unknown")
        with pytest.raises(IllegalTransition):
            add(led, "u2", safety="unknown")


def test_illegal_transitions():
    g, led = ledger()
    with g.hold():
        add(led, "c1")
        with pytest.raises(IllegalTransition):
            led.transition("c1", "succeeded", 1)  # pending -> succeeded
        led.transition("c1", "in_flight", 1)
        led.transition("c1", "failed", 2)
        with pytest.raises(IllegalTransition):
            led.transition("c1", "succeeded", 3)


def test_retry_of_only_for_the_unknown_holder():
    g, led = ledger()
    with g.hold():
        add(led, "c1")
        led.transition("c1", "in_flight", 1)
        with pytest.raises(IllegalTransition):
            led.add(call_id="c2", step_id="s", goal_gen=1, tool="book", arguments={"d": 1}, reads={},
                    safety="state_changing", now=2, retry_of="c1")
        led.transition("c1", "unknown", 2)
        led.add(call_id="c2", step_id="s", goal_gen=1, tool="book", arguments={"d": 1}, reads={},
                safety="state_changing", now=3, retry_of="c1")


class LedgerMachine(RuleBasedStateMachine):
    """Random operation sequences never leave two live writes on one key."""

    def __init__(self):
        super().__init__()
        self.guard, self.led = ledger()
        self.n = 0

    @rule(arg=st.integers(0, 2))
    def try_add(self, arg):
        self.n += 1
        with self.guard.hold():
            try:
                add(self.led, f"c{self.n}", args={"d": arg})
            except IllegalTransition:
                pass

    @rule(i=st.integers(0, 30), to=st.sampled_from(["in_flight", "succeeded", "failed", "unknown", "cancelled"]))
    def try_transition(self, i, to):
        ids = [e.call_id for e in self.led]
        if not ids:
            return
        with self.guard.hold():
            try:
                self.led.transition(ids[i % len(ids)], to, self.n)
            except IllegalTransition:
                pass

    @invariant()
    def at_most_one_live_write_per_key(self):
        live: dict[str, int] = {}
        for e in self.led:
            if e.status in ("pending", "in_flight", "succeeded", "unknown") or e.in_doubt:
                live[e.idem_key] = live.get(e.idem_key, 0) + 1
        assert all(n == 1 for n in live.values()), live


TestLedgerMachine = LedgerMachine.TestCase
TestLedgerMachine.settings = settings(max_examples=150, stateful_step_count=40, deadline=None)
