from __future__ import annotations

import json
from pathlib import Path

from org_harvest.output import NdjsonWriter


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
