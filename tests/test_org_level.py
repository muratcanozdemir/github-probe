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
from org_harvest.harvest.org_level import (
    ORG_LEVEL_DATASET_NAMES,
    fetch_organization_directory,
)
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
