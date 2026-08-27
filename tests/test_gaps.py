from __future__ import annotations

from org_harvest.gaps import Gap


class TestGap:
    def test_now_stamps_a_utc_timestamp(self):
        gap = Gap.now("issues", resource_id="R_1", field_path="issues.0", reason="boom")
        assert gap.dataset == "issues"
        assert gap.resource_id == "R_1"
        assert gap.field_path == "issues.0"
        assert gap.reason == "boom"
        assert gap.occurred_at  # non-empty ISO-8601 string

    def test_to_dict_round_trips_every_field(self):
        gap = Gap.now("issues", resource_id=None, field_path=None, reason="boom")
        as_dict = gap.to_dict()
        assert as_dict == {
            "dataset": "issues",
            "resource_id": None,
            "field_path": None,
            "reason": "boom",
            "occurred_at": gap.occurred_at,
        }
