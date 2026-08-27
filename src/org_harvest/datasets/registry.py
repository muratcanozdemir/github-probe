"""The declarative dataset registry (architecture.md, Decision 1).

Every dataset org-harvest can fetch is one `DatasetSpec` in one place — its
tier, level, required permission(s), and dependencies. A single generic
engine (built in Stories 5/6) reads these specs to build each dataset's
GraphQL fragment, track its checkpoint cursor, and derive its Parquet
schema, so the query and the schema can never drift apart independently of
each other.

This module owns the registry *mechanism* and is populated with metadata
for all 37 datasets by `catalog.py`. `fields`, `parent_key`, and `schema`
start as `None` and are filled in by whichever story first makes that
dataset fetchable (Story 5 for organization-level defaults, Story 6 for
repository-level defaults, Story 11 for the optional tier) via
`complete_fetch_details()`. Preflight and dataset listing (Story 4) only
need the metadata below — they work correctly before any dataset is
actually fetchable.
"""

from __future__ import annotations

import dataclasses
from enum import Enum

from org_harvest.errors import ErrorKind, OrgHarvestError


class DatasetTier(Enum):
    """See FR-1: default datasets ship in every run unless narrowed;
    optional datasets require explicit selection (AC-2.3)."""

    DEFAULT = "default"
    OPTIONAL = "optional"


class DatasetLevel(Enum):
    """Which phase (architecture.md, Decision 4) a dataset belongs to."""

    ORGANIZATION = "organization"
    REPOSITORY = "repository"


@dataclasses.dataclass(frozen=True)
class DatasetSpec:
    name: str
    description: str
    tier: DatasetTier
    level: DatasetLevel
    #: GitHub App permission names required to read this dataset (AC-6.1,
    #: AC-6.2). Best-effort mapping to GitHub's documented App permissions —
    #: see the module docstring in `catalog.py` for the caveat.
    required_permissions: tuple[str, ...]
    #: Other dataset names that must be selected alongside this one (AC-2.6).
    depends_on: tuple[str, ...] = ()
    #: Populated once this dataset is fetchable (Story 5/6/11). `None` means
    #: "known to exist, not yet implemented" rather than "invalid".
    fields: tuple[str, ...] | None = None
    parent_key: str | None = None


_REGISTRY: dict[str, DatasetSpec] = {}


def register(spec: DatasetSpec) -> None:
    if spec.name in _REGISTRY:
        raise OrgHarvestError(
            f"dataset '{spec.name}' is already registered", kind=ErrorKind.INVALID_USAGE
        )
    _REGISTRY[spec.name] = spec


def get(name: str) -> DatasetSpec:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise OrgHarvestError(
            f"unknown dataset '{name}'. Valid datasets: {', '.join(sorted(_REGISTRY))}",
            kind=ErrorKind.INVALID_USAGE,
        ) from None


def all_specs() -> tuple[DatasetSpec, ...]:
    return tuple(_REGISTRY.values())


def default_tier_names() -> tuple[str, ...]:
    return tuple(s.name for s in _REGISTRY.values() if s.tier is DatasetTier.DEFAULT)


def complete_fetch_details(name: str, *, fields: tuple[str, ...], parent_key: str | None) -> None:
    """Called by the story that makes `name` fetchable, filling in the
    details preflight and listing don't need but the fetch engine does."""
    existing = get(name)
    _REGISTRY[name] = dataclasses.replace(existing, fields=fields, parent_key=parent_key)
