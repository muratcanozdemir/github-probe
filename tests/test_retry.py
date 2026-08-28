from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
import respx

from org_harvest.checkpoint import CURSOR_DONE, CheckpointStore
from org_harvest.credentials import StaticTokenCredentialProvider
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.gaps import DatasetOutcome, Gap
from org_harvest.harvest.org_level import register_fetch_details as _register_org_fetch_details
from org_harvest.manifest import (
    ConsumptionStats,
    Manifest,
    build_manifest,
    read_manifest,
    write_manifest,
)
from org_harvest.retry import retry_gaps
from org_harvest.transport import Transport

# Repo-level datasets get their field lists registered as a side effect of
# fetch_repository_datasets() running during a retry; "repositories" is an
# org-level dataset these tests never fetch through fetch_organization_directory,
# so it needs this to have a field list finalize_snapshot() can convert against.
_register_org_fetch_details()

GITHUB = "https://api.github.com"


def _rate_limit() -> dict:
    reset_at = (datetime.now(UTC) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    return {"limit": 5000, "remaining": 4000, "resetAt": reset_at, "cost": 1, "nodeCount": 1}


def _write_repositories(snapshot_dir: Path, repos: list[tuple[str, str]]) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    with (snapshot_dir / "repositories.ndjson").open("w", encoding="utf-8") as f:
        for repo_id, name in repos:
            f.write(json.dumps({"id": repo_id, "name": name}) + "\n")


def _write_ndjson(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _seed_manifest(
    snapshot_dir: Path, *, dataset_counts: dict[str, int], gaps: tuple[Gap, ...]
) -> Manifest:
    outcomes = tuple(
        DatasetOutcome(name, count, tuple(g for g in gaps if g.dataset == name))
        for name, count in dataset_counts.items()
    )
    manifest = build_manifest(
        org="acme",
        api_host="github.com",
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:01:00+00:00",
        dataset_selection=tuple(dataset_counts),
        dataset_outcomes=outcomes,
        consumption=ConsumptionStats(),
    )
    write_manifest(snapshot_dir, manifest)
    return manifest


def _transport() -> Transport:
    return Transport(StaticTokenCredentialProvider("ghs_x"))


class TestNoManifest:
    async def test_a_snapshot_with_no_manifest_is_not_retryable(self, tmp_path: Path):
        transport = _transport()
        with pytest.raises(OrgHarvestError) as exc_info:
            await retry_gaps(transport, transport.credentials, org="acme", snapshot_dir=tmp_path)
        assert exc_info.value.kind is ErrorKind.INVALID_USAGE
        await transport.aclose()


class TestNoGaps:
    async def test_a_snapshot_with_no_gaps_is_a_reported_noop_ac_11_5(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        original = _seed_manifest(tmp_path, dataset_counts={"organization": 1}, gaps=())

        called = {"finalize": False}
        monkeypatch.setattr(
            "org_harvest.retry.finalize_snapshot",
            lambda *a, **k: called.__setitem__("finalize", True),
        )

        transport = _transport()
        result = await retry_gaps(
            transport, transport.credentials, org="acme", snapshot_dir=tmp_path
        )
        assert result.retried is False
        assert result.manifest == original
        assert result.datasets_retried == ()
        assert called["finalize"] is False
        assert not (tmp_path / "checkpoint.json").exists()  # never even opened
        await transport.aclose()


class TestRepoLevelRetry:
    async def test_a_successful_retry_clears_the_gap_and_updates_counts_ac_11_2_ac_11_3(
        self, tmp_path: Path
    ):
        _write_repositories(tmp_path, [("R_1", "repo1"), ("R_2", "repo2")])
        _write_ndjson(
            tmp_path / "issues.ndjson",
            [{"id": "I_2", "repository_id": "R_2", "number": 2, "title": "ok"}],
        )
        checkpoint = CheckpointStore.create(
            tmp_path / "checkpoint.json", org="acme", dataset_selection=("issues",)
        )
        checkpoint.set_dataset_status("issues", "complete")
        checkpoint.set_cursor("issues:R_1", CURSOR_DONE)
        gap = Gap.now("issues", resource_id="R_1", field_path=None, reason="boom")
        checkpoint.record_gap(gap)

        _seed_manifest(tmp_path, dataset_counts={"issues": 1}, gaps=(gap,))

        queried_names: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            i = 0
            data: dict[str, Any] = {}
            while f"repo{i}_name" in body["variables"]:
                queried_names.append(body["variables"][f"repo{i}_name"])
                data[f"repo{i}"] = {
                    "id": "R_1",
                    "issues": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "I_1",
                                "number": 1,
                                "title": "fixed",
                                "state": "OPEN",
                                "createdAt": "2020-01-01T00:00:00Z",
                                "updatedAt": "2020-01-01T00:00:00Z",
                                "closedAt": None,
                                "author": {"login": "alice"},
                                "milestone": None,
                                "labels": {"nodes": []},
                                "assignees": {"nodes": []},
                            }
                        ],
                    },
                }
                i += 1
            return httpx.Response(200, json={"data": {"rateLimit": _rate_limit(), **data}})

        transport = _transport()
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await retry_gaps(
                transport, transport.credentials, org="acme", snapshot_dir=tmp_path
            )

        assert queried_names == ["repo1"]  # only the gapped repo was ever queried
        assert result.retried is True
        assert result.datasets_retried == ("issues",)
        assert result.manifest.dataset_counts["issues"] == 2
        assert result.manifest.gaps == ()
        assert result.manifest.last_retried_at is not None

        # finalize (AC-11.4) converts and removes the NDJSON by default —
        # the Parquet file is the durable record of the merged result.
        table = pq.read_table(tmp_path / "issues.parquet")
        assert set(table.column("id").to_pylist()) == {"I_1", "I_2"}  # old + new, no duplication

        on_disk = read_manifest(tmp_path)
        assert on_disk == result.manifest
        await transport.aclose()

    async def test_a_retry_that_fails_again_keeps_an_updated_gap_ac_11_2(self, tmp_path: Path):
        _write_repositories(tmp_path, [("R_1", "repo1")])
        checkpoint = CheckpointStore.create(
            tmp_path / "checkpoint.json", org="acme", dataset_selection=("issues",)
        )
        checkpoint.set_dataset_status("issues", "complete")
        checkpoint.set_cursor("issues:R_1", CURSOR_DONE)
        old_gap = Gap.now("issues", resource_id="R_1", field_path=None, reason="first failure")
        checkpoint.record_gap(old_gap)
        _seed_manifest(tmp_path, dataset_counts={"issues": 0}, gaps=(old_gap,))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {"rateLimit": _rate_limit()},
                    "errors": [{"message": "still broken", "path": ["repo0", "issues"]}],
                },
            )

        transport = _transport()
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await retry_gaps(
                transport, transport.credentials, org="acme", snapshot_dir=tmp_path
            )

        assert len(result.manifest.gaps) == 1
        new_gap = result.manifest.gaps[0]
        assert new_gap.reason == "still broken"
        assert new_gap.occurred_at != old_gap.occurred_at or new_gap.reason != old_gap.reason
        await transport.aclose()

    async def test_untouched_datasets_are_preserved_in_the_new_manifest(self, tmp_path: Path):
        _write_repositories(tmp_path, [("R_1", "repo1")])
        _write_ndjson(tmp_path / "labels.ndjson", [{"id": "L_1", "repository_id": "R_1"}])
        checkpoint = CheckpointStore.create(
            tmp_path / "checkpoint.json", org="acme", dataset_selection=("issues", "labels")
        )
        checkpoint.set_dataset_status("issues", "complete")
        checkpoint.set_dataset_status("labels", "complete")
        checkpoint.set_cursor("issues:R_1", CURSOR_DONE)
        gap = Gap.now("issues", resource_id="R_1", field_path=None, reason="boom")
        checkpoint.record_gap(gap)
        _seed_manifest(tmp_path, dataset_counts={"issues": 0, "labels": 1}, gaps=(gap,))

        labels_queried = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if "id labels(" in body["query"]:
                labels_queried["n"] += 1
            return httpx.Response(
                200,
                json={
                    "data": {
                        "rateLimit": _rate_limit(),
                        "repo0": {
                            "id": "R_1",
                            "issues": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [],
                            },
                        },
                    }
                },
            )

        transport = _transport()
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await retry_gaps(
                transport, transport.credentials, org="acme", snapshot_dir=tmp_path
            )

        assert labels_queried["n"] == 0  # labels never had a gap, never retried
        assert result.manifest.dataset_counts["labels"] == 1
        assert result.datasets_retried == ("issues",)
        await transport.aclose()


class TestOrgLevelRetry:
    async def test_a_whole_dataset_gap_is_refetched_from_scratch(self, tmp_path: Path):
        checkpoint = CheckpointStore.create(
            tmp_path / "checkpoint.json", org="acme", dataset_selection=("members",)
        )
        checkpoint.set_dataset_status("members", "complete")
        gap = Gap.now("members", resource_id="acme", field_path=None, reason="boom")
        checkpoint.record_gap(gap)
        _seed_manifest(tmp_path, dataset_counts={"members": 0}, gaps=(gap,))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "rateLimit": _rate_limit(),
                        "organization": {
                            "membersWithRole": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
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
                        },
                    }
                },
            )

        transport = _transport()
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await retry_gaps(
                transport, transport.credentials, org="acme", snapshot_dir=tmp_path
            )

        assert result.manifest.dataset_counts["members"] == 1
        assert result.manifest.gaps == ()
        table = pq.read_table(tmp_path / "members.parquet")
        assert table.column("login").to_pylist() == ["alice"]
        await transport.aclose()

    async def test_a_team_scoped_gap_only_requeries_that_team(self, tmp_path: Path):
        _write_ndjson(
            tmp_path / "teams.ndjson",
            [{"id": "T_1", "slug": "core"}, {"id": "T_2", "slug": "infra"}],
        )
        checkpoint = CheckpointStore.create(
            tmp_path / "checkpoint.json", org="acme", dataset_selection=("team_members",)
        )
        checkpoint.set_dataset_status("team_members", "complete")
        checkpoint.set_cursor("team_members:T_2", CURSOR_DONE)
        gap = Gap.now("team_members", resource_id="T_1", field_path=None, reason="boom")
        checkpoint.record_gap(gap)
        _seed_manifest(tmp_path, dataset_counts={"team_members": 0}, gaps=(gap,))

        queried_slugs: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if "team(slug:" in body["query"]:
                queried_slugs.append(body["variables"]["slug"])
            return httpx.Response(
                200,
                json={
                    "data": {
                        "rateLimit": _rate_limit(),
                        "organization": {
                            "team": {
                                "members": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
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
                        },
                    }
                },
            )

        transport = _transport()
        with respx.mock(base_url=GITHUB) as router:
            router.post("/graphql").mock(side_effect=handler)
            result = await retry_gaps(
                transport, transport.credentials, org="acme", snapshot_dir=tmp_path
            )

        assert queried_slugs == ["core"]  # T_2 was already CURSOR_DONE, never re-queried
        assert result.manifest.dataset_counts["team_members"] == 1
        await transport.aclose()
