from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from org_harvest.checkpoint import CheckpointStore
from org_harvest.credentials import StaticTokenCredentialProvider
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.harvest.repo_level import (
    _REPO_CONNECTIONS,
    REPO_LEVEL_DATASET_NAMES,
    _RepoLevelHarvester,
    _RepoState,
    fetch_repository_datasets,
)
from org_harvest.harvest.systemic import SystemicFailureGuard
from org_harvest.hosts import ApiHost
from org_harvest.transport import Transport

GITHUB = "https://api.github.com"

_CANNED_NODES: dict[str, dict[str, Any]] = {
    "issues": {
        "id": "I_1",
        "number": 1,
        "title": "Bug",
        "state": "OPEN",
        "createdAt": "2020-01-01T00:00:00Z",
        "updatedAt": "2020-01-02T00:00:00Z",
        "closedAt": None,
        "author": {"login": "alice"},
        "milestone": None,
        "labels": {"nodes": [{"name": "bug"}]},
        "assignees": {"nodes": [{"login": "bob"}]},
    },
    "pull_requests": {
        "id": "PR_1",
        "number": 1,
        "title": "Fix",
        "state": "OPEN",
        "createdAt": "2020-01-01T00:00:00Z",
        "updatedAt": "2020-01-02T00:00:00Z",
        "closedAt": None,
        "mergedAt": None,
        "author": {"login": "alice"},
        "milestone": None,
        "labels": {"nodes": []},
        "assignees": {"nodes": []},
    },
    "discussions": {
        "id": "D_1",
        "number": 1,
        "title": "Question",
        "createdAt": "2020-01-01T00:00:00Z",
        "updatedAt": "2020-01-01T00:00:00Z",
        "author": {"login": "alice"},
        "answerChosenAt": None,
    },
    "releases": {
        "id": "REL_1",
        "name": "v1",
        "tagName": "v1.0.0",
        "createdAt": "2020-01-01T00:00:00Z",
        "publishedAt": "2020-01-01T00:00:00Z",
        "isDraft": False,
        "isPrerelease": False,
        "author": {"login": "alice"},
    },
    "labels": {"id": "L_1", "name": "bug", "color": "ff0000", "description": None},
    "milestones": {
        "id": "M_1",
        "number": 1,
        "title": "v1",
        "state": "OPEN",
        "createdAt": "2020-01-01T00:00:00Z",
        "dueOn": None,
    },
    "collaborators": {"id": "U_1", "login": "alice"},
    "branch_protection_rules": {
        "id": "BPR_1",
        "pattern": "main",
        "requiresApprovingReviews": True,
        "requiredApprovingReviewCount": 1,
        "requiresStatusChecks": True,
    },
    "repo_rulesets": {
        "id": "RRS_1",
        "databaseId": 1,
        "name": "protect",
        "target": "BRANCH",
        "enforcement": "ACTIVE",
    },
    "repo_custom_property_values": {"propertyName": "team", "value": "core"},
    "environments": {"id": "ENV_1", "name": "production"},
    "deployments": {
        "id": "DEP_1",
        "state": "SUCCESS",
        "createdAt": "2020-01-01T00:00:00Z",
        "updatedAt": "2020-01-01T00:00:00Z",
    },
    "vulnerability_alerts": {
        "id": "VA_1",
        "state": "OPEN",
        "createdAt": "2020-01-01T00:00:00Z",
        "securityVulnerability": {"severity": "HIGH", "package": {"name": "lodash"}},
    },
    "topics": {"id": "TOP_1", "topic": {"name": "python"}},
    "languages": {"name": "Python"},
}
_CANNED_EDGE_VALUES: dict[str, Any] = {"collaborators": "WRITE", "languages": 12345}


def _rate_limit(remaining: int = 4000) -> dict:
    reset_at = (datetime.now(UTC) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    return {"limit": 5000, "remaining": remaining, "resetAt": reset_at, "cost": 1, "nodeCount": 1}


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_repositories(snapshot_dir: Path, repos: list[tuple[str, str]]) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    with (snapshot_dir / "repositories.ndjson").open("w", encoding="utf-8") as f:
        for repo_id, name in repos:
            f.write(json.dumps({"id": repo_id, "name": name}) + "\n")


def _match_spec(query: str):
    for spec in _REPO_CONNECTIONS:
        marker = f"{spec.connection_field}(" if spec.paginated else f"{spec.connection_field} {{"
        if marker in query:
            return spec
    return None


def _happy_path_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    query = body["query"]
    variables = body["variables"]
    spec = _match_spec(query)
    assert spec is not None, query
    data: dict[str, Any] = {}
    i = 0
    while f"repo{i}_name" in variables:
        alias = f"repo{i}"
        node = dict(_CANNED_NODES[spec.dataset])
        if spec.paginated:
            if spec.edge_field:
                inner: Any = {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "edges": [{spec.edge_field: _CANNED_EDGE_VALUES[spec.dataset], "node": node}],
                }
            else:
                inner = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [node]}
        else:
            inner = [node]
        data[alias] = {"id": f"R_{i}", spec.connection_field: inner}
        i += 1
    return httpx.Response(200, json={"data": {"rateLimit": _rate_limit(), **data}})


class TestHappyPath:
    async def test_fetches_all_fifteen_repo_level_datasets(self, tmp_path: Path):
        snapshot_dir = tmp_path / "snapshot"
        _write_repositories(snapshot_dir, [("R_1", "repo1"), ("R_2", "repo2")])
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=_happy_path_handler)
            result = await fetch_repository_datasets(
                transport, org="acme", snapshot_dir=snapshot_dir
            )
        assert {o.name for o in result.dataset_outcomes} == set(REPO_LEVEL_DATASET_NAMES)
        assert not result.has_gaps
        for outcome in result.dataset_outcomes:
            assert outcome.record_count == 2  # one record per repo
        await transport.aclose()

    async def test_records_carry_repository_id_parent_key_ac_8_6(self, tmp_path: Path):
        snapshot_dir = tmp_path / "snapshot"
        _write_repositories(snapshot_dir, [("R_1", "repo1")])
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=_happy_path_handler)
            await fetch_repository_datasets(transport, org="acme", snapshot_dir=snapshot_dir)
        issues = _read_lines(snapshot_dir / "issues.ndjson")
        assert issues[0]["repository_id"] == "R_1"
        assert issues[0]["id"] == "I_1"
        assert issues[0]["author"] == {"login": "alice"}
        await transport.aclose()

    async def test_synthesizes_identity_for_types_with_no_node_id(self, tmp_path: Path):
        snapshot_dir = tmp_path / "snapshot"
        _write_repositories(snapshot_dir, [("R_1", "repo1")])
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=_happy_path_handler)
            await fetch_repository_datasets(transport, org="acme", snapshot_dir=snapshot_dir)
        props = _read_lines(snapshot_dir / "repo_custom_property_values.ndjson")
        assert props[0]["id"] == "team"
        languages = _read_lines(snapshot_dir / "languages.ndjson")
        assert languages[0]["id"] == "Python"
        assert languages[0]["size"] == 12345
        await transport.aclose()

    async def test_collaborators_carry_edge_permission(self, tmp_path: Path):
        snapshot_dir = tmp_path / "snapshot"
        _write_repositories(snapshot_dir, [("R_1", "repo1")])
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=_happy_path_handler)
            await fetch_repository_datasets(transport, org="acme", snapshot_dir=snapshot_dir)
        collaborators = _read_lines(snapshot_dir / "collaborators.ndjson")
        assert collaborators[0]["permission"] == "WRITE"
        await transport.aclose()

    async def test_checkpoint_marks_every_dataset_complete(self, tmp_path: Path):
        snapshot_dir = tmp_path / "snapshot"
        _write_repositories(snapshot_dir, [("R_1", "repo1")])
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=_happy_path_handler)
            await fetch_repository_datasets(transport, org="acme", snapshot_dir=snapshot_dir)
        checkpoint = CheckpointStore.load(snapshot_dir / "checkpoint.json")
        for name in REPO_LEVEL_DATASET_NAMES:
            assert checkpoint.dataset_status[name] == "complete"
        await transport.aclose()


class TestNoRepositories:
    async def test_empty_repository_list_is_a_valid_empty_result_ec_1(self, tmp_path: Path):
        """No `repositories.ndjson` (or an empty one) means no repository to
        fan out over — zero records for every dataset, no network calls at
        all, not an error."""
        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir(parents=True)
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url=GITHUB):
            result = await fetch_repository_datasets(
                transport, org="acme", snapshot_dir=snapshot_dir
            )
        assert all(o.record_count == 0 for o in result.dataset_outcomes)
        assert not result.has_gaps
        await transport.aclose()


class TestPartialFailureAttribution:
    async def test_one_bad_repository_in_a_batch_does_not_block_its_batch_mates_ac_5_7(
        self, tmp_path: Path
    ):
        snapshot_dir = tmp_path / "snapshot"
        _write_repositories(snapshot_dir, [("R_1", "repo1"), ("R_2", "repo2")])

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            query = body["query"]
            if "issues(" not in query:
                return _happy_path_handler(request)
            node = dict(_CANNED_NODES["issues"])
            data = {
                "repo0": {
                    "id": "R_1",
                    "issues": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [node],
                    },
                },
                "repo1": None,
            }
            errors = [
                {
                    "type": "FORBIDDEN",
                    "path": ["repo1", "issues"],
                    "message": "Resource not accessible by integration",
                }
            ]
            return httpx.Response(
                200, json={"data": {"rateLimit": _rate_limit(), **data}, "errors": errors}
            )

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await fetch_repository_datasets(
                transport, org="acme", snapshot_dir=snapshot_dir, batch_width=2
            )
        issues_outcome = next(o for o in result.dataset_outcomes if o.name == "issues")
        assert issues_outcome.record_count == 1
        gap = issues_outcome.gaps[0]
        assert gap.resource_id == "R_2"
        assert gap.field_path == "issues"
        assert "not accessible" in gap.reason
        await transport.aclose()

    async def test_exhausted_retries_gaps_every_repo_in_the_invalidated_batch(self, tmp_path: Path):
        snapshot_dir = tmp_path / "snapshot"
        _write_repositories(snapshot_dir, [("R_1", "repo1"), ("R_2", "repo2")])

        async def no_sleep(seconds: float) -> None:
            return None

        def handler(request: httpx.Request) -> httpx.Response:
            query = json.loads(request.content)["query"]
            if "issues(" in query:
                return httpx.Response(503)
            return _happy_path_handler(request)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider, sleep=no_sleep, max_retries=1, backoff_base_seconds=0)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await fetch_repository_datasets(
                transport, org="acme", snapshot_dir=snapshot_dir, batch_width=2
            )
        issues_outcome = next(o for o in result.dataset_outcomes if o.name == "issues")
        assert issues_outcome.record_count == 0
        assert {g.resource_id for g in issues_outcome.gaps} == {"R_1", "R_2"}
        assert all(g.field_path is None for g in issues_outcome.gaps)
        await transport.aclose()


class TestNodeLimitRetry:
    async def test_node_limit_error_retries_with_a_smaller_page_size_ac_7_8(self, tmp_path: Path):
        snapshot_dir = tmp_path / "snapshot"
        page_sizes_seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            page_size = body["variables"]["pageSize"]
            page_sizes_seen.append(page_size)
            if page_size > 5:
                return httpx.Response(
                    200,
                    json={
                        "data": {"rateLimit": _rate_limit()},
                        "errors": [
                            {"type": "MAX_NODE_LIMIT_EXCEEDED", "message": "exceeds node limit"}
                        ],
                    },
                )
            return _happy_path_handler(request)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        checkpoint = CheckpointStore.create(
            snapshot_dir / "checkpoint.json", org="acme", dataset_selection=("issues",)
        )
        harvester = _RepoLevelHarvester(
            transport,
            org="acme",
            snapshot_dir=snapshot_dir,
            api_host=ApiHost(),
            checkpoint=checkpoint,
            page_size=50,
            batch_width=10,
            systemic_guard=SystemicFailureGuard(),
        )
        spec = next(s for s in _REPO_CONNECTIONS if s.dataset == "issues")
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await harvester._run_batch(spec, [_RepoState(id="R_1", name="repo1")], 50)
        harvester.close_writers()
        assert result.written == 1
        assert min(page_sizes_seen) <= 5
        assert page_sizes_seen[0] == 50
        await transport.aclose()

    async def test_node_limit_persisting_at_minimum_page_size_splits_the_batch(
        self, tmp_path: Path
    ):
        snapshot_dir = tmp_path / "snapshot"

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            variables = body["variables"]
            width = sum(1 for k in variables if k.endswith("_name"))
            if width > 1:
                return httpx.Response(
                    200,
                    json={
                        "data": {"rateLimit": _rate_limit()},
                        "errors": [
                            {"type": "MAX_NODE_LIMIT_EXCEEDED", "message": "exceeds node limit"}
                        ],
                    },
                )
            return _happy_path_handler(request)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        checkpoint = CheckpointStore.create(
            snapshot_dir / "checkpoint.json", org="acme", dataset_selection=("issues",)
        )
        harvester = _RepoLevelHarvester(
            transport,
            org="acme",
            snapshot_dir=snapshot_dir,
            api_host=ApiHost(),
            checkpoint=checkpoint,
            page_size=4,
            batch_width=2,
            systemic_guard=SystemicFailureGuard(),
        )
        spec = next(s for s in _REPO_CONNECTIONS if s.dataset == "issues")
        batch = [_RepoState(id="R_1", name="repo1"), _RepoState(id="R_2", name="repo2")]
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await harvester._run_batch(spec, batch, 4)
        harvester.close_writers()
        assert result.written == 2
        assert result.gaps == []
        await transport.aclose()

    async def test_unrecoverable_node_limit_at_width_one_becomes_a_gap(self, tmp_path: Path):
        snapshot_dir = tmp_path / "snapshot"

        def always_node_limit(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {"rateLimit": _rate_limit()},
                    "errors": [
                        {"type": "MAX_NODE_LIMIT_EXCEEDED", "message": "exceeds node limit"}
                    ],
                },
            )

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        checkpoint = CheckpointStore.create(
            snapshot_dir / "checkpoint.json", org="acme", dataset_selection=("issues",)
        )
        harvester = _RepoLevelHarvester(
            transport,
            org="acme",
            snapshot_dir=snapshot_dir,
            api_host=ApiHost(),
            checkpoint=checkpoint,
            page_size=1,
            batch_width=1,
            systemic_guard=SystemicFailureGuard(),
        )
        spec = next(s for s in _REPO_CONNECTIONS if s.dataset == "issues")
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=always_node_limit)
            result = await harvester._run_batch(spec, [_RepoState(id="R_1", name="repo1")], 1)
        harvester.close_writers()
        assert result.written == 0
        assert len(result.gaps) == 1
        assert "node limit" in result.gaps[0].reason
        await transport.aclose()


class TestSystemicFailure:
    async def test_a_run_of_no_response_failures_stops_the_run_ec_8(self, tmp_path: Path):
        snapshot_dir = tmp_path / "snapshot"
        _write_repositories(snapshot_dir, [(f"R_{i}", f"repo{i}") for i in range(20)])

        async def no_sleep(seconds: float) -> None:
            return None

        def always_503(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider, sleep=no_sleep, max_retries=0, backoff_base_seconds=0)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=always_503)
            with pytest.raises(OrgHarvestError) as exc_info:
                await fetch_repository_datasets(
                    transport,
                    org="acme",
                    snapshot_dir=snapshot_dir,
                    batch_width=1,
                    systemic_guard=SystemicFailureGuard(max_consecutive_failures=3),
                )
        assert exc_info.value.kind is ErrorKind.SYSTEMIC_FAILURE
        await transport.aclose()

    async def test_a_batch_transport_failure_counts_as_one_attempt_not_one_per_repo(
        self, tmp_path: Path
    ):
        """A single invalidated batch of many repositories is one failed
        request, not `batch_width` failed requests — the guard tracks
        requests, not the resources inside them."""
        snapshot_dir = tmp_path / "snapshot"
        _write_repositories(snapshot_dir, [(f"R_{i}", f"repo{i}") for i in range(5)])

        async def no_sleep(seconds: float) -> None:
            return None

        def always_503(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider, sleep=no_sleep, max_retries=0, backoff_base_seconds=0)
        # batch_width=5 means all 5 repos fail in ONE request per dataset —
        # if the guard counted per-repository, 5 datasets would already be
        # 25 failures and blow past this threshold several times over.
        guard = SystemicFailureGuard(max_consecutive_failures=5)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=always_503)
            with pytest.raises(OrgHarvestError) as exc_info:
                await fetch_repository_datasets(
                    transport,
                    org="acme",
                    snapshot_dir=snapshot_dir,
                    batch_width=5,
                    systemic_guard=guard,
                )
        assert exc_info.value.kind is ErrorKind.SYSTEMIC_FAILURE
        # Tripped after exactly 5 failed requests (one per dataset attempted
        # so far), not 25 — proving the guard counts requests, not repos.
        assert guard.total_attempts == 5
        await transport.aclose()
