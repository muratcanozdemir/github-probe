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

        async def fake_do_run(provider, *, org, snapshot_root, api_host, fail_fast):
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
