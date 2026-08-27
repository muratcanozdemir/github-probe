from __future__ import annotations

import httpx

from org_harvest.graphql import extract_rate_limit_snapshot


class TestExtractRateLimitSnapshot:
    def test_reads_the_rate_limit_block(self):
        resp = httpx.Response(
            200,
            json={
                "data": {
                    "rateLimit": {
                        "limit": 5000,
                        "remaining": 4999,
                        "resetAt": "2099-01-01T00:00:00Z",
                        "cost": 1,
                        "nodeCount": 1,
                    }
                }
            },
        )
        snapshot = extract_rate_limit_snapshot(resp)
        assert snapshot is not None
        assert snapshot.limit == 5000
        assert snapshot.remaining == 4999

    def test_returns_none_for_a_malformed_body(self):
        resp = httpx.Response(200, json={"data": {}})
        assert extract_rate_limit_snapshot(resp) is None

    def test_returns_none_for_a_non_json_body(self):
        resp = httpx.Response(200, text="not json")
        assert extract_rate_limit_snapshot(resp) is None
