"""org-harvest: download a complete GitHub organization snapshot via GraphQL,
authenticating as a GitHub App installation.

Public API is re-exported here (AC-9.1) — import from the package root, not
from submodules.
"""

from org_harvest.checkpoint import CheckpointState, CheckpointStore
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
from org_harvest.finalize import FinalizeResult, finalize_dataset, finalize_snapshot
from org_harvest.gaps import DatasetOutcome, Gap
from org_harvest.harvest.org_level import OrgLevelResult, fetch_organization_directory
from org_harvest.harvest.repo_level import RepoLevelResult, fetch_repository_datasets
from org_harvest.harvest.systemic import SystemicFailureGuard
from org_harvest.hosts import ApiHost
from org_harvest.manifest import (
    CompletionStatus,
    ConsumptionStats,
    Manifest,
    RootIndex,
    build_manifest,
    is_snapshot_complete,
    read_manifest,
    rebuild_root_index,
    write_manifest,
)
from org_harvest.preflight import DatasetVerdict, PreflightReport, Verdict, run_preflight
from org_harvest.ratelimit import BudgetTracker, ConcurrencyLimiter, RateLimitSnapshot
from org_harvest.run import ExitStatus, RunResult, exit_status_for_error, run_snapshot
from org_harvest.transport import Transport

__all__ = [
    "AppKeyCredentialProvider",
    "ApiHost",
    "BudgetTracker",
    "CheckpointState",
    "CheckpointStore",
    "CompletionStatus",
    "ConcurrencyLimiter",
    "ConsumptionStats",
    "CredentialProvider",
    "DatasetLevel",
    "DatasetOutcome",
    "DatasetSpec",
    "DatasetTier",
    "DatasetVerdict",
    "ErrorKind",
    "ExitStatus",
    "FinalizeResult",
    "Gap",
    "Manifest",
    "OrgHarvestError",
    "OrgLevelResult",
    "PreflightReport",
    "RateLimitSnapshot",
    "RepoLevelResult",
    "RootIndex",
    "RunResult",
    "SystemicFailureGuard",
    "StaticTokenCredentialProvider",
    "Transport",
    "Verdict",
    "all_dataset_specs",
    "build_credential_provider",
    "build_manifest",
    "default_dataset_names",
    "exit_status_for_error",
    "fetch_organization_directory",
    "fetch_repository_datasets",
    "finalize_dataset",
    "finalize_snapshot",
    "get_dataset_spec",
    "is_snapshot_complete",
    "read_manifest",
    "rebuild_root_index",
    "run_preflight",
    "run_snapshot",
    "write_manifest",
]
