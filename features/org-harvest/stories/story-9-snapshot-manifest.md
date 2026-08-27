# Story 9: Describe a snapshot's completeness in its manifest

**Status:** APPROVED
**Depends On:** 8
**UI Changes:** No

## User Story

As an auditor relying on this data, I can tell whether a snapshot is complete just by looking at it on disk — without re-reading console output — so that I never mistake an incomplete download for a complete one.

## Acceptance Criteria

- AC-5.6: The presence or absence of gaps is discoverable from the snapshot alone, without the run's console output.
- AC-8.7: Each snapshot contains a manifest describing that snapshot: org, API host, tool version, start and completion times, dataset selection, per-dataset record counts, all gaps, scope restrictions, consumption statistics, and completion status.
- AC-8.8: A root index lists all snapshots per org and identifies the most recent snapshot that completed with no gaps and no scope restriction.
- AC-8.11: A snapshot without a manifest is treated as incomplete by every operation that reads snapshots.

## Scope

**Included:**
- Writing a per-snapshot manifest recording org, API host, tool version, start and completion times, dataset selection, per-dataset counts, every gap, scope restrictions, consumption statistics, and completion status — sourced from Stories 5–8's outputs.
- Maintaining a per-org root index listing every snapshot for that org and pointing at the most recent one with no gaps and no scope restriction.
- Making gap presence, and overall completeness, readable from the manifest alone with no dependency on console output.
- Treating any snapshot lacking a manifest — for whatever reason, including a run that died during finalization — as incomplete by every operation that reads snapshots (listing, resuming, retry-gaps).

**Excluded:**
- Producing the per-dataset counts, gaps, and consumption figures themselves — those come from Stories 5–8; this story is responsible for aggregating and persisting them, not generating them.
- Reporting a run's outcome to the console or as an exit code (Story 10) — this story is the on-disk record; Story 10 is what a user sees while and after running the command.
- Updating the manifest after a retry-gaps operation (Story 14) — this story defines the manifest's shape and its "no manifest = incomplete" rule; Story 14 is responsible for updating an existing manifest afterward.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 8's acceptance criteria are met: a snapshot's NDJSON can be finalized into Parquet, with conversion outcomes (including any conversion-failure gaps) known.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.
