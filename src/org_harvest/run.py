"""Wires Stories 1-9's already-tested building blocks into the single
`run` command AC-1.1 promises: preflight (Story 4) gates the run, Phase 1
(Story 5) then Phase 2 (Story 7's resilience wrapping Story 6) fetch the
full default tier, Story 8 finalizes NDJSON to Parquet, and Story 9 writes
the manifest and rebuilds the root index.

This module's own contribution is the exit-status enumeration (FR-10) and
the cumulative consumption figures (AC-1.3) that Story 9 deliberately left
for whoever drives a run end to end to compute — see `manifest.py`'s module
docstring.

Dataset narrowing (Story 11) and resume (Story 12/13) are layered on top of
this by later stories: this module always runs the complete default tier,
from a fresh snapshot directory, start to finish.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from time import perf_counter

from org_harvest.checkpoint import CheckpointStore
from org_harvest.credentials import CredentialProvider
from org_harvest.datasets import default_tier_names
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.finalize import finalize_snapshot
from org_harvest.harvest.org_level import fetch_organization_directory
from org_harvest.harvest.repo_level import fetch_repository_datasets
from org_harvest.harvest.systemic import SystemicFailureGuard
from org_harvest.hosts import ApiHost
from org_harvest.manifest import (
    CompletionStatus,
    ConsumptionStats,
    Manifest,
    build_manifest,
    rebuild_root_index,
    write_manifest,
)
from org_harvest.preflight import Verdict, run_preflight
from org_harvest.timeutil import utc_now_compact, utc_now_iso
from org_harvest.transport import Transport


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
    credentials configuration, an unknown dataset name, or similar."""

    AUTH_FAILURE = 4
    """Authentication or authorization failed: an expired non-refreshable
    token, a rejected refresh, an org that doesn't exist, or an
    uninstalled App."""

    CONCURRENT_RUN_REFUSED = 5
    """Another run already claims this org's snapshot root. The
    underlying claim/lock check is Story 13's; this status exists in the
    enumeration now so Story 13 has nothing to add to it later."""

    PREFLIGHT_BLOCKED = 6
    """`--fail-fast` was given and preflight found at least one blocked
    dataset (AC-6.4); the run never started."""

    UNEXPECTED_FAILURE = 7
    """A request failed after exhausting retries, or some other
    unanticipated failure occurred."""

    USER_INTERRUPT = 130
    """The user interrupted the run (Ctrl-C). 130 is the conventional
    Unix exit code for a process killed by SIGINT (128 + signal 2)."""


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


async def run_snapshot(
    transport: Transport,
    credentials: CredentialProvider,
    *,
    org: str,
    snapshot_root: Path,
    api_host: ApiHost | None = None,
    fail_fast: bool = False,
) -> RunResult:
    """Runs preflight, then Phase 1, then Phase 2, then finalizes and
    writes the manifest — the complete default tier (AC-1.2), start to
    finish, in one new snapshot directory (AC-1.5, AC-1.6, AC-1.7).

    Never raises `OrgHarvestError` — every failure this function's own
    collaborators can raise is caught and turned into the matching
    `RunResult.exit_status` (FR-10) instead, since a caller (the CLI, or a
    future library caller) needs a result to report either way. A
    `KeyboardInterrupt` is deliberately not caught here — the caller maps
    that to `ExitStatus.USER_INTERRUPT` itself (see `cli.py`), since
    presenting an interrupt is a caller concern, not this function's."""
    host = api_host or ApiHost()
    dataset_names = default_tier_names()
    started_perf = perf_counter()
    started_at = utc_now_iso()

    try:
        report = await run_preflight(
            transport, credentials, org=org, dataset_names=dataset_names, api_host=host
        )
    except OrgHarvestError as exc:
        return RunResult(
            exit_status_for_error(exc), None, None, perf_counter() - started_perf, str(exc)
        )

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
        )

    snapshot_dir = snapshot_root / org.lower() / utc_now_compact()
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = CheckpointStore.create(
        snapshot_dir / "checkpoint.json", org=org, dataset_selection=dataset_names
    )
    guard = SystemicFailureGuard()

    try:
        org_result = await fetch_organization_directory(
            transport,
            credentials,
            org=org,
            snapshot_dir=snapshot_dir,
            api_host=host,
            checkpoint=checkpoint,
            systemic_guard=guard,
        )
        repo_result = await fetch_repository_datasets(
            transport,
            org=org,
            snapshot_dir=snapshot_dir,
            api_host=host,
            checkpoint=checkpoint,
            systemic_guard=guard,
        )
    except OrgHarvestError as exc:
        return RunResult(
            exit_status_for_error(exc),
            snapshot_dir,
            None,
            perf_counter() - started_perf,
            str(exc),
        )

    finalize_result = finalize_snapshot(snapshot_dir)
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
        dataset_selection=dataset_names,
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
    return RunResult(status, snapshot_dir, manifest, perf_counter() - started_perf)
