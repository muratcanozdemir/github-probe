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
