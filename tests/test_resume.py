from __future__ import annotations

from pathlib import Path

from org_harvest.resume import find_named_snapshot, find_newest_incomplete_snapshot


def _make_snapshot(org_dir: Path, name: str, *, checkpoint: bool, manifest: bool) -> Path:
    snap = org_dir / name
    snap.mkdir(parents=True)
    if checkpoint:
        (snap / "checkpoint.json").write_text("{}", encoding="utf-8")
    if manifest:
        (snap / "manifest.json").write_text("{}", encoding="utf-8")
    return snap


class TestFindNewestIncompleteSnapshot:
    def test_missing_org_dir_returns_none_ac_4_4(self, tmp_path: Path):
        assert find_newest_incomplete_snapshot(tmp_path / "nope") is None

    def test_empty_org_dir_returns_none_ac_4_4(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        org_dir.mkdir()
        assert find_newest_incomplete_snapshot(org_dir) is None

    def test_a_complete_snapshot_is_never_returned_ac_4_4(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        _make_snapshot(org_dir, "20260101T000000Z", checkpoint=True, manifest=True)
        assert find_newest_incomplete_snapshot(org_dir) is None

    def test_picks_the_lexicographically_newest_incomplete_snapshot_ac_4_2(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        older = _make_snapshot(org_dir, "20260101T000000Z", checkpoint=True, manifest=False)
        newer = _make_snapshot(org_dir, "20260102T000000Z", checkpoint=True, manifest=False)
        assert older != newer
        assert find_newest_incomplete_snapshot(org_dir) == newer

    def test_ignores_a_newer_complete_snapshot_in_favor_of_an_older_incomplete_one(
        self, tmp_path: Path
    ):
        org_dir = tmp_path / "acme"
        incomplete = _make_snapshot(org_dir, "20260101T000000Z", checkpoint=True, manifest=False)
        _make_snapshot(org_dir, "20260102T000000Z", checkpoint=True, manifest=True)
        assert find_newest_incomplete_snapshot(org_dir) == incomplete

    def test_a_directory_with_neither_file_is_not_a_candidate(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        _make_snapshot(org_dir, "20260101T000000Z", checkpoint=False, manifest=False)
        assert find_newest_incomplete_snapshot(org_dir) is None


class TestFindNamedSnapshot:
    def test_returns_the_directory_when_it_exists_ac_4_3(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        snap = _make_snapshot(org_dir, "20260101T000000Z", checkpoint=True, manifest=False)
        assert find_named_snapshot(org_dir, "20260101T000000Z") == snap

    def test_returns_none_when_no_such_directory_exists(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        org_dir.mkdir()
        assert find_named_snapshot(org_dir, "20260101T000000Z") is None

    def test_returns_none_when_org_dir_itself_is_missing(self, tmp_path: Path):
        assert find_named_snapshot(tmp_path / "nope", "20260101T000000Z") is None

    def test_a_complete_named_snapshot_is_still_resolved_not_a_not_found_case(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        snap = _make_snapshot(org_dir, "20260101T000000Z", checkpoint=True, manifest=True)
        assert find_named_snapshot(org_dir, "20260101T000000Z") == snap
