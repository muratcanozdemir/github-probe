from __future__ import annotations

from pathlib import Path

from org_harvest.gaps import DatasetOutcome, Gap
from org_harvest.manifest import (
    CompletionStatus,
    ConsumptionStats,
    Manifest,
    build_manifest,
    is_snapshot_complete,
    read_manifest,
    rebuild_root_index,
    write_manifest,
)


def _manifest(**overrides) -> Manifest:
    defaults = dict(
        org="acme",
        api_host="github.com",
        tool_version="0.1.0",
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T01:00:00+00:00",
        dataset_selection=("organization", "members"),
        dataset_counts={"organization": 1, "members": 5},
        gaps=(),
        scope_restricted=False,
    )
    defaults.update(overrides)
    return Manifest(**defaults)


class TestManifestStatus:
    def test_clean_run_is_complete(self):
        assert _manifest().status is CompletionStatus.COMPLETE

    def test_gaps_mean_complete_with_gaps(self):
        gap = Gap.now("members", resource_id="acme", field_path=None, reason="boom")
        assert _manifest(gaps=(gap,)).status is CompletionStatus.COMPLETE_WITH_GAPS

    def test_scope_restriction_alone_means_complete_with_gaps(self):
        assert _manifest(scope_restricted=True).status is CompletionStatus.COMPLETE_WITH_GAPS


class TestManifestRoundTrip:
    def test_to_dict_from_dict_round_trips_every_field(self):
        gap = Gap.now("members", resource_id="acme", field_path="a.b", reason="boom")
        original = _manifest(
            gaps=(gap,),
            consumption=ConsumptionStats(graphql_points_consumed=42, rate_limit_waits=2),
        )
        restored = Manifest.from_dict(original.to_dict())
        assert restored == original

    def test_write_then_read_round_trips_ac_5_6(self, tmp_path: Path):
        manifest = _manifest(
            gaps=(Gap.now("members", resource_id=None, field_path=None, reason="x"),)
        )
        write_manifest(tmp_path, manifest)
        restored = read_manifest(tmp_path)
        assert restored == manifest
        # Gap presence is readable from the file alone, no console needed.
        assert len(restored.gaps) == 1

    def test_write_leaves_no_tmp_file_behind(self, tmp_path: Path):
        write_manifest(tmp_path, _manifest())
        assert list(tmp_path.glob("*.tmp")) == []


class TestIsSnapshotComplete:
    def test_false_when_no_manifest_exists_ac_8_11(self, tmp_path: Path):
        assert is_snapshot_complete(tmp_path) is False

    def test_true_once_a_manifest_is_written(self, tmp_path: Path):
        write_manifest(tmp_path, _manifest())
        assert is_snapshot_complete(tmp_path) is True

    def test_true_even_for_a_gapped_manifest(self, tmp_path: Path):
        """ "Incomplete" (AC-8.11) means no manifest at all, not "has gaps" —
        a gapped-but-finalized run still has a manifest and is a real,
        readable result, just not a clean one."""
        gap = Gap.now("members", resource_id=None, field_path=None, reason="x")
        write_manifest(tmp_path, _manifest(gaps=(gap,)))
        assert is_snapshot_complete(tmp_path) is True


class TestBuildManifest:
    def test_aggregates_counts_and_gaps_from_dataset_outcomes(self):
        gap = Gap.now("members", resource_id="acme", field_path=None, reason="boom")
        outcomes = (
            DatasetOutcome("organization", 1, ()),
            DatasetOutcome("members", 5, (gap,)),
        )
        manifest = build_manifest(
            org="acme",
            api_host="github.com",
            started_at="s",
            completed_at="c",
            dataset_selection=("organization", "members"),
            dataset_outcomes=outcomes,
        )
        assert manifest.dataset_counts == {"organization": 1, "members": 5}
        assert manifest.gaps == (gap,)
        assert manifest.status is CompletionStatus.COMPLETE_WITH_GAPS

    def test_folds_in_conversion_gaps_without_touching_counts(self):
        fetch_outcome = DatasetOutcome("organization", 1, ())
        conversion_gap = Gap.now(
            "organization", resource_id=None, field_path=None, reason="parquet failed"
        )
        conversion_outcome = DatasetOutcome("organization", 0, (conversion_gap,))
        manifest = build_manifest(
            org="acme",
            api_host="github.com",
            started_at="s",
            completed_at="c",
            dataset_selection=("organization",),
            dataset_outcomes=(fetch_outcome,),
            conversion_outcomes=(conversion_outcome,),
        )
        assert manifest.dataset_counts == {"organization": 1}  # fetch count preserved
        assert manifest.gaps == (conversion_gap,)

    def test_scope_restricted_flag_is_passed_through(self):
        manifest = build_manifest(
            org="acme",
            api_host="github.com",
            started_at="s",
            completed_at="c",
            dataset_selection=("organization",),
            scope_restricted=True,
        )
        assert manifest.scope_restricted is True
        assert manifest.status is CompletionStatus.COMPLETE_WITH_GAPS

    def test_uses_the_real_tool_version(self):
        from org_harvest.constants import TOOL_VERSION

        manifest = build_manifest(
            org="acme",
            api_host="github.com",
            started_at="s",
            completed_at="c",
            dataset_selection=(),
        )
        assert manifest.tool_version == TOOL_VERSION


class TestRootIndex:
    def test_lists_every_snapshot_directory_ac_8_8(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        (org_dir / "20260101T000000Z").mkdir(parents=True)
        (org_dir / "20260102T000000Z").mkdir(parents=True)
        index = rebuild_root_index(org_dir, "acme")
        assert {s.timestamp for s in index.snapshots} == {"20260101T000000Z", "20260102T000000Z"}

    def test_a_directory_with_no_manifest_is_incomplete(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        (org_dir / "20260101T000000Z").mkdir(parents=True)
        index = rebuild_root_index(org_dir, "acme")
        assert index.snapshots[0].status == "incomplete"
        assert index.latest_complete is None

    def test_latest_complete_ignores_gapped_and_scope_restricted_snapshots(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        clean_dir = org_dir / "20260101T000000Z"
        gapped_dir = org_dir / "20260102T000000Z"
        clean_dir.mkdir(parents=True)
        gapped_dir.mkdir(parents=True)
        write_manifest(clean_dir, _manifest())
        gap = Gap.now("members", resource_id=None, field_path=None, reason="x")
        write_manifest(gapped_dir, _manifest(gaps=(gap,)))

        index = rebuild_root_index(org_dir, "acme")
        assert index.latest_complete == "20260101T000000Z"

    def test_latest_complete_picks_the_newest_qualifying_snapshot(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        for ts in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
            d = org_dir / ts
            d.mkdir(parents=True)
            write_manifest(d, _manifest())
        index = rebuild_root_index(org_dir, "acme")
        assert index.latest_complete == "20260103T000000Z"

    def test_rebuild_writes_the_index_file_to_disk(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        org_dir.mkdir(parents=True)
        rebuild_root_index(org_dir, "acme")
        assert (org_dir / "index.json").exists()

    def test_org_with_no_snapshots_yet_is_a_valid_empty_index(self, tmp_path: Path):
        org_dir = tmp_path / "acme"
        index = rebuild_root_index(org_dir, "acme")
        assert index.snapshots == ()
        assert index.latest_complete is None
