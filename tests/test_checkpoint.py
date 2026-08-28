from __future__ import annotations

import json
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

    def test_repository_exclude_flags_round_trip_ac_4_8(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        CheckpointStore.create(
            path,
            org="acme",
            dataset_selection=("organization",),
            repository_exclude_archived=True,
            repository_exclude_forks=True,
        )
        loaded = CheckpointStore.load(path)
        assert loaded.repository_exclude_archived is True
        assert loaded.repository_exclude_forks is True

    def test_repository_exclude_flags_default_false(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        CheckpointStore.create(path, org="acme", dataset_selection=("organization",))
        loaded = CheckpointStore.load(path)
        assert loaded.repository_exclude_archived is False
        assert loaded.repository_exclude_forks is False

    def test_a_checkpoint_written_before_the_exclude_flags_existed_still_loads(
        self, tmp_path: Path
    ):
        path = tmp_path / "checkpoint.json"
        CheckpointStore.create(path, org="acme", dataset_selection=("organization",))
        raw = json.loads(path.read_text(encoding="utf-8"))
        del raw["repository_exclude_archived"]
        del raw["repository_exclude_forks"]
        path.write_text(json.dumps(raw), encoding="utf-8")
        loaded = CheckpointStore.load(path)
        assert loaded.repository_exclude_archived is False
        assert loaded.repository_exclude_forks is False


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


class TestCheckpointResume:
    def test_resume_opens_the_same_state_a_fresh_load_would_see_ac_4_2(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        store = CheckpointStore.create(path, org="acme", dataset_selection=("organization",))
        store.set_dataset_status("organization", "complete")
        store.set_cursor("members", "CURSOR_A")

        resumed = CheckpointStore.resume(path)
        assert resumed.state.org == "acme"
        assert resumed.state.dataset_status["organization"] == "complete"
        assert resumed.state.cursors["members"] == "CURSOR_A"

    def test_resumed_store_can_keep_mutating_and_saving(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        CheckpointStore.create(path, org="acme", dataset_selection=("organization", "members"))

        resumed = CheckpointStore.resume(path)
        resumed.set_dataset_status("members", "complete")

        reloaded = CheckpointStore.load(path)
        assert reloaded.dataset_status["members"] == "complete"
