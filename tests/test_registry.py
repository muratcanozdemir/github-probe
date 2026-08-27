from __future__ import annotations

import pytest

from org_harvest.datasets import DatasetLevel, DatasetTier, all_specs, default_tier_names, get
from org_harvest.errors import ErrorKind, OrgHarvestError


class TestCatalogPopulated:
    def test_all_37_datasets_registered(self):
        assert len(all_specs()) == 37

    def test_default_tier_has_26_datasets(self):
        # 11 org-level + 15 repo-level, per FR-1.
        assert len(default_tier_names()) == 26

    def test_optional_tier_has_11_datasets(self):
        optional = [s for s in all_specs() if s.tier is DatasetTier.OPTIONAL]
        assert len(optional) == 11

    def test_every_dataset_has_at_least_one_permission(self):
        for spec in all_specs():
            assert len(spec.required_permissions) >= 1, spec.name

    def test_every_dataset_name_is_unique(self):
        names = [s.name for s in all_specs()]
        assert len(names) == len(set(names))

    def test_team_members_depends_on_teams(self):
        spec = get("team_members")
        assert "teams" in spec.depends_on

    def test_repositories_dataset_is_organization_level(self):
        assert get("repositories").level is DatasetLevel.ORGANIZATION

    def test_issues_dataset_is_repository_level(self):
        assert get("issues").level is DatasetLevel.REPOSITORY


class TestGet:
    def test_unknown_dataset_raises_ac_2_4(self):
        with pytest.raises(OrgHarvestError) as exc_info:
            get("does_not_exist")
        assert exc_info.value.kind is ErrorKind.INVALID_USAGE
        assert "does_not_exist" in str(exc_info.value)

    def test_error_lists_valid_names(self):
        with pytest.raises(OrgHarvestError) as exc_info:
            get("nope")
        assert "organization" in str(exc_info.value)


class TestRegisterGuardsAgainstDuplicates:
    def test_registering_an_existing_name_raises(self):
        from org_harvest.datasets.registry import DatasetLevel as _Level
        from org_harvest.datasets.registry import DatasetSpec as _Spec
        from org_harvest.datasets.registry import DatasetTier as _Tier
        from org_harvest.datasets.registry import register

        duplicate = _Spec("organization", "dup", _Tier.DEFAULT, _Level.ORGANIZATION, ("metadata",))
        with pytest.raises(OrgHarvestError) as exc_info:
            register(duplicate)
        assert exc_info.value.kind is ErrorKind.INVALID_USAGE
        # The real entry must be untouched by the failed attempt.
        assert get("organization").description != "dup"
