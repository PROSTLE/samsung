import threading

import pytest
from hypothesis import given
from hypothesis import strategies as st

from keel.kernel.guard import SingleWriterViolation, WriteGuard
from keel.kernel.slots import Rejection, SlotChange, SlotStore


def store():
    g = WriteGuard()
    return g, SlotStore(g)


def test_new_version_only_when_value_changes():
    g, s = store()
    with g.hold():
        a = s.propose("city", "Pune", source="text", confidence=0.9, now=10)
        same = s.propose("city", "Pune", source="audio", confidence=0.7, now=20)
        b = s.propose("city", "Mumbai", source="text", confidence=0.9, now=30)
    assert isinstance(a, SlotChange) and a.new.version == 1 and a.old is None
    assert same is None
    assert isinstance(b, SlotChange) and (b.old.version, b.new.version) == (1, 2)
    assert [v.value for v in s.history("city")] == ["Pune", "Mumbai"]
    assert s.get("city").updated_at == 30 and s.version("missing") == 0


def test_correction_locks_against_later_perception():
    g, s = store()
    with g.hold():
        s.propose("appliance", "washer", source="frame", confidence=0.8, now=0)
        s.propose("appliance", "dryer", source="text", confidence=0.95, now=10, correction=True)
        r = s.propose("appliance", "washer", source="frame", confidence=0.99, now=20)
    assert isinstance(r, Rejection) and r.reason == "locked"
    assert s.get("appliance").value == "dryer" and s.is_locked("appliance")


def test_a_second_correction_can_change_a_locked_slot():
    g, s = store()
    with g.hold():
        s.propose("day", "fri", source="text", confidence=0.9, now=0, correction=True)
        c = s.propose("day", "sat", source="text", confidence=0.9, now=5, correction=True)
    assert isinstance(c, SlotChange) and s.get("day").value == "sat"


def test_correction_to_the_same_value_locks_without_new_version():
    g, s = store()
    with g.hold():
        s.propose("day", "fri", source="text", confidence=0.9, now=0)
        assert s.propose("day", "fri", source="text", confidence=0.9, now=5, correction=True) is None
    assert s.is_locked("day") and s.version("day") == 1


def test_release_locks():
    g, s = store()
    with g.hold():
        s.propose("day", "fri", source="text", confidence=0.9, now=0, correction=True)
        s.release_locks()
        assert isinstance(s.propose("day", "sat", source="frame", confidence=0.9, now=1), SlotChange)


def test_mutation_outside_the_kernel_is_refused():
    _, s = store()
    with pytest.raises(SingleWriterViolation):
        s.propose("x", 1, source="text", confidence=1.0, now=0)


def test_mutation_from_another_thread_is_refused():
    g, s = store()
    errors = []

    def worker():
        try:
            s.propose("x", 1, source="text", confidence=1.0, now=0)
        except SingleWriterViolation as exc:
            errors.append(exc)

    with g.hold():
        t = threading.Thread(target=worker)
        t.start()
        t.join()
    assert len(errors) == 1


proposals = st.lists(
    st.tuples(st.sampled_from(["a", "b"]), st.integers(0, 3), st.booleans(), st.sampled_from(["text", "frame"])),
    max_size=60,
)


@given(proposals)
def test_store_properties(ops):
    g, s = store()
    model: dict[str, tuple[int, bool, int]] = {}  # name -> (value, locked, version)
    with g.hold():
        for t, (name, value, correction, source) in enumerate(ops):
            out = s.propose(name, value, source=source, confidence=0.9, now=t, correction=correction)
            cur = model.get(name)
            if cur and cur[1] and not correction:
                assert isinstance(out, Rejection)
                continue
            locked = (cur[1] if cur else False) or correction
            if cur and cur[0] == value:
                assert out is None
                model[name] = (value, locked, cur[2])
            else:
                assert isinstance(out, SlotChange)
                assert out.new.version == (cur[2] + 1 if cur else 1)
                model[name] = (value, locked, out.new.version)
    for name, (value, locked, version) in model.items():
        assert s.get(name).value == value and s.is_locked(name) == locked and s.version(name) == version
        versions = [v.version for v in s.history(name)]
        assert versions == list(range(1, len(versions) + 1))
