"""Cooperative Ctrl-C handling for a run (Story 13, AC-4.11).

`InterruptGuard` is a context manager that installs a SIGINT handler for
its lifetime. The **first** interrupt sets `requested` without raising —
so a caller in the middle of a paginated fetch can finish writing and
checkpointing its current page, then notice `requested` at the next safe
boundary and stop cleanly, rather than being torn out of an arbitrary
`await` by a raw `KeyboardInterrupt` at an unpredictable point. A
**second** interrupt restores Python's default SIGINT behavior and raises
`KeyboardInterrupt` immediately (AC-4.11: "a second interrupt stops
immediately"), in case cooperative shutdown isn't happening fast enough.

`signal.signal()` only works from the main thread — installing a handler
from any other thread raises `ValueError`. Rather than let that surface as
a crash to a future library caller (Story 15) that drives `run_snapshot()`
from a worker thread, `InterruptGuard` degrades to a no-op in that case:
`requested` simply never becomes `True`, so the guarded code behaves
exactly as it would with no interrupt support at all.
"""

from __future__ import annotations

import signal
from types import FrameType
from typing import Any


class InterruptGuard:
    def __init__(self) -> None:
        self.requested = False
        self._previous_handler: Any = None
        self._installed = False

    def __enter__(self) -> InterruptGuard:
        try:
            self._previous_handler = signal.signal(signal.SIGINT, self._handle_sigint)
            self._installed = True
        except ValueError:
            self._installed = False
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._installed:
            signal.signal(signal.SIGINT, self._previous_handler)
            self._installed = False

    def _handle_sigint(self, signum: int, frame: FrameType | None) -> None:
        if self.requested:
            # Second interrupt: stop being cooperative and raise right here,
            # right now — restoring the previous handler first so a third
            # signal (or this one, if something catches and re-raises) falls
            # through to ordinary default behavior rather than looping.
            if self._installed:
                signal.signal(signal.SIGINT, self._previous_handler)
                self._installed = False
            raise KeyboardInterrupt
        self.requested = True
