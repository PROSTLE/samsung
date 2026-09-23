"""Single-writer enforcement.

All session state is owned by Kernel.handle(). Stores call guard.check() before
every mutation; it raises unless the caller is inside handle() on the thread
that entered it. This turns "only the event loop mutates state" from a
convention into a checked invariant.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional


class SingleWriterViolation(RuntimeError):
    pass


class WriteGuard:
    def __init__(self) -> None:
        self._holder: Optional[int] = None

    @contextmanager
    def hold(self) -> Iterator[None]:
        if self._holder is not None:
            raise SingleWriterViolation("kernel.handle() re-entered; events must be posted, not handled inline")
        self._holder = threading.get_ident()
        try:
            yield
        finally:
            self._holder = None

    def check(self) -> None:
        if self._holder is None:
            raise SingleWriterViolation("session state mutated outside kernel.handle()")
        if self._holder != threading.get_ident():
            raise SingleWriterViolation("session state mutated from a thread other than the kernel's")
