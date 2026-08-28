from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from org_harvest.checkpoint import CheckpointStore
from org_harvest.credentials import StaticTokenCredentialProvider
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.gaps import Gap
from org_harvest.harvest.org_level import (
    ORG_LEVEL_DATASET_NAMES,
    fetch_organization_directory,
)
from org_harvest.harvest.systemic import SystemicFailureGuard
from org_harvest.selection import RepositoryFilter
from org_harvest.transport import Transport

GITHUB = "https://api.github.com"
GRAPHQL = f"{GITHUB}/graphql"


def _rate_limit(remaining: int = 4000) -> dict:
    reset_at = (datetime.now(UTC) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    return {"limit": 5000, "remaining": remaining, "resetAt": reset_at, "cost": 1, "nodeCount": 1}


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _page_info(has_next: bool, cursor: str | None) -> dict:
    return {"hasNextPage": has_next, "endCursor": cursor}


def _happy_path_handler(request: httpx.Request) -> httpx.Response:
    """Every org-level connection returns exactly one page of exactly one
    record, and there is exactly one team (so team_members/team_repositories
    each make exactly one nested request). Shared by the full happy-path
    dispatcher and by single-dataset-override tests, which fall back to
    this for every query except the one they're overriding."""
    query = json.loads(request.content)["query"]
    data: dict
    if "team(slug:" in query and "members(" in query:
        data = {
            "organization": {
                "team": {
                    "members": {
                        "pageInfo": _page_info(False, None),
                        "edges": [
                            {
                                "role": "member",
                                "node": {
                                    "id": "U_1",
                                    "login": "alice",
                                    "name": "Alice",
                                    "email": "",
                                },
                            }
                        ],
                    }
                }
            }
        }
    elif "team(slug:" in query and "repositories(" in query:
        data = {
            "organization": {
                "team": {
                    "repositories": {
                        "pageInfo": _page_info(False, None),
                        "edges": [
                            {
                                "permission": "WRITE",
                                "node": {
                                    "id": "R_1",
                                    "name": "repo1",
                                    "nameWithOwner": "acme/repo1",
                                },
                            }
                        ],
                    }
                }
            }
        }
    elif "membersWithRole(" in query:
        data = {
            "organization": {
                "membersWithRole": {
                    "pageInfo": _page_info(False, None),
                    "edges": [
                        {
                            "role": "ADMIN",
                            "node": {
                                "id": "U_1",
                                "databaseId": 1,
                                "login": "alice",
                                "name": "Alice",
                                "email": "a@x.com",
                                "createdAt": "2020-01-01T00:00:00Z",
                            },
                        }
                    ],
                }
            }
        }
    elif "pendingMembers(" in query:
        data = {
            "organization": {
                "pendingMembers": {
                    "pageInfo": _page_info(False, None),
                    "nodes": [
                        {
                            "id": "U_2",
                            "databaseId": 2,
                            "login": "bob",
                            "name": "Bob",
                            "email": None,
                            "createdAt": "2020-01-02T00:00:00Z",
                        }
                    ],
                }
            }
        }
    elif "teams(" in query:
        data = {
            "organization": {
                "teams": {
                    "pageInfo": _page_info(False, None),
                    "nodes": [
                        {
                            "id": "T_1",
                            "databaseId": 10,
                            "name": "Core",
                            "slug": "core",
                            "description": None,
                            "privacy": "VISIBLE",
                            "notificationSetting": "NOTIFICATIONS_ENABLED",
                            "createdAt": "2019-01-01T00:00:00Z",
                            "updatedAt": "2019-01-01T00:00:00Z",
                        }
                    ],
                }
            }
        }
    elif "rulesets(" in query:
        data = {
            "organization": {
                "rulesets": {
                    "pageInfo": _page_info(False, None),
                    "nodes": [
                        {
                            "id": "RS_1",
                            "databaseId": 100,
                            "name": "protect-main",
                            "target": "BRANCH",
                            "enforcement": "ACTIVE",
                        }
                    ],
                }
            }
        }
    elif "repositoryCustomProperties(" in query:
        data = {
            "organization": {
                "repositoryCustomProperties": {
                    "pageInfo": _page_info(False, None),
                    "nodes": [
                        {
                            "propertyName": "team",
                            "valueType": "STRING",
                            "required": False,
                            "defaultValue": None,
                            "allowedValues": None,
                        }
                    ],
                }
            }
        }
    elif "domains(" in query:
        data = {
            "organization": {
                "domains": {
                    "pageInfo": _page_info(False, None),
                    "nodes": [
                        {
                            "id": "D_1",
                            "domain": "acme.com",
                            "isVerified": True,
                            "isApproved": True,
                            "createdAt": "2018-01-01T00:00:00Z",
                        }
                    ],
                }
            }
        }
    elif "ipAllowListEntries(" in query:
        data = {
            "organization": {
                "ipAllowListEntries": {
                    "pageInfo": _page_info(False, None),
                    "nodes": [
                        {
                            "id": "IP_1",
                            "allowListValue": "10.0.0.0/8",
                            "isActive": True,
                            "name": "office",
                            "createdAt": "2018-01-01T00:00:00Z",
                        }
                    ],
                }
            }
        }
    elif "repositories(" in query:
        data = {
            "organization": {
                "repositories": {
                    "pageInfo": _page_info(False, None),
                    "nodes": [
                        {
                            "id": "R_1",
                            "databaseId": 200,
                            "name": "repo1",
                            "nameWithOwner": "acme/repo1",
                            "isPrivate": False,
                            "isArchived": False,
                            "isFork": False,
                            "isDisabled": False,
                            "isEmpty": False,
                            "visibility": "PUBLIC",
                            "createdAt": "2017-01-01T00:00:00Z",
                            "updatedAt": "2017-01-01T00:00:00Z",
                            "pushedAt": "2017-01-01T00:00:00Z",
                        }
                    ],
                }
            }
        }
    else:
        # The organization scalar query (no connections, no pageInfo).
        data = {
            "organization": {
                "id": "O_1",
                "databaseId": 1000,
                "login": "acme",
                "name": "Acme Corp",
                "description": None,
                "email": None,
                "location": None,
                "websiteUrl": None,
                "isVerified": True,
                "createdAt": "2015-01-01T00:00:00Z",
                "updatedAt": "2015-01-01T00:00:00Z",
                "requiresTwoFactorAuthentication": True,
                "membersCanForkPrivateRepositories": False,
                "webCommitSignoffRequired": False,
                "ipAllowListEnabledSetting": "ENABLED",
            }
        }
    return httpx.Response(200, json={"data": {"rateLimit": _rate_limit(), **data}})


def _happy_path_dispatcher() -> respx.MockRouter:
    router = respx.mock(base_url=GITHUB)
    router.post("/graphql").mock(side_effect=_happy_path_handler)
    return router


class TestHappyPath:
    async def test_fetches_all_eleven_org_level_datasets(self, tmp_path: Path):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with _happy_path_dispatcher():
            result = await fetch_organization_directory(
                transport, provider, org="acme", snapshot_dir=snapshot_dir
            )
        assert {o.name for o in result.dataset_outcomes} == set(ORG_LEVEL_DATASET_NAMES)
        assert not result.has_gaps
        assert result.gaps == ()
        await transport.aclose()

    async def test_writes_one_ndjson_file_per_dataset(self, tmp_path: Path):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with _happy_path_dispatcher():
            await fetch_organization_directory(
                transport, provider, org="acme", snapshot_dir=snapshot_dir
            )
        for name in ORG_LEVEL_DATASET_NAMES:
            assert (snapshot_dir / f"{name}.ndjson").exists()
        await transport.aclose()

    async def test_records_carry_stable_identifiers_ac_8_6(self, tmp_path: Path):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with _happy_path_dispatcher():
            await fetch_organization_directory(
                transport, provider, org="acme", snapshot_dir=snapshot_dir
            )
        members = _read_lines(snapshot_dir / "members.ndjson")
        assert members[0]["id"] == "U_1"
        assert members[0]["role"] == "ADMIN"
        # org_custom_properties' GraphQL type has no node id — a stable id
        # is synthesized from its natural key instead.
        props = _read_lines(snapshot_dir / "org_custom_properties.ndjson")
        assert props[0]["id"] == "team"
        assert props[0]["property_name"] == "team"
        await transport.aclose()

    async def test_team_members_and_repositories_carry_team_id_parent_key(self, tmp_path: Path):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with _happy_path_dispatcher():
            await fetch_organization_directory(
                transport, provider, org="acme", snapshot_dir=snapshot_dir
            )
        team_members = _read_lines(snapshot_dir / "team_members.ndjson")
        assert team_members[0]["team_id"] == "T_1"
        assert team_members[0]["role"] == "member"
        team_repos = _read_lines(snapshot_dir / "team_repositories.ndjson")
        assert team_repos[0]["team_id"] == "T_1"
        assert team_repos[0]["permission"] == "WRITE"
        await transport.aclose()

    async def test_checkpoint_marks_every_dataset_complete(self, tmp_path: Path):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with _happy_path_dispatcher():
            await fetch_organization_directory(
                transport, provider, org="acme", snapshot_dir=snapshot_dir
            )
        checkpoint = CheckpointStore.load(snapshot_dir / "checkpoint.json")
        for name in ORG_LEVEL_DATASET_NAMES:
            assert checkpoint.dataset_status[name] == "complete"
        await transport.aclose()

    async def test_reachable_repository_count_reflects_written_records(self, tmp_path: Path):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with _happy_path_dispatcher():
            result = await fetch_organization_directory(
                transport, provider, org="acme", snapshot_dir=snapshot_dir
            )
        assert result.reachable_repository_count == 1
        await transport.aclose()

    async def test_not_scope_restricted_for_all_repository_selection(self, tmp_path: Path):
        provider = StaticTokenCredentialProvider("ghs_x")
        provider.repository_selection = "all"
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with _happy_path_dispatcher():
            result = await fetch_organization_directory(
                transport, provider, org="acme", snapshot_dir=snapshot_dir
            )
        assert result.scope_restricted is False
        await transport.aclose()

    async def test_scope_restricted_when_installation_is_repo_scoped_ec_3(self, tmp_path: Path):
        provider = StaticTokenCredentialProvider("ghs_x")
        provider.repository_selection = "selected"
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with _happy_path_dispatcher():
            result = await fetch_organization_directory(
                transport, provider, org="acme", snapshot_dir=snapshot_dir
            )
        assert result.scope_restricted is True
        await transport.aclose()


class TestPagination:
    async def test_paginates_a_connection_across_multiple_pages_and_checkpoints_cursor(
        self, tmp_path: Path
    ):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            query = body["query"]
            if "membersWithRole(" not in query:
                return httpx.Response(200, json={"data": {"rateLimit": _rate_limit()}})
            calls["n"] += 1
            cursor = body["variables"]["cursor"]
            if cursor is None:
                page = {
                    "pageInfo": _page_info(True, "CURSOR_1"),
                    "edges": [
                        {
                            "role": "MEMBER",
                            "node": {
                                "id": "U_1",
                                "databaseId": 1,
                                "login": "alice",
                                "name": "Alice",
                                "email": "a@x.com",
                                "createdAt": "2020-01-01T00:00:00Z",
                            },
                        }
                    ],
                }
            else:
                assert cursor == "CURSOR_1"
                page = {
                    "pageInfo": _page_info(False, None),
                    "edges": [
                        {
                            "role": "MEMBER",
                            "node": {
                                "id": "U_2",
                                "databaseId": 2,
                                "login": "bob",
                                "name": "Bob",
                                "email": "b@x.com",
                                "createdAt": "2020-01-02T00:00:00Z",
                            },
                        }
                    ],
                }
            return httpx.Response(
                200,
                json={
                    "data": {"rateLimit": _rate_limit(), "organization": {"membersWithRole": page}}
                },
            )

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            # Only exercise the `members` dataset directly, bypassing the
            # rest of the full-directory fetch for this pagination-focused test.
            from org_harvest.checkpoint import CheckpointStore
            from org_harvest.harvest.org_level import _ORG_CONNECTIONS, _OrgLevelHarvester
            from org_harvest.interrupt import InterruptGuard
            from org_harvest.selection import RepositoryFilter

            checkpoint = CheckpointStore.create(
                snapshot_dir / "checkpoint.json", org="acme", dataset_selection=("members",)
            )
            harvester = _OrgLevelHarvester(
                transport,
                org="acme",
                snapshot_dir=snapshot_dir,
                api_host=__import__("org_harvest").ApiHost(),
                checkpoint=checkpoint,
                page_size=1,
                systemic_guard=SystemicFailureGuard(),
                repository_filter=RepositoryFilter(),
                interrupt=InterruptGuard(),
            )
            spec = next(s for s in _ORG_CONNECTIONS if s.dataset == "members")
            outcome = await harvester.fetch_org_connection(spec)
        assert calls["n"] == 2
        assert outcome.record_count == 2
        records = _read_lines(snapshot_dir / "members.ndjson")
        assert [r["login"] for r in records] == ["alice", "bob"]
        checkpoint_state = CheckpointStore.load(snapshot_dir / "checkpoint.json")
        assert checkpoint_state.cursors["members"] is None  # last page had endCursor=None
        assert checkpoint_state.dataset_status["members"] == "complete"
        await transport.aclose()


class TestPartialFailure:
    async def test_graphql_partial_error_becomes_a_gap_without_aborting_ac_5_1(
        self, tmp_path: Path
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            query = json.loads(request.content)["query"]
            if "rulesets(" in query:
                return httpx.Response(
                    200,
                    json={
                        "data": {"rateLimit": _rate_limit(), "organization": None},
                        "errors": [
                            {
                                "type": "FORBIDDEN",
                                "path": ["organization", "rulesets"],
                                "message": "Resource not accessible",
                            }
                        ],
                    },
                )
            return _happy_path_handler(request)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await fetch_organization_directory(
                transport, provider, org="acme", snapshot_dir=snapshot_dir
            )
        assert result.has_gaps
        gap = next(g for g in result.gaps if g.dataset == "org_rulesets")
        assert gap.field_path == "organization.rulesets"
        assert "Resource not accessible" in gap.reason
        # Every other dataset still completed (AC-5.5).
        other_outcomes = [o for o in result.dataset_outcomes if o.name != "org_rulesets"]
        assert all(not o.gaps for o in other_outcomes)
        checkpoint_state = CheckpointStore.load(snapshot_dir / "checkpoint.json")
        assert len(checkpoint_state.gaps) == 1
        await transport.aclose()

    async def test_exhausted_retries_becomes_a_gap_ac_5_3(self, tmp_path: Path, monkeypatch):
        async def always_fail_sleep(seconds: float) -> None:
            return None

        def handler(request: httpx.Request) -> httpx.Response:
            query = json.loads(request.content)["query"]
            if "domains(" in query:
                return httpx.Response(503)
            return _happy_path_handler(request)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(
            provider, sleep=always_fail_sleep, max_retries=1, backoff_base_seconds=0
        )
        snapshot_dir = tmp_path / "snapshot"
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await fetch_organization_directory(
                transport, provider, org="acme", snapshot_dir=snapshot_dir
            )
        gap = next(g for g in result.gaps if g.dataset == "org_domains")
        assert gap.field_path is None
        # The run continued past the failed dataset (AC-5.5).
        assert {o.name for o in result.dataset_outcomes} == set(ORG_LEVEL_DATASET_NAMES)
        await transport.aclose()

    async def test_unauthorized_response_propagates_instead_of_becoming_a_gap(self, tmp_path: Path):
        """A 401 is an authentication failure (US-3), not a per-resource
        condition — it must stop the run, not be swallowed as a gap."""

        def handler(request: httpx.Request) -> httpx.Response:
            query = json.loads(request.content)["query"]
            if "teams(" in query:
                return httpx.Response(401)
            return _happy_path_handler(request)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            with pytest.raises(OrgHarvestError) as exc_info:
                await fetch_organization_directory(
                    transport, provider, org="acme", snapshot_dir=snapshot_dir
                )
        assert exc_info.value.kind is ErrorKind.AUTH_EXPIRED
        await transport.aclose()


class TestSystemicFailure:
    async def test_a_run_of_no_response_failures_stops_the_run_ec_8(self, tmp_path: Path):
        """Every dataset request fails with no usable response — a
        simulated outage. The default guard should trip well before all
        eleven org-level datasets are attempted, and the failure must be a
        SYSTEMIC_FAILURE, not accumulated gaps for every dataset."""

        async def no_sleep(seconds: float) -> None:
            return None

        def always_503(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider, sleep=no_sleep, max_retries=0, backoff_base_seconds=0)
        snapshot_dir = tmp_path / "snapshot"
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=always_503)
            with pytest.raises(OrgHarvestError) as exc_info:
                await fetch_organization_directory(
                    transport,
                    provider,
                    org="acme",
                    snapshot_dir=snapshot_dir,
                    systemic_guard=SystemicFailureGuard(max_consecutive_failures=3),
                )
        assert exc_info.value.kind is ErrorKind.SYSTEMIC_FAILURE
        await transport.aclose()

    async def test_a_shared_guard_can_span_multiple_calls(self, tmp_path: Path):
        """Demonstrates the seam Story 10 will use: one guard instance
        passed to both phases accumulates failures across both."""

        def always_503(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        async def no_sleep(seconds: float) -> None:
            return None

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider, sleep=no_sleep, max_retries=0, backoff_base_seconds=0)
        snapshot_dir = tmp_path / "snapshot"
        guard = SystemicFailureGuard(max_consecutive_failures=2)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=always_503)
            with pytest.raises(OrgHarvestError) as exc_info:
                await fetch_organization_directory(
                    transport,
                    provider,
                    org="acme",
                    snapshot_dir=snapshot_dir,
                    systemic_guard=guard,
                )
        assert exc_info.value.kind is ErrorKind.SYSTEMIC_FAILURE
        assert guard.consecutive_failures >= 2
        await transport.aclose()

    async def test_graphql_partial_errors_do_not_count_toward_the_systemic_guard(
        self, tmp_path: Path
    ):
        """A response that arrives with GraphQL-level errors (data present
        or null, errors populated) is the Story 5/6 gap case, not the
        no-response case this guard tracks — it must not push the
        consecutive-failure counter."""

        def handler(request: httpx.Request) -> httpx.Response:
            query = json.loads(request.content)["query"]
            if "rulesets(" in query:
                return httpx.Response(
                    200,
                    json={
                        "data": {"rateLimit": _rate_limit(), "organization": None},
                        "errors": [{"path": ["organization", "rulesets"], "message": "nope"}],
                    },
                )
            return _happy_path_handler(request)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        guard = SystemicFailureGuard(max_consecutive_failures=1)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await fetch_organization_directory(
                transport, provider, org="acme", snapshot_dir=snapshot_dir, systemic_guard=guard
            )
        # Did not raise, even though max_consecutive_failures=1 — a GraphQL
        # partial error was never reported as a "no response" attempt.
        assert result.has_gaps
        assert guard.consecutive_failures == 0
        await transport.aclose()


class TestDatasetNarrowing:
    async def test_only_selected_datasets_are_fetched(self, tmp_path: Path):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with _happy_path_dispatcher():
            result = await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("organization", "members"),
            )
        assert {o.name for o in result.dataset_outcomes} == {"organization", "members"}
        assert not (snapshot_dir / "teams.ndjson").exists()
        await transport.aclose()

    async def test_none_means_the_full_default_tier_unchanged(self, tmp_path: Path):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with _happy_path_dispatcher():
            result = await fetch_organization_directory(
                transport, provider, org="acme", snapshot_dir=snapshot_dir, dataset_names=None
            )
        assert {o.name for o in result.dataset_outcomes} == set(ORG_LEVEL_DATASET_NAMES)
        await transport.aclose()

    async def test_team_members_alone_still_reads_teams_written_by_this_same_selection(
        self, tmp_path: Path
    ):
        """Mirrors what `resolve_dataset_selection()` would hand this
        function after auto-including `teams` (AC-2.6) — exercised here
        directly against `org_level.py`'s own narrowing."""
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with _happy_path_dispatcher():
            result = await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("teams", "team_members"),
            )
        assert {o.name for o in result.dataset_outcomes} == {"teams", "team_members"}
        team_members = _read_lines(snapshot_dir / "team_members.ndjson")
        assert team_members[0]["team_id"] == "T_1"
        await transport.aclose()

    async def test_selecting_an_unimplemented_optional_dataset_becomes_a_gap(self, tmp_path: Path):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with _happy_path_dispatcher():
            result = await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("organization", "audit_log"),
            )
        by_name = {o.name: o for o in result.dataset_outcomes}
        assert by_name["audit_log"].record_count == 0
        assert len(by_name["audit_log"].gaps) == 1
        assert "not yet implemented" in by_name["audit_log"].gaps[0].reason
        await transport.aclose()


def _repo_node(n: int, *, is_archived: bool = False, is_fork: bool = False) -> dict:
    return {
        "id": f"R_{n}",
        "databaseId": n,
        "name": f"repo{n}",
        "nameWithOwner": f"acme/repo{n}",
        "isPrivate": False,
        "isArchived": is_archived,
        "isFork": is_fork,
        "isDisabled": False,
        "isEmpty": False,
        "visibility": "PUBLIC",
        "createdAt": "2017-01-01T00:00:00Z",
        "updatedAt": "2017-01-01T00:00:00Z",
        "pushedAt": "2017-01-01T00:00:00Z",
    }


def _repositories_handler(nodes: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if "repositories(" not in query:
            return _happy_path_handler(request)
        data = {
            "organization": {"repositories": {"pageInfo": _page_info(False, None), "nodes": nodes}}
        }
        return httpx.Response(200, json={"data": {"rateLimit": _rate_limit(), **data}})

    return handler


class TestRepositoryFilter:
    async def test_excludes_archived_repositories_ac_2_8(self, tmp_path: Path):
        nodes = [_repo_node(1), _repo_node(2, is_archived=True)]
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=_repositories_handler(nodes))
            result = await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("repositories",),
                repository_filter=RepositoryFilter(exclude_archived=True),
            )
        repos = _read_lines(snapshot_dir / "repositories.ndjson")
        assert [r["name"] for r in repos] == ["repo1"]
        assert result.reachable_repository_count == 1
        await transport.aclose()

    async def test_excludes_forks_ac_2_8(self, tmp_path: Path):
        nodes = [_repo_node(1), _repo_node(2, is_fork=True)]
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=_repositories_handler(nodes))
            await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("repositories",),
                repository_filter=RepositoryFilter(exclude_forks=True),
            )
        repos = _read_lines(snapshot_dir / "repositories.ndjson")
        assert [r["name"] for r in repos] == ["repo1"]
        await transport.aclose()

    async def test_name_allowlist_restricts_to_named_repositories_ac_2_8(self, tmp_path: Path):
        nodes = [_repo_node(1), _repo_node(2)]
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=_repositories_handler(nodes))
            await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("repositories",),
                repository_filter=RepositoryFilter(names=frozenset({"repo2"})),
            )
        repos = _read_lines(snapshot_dir / "repositories.ndjson")
        assert [r["name"] for r in repos] == ["repo2"]
        await transport.aclose()

    async def test_no_filter_keeps_every_repository(self, tmp_path: Path):
        nodes = [_repo_node(1), _repo_node(2, is_archived=True), _repo_node(3, is_fork=True)]
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=_repositories_handler(nodes))
            await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("repositories",),
            )
        repos = _read_lines(snapshot_dir / "repositories.ndjson")
        assert len(repos) == 3
        await transport.aclose()


class TestResume:
    async def test_a_complete_dataset_is_not_refetched_ac_4_2_ac_4_5(self, tmp_path: Path):
        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir(parents=True)
        (snapshot_dir / "organization.ndjson").write_text(
            json.dumps({"id": "O_1", "login": "acme"}) + "\n", encoding="utf-8"
        )
        checkpoint = CheckpointStore.create(
            snapshot_dir / "checkpoint.json", org="acme", dataset_selection=("organization",)
        )
        checkpoint.set_dataset_status("organization", "complete")

        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return _happy_path_handler(request)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url=GITHUB, assert_all_called=False) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("organization",),
                checkpoint=checkpoint,
            )
        assert calls["n"] == 0  # no network call at all for the already-complete dataset
        outcome = result.dataset_outcomes[0]
        assert outcome.name == "organization"
        assert outcome.record_count == 1
        await transport.aclose()

    async def test_a_completed_datasets_prior_gaps_still_surface_on_resume(self, tmp_path: Path):
        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir(parents=True)
        checkpoint = CheckpointStore.create(
            snapshot_dir / "checkpoint.json", org="acme", dataset_selection=("organization",)
        )
        checkpoint.record_gap(
            Gap.now("organization", resource_id="acme", field_path=None, reason="boom")
        )
        checkpoint.set_dataset_status("organization", "complete")

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url=GITHUB):
            result = await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("organization",),
                checkpoint=checkpoint,
            )
        outcome = result.dataset_outcomes[0]
        assert len(outcome.gaps) == 1
        assert outcome.gaps[0].reason == "boom"
        await transport.aclose()

    async def test_resumes_a_connection_from_its_stored_cursor_without_duplicating_ac_4_5(
        self, tmp_path: Path
    ):
        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir(parents=True)
        # Page 1 was already written and checkpointed by a prior attempt.
        (snapshot_dir / "members.ndjson").write_text(
            json.dumps(
                {
                    "id": "U_1",
                    "database_id": 1,
                    "login": "alice",
                    "name": "Alice",
                    "email": "a@x.com",
                    "created_at": "2020-01-01T00:00:00Z",
                    "role": "ADMIN",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        checkpoint = CheckpointStore.create(
            snapshot_dir / "checkpoint.json", org="acme", dataset_selection=("members",)
        )
        checkpoint.set_cursor("members", "CURSOR_1")

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if "membersWithRole(" in body["query"]:
                assert body["variables"]["cursor"] == "CURSOR_1"
                page = {
                    "pageInfo": _page_info(False, None),
                    "edges": [
                        {
                            "role": "MEMBER",
                            "node": {
                                "id": "U_2",
                                "databaseId": 2,
                                "login": "bob",
                                "name": "Bob",
                                "email": "b@x.com",
                                "createdAt": "2020-01-02T00:00:00Z",
                            },
                        }
                    ],
                }
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "rateLimit": _rate_limit(),
                            "organization": {"membersWithRole": page},
                        }
                    },
                )
            return _happy_path_handler(request)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("members",),
                checkpoint=checkpoint,
            )
        members = _read_lines(snapshot_dir / "members.ndjson")
        assert [m["id"] for m in members] == ["U_1", "U_2"]
        await transport.aclose()

    async def test_a_done_team_is_skipped_while_another_team_resumes_ac_4_5(self, tmp_path: Path):
        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir(parents=True)
        snapshot_dir_teams = snapshot_dir / "teams.ndjson"
        snapshot_dir_teams.write_text(
            json.dumps({"id": "T_1", "slug": "core"})
            + "\n"
            + json.dumps({"id": "T_2", "slug": "infra"})
            + "\n",
            encoding="utf-8",
        )
        checkpoint = CheckpointStore.create(
            snapshot_dir / "checkpoint.json", org="acme", dataset_selection=("team_members",)
        )
        checkpoint.set_dataset_status("teams", "complete")
        checkpoint.set_cursor("team_members:T_1", "__done__")

        queried_slugs: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if "team(slug:" in body["query"] and "members(" in body["query"]:
                queried_slugs.append(body["variables"]["slug"])
            return _happy_path_handler(request)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("team_members",),
                checkpoint=checkpoint,
            )
        assert queried_slugs == ["infra"]  # T_1 (core) skipped as already done
        await transport.aclose()


class TestInterrupt:
    async def test_a_requested_interrupt_stops_pagination_after_the_in_flight_page_ac_4_11(
        self, tmp_path: Path
    ):
        from org_harvest.harvest.org_level import _ORG_CONNECTIONS, _OrgLevelHarvester
        from org_harvest.hosts import ApiHost
        from org_harvest.interrupt import InterruptGuard

        interrupt = InterruptGuard()
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            cursor = body["variables"]["cursor"]
            calls["n"] += 1
            if cursor is None:
                page = {
                    "pageInfo": _page_info(True, "CURSOR_1"),
                    "edges": [{"role": "MEMBER", "node": {"id": "U_1", "login": "alice"}}],
                }
                interrupt.requested = True  # simulate Ctrl-C arriving mid-page
            else:
                page = {
                    "pageInfo": _page_info(False, None),
                    "edges": [{"role": "MEMBER", "node": {"id": "U_2", "login": "bob"}}],
                }
            return httpx.Response(
                200,
                json={
                    "data": {"rateLimit": _rate_limit(), "organization": {"membersWithRole": page}}
                },
            )

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        checkpoint = CheckpointStore.create(
            snapshot_dir / "checkpoint.json", org="acme", dataset_selection=("members",)
        )
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            harvester = _OrgLevelHarvester(
                transport,
                org="acme",
                snapshot_dir=snapshot_dir,
                api_host=ApiHost(),
                checkpoint=checkpoint,
                page_size=1,
                systemic_guard=SystemicFailureGuard(),
                repository_filter=RepositoryFilter(),
                interrupt=interrupt,
            )
            spec = next(s for s in _ORG_CONNECTIONS if s.dataset == "members")
            outcome = await harvester.fetch_org_connection(spec)
        assert calls["n"] == 1  # the second page was never requested
        assert outcome.record_count == 1
        state = CheckpointStore.load(snapshot_dir / "checkpoint.json")
        assert state.cursors["members"] == "CURSOR_1"  # real cursor, resumable
        assert state.dataset_status.get("members") != "complete"
        await transport.aclose()

    async def test_an_interrupt_stops_the_outer_dataset_loop_too(self, tmp_path: Path):
        from org_harvest.interrupt import InterruptGuard

        interrupt = InterruptGuard()

        def handler(request: httpx.Request) -> httpx.Response:
            query = json.loads(request.content)["query"]
            if "membersWithRole(" in query:
                interrupt.requested = True
            return _happy_path_handler(request)

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("organization", "members", "pending_members"),
                interrupt=interrupt,
            )
        names = {o.name for o in result.dataset_outcomes}
        assert "organization" in names  # ran before the interrupt was set
        assert "members" in names  # the in-flight dataset still finishes its call
        assert "pending_members" not in names  # never started once interrupted
        await transport.aclose()


class TestProgress:
    """Story 15, AC-9.4 — `on_progress` is called once per dataset, right
    after that dataset's outcome is known."""

    async def test_on_progress_emits_one_dataset_complete_event_per_dataset(self, tmp_path: Path):
        from org_harvest.progress import ProgressEvent, ProgressEventKind

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        events: list[ProgressEvent] = []
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=_happy_path_handler)
            await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("organization", "members"),
                on_progress=events.append,
            )
        assert all(e.kind is ProgressEventKind.DATASET_COMPLETE for e in events)
        assert {e.dataset for e in events} == {"organization", "members"}
        by_name = {e.dataset: e for e in events}
        assert by_name["members"].record_count == 1
        assert by_name["members"].gap_count == 0
        assert "members" in by_name["members"].message
        await transport.aclose()

    async def test_no_on_progress_means_no_events_and_no_error(self, tmp_path: Path):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=_happy_path_handler)
            result = await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("organization", "members"),
            )
        assert {o.name for o in result.dataset_outcomes} == {"organization", "members"}
        await transport.aclose()

    async def test_a_not_yet_implemented_dataset_gap_also_emits_a_progress_event(
        self, tmp_path: Path
    ):
        from org_harvest.progress import ProgressEvent, ProgressEventKind

        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        snapshot_dir = tmp_path / "snapshot"
        events: list[ProgressEvent] = []
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=_happy_path_handler)
            await fetch_organization_directory(
                transport,
                provider,
                org="acme",
                snapshot_dir=snapshot_dir,
                dataset_names=("organization", "audit_log"),
                on_progress=events.append,
            )
        gap_events = [e for e in events if e.dataset == "audit_log"]
        assert len(gap_events) == 1
        assert gap_events[0].kind is ProgressEventKind.DATASET_COMPLETE
        assert gap_events[0].gap_count == 1
        await transport.aclose()
