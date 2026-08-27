# Story 5: Download the organization's directory

**Status:** APPROVED
**Depends On:** 4
**UI Changes:** No

## User Story

As an engineer auditing a GitHub organization, I can download that organization's settings, membership, teams, and repository list so that I have the org-level picture on disk before any per-repository data is fetched.

## Acceptance Criteria

- AC-1.2 (org-level slice): The snapshot includes every organization-level dataset in the default tier — `organization`, `members`, `pending_members`, `teams`, `team_members`, `team_repositories`, `repositories`, `org_rulesets`, `org_custom_properties`, `org_domains`, `org_ip_allow_list`.
- AC-4.1: Progress is checkpointed continuously at page granularity, including the cursor position within each collection.
- AC-5.1: When the API returns partial data alongside errors, the successful portion is written and each failure is recorded as a structured gap.
- AC-5.2: Each gap records dataset, resource identifier, the field path when the API supplies one, reason, and UTC timestamp.
- AC-5.8: When the installation is scoped to selected repositories rather than all, the tool records the restriction in the manifest, marks the snapshot as scope-restricted, and reports how many of the organization's repositories it can reach.
- AC-8.1: During the run, records are appended as newline-delimited UTF-8 JSON, one file per dataset.
- AC-8.6: Every record carries a stable identifier, and every child record carries the identifier of its parent.

## Scope

**Included:**
- Fetching all eleven organization-level default-tier datasets, completing each dataset's registry entry (field list, GraphQL fragment, parent-key, Parquet schema) from Story 4's skeleton.
- Cursor-based pagination per collection, with per-collection checkpoint cursors persisted continuously.
- Writing fetched records as NDJSON, one file per dataset, each carrying a stable identifier (`team_members` and `team_repositories` additionally carrying their team's identifier as a parent key).
- Recording a gap when part of an org-level response fails, without aborting the rest of the org-level fetch.
- Detecting and recording a repository-scoped installation (EC-3) at the point the `repositories` dataset is fetched, since that's where the restriction first becomes observable.

**Excluded:**
- Any repository-level dataset — issues, pull requests, and everything nested under a repository (Story 6).
- Parquet conversion, manifest writing, and root-index maintenance (Stories 8 and 9) — this story only produces NDJSON and checkpoint state.
- Resuming an interrupted org-level fetch (Story 12) — this story establishes the checkpoint mechanics that resume later builds on, but doesn't implement resume itself.
- Dataset selection/narrowing (Story 11) — this story fetches the full org-level default tier unconditionally.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 4's acceptance criteria are met: the dataset registry exists with metadata for all 37 datasets, and preflight can report readiness against it.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/timeutil.py` — new: `utc_now_iso()` and `utc_now_compact()`, so every module that stamps a gap, a checkpoint write, or (later) a snapshot directory name agrees on the exact same UTC format (FR-8).
2. `src/org_harvest/graphql.py` — new: `extract_rate_limit_snapshot()`, pulled out of preflight's private copy (DEV-3) since the org-level fetch engine needs the identical extraction.
3. `src/org_harvest/gaps.py` — new: the frozen `Gap` dataclass (dataset, resource_id, field_path, reason, occurred_at) implementing AC-5.2's exact field list, with a `Gap.now(...)` convenience constructor and `to_dict()` for JSON serialization.
4. `src/org_harvest/output.py` — new: `NdjsonWriter`, appending one flushed-and-fsynced JSON line per record (AC-8.1), open-in-append-mode so a later resumed run (Story 12) can continue an existing file without this class ever needing to read it back.
5. `src/org_harvest/checkpoint.py` — new: `CheckpointState` (schema version, tool version, org, dataset selection, repository filter, per-dataset status, per-collection cursors, gaps) and `CheckpointStore`, which creates fresh state, mutates it, and persists atomically (write-to-temp-then-`os.replace`) on every mutation (AC-4.1). Gaps are stored here rather than invented a second home, since manifest.json doesn't exist until Story 9 and gaps must be discoverable from the snapshot alone the moment they occur (AC-5.6) — Story 9 is expected to fold this list into the manifest at finalization time.
6. `src/org_harvest/harvest/org_level.py` — new: the Phase 1 engine. A `_ConnectionSpec` table drives one generic paginator (`_fetch_org_connection`) for the eight direct organization-level connections; `organization` itself is a singleton scalar fetch; `team_members`/`team_repositories` paginate per-team via a second generic function (`_fetch_team_connection`), sequentially rather than alias-batched (architecture.md Decision 2 scopes alias-batching to repository-level Phase 2 specifically — documented as a deliberate, not accidental, scope boundary). `register_fetch_details()` completes all eleven datasets' registry entries via `complete_fetch_details()` (closing out Story 4's placeholder). `fetch_organization_directory()` orchestrates all eleven fetches, returning an `OrgLevelResult` with per-dataset outcomes, accumulated gaps, and EC-3's scope-restriction detection (from `credentials.repository_selection`) plus how many repositories were actually reached (AC-5.8).
7. `src/org_harvest/preflight.py` — modify: use the new shared `extract_rate_limit_snapshot` (DEV-3).
8. `src/org_harvest/__init__.py` — re-export the new public types (`Gap`, `CheckpointState`, `CheckpointStore`, `OrgLevelResult`, `DatasetOutcome`, `fetch_organization_directory`).

### Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `src/org_harvest/timeutil.py` | Create | Shared UTC timestamp helpers |
| `src/org_harvest/graphql.py` | Create | Shared rate-limit-snapshot extraction (DEV-3) |
| `src/org_harvest/gaps.py` | Create | The `Gap` record (AC-5.2) |
| `src/org_harvest/output.py` | Create | `NdjsonWriter` (AC-8.1) |
| `src/org_harvest/checkpoint.py` | Create | Checkpoint state + durable store (AC-4.1, Decision 5) |
| `src/org_harvest/harvest/__init__.py` | Create | Fetch-engine package |
| `src/org_harvest/harvest/org_level.py` | Create | Phase 1 fetch engine (AC-1.2, AC-5.1, AC-5.3, AC-5.8, AC-8.6) |
| `src/org_harvest/preflight.py` | Modify | Use shared `extract_rate_limit_snapshot` (DEV-3) |
| `src/org_harvest/__init__.py` | Modify | Re-export new public types |
| `tests/test_gaps.py` | Create | `Gap` construction and serialization |
| `tests/test_output.py` | Create | `NdjsonWriter` durability and append behavior |
| `tests/test_checkpoint.py` | Create | Checkpoint create/mutate/round-trip/durability |
| `tests/test_graphql.py` | Create | Shared rate-limit extraction |
| `tests/test_org_level.py` | Create | Full-directory happy path, pagination, partial/total failure, scope restriction, record identity |

### Cross-Module Seams

- **Checkpoint is written by `org_level.py`, read by `CheckpointStore.load` in tests today and by Story 12/13's resume logic later.** Confirmed: `TestCheckpointDurability::test_each_mutation_is_immediately_persisted` and `TestHappyPath::test_checkpoint_marks_every_dataset_complete` both write via one code path and read back via a fresh `CheckpointStore.load` call, proving the on-disk shape round-trips correctly — the same read path Story 12 will reuse.
- **`teams.ndjson`, written by `fetch_org_connection`, is read back off disk by `fetch_organization_directory` itself** (`_read_ndjson`) to drive `team_members`/`team_repositories`'s per-team loop — a same-story, write-then-read seam across what will become Story 12's resume boundary (a resumed run reconstructing the team list from a previous run's file rather than an in-memory list is exactly this same read path). Confirmed by `TestHappyPath::test_team_members_and_repositories_carry_team_id_parent_key`.
- **`registry.fields`, written here via `complete_fetch_details()`, will be read by Story 8's Parquet schema derivation.** Unconfirmed today (Story 8 doesn't exist yet) — flagged for Story 8 to verify the field-name lists declared here actually match every key `_flatten_node` produces.

### Testing Approach

- **Unit — `tests/test_gaps.py`:** `Gap.now()` stamps every AC-5.2 field including a real UTC timestamp; `to_dict()` round-trips exactly.
- **Unit — `tests/test_output.py`:** one JSON line per record; parent directories created on demand; a second `NdjsonWriter` on the same path appends rather than truncates (the property Story 12's resume depends on); non-ASCII values are preserved verbatim rather than escaped.
- **Unit — `tests/test_checkpoint.py`:** `create()` writes immediately and records org/selection/tool-version; a full round trip through `set_cursor`/`set_dataset_status`/`record_gap` survives a fresh `load()`; `repository_filter=None` round-trips as `None` (not an empty tuple); no `.tmp` file is left behind after a save (the atomic-write mechanism); each mutation is durable without an explicit final `save()` call.
- **Unit — `tests/test_graphql.py`:** the shared extractor reads a well-formed rate-limit block and returns `None` for both a malformed JSON body and a non-JSON body.
- **Integration — `tests/test_org_level.py`:** a full happy-path run (respx, dispatching canned responses by which GraphQL connection field appears in the query text) asserts all eleven datasets are fetched, one NDJSON file per dataset exists, records carry stable identifiers including the synthesized `id` for `org_custom_properties` (whose GraphQL type has no node id) and the `team_id` parent key on `team_members`/`team_repositories` (AC-8.6), checkpoint marks every dataset complete, `reachable_repository_count` reflects records actually written, and `scope_restricted` reflects `credentials.repository_selection` in both directions (EC-3); a dedicated pagination test drives `_fetch_org_connection` directly across two pages, asserting the cursor variable sent on page two, the checkpoint's persisted cursor, and both records landing in file order; partial-failure tests cover a GraphQL-level error (data null, errors populated) becoming a gap with the correct `field_path` while every other dataset still completes (AC-5.1, AC-5.5) and a transport-level exhausted-retries failure becoming a gap with `field_path=None` (AC-5.3); a 401 mid-run is asserted to propagate as `OrgHarvestError(kind=AUTH_EXPIRED)` rather than being swallowed as a gap, proving the distinction between a per-resource condition and an authentication failure is actually enforced, not just documented.

### Risks

- **The exact real-world GraphQL edge shape for `TeamMemberEdge.role` and `TeamRepositoryEdge.permission` is asserted from schema knowledge (exploration.md), not verified against a live installation** — if GitHub's actual field names differ, this surfaces as a preflight-passes-but-harvest-gaps situation (a GraphQL "Cannot query field" error becomes a tool-level fault per FR-5, not a silent wrong answer), not silent data corruption. Flagged here rather than assumed correct.
- **Field lists are a representative subset, not FR-1's literal "every non-connection scalar field."** Documented in `org_level.py`'s module docstring; expanding any dataset's field list later is a one-place change to its `_ConnectionSpec.node_selection`/`record_fields`, with no drift risk against the Parquet schema Story 8 will derive from the same `record_fields`.

### Decisions Made

- **Checkpoint, not a yet-nonexistent manifest, is where gaps live until Story 9.** See the module docstring in `checkpoint.py` — this is a forward-looking design choice logged here so Story 9 knows to read gaps from checkpoint state when building `manifest.json`, rather than assuming gaps arrive some other way.
- **`team_members`/`team_repositories` are fetched sequentially per team, not alias-batched.** Architecture.md's Decision 2 explicitly scopes alias-batching to repository-level Phase 2 requests; extending it to team-level Phase 1 requests was never required and would add the same per-alias failure-attribution complexity Decision 2 accepts for repositories, for a collection (an org's team count) that is reliably small. Documented rather than silently assumed.
- **`reachable_repository_count` is simply how many `repositories` records were successfully written**, not compared against a separately-fetched "true org total." GraphQL's `organization.repositories` connection is itself scoped by the installation's own permissions, so there is no API call this tool can make that bypasses that scoping to learn a true, scope-independent total — the count this tool can actually observe is the only number AC-5.8 asks it to report.
- See DEV-3 in `stories/deviations.md` for the `graphql.py` extraction out of Story 4's `preflight.py`.
