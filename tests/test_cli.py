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
