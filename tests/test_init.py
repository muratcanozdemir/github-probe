"""Tests for the package root's public API (Story 15, AC-9.1: the package
exposes its public types and entry points from the package root)."""

from __future__ import annotations

import org_harvest


class TestPublicReExports:
    """Every public type/entry point added across Stories 1-15 must be
    importable from `org_harvest` directly, not just from its submodule."""

    def test_every_name_in_all_is_actually_present_on_the_module(self) -> None:
        missing = [name for name in org_harvest.__all__ if not hasattr(org_harvest, name)]
        assert missing == []

    def test_all_has_no_duplicate_names(self) -> None:
        assert len(org_harvest.__all__) == len(set(org_harvest.__all__))

    def test_core_entry_point_run_snapshot_is_exported(self) -> None:
        assert org_harvest.run_snapshot is not None

    def test_credential_providers_are_exported(self) -> None:
        assert org_harvest.AppKeyCredentialProvider is not None
        assert org_harvest.StaticTokenCredentialProvider is not None
        assert org_harvest.build_credential_provider is not None

    def test_the_one_documented_exception_type_is_exported_ac_9_5(self) -> None:
        assert org_harvest.OrgHarvestError is not None
        assert org_harvest.ErrorKind is not None

    def test_structured_result_types_are_exported_ac_9_2(self) -> None:
        assert org_harvest.RunResult is not None
        assert org_harvest.Manifest is not None
        assert org_harvest.ConsumptionStats is not None
        assert org_harvest.Gap is not None
        assert org_harvest.DatasetOutcome is not None

    def test_story_12_to_15_additions_are_exported(self) -> None:
        assert org_harvest.CURSOR_DONE is not None
        assert org_harvest.InterruptGuard is not None
        assert org_harvest.OrgClaim is not None
        assert org_harvest.ClaimConflict is not None
        assert org_harvest.find_newest_incomplete_snapshot is not None
        assert org_harvest.find_named_snapshot is not None
        assert org_harvest.retry_gaps is not None
        assert org_harvest.RetryResult is not None
        assert org_harvest.ProgressEvent is not None
        assert org_harvest.ProgressEventKind is not None
        assert org_harvest.ProgressCallback is not None
