"""Clocks for the kernel.

The guide's harness is a "virtual clock streaming harness" with
"deterministic event replay" (original guide v1.0.0, section 4). Keel needs the same property
for its own tests and fuzzer: given the same inputs, the same timers fire in
the same order at the same virtual times.

VirtualClock is a discrete-event scheduler. Time only moves when the driver
calls advance_to()/run_next(); callbacks run synchronously in (deadline,
insertion order), so there is no dependence on asyncio scheduling or wall
time. MonotonicClock implements the same interface on a live asyncio loop.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

Callback = Callable[[], None]


class TimerHandle:
    __slots__ = ("deadline_ms", "seq", "callback", "cancelled", "_native")

    def __init__(self, deadline_ms: int, seq: int, callback: Callback) -> None:
        self.deadline_ms = deadline_ms
        self.seq = seq
        self.callback = callback
        self.cancelled = False
        self._native: Optional[asyncio.TimerHandle] = None

    def cancel(self) -> None:
        self.cancelled = True
        if self._native is not None:
            self._native.cancel()

    def __lt__(self, other: "TimerHandle") -> bool:
        return (self.deadline_ms, self.seq) < (other.deadline_ms, other.seq)


class Clock(ABC):
    @abstractmethod
    def now_ms(self) -> int: ...

    @abstractmethod
    def call_at(self, deadline_ms: int, callback: Callback) -> TimerHandle: ...

    def call_later(self, delay_ms: int, callback: Callback) -> TimerHandle:
        if delay_ms < 0:
            raise ValueError(f"negative delay: {delay_ms}")
        return self.call_at(self.now_ms() + delay_ms, callback)


class VirtualClock(Clock):
    def __init__(self, start_ms: int = 0) -> None:
        self._now = start_ms
        self._heap: list[TimerHandle] = []
        self._seq = itertools.count()

    def now_ms(self) -> int:
        return self._now

    def call_at(self, deadline_ms: int, callback: Callback) -> TimerHandle:
        if deadline_ms < self._now:
            raise ValueError(f"deadline {deadline_ms} is before now {self._now}")
        handle = TimerHandle(deadline_ms, next(self._seq), callback)
        heapq.heappush(self._heap, handle)
        return handle

    def _drop_cancelled(self) -> None:
        while self._heap and self._heap[0].cancelled:
            heapq.heappop(self._heap)

    def next_deadline(self) -> Optional[int]:
        self._drop_cancelled()
        return self._heap[0].deadline_ms if self._heap else None

    def pending(self) -> int:
        return sum(1 for h in self._heap if not h.cancelled)

    def run_next(self) -> bool:
        """Fire the earliest live timer. Returns False if none remain."""
        self._drop_cancelled()
        if not self._heap:
            return False
        handle = heapq.heappop(self._heap)
        self._now = handle.deadline_ms
        handle.callback()
        return True

    def advance_to(self, t_ms: int) -> None:
        """Fire every timer due at or before t_ms, then set now to t_ms.

        Timers scheduled by callbacks during the advance also fire if they
        fall inside the window.
        """
        if t_ms < self._now:
            raise ValueError(f"cannot move time backwards: {t_ms} < {self._now}")
        while True:
            nxt = self.next_deadline()
            if nxt is None or nxt > t_ms:
                break
            self.run_next()
        self._now = t_ms

    def run_until_idle(self) -> None:
        while self.run_next():
            pass


class MonotonicClock(Clock):
    """Wall-clock implementation for live runs, bound to an asyncio loop.

    Time is read from perf_counter_ns, not loop.time(): on Windows the
    asyncio clock ticks in 15.625 ms steps and the loop may fire a timer up
    to one step early (measured on the dev machine; see test_clock.py). A
    timer that wakes early is re-armed, so a callback never observes
    now_ms() < its deadline.
    """

    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._loop = loop or asyncio.get_running_loop()
        self._t0_ns = time.perf_counter_ns()
        self._seq = itertools.count()

    def now_ms(self) -> int:
        return (time.perf_counter_ns() - self._t0_ns) // 1_000_000

    def call_at(self, deadline_ms: int, callback: Callback) -> TimerHandle:
        handle = TimerHandle(deadline_ms, next(self._seq), callback)

        def fire() -> None:
            if handle.cancelled:
                return
            remaining = deadline_ms - self.now_ms()
            if remaining > 0:
                handle._native = self._loop.call_later(remaining / 1000, fire)
                return
            callback()

        handle._native = self._loop.call_later(max(0, deadline_ms - self.now_ms()) / 1000, fire)
        return handle
