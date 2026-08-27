"""Declares a fixed PyArrow type for every field name across every
dataset (AC-8.3), so a dataset's Parquet schema depends only on its
registry field list — never on which fields happened to be populated in a
particular run. A field this table has no explicit type for defaults to
`pa.string()`, which safely covers every plain scalar GitHub returns
(including timestamps, which are preserved verbatim as ISO-8601 strings
per FR-8's "GitHub's own field values preserved verbatim," not re-parsed
into a different type) as well as every nested object or list, which is
JSON-encoded into that same string column at write time (FR-8's explicit
fallback: "a value that cannot be represented shall be stored
JSON-encoded"). This keeps every dataset's schema simple, always valid for
a zero-row or all-null file, and immune to a nested field's shape drifting
across a GitHub API change.
"""

from __future__ import annotations

import pyarrow as pa

#: Fields whose GraphQL value is a genuine boolean, preserved as one.
_BOOL_FIELDS = frozenset(
    {
        "is_verified",
        "requires_two_factor_authentication",
        "members_can_fork_private_repositories",
        "web_commit_signoff_required",
        "is_private",
        "is_archived",
        "is_fork",
        "is_disabled",
        "is_empty",
        "required",
        "is_active",
        "is_approved",
        "is_draft",
        "is_prerelease",
        "requires_approving_reviews",
        "requires_status_checks",
    }
)

#: Fields whose GraphQL value is a genuine integer, preserved as one.
_INT_FIELDS = frozenset(
    {
        "database_id",
        "number",
        "size",
        "required_approving_review_count",
    }
)

#: Fields whose GraphQL value is a nested object or list (a GraphQL "node"
#: sub-selection, an `edges`/`nodes` list, or similar) rather than a plain
#: scalar — JSON-encoded into the string column at write time.
NESTED_FIELDS = frozenset(
    {
        "author",
        "milestone",
        "labels",
        "assignees",
        "security_vulnerability",
        "topic",
        "allowed_values",
    }
)


def field_type(field_name: str) -> pa.DataType:
    if field_name in _BOOL_FIELDS:
        return pa.bool_()
    if field_name in _INT_FIELDS:
        return pa.int64()
    return pa.string()


def dataset_schema(fields: tuple[str, ...]) -> pa.Schema:
    """The declared Parquet schema for a dataset with this field list —
    every field nullable, since any single record may be missing any
    field (a `None` from GitHub, or one gapped alongside others in the
    same page)."""
    return pa.schema([pa.field(name, field_type(name), nullable=True) for name in fields])
