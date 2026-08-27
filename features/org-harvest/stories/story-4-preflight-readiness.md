# Story 4: Check readiness before running a harvest

**Status:** APPROVED
**Depends On:** 2
**UI Changes:** No

## User Story

As an operator, I can list the available datasets and run a preflight check so that I know up front what my App cannot read and what a run will cost, instead of finding out hours into a long run.

## Acceptance Criteria

- AC-2.7: The user can list all datasets — both tiers — with a one-line description, tier, and required permissions for each, without running a download.
- AC-6.1: Before fetching, the tool determines which permissions the active token carries and whether the installation is scoped to selected repositories.
- AC-6.2: It warns about each selected dataset that will fail or degrade, naming the missing permission.
- AC-6.3: It reports an estimate for the run: repository count, projected GraphQL point cost, and projected duration including expected rate-limit waits.
- AC-6.4: The run proceeds despite warnings unless the user opts into failing fast.
- AC-6.5: Preflight can be run standalone without downloading; it exits non-zero when any selected dataset is blocked, and prints a per-dataset ready / degraded / blocked verdict.

## Scope

**Included:**
- Establishing the dataset registry: for each of the 37 datasets defined by the spec's FR-1, its name, description, tier (default/optional), and required permission(s). (Each dataset's field list, GraphQL fragment, and Parquet schema are added later, by the story that first makes that dataset fetchable — Story 5 or Story 6.)
- A standalone listing of every dataset with its description, tier, and required permissions.
- Determining the active token's actual permissions and whether the installation is repository-scoped.
- Comparing held permissions against a dataset selection and warning on each dataset that will fail or degrade.
- A cost and duration estimate (repository count, projected point cost, projected duration including expected waits) without downloading anything.
- Fail-fast mode, and the per-dataset ready/degraded/blocked verdict with its own exit behavior.

**Excluded:**
- Actually fetching any dataset's data (Stories 5 and 6) — this story only inspects readiness and reports estimates.
- Dataset selection syntax for narrowing or expanding a run (Story 11) — this story consumes whatever selection is given to compute warnings and estimates, but doesn't define how a selection is expressed or validated.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 2's acceptance criteria are met: the tool can make live, rate-limit-aware requests and knows the current point-cost model, which preflight's estimate reuses.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.
