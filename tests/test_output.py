from __future__ import annotations

import json
from pathlib import Path

import pytest

from org_harvest.output import NdjsonWriter, count_records, read_ndjson_tolerant


class TestNdjsonWriter:
    def test_writes_one_json_line_per_record_ac_8_1(self, tmp_path: Path):
        path = tmp_path / "issues.ndjson"
        with NdjsonWriter(path) as writer:
            writer.write_record({"id": "1", "title": "a"})
            writer.write_record({"id": "2", "title": "b"})
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"id": "1", "title": "a"}
        assert json.loads(lines[1]) == {"id": "2", "title": "b"}

    def test_creates_parent_directories(self, tmp_path: Path):
        path = tmp_path / "nested" / "dir" / "issues.ndjson"
        with NdjsonWriter(path) as writer:
            writer.write_record({"id": "1"})
        assert path.exists()

    def test_appends_to_an_existing_file_rather_than_overwriting(self, tmp_path: Path):
        path = tmp_path / "issues.ndjson"
        with NdjsonWriter(path) as writer:
            writer.write_record({"id": "1"})
        with NdjsonWriter(path) as writer:
            writer.write_record({"id": "2"})
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2

    def test_preserves_non_ascii_field_values_verbatim(self, tmp_path: Path):
        path = tmp_path / "members.ndjson"
        with NdjsonWriter(path) as writer:
            writer.write_record({"id": "1", "name": "José"})
        line = path.read_text(encoding="utf-8").splitlines()[0]
        assert json.loads(line)["name"] == "José"


class TestReadNdjsonTolerant:
    def test_missing_file_is_an_empty_list(self, tmp_path: Path):
        assert read_ndjson_tolerant(tmp_path / "nope.ndjson") == []

    def test_reads_every_complete_record(self, tmp_path: Path):
        path = tmp_path / "issues.ndjson"
        path.write_text('{"id": "1"}\n{"id": "2"}\n', encoding="utf-8")
        assert read_ndjson_tolerant(path) == [{"id": "1"}, {"id": "2"}]

    def test_discards_a_truncated_trailing_line_ac_4_6(self, tmp_path: Path):
        path = tmp_path / "issues.ndjson"
        path.write_text('{"id": "1"}\n{"id": "2", "titl', encoding="utf-8")
        assert read_ndjson_tolerant(path) == [{"id": "1"}]

    def test_a_malformed_non_trailing_line_still_raises(self, tmp_path: Path):
        path = tmp_path / "issues.ndjson"
        path.write_text('{not valid}\n{"id": "2"}\n', encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            read_ndjson_tolerant(path)


class TestCountRecords:
    def test_missing_file_is_zero(self, tmp_path: Path):
        assert count_records(tmp_path / "nope.ndjson") == 0

    def test_counts_complete_records_only(self, tmp_path: Path):
        path = tmp_path / "issues.ndjson"
        path.write_text('{"id": "1"}\n{"id": "2"}\n{"id": "3", "trunc', encoding="utf-8")
        assert count_records(path) == 2
