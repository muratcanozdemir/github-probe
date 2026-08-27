# Story 2: Pace requests within GitHub's rate limits

**Status:** APPROVED
**Depends On:** 1
**UI Changes:** No

## User Story

As an operator sharing an App installation with other tools, I can have every request self-pace against GitHub's live rate limits so that the tool neither gets blocked nor starves other consumers.

## Acceptance Criteria

- AC-7.1: The tool paces requests against the live remaining budget reported by the API rather than an assumed limit, so standard, Enterprise Cloud, and Enterprise Server installations all work without configuration.
- AC-7.2: It stays within GitHub's concurrent-request and per-minute secondary limits, and backs off and reduces concurrency when a secondary limit is signalled.
- AC-7.3: On exhausting the hourly budget it waits until the advertised reset and continues, reporting the wait and its expected end.
- AC-7.6: Retries use exponential backoff with jitter; transient failures are retried up to a bounded count and permanent ones are not.
- AC-7.10: Every request carries an identifying user agent, and REST requests pin an explicit API version.

## Scope

**Included:**
- A shared request-sending path used by every future GraphQL and REST call, reading the live remaining-budget figures from API responses to pace subsequent requests.
- Bounded concurrency and reaction to a signalled secondary rate limit.
- Waiting out an exhausted hourly budget until the advertised reset, with the wait and its end reported.
- Exponential backoff with jitter for transient failures (network errors, timeouts, 429, 5xx, unparseable bodies), with a bounded retry count; permanent failures are not retried.
- The user agent and REST API version headers sent on every request.

**Excluded:**
- Stopping a wait early because a non-refreshable credential would expire first, an overall wait ceiling, a user-supplied consumption floor, and separate REST-budget accounting (Story 3) — this story establishes the core pacing loop; those are refinements on top of it.
- Reducing the alias-batch width on a node-limit failure (Story 6) — that refinement only exists once repository-level batching exists; this story's page-size behavior is the generic mechanism any paginated query can use.
- Any real dataset content — this story is exercised with synthetic/mocked requests through Story 1's authenticated transport, not real org data.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 1's acceptance criteria are met: the tool can obtain a valid, authenticated request path via either credential form.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/errors.py` — add `ErrorKind.REQUEST_FAILED` for a request that exhausts retries with no usable response (the raise point AC-5.3's gap-recording will later catch).
2. `src/org_harvest/ratelimit.py` — `RateLimitSnapshot`, `BudgetTracker` (AC-7.1, AC-7.3), `ConcurrencyLimiter` (AC-7.2), both with injectable `now`/`sleep` for waiting-free tests.
3. `src/org_harvest/transport.py` — `Transport.send()`: shared retry/backoff-with-jitter (AC-7.6), auth header injection via Story 1's `CredentialProvider`, identifying headers (AC-7.10), and budget pacing before each attempt. Rate-limit extraction is injected by the caller (`extract_budget`) rather than hardcoded, since GraphQL/REST report their budgets in different shapes that don't exist until Stories 5/6.
4. `src/org_harvest/__init__.py` — extend re-exports.

### Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `src/org_harvest/errors.py` | Modify | Add `REQUEST_FAILED` |
| `src/org_harvest/ratelimit.py` | Create | `RateLimitSnapshot`, `BudgetTracker`, `ConcurrencyLimiter` |
| `src/org_harvest/transport.py` | Create | `Transport` |
| `src/org_harvest/__init__.py` | Modify | Re-export new public types |
| `tests/test_ratelimit.py` | Create | Budget wait and concurrency-limiter behavior |
| `tests/test_transport.py` | Create | Full retry/pacing/header/concurrency coverage |

### Cross-Module Seams

One seam worth naming explicitly: `Transport.send()` calls `raise_on_unauthorized(self.credentials)` (Story 1) on a 401. Confirmed both sides in `tests/test_transport.py::TestRetryBehavior::test_401_raises_via_credential_provider_ac_3_4` — a static-token `Transport` receiving a mocked 401 raises `AUTH_EXPIRED`, proving Story 1's contract is actually wired into Story 2's request path, not just independently unit-tested.

### Testing Approach

- **Unit — `tests/test_ratelimit.py`:** `BudgetTracker` — no wait with no snapshot, no wait above the floor, waits the exact remaining time when exhausted with a controllable clock (AC-7.3), no wait when the reset time has already passed. `ConcurrencyLimiter` — bounds concurrent acquires (AC-7.2, lattice-style in-flight counter), reduces then auto-restores after its cooldown using a fully fake clock (no real sleep — AC-10.4), FIFO-ish fairness under contention.
- **Unit — `tests/test_transport.py`:** headers include Authorization/User-Agent/Content-Type for GraphQL-style calls and Accept/API-version for REST-style calls (AC-7.10); `extract_budget` callback updates the tracker (AC-7.1); a pre-exhausted budget causes a wait of the exact remaining duration before the request goes out (AC-7.3, fake sleep/now, no real delay); 429 and a transient network error are each retried and succeed, with the exact backoff+jitter value asserted (AC-7.6); retries exhausted raises `REQUEST_FAILED`; a non-retryable 4xx is returned to the caller, not raised or retried; a secondary-limit signal (403 + `Retry-After`) reduces the concurrency limit and honors the retry-after duration (AC-7.2); concurrent `send()` calls across a shared `Transport` stay within the configured bound (lattice-style handler-based measurement).

### Risks

- **Secondary-limit detection heuristic** (`403`/`429` with a `Retry-After` header, or body text containing "rate limit") is not a documented, guaranteed GitHub response contract — it is the best available signal without a live API to verify against. If GitHub's actual wording differs, the effect is a missed cooldown, not incorrect behavior (the normal 429 retryable-status path still catches most secondary-limit cases).
- **`ConcurrencyLimiter`'s cooldown restoration relies on a future `acquire()`/`release()` call to re-check the deadline** rather than a background timer — correct as long as in-flight requests always eventually release (guaranteed here, since `Transport.send()` releases in a `finally`), but worth remembering if this class is ever reused somewhere without that guarantee.

### Decisions Made

- **Rate-limit extraction is injected, not hardcoded** — Transport has no opinion on GraphQL vs. REST response shape; Story 5/6 supply real extractors. This keeps Story 2 fully testable against synthetic endpoints, independent of any dataset concept, and is the direct reason its test suite needed no GraphQL/REST fixtures at all.
- **A custom `asyncio.Condition`-based `ConcurrencyLimiter`, not a plain `asyncio.Semaphore`** — a semaphore's permit count can't be shrunk safely at runtime with in-flight holders; the secondary-limit reduction (AC-7.2) needs exactly that.
