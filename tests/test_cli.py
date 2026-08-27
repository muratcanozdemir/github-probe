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
