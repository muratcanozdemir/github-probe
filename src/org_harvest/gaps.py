"""The structured gap record every partial or total fetch failure becomes
(FR-5, AC-5.1, AC-5.2) — never a swallowed exception, never a silently
incomplete file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from org_harvest.timeutil import utc_now_iso


@dataclass(frozen=True)
class Gap:
    """One recorded failure. `resource_id` identifies what was being
    fetched (an org login, a repository node id, ...); `field_path` is the
    GraphQL error's `path` when the API supplied one, `None` otherwise
    (e.g. a request that failed transport-wide, with no GraphQL response to
    carry a path at all)."""

    dataset: str
    resource_id: str | None
    field_path: str | None
    reason: str
    occurred_at: str

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Gap:
        """Reconstructs a `Gap` from its `to_dict()`/checkpoint-ledger
        form (Story 12, AC-4.2/AC-4.5) — a resumed run's history is only
        ever read back through this, never `Gap(**data)` directly, so a
        loosely-typed loaded dict doesn't need to satisfy the dataclass's
        exact field types on its own."""
        return Gap(
            dataset=str(data["dataset"]),
            resource_id=data.get("resource_id"),
            field_path=data.get("field_path"),
            reason=str(data.get("reason", "")),
            occurred_at=str(data.get("occurred_at", "")),
        )

    @staticmethod
    def now(dataset: str, *, resource_id: str | None, field_path: str | None, reason: str) -> Gap:
        return Gap(
            dataset=dataset,
            resource_id=resource_id,
            field_path=field_path,
            reason=reason,
            occurred_at=utc_now_iso(),
        )


@dataclass(frozen=True)
class DatasetOutcome:
    """How one dataset's fetch went, whether that fetch was org-level
    (Story 5) or repository-level (Story 6) — the same shape either way, so
    callers that aggregate across both phases don't need to distinguish."""

    name: str
    record_count: int
    gaps: tuple[Gap, ...]
