from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from org_harvest.checkpoint import CheckpointStore
from org_harvest.credentials import StaticTokenCredentialProvider
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.finalize import FinalizeResult
from org_harvest.gaps import DatasetOutcome, Gap
from org_harvest.harvest.org_level import OrgLevelResult
from org_harvest.harvest.repo_level import RepoLevelResult
from org_harvest.manifest import is_snapshot_complete
from org_harvest.preflight import DatasetVerdict, PreflightReport, Verdict
from org_harvest.ratelimit import RateLimitSnapshot
from org_harvest.run import ExitStatus, exit_status_for_error, run_snapshot
from org_harvest.transport import Transport

TIMESTAMP_RE = re.compile(r"^\d{8}T\d{6}Z$")


def _recent_compact(minutes_ago: int) -> str:
    """A `utc_now_compact()`-shaped timestamp a few minutes in the past —
    distinct and orderable for resume-discovery tests, but well inside
    the default staleness window."""
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime("%Y%m%dT%H%M%SZ")


def _report(blocked: tuple[str, ...] = ()) -> PreflightReport:
    verdicts = tuple(
        DatasetVerdict(name, Verdict.BLOCKED if name in blocked else Verdict.READY)
        for name in ("organization", "members", "issues")
    )
    return PreflightReport(
        org="acme",
        repository_count=1,
        scope_restricted=False,
        dataset_verdicts=verdicts,
        estimated_points=3,
        estimated_duration_seconds=0.0,
    )


def _org_result(*, gaps: tuple[Gap, ...] = (), scope_restricted: bool = False) -> OrgLevelResult:
    return OrgLevelResult(
        dataset_outcomes=(DatasetOutcome("organization", 1, gaps),),
        scope_restricted=scope_restricted,
        reachable_repository_count=1,
    )


def _repo_result(*, gaps: tuple[Gap, ...] = ()) -> RepoLevelResult:
    return RepoLevelResult(dataset_outcomes=(DatasetOutcome("issues", 5, gaps),))


def _patch_success(
    monkeypatch: pytest.MonkeyPatch,
    *,
    blocked: tuple[str, ...] = (),
    org_gaps: tuple[Gap, ...] = (),
    repo_gaps: tuple[Gap, ...] = (),
    scope_restricted: bool = False,
    calls: dict[str, int] | None = None,
) -> None:
    async def fake_preflight(*args, **kwargs):
        if calls is not None:
            calls["preflight"] = calls.get("preflight", 0) + 1
        return _report(blocked)

    async def fake_fetch_org(*args, **kwargs):
        if calls is not None:
            calls["fetch_org"] = calls.get("fetch_org", 0) + 1
        return _org_result(gaps=org_gaps, scope_restricted=scope_restricted)

    async def fake_fetch_repo(*args, **kwargs):
        if calls is not None:
            calls["fetch_repo"] = calls.get("fetch_repo", 0) + 1
        return _repo_result(gaps=repo_gaps)

    def fake_finalize(*args, **kwargs):
        if calls is not None:
            calls["finalize"] = calls.get("finalize", 0) + 1
        return FinalizeResult(())

    monkeypatch.setattr("org_harvest.run.run_preflight", fake_preflight)
    monkeypatch.setattr("org_harvest.run.fetch_organization_directory", fake_fetch_org)
    monkeypatch.setattr("org_harvest.run.fetch_repository_datasets", fake_fetch_repo)
    monkeypatch.setattr("org_harvest.run.finalize_snapshot", fake_finalize)


def _transport() -> Transport:
    return Transport(StaticTokenCredentialProvider("ghs_x"))


class TestExitStatusMapping:
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            (ErrorKind.CREDENTIAL_CONFLICT, ExitStatus.INVALID_USAGE),
            (ErrorKind.CREDENTIAL_INVALID, ExitStatus.INVALID_USAGE),
            (ErrorKind.INVALID_USAGE, ExitStatus.INVALID_USAGE),
            (ErrorKind.AUTH_EXPIRED, ExitStatus.AUTH_FAILURE),
            (ErrorKind.AUTH_FAILED, ExitStatus.AUTH_FAILURE),
            (ErrorKind.ORG_NOT_FOUND, ExitStatus.AUTH_FAILURE),
            (ErrorKind.APP_NOT_INSTALLED, ExitStatus.AUTH_FAILURE),
            (ErrorKind.RATE_LIMIT_WAIT_EXCEEDED, ExitStatus.STOPPED_RESUMABLE),
            (ErrorKind.SYSTEMIC_FAILURE, ExitStatus.STOPPED_RESUMABLE),
            (ErrorKind.REQUEST_FAILED, ExitStatus.UNEXPECTED_FAILURE),
        ],
    )
    def test_every_error_kind_maps_to_its_documented_status(self, kind, expected):
        exc = OrgHarvestError("boom", kind=kind)
        assert exit_status_for_error(exc) is expected

    def test_user_interrupt_and_concurrent_run_are_distinct_values(self):
        # Not reachable through exit_status_for_error (see module docstring)
        # but must exist and stay distinct from every other status (FR-10).
        values = {s.value for s in ExitStatus}
        assert len(values) == len(list(ExitStatus))
        assert ExitStatus.USER_INTERRUPT.value == 130
        assert ExitStatus.CONCURRENT_RUN_REFUSED not in (
            ExitStatus.SUCCESS,
            ExitStatus.COMPLETED_WITH_GAPS,
        )


class TestPreflightGating:
    async def test_preflight_failure_maps_to_its_exit_status_no_snapshot_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_preflight(*args, **kwargs):
            raise OrgHarvestError("no such org", kind=ErrorKind.ORG_NOT_FOUND)

        monkeypatch.setattr("org_harvest.run.run_preflight", fake_preflight)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.AUTH_FAILURE
        assert result.snapshot_dir is None
        assert result.manifest is None
        assert "no such org" in (result.message or "")
        await transport.aclose()

    async def test_fail_fast_stops_before_creating_a_snapshot_dir_ac_6_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        calls: dict[str, int] = {}
        _patch_success(monkeypatch, blocked=("organization",), calls=calls)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path, fail_fast=True
        )
        assert result.exit_status is ExitStatus.PREFLIGHT_BLOCKED
        assert result.snapshot_dir is None
        assert "organization" in (result.message or "")
        assert "fetch_org" not in calls
        # The org claim (Story 13, FR-9) needs `tmp_path/acme` to exist to
        # hold its lock file, so the org directory itself now exists even
        # on a fail-fast abort — but no timestamped snapshot directory
        # inside it was ever created.
        assert not any((tmp_path / "acme").glob("2*"))
        await transport.aclose()

    async def test_blocked_without_fail_fast_proceeds_ac_6_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        calls: dict[str, int] = {}
        _patch_success(monkeypatch, blocked=("organization",), calls=calls)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.SUCCESS
        assert calls["fetch_org"] == 1
        assert calls["fetch_repo"] == 1
        await transport.aclose()


class TestHappyPath:
    async def test_clean_run_exits_success_and_writes_manifest_ac_1_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.SUCCESS
        assert result.manifest is not None
        assert result.manifest.dataset_counts == {"organization": 1, "issues": 5}
        assert result.snapshot_dir is not None
        assert is_snapshot_complete(result.snapshot_dir)
        await transport.aclose()

    async def test_directory_layout_is_root_org_lowercased_timestamp_ac_1_6(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="ACME", snapshot_root=tmp_path
        )
        assert result.snapshot_dir is not None
        assert result.snapshot_dir.parent.parent == tmp_path
        assert result.snapshot_dir.parent.name == "acme"
        assert TIMESTAMP_RE.match(result.snapshot_dir.name)
        await transport.aclose()

    async def test_root_index_is_rebuilt_after_a_run_ac_8_8(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.snapshot_dir is not None
        assert (result.snapshot_dir.parent / "index.json").exists()
        await transport.aclose()

    async def test_gap_in_a_dataset_outcome_means_completed_with_gaps_ac_5_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        gap = Gap.now("issues", resource_id="R_1", field_path=None, reason="boom")
        _patch_success(monkeypatch, repo_gaps=(gap,))
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.COMPLETED_WITH_GAPS
        assert result.manifest is not None
        assert result.manifest.gaps == (gap,)
        await transport.aclose()

    async def test_scope_restriction_alone_means_completed_with_gaps_ac_5_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_success(monkeypatch, scope_restricted=True)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.COMPLETED_WITH_GAPS
        assert result.manifest is not None
        assert result.manifest.scope_restricted is True
        await transport.aclose()


class TestPhaseFailure:
    async def test_phase_1_failure_is_stopped_resumable_with_no_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_preflight(*args, **kwargs):
            return _report()

        async def fake_fetch_org(*args, **kwargs):
            raise OrgHarvestError("outage", kind=ErrorKind.SYSTEMIC_FAILURE)

        monkeypatch.setattr("org_harvest.run.run_preflight", fake_preflight)
        monkeypatch.setattr("org_harvest.run.fetch_organization_directory", fake_fetch_org)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.STOPPED_RESUMABLE
        assert result.manifest is None
        assert result.snapshot_dir is not None
        assert (result.snapshot_dir / "checkpoint.json").exists()
        assert not is_snapshot_complete(result.snapshot_dir)
        await transport.aclose()

    async def test_phase_2_failure_is_stopped_resumable_with_no_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_preflight(*args, **kwargs):
            return _report()

        async def fake_fetch_org(*args, **kwargs):
            return _org_result()

        async def fake_fetch_repo(*args, **kwargs):
            raise OrgHarvestError("token expired", kind=ErrorKind.RATE_LIMIT_WAIT_EXCEEDED)

        monkeypatch.setattr("org_harvest.run.run_preflight", fake_preflight)
        monkeypatch.setattr("org_harvest.run.fetch_organization_directory", fake_fetch_org)
        monkeypatch.setattr("org_harvest.run.fetch_repository_datasets", fake_fetch_repo)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.STOPPED_RESUMABLE
        assert result.manifest is None
        assert not is_snapshot_complete(result.snapshot_dir)
        await transport.aclose()


class TestConsumptionStats:
    async def test_manifest_consumption_reflects_transport_counters_ac_1_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        transport = _transport()
        with respx.mock:
            respx.post("https://example.test/graphql").mock(
                return_value=httpx.Response(200, json={})
            )
            respx.get("https://example.test/rest").mock(return_value=httpx.Response(200, json={}))

            await transport.send_graphql(
                "https://example.test/graphql",
                payload={},
                extract_budget=lambda resp: RateLimitSnapshot(
                    limit=5000, remaining=4990, reset_at=1.0
                ),
            )
            await transport.send_graphql(
                "https://example.test/graphql",
                payload={},
                extract_budget=lambda resp: RateLimitSnapshot(
                    limit=5000, remaining=4980, reset_at=1.0
                ),
            )
            await transport.send_rest("GET", "https://example.test/rest")

        _patch_success(monkeypatch)
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.manifest is not None
        c = result.manifest.consumption
        assert c.graphql_points_consumed == 10  # 4990 -> 4980
        assert c.graphql_requests == 2
        assert c.rest_requests_consumed == 1
        assert c.rate_limit_waits == 0
        await transport.aclose()


class TestResume:
    async def test_no_incomplete_snapshot_means_a_fresh_run_resumed_from_is_none_ac_4_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.resumed_from is None
        await transport.aclose()

    async def test_default_resumes_the_newest_incomplete_snapshot_ac_4_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        org_dir = tmp_path / "acme"
        existing = org_dir / _recent_compact(5)
        existing.mkdir(parents=True)
        CheckpointStore.create(
            existing / "checkpoint.json", org="acme", dataset_selection=("organization",)
        )
        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport,
            transport.credentials,
            org="acme",
            snapshot_root=tmp_path,
            dataset_names=("organization",),
        )
        assert result.resumed_from == existing
        assert result.snapshot_dir == existing
        subdirs = [p for p in org_dir.iterdir() if p.is_dir()]
        assert subdirs == [existing]  # no second snapshot dir was created
        await transport.aclose()

    async def test_force_fresh_ignores_an_existing_incomplete_snapshot_ac_4_7(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        org_dir = tmp_path / "acme"
        existing = org_dir / _recent_compact(5)
        existing.mkdir(parents=True)
        CheckpointStore.create(
            existing / "checkpoint.json", org="acme", dataset_selection=("organization",)
        )
        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport,
            transport.credentials,
            org="acme",
            snapshot_root=tmp_path,
            force_fresh=True,
        )
        assert result.resumed_from is None
        assert result.snapshot_dir != existing
        subdirs = [p for p in org_dir.iterdir() if p.is_dir()]
        assert len(subdirs) == 2  # the old one, plus a fresh one
        await transport.aclose()

    async def test_resume_by_name_targets_that_exact_snapshot_ac_4_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        org_dir = tmp_path / "acme"
        older = org_dir / _recent_compact(10)
        newer = org_dir / _recent_compact(5)
        for snap in (older, newer):
            snap.mkdir(parents=True)
            CheckpointStore.create(
                snap / "checkpoint.json", org="acme", dataset_selection=("organization",)
            )
        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport,
            transport.credentials,
            org="acme",
            snapshot_root=tmp_path,
            dataset_names=("organization",),
            resume=older.name,
        )
        assert result.resumed_from == older  # named snapshot wins over the newer one
        await transport.aclose()

    async def test_resume_by_unknown_name_is_invalid_usage_no_network_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        calls: dict[str, int] = {}
        _patch_success(monkeypatch, calls=calls)
        transport = _transport()
        result = await run_snapshot(
            transport,
            transport.credentials,
            org="acme",
            snapshot_root=tmp_path,
            resume="does-not-exist",
        )
        assert result.exit_status is ExitStatus.INVALID_USAGE
        assert result.snapshot_dir is None
        assert "does-not-exist" in (result.message or "")
        assert "preflight" not in calls
        await transport.aclose()


class TestResumeSafetyGuards:
    async def test_org_mismatch_refuses_resume_ac_4_8(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        snap = org_dir / _recent_compact(1)
        snap.mkdir(parents=True)
        CheckpointStore.create(
            snap / "checkpoint.json", org="OTHERORG", dataset_selection=("organization",)
        )
        transport = _transport()
        result = await run_snapshot(
            transport,
            transport.credentials,
            org="acme",
            snapshot_root=tmp_path,
            dataset_names=("organization",),
        )
        assert result.exit_status is ExitStatus.INVALID_USAGE
        assert "org" in (result.message or "").lower()
        await transport.aclose()

    async def test_dataset_selection_mismatch_refuses_resume_ac_4_8(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        snap = org_dir / _recent_compact(1)
        snap.mkdir(parents=True)
        CheckpointStore.create(
            snap / "checkpoint.json", org="acme", dataset_selection=("organization", "members")
        )
        transport = _transport()
        result = await run_snapshot(
            transport,
            transport.credentials,
            org="acme",
            snapshot_root=tmp_path,
            dataset_names=("organization",),
        )
        assert result.exit_status is ExitStatus.INVALID_USAGE
        assert "selection" in (result.message or "").lower()
        await transport.aclose()

    async def test_repository_filter_mismatch_refuses_resume_ac_4_8(self, tmp_path: Path):
        from org_harvest.selection import RepositoryFilter

        org_dir = tmp_path / "acme"
        snap = org_dir / _recent_compact(1)
        snap.mkdir(parents=True)
        CheckpointStore.create(
            snap / "checkpoint.json",
            org="acme",
            dataset_selection=("organization",),
            repository_exclude_archived=True,
        )
        transport = _transport()
        result = await run_snapshot(
            transport,
            transport.credentials,
            org="acme",
            snapshot_root=tmp_path,
            dataset_names=("organization",),
            repository_filter=RepositoryFilter(),
        )
        assert result.exit_status is ExitStatus.INVALID_USAGE
        assert "repository filter" in (result.message or "").lower()
        await transport.aclose()

    async def test_matching_selection_and_filter_proceeds_to_resume(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from org_harvest.selection import RepositoryFilter

        org_dir = tmp_path / "acme"
        snap = org_dir / _recent_compact(1)
        snap.mkdir(parents=True)
        CheckpointStore.create(
            snap / "checkpoint.json",
            org="acme",
            dataset_selection=("organization",),
            repository_filter=("repo1",),
            repository_exclude_archived=True,
        )
        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport,
            transport.credentials,
            org="acme",
            snapshot_root=tmp_path,
            dataset_names=("organization",),
            repository_filter=RepositoryFilter(names=frozenset({"repo1"}), exclude_archived=True),
        )
        assert result.resumed_from == snap
        await transport.aclose()

    async def test_unreadable_checkpoint_refuses_resume_ac_4_9(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        snap = org_dir / _recent_compact(1)
        snap.mkdir(parents=True)
        (snap / "checkpoint.json").write_text("not json at all", encoding="utf-8")
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.INVALID_USAGE
        assert "--force-fresh" in (result.message or "")
        await transport.aclose()

    async def test_incompatible_schema_version_refuses_resume_ac_4_9(self, tmp_path: Path):
        import json

        org_dir = tmp_path / "acme"
        snap = org_dir / _recent_compact(1)
        snap.mkdir(parents=True)
        CheckpointStore.create(
            snap / "checkpoint.json", org="acme", dataset_selection=("organization",)
        )
        raw = json.loads((snap / "checkpoint.json").read_text(encoding="utf-8"))
        raw["schema_version"] = 999
        (snap / "checkpoint.json").write_text(json.dumps(raw), encoding="utf-8")
        transport = _transport()
        result = await run_snapshot(
            transport,
            transport.credentials,
            org="acme",
            snapshot_root=tmp_path,
            dataset_names=("organization",),
        )
        assert result.exit_status is ExitStatus.INVALID_USAGE
        assert "incompatible" in (result.message or "").lower()
        await transport.aclose()

    async def test_stale_snapshot_refuses_resume_by_default_ac_4_10(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        old_name = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y%m%dT%H%M%SZ")
        snap = org_dir / old_name
        snap.mkdir(parents=True)
        CheckpointStore.create(
            snap / "checkpoint.json", org="acme", dataset_selection=("organization",)
        )
        transport = _transport()
        result = await run_snapshot(
            transport,
            transport.credentials,
            org="acme",
            snapshot_root=tmp_path,
            dataset_names=("organization",),
        )
        assert result.exit_status is ExitStatus.INVALID_USAGE
        assert "stale" in (result.message or "").lower() or "days old" in (result.message or "")
        await transport.aclose()

    async def test_allow_stale_resume_overrides_the_staleness_refusal_ac_4_10(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        org_dir = tmp_path / "acme"
        old_name = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y%m%dT%H%M%SZ")
        snap = org_dir / old_name
        snap.mkdir(parents=True)
        CheckpointStore.create(
            snap / "checkpoint.json", org="acme", dataset_selection=("organization",)
        )
        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport,
            transport.credentials,
            org="acme",
            snapshot_root=tmp_path,
            dataset_names=("organization",),
            allow_stale_resume=True,
        )
        assert result.resumed_from == snap
        await transport.aclose()

    async def test_custom_stale_after_days_is_honored_ac_4_10(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        snap = org_dir / _recent_compact(1)  # one minute old
        snap.mkdir(parents=True)
        CheckpointStore.create(
            snap / "checkpoint.json", org="acme", dataset_selection=("organization",)
        )
        transport = _transport()
        result = await run_snapshot(
            transport,
            transport.credentials,
            org="acme",
            snapshot_root=tmp_path,
            dataset_names=("organization",),
            stale_after_days=0.0001,  # ~8.6 seconds — one minute is already stale
        )
        assert result.exit_status is ExitStatus.INVALID_USAGE
        await transport.aclose()


class TestConcurrentRun:
    async def test_a_live_claim_refuses_a_second_run_ec_13(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from org_harvest.lock import OrgClaim

        held = OrgClaim.acquire(tmp_path / "acme")
        assert isinstance(held, OrgClaim)
        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.CONCURRENT_RUN_REFUSED
        held.release()
        await transport.aclose()

    async def test_a_different_org_is_not_blocked_by_another_orgs_claim_ec_13(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from org_harvest.lock import OrgClaim

        held = OrgClaim.acquire(tmp_path / "widgets")
        assert isinstance(held, OrgClaim)
        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.SUCCESS
        held.release()
        await transport.aclose()

    async def test_a_stale_claim_is_reclaimed_with_a_warning_reported_ec_12(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import json

        org_dir = tmp_path / "acme"
        org_dir.mkdir(parents=True)
        (org_dir / ".run.lock").write_text(
            json.dumps({"pid": 2**30, "claimed_at": "2020-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )
        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.SUCCESS
        assert result.reclaimed_stale_claim is True
        await transport.aclose()

    async def test_the_claim_is_released_after_a_successful_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from org_harvest.lock import LOCK_FILENAME

        _patch_success(monkeypatch)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.SUCCESS
        assert not (tmp_path / "acme" / LOCK_FILENAME).exists()
        await transport.aclose()

    async def test_the_claim_is_released_even_when_a_phase_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from org_harvest.lock import LOCK_FILENAME

        async def fake_preflight(*args, **kwargs):
            return _report()

        async def fake_fetch_org(*args, **kwargs):
            raise OrgHarvestError("boom", kind=ErrorKind.REQUEST_FAILED)

        monkeypatch.setattr("org_harvest.run.run_preflight", fake_preflight)
        monkeypatch.setattr("org_harvest.run.fetch_organization_directory", fake_fetch_org)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.UNEXPECTED_FAILURE
        assert not (tmp_path / "acme" / LOCK_FILENAME).exists()
        await transport.aclose()


class TestInterruptOrchestration:
    async def test_a_cooperative_interrupt_during_phase_1_stops_before_phase_2_ac_4_11(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        calls: dict[str, int] = {}

        async def fake_preflight(*args, **kwargs):
            return _report()

        async def fake_fetch_org(*args, **kwargs):
            calls["fetch_org"] = calls.get("fetch_org", 0) + 1
            kwargs["interrupt"].requested = True
            return _org_result()

        async def fake_fetch_repo(*args, **kwargs):
            calls["fetch_repo"] = calls.get("fetch_repo", 0) + 1
            return _repo_result()

        def fake_finalize(*args, **kwargs):
            calls["finalize"] = calls.get("finalize", 0) + 1
            return FinalizeResult(())

        monkeypatch.setattr("org_harvest.run.run_preflight", fake_preflight)
        monkeypatch.setattr("org_harvest.run.fetch_organization_directory", fake_fetch_org)
        monkeypatch.setattr("org_harvest.run.fetch_repository_datasets", fake_fetch_repo)
        monkeypatch.setattr("org_harvest.run.finalize_snapshot", fake_finalize)

        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.USER_INTERRUPT
        assert result.manifest is None
        assert "fetch_repo" not in calls
        assert "finalize" not in calls
        assert result.snapshot_dir is not None
        assert "--resume" in (result.message or "")
        assert not (result.snapshot_dir / "manifest.json").exists()
        await transport.aclose()

    async def test_an_interrupt_during_phase_2_also_skips_finalize(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        calls: dict[str, int] = {}

        async def fake_preflight(*args, **kwargs):
            return _report()

        async def fake_fetch_org(*args, **kwargs):
            return _org_result()

        async def fake_fetch_repo(*args, **kwargs):
            kwargs["interrupt"].requested = True
            return _repo_result()

        def fake_finalize(*args, **kwargs):
            calls["finalize"] = calls.get("finalize", 0) + 1
            return FinalizeResult(())

        monkeypatch.setattr("org_harvest.run.run_preflight", fake_preflight)
        monkeypatch.setattr("org_harvest.run.fetch_organization_directory", fake_fetch_org)
        monkeypatch.setattr("org_harvest.run.fetch_repository_datasets", fake_fetch_repo)
        monkeypatch.setattr("org_harvest.run.finalize_snapshot", fake_finalize)

        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.USER_INTERRUPT
        assert result.manifest is None
        assert "finalize" not in calls
        await transport.aclose()

    async def test_the_claim_is_released_after_a_cooperative_interrupt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from org_harvest.lock import LOCK_FILENAME

        async def fake_preflight(*args, **kwargs):
            return _report()

        async def fake_fetch_org(*args, **kwargs):
            kwargs["interrupt"].requested = True
            return _org_result()

        monkeypatch.setattr("org_harvest.run.run_preflight", fake_preflight)
        monkeypatch.setattr("org_harvest.run.fetch_organization_directory", fake_fetch_org)
        transport = _transport()
        result = await run_snapshot(
            transport, transport.credentials, org="acme", snapshot_root=tmp_path
        )
        assert result.exit_status is ExitStatus.USER_INTERRUPT
        assert not (tmp_path / "acme" / LOCK_FILENAME).exists()
        await transport.aclose()
