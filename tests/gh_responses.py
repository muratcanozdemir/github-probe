"""Shared mocked-response builders for GitHub's REST auth endpoints.

Not a test file itself (no `test_` prefix) — imported by tests that need to
mock installation discovery and token minting.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

GITHUB = "https://api.github.com"


def installation_response(
    installation_id: int = 42, repository_selection: str = "all"
) -> httpx.Response:
    return httpx.Response(
        200, json={"id": installation_id, "repository_selection": repository_selection}
    )


def token_response(token: str = "ghs_abc123", expires_in_seconds: int = 3600) -> httpx.Response:
    expires_at = (
        (datetime.now(UTC) + timedelta(seconds=expires_in_seconds))
        .isoformat()
        .replace("+00:00", "Z")
    )
    return httpx.Response(
        200,
        json={
            "token": token,
            "expires_at": expires_at,
            "permissions": {"contents": "read", "members": "read"},
            "repository_selection": "all",
        },
    )
