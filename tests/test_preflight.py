from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import respx

from org_harvest.credentials import AppKeyCredentialProvider, StaticTokenCredentialProvider
from org_harvest.preflight import Verdict, run_preflight
from org_harvest.transport import Transport


def _rate_limit_json(limit: int = 5000, remaining: int = 4000) -> dict:
    reset_at = (datetime.now(UTC) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    return {"limit": limit, "remaining": remaining, "resetAt": reset_at, "cost": 1, "nodeCount": 1}


def _preflight_response(
    repo_count: int = 300, limit: int = 5000, remaining: int = 4000
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "rateLimit": _rate_limit_json(limit, remaining),
                "organization": {"repositories": {"totalCount": repo_count}},
            }
        },
    )


class TestPermissionVerdicts:
    async def test_ready_when_permission_granted(self):
        provider = StaticTokenCredentialProvider("ghs_x")
        provider.permissions = {"metadata": "read"}
        transport = Transport(provider)
        with respx.mock(base_url="https://api.github.com") as mock:
            mock.post("/graphql").mock(return_value=_preflight_response())
            report = await run_preflight(
                transport, provider, org="acme", dataset_names=("organization",)
            )
        assert report.dataset_verdicts[0].verdict is Verdict.READY
        await transport.aclose()

    async def test_blocked_when_permission_missing_ac_6_2(self):
        provider = StaticTokenCredentialProvider("ghs_x")
        provider.permissions = {"metadata": "read"}  # no "members"
        transport = Transport(provider)
        with respx.mock(base_url="https://api.github.com") as mock:
            mock.post("/graphql").mock(return_value=_preflight_response())
            report = await run_preflight(
                transport, provider, org="acme", dataset_names=("members",)
            )
        verdict = report.dataset_verdicts[0]
        assert verdict.verdict is Verdict.BLOCKED
        assert "members" in verdict.reason
        assert report.any_blocked is True
        await transport.aclose()

    async def test_degraded_when_permissions_unknown_ac_6_1(self):
        provider = StaticTokenCredentialProvider("ghs_x")  # permissions stay None
        transport = Transport(provider)
        with respx.mock(base_url="https://api.github.com") as mock:
            mock.post("/graphql").mock(return_value=_preflight_response())
            report = await run_preflight(
                transport, provider, org="acme", dataset_names=("organization",)
            )
        verdict = report.dataset_verdicts[0]
        assert verdict.verdict is Verdict.DEGRADED
        assert "unknown" in verdict.reason
        assert report.any_blocked is False
        await transport.aclose()

    async def test_multiple_datasets_get_independent_verdicts(self):
        provider = StaticTokenCredentialProvider("ghs_x")
        provider.permissions = {"metadata": "read", "issues": "read"}
        transport = Transport(provider)
        with respx.mock(base_url="https://api.github.com") as mock:
            mock.post("/graphql").mock(return_value=_preflight_response())
            report = await run_preflight(
                transport,
                provider,
                org="acme",
                dataset_names=("organization", "members", "issues"),
            )
        by_name = {v.dataset: v.verdict for v in report.dataset_verdicts}
        assert by_name["organization"] is Verdict.READY
        assert by_name["issues"] is Verdict.READY
        assert by_name["members"] is Verdict.BLOCKED
        await transport.aclose()


class TestScopeRestriction:
    async def test_reports_scope_restricted_installation_ec_3(self):
        provider = StaticTokenCredentialProvider("ghs_x")
        provider.repository_selection = "selected"
        transport = Transport(provider)
        with respx.mock(base_url="https://api.github.com") as mock:
            mock.post("/graphql").mock(return_value=_preflight_response())
            report = await run_preflight(
                transport, provider, org="acme", dataset_names=("organization",)
            )
        assert report.scope_restricted is True
        await transport.aclose()

    async def test_not_scope_restricted_when_all_repositories(self):
        provider = StaticTokenCredentialProvider("ghs_x")
        provider.repository_selection = "all"
        transport = Transport(provider)
        with respx.mock(base_url="https://api.github.com") as mock:
            mock.post("/graphql").mock(return_value=_preflight_response())
            report = await run_preflight(
                transport, provider, org="acme", dataset_names=("organization",)
            )
        assert report.scope_restricted is False
        await transport.aclose()


class TestCostEstimate:
    async def test_estimates_points_from_repo_and_dataset_counts_ac_6_3(self):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url="https://api.github.com") as mock:
            mock.post("/graphql").mock(
                return_value=_preflight_response(repo_count=100, limit=5000, remaining=4000)
            )
            # "organization" is org-level (1 pt), "issues" is repo-level (100 * 1 pt).
            report = await run_preflight(
                transport, provider, org="acme", dataset_names=("organization", "issues")
            )
        assert report.repository_count == 100
        assert report.estimated_points == 1 + 100 * 1
        assert report.estimated_duration_seconds == 0.0  # well within remaining budget
        await transport.aclose()

    async def test_estimates_additional_wait_when_budget_insufficient(self):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url="https://api.github.com") as mock:
            # 5000 repos * 1 point each for "issues" = 5000 points needed, but
            # only 100 remain in a 1000-point window -> at least one extra wait.
            mock.post("/graphql").mock(
                return_value=_preflight_response(repo_count=5000, limit=1000, remaining=100)
            )
            report = await run_preflight(transport, provider, org="acme", dataset_names=("issues",))
        assert report.estimated_duration_seconds is not None
        assert report.estimated_duration_seconds > 0
        await transport.aclose()


class TestGraphQLBudgetUpdated:
    async def test_transport_graphql_budget_reflects_preflight_response(self):
        provider = StaticTokenCredentialProvider("ghs_x")
        transport = Transport(provider)
        with respx.mock(base_url="https://api.github.com") as mock:
            mock.post("/graphql").mock(return_value=_preflight_response(limit=5000, remaining=4321))
            await run_preflight(transport, provider, org="acme", dataset_names=("organization",))
        assert transport.graphql_budget.snapshot is not None
        assert transport.graphql_budget.snapshot.remaining == 4321
        await transport.aclose()


class TestWithRealAppKeyProvider:
    async def test_preflight_end_to_end_with_app_key_provider(self, rsa_private_key_path):
        provider = AppKeyCredentialProvider(
            private_key_path=rsa_private_key_path, client_id="Iv1.abc", org="acme"
        )
        transport = Transport(provider)
        with respx.mock(base_url="https://api.github.com") as mock:
            mock.get("/orgs/acme/installation").mock(
                return_value=httpx.Response(200, json={"id": 1, "repository_selection": "all"})
            )
            expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            mock.post("/app/installations/1/access_tokens").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "token": "ghs_x",
                        "expires_at": expires_at,
                        "permissions": {"metadata": "read", "members": "read"},
                        "repository_selection": "all",
                    },
                )
            )
            mock.post("/graphql").mock(return_value=_preflight_response())
            report = await run_preflight(
                transport, provider, org="acme", dataset_names=("organization", "members")
            )
        assert all(v.verdict is Verdict.READY for v in report.dataset_verdicts)
        assert report.scope_restricted is False
        await transport.aclose()
        await provider.aclose()
