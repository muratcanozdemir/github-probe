"""Wires Stories 1-9's already-tested building blocks into the single
`run` command AC-1.1 promises: preflight (Story 4) gates the run, Phase 1
(Story 5) then Phase 2 (Story 7's resilience wrapping Story 6) fetch the
selected tier, Story 8 finalizes NDJSON to Parquet, and Story 9 writes the
manifest and rebuilds the root index.

This module's own contribution is the exit-status enumeration (FR-10), the
cumulative consumption figures (AC-1.3) that Story 9 deliberately left for
whoever drives a run end to end to compute (see `manifest.py`'s module
docstring), and — as of Story 11 — resolving the dataset selection and
repository filter before any of the above runs (AC-2.4/AC-2.5: before any
network call).

Story 12 layers resume on top: before creating a snapshot directory,
`run_snapshot()` checks (unless `force_fresh` is set) whether there is an
existing snapshot to continue instead of starting fresh — a specific one,
if `resume` names it (AC-4.3), otherwise the newest incomplete snapshot for
this org (AC-4.2).

Story 13 adds the guards around that: a resume whose recorded org, dataset
selection, or repository filter doesn't match this request is refused
(AC-4.8); an unreadable or version-incompatible checkpoint is refused with
a "start fresh" message (AC-4.9); a resume older than `stale_after_days` is
refused unless `allow_stale_resume` overrides it (AC-4.10); a first Ctrl-C
finishes the in-flight page and stops cleanly, a second stops immediately
(AC-4.11); and a per-org claim (`lock.py`) refuses a second concurrent run
against the same org while never blocking a different org sharing the same
`--snapshot-root` (FR-9, EC-12, EC-13).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from time import perf_counter

from org_harvest.checkpoint import CHECKPOINT_SCHEMA_VERSION, CheckpointState, CheckpointStore
from org_harvest.credentials import CredentialProvider
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.finalize import finalize_snapshot
from org_harvest.harvest.org_level import fetch_organization_directory
from org_harvest.harvest.repo_level import fetch_repository_datasets
from org_harvest.harvest.systemic import SystemicFailureGuard
from org_harvest.hosts import ApiHost
from org_harvest.interrupt import InterruptGuard
from org_harvest.lock import ClaimConflict, OrgClaim
from org_harvest.manifest import (
    CompletionStatus,
    ConsumptionStats,
    Manifest,
    build_manifest,
    rebuild_root_index,
    write_manifest,
)
from org_harvest.preflight import Verdict, run_preflight
from org_harvest.progress import ProgressCallback, ProgressEvent, ProgressEventKind
from org_harvest.resume import find_named_snapshot, find_newest_incomplete_snapshot
from org_harvest.selection import DatasetSelection, RepositoryFilter, resolve_dataset_selection
from org_harvest.timeutil import parse_compact_utc, utc_now_compact, utc_now_iso
from org_harvest.transport import Transport

_DEFAULT_STALE_AFTER_DAYS = 7.0


class ExitStatus(IntEnum):
    """FR-10's distinct, documented exit statuses. Values are stable once
    released — a caller (CI, an operator's script) may depend on them."""

    SUCCESS = 0
    """Every selected dataset completed with no gaps and no scope
    restriction (AC-1.4)."""

    COMPLETED_WITH_GAPS = 1
    """The run finished and wrote a manifest, but at least one dataset has
    a gap or the installation is scope-restricted (AC-5.4)."""

    STOPPED_RESUMABLE = 2
    """The run stopped before finalizing — a refused rate-limit wait
    (AC-7.4/AC-7.5) or a systemic failure (FR-5) — leaving a checkpoint
    but no manifest, so the snapshot is resumable (Story 12) but not
    complete (AC-8.11)."""

    INVALID_USAGE = 3
    """The request was invalid independent of any network call: bad
    credentials configuration, an unknown dataset name, an unsafe resume
    (AC-4.8, AC-4.9, AC-4.10), or similar."""

    AUTH_FAILURE = 4
    """Authentication or authorization failed: an expired non-refreshable
    token, a rejected refresh, an org that doesn't exist, or an
    uninstalled App."""

    CONCURRENT_RUN_REFUSED = 5
    """Another run already claims this org within this snapshot root
    (FR-9, EC-13)."""

    PREFLIGHT_BLOCKED = 6
    """`--fail-fast` was given and preflight found at least one blocked
    dataset (AC-6.4); the run never started."""

    UNEXPECTED_FAILURE = 7
    """A request failed after exhausting retries, or some other
    unanticipated failure occurred."""

    USER_INTERRUPT = 130
    """The user interrupted the run (Ctrl-C). 130 is the conventional
    Unix exit code for a process killed by SIGINT (128 + signal 2). Both
    a graceful first interrupt (AC-4.11, `run_snapshot()` returns
    normally) and a second, immediate one (`KeyboardInterrupt` propagates
    and is caught by the CLI) end up here."""


_ERROR_KIND_TO_EXIT_STATUS: dict[ErrorKind, ExitStatus] = {
    ErrorKind.CREDENTIAL_CONFLICT: ExitStatus.INVALID_USAGE,
    ErrorKind.CREDENTIAL_INVALID: ExitStatus.INVALID_USAGE,
    ErrorKind.INVALID_USAGE: ExitStatus.INVALID_USAGE,
    ErrorKind.AUTH_EXPIRED: ExitStatus.AUTH_FAILURE,
    ErrorKind.AUTH_FAILED: ExitStatus.AUTH_FAILURE,
    ErrorKind.ORG_NOT_FOUND: ExitStatus.AUTH_FAILURE,
    ErrorKind.APP_NOT_INSTALLED: ExitStatus.AUTH_FAILURE,
    ErrorKind.RATE_LIMIT_WAIT_EXCEEDED: ExitStatus.STOPPED_RESUMABLE,
    ErrorKind.SYSTEMIC_FAILURE: ExitStatus.STOPPED_RESUMABLE,
    ErrorKind.REQUEST_FAILED: ExitStatus.UNEXPECTED_FAILURE,
}


def exit_status_for_error(exc: OrgHarvestError) -> ExitStatus:
    """Maps a raised `OrgHarvestError` to its documented exit status
    (FR-10). Every `ErrorKind` is covered explicitly above; a future
    addition to `ErrorKind` that forgets to extend this table falls back
    to `UNEXPECTED_FAILURE` rather than raising `KeyError`, since an
    unrecognized failure is, definitionally, unexpected."""
    return _ERROR_KIND_TO_EXIT_STATUS.get(exc.kind, ExitStatus.UNEXPECTED_FAILURE)


@dataclass(frozen=True)
class RunResult:
    """What one `run_snapshot()` call produced, for the CLI (or a future
    library caller, Story 15) to report."""

    exit_status: ExitStatus
    snapshot_dir: Path | None
    manifest: Manifest | None
    elapsed_seconds: float
    message: str | None = None
    #: Datasets pulled in automatically because something explicitly
    #: selected depends on them (AC-2.6) — empty when nothing needed
    #: auto-inclusion, including every run with no `dataset_names` given.
    auto_included_datasets: tuple[str, ...] = ()
    #: The pre-existing incomplete snapshot this run continued (Story 12,
    #: AC-4.2/AC-4.3) — `None` when this run started a fresh snapshot
    #: (including every run before Story 12, and any `force_fresh` run).
    resumed_from: Path | None = None
    #: `True` when this run's org claim (Story 13, FR-9) was acquired by
    #: detecting and overwriting a stale claim left by a terminated
    #: process (EC-12) — the caller reports this as a warning.
    reclaimed_stale_claim: bool = False


def _resume_compatibility_error(
    state: CheckpointState,
    *,
    org: str,
    selection: DatasetSelection,
    repository_filter: RepositoryFilter | None,
) -> str | None:
    """AC-4.8: a resume whose recorded org, dataset selection, or
    repository filter doesn't match this request is unsafe — the stored
    cursors and gap ledger were built against a different scope, so
    continuing would silently mix the two. Returns an explanation string
    naming what differs, or `None` when everything matches."""
    if state.org.lower() != org.lower():
        return f"checkpoint was recorded for org {state.org!r}, not {org!r}"
    if set(state.dataset_selection) != set(selection.names):
        return (
            "checkpoint's dataset selection "
            f"{sorted(state.dataset_selection)} does not match the requested "
            f"selection {sorted(selection.names)}"
        )
    current_names = (
        tuple(sorted(repository_filter.names))
        if repository_filter and repository_filter.names
        else None
    )
    current_exclude_archived = repository_filter.exclude_archived if repository_filter else False
    current_exclude_forks = repository_filter.exclude_forks if repository_filter else False
    stored_names = tuple(sorted(state.repository_filter)) if state.repository_filter else None
    if (
        current_names != stored_names
        or current_exclude_archived != state.repository_exclude_archived
        or current_exclude_forks != state.repository_exclude_forks
    ):
        return "checkpoint's repository filter does not match the requested repository filter"
    return None


def _snapshot_age_days(snapshot_dir: Path) -> float | None:
    """The snapshot's age, computed from its directory name (a
    `utc_now_compact()` timestamp) rather than filesystem mtime, which an
    unrelated copy or backup could change (AC-4.10). Returns `None` when
    the name isn't in that format at all — a user-renamed or hand-created
    directory can't be judged for staleness, so it simply isn't."""
    try:
        created = parse_compact_utc(snapshot_dir.name)
    except ValueError:
        return None
    return (datetime.now(UTC) - created).total_seconds() / 86400


async def run_snapshot(
    transport: Transport,
    credentials: CredentialProvider,
    *,
    org: str,
    snapshot_root: Path,
    api_host: ApiHost | None = None,
    fail_fast: bool = False,
    dataset_names: Sequence[str] | None = None,
    repository_filter: RepositoryFilter | None = None,
    item_cap: int | None = None,
    resume: str | None = None,
    force_fresh: bool = False,
    stale_after_days: float = _DEFAULT_STALE_AFTER_DAYS,
    allow_stale_resume: bool = False,
    on_progress: ProgressCallback | None = None,
) -> RunResult:
    """Runs preflight, then Phase 1, then Phase 2, then finalizes and
    writes the manifest — in either a fresh snapshot directory or a
    resumed one (AC-1.5, AC-1.6, AC-1.7). `dataset_names` (`None` for the
    full default tier, AC-2.2) is resolved — validated, dependency-closed
    (AC-2.6) — before anything else happens, so an invalid selection
    (AC-2.4, AC-2.5) never reaches preflight or spends a network call.
    `repository_filter` and `item_cap` (AC-2.8, AC-2.9) are threaded to
    Phase 1 and Phase 2 respectively.

    Resume selection (Story 12): unless `force_fresh` is set, this looks
    for a snapshot to continue instead of starting a new one — the one
    named by `resume` (AC-4.3), or otherwise the newest incomplete
    snapshot already on disk for this org (AC-4.2). When neither applies
    (no `resume` given and nothing incomplete is found), a fresh snapshot
    is created exactly as before (AC-4.4). `RunResult.resumed_from` reports
    which snapshot, if any, was resumed.

    Resume safety (Story 13): a discovered or named snapshot is refused —
    before preflight, before any network call — if its checkpoint can't be
    read or was written by an incompatible schema version (AC-4.9), if its
    recorded org/selection/repository-filter doesn't match this request
    (AC-4.8), or if it's older than `stale_after_days` and
    `allow_stale_resume` wasn't given (AC-4.10). A per-org claim (FR-9)
    refuses to start at all while another run already holds one for this
    org — reclaiming it automatically, with `RunResult.reclaimed_stale_claim`
    reporting that, if the claim's process is no longer alive (EC-12).

    A first Ctrl-C (AC-4.11) is handled cooperatively: the in-flight page
    finishes, its checkpoint write lands, and this function returns
    normally with `ExitStatus.USER_INTERRUPT` and a message naming the
    command to resume — no manifest is written, so the snapshot stays
    resumable. A second Ctrl-C stops immediately: it raises
    `KeyboardInterrupt`, which this function does not catch — the caller
    maps that to `ExitStatus.USER_INTERRUPT` itself (see `cli.py`).

    Otherwise never raises `OrgHarvestError` — every failure this
    function's own collaborators can raise is caught and turned into the
    matching `RunResult.exit_status` (FR-10) instead, since a caller (the
    CLI, or a future library caller) needs a result to report either way.

    `on_progress` (Story 15, AC-9.4) is called as the run proceeds — once
    per phase boundary, once per dataset as its outcome becomes known, and
    once per rate-limit wait `transport` actually takes — rather than only
    once at the very end. `None` (the default) means no observation at
    all, at zero cost beyond the `is not None` checks."""

    def _emit_phase(kind: ProgressEventKind, phase: str) -> None:
        if on_progress is not None:
            verb = "starting" if kind is ProgressEventKind.PHASE_STARTED else "finished"
            on_progress(ProgressEvent(kind=kind, message=f"{verb}: {phase}", phase=phase))

    host = api_host or ApiHost()
    started_perf = perf_counter()
    started_at = utc_now_iso()

    try:
        selection = resolve_dataset_selection(dataset_names)
    except OrgHarvestError as exc:
        return RunResult(
            exit_status_for_error(exc), None, None, perf_counter() - started_perf, str(exc)
        )

    org_dir = snapshot_root / org.lower()

    candidate: Path | None
    if force_fresh:
        candidate = None
    elif resume is not None:
        candidate = find_named_snapshot(org_dir, resume)
        if candidate is None:
            # A named resume target that doesn't exist is invalid
            # independent of any network call (AC-4.3), same as an unknown
            # dataset name.
            return RunResult(
                ExitStatus.INVALID_USAGE,
                None,
                None,
                perf_counter() - started_perf,
                f"no snapshot named {resume!r} found under {org_dir}",
            )
    else:
        candidate = find_newest_incomplete_snapshot(org_dir)

    resumed_checkpoint: CheckpointStore | None = None
    if candidate is not None:
        try:
            resumed_checkpoint = CheckpointStore.resume(candidate / "checkpoint.json")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return RunResult(
                ExitStatus.INVALID_USAGE,
                None,
                None,
                perf_counter() - started_perf,
                f"checkpoint at {candidate} is unreadable ({exc}) — start fresh with "
                f"--force-fresh instead",
            )
        if resumed_checkpoint.state.schema_version != CHECKPOINT_SCHEMA_VERSION:
            return RunResult(
                ExitStatus.INVALID_USAGE,
                None,
                None,
                perf_counter() - started_perf,
                f"checkpoint at {candidate} was written by an incompatible tool "
                f"version (schema {resumed_checkpoint.state.schema_version}, expected "
                f"{CHECKPOINT_SCHEMA_VERSION}) — start fresh with --force-fresh instead",
            )
        mismatch = _resume_compatibility_error(
            resumed_checkpoint.state,
            org=org,
            selection=selection,
            repository_filter=repository_filter,
        )
        if mismatch is not None:
            return RunResult(
                ExitStatus.INVALID_USAGE,
                None,
                None,
                perf_counter() - started_perf,
                f"refusing to resume {candidate}: {mismatch}",
            )
        age_days = _snapshot_age_days(candidate)
        if age_days is not None and age_days > stale_after_days and not allow_stale_resume:
            return RunResult(
                ExitStatus.INVALID_USAGE,
                None,
                None,
                perf_counter() - started_perf,
                f"snapshot {candidate} is {age_days:.1f} days old, older than the "
                f"{stale_after_days:g}-day staleness window — pass --allow-stale-resume "
                f"to resume it anyway, or --force-fresh to start over",
            )

    claim = OrgClaim.acquire(org_dir)
    if isinstance(claim, ClaimConflict):
        return RunResult(
            ExitStatus.CONCURRENT_RUN_REFUSED,
            None,
            None,
            perf_counter() - started_perf,
            f"another run (pid {claim.pid}, claimed at {claim.claimed_at}) already holds "
            f"org {org!r} in this snapshot root",
        )

    with claim:
        _emit_phase(ProgressEventKind.PHASE_STARTED, "preflight")
        try:
            report = await run_preflight(
                transport, credentials, org=org, dataset_names=selection.names, api_host=host
            )
        except OrgHarvestError as exc:
            return RunResult(
                exit_status_for_error(exc),
                None,
                None,
                perf_counter() - started_perf,
                str(exc),
                reclaimed_stale_claim=claim.reclaimed_stale,
            )
        _emit_phase(ProgressEventKind.PHASE_COMPLETE, "preflight")

        if fail_fast and report.any_blocked:
            blocked = ", ".join(
                v.dataset for v in report.dataset_verdicts if v.verdict is Verdict.BLOCKED
            )
            return RunResult(
                ExitStatus.PREFLIGHT_BLOCKED,
                None,
                None,
                perf_counter() - started_perf,
                f"preflight found blocked dataset(s): {blocked}",
                reclaimed_stale_claim=claim.reclaimed_stale,
            )

        resumed_from: Path | None = None
        if candidate is not None and resumed_checkpoint is not None:
            snapshot_dir = candidate
            checkpoint = resumed_checkpoint
            resumed_from = snapshot_dir
        else:
            snapshot_dir = org_dir / utc_now_compact()
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            checkpoint = CheckpointStore.create(
                snapshot_dir / "checkpoint.json",
                org=org,
                dataset_selection=selection.names,
                repository_filter=(
                    tuple(sorted(repository_filter.names))
                    if repository_filter and repository_filter.names
                    else None
                ),
                repository_exclude_archived=repository_filter.exclude_archived
                if repository_filter
                else False,
                repository_exclude_forks=repository_filter.exclude_forks
                if repository_filter
                else False,
            )
        guard = SystemicFailureGuard()
        interrupt = InterruptGuard()

        if on_progress is not None:
            transport.set_wait_callback(
                lambda seconds: on_progress(
                    ProgressEvent(
                        kind=ProgressEventKind.RATE_LIMIT_WAIT,
                        message=f"waiting {seconds:.1f}s for the rate limit to recover",
                        wait_seconds=seconds,
                    )
                )
            )

        def _interrupted_result() -> RunResult:
            resume_cmd = f"org-harvest run {org} --resume {snapshot_dir.name}"
            return RunResult(
                ExitStatus.USER_INTERRUPT,
                snapshot_dir,
                None,
                perf_counter() - started_perf,
                f"interrupted; resume with: {resume_cmd}",
                auto_included_datasets=selection.auto_included,
                resumed_from=resumed_from,
                reclaimed_stale_claim=claim.reclaimed_stale,
            )

        with interrupt:
            _emit_phase(ProgressEventKind.PHASE_STARTED, "phase1")
            try:
                org_result = await fetch_organization_directory(
                    transport,
                    credentials,
                    org=org,
                    snapshot_dir=snapshot_dir,
                    api_host=host,
                    checkpoint=checkpoint,
                    systemic_guard=guard,
                    dataset_names=selection.names,
                    repository_filter=repository_filter,
                    interrupt=interrupt,
                    on_progress=on_progress,
                )
            except OrgHarvestError as exc:
                return RunResult(
                    exit_status_for_error(exc),
                    snapshot_dir,
                    None,
                    perf_counter() - started_perf,
                    str(exc),
                    auto_included_datasets=selection.auto_included,
                    resumed_from=resumed_from,
                    reclaimed_stale_claim=claim.reclaimed_stale,
                )

            _emit_phase(ProgressEventKind.PHASE_COMPLETE, "phase1")
            if interrupt.requested:
                return _interrupted_result()

            _emit_phase(ProgressEventKind.PHASE_STARTED, "phase2")
            try:
                repo_result = await fetch_repository_datasets(
                    transport,
                    org=org,
                    snapshot_dir=snapshot_dir,
                    api_host=host,
                    checkpoint=checkpoint,
                    systemic_guard=guard,
                    dataset_names=selection.names,
                    item_cap=item_cap,
                    interrupt=interrupt,
                    on_progress=on_progress,
                )
            except OrgHarvestError as exc:
                return RunResult(
                    exit_status_for_error(exc),
                    snapshot_dir,
                    None,
                    perf_counter() - started_perf,
                    str(exc),
                    auto_included_datasets=selection.auto_included,
                    resumed_from=resumed_from,
                    reclaimed_stale_claim=claim.reclaimed_stale,
                )

            _emit_phase(ProgressEventKind.PHASE_COMPLETE, "phase2")
            if interrupt.requested:
                return _interrupted_result()

        _emit_phase(ProgressEventKind.PHASE_STARTED, "finalize")
        finalize_result = finalize_snapshot(snapshot_dir)
        _emit_phase(ProgressEventKind.PHASE_COMPLETE, "finalize")
        completed_at = utc_now_iso()

        consumption = ConsumptionStats(
            graphql_points_consumed=transport.graphql_budget.total_consumed,
            graphql_requests=transport.graphql_request_count,
            rest_requests_consumed=transport.rest_request_count,
            rate_limit_waits=transport.rate_limit_wait_count,
            total_wait_seconds=transport.total_wait_seconds,
        )
        manifest = build_manifest(
            org=org,
            api_host=host.host,
            started_at=started_at,
            completed_at=completed_at,
            dataset_selection=selection.names,
            dataset_outcomes=org_result.dataset_outcomes + repo_result.dataset_outcomes,
            scope_restricted=org_result.scope_restricted,
            conversion_outcomes=finalize_result.dataset_outcomes,
            consumption=consumption,
        )
        write_manifest(snapshot_dir, manifest)
        rebuild_root_index(snapshot_dir.parent, org.lower())

        status = (
            ExitStatus.SUCCESS
            if manifest.status is CompletionStatus.COMPLETE
            else ExitStatus.COMPLETED_WITH_GAPS
        )
        return RunResult(
            status,
            snapshot_dir,
            manifest,
            perf_counter() - started_perf,
            auto_included_datasets=selection.auto_included,
            resumed_from=resumed_from,
            reclaimed_stale_claim=claim.reclaimed_stale,
        )
