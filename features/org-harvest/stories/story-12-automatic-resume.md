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

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/checkpoint.py` — add `CURSOR_DONE = "__done__"`, a sentinel cursor value marking one sub-resource (a repository within a repo-level dataset, a team within a per-team org-level connection) as fully attempted — reached its natural pagination end, or ended in a recorded gap — distinct from `None` (never attempted) and a real opaque GraphQL cursor (in progress). Add `CheckpointStore.resume(path) -> CheckpointStore`, a second constructor (alongside `create()`) that loads existing on-disk state instead of starting fresh; compatibility checks on that state (schema version, org, selection match, staleness) are explicitly left to Story 13.
2. `src/org_harvest/gaps.py` — add `Gap.from_dict(data: dict[str, Any]) -> Gap`, reconstructing a `Gap` from its checkpoint-ledger form (needed because a loaded `checkpoint.state.gaps` entry is a loosely-typed dict that doesn't satisfy `Gap`'s exact per-field types under `mypy --strict` when unpacked as `Gap(**g)`).
3. `src/org_harvest/output.py` — add `read_ndjson_tolerant(path) -> list[dict]` and `count_records(path) -> int`. The tolerant reader discards exactly one trailing unparseable line (the signature of a process killed mid-write, AC-4.6) while still raising `json.JSONDecodeError` for a malformed line anywhere else in the file. `count_records()` re-reads the file rather than trusting an in-memory counter, so a resumed dataset's reported count reflects pre-existing plus newly-written records regardless of which process wrote which line.
4. `src/org_harvest/finalize.py` — replace the private `_read_ndjson` with `output.read_ndjson_tolerant`, so Parquet conversion inherits the same trailing-truncation tolerance (AC-4.6) rather than treating a killed run's last partial line as a conversion failure.
5. `src/org_harvest/harvest/org_level.py` — for every dataset-fetching method (`fetch_organization_scalar`, `fetch_org_connection`, `fetch_team_connection`):
   - Skip entirely (zero network calls) when `self._checkpoint.state.dataset_status.get(dataset) == "complete"`, returning a `_resumed_outcome()` built from `count_records()` on the existing NDJSON file plus any gaps already recorded against that dataset in the checkpoint (AC-4.2, AC-4.5).
   - `fetch_team_connection` additionally tracks a `CURSOR_DONE` marker per team (cursor key `f"{dataset}:{team_id}"`), skipping any team already marked done, and marking each team done the moment its pagination naturally ends or it produces a gap — needed for AC-4.5's "do not re-fetch already-finished pages" to hold at the per-team level, not just the whole-dataset level.
   - Every outcome's `record_count` now comes from `count_records()` on the dataset's NDJSON file, not an in-memory counter — a single code path serves both the fresh and resumed cases.
6. `src/org_harvest/harvest/repo_level.py`:
   - `fetch_repo_dataset(spec, repos)` now: skips entirely if the whole dataset is already `"complete"` (same as org-level); otherwise builds its work queue by reading each repository's stored cursor from `self._checkpoint.state.cursors[f"{spec.dataset}:{repo.id}"]`, skipping any repository already marked `CURSOR_DONE` and seeding `repo.cursor` with whatever real cursor (or `None`) is stored for every other repository. This checkpoint-driven reset supersedes DEV-8's earlier blanket `repo.cursor = None` reset (DEV-9) — it fixes the same cross-dataset cursor leak while also being the mechanism that makes resume itself possible.
   - `_run_batch()`'s pagination-completion logic now calls `self._checkpoint.set_cursor(f"{spec.dataset}:{repo.id}", CURSOR_DONE)` whenever a repository's pagination for this dataset is truly finished (no next page, item cap reached, or an unrecoverable gap), instead of leaving the cursor at its last real value. A non-paginated spec is marked `CURSOR_DONE` immediately after its single successful fetch (DEV-11 — these previously received no completion tracking at all, which would have caused duplicate records on any resume).
   - Every outcome's `record_count` comes from `count_records()`, matching org-level's change.
7. Tests: `TestCheckpointResume` in `tests/test_checkpoint.py`; `TestReadNdjsonTolerant`/`TestCountRecords` in `tests/test_output.py`; two Story 8 fixtures in `tests/test_finalize.py` rewritten so their malformed line is non-trailing (DEV-10), plus a new trailing-truncation test; `TestResume` in both `tests/test_org_level.py` and `tests/test_repo_level.py`.
8. Create `src/org_harvest/resume.py`: `find_newest_incomplete_snapshot(org_dir) -> Path | None` (AC-4.2) and `find_named_snapshot(org_dir, name) -> Path | None` (AC-4.3), both built on Decision 5's existing "checkpoint present, manifest absent = incomplete" rule. Neither function opens or validates the checkpoint it finds — that's Story 13's job.
9. `src/org_harvest/run.py` — `run_snapshot()` gains `resume: str | None` and `force_fresh: bool = False`. A named-resume target that doesn't exist is resolved to `INVALID_USAGE` before preflight runs (same "before any network call" principle Story 11 established for dataset-selection errors). Otherwise: `force_fresh` always creates a fresh snapshot; a given `resume` name opens exactly that (already-confirmed-to-exist) snapshot via `CheckpointStore.resume()`; with neither given, `find_newest_incomplete_snapshot()` decides automatically, falling back to a fresh snapshot when nothing incomplete exists (AC-4.4). `RunResult` gained `resumed_from: Path | None`, reporting which snapshot (if any) was resumed.
10. `src/org_harvest/cli.py` — `run` command gains `--resume SNAPSHOT` and `--force-fresh`, threaded through `_do_run`/`run_snapshot`; `_print_run_result()` prints `resuming snapshot: <path>` when `RunResult.resumed_from` is set (AC-4.2's "reports ... from where").
11. Tests: `tests/test_resume.py` (new); `TestResume` in `tests/test_run.py`; `TestRunCommandResumeOptions` in `tests/test_cli.py`.

### Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `src/org_harvest/checkpoint.py` | Modify | `CURSOR_DONE` sentinel, `CheckpointStore.resume()` |
| `src/org_harvest/gaps.py` | Modify | `Gap.from_dict()` |
| `src/org_harvest/output.py` | Modify | `read_ndjson_tolerant()`, `count_records()` |
| `src/org_harvest/finalize.py` | Modify | Use the tolerant reader (AC-4.6) |
| `src/org_harvest/harvest/org_level.py` | Modify | Whole-dataset and per-team resume/skip logic |
| `src/org_harvest/harvest/repo_level.py` | Modify | Whole-dataset and per-repository resume/skip logic |
| `src/org_harvest/resume.py` | Create | Snapshot discovery: newest-incomplete and by-name |
| `src/org_harvest/run.py` | Modify | `resume`/`force_fresh` parameters and branching |
| `src/org_harvest/cli.py` | Modify | `--resume`/`--force-fresh` flags and reporting |
| `tests/test_checkpoint.py` | Modify | `TestCheckpointResume` |
| `tests/test_output.py` | Modify | `TestReadNdjsonTolerant`, `TestCountRecords` |
| `tests/test_finalize.py` | Modify | Non-trailing fixtures (DEV-10) + new trailing-truncation test |
| `tests/test_org_level.py` | Modify | `TestResume` |
| `tests/test_repo_level.py` | Modify | `TestResume` |
| `tests/test_resume.py` | Create | Snapshot-discovery unit tests |
| `tests/test_run.py` | Modify | `TestResume` (orchestration) |
| `tests/test_cli.py` | Modify | `TestRunCommandResumeOptions` |

### Cross-Module Seams

| Write Module | Write Path | Read Module | Read Path | Data Description | Required Integration Test | AC Ref | Confirmed |
|---|---|---|---|---|---|---|---|
| `checkpoint.py` (`set_cursor`, `set_dataset_status`, `record_gap`) | `<snapshot_dir>/checkpoint.json` | `org_level.py`/`repo_level.py` (resume-time reads via `CheckpointStore.resume()`) | file read, same process, later invocation | Per-dataset status, per-collection/per-repository cursors (including `CURSOR_DONE`), and recorded gaps from a prior, possibly-killed run | `TestResume` in `test_org_level.py` and `test_repo_level.py` (write a checkpoint state directly, then assert the fetch call reads and honors it) | AC-4.2, AC-4.5 | Confirmed — this story owns both ends |
| `org_level.py`/`repo_level.py` (NDJSON writes) | `<snapshot_dir>/<dataset>.ndjson` | `output.count_records()` (same modules, end of each fetch) | file read | The ground-truth record count for a dataset, whether written this run or a prior one | Covered inline by every `TestResume` case (`outcome.record_count` assertions) and by `TestCountRecords` in `test_output.py` directly | AC-4.5 | Confirmed |
| `resume.py` (`find_newest_incomplete_snapshot`/`find_named_snapshot`) | in-memory `Path \| None` return value | `run.py` (`run_snapshot`) | function return value | Which snapshot directory (if any) to resume | `TestResume` in `test_run.py` (pre-creates an incomplete snapshot directory, asserts `run_snapshot()` reuses it rather than creating a new one) | AC-4.2, AC-4.3, AC-4.4, AC-4.7 | Confirmed — this story owns both ends |

### Testing Approach

- **`tests/test_checkpoint.py` (+2 cases):** `TestCheckpointResume` — `resume()` opens a checkpoint and sees exactly the state a fresh `load()` would; the resumed store can keep mutating and saving afterward (proving it isn't a read-only snapshot).
- **`tests/test_output.py` (+6 cases):** `TestReadNdjsonTolerant` — missing file is an empty list; every complete record is read; a truncated trailing line is discarded (AC-4.6); a malformed *non-trailing* line still raises. `TestCountRecords` — missing file is zero; counts complete records only, ignoring a truncated trailing one.
- **`tests/test_finalize.py`:** two existing Story 8 tests' fixtures rewritten so their malformed line is not trailing (DEV-10), preserving their original "malformed data still fails conversion" intent; one new test, `test_a_truncated_trailing_line_is_discarded_not_a_conversion_failure_ac_4_6`, asserting a file with 2 complete records and 1 truncated trailing one converts cleanly with zero gaps and a record count of 2.
- **`tests/test_org_level.py` (+4 cases, `TestResume`):** a dataset already marked `"complete"` triggers zero network calls and reports the correct count from disk (AC-4.2/AC-4.5); a completed dataset's previously-recorded gaps still surface in its resumed outcome; a connection with a stored (non-`CURSOR_DONE`) cursor resumes from exactly that cursor and appends rather than duplicates; a team already marked `CURSOR_DONE` is skipped while a sibling team with no stored cursor is still fetched normally.
- **`tests/test_repo_level.py` (+4 cases, `TestResume`):** a dataset already marked `"complete"` triggers zero network calls; a repository mid-pagination resumes from its stored cursor without duplicating the already-written first page; a repository marked `CURSOR_DONE` for a dataset is skipped while another repository for the same dataset still resumes/fetches normally; a non-paginated spec's `CURSOR_DONE` marker is honored on resume (DEV-11's regression coverage).
- **`tests/test_resume.py` (10 cases, new):** `find_newest_incomplete_snapshot` — missing org directory, empty org directory, and a directory containing only complete snapshots all return `None` (AC-4.4); picks the lexicographically newest among several incomplete candidates (AC-4.2); a newer *complete* snapshot doesn't shadow an older incomplete one; a directory with neither file is not a candidate at all. `find_named_snapshot` — returns the directory when it exists (AC-4.3), `None` when it doesn't or the org directory itself is missing, and resolves an already-complete named snapshot rather than treating it as not-found (that judgment is explicitly Story 13's).
- **`tests/test_run.py` (+5 cases, `TestResume`):** no incomplete snapshot present means a fresh run with `resumed_from is None` (regression, AC-4.4); the default (no `resume`, no `force_fresh`) path resumes the newest incomplete snapshot and creates no second directory (AC-4.2); `force_fresh=True` ignores an existing incomplete snapshot and creates a new one alongside it (AC-4.7); a named `resume` targets exactly that snapshot even when a newer one exists (AC-4.3); an unknown `resume` name is `INVALID_USAGE` with no snapshot directory created and no preflight call made.
- **`tests/test_cli.py` (+4 cases, `TestRunCommandResumeOptions`):** `--resume` and `--force-fresh` are each passed through to `run_snapshot()` correctly; omitting both passes `resume=None, force_fresh=False`; `RunResult.resumed_from` being set is reported in the CLI's printed output (AC-4.2's "reports ... from where").
- No end-to-end test drives a real multi-page GraphQL exchange across a process kill and restart — that would require actually killing a process mid-run, which is impractical to script reliably; the mid-pagination resume tests instead directly construct the checkpoint state a kill would have left behind (a stored cursor, a partially-written NDJSON file) and verify the fetch functions behave correctly from that state, which is the same effective contract without the flakiness of an actual process kill.

### Risks

- `CheckpointState.repository_filter` (already present as a field since Story 5/9) is read by nothing yet — a resumed run does not currently verify that its `--repos`/`--exclude-archived`/`--exclude-forks` flags match what the checkpoint recorded, so resuming with a *different* repository filter than the original run silently mixes filter semantics across the two runs' worth of data. This is explicitly Story 13's territory (its own listed exclusion: "refusing to resume a snapshot whose selection, repository filter, org, ... make resuming unsafe") — flagged here so Story 13's planning doesn't have to rediscover it.
- Similarly, nothing yet compares `CheckpointState.dataset_selection`, `.org`, or `.schema_version` against the current run's request before resuming — an operator could resume `acme`'s checkpoint while accidentally targeting a differently-cased org string, or resume with a narrower or wider dataset selection than the original run used, and get a silently inconsistent snapshot. Story 13 owns adding these guards; this story's `CheckpointStore.resume()` and `find_named_snapshot()`/`find_newest_incomplete_snapshot()` deliberately do no such validation themselves, per the story's own excluded scope.
- A live Ctrl-C during a run is not given any special handling by this story (excluded scope, explicitly Story 13's) — an interrupted run simply stops wherever the current `await` happens to be, relying on the checkpoint's continuous per-page writes (Story 5/6) to make that stopping point resumable. This story's contribution is entirely about the *next* run correctly picking up from whatever the checkpoint says, not about making the interrupt itself graceful.
- Concurrent-run locking is likewise excluded (Story 13) — two processes could both discover and resume the same incomplete snapshot simultaneously right now, corrupting the checkpoint via racing writes. Not addressed here.

### Decisions Made

- **A single `CURSOR_DONE` sentinel, reused for both org-level per-team tracking and repo-level per-repository tracking**, rather than two differently-named or differently-typed completion markers — the semantic ("fully attempted, whether by natural pagination end or a recorded gap") is identical in both places, and a shared constant keeps that semantic from silently diverging between the two harvest modules.
- **Every dataset-outcome-producing method in both harvest modules now derives `record_count` from `count_records()` (re-reading the NDJSON file) rather than an in-memory counter, even on the non-resumed happy path** — not just the resumed path. A resumed dataset's true count must reflect pre-existing plus newly-fetched records either way, and using one ground-truth-based code path for both cases is simpler and strictly safer than maintaining two separate counting strategies that could drift apart.
- **DEV-8's blanket cursor reset is superseded, not layered alongside, DEV-9's checkpoint-driven reset** — keeping both would mean two different pieces of code deciding what a repository's starting cursor should be for a given dataset; the checkpoint-driven version is a strict superset of what DEV-8 fixed (see DEV-9's reasoning), so DEV-8's fix was removed rather than kept as a redundant safety net.
- **Snapshot directory discovery (`resume.py`) does zero validation of what it finds** — it answers "which directory" only, deferring every "is it safe to actually resume this" question to Story 13, matching this story's own explicitly stated excluded scope rather than scope-creeping into the next story's territory.
- **A named `--resume` target that doesn't exist is `INVALID_USAGE`, checked before preflight** — the same "fail before any network call" principle Story 11 established for an unknown dataset name; a resume request is either satisfiable by a name on disk or it isn't, and that's knowable with no network access at all.
