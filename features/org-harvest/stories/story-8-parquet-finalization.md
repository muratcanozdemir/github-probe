# Story 8: Finalize a snapshot into Parquet with a stable schema

**Status:** APPROVED
**Depends On:** 7
**UI Changes:** No

## User Story

As a data analyst, I can load a finished snapshot as Parquet directly into my usual tools, with a schema that's identical whether the org is huge or nearly empty, so that I never have to write a parser for the raw download.

## Acceptance Criteria

- AC-8.2: On successful completion each NDJSON file is converted to Parquet using that dataset's declared schema.
- AC-8.3: The Parquet schema for a dataset is identical across runs and orgs, including when the dataset has zero records or a column is null throughout.
- AC-8.4: Conversion failure for one dataset discards no downloaded data and is recorded as a gap.
- AC-8.5: Finalization — conversion plus manifest write — is re-runnable on its own against a complete snapshot, without re-downloading.
- AC-8.9: The user can keep the intermediate NDJSON rather than having it removed after conversion.
- AC-8.10: An interrupted run leaves valid, readable NDJSON for everything completed so far.

## Scope

**Included:**
- Converting each dataset's NDJSON file to Parquet using that dataset's declared schema (from the registry established in Stories 4–6), so schema never depends on which fields happened to be present in a given run.
- Producing a correctly-schemed, loadable Parquet file even for a zero-record dataset or one where a column is null throughout.
- A conversion failure for one dataset is recorded as a gap and does not discard any already-downloaded data for that dataset or any other.
- Finalization is a standalone, re-runnable operation against an already-complete snapshot's NDJSON, without triggering any re-download.
- Retaining intermediate NDJSON on request instead of removing it after conversion.
- Ensuring an interrupted run (mid-fetch, before finalization) leaves whatever NDJSON it wrote in a valid, readable state.

**Excluded:**
- Writing the manifest itself and the root index (Story 9) — this story converts data and reports conversion outcomes; the manifest that records those outcomes for the whole snapshot is built next.
- Discarding a partially-written trailing record after an abrupt kill (already covered by Stories 5 and 6's checkpoint/write mechanics) — this story assumes the NDJSON it reads is already well-formed up to its last complete record.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 7's acceptance criteria are met: a run produces NDJSON for every dataset it attempts, records gaps for failures, and stops cleanly (without finalizing) on a systemic failure.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/parquet_schema.py` — new: `field_type()` maps a field name to a fixed PyArrow type (bool/int for the handful of genuinely non-string GraphQL values; every other field, including timestamps and every nested object/list, defaults to `pa.string()`); `dataset_schema()` builds a fully-nullable `pa.Schema` from a dataset's `fields` tuple. Nested fields are JSON-encoded rather than modeled as nested Arrow types — an explicit reading of FR-8's own fallback clause ("a value that cannot be represented shall be stored JSON-encoded"), chosen over hand-modeling exact nested struct shapes for every dataset, which would multiply the surface area prone to drifting from what the fetch engines actually write.
2. `src/org_harvest/finalize.py` — new: `finalize_dataset()` reads one dataset's NDJSON (if present — an empty/missing file is a valid zero-row case), builds the declared schema, JSON-encodes nested/nested-shaped values into the string columns `parquet_schema.py` declared for them, writes Parquet, and removes the source NDJSON unless `keep_ndjson=True`. Any exception during this — malformed JSON, an unknown dataset, a PyArrow write failure — is caught and turned into a one-element `gaps` tuple on the returned `DatasetOutcome`, and critically happens *before* the NDJSON-removal step is ever reached, so a failed conversion can never discard the source data (AC-8.4). `finalize_snapshot()` discovers which datasets to finalize by globbing `*.ndjson` in the snapshot directory — not a hardcoded list — so it degrades correctly once Story 11 can produce a narrower snapshot.
3. `src/org_harvest/__init__.py` — re-export `finalize_dataset`, `finalize_snapshot`, `FinalizeResult`.

### Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `src/org_harvest/parquet_schema.py` | Create | Field-name-to-PyArrow-type table and schema builder |
| `src/org_harvest/finalize.py` | Create | NDJSON→Parquet conversion, gap-on-failure, standalone/re-runnable |
| `src/org_harvest/__init__.py` | Modify | Re-export new public types |
| `tests/test_parquet_schema.py` | Create | Type mapping and schema-building behavior |
| `tests/test_finalize.py` | Create | Conversion, zero-row/all-null stability, failure isolation, re-runnability, keep-NDJSON |

### Cross-Module Seams

- **`registry.fields`, written by Story 5/6's `register_fetch_details()`, is read here by `finalize_dataset()` via `datasets.get(name).fields`.** This is the seam Story 5's plan flagged as "unconfirmed until Story 8" — now confirmed: every test in `tests/test_finalize.py` calls `register_org_fetch_details()`/`register_repo_fetch_details()` at module load (mirroring how a real run always calls a harvest phase, which populates these, before ever finalizing) and then exercises real dataset names (`organization`, `issues`) end to end through `finalize_dataset`, proving the field list Story 5/6 declared is exactly what this story converts against — not a separately-maintained list that could drift from it.
- **`*.ndjson`, written by Story 5/6's `NdjsonWriter`, is read by `finalize_snapshot()`'s glob.** Confirmed by `TestFinalizeSnapshot::test_finalizes_every_dataset_with_an_ndjson_file_present`, which writes files the same way the harvest engines do (one JSON line per record) rather than through any finalize-side helper.

### Testing Approach

- **Unit — `tests/test_parquet_schema.py`:** known bool/int field names map correctly; anything else (including a timestamp-shaped name) defaults to string; schema field order matches input order and every field is nullable; two calls with the same field list produce equal schemas (determinism, AC-8.3's structural precondition).
- **Unit — `tests/test_finalize.py`:** a populated dataset converts with correct values and types (`TestFinalizeDataset`); a zero-record file produces a valid, loadable Parquet file with the full schema still declared (AC-8.3); the *same* schema is read back whether the file was zero-row or populated, proven by comparing `pq.read_schema()` output across both cases directly rather than asserting each in isolation; an all-null column stays valid and correctly reports its null count; a nested field round-trips through JSON-encoding correctly, and a nested field that was `None` stays `None` rather than becoming the string `"null"`; a malformed NDJSON line becomes a gap with no Parquet file written and the source NDJSON left untouched (AC-8.4); an unknown dataset name is a gap, not a crash; the default behavior removes NDJSON after conversion, `keep_ndjson=True` retains it (AC-8.9). `TestFinalizeSnapshot` covers discovering datasets by glob, re-running finalization twice against retained NDJSON and getting byte-for-byte-equal Parquet both times (AC-8.5), one dataset's conversion failure not blocking another's, and an empty snapshot directory being a valid empty result rather than an error.

### Risks

- **Nested fields are JSON-encoded strings, not typed nested Arrow columns** — an analyst querying, say, `labels` will need to parse that column's JSON themselves rather than using Parquet's native nested-column support directly. This is the direct, spec-sanctioned trade-off of FR-8's own fallback clause, documented here (and to be documented again in Story 16's README) rather than silently chosen.
- **Every non-bool/int field is typed as a string, including numeric-looking fields this table doesn't know about** (e.g. a future field added to a dataset's `record_fields` without also being added to `_BOOL_FIELDS`/`_INT_FIELDS` here) — it will still convert successfully (as a string), just not as the "natural" PyArrow type. This fails safe (never a conversion crash) rather than silently, and is a one-place fix (`parquet_schema.py`) if a specific dataset's schema turns out to need a corrected type later.

### Decisions Made

- **`finalize_snapshot()` discovers datasets from the filesystem (`glob("*.ndjson")`), not from a hardcoded list of all 26 default-tier names.** This is what makes AC-8.5's "re-runnable... without re-downloading" true independent of which datasets a given run actually attempted — including, later, Story 11's narrower selections — without this story needing to know about selection at all.
- **A conversion failure is caught with a broad `except Exception`, not a narrower exception type.** Deliberate: this function's job is "never let one dataset's conversion problem crash the whole finalize pass or destroy source data," and the specific failure mode (a `json.JSONDecodeError`, a PyArrow `ArrowInvalid`, an `OSError` writing the file) is exactly the kind of detail that should end up in the gap's `reason` string, not dictate a different code path.
