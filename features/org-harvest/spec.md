# Feature Specification: org-harvest

**Status:** APPROVED
**Challenged:** No
**UI Required:** No
**Created:** 2026-08-27
**Feature:** A Python CLI and library that downloads a complete, self-consistent snapshot of a GitHub organization via the GraphQL API, authenticating as a GitHub App installation.

## Overview

Anyone who needs to know everything about a GitHub organization — for audit, migration, analysis, or archival — currently has to stitch together dozens of paginated API calls, manage a token that expires every hour, and stay inside a point budget that caps out well below what a large org actually contains. There is no single command that produces a trustworthy picture of an org and tells you honestly where it fell short.

`org-harvest` is that command. Pointed at an organization, it authenticates as a GitHub App installation, walks the organization's GraphQL graph — settings, members, teams, repositories, and the issue, pull request, access-control, and configuration data beneath them — and writes the result to disk as a timestamped snapshot. It is built for orgs large enough that a single run will outlive its token and exhaust its hourly budget: it checkpoints continuously, resumes where it stopped, and refreshes credentials without user involvement. Where data is missing — a permission absent, a repository unreachable, an installation scoped to only some repositories — the snapshot records the gap explicitly rather than presenting an incomplete file as a complete one.

## User Stories

### US-1: Download a complete organization snapshot
As an engineer auditing a GitHub organization, I want to run one command and receive a complete snapshot of that org so that I can analyze it offline without writing API code.

**Acceptance Criteria:**
- **AC-1.1:** Given valid credentials and an org login, a single command produces a snapshot on disk without further interaction.
- **AC-1.2:** The snapshot includes every dataset in the default tier defined by FR-1.
- **AC-1.3:** On completion the tool reports per-dataset record counts, elapsed time, GraphQL points consumed, REST requests consumed, and the number of rate-limit waits.
- **AC-1.4:** The command exits `0` when every selected dataset completed with no gaps and no scope restriction.
- **AC-1.5:** Each run writes a new snapshot directory named for the run's UTC start time; a completed snapshot is never modified by a later run, except by the retry-gaps operation of US-11 acting on that specific snapshot.
- **AC-1.6:** The snapshot root defaults to `./snapshots` and is user-overridable; the layout is `<root>/<org-login-lowercased>/<utc-timestamp>/`.
- **AC-1.7:** Directory and file names derive only from the org login and dataset names, never from repository, team, or user names.

### US-2: Choose which datasets to download
As an engineer working with a very large organization, I want to control which datasets are downloaded so that I get a useful result inside one rate-limit window instead of waiting hours for data I won't use.

**Acceptance Criteria:**
- **AC-2.1:** The user can restrict a run to a named subset of the default tier.
- **AC-2.2:** With no selection given, the run includes exactly the default tier.
- **AC-2.3:** Naming an optional (off-by-default) dataset in the selection enables it; the same mechanism both narrows within the default tier and opts into the optional tier.
- **AC-2.4:** Requesting an unknown dataset name fails immediately, before any network call, listing the valid names.
- **AC-2.5:** A selection that resolves to zero datasets is rejected before any network call.
- **AC-2.6:** Selecting a dataset that depends on another automatically includes the dependency, and the run reports that it did so.
- **AC-2.7:** The user can list all datasets — both tiers — with a one-line description, tier, and required permissions for each, without running a download.
- **AC-2.8:** The user can restrict a run to a subset of repositories, and can exclude archived repositories and forks.
- **AC-2.9:** The user can cap the number of items collected per repository-level collection.

### US-3: Authenticate as a GitHub App installation
As an operator running this in automation, I want to supply either an App private key or a pre-minted installation token so that I can run it locally with full credentials and in CI without shipping a private key into the runner.

**Acceptance Criteria:**
- **AC-3.1:** Given a PEM private key, a client ID, and an org login, the tool discovers the installation for that org and mints its own installation token.
- **AC-3.2:** Given a pre-minted installation token, the tool uses it directly and never requires a private key.
- **AC-3.3:** When minting its own tokens, the tool refreshes the token before expiry so that a run longer than one hour continues uninterrupted.
- **AC-3.4:** When given a pre-minted token that expires mid-run, the run stops cleanly with resumable state and a message explaining that a private key enables automatic refresh.
- **AC-3.5:** Credentials never appear in the snapshot, the manifest, logs, progress output, or error messages.
- **AC-3.6:** Supplying neither credential form, or both, fails immediately, naming the accepted combinations.
- **AC-3.7:** Credential material that is missing, unreadable, malformed, passphrase-protected, or mismatched with the given client ID fails before any data request, with a message naming which of these it was.
- **AC-3.8:** The CLI accepts credentials via documented environment variables (the CI path) or explicit arguments; supplying a secret as a command-line argument emits a warning that it is visible in process listings.
- **AC-3.9:** The tool targets a configurable API base host, defaulting to github.com, so that Enterprise Cloud data-residency tenants and Enterprise Server hosts are supported.

### US-4: Resume an interrupted run
As an engineer downloading a large organization, I want an interrupted run to resume where it stopped so that hours of work and a spent point budget are not lost to one failure.

**Acceptance Criteria:**
- **AC-4.1:** Progress is checkpointed continuously at page granularity, including the cursor position within each collection.
- **AC-4.2:** Re-running the same command resumes the newest incomplete snapshot for that org in that root, and reports which snapshot it is resuming and from where.
- **AC-4.3:** The user can name a specific snapshot to resume instead of taking the newest.
- **AC-4.4:** When no incomplete snapshot exists, a re-run starts a new snapshot rather than modifying the completed one.
- **AC-4.5:** Resumed runs do not re-fetch pages already written and do not duplicate records.
- **AC-4.6:** A run terminated abruptly is still resumable, losing at most the in-flight page; a partially written trailing record is discarded rather than corrupting the file.
- **AC-4.7:** The user can force a fresh run that ignores existing incomplete snapshots.
- **AC-4.8:** Resuming a snapshot whose dataset selection, repository filter, or org differs from the original is refused with an explanation.
- **AC-4.9:** Resuming a snapshot whose checkpoint state is unreadable, or which was written by an incompatible tool version, is refused with an explanation directing the user to start fresh.
- **AC-4.10:** Resuming a snapshot older than a configurable staleness window (default 7 days) is refused unless the user overrides, because stored cursors may no longer be meaningful.
- **AC-4.11:** On interruption by the user, the tool finishes the in-flight page, flushes checkpoint state, prints the command to resume, and exits with the interrupt status; a second interrupt stops immediately.

### US-5: Know exactly what is missing
As an auditor relying on this data, I want every gap recorded in the snapshot so that I never mistake an incomplete download for a complete one.

**Acceptance Criteria:**
- **AC-5.1:** When the API returns partial data alongside errors, the successful portion is written and each failure is recorded as a structured gap.
- **AC-5.2:** Each gap records dataset, resource identifier, the field path when the API supplies one, reason, and UTC timestamp.
- **AC-5.3:** A request that yields no usable response after its retries are exhausted is recorded as a gap; the run continues.
- **AC-5.4:** A run that completed with one or more gaps exits with the gaps status and prints a summary.
- **AC-5.5:** A run that hits gaps still attempts every other dataset.
- **AC-5.6:** The presence or absence of gaps is discoverable from the snapshot alone, without the run's console output.
- **AC-5.7:** A permanently inaccessible repository does not prevent the rest of the org from being downloaded.
- **AC-5.8:** When the installation is scoped to selected repositories rather than all, the tool records the restriction in the manifest, marks the snapshot as scope-restricted, and reports how many of the organization's repositories it can reach.

### US-6: Know about problems before spending hours
As an operator, I want to be told up front what my App cannot read and what the run will cost so that a long run doesn't fail partway through on something knowable at the start.

**Acceptance Criteria:**
- **AC-6.1:** Before fetching, the tool determines which permissions the active token carries and whether the installation is scoped to selected repositories.
- **AC-6.2:** It warns about each selected dataset that will fail or degrade, naming the missing permission.
- **AC-6.3:** It reports an estimate for the run: repository count, projected GraphQL point cost, and projected duration including expected rate-limit waits.
- **AC-6.4:** The run proceeds despite warnings unless the user opts into failing fast.
- **AC-6.5:** Preflight can be run standalone without downloading; it exits non-zero when any selected dataset is blocked, and prints a per-dataset ready / degraded / blocked verdict.

### US-7: Stay inside GitHub's rate limits
As an operator sharing an App installation with other tools, I want this to throttle itself so that it neither gets blocked nor starves other consumers.

**Acceptance Criteria:**
- **AC-7.1:** The tool paces requests against the live remaining budget reported by the API rather than an assumed limit, so standard, Enterprise Cloud, and Enterprise Server installations all work without configuration.
- **AC-7.2:** It stays within GitHub's concurrent-request and per-minute secondary limits, and backs off and reduces concurrency when a secondary limit is signalled.
- **AC-7.3:** On exhausting the hourly budget it waits until the advertised reset and continues, reporting the wait and its expected end.
- **AC-7.4:** It does not begin a wait that would outlast the remaining credential lifetime when it cannot refresh the credential; it stops cleanly and resumably instead.
- **AC-7.5:** Total waiting is bounded by a user-configurable ceiling, after which the run stops cleanly and resumably.
- **AC-7.6:** Retries use exponential backoff with jitter; transient failures are retried up to a bounded count and permanent ones are not.
- **AC-7.7:** The user can reserve budget for other consumers by capping this tool's consumption; the cap's meaning is a floor on remaining points that the tool will not cross.
- **AC-7.8:** Page sizes stay within API limits, and a query that exceeds the node ceiling is retried at a smaller page size before being recorded as a gap.
- **AC-7.9:** REST requests are paced against their own separate budget and reported separately.
- **AC-7.10:** Every request carries an identifying user agent, and REST requests pin an explicit API version.

### US-8: Get data in an analysis-ready format
As a data analyst, I want the finished snapshot as Parquet so that I can load it directly into my usual tools without writing a parser.

**Acceptance Criteria:**
- **AC-8.1:** During the run, records are appended as newline-delimited UTF-8 JSON, one file per dataset.
- **AC-8.2:** On successful completion each NDJSON file is converted to Parquet using that dataset's declared schema.
- **AC-8.3:** The Parquet schema for a dataset is identical across runs and orgs, including when the dataset has zero records or a column is null throughout.
- **AC-8.4:** Conversion failure for one dataset discards no downloaded data and is recorded as a gap.
- **AC-8.5:** Finalization — conversion plus manifest write — is re-runnable on its own against a complete snapshot, without re-downloading.
- **AC-8.6:** Every record carries a stable identifier, and every child record carries the identifier of its parent.
- **AC-8.7:** Each snapshot contains a manifest describing that snapshot: org, API host, tool version, start and completion times, dataset selection, per-dataset record counts, all gaps, scope restrictions, consumption statistics, and completion status.
- **AC-8.8:** A root index lists all snapshots per org and identifies the most recent snapshot that completed with no gaps and no scope restriction.
- **AC-8.9:** The user can keep the intermediate NDJSON rather than having it removed after conversion.
- **AC-8.10:** An interrupted run leaves valid, readable NDJSON for everything completed so far.
- **AC-8.11:** A snapshot without a manifest is treated as incomplete by every operation that reads snapshots.

### US-9: Use it as a library
As a developer building a data pipeline, I want to drive the crawler from my own Python code so that I can embed it rather than shell out to a CLI.

**Acceptance Criteria:**
- **AC-9.1:** The package exposes its public types and entry points from the package root.
- **AC-9.2:** A caller can run a harvest programmatically and receive a structured result including counts, gaps, and consumption statistics.
- **AC-9.3:** Credentials are passed explicitly as parameters; the library never reads them from the environment on its own.
- **AC-9.4:** Callers can observe progress as the run proceeds rather than only at the end.
- **AC-9.5:** The library surfaces one documented exception type for its failures.

### US-10: Trust the tool
As a maintainer, I want thorough automated tests and CI so that changes don't silently break a tool people rely on for archival data.

**Acceptance Criteria:**
- **AC-10.1:** Tests cover authentication and refresh, pagination, resume, rate limiting, partial failures, systemic failure, permission and scope preflight, retry-gaps, and output writing including empty and zero-row datasets.
- **AC-10.2:** No test makes a live network call; all API interaction is faked at the transport layer.
- **AC-10.3:** Tests assert on outgoing requests — headers, query shape, pagination arguments — not only on parsed responses.
- **AC-10.4:** Rate-limit, backoff, and wait behavior is tested without real waiting.
- **AC-10.5:** CI runs linting, strict type checking, and the full test suite on every push to the default branch and every pull request.
- **AC-10.6:** CI fails on any lint error, type error, or test failure.
- **AC-10.7:** Third-party CI actions are pinned to full commit SHAs with the version in a trailing comment.
- **AC-10.8:** A tag-triggered release workflow runs the full quality gate, then builds the distribution, generates a CycloneDX SBOM, and attaches both to a GitHub Release.
- **AC-10.9:** The release fails if any quality-gate step fails or if an expected build artifact is missing.

### US-11: Close the gaps in an existing snapshot
As an auditor who has granted a missing permission or waited out a transient failure, I want to retry only what failed so that I don't spend a whole budget window re-downloading what already succeeded.

**Acceptance Criteria:**
- **AC-11.1:** The user can re-attempt only the gapped resources of an existing completed snapshot.
- **AC-11.2:** Resources that now succeed are written into that snapshot and their gaps cleared; those that fail again keep their gaps, updated with the new reason and timestamp.
- **AC-11.3:** The manifest records that a retry occurred and when, and the snapshot's completion status is recalculated.
- **AC-11.4:** Retry-gaps re-runs finalization so Parquet output and counts stay consistent with the NDJSON.
- **AC-11.5:** Retry-gaps against a snapshot with no gaps is a reported no-op, not an error.

## Functional Requirements

### FR-1: Datasets and tiers
- The tool shall define two tiers. A dataset is **optional** if it is high-volume relative to its audit value, needs permissions beyond a standard read-only App, or is only reachable via REST. Everything else is **default**.
- **Default tier, organization level:** `organization` (settings and metadata scalars), `members`, `pending_members`, `teams`, `team_members`, `team_repositories`, `repositories`, `org_rulesets`, `org_custom_properties`, `org_domains`, `org_ip_allow_list`.
- **Default tier, repository level:** `issues`, `pull_requests`, `discussions`, `releases`, `labels`, `milestones`, `collaborators`, `branch_protection_rules`, `repo_rulesets`, `repo_custom_property_values`, `environments`, `deployments`, `vulnerability_alerts`, `topics`, `languages`.
- **Optional tier:** `workflow_runs`, `check_suites`, `packages`, `projects_v2`, `forks`, `stargazers`, `watchers`, `dependency_graph_manifests`, `deploy_keys`, `audit_log` (elevated permission), `org_webhooks` (REST).
- Issues, pull requests, and discussions in the default tier shall be collected as records in their own right — title, state, author, timestamps, labels, assignees, milestone — without their comment, review, reaction, or timeline sub-collections.
- Each dataset shall have a declared, versioned field list published in the repository documentation. Absent a specific reason to differ, a dataset's field list shall be every non-connection scalar field of its GraphQL type, excluding `viewer*` fields (which describe the caller, not the org) and sponsorship or monetary fields.
- Selecting datasets shall both narrow within the default tier and opt into the optional tier. Unknown names and empty resolved selections shall be rejected before any network call.
- Archived, disabled, empty, and forked repositories shall be included by default and shall be filterable out. An expected empty result from a disabled or empty repository shall be recorded as a zero count, not a gap.

### FR-2: Authentication
- The tool shall accept either a PEM private key path plus a client ID, or a pre-minted installation token. Exactly one form shall be supplied.
- With a private key, the tool shall generate a short-lived App JWT, discover the installation for the target org, and exchange the JWT for an installation token, refreshing it before expiry so runs may exceed one hour.
- With a pre-minted token, expiry shall end the run cleanly with resumable state and a message explaining that a private key enables refresh.
- Credential material shall be validated before any data request; failures shall distinguish missing, unreadable, malformed, passphrase-protected, and mismatched-client-ID cases.
- A token refresh that fails mid-run — installation suspended, uninstalled, or key revoked — shall stop the run cleanly and resumably, distinguishably from a transient network failure.
- The CLI shall accept credentials through documented environment variables or explicit arguments, and shall warn when a secret is passed as an argument. The private key shall be supplied as a file path, never inline.
- The API base host shall be configurable and shall default to github.com; JWT and token-minting endpoints shall follow the configured host.
- Credentials shall never appear in output, logs, manifests, or error text.

### FR-3: Data retrieval
- Data shall be retrieved via GraphQL wherever GraphQL exposes it; REST shall be used only for authentication and for datasets GraphQL cannot reach. The manifest shall record which datasets came from REST.
- Collections shall be paginated by cursor until exhausted, with a stable, explicitly requested sort order.
- Records shall be deduplicated on their identifier as they are written, so that resources shifting between pages during a crawl neither duplicate nor corrupt the output.
- Page sizes shall respect API maxima and shall be reducible for wide queries to stay under the node ceiling.

### FR-4: Rate limiting and pacing
- Pacing shall be driven by the live remaining budget reported by the API, not a hardcoded limit.
- Concurrency shall be bounded below GitHub's concurrent-request limit; per-minute secondary limits shall be respected, and a signalled secondary limit shall trigger backoff and reduced concurrency.
- On budget exhaustion the tool shall wait until the reported reset, reporting the wait and its end. It shall not begin a wait that outlasts a non-refreshable credential, and total waiting shall be bounded by a user-configurable ceiling.
- A user-supplied consumption cap shall act as a floor on remaining points that the tool will not cross.
- REST requests shall be paced against their own budget and accounted separately.
- Retries shall use exponential backoff with jitter, bounded in count. Transient failures — network errors, timeouts, 429, 5xx, unparseable response bodies — shall be retried; permanent ones shall not.
- Every request shall carry an identifying user agent, and REST requests shall pin an explicit API version.
- Numeric options out of valid range, including a consumption floor that no single query could satisfy, shall be rejected before any network call.

### FR-5: Partial failure and systemic failure
- A response containing both data and errors shall have its data written and each error recorded as a gap, carrying the error's field path when supplied.
- A request yielding no usable response after exhausting retries shall be recorded as a gap.
- Gaps shall not abort the run; remaining datasets shall still be attempted.
- A failure rate or consecutive-failure count above a configurable threshold shall be treated as systemic: the run shall stop cleanly with resumable state, and shall not finalize, rather than accumulating gaps against an outage.
- A query rejected because a requested field no longer exists in the schema shall be reported as a tool-level fault requiring an upgrade, not as per-resource gaps.
- Gaps shall be written into the snapshot manifest and summarized on the console.

### FR-6: Checkpointing and resume
- Progress shall be checkpointed continuously at page granularity, recording per-dataset status, per-collection cursors, and the run's original dataset selection and repository filter.
- Checkpoint state shall live with the snapshot it belongs to and shall record the tool version that wrote it.
- Re-running shall resume the newest incomplete snapshot for that org in that root; a specific snapshot may be named instead; a forced-fresh flag shall ignore existing incomplete snapshots.
- Resume shall be refused, with an explanation, when the dataset selection, repository filter, or org differs; when checkpoint state is unreadable or version-incompatible; and when the snapshot is older than a configurable staleness window.
- Resume shall survive abrupt termination, losing at most the in-flight page.
- User interruption shall complete the in-flight page, flush state, print the resume command, and exit with the interrupt status; a second interrupt shall stop immediately.

### FR-7: Preflight
- Before fetching, the tool shall determine the permissions the active token carries and whether the installation is scoped to selected repositories.
- It shall warn for each selected dataset expected to fail or degrade, naming the missing permission, and shall report an estimate of repository count, projected point cost, and projected duration.
- Warnings shall not block by default; an opt-in flag shall make them fatal.
- Preflight shall be runnable standalone, printing a per-dataset ready / degraded / blocked verdict and exiting non-zero when any selected dataset is blocked.

### FR-8: Output, identity, and manifests
- Records shall be appended during the run as newline-delimited UTF-8 JSON, one file per dataset, with GitHub's own field values preserved verbatim.
- Every record shall carry its GraphQL node identifier as its canonical key, plus its numeric database identifier where the type provides one. Child records shall carry their parent's identifier — repository-level records carry the repository id, `team_members` and `team_repositories` carry the team id.
- Each dataset shall have a declared column schema derived from its field list, so Parquet output is identical across runs and valid for zero-row datasets. Nested objects and lists shall be preserved as nested types; a value that cannot be represented shall be stored JSON-encoded.
- On success each NDJSON file shall be converted to Parquet; intermediate NDJSON shall be removed unless the user opts to keep it. Conversion failure shall be recorded as a gap and shall discard no data.
- Finalization shall be independently re-runnable against a complete snapshot.
- Each snapshot shall carry a manifest recording org, API host, tool version, UTC start and completion times, dataset selection, per-dataset counts, gaps, scope restrictions, consumption statistics, retry history, and completion status. A snapshot lacking a manifest shall be treated as incomplete.
- A root index shall list snapshots per org and identify the most recent one that completed with no gaps and no scope restriction.
- All tool-generated timestamps shall be UTC ISO-8601, with a filesystem-safe compact form for directory names.
- Snapshot layout shall be `<root>/<org-login-lowercased>/<utc-timestamp>/`, with the root defaulting to `./snapshots` and user-overridable. One root may hold many orgs.

### FR-9: Concurrent-run safety
- A run shall claim the org within the root for its duration and refuse to start when another run holds it.
- The claim shall carry a liveness signal so a claim left by a terminated process is detected as stale, reclaimed automatically with a warning, and overridable explicitly.
- Claims shall be scoped per org, so runs against different orgs sharing a root do not block each other.

### FR-10: Progress and exit statuses
- During a run the tool shall report progress at dataset and repository granularity, including during rate-limit waits, with quiet and verbose controls and output suited to a non-interactive log.
- The CLI shall use distinct exit statuses: success; completed with gaps or scope restriction; stopped but resumable; invalid usage or configuration; authentication or authorization failure; concurrent-run refusal; preflight blocked; unexpected failure; and user interrupt. Each shall be documented.

### FR-11: Interfaces
- The tool shall ship a console entry point and an importable async API, with public types re-exported from the package root.
- The library shall accept credentials as explicit parameters and shall not read the environment itself.
- Programmatic runs shall return a structured result with counts, gaps, and consumption statistics, and shall support progress observation during the run.
- Library failures shall be raised as one documented exception type.

### FR-12: Retry-gaps
- The tool shall support re-attempting only the gapped resources of an existing snapshot, writing newly successful results into it, clearing resolved gaps, updating unresolved ones, recording the retry in the manifest, recalculating completion status, and re-running finalization.
- Retry against a snapshot with no gaps shall be a reported no-op.

### FR-13: Documentation
- The README shall cover: creating and installing the GitHub App; the permissions each dataset requires; both credential forms and the environment variables for CI; dataset tiers and selection; resume, retry-gaps, and force-fresh; the snapshot layout, manifest format, and gap representation; the exit-status table; configuring a non-github.com host; and the caveat that resuming across CI jobs requires the snapshot directory to be cached or restored between them.

## Scope

### In Scope
- The default and optional dataset tiers enumerated in FR-1.
- GitHub App installation authentication with automatic refresh, pre-minted token support, and a configurable API base host.
- Self-pacing against live rate-limit figures, including secondary limits and a separate REST budget.
- Continuous checkpointing, automatic and explicit resume, and forced-fresh runs.
- Explicit gap recording, systemic-failure detection, and the retry-gaps operation.
- Repository-subset, archived, and fork filters, and per-collection caps.
- Permission and repository-scope preflight with cost estimation, inline and standalone.
- NDJSON during the run, declared-schema Parquet on completion, a per-snapshot manifest, and a root index.
- Concurrent-run safety with stale-claim recovery.
- CLI and importable library; documented exit statuses; progress reporting.
- The README deliverable in FR-13.
- Comprehensive offline tests and CI running lint, strict type checking, and tests.
- A tag-triggered release workflow producing wheels and a CycloneDX SBOM attached to a GitHub Release.

### Out of Scope
- **Issue and pull request conversation threads** — comments, reviews, reactions, and timeline items. Excluded entirely, not behind a flag: nesting these multiplies node counts past what the point budget supports at org scale.
- **Commit history and git content.** No commit metadata, no cloning, no blobs, no file trees.
- **Incremental or differential downloads.** Every run is a full snapshot. GraphQL offers no ETags, and `updatedAt`/`pushedAt` filtering silently misses deletions.
- **Repository traffic (views and clones).** REST-only with 14-day retention, so a one-shot snapshot is inherently lossy; it needs scheduled collection, a different feature.
- **Writes to organization data.** Read-only; no mutations. Minting an installation token is an authentication call, not a write.
- **Multi-organization and enterprise-wide crawls.** One org per run.
- **Analysis, diffing, reporting, or visualization** of downloaded data.
- **Snapshot retention and pruning.** Snapshots accumulate; pruning is the user's responsibility. The tool lists what exists but does not delete.
- **Publishing to PyPI.** Releases attach wheels to a GitHub Release only, matching every sibling project.
- **Coverage gates and a Python version matrix.** Not selected during requirements gathering.

## Edge Cases

### EC-1: Organization has no repositories
- **Scenario:** Target org exists but is empty.
- **Expected Behavior:** Run completes successfully. Org and member datasets are written; repository-dependent datasets produce zero-record files that still carry their declared Parquet schema. Exit zero, no gaps.

### EC-2: Organization does not exist or the App is not installed
- **Scenario:** Wrong org login, or the App has no installation there.
- **Expected Behavior:** Fail before any dataset work, distinguishing "no such organization" from "App not installed on this organization" and naming the remedy. Nothing is written.

### EC-3: Installation is scoped to selected repositories
- **Scenario:** The App is installed on 40 of the org's 900 repositories.
- **Expected Behavior:** Preflight reports that the installation reaches 40 of 900. The run proceeds, the manifest records the restriction and the reachable set, and the snapshot is marked scope-restricted so it is never presented as a complete org snapshot. Fail-fast mode refuses to start.

### EC-4: Very large organization exceeds the hourly budget
- **Scenario:** A 5,000-repository org needs far more points than the 12,500/hour ceiling.
- **Expected Behavior:** The run continues across multiple rate-limit windows, waiting for resets, refreshing the token, and checkpointing throughout, until it completes or hits the configured wait ceiling.

### EC-5: Token expires mid-run
- **Scenario:** The one-hour installation token lapses during a long crawl.
- **Expected Behavior:** With a private key, a new token is minted transparently. With a pre-minted token, the run stops cleanly and resumably. If a rate-limit wait would outlast a non-refreshable token, the tool stops before waiting rather than sleeping into a certain failure.

### EC-6: Token refresh fails mid-run
- **Scenario:** An admin uninstalls or suspends the App, or the key is revoked, during a run.
- **Expected Behavior:** Stop cleanly with resumable state and an authorization status distinguishable from a transient network failure, naming the likely cause.

### EC-7: Single repository is inaccessible
- **Scenario:** One repo returns not-found or a permission error while the rest are readable.
- **Expected Behavior:** That repo is recorded as a gap naming the reason; every other repo downloads normally. The run finishes and exits with the gaps status.

### EC-8: GitHub outage — everything fails
- **Scenario:** The API is down partway through a 5,000-repo crawl.
- **Expected Behavior:** The systemic-failure threshold trips. The run stops cleanly with resumable state rather than recording thousands of gaps and finalizing a near-empty snapshot as complete.

### EC-9: Response is not GraphQL at all
- **Scenario:** An HTML maintenance page, a proxy interstitial, or a truncated body arrives instead of JSON.
- **Expected Behavior:** Treated as a transient failure and retried; on exhaustion, recorded as a gap against the page being fetched. It does not crash the run or corrupt output.

### EC-10: Requested field no longer exists in the schema
- **Scenario:** GitHub retires a field the tool queries, so the query fails validation with no data on every page.
- **Expected Behavior:** Recognized as a tool-level fault. One clear message that the tool's queries are out of date and must be upgraded — not thousands of per-resource gaps.

### EC-11: Process killed mid-write
- **Scenario:** SIGKILL, reboot, or a full disk during a page write.
- **Expected Behavior:** Existing output stays valid and parseable. A partially written trailing record is discarded on resume. At most the in-flight page is lost.

### EC-12: Stale claim from a killed run
- **Scenario:** A run is SIGKILLed; the user immediately re-runs to resume.
- **Expected Behavior:** The claim's liveness signal shows it is stale. The tool reclaims it with a warning and proceeds with the resume, rather than refusing forever as though a run were still active.

### EC-13: Two runs against the same org
- **Scenario:** Two processes target the same org and root simultaneously.
- **Expected Behavior:** The second is refused with a clear message and its own exit status. A concurrent run against a *different* org in the same root proceeds normally.

### EC-14: Disk fills during a run
- **Scenario:** No space remains partway through.
- **Expected Behavior:** Stop with a clear out-of-space message, preserving checkpoint state so the run resumes after space is freed. Do not leave a snapshot that looks complete.

### EC-15: Secondary rate limit triggered
- **Scenario:** GitHub signals a secondary limit despite pacing.
- **Expected Behavior:** Back off with jitter, reduce concurrency, and retry. Repeated triggering surfaces a warning that concurrency may be too aggressive.

### EC-16: Node limit exceeded on a query
- **Scenario:** A wide query against a repository with very large collections exceeds the per-query node ceiling.
- **Expected Behavior:** Detect, retry at a smaller page size, continue. Only failure at the minimum page size becomes a gap.

### EC-17: Resources change during the crawl
- **Scenario:** Repositories are created, deleted, or renamed while pagination is in progress, or between a run and its resume.
- **Expected Behavior:** Stable sort order and identifier-based deduplication prevent duplicated or corrupted records. Deleted resources yield gaps, not failures. The manifest records start and completion times so consumers know the snapshot spans an interval rather than an instant.

### EC-18: One repository dwarfs the budget
- **Scenario:** A single repository holds 50,000 issues.
- **Expected Behavior:** The per-collection cap, when set, bounds it. When uncapped, the run reports that one resource is consuming a disproportionate share so the user can intervene.

### EC-19: Malformed or unexpected response content
- **Scenario:** A field promised by the schema arrives null, or a record's shape is unexpected.
- **Expected Behavior:** Do not crash. Write what is present, record the anomaly as a gap with its field path when the API supplies one, and continue.

### EC-20: Dataset selected that the App cannot read
- **Scenario:** The user requests the audit log without the required permission.
- **Expected Behavior:** Preflight warns before fetching. By default the run proceeds and records the dataset as a gap; fail-fast stops before spending budget.

### EC-21: Corrupt or version-incompatible checkpoint
- **Scenario:** The checkpoint was truncated by a kill or disk-full, or was written by an incompatible version.
- **Expected Behavior:** Refuse to resume, explain why, and direct the user to start fresh — rather than silently restarting a dataset and duplicating records.

### EC-22: Run dies during finalization
- **Scenario:** NDJSON is complete but conversion or the manifest write is interrupted.
- **Expected Behavior:** The snapshot has no manifest and is therefore treated as incomplete. Finalization is re-runnable on its own without re-downloading.

### EC-23: Clock skew on the host
- **Scenario:** The host clock is off by minutes, so generated JWTs are rejected.
- **Expected Behavior:** Surface the authentication failure with clock skew named as the likely cause, since the raw API error is opaque about it.

### EC-24: Unicode and case in identifiers
- **Scenario:** Team and repository names contain emoji, RTL text, or control characters; the org login is given as `MyOrg` rather than `myorg`.
- **Expected Behavior:** Content is preserved verbatim in UTF-8. No output path derives from a resource name, so no collision is possible. The org login is case-folded for snapshot lookup, so `MyOrg` and `myorg` resolve to the same snapshots.

## Non-Functional Hints

- **Performance:** Throughput is bounded by GitHub's rate limits, not local compute; success means keeping the budget saturated without exceeding it. The test suite should stay fast enough to run on every save — no real sleeping.
- **Security:** Read-only against organization data. Private keys and tokens must never be logged, persisted, or included in output, and secrets should reach the CLI through the environment rather than arguments. Requesting the narrowest workable permission set is preferred. Snapshots may contain private organizational data and the docs should say so.
- **Scalability:** The design target is an org with thousands of repositories, where a run spans multiple rate-limit windows and token lifetimes. Memory use should not grow with org size — records stream to disk rather than accumulating.
- **Reliability:** Correctness of the gap record matters more than completeness of the data. A snapshot that knows what it is missing is useful; one that silently omits data is not. This is why an installation scoped to selected repositories must be surfaced rather than quietly producing a smaller snapshot.
- **Accessibility:** Not applicable — no UI.

## Dependencies

- **GitHub GraphQL API** — primary data source. Constraints verified in `exploration.md`: 5,000 points/hour per installation scaling to a 12,500 cap, 500,000-node per-query limit, page sizes 1–100, 100 concurrent requests, 2,000 points/minute, and no ETag support.
- **GitHub REST API** — installation discovery, token minting, and the datasets GraphQL cannot reach.
- **A registered GitHub App** — the user must create one, grant it read permissions, install it on the target org, and hold its private key or a minted token. A user-supplied prerequisite the tool cannot provide.
- **Python 3.12+**, with `uv` for environment and dependency management.
- **`features/org-harvest/exploration.md`** — the source of the API constraints, verified GraphQL field lists, and house conventions this spec assumes.

## Open Questions

None. All questions raised during requirements gathering and the completeness check have been resolved. The CI/CD question was closed at approval by bringing the tag-triggered release workflow and SBOM into scope; coverage gates and a Python version matrix remain deliberately excluded.

## Screenshots

Not applicable — this feature has no UI.
