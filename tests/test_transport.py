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
