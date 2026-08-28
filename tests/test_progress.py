"""Tests for the progress-observation types (Story 15, AC-9.4)."""

from __future__ import annotations

from org_harvest.progress import ProgressCallback, ProgressEvent, ProgressEventKind


class TestProgressEventKind:
    def test_all_four_phase_lifecycle_kinds_exist(self) -> None:
        assert ProgressEventKind.PHASE_STARTED.value == "phase_started"
        assert ProgressEventKind.DATASET_COMPLETE.value == "dataset_complete"
        assert ProgressEventKind.RATE_LIMIT_WAIT.value == "rate_limit_wait"
        assert ProgressEventKind.PHASE_COMPLETE.value == "phase_complete"


class TestProgressEvent:
    def test_message_is_the_only_required_field_besides_kind(self) -> None:
        event = ProgressEvent(kind=ProgressEventKind.PHASE_STARTED, message="starting: phase1")
        assert event.phase is None
        assert event.dataset is None
        assert event.record_count is None
        assert event.gap_count is None
        assert event.wait_seconds is None

    def test_phase_event_carries_phase_name(self) -> None:
        event = ProgressEvent(
            kind=ProgressEventKind.PHASE_COMPLETE, message="finished: finalize", phase="finalize"
        )
        assert event.phase == "finalize"

    def test_dataset_complete_event_carries_counts(self) -> None:
        event = ProgressEvent(
            kind=ProgressEventKind.DATASET_COMPLETE,
            message="members: 12 record(s), 0 gap(s)",
            dataset="members",
            record_count=12,
            gap_count=0,
        )
        assert event.dataset == "members"
        assert event.record_count == 12
        assert event.gap_count == 0

    def test_rate_limit_wait_event_carries_wait_seconds(self) -> None:
        event = ProgressEvent(
            kind=ProgressEventKind.RATE_LIMIT_WAIT,
            message="waiting 3.5s for the rate limit to recover",
            wait_seconds=3.5,
        )
        assert event.wait_seconds == 3.5

    def test_event_is_frozen(self) -> None:
        event = ProgressEvent(kind=ProgressEventKind.PHASE_STARTED, message="starting: phase1")
        try:
            event.message = "mutated"  # type: ignore[misc]
        except AttributeError:
            pass
        else:
            raise AssertionError("ProgressEvent should be immutable")


class TestProgressCallback:
    def test_progress_callback_type_accepts_a_plain_function(self) -> None:
        received: list[ProgressEvent] = []

        def on_progress(event: ProgressEvent) -> None:
            received.append(event)

        callback: ProgressCallback = on_progress
        callback(ProgressEvent(kind=ProgressEventKind.PHASE_STARTED, message="starting: phase1"))
        assert len(received) == 1
