"""The structured gap record every partial or total fetch failure becomes
(FR-5, AC-5.1, AC-5.2) — never a swallowed exception, never a silently
incomplete file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

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
    def now(dataset: str, *, resource_id: str | None, field_path: str | None, reason: str) -> Gap:
        return Gap(
            dataset=dataset,
            resource_id=resource_id,
            field_path=field_path,
            reason=reason,
            occurred_at=utc_now_iso(),
        )
