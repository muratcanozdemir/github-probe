"""Converts a snapshot's per-dataset NDJSON files to Parquet (AC-8.2),
using each dataset's declared field list from the registry so the schema
never depends on which fields happened to be present in this particular
run (AC-8.3) — see `parquet_schema.py` for the field-name-to-type table
this relies on.

Standalone and re-runnable (AC-8.5): reads whatever `*.ndjson` files exist
in a snapshot directory, converts each to `<dataset>.parquet`, and removes
the source NDJSON unless `keep_ndjson` is set (AC-8.9). Never re-fetches
anything — it only reads files a harvest phase already wrote. A conversion
failure for one dataset is recorded as a gap and never touches that
dataset's (or any other dataset's) already-downloaded NDJSON (AC-8.4):
the source file is only ever removed *after* its Parquet file has been
written successfully.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from org_harvest.datasets import get
from org_harvest.gaps import DatasetOutcome, Gap
from org_harvest.parquet_schema import NESTED_FIELDS, dataset_schema


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _column_values(records: list[dict[str, Any]], field: str) -> list[Any]:
    values = []
    for record in records:
        value = record.get(field)
        if field in NESTED_FIELDS or isinstance(value, dict | list):
            value = json.dumps(value, sort_keys=True) if value is not None else None
        values.append(value)
    return values


def finalize_dataset(
    snapshot_dir: Path, dataset: str, *, keep_ndjson: bool = False
) -> DatasetOutcome:
    """Converts one dataset's NDJSON to Parquet. Never raises — a
    conversion failure becomes a single-element `gaps` tuple on the
    returned outcome (AC-8.4), with the source NDJSON left untouched."""
    ndjson_path = snapshot_dir / f"{dataset}.ndjson"
    parquet_path = snapshot_dir / f"{dataset}.parquet"
    try:
        spec = get(dataset)
        if spec.fields is None:
            raise ValueError(f"dataset '{dataset}' has no declared field list to convert against")
        records = _read_ndjson(ndjson_path)
        schema = dataset_schema(spec.fields)
        arrays = [
            pa.array(_column_values(records, name), type=schema.field(name).type)
            for name in spec.fields
        ]
        table = pa.Table.from_arrays(arrays, schema=schema)
        pq.write_table(table, parquet_path)
        if not keep_ndjson and ndjson_path.exists():
            ndjson_path.unlink()
        return DatasetOutcome(dataset, len(records), ())
    except Exception as exc:  # any conversion failure becomes a gap, not a crash (AC-8.4)
        gap = Gap.now(
            dataset,
            resource_id=None,
            field_path=None,
            reason=f"Parquet conversion failed: {exc}",
        )
        return DatasetOutcome(dataset, 0, (gap,))


class FinalizeResult:
    def __init__(self, dataset_outcomes: tuple[DatasetOutcome, ...]) -> None:
        self.dataset_outcomes = dataset_outcomes

    @property
    def gaps(self) -> tuple[Gap, ...]:
        return tuple(g for outcome in self.dataset_outcomes for g in outcome.gaps)

    @property
    def has_gaps(self) -> bool:
        return any(outcome.gaps for outcome in self.dataset_outcomes)


def finalize_snapshot(snapshot_dir: Path, *, keep_ndjson: bool = False) -> FinalizeResult:
    """Finalizes every dataset with an NDJSON file present in
    `snapshot_dir` (AC-8.5) — whichever subset a harvest run actually
    produced, not a hardcoded list of all 26 default-tier datasets, so this
    works unchanged once Story 11's dataset selection can produce a
    narrower snapshot."""
    dataset_names = sorted(p.stem for p in snapshot_dir.glob("*.ndjson"))
    outcomes = tuple(
        finalize_dataset(snapshot_dir, name, keep_ndjson=keep_ndjson) for name in dataset_names
    )
    return FinalizeResult(outcomes)
