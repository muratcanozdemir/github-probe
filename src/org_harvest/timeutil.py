"""UTC timestamp helpers (FR-8: "All tool-generated timestamps shall be UTC
ISO-8601, with a filesystem-safe compact form for directory names.").

Centralized here so every module that stamps a gap, a checkpoint write, or a
snapshot directory name agrees on the exact same format.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    """An ISO-8601 UTC timestamp, e.g. `2026-08-28T12:34:56.789012+00:00`."""
    return datetime.now(UTC).isoformat()


def utc_now_compact() -> str:
    """A filesystem-safe compact UTC timestamp for snapshot directory names
    (AC-1.5), e.g. `20260828T123456Z`."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def parse_compact_utc(value: str) -> datetime:
    """Parses a `utc_now_compact()`-formatted timestamp back into a
    timezone-aware UTC `datetime` — used by Story 13's staleness check
    (AC-4.10) to compute a snapshot's age directly from its directory
    name, rather than a filesystem mtime that could be changed by an
    unrelated copy or backup operation. Raises `ValueError` if `value`
    isn't in the expected shape, e.g. a snapshot directory name that
    doesn't match `utc_now_compact()`'s format at all."""
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
