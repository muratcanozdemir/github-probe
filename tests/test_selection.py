from __future__ import annotations

import pytest

from org_harvest.datasets import default_tier_names
from org_harvest.errors import ErrorKind, OrgHarvestError
from org_harvest.selection import RepositoryFilter, resolve_dataset_selection


class TestDefaultSelection:
    def test_none_resolves_to_the_full_default_tier_ac_2_2(self):
        selection = resolve_dataset_selection(None)
        assert selection.names == default_tier_names()
        assert selection.auto_included == ()


class TestNarrowing:
    def test_a_named_subset_of_the_default_tier_is_honored_ac_2_1(self):
        selection = resolve_dataset_selection(("organization",))
        assert selection.names == ("organization",)

    def test_naming_an_optional_dataset_enables_it_ac_2_3(self):
        selection = resolve_dataset_selection(("audit_log",))
        assert "audit_log" in selection.names


class TestValidation:
    def test_unknown_name_raises_before_any_network_call_ac_2_4(self):
        with pytest.raises(OrgHarvestError) as exc_info:
            resolve_dataset_selection(("does_not_exist",))
        assert exc_info.value.kind is ErrorKind.INVALID_USAGE
        assert "does_not_exist" in str(exc_info.value)

    def test_empty_selection_is_rejected_ac_2_5(self):
        with pytest.raises(OrgHarvestError) as exc_info:
            resolve_dataset_selection(())
        assert exc_info.value.kind is ErrorKind.INVALID_USAGE


class TestDependencyAutoInclusion:
    def test_team_members_pulls_in_teams_and_reports_it_ac_2_6(self):
        selection = resolve_dataset_selection(("team_members",))
        assert "teams" in selection.names
        assert "teams" in selection.auto_included
        assert "team_members" not in selection.auto_included

    def test_a_repo_level_dataset_pulls_in_repositories_ac_2_6(self):
        selection = resolve_dataset_selection(("issues",))
        assert "repositories" in selection.names
        assert "repositories" in selection.auto_included

    def test_team_repositories_pulls_in_both_teams_and_repositories(self):
        selection = resolve_dataset_selection(("team_repositories",))
        assert set(selection.auto_included) >= {"teams", "repositories"}

    def test_explicitly_requesting_a_dependency_does_not_mark_it_auto_included(self):
        selection = resolve_dataset_selection(("repositories", "issues"))
        assert "repositories" not in selection.auto_included
        assert selection.names.count("repositories") == 1

    def test_duplicate_requested_names_are_deduplicated(self):
        selection = resolve_dataset_selection(("organization", "organization"))
        assert selection.names.count("organization") == 1


class TestRepositoryFilter:
    def test_no_filter_conditions_is_a_noop(self):
        assert RepositoryFilter().is_noop

    def test_a_configured_filter_is_not_a_noop(self):
        assert not RepositoryFilter(exclude_archived=True).is_noop

    def test_name_allowlist_restricts_to_named_repositories_ac_2_8(self):
        rf = RepositoryFilter(names=frozenset({"keep"}))
        assert rf.allows(name="keep", is_archived=False, is_fork=False)
        assert not rf.allows(name="drop", is_archived=False, is_fork=False)

    def test_exclude_archived_ac_2_8(self):
        rf = RepositoryFilter(exclude_archived=True)
        assert not rf.allows(name="r", is_archived=True, is_fork=False)
        assert rf.allows(name="r", is_archived=False, is_fork=False)

    def test_exclude_forks_ac_2_8(self):
        rf = RepositoryFilter(exclude_forks=True)
        assert not rf.allows(name="r", is_archived=False, is_fork=True)
        assert rf.allows(name="r", is_archived=False, is_fork=False)

    def test_conditions_combine(self):
        rf = RepositoryFilter(
            names=frozenset({"a", "b"}), exclude_archived=True, exclude_forks=True
        )
        assert rf.allows(name="a", is_archived=False, is_fork=False)
        assert not rf.allows(name="a", is_archived=True, is_fork=False)
        assert not rf.allows(name="a", is_archived=False, is_fork=True)
        assert not rf.allows(name="c", is_archived=False, is_fork=False)
