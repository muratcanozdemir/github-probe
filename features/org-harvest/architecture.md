# Architecture Brief: org-harvest

**Created:** 2026-08-27

## Key Decisions

### Decision 1: Declarative dataset registry, one generic engine
**Decision:** Each of the 37 datasets (FR-1) is defined as a single declarative spec — field list, parent-key field(s), tier, required permission(s), and Parquet schema — held in one place per dataset. One generic engine reads these specs to build each dataset's GraphQL fragment, track its checkpoint cursor, validate preflight permissions against it, and derive its Parquet schema. No dataset gets its own hand-written fetch/parse/schema function.

**Reasoning:** The completeness check surfaced a concrete failure mode this prevents: query fields and Parquet schema maintained independently drift apart, which is exactly what AC-8.3 ("Parquet schema... identical across runs") forbids. A declarative spec makes the query and the schema two projections of the same source, so they cannot drift. It also means the 37-dataset surface — and the optional-tier datasets a later change might add — is a data change in one registry, not new code in three places (query builder, checkpoint bookkeeping, schema writer).

**Alternative considered:** Hand-written fetch+parse+schema per dataset. More flexible for any one dataset's quirks, but 37x the boilerplate with no structural guard against the drift above.

### Decision 2: Alias-batched GraphQL requests across repositories
**Decision:** Repository-level dataset requests batch multiple repositories into one GraphQL request using aliases (`repo0: repository(...) { issues(...) }`, `repo1: repository(...) { ... }`, ...), rather than issuing one request per repository. Batch width is a tunable, not fixed.

**Reasoning:** This was chosen over the single-repository-per-request alternative for the round-trip savings against the 100-concurrent-request and 2,000-points-per-minute secondary limits (exploration.md). It does add real complexity that the following constraints exist to contain:
- **Per-repo cursor state, not per-batch.** Each alias in a batch carries its own `after` cursor variable, because different repositories reach the "next page" step at different times. A batch is formed by grouping whatever repositories are currently pending the same dataset's next page — it is not a fixed static grouping.
- **Partial-failure attribution uses the GraphQL error `path`.** GraphQL's `errors[].path` array is prefixed with the alias name that produced it, so a failure inside one aliased repository is attributable to that repository specifically, not the whole batch. The response demultiplexer must map `path[0]` back to the alias→repository mapping before writing gaps (FR-5, AC-5.2) — this is what makes per-repo gap attribution possible despite batching, and it must be exercised by tests, not assumed.
- **Node-limit retries (EC-16) may need to shrink batch width, not only page size.** The 500,000-node ceiling applies to the whole request, so an oversized aliased query can fail before any alias executes. The retry strategy tries a smaller page size first (matching AC-7.8's literal wording); if that alone is insufficient, it must also reduce the number of aliases in the batch. Both levers exist for exactly this reason — do not implement only one.
- **A transport-level failure (network error, persistent 5xx) invalidates the whole batch,** since nothing in it executed; standard retry/backoff (FR-4) applies to the batch as a unit. This is different from a GraphQL-level partial error, which is per-alias.

**Alternative considered:** One repository per request. Simpler cursor bookkeeping and unambiguous failure attribution with no per-alias path parsing, at the cost of more round-trips. Rejected in favor of the round-trip savings, with the constraints above adopted specifically to keep failure attribution and node-limit handling correct under batching.

### Decision 3: One shared transport layer, pluggable credential provider
**Decision:** A single transport component owns request sending, retry/backoff-with-jitter, and rate-limit pacing (GraphQL points and REST requests accounted separately per AC-7.9) for both the GraphQL and REST paths. Authentication is injected through a credential-provider interface with two implementations: one that mints and refreshes installation tokens from a private key, one that wraps a static pre-minted token and reports itself non-refreshable. The transport layer consults the provider before a rate-limit wait to satisfy AC-7.4 (never sleep past a non-refreshable credential's expiry).

**Reasoning:** Directly follows `lattice`'s `LLMClient` pattern (exploration.md) — one place owns retry/backoff/pacing rather than duplicating it across a GraphQL client and a REST client. The credential-provider split isolates the one behavioral difference between the two auth modes (US-3) — refreshability — behind one interface, so the transport layer and every dataset fetch are written once and work under both.

### Decision 4: Two-phase harvest pipeline
**Decision:** A run has two phases. Phase 1 fetches every organization-level dataset, including the repository list that anchors Phase 2. Phase 2 fans out repository-level datasets, batched per Decision 2, across the repository set Phase 1 produced. Resume re-enters at whichever phase was incomplete; Phase 2 cannot start, even on resume, until Phase 1's repository list is complete.

**Reasoning:** Repository-level datasets have no target set to fan out over until the repository list exists — this is a hard data dependency, not a preference. Making it an explicit phase boundary (rather than an implicit ordering that falls out of code structure) gives `/break-stories` a clean place to draw a story boundary and gives resume an unambiguous re-entry rule.

### Decision 5: Checkpoint and manifest file shapes
**Decision:** Checkpoint state is one JSON file per snapshot, recording per-dataset status, a per-(repository, dataset) cursor map, the run's original dataset selection and repository filter (for AC-4.8), and the tool version that wrote it (for AC-4.9). Two separate manifests exist: a per-snapshot `manifest.json` inside the snapshot directory (org, host, timings, counts, gaps, scope restriction, consumption stats, retry history — AC-8.7), and a per-org root index file that lists all snapshots for that org and points at the newest one with no gaps and no scope restriction (AC-8.8). Concurrent-run safety (FR-9) uses a third small file — a claim recording the holding process's identity and a heartbeat timestamp, whose staleness is judged by heartbeat age.

**Reasoning:** Mirrors `lattice/snapshot.py`'s manifest.json convention, split into two files because a per-snapshot manifest structurally cannot describe "the latest snapshot across all snapshots" (that information doesn't exist until other snapshots do) — collapsing them into one file was one of the ambiguities the completeness check found in the draft spec. The claim file's heartbeat is what makes stale-claim detection (EC-12) possible without an external lock service.

### Decision 6: Retry-gaps reuses the fetch engine
**Decision:** Retry-gaps (US-11) is not a separate implementation. It builds a resource-id filter from the snapshot's recorded gaps and drives the same Phase 2 fetch engine (Decision 1's generic engine, Decision 2's batching) scoped to that filter, writing results into the existing snapshot and updating its manifest and checkpoint in place.

**Reasoning:** With 37 datasets, a second hand-written retry path per dataset would double the surface prone to the drift problem Decision 1 exists to prevent. Reuse also means retry-gaps inherits every dataset's batching, gap-attribution, and schema behavior automatically as those evolve.

### Decision 7: Package structure follows house convention, scaled up
**Decision:** `src/org_harvest/` is a nested package, not `lattice`'s flat single-directory layout, because this feature's surface (auth, transport, dataset registry, checkpoint/resume, output, CLI) is substantially larger. Public types are still re-exported flat from the package root `__init__.py` (AC-9.1), matching the house convention observed in every sibling project regardless of internal nesting.

**Reasoning:** `lattice` (~7 flat modules) fits a flat layout; this feature's 37-dataset registry plus two auth modes plus checkpoint/resume machinery does not. The public-API-is-flat convention is preserved regardless of internal organization — callers still do `from org_harvest import Harvester, HarvestConfig`, never reaching into submodules.

## Constraints

- Every dataset is defined once as a declarative spec (fields, parent-key, tier, permission, schema); no per-dataset bespoke fetch, parse, or schema code (Decision 1).
- Repository-level GraphQL requests batch repositories via aliases with a tunable batch width; the response demultiplexer attributes partial failures using `errors[].path`, and the node-limit retry path must be able to reduce batch width in addition to page size (Decision 2).
- All GraphQL and REST requests route through one shared transport component that owns retry/backoff/jitter and rate-limit pacing, parameterized by a credential-provider interface (Decision 3).
- Phase 2 (repository-level datasets) cannot begin, including on resume, until Phase 1 (organization-level datasets, including the repository list) is complete (Decision 4).
- Checkpoint state is one JSON file per snapshot; a per-snapshot `manifest.json` and a per-org root index are separate files, plus a claim file with a heartbeat for concurrent-run safety (Decision 5).
- Retry-gaps must not duplicate per-dataset fetch logic — it drives the same engine scoped to a resource-id filter (Decision 6).
- Public types are re-exported flat from the package root regardless of internal module nesting (Decision 7).

## Pattern Reference

- **Closest analogue:** `~/lattice` — `src/lattice/client.py` for the retry/backoff/token-bucket shape the shared transport layer generalizes; `src/lattice/snapshot.py` for the manifest-pointer persistence shape the two-manifest design extends; `src/lattice/__init__.py` for the flat-re-export convention.
- **Verified API facts this brief assumes:** `features/org-harvest/exploration.md` — GraphQL error `path` semantics, the 500,000-node ceiling, secondary rate limits, and installation-token scoping all come from the schema and docs fetched during exploration, not from memory.

## Cross-Story Coordination

- The declarative dataset registry (Decision 1) and the shared transport + credential-provider layer (Decision 3) are shared foundations every other story depends on — they should be the first stories built, before any individual dataset's story.
- The alias-batching query builder and response demultiplexer (Decision 2) are a shared component consumed by every repository-level dataset story; it is built once, parameterized by the dataset registry, not reimplemented per dataset.
- Phase 1 must be functionally complete — including tests proving the repository list is durable across resume — before Phase 2 stories can be meaningfully implemented or tested, since Phase 2 has no fan-out target without it (Decision 4).
- Checkpoint, manifest, and claim-file formats (Decision 5) must be fixed before any dataset-fetch story is implemented, since every fetch story reads and writes them.
- Retry-gaps (US-11, Decision 6) should be sequenced after the Phase 2 fetch engine works end to end, since it reuses that engine rather than introducing its own.
