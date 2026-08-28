"""Progress observation for a programmatic run (Story 15, AC-9.4, FR-10:
"report progress at dataset ... granularity ... including during
rate-limit waits").

A caller passes `on_progress` to `run_snapshot()` to be notified as the
run proceeds — after each dataset finishes fetching, before/after each
phase, and whenever a rate-limit wait actually happens — rather than only
once the whole run is done. This adds no new harvesting behavior of its
own (Story 15's Excluded scope): it only reports events at moments the
run already reaches.

The callback is invoked synchronously and is expected to be cheap and
non-blocking (logging, updating a progress bar, forwarding to a queue) —
`run_snapshot()` does not catch exceptions it raises, so a callback that
raises stops the run exactly as any other unhandled exception would.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class ProgressEventKind(Enum):
    """What kind of thing just happened. See `ProgressEvent` for which
    fields are populated for each kind."""

    PHASE_STARTED = "phase_started"
    """A phase (`"preflight"`, `"phase1"`, `"phase2"`, `"finalize"`) is
    about to begin. `phase` is set; every other optional field is `None`."""

    DATASET_COMPLETE = "dataset_complete"
    """One dataset's fetch has finished (successfully, with gaps, or
    entirely gapped) — `dataset`, `record_count`, and `gap_count` are set."""

    RATE_LIMIT_WAIT = "rate_limit_wait"
    """The transport is pausing for the rate limit to recover before its
    next request. `wait_seconds` is set."""

    PHASE_COMPLETE = "phase_complete"
    """A phase (matching a prior `PHASE_STARTED`) has finished. `phase` is
    set; every other optional field is `None`."""


@dataclass(frozen=True)
class ProgressEvent:
    """One progress notification. `message` is always a ready-to-log,
    human-readable summary — a caller that just wants something
    reasonable to print doesn't need to branch on `kind` at all."""

    kind: ProgressEventKind
    message: str
    phase: str | None = None
    dataset: str | None = None
    record_count: int | None = None
    gap_count: int | None = None
    wait_seconds: float | None = None


#: A caller-supplied observer. Invoked synchronously, in-line with the run
#: — see the module docstring for what that implies about exceptions.
ProgressCallback = Callable[[ProgressEvent], None]
