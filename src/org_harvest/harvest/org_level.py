"""Phase 1 (architecture.md, Decision 4): fetches all eleven
organization-level default-tier datasets (AC-1.2) and writes each as NDJSON
(AC-8.1), checkpointing continuously at page granularity (AC-4.1) and
recording gaps rather than aborting on partial or total failure (AC-5.1,
AC-5.2, AC-5.3, FR-5).

Query shape: every direct organization-level connection (`members`,
`pending_members`, `teams`, `repositories`, `org_rulesets`,
`org_custom_properties`, `org_domains`, `org_ip_allow_list`) is fetched by
one generic paginator (`_fetch_org_connection`), parameterized by a
`_ConnectionSpec` naming the GraphQL connection field and its node
selection. `organization` itself is a singleton scalar fetch with no
connection at all. `team_members` and `team_repositories` are connections
nested under each team rather than under the organization directly, so they
paginate per team (`_fetch_team_connection`) rather than through the same
org-level paginator — deliberately NOT alias-batched across teams the way
Story 6 batches repositories (architecture.md Decision 2 scopes
alias-batching to repository-level Phase 2 requests specifically); an
organization's team count is small enough that sequential per-team
pagination is the simpler correct choice here, documented rather than
assumed.

Field lists below are a solid, representative subset of each GraphQL type's
scalar fields (exploration.md), not a literal enumeration of every field
FR-1 aspirationally describes — expanding a dataset's field list later is a
one-place, non-breaking change to the tables below.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from org_harvest.checkpoint import CURSOR_DONE, CheckpointStore
from org_harvest.credentials import CredentialProvider
from org_harvest.datasets import DatasetLevel, complete_fetch_details, get
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.gaps import DatasetOutcome, Gap
from org_harvest.graphql import extract_rate_limit_snapshot
from org_harvest.harvest.flatten import flatten_node
from org_harvest.harvest.systemic import SystemicFailureGuard
from org_harvest.hosts import ApiHost
from org_harvest.interrupt import InterruptGuard
from org_harvest.output import NdjsonWriter, count_records
from org_harvest.progress import ProgressCallback, ProgressEvent, ProgressEventKind
from org_harvest.selection import RepositoryFilter
from org_harvest.transport import Transport

_DEFAULT_PAGE_SIZE = 100

ORG_LEVEL_DATASET_NAMES: tuple[str, ...] = (
    "organization",
    "members",
    "pending_members",
    "teams",
    "team_members",
    "team_repositories",
    "repositories",
    "org_rulesets",
    "org_custom_properties",
    "org_domains",
    "org_ip_allow_list",
)


@dataclass(frozen=True)
class _ConnectionSpec:
    """One org-level (or per-team) GraphQL connection to page through."""

    dataset: str
    connection_field: str
    node_selection: str
    #: Extra field read off the *edge* rather than the node (e.g. a
    #: membership's `role`), written into the flattened record under this
    #: key. `None` means the connection has no interesting edge data, so
    #: `nodes { ... }` is queried directly instead of `edges { node { ... } }`.
    edge_field: str | None = None
    #: GraphQL node field carrying this record's identity. Falls back to
    #: `"id"` for every type that has a node id; the one dataset whose type
    #: doesn't (`org_custom_properties`) names its natural key instead, and
    #: `flatten_node` synthesizes `id` from it (AC-8.6).
    id_field: str = "id"
    record_fields: tuple[str, ...] = ()


_ORG_CONNECTIONS: tuple[_ConnectionSpec, ...] = (
    _ConnectionSpec(
        dataset="members",
        connection_field="membersWithRole",
        node_selection="id databaseId login name email createdAt",
        edge_field="role",
        record_fields=("id", "database_id", "login", "name", "email", "created_at", "role"),
    ),
    _ConnectionSpec(
        dataset="pending_members",
        connection_field="pendingMembers",
        node_selection="id databaseId login name email createdAt",
        record_fields=("id", "database_id", "login", "name", "email", "created_at"),
    ),
    _ConnectionSpec(
        dataset="teams",
        connection_field="teams",
        node_selection=(
            "id databaseId name slug description privacy notificationSetting createdAt updatedAt"
        ),
        record_fields=(
            "id",
            "database_id",
            "name",
            "slug",
            "description",
            "privacy",
            "notification_setting",
            "created_at",
            "updated_at",
        ),
    ),
    _ConnectionSpec(
        dataset="repositories",
        connection_field="repositories",
        node_selection=(
            "id databaseId name nameWithOwner isPrivate isArchived "
            "isFork isDisabled isEmpty visibility createdAt updatedAt pushedAt"
        ),
        record_fields=(
            "id",
            "database_id",
            "name",
            "name_with_owner",
            "is_private",
            "is_archived",
            "is_fork",
            "is_disabled",
            "is_empty",
            "visibility",
            "created_at",
            "updated_at",
            "pushed_at",
        ),
    ),
    _ConnectionSpec(
        dataset="org_rulesets",
        connection_field="rulesets",
        node_selection="id databaseId name target enforcement",
        record_fields=("id", "database_id", "name", "target", "enforcement"),
    ),
    _ConnectionSpec(
        dataset="org_custom_properties",
        connection_field="repositoryCustomProperties",
        node_selection="propertyName valueType required defaultValue allowedValues",
        id_field="propertyName",
        record_fields=(
            "id",
            "property_name",
            "value_type",
            "required",
            "default_value",
            "allowed_values",
        ),
    ),
    _ConnectionSpec(
        dataset="org_domains",
        connection_field="domains",
        node_selection="id domain isVerified isApproved createdAt",
        record_fields=("id", "domain", "is_verified", "is_approved", "created_at"),
    ),
    _ConnectionSpec(
        dataset="org_ip_allow_list",
        connection_field="ipAllowListEntries",
        node_selection="id allowListValue isActive name createdAt",
        record_fields=("id", "allow_list_value", "is_active", "name", "created_at"),
    ),
)

_TEAM_CONNECTIONS: tuple[_ConnectionSpec, ...] = (
    _ConnectionSpec(
        dataset="team_members",
        connection_field="members",
        node_selection="id login name email",
        edge_field="role",
        record_fields=("id", "login", "name", "email", "role", "team_id"),
    ),
    _ConnectionSpec(
        dataset="team_repositories",
        connection_field="repositories",
        node_selection="id name nameWithOwner",
        edge_field="permission",
        record_fields=("id", "name", "name_with_owner", "permission", "team_id"),
    ),
)

_ORGANIZATION_SCALAR_FIELDS = (
    "id databaseId login name description email location websiteUrl isVerified "
    "createdAt updatedAt requiresTwoFactorAuthentication "
    "membersCanForkPrivateRepositories webCommitSignoffRequired "
    "ipAllowListEnabledSetting"
)
_ORGANIZATION_RECORD_FIELDS = (
    "id",
    "database_id",
    "login",
    "name",
    "description",
    "email",
    "location",
    "website_url",
    "is_verified",
    "created_at",
    "updated_at",
    "requires_two_factor_authentication",
    "members_can_fork_private_repositories",
    "web_commit_signoff_required",
    "ip_allow_list_enabled_setting",
)


def register_fetch_details() -> None:
    """Completes the registry entries this story makes fetchable
    (`complete_fetch_details`), so Story 8's Parquet schema derivation has a
    flat field-name list to work from for every org-level default dataset.
    Idempotent — safe to call more than once per process."""
    complete_fetch_details("organization", fields=_ORGANIZATION_RECORD_FIELDS, parent_key=None)
    for spec in _ORG_CONNECTIONS:
        complete_fetch_details(spec.dataset, fields=spec.record_fields, parent_key=None)
    for spec in _TEAM_CONNECTIONS:
        complete_fetch_details(spec.dataset, fields=spec.record_fields, parent_key="team_id")


def _error_path(err: dict[str, Any]) -> str | None:
    path = err.get("path")
    if not path:
        return None
    return ".".join(str(p) for p in path)


@dataclass(frozen=True)
class OrgLevelResult:
    dataset_outcomes: tuple[DatasetOutcome, ...]
    #: True when the App's installation is scoped to selected repositories
    #: rather than all of them (EC-3, AC-5.8).
    scope_restricted: bool
    #: How many repositories this installation can actually reach —
    #: however many `repositories` records were successfully written.
    reachable_repository_count: int

    @property
    def gaps(self) -> tuple[Gap, ...]:
        return tuple(g for outcome in self.dataset_outcomes for g in outcome.gaps)

    @property
    def has_gaps(self) -> bool:
        return any(outcome.gaps for outcome in self.dataset_outcomes)


class _OrgLevelHarvester:
    def __init__(
        self,
        transport: Transport,
        *,
        org: str,
        snapshot_dir: Path,
        api_host: ApiHost,
        checkpoint: CheckpointStore,
        page_size: int,
        systemic_guard: SystemicFailureGuard,
        repository_filter: RepositoryFilter,
        interrupt: InterruptGuard,
    ) -> None:
        self._transport = transport
        self._org = org
        self._snapshot_dir = snapshot_dir
        self._url = api_host.graphql_url
        self._checkpoint = checkpoint
        self._page_size = page_size
        self._systemic_guard = systemic_guard
        self._repository_filter = repository_filter
        self._interrupt = interrupt

    @property
    def interrupted(self) -> bool:
        """Story 13, AC-4.11: `True` once the user has asked (once) to stop
        — checked between pages and between datasets so the current page
        finishes and its checkpoint write lands before anything stops."""
        return self._interrupt.requested

    def _writer(self, dataset: str) -> NdjsonWriter:
        return NdjsonWriter(self._snapshot_dir / f"{dataset}.ndjson")

    async def _query(
        self, query: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
        """Returns `(data, errors, transport_failure_reason)`. A non-`None`
        third element means the request itself never produced a usable
        response after Transport's own retries were exhausted (AC-5.3) —
        distinct from a GraphQL-level partial error, which comes back as a
        populated `errors` list alongside real `data`. Any other
        `OrgHarvestError` (auth failure, a refused rate-limit wait) is not
        a per-resource condition and is left to propagate, stopping the run
        cleanly per whichever AC governs that failure."""
        try:
            resp = await self._transport.send_graphql(
                self._url,
                payload={"query": query, "variables": variables},
                extract_budget=extract_rate_limit_snapshot,
            )
        except OrgHarvestError as exc:
            if exc.kind is ErrorKind.REQUEST_FAILED:
                self._systemic_guard.record_attempt(failed=True)
                return None, [], str(exc)
            raise
        resp.raise_for_status()
        body = resp.json()
        self._systemic_guard.record_attempt(failed=False)
        return body.get("data"), body.get("errors", []), None

    def _record_errors(
        self, dataset: str, resource_id: str, errors: list[dict[str, Any]]
    ) -> list[Gap]:
        gaps = []
        for err in errors:
            gap = Gap.now(
                dataset,
                resource_id=resource_id,
                field_path=_error_path(err),
                reason=err.get("message", "graphql error"),
            )
            gaps.append(gap)
            self._checkpoint.record_gap(gap)
        return gaps

    def _record_missing(self, dataset: str, resource_id: str, reason: str) -> Gap:
        gap = Gap.now(dataset, resource_id=resource_id, field_path=None, reason=reason)
        self._checkpoint.record_gap(gap)
        return gap

    def _is_already_complete(self, dataset: str) -> bool:
        return self._checkpoint.state.dataset_status.get(dataset) == "complete"

    def _resumed_outcome(self, dataset: str) -> DatasetOutcome:
        """AC-4.2/AC-4.5: this dataset was already marked complete by a
        prior attempt on this same checkpoint — skip re-fetching it
        entirely rather than issuing a wasted "no more pages" request.
        The record count comes from the NDJSON file itself (ground truth,
        tolerant of a truncated trailing line — AC-4.6); gaps come from
        whatever this dataset already recorded to the checkpoint's gap
        ledger, so a resumed run's manifest still reflects that history."""
        path = self._snapshot_dir / f"{dataset}.ndjson"
        gaps = tuple(
            Gap.from_dict(g) for g in self._checkpoint.state.gaps if g.get("dataset") == dataset
        )
        return DatasetOutcome(dataset, count_records(path), gaps)

    async def fetch_organization_scalar(self) -> DatasetOutcome:
        if self._is_already_complete("organization"):
            return self._resumed_outcome("organization")
        query = f"""
        query($org: String!) {{
          rateLimit {{ limit remaining resetAt cost nodeCount }}
          organization(login: $org) {{ {_ORGANIZATION_SCALAR_FIELDS} }}
        }}
        """
        data, errors, transport_failure = await self._query(query, {"org": self._org})
        gaps: list[Gap] = []
        with self._writer("organization") as writer:
            if transport_failure is not None:
                gaps.append(self._record_missing("organization", self._org, transport_failure))
            else:
                gaps.extend(self._record_errors("organization", self._org, errors))
                org_data = data.get("organization") if data else None
                if org_data is not None:
                    writer.write_record(flatten_node(org_data, edge_field=None, edge_value=None))
                elif not errors:
                    gaps.append(
                        self._record_missing(
                            "organization", self._org, "no data returned for the organization"
                        )
                    )
        self._checkpoint.set_dataset_status("organization", "complete")
        path = self._snapshot_dir / "organization.ndjson"
        return DatasetOutcome("organization", count_records(path), tuple(gaps))

    async def fetch_org_connection(self, spec: _ConnectionSpec) -> DatasetOutcome:
        if self._is_already_complete(spec.dataset):
            return self._resumed_outcome(spec.dataset)
        cursor = self._checkpoint.state.cursors.get(spec.dataset)
        gaps: list[Gap] = []
        selection = (
            f"edges {{ {spec.edge_field} node {{ {spec.node_selection} }} }}"
            if spec.edge_field
            else f"nodes {{ {spec.node_selection} }}"
        )
        with self._writer(spec.dataset) as writer:
            while True:
                query = f"""
                query($org: String!, $cursor: String, $pageSize: Int!) {{
                  rateLimit {{ limit remaining resetAt cost nodeCount }}
                  organization(login: $org) {{
                    {spec.connection_field}(first: $pageSize, after: $cursor) {{
                      pageInfo {{ hasNextPage endCursor }}
                      {selection}
                    }}
                  }}
                }}
                """
                data, errors, transport_failure = await self._query(
                    query, {"org": self._org, "cursor": cursor, "pageSize": self._page_size}
                )
                if transport_failure is not None:
                    gaps.append(self._record_missing(spec.dataset, self._org, transport_failure))
                    break
                gaps.extend(self._record_errors(spec.dataset, self._org, errors))
                connection = None
                if data and data.get("organization"):
                    connection = data["organization"].get(spec.connection_field)
                if connection is None:
                    if not errors:
                        gaps.append(
                            self._record_missing(
                                spec.dataset, self._org, "no data returned for this dataset"
                            )
                        )
                    break
                items = connection["edges"] if spec.edge_field else connection["nodes"]
                for item in items:
                    node = item["node"] if spec.edge_field else item
                    edge_value = item.get(spec.edge_field) if spec.edge_field else None
                    record = flatten_node(
                        node,
                        edge_field=spec.edge_field,
                        edge_value=edge_value,
                        id_field=spec.id_field,
                    )
                    if spec.dataset == "repositories" and not self._repository_filter.allows(
                        name=str(record.get("name", "")),
                        is_archived=bool(record.get("is_archived")),
                        is_fork=bool(record.get("is_fork")),
                    ):
                        continue
                    writer.write_record(record)
                page_info = connection["pageInfo"]
                cursor = page_info["endCursor"]
                self._checkpoint.set_cursor(spec.dataset, cursor)
                if not page_info["hasNextPage"] or self.interrupted:
                    break
        if not self.interrupted:
            self._checkpoint.set_dataset_status(spec.dataset, "complete")
        path = self._snapshot_dir / f"{spec.dataset}.ndjson"
        return DatasetOutcome(spec.dataset, count_records(path), tuple(gaps))

    async def fetch_team_connection(
        self, spec: _ConnectionSpec, teams: list[dict[str, Any]]
    ) -> DatasetOutcome:
        if self._is_already_complete(spec.dataset):
            return self._resumed_outcome(spec.dataset)
        gaps: list[Gap] = []
        selection = f"edges {{ {spec.edge_field} node {{ {spec.node_selection} }} }}"
        with self._writer(spec.dataset) as writer:
            for team in teams:
                if self.interrupted:
                    break
                team_id = team["id"]
                team_slug = team["slug"]
                cursor_key = f"{spec.dataset}:{team_id}"
                cursor = self._checkpoint.state.cursors.get(cursor_key)
                if cursor == CURSOR_DONE:
                    continue  # already fully fetched for this team in a prior attempt
                while True:
                    query = f"""
                    query($org: String!, $slug: String!, $cursor: String, $pageSize: Int!) {{
                      rateLimit {{ limit remaining resetAt cost nodeCount }}
                      organization(login: $org) {{
                        team(slug: $slug) {{
                          {spec.connection_field}(first: $pageSize, after: $cursor) {{
                            pageInfo {{ hasNextPage endCursor }}
                            {selection}
                          }}
                        }}
                      }}
                    }}
                    """
                    data, errors, transport_failure = await self._query(
                        query,
                        {
                            "org": self._org,
                            "slug": team_slug,
                            "cursor": cursor,
                            "pageSize": self._page_size,
                        },
                    )
                    if transport_failure is not None:
                        gaps.append(self._record_missing(spec.dataset, team_id, transport_failure))
                        break
                    gaps.extend(self._record_errors(spec.dataset, team_id, errors))
                    connection = None
                    team_data = data.get("organization", {}).get("team") if data else None
                    if team_data:
                        connection = team_data.get(spec.connection_field)
                    if connection is None:
                        if not errors:
                            gaps.append(
                                self._record_missing(
                                    spec.dataset, team_id, "no data returned for this team"
                                )
                            )
                        self._checkpoint.set_cursor(cursor_key, CURSOR_DONE)
                        break
                    for item in connection["edges"]:
                        node = item["node"]
                        record = flatten_node(
                            node, edge_field=spec.edge_field, edge_value=item.get(spec.edge_field)
                        )
                        record["team_id"] = team_id
                        writer.write_record(record)
                    page_info = connection["pageInfo"]
                    if page_info["hasNextPage"]:
                        cursor = page_info["endCursor"]
                        self._checkpoint.set_cursor(cursor_key, cursor)
                        if self.interrupted:
                            break
                    else:
                        self._checkpoint.set_cursor(cursor_key, CURSOR_DONE)
                        break
        if not self.interrupted:
            self._checkpoint.set_dataset_status(spec.dataset, "complete")
        path = self._snapshot_dir / f"{spec.dataset}.ndjson"
        return DatasetOutcome(spec.dataset, count_records(path), tuple(gaps))


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


async def fetch_organization_directory(
    transport: Transport,
    credentials: CredentialProvider,
    *,
    org: str,
    snapshot_dir: Path,
    api_host: ApiHost | None = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    checkpoint: CheckpointStore | None = None,
    systemic_guard: SystemicFailureGuard | None = None,
    dataset_names: Sequence[str] | None = None,
    repository_filter: RepositoryFilter | None = None,
    interrupt: InterruptGuard | None = None,
    team_ids: frozenset[str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> OrgLevelResult:
    """Fetches organization-level datasets (AC-1.2) and writes each to
    `snapshot_dir` as NDJSON. `dataset_names` narrows or expands the
    default org-level tier (Story 11, AC-2.1/AC-2.3) — `None` (the
    default) fetches every org-level dataset in `ORG_LEVEL_DATASET_NAMES`,
    matching this function's pre-Story-11 behavior exactly. A selected
    name this module has no connection spec for (an optional-tier dataset
    Story 11 makes *selectable* without implementing its fetch — see
    story-11's Scope) becomes a single explicit gap rather than silently
    producing nothing, consistent with never presenting an incomplete
    result as complete. `repository_filter` (AC-2.8) is applied while
    writing the `repositories` dataset, which is also Phase 2's fan-out
    source (architecture.md, Decision 4) — filtering there narrows both at
    once. `team_ids` (Story 14, AC-11.1) narrows `team_members`/
    `team_repositories` to just the named teams — `None` (the default)
    fetches every team, matching pre-Story-14 behavior; it has no effect
    on any other dataset. `on_progress` (Story 15, AC-9.4) is called once
    per dataset, right after that dataset's outcome is known.

    Raises `OrgHarvestError(kind=SYSTEMIC_FAILURE)` (FR-5, EC-8) if
    `systemic_guard` — shared with Phase 2 by the caller if desired, or left
    to default to a fresh one scoped just to this phase — trips its
    consecutive-failure or failure-rate threshold partway through."""
    register_fetch_details()
    host = api_host or ApiHost()
    selected = set(dataset_names) if dataset_names is not None else None
    effective_selection = (
        ORG_LEVEL_DATASET_NAMES
        if selected is None
        else tuple(name for name in ORG_LEVEL_DATASET_NAMES if name in selected)
    )
    if checkpoint is None:
        checkpoint = CheckpointStore.create(
            snapshot_dir / "checkpoint.json",
            org=org,
            dataset_selection=effective_selection,
        )
    harvester = _OrgLevelHarvester(
        transport,
        org=org,
        snapshot_dir=snapshot_dir,
        api_host=host,
        checkpoint=checkpoint,
        page_size=page_size,
        systemic_guard=systemic_guard or SystemicFailureGuard(),
        repository_filter=repository_filter or RepositoryFilter(),
        interrupt=interrupt or InterruptGuard(),
    )

    outcomes: list[DatasetOutcome] = []

    def _emit(outcome: DatasetOutcome) -> None:
        outcomes.append(outcome)
        if on_progress is not None:
            on_progress(
                ProgressEvent(
                    kind=ProgressEventKind.DATASET_COMPLETE,
                    message=f"{outcome.name}: {outcome.record_count} record(s), "
                    f"{len(outcome.gaps)} gap(s)",
                    dataset=outcome.name,
                    record_count=outcome.record_count,
                    gap_count=len(outcome.gaps),
                )
            )

    if (selected is None or "organization" in selected) and not harvester.interrupted:
        _emit(await harvester.fetch_organization_scalar())

    connections_by_name = {spec.dataset: spec for spec in _ORG_CONNECTIONS}
    reachable_repository_count = 0
    for name in ("members", "pending_members", "teams", "repositories"):
        if harvester.interrupted:
            break
        if selected is not None and name not in selected:
            continue
        outcome = await harvester.fetch_org_connection(connections_by_name[name])
        _emit(outcome)
        if name == "repositories":
            reachable_repository_count = outcome.record_count
    for name in ("org_rulesets", "org_custom_properties", "org_domains", "org_ip_allow_list"):
        if harvester.interrupted:
            break
        if selected is None or name in selected:
            _emit(await harvester.fetch_org_connection(connections_by_name[name]))

    team_connections_by_name = {spec.dataset: spec for spec in _TEAM_CONNECTIONS}
    needs_team_members = selected is None or "team_members" in selected
    needs_team_repositories = selected is None or "team_repositories" in selected
    if not harvester.interrupted and (needs_team_members or needs_team_repositories):
        teams = _read_ndjson(snapshot_dir / "teams.ndjson")
        if team_ids is not None:
            # Story 14, AC-11.1: retry-gaps narrows to just the teams that
            # gapped, rather than re-paginating every team's connection.
            teams = [t for t in teams if t["id"] in team_ids]
        if needs_team_members:
            _emit(
                await harvester.fetch_team_connection(
                    team_connections_by_name["team_members"], teams
                )
            )
        if needs_team_repositories and not harvester.interrupted:
            _emit(
                await harvester.fetch_team_connection(
                    team_connections_by_name["team_repositories"], teams
                )
            )

    if selected is not None and not harvester.interrupted:
        implemented = {"organization", *connections_by_name, *team_connections_by_name}
        for name in sorted(selected - implemented):
            if get(name).level is not DatasetLevel.ORGANIZATION:
                continue
            gap = Gap.now(
                name, resource_id=None, field_path=None, reason="dataset not yet implemented"
            )
            checkpoint.record_gap(gap)
            _emit(DatasetOutcome(name, 0, (gap,)))

    return OrgLevelResult(
        dataset_outcomes=tuple(outcomes),
        scope_restricted=credentials.repository_selection == "selected",
        reachable_repository_count=reachable_repository_count,
    )
