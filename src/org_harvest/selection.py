"""Dataset selection and repository filtering (US-2).

`resolve_dataset_selection()` is pure — it consults only the dataset
registry (Story 4) and never makes a network call — so a bad selection
(AC-2.4, AC-2.5) fails before anything is spent on it. Dependency
auto-inclusion (AC-2.6) walks `DatasetSpec.depends_on` to a fixed point,
recording which names were pulled in rather than explicitly requested so
the caller can report that back to the user.

`RepositoryFilter` (AC-2.8) is applied by Story 5's organization-level
fetch when it writes the `repositories` dataset — filtering there, rather
than after the fact, means the `repositories` dataset itself and Phase 2's
fan-out (which reads that same file, architecture.md Decision 4) agree
automatically about which repositories are in scope for this run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from org_harvest.datasets import default_tier_names, get
from org_harvest.errors import ErrorKind, OrgHarvestError


@dataclass(frozen=True)
class DatasetSelection:
    """The result of resolving a user's requested dataset names."""

    #: Every dataset that will actually run, including auto-included
    #: dependencies, in registry-declaration order (stable, reproducible
    #: query order across runs).
    names: tuple[str, ...]
    #: The subset of `names` that were pulled in only because something
    #: explicitly requested depends on them (AC-2.6) — not asked for
    #: directly. Empty when nothing needed auto-inclusion.
    auto_included: tuple[str, ...]


def resolve_dataset_selection(requested: Sequence[str] | None) -> DatasetSelection:
    """AC-2.1/AC-2.2/AC-2.3: with no selection (`None`), the full default
    tier; otherwise a validated, dependency-closed selection that may also
    opt into optional-tier datasets by naming them — the same mechanism
    both narrows the default tier and opts into the optional one.

    Raises `OrgHarvestError(kind=INVALID_USAGE)` for an unknown name
    (AC-2.4) or a selection that resolves to zero datasets (AC-2.5) —
    before any network call, since this function makes none.
    """
    if requested is None:
        return DatasetSelection(default_tier_names(), ())
    explicit = tuple(dict.fromkeys(requested))  # de-dup, preserve request order
    if not explicit:
        raise OrgHarvestError(
            "Dataset selection resolved to an empty set.", kind=ErrorKind.INVALID_USAGE
        )
    for name in explicit:
        get(name)  # raises INVALID_USAGE, listing valid names, on an unknown one

    resolved: list[str] = []
    auto_included: list[str] = []

    def _include(name: str, *, explicitly_requested: bool) -> None:
        if name in resolved:
            return
        for dep in get(name).depends_on:
            _include(dep, explicitly_requested=False)
        resolved.append(name)
        if not explicitly_requested:
            auto_included.append(name)

    for name in explicit:
        _include(name, explicitly_requested=True)

    return DatasetSelection(tuple(resolved), tuple(auto_included))


@dataclass(frozen=True)
class RepositoryFilter:
    """AC-2.8: restricts which repositories a run considers at all.
    Applied once, where the `repositories` dataset itself is written
    (Story 5) — see the module docstring for why that single point is
    enough to also narrow Phase 2's fan-out."""

    #: An explicit repository-name allowlist, or `None` for no subset
    #: restriction (every repository the installation can see).
    names: frozenset[str] | None = None
    exclude_archived: bool = False
    exclude_forks: bool = False

    def allows(self, *, name: str, is_archived: bool, is_fork: bool) -> bool:
        if self.names is not None and name not in self.names:
            return False
        if self.exclude_archived and is_archived:
            return False
        return not (self.exclude_forks and is_fork)

    @property
    def is_noop(self) -> bool:
        """True when this filter excludes nothing — lets a caller skip
        building one at all rather than constructing an always-`True`
        filter."""
        return self.names is None and not self.exclude_archived and not self.exclude_forks
