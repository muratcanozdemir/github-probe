# Story 7: Keep a run going through failures

**Status:** APPROVED
**Depends On:** 6
**UI Changes:** No

## User Story

As an auditor relying on this data, I can trust that one failed request never silently derails the rest of a run, and that a genuine outage stops the run cleanly instead of quietly producing a snapshot that is almost entirely gaps.

## Acceptance Criteria

- AC-5.3: A request that yields no usable response after its retries are exhausted is recorded as a gap; the run continues.
- AC-5.5: A run that hits gaps still attempts every other dataset.
- FR-5 (systemic failure): A failure rate or consecutive-failure count above a configurable threshold is treated as systemic — the run stops cleanly with resumable state, and does not finalize, rather than accumulating gaps against an outage.
- EC-8: When the API is down partway through a large crawl, the systemic-failure threshold trips and the run stops cleanly with resumable state, rather than recording thousands of gaps and finalizing a near-empty snapshot as complete.

## Scope

**Included:**
- Recording a gap when a request exhausts its retries with no usable response at all (as distinct from Story 6's "response arrived but contained errors" case).
- Continuing to every other dataset after a gap, rather than stopping the run.
- A configurable failure-rate or consecutive-failure threshold that, once crossed, stops the run cleanly with resumable state and skips finalization — distinguishing "an outage happened" from "some things are gapped."

**Excluded:**
- Recording the gap itself when a response does arrive with errors (Story 6, and Story 5 for org-level data) — this story only adds the no-response and systemic-outage cases on top of that existing mechanism.
- What "resumable state" concretely means for restarting after a systemic stop (Story 12) — this story is responsible for stopping cleanly and leaving that state behind, not for the resume mechanics themselves.
- Exit-status reporting for a gapped run (Story 10) — this story ensures gaps are recorded and the run keeps going; how that's reported to the caller is Story 10's concern.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 6's acceptance criteria are met: repository-level datasets fetch across the org with per-repository gap attribution for responses that do arrive.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/errors.py` — add `ErrorKind.SYSTEMIC_FAILURE` for a consecutive-failure or failure-rate threshold crossed (FR-5, EC-8).
2. `src/org_harvest/harvest/systemic.py` — new: `SystemicFailureGuard`, tracking `total_attempts`/`total_failures`/`consecutive_failures` across however many `record_attempt(failed=...)` calls the caller reports. Raises `OrgHarvestError(kind=SYSTEMIC_FAILURE)` the moment either `max_consecutive_failures` or (once `min_attempts_for_rate` attempts have happened) `max_failure_rate` is crossed. A GraphQL partial error (data arrived, `errors` populated) is never reported here — only a request that exhausted Transport's own retries with no usable response at all (AC-5.3) counts, matching this story's Scope explicitly excluding the "response arrived with errors" case from systemic accounting.
3. `src/org_harvest/harvest/org_level.py` and `src/org_harvest/harvest/repo_level.py` — modify: both harvesters take a required `systemic_guard: SystemicFailureGuard` and call `record_attempt()` from within their shared `_query()` method — the one place both already distinguish a transport-level failure from a real response (DEV-6). `fetch_organization_directory()`/`fetch_repository_datasets()` gain an optional `systemic_guard` parameter, defaulting to a fresh guard scoped to that call when the caller passes none, so a shared guard can span both phases (Story 10's concern) or each phase can get its own.
4. `src/org_harvest/__init__.py` — re-export `SystemicFailureGuard`.

### Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `src/org_harvest/errors.py` | Modify | Add `SYSTEMIC_FAILURE` |
| `src/org_harvest/harvest/systemic.py` | Create | The threshold guard (FR-5, EC-8) |
| `src/org_harvest/harvest/org_level.py` | Modify | Wire guard into Phase 1's `_query()` (DEV-6) |
| `src/org_harvest/harvest/repo_level.py` | Modify | Wire guard into Phase 2's `_query()` (DEV-6) |
| `src/org_harvest/__init__.py` | Modify | Re-export `SystemicFailureGuard` |
| `tests/test_systemic.py` | Create | Guard behavior in isolation |
| `tests/test_org_level.py` | Modify | Phase 1 integration: outage stops the run, partial errors don't count |
| `tests/test_repo_level.py` | Modify | Phase 2 integration: outage stops the run, per-request (not per-repo) counting |

### Cross-Module Seams

- **`SystemicFailureGuard` is written to by both `org_level._query()` and `repo_level._query()`, and read by nothing yet within this story** — it only raises. The seam this story sets up but doesn't close is Story 10's: a shared guard instance passed to both `fetch_organization_directory()` and `fetch_repository_datasets()` so an outage spanning the Phase 1→Phase 2 boundary is still detected as one systemic condition rather than two independent half-thresholds. `TestSystemicFailure::test_a_shared_guard_can_span_multiple_calls` in `tests/test_org_level.py` exercises passing an external guard in, proving the seam works, though the actual cross-phase sharing is Story 10's to wire up.

### Testing Approach

- **Unit — `tests/test_systemic.py`:** consecutive-failure counting resets on any success and trips exactly at the configured threshold; the failure-rate check is inert below `min_attempts_for_rate` even at a high rate, and trips once both the rate and sample size are met; a sustained low rate never trips regardless of how many attempts accumulate.
- **Integration — `tests/test_org_level.py` (`TestSystemicFailure`):** a simulated total outage (every request returns 503, retries exhausted) raises `SYSTEMIC_FAILURE` rather than completing with eleven gapped datasets (EC-8); an externally-supplied guard can be shared across calls; a GraphQL partial error on one dataset does not push the guard's consecutive-failure counter at all, proven by setting `max_consecutive_failures=1` and confirming the run still completes (with a gap, not a systemic-failure exception).
- **Integration — `tests/test_repo_level.py` (`TestSystemicFailure`):** the same outage scenario across repository-level requests; a dedicated test proves the guard counts *requests*, not repositories inside a batch — a `batch_width=5` outage trips a `max_consecutive_failures=5` guard after exactly 5 requests, not 25, which would be the case if failures were (incorrectly) attributed per-repository instead of per-request.

### Risks

- None specific to this story — it is purely additive on top of Stories 5/6's already-tested request/gap machinery, and the one behavioral question (does a batch's per-repo gaps inflate the systemic count) is directly tested rather than assumed.

### Decisions Made

- **The guard counts *requests*, not resources inside a response.** A single invalidated alias-batch of ten repositories is one failed attempt, not ten — matching "a request that yields no usable response" (AC-5.3) literally, and avoiding a batch-width-dependent false trip (a wide batch shouldn't look ten times "more broken" than a narrow one hitting the same transport failure).
- **Two independent thresholds (consecutive count and overall rate), both must be given a chance to trip, neither alone is sufficient** — a hard outage trips the consecutive counter quickly; a flaky-but-not-dead API (failing every other request, forever) would never string together enough consecutive failures but is exactly the sustained-degradation case the rate threshold catches. `min_attempts_for_rate` exists so the rate check doesn't fire off a tiny, statistically meaningless sample (e.g. 1 failure out of 2 attempts).
- See DEV-6 in `stories/deviations.md` for the internal harvester constructor change.
