"""
Tests for dashboard profile-based gating of domain-specific sections.
"""

from __future__ import annotations

import pytest


class TestApiaryProfileGating:
    def test_apiary_profiles_recognized(self):
        from web.dashboard import _APIARY_PROFILES
        assert "apiary" in _APIARY_PROFILES
        assert "cottage" in _APIARY_PROFILES

    def test_non_apiary_profile_not_recognized(self):
        from web.dashboard import _APIARY_PROFILES
        assert "industrial" not in _APIARY_PROFILES
        assert "medical" not in _APIARY_PROFILES

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
            config = {"profile": "APIARY"}

        assert _is_apiary_profile(FakeHivemind()) is True

    def test_is_apiary_profile_empty(self):
        from web.dashboard import _is_apiary_profile

        class FakeHivemind:
            config = {}

        assert _is_apiary_profile(FakeHivemind()) is False
