"""Preflight readiness checks, run before spending an hour on a download
that a missing permission or a repository-scoped installation would have
made incomplete anyway (US-6).

Issues its own small GraphQL query directly through `Transport` rather than
depending on the dataset-fetch engine (Story 5/6), so it stays a genuinely
standalone capability (AC-6.5) that doesn't need the rest of the harvest to
exist.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import httpx

from org_harvest.credentials import CredentialProvider
from org_harvest.datasets import DatasetLevel, DatasetSpec, get
from org_harvest.hosts import ApiHost
from org_harvest.ratelimit import RateLimitSnapshot
from org_harvest.transport import Transport

_PREFLIGHT_QUERY = """
query($org: String!) {
  rateLimit { limit remaining resetAt cost nodeCount }
  organization(login: $org) {
    repositories { totalCount }
  }
}
"""

#: Rough per-repository-dataset and per-org-dataset point cost used only for
#: the up-front estimate (AC-6.3) — the true cost is only knowable from the
#: `rateLimit.cost` field after a query actually runs (architecture.md).
_ESTIMATED_POINTS_PER_REPO_DATASET = 1
_ESTIMATED_POINTS_PER_ORG_DATASET = 1
_SECONDS_PER_RATE_LIMIT_WINDOW = 3600.0


class Verdict(Enum):
    READY = "ready"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class DatasetVerdict:
    dataset: str
    verdict: Verdict
    reason: str = ""


@dataclass(frozen=True)
class PreflightReport:
    org: str
    repository_count: int | None
    scope_restricted: bool
    dataset_verdicts: tuple[DatasetVerdict, ...]
    estimated_points: int | None
    estimated_duration_seconds: float | None

    @property
    def any_blocked(self) -> bool:
        return any(v.verdict is Verdict.BLOCKED for v in self.dataset_verdicts)


def _extract_graphql_budget(resp: httpx.Response) -> RateLimitSnapshot | None:
    try:
        data = resp.json()["data"]["rateLimit"]
    except (KeyError, TypeError, ValueError):
        return None
    return RateLimitSnapshot(
        limit=data["limit"],
        remaining=data["remaining"],
        reset_at=datetime.fromisoformat(data["resetAt"].replace("Z", "+00:00")).timestamp(),
    )


def _check_dataset_permissions(
    spec: DatasetSpec, permissions: dict[str, str] | None
) -> DatasetVerdict:
    if permissions is None:
        return DatasetVerdict(
            spec.name,
            Verdict.DEGRADED,
            reason="permissions unknown for a pre-minted token; readiness cannot be "
            "pre-verified for this dataset",
        )
    missing = tuple(
        p for p in spec.required_permissions if permissions.get(p) not in ("read", "write")
    )
    if missing:
        return DatasetVerdict(
            spec.name, Verdict.BLOCKED, reason=f"missing permission(s): {', '.join(missing)}"
        )
    return DatasetVerdict(spec.name, Verdict.READY)


def _estimate(
    dataset_names: Sequence[str], repository_count: int, limit: int, remaining: int
) -> tuple[int, float]:
    org_level_count = sum(
        1 for name in dataset_names if get(name).level is DatasetLevel.ORGANIZATION
    )
    repo_level_count = len(dataset_names) - org_level_count
    estimated_points = (
        org_level_count * _ESTIMATED_POINTS_PER_ORG_DATASET
        + repository_count * repo_level_count * _ESTIMATED_POINTS_PER_REPO_DATASET
    )
    if limit <= 0:
        return estimated_points, 0.0
    shortfall = max(0, estimated_points - remaining)
    windows_needed = math.ceil(shortfall / limit) if shortfall else 0
    return estimated_points, windows_needed * _SECONDS_PER_RATE_LIMIT_WINDOW


async def run_preflight(
    transport: Transport,
    credentials: CredentialProvider,
    *,
    org: str,
    dataset_names: Sequence[str],
    api_host: ApiHost | None = None,
) -> PreflightReport:
    host = api_host or ApiHost()
    resp = await transport.send_graphql(
        host.graphql_url,
        payload={"query": _PREFLIGHT_QUERY, "variables": {"org": org}},
        extract_budget=_extract_graphql_budget,
    )
    resp.raise_for_status()
    body = resp.json()["data"]
    org_data = body.get("organization")
    repository_count = org_data["repositories"]["totalCount"] if org_data else None
    rate_limit = body["rateLimit"]

    verdicts = tuple(
        _check_dataset_permissions(get(name), credentials.permissions) for name in dataset_names
    )

    estimated_points: int | None = None
    estimated_duration: float | None = None
    if repository_count is not None:
        estimated_points, estimated_duration = _estimate(
            dataset_names, repository_count, rate_limit["limit"], rate_limit["remaining"]
        )

    return PreflightReport(
        org=org,
        repository_count=repository_count,
        scope_restricted=credentials.repository_selection == "selected",
        dataset_verdicts=verdicts,
        estimated_points=estimated_points,
        estimated_duration_seconds=estimated_duration,
    )
