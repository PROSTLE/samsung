"""Shared builders. Names and values here are generic test data, not scenarios."""

from __future__ import annotations

import pytest

from keel.config import load_config, with_overrides
from keel.kernel.internal import Interpretation, SlotProposal
from keel.kernel.plan import Goal, SlotRef, Step


@pytest.fixture
def cfg():
    return with_overrides(load_config(), trace={"record_wall_time": False})


def text(eid, ts, words="…", eot=True):
    return {"type": "text", "event_id": eid, "session_id": "sim", "ts_ms": ts, "text": words, "end_of_turn": eot}


def frame(eid, ts):
    return {"type": "frame", "event_id": eid, "session_id": "sim", "ts_ms": ts, "media": {"uri": f"{eid}.png"}}


def interrupt(eid, ts):
    return {"type": "interrupt", "event_id": eid, "session_id": "sim", "ts_ms": ts}


def tool(name, read_only, **extra):
    return {"name": name, "parameters": {"type": "object"}, "annotations": {"readOnlyHint": read_only, **extra}}


def manifest(ts=0, *tools):
    return {"type": "manifest", "event_id": "m0", "session_id": "sim", "ts_ms": ts, "tools": list(tools)}


def prop(name, value, source="text", conf=0.95, correction=False):
    return SlotProposal(name=name, value=value, source=source, confidence=conf, correction=correction)


def interp(*proposals, goal=None, **kw):
    return Interpretation(proposals=tuple(proposals), goal=goal, **kw)


def step(step_id, tool_name, **slot_args):
    return Step(step_id=step_id, tool=tool_name, bindings={a: SlotRef(slot=s) for a, s in slot_args.items()})


def goal(intent, *steps, reply=None):
    return Goal(intent=intent, steps=tuple(steps), reply=reply)
