from __future__ import annotations

import re

import pytest

from org_harvest.timeutil import parse_compact_utc, utc_now_compact, utc_now_iso

_COMPACT_RE = re.compile(r"^\d{8}T\d{6}Z$")


class TestUtcNowCompact:
    def test_matches_the_documented_format(self):
        assert _COMPACT_RE.match(utc_now_compact())


class TestUtcNowIso:
    def test_is_a_valid_iso_8601_string(self):
        from datetime import datetime

        datetime.fromisoformat(utc_now_iso())


class TestParseCompactUtc:
    def test_round_trips_through_utc_now_compact(self):
        compact = utc_now_compact()
        parsed = parse_compact_utc(compact)
        assert parsed.strftime("%Y%m%dT%H%M%SZ") == compact

    def test_parses_a_known_value(self):
        parsed = parse_compact_utc("20260828T123456Z")
        assert (parsed.year, parsed.month, parsed.day) == (2026, 8, 28)
        assert (parsed.hour, parsed.minute, parsed.second) == (12, 34, 56)

    def test_result_is_timezone_aware_utc(self):
        from datetime import UTC

        parsed = parse_compact_utc("20260828T123456Z")
        assert parsed.tzinfo == UTC

    def test_rejects_a_malformed_value(self):
        with pytest.raises(ValueError):
            parse_compact_utc("not-a-timestamp")
