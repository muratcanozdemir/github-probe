# Story 3: Avoid wasted waits and share budget with other consumers

**Status:** APPROVED
**Depends On:** 2
**UI Changes:** No

## User Story

As an operator sharing an App installation with other tools, I can bound how long the tool waits and how much of the shared budget it consumes so that a long run never wastes time waiting into a certain failure and never starves other consumers of the installation.

## Acceptance Criteria

- AC-7.4: It does not begin a wait that would outlast the remaining credential lifetime when it cannot refresh the credential; it stops cleanly and resumably instead.
- AC-7.5: Total waiting is bounded by a user-configurable ceiling, after which the run stops cleanly and resumably.
- AC-7.7: The user can reserve budget for other consumers by capping this tool's consumption; the cap's meaning is a floor on remaining points that the tool will not cross.
- AC-7.9: REST requests are paced against their own separate budget and reported separately.

## Scope

**Included:**
- Comparing a prospective rate-limit wait against the active credential's remaining lifetime (from Story 1's credential provider) before waiting, and stopping cleanly and resumably instead of waiting into an unrecoverable expiry.
- A configurable ceiling on total accumulated waiting across a run, after which the run stops cleanly and resumably.
- A user-supplied consumption floor that the tool will not cross, reserving headroom for other consumers of the same installation.
- Independent budget tracking and reporting for REST requests, separate from GraphQL point consumption.

**Excluded:**
- The base pacing, retry, and secondary-limit behavior this story refines (Story 2).
- Reporting these consumption figures in a run's final summary or manifest — that reporting is Story 10 (single-command run) and Story 9 (manifest), which consume the statistics this story produces.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 2's acceptance criteria are met: requests self-pace against live budget figures, respect secondary limits, and retry transient failures with backoff.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.
