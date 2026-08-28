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

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/checkpoint.py` — add two mutator methods alongside the existing `set_cursor`/`set_dataset_status`/`record_gap`: `reset_dataset(dataset)` (clears a dataset's completion status and every gap recorded against it) and `clear_cursor(key)` (removes a cursor entry outright — distinct from `set_cursor(key, None)`, which would instead record "never attempted," itself indistinguishable from a key that was never touched at all).
2. `src/org_harvest/manifest.py` — `build_manifest()` gains a `last_retried_at: str | None = None` parameter, passed straight through to the `Manifest` dataclass field Story 9 already reserved for this story.
3. `src/org_harvest/harvest/org_level.py` — `fetch_organization_directory()` gains `team_ids: frozenset[str] | None = None`, applied as a filter on the `teams` list read from `teams.ndjson` right before `team_members`/`team_repositories` are fetched; `None` (the default) fetches every team, matching pre-Story-14 behavior exactly.
4. `src/org_harvest/harvest/repo_level.py` — `fetch_repository_datasets()` gains `repository_ids: frozenset[str] | None = None`, applied as a filter on the repos list read from `repositories.ndjson`; same `None`-means-unfiltered default.
5. Create `src/org_harvest/retry.py`: `retry_gaps()` reads the snapshot's manifest (raising `OrgHarvestError(kind=INVALID_USAGE)` if none exists — an incomplete snapshot is Story 12/13's resume territory, not this one's) and groups its gaps by dataset. No gaps at all is the AC-11.5 no-op: returns immediately with no checkpoint access, no re-fetch, no finalize, no manifest rewrite. Otherwise, for each gapped dataset (one `fetch_organization_directory()`/`fetch_repository_datasets()` call per dataset, never batched together, so retrying dataset A's gaps can't accidentally over-fetch data for dataset B just because both gapped on an overlapping-but-different resource set): `_reset_for_retry()` clears the dataset's completion status, its own gap entries, and either its one whole-dataset cursor key or the specific per-resource cursor keys for its gapped resources (repository ids for repo-level datasets, team ids for `team_members`/`team_repositories`; every other org-level dataset has no per-resource cursor scheme at all, even though its gap's `resource_id` is set — DEV-14). Every other dataset (never gapped) is left completely untouched — its outcome is reconstructed from the *existing* manifest (its record count and zero gaps) rather than re-fetched, so the new manifest still accounts for everything the original run did. `finalize_snapshot()` (Story 8) then re-runs unconditionally over the whole snapshot directory (AC-11.4), and a fresh manifest is built and written with `last_retried_at` set to now (AC-11.3), followed by `rebuild_root_index()`.
6. `src/org_harvest/cli.py` — new `retry-gaps ORG SNAPSHOT` command (with the same `--snapshot-root` and shared credential options as `run`), resolving `snapshot_dir = snapshot_root/org.lower()/SNAPSHOT` and printing which datasets were retried, their updated counts, and whether any gaps remain; exits `SUCCESS`/`COMPLETED_WITH_GAPS` mirroring `run`'s own exit-status logic (or `SUCCESS` for the no-op case), reusing `ExitStatus`/`exit_status_for_error` rather than inventing new codes for this command.
7. Tests: new `tests/test_retry.py` (`TestNoManifest`, `TestNoGaps`, `TestRepoLevelRetry`, `TestOrgLevelRetry`); new `TestRetryGapsCommand` in `tests/test_cli.py`.

### Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `src/org_harvest/checkpoint.py` | Modify | `reset_dataset()`, `clear_cursor()` |
| `src/org_harvest/manifest.py` | Modify | `build_manifest(last_retried_at=...)` |
| `src/org_harvest/harvest/org_level.py` | Modify | `team_ids` filter |
| `src/org_harvest/harvest/repo_level.py` | Modify | `repository_ids` filter |
| `src/org_harvest/retry.py` | Create | `retry_gaps()`, `RetryResult` |
| `src/org_harvest/cli.py` | Modify | `retry-gaps` command |
| `tests/test_retry.py` | Create | Core retry-mechanics tests |
| `tests/test_cli.py` | Modify | `TestRetryGapsCommand` |

### Cross-Module Seams

| Write Module | Write Path | Read Module | Read Path | Data Description | Required Integration Test | AC Ref | Confirmed |
|---|---|---|---|---|---|---|---|
| `manifest.py` (a prior `run_snapshot()`'s `write_manifest`) | `<snapshot_dir>/manifest.json` | `retry.py` (`read_manifest`) | file read | The prior run's gaps, dataset counts, and every other manifest field a retry must preserve for untouched datasets | `tests/test_retry.py`'s every test (each seeds a manifest via `build_manifest()`/`write_manifest()`, then calls `retry_gaps()` and inspects the rewritten one) | AC-11.1–AC-11.3 | Confirmed — this story owns both ends |
| `checkpoint.py` (`reset_dataset`/`clear_cursor`) | `<snapshot_dir>/checkpoint.json` | `org_level.py`/`repo_level.py` (their existing resume-aware skip/resume checks, unchanged since Story 12) | file read via the same `CheckpointStore` instance | Which datasets/resources should be treated as "never attempted" for this retry pass | `TestRepoLevelRetry`/`TestOrgLevelRetry` (each asserts the gapped resource is genuinely re-queried, and — where applicable — that an *un*-gapped sibling resource is not) | AC-11.1 | Confirmed |

### Testing Approach

- **`tests/test_retry.py` (7 cases):** `TestNoManifest` — a snapshot with no `manifest.json` raises `OrgHarvestError(kind=INVALID_USAGE)`. `TestNoGaps` — a clean manifest is a reported no-op: `retried=False`, the original manifest object returned unchanged, `finalize_snapshot` never called, no `checkpoint.json` ever created. `TestRepoLevelRetry` — a gapped repository, once fixed, has its gap cleared and its record merged into the dataset's Parquet output alongside the repository that already succeeded (verified by reading the post-finalize `.parquet` file, since finalize removes the NDJSON by default); a repository that fails again keeps exactly one gap, with a new reason and timestamp; a dataset that never gapped is provably never re-queried (a handler assertion) and its original count is preserved untouched in the new manifest. `TestOrgLevelRetry` — a whole-org-level dataset gap (`members`) is refetched from scratch and its Parquet output reflects the new data; a team-scoped gap (`team_members`) re-queries only the gapped team, leaving a sibling team already marked `CURSOR_DONE` untouched.
- **`tests/test_cli.py` (+4 cases, `TestRetryGapsCommand`):** the no-op case prints its message and exits 0; a fully-resolved retry prints the retried datasets and "all gaps resolved," exiting 0; a retry with gaps still remaining exits `COMPLETED_WITH_GAPS`; the snapshot directory is built correctly from `org.lower()`/`--snapshot-root`/the given snapshot name.
- No test drives `retry_gaps()` through the full `run` → gap → `retry-gaps` pipeline end-to-end in one process — each layer (the fetch engines' resume-aware mechanics, this story's grouping/reset/re-fetch logic, and the CLI's argument wiring) already has its own focused coverage, and Story 12's own test suite already proves the fetch engines behave correctly when handed a checkpoint with specific entries cleared — which is exactly what this story's reset step produces.

### Risks

- Retrying a dataset whose original gap was a whole-snapshot-level systemic failure (FR-5, `SystemicFailureGuard` tripping mid-fetch) re-attempts that dataset with a *fresh* `SystemicFailureGuard` (the default one `fetch_organization_directory`/`fetch_repository_datasets` construct when none is passed) — a systemic failure during a retry stops just that one dataset's retry attempt (the guard raises `OrgHarvestError`, uncaught by `retry_gaps()`, propagating to the caller) rather than being caught and turned into a gap the way an individual resource's failure is. This matches how systemic failures are handled everywhere else in the codebase (they stop the run, they don't become an ordinary gap) but is worth calling out: a retry that hits a fresh outage exits with an error rather than a partial "some gaps remain" result.
- A dataset gapped with `resource_id=None` (the "dataset not yet implemented" gap Story 11 introduced for a selected-but-unimplemented optional dataset) is retried the same way as any whole-dataset gap — since there's still no fetch implementation for it, the retry simply regenerates the identical gap. This is correct (AC-11.2's "fail again, keep the gap updated") but means such a dataset can never actually be resolved by retry-gaps alone; only implementing its fetch (a future story) would change that.
- Retry-gaps has no equivalent of Story 13's concurrent-run claim — nothing stops a `retry-gaps` invocation from racing with a `run` resuming the same snapshot, or with a second concurrent `retry-gaps` call. This wasn't in this story's scope and isn't addressed here; flagged for awareness rather than as a defect, since Story 13's guards were scoped specifically to `run_snapshot()`.

### Decisions Made

- **One fetch call per gapped dataset, never a single batched call across multiple gapped datasets** — even though `fetch_organization_directory()`/`fetch_repository_datasets()` both accept a multi-name `dataset_names` selection, using it here would force a single shared `team_ids`/`repository_ids` filter across every dataset in that call, over-fetching any dataset whose own gapped-resource set is a strict subset of another's. Calling once per dataset costs more round-trips in the (presumably rare) case of multiple gapped datasets, in exchange for never re-downloading more than exactly what failed — matching AC-11.1's "only the gapped resources" literally, not just approximately.
- **Untouched datasets are reconstructed from the existing manifest, not re-read from their NDJSON/Parquet files** — the manifest already has exactly the two pieces this story needs (record count, gap list) in one place, cheaper and simpler than re-deriving them from disk for datasets nothing is changing.
- **`retry-gaps` is a new top-level CLI command, not a flag on `run`** — a retry targets a specific, already-completed snapshot by name rather than starting or resuming a run, which is a different enough operation (different arguments, different exit-status derivation, no preflight/claim/interrupt machinery at all) to warrant its own command rather than overloading `run`'s.
- **DEV-14's fix (routing per-resource vs. whole-dataset cursor clearing by dataset shape, not by gap payload) reflects a general principle worth stating explicitly**: `Gap.resource_id` is populated for *every* gap (Story 5 onward), including ones for datasets with no real per-resource concept — code that consumes gaps for anything beyond display/counting must know each dataset's own shape, not infer it from whether a field happens to be non-`None`.
