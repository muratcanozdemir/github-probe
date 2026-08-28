from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx

from org_harvest.credentials import StaticTokenCredentialProvider
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.ratelimit import BudgetTracker, RateLimitSnapshot
from org_harvest.transport import Transport

URL = "https://example.test/thing"


def _fake_sleep_recorder() -> tuple[list[float], object]:
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    return calls, fake_sleep


def _make_transport(**kwargs: Any) -> Transport:
    provider = StaticTokenCredentialProvider("ghs_test")
    return Transport(provider, **kwargs)


class TestHeaders:
    async def test_sends_authorization_and_user_agent_ac_7_10(self):
        transport = _make_transport()
        budget = BudgetTracker("graphql")
        with respx.mock:
            route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
            await transport.send("GET", URL, budget=budget)
        headers = route.calls[0].request.headers
        assert headers["authorization"] == "Bearer ghs_test"
        assert "org-harvest/" in headers["user-agent"]
        assert headers["content-type"] == "application/json"
        await transport.aclose()

    async def test_rest_style_headers_pin_api_version_ac_7_10(self):
        transport = _make_transport()
        budget = BudgetTracker("rest")
        with respx.mock:
            route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
            await transport.send("GET", URL, budget=budget, rest_style_headers=True)
        headers = route.calls[0].request.headers
        assert headers["x-github-api-version"]
        assert headers["accept"] == "application/vnd.github+json"
        await transport.aclose()


class TestBudgetPacing:
    async def test_updates_budget_from_extract_callback_ac_7_1(self):
        transport = _make_transport()
        budget = BudgetTracker("graphql")

        def extract(resp: httpx.Response) -> RateLimitSnapshot:
            return RateLimitSnapshot(limit=5000, remaining=4999, reset_at=123.0)

        with respx.mock:
            respx.get(URL).mock(return_value=httpx.Response(200, json={}))
            await transport.send("GET", URL, budget=budget, extract_budget=extract)

        assert budget.snapshot is not None
        assert budget.snapshot.remaining == 4999
        await transport.aclose()

    async def test_waits_before_sending_when_budget_exhausted_ac_7_3(self):
        calls, fake_sleep = _fake_sleep_recorder()
        transport = _make_transport(sleep=fake_sleep, now=lambda: 1_000.0)
        budget = BudgetTracker("graphql")
        budget.update(RateLimitSnapshot(limit=5000, remaining=0, reset_at=1_030.0))

        with respx.mock:
            respx.get(URL).mock(return_value=httpx.Response(200, json={}))
            await transport.send("GET", URL, budget=budget)

        assert calls[0] == 30.0
        await transport.aclose()


class TestRetryBehavior:
    async def test_retries_on_429_then_succeeds_ac_7_6(self):
        calls, fake_sleep = _fake_sleep_recorder()
        transport = _make_transport(sleep=fake_sleep, backoff_base_seconds=1.0, jitter=lambda: 0.0)
        budget = BudgetTracker("graphql")
        with respx.mock:
            route = respx.get(URL)
            route.side_effect = [httpx.Response(429), httpx.Response(200, json={"ok": True})]
            resp = await transport.send("GET", URL, budget=budget)

        assert resp.json() == {"ok": True}
        assert route.call_count == 2
        assert calls == [1.0]  # backoff_base * 2**0 + jitter(0)
        await transport.aclose()

    async def test_retries_on_transient_network_error(self):
        _, fake_sleep = _fake_sleep_recorder()
        transport = _make_transport(sleep=fake_sleep)
        budget = BudgetTracker("graphql")
        with respx.mock:
            route = respx.get(URL)
            route.side_effect = [
                httpx.ConnectError("boom"),
                httpx.Response(200, json={"ok": True}),
            ]
            resp = await transport.send("GET", URL, budget=budget)
        assert resp.json() == {"ok": True}
        await transport.aclose()

    async def test_exhausts_retries_and_raises_request_failed(self):
        _, fake_sleep = _fake_sleep_recorder()
        transport = _make_transport(sleep=fake_sleep, max_retries=2)
        budget = BudgetTracker("graphql")
        with respx.mock:
            respx.get(URL).mock(return_value=httpx.Response(503))
            with pytest.raises(OrgHarvestError) as exc_info:
                await transport.send("GET", URL, budget=budget)
        assert exc_info.value.kind is ErrorKind.REQUEST_FAILED
        await transport.aclose()

    async def test_non_retryable_status_is_returned_not_raised(self):
        transport = _make_transport()
        budget = BudgetTracker("graphql")
        with respx.mock:
            route = respx.get(URL).mock(return_value=httpx.Response(400))
            resp = await transport.send("GET", URL, budget=budget)
        assert resp.status_code == 400
        assert route.call_count == 1
        await transport.aclose()

    async def test_401_raises_via_credential_provider_ac_3_4(self):
        transport = _make_transport()  # static token -> not refreshable
        budget = BudgetTracker("graphql")
        with respx.mock:
            respx.get(URL).mock(return_value=httpx.Response(401))
            with pytest.raises(OrgHarvestError) as exc_info:
                await transport.send("GET", URL, budget=budget)
        assert exc_info.value.kind is ErrorKind.AUTH_EXPIRED
        await transport.aclose()


class TestSecondaryRateLimit:
    async def test_backs_off_on_secondary_limit_signal_ac_7_2(self):
        calls, fake_sleep = _fake_sleep_recorder()
        transport = _make_transport(sleep=fake_sleep)
        budget = BudgetTracker("graphql")
        with respx.mock:
            route = respx.get(URL)
            route.side_effect = [
                httpx.Response(403, headers={"Retry-After": "5"}, text="secondary rate limit"),
                httpx.Response(200, json={"ok": True}),
            ]
            resp = await transport.send("GET", URL, budget=budget)
        assert resp.json() == {"ok": True}
        assert calls[0] >= 5.0
        assert transport.current_concurrency_limit < 50  # default max_concurrency
        await transport.aclose()


class TestConcurrencyBound:
    async def test_bounds_concurrent_sends_across_calls(self):
        transport = _make_transport(max_concurrency=2)
        budget = BudgetTracker("graphql")
        in_flight = 0
        max_seen = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, max_seen
            in_flight += 1
            max_seen = max(max_seen, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return httpx.Response(200, json={})

        with respx.mock:
            respx.get(URL).mock(side_effect=handler)
            await asyncio.gather(*[transport.send("GET", URL, budget=budget) for _ in range(6)])
        assert max_seen <= 2
        await transport.aclose()


class TestReserveFloor:
    async def test_reserve_floor_waits_before_natural_exhaustion_ac_7_7(self):
        calls, fake_sleep = _fake_sleep_recorder()
        transport = _make_transport(sleep=fake_sleep, reserve_floor=100, now=lambda: 1_000.0)
        budget = BudgetTracker("graphql")
        budget.update(RateLimitSnapshot(limit=5000, remaining=50, reset_at=1_020.0))
        with respx.mock:
            respx.get(URL).mock(return_value=httpx.Response(200, json={}))
            await transport.send("GET", URL, budget=budget)
        assert calls == [20.0]
        await transport.aclose()


class TestWaitSafety:
    async def test_refuses_wait_that_outlasts_non_refreshable_token_ac_7_4(self):
        import time as _time

        from org_harvest.credentials import StaticTokenCredentialProvider

        # The token's expiry is real-wall-clock-based (Story 1); only the
        # *rate-limit* clock is faked here — so anchor expiry to real time.
        provider = StaticTokenCredentialProvider("ghs_x", expires_at=_time.time() + 10)
        transport = Transport(provider, now=lambda: 1_000.0)
        budget = BudgetTracker("graphql")
        # Rate limit resets in 45 minutes (fake-clock units) — well past the
        # token's real 10s left.
        budget.update(RateLimitSnapshot(limit=5000, remaining=0, reset_at=1_000.0 + 2700))

        with pytest.raises(OrgHarvestError) as exc_info:
            await transport.send("GET", URL, budget=budget)
        assert exc_info.value.kind is ErrorKind.RATE_LIMIT_WAIT_EXCEEDED
        await transport.aclose()

    async def test_allows_wait_within_non_refreshable_token_lifetime(self):
        import time as _time

        from org_harvest.credentials import StaticTokenCredentialProvider

        provider = StaticTokenCredentialProvider("ghs_x", expires_at=_time.time() + 3600)
        _, fake_sleep = _fake_sleep_recorder()
        transport = Transport(provider, sleep=fake_sleep, now=lambda: 1_000.0)
        budget = BudgetTracker("graphql")
        budget.update(RateLimitSnapshot(limit=5000, remaining=0, reset_at=1_000.0 + 30))

        with respx.mock:
            respx.get(URL).mock(return_value=httpx.Response(200, json={}))
            resp = await transport.send("GET", URL, budget=budget)
        assert resp.status_code == 200
        await transport.aclose()

    async def test_app_key_provider_never_blocked_by_lifetime_check(self, rsa_private_key_path):
        from org_harvest.credentials import AppKeyCredentialProvider

        provider = AppKeyCredentialProvider(
            private_key_path=rsa_private_key_path, client_id="Iv1.abc", org="acme"
        )
        _, fake_sleep = _fake_sleep_recorder()
        transport = Transport(provider, sleep=fake_sleep, now=lambda: 1_000.0)
        budget = BudgetTracker("graphql")
        # Even a very long wait is fine — the App-key provider always refreshes.
        budget.update(RateLimitSnapshot(limit=5000, remaining=0, reset_at=1_000.0 + 3600))

        with respx.mock(base_url="https://api.github.com") as mock:
            mock.get("/orgs/acme/installation").mock(
                return_value=httpx.Response(200, json={"id": 1, "repository_selection": "all"})
            )
            from datetime import UTC, datetime, timedelta

            expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            mock.post("/app/installations/1/access_tokens").mock(
                return_value=httpx.Response(
                    200, json={"token": "ghs_x", "expires_at": expires_at, "permissions": {}}
                )
            )
            mock.get(URL).mock(return_value=httpx.Response(200, json={}))
            resp = await transport.send("GET", URL, budget=budget)
        assert resp.status_code == 200
        await transport.aclose()
        await provider.aclose()

    async def test_refuses_wait_exceeding_total_wait_ceiling_ac_7_5(self):
        _, fake_sleep = _fake_sleep_recorder()
        transport = _make_transport(
            sleep=fake_sleep, now=lambda: 1_000.0, max_total_wait_seconds=100.0
        )
        budget = BudgetTracker("graphql")
        budget.update(RateLimitSnapshot(limit=5000, remaining=0, reset_at=1_000.0 + 150))

        with pytest.raises(OrgHarvestError) as exc_info:
            await transport.send("GET", URL, budget=budget)
        assert exc_info.value.kind is ErrorKind.RATE_LIMIT_WAIT_EXCEEDED
        await transport.aclose()

    async def test_accumulates_total_wait_across_multiple_waits(self):
        clock = {"t": 1_000.0}
        waited: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            waited.append(seconds)
            clock["t"] += seconds

        transport = _make_transport(
            sleep=fake_sleep, now=lambda: clock["t"], max_total_wait_seconds=1000.0
        )
        budget = BudgetTracker("graphql")

        with respx.mock:
            respx.get(URL).mock(return_value=httpx.Response(200, json={}))
            budget.update(RateLimitSnapshot(limit=5000, remaining=0, reset_at=clock["t"] + 30))
            await transport.send("GET", URL, budget=budget)
            assert transport.total_wait_seconds == 30.0

            budget.update(RateLimitSnapshot(limit=5000, remaining=0, reset_at=clock["t"] + 40))
            await transport.send("GET", URL, budget=budget)
            assert transport.total_wait_seconds == 70.0
        await transport.aclose()


class TestSeparateBudgets:
    async def test_graphql_and_rest_use_independent_budgets_ac_7_9(self):
        transport = _make_transport()
        with respx.mock:
            respx.post("https://example.test/graphql").mock(
                return_value=httpx.Response(200, json={})
            )
            respx.get("https://example.test/rest").mock(return_value=httpx.Response(200, json={}))

            def gql_extract(resp: httpx.Response) -> RateLimitSnapshot:
                return RateLimitSnapshot(limit=5000, remaining=4000, reset_at=1.0)

            def rest_extract(resp: httpx.Response) -> RateLimitSnapshot:
                return RateLimitSnapshot(limit=15000, remaining=14900, reset_at=2.0)

            await transport.send_graphql(
                "https://example.test/graphql", payload={"query": "{}"}, extract_budget=gql_extract
            )
            await transport.send_rest(
                "GET", "https://example.test/rest", extract_budget=rest_extract
            )

        assert transport.graphql_budget.snapshot is not None
        assert transport.graphql_budget.snapshot.remaining == 4000
        assert transport.rest_budget.snapshot is not None
        assert transport.rest_budget.snapshot.remaining == 14900
        await transport.aclose()

    async def test_send_graphql_uses_json_content_type(self):
        transport = _make_transport()
        with respx.mock:
            route = respx.post("https://example.test/graphql").mock(
                return_value=httpx.Response(200, json={})
            )
            await transport.send_graphql(
                "https://example.test/graphql", payload={"query": "{ viewer { login } }"}
            )
        sent = route.calls[0].request
        assert sent.headers["content-type"] == "application/json"
        import json as _json

        assert _json.loads(sent.content) == {"query": "{ viewer { login } }"}
        await transport.aclose()

    async def test_send_rest_uses_rest_headers(self):
        transport = _make_transport()
        with respx.mock:
            route = respx.get("https://example.test/rest").mock(
                return_value=httpx.Response(200, json={})
            )
            await transport.send_rest("GET", "https://example.test/rest")
        assert route.calls[0].request.headers["x-github-api-version"]
        await transport.aclose()


class TestRequestAndWaitCounters:
    async def test_send_graphql_and_send_rest_each_count_their_own_calls_ac_1_3(self):
        transport = _make_transport()
        with respx.mock:
            respx.post("https://example.test/graphql").mock(
                return_value=httpx.Response(200, json={})
            )
            respx.get("https://example.test/rest").mock(return_value=httpx.Response(200, json={}))
            await transport.send_graphql("https://example.test/graphql", payload={})
            await transport.send_graphql("https://example.test/graphql", payload={})
            await transport.send_rest("GET", "https://example.test/rest")
        assert transport.graphql_request_count == 2
        assert transport.rest_request_count == 1
        await transport.aclose()

    async def test_rate_limit_wait_count_increments_only_when_a_wait_actually_happens(self):
        calls, fake_sleep = _fake_sleep_recorder()
        transport = _make_transport(sleep=fake_sleep, now=lambda: 1_000.0)
        budget = BudgetTracker("graphql")
        with respx.mock:
            respx.get(URL).mock(return_value=httpx.Response(200, json={}))
            # No snapshot yet -> no wait.
            await transport.send("GET", URL, budget=budget)
            assert transport.rate_limit_wait_count == 0

            budget.update(RateLimitSnapshot(limit=5000, remaining=0, reset_at=1_030.0))
            await transport.send("GET", URL, budget=budget)
            assert transport.rate_limit_wait_count == 1
            assert calls == [30.0]
        await transport.aclose()
