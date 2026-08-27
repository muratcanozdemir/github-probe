# Story 12: Resume an interrupted run automatically

**Status:** APPROVED
**Depends On:** 6
**UI Changes:** No

## User Story

As an engineer downloading a large organization, I can re-run the same command after an interruption and have it pick up where it stopped so that hours of work and a spent point budget are not lost to one failure.

## Acceptance Criteria

- AC-4.2: Re-running the same command resumes the newest incomplete snapshot for that org in that root, and reports which snapshot it is resuming and from where.
- AC-4.3: The user can name a specific snapshot to resume instead of taking the newest.
- AC-4.4: When no incomplete snapshot exists, a re-run starts a new snapshot rather than modifying the completed one.
- AC-4.5: Resumed runs do not re-fetch pages already written and do not duplicate records.
- AC-4.6: A run terminated abruptly is still resumable, losing at most the in-flight page; a partially written trailing record is discarded rather than corrupting the file.
- AC-4.7: The user can force a fresh run that ignores existing incomplete snapshots.

## Scope

**Included:**
- Discovering and resuming the newest incomplete snapshot for an org automatically, or a user-named specific snapshot.
- Starting a fresh snapshot, rather than touching a completed one, when no incomplete snapshot exists.
- Continuing from Story 5/6's checkpoint cursors without re-fetching completed pages or duplicating records.
- Surviving abrupt termination (process kill, crash) with at most the in-flight page lost, and discarding any partially written trailing NDJSON record on resume.
- A forced-fresh option that ignores any existing incomplete snapshot.

**Excluded:**
- Refusing to resume a snapshot whose selection, repository filter, org, checkpoint integrity, or age make resuming unsafe (Story 13) — this story implements the happy-path resume; the guards that decline an unsafe resume are separate.
- Graceful handling of a live user interrupt (Ctrl-C) while a run is in progress (Story 13) — this story covers resuming *after* a prior run has already stopped, not the interrupt moment itself.
- Concurrent-run claim/lock behavior (Story 13) — a second process racing to resume the same snapshot is a distinct concern from the single-process resume mechanics here.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 6's acceptance criteria are met: both organization-level and repository-level fetches persist per-collection checkpoint cursors as they run.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.
