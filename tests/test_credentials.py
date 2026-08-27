from __future__ import annotations

from pathlib import Path

import httpx
import jwt
import pytest
import respx

from org_harvest.credentials import (
    AppKeyCredentialProvider,
    StaticTokenCredentialProvider,
    build_credential_provider,
    raise_on_unauthorized,
)
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.hosts import ApiHost
from tests.gh_responses import GITHUB, installation_response, token_response


class TestStaticTokenCredentialProvider:
    async def test_returns_token_without_network(self):
        provider = StaticTokenCredentialProvider("ghs_static")
        assert await provider.get_token() == "ghs_static"
        assert provider.can_refresh() is False
        assert provider.seconds_until_expiry() is None
        assert provider.installation_id is None
        await provider.aclose()

    def test_rejects_empty_token(self):
        with pytest.raises(OrgHarvestError) as exc_info:
            StaticTokenCredentialProvider("   ")
        assert exc_info.value.kind is ErrorKind.CREDENTIAL_INVALID


class TestBuildCredentialProvider:
    def test_rejects_both_forms_ac_3_6(self, rsa_private_key_path: Path):
        with pytest.raises(OrgHarvestError) as exc_info:
            build_credential_provider(
                private_key_path=rsa_private_key_path,
                client_id="Iv1.abc",
                token="ghs_x",
                org="acme",
            )
        assert exc_info.value.kind is ErrorKind.CREDENTIAL_CONFLICT

    def test_rejects_neither_form_ac_3_6(self):
        with pytest.raises(OrgHarvestError) as exc_info:
            build_credential_provider(private_key_path=None, client_id=None, token=None, org="acme")
        assert exc_info.value.kind is ErrorKind.CREDENTIAL_CONFLICT

    def test_returns_static_provider_for_token(self):
        provider = build_credential_provider(
            private_key_path=None, client_id=None, token="ghs_x", org="acme"
        )
        assert isinstance(provider, StaticTokenCredentialProvider)

    def test_returns_app_key_provider_for_key_and_client_id(self, rsa_private_key_path: Path):
        provider = build_credential_provider(
            private_key_path=rsa_private_key_path,
            client_id="Iv1.abc",
            token=None,
            org="acme",
        )
        assert isinstance(provider, AppKeyCredentialProvider)

    def test_rejects_partial_key_form(self, rsa_private_key_path: Path):
        with pytest.raises(OrgHarvestError) as exc_info:
            build_credential_provider(
                private_key_path=rsa_private_key_path,
                client_id=None,
                token=None,
                org="acme",
            )
        assert exc_info.value.kind is ErrorKind.CREDENTIAL_CONFLICT


class TestPrivateKeyValidation:
    def test_rejects_missing_file_ac_3_7(self, tmp_path: Path):
        with pytest.raises(OrgHarvestError) as exc_info:
            AppKeyCredentialProvider(
                private_key_path=tmp_path / "does-not-exist.pem",
                client_id="Iv1.abc",
                org="acme",
            )
        assert exc_info.value.kind is ErrorKind.CREDENTIAL_INVALID

    def test_rejects_malformed_pem_ac_3_7(self, tmp_path: Path):
        bad = tmp_path / "bad.pem"
        bad.write_text("this is not a pem file")
        with pytest.raises(OrgHarvestError) as exc_info:
            AppKeyCredentialProvider(private_key_path=bad, client_id="Iv1.abc", org="acme")
        assert exc_info.value.kind is ErrorKind.CREDENTIAL_INVALID

    def test_rejects_passphrase_protected_pem_ac_3_7(self, encrypted_rsa_private_key_path: Path):
        with pytest.raises(OrgHarvestError) as exc_info:
            AppKeyCredentialProvider(
                private_key_path=encrypted_rsa_private_key_path,
                client_id="Iv1.abc",
                org="acme",
            )
        assert exc_info.value.kind is ErrorKind.CREDENTIAL_INVALID

    def test_rejects_empty_client_id(self, rsa_private_key_path: Path):
        with pytest.raises(OrgHarvestError) as exc_info:
            AppKeyCredentialProvider(
                private_key_path=rsa_private_key_path, client_id="", org="acme"
            )
        assert exc_info.value.kind is ErrorKind.CREDENTIAL_INVALID


class TestAppKeyCredentialProviderMinting:
    async def test_discovers_and_mints_ac_3_1(self, rsa_private_key_path: Path):
        provider = AppKeyCredentialProvider(
            private_key_path=rsa_private_key_path, client_id="Iv1.abc", org="acme"
        )
        with respx.mock(base_url=GITHUB) as mock:
            install_route = mock.get("/orgs/acme/installation").mock(
                return_value=installation_response(installation_id=99)
            )
            token_route = mock.post("/app/installations/99/access_tokens").mock(
                return_value=token_response(token="ghs_minted")
            )
            token = await provider.get_token()

        assert token == "ghs_minted"
        assert provider.installation_id == 99
        assert provider.repository_selection == "all"
        assert provider.permissions == {"contents": "read", "members": "read"}

        install_headers = install_route.calls[0].request.headers
        assert install_headers["accept"] == "application/vnd.github+json"
        assert "org-harvest/" in install_headers["user-agent"]
        assert install_headers["x-github-api-version"]

        sent_jwt = install_headers["authorization"].removeprefix("Bearer ")
        claims = jwt.decode(sent_jwt, options={"verify_signature": False})
        assert claims["iss"] == "Iv1.abc"
        assert claims["iat"] < claims["exp"]
        assert claims["exp"] - claims["iat"] <= 600  # never exceeds the 10-minute ceiling

        assert token_route.calls[0].request.headers["authorization"] == f"Bearer {sent_jwt}"
        await provider.aclose()

    async def test_reuses_valid_token_without_reminting(self, rsa_private_key_path: Path):
        provider = AppKeyCredentialProvider(
            private_key_path=rsa_private_key_path, client_id="Iv1.abc", org="acme"
        )
        with respx.mock(base_url=GITHUB) as mock:
            mock.get("/orgs/acme/installation").mock(return_value=installation_response())
            token_route = mock.post("/app/installations/42/access_tokens").mock(
                return_value=token_response(expires_in_seconds=3600)
            )
            await provider.get_token()
            await provider.get_token()

        assert token_route.call_count == 1
        await provider.aclose()

    async def test_refreshes_before_expiry_ac_3_3(self, rsa_private_key_path: Path):
        provider = AppKeyCredentialProvider(
            private_key_path=rsa_private_key_path, client_id="Iv1.abc", org="acme"
        )
        with respx.mock(base_url=GITHUB) as mock:
            install_route = mock.get("/orgs/acme/installation").mock(
                return_value=installation_response()
            )
            token_route = mock.post("/app/installations/42/access_tokens")
            token_route.side_effect = [
                token_response(token="ghs_first", expires_in_seconds=60),
                token_response(token="ghs_second", expires_in_seconds=3600),
            ]
            first = await provider.get_token()
            second = await provider.get_token()

        assert first == "ghs_first"
        assert second == "ghs_second"
        assert token_route.call_count == 2
        assert install_route.call_count == 1  # installation id is cached
        await provider.aclose()

    async def test_retries_transient_failure_then_succeeds(self, rsa_private_key_path: Path):
        provider = AppKeyCredentialProvider(
            private_key_path=rsa_private_key_path, client_id="Iv1.abc", org="acme"
        )
        with respx.mock(base_url=GITHUB) as mock:
            install_route = mock.get("/orgs/acme/installation")
            install_route.side_effect = [
                httpx.Response(503),
                installation_response(),
            ]
            mock.post("/app/installations/42/access_tokens").mock(return_value=token_response())
            token = await provider.get_token()

        assert token == "ghs_abc123"
        assert install_route.call_count == 2
        await provider.aclose()


class TestAppKeyCredentialProviderFailureModes:
    async def test_distinguishes_org_not_found_ec_2(self, rsa_private_key_path: Path):
        provider = AppKeyCredentialProvider(
            private_key_path=rsa_private_key_path, client_id="Iv1.abc", org="ghost-org"
        )
        with respx.mock(base_url=GITHUB) as mock:
            mock.get("/orgs/ghost-org/installation").mock(return_value=httpx.Response(404))
            mock.get("/orgs/ghost-org").mock(return_value=httpx.Response(404))
            with pytest.raises(OrgHarvestError) as exc_info:
                await provider.get_token()
        assert exc_info.value.kind is ErrorKind.ORG_NOT_FOUND
        await provider.aclose()

    async def test_distinguishes_app_not_installed_ec_2(self, rsa_private_key_path: Path):
        provider = AppKeyCredentialProvider(
            private_key_path=rsa_private_key_path, client_id="Iv1.abc", org="acme"
        )
        with respx.mock(base_url=GITHUB) as mock:
            mock.get("/orgs/acme/installation").mock(return_value=httpx.Response(404))
            mock.get("/orgs/acme").mock(return_value=httpx.Response(200, json={"login": "acme"}))
            with pytest.raises(OrgHarvestError) as exc_info:
                await provider.get_token()
        assert exc_info.value.kind is ErrorKind.APP_NOT_INSTALLED
        await provider.aclose()

    async def test_detects_credential_mismatch_or_clock_skew(self, rsa_private_key_path: Path):
        provider = AppKeyCredentialProvider(
            private_key_path=rsa_private_key_path, client_id="Iv1.abc", org="acme"
        )
        with respx.mock(base_url=GITHUB) as mock:
            mock.get("/orgs/acme/installation").mock(return_value=httpx.Response(401))
            with pytest.raises(OrgHarvestError) as exc_info:
                await provider.get_token()
        assert exc_info.value.kind is ErrorKind.CREDENTIAL_INVALID
        assert "clock" in str(exc_info.value).lower()
        await provider.aclose()

    async def test_mint_failure_after_suspension_ec_6(self, rsa_private_key_path: Path):
        provider = AppKeyCredentialProvider(
            private_key_path=rsa_private_key_path, client_id="Iv1.abc", org="acme"
        )
        with respx.mock(base_url=GITHUB) as mock:
            mock.get("/orgs/acme/installation").mock(return_value=installation_response())
            mock.post("/app/installations/42/access_tokens").mock(return_value=httpx.Response(403))
            with pytest.raises(OrgHarvestError) as exc_info:
                await provider.get_token()
        assert exc_info.value.kind is ErrorKind.AUTH_FAILED
        await provider.aclose()


class TestRaiseOnUnauthorized:
    def test_static_token_reports_expired_ac_3_4(self):
        provider = StaticTokenCredentialProvider("ghs_static")
        with pytest.raises(OrgHarvestError) as exc_info:
            raise_on_unauthorized(provider)
        assert exc_info.value.kind is ErrorKind.AUTH_EXPIRED
        assert "private key" in str(exc_info.value).lower()

    def test_app_key_provider_reports_auth_failed(self, rsa_private_key_path: Path):
        provider = AppKeyCredentialProvider(
            private_key_path=rsa_private_key_path, client_id="Iv1.abc", org="acme"
        )
        with pytest.raises(OrgHarvestError) as exc_info:
            raise_on_unauthorized(provider)
        assert exc_info.value.kind is ErrorKind.AUTH_FAILED


async def test_api_host_is_configurable_ac_3_9(rsa_private_key_path: Path):
    provider = AppKeyCredentialProvider(
        private_key_path=rsa_private_key_path,
        client_id="Iv1.abc",
        org="acme",
        api_host=ApiHost("github.example.com"),
    )
    with respx.mock(base_url="https://github.example.com/api/v3") as mock:
        mock.get("/orgs/acme/installation").mock(return_value=installation_response())
        mock.post("/app/installations/42/access_tokens").mock(return_value=token_response())
        token = await provider.get_token()
    assert token == "ghs_abc123"
    await provider.aclose()


class TestStaticTokenKnownExpiry:
    async def test_reports_expiry_when_given(self):
        from datetime import UTC, datetime, timedelta

        expires_at = (datetime.now(UTC) + timedelta(seconds=120)).timestamp()
        provider = StaticTokenCredentialProvider("ghs_x", expires_at=expires_at)
        remaining = provider.seconds_until_expiry()
        assert remaining is not None
        assert 100 < remaining <= 120

    async def test_unknown_expiry_by_default(self):
        provider = StaticTokenCredentialProvider("ghs_x")
        assert provider.seconds_until_expiry() is None
