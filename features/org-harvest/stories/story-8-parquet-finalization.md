# Story 8: Finalize a snapshot into Parquet with a stable schema

**Status:** APPROVED
**Depends On:** 7
**UI Changes:** No

## User Story

As a data analyst, I can load a finished snapshot as Parquet directly into my usual tools, with a schema that's identical whether the org is huge or nearly empty, so that I never have to write a parser for the raw download.

## Acceptance Criteria

- AC-8.2: On successful completion each NDJSON file is converted to Parquet using that dataset's declared schema.
- AC-8.3: The Parquet schema for a dataset is identical across runs and orgs, including when the dataset has zero records or a column is null throughout.
- AC-8.4: Conversion failure for one dataset discards no downloaded data and is recorded as a gap.
- AC-8.5: Finalization — conversion plus manifest write — is re-runnable on its own against a complete snapshot, without re-downloading.
- AC-8.9: The user can keep the intermediate NDJSON rather than having it removed after conversion.
- AC-8.10: An interrupted run leaves valid, readable NDJSON for everything completed so far.

## Scope

**Included:**
- Converting each dataset's NDJSON file to Parquet using that dataset's declared schema (from the registry established in Stories 4–6), so schema never depends on which fields happened to be present in a given run.
- Producing a correctly-schemed, loadable Parquet file even for a zero-record dataset or one where a column is null throughout.
- A conversion failure for one dataset is recorded as a gap and does not discard any already-downloaded data for that dataset or any other.
- Finalization is a standalone, re-runnable operation against an already-complete snapshot's NDJSON, without triggering any re-download.
- Retaining intermediate NDJSON on request instead of removing it after conversion.
- Ensuring an interrupted run (mid-fetch, before finalization) leaves whatever NDJSON it wrote in a valid, readable state.

**Excluded:**
- Writing the manifest itself and the root index (Story 9) — this story converts data and reports conversion outcomes; the manifest that records those outcomes for the whole snapshot is built next.
- Discarding a partially-written trailing record after an abrupt kill (already covered by Stories 5 and 6's checkpoint/write mechanics) — this story assumes the NDJSON it reads is already well-formed up to its last complete record.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 7's acceptance criteria are met: a run produces NDJSON for every dataset it attempts, records gaps for failures, and stops cleanly (without finalizing) on a systemic failure.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.
