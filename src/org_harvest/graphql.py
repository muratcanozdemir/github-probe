"""Shared GraphQL response helpers used by everything that issues a GraphQL
request directly through `Transport`: preflight (Story 4) and the org-level
and repository-level fetch engines (Stories 5/6).

Pulled out as its own module (DEV-3) rather than left as preflight's private
helper once a second caller needed the exact same extraction — one place to
get right rather than two copies that could drift.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from org_harvest.ratelimit import RateLimitSnapshot


def extract_rate_limit_snapshot(resp: httpx.Response) -> RateLimitSnapshot | None:
    """Reads the `rateLimit { limit remaining resetAt }` block every
    org-harvest GraphQL query asks for, alongside its real payload. Returns
    `None` when the response doesn't have the expected shape (e.g. a
    malformed body, or an error response with no `data` at all) rather than
    raising — a missing rate-limit figure is not itself a fetch failure."""
    try:
        data = resp.json()["data"]["rateLimit"]
    except (KeyError, TypeError, ValueError):
        return None
    return RateLimitSnapshot(
        limit=data["limit"],
        remaining=data["remaining"],
        reset_at=datetime.fromisoformat(data["resetAt"].replace("Z", "+00:00")).timestamp(),
    )
