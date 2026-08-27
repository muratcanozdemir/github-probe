# Story 15: Use org-harvest as a library

**Status:** APPROVED
**Depends On:** 10
**UI Changes:** No

## User Story

As a developer building a data pipeline, I can drive the crawler from my own Python code so that I can embed it in a larger pipeline rather than shelling out to the CLI.

## Acceptance Criteria

- AC-9.1: The package exposes its public types and entry points from the package root.
- AC-9.2: A caller can run a harvest programmatically and receive a structured result including counts, gaps, and consumption statistics.
- AC-9.3: Credentials are passed explicitly as parameters; the library never reads them from the environment on its own.
- AC-9.4: Callers can observe progress as the run proceeds rather than only at the end.
- AC-9.5: The library surfaces one documented exception type for its failures.

## Scope

**Included:**
- Flat re-exports of every public type and entry point from the package root, so callers never need to import from an internal submodule.
- A programmatic entry point that runs a harvest (equivalent to Story 10's single command) and returns a structured result carrying per-dataset counts, gaps, and consumption statistics.
- Confirming the library layer never reads credentials from the environment itself — that is exclusively the CLI's responsibility (Story 1), with the library always taking credentials as explicit parameters.
- A progress-observation mechanism so a caller can react during a run, not only after it completes.
- One documented exception type covering every library-raised failure.

**Excluded:**
- The CLI's own credential input surface (environment variables, arguments) — that's Story 1; this story only confirms the library underneath never bypasses explicit parameters.
- Any new fetch, resume, retry-gaps, or output behavior — this story exposes existing capability programmatically, it doesn't add new harvesting behavior.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 10's acceptance criteria are met: a complete organization snapshot can be produced end to end, with counts, gaps, and consumption statistics available at completion.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.
