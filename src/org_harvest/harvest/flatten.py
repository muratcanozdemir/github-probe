"""Shared GraphQL-node-to-record flattening (DEV-4), used by both the
Phase 1 (`org_level.py`) and Phase 2 (`repo_level.py`) fetch engines so the
one JSON-shaping rule — camelCase GraphQL field names become snake_case
record keys, and a record without a native node id gets one synthesized
from its declared natural key — exists in exactly one place.
"""

from __future__ import annotations

from typing import Any


def snake_case(name: str) -> str:
    out: list[str] = []
    for ch in name:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def flatten_node(
    node: dict[str, Any], *, edge_field: str | None, edge_value: Any, id_field: str = "id"
) -> dict[str, Any]:
    """Converts one GraphQL node (plus, optionally, one field read off its
    edge) into a flat record. Nested objects and lists are preserved
    as-is (FR-8) — only the top-level keys are renamed. If the node has no
    native `id` (AC-8.6), one is synthesized from `id_field`."""
    record = {snake_case(k): v for k, v in node.items()}
    if edge_field is not None:
        record[snake_case(edge_field)] = edge_value
    if "id" not in record:
        record["id"] = record.get(snake_case(id_field))
    return record
