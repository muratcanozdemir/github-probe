"""Per-org concurrent-run claim (Story 13, FR-9, EC-12, EC-13).

A run claims `<org_dir>/.run.lock` for its duration, refusing to start when
another run already holds a live claim for that same org — but never
blocking a run against a *different* org sharing the same `--snapshot-root`,
since the claim file lives inside the org's own directory (`org_dir`),
scoped exactly the way `find_newest_incomplete_snapshot()` (`resume.py`)
already scopes snapshot discovery.

The claim carries a liveness signal — the claiming process's PID — checked
with a zero-signal `os.kill()` probe rather than trusted blindly, so a claim
left behind by a process that was killed (rather than exiting cleanly and
releasing it) is detected as stale and reclaimed automatically, with a
warning, instead of blocking every future run against that org forever
(EC-12).
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from org_harvest.timeutil import utc_now_iso

LOCK_FILENAME = ".run.lock"


@dataclass(frozen=True)
class ClaimConflict:
    """A still-live claim already held by another process (EC-13) —
    returned by `OrgClaim.acquire()` instead of a usable `OrgClaim`."""

    pid: int
    claimed_at: str


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but is owned by someone else — still alive as
        # far as we're concerned. Treating this as "alive" is the safe
        # direction to be wrong in: it can only cause an unnecessary refusal,
        # never a false reclaim of someone else's still-running claim.
        return True
    return True


def _read_claim(path: Path) -> tuple[int, str] | None:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return int(data["pid"]), str(data["claimed_at"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        # An unreadable or malformed claim file can't prove anyone is
        # actually running — treat it the same as no claim at all rather
        # than refusing every future run against this org forever.
        return None


def _write_claim(path: Path) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "claimed_at": utc_now_iso()}, f)
    os.replace(tmp_path, path)


class OrgClaim:
    """A held claim. Release it (directly, or via `with`) when the run
    ends so the next run doesn't have to wait out a liveness check against
    a process that already exited cleanly."""

    def __init__(self, path: Path, *, reclaimed_stale: bool = False) -> None:
        self._path = path
        #: `True` when this claim was acquired by detecting and overwriting
        #: a stale claim left by a terminated process (EC-12) — the caller
        #: reports this to the user as a warning, not silently.
        self.reclaimed_stale = reclaimed_stale

    @classmethod
    def acquire(cls, org_dir: Path) -> OrgClaim | ClaimConflict:
        org_dir.mkdir(parents=True, exist_ok=True)
        path = org_dir / LOCK_FILENAME
        existing = _read_claim(path)
        reclaimed_stale = False
        if existing is not None:
            pid, claimed_at = existing
            if _is_pid_alive(pid):
                return ClaimConflict(pid=pid, claimed_at=claimed_at)
            reclaimed_stale = True
        _write_claim(path)
        return cls(path, reclaimed_stale=reclaimed_stale)

    def release(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()

    def __enter__(self) -> OrgClaim:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
