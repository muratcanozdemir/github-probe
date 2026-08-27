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
