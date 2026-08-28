from __future__ import annotations

import json
import os
from pathlib import Path

from org_harvest.lock import LOCK_FILENAME, ClaimConflict, OrgClaim


class TestAcquire:
    def test_acquires_a_fresh_claim_and_writes_a_lock_file(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        result = OrgClaim.acquire(org_dir)
        assert isinstance(result, OrgClaim)
        assert (org_dir / LOCK_FILENAME).exists()
        assert result.reclaimed_stale is False

    def test_a_live_claim_by_this_same_process_is_a_conflict_ec_13(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        first = OrgClaim.acquire(org_dir)
        assert isinstance(first, OrgClaim)
        second = OrgClaim.acquire(org_dir)
        assert isinstance(second, ClaimConflict)
        assert second.pid == os.getpid()

    def test_releasing_lets_a_second_acquire_succeed(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        first = OrgClaim.acquire(org_dir)
        assert isinstance(first, OrgClaim)
        first.release()
        second = OrgClaim.acquire(org_dir)
        assert isinstance(second, OrgClaim)

    def test_context_manager_releases_on_exit(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        with OrgClaim.acquire(org_dir) as claim:
            assert isinstance(claim, OrgClaim)
            assert (org_dir / LOCK_FILENAME).exists()
        assert not (org_dir / LOCK_FILENAME).exists()

    def test_a_claim_left_by_a_dead_pid_is_reclaimed_with_a_warning_flag_ec_12(
        self, tmp_path: Path
    ):
        org_dir = tmp_path / "acme"
        org_dir.mkdir(parents=True)
        # A PID essentially guaranteed not to exist.
        dead_pid = 2**30
        (org_dir / LOCK_FILENAME).write_text(
            json.dumps({"pid": dead_pid, "claimed_at": "2020-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )
        result = OrgClaim.acquire(org_dir)
        assert isinstance(result, OrgClaim)
        assert result.reclaimed_stale is True

    def test_an_unreadable_claim_file_is_treated_as_no_claim(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        org_dir.mkdir(parents=True)
        (org_dir / LOCK_FILENAME).write_text("not json at all", encoding="utf-8")
        result = OrgClaim.acquire(org_dir)
        assert isinstance(result, OrgClaim)

    def test_different_orgs_never_conflict_with_each_other(self, tmp_path: Path):
        acme_claim = OrgClaim.acquire(tmp_path / "acme")
        widgets_claim = OrgClaim.acquire(tmp_path / "widgets")
        assert isinstance(acme_claim, OrgClaim)
        assert isinstance(widgets_claim, OrgClaim)

    def test_release_is_safe_to_call_when_the_file_is_already_gone(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        claim = OrgClaim.acquire(org_dir)
        assert isinstance(claim, OrgClaim)
        (org_dir / LOCK_FILENAME).unlink()
        claim.release()  # must not raise
