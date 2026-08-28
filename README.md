# org-harvest

Downloads a complete, self-consistent snapshot of a GitHub organization via
the GraphQL API, authenticating as a GitHub App installation. A snapshot
covers organization metadata, membership, teams, repositories, and
per-repository data (issues, pull requests, releases, and more), written to
NDJSON and finalized to Parquet, alongside a manifest recording exactly what
succeeded, what didn't, and how much of the API budget the run consumed.

- [Installation](#installation)
- [Creating and installing the GitHub App](#creating-and-installing-the-github-app)
- [Authentication](#authentication)
- [Quick start](#quick-start)
- [Datasets: tiers and selection](#datasets-tiers-and-selection)
- [Restricting to specific repositories](#restricting-to-specific-repositories)
- [Preflight](#preflight)
- [Resume, force-fresh, and retry-gaps](#resume-force-fresh-and-retry-gaps)
- [Progress output (`--verbose`)](#progress-output---verbose)
- [Snapshot layout](#snapshot-layout)
- [Manifest format](#manifest-format)
- [Gap representation](#gap-representation)
- [Exit statuses](#exit-statuses)
- [Configuring a non-github.com host](#configuring-a-non-githubcom-host)
- [Using a CI job cache for resume](#using-a-ci-job-cache-for-resume)
- [Using org-harvest as a library](#using-org-harvest-as-a-library)

## Installation

Requires Python 3.12+.

```bash
uv tool install org-harvest
# or
pip install org-harvest
```

Both install the `org-harvest` command. For development, clone the
repository and run `uv sync`, then `uv run org-harvest --help` from the
checkout.

## Creating and installing the GitHub App

org-harvest authenticates as a **GitHub App installation**, not as a user —
this gives it a scoped, revocable identity independent of any one person's
account.

1. **Create the App**: in your organization's settings, go to *Developer
   settings → GitHub Apps → New GitHub App*. Any name/homepage URL works;
   this App is only ever used programmatically, so webhooks can be
   disabled.
2. **Grant permissions.** Run `org-harvest datasets list` to see every
   dataset alongside the exact permission(s) it needs. The full set of
   permissions any dataset in this tool can require is:

   | Permission | Datasets it gates (examples) |
   |---|---|
   | `metadata` | `organization`, `repositories` |
   | `members` | `members`, `pending_members`, `teams`, `team_members`, `team_repositories` |
   | `organization_administration` | `org_rulesets`, `org_domains`, `org_ip_allow_list`, `audit_log` |
   | `organization_custom_properties` | `org_custom_properties` |
   | `organization_hooks` | `org_webhooks` |
   | `organization_projects` | `projects_v2` |
   | `contents` | `dependency_graph_manifests` |
   | `issues` | `issues` |
   | `pull_requests` | `pull_requests` |
   | `discussions` | `discussions` |
   | `administration` | `branch_protection_rules`, `repo_rulesets`, `environments`, `deploy_keys` |
   | `repository_custom_properties` | `repo_custom_property_values` |
   | `deployments` | `deployments` |
   | `vulnerability_alerts` | `vulnerability_alerts` |
   | `checks` | `check_suites` |
   | `actions` | `workflow_runs` |
   | `packages` | `packages` |

   Grant **read-only** access for every permission above unless you know
   you need write access for something else. If you only plan to run the
   default-tier datasets (see below), you can skip permissions that are
   only needed by optional-tier datasets (`organization_administration`,
   `organization_custom_properties`, `organization_hooks`,
   `organization_projects`, `contents`, `administration`,
   `repository_custom_properties`, `checks`, `actions`, `packages`) until
   you actually opt into those datasets.
3. **Generate a private key** for the App (same page, *Private keys →
   Generate a private key*) and save the downloaded `.pem` file somewhere
   `org-harvest` can read it. Note the App's **Client ID**, shown near the
   top of the same page.
4. **Install the App** on your organization: *Install App* in the App's
   settings, choosing "All repositories" or a specific subset. Installing
   on a subset means the installation is *repository-scoped* — see
   `--repos`/scope-restriction warnings below, since this also affects
   what org-level, all-repository datasets (like `repositories` itself)
   can see.

Once installed, run `org-harvest auth-check <org>` (see
[Authentication](#authentication) for credentials) to confirm the App can
find its installation for that org before running a full snapshot.

## Authentication

Every command accepts the same credential options, in two mutually
exclusive forms:

**Form 1 — GitHub App key** (recommended; tokens are minted and refreshed
automatically):

```bash
org-harvest run acme \
  --app-private-key-path ./acme-org-harvest.pem \
  --app-client-id Iv1.abc123
```

**Form 2 — a pre-minted installation access token** (useful when something
else already manages token minting, or for quick local testing):

```bash
org-harvest run acme --token ghs_...
```

A pre-minted token is never refreshed — if the run outlasts the token's
lifetime, it stops with a resumable error rather than guessing at a new
token. Its permissions also can't be introspected locally, so
`preflight`/`run` report `degraded` (not `ready`/`blocked`) for every
dataset when using this form.

**Environment variables (for CI)** — every credential/host option has a
matching environment variable, so nothing needs to be passed on the command
line (where it would be visible in process listings):

| Option | Environment variable |
|---|---|
| `--app-private-key-path` | `ORG_HARVEST_APP_PRIVATE_KEY_PATH` |
| `--app-client-id` | `ORG_HARVEST_APP_CLIENT_ID` |
| `--token` | `ORG_HARVEST_TOKEN` |
| `--api-host` | `ORG_HARVEST_API_HOST` |

Passing `--token` directly on the command line prints a warning to stderr
for exactly this reason — prefer `ORG_HARVEST_TOKEN` in CI.

`--app-private-key-path`/`--app-client-id` and `--token` are mutually
exclusive; passing both is rejected before any network call.

## Quick start

```bash
export ORG_HARVEST_APP_PRIVATE_KEY_PATH=./acme-org-harvest.pem
export ORG_HARVEST_APP_CLIENT_ID=Iv1.abc123

org-harvest auth-check acme          # confirm the App can authenticate
org-harvest preflight acme           # check readiness without downloading anything
org-harvest run acme                 # download the full default-tier snapshot
```

A successful run prints per-dataset record counts, elapsed time, API
consumption, and the snapshot directory it wrote to, then exits `0`.

## Datasets: tiers and selection

`org-harvest datasets list` prints every dataset with its tier, level
(`organization` or `repository`), and required permission(s).

- **Default tier**: runs automatically unless narrowed with `--datasets`.
  Covers organization metadata, members, teams, repositories, issues, pull
  requests, discussions, releases, labels, milestones, collaborators,
  branch protection rules, repository rulesets, custom property values,
  environments, deployments, vulnerability alerts, topics, and languages.
- **Optional tier**: only runs when explicitly named. Covers
  higher-permission or high-volume data: `workflow_runs`, `check_suites`,
  `packages`, `forks`, `stargazers`, `watchers`,
  `dependency_graph_manifests`, `deploy_keys`, `projects_v2`, `audit_log`,
  and `org_webhooks`.

```bash
# Narrow to just these two default-tier datasets:
org-harvest run acme --datasets organization,members
```

`--datasets` always replaces the default selection with exactly the names
given (plus their dependencies) — there's no shorthand for "the default
tier plus one more optional dataset"; to add an optional-tier dataset
alongside the default tier, list every default-tier dataset you want
explicitly, together with the optional one(s):

```bash
org-harvest run acme --datasets organization,members,pending_members,teams,\
team_members,team_repositories,repositories,org_rulesets,org_custom_properties,\
org_domains,org_ip_allow_list,issues,pull_requests,discussions,releases,labels,\
milestones,collaborators,branch_protection_rules,repo_rulesets,\
repo_custom_property_values,environments,deployments,vulnerability_alerts,\
topics,languages,workflow_runs
```

Naming a dataset automatically pulls in whatever it depends on (e.g.
`team_members` depends on `teams`) — the run's output reports which
datasets were auto-included this way. An unknown dataset name, or a
selection that resolves to zero datasets, is rejected before any network
call. Run `org-harvest datasets list` to get the exact current names for
this kind of "default tier + optional extra" selection.

## Restricting to specific repositories

```bash
org-harvest run acme --repos service-a,service-b
org-harvest run acme --exclude-archived --exclude-forks
org-harvest run acme --max-items-per-collection 500
```

`--repos` restricts the run to exactly the named repositories;
`--exclude-archived`/`--exclude-forks` drop archived/forked repositories
from an otherwise-unrestricted run. `--max-items-per-collection` caps how
many items are collected per repository-level dataset per repository
(e.g. per-repo issue count), for a bounded run against very active repos.

## Preflight

```bash
org-harvest preflight acme --datasets organization,issues
```

Reports, per dataset, whether the current credentials are `ready`,
`degraded` (permissions unknown — always the case for a pre-minted token),
or `blocked` (a required permission is missing), plus an estimate of API
points and any additional rate-limit wait the full run would need. Exits
non-zero if anything is blocked. `run --fail-fast` performs the same check
internally and aborts before downloading anything if it finds a blocked
dataset, instead of proceeding and recording the gap.

## Resume, force-fresh, and retry-gaps

A `run` that stops partway through — a rate-limit wait that would outlast
the token, an interrupted process, a systemic failure — leaves behind an
incomplete, resumable snapshot (no `manifest.json` yet). Running `run`
again against the same org **automatically resumes** the newest incomplete
snapshot rather than starting over:

```bash
org-harvest run acme   # picks up where the last incomplete run left off
```

- `--resume SNAPSHOT` resumes a specific snapshot by its timestamp
  directory name instead of the newest incomplete one.
- `--force-fresh` always starts a brand-new snapshot, ignoring any
  incomplete one.
- A resume is refused (independent of any network call) if the recorded
  org, dataset selection, or repository filter doesn't match this
  invocation, or if the checkpoint was written by an incompatible tool
  version — these mean the stored progress can't be safely continued, and
  `--force-fresh` is the way out.
- `--stale-after-days` (default `7`) refuses to resume a snapshot older
  than that; `--allow-stale-resume` overrides it.
- A first Ctrl-C stops cooperatively — the in-flight page finishes, its
  progress is saved, and the snapshot stays resumable. A second Ctrl-C
  stops immediately.
- Only one `run` can hold a given org at a time within the same
  `--snapshot-root`; a second concurrent run against the same org is
  refused (a stale claim left by a killed process is detected and
  reclaimed automatically).

A **completed** snapshot (one with a manifest) that still has gaps —
individual resources that failed after retries, or an entire dataset that
errored out — can be selectively re-attempted without redownloading
anything that already succeeded:

```bash
org-harvest retry-gaps acme 20260115T093000Z
```

This re-fetches only the gapped resources of each gapped dataset,
leaves every other (already-clean) dataset's data untouched, and rewrites
the manifest with `last_retried_at` set. Running it against a snapshot
with no gaps is a no-op that exits `0` immediately.

## Progress output (`--verbose`)

```bash
org-harvest run acme --verbose
```

Prints one line per progress event to stderr as the run proceeds: each
phase's start/finish (`preflight`, `phase1` for organization-level data,
`phase2` for repository-level data, `finalize`), each dataset's outcome as
soon as it's known, and any rate-limit wait actually taken — instead of
only a summary at the very end. Without `--verbose`, nothing extra is
printed and stdout carries only the final summary.

## Snapshot layout

```
<snapshot-root>/<org (lowercased)>/
  .run.lock                    # held only while a run/retry is in progress
  index.json                   # root index — every snapshot for this org, and the latest complete one
  <timestamp>/                 # e.g. 20260115T093000Z, one per run (fresh or resumed)
    checkpoint.json            # internal resume/retry state — not a stable public format
    manifest.json              # present only once the run completes — see below
    organization.parquet
    members.parquet
    issues.parquet
    ...                        # one <dataset>.parquet per dataset actually fetched
```

`<timestamp>` is this run's start time in compact UTC (`YYYYMMDDTHHMMSSZ`).
Each dataset is written first as `<dataset>.ndjson` and converted to
`<dataset>.parquet` once its phase finishes; the NDJSON is removed after a
successful conversion unless the process is interrupted first (an
incomplete snapshot may still have `.ndjson` files alongside or instead of
`.parquet` ones). A snapshot with no `manifest.json` is, by definition,
incomplete — every reader in this tool treats it that way, and it's safe to
resume.

## Manifest format

`manifest.json` is written once a run finishes (successfully or with
gaps) and is the authoritative record of what happened — readable on its
own, without needing console output:

```json
{
  "org": "acme",
  "api_host": "github.com",
  "tool_version": "0.1.0",
  "started_at": "2026-01-15T09:30:00+00:00",
  "completed_at": "2026-01-15T10:12:34+00:00",
  "dataset_selection": ["organization", "members", "issues", "..."],
  "dataset_counts": {"organization": 1, "members": 42, "issues": 1830},
  "gaps": [],
  "scope_restricted": false,
  "consumption": {
    "graphql_points_consumed": 5230,
    "graphql_requests": 118,
    "rest_requests_consumed": 3,
    "rate_limit_waits": 0,
    "total_wait_seconds": 0.0
  },
  "status": "complete",
  "last_retried_at": null
}
```

- `status` is `"complete"` only when `gaps` is empty and
  `scope_restricted` is `false`; otherwise `"complete_with_gaps"` — this
  matches the CLI's exit-status split (see below).
- `scope_restricted` is `true` when the App installation is scoped to
  selected repositories rather than all of them, since org-level,
  all-repository datasets (like `repositories` itself) can't see what the
  installation wasn't granted access to.
- `last_retried_at` is set by `retry-gaps` and stays `null` until the
  first retry against this snapshot.

## Gap representation

Every partial or total dataset failure becomes a structured entry in
`gaps` — never a swallowed exception or a silently incomplete file:

```json
{
  "dataset": "issues",
  "resource_id": "R_kgDOA1b2c3",
  "field_path": null,
  "reason": "request to https://api.github.com/graphql failed after 6 attempts: ...",
  "occurred_at": "2026-01-15T10:05:12+00:00"
}
```

- `resource_id` identifies what was being fetched — a repository or team
  node ID for a per-resource gap, or the organization's own login for a
  gap that applies to an entire org-level dataset (there's no
  per-resource concept for e.g. `members` or `org_rulesets`).
- `field_path` is the GraphQL error's own `path`, when the API returned a
  partial response with a field-level error; `None` for a gap from a
  transport-wide failure with no GraphQL response to carry one.
- A dataset that was selected but has no fetch implementation yet becomes
  a single gap with `resource_id: null` and a `"dataset not yet
  implemented"` reason, rather than silently producing nothing.

## Exit statuses

| Code | Name | Meaning |
|---|---|---|
| 0 | `SUCCESS` | Every selected dataset completed with no gaps and no scope restriction. |
| 1 | `COMPLETED_WITH_GAPS` | The run finished and wrote a manifest, but at least one dataset has a gap or the installation is scope-restricted. |
| 2 | `STOPPED_RESUMABLE` | The run stopped before finalizing — a rate-limit wait that would have outlasted the token, or a systemic failure — leaving a checkpoint but no manifest; resumable. |
| 3 | `INVALID_USAGE` | The request was invalid independent of any network call: bad credential configuration, an unknown dataset name, an unsafe resume. |
| 4 | `AUTH_FAILURE` | Authentication or authorization failed: an expired non-refreshable token, a rejected refresh, an org that doesn't exist, or an uninstalled App. |
| 5 | `CONCURRENT_RUN_REFUSED` | Another run already claims this org within this `--snapshot-root`. |
| 6 | `PREFLIGHT_BLOCKED` | `--fail-fast` was given and preflight found at least one blocked dataset; the run never started. |
| 7 | `UNEXPECTED_FAILURE` | A request failed after exhausting retries, or some other unanticipated failure occurred. |
| 130 | `USER_INTERRUPT` | The user interrupted the run (Ctrl-C), gracefully or immediately. |

## Configuring a non-github.com host

```bash
# GitHub Enterprise Cloud with data residency:
org-harvest run acme --api-host api.octocorp.ghe.com

# GitHub Enterprise Server:
org-harvest run acme --api-host github.mycompany.internal
```

`--api-host` (or `ORG_HARVEST_API_HOST`) accepts three shapes: the default
`github.com`; a hostname already starting with `api.`, used as-is (GHEC
data-residency tenants); or any other hostname, treated as a GitHub
Enterprise Server appliance whose REST and GraphQL APIs are published
under `/api/v3` and `/api/graphql` respectively.

## Using a CI job cache for resume

Automatic resume only works if the same snapshot directory is available
to the process that resumes it. In CI, each job typically runs in a fresh
checkout with nothing preserved between jobs — so **resuming a snapshot
across separate CI jobs requires explicitly caching or restoring the
`--snapshot-root` directory between them** (e.g. `actions/cache` keyed on
the org, or an artifact upload/download step). Without that, a run that
stops partway through in one job has nothing to resume from in the next,
and will start fresh instead.

## Using org-harvest as a library

Every public type and entry point is re-exported from the package root:

```python
import asyncio
from pathlib import Path
from org_harvest import (
    StaticTokenCredentialProvider,
    Transport,
    run_snapshot,
    OrgHarvestError,
    ProgressEvent,
)


async def main() -> None:
    provider = StaticTokenCredentialProvider("ghs_...")
    transport = Transport(provider)
    try:
        result = await run_snapshot(
            transport,
            provider,
            org="acme",
            snapshot_root=Path("./snapshots"),
            on_progress=lambda event: print(event.message),
        )
    finally:
        await transport.aclose()
        await provider.aclose()

    print(result.exit_status)
    if result.manifest is not None:  # None if the run stopped before finalizing
        print(result.manifest.dataset_counts)


asyncio.run(main())
```

- Credentials are always passed explicitly — the library never reads
  `ORG_HARVEST_*` environment variables itself; that's exclusively the
  CLI's responsibility.
- `run_snapshot()` returns a `RunResult` carrying the exit status, the
  snapshot directory, and (once available) the full `Manifest` — the same
  counts, gaps, and consumption statistics the CLI prints.
- `on_progress` (optional) is called with a `ProgressEvent` at each phase
  boundary, once per dataset as its outcome becomes known, and once per
  rate-limit wait actually taken.
- Every documented failure raises `OrgHarvestError`, carrying an
  `ErrorKind` — the one exception type the library surfaces.
- `retry_gaps()` is the programmatic equivalent of `retry-gaps`.
