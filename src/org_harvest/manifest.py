"""Per-snapshot manifest and per-org root index (architecture.md,
Decision 5) — the on-disk record of what a run actually did (AC-8.7),
readable without any dependency on console output (AC-5.6), and the thing
whose mere *absence* marks a snapshot incomplete (AC-8.11).

This story aggregates and persists the counts, gaps, and scope-restriction
flag Stories 5–8 already produced (`build_manifest()`); it does not
compute resource-consumption figures itself — `ConsumptionStats` is
whatever the caller (Story 10's full-run orchestration, which is the
thing that actually drives a run end to end and already needs to report
these same figures for AC-1.3) passes in.

The root index is always *rebuilt* from what's actually on disk
(`rebuild_root_index()`), never incrementally patched — so it can never
drift from the manifests it's summarizing, at the cost of an O(snapshots)
scan each time it's updated, which is cheap at the scale ("snapshots for
one org") this covers.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from org_harvest.constants import TOOL_VERSION
from org_harvest.gaps import DatasetOutcome, Gap

MANIFEST_FILENAME = "manifest.json"
ROOT_INDEX_FILENAME = "index.json"


class CompletionStatus(Enum):
    """AC-1.4's exit-0 condition, made a first-class value here rather
    than something every reader has to re-derive from `gaps`/
    `scope_restricted` themselves."""

    COMPLETE = "complete"
    COMPLETE_WITH_GAPS = "complete_with_gaps"


@dataclass(frozen=True)
class ConsumptionStats:
    """Resource-consumption figures for one run. Every field is optional
    and defaults to unknown/zero — this story only persists whatever it's
    given (see the module docstring)."""

    graphql_points_consumed: int | None = None
    graphql_requests: int | None = None
    rest_requests_consumed: int | None = None
    rate_limit_waits: int = 0
    total_wait_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsumptionStats:
        return cls(
            graphql_points_consumed=data.get("graphql_points_consumed"),
            graphql_requests=data.get("graphql_requests"),
            rest_requests_consumed=data.get("rest_requests_consumed"),
            rate_limit_waits=data.get("rate_limit_waits", 0),
            total_wait_seconds=data.get("total_wait_seconds", 0.0),
        )


@dataclass(frozen=True)
class Manifest:
    org: str
    api_host: str
    tool_version: str
    started_at: str
    completed_at: str
    dataset_selection: tuple[str, ...]
    dataset_counts: dict[str, int]
    gaps: tuple[Gap, ...]
    scope_restricted: bool
    consumption: ConsumptionStats = field(default_factory=ConsumptionStats)
    #: Set by Story 14 when a retry-gaps operation has run against this
    #: snapshot (AC-11.3) — `None` means no retry has happened yet.
    last_retried_at: str | None = None

    @property
    def status(self) -> CompletionStatus:
        if self.gaps or self.scope_restricted:
            return CompletionStatus.COMPLETE_WITH_GAPS
        return CompletionStatus.COMPLETE

    def to_dict(self) -> dict[str, Any]:
        return {
            "org": self.org,
            "api_host": self.api_host,
            "tool_version": self.tool_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "dataset_selection": list(self.dataset_selection),
            "dataset_counts": dict(self.dataset_counts),
            "gaps": [g.to_dict() for g in self.gaps],
            "scope_restricted": self.scope_restricted,
            "consumption": self.consumption.to_dict(),
            "status": self.status.value,
            "last_retried_at": self.last_retried_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        return cls(
            org=data["org"],
            api_host=data["api_host"],
            tool_version=data["tool_version"],
            started_at=data["started_at"],
            completed_at=data["completed_at"],
            dataset_selection=tuple(data["dataset_selection"]),
            dataset_counts=dict(data["dataset_counts"]),
            gaps=tuple(Gap(**g) for g in data.get("gaps", [])),
            scope_restricted=data["scope_restricted"],
            consumption=ConsumptionStats.from_dict(data.get("consumption", {})),
            last_retried_at=data.get("last_retried_at"),
        )


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def write_manifest(snapshot_dir: Path, manifest: Manifest) -> None:
    _atomic_write_json(snapshot_dir / MANIFEST_FILENAME, manifest.to_dict())


def read_manifest(snapshot_dir: Path) -> Manifest | None:
    path = snapshot_dir / MANIFEST_FILENAME
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return Manifest.from_dict(json.load(f))


def is_snapshot_complete(snapshot_dir: Path) -> bool:
    """AC-8.11: a snapshot without a manifest is treated as incomplete by
    every operation that reads snapshots — this is that check, in one
    place, so no reader has to reinvent it."""
    return read_manifest(snapshot_dir) is not None


def build_manifest(
    *,
    org: str,
    api_host: str,
    started_at: str,
    completed_at: str,
    dataset_selection: tuple[str, ...],
    dataset_outcomes: tuple[DatasetOutcome, ...] = (),
    scope_restricted: bool = False,
    conversion_outcomes: tuple[DatasetOutcome, ...] = (),
    consumption: ConsumptionStats | None = None,
) -> Manifest:
    """Aggregates Stories 5–8's outputs into one manifest. `dataset_outcomes`
    is every dataset's fetch result (org-level and repository-level
    combined — record counts come from here, since that's what was
    actually downloaded); `conversion_outcomes` (Story 8's `FinalizeResult`)
    contributes its own gaps (a conversion failure) without touching the
    counts already established by the fetch."""
    dataset_counts = {outcome.name: outcome.record_count for outcome in dataset_outcomes}
    gaps: list[Gap] = [g for outcome in dataset_outcomes for g in outcome.gaps]
    gaps.extend(g for outcome in conversion_outcomes for g in outcome.gaps)
    return Manifest(
        org=org,
        api_host=api_host,
        tool_version=TOOL_VERSION,
        started_at=started_at,
        completed_at=completed_at,
        dataset_selection=dataset_selection,
        dataset_counts=dataset_counts,
        gaps=tuple(gaps),
        scope_restricted=scope_restricted,
        consumption=consumption or ConsumptionStats(),
    )


@dataclass(frozen=True)
class SnapshotIndexEntry:
    timestamp: str
    status: str


@dataclass(frozen=True)
class RootIndex:
    org: str
    snapshots: tuple[SnapshotIndexEntry, ...]
    #: Timestamp of the newest snapshot with `CompletionStatus.COMPLETE`
    #: (no gaps, no scope restriction — AC-8.8's literal wording), or
    #: `None` if no snapshot for this org qualifies yet.
    latest_complete: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "org": self.org,
            "snapshots": [{"timestamp": s.timestamp, "status": s.status} for s in self.snapshots],
            "latest_complete": self.latest_complete,
        }


def rebuild_root_index(org_dir: Path, org: str) -> RootIndex:
    """Recomputes the root index for one org from whatever snapshot
    directories and manifests actually exist under `org_dir` — never
    trusted to incremental bookkeeping, so it can never drift from what's
    really on disk. Call this after every `write_manifest()` (and after
    Story 14's retry-gaps updates one)."""
    entries = []
    if org_dir.exists():
        for child in sorted(p for p in org_dir.iterdir() if p.is_dir()):
            manifest = read_manifest(child)
            status = manifest.status.value if manifest is not None else "incomplete"
            entries.append(SnapshotIndexEntry(timestamp=child.name, status=status))
    latest_complete = next(
        (e.timestamp for e in reversed(entries) if e.status == CompletionStatus.COMPLETE.value),
        None,
    )
    index = RootIndex(org=org, snapshots=tuple(entries), latest_complete=latest_complete)
    _atomic_write_json(org_dir / ROOT_INDEX_FILENAME, index.to_dict())
    return index
