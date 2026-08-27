# Story 10: Download a complete organization snapshot in one command

**Status:** APPROVED
**Depends On:** 3, 9
**UI Changes:** No

## User Story

As an engineer auditing a GitHub organization, I can run one command and receive a complete snapshot of that org so that I can analyze it offline without writing API code.

## Acceptance Criteria

- AC-1.1: Given valid credentials and an org login, a single command produces a snapshot on disk without further interaction.
- AC-1.2 (full): The snapshot includes every dataset in the default tier — every organization-level and repository-level dataset from Stories 5 and 6, run together end to end.
- AC-1.3: On completion the tool reports per-dataset record counts, elapsed time, GraphQL points consumed, REST requests consumed, and the number of rate-limit waits.
- AC-1.4: The command exits `0` when every selected dataset completed with no gaps and no scope restriction.
- AC-1.5: Each run writes a new snapshot directory named for the run's UTC start time; a completed snapshot is never modified by a later run, except by the retry-gaps operation of Story 14 acting on that specific snapshot.
- AC-1.6: The snapshot root defaults to `./snapshots` and is user-overridable; the layout is `<root>/<org-login-lowercased>/<utc-timestamp>/`.
- AC-1.7: Directory and file names derive only from the org login and dataset names, never from repository, team, or user names.
- AC-5.4: A run that completed with one or more gaps exits with the gaps status and prints a summary.

## Scope

**Included:**
- Wiring authentication (Story 1), pacing (Stories 2–3), preflight (Story 4), the organization-level fetch (Story 5), and the repository-level fetch (Story 7's resilience wrapping Story 6) into a single command that runs Phase 1 then Phase 2 and finalizes (Story 9) without further user interaction.
- The full, distinct exit-status set from the spec's FR-10: success, completed-with-gaps-or-scope-restriction, stopped-but-resumable, invalid usage/configuration, authentication/authorization failure, concurrent-run refusal, preflight-blocked, unexpected failure, and user interrupt.
- Reporting per-dataset counts, elapsed time, GraphQL and REST consumption, and rate-limit wait count on completion.
- The snapshot's directory naming, layout, and root-override behavior, and the rule that directory/file names never derive from repository, team, or user names.

**Excluded:**
- Restricting the run to a named dataset subset (Story 11) — this story's "complete snapshot" always means the full default tier.
- Resume behavior for an interrupted run (Stories 12–13) — this story defines what a clean full run looks like; resuming one that didn't finish is separate.
- Concurrent-run refusal's underlying claim/lock mechanism (Story 13) — this story only needs the resulting exit status to exist in the enumeration above.
- Library-level programmatic invocation (Story 15) — this story is the CLI's single-command surface.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 3's acceptance criteria are met: waits respect the credential lifetime and a configurable ceiling, and REST consumption is tracked separately.
- Story 9's acceptance criteria are met: a run's organization-level and repository-level data can be fetched, gaps recorded, finalized into Parquet, and described in a manifest and root index.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.
