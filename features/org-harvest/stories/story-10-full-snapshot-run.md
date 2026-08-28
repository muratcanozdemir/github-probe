# Story 10: Download a complete organization snapshot in one command

**Status:** APPROVED
**Depends On:** 3, 9
**UI Changes:** No

## User Story

As an engineer auditing a GitHub organization, I can run one command and receive a complete snapshot of that org so that I can analyze it offline without writing API code.

## Acceptance Criteria

- AC-1.1: Given valid credentials and an org login, a single command produces a snapshot on disk without further interaction.
- AC-1.2 (full): The snapshot includes every dataset in the default tier — every organization-level and repository-level dataset from Stories 5 and 6, run together end to end.
- AC-1.3: On completion the tool reports per-dataset record counts, elapsed time, GraphQL points consumed, REST requests consumed, and the number of rate-limit waits.
- AC-1.4: The command exits `0` when every selected dataset completed with no gaps and no scope restriction.
- AC-1.5: Each run writes a new snapshot directory named for the run's UTC start time; a completed snapshot is never modified by a later run, except by the retry-gaps operation of Story 14 acting on that specific snapshot.
- AC-1.6: The snapshot root defaults to `./snapshots` and is user-overridable; the layout is `<root>/<org-login-lowercased>/<utc-timestamp>/`.
- AC-1.7: Directory and file names derive only from the org login and dataset names, never from repository, team, or user names.
- AC-5.4: A run that completed with one or more gaps exits with the gaps status and prints a summary.

## Scope

**Included:**
- Wiring authentication (Story 1), pacing (Stories 2–3), preflight (Story 4), the organization-level fetch (Story 5), and the repository-level fetch (Story 7's resilience wrapping Story 6) into a single command that runs Phase 1 then Phase 2 and finalizes (Story 9) without further user interaction.
- The full, distinct exit-status set from the spec's FR-10: success, completed-with-gaps-or-scope-restriction, stopped-but-resumable, invalid usage/configuration, authentication/authorization failure, concurrent-run refusal, preflight-blocked, unexpected failure, and user interrupt.
- Reporting per-dataset counts, elapsed time, GraphQL and REST consumption, and rate-limit wait count on completion.
- The snapshot's directory naming, layout, and root-override behavior, and the rule that directory/file names never derive from repository, team, or user names.

**Excluded:**
- Restricting the run to a named dataset subset (Story 11) — this story's "complete snapshot" always means the full default tier.
- Resume behavior for an interrupted run (Stories 12–13) — this story defines what a clean full run looks like; resuming one that didn't finish is separate.
- Concurrent-run refusal's underlying claim/lock mechanism (Story 13) — this story only needs the resulting exit status to exist in the enumeration above.
- Library-level programmatic invocation (Story 15) — this story is the CLI's single-command surface.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 3's acceptance criteria are met: waits respect the credential lifetime and a configurable ceiling, and REST consumption is tracked separately.
- Story 9's acceptance criteria are met: a run's organization-level and repository-level data can be fetched, gaps recorded, finalized into Parquet, and described in a manifest and root index.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.

---

## Implementation Plan

### Implementation Steps

1. Instrument `src/org_harvest/ratelimit.py`'s `BudgetTracker` with a `total_consumed` property, accumulated from successive `remaining` observations under an unchanged `limit` — the source for AC-1.3's "GraphQL points consumed"/"REST requests consumed" figures. An upward jump in `remaining` (a window reset, including the optimistic reset `wait_if_exhausted` applies right after a wait) is deliberately not counted as negative consumption.
2. Instrument `src/org_harvest/transport.py`'s `Transport` with three new counters — `graphql_request_count` (incremented once per `send_graphql` call), `rest_request_count` (once per `send_rest` call), and `rate_limit_wait_count` (once per `send()` attempt where `wait_if_exhausted` actually waited) — exposed as read-only properties, matching the existing `total_wait_seconds` pattern.
3. Create `src/org_harvest/run.py`:
   - `ExitStatus` (`IntEnum`) — FR-10's full, documented exit-status enumeration, including `CONCURRENT_RUN_REFUSED` (Story 13's future claim/lock check — not implemented here, just reserved in the enumeration per this story's stated scope) and `USER_INTERRUPT = 130` (the conventional Unix SIGINT exit code).
   - `exit_status_for_error()` — a table mapping every `ErrorKind` to its documented `ExitStatus`, falling back to `UNEXPECTED_FAILURE` for anything not explicitly listed.
   - `RunResult` — exit status, snapshot directory (or `None` if none was created), manifest (or `None` if the run didn't reach finalization), elapsed seconds, and an optional message.
   - `run_snapshot()` — the orchestration: preflight (Story 4) gates the run (`--fail-fast` + any blocked dataset → `PREFLIGHT_BLOCKED` before creating anything); a fresh `<root>/<org-lower>/<utc-timestamp>/` directory and a single shared `CheckpointStore`/`SystemicFailureGuard` are created; Phase 1 (Story 5) then Phase 2 (Story 7 wrapping Story 6) run in sequence; Story 8's `finalize_snapshot()` converts NDJSON to Parquet; Story 9's `build_manifest()`/`write_manifest()`/`rebuild_root_index()` close out the run. Every `OrgHarvestError` from preflight or either phase is caught and mapped to its `RunResult.exit_status` rather than propagating — a `KeyboardInterrupt` is deliberately left to propagate to the caller.
4. Wire `main run ORG` into `src/org_harvest/cli.py`: shared credential options (reused from `preflight`/`auth-check`), `--snapshot-root` (default `./snapshots`), `--fail-fast`; builds the credential provider and `Transport`, calls `run_snapshot()`, prints the AC-1.3 summary (per-dataset counts, elapsed time, GraphQL points/requests, REST requests, rate-limit waits, gap count, scope-restriction warning, snapshot path), and exits with `result.exit_status`. A `KeyboardInterrupt` around the async call is caught and mapped to `ExitStatus.USER_INTERRUPT`.
5. Re-export `ExitStatus`, `RunResult`, `exit_status_for_error`, `run_snapshot` from `src/org_harvest/__init__.py`.
6. Tests: `tests/test_run.py` (orchestration, mocking `run_preflight`/`fetch_organization_directory`/`fetch_repository_datasets`/`finalize_snapshot`), new counter tests in `tests/test_ratelimit.py`/`tests/test_transport.py`, and new `run`-command tests in `tests/test_cli.py`.

### Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `src/org_harvest/ratelimit.py` | Modify | `BudgetTracker.total_consumed` |
| `src/org_harvest/transport.py` | Modify | Request/wait counters for AC-1.3 |
| `src/org_harvest/run.py` | Create | `ExitStatus`, `RunResult`, `run_snapshot()` orchestration |
| `src/org_harvest/cli.py` | Modify | `run` command |
| `src/org_harvest/__init__.py` | Modify | Re-export Story 10's public API |
| `tests/test_ratelimit.py` | Modify | `BudgetTracker.total_consumed` tests |
| `tests/test_transport.py` | Modify | Request/wait counter tests |
| `tests/test_run.py` | Create | Orchestration unit tests |
| `tests/test_cli.py` | Modify | `run` command tests |

### Cross-Module Seams

| Write Module | Write Path | Read Module | Read Path | Data Description | Required Integration Test | AC Ref | Confirmed |
|---|---|---|---|---|---|---|---|
| `run.py` (`run_snapshot`) | `<snapshot_dir>/manifest.json`, `<org_dir>/index.json` (via Story 9's `write_manifest`/`rebuild_root_index`) | Story 12/13 (resume discovery), Story 14 (retry-gaps), a future listing command | `read_manifest`/`is_snapshot_complete`/`index.json` | A completed run's on-disk record | `test_run.py::TestHappyPath` (asserts `is_snapshot_complete()` and `index.json` presence directly, since the reading stories don't exist yet) | AC-1.5, AC-1.6, AC-8.7, AC-8.8 | Confirmed (this story owns the write side and Story 9's already-tested read side; Stories 12/13/14 are the not-yet-built external callers) |
| `run.py` (`run_snapshot`) | `<snapshot_dir>/checkpoint.json` (via Story 5/6's shared `CheckpointStore`) | Story 12/13 (resume) | `CheckpointStore.load` | Per-dataset status and cursors from a run that stopped before finalizing | `test_run.py::TestPhaseFailure` (asserts the checkpoint file exists after a phase raises) | AC-4.1 (already covered by Story 5's tests) | Confirmed for the write side; Story 12/13 is the not-yet-built reader |
| `cli.py` (`run` command) | process exit code | An operator's shell/CI script | `$?` / CI step status | FR-10's exit-status contract | `test_cli.py::TestRunCommand` (asserts `invocation.exit_code` for each mapped status) | FR-10 | Confirmed — this story is both ends of this seam |

### Testing Approach

- **`tests/test_ratelimit.py` (`TestBudgetTrackerConsumption`, 4 cases):** no consumption from a single observation; accumulates across decreasing observations; an upward jump (reset) is not counted; a limit change is not counted.
- **`tests/test_transport.py` (`TestRequestAndWaitCounters`, 2 cases):** `send_graphql`/`send_rest` each count their own calls independently; `rate_limit_wait_count` increments only on an actual wait, not on every budget check.
- **`tests/test_run.py` (22 cases):**
  - `TestExitStatusMapping` — every `ErrorKind` maps to its documented status (parametrized); every `ExitStatus` value is distinct, including the two not reachable through `exit_status_for_error` (`CONCURRENT_RUN_REFUSED`, `USER_INTERRUPT`).
  - `TestPreflightGating` — a preflight failure maps to its exit status with no snapshot directory created; `--fail-fast` with a blocked dataset stops before creating anything; a blocked dataset without `--fail-fast` proceeds into both phases (AC-6.4).
  - `TestHappyPath` — a clean run exits `SUCCESS` and produces a complete, readable snapshot (AC-1.4); the directory layout is `<root>/<org-lower>/<timestamp>/` (AC-1.6); the root index is rebuilt (AC-8.8); a dataset gap, and scope restriction alone, each force `COMPLETED_WITH_GAPS` (AC-5.4).
  - `TestPhaseFailure` — a Phase 1 or Phase 2 failure maps to `STOPPED_RESUMABLE`, leaves a checkpoint on disk, and writes no manifest (`is_snapshot_complete()` is `False`).
  - `TestConsumptionStats` — the manifest's `consumption` figures reflect real `Transport` counters, seeded through actual `send_graphql`/`send_rest` calls before the (collaborator-mocked) orchestration runs.
- **`tests/test_cli.py` (`TestRunCommand`, 6 cases):** conflicting credentials rejected before any network call; a successful `RunResult` prints the full AC-1.3 summary and exits `0`; a gapped result reports the gap count and exits `1`; a preflight-blocked result exits `6` with its message; a `KeyboardInterrupt` from `_do_run` exits `130`; `--snapshot-root` is threaded through to the orchestrator.
- Orchestration tests mock `run_preflight`/`fetch_organization_directory`/`fetch_repository_datasets`/`finalize_snapshot` directly rather than re-mocking all ~26 GraphQL query shapes those already-tested modules issue — Stories 4-9's own test suites already cover that mechanics in full; this story's tests focus on what it actually contributes: exit-status mapping, directory/manifest/index wiring, and consumption-stat aggregation.

### Risks

- `graphql_request_count`/`rest_request_count` count logical calls to `send_graphql`/`send_rest`, not raw HTTP attempts — a call that took several retries to succeed is still one "request" in the summary. This matches how an operator thinks about "how many GraphQL requests did this run make" more closely than a retry-inflated count would, but means AC-1.3's reported figure is not literally "sockets opened."
- `BudgetTracker.total_consumed`'s "don't count an upward jump" rule slightly undercounts true consumption immediately after a rate-limit-triggered wait (the cost of the first request after a reset is folded into the *next* observed delta, not attributed to the request that actually caused it) — accepted as a reasonable approximation given GitHub exposes no direct "points consumed this response" figure outside `rateLimit.cost`, which the fetch engines don't currently thread through to `Transport`.
- `run_snapshot()`'s `snapshot_dir.mkdir(parents=True, exist_ok=True)` tolerates a same-second collision rather than raising, since Story 10 explicitly excludes concurrent-run refusal (Story 13's job); a caller relying on strict uniqueness before Story 13 lands should be aware two runs starting in the same UTC second against the same org would currently share a directory.

### Decisions Made

- **`run_snapshot()` always fetches the complete default tier** — no `dataset_names` parameter — since dataset narrowing is explicitly Story 11's job and `fetch_organization_directory`/`fetch_repository_datasets` themselves have no narrowing hook yet either.
- **`--fail-fast` only inspects `any_blocked`, not degraded verdicts** — a `DEGRADED` verdict (permissions merely unknowable, e.g. a pre-minted token) is not the "will definitely fail" condition EC-20 describes; only a confirmed missing permission (`BLOCKED`) is worth aborting a whole run over before spending any budget.
- **A blocked dataset without `--fail-fast` needs no new code to become a gap** — the fetch engines already turn a GraphQL permission error on a specific field into a per-resource gap via their existing `_error_path`/`_record_errors` machinery (Stories 5-7); Story 10 doesn't narrow the query, so the natural GraphQL response for a genuinely inaccessible field already produces the gap EC-20 describes.
- **Consumption-stat computation lives entirely in `Transport`/`BudgetTracker`**, not `run.py` — `run_snapshot()` only reads the already-accumulated counters at the end of a run, keeping the actual instrumentation next to the code issuing the requests rather than duplicated in the orchestrator.
- **`ExitStatus` is an `IntEnum` with stable, hand-assigned values** (not auto-numbered) — an operator's CI script may branch on the numeric exit code, so a future addition to the enum must append rather than renumber.
