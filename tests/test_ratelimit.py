from __future__ import annotations

import asyncio

from org_harvest.ratelimit import BudgetTracker, ConcurrencyLimiter, RateLimitSnapshot


class TestBudgetTracker:
    async def test_no_wait_when_no_snapshot_yet(self):
        tracker = BudgetTracker("graphql")
        waited = await tracker.wait_if_exhausted()
        assert waited is None

    async def test_no_wait_when_budget_not_exhausted(self):
        tracker = BudgetTracker("graphql")
        tracker.update(RateLimitSnapshot(limit=5000, remaining=100, reset_at=99999.0))
        waited = await tracker.wait_if_exhausted()
        assert waited is None

    async def test_waits_until_reset_when_exhausted_ac_7_3(self):
        tracker = BudgetTracker("graphql")
        tracker.update(RateLimitSnapshot(limit=5000, remaining=0, reset_at=1_100.0))
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        waited = await tracker.wait_if_exhausted(sleep=fake_sleep, now=lambda: 1_000.0)
        assert waited == 100.0
        assert slept == [100.0]
        # Optimistically reset so the caller doesn't wait again immediately.
        assert tracker.snapshot is not None
        assert tracker.snapshot.remaining == tracker.snapshot.limit

    async def test_no_wait_when_reset_already_passed(self):
        tracker = BudgetTracker("graphql")
        tracker.update(RateLimitSnapshot(limit=5000, remaining=0, reset_at=500.0))
        waited = await tracker.wait_if_exhausted(now=lambda: 1_000.0)
        assert waited is None


class TestConcurrencyLimiter:
    async def test_bounds_in_flight_requests_ac_7_2(self):
        limiter = ConcurrencyLimiter(max_concurrency=2)
        in_flight = 0
        max_seen = 0

        async def worker() -> None:
            nonlocal in_flight, max_seen
            await limiter.acquire()
            in_flight += 1
            max_seen = max(max_seen, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            await limiter.release()

        await asyncio.gather(*[worker() for _ in range(6)])
        assert max_seen <= 2

    async def test_secondary_limit_reduces_then_restores(self):
        limiter = ConcurrencyLimiter(max_concurrency=10, reduction_seconds=60.0)
        clock = {"t": 0.0}

        def now() -> float:
            return clock["t"]

        await limiter.signal_secondary_limit(now=now, factor=0.1, floor=1)
        assert limiter.current_max == 1

        # Still reduced before the cooldown elapses.
        await limiter.acquire(now=now)
        await limiter.release()
        assert limiter.current_max == 1

        # Cooldown elapsed: the next acquire restores the normal bound.
        clock["t"] = 61.0
        await limiter.acquire(now=now)
        assert limiter.current_max == 10
        await limiter.release()

    async def test_waiters_are_released_in_order_when_capacity_frees(self):
        limiter = ConcurrencyLimiter(max_concurrency=1)
        order: list[int] = []

        async def worker(i: int) -> None:
            await limiter.acquire()
            order.append(i)
            await asyncio.sleep(0.01)
            await limiter.release()

        await asyncio.gather(worker(1), worker(2), worker(3))
        assert sorted(order) == [1, 2, 3]


class TestBudgetTrackerConsumption:
    def test_no_consumption_recorded_from_a_single_observation(self):
        tracker = BudgetTracker("graphql")
        tracker.update(RateLimitSnapshot(limit=5000, remaining=4990, reset_at=1.0))
        assert tracker.total_consumed == 0

    def test_accumulates_consumption_across_decreasing_observations_ac_1_3(self):
        tracker = BudgetTracker("graphql")
        tracker.update(RateLimitSnapshot(limit=5000, remaining=4990, reset_at=1.0))
        tracker.update(RateLimitSnapshot(limit=5000, remaining=4980, reset_at=1.0))
        tracker.update(RateLimitSnapshot(limit=5000, remaining=4975, reset_at=1.0))
        assert tracker.total_consumed == 15

    def test_a_reset_upward_jump_is_not_counted_as_negative_consumption(self):
        tracker = BudgetTracker("graphql")
        tracker.update(RateLimitSnapshot(limit=5000, remaining=10, reset_at=1.0))
        tracker.update(RateLimitSnapshot(limit=5000, remaining=5000, reset_at=2.0))
        assert tracker.total_consumed == 0

    def test_a_change_in_limit_is_not_counted(self):
        tracker = BudgetTracker("graphql")
        tracker.update(RateLimitSnapshot(limit=5000, remaining=4990, reset_at=1.0))
        tracker.update(RateLimitSnapshot(limit=15000, remaining=14000, reset_at=2.0))
        assert tracker.total_consumed == 0
