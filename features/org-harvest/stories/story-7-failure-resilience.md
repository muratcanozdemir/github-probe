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
