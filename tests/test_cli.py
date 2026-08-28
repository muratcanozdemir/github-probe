from __future__ import annotations

from pathlib import Path

import httpx
import respx
from click.testing import CliRunner

from org_harvest.cli import main
from tests.gh_responses import GITHUB, installation_response, token_response


def test_auth_check_rejects_conflicting_credentials_ac_3_6():
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["auth-check", "acme", "--token", "ghs_x", "--app-client-id", "Iv1.abc"],
    )
    assert result.exit_code == 1
    assert "not both" in result.output


def test_auth_check_with_static_token_warns_on_command_line_ac_3_8():
    runner = CliRunner()
    with respx.mock(base_url=GITHUB):
        result = runner.invoke(main, ["auth-check", "acme", "--token", "ghs_static"])
    assert result.exit_code == 0
    assert "visible in process listings" in result.output
    assert "pre-minted token" in result.output


def test_auth_check_with_token_env_var_no_warning(monkeypatch):
    monkeypatch.setenv("ORG_HARVEST_TOKEN", "ghs_env")
    runner = CliRunner()
    with respx.mock(base_url=GITHUB):
        result = runner.invoke(main, ["auth-check", "acme"])
    assert result.exit_code == 0
    assert "visible in process listings" not in result.output


def test_auth_check_with_app_key_ac_3_1(rsa_private_key_path: Path):
    runner = CliRunner()
    with respx.mock(base_url=GITHUB) as mock:
        mock.get("/orgs/acme/installation").mock(
            return_value=installation_response(installation_id=7)
        )
        mock.post("/app/installations/7/access_tokens").mock(return_value=token_response())
        result = runner.invoke(
            main,
            [
                "auth-check",
                "acme",
                "--app-private-key-path",
                str(rsa_private_key_path),
                "--app-client-id",
                "Iv1.abc",
            ],
        )
    assert result.exit_code == 0
    assert "installation id 7" in result.output


def test_auth_check_reports_org_not_found(rsa_private_key_path: Path):
    runner = CliRunner()
    with respx.mock(base_url=GITHUB) as mock:
        mock.get("/orgs/ghost/installation").mock(return_value=httpx.Response(404))
        mock.get("/orgs/ghost").mock(return_value=httpx.Response(404))
        result = runner.invoke(
            main,
            [
                "auth-check",
                "ghost",
                "--app-private-key-path",
                str(rsa_private_key_path),
                "--app-client-id",
                "Iv1.abc",
            ],
        )
    assert result.exit_code == 1
    assert "does not exist" in result.output


def test_datasets_list_shows_all_37_with_permissions_ac_2_7():
    runner = CliRunner()
    result = runner.invoke(main, ["datasets", "list"])
    assert result.exit_code == 0
    assert "organization [default/organization]" in result.output
    assert "issues [default/repository]" in result.output
    assert result.output.count("\n") == 37


def test_preflight_degraded_for_static_token_unknown_permissions_ac_6_1():
    from tests.gh_responses import preflight_response

    runner = CliRunner()
    with respx.mock(base_url=GITHUB) as mock:
        mock.post("/graphql").mock(return_value=preflight_response())
        result = runner.invoke(
            main,
            [
                "preflight",
                "acme",
                "--token",
                "ghs_x",
                "--datasets",
                "organization",
            ],
        )
    # A pre-minted token's permissions can't be introspected, so it's DEGRADED
    # rather than READY or BLOCKED — degraded alone does not fail the command.
    assert result.exit_code == 0
    assert "organization: degraded" in result.output


def test_preflight_ready_for_granted_permissions(rsa_private_key_path: Path):
    from tests.gh_responses import preflight_response

    runner = CliRunner()
    with respx.mock(base_url=GITHUB) as mock:
        mock.get("/orgs/acme/installation").mock(return_value=installation_response())
        mock.post("/app/installations/42/access_tokens").mock(return_value=token_response())
        mock.post("/graphql").mock(return_value=preflight_response())
        result = runner.invoke(
            main,
            [
                "preflight",
                "acme",
                "--app-private-key-path",
                str(rsa_private_key_path),
                "--app-client-id",
                "Iv1.abc",
                "--datasets",
                "members",
            ],
        )
    assert result.exit_code == 0
    assert "members: ready" in result.output


def test_preflight_exits_nonzero_when_blocked_ac_6_5(rsa_private_key_path: Path):
    from tests.gh_responses import preflight_response

    runner = CliRunner()
    with respx.mock(base_url=GITHUB) as mock:
        mock.get("/orgs/acme/installation").mock(return_value=installation_response())
        mock.post("/app/installations/42/access_tokens").mock(
            return_value=token_response()  # only grants "contents" and "members"
        )
        mock.post("/graphql").mock(return_value=preflight_response())
        result = runner.invoke(
            main,
            [
                "preflight",
                "acme",
                "--app-private-key-path",
                str(rsa_private_key_path),
                "--app-client-id",
                "Iv1.abc",
                "--datasets",
                "organization",
            ],
        )
    assert result.exit_code == 1
    assert "organization: blocked" in result.output


def test_preflight_rejects_unknown_dataset_name_ac_2_4():
    runner = CliRunner()
    result = runner.invoke(
        main, ["preflight", "acme", "--token", "ghs_x", "--datasets", "does_not_exist"]
    )
    assert result.exit_code == 1
    assert "does_not_exist" in result.output


def test_preflight_reports_scope_restriction_ec_3(rsa_private_key_path: Path):
    from tests.gh_responses import preflight_response

    runner = CliRunner()
    with respx.mock(base_url=GITHUB) as mock:
        mock.get("/orgs/acme/installation").mock(
            return_value=httpx.Response(200, json={"id": 5, "repository_selection": "selected"})
        )
        mock.post("/app/installations/5/access_tokens").mock(
            return_value=httpx.Response(
                200,
                json={
                    "token": "ghs_abc123",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "permissions": {"contents": "read", "members": "read"},
                    "repository_selection": "selected",
                },
            )
        )
        mock.post("/graphql").mock(return_value=preflight_response())
        result = runner.invoke(
            main,
            [
                "preflight",
                "acme",
                "--app-private-key-path",
                str(rsa_private_key_path),
                "--app-client-id",
                "Iv1.abc",
                "--datasets",
                "members",
            ],
        )
    assert result.exit_code == 0
    assert "scoped to selected repositories" in result.output


class TestRunCommand:
    def test_rejects_conflicting_credentials_before_any_network_call(self):
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", "acme", "--token", "ghs_x", "--app-client-id", "Iv1.abc"]
        )
        assert result.exit_code == 3  # ExitStatus.INVALID_USAGE
        assert "not both" in result.output

    def test_success_prints_summary_and_exits_zero_ac_1_3(self, tmp_path: Path, monkeypatch):
        from org_harvest.gaps import DatasetOutcome
        from org_harvest.manifest import ConsumptionStats, build_manifest
        from org_harvest.run import ExitStatus, RunResult

        snapshot_dir = tmp_path / "acme" / "20260101T000000Z"
        manifest = build_manifest(
            org="acme",
            api_host="github.com",
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:01:00+00:00",
            dataset_selection=("organization",),
            dataset_outcomes=(DatasetOutcome("organization", 1, ()),),
            consumption=ConsumptionStats(
                graphql_points_consumed=10,
                graphql_requests=2,
                rest_requests_consumed=0,
                rate_limit_waits=1,
            ),
        )
        result = RunResult(ExitStatus.SUCCESS, snapshot_dir, manifest, 12.5)

        async def fake_do_run(*args, **kwargs):
            return result

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x"])
        assert invocation.exit_code == 0
        assert "organization: 1" in invocation.output
        assert "elapsed: 12.5s" in invocation.output
        assert "graphql points consumed: 10" in invocation.output
        assert "rate-limit waits: 1" in invocation.output
        assert str(snapshot_dir) in invocation.output

    def test_completed_with_gaps_reports_gap_count_and_exits_one_ac_5_4(
        self, tmp_path, monkeypatch
    ):
        from org_harvest.gaps import DatasetOutcome, Gap
        from org_harvest.manifest import build_manifest
        from org_harvest.run import ExitStatus, RunResult

        snapshot_dir = tmp_path / "acme" / "20260101T000000Z"
        gap = Gap.now("organization", resource_id=None, field_path=None, reason="boom")
        manifest = build_manifest(
            org="acme",
            api_host="github.com",
            started_at="s",
            completed_at="c",
            dataset_selection=("organization",),
            dataset_outcomes=(DatasetOutcome("organization", 1, (gap,)),),
        )
        result = RunResult(ExitStatus.COMPLETED_WITH_GAPS, snapshot_dir, manifest, 1.0)

        async def fake_do_run(*args, **kwargs):
            return result

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x"])
        assert invocation.exit_code == 1
        assert "completed with 1 gap(s)" in invocation.output

    def test_preflight_blocked_exit_status_and_message(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        result = RunResult(
            ExitStatus.PREFLIGHT_BLOCKED,
            None,
            None,
            0.1,
            "preflight found blocked dataset(s): organization",
        )

        async def fake_do_run(*args, **kwargs):
            return result

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x", "--fail-fast"])
        assert invocation.exit_code == 6  # ExitStatus.PREFLIGHT_BLOCKED
        assert "preflight found blocked dataset(s)" in invocation.output

    def test_keyboard_interrupt_exits_130_ac_10(self, monkeypatch):
        async def fake_do_run(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x"])
        assert invocation.exit_code == 130
        assert "interrupted" in invocation.output

    def test_snapshot_root_option_is_accepted(self, tmp_path, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        captured = {}

        async def fake_do_run(provider, *, org, snapshot_root, api_host, fail_fast, **kwargs):
            captured["snapshot_root"] = snapshot_root
            return RunResult(ExitStatus.SUCCESS, None, None, 0.0)

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        custom_root = tmp_path / "custom-snapshots"
        invocation = runner.invoke(
            main, ["run", "acme", "--token", "ghs_x", "--snapshot-root", str(custom_root)]
        )
        assert invocation.exit_code == 0
        assert captured["snapshot_root"] == custom_root


class TestRunCommandDatasetAndRepoOptions:
    def test_datasets_option_is_resolved_and_passed_through_ac_2_1(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        captured = {}

        async def fake_do_run(provider, *, org, snapshot_root, api_host, fail_fast, **kwargs):
            captured.update(kwargs)
            return RunResult(ExitStatus.SUCCESS, None, None, 0.0)

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(
            main, ["run", "acme", "--token", "ghs_x", "--datasets", "organization,members"]
        )
        assert invocation.exit_code == 0
        assert captured["dataset_names"] == ("organization", "members")

    def test_no_datasets_option_passes_none_ac_2_2(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        captured = {}

        async def fake_do_run(provider, *, org, snapshot_root, api_host, fail_fast, **kwargs):
            captured.update(kwargs)
            return RunResult(ExitStatus.SUCCESS, None, None, 0.0)

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x"])
        assert invocation.exit_code == 0
        assert captured["dataset_names"] is None

    def test_repo_filter_flags_build_a_repository_filter_ac_2_8(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        captured = {}

        async def fake_do_run(provider, *, org, snapshot_root, api_host, fail_fast, **kwargs):
            captured.update(kwargs)
            return RunResult(ExitStatus.SUCCESS, None, None, 0.0)

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(
            main,
            [
                "run",
                "acme",
                "--token",
                "ghs_x",
                "--repos",
                "keep-a,keep-b",
                "--exclude-archived",
                "--exclude-forks",
            ],
        )
        assert invocation.exit_code == 0
        rf = captured["repository_filter"]
        assert rf.names == frozenset({"keep-a", "keep-b"})
        assert rf.exclude_archived is True
        assert rf.exclude_forks is True

    def test_no_repo_filter_flags_passes_none_not_a_noop_filter(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        captured = {}

        async def fake_do_run(provider, *, org, snapshot_root, api_host, fail_fast, **kwargs):
            captured.update(kwargs)
            return RunResult(ExitStatus.SUCCESS, None, None, 0.0)

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x"])
        assert invocation.exit_code == 0
        assert captured["repository_filter"] is None

    def test_max_items_per_collection_is_passed_through_ac_2_9(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        captured = {}

        async def fake_do_run(provider, *, org, snapshot_root, api_host, fail_fast, **kwargs):
            captured.update(kwargs)
            return RunResult(ExitStatus.SUCCESS, None, None, 0.0)

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(
            main, ["run", "acme", "--token", "ghs_x", "--max-items-per-collection", "500"]
        )
        assert invocation.exit_code == 0
        assert captured["item_cap"] == 500

    def test_run_reports_auto_included_dependencies_ac_2_6(self, tmp_path, monkeypatch):
        from org_harvest.gaps import DatasetOutcome
        from org_harvest.manifest import build_manifest
        from org_harvest.run import ExitStatus, RunResult

        manifest = build_manifest(
            org="acme",
            api_host="github.com",
            started_at="s",
            completed_at="c",
            dataset_selection=("issues", "repositories"),
            dataset_outcomes=(
                DatasetOutcome("issues", 1, ()),
                DatasetOutcome("repositories", 1, ()),
            ),
        )
        result = RunResult(
            ExitStatus.SUCCESS,
            tmp_path,
            manifest,
            1.0,
            auto_included_datasets=("repositories",),
        )

        async def fake_do_run(*args, **kwargs):
            return result

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(
            main, ["run", "acme", "--token", "ghs_x", "--datasets", "issues"]
        )
        assert invocation.exit_code == 0
        assert "auto-included dependencies: repositories" in invocation.output


class TestPreflightAutoIncludedReporting:
    def test_preflight_reports_auto_included_dependencies_ac_2_6(self):
        from tests.gh_responses import preflight_response

        runner = CliRunner()
        with respx.mock(base_url=GITHUB) as mock:
            mock.post("/graphql").mock(return_value=preflight_response())
            result = runner.invoke(
                main,
                ["preflight", "acme", "--token", "ghs_x", "--datasets", "team_members"],
            )
        assert result.exit_code == 0
        assert "auto-included dependencies:" in result.output
        assert "teams" in result.output


class TestRunCommandResumeOptions:
    def test_resume_option_is_passed_through_ac_4_3(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        captured = {}

        async def fake_do_run(provider, *, org, snapshot_root, api_host, fail_fast, **kwargs):
            captured.update(kwargs)
            return RunResult(ExitStatus.SUCCESS, None, None, 0.0)

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(
            main, ["run", "acme", "--token", "ghs_x", "--resume", "20260101T000000Z"]
        )
        assert invocation.exit_code == 0
        assert captured["resume"] == "20260101T000000Z"
        assert captured["force_fresh"] is False

    def test_force_fresh_flag_is_passed_through_ac_4_7(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        captured = {}

        async def fake_do_run(provider, *, org, snapshot_root, api_host, fail_fast, **kwargs):
            captured.update(kwargs)
            return RunResult(ExitStatus.SUCCESS, None, None, 0.0)

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x", "--force-fresh"])
        assert invocation.exit_code == 0
        assert captured["resume"] is None
        assert captured["force_fresh"] is True

    def test_neither_flag_given_passes_none_and_false(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        captured = {}

        async def fake_do_run(provider, *, org, snapshot_root, api_host, fail_fast, **kwargs):
            captured.update(kwargs)
            return RunResult(ExitStatus.SUCCESS, None, None, 0.0)

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x"])
        assert invocation.exit_code == 0
        assert captured["resume"] is None
        assert captured["force_fresh"] is False

    def test_resumed_from_is_reported_in_output_ac_4_2(self, tmp_path: Path, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        resumed = tmp_path / "acme" / "20260101T000000Z"
        result = RunResult(ExitStatus.SUCCESS, None, None, 0.0, resumed_from=resumed)

        async def fake_do_run(*args, **kwargs):
            return result

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x"])
        assert invocation.exit_code == 0
        assert f"resuming snapshot: {resumed}" in invocation.output


class TestRunCommandResumeSafetyOptions:
    def test_stale_after_days_and_allow_stale_resume_are_passed_through_ac_4_10(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        captured = {}

        async def fake_do_run(provider, *, org, snapshot_root, api_host, fail_fast, **kwargs):
            captured.update(kwargs)
            return RunResult(ExitStatus.SUCCESS, None, None, 0.0)

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(
            main,
            [
                "run",
                "acme",
                "--token",
                "ghs_x",
                "--stale-after-days",
                "3",
                "--allow-stale-resume",
            ],
        )
        assert invocation.exit_code == 0
        assert captured["stale_after_days"] == 3.0
        assert captured["allow_stale_resume"] is True

    def test_defaults_are_seven_days_and_not_allowed(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        captured = {}

        async def fake_do_run(provider, *, org, snapshot_root, api_host, fail_fast, **kwargs):
            captured.update(kwargs)
            return RunResult(ExitStatus.SUCCESS, None, None, 0.0)

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x"])
        assert invocation.exit_code == 0
        assert captured["stale_after_days"] == 7.0
        assert captured["allow_stale_resume"] is False

    def test_reclaimed_stale_claim_prints_a_warning_ec_12(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        result = RunResult(ExitStatus.SUCCESS, None, None, 0.0, reclaimed_stale_claim=True)

        async def fake_do_run(*args, **kwargs):
            return result

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x"])
        assert invocation.exit_code == 0
        assert "reclaimed a stale run claim" in invocation.output

    def test_concurrent_run_refused_exits_with_its_own_status(self, monkeypatch):
        from org_harvest.run import ExitStatus, RunResult

        result = RunResult(
            ExitStatus.CONCURRENT_RUN_REFUSED, None, None, 0.0, message="already running"
        )

        async def fake_do_run(*args, **kwargs):
            return result

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x"])
        assert invocation.exit_code == ExitStatus.CONCURRENT_RUN_REFUSED
        assert "already running" in invocation.output

    def test_user_interrupt_result_prints_the_resume_message_without_error_prefix_ac_4_11(
        self, tmp_path: Path, monkeypatch
    ):
        from org_harvest.run import ExitStatus, RunResult

        snap = tmp_path / "acme" / "20260101T000000Z"
        result = RunResult(
            ExitStatus.USER_INTERRUPT,
            snap,
            None,
            0.0,
            message="interrupted; resume with: org-harvest run acme --resume 20260101T000000Z",
        )

        async def fake_do_run(*args, **kwargs):
            return result

        monkeypatch.setattr("org_harvest.cli._do_run", fake_do_run)
        runner = CliRunner()
        invocation = runner.invoke(main, ["run", "acme", "--token", "ghs_x"])
        assert invocation.exit_code == 130
        assert "error:" not in invocation.output
        assert "resume with: org-harvest run acme --resume 20260101T000000Z" in invocation.output


class TestRetryGapsCommand:
    def test_reports_no_op_and_exits_zero_when_no_gaps_ac_11_5(self, tmp_path, monkeypatch):
        from org_harvest.retry import RetryResult

        async def fake_do_retry(provider, *, org, snapshot_dir, api_host):
            return RetryResult(retried=False, manifest=_manifest_stub())

        monkeypatch.setattr("org_harvest.cli._do_retry_gaps", fake_do_retry)
        runner = CliRunner()
        result = runner.invoke(main, ["retry-gaps", "acme", "20260101T000000Z", "--token", "ghs_x"])
        assert result.exit_code == 0
        assert "no gaps to retry" in result.output

    def test_reports_retried_datasets_and_exits_zero_when_all_resolved(self, tmp_path, monkeypatch):
        from org_harvest.retry import RetryResult

        async def fake_do_retry(provider, *, org, snapshot_dir, api_host):
            return RetryResult(
                retried=True, manifest=_manifest_stub(gaps=()), datasets_retried=("issues",)
            )

        monkeypatch.setattr("org_harvest.cli._do_retry_gaps", fake_do_retry)
        runner = CliRunner()
        result = runner.invoke(main, ["retry-gaps", "acme", "20260101T000000Z", "--token", "ghs_x"])
        assert result.exit_code == 0
        assert "retried datasets: issues" in result.output
        assert "all gaps resolved" in result.output

    def test_exits_with_completed_with_gaps_when_some_gaps_remain(self, tmp_path, monkeypatch):
        from org_harvest.gaps import Gap
        from org_harvest.retry import RetryResult
        from org_harvest.run import ExitStatus

        remaining_gap = Gap.now("issues", resource_id="R_1", field_path=None, reason="still broken")

        async def fake_do_retry(provider, *, org, snapshot_dir, api_host):
            return RetryResult(
                retried=True,
                manifest=_manifest_stub(gaps=(remaining_gap,)),
                datasets_retried=("issues",),
            )

        monkeypatch.setattr("org_harvest.cli._do_retry_gaps", fake_do_retry)
        runner = CliRunner()
        result = runner.invoke(main, ["retry-gaps", "acme", "20260101T000000Z", "--token", "ghs_x"])
        assert result.exit_code == ExitStatus.COMPLETED_WITH_GAPS
        assert "still 1 gap(s) remaining" in result.output

    def test_snapshot_dir_is_built_from_org_and_snapshot_root(self, tmp_path, monkeypatch):
        from org_harvest.retry import RetryResult

        captured = {}

        async def fake_do_retry(provider, *, org, snapshot_dir, api_host):
            captured["snapshot_dir"] = snapshot_dir
            return RetryResult(retried=False, manifest=_manifest_stub())

        monkeypatch.setattr("org_harvest.cli._do_retry_gaps", fake_do_retry)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "retry-gaps",
                "ACME",
                "20260101T000000Z",
                "--snapshot-root",
                str(tmp_path),
                "--token",
                "ghs_x",
            ],
        )
        assert result.exit_code == 0
        assert captured["snapshot_dir"] == tmp_path / "acme" / "20260101T000000Z"


def _manifest_stub(gaps=()):
    from org_harvest.gaps import DatasetOutcome
    from org_harvest.manifest import ConsumptionStats, build_manifest

    return build_manifest(
        org="acme",
        api_host="github.com",
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:01:00+00:00",
        dataset_selection=("issues",),
        dataset_outcomes=(DatasetOutcome("issues", 5, gaps),),
        consumption=ConsumptionStats(),
        last_retried_at="2026-01-02T00:00:00+00:00",
    )
