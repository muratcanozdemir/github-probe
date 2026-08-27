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
- Fetching all fourteen repository-level default-tier datasets, completing each dataset's registry entry from Story 4's skeleton, fanning out over every repository from Story 5's repository list.
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
