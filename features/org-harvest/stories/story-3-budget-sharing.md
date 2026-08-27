# Story 3: Avoid wasted waits and share budget with other consumers

**Status:** APPROVED
**Depends On:** 2
**UI Changes:** No

## User Story

As an operator sharing an App installation with other tools, I can bound how long the tool waits and how much of the shared budget it consumes so that a long run never wastes time waiting into a certain failure and never starves other consumers of the installation.

## Acceptance Criteria

- AC-7.4: It does not begin a wait that would outlast the remaining credential lifetime when it cannot refresh the credential; it stops cleanly and resumably instead.
- AC-7.5: Total waiting is bounded by a user-configurable ceiling, after which the run stops cleanly and resumably.
- AC-7.7: The user can reserve budget for other consumers by capping this tool's consumption; the cap's meaning is a floor on remaining points that the tool will not cross.
- AC-7.9: REST requests are paced against their own separate budget and reported separately.

## Scope

**Included:**
- Comparing a prospective rate-limit wait against the active credential's remaining lifetime (from Story 1's credential provider) before waiting, and stopping cleanly and resumably instead of waiting into an unrecoverable expiry.
- A configurable ceiling on total accumulated waiting across a run, after which the run stops cleanly and resumably.
- A user-supplied consumption floor that the tool will not cross, reserving headroom for other consumers of the same installation.
- Independent budget tracking and reporting for REST requests, separate from GraphQL point consumption.

**Excluded:**
- The base pacing, retry, and secondary-limit behavior this story refines (Story 2).
- Reporting these consumption figures in a run's final summary or manifest — that reporting is Story 10 (single-command run) and Story 9 (manifest), which consume the statistics this story produces.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 2's acceptance criteria are met: requests self-pace against live budget figures, respect secondary limits, and retry transient failures with backoff.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/errors.py` — add `ErrorKind.RATE_LIMIT_WAIT_EXCEEDED` for a wait refused by either safety check.
2. `src/org_harvest/ratelimit.py` — add a `before_wait` hook to `BudgetTracker.wait_if_exhausted()`, called with the computed wait duration immediately before the actual sleep, able to raise and prevent it. Backward compatible: defaults to `None`, Story 2's existing tests are unaffected.
3. `src/org_harvest/transport.py` — `Transport` now owns two named budgets (`graphql_budget`, `rest_budget`) and two convenience methods (`send_graphql`, `send_rest`) pacing against them separately (AC-7.9); a `reserve_floor` constructor param is threaded through as `min_remaining` (AC-7.7); a `_check_wait_is_safe` method wired as the new `before_wait` hook enforces AC-7.4 (credential-lifetime-aware) and AC-7.5 (configurable total-wait ceiling), accumulating `total_wait_seconds` for later reporting.
4. `src/org_harvest/credentials.py` — extend `StaticTokenCredentialProvider` with an optional `expires_at` parameter so AC-7.4's check has real data to act on for the one credential type it targets (see DEV-1).

### Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `src/org_harvest/errors.py` | Modify | Add `RATE_LIMIT_WAIT_EXCEEDED` |
| `src/org_harvest/ratelimit.py` | Modify | `before_wait` hook on `wait_if_exhausted` |
| `src/org_harvest/transport.py` | Modify | Separate budgets + convenience methods, reserve floor, wait-safety check, total-wait accounting |
| `src/org_harvest/credentials.py` | Modify | `StaticTokenCredentialProvider(expires_at=...)` (DEV-1) |
| `tests/test_credentials.py` | Modify | Cover the new optional expiry |
| `tests/test_transport.py` | Modify | Cover reserve floor, both wait-safety checks, and separate-budget behavior |

### Cross-Module Seams

`Transport._check_wait_is_safe` reads `credentials.can_refresh()` and `credentials.seconds_until_expiry()` (Story 1) to decide whether a wait is safe — the same seam Story 2 established for `raise_on_unauthorized`, now exercised a second way. Confirmed by `TestWaitSafety::test_app_key_provider_never_blocked_by_lifetime_check`, which runs a *real* `AppKeyCredentialProvider` discover-and-mint flow through a `Transport` facing a very long rate-limit wait, proving the two Story 1 credential forms produce genuinely different Story 3 behavior rather than each being tested in isolation.

### Testing Approach

- **Unit — `tests/test_credentials.py`:** `StaticTokenCredentialProvider(expires_at=...)` reports a decreasing real-time-based remaining lifetime; omitting it preserves Story 1's "unknown" behavior exactly.
- **Unit — `tests/test_transport.py`:** a reserve floor above zero triggers a wait while raw `remaining` is still positive (AC-7.7); a wait that would outlast a non-refreshable token's *known* expiry raises before sleeping, one within it proceeds, and an `AppKeyCredentialProvider` is never blocked by this check regardless of wait length (AC-7.4 — all three cases matter, not just the positive case); a wait exceeding the configured total-wait ceiling raises without sleeping, and wait time accumulates correctly across multiple waits on one `Transport` (AC-7.5); `send_graphql`/`send_rest` update independent `graphql_budget`/`rest_budget` trackers and send the right header/content-type shape for each (AC-7.9).

### Risks

- **`StaticTokenCredentialProvider.seconds_until_expiry()` uses real wall-clock time, not an injectable clock** — a deliberate choice (a credential's real expiry shouldn't be spoofable the way a rate-limit clock is), but it means any future test involving both a fake `Transport` clock and a static token's expiry must anchor the expiry to real `time.time()`, not the fake clock's epoch. Documented as a comment in the affected tests after this exact mistake was caught and fixed here (both `TestWaitSafety` tests originally used the fake epoch and either failed outright or passed for the wrong reason).

### Decisions Made

- See DEV-1 in `stories/deviations.md` for the `StaticTokenCredentialProvider(expires_at=...)` addition and why it was necessary for AC-7.4 to be more than structurally present.
- **`_check_wait_is_safe` also performs the total-wait accounting** (`self._total_wait_seconds += wait_for`) rather than a separate step — it's the single point already guaranteed to run exactly once per actual wait, before the wait is committed to.
