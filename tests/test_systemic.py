from __future__ import annotations

import pytest

from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.harvest.systemic import SystemicFailureGuard


class TestConsecutiveFailures:
    def test_does_not_trip_below_the_threshold(self):
        guard = SystemicFailureGuard(max_consecutive_failures=3)
        for _ in range(2):
            guard.record_attempt(failed=True)
        assert guard.consecutive_failures == 2

    def test_trips_at_the_threshold_ec_8(self):
        guard = SystemicFailureGuard(max_consecutive_failures=3)
        guard.record_attempt(failed=True)
        guard.record_attempt(failed=True)
        with pytest.raises(OrgHarvestError) as exc_info:
            guard.record_attempt(failed=True)
        assert exc_info.value.kind is ErrorKind.SYSTEMIC_FAILURE
        assert "3 consecutive" in str(exc_info.value)

    def test_a_success_resets_the_consecutive_count(self):
        guard = SystemicFailureGuard(max_consecutive_failures=3)
        guard.record_attempt(failed=True)
        guard.record_attempt(failed=True)
        guard.record_attempt(failed=False)
        assert guard.consecutive_failures == 0
        # Two more failures after the reset still isn't three in a row.
        guard.record_attempt(failed=True)
        guard.record_attempt(failed=True)


class TestFailureRate:
    def test_a_high_rate_with_too_few_attempts_does_not_trip(self):
        guard = SystemicFailureGuard(
            max_consecutive_failures=100, max_failure_rate=0.5, min_attempts_for_rate=10
        )
        # 2 of 3 failed (66%) but fewer than min_attempts_for_rate attempts.
        guard.record_attempt(failed=True)
        guard.record_attempt(failed=False)
        guard.record_attempt(failed=True)

    def test_trips_once_the_rate_and_sample_size_are_both_met(self):
        guard = SystemicFailureGuard(
            max_consecutive_failures=100, max_failure_rate=0.5, min_attempts_for_rate=4
        )
        guard.record_attempt(failed=True)
        guard.record_attempt(failed=False)
        guard.record_attempt(failed=True)
        with pytest.raises(OrgHarvestError) as exc_info:
            guard.record_attempt(failed=False)
        assert exc_info.value.kind is ErrorKind.SYSTEMIC_FAILURE
        assert "2 of 4" in str(exc_info.value)

    def test_a_low_rate_never_trips_regardless_of_sample_size(self):
        guard = SystemicFailureGuard(
            max_consecutive_failures=100, max_failure_rate=0.5, min_attempts_for_rate=4
        )
        for _ in range(20):
            guard.record_attempt(failed=False)
        guard.record_attempt(failed=True)  # 1 of 21, well under 50%
