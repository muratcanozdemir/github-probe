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

---

## Implementation Plan

### Implementation Steps

1. Confirm the exact toolchain versions and commands this story wires together, rather than guessing: `uv --version` (0.12.5, matching this dev environment), the `[dependency-groups] dev` and `[tool.ruff]`/`[tool.mypy]`/`[tool.pytest.ini_options]` sections of `pyproject.toml` (line length 100, `mypy --strict` scoped to `files = ["src"]`, `pytest`'s `testpaths = ["tests"]`), and that `uv.lock` is committed (so `uv sync --locked` is meaningful in CI rather than silently regenerating the lock).
2. Resolve real, current commit SHAs for every third-party GitHub Action needed (AC-10.7) by querying the GitHub API directly (`GET /repos/{owner}/{repo}/releases/latest` for the current tag, then `GET /repos/{owner}/{repo}/git/refs/tags/{tag}` — resolving through `GET /repos/{owner}/{repo}/git/tags/{sha}` for an annotated tag — to the underlying commit), then verifying each resolved SHA actually resolves via `GET /repos/{owner}/{repo}/commits/{sha}` before using it: `actions/checkout` (v7.0.1), `astral-sh/setup-uv` (v10.0.1), `softprops/action-gh-release` (v3.0.2).
3. Create `.github/workflows/ci.yml`: a `quality` job triggered on `push` to `main` and every `pull_request`, running (as separate steps, so a failure names exactly what broke, AC-10.6) `ruff check`, `ruff format --check`, `mypy src/`, and `pytest -q` after `uv sync --locked`. Also declares a `workflow_call` trigger so it can be reused as-is by the release workflow, rather than maintaining a second copy of the same four steps.
4. Create `.github/workflows/release.yml`, triggered on a `v*` tag push: a `quality` job that `uses: ./.github/workflows/ci.yml` (the exact same gate as CI, satisfying AC-10.8's "runs the full quality gate"), then a `release` job with `needs: quality` (so a quality-gate failure blocks the release entirely) that builds the distribution (`uv build`), explicitly verifies both a wheel and an sdist landed in `dist/` (failing the job with an `::error::` annotation otherwise — a first, explicit layer of AC-10.9), generates a CycloneDX SBOM via `cyclonedx-py environment` against the synced venv (pinned by exact PyPI version, `cyclonedx-bom==7.3.1`, run through `uvx` — a Python tool rather than a GitHub Action, so it's outside AC-10.7's SHA-pinning requirement but still version-pinned), verifies the SBOM file is non-empty and valid JSON (a second layer of AC-10.9), then attaches the wheel, sdist, and SBOM to a GitHub Release via `softprops/action-gh-release` with `fail_on_unmatched_files: true` (a third, belt-and-suspenders layer of AC-10.9 at the point of attachment).
5. Verify every step actually works before trusting it in CI: ran `uv build` locally and inspected `dist/`; ran the exact `cyclonedx-py environment` invocation locally against this project's `.venv` and inspected the resulting SBOM's `bomFormat`/`specVersion`/component count; then did a full clean dry run of the release job's build → verify-artifacts → generate-SBOM → verify-SBOM script sequence end-to-end in an isolated copy of the repository, confirming the exact shell logic used in `release.yml` (not just the individual commands in isolation) succeeds.
6. Ran the existing quality gate (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest -q`) to confirm this documentation/config-only story caused no regression to `src/`/`tests/` — this story adds no application code and needed none of its own tests.

### Files Created/Modified

| File | Action | Purpose |
|------|--------|---------|
| `.github/workflows/ci.yml` | Create | Lint/typecheck/test on every push to `main` and every PR (AC-10.5, AC-10.6); also reusable via `workflow_call` |
| `.github/workflows/release.yml` | Create | Tag-triggered quality gate, build, SBOM, and GitHub Release (AC-10.8, AC-10.9) |

### Cross-Module Seams

No cross-module seams identified — this story adds CI/release configuration only, with no code that reads or writes data another module produces.

### Testing Approach

- No new automated tests — GitHub Actions workflow files aren't unit-testable in this codebase's test suite, and this story adds no application code.
- Verification performed instead of tests: every action reference's commit SHA was independently confirmed to exist via the GitHub API before being used; the release job's exact build/verify/SBOM-generate/verify script sequence was dry-run locally end-to-end in an isolated copy of the repository (not just each command individually) and confirmed to succeed, producing a real wheel, sdist, and valid CycloneDX 1.6 SBOM; the existing quality gate was re-run in full to confirm zero regression (374 passed, unchanged from Story 16).
- The workflows themselves will get their first real exercise on the next push/PR/tag against this repository once it has a remote — that's expected for CI/release infrastructure and isn't something a local dry run can fully substitute for (there's no GitHub Actions runner available in this environment to execute the `.yml` files directly).

### Risks

- The local dry run exercises the same shell logic the workflow uses, but not the GitHub Actions runner environment itself (its exact Ubuntu image, network egress rules, or the real `softprops/action-gh-release`/`astral-sh/setup-uv` action code) — a first real run against GitHub Actions is the only way to fully confirm the workflow YAML is accepted and behaves as intended end to end.
- Pinned commit SHAs (`actions/checkout`, `astral-sh/setup-uv`, `softprops/action-gh-release`) will need manual updates over time as new versions are needed (e.g. for a new runner image or a security fix) — this is the explicit trade-off AC-10.7 asks for (supply-chain safety over auto-updating convenience) and isn't a defect, but it does mean these pins have no automatic refresh mechanism (e.g. Dependabot/Renovate) configured as part of this story.
- `cyclonedx-py environment` reflects whatever is installed in the release job's own venv at the moment it runs (i.e., exactly what `uv sync --locked` installed from `uv.lock`) — this is the intended scope (a build-time SBOM of what actually shipped) but means the SBOM would need regenerating on release if dependencies change without an accompanying tag.

### Decisions Made

- **The release workflow calls `ci.yml` as a reusable workflow (`uses: ./.github/workflows/ci.yml`) instead of duplicating its four quality-gate steps** — one source of truth for what "the full quality gate" means, so a future change to CI's steps doesn't silently drift out of sync with what the release workflow re-runs before shipping.
- **`cyclonedx-py environment` (a Python tool run via `uvx`, pinned by exact PyPI version) was chosen over `anchore/sbom-action` (a Go/syft-based GitHub Action)** — this project's dependencies are entirely Python-managed through `uv`/`pyproject.toml`, and `cyclonedx-py`'s `environment` subcommand reads that installed environment directly, producing a CycloneDX SBOM scoped precisely to this package's actual Python dependency graph, without adding another SHA-pinned GitHub Action whose scanning behavior (syft's generic filesystem detection) is a less precise match for a pure-Python project than a Python-native, dependency-aware tool.
- **Explicit artifact/SBOM verification steps are added in `release.yml` beyond `fail_on_unmatched_files: true` alone** — `uv build` or `cyclonedx-py` failing silently to produce their expected output (as opposed to raising a nonzero exit) would otherwise only surface as a confusing "no files matched" error at the release-upload step, several steps removed from the actual cause; failing immediately, at the step that should have produced the file, gives a much clearer signal (AC-10.9's intent, not just its letter).
- **CI triggers on push to `main`** — no other branch existed to observe in this repository at the time of writing (it has no branches beyond this feature branch and no remote yet), so `main` was chosen as the conventional GitHub default-branch name; this should be revisited if the repository's actual default branch, once created on GitHub, differs.
