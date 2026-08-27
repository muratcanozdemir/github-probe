"""The fetch engines that actually walk GitHub's GraphQL graph and write
records to disk. `org_level` (Story 5) covers Phase 1 — the eleven
organization-level default-tier datasets (architecture.md, Decision 4).
Phase 2 (repository-level datasets, Story 6) lives alongside it here once
built.
"""

from __future__ import annotations
