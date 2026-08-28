"""Snapshot discovery for resuming an interrupted run (Story 12, AC-4.2,
AC-4.3, AC-4.4).

A snapshot directory is "incomplete" by Decision 5's own rule (already
relied on by `manifest.py`/`finalize.py`): it has a checkpoint but no
manifest. This module only answers "which directory should `run_snapshot()`
resume, if any" — it does not open or validate the checkpoint it finds.
Compatibility checks on a discovered checkpoint's contents (schema version,
org, dataset selection, repository filter, staleness) are Story 13's job,
not this one's.
"""

from __future__ import annotations

from pathlib import Path

from org_harvest.checkpoint import CHECKPOINT_FILENAME
from org_harvest.manifest import MANIFEST_FILENAME


def _is_incomplete_snapshot(path: Path) -> bool:
    return (path / CHECKPOINT_FILENAME).exists() and not (path / MANIFEST_FILENAME).exists()


def find_newest_incomplete_snapshot(org_dir: Path) -> Path | None:
    """Finds the newest incomplete snapshot directory directly under
    `org_dir` (i.e. `snapshot_root/org.lower()`), for automatic resume
    (AC-4.2). Snapshot directories are named with
    `timeutil.utc_now_compact()`'s lexicographically-sortable timestamp, so
    the newest is simply the highest directory name among incomplete
    candidates — no need to stat mtimes.

    Returns `None` when `org_dir` doesn't exist yet, or no incomplete
    snapshot is found among its children — either case means a run should
    start fresh instead (AC-4.4)."""
    if not org_dir.is_dir():
        return None
    candidates = [p for p in org_dir.iterdir() if p.is_dir() and _is_incomplete_snapshot(p)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


def find_named_snapshot(org_dir: Path, name: str) -> Path | None:
    """Resolves a user-named snapshot to resume (AC-4.3) — `name` is a bare
    snapshot directory name (e.g. `20260828T123456Z`), not a full or
    relative path. Returns `None` if no such directory exists under
    `org_dir` at all. Deliberately does not require the named snapshot to
    be incomplete: whether resuming an already-complete or otherwise
    unsafe snapshot should be refused is Story 13's compatibility-guard
    territory, not a "not found" case here."""
    candidate = org_dir / name
    return candidate if candidate.is_dir() else None
