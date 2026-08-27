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
