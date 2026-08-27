"""The single exception type org-harvest raises (AC-9.5).

Rather than growing a hierarchy of exception subclasses as new failure modes
are added story by story, every failure is an `OrgHarvestError` carrying a
`kind` from `ErrorKind`. Callers who need to branch on the failure category
(the CLI choosing an exit status, a library caller deciding whether to retry)
match on `.kind` instead of `except SomeSpecificError`.
"""

from __future__ import annotations

from enum import Enum, auto


class ErrorKind(Enum):
    """Categorizes an `OrgHarvestError` without multiplying exception types."""

    CREDENTIAL_CONFLICT = auto()
    """Neither or both credential forms were supplied (AC-3.6)."""

    CREDENTIAL_INVALID = auto()
    """Credential material is missing, unreadable, malformed, passphrase-protected,
    or does not match the given client ID (AC-3.7)."""

    AUTH_EXPIRED = auto()
    """A pre-minted static token has expired and cannot be refreshed (AC-3.4)."""

    AUTH_FAILED = auto()
    """Authentication failed for a reason other than expiry (e.g. a mid-run
    refresh failure because the installation was suspended or uninstalled,
    or a suspected host clock skew) (EC-6, EC-23)."""

    ORG_NOT_FOUND = auto()
    """The organization login does not exist (EC-2)."""

    APP_NOT_INSTALLED = auto()
    """The organization exists but the App has no installation on it (EC-2)."""

    REQUEST_FAILED = auto()
    """A request yielded no usable response after exhausting retries
    (network errors, timeouts, or persistent 429/5xx) (AC-5.3)."""

    RATE_LIMIT_WAIT_EXCEEDED = auto()
    """Waiting for the rate limit to reset was refused because it would
    outlast a non-refreshable credential (AC-7.4), or because it would
    exceed the configured total-wait ceiling (AC-7.5). The run stops
    cleanly with whatever has already been checkpointed to disk."""


class OrgHarvestError(Exception):
    """Raised for every org-harvest failure. See `ErrorKind` for categories."""

    def __init__(self, message: str, *, kind: ErrorKind) -> None:
        super().__init__(message)
        self.kind = kind
