"""Populates the dataset registry with metadata for all 37 datasets (FR-1).

**Permission caveat:** the `required_permissions` below are org-harvest's
best-effort mapping to GitHub's documented GitHub App permission names
(see "Choosing permissions for a GitHub App" in GitHub's docs). GitHub does
not publish a single authoritative permission-to-GraphQL-field table
(exploration.md), so a handful of these are reasoned mappings rather than
values pulled directly from a table. If a preflight verdict looks wrong for
a specific dataset against a real installation, treat this mapping —
`ORG_LEVEL_DATASETS` / `REPO_LEVEL_DATASETS` below — as the place to fix it;
it is org-harvest's single source of truth for the mapping, not GitHub's.

Field lists and Parquet schemas are added later by `complete_fetch_details()`
calls in the modules that make each dataset fetchable (Story 5, 6, and 11).
"""

from __future__ import annotations

from org_harvest.datasets.registry import DatasetLevel, DatasetSpec, DatasetTier, register

# --- Default tier, organization level (FR-1) --------------------------------

_ORG_DEFAULT: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "organization",
        "Organization settings and metadata (login, name, billing email, "
        "2FA requirement, SAML, IP allow-list settings).",
        DatasetTier.DEFAULT,
        DatasetLevel.ORGANIZATION,
        ("metadata",),
    ),
    DatasetSpec(
        "members",
        "Organization members and their role.",
        DatasetTier.DEFAULT,
        DatasetLevel.ORGANIZATION,
        ("members",),
    ),
    DatasetSpec(
        "pending_members",
        "Organization membership invitations not yet accepted.",
        DatasetTier.DEFAULT,
        DatasetLevel.ORGANIZATION,
        ("members",),
    ),
    DatasetSpec(
        "teams",
        "Teams, their privacy, and parent/child relationships.",
        DatasetTier.DEFAULT,
        DatasetLevel.ORGANIZATION,
        ("members",),
    ),
    DatasetSpec(
        "team_members",
        "Team membership.",
        DatasetTier.DEFAULT,
        DatasetLevel.ORGANIZATION,
        ("members",),
        depends_on=("teams",),
    ),
    DatasetSpec(
        "team_repositories",
        "Repository access grants held by each team.",
        DatasetTier.DEFAULT,
        DatasetLevel.ORGANIZATION,
        ("members",),
        depends_on=("teams", "repositories"),
    ),
    DatasetSpec(
        "repositories",
        "The organization's repository list and per-repository settings.",
        DatasetTier.DEFAULT,
        DatasetLevel.ORGANIZATION,
        ("metadata",),
    ),
    DatasetSpec(
        "org_rulesets",
        "Organization-wide repository rulesets.",
        DatasetTier.DEFAULT,
        DatasetLevel.ORGANIZATION,
        ("organization_administration",),
    ),
    DatasetSpec(
        "org_custom_properties",
        "Custom property definitions available to repositories in the org.",
        DatasetTier.DEFAULT,
        DatasetLevel.ORGANIZATION,
        ("organization_custom_properties",),
    ),
    DatasetSpec(
        "org_domains",
        "Verified domains for the organization.",
        DatasetTier.DEFAULT,
        DatasetLevel.ORGANIZATION,
        ("organization_administration",),
    ),
    DatasetSpec(
        "org_ip_allow_list",
        "IP allow-list entries for the organization.",
        DatasetTier.DEFAULT,
        DatasetLevel.ORGANIZATION,
        ("organization_administration",),
    ),
)

# --- Default tier, repository level (FR-1) -----------------------------------

_REPO_DEFAULT: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "issues",
        "Issues (title, state, author, timestamps, labels, assignees, "
        "milestone) without their comment/timeline sub-collections.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("issues",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "pull_requests",
        "Pull requests (same shape as issues) without their review/comment sub-collections.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("pull_requests",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "discussions",
        "Discussions without their comment sub-collections.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("discussions",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "releases",
        "Releases and their tag/asset metadata.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("contents",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "labels",
        "Label definitions.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("issues",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "milestones",
        "Milestone definitions.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("issues",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "collaborators",
        "Repository collaborators and their permission level.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("metadata",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "branch_protection_rules",
        "Branch protection rules.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("administration",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "repo_rulesets",
        "Repository-level rulesets.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("administration",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "repo_custom_property_values",
        "Custom property values set on the repository.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("repository_custom_properties",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "environments",
        "Deployment environments and their protection rules.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("environments",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "deployments",
        "Deployments.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("deployments",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "vulnerability_alerts",
        "Dependabot vulnerability alerts.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("vulnerability_alerts",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "topics",
        "Repository topics.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("metadata",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "languages",
        "Detected languages and their byte share.",
        DatasetTier.DEFAULT,
        DatasetLevel.REPOSITORY,
        ("metadata",),
        depends_on=("repositories",),
    ),
)

# --- Optional tier (FR-1) -----------------------------------------------------

_OPTIONAL: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "workflow_runs",
        "GitHub Actions workflow runs. High-volume on active repos.",
        DatasetTier.OPTIONAL,
        DatasetLevel.REPOSITORY,
        ("actions",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "check_suites",
        "Check suites. High-volume on active repos.",
        DatasetTier.OPTIONAL,
        DatasetLevel.REPOSITORY,
        ("checks",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "packages",
        "Packages published from the repository.",
        DatasetTier.OPTIONAL,
        DatasetLevel.REPOSITORY,
        ("packages",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "forks",
        "Repository forks. High-volume on popular repos.",
        DatasetTier.OPTIONAL,
        DatasetLevel.REPOSITORY,
        ("metadata",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "stargazers",
        "Users who starred the repository. High-volume on popular repos.",
        DatasetTier.OPTIONAL,
        DatasetLevel.REPOSITORY,
        ("metadata",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "watchers",
        "Users watching the repository. High-volume on popular repos.",
        DatasetTier.OPTIONAL,
        DatasetLevel.REPOSITORY,
        ("metadata",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "dependency_graph_manifests",
        "Dependency graph manifests and their resolved dependencies.",
        DatasetTier.OPTIONAL,
        DatasetLevel.REPOSITORY,
        ("contents",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "deploy_keys",
        "Repository deploy keys.",
        DatasetTier.OPTIONAL,
        DatasetLevel.REPOSITORY,
        ("administration",),
        depends_on=("repositories",),
    ),
    DatasetSpec(
        "projects_v2",
        "Organization-owned Projects (v2).",
        DatasetTier.OPTIONAL,
        DatasetLevel.ORGANIZATION,
        ("organization_projects",),
    ),
    DatasetSpec(
        "audit_log",
        "Organization audit log entries. Elevated permission; 90-120 day "
        "retention; GitHub separately rate-limits this endpoint.",
        DatasetTier.OPTIONAL,
        DatasetLevel.ORGANIZATION,
        ("organization_administration",),
    ),
    DatasetSpec(
        "org_webhooks",
        "Organization webhook configurations. REST-only (no GraphQL equivalent).",
        DatasetTier.OPTIONAL,
        DatasetLevel.ORGANIZATION,
        ("organization_hooks",),
    ),
)

ALL_DATASETS: tuple[DatasetSpec, ...] = _ORG_DEFAULT + _REPO_DEFAULT + _OPTIONAL


def register_all() -> None:
    for spec in ALL_DATASETS:
        register(spec)
