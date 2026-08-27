"""The dataset registry's public surface.

Importing this module registers metadata for all 37 datasets (`catalog.py`).
Stories that make a dataset fetchable import their own module in addition
(e.g. `org_harvest.datasets.org_level`), which calls `complete_fetch_details`
for the datasets it implements.
"""

from __future__ import annotations

from org_harvest.datasets.catalog import register_all
from org_harvest.datasets.registry import (
    DatasetLevel,
    DatasetSpec,
    DatasetTier,
    all_specs,
    complete_fetch_details,
    default_tier_names,
    get,
)

register_all()

__all__ = [
    "DatasetLevel",
    "DatasetSpec",
    "DatasetTier",
    "all_specs",
    "complete_fetch_details",
    "default_tier_names",
    "get",
]
