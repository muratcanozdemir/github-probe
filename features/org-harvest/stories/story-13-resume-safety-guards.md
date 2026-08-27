# Story 13: Guard resume and concurrent runs against unsafe state

**Status:** APPROVED
**Depends On:** 11, 12
**UI Changes:** No

## User Story

As an engineer running long, unattended harvests, I can trust that the tool refuses to resume when doing so would be unsafe, and refuses to let two runs collide, without ever getting stuck unable to recover from a killed process.

## Acceptance Criteria

- AC-4.8: Resuming a snapshot whose dataset selection, repository filter, or org differs from the original is refused with an explanation.
- AC-4.9: Resuming a snapshot whose checkpoint state is unreadable, or which was written by an incompatible tool version, is refused with an explanation directing the user to start fresh.
- AC-4.10: Resuming a snapshot older than a configurable staleness window (default 7 days) is refused unless the user overrides, because stored cursors may no longer be meaningful.
- AC-4.11: On interruption by the user, the tool finishes the in-flight page, flushes checkpoint state, prints the command to resume, and exits with the interrupt status; a second interrupt stops immediately.
- FR-9 (concurrent-run safety): A run claims the org within the root for its duration and refuses to start when another run holds it; the claim carries a liveness signal so a claim left by a terminated process is detected as stale and reclaimed automatically with a warning, or overridable explicitly; claims are scoped per org, so runs against different orgs sharing a root don't block each other.
- EC-12: After a run is killed, the next run's attempt to resume detects the stale claim via its liveness signal, reclaims it with a warning, and proceeds — rather than refusing forever as though a run were still active.
- EC-13: Two runs against the same org and root are refused for the second one with a clear message and its own exit status; a concurrent run against a different org in the same root proceeds normally.

## Scope

**Included:**
- Refusing to resume when the dataset selection, repository filter, or org doesn't match the original run (comparing against what Story 11's selection mechanism recorded at start).
- Refusing to resume unreadable or version-incompatible checkpoint state, with a clear "start fresh" message.
- A configurable staleness window past which a resume is refused unless overridden.
- Graceful handling of a user interrupt: finish the in-flight page, flush state, print the resume command, exit with the interrupt status; a second interrupt stops immediately.
- A per-org claim with a liveness heartbeat: a live claim blocks a second concurrent run against the same org; a stale claim (from a killed process) is detected and reclaimed automatically with a warning; claims don't block runs against different orgs sharing the same root.

**Excluded:**
- The underlying resume mechanics this story guards — automatic discovery of the newest incomplete snapshot, cursor continuation, and duplicate-free writes (Story 12).
- The dataset-selection and repository-filter mechanism being compared against (Story 11) — this story only compares recorded values, it doesn't define the selection syntax.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 11's acceptance criteria are met: a run's dataset selection and repository filter are established and can be recorded.
- Story 12's acceptance criteria are met: the happy-path automatic-resume mechanism works.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.
