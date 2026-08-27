"""org-harvest: download a complete GitHub organization snapshot via GraphQL,
authenticating as a GitHub App installation.

Public API is re-exported here (AC-9.1) — import from the package root, not
from submodules.
"""

from org_harvest.credentials import (
    AppKeyCredentialProvider,
    CredentialProvider,
    StaticTokenCredentialProvider,
    build_credential_provider,
)
from org_harvest.datasets import DatasetLevel, DatasetSpec, DatasetTier
from org_harvest.datasets import all_specs as all_dataset_specs
from org_harvest.datasets import default_tier_names as default_dataset_names
from org_harvest.datasets import get as get_dataset_spec
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.hosts import ApiHost
from org_harvest.preflight import DatasetVerdict, PreflightReport, Verdict, run_preflight
from org_harvest.ratelimit import BudgetTracker, ConcurrencyLimiter, RateLimitSnapshot
from org_harvest.transport import Transport

__all__ = [
    "AppKeyCredentialProvider",
    "ApiHost",
    "BudgetTracker",
    "ConcurrencyLimiter",
    "CredentialProvider",
    "DatasetLevel",
    "DatasetSpec",
    "DatasetTier",
    "DatasetVerdict",
    "ErrorKind",
    "OrgHarvestError",
    "PreflightReport",
    "RateLimitSnapshot",
    "StaticTokenCredentialProvider",
    "Transport",
    "Verdict",
    "all_dataset_specs",
    "build_credential_provider",
    "default_dataset_names",
    "get_dataset_spec",
    "run_preflight",
]
