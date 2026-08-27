"""Values shared across modules that don't belong to any single one."""

from __future__ import annotations

from importlib import metadata

try:
    _VERSION = metadata.version("org-harvest")
except metadata.PackageNotFoundError:  # pragma: no cover - editable/unbuilt checkout
    _VERSION = "0.0.0+dev"

#: Sent on every request (AC-7.10). GitHub asks for an identifying user agent
#: so it can contact an operator about traffic it considers abusive.
USER_AGENT = f"org-harvest/{_VERSION}"

#: Pinned on every REST request (AC-7.10) so behavior doesn't silently shift
#: as GitHub's "current" REST version changes over the tool's lifetime.
REST_API_VERSION = "2022-11-28"

TOOL_VERSION = _VERSION
