"""Live rate-limit tracking and adaptive concurrency (US-7).

`BudgetTracker` records the remaining-budget figures GitHub actually reports
on a response — never an assumed constant (AC-7.1) — and knows whether the
tracked budget is exhausted and, if so, how long until it resets (AC-7.3).

`ConcurrencyLimiter` bounds how many requests are in flight at once and can
temporarily shrink that bound when a secondary rate limit is signalled,
restoring it automatically after a cooldown (AC-7.2).

Both accept injectable `now`/`sleep` callables so tests can exercise waiting
behavior without any real delay (AC-10.4).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

Sleep = Callable[[float], Awaitable[None]]
Now = Callable[[], float]


@dataclass(frozen=True)
class RateLimitSnapshot:
    """The budget figures as of the most recent response that reported them."""

    limit: int
    remaining: int
    reset_at: float  # unix epoch seconds


class BudgetTracker:
    """Tracks one named rate-limit budget (e.g. "graphql" points/hour, or
    "rest" requests/hour)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._snapshot: RateLimitSnapshot | None = None

    def update(self, snapshot: RateLimitSnapshot) -> None:
        self._snapshot = snapshot

    @property
    def snapshot(self) -> RateLimitSnapshot | None:
        return self._snapshot

    async def wait_if_exhausted(
        self,
        *,
        min_remaining: int = 1,
        sleep: Sleep = asyncio.sleep,
        now: Now = time.time,
    ) -> float | None:
        """If the tracked budget is at or under `min_remaining`, sleep until
        the advertised reset (AC-7.3). Returns seconds waited, or `None` if
        no wait was needed."""
        snap = self._snapshot
        if snap is None or snap.remaining > min_remaining:
            return None
        wait_for = snap.reset_at - now()
        if wait_for <= 0:
            return None
        await sleep(wait_for)
        # Optimistic reset; the next real response corrects this.
        self._snapshot = RateLimitSnapshot(
            limit=snap.limit, remaining=snap.limit, reset_at=now() + 3600.0
        )
        return wait_for


class ConcurrencyLimiter:
    """Bounds in-flight requests, with a temporary reduction when a secondary
    rate limit is signalled (AC-7.2)."""

    def __init__(self, max_concurrency: int, *, reduction_seconds: float = 60.0) -> None:
        self._normal_max = max_concurrency
        self._current_max = max_concurrency
        self._reduced_until: float | None = None
        self._reduction_seconds = reduction_seconds
        self._in_flight = 0
        self._condition = asyncio.Condition()

    def _maybe_restore(self, now_value: float) -> None:
        if self._reduced_until is not None and now_value >= self._reduced_until:
            self._current_max = self._normal_max
            self._reduced_until = None

    async def acquire(self, *, now: Now = time.time) -> None:
        async with self._condition:
            self._maybe_restore(now())
            while self._in_flight >= self._current_max:
                await self._condition.wait()
                self._maybe_restore(now())
            self._in_flight += 1

    async def release(self) -> None:
        async with self._condition:
            self._in_flight -= 1
            self._condition.notify_all()

    async def signal_secondary_limit(
        self, *, now: Now = time.time, factor: float = 0.5, floor: int = 1
    ) -> None:
        async with self._condition:
            self._current_max = max(floor, int(self._current_max * factor))
            self._reduced_until = now() + self._reduction_seconds
            self._condition.notify_all()

    @property
    def current_max(self) -> int:
        return self._current_max
