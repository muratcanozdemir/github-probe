# Story 1: Authenticate as a GitHub App installation

**Status:** APPROVED
**Depends On:** None
**UI Changes:** No

## User Story

As an operator running this in automation, I can supply either a GitHub App private key or a pre-minted installation token so that I can run it locally with full credentials and in CI without shipping a private key into the runner.

## Acceptance Criteria

- AC-3.1: Given a PEM private key, a client ID, and an org login, the tool discovers the installation for that org and mints its own installation token.
- AC-3.2: Given a pre-minted installation token, the tool uses it directly and never requires a private key.
- AC-3.3: When minting its own tokens, the tool refreshes the token before expiry so that a run longer than one hour continues uninterrupted.
- AC-3.4: When given a pre-minted token that expires mid-run, the run stops cleanly with resumable state and a message explaining that a private key enables automatic refresh.
- AC-3.5: Credentials never appear in the snapshot, the manifest, logs, progress output, or error messages.
- AC-3.6: Supplying neither credential form, or both, fails immediately, naming the accepted combinations.
- AC-3.7: Credential material that is missing, unreadable, malformed, passphrase-protected, or mismatched with the given client ID fails before any data request, with a message naming which of these it was.
- AC-3.8: The CLI accepts credentials via documented environment variables (the CI path) or explicit arguments; supplying a secret as a command-line argument emits a warning that it is visible in process listings.
- AC-3.9: The tool targets a configurable API base host, defaulting to github.com, so that Enterprise Cloud data-residency tenants and Enterprise Server hosts are supported.

## Scope

**Included:**
- Both credential forms: private-key-plus-client-ID (mint and auto-refresh) and pre-minted token (use directly, no refresh).
- Installation discovery for an org login given a private key.
- Credential validation before any data request, distinguishing the specific failure reason.
- Configurable API base host affecting where JWT and token-minting endpoints are targeted.
- Credential redaction across every surface this story touches (errors, logs).
- The CLI's credential input surface (env vars and arguments) and the argument-exposure warning.

**Excluded:**
- Any GraphQL data fetching or dataset concepts (Story 5 onward).
- Rate-limit pacing, retries, and backoff on requests made through this credential layer (Story 2) — this story establishes that requests carry the right auth, not how they're paced or retried.
- Permission and repository-scope introspection used for preflight reporting (Story 4) — this story only establishes that a token can be obtained and is valid; what it's permitted to do is a separate concern.
- Mid-run token-refresh failure due to a suspended or uninstalled App (covered by EC-6, deferred until a story exercises multi-window runs — Story 3).

## Entry Criteria (Functional)

Before starting this story, ensure:
- A registered GitHub App exists with a private key available, and/or a pre-minted installation token is available, for use in test fixtures.
- The target org login and expected installation relationship are known for test scenarios (installed / not installed).

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/errors.py` — single `OrgHarvestError` exception with an `ErrorKind` enum (AC-9.5's "one documented exception type" is established here since every later story raises through this same type).
2. `src/org_harvest/hosts.py` — `ApiHost` value object resolving REST/GraphQL base URLs for github.com, a GHEC data-residency host, or a GHES appliance host (AC-3.9).
3. `src/org_harvest/constants.py` — shared `USER_AGENT` and `REST_API_VERSION` constants, sent on every request from this story onward.
4. `src/org_harvest/credentials.py` — `CredentialProvider` Protocol; `StaticTokenCredentialProvider` (AC-3.2, AC-3.4's contract via `raise_on_unauthorized`); `AppKeyCredentialProvider` (AC-3.1, AC-3.3, AC-3.7, AC-3.9, plus EC-2 org/installation distinction and EC-6/EC-23 failure messages); `build_credential_provider()` factory enforcing AC-3.6.
5. `src/org_harvest/cli.py` — `org-harvest` click group, shared `_credential_options` decorator (AC-3.8, including the command-line-token warning), and an `auth-check` command exercising the whole story end to end.
6. `src/org_harvest/__init__.py` — flat re-exports of this story's public types.

### Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `pyproject.toml` | Create | Package metadata, deps (httpx, pyjwt[crypto], pyarrow, click), dev tooling (mypy strict, ruff, pytest, pytest-asyncio, respx) |
| `README.md` | Create (placeholder) | Satisfies hatchling's readme requirement; filled in by Story 16 |
| `src/org_harvest/errors.py` | Create | `OrgHarvestError`, `ErrorKind` |
| `src/org_harvest/hosts.py` | Create | `ApiHost` |
| `src/org_harvest/constants.py` | Create | `USER_AGENT`, `REST_API_VERSION`, `TOOL_VERSION` |
| `src/org_harvest/credentials.py` | Create | Both credential providers, factory, `raise_on_unauthorized` |
| `src/org_harvest/cli.py` | Create | CLI group, credential options, `auth-check` |
| `src/org_harvest/__init__.py` | Create | Flat re-exports |
| `tests/conftest.py` | Create | RSA test-key fixtures (plain + passphrase-protected PEM) |
| `tests/gh_responses.py` | Create | Shared mocked-response builders for installation discovery / token minting (not a test file; imported by two) |
| `tests/test_errors.py`, `tests/test_hosts.py`, `tests/test_credentials.py`, `tests/test_cli.py` | Create | Full test coverage below |

### Cross-Module Seams

No cross-module seams — this is a single-package Python project with no separate services/processes at this stage. The one boundary worth naming: `AppKeyCredentialProvider` sets `installation_id` / `repository_selection` / `permissions` as public attributes specifically so Story 4 (preflight) and Story 5 (EC-3 scoped-installation detection) can read them without re-deriving them. Confirmed — those are the exact attributes those later stories consume.

### Testing Approach

- **Unit — `tests/test_errors.py`, `tests/test_hosts.py`:** exception carries `kind`; `ApiHost` resolves all three host shapes correctly.
- **Unit — `tests/test_credentials.py`** (respx-mocked at the transport layer, no live calls — AC-10.2 applied from the start): static-token provider returns immediately with no network call and reports `can_refresh() is False`; `build_credential_provider` enforces AC-3.6 (both/neither/partial-key-form all rejected); PEM validation rejects missing/malformed/passphrase-protected files and an empty client ID (AC-3.7); full discover→mint flow asserts the outgoing JWT's `iss`/`iat`/`exp` claims and the request headers (Accept, User-Agent, API version) — request-shape assertions, not just parsed responses (AC-10.3); token reuse vs. refresh-before-expiry (AC-3.3) verified by asserting mint-endpoint call counts; org-not-found vs. app-not-installed distinguished via a follow-up `GET /orgs/{org}` (EC-2); a 401 on installation discovery raises with clock-skew wording (EC-23); a 403 on token minting after installation discovery succeeded raises `AUTH_FAILED` (EC-6); a transient 503 is retried and succeeds without any real sleep in the test (AC-10.4 applied from the start).
- **Unit — `tests/test_cli.py`** (click's `CliRunner`): conflicting credentials rejected; static token via CLI arg warns about process-listing visibility, via env var does not (AC-3.8); full app-key flow end to end through the CLI; org-not-found surfaces through to the CLI's exit code and message.

### Risks

- **JWT `iss` claim (client ID vs. numeric App ID):** GitHub's current docs recommend the client ID. Implemented that way; if a user supplies a numeric App ID here it will fail at the first bootstrap call with a clear `CREDENTIAL_INVALID`/clock-skew message rather than silently misbehaving — acceptable given the spec doesn't require distinguishing the two locally.
- **GHEC data-residency URL shape** is a documented heuristic (`api.` prefix ⇒ used as-is), not verified against a live tenant (no such tenant available to test against). Flagged in `hosts.py`'s docstring; revisit if a real tenant surfaces a different shape.

### Decisions Made

- **PyJWT + cryptography for JWT signing** — matches GitHub's own documented Python example (exploration.md), and `pyjwt[crypto]` pulls in `cryptography` for RS256 without a separate dependency.
- **`click` for the CLI** — matches `notecast-py`'s convention among the sibling projects, and its subcommand support is a real win here (Story 10 adds `run`, Story 4 adds `preflight`/`datasets`, Story 14 adds `retry-gaps`).
- **Bootstrap requests use their own small retry loop, not Story 2's Transport** — avoids a circular dependency (Transport is parameterized *by* a CredentialProvider, so the provider must work standalone), per architecture.md Decision 3.
- **`ruff format` adopted alongside `ruff check`** in place of `black` — matches `codegraph-lsp`/`notecast-py` rather than `lattice`, avoiding an extra dev dependency for formatting.
- **Removed `types-pyjwt` from dev dependencies** — PyJWT ships its own inline types (`py.typed`) and the separate stub package was stale, reporting `encode()` as returning `bytes` when it returns `str`; this was a hard mypy error, not a style choice.

### Notable additions beyond the story's own AC list

Two edge cases without a dedicated AC number are naturally satisfied here rather than left unhomed, since installation discovery is exactly where they surface: **EC-2** (org-not-found vs. app-not-installed) and **EC-23** (clock-skew wording on a 401 during discovery). Logged for traceability rather than silently folded in.
