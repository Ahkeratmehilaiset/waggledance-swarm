"""
Tests for dashboard profile-based gating of domain-specific sections.
"""

from __future__ import annotations

import pytest


class TestDomainProfileGating:
    def test_domain_profiles_recognized(self):
        from web.dashboard import _DOMAIN_PROFILES
        assert "cottage" in _DOMAIN_PROFILES
        assert "factory" in _DOMAIN_PROFILES

    def test_non_domain_profile_not_recognized(self):
        from web.dashboard import _DOMAIN_PROFILES
        assert "industrial" not in _DOMAIN_PROFILES
        assert "medical" not in _DOMAIN_PROFILES

    def test_is_apiary_profile_true(self):
        from web.dashboard import _is_apiary_profile

        class FakeHivemind:
            config = {"profile": "cottage"}

        assert _is_apiary_profile(FakeHivemind()) is True

    def test_is_apiary_profile_false(self):
        from web.dashboard import _is_apiary_profile

        class FakeHivemind:
            config = {"profile": "INDUSTRIAL"}

        assert _is_apiary_profile(FakeHivemind()) is False

    def test_is_apiary_profile_case_insensitive(self):
        from web.dashboard import _is_apiary_profile

        class FakeHivemind:
            config = {"profile": "FACTORY"}

        assert _is_apiary_profile(FakeHivemind()) is True

    def test_is_apiary_profile_empty(self):
        from web.dashboard import _is_apiary_profile

        class FakeHivemind:
            config = {}

        assert _is_apiary_profile(FakeHivemind()) is False
