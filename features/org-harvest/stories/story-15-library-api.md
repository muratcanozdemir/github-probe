# Story 15: Use org-harvest as a library

**Status:** APPROVED
**Depends On:** 10
**UI Changes:** No

## User Story

As a developer building a data pipeline, I can drive the crawler from my own Python code so that I can embed it in a larger pipeline rather than shelling out to the CLI.

## Acceptance Criteria

- AC-9.1: The package exposes its public types and entry points from the package root.
- AC-9.2: A caller can run a harvest programmatically and receive a structured result including counts, gaps, and consumption statistics.
- AC-9.3: Credentials are passed explicitly as parameters; the library never reads them from the environment on its own.
- AC-9.4: Callers can observe progress as the run proceeds rather than only at the end.
- AC-9.5: The library surfaces one documented exception type for its failures.

## Scope

**Included:**
- Flat re-exports of every public type and entry point from the package root, so callers never need to import from an internal submodule.
- A programmatic entry point that runs a harvest (equivalent to Story 10's single command) and returns a structured result carrying per-dataset counts, gaps, and consumption statistics.
- Confirming the library layer never reads credentials from the environment itself — that is exclusively the CLI's responsibility (Story 1), with the library always taking credentials as explicit parameters.
- A progress-observation mechanism so a caller can react during a run, not only after it completes.
- One documented exception type covering every library-raised failure.

**Excluded:**
- The CLI's own credential input surface (environment variables, arguments) — that's Story 1; this story only confirms the library underneath never bypasses explicit parameters.
- Any new fetch, resume, retry-gaps, or output behavior — this story exposes existing capability programmatically, it doesn't add new harvesting behavior.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 10's acceptance criteria are met: a complete organization snapshot can be produced end to end, with counts, gaps, and consumption statistics available at completion.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/progress.py` (new) — `ProgressEventKind` (`PHASE_STARTED`, `DATASET_COMPLETE`, `RATE_LIMIT_WAIT`, `PHASE_COMPLETE`), the frozen `ProgressEvent` dataclass (`kind`, `message`, plus optional `phase`/`dataset`/`record_count`/`gap_count`/`wait_seconds`), and `ProgressCallback = Callable[[ProgressEvent], None]`. This is new capability — nothing in the codebase reported progress during a run before this story (confirmed by grep before starting).
2. `src/org_harvest/transport.py` — `Transport` gains `set_wait_callback(callback)`, a setter (not a constructor parameter, since `run_snapshot()` receives an already-constructed `Transport` from its caller) storing `self._on_wait`; `send()` invokes it with the wait duration exactly where it already increments `_rate_limit_wait_count`.
3. `src/org_harvest/harvest/org_level.py` / `repo_level.py` — both `fetch_organization_directory()`/`fetch_repository_datasets()` gain `on_progress: ProgressCallback | None = None`; every `outcomes.append(...)` call site is refactored into a local `_emit(outcome)` closure that appends and, when `on_progress` is set, emits a `DATASET_COMPLETE` event (dataset name, record count, gap count) — including the "not yet implemented" gap path, so a selected-but-unimplemented dataset still reports as a (gapped) completion rather than silently producing nothing.
4. `src/org_harvest/run.py` — `run_snapshot()` gains `on_progress: ProgressCallback | None = None` as its last parameter. An `_emit_phase(kind, phase)` closure (defined at the top of the function body, before dataset-selection resolution or resume-target lookup, so it's in scope for every phase including preflight) wraps `run_preflight()`, the Phase 1 (`fetch_organization_directory`) call, the Phase 2 (`fetch_repository_datasets`) call, and `finalize_snapshot()` with matching `PHASE_STARTED`/`PHASE_COMPLETE` events — four phases: `"preflight"`, `"phase1"`, `"phase2"`, `"finalize"`. `on_progress` is also threaded through to both fetch calls' own `on_progress` parameter, and — when set — installed on `transport` via `set_wait_callback()` right after the org claim is acquired, translating each wait into a `RATE_LIMIT_WAIT` event.
5. `src/org_harvest/cli.py` — `run` command gains a `--verbose` flag; when set, `_do_run()` is given `on_progress=_print_progress_event`, a small helper that echoes `event.message` to stderr (so it never mixes with the result summary on stdout). Without `--verbose`, `on_progress=None` — zero behavioral change from Story 14.
6. `src/org_harvest/__init__.py` — audited against every module Stories 1-15 added and completed the flat re-export: added `CURSOR_DONE`, `InterruptGuard`, `OrgClaim`/`ClaimConflict`, `find_newest_incomplete_snapshot`/`find_named_snapshot`, `retry_gaps`/`RetryResult`, and `ProgressEvent`/`ProgressEventKind`/`ProgressCallback` to both the imports and `__all__` (AC-9.1). `timeutil.py`'s helpers (`utc_now_iso`, `utc_now_compact`, `parse_compact_utc`) were deliberately left un-exported — internal timestamp plumbing, not a public type or entry point a library caller needs.
7. Verified (no code changes needed): AC-9.2 is already satisfied by `RunResult`/`Manifest` (dataset counts, gaps, consumption stats) from Story 9/10; AC-9.3 by grepping the entire `src/org_harvest/` tree for `os.environ`/`getenv` and finding none outside `cli.py`'s `click.option(envvar=...)` declarations, which are CLI-only; AC-9.5 by `OrgHarvestError`/`ErrorKind` already being the single exception type every documented failure path raises.
8. Tests: new `tests/test_progress.py`, new `tests/test_init.py`; additions to `tests/test_transport.py` (`TestWaitCallback`), `tests/test_org_level.py`/`tests/test_repo_level.py` (`TestProgress` in each), `tests/test_run.py` (`TestProgress`), and `tests/test_cli.py` (`TestRunCommandVerbose`).

### Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `src/org_harvest/progress.py` | Create | `ProgressEvent`, `ProgressEventKind`, `ProgressCallback` |
| `src/org_harvest/transport.py` | Modify | `set_wait_callback()`, wired into `send()`'s existing wait path |
| `src/org_harvest/harvest/org_level.py` | Modify | `on_progress` param, `_emit()` helper |
| `src/org_harvest/harvest/repo_level.py` | Modify | `on_progress` param, `_emit()` helper |
| `src/org_harvest/run.py` | Modify | `on_progress` param; phase-boundary events around all four phases; wires dataset/wait callbacks through |
| `src/org_harvest/cli.py` | Modify | `--verbose` flag, `_print_progress_event()` |
| `src/org_harvest/__init__.py` | Modify | Completed flat re-exports for Stories 12-15 |
| `tests/test_progress.py` | Create | `ProgressEvent`/`ProgressEventKind`/`ProgressCallback` unit tests |
| `tests/test_init.py` | Create | Re-export completeness tests (AC-9.1) |
| `tests/test_transport.py` | Modify | `TestWaitCallback` |
| `tests/test_org_level.py` | Modify | `TestProgress` |
| `tests/test_repo_level.py` | Modify | `TestProgress` |
| `tests/test_run.py` | Modify | `TestProgress` |
| `tests/test_cli.py` | Modify | `TestRunCommandVerbose` |

### Cross-Module Seams

| Write Module | Write Path | Read Module | Read Path | Data Description | Required Integration Test | AC Ref | Confirmed |
|---|---|---|---|---|---|---|---|
| `transport.py` (`send()`'s wait path) | in-process callback invocation | `run.py` (the lambda installed via `set_wait_callback`) | in-process callback invocation | The wait duration, translated into a `RATE_LIMIT_WAIT` `ProgressEvent` | `TestProgress::test_rate_limit_wait_events_are_wired_to_the_transport` in `tests/test_run.py` | AC-9.4 | Confirmed |
| `harvest/org_level.py` / `harvest/repo_level.py` (`_emit()`) | in-process callback invocation | `run.py` (`on_progress=on_progress` passed straight through to both fetch calls) | in-process callback invocation | Per-dataset `DATASET_COMPLETE` events, unmodified, from either phase | `TestProgress::test_dataset_complete_events_from_each_phase_pass_through` in `tests/test_run.py` | AC-9.4 | Confirmed |
| `run.py` (`on_progress` parameter) | in-process callback invocation | `cli.py` (`_print_progress_event`, installed only under `--verbose`) | in-process callback invocation | Every event's `message`, printed to stderr | `TestRunCommandVerbose::test_verbose_prints_progress_events_to_stderr` in `tests/test_cli.py` | AC-9.4 | Confirmed |

### Testing Approach

- **`tests/test_progress.py` (9 cases):** `ProgressEventKind`'s four values; `ProgressEvent`'s optional-field defaults, per-kind field population, and immutability; `ProgressCallback` accepting a plain function.
- **`tests/test_transport.py` (+3 cases, `TestWaitCallback`):** the callback receives the exact wait duration when a wait actually happens; it's never invoked when no wait happens; `set_wait_callback(None)` clears a previously-set callback without affecting `rate_limit_wait_count`.
- **`tests/test_org_level.py` / `tests/test_repo_level.py` (+3 cases each, `TestProgress`):** one `DATASET_COMPLETE` event per selected dataset with the right name/count/gap-count and a non-empty message; no callback given means no error and normal results; a selected-but-unimplemented dataset (`audit_log` for org-level, `workflow_runs` for repo-level) still emits exactly one `DATASET_COMPLETE` event carrying `gap_count=1`.
- **`tests/test_run.py` (+4 cases, `TestProgress`):** the exact eight-event `(kind, phase)` sequence across all four phases on a clean run; dataset-complete events from custom fakes (simulating what the real fetch functions do) pass through unmodified; the wait-callback installed on `transport` produces a correctly-shaped `RATE_LIMIT_WAIT` event when invoked; no `on_progress` means `transport.set_wait_callback` is never called at all.
- **`tests/test_cli.py` (+3 cases, `TestRunCommandVerbose`):** `--verbose` passes a non-`None` `on_progress` to `_do_run`; without it, `on_progress` is `None`; a `--verbose` run's progress events land on stderr, not stdout.
- **`tests/test_init.py` (7 cases):** every name in `__all__` actually resolves on the module (catches a typo'd re-export); no duplicate names; spot-checks that specific Story 1-15 additions — the core entry point, credential providers, the one exception type, structured result types, and every Story 12-15 addition — are present.
- AC-9.2, AC-9.3, and AC-9.5 needed no new tests: AC-9.2/AC-9.5 are already covered by Story 9/10's existing `RunResult`/`Manifest`/`OrgHarvestError` test suites, and AC-9.3 was verified by inspection (a `grep` for `os.environ`/`getenv` across `src/org_harvest/`, confirming zero hits outside `cli.py`).

### Risks

- `DATASET_COMPLETE` granularity is per-dataset, not per-repository — a caller watching a long Phase 2 run on a large org sees nothing between "phase2 started" and the first dataset's completion across *all* repositories, which for a big org could be a long quiet stretch. Threading progress callbacks through `repo_level.py`'s recursive per-repository-batch fan-out (`_run_batch()`) would be a materially larger change than this story's Excluded scope ("no new fetch/resume/retry/output behavior — exposes existing capability programmatically") supports, so it's deliberately out of scope here.
- A callback that raises is not caught anywhere in the chain (`transport.py`, `org_level.py`/`repo_level.py`, or `run.py`) — this is documented explicitly in `progress.py`'s module docstring as intentional (a raising callback stops the run exactly as any other unhandled exception would), but it does mean a buggy caller-supplied callback can turn a clean run into an unexpected crash rather than a `RunResult`.

### Decisions Made

- **Dataset-level (not per-repository) progress granularity** — matches this story's Excluded scope; see Risks above.
- **`Transport.set_wait_callback()` as a setter, not a constructor parameter** — `run_snapshot()` never constructs its own `Transport` (it receives one from its caller, CLI or library), so a setter is the only way to attach the callback after the fact without changing `Transport.__init__`'s signature for every existing caller.
- **`timeutil.py`'s helpers are not re-exported from the package root** — they're internal timestamp plumbing (compact-format parsing/formatting used by checkpoint/resume/lock internals), not a type or entry point a library caller drives a harvest with; re-exporting them would pad the public surface with implementation detail AC-9.1 doesn't ask for.
- **DEV-15**: the preflight phase was initially missing from `run.py`'s phase-event wiring despite `progress.py`'s docstring already committing to it — fixed by moving the `_emit_phase` closure's definition earlier in the function and wrapping `run_preflight()` with it. See `deviations.md`.
