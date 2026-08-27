from __future__ import annotations

from pathlib import Path

from org_harvest.checkpoint import CHECKPOINT_SCHEMA_VERSION, CheckpointStore
from org_harvest.constants import TOOL_VERSION
from org_harvest.gaps import Gap


class TestCheckpointCreate:
    def test_create_writes_a_file_immediately(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        CheckpointStore.create(path, org="acme", dataset_selection=("organization",))
        assert path.exists()

    def test_create_records_org_selection_and_tool_version_ac_4_1(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        store = CheckpointStore.create(
            path, org="acme", dataset_selection=("organization", "members")
        )
        assert store.state.org == "acme"
        assert store.state.dataset_selection == ("organization", "members")
        assert store.state.tool_version == TOOL_VERSION
        assert store.state.schema_version == CHECKPOINT_SCHEMA_VERSION


class TestCheckpointRoundTrip:
    def test_load_reproduces_the_saved_state(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        store = CheckpointStore.create(
            path, org="acme", dataset_selection=("organization",), repository_filter=("repo1",)
        )
        store.set_cursor("members", "CURSOR_A")
        store.set_dataset_status("members", "in_progress")
        store.record_gap(Gap.now("members", resource_id="acme", field_path=None, reason="boom"))

        loaded = CheckpointStore.load(path)
        assert loaded.org == "acme"
        assert loaded.repository_filter == ("repo1",)
        assert loaded.cursors["members"] == "CURSOR_A"
        assert loaded.dataset_status["members"] == "in_progress"
        assert len(loaded.gaps) == 1
        assert loaded.gaps[0]["reason"] == "boom"

    def test_repository_filter_none_round_trips_as_none(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        CheckpointStore.create(path, org="acme", dataset_selection=("organization",))
        loaded = CheckpointStore.load(path)
        assert loaded.repository_filter is None


class TestCheckpointDurability:
    def test_save_leaves_no_tmp_file_behind(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        store = CheckpointStore.create(path, org="acme", dataset_selection=("organization",))
        store.set_cursor("members", "CURSOR_A")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_each_mutation_is_immediately_persisted(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        store = CheckpointStore.create(path, org="acme", dataset_selection=("organization",))
        store.set_dataset_status("organization", "complete")
        # A fresh load — simulating a process that crashed right after this
        # call — sees the mutation without any further save.
        assert CheckpointStore.load(path).dataset_status["organization"] == "complete"
