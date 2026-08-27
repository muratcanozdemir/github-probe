# Story 6: Fetch repository-level datasets across the organization

**Status:** APPROVED
**Depends On:** 5
**UI Changes:** No

## User Story

As an engineer auditing a GitHub organization, I can download the default repository-level datasets — issues, pull requests, discussions, releases, labels, milestones, and the repository's access-control and configuration data — for every repository in the org, so that one bad or oversized repository never derails the rest of the download.

## Acceptance Criteria

- AC-1.2 (repository-level slice): The snapshot includes every repository-level dataset in the default tier — `issues`, `pull_requests`, `discussions`, `releases`, `labels`, `milestones`, `collaborators`, `branch_protection_rules`, `repo_rulesets`, `repo_custom_property_values`, `environments`, `deployments`, `vulnerability_alerts`, `topics`, `languages` — for every repository in the org (subject to Story 5's repository list and any scope restriction it recorded).
- AC-5.1: When the API returns partial data alongside errors, the successful portion is written and each failure is recorded as a structured gap.
- AC-5.2: Each gap records dataset, resource identifier, the field path when the API supplies one, reason, and UTC timestamp.
- AC-5.7: A permanently inaccessible repository does not prevent the rest of the org from being downloaded.
- AC-7.8: Page sizes stay within API limits, and a query that exceeds the node ceiling is retried at a smaller page size before being recorded as a gap.
- AC-8.1: During the run, records are appended as newline-delimited UTF-8 JSON, one file per dataset.
- AC-8.6: Every record carries a stable identifier, and every child record carries the identifier of its parent.

## Scope

**Included:**
- Fetching all fifteen repository-level default-tier datasets, completing each dataset's registry entry from Story 4's skeleton, fanning out over every repository from Story 5's repository list.
- Issues, pull requests, and discussions are collected as records in their own right (title, state, author, timestamps, labels, assignees, milestone) without their comment, review, reaction, or timeline sub-collections.
- Batching multiple repositories per GraphQL request via aliases, with per-repository cursor state within a batch and a tunable batch width (architecture.md, Decision 2).
- Attributing a partial failure to the specific repository that produced it, using the GraphQL error's path, rather than failing the whole batch.
- A permanently inaccessible repository is recorded as a gap and does not block any other repository's data.
- Node-limit handling that can reduce both page size and alias-batch width, since an oversized batched query can fail before any alias executes (architecture.md, Decision 2).
- Every repository-level record carries its repository's identifier as its parent key.
- Archived, disabled, empty, and forked repositories are included by default (filtering them out is Story 11).

**Excluded:**
- Conversation threads (issue/PR comments, reviews, reactions, timeline) and commit history — out of scope for the whole feature per spec.md.
- Optional-tier datasets (workflow runs, forks, stargazers, packages, etc.) and audit log — Story 11 makes these selectable, but this story does not fetch them.
- Repository-subset filtering, archived/fork exclusion, and per-collection item caps (Story 11) — this story fetches every default repository-level dataset for every repository unconditionally.
- Systemic-failure detection when an outage causes widespread failure across many repositories (Story 7) — this story handles one bad repository among many good ones, not a global outage.
- Parquet conversion and manifest writing (Stories 8 and 9).

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 5's acceptance criteria are met: the organization's repository list is fetched, persisted, and (if applicable) marked with a scope restriction.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/harvest/flatten.py` — new: `snake_case()`/`flatten_node()`, extracted out of Story 5's `org_level.py` (DEV-4) since Phase 2 needs the identical flattening rule.
2. `src/org_harvest/gaps.py` — modify: add a shared `DatasetOutcome` dataclass (DEV-5), used by both phases instead of two structurally-identical copies.
3. `src/org_harvest/harvest/repo_level.py` — new: the Phase 2 engine. A `_RepoConnectionSpec` table (fifteen entries) drives one generic alias-batching paginator (`_RepoLevelHarvester.fetch_repo_dataset` / `_run_batch`), fanning out over every repository Story 5 wrote to `repositories.ndjson`. One dataset is fetched at a time; each round trip aliases up to `batch_width` still-pending repositories under root `repository(owner, name)` fields (architecture.md Decision 2). Partial GraphQL errors are attributed to a specific repository via `errors[].path[0]` → alias → repository mapping; a transport-level failure invalidates the whole batch (nothing in it executed). Node-limit handling (`_is_node_limit_error`) first halves page size, then — once page size bottoms out at 1 and the batch still has more than one repository — splits the batch and retries each half at the original page size, recursing until either something succeeds or a width-one, page-size-one batch still fails (recorded as an unrecoverable gap). `repo_custom_property_values` is modeled as `paginated=False` — Repository's `customPropertyValues` field is a plain list, not a connection, so its query omits `first`/`after`/`pageInfo` entirely.
4. `src/org_harvest/__init__.py` — modify: re-export `RepoLevelResult`, `fetch_repository_datasets`; `DatasetOutcome` now points at the shared `gaps.DatasetOutcome` (DEV-5).
5. `features/org-harvest/stories/story-6-repository-datasets.md` — fix a pre-existing "fourteen" → "fifteen" count typo in the Scope section (FR-1 always specified fifteen repository-level default-tier datasets; the story text undercounted them by one).

### Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `src/org_harvest/harvest/flatten.py` | Create | Shared node-flattening logic (DEV-4) |
| `src/org_harvest/gaps.py` | Modify | Shared `DatasetOutcome` (DEV-5) |
| `src/org_harvest/harvest/org_level.py` | Modify | Use shared `flatten_node`/`DatasetOutcome` (DEV-4, DEV-5) |
| `src/org_harvest/harvest/repo_level.py` | Create | Phase 2 alias-batched fetch engine |
| `src/org_harvest/__init__.py` | Modify | Re-export new public types |
| `tests/test_repo_level.py` | Create | Happy path, EC-1, partial/total failure attribution, node-limit retry ladder |

### Cross-Module Seams

- **`repositories.ndjson`, written by Story 5's `fetch_organization_directory`, is read by `repo_level._read_repositories()`.** This is the Decision 4 phase boundary made concrete: Phase 2 has no fan-out target without Phase 1's output. Confirmed by every test in `tests/test_repo_level.py` — each writes `repositories.ndjson` via the same `_write_repositories()` helper before calling `fetch_repository_datasets`, and `TestNoRepositories::test_empty_repository_list_is_a_valid_empty_result_ec_1` confirms the read path tolerates a missing file entirely.
- **`registry.fields`, written here via `complete_fetch_details()`, will be read by Story 8's Parquet schema derivation** — same unconfirmed-until-Story-8 seam already flagged in Story 5's plan, now also true for these fifteen datasets.

### Testing Approach

- **Integration — `tests/test_repo_level.py`:** a happy-path test (a generic respx handler that identifies which of the fifteen `_RepoConnectionSpec`s a query is for by matching its connection field, then returns one canned node per aliased repository) asserts all fifteen datasets are fetched, one record per repository each, no gaps, and checkpoint marks every dataset complete; targeted tests confirm `repository_id` parent keys (AC-8.6), edge-derived fields (`collaborators.permission`, `languages.size`), and synthesized identity for the two node types with no native GraphQL id (`repo_custom_property_values`, `languages`); `TestNoRepositories` proves an empty repository list produces a valid zero-count result with **no GraphQL requests at all** (an un-mocked respx router would raise `AllMockedAssertionError` if the code tried); `TestPartialFailureAttribution` proves a GraphQL error on one aliased repository in a batch is attributed to that repository specifically via `path[0]` while its batch-mate's data is still written (AC-5.7, Decision 2's core claim, actually exercised rather than assumed) and that an exhausted-retries transport failure gaps every repository in the invalidated batch; `TestNodeLimitRetry` drives `_run_batch` directly to prove all three rungs of the node-limit ladder (AC-7.8): page-size reduction succeeding on its own, page-size-exhausted-so-batch-split succeeding, and a width-one/page-size-one batch that still fails becoming a genuine gap rather than looping forever.

### Risks

- **Field lists are a representative subset**, same caveat as Story 5 — nested `labels`/`assignees` on issues and pull requests are capped at their first 20 via `first: 20` with no further pagination of that sub-collection, which is a pragmatic bound (an issue or PR with more than 20 labels or assignees is rare) documented here rather than silently accepted.
- **The real-world GraphQL field names for `TeamMemberEdge`-style edge data on `collaborators` (`permission`) and `languages` (`size`) are asserted from schema knowledge, not verified against a live installation** — same caveat as Story 5's team-edge fields, and the same failure mode applies (a "Cannot query field" tool-level fault, not silent wrong data) if wrong.
- **The node-limit error detection (`_is_node_limit_error`) is best-effort pattern matching**, since GitHub does not document one single, stable error shape for this condition — documented in the function's own docstring, matching the pattern already established for the permission-name mapping in `catalog.py`.

### Decisions Made

- **One dataset per batched request, not multiple datasets combined per repository alias.** Combining datasets would require independent pagination state for multiple nested connections within one alias, multiplying the per-repo cursor bookkeeping and the node-limit retry surface for no clear benefit — architecture.md's "per-repo cursor state within a batch" language is satisfied by tracking one connection's cursor per repo, which this design does cleanly.
- **`repo_custom_property_values` is fetched via the same generic batching machinery as every paginated dataset, parameterized by `paginated=False`**, rather than a separate code path — keeping exactly one fetch engine per architecture.md Decision 1's spirit, even for the one dataset whose GraphQL shape isn't actually a connection.
- See DEV-4 and DEV-5 in `stories/deviations.md` for the `flatten.py` and `DatasetOutcome` consolidations.
