# Story 16: Document how to set up and operate org-harvest

**Status:** APPROVED
**Depends On:** 13, 14, 15
**UI Changes:** No

## User Story

As an operator setting this tool up for the first time, I can read a README that walks me through creating the GitHub App, choosing credentials, selecting datasets, and understanding a snapshot's output, so that I don't have to reverse-engineer the tool's behavior from its source.

## Acceptance Criteria

- FR-13 (documentation): The README covers creating and installing the GitHub App; the permissions each dataset requires; both credential forms and the environment variables for CI; dataset tiers and selection; resume, retry-gaps, and force-fresh; the snapshot layout, manifest format, and gap representation; the exit-status table; configuring a non-github.com host; and the caveat that resuming across CI jobs requires the snapshot directory to be cached or restored between them.

## Scope

**Included:**
- Step-by-step instructions for creating and installing a GitHub App suitable for this tool, and the permissions each dataset requires (traceable to the registry from Stories 4–6, 11).
- Documentation of both credential forms (Story 1) and the CI environment-variable path.
- Documentation of dataset tiers and the selection mechanism (Story 11).
- Documentation of resume, force-fresh, and retry-gaps (Stories 12, 13, 14), including the explicit caveat that resuming across separate CI jobs requires the snapshot directory to be cached or restored between them.
- Documentation of the snapshot directory layout, manifest format, and how gaps are represented (Stories 5–9).
- The full exit-status table (Story 10).
- How to configure a non-github.com API host (Story 1).

**Excluded:**
- Any behavior change to the tool itself — this story is documentation only.
- CI workflow and release automation setup (Story 17) — this story documents how to *use* the tool, not how the project's own repository is built and released.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 13's acceptance criteria are met: resume and concurrent-run guards are final.
- Story 14's acceptance criteria are met: retry-gaps is final.
- Story 15's acceptance criteria are met: the library API is final.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.

---

## Implementation Plan

### Implementation Steps

1. Verify the CLI's actual behavior against the source rather than trusting memory: read `cli.py` in full (all commands, options, help text, env var names), `datasets/registry.py`/`catalog.py` (tier/level/permission metadata for all 37 datasets), `hosts.py` (the three `--api-host` shapes), `manifest.py`/`gaps.py` (on-disk JSON shapes), `finalize.py`/`lock.py` (file/directory naming), `run.py` (the `ExitStatus` enum, verbatim), and `preflight.py` (verdict meanings) — every claim in the README traces to one of these.
2. Cross-check every dataset name and permission mapping against the actual registry by running `uv run org-harvest datasets list` and `uv run org-harvest run --help` / `retry-gaps --help` / `preflight --help` against the real CLI, rather than transcribing from the source read alone — catches any drift between a docstring and the option actually wired up.
3. Write `README.md`, replacing the Story-1-era placeholder, structured as: installation; creating and installing the GitHub App (with a permission-to-dataset table covering all 18 distinct permission names used across the registry); both credential forms and the CI environment-variable table; a quick start; dataset tiers and selection (with the explicit caveat that `--datasets` replaces rather than extends the default tier — confirmed by reading `resolve_dataset_selection()`, which has no "default tier plus this" shorthand); repository restriction flags; preflight; resume/force-fresh/retry-gaps; `--verbose` progress output; the snapshot directory layout (with the exact per-file locations of `.run.lock`, `index.json`, `checkpoint.json`, `manifest.json`, and per-dataset NDJSON/Parquet files); the manifest JSON shape with a real example; gap JSON shape with a real example; the full exit-status table (transcribed verbatim from `ExitStatus`'s docstrings in `run.py`); configuring a non-github.com host; the CI-job-cache caveat for cross-job resume (FR-13's explicit requirement); and a short "using as a library" section demonstrating `run_snapshot()` (Story 15's dependency on this story).
4. Verify every command/option name, help text, and printed example against the live CLI (`uv run org-harvest ... --help`, `uv run org-harvest datasets list`) rather than the source alone, to catch help-text drift. Caught and fixed one real inaccuracy this way: an initial draft's `--datasets default,workflow_runs` example assumed a `"default"` keyword `resolve_dataset_selection()` doesn't have (see Decisions Made).
5. Verify the one embedded Python code sample compiles (`compile()`), and fix a type mismatch it initially had (see Decisions Made).
6. Run the full quality gate — this story touches no `src/` or `tests/` files, so this step exists to confirm the documentation change caused no regression (`ruff format` reformats embedded fenced code blocks inside Markdown, so `README.md` itself is subject to the gate).

### Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `README.md` | Modify (replace placeholder) | Full operator documentation per FR-13 |

### Cross-Module Seams

No cross-module seams identified — this story only adds a documentation file; it makes no code changes and defines no new read/write path between modules.

### Testing Approach

- No new automated tests — this is a documentation-only story with no new logic to unit-test.
- Verification performed instead: every documented command, option, and help string was checked against the actual CLI output (`--help` on `run`/`retry-gaps`/`preflight`, and `datasets list`'s real 37-line output) rather than trusting the source read alone; the one embedded Python snippet was verified to `compile()` successfully; the full existing quality gate (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest`) was re-run to confirm zero regressions from a documentation-only change (374 passed, unchanged from Story 15's count).

### Risks

- Any future story that renames a CLI flag, adds a dataset, or changes the exit-status table without updating `README.md` will silently drift out of sync — nothing in the quality gate catches documentation staleness automatically (there's no doctest-style verification of the README's command examples against the live CLI).

### Decisions Made

- **`--datasets` has no "default tier plus one more" shorthand, and the README says so explicitly** — verified by reading `resolve_dataset_selection()`, which only supports `None` (the full default tier) or an explicit, dependency-closed list; a first draft's `--datasets default,workflow_runs` example was wrong (`"default"` isn't a registered dataset name and would be rejected as unknown) and was replaced with an accurate example that spells out the full default-tier list alongside the optional addition.
- **The embedded library-usage snippet passes `Path("./snapshots")`, not a bare string, to `snapshot_root`** — `run_snapshot()`'s signature takes a `Path` with no internal coercion (confirmed by reading `run.py`, where `snapshot_root / org.lower()` would raise `TypeError` on a plain `str`); a first draft passed a string, which was corrected before publishing.
- **A full permission-to-dataset table is included rather than just pointing at `datasets list`** — an operator setting up the GitHub App for the first time needs to know what to grant *before* the tool is even installed/runnable, so the permission list has to stand on its own in the README rather than assuming the reader already has a working installation to query.
