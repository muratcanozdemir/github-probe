"""Writes fetched records to disk as newline-delimited UTF-8 JSON, one file
per dataset (AC-8.1), durably enough that an abrupt kill loses at most the
record in flight (AC-4.6, AC-8.10).

Each write is flushed and fsynced immediately. This is stronger than the
spec's page-granularity durability requirement (AC-4.1 checkpoints at page
granularity, not record granularity) but simpler to reason about and cheap
at the record counts a single organization produces; it is not a tuning
knob today. `NdjsonWriter` never buffers a record across calls: `close()`
leaves the file exactly as durable as the last `write_record()` did.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class NdjsonWriter:
    """Appends JSON-encoded records to `path`, one per line. Opens in
    append mode so a resumed run (Story 12) can continue an existing file
    without re-reading it here — this class only ever writes forward."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")

    def write_record(self, record: Mapping[str, Any]) -> None:
        self._file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        self._file.write("\n")
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> NdjsonWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
