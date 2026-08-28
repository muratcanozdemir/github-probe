"""The one-JSON-file-per-snapshot checkpoint (architecture.md, Decision 5).

This story (5) establishes the checkpoint mechanics — continuous writes at
page granularity (AC-4.1), per-dataset status, per-collection cursors, the
run's original dataset selection, and the tool version that wrote it — that
Story 12/13 will later read back to actually resume a run. Nothing here
reads an existing checkpoint to resume; `CheckpointStore.create()` always
starts fresh, matching this story's excluded scope.

Gaps (FR-5) are recorded here too, alongside cursors: it is the only
snapshot-durable state this story has access to before Story 9 introduces
`manifest.json`, and gaps must be "discoverable from the snapshot alone"
(AC-5.6) the moment they occur, not only once a manifest is written at the
end of a run. Story 9 is expected to fold this list into the manifest at
finalization time rather than inventing a second place gaps live.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from org_harvest.constants import TOOL_VERSION
from org_harvest.gaps import Gap

CHECKPOINT_FILENAME = "checkpoint.json"

#: Bumped only if the checkpoint's on-disk shape changes incompatibly.
#: Story 12/13 compares this against a loaded checkpoint's own version
#: to implement AC-4.9's "incompatible tool version" refusal.
CHECKPOINT_SCHEMA_VERSION = 1

#: Stored as a cursor value (Story 12, AC-4.5) to mark one sub-resource
#: (a repository within a repo-level dataset, a team within a per-team
#: org-level connection) as fully attempted — paginated to its natural
#: end, or ended in a recorded gap — so a resumed run skips re-fetching
#: it entirely rather than re-requesting an empty final page. Distinct
#: from `None` (never attempted) and from a real opaque GraphQL cursor
#: (still in progress).
CURSOR_DONE = "__done__"


@dataclass
class CheckpointState:
    schema_version: int
    tool_version: str
    org: str
    dataset_selection: tuple[str, ...]
    repository_filter: tuple[str, ...] | None
    dataset_status: dict[str, str] = field(default_factory=dict)
    #: Cursor key is the dataset name for a direct org-level connection, or
    #: `"{dataset}:{parent_id}"` for a connection nested under a parent
    #: resource (e.g. `team_members:T_kwDOA...`).
    cursors: dict[str, str | None] = field(default_factory=dict)
    gaps: list[dict[str, str | None]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tool_version": self.tool_version,
            "org": self.org,
            "dataset_selection": list(self.dataset_selection),
            "repository_filter": (
                list(self.repository_filter) if self.repository_filter is not None else None
            ),
            "dataset_status": self.dataset_status,
            "cursors": self.cursors,
            "gaps": self.gaps,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CheckpointState:
        return cls(
            schema_version=data["schema_version"],
            tool_version=data["tool_version"],
            org=data["org"],
            dataset_selection=tuple(data["dataset_selection"]),
            repository_filter=(
                tuple(data["repository_filter"])
                if data.get("repository_filter") is not None
                else None
            ),
            dataset_status=dict(data.get("dataset_status", {})),
            cursors=dict(data.get("cursors", {})),
            gaps=list(data.get("gaps", [])),
        )


class CheckpointStore:
    """Owns one checkpoint file's lifecycle: create fresh, mutate in
    memory, and persist atomically (write-to-temp-then-replace) so a kill
    mid-write never leaves a half-written, corrupt checkpoint behind."""

    def __init__(self, path: Path, state: CheckpointState) -> None:
        self.path = path
        self.state = state

    @classmethod
    def create(
        cls,
        path: Path,
        *,
        org: str,
        dataset_selection: tuple[str, ...],
        repository_filter: tuple[str, ...] | None = None,
    ) -> CheckpointStore:
        state = CheckpointState(
            schema_version=CHECKPOINT_SCHEMA_VERSION,
            tool_version=TOOL_VERSION,
            org=org,
            dataset_selection=dataset_selection,
            repository_filter=repository_filter,
        )
        store = cls(path, state)
        store.save()
        return store

    @classmethod
    def load(cls, path: Path) -> CheckpointState:
        with path.open(encoding="utf-8") as f:
            return CheckpointState.from_json(json.load(f))

    @classmethod
    def resume(cls, path: Path) -> CheckpointStore:
        """Opens an existing checkpoint for a resumed run (Story 12,
        AC-4.2/AC-4.3) — unlike `create()`, this reads whatever state is
        already on disk (dataset statuses, cursors, gaps) rather than
        starting fresh, so the harvest engines' own "skip what's already
        complete, continue from the stored cursor otherwise" logic has
        real history to act on. Compatibility checks (schema version, org,
        selection match) are Story 13's job, not this constructor's."""
        return cls(path, cls.load(path))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(self.state.to_json(), f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.path)

    def set_dataset_status(self, dataset: str, status: str) -> None:
        self.state.dataset_status[dataset] = status
        self.save()

    def set_cursor(self, key: str, cursor: str | None) -> None:
        self.state.cursors[key] = cursor
        self.save()

    def record_gap(self, gap: Gap) -> None:
        self.state.gaps.append(gap.to_dict())
        self.save()
