"""Systemic-failure detection (FR-5, EC-8): distinguishes "an outage
happened" from "some things are gapped."

Every request that exhausts its retries with no usable response at all
(AC-5.3's case, from Story 5/6) is reported here. A GraphQL partial error on
an otherwise-successful response — data arrived, one field errored — is
*not* reported: that is exactly the "gap, not outage" case AC-5.1 exists
for, and Story 6's Scope explicitly excludes it from counting toward this
guard. Once either threshold trips, `SystemicFailureGuard` raises rather
than returning a verdict, so the same "let it propagate" pattern org_level
and repo_level already use for auth and rate-limit-wait failures applies
here too — the run stops wherever it is, without finalizing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from org_harvest.errors import ErrorKind, OrgHarvestError

#: Ten in a row with no usable response at all is treated as an outage
#: rather than eleven separate unlucky gaps.
_DEFAULT_MAX_CONSECUTIVE_FAILURES = 10
#: Once there have been enough attempts to be meaningful, a sustained 50%+
#: failure rate is also treated as systemic even if it never strings
#: together `max_consecutive_failures` in a row (e.g. every other request
#: failing, forever).
_DEFAULT_MAX_FAILURE_RATE = 0.5
_DEFAULT_MIN_ATTEMPTS_FOR_RATE = 10


@dataclass
class SystemicFailureGuard:
    """Tracks no-usable-response failures across however many requests the
    caller reports to it — one guard can span both harvest phases, or the
    caller can use one per phase, whichever fits how it wants "systemic"
    scoped (Story 10 decides which)."""

    max_consecutive_failures: int = _DEFAULT_MAX_CONSECUTIVE_FAILURES
    max_failure_rate: float = _DEFAULT_MAX_FAILURE_RATE
    min_attempts_for_rate: int = _DEFAULT_MIN_ATTEMPTS_FOR_RATE
    consecutive_failures: int = field(default=0, init=False)
    total_attempts: int = field(default=0, init=False)
    total_failures: int = field(default=0, init=False)

    def record_attempt(self, *, failed: bool) -> None:
        """Called once per request that either produced a usable response
        (`failed=False`) or exhausted its retries with none (`failed=True`
        — AC-5.3). Raises `OrgHarvestError(kind=SYSTEMIC_FAILURE)` the
        moment either threshold is crossed."""
        self.total_attempts += 1
        if failed:
            self.total_failures += 1
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 0
        self._check()

    def _check(self) -> None:
        if self.consecutive_failures >= self.max_consecutive_failures:
            raise OrgHarvestError(
                f"{self.consecutive_failures} consecutive requests failed with no "
                "usable response — treating this as a systemic outage rather than "
                "accumulating gaps against it. Stopping cleanly; the run is resumable.",
                kind=ErrorKind.SYSTEMIC_FAILURE,
            )
        if self.total_attempts >= self.min_attempts_for_rate:
            rate = self.total_failures / self.total_attempts
            if rate >= self.max_failure_rate:
                raise OrgHarvestError(
                    f"{self.total_failures} of {self.total_attempts} requests "
                    f"({rate:.0%}) failed with no usable response — treating this as a "
                    "systemic outage rather than accumulating gaps against it. Stopping "
                    "cleanly; the run is resumable.",
                    kind=ErrorKind.SYSTEMIC_FAILURE,
                )
