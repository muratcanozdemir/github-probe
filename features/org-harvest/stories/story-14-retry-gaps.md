# Story 14: Retry only what failed

**Status:** APPROVED
**Depends On:** 9
**UI Changes:** No

## User Story

As an auditor who has granted a missing permission or waited out a transient failure, I can retry only the gapped resources of an existing snapshot so that I don't spend a whole budget window re-downloading what already succeeded.

## Acceptance Criteria

- AC-11.1: The user can re-attempt only the gapped resources of an existing completed snapshot.
- AC-11.2: Resources that now succeed are written into that snapshot and their gaps cleared; those that fail again keep their gaps, updated with the new reason and timestamp.
- AC-11.3: The manifest records that a retry occurred and when, and the snapshot's completion status is recalculated.
- AC-11.4: Retry-gaps re-runs finalization so Parquet output and counts stay consistent with the NDJSON.
- AC-11.5: Retry-gaps against a snapshot with no gaps is a reported no-op, not an error.

## Scope

**Included:**
- Building a resource-id filter from an existing snapshot's recorded gaps and driving the same fetch mechanism from Stories 5–7, scoped to that filter, rather than a separate implementation.
- Writing newly successful results into the existing snapshot and clearing their gaps; updating (not discarding) gaps that recur, with a fresh reason and timestamp.
- Recording the retry — that it happened, and when — in the manifest, and recalculating the snapshot's completion status.
- Re-running finalization (Story 8/9) afterward so Parquet and counts reflect the updated NDJSON.
- Treating a snapshot with no gaps as a reported no-op rather than an error.

**Excluded:**
- Any change to how gaps are recorded during the original run — this story reads gaps that already exist and re-attempts their resources.
- Retrying a snapshot that isn't complete (has no manifest) — per Story 9's rule, an unfinished snapshot is resumed (Story 12), not retry-gapped.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 9's acceptance criteria are met: a completed snapshot has a manifest recording its gaps and completion status, and finalization can be re-run standalone.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.
