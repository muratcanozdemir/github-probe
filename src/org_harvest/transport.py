"""The shared request-sending path used by every GraphQL and REST call
(architecture.md, Decision 3).

One place owns retry/backoff-with-jitter (AC-7.6), live-budget pacing
(AC-7.1, AC-7.3), adaptive concurrency (AC-7.2), and the identifying headers
GitHub asks for (AC-7.10). Authentication is injected through a
`CredentialProvider`, so the same `Transport` works under both credential
forms from Story 1 without branching.

Rate-limit budget extraction is deliberately left to the caller
(`extract_budget`): GraphQL reports its budget inside the JSON body and REST
reports it in response headers, and neither shape exists here — Stories 5/6
supply the real extractors when they build the GraphQL/REST clients. This
keeps Transport testable against synthetic requests, independent of any
dataset concept.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from org_harvest.constants import REST_API_VERSION, USER_AGENT
from org_harvest.credentials import CredentialProvider, raise_on_unauthorized
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.ratelimit import BudgetTracker, ConcurrencyLimiter, RateLimitSnapshot

ExtractBudget = Callable[[httpx.Response], RateLimitSnapshot | None]

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

_DEFAULT_MAX_CONCURRENCY = 50
_DEFAULT_MAX_RETRIES = 5
_DEFAULT_BACKOFF_BASE_SECONDS = 1.0
_DEFAULT_TIMEOUT_SECONDS = 30.0


def _looks_like_secondary_rate_limit(resp: httpx.Response) -> bool:
    if resp.status_code not in (403, 429):
        return False
    if "retry-after" in resp.headers:
        return True
    return "rate limit" in resp.text.lower()


class Transport:
    """Sends one request at a time with pacing, retry, and adaptive
    concurrency. Higher-level GraphQL/REST clients (Story 5/6) build on
    this rather than talking to httpx directly."""

    def __init__(
        self,
        credentials: CredentialProvider,
        *,
        max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base_seconds: float = _DEFAULT_BACKOFF_BASE_SECONDS,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], float] = time.time,
        jitter: Callable[[], float] = lambda: random.uniform(0, 0.25),
    ) -> None:
        self.credentials = credentials
        self._http = httpx.AsyncClient(timeout=timeout_seconds)
        self._limiter = ConcurrencyLimiter(max_concurrency)
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._sleep = sleep
        self._now = now
        self._jitter = jitter

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def current_concurrency_limit(self) -> int:
        """The effective concurrency bound right now — lower than configured
        while a secondary-limit cooldown is active (AC-7.2)."""
        return self._limiter.current_max

    async def send(
        self,
        method: str,
        url: str,
        *,
        budget: BudgetTracker,
        extract_budget: ExtractBudget | None = None,
        json: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        rest_style_headers: bool = False,
    ) -> httpx.Response:
        """Send one request, pacing against `budget` and retrying transient
        failures. Returns the response for the caller to interpret (a 2xx or
        a non-retryable 4xx) — raises `OrgHarvestError` only for an
        unauthorized token or for exhausted retries."""
        last_exc: Exception | None = None
        retry_after_seconds = 0.0

        for attempt in range(self._max_retries + 1):
            await budget.wait_if_exhausted(sleep=self._sleep, now=self._now)
            headers = await self._build_headers(extra_headers, rest_style_headers)

            await self._limiter.acquire(now=self._now)
            try:
                resp = await self._http.request(method, url, json=json, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                resp = None
            finally:
                await self._limiter.release()

            if resp is not None:
                if extract_budget is not None:
                    snapshot = extract_budget(resp)
                    if snapshot is not None:
                        budget.update(snapshot)

                if resp.status_code == 401:
                    raise_on_unauthorized(self.credentials)

                if _looks_like_secondary_rate_limit(resp):
                    await self._limiter.signal_secondary_limit(now=self._now)
                    retry_after_seconds = float(resp.headers.get("retry-after", 0) or 0)
                    last_exc = OrgHarvestError(
                        f"secondary rate limit signalled by {url}",
                        kind=ErrorKind.REQUEST_FAILED,
                    )
                elif resp.status_code in RETRYABLE_STATUS:
                    last_exc = OrgHarvestError(
                        f"retryable status {resp.status_code} from {url}",
                        kind=ErrorKind.REQUEST_FAILED,
                    )
                else:
                    return resp

            if attempt >= self._max_retries:
                break
            backoff = max(retry_after_seconds, self._backoff_base * (2**attempt))
            await self._sleep(backoff + self._jitter())
            retry_after_seconds = 0.0

        raise OrgHarvestError(
            f"request to {url} failed after {self._max_retries + 1} attempts: {last_exc}",
            kind=ErrorKind.REQUEST_FAILED,
        ) from last_exc

    async def _build_headers(
        self, extra_headers: dict[str, str] | None, rest_style_headers: bool
    ) -> dict[str, str]:
        token = await self.credentials.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        }
        if rest_style_headers:
            headers["X-GitHub-Api-Version"] = REST_API_VERSION
            headers["Accept"] = "application/vnd.github+json"
        else:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        return headers
