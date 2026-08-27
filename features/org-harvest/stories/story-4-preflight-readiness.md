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

---

## Implementation Plan

### Implementation Steps

1. `src/org_harvest/datasets/registry.py` — new module: `DatasetTier`, `DatasetLevel` enums, frozen `DatasetSpec` dataclass, an in-memory `_REGISTRY` dict with `register()`/`get()`/`all_specs()`/`default_tier_names()`, and `complete_fetch_details()` for Story 5/6 to attach a dataset's field list and parent key once it becomes fetchable, without redefining the whole spec.
2. `src/org_harvest/datasets/catalog.py` — new module: all 37 `DatasetSpec` entries from FR-1's catalog (11 org-default, 15 repo-default, 11 optional), each with its tier, level, and required permission(s); a `register_all()` that populates the registry; an extensive module docstring caveat that the permission-name mapping is best-effort since GitHub publishes no authoritative permission-to-field table.
3. `src/org_harvest/datasets/__init__.py` — re-exports the public registry API and calls `register_all()` at import time (AC-2.7's listing and every later permission check both depend on the registry being populated as soon as the package is imported).
4. `src/org_harvest/preflight.py` — new module: `_PREFLIGHT_QUERY` (a single small GraphQL query for `rateLimit` and `organization.repositories.totalCount`, issued directly through `Transport` rather than depending on the not-yet-built fetch engine); `Verdict` enum (READY/DEGRADED/BLOCKED per AC-6.5's literal wording); `DatasetVerdict` and `PreflightReport` (with an `any_blocked` property driving the CLI's exit code); `_check_dataset_permissions()` implementing AC-6.1/AC-6.2 (BLOCKED when a required permission is missing and named in the reason; DEGRADED — folded into the same three-state contract rather than a fourth "unknown" state — when the credential type can't report its permissions at all, e.g. a pre-minted static token); `_estimate()` implementing AC-6.3 (repository count × per-dataset point cost, plus additional rate-limit windows needed if the estimate exceeds what's currently remaining); `run_preflight()` wiring it all together against a live `Transport`.
5. `src/org_harvest/credentials.py` — extend `CredentialProvider` with `permissions: dict[str, str] | None` and `repository_selection: str | None` (DEV-2) so preflight can read a credential's actual grants generically across both provider types, without isinstance branching. `StaticTokenCredentialProvider` reports both as `None` (a pre-minted token's permissions are not knowable via any API), matching the real limitation rather than pretending certainty; `AppKeyCredentialProvider` populates both from the installation-discovery and token-minting responses.
6. `src/org_harvest/cli.py` — `_parse_dataset_selection()` (resolves `--datasets` or defaults to the full default tier, validating each name through `get()` before any network call — AC-2.4/AC-2.5's error path, reused here ahead of Story 11 formally owning selection syntax); `datasets list` subcommand (AC-2.7); `preflight` command (AC-6.5) that builds a credential provider, runs `run_preflight()`, prints the report, and exits non-zero iff `any_blocked`.
7. `src/org_harvest/__init__.py` — re-export the new public types (`DatasetSpec`, `DatasetTier`, `DatasetLevel`, `all_dataset_specs`, `default_dataset_names`, `get_dataset_spec`, `PreflightReport`, `DatasetVerdict`, `Verdict`, `run_preflight`).

### Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `src/org_harvest/datasets/registry.py` | Create | Declarative dataset spec + registry (architecture.md Decision 1) |
| `src/org_harvest/datasets/catalog.py` | Create | All 37 dataset specs from FR-1 |
| `src/org_harvest/datasets/__init__.py` | Create | Public re-exports, populates registry at import |
| `src/org_harvest/preflight.py` | Create | Permission/scope/cost preflight check (AC-6.1–AC-6.5) |
| `src/org_harvest/credentials.py` | Modify | `permissions`/`repository_selection` on `CredentialProvider` (DEV-2) |
| `src/org_harvest/cli.py` | Modify | `datasets list` and `preflight` commands |
| `src/org_harvest/__init__.py` | Modify | Re-export new public types |
| `tests/test_registry.py` | Create | Registry population, lookup, duplicate-registration guard |
| `tests/test_preflight.py` | Create | Permission verdicts, scope restriction, cost estimate, budget update |
| `tests/test_cli.py` | Modify | `datasets list` and `preflight` command coverage |
| `tests/gh_responses.py` | Modify | Shared `preflight_response()` builder |

### Cross-Module Seams

No cross-module seams beyond what Stories 1–3 already established (`preflight.py` reads `credentials.permissions`/`credentials.repository_selection` — the same Protocol-based seam `transport.py` already uses for `can_refresh()`/`seconds_until_expiry()`). `complete_fetch_details()` is a seam Story 5/6 will exercise (registry written here, completed there), not one this story both writes and reads.

### Testing Approach

- **Unit — `tests/test_registry.py`:** all 37 datasets registered with the right tier split (26 default / 11 optional); every dataset has ≥1 required permission and a unique name; a spot-check of known dependency/level facts (`team_members` depends on `teams`, `repositories` is org-level, `issues` is repo-level); `get()` raises `INVALID_USAGE` naming the unknown dataset and listing valid ones (AC-2.4); registering a duplicate name raises without corrupting the existing entry.
- **Unit — `tests/test_preflight.py`:** permission checks in isolation for READY (all required permissions granted), BLOCKED (missing permission named in the reason — AC-6.2), and DEGRADED (permissions unknown, e.g. a static token — AC-6.1); scope-restriction reporting for both `"selected"` and `"all"`; cost estimate arithmetic for both the well-within-budget case (zero extra wait) and the budget-exceeded case (extra rate-limit windows); the GraphQL budget tracker is updated from the preflight response; an end-to-end run through a real `AppKeyCredentialProvider` discover-and-mint flow, not just a static token, to prove both credential forms actually integrate.
- **CLI — `tests/test_cli.py`:** `datasets list` prints all 37 lines with tier/level/permissions; `preflight` prints "degraded" (not "ready") for a static token whose permissions can't be introspected and still exits 0 (AC-6.4 — a warning alone doesn't fail the command); `preflight` prints "ready" when an App-key-derived token actually holds the required permission; `preflight` exits 1 and prints "blocked" naming the missing permission when a required permission isn't held (AC-6.5); an unknown `--datasets` name is rejected before any network call (AC-2.4); a repository-scoped installation triggers the scope-restriction warning line (EC-3).

### Risks

- None specific to this story beyond the general risk (documented in Story 3) that `StaticTokenCredentialProvider`'s real-time-based fields must not be mixed with a `Transport`'s fake clock in the same assertion — not applicable here since preflight doesn't touch wait-safety timing.

### Decisions Made

- **AC-6.4 ("the run proceeds despite warnings unless the user opts into failing fast") has no command to attach a `--fail-fast` flag to yet** — the standalone `preflight` command's own exit behavior is already fully specified by AC-6.5 (non-zero iff any dataset is blocked; a warning/degraded verdict alone does not fail it, which is the "proceeds despite warnings" half of AC-6.4). The "opts into failing fast" half is a property of the future `run` command (Story 10), which is the only place a full download can be aborted mid-flight on this condition. This story implements the shared `run_preflight()`/`PreflightReport` that Story 10 will call and gate on; Story 10 owns the actual `--fail-fast` flag.
- **Folded an "unknown" case into `DEGRADED`** rather than adding a fourth verdict state, since AC-6.5 literally names three states ("ready / degraded / blocked") and a static token's unknowable permissions are a genuine degradation of preflight's confidence, not a new category of outcome.
- See DEV-2 in `stories/deviations.md` for the `CredentialProvider.permissions`/`.repository_selection` extension and why a Protocol-level addition (not isinstance branching) was the right shape for this seam.
