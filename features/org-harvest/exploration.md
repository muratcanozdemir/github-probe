# Codebase Exploration: org-harvest

**Generated:** 2026-08-27 21:57
**Feature:** A Python tool that queries the GitHub GraphQL API using GitHub App installation tokens and downloads everything knowable about an organization, with heavy test coverage and CI/CD.

> **Scope note:** `/home/murat/github-probe` was an empty, non-git directory at exploration time.
> There is no existing codebase in this repo to explore. Exploration therefore covered two
> substitute sources: (1) the author's five sibling Python projects on this machine, which
> establish firm house conventions this repo should inherit, and (2) the GitHub GraphQL/REST
> API surface itself, which is the real "existing system" this feature integrates with and
> whose constraints will drive the spec. API facts below were verified against the published
> GraphQL schema (`docs.github.com/public/fpt/schema.docs.graphql`, 1.5 MB, fetched today) and
> current GitHub docs — not recalled from memory.

---

## Similar Features

No prior art in this repo. Five sibling Python projects were examined:
`~/lattice`, `~/pr-drift`, `~/codegraph-lsp`, `~/notecast-py`, `~/s3-vectors-at-home`.

### `~/lattice` — the closest structural analogue and the most mature project

`lattice` is a rate-limited async HTTP client wrapping a paginated/metered third-party API,
with snapshot persistence. That is structurally the same problem as this feature. What it does:

- **`src/lattice/client.py`** — a single `LLMClient` class over `httpx.AsyncClient`, configured
  by a frozen-ish `@dataclass ClientConfig` (base_url, api_key, rpm, tpm, max_concurrency,
  timeout_seconds, max_retries, backoff_base_seconds). Client is an async context manager
  (`__aenter__`/`__aexit__`/`aclose`).
- **Two independent `_TokenBucket` rate limiters** (requests/min and tokens/min) rather than one
  combined limiter — the module docstring explains this is because the provider enforces both
  independently and hitting either returns 429. Buckets refill continuously off `time.monotonic()`
  and guard with an `asyncio.Lock`.
- **A deliberate anti-deadlock escape hatch**: if a single request's cost exceeds total bucket
  capacity, `acquire()` returns immediately rather than waiting forever. There is a test for this.
- **Reservation-then-reconcile accounting**: cost is estimated and reserved before the call, then
  `bucket.adjust(actual - reserved)` corrects it after the response lands. The docstring is explicit
  that this is approximate and converges over a session — "it is not exact, and is not meant to be."
- **`_request_with_retry`** — `RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})`.
  Non-retryable 4xx fails immediately with the comment "retrying a 400 burns the budget for nothing."
  Backoff is `base * 2**attempt` plus up to 10% jitter. Exhaustion raises a single domain
  exception `LLMError` chained with `from last_exc`.
- **`src/lattice/snapshot.py`** — persistence pattern: one directory per table,
  `{root}/{table}/{table}_{timestamp}.parquet` plus a `manifest.json` with `{latest, snapshots}`.
  Rollback = repointing the manifest. The docstring names what it does *not* do: "Concurrent
  writers to the SAME table will race on manifest.json (last write wins, no locking) — this is
  fine for the single-process pipeline-run usage this is meant for."
- **`src/lattice/__init__.py`** — flat re-export of every public symbol with an explicit `__all__`.
  Callers import from the package root (`from lattice import ClientConfig, LLMClient`), never from
  submodules.

### `~/pr-drift/src/pr_drift/github.py` — the only existing GitHub API code

Twelve lines. `fetch_pr_diff(owner, repo, pr_number, token)` using `urllib.request`, with an
explicit `timeout=10`. Notable convention even at this size: **the token is a required function
parameter, never read from the environment inside the fetching code.** Its test asserts on the
constructed request (URL, `Authorization: Bearer`, `Accept` header, timeout) rather than on live
behaviour. There is no GraphQL, pagination, App-auth, or retry code anywhere in the sibling repos —
all of that is new ground for this feature.

### Conventions common to all five projects

| Convention | Value |
|---|---|
| Layout | `src/<package>/` with `tests/` as a sibling — 4 of 5 projects |
| Build backend | `hatchling` — 4 of 5 (`pr-drift` uses setuptools; it is the oldest) |
| Env / installer | `uv` (`uv sync --group dev`, `uv run`, `uv build`) |
| Dev deps | PEP 735 `[dependency-groups] dev = [...]`, not `[project.optional-dependencies]` |
| Test runner | `pytest`, with `[tool.pytest.ini_options] testpaths = ["tests"]` |
| Linter | `ruff`, `line-length = 100` |
| HTTP | `httpx` (`lattice`, `s3-vectors-at-home`) |
| Models | `pydantic>=2` where structured data exists (`lattice`) |
| CLI | `[project.scripts]` console entry point — `codegraph`, `notecast`, `s3-vectors-cli` |
| Naming | Public API is plain nouns/verbs; private helpers are `_`-prefixed module-level functions |

Docstring style across the codebase is distinctive and worth matching: module docstrings explain
**why a design choice was made and what it deliberately does not do**, not what the code is. The
comment density inside functions is low — comments appear only at genuinely non-obvious decisions.

---

## GitHub GraphQL API — the organization data surface

Field names below are verified verbatim against the published schema.

### `Organization` — connections and fields that carry data worth downloading

Bulk connections (each paginated, each a cost centre):
`repositories`, `membersWithRole`, `pendingMembers`, `teams`, `projectsV2`, `projects`,
`recentProjects`, `packages`, `auditLog`, `ipAllowListEntries`, `domains`, `rulesets`,
`repositoryMigrations`, `repositoryCustomProperties`, `repositoryDiscussions`,
`repositoryDiscussionComments`, `issueTypes`, `issueFields`, `mannequins`, `memberStatuses`,
`enterpriseOwners`, `pinnedItems`, `pinnableItems`, `innersourceVulnerabilities`.

Scalar/settings fields (one cheap query gets all of them):
`login`, `name`, `databaseId`, `id`, `description`, `descriptionHTML`, `email`, `location`,
`websiteUrl`, `twitterUsername`, `avatarUrl`, `isVerified`, `createdAt`, `updatedAt`,
`archivedAt`, `organizationBillingEmail`, `requiresTwoFactorAuthentication`,
`membersCanForkPrivateRepositories`, `webCommitSignoffRequired`, `ipAllowListEnabledSetting`,
`ipAllowListForInstalledAppsEnabledSetting`, `notificationDeliveryRestrictionEnabledSetting`,
`samlIdentityProvider`, `interactionAbility`, `announcementBanner`.

Sponsors-related fields exist too (`sponsors`, `sponsorsListing`, `sponsorshipsAsMaintainer`,
`sponsorshipsAsSponsor`, `lifetimeReceivedSponsorshipValues`, and several `*InCents` money fields)
— present in the schema but likely empty for most orgs.

There is also a large family of `viewer*` fields (`viewerCanAdminister`, `viewerIsAMember`,
`viewerCanCreateRepositories`, …). These describe *the caller's* permissions, not the org, and
their values will differ between a user token and an installation token.

### `Repository` — the deepest and most expensive subtree

The `Repository` type exposes ~150 fields. The paginated connections are what make a full
download expensive: `issues`, `pullRequests`, `discussions`, `releases`, `refs`, `commitComments`,
`collaborators`, `assignableUsers`, `mentionableUsers`, `labels`, `milestones`, `deployments`,
`environments`, `deployKeys`, `branchProtectionRules`, `rulesets`, `packages`, `forks`,
`stargazers`, `watchers`, `submodules`, `languages`, `repositoryTopics`, `vulnerabilityAlerts`,
`dependencyGraphManifests`, `projectsV2`, `pinnedDiscussions`, `issueTemplates`,
`pullRequestTemplates`, `repositoryCustomPropertyValues`, `suggestedActors`, `codeowners`.

Metadata/settings fields include `nameWithOwner`, `visibility`, `isPrivate`, `isArchived`,
`isFork`, `isTemplate`, `isEmpty`, `isDisabled`, `isLocked`, `isMirror`, `diskUsage`,
`forkCount`, `stargazerCount`, `pushedAt`, `createdAt`, `updatedAt`, `licenseInfo`,
`primaryLanguage`, `defaultBranchRef`, `parent`, `templateRepository`, `mergeQueue`,
`codeOfConduct`, `fundingLinks`, `contactLinks`, `securityPolicyUrl`, and the full merge-strategy
group (`mergeCommitAllowed`, `squashMergeAllowed`, `rebaseMergeAllowed`,
`deleteBranchOnMerge`, `mergeCommitTitle`/`Message`, `squashMergeCommitTitle`/`Message`).

**Boundary — this is where "everything" gets dangerous.** `issues` and `pullRequests` each nest
their own connections (`comments`, `timelineItems`, `reviews`, `commits`, `labels`, `assignees`,
`reactions`). Nesting connections multiplies node count, and multiplication is what trips the
node limit and the point cost, not depth per se.

### `Team`

`members`, `repositories`, `childTeams`, `invitations`, `memberStatuses`, `ancestors`,
`parentTeam`, plus `name`, `slug`, `combinedSlug`, `description`, `privacy`, `notificationSetting`,
`avatarUrl`, `databaseId`, `createdAt`, `updatedAt`, and the review-delegation group
(`reviewRequestDelegationEnabled`, `reviewRequestDelegationAlgorithm`,
`reviewRequestDelegationMemberCount`, `reviewRequestDelegationNotifyTeam`).

### Types confirmed present in GraphQL

`WorkflowRun`, `CheckSuite`, `RepositoryVulnerabilityAlert`, `SecurityAdvisory`, `PackageVersion`,
`Commit`, `DependencyGraphManifest` all exist as GraphQL types — so Actions runs, check suites,
Dependabot alerts, packages, commit history, and the dependency graph are reachable without
falling back to REST.

### What GraphQL does *not* cover (REST-only)

A tool claiming "everything" will need a REST path for at least:

- **Git events** — GitHub's docs state plainly that Git events cannot be retrieved via GraphQL.
- **Repository traffic** — views and clones. REST only, and retained **only 14 days**, which makes
  it the one dataset where a single download is inherently lossy and repeat scheduled runs are the
  only way to get history.
- Organization webhooks, org/repo secrets and variables, billing and Actions usage, and
  Copilot seat assignments are REST-side concerns not represented in the org GraphQL subtree.

**Audit log caveat:** `Organization.auditLog` *does* exist in GraphQL (verified in the schema; it
takes standard `first`/`last`/`after`/`before` plus an ordering argument). But retention is only
**90–120 days**, it requires elevated org-admin permission, and GitHub has separately rate-limited
audit-log queries because of load on their data stores. Treat it as a privileged, best-effort,
possibly-unavailable dataset rather than a core one.

---

## Authentication — GitHub App installation tokens

Two-step flow, both steps REST (there is no GraphQL path to minting a token):

**Step 1 — App JWT.**
- Algorithm **must** be `RS256`.
- `iat` — set **60 seconds in the past** to absorb clock drift.
- `exp` — no more than **10 minutes** in the future.
- `iss` — **the client ID**. GitHub's docs now explicitly recommend the client ID over the numeric
  App ID.
- GitHub's own documented Python example uses **PyJWT** with a PEM private key file.
- Server clock accuracy matters; the docs recommend NTP.

**Step 2 — installation access token.**
- `POST /app/installations/{installation_id}/access_tokens`, App JWT in `Authorization: Bearer`.
- Response carries the token, its expiry, its permissions, and the repositories it can reach.
- **Token lifetime is 1 hour.** A full download of a large org will outlive a single token, so
  refresh is a functional requirement, not an optimisation.
- Optional scoping in the request body: `repositories` or `repository_ids` (**max 500**), and
  `permissions` to request a subset of what the App holds.
- Without those parameters the token inherits all of the App's permissions and repo access.

**Finding the installation id:** `GET /orgs/{org}/installation` is the direct route for this
feature's use case. `GET /app/installations` lists all; webhook payloads also carry it.

**Permissions.** GitHub App permissions are not OAuth scopes — they are set at App registration
and classified as repository, organization, enterprise, or account permissions. The "Members"
organization permission governs member/team access. **An under-permissioned GraphQL query returns
401, not a partial result** — so permission gaps surface as hard failures on whole sub-queries.
The docs give no single authoritative permission→GraphQL-field table; GitHub's own advice is to
test the App against the queries you intend to run.

---

## Rate limits, node limits, and query cost

**Primary limit (points/hour), by auth method:**

| Caller | Limit |
|---|---|
| Standard GitHub App installation | **5,000 points/hour per installation** |
| GHEC App installation | 10,000 points/hour per installation |
| User (PAT) | 5,000 (10,000 on GHEC) |
| Actions `GITHUB_TOKEN` | 1,000/hour per repository |

**Installation scaling — directly relevant, since this feature targets whole orgs:** a standard
installation earns **+50 points/hour per repository beyond 20 repositories**, and **+50 points/hour
per user beyond 20 users**, capped at **12,500 points/hour**. Bigger orgs raise the ceiling, but the
cap arrives well before the workload does — a 5,000-repo org gets the same 12,500 as a 300-repo one.

**Node limit:** a single query may not exceed **500,000 total nodes**. `first`/`last` must be within
**1–100**, and GitHub advises requesting fewer than 100 when a query touches a lot of data.

**Cost formula:** sum the requests needed for each unique connection *assuming the maximum
`first`/`last` value*, divide by 100, round. **Minimum 1 point per call.** Cost is charged on the
declared page size, not on rows actually returned — asking for 100 and getting 3 costs the same as
asking for 100 and getting 100.

**Secondary limits (separate from points/hour, and easy to miss):**
- No more than **100 concurrent requests**.
- No more than **2,000 points/minute** at the GraphQL endpoint, where a query counts 1 point and a
  mutation counts 5. This is a *different* point currency from the hourly budget.

**Self-measurement:** the `rateLimit` object returns `cost`, `limit`, `remaining`, `used`,
`resetAt`, and `nodeCount` — so a query can report its own price and the caller can steer on live
numbers instead of estimates. `nodeCount` in particular gives feedback against the 500,000 ceiling.

**No ETags.** GraphQL does not support ETags, so conditional requests cannot be used to make
re-downloads cheap. Any incremental strategy has to be built on `updatedAt`/`pushedAt` filtering
and client-side caching keyed by query + variable hash.

---

## Pagination and partial-failure behaviour

- Cursor-based (`pageInfo { hasNextPage, endCursor }`, `after`/`before`). Cursors are opaque;
  GitHub's guidance is to keep them opaque and to sort consistently (e.g. by creation date) so
  pages stay stable.
- Max page size 100, and GitHub explicitly advises smaller pages for wide queries — meaning the
  page size is a tuning knob traded against node count, not a constant.
- **GraphQL returns HTTP 200 with a partial `data` object plus an `errors` array.** A single
  inaccessible repository, or one field the installation lacks permission for, can null out part of
  a response while the rest succeeds. This is the single most important behavioural difference from
  the REST client in `pr-drift`, where the retry/error model keys off status codes alone: here,
  status code 200 does not mean success.
- `RATE_LIMITED` and `NOT_FOUND` arrive inside that `errors` array rather than as HTTP statuses.
  `NOT_FOUND` is also how GraphQL reports "exists but you can't see it," so it is not reliably
  distinguishable from genuine absence.
- Recommended backoff per GitHub's guidance: throttle proactively, then exponential backoff **with
  jitter**, and on a primary-limit hit wait until the advertised reset time rather than retrying
  blind. `lattice` already implements exactly this shape (`base * 2**attempt` + 10% jitter).

---

## Testing patterns

`~/lattice/tests/test_client.py` is the reference. 13 tests, all `pytest.mark.asyncio`, all using
**`respx`** to mock at the httpx transport layer. The `pyproject.toml` carries an inline comment
stating the intent: *"mock httpx at the transport layer, no live API calls in CI."*

Techniques used there that transfer directly:

- `respx.mock(base_url=...)` as a context manager; `route.side_effect = [Response(429), Response(200)]`
  to script a retry sequence, then assert `route.call_count == 2`.
- A module-level `_completion_response()` factory builds valid payloads so each test overrides only
  the one field it cares about.
- **Header and payload assertions via a handler closure** that captures `request.headers` /
  `request.content` into a list, then asserts on it after the block exits — this is how auth
  headers are verified without a live call.
- Malformed-response tests are first-class: `test_handles_null_usage_in_response` and
  `test_handles_none_content_in_input_message` both feed nulls where the schema promised objects.
- Concurrency is tested behaviourally: a handler increments/decrements an in-flight counter and the
  test asserts `max_seen <= 2`.
- Rate limiting is tested without real waiting: start a task, `await asyncio.sleep(0.05)`, assert
  `not task.done()`, then cancel and assert `CancelledError`. Retry tests set
  `backoff_base_seconds=0.01` to keep the suite fast.

`~/lattice` also configures `asyncio_mode = "auto"` in `[tool.pytest.ini_options]`.
`~/pr-drift` uses stdlib `unittest` with `MagicMock` instead — the older style; `lattice`'s
pytest+respx approach is the newer and better-developed one.

---

## CI/CD patterns

Every sibling project has `.github/workflows/ci.yml`; four of five also have `release.yml`.
Two tiers of maturity exist, and **`~/lattice` is clearly the intended standard**:

**`lattice/ci.yml` (the mature form):**
- `on: push: branches: [main]` + `pull_request`.
- `permissions: contents: read` declared at workflow level.
- `concurrency: group: ci-${{ github.workflow }}-${{ github.ref }}`, `cancel-in-progress: true`.
- **All third-party actions pinned to full commit SHAs with the version in a trailing comment** —
  e.g. `actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0`.
- Steps: install `uv` (`astral-sh/setup-uv`, `enable-cache: true`) → `uv python install` →
  `uv sync --group dev` → **`uv run mypy --strict src/lattice`** → `uv run pytest -v` →
  `uv run black --check`.
- Single job, `ubuntu-latest`, **no matrix** — none of the five projects test across Python versions
  or OSes.

**`lattice/release.yml`** — tag-triggered (`v*`), three dependent jobs: `build` (`uv build`, upload
wheel artifact) → `sbom` (**CycloneDX SBOM** via `uvx --from cyclonedx-bom==7.* cyclonedx-py
environment .venv --spec-version 1.6`, uploaded as an artifact) → `publish` (needs both;
`softprops/action-gh-release` with `generate_release_notes: true`, `fail_on_unmatched_files: true`,
and `permissions: contents: write` scoped to that job only). Artifacts use `retention-days: 1` and
`if-no-files-found: error`.

The weaker tier (`pr-drift`, `codegraph-lsp`) uses floating tags (`actions/checkout@v4`), runs
`ruff check` instead of mypy, and has no SBOM. `codegraph-lsp` additionally configures **pyright**
in `pyproject.toml` (`typeCheckingMode = "basic"`) though CI does not run it.

**Nothing in any sibling repo publishes to PyPI** — releases attach wheels to GitHub Releases only.
No project has a coverage gate, a Dependabot config, a `CODEOWNERS`, or branch-protection config
checked in. No project runs a security scanner in CI.

---

## Cross-Cutting Patterns

1. **Credentials are passed in, never fetched.** Both `pr-drift`'s `fetch_pr_diff(..., token)` and
   `lattice`'s `ClientConfig(api_key=...)` take the secret as an explicit parameter. No sibling
   project reads `os.environ` inside its client code. This is what makes their tests trivial and it
   is a convention this feature should not break — significant here, because App auth involves a
   *private key file* plus a derived token, which is more tempting to hide inside the client.

2. **One `@dataclass` config object per client, with defaults.** Constants that tune behaviour
   (`DEFAULT_TIMEOUT_SECONDS`, `RETRYABLE_STATUS_CODES`) are module-level and uppercase.

3. **One domain exception per module** (`LLMError`, `ExtractionError`), raised with `from exc` to
   preserve the chain. No exception hierarchies anywhere in the sibling code.

4. **Docstrings state deliberate non-goals.** `snapshot.py` names its race condition and declares it
   acceptable for the intended usage; `client.py` says its token accounting "is not exact, and is
   not meant to be." Bounded, honestly-documented scope is the house style — relevant to a feature
   whose name promises "everything."

5. **Approximate-then-reconcile beats precise-upfront.** The reservation/adjust pattern in
   `_TokenBucket` maps directly onto GraphQL point costing, where the true cost is only knowable
   from the `rateLimit.cost` field *after* the query runs.

6. **Tests assert on the request, not just the response.** The strongest tests in both repos capture
   what went over the wire — URL, headers, JSON body — and assert on that.

7. **`uv` end to end**, in both local workflow and CI. Any tooling choice here should be
   `uv run`-able.

---

## Open Questions for the Spec

Findings only; these are the decisions the exploration surfaced but cannot settle.

1. **What does "everything" bound to?** The `Repository` type alone has ~150 fields with ~30 nested
   connections. A full crawl of issues → comments → reactions for a large org is orders of magnitude
   beyond a 12,500 point/hour ceiling. The spec needs an explicit dataset list and an explicit
   statement of what is out of scope.

2. **One-shot download or resumable/incremental?** Token lifetime is 1 hour and the hourly point
   budget caps at 12,500. Any org large enough to be interesting will exceed both within one run,
   which makes checkpoint/resume a correctness concern rather than a feature.

3. **Output format and layout.** `lattice` writes timestamped Parquet plus a `manifest.json`
   pointer. Whether this feature reuses that shape, emits newline-delimited JSON, or writes a
   database is unsettled — and it interacts with question 2, since resume needs to know what has
   already landed.

4. **GraphQL-only, or GraphQL + REST fallback?** Traffic data (14-day retention), Git events, org
   webhooks, secrets, and billing have no GraphQL representation. "Everything" and "GraphQL
   querying repository" are in mild tension in the original request.

5. **Behaviour on partial failure.** Given HTTP 200 responses carrying an `errors` array, does a
   permission-denied subtree abort the run, get recorded as a gap in the output, or get silently
   skipped? Whatever is chosen needs to be visible to the user, since the alternative is a download
   that is quietly incomplete.

6. **Which App permissions are required vs. optional?** Under-permissioned queries return 401 rather
   than degrading, and GitHub publishes no permission→field map. A preflight capability check is one
   answer; a documented required-permission set is another.

7. **Sync or async?** `lattice` is fully async (and its concurrency/rate-limit tests depend on that).
   The 100-concurrent-request secondary limit only becomes a real constraint if the tool is
   concurrent at all.

8. **Enterprise Cloud support?** The point ceiling doubles on GHEC and `Organization.auditLog`
   behaves differently. Targeting GHEC changes the rate-limit maths.
