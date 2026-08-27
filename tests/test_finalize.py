from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from org_harvest.finalize import finalize_dataset, finalize_snapshot
from org_harvest.harvest.org_level import register_fetch_details as register_org_fetch_details
from org_harvest.harvest.repo_level import register_fetch_details as register_repo_fetch_details

# Field lists are only populated on the registry once a fetch engine has
# run (Story 5/6's `register_fetch_details()`); finalization depends on
# that having already happened during the harvest this snapshot came from.
register_org_fetch_details()
register_repo_fetch_details()


def _write_ndjson(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


class TestFinalizeDataset:
    def test_converts_ndjson_to_parquet_with_the_declared_schema_ac_8_2(self, tmp_path: Path):
        _write_ndjson(
            tmp_path / "organization.ndjson",
            [{"id": "O_1", "database_id": 1, "login": "acme", "is_verified": True}],
        )
        outcome = finalize_dataset(tmp_path, "organization")
        assert outcome.record_count == 1
        assert outcome.gaps == ()
        table = pq.read_table(tmp_path / "organization.parquet")
        assert table.num_rows == 1
        assert "login" in table.column_names
        assert table.column("database_id")[0].as_py() == 1
        assert table.column("is_verified")[0].as_py() is True

    def test_zero_record_dataset_produces_a_valid_loadable_file_ac_8_3(self, tmp_path: Path):
        _write_ndjson(tmp_path / "organization.ndjson", [])
        outcome = finalize_dataset(tmp_path, "organization")
        assert outcome.record_count == 0
        table = pq.read_table(tmp_path / "organization.parquet")
        assert table.num_rows == 0
        # The schema still declares every field, even with zero rows.
        assert "database_id" in table.column_names

    def test_schema_is_identical_whether_zero_rows_or_populated_ac_8_3(self, tmp_path: Path):
        _write_ndjson(tmp_path / "organization.ndjson", [])
        finalize_dataset(tmp_path, "organization")
        empty_schema = pq.read_schema(tmp_path / "organization.parquet")

        _write_ndjson(
            tmp_path / "organization.ndjson",
            [{"id": "O_1", "database_id": 1, "login": "acme"}],
        )
        finalize_dataset(tmp_path, "organization")
        populated_schema = pq.read_schema(tmp_path / "organization.parquet")

        assert empty_schema == populated_schema

    def test_column_null_throughout_stays_valid_ac_8_3(self, tmp_path: Path):
        _write_ndjson(
            tmp_path / "organization.ndjson",
            [{"id": "O_1", "email": None}, {"id": "O_2", "email": None}],
        )
        finalize_dataset(tmp_path, "organization")
        table = pq.read_table(tmp_path / "organization.parquet")
        assert table.column("email").null_count == 2

    def test_nested_fields_are_json_encoded_ac_8_3(self, tmp_path: Path):
        _write_ndjson(
            tmp_path / "issues.ndjson",
            [
                {
                    "id": "I_1",
                    "repository_id": "R_1",
                    "author": {"login": "alice"},
                    "labels": {"nodes": [{"name": "bug"}]},
                }
            ],
        )
        outcome = finalize_dataset(tmp_path, "issues")
        assert outcome.gaps == ()
        table = pq.read_table(tmp_path / "issues.parquet")
        author_value = table.column("author")[0].as_py()
        assert json.loads(author_value) == {"login": "alice"}

    def test_nested_field_left_null_stays_null_not_the_string_null(self, tmp_path: Path):
        _write_ndjson(tmp_path / "issues.ndjson", [{"id": "I_1", "author": None}])
        finalize_dataset(tmp_path, "issues")
        table = pq.read_table(tmp_path / "issues.parquet")
        assert table.column("author")[0].as_py() is None

    def test_conversion_failure_is_a_gap_and_leaves_ndjson_untouched_ac_8_4(self, tmp_path: Path):
        ndjson_path = tmp_path / "organization.ndjson"
        # A malformed line breaks JSON parsing, forcing a conversion failure.
        ndjson_path.write_text('{"id": "O_1"\n', encoding="utf-8")
        outcome = finalize_dataset(tmp_path, "organization")
        assert len(outcome.gaps) == 1
        assert "conversion failed" in outcome.gaps[0].reason.lower()
        assert not (tmp_path / "organization.parquet").exists()
        assert ndjson_path.exists()  # never discarded

    def test_unknown_dataset_is_a_gap_not_a_crash(self, tmp_path: Path):
        _write_ndjson(tmp_path / "not_a_real_dataset.ndjson", [{"id": "1"}])
        outcome = finalize_dataset(tmp_path, "not_a_real_dataset")
        assert len(outcome.gaps) == 1

    def test_removes_ndjson_by_default(self, tmp_path: Path):
        ndjson_path = tmp_path / "organization.ndjson"
        _write_ndjson(ndjson_path, [{"id": "O_1"}])
        finalize_dataset(tmp_path, "organization")
        assert not ndjson_path.exists()

    def test_keeps_ndjson_when_requested_ac_8_9(self, tmp_path: Path):
        ndjson_path = tmp_path / "organization.ndjson"
        _write_ndjson(ndjson_path, [{"id": "O_1"}])
        finalize_dataset(tmp_path, "organization", keep_ndjson=True)
        assert ndjson_path.exists()


class TestFinalizeSnapshot:
    def test_finalizes_every_dataset_with_an_ndjson_file_present(self, tmp_path: Path):
        _write_ndjson(tmp_path / "organization.ndjson", [{"id": "O_1"}])
        _write_ndjson(tmp_path / "issues.ndjson", [{"id": "I_1"}])
        result = finalize_snapshot(tmp_path)
        assert {o.name for o in result.dataset_outcomes} == {"organization", "issues"}
        assert not result.has_gaps
        assert (tmp_path / "organization.parquet").exists()
        assert (tmp_path / "issues.parquet").exists()

    def test_is_re_runnable_without_redownloading_ac_8_5(self, tmp_path: Path):
        _write_ndjson(tmp_path / "organization.ndjson", [{"id": "O_1", "login": "acme"}])
        finalize_snapshot(tmp_path, keep_ndjson=True)
        first_table = pq.read_table(tmp_path / "organization.parquet")

        # Re-running finalization again, with no new download, reproduces
        # the same Parquet output from the same still-present NDJSON.
        result = finalize_snapshot(tmp_path, keep_ndjson=True)
        second_table = pq.read_table(tmp_path / "organization.parquet")
        assert first_table.equals(second_table)
        assert not result.has_gaps

    def test_a_conversion_failure_in_one_dataset_does_not_block_others(self, tmp_path: Path):
        (tmp_path / "organization.ndjson").write_text("{broken", encoding="utf-8")
        _write_ndjson(tmp_path / "issues.ndjson", [{"id": "I_1"}])
        result = finalize_snapshot(tmp_path)
        by_name = {o.name: o for o in result.dataset_outcomes}
        assert by_name["organization"].gaps
        assert not by_name["issues"].gaps
        assert (tmp_path / "issues.parquet").exists()

    def test_empty_snapshot_directory_is_a_valid_empty_result(self, tmp_path: Path):
        result = finalize_snapshot(tmp_path)
        assert result.dataset_outcomes == ()
        assert not result.has_gaps
