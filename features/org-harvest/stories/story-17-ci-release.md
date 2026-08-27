# Story 17: Set up CI and release automation

**Status:** APPROVED
**Depends On:** 16
**UI Changes:** No

## User Story

As a maintainer, I can rely on CI to catch lint, type, and test failures on every push and pull request, and on a tag push to produce a signed-off, SBOM-accompanied GitHub Release, so that changes don't silently break a tool people rely on for archival data.

## Acceptance Criteria

- AC-10.5: CI runs linting, strict type checking, and the full test suite on every push to the default branch and every pull request.
- AC-10.6: CI fails on any lint error, type error, or test failure.
- AC-10.7: Third-party CI actions are pinned to full commit SHAs with the version in a trailing comment.
- AC-10.8: A tag-triggered release workflow runs the full quality gate, then builds the distribution, generates a CycloneDX SBOM, and attaches both to a GitHub Release.
- AC-10.9: The release fails if any quality-gate step fails or if an expected build artifact is missing.

## Scope

**Included:**
- A CI workflow running lint, strict type checking, and the full test suite on every push to the default branch and on every pull request, failing the build on any error.
- SHA-pinning every third-party action used, with the version recorded in a trailing comment.
- A tag-triggered release workflow that re-runs the quality gate, builds the package, generates a CycloneDX SBOM, and attaches both the build and the SBOM to a GitHub Release — failing if any step fails or an expected artifact is missing.

**Excluded:**
- Per-story test coverage of authentication, pagination, resume, rate limiting, partial failures, systemic failure, preflight, retry-gaps, and output writing (spec's AC-10.1–10.4) — that coverage is delivered incrementally by every story's own exit criteria ("quality checks pass... tests"), not by this story. This story configures the CI *infrastructure* those tests run under; it does not add new test coverage of its own.
- Publishing to PyPI, coverage gates, and a Python version matrix — explicitly out of scope per spec.md.

## Entry Criteria (Functional)

Before starting this story, ensure:
- Story 16's acceptance criteria are met: the feature's documentation is complete, since a release should ship with accurate usage instructions.
- Every other story's own tests already pass locally — this story wires existing test coverage into CI, it does not need to add coverage of its own.

## Exit Criteria (Functional)

This story is complete when:
- All acceptance criteria are verified.
- Quality checks pass (lint, typecheck, tests).
- Code reviewed.
