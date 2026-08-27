# Story 11: Choose which datasets to download

**Status:** APPROVED
**Depends On:** 6
**UI Changes:** No

## User Story

As an engineer working with a very large organization, I can control which datasets and repositories are included in a run so that I get a useful result inside one rate-limit window instead of waiting hours for data I won't use.

## Acceptance Criteria

- AC-2.1: The user can restrict a run to a named subset of the default tier.
- AC-2.2: With no selection given, the run includes exactly the default tier.
- AC-2.3: Naming an optional (off-by-default) dataset in the selection enables it; the same mechanism both narrows within the default tier and opts into the optional tier.
- AC-2.4: Requesting an unknown dataset name fails immediately, before any network call, listing the valid names.
- AC-2.5: A selection that resolves to zero datasets is rejected before any network call.
- AC-2.6: Selecting a dataset that depends on another automatically includes the dependency, and the run reports that it did so.
- AC-2.8: The user can restrict a run to a subset of repositories, and can exclude archived repositories and forks.
- AC-2.9: The user can cap the number of items collected per repository-level collection.

## Scope

**Included:**
- Selection syntax that both narrows the default tier and opts into optional-tier datasets by the same mechanism.
- Validation of a selection before any network call: unknown names rejected with the valid-name list, and an empty resolved selection rejected.
- Automatic inclusion of a selected dataset's dependencies, with that inclusion reported to the user.
- A repository-subset filter, and flags to exclude archived repositories and forks from a run.
- A per-repository-level-collection item cap.
- Applying all of the above to both Phase 1 (Story 5) and Phase 2 (Story 6) fetching.

**Excluded:**
- Listing datasets without running a download (already delivered by Story 4's AC-2.7) — this story only adds the mechanics of restricting and expanding an actual run's selection.
- Any optional-tier dataset's fetch implementation itself — this story makes optional datasets *selectable*; whichever dataset is selected must already exist in the registry with a complete field list and schema (from Stories 5/6, extended as needed for optional-tier entries).

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 6's acceptance criteria are met: both the organization-level and repository-level default-tier fetches work end to end, so selection has real fetch behavior to narrow or extend.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.
