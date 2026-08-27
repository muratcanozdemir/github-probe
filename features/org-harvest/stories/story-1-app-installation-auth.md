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
