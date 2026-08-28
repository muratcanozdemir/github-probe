# Story 11: Choose which datasets to download

**Status:** APPROVED
**Depends On:** 6
**UI Changes:** No

## User Story

As an engineer working with a very large organization, I can control which datasets and repositories are included in a run so that I get a useful result inside one rate-limit window instead of waiting hours for data I won't use.

## Acceptance Criteria

- AC-2.1: The user can restrict a run to a named subset of the default tier.
- AC-2.2: With no selection given, the run includes exactly the default tier.
- AC-2.3: Naming an optional (off-by-default) dataset in the selection enables it; the same mechanism both narrows within the default tier and opts into the optional tier.
- AC-2.4: Requesting an unknown dataset name fails immediately, before any network call, listing the valid names.
- AC-2.5: A selection that resolves to zero datasets is rejected before any network call.
- AC-2.6: Selecting a dataset that depends on another automatically includes the dependency, and the run reports that it did so.
- AC-2.8: The user can restrict a run to a subset of repositories, and can exclude archived repositories and forks.
- AC-2.9: The user can cap the number of items collected per repository-level collection.

## Scope

**Included:**
- Selection syntax that both narrows the default tier and opts into optional-tier datasets by the same mechanism.
- Validation of a selection before any network call: unknown names rejected with the valid-name list, and an empty resolved selection rejected.
- Automatic inclusion of a selected dataset's dependencies, with that inclusion reported to the user.
- A repository-subset filter, and flags to exclude archived repositories and forks from a run.
- A per-repository-level-collection item cap.
- Applying all of the above to both Phase 1 (Story 5) and Phase 2 (Story 6) fetching.

**Excluded:**
- Listing datasets without running a download (already delivered by Story 4's AC-2.7) — this story only adds the mechanics of restricting and expanding an actual run's selection.
- Any optional-tier dataset's fetch implementation itself — this story makes optional datasets *selectable*; whichever dataset is selected must already exist in the registry with a complete field list and schema (from Stories 5/6, extended as needed for optional-tier entries).

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 6's acceptance criteria are met: both the organization-level and repository-level default-tier fetches work end to end, so selection has real fetch behavior to narrow or extend.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/datasets/catalog.py` — declare `depends_on=("repositories",)` on all 23 repository-level `DatasetSpec` entries (15 default-tier + 8 optional-tier) that lacked it (DEV-7) — a real, pre-existing structural dependency (Phase 2 fans out over exactly what Phase 1's `repositories` dataset wrote, architecture.md Decision 4) that Story 11's dependency-closure mechanism needs the registry to state explicitly.
2. Create `src/org_harvest/selection.py`:
   - `DatasetSelection` (`names`, `auto_included`) and `resolve_dataset_selection(requested: Sequence[str] | None) -> DatasetSelection` — `None` → full default tier (AC-2.2); otherwise validates every name via `get()` (AC-2.4), rejects an empty resolved set (AC-2.5), and walks `depends_on` to a fixed point, recording which names were pulled in only via a dependency (AC-2.6) — all before any network call.
   - `RepositoryFilter` (`names`, `exclude_archived`, `exclude_forks`, `.allows()`, `.is_noop`) — AC-2.8.
3. `src/org_harvest/harvest/org_level.py` — `fetch_organization_directory()` gains `dataset_names`/`repository_filter` parameters: `dataset_names=None` preserves exact pre-Story-11 behavior; a given selection narrows which org-level connections run and which optional-tier org-level names (selected but unimplemented) become an explicit "dataset not yet implemented" gap rather than silent nothing. `repository_filter` is applied where the `repositories` connection writes records — the same point Phase 2 later reads from, so filtering there narrows both the dataset and the fan-out at once. `_OrgLevelHarvester` gained a required `repository_filter` constructor parameter (matching DEV-6's precedent for `systemic_guard`).
4. `src/org_harvest/harvest/repo_level.py` — `fetch_repository_datasets()` gains matching `dataset_names`/`item_cap` parameters, with the same "unimplemented optional dataset → explicit gap" handling. `item_cap` (AC-2.9) is enforced per (repository, dataset) via a `written_per_repo` dict threaded through `_run_batch`'s recursive node-limit-retry calls; a repository that hits its cap stops being re-queued even if `hasNextPage` is still `true`. Fixed a related latent bug (DEV-8): `_RepoState.cursor` is now reset to `None` at the start of every `fetch_repo_dataset()` call, since the same `_RepoState` objects are reused across every dataset in `fetch_repository_datasets()`'s loop and a stale cursor from one dataset was otherwise able to leak into the next dataset's first page for the same repository.
5. `src/org_harvest/run.py` — `run_snapshot()` gains `dataset_names`/`repository_filter`/`item_cap` parameters; resolves the selection via `resolve_dataset_selection()` as its very first step (before preflight, so AC-2.4/AC-2.5 fail before any network call); threads `selection.names` into preflight and both fetch phases; reports `selection.auto_included` on `RunResult.auto_included_datasets` (AC-2.6).
6. `src/org_harvest/cli.py` — `run` command gains `--datasets`, `--repos`, `--exclude-archived`, `--exclude-forks`, `--max-items-per-collection`; `preflight` command now resolves its `--datasets` option through the same shared `resolve_dataset_selection()` (replacing its own private, dependency-blind parser) and prints auto-included dependencies too, for consistency between what preflight reports and what an actual run would do.
7. Re-export `DatasetSelection`, `RepositoryFilter`, `resolve_dataset_selection` from `src/org_harvest/__init__.py`.
8. Tests: new `tests/test_selection.py`; new test classes in `tests/test_org_level.py` (`TestDatasetNarrowing`, `TestRepositoryFilter`) and `tests/test_repo_level.py` (`TestDatasetNarrowing`, `TestItemCap`, `TestCursorIsolationAcrossDatasets` — the DEV-8 regression test); new test classes in `tests/test_cli.py` (`TestRunCommandDatasetAndRepoOptions`, `TestPreflightAutoIncludedReporting`).

### Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `src/org_harvest/datasets/catalog.py` | Modify | Declare `repositories` as a dependency of every repo-level dataset (DEV-7) |
| `src/org_harvest/selection.py` | Create | `DatasetSelection`, `resolve_dataset_selection()`, `RepositoryFilter` |
| `src/org_harvest/harvest/org_level.py` | Modify | Dataset narrowing, repository filtering, unimplemented-dataset gaps |
| `src/org_harvest/harvest/repo_level.py` | Modify | Dataset narrowing, item cap, unimplemented-dataset gaps, cursor-reset fix (DEV-8) |
| `src/org_harvest/run.py` | Modify | Thread selection/filter/cap through; report auto-included datasets |
| `src/org_harvest/cli.py` | Modify | `run`'s new options; `preflight` uses shared selection resolution |
| `src/org_harvest/__init__.py` | Modify | Re-export Story 11's public API |
| `tests/test_selection.py` | Create | Selection resolution and repository filter unit tests |
| `tests/test_org_level.py` | Modify | Narrowing and repository-filter tests |
| `tests/test_repo_level.py` | Modify | Narrowing, item-cap, and cursor-isolation regression tests |
| `tests/test_cli.py` | Modify | `run`/`preflight` CLI option tests |

### Cross-Module Seams

| Write Module | Write Path | Read Module | Read Path | Data Description | Required Integration Test | AC Ref | Confirmed |
|---|---|---|---|---|---|---|---|
| `org_level.py` (`repositories` connection, filtered) | `<snapshot_dir>/repositories.ndjson` | `repo_level.py` (`_read_repositories`) | file read | The exact repository set Phase 2 fans out over | `test_org_level.py::TestRepositoryFilter` (write-side: filtered records on disk) + Story 5/6's pre-existing `_read_repositories` coverage (read-side: already proven to read whatever this file contains) | AC-2.8 | Confirmed — filtering at the single write point this seam already existed around (architecture.md, Decision 4) means no new read-side code was needed at all |
| `selection.py` (`resolve_dataset_selection`) | in-memory `DatasetSelection.names` | `run.py`, `cli.py`'s `preflight` command | function return value | The validated, dependency-closed dataset list a run/preflight actually uses | `test_run.py` (existing, unmodified — proves `run_snapshot()`'s default-`None` path is unaffected), new `TestRunCommandDatasetAndRepoOptions`/`TestPreflightAutoIncludedReporting` in `test_cli.py` | AC-2.1–AC-2.6 | Confirmed — this story owns both ends |

### Testing Approach

- **`tests/test_selection.py` (16 cases):** default selection (AC-2.2); narrowing (AC-2.1) and optional-dataset opt-in (AC-2.3); unknown-name (AC-2.4) and empty-selection (AC-2.5) rejection; dependency auto-inclusion and its reporting, including the two-hop `team_repositories` → `teams` + `repositories` case (AC-2.6); `RepositoryFilter`'s name-allowlist/archived/fork exclusion and combinations, plus its `is_noop` property (AC-2.8).
- **`tests/test_org_level.py` (+8 cases):** `TestDatasetNarrowing` — a subset selection fetches only those datasets and skips writing files for the rest; `None` matches pre-Story-11 behavior exactly (regression); `team_members` alone (as `resolve_dataset_selection` would hand it, already including `teams`) still reads the `teams.ndjson` it needs; selecting an unimplemented optional org-level dataset (`audit_log`) becomes a single gap, not silence. `TestRepositoryFilter` — archived exclusion, fork exclusion, name allowlist, and the no-filter case keeping everything, all verified against what's actually written to `repositories.ndjson`.
- **`tests/test_repo_level.py` (+6 cases):** `TestDatasetNarrowing` — mirrors org-level's narrowing/regression/unimplemented-gap cases for the repo-level tier. `TestItemCap` — a two-page fixture proves collection stops exactly at the cap without producing a gap, and that omitting the cap still collects every page (regression). `TestCursorIsolationAcrossDatasets` — a dedicated regression test for DEV-8: fetches a multi-page dataset then a second dataset for the same repository, asserting the second dataset's first request uses cursor `None`, not the first dataset's leftover cursor.
- **`tests/test_cli.py` (+7 cases):** `--datasets` resolution and pass-through, and that omitting it passes `None` (not an empty selection); `--repos`/`--exclude-archived`/`--exclude-forks` build the expected `RepositoryFilter`, and that omitting all three passes `None` rather than a live-but-inert filter object; `--max-items-per-collection` pass-through; the `run` command prints auto-included dependencies (AC-2.6) when `RunResult.auto_included_datasets` is non-empty; `preflight` reports the same for `team_members` (which auto-includes `teams`).
- No end-to-end test drives `run_snapshot()` through a real multi-dataset GraphQL exchange combining selection, filtering, and the cap simultaneously — Stories 5-10's suites, together with this story's narrower, targeted additions above, already exercise every one of those mechanisms individually against the real query/response mechanics; a combined end-to-end test would mostly re-assert wiring `test_run.py`'s existing mocked-collaborator tests already cover.

### Risks

- Selecting an optional-tier dataset this module has no connection spec for is deliberately turned into a single explicit "dataset not yet implemented" gap rather than an error or silence — an interpretation of the story's own Excluded-scope note (fetch implementation for optional datasets is out of scope) chosen to keep faith with the project's stated principle of never presenting an incomplete result as complete (spec.md Overview). A future story implementing one of these optional datasets simply adds its connection spec; this gap disappears on its own with no change needed here.
- DEV-8's cursor-reset fix changes `fetch_repo_dataset()`'s behavior for any *hypothetical* caller that relied on `_RepoState.cursor` surviving across separate calls with the same object instances outside of `fetch_repository_datasets()`'s own loop — no such caller exists today (the loop is the only caller), so this is theoretical, not an actual behavior change to any tested or documented seam.
- `item_cap` is enforced by simply not re-queuing a capped repository, leaving its checkpoint cursor at whatever page it stopped on (not marked specially as "capped" versus "genuinely finished") — a future resume (Story 12) or retry-gaps (Story 14) operation resuming this exact dataset would, by design, pick up more items past the cap if re-run without the same cap. This is consistent with AC-2.9's plain reading (a per-run collection limit, not a permanent one) but worth flagging for Story 12/14's own planning.

### Decisions Made

- **`resolve_dataset_selection()` lives in a new top-level `selection.py`, not inside `datasets/registry.py`** — it's selection *policy* (what a run should include) built on top of the registry's *data* (what depends on what), and Story 15's future library API surface benefits from that being a separately importable, single-purpose module rather than bundled with the registry mechanism itself.
- **The repository filter is applied at exactly one point** (`org_level.py`'s `repositories` connection write), not duplicated in `repo_level.py` — since Phase 2 already reads its repository list from Phase 1's output file (architecture.md, Decision 4), filtering upstream automatically and correctly narrows Phase 2 with no additional code there.
- **`item_cap` counts items written, not GraphQL pages fetched** — matching AC-2.9's literal wording ("cap the number of items collected"), so a page that would exceed the cap is truncated mid-page rather than being fetched-then-discarded or rejected wholesale.
- **DEV-7's dependency declarations and DEV-8's cursor fix were both necessary for this story's own acceptance criteria to hold**, not incidental cleanup — see each deviation's reasoning for why leaving either unaddressed would have made AC-2.6 or AC-2.9 behave incorrectly.
