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

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/checkpoint.py` — add `repository_exclude_archived: bool = False` and `repository_exclude_forks: bool = False` to `CheckpointState`, alongside the existing `repository_filter` (name allowlist) field, with `to_json`/`from_json` round-tripping and backward-compatible `.get(..., False)` defaults for a checkpoint written before this story. `CheckpointStore.create()` gains matching keyword parameters.
2. `src/org_harvest/timeutil.py` — add `parse_compact_utc(value) -> datetime`, the inverse of `utc_now_compact()`, so a snapshot's age can be computed from its directory name (AC-4.10) rather than a filesystem mtime an unrelated copy or backup could change.
3. Create `src/org_harvest/interrupt.py`: `InterruptGuard`, a context manager installing a SIGINT handler for its lifetime. First signal sets `requested` without raising; second signal restores the previous handler and raises `KeyboardInterrupt` immediately (AC-4.11). Degrades to a no-op off the main thread (`signal.signal()`'s own restriction) rather than crashing a future non-CLI caller (Story 15).
4. Create `src/org_harvest/lock.py`: `OrgClaim`/`ClaimConflict`, a per-org claim file (`<org_dir>/.run.lock`) carrying the claiming process's PID and a timestamp. `OrgClaim.acquire()` refuses (`ClaimConflict`) if an existing claim's PID is still alive (`os.kill(pid, 0)` probe), or silently reclaims it (`OrgClaim.reclaimed_stale = True`) if not (EC-12). Scoped per org directory, so a claim never blocks a different org sharing the same `--snapshot-root` (EC-13).
5. `src/org_harvest/harvest/org_level.py` and `repo_level.py` — both harvester classes gain a required `interrupt: InterruptGuard` constructor parameter (public functions default it to a fresh `InterruptGuard()`, matching the `systemic_guard`/DEV-6 precedent) and an `interrupted` property. Every pagination loop (org-level connections, per-team connections, repo-level batches) checks `interrupted` immediately after its per-page checkpoint write and breaks without marking the dataset `"complete"` if set, so the stored cursor is real and resumable rather than a completion marker. The outer per-dataset loops in both `fetch_organization_directory()` and `fetch_repository_datasets()` also check `interrupted` between datasets, so no *new* dataset starts once an interrupt has been requested, even though the one already in flight finishes its current page first.
6. Create `src/org_harvest/resume.py` — no change this story (already exists from Story 12); referenced by `run.py`'s discovery calls, unchanged.
7. `src/org_harvest/run.py` — substantially restructured:
   - `run_snapshot()` gains `stale_after_days: float = 7.0` and `allow_stale_resume: bool = False`.
   - The resume-target `candidate` is now resolved *before* preflight (moved earlier than Story 12 had it), so this story's new refusal categories — AC-4.8, AC-4.9, AC-4.10 — all fail before any network call, extending the same principle Story 11 established for dataset-selection errors.
   - When `candidate is not None`: `CheckpointStore.resume()` is wrapped in a `try/except (OSError, ValueError, KeyError, TypeError)` — an unreadable or malformed checkpoint refuses with a "start fresh with --force-fresh" message (AC-4.9). A loaded checkpoint's `schema_version` is compared against `CHECKPOINT_SCHEMA_VERSION` (AC-4.9). `_resume_compatibility_error()` compares org (case-insensitively), dataset selection (as sets), and the full repository filter (names + both exclude flags) against the current request, refusing with a message naming what differs (AC-4.8). `_snapshot_age_days()` (parsing the directory name) is compared against `stale_after_days`, refusing unless `allow_stale_resume` (AC-4.10) — a directory name that doesn't parse as a compact timestamp at all (a hand-named or externally-copied directory) skips this check rather than blocking on an unknowable age.
   - `OrgClaim.acquire(org_dir)` runs next; a `ClaimConflict` returns `ExitStatus.CONCURRENT_RUN_REFUSED` immediately (EC-13). Everything from preflight through finalize now runs inside `with claim:`, so the claim releases on every return path — success, every error branch, and the new interrupted-return path — via the context manager's `__exit__`, not scattered manual `.release()` calls.
   - An `InterruptGuard` wraps the two fetch-phase calls (`with interrupt:`); after each phase, `if interrupt.requested:` returns a `RunResult(ExitStatus.USER_INTERRUPT, ...)` with a message naming the exact `org-harvest run <org> --resume <snapshot>` command to continue, and skips `finalize_snapshot()`/`build_manifest()`/`write_manifest()` entirely — so no manifest exists afterward, keeping the snapshot's "checkpoint but no manifest = incomplete" signature (Decision 5) intact for the next resume to find.
   - `RunResult` gains `reclaimed_stale_claim: bool = False`, threaded through every return path from claim acquisition onward.
   - `DEV-12`: the fresh-checkpoint-creation branch now actually passes `repository_filter`/`repository_exclude_archived`/`repository_exclude_forks` derived from the run's real `repository_filter` argument to `CheckpointStore.create()` — previously never wired at all.
8. `src/org_harvest/cli.py` — `run` command gains `--stale-after-days FLOAT` (default 7.0) and `--allow-stale-resume`, threaded through `_do_run`/`run_snapshot`. `_print_run_result()` prints a `warning: reclaimed a stale run claim...` line when `RunResult.reclaimed_stale_claim` is set, and gains a dedicated branch for `ExitStatus.USER_INTERRUPT` that prints the resume message as a plain notice (no `error:` prefix, not stderr) since a graceful interrupt is expected behavior, not a failure.
9. Tests: `tests/test_interrupt.py`, `tests/test_lock.py` (new); `TestParseCompactUtc` added to new `tests/test_timeutil.py`; `TestCheckpointResume`'s sibling additions in `tests/test_checkpoint.py` for the two new fields; `TestInterrupt` in both `tests/test_org_level.py` and `tests/test_repo_level.py`; `TestResumeSafetyGuards`, `TestConcurrentRun`, `TestInterruptOrchestration` in `tests/test_run.py`; `TestRunCommandResumeSafetyOptions` in `tests/test_cli.py`.

### Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `src/org_harvest/checkpoint.py` | Modify | `repository_exclude_archived`/`repository_exclude_forks` fields |
| `src/org_harvest/timeutil.py` | Modify | `parse_compact_utc()` |
| `src/org_harvest/interrupt.py` | Create | `InterruptGuard` — cooperative then immediate Ctrl-C |
| `src/org_harvest/lock.py` | Create | `OrgClaim`/`ClaimConflict` — per-org concurrent-run claim |
| `src/org_harvest/harvest/org_level.py` | Modify | Interrupt checks in every pagination/dataset loop |
| `src/org_harvest/harvest/repo_level.py` | Modify | Interrupt checks in the batch queue and dataset loop |
| `src/org_harvest/run.py` | Modify | Resume compatibility/staleness guards, claim, interrupt orchestration |
| `src/org_harvest/cli.py` | Modify | `--stale-after-days`/`--allow-stale-resume` flags, warning/interrupt output |
| `tests/test_interrupt.py` | Create | `InterruptGuard` unit tests |
| `tests/test_lock.py` | Create | `OrgClaim`/`ClaimConflict` unit tests |
| `tests/test_timeutil.py` | Create | `parse_compact_utc()`/`utc_now_compact()` unit tests |
| `tests/test_checkpoint.py` | Modify | New field round-trip and back-compat tests |
| `tests/test_org_level.py` | Modify | `TestInterrupt` |
| `tests/test_repo_level.py` | Modify | `TestInterrupt` |
| `tests/test_run.py` | Modify | `TestResumeSafetyGuards`, `TestConcurrentRun`, `TestInterruptOrchestration` |
| `tests/test_cli.py` | Modify | `TestRunCommandResumeSafetyOptions` |

### Cross-Module Seams

| Write Module | Write Path | Read Module | Read Path | Data Description | Required Integration Test | AC Ref | Confirmed |
|---|---|---|---|---|---|---|---|
| `lock.py` (`_write_claim`) | `<org_dir>/.run.lock` | `lock.py` (`_read_claim`, a later `acquire()` call — possibly a different process) | file read | Claiming process's PID and claim timestamp | `test_lock.py`'s `TestAcquire` (write-then-read within one process; a real second-process race is out of this story's test scope, same as any file-lock scheme) | FR-9, EC-12, EC-13 | Confirmed |
| `checkpoint.py` (`CheckpointStore.create`, now with filter fields) | `<snapshot_dir>/checkpoint.json` | `run.py` (`_resume_compatibility_error`, on a later resume attempt) | file read via `CheckpointStore.resume()` | The original run's org, dataset selection, and full repository filter | `TestResumeSafetyGuards` in `test_run.py` (write via a real run, or a direct `CheckpointStore.create()` fixture; read via a subsequent `run_snapshot()` call) | AC-4.8 | Confirmed — this story owns both ends |
| `org_level.py`/`repo_level.py` (`interrupted` checks) | in-memory `InterruptGuard.requested` | same modules, next loop iteration | in-process read | Whether the user has asked to stop | `TestInterrupt` in both harvest test files (a test handler flips `requested` mid-fetch, asserting the next page is never requested and the dataset isn't marked complete) | AC-4.11 | Confirmed |

### Testing Approach

- **`tests/test_interrupt.py` (6 cases):** not-yet-requested is `False`; a first signal sets `requested` without raising; a second raises `KeyboardInterrupt` immediately; the previous SIGINT handler is restored on exit, and after a second-signal raise too; a real `os.kill(os.getpid(), SIGINT)` is delivered to the installed handler (not just the internal method called directly), proving the signal wiring itself works, not just the state machine.
- **`tests/test_lock.py` (8 cases):** a fresh acquire writes the lock file; a second acquire by the same (therefore alive) process is a conflict (EC-13); releasing lets a second acquire succeed; the context-manager form releases on exit; a claim left by a PID that can't exist (`2**30`) is reclaimed with `reclaimed_stale=True` (EC-12); an unreadable/malformed claim file is treated as no claim at all; two different orgs' claims never conflict with each other; releasing twice (file already gone) doesn't raise.
- **`tests/test_timeutil.py` (7 cases, new file):** `utc_now_compact()` matches its documented format; `utc_now_iso()` parses as ISO-8601; `parse_compact_utc()` round-trips through `utc_now_compact()`, parses a known value's fields correctly, returns a UTC-aware result, and raises `ValueError` on a malformed value.
- **`tests/test_checkpoint.py` (+3 cases):** the two new exclude flags round-trip through save/load; they default to `False` when omitted at creation; a checkpoint file missing the two new keys entirely (simulating one written before this story) still loads with `False` defaults rather than raising `KeyError`.
- **`tests/test_org_level.py`/`tests/test_repo_level.py` (+2 cases each, `TestInterrupt`):** a handler sets `interrupt.requested = True` while serving the first of two pages — the second page is never requested, the record from the first page is still written and counted, the real (non-`CURSOR_DONE`) cursor is stored, and `dataset_status` is never marked `"complete"`. A second test drives the full `fetch_organization_directory()`/`fetch_repository_datasets()` entry point across two datasets, setting the interrupt while serving the first, and asserts the first dataset's outcome is present while the second was never started at all.
- **`tests/test_run.py` (+16 cases across three new classes):**
  - `TestResumeSafetyGuards`: org mismatch, dataset-selection mismatch, and repository-filter mismatch each refuse with `INVALID_USAGE` and a message naming what differs (AC-4.8); a matching selection and filter proceeds to actually resume; an unreadable checkpoint and an incompatible `schema_version` each refuse with a "start fresh"/"incompatible" message (AC-4.9); a snapshot older than the default 7-day window refuses, `allow_stale_resume=True` overrides that refusal, and a custom `stale_after_days` is honored against a snapshot that's stale only under the tighter threshold (AC-4.10).
  - `TestConcurrentRun`: a live claim refuses a second run (EC-13); a claim on a *different* org never blocks this one (EC-13); a claim left by a dead PID is reclaimed with `RunResult.reclaimed_stale_claim=True` reported (EC-12); the claim file is gone after both a successful run and a run where a phase raises (proving release happens on every path, not just the happy one).
  - `TestInterruptOrchestration`: an interrupt raised during Phase 1 stops before Phase 2 ever runs, skips `finalize_snapshot()`, returns `ExitStatus.USER_INTERRUPT` with a `--resume`-naming message and no manifest, and leaves no `manifest.json` file behind; an interrupt during Phase 2 likewise skips finalize; the org claim is released even after a cooperative interrupt.
- **`tests/test_cli.py` (+5 cases, `TestRunCommandResumeSafetyOptions`):** `--stale-after-days`/`--allow-stale-resume` pass through correctly; the defaults (7.0, `False`) apply when omitted; `reclaimed_stale_claim` prints its warning line; `CONCURRENT_RUN_REFUSED` exits with its own distinct status code; a `USER_INTERRUPT` result prints its resume message as a plain notice with no `error:` prefix.
- No test exercises two real OS processes racing on the same claim file — that would require actually spawning a second process and is exactly the kind of flaky, environment-sensitive test this codebase's existing conventions (e.g. Story 12's note on not scripting an actual process kill) avoid; the claim mechanism's correctness is instead verified at the unit level (`test_lock.py`) plus the liveness-probe logic (`_is_pid_alive`) being exercised against both a real, alive PID (this test process's own) and a synthetically dead one.

### Risks

- The staleness check's "unparseable snapshot name skips the check" behavior means a user who manually renames a snapshot directory (or restores one from a differently-named backup) loses staleness protection entirely for that snapshot, silently. This is a deliberate choice (an unknown age can't be judged stale, so it isn't) rather than a conservative default in either direction — worth revisiting if it proves surprising in practice.
- `_is_pid_alive()`'s `PermissionError` branch treats a claim as alive when the probing process can't signal the claiming one (e.g., a different user's process, in some multi-user snapshot-root setups) — the safe direction to be wrong in (never a false reclaim), but it does mean a claim from a differently-privileged, actually-dead process is never automatically reclaimed; an operator would need to remove the `.run.lock` file by hand in that specific scenario.
- Interrupt checks stop *starting new work* at page/dataset granularity but do not attempt to cancel an already-in-flight HTTP request faster than it would otherwise complete — a first Ctrl-C during a very slow single request still waits for that request's own timeout/response before the cooperative check is even reached. This matches AC-4.11's literal wording ("finishes the in-flight page") but is worth noting as the practical latency a first interrupt can have in the worst case; a second Ctrl-C remains available for genuinely immediate termination.

### Decisions Made

- **Resume-target resolution and all of AC-4.8/AC-4.9/AC-4.10's checks now happen before preflight**, extending Story 11's "fail before any network call" principle to every new refusal category this story adds — a resume that's unsafe for purely local, filesystem-known reasons should never cost a network round-trip to discover.
- **The org claim wraps everything from preflight onward in a single `with claim:` block**, rather than calling `.release()` manually on each return path — every one of this function's many early returns (errors, interrupts, refusals) benefits from the context manager's guaranteed cleanup with no risk of a future return statement forgetting to release it.
- **`InterruptGuard` and `OrgClaim` are separate, independent primitives**, not combined into one "run session" object — a claim's lifetime spans the whole `run_snapshot()` call while the interrupt guard's SIGINT handler only needs to be active around the two fetch phases (preflight and finalize are comparatively quick and have nothing mid-operation to interrupt cooperatively), and keeping them separate lets each be unit-tested and reasoned about on its own.
- **A repository-filter mismatch check compares the *entire* filter (names plus both exclude flags) as a single pass/fail**, rather than allowing, say, a matching name-list with a different exclude-archived setting to partially proceed — any difference at all means the original run's cursors and gap ledger were built against a different repository set, which AC-4.8 treats as unsafe regardless of which specific piece changed.
- **DEV-12's fix (actually wiring `repository_filter` into fresh-checkpoint creation) was made in this story rather than deferred**, since AC-4.8's repository-filter comparison is meaningless without it — the same reasoning Story 11/12 used for DEV-7/DEV-8/DEV-11's "found a necessary precondition, fixed it now" pattern.
