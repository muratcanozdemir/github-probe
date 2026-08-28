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

---

## Implementation Plan

### Implementation Steps

1. Add `TOOL_VERSION` to `src/org_harvest/constants.py` if not already present (checked: it already existed from Story 1's preflight/version reporting work — reused as-is).
2. Create `src/org_harvest/manifest.py`:
   - `CompletionStatus` enum (`COMPLETE`, `COMPLETE_WITH_GAPS`).
   - `ConsumptionStats` frozen dataclass — every field optional/defaulted, since Story 9 only persists whatever Story 10 hands it (see Risks).
   - `Manifest` frozen dataclass with a `status` property derived from `gaps`/`scope_restricted` (AC-8.7's "completion status" field), plus `to_dict`/`from_dict` for JSON round-tripping.
   - `_atomic_write_json()` — same temp-file + `fsync` + `os.replace()` pattern already established in `checkpoint.py`, reused here rather than re-derived, so both modules fail the same way under a crash mid-write.
   - `write_manifest()` / `read_manifest()` — thin wrappers over the atomic writer and a JSON read; `read_manifest()` returns `None` on a missing file rather than raising.
   - `is_snapshot_complete()` — AC-8.11 in one place: `read_manifest(...) is not None`.
   - `build_manifest()` — aggregates `DatasetOutcome` tuples (fetch outcomes for counts, fetch + conversion outcomes for gaps) into a `Manifest`, stamping `TOOL_VERSION` itself so callers can't pass a stale value.
   - `SnapshotIndexEntry` / `RootIndex` dataclasses and `rebuild_root_index()` — AC-8.8: scans every subdirectory of an org's snapshot root, reads each one's manifest (or marks it `"incomplete"`), and picks `latest_complete` as the newest entry whose status is strictly `COMPLETE` (not `COMPLETE_WITH_GAPS`).
3. Update `src/org_harvest/__init__.py` — re-export `CompletionStatus`, `ConsumptionStats`, `Manifest`, `RootIndex`, `build_manifest`, `is_snapshot_complete`, `read_manifest`, `rebuild_root_index`, `write_manifest`.
4. Write `tests/test_manifest.py` covering status derivation, round-tripping (in-memory and through disk), `is_snapshot_complete`'s no-manifest-means-incomplete semantics, `build_manifest`'s aggregation (including that conversion gaps fold in without touching fetch-sourced counts), and `rebuild_root_index`'s listing/`latest_complete` selection.

### Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `src/org_harvest/manifest.py` | Create | Manifest, root index, atomic JSON persistence |
| `src/org_harvest/__init__.py` | Modify | Re-export Story 9's public API |
| `tests/test_manifest.py` | Create | Unit tests for all of the above |

### Cross-Module Seams

| Write Module | Write Path | Read Module | Read Path | Data Description | Required Integration Test | AC Ref | Confirmed |
|---|---|---|---|---|---|---|---|
| `manifest.py` (`write_manifest`) | `<snapshot_dir>/manifest.json` | Story 10 (run summary/exit code), Story 12/13 (resume discovery), Story 14 (retry-gaps) | `read_manifest` / `is_snapshot_complete` | Per-snapshot completeness record | `test_manifest.py::TestManifestRoundTrip`, `TestIsSnapshotComplete` (write+read in the same test, since the reading modules don't exist yet) | AC-5.6, AC-8.7, AC-8.11 | Confirmed (this story owns both the write and the read API; Stories 10/12/13/14 are the not-yet-built external callers) |
| `manifest.py` (`rebuild_root_index`) | `<org_dir>/index.json` | A future listing/CLI command (not yet built) | none yet | Per-org snapshot listing + latest-complete pointer | `test_manifest.py::TestRootIndex` | AC-8.8 | Unconfirmed downstream reader (no consumer exists until a later story); the on-disk shape itself is fully tested by writing and re-reading `index.json` |

### Testing Approach

- **Unit tests (`tests/test_manifest.py`, 19 cases):**
  - `TestManifestStatus` — clean run is `COMPLETE`; a gap, and scope restriction alone, each force `COMPLETE_WITH_GAPS`.
  - `TestManifestRoundTrip` — `to_dict`/`from_dict` round-trips every field including a populated `ConsumptionStats`; `write_manifest`/`read_manifest` round-trips through actual disk I/O; no `.tmp` file is left behind after a write.
  - `TestIsSnapshotComplete` — `False` with no file (AC-8.11), `True` once written, and `True` even for a gapped manifest (a finalized-but-gapped run is still a real, readable result — "incomplete" means "no manifest," not "has gaps").
  - `TestBuildManifest` — aggregates counts and gaps from fetch outcomes; folds conversion-outcome gaps in without perturbing fetch-sourced counts; passes `scope_restricted` through; stamps the real `TOOL_VERSION`.
  - `TestRootIndex` — lists every snapshot directory under an org root; a directory with no manifest shows `"incomplete"`; `latest_complete` ignores gapped/scope-restricted snapshots and picks the newest strictly-complete one; an org with zero snapshots yet is a valid empty index; the index is actually written to `index.json`.
- No integration test against Stories 10/12/13/14 is possible yet since those stories don't exist — the seam table above records this as the expected, not-yet-closed state; those stories are responsible for their own read-side tests against this already-tested write-side contract.

### Risks

- `ConsumptionStats`'s figures (GraphQL points, REST requests, rate-limit waits) are not computed by this story — Story 9 only defines the shape and persists whatever it's given. Story 10, which drives a run end-to-end and already needs these same numbers for its own reporting (FR-1/AC-1.3), is the natural place to compute them; deferring that computation here would mean reaching into already-completed Transport/BudgetTracker code from Stories 2/3 for a concern this story doesn't otherwise need. Tracked here so Story 10's plan doesn't rediscover this as a surprise.
- `rebuild_root_index()`'s O(snapshots) full rescan on every call is a deliberate trade against incremental-index drift (AC-8.8 implies the index must never disagree with what's on disk); acceptable at "snapshots for one org" scale, but a caller invoking it very frequently (e.g., per-dataset instead of per-run) would make it needlessly expensive — Story 10 should call it once per run, not per dataset.

### Decisions Made

- **`status` is a derived property, not a stored field, on `Manifest`** — computed from `gaps`/`scope_restricted` every time rather than persisted redundantly, so it can never drift from the data it's derived from; `to_dict()` still serializes it (as `.value`) purely for human/tool readability of the JSON file, and `from_dict()` ignores that key on the way back in.
- **Root index is always rebuilt from a full scan, never incrementally patched** — chosen over incremental updates specifically to satisfy AC-8.8's implicit "must reflect reality" requirement; the cost is an extra directory scan per rebuild, judged cheap at this scale (see Risks).
- **`ConsumptionStats` computation is explicitly out of scope for this story** — see Risks; this story only defines and persists the shape.
- **`build_manifest()` stamps `TOOL_VERSION` itself** rather than accepting it as a parameter, so every manifest reflects the actual running tool version and callers can't accidentally pass a stale or hardcoded one.
