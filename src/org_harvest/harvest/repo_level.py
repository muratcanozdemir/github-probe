"""Phase 2 (architecture.md, Decision 4): fetches all fifteen
repository-level default-tier datasets (AC-1.2) across every repository in
the org, batching multiple repositories per GraphQL request via aliases
(architecture.md, Decision 2).

Query shape: one dataset at a time, one generic batching paginator
(`_RepoLevelHarvester.fetch_repo_dataset`) fans out over every
still-pending repository for that dataset. Each round trip aliases up to
`batch_width` repositories under the root `repository(owner, name)` field:

    query(...) {
      rateLimit { limit remaining resetAt cost nodeCount }
      repo0: repository(owner: $owner, name: $repo0_name) {
        id
        issues(first: $pageSize, after: $repo0_cursor) { pageInfo { ... } nodes { ... } }
      }
      repo1: repository(owner: $owner, name: $repo1_name) { ... }
      ...
    }

Per-repo cursor state lives outside the query text (in `_RepoState`), so a
batch is whatever repositories are currently pending this dataset's next
page — never a fixed grouping (Decision 2). A GraphQL error's `path[0]`
names the alias that produced it, which is mapped back to that alias's
repository before the gap is recorded (`errors[].path`-based attribution —
Decision 2), so one bad repository never obscures — or is obscured by —
its batch-mates (AC-5.7). A transport-level failure (exhausted retries)
invalidates the whole batch, since nothing in it executed.

Node-limit handling (AC-7.8, EC-16) tries a smaller page size first; if a
single-page-size batch of more than one repository still exceeds the node
limit, the batch itself is split and retried at the original page size —
both levers exist because the 500,000-node ceiling applies to the request
as a whole, and either dimension alone may not be enough to fit under it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from org_harvest.checkpoint import CURSOR_DONE, CheckpointStore
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
from org_harvest.transport import Transport

_DEFAULT_PAGE_SIZE = 50
_DEFAULT_BATCH_WIDTH = 10
_MIN_PAGE_SIZE = 1

REPO_LEVEL_DATASET_NAMES: tuple[str, ...] = (
    "issues",
    "pull_requests",
    "discussions",
    "releases",
    "labels",
    "milestones",
    "collaborators",
    "branch_protection_rules",
    "repo_rulesets",
    "repo_custom_property_values",
    "environments",
    "deployments",
    "vulnerability_alerts",
    "topics",
    "languages",
)


@dataclass(frozen=True)
class _RepoConnectionSpec:
    """One repository-level GraphQL field to page through (or, when
    `paginated` is `False`, to fetch wholesale in a single pass — some
    Repository fields, like `customPropertyValues`, are a plain list with
    no `first`/`after`/`pageInfo` shape at all)."""

    dataset: str
    connection_field: str
    node_selection: str
    edge_field: str | None = None
    id_field: str = "id"
    paginated: bool = True
    record_fields: tuple[str, ...] = ()


_REPO_CONNECTIONS: tuple[_RepoConnectionSpec, ...] = (
    _RepoConnectionSpec(
        dataset="issues",
        connection_field="issues",
        node_selection=(
            "id number title state createdAt updatedAt closedAt "
            "author { login } milestone { id title } "
            "labels(first: 20) { nodes { name } } "
            "assignees(first: 20) { nodes { login } }"
        ),
        record_fields=(
            "id",
            "number",
            "title",
            "state",
            "created_at",
            "updated_at",
            "closed_at",
            "author",
            "milestone",
            "labels",
            "assignees",
        ),
    ),
    _RepoConnectionSpec(
        dataset="pull_requests",
        connection_field="pullRequests",
        node_selection=(
            "id number title state createdAt updatedAt closedAt mergedAt "
            "author { login } milestone { id title } "
            "labels(first: 20) { nodes { name } } "
            "assignees(first: 20) { nodes { login } }"
        ),
        record_fields=(
            "id",
            "number",
            "title",
            "state",
            "created_at",
            "updated_at",
            "closed_at",
            "merged_at",
            "author",
            "milestone",
            "labels",
            "assignees",
        ),
    ),
    _RepoConnectionSpec(
        dataset="discussions",
        connection_field="discussions",
        node_selection="id number title createdAt updatedAt author { login } answerChosenAt",
        record_fields=(
            "id",
            "number",
            "title",
            "created_at",
            "updated_at",
            "author",
            "answer_chosen_at",
        ),
    ),
    _RepoConnectionSpec(
        dataset="releases",
        connection_field="releases",
        node_selection=(
            "id name tagName createdAt publishedAt isDraft isPrerelease author { login }"
        ),
        record_fields=(
            "id",
            "name",
            "tag_name",
            "created_at",
            "published_at",
            "is_draft",
            "is_prerelease",
            "author",
        ),
    ),
    _RepoConnectionSpec(
        dataset="labels",
        connection_field="labels",
        node_selection="id name color description",
        record_fields=("id", "name", "color", "description"),
    ),
    _RepoConnectionSpec(
        dataset="milestones",
        connection_field="milestones",
        node_selection="id number title state createdAt dueOn",
        record_fields=("id", "number", "title", "state", "created_at", "due_on"),
    ),
    _RepoConnectionSpec(
        dataset="collaborators",
        connection_field="collaborators",
        node_selection="id login",
        edge_field="permission",
        record_fields=("id", "login", "permission"),
    ),
    _RepoConnectionSpec(
        dataset="branch_protection_rules",
        connection_field="branchProtectionRules",
        node_selection=(
            "id pattern requiresApprovingReviews requiredApprovingReviewCount requiresStatusChecks"
        ),
        record_fields=(
            "id",
            "pattern",
            "requires_approving_reviews",
            "required_approving_review_count",
            "requires_status_checks",
        ),
    ),
    _RepoConnectionSpec(
        dataset="repo_rulesets",
        connection_field="rulesets",
        node_selection="id databaseId name target enforcement",
        record_fields=("id", "database_id", "name", "target", "enforcement"),
    ),
    _RepoConnectionSpec(
        dataset="repo_custom_property_values",
        connection_field="customPropertyValues",
        node_selection="propertyName value",
        id_field="propertyName",
        paginated=False,
        record_fields=("id", "property_name", "value"),
    ),
    _RepoConnectionSpec(
        dataset="environments",
        connection_field="environments",
        node_selection="id name",
        record_fields=("id", "name"),
    ),
    _RepoConnectionSpec(
        dataset="deployments",
        connection_field="deployments",
        node_selection="id state createdAt updatedAt",
        record_fields=("id", "state", "created_at", "updated_at"),
    ),
    _RepoConnectionSpec(
        dataset="vulnerability_alerts",
        connection_field="vulnerabilityAlerts",
        node_selection=("id state createdAt securityVulnerability { severity package { name } }"),
        record_fields=("id", "state", "created_at", "security_vulnerability"),
    ),
    _RepoConnectionSpec(
        dataset="topics",
        connection_field="repositoryTopics",
        node_selection="id topic { name }",
        record_fields=("id", "topic"),
    ),
    _RepoConnectionSpec(
        dataset="languages",
        connection_field="languages",
        node_selection="name",
        edge_field="size",
        id_field="name",
        record_fields=("id", "name", "size"),
    ),
)


def register_fetch_details() -> None:
    """Completes the registry entries this story makes fetchable, parallel
    to Story 5's `org_level.register_fetch_details()`. Idempotent."""
    for spec in _REPO_CONNECTIONS:
        complete_fetch_details(spec.dataset, fields=spec.record_fields, parent_key="repository_id")


def _is_node_limit_error(err: dict[str, Any]) -> bool:
    """Best-effort detection (EC-16): GitHub's node-limit rejection has no
    single documented, stable shape, so this matches on the `type` field
    GitHub is known to use plus a message substring as a fallback."""
    if err.get("type") == "MAX_NODE_LIMIT_EXCEEDED":
        return True
    return "node limit" in str(err.get("message", "")).lower()


def _error_path_after_alias(err: dict[str, Any]) -> str | None:
    path = err.get("path") or []
    rest = path[1:]
    if not rest:
        return None
    return ".".join(str(p) for p in rest)


@dataclass
class _RepoState:
    id: str
    name: str
    cursor: str | None = None


@dataclass
class _BatchResult:
    written: int = 0
    gaps: list[Gap] = field(default_factory=list)
    still_pending: list[_RepoState] = field(default_factory=list)


@dataclass(frozen=True)
class RepoLevelResult:
    dataset_outcomes: tuple[DatasetOutcome, ...]

    @property
    def gaps(self) -> tuple[Gap, ...]:
        return tuple(g for outcome in self.dataset_outcomes for g in outcome.gaps)

    @property
    def has_gaps(self) -> bool:
        return any(outcome.gaps for outcome in self.dataset_outcomes)


def _read_repositories(snapshot_dir: Path) -> list[_RepoState]:
    import json

    path = snapshot_dir / "repositories.ndjson"
    if not path.exists():
        return []
    repos = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            repos.append(_RepoState(id=record["id"], name=record["name"]))
    return repos


class _RepoLevelHarvester:
    def __init__(
        self,
        transport: Transport,
        *,
        org: str,
        snapshot_dir: Path,
        api_host: ApiHost,
        checkpoint: CheckpointStore,
        page_size: int,
        batch_width: int,
        systemic_guard: SystemicFailureGuard,
        item_cap: int | None,
        interrupt: InterruptGuard,
    ) -> None:
        self._transport = transport
        self._org = org
        self._snapshot_dir = snapshot_dir
        self._url = api_host.graphql_url
        self._checkpoint = checkpoint
        self._page_size = page_size
        self._batch_width = batch_width
        self._writers: dict[str, NdjsonWriter] = {}
        self._systemic_guard = systemic_guard
        #: AC-2.9 — caps how many items `fetch_repo_dataset` collects per
        #: repository for the dataset it's currently fetching. `None` means
        #: uncapped (pre-Story-11 behavior).
        self._item_cap = item_cap
        self._interrupt = interrupt

    @property
    def interrupted(self) -> bool:
        """Story 13, AC-4.11 — mirrors `_OrgLevelHarvester.interrupted`."""
        return self._interrupt.requested

    async def _query(
        self, query: str, variables: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
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

    def _build_query(
        self, spec: _RepoConnectionSpec, batch: list[_RepoState], page_size: int
    ) -> tuple[str, dict[str, Any]]:
        variables: dict[str, Any] = {"owner": self._org, "pageSize": page_size}
        var_decls = ["$owner: String!", "$pageSize: Int!"]
        parts = []
        for i, repo in enumerate(batch):
            alias = f"repo{i}"
            variables[f"{alias}_name"] = repo.name
            var_decls.append(f"${alias}_name: String!")
            if spec.paginated:
                variables[f"{alias}_cursor"] = repo.cursor
                var_decls.append(f"${alias}_cursor: String")
                field_call = f"{spec.connection_field}(first: $pageSize, after: ${alias}_cursor)"
                inner = f"pageInfo {{ hasNextPage endCursor }} {_selection(spec)}"
            else:
                field_call = spec.connection_field
                inner = spec.node_selection
            parts.append(
                f"{alias}: repository(owner: $owner, name: ${alias}_name) {{ "
                f"id {field_call} {{ {inner} }} }}"
            )
        query = (
            f"query({', '.join(var_decls)}) {{ "
            f"rateLimit {{ limit remaining resetAt cost nodeCount }} "
            f"{' '.join(parts)} }}"
        )
        return query, variables

    async def _run_batch(
        self,
        spec: _RepoConnectionSpec,
        batch: list[_RepoState],
        page_size: int,
        written_per_repo: dict[str, int],
    ) -> _BatchResult:
        query, variables = self._build_query(spec, batch, page_size)
        data, errors, transport_failure = await self._query(query, variables)

        if transport_failure is not None:
            gaps = [
                self._record_missing(spec.dataset, repo.id, transport_failure) for repo in batch
            ]
            return _BatchResult(gaps=gaps)

        if any(_is_node_limit_error(e) for e in errors):
            if page_size > _MIN_PAGE_SIZE:
                return await self._run_batch(
                    spec, batch, max(_MIN_PAGE_SIZE, page_size // 2), written_per_repo
                )
            if len(batch) > 1:
                mid = len(batch) // 2
                left = await self._run_batch(spec, batch[:mid], self._page_size, written_per_repo)
                right = await self._run_batch(spec, batch[mid:], self._page_size, written_per_repo)
                return _BatchResult(
                    written=left.written + right.written,
                    gaps=[*left.gaps, *right.gaps],
                    still_pending=[*left.still_pending, *right.still_pending],
                )
            gap = self._record_missing(
                spec.dataset,
                batch[0].id,
                "node limit exceeded even at minimum page size and batch width",
            )
            # Permanent, not transient — a resumed run gains nothing by
            # re-attempting the same unrecoverable query (AC-4.5).
            self._checkpoint.set_cursor(f"{spec.dataset}:{batch[0].id}", CURSOR_DONE)
            return _BatchResult(gaps=[gap])

        errors_by_alias: dict[str, list[dict[str, Any]]] = {}
        for err in errors:
            path = err.get("path") or []
            if path:
                errors_by_alias.setdefault(str(path[0]), []).append(err)

        result = _BatchResult()
        for i, repo in enumerate(batch):
            alias = f"repo{i}"
            alias_errors = errors_by_alias.get(alias, [])
            for err in alias_errors:
                gap = Gap.now(
                    spec.dataset,
                    resource_id=repo.id,
                    field_path=_error_path_after_alias(err),
                    reason=err.get("message", "graphql error"),
                )
                result.gaps.append(gap)
                self._checkpoint.record_gap(gap)

            repo_data = data.get(alias) if data else None
            if repo_data is None:
                if not alias_errors:
                    result.gaps.append(
                        self._record_missing(
                            spec.dataset, repo.id, "no data returned for this repository"
                        )
                    )
                self._checkpoint.set_cursor(f"{spec.dataset}:{repo.id}", CURSOR_DONE)
                continue

            connection = repo_data.get(spec.connection_field)
            if connection is None:
                if not alias_errors:
                    result.gaps.append(
                        self._record_missing(
                            spec.dataset, repo.id, "no data returned for this dataset"
                        )
                    )
                self._checkpoint.set_cursor(f"{spec.dataset}:{repo.id}", CURSOR_DONE)
                continue

            if spec.paginated:
                items = connection["edges"] if spec.edge_field else connection["nodes"]
            else:
                items = connection

            written_so_far = written_per_repo.get(repo.id, 0)
            for item in items:
                if self._item_cap is not None and written_so_far >= self._item_cap:
                    break
                node = item["node"] if spec.edge_field and spec.paginated else item
                edge_value = (
                    item.get(spec.edge_field) if spec.edge_field and spec.paginated else None
                )
                record = flatten_node(
                    node, edge_field=spec.edge_field, edge_value=edge_value, id_field=spec.id_field
                )
                record["repository_id"] = repo.id
                self._writer(spec.dataset).write_record(record)
                result.written += 1
                written_so_far += 1
            written_per_repo[repo.id] = written_so_far

            if spec.paginated:
                page_info = connection["pageInfo"]
                reached_cap = self._item_cap is not None and written_so_far >= self._item_cap
                if page_info["hasNextPage"] and not reached_cap:
                    repo.cursor = page_info["endCursor"]
                    result.still_pending.append(repo)
                    self._checkpoint.set_cursor(f"{spec.dataset}:{repo.id}", repo.cursor)
                else:
                    self._checkpoint.set_cursor(f"{spec.dataset}:{repo.id}", CURSOR_DONE)
            else:
                self._checkpoint.set_cursor(f"{spec.dataset}:{repo.id}", CURSOR_DONE)

        return result

    def _writer(self, dataset: str) -> NdjsonWriter:
        if dataset not in self._writers:
            self._writers[dataset] = NdjsonWriter(self._snapshot_dir / f"{dataset}.ndjson")
        return self._writers[dataset]

    def close_writers(self) -> None:
        for writer in self._writers.values():
            writer.close()
        self._writers.clear()

    def _record_missing(self, dataset: str, resource_id: str, reason: str) -> Gap:
        gap = Gap.now(dataset, resource_id=resource_id, field_path=None, reason=reason)
        self._checkpoint.record_gap(gap)
        return gap

    async def fetch_repo_dataset(
        self, spec: _RepoConnectionSpec, repos: list[_RepoState]
    ) -> DatasetOutcome:
        if self._checkpoint.state.dataset_status.get(spec.dataset) == "complete":
            return self._resumed_outcome(spec.dataset)

        # Each `_RepoState` is shared across every dataset's fetch (the same
        # objects are reused for every spec in `fetch_repository_datasets`'s
        # loop), so this dataset's own checkpoint state — not whatever a
        # previous dataset's fetch left on the object — decides where each
        # repository starts: `None` if never attempted for this dataset, a
        # real cursor if a prior attempt got partway through paginating it
        # (AC-4.2, AC-4.5), or skipped entirely if already `CURSOR_DONE`
        # (fully paginated, or ended in a recorded gap, in a prior attempt).
        queue: list[_RepoState] = []
        for repo in repos:
            stored = self._checkpoint.state.cursors.get(f"{spec.dataset}:{repo.id}")
            if stored == CURSOR_DONE:
                continue
            repo.cursor = stored
            queue.append(repo)

        gaps: list[Gap] = []
        written_per_repo: dict[str, int] = {}
        while queue:
            if self.interrupted:
                break
            batch, queue = queue[: self._batch_width], queue[self._batch_width :]
            result = await self._run_batch(spec, batch, self._page_size, written_per_repo)
            gaps.extend(result.gaps)
            queue.extend(result.still_pending)
        if not self.interrupted:
            self._checkpoint.set_dataset_status(spec.dataset, "complete")
        path = self._snapshot_dir / f"{spec.dataset}.ndjson"
        return DatasetOutcome(spec.dataset, count_records(path), tuple(gaps))

    def _resumed_outcome(self, dataset: str) -> DatasetOutcome:
        """AC-4.2/AC-4.5: mirrors `org_level._OrgLevelHarvester._resumed_outcome` —
        this whole dataset was already marked complete by a prior attempt,
        so skip re-fetching it entirely."""
        path = self._snapshot_dir / f"{dataset}.ndjson"
        gaps = tuple(
            Gap.from_dict(g) for g in self._checkpoint.state.gaps if g.get("dataset") == dataset
        )
        return DatasetOutcome(dataset, count_records(path), gaps)


def _selection(spec: _RepoConnectionSpec) -> str:
    if spec.edge_field:
        return f"edges {{ {spec.edge_field} node {{ {spec.node_selection} }} }}"
    return f"nodes {{ {spec.node_selection} }}"


async def fetch_repository_datasets(
    transport: Transport,
    *,
    org: str,
    snapshot_dir: Path,
    api_host: ApiHost | None = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    batch_width: int = _DEFAULT_BATCH_WIDTH,
    checkpoint: CheckpointStore | None = None,
    systemic_guard: SystemicFailureGuard | None = None,
    dataset_names: Sequence[str] | None = None,
    item_cap: int | None = None,
    interrupt: InterruptGuard | None = None,
    repository_ids: frozenset[str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> RepoLevelResult:
    """Fetches repository-level datasets (AC-1.2) for every repository
    Story 5 wrote to `repositories.ndjson`, and writes each dataset to
    `snapshot_dir` as NDJSON. Requires Phase 1 to have already run in this
    `snapshot_dir` (architecture.md, Decision 4) — an empty repository
    list is a valid, empty result, not an error (EC-1).

    `dataset_names` narrows or expands the default repo-level tier (Story
    11, AC-2.1/AC-2.3) the same way `fetch_organization_directory`'s does
    — `None` fetches every dataset in `REPO_LEVEL_DATASET_NAMES`, matching
    this function's pre-Story-11 behavior exactly; a selected name with no
    connection spec here (an optional-tier dataset Story 11 makes
    *selectable* without implementing its fetch) becomes a single explicit
    gap. `item_cap` (AC-2.9) stops collecting further items for a
    repository, dataset pair once that many have been written, even if
    more pages remain. `repository_ids` (Story 14, AC-11.1) narrows the
    fan-out to just the named repositories — `None` (the default) fans out
    over every repository, matching pre-Story-14 behavior. `on_progress`
    (Story 15, AC-9.4) is called once per dataset, right after that
    dataset's outcome (across every repository) is known.

    Raises `OrgHarvestError(kind=SYSTEMIC_FAILURE)` (FR-5, EC-8) if
    `systemic_guard` — shared with Phase 1 by the caller if desired, or left
    to default to a fresh one scoped just to this phase — trips its
    consecutive-failure or failure-rate threshold partway through."""
    register_fetch_details()
    host = api_host or ApiHost()
    selected = set(dataset_names) if dataset_names is not None else None
    effective_selection = (
        REPO_LEVEL_DATASET_NAMES
        if selected is None
        else tuple(name for name in REPO_LEVEL_DATASET_NAMES if name in selected)
    )
    if checkpoint is None:
        checkpoint = CheckpointStore.create(
            snapshot_dir / "checkpoint.json",
            org=org,
            dataset_selection=effective_selection,
        )
    repos = _read_repositories(snapshot_dir)
    if repository_ids is not None:
        repos = [r for r in repos if r.id in repository_ids]
    harvester = _RepoLevelHarvester(
        transport,
        org=org,
        snapshot_dir=snapshot_dir,
        api_host=host,
        checkpoint=checkpoint,
        page_size=page_size,
        batch_width=batch_width,
        systemic_guard=systemic_guard or SystemicFailureGuard(),
        item_cap=item_cap,
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

    try:
        for spec in _REPO_CONNECTIONS:
            if harvester.interrupted:
                break
            if selected is None or spec.dataset in selected:
                _emit(await harvester.fetch_repo_dataset(spec, list(repos)))
    finally:
        harvester.close_writers()

    if selected is not None and not harvester.interrupted:
        implemented = {spec.dataset for spec in _REPO_CONNECTIONS}
        for name in sorted(selected - implemented):
            if get(name).level is not DatasetLevel.REPOSITORY:
                continue
            gap = Gap.now(
                name, resource_id=None, field_path=None, reason="dataset not yet implemented"
            )
            checkpoint.record_gap(gap)
            _emit(DatasetOutcome(name, 0, (gap,)))

    return RepoLevelResult(dataset_outcomes=tuple(outcomes))
