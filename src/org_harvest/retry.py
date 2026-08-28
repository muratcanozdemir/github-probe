"""Retries only the resources recorded as gaps in an existing, completed
snapshot (Story 14, AC-11.1 through AC-11.5) — reusing Stories 5-7's fetch
engines, scoped down to exactly what failed, rather than a separate
implementation.

A gap's `resource_id` already identifies exactly what to re-attempt: a
repository node id for a repository-level dataset, a team node id for
`team_members`/`team_repositories`, or the org login itself for every
other organization-level dataset (there being only one such "resource" per
org). This module groups gaps by dataset, resets just enough checkpoint
state for each gapped dataset to be re-attempted (its completion status,
its gaps, and — where a per-resource cursor scheme exists — exactly the
gapped resources' cursor entries, including a `CURSOR_DONE` one), then
calls `fetch_organization_directory()`/`fetch_repository_datasets()` again
with `dataset_names` narrowed to one dataset at a time and, where
applicable, `team_ids`/`repository_ids` narrowed to the gapped resources —
one call per gapped dataset, so retrying dataset A's gaps never re-fetches
extra data for dataset B just because both happened to gap on an
overlapping but not identical set of resources.

Datasets that never gapped are left entirely untouched: their outcome is
reconstructed from the existing manifest (record count plus zero gaps)
rather than re-fetched, so the new manifest this module writes still
reflects everything the original run did, not just what was retried.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from org_harvest.checkpoint import CheckpointStore
from org_harvest.credentials import CredentialProvider
from org_harvest.datasets import DatasetLevel, get
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.finalize import finalize_snapshot
from org_harvest.gaps import DatasetOutcome, Gap
from org_harvest.harvest.org_level import fetch_organization_directory
from org_harvest.harvest.repo_level import fetch_repository_datasets
from org_harvest.hosts import ApiHost
from org_harvest.manifest import (
    Manifest,
    build_manifest,
    read_manifest,
    rebuild_root_index,
    write_manifest,
)
from org_harvest.timeutil import utc_now_iso
from org_harvest.transport import Transport

#: The two org-level datasets whose gaps carry a *team* resource id rather
#: than the org login — their retry needs `team_ids`, not a whole-dataset
#: re-fetch.
_TEAM_SCOPED_DATASETS = frozenset({"team_members", "team_repositories"})


@dataclass(frozen=True)
class RetryResult:
    """What one `retry_gaps()` call did. `retried` is `False` for the
    AC-11.5 no-op case (nothing to retry) — `manifest` is then simply the
    snapshot's existing, unmodified manifest."""

    retried: bool
    manifest: Manifest
    datasets_retried: tuple[str, ...] = ()


def _outcome_from_manifest(manifest: Manifest, dataset: str) -> DatasetOutcome:
    """Reconstructs a dataset's outcome from an already-written manifest —
    used for every dataset this retry pass isn't touching, so the new
    manifest built at the end still accounts for it."""
    gaps = tuple(g for g in manifest.gaps if g.dataset == dataset)
    return DatasetOutcome(dataset, manifest.dataset_counts.get(dataset, 0), gaps)


def _reset_for_retry(checkpoint: CheckpointStore, dataset: str, resource_ids: set[str]) -> None:
    """Clears exactly what stands between a gapped resource and being
    re-attempted as if for the first time (AC-11.2): the dataset's
    completion status (so it isn't skipped as already `"complete"`), its
    own recorded gaps (a fresh attempt records its own, whether it
    succeeds or fails again), and either the specific per-resource cursor
    entries for the gapped resources (which may be a real cursor or a
    `CURSOR_DONE` marker — either way, stale) or, when there's no
    per-resource cursor scheme for this dataset, its one whole-dataset
    cursor key."""
    checkpoint.reset_dataset(dataset)
    if resource_ids:
        for resource_id in resource_ids:
            checkpoint.clear_cursor(f"{dataset}:{resource_id}")
    else:
        checkpoint.clear_cursor(dataset)


async def retry_gaps(
    transport: Transport,
    credentials: CredentialProvider,
    *,
    org: str,
    snapshot_dir: Path,
    api_host: ApiHost | None = None,
) -> RetryResult:
    """Re-attempts every gapped resource in `snapshot_dir`'s existing,
    completed snapshot (AC-11.1). Requires a manifest to already exist —
    an incomplete snapshot (no manifest) is Story 12/13's resume territory,
    not this function's, and raises `OrgHarvestError(kind=INVALID_USAGE)`.

    Returns a no-op `RetryResult` (AC-11.5) — no checkpoint mutation, no
    re-fetch, no finalize re-run, no manifest rewrite — when the snapshot
    currently has no gaps at all."""
    manifest = read_manifest(snapshot_dir)
    if manifest is None:
        raise OrgHarvestError(
            f"snapshot at {snapshot_dir} has no manifest — it is incomplete, not "
            "retryable; resume it instead",
            kind=ErrorKind.INVALID_USAGE,
        )

    gaps_by_dataset: dict[str, list[Gap]] = defaultdict(list)
    for gap in manifest.gaps:
        gaps_by_dataset[gap.dataset].append(gap)

    if not gaps_by_dataset:
        return RetryResult(retried=False, manifest=manifest)

    host = api_host or ApiHost()
    checkpoint = CheckpointStore.resume(snapshot_dir / "checkpoint.json")

    fresh_outcomes: dict[str, DatasetOutcome] = {}
    for dataset in sorted(gaps_by_dataset):
        resource_ids = {
            g.resource_id for g in gaps_by_dataset[dataset] if g.resource_id is not None
        }
        level = get(dataset).level
        # Only repository-level datasets (per-repo) and the two team-scoped
        # org-level connections key their checkpoint cursors per resource
        # id — every other org-level dataset has exactly one whole-dataset
        # cursor, even though its gap's `resource_id` is set (to the org
        # login itself, its one and only "resource"). Passing the wrong
        # set here would make `_reset_for_retry` clear a cursor key that
        # was never used instead of the real one.
        per_resource = level is DatasetLevel.REPOSITORY or dataset in _TEAM_SCOPED_DATASETS
        _reset_for_retry(checkpoint, dataset, resource_ids if per_resource else set())
        if level is DatasetLevel.ORGANIZATION:
            org_result = await fetch_organization_directory(
                transport,
                credentials,
                org=org,
                snapshot_dir=snapshot_dir,
                api_host=host,
                checkpoint=checkpoint,
                dataset_names=(dataset,),
                team_ids=frozenset(resource_ids) if dataset in _TEAM_SCOPED_DATASETS else None,
            )
            fresh_outcomes[dataset] = org_result.dataset_outcomes[0]
        else:
            repo_result = await fetch_repository_datasets(
                transport,
                org=org,
                snapshot_dir=snapshot_dir,
                api_host=host,
                checkpoint=checkpoint,
                dataset_names=(dataset,),
                repository_ids=frozenset(resource_ids) if resource_ids else None,
            )
            fresh_outcomes[dataset] = repo_result.dataset_outcomes[0]

    all_datasets = set(manifest.dataset_counts) | set(fresh_outcomes)
    dataset_outcomes = tuple(
        fresh_outcomes[name] if name in fresh_outcomes else _outcome_from_manifest(manifest, name)
        for name in sorted(all_datasets)
    )

    finalize_result = finalize_snapshot(snapshot_dir)
    retried_at = utc_now_iso()
    new_manifest = build_manifest(
        org=manifest.org,
        api_host=manifest.api_host,
        started_at=manifest.started_at,
        completed_at=manifest.completed_at,
        dataset_selection=manifest.dataset_selection,
        dataset_outcomes=dataset_outcomes,
        scope_restricted=manifest.scope_restricted,
        conversion_outcomes=finalize_result.dataset_outcomes,
        consumption=manifest.consumption,
        last_retried_at=retried_at,
    )
    write_manifest(snapshot_dir, new_manifest)
    rebuild_root_index(snapshot_dir.parent, org.lower())

    return RetryResult(
        retried=True, manifest=new_manifest, datasets_retried=tuple(sorted(gaps_by_dataset))
    )
