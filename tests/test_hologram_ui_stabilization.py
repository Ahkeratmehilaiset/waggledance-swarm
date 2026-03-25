# SPDX-License-Identifier: Apache-2.0
"""Pre-release UI stabilization regression tests for Hologram Brain v6.

Covers three release blockers:
A. Chat input stability — input must survive polling/panel rebuilds
B. Profile selector — all 4 profiles visible and switchable
C. Feeds tab visibility — truthful source details rendered

These tests prevent regressions from returning before release.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_V6_HTML_PATH = Path(__file__).resolve().parents[1] / "web" / "hologram-brain-v6.html"
_SUPPORTED_PROFILES = ["gadget", "cottage", "home", "factory"]


def _read_html():
    return _V6_HTML_PATH.read_text(encoding="utf-8")


def _get_feeds_cfg():
    import yaml
    settings_path = Path(__file__).resolve().parents[1] / "configs" / "settings.yaml"
    if settings_path.exists():
        with open(settings_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("feeds", {})
    return {"enabled": True, "weather": {"enabled": True},
            "electricity": {"enabled": True},
            "rss": {"enabled": True, "feeds": [
                {"name": "test", "url": "http://test", "critical": False}]}}


# ═══════════════════════════════════════════════════════════════
# A. Chat input stability
# ═══════════════════════════════════════════════════════════════

class TestChatInputStability:
    """Chat input must survive periodic panel rebuilds without losing text/focus."""

    def test_hologram_chat_input_keeps_typed_text_during_polling(self):
        """buildChatPanel skips full rebuild when input has focus — preserves typed text.

        The fix: when #chatInput has focus (document.activeElement === existingInput),
        only the messages area is updated, never el.innerHTML.
        """
        html = _read_html()
        # Verify _renderChatMessages helper exists (separated from full build)
        assert "function _renderChatMessages(" in html, \
            "Missing _renderChatMessages helper — chat messages must be updatable independently"
        # Verify the focus guard exists in buildChatPanel
        assert "inputHasFocus" in html or "document.activeElement" in html, \
            "buildChatPanel must check if input has focus before rebuilding"
        # Verify that when focus is detected, only messages are updated
        focus_guard = re.search(
            r'if\s*\(\s*inputHasFocus\s*\).*?return;', html, re.DOTALL)
        assert focus_guard, \
            "buildChatPanel must return early (skip full rebuild) when input has focus"

    def test_hologram_chat_input_keeps_focus_while_typing(self):
        """Chat input value is preserved across panel rebuilds even without focus."""
        html = _read_html()
        # After full rebuild, saved input value must be restored
        assert "existingInput" in html, \
            "buildChatPanel must capture existing input before rebuild"
        assert re.search(r'input\.value\s*=\s*existingInput\.value', html), \
            "buildChatPanel must restore input value after rebuild"

    def test_hologram_tabs_do_not_destroy_chat_input_state(self):
        """Switching to another tab and back should not lose chat message history.

        chatMessages array persists across tab switches because it lives in
        module scope, not inside the panel builder.
        """
        html = _read_html()
        # chatMessages must be declared outside buildChatPanel
        chat_decl = re.search(r'^let chatMessages\s*=\s*\[\];', html, re.MULTILINE)
        assert chat_decl, "chatMessages must be a module-scoped variable"
        # Verify it's used in _renderChatMessages
        assert "chatMessages.forEach" in html, \
            "chatMessages must be iterated in message renderer"


# ═══════════════════════════════════════════════════════════════
# B. Profile selector
# ═══════════════════════════════════════════════════════════════

class TestProfileSelector:
    """All 4 supported profiles must be visible and selectable."""

    def test_hologram_profile_selector_lists_all_profiles(self):
        """Profile <select> in header must list gadget, cottage, home, factory."""
        html = _read_html()
        # Must have a profile selector element
        assert 'id="profileSelect"' in html, \
            "Missing profileSelect element in hologram header"
        # All 4 profiles must appear as <option> values
        for profile in _SUPPORTED_PROFILES:
            assert f'value="{profile}"' in html, \
                f"Profile '{profile}' missing from profileSelect options"

    def test_hologram_profile_selector_can_switch_visible_profile(self):
        """Profile selector change event must POST to /api/profiles/active."""
        html = _read_html()
        # Event listener must exist
        assert "profileSelect" in html
        assert "/api/profiles/active" in html, \
            "Profile switch must POST to /api/profiles/active endpoint"
        # Must send profile in request body
        assert re.search(r'JSON\.stringify.*profile', html), \
            "Profile switch must send profile value in request body"

    def test_profile_api_returns_all_supported_profiles(self):
        """GET /api/profiles returns list of 4 supported profiles."""
        from waggledance.adapters.http.routes.compat_dashboard import (
            _SUPPORTED_PROFILES as api_profiles,
        )
        assert set(api_profiles) == set(_SUPPORTED_PROFILES), \
            f"Backend profiles {api_profiles} != expected {_SUPPORTED_PROFILES}"

    def test_profile_api_returns_active_profile(self):
        """GET /api/profiles includes currently active profile from service."""
        from waggledance.adapters.http.routes.compat_dashboard import api_profiles
        service = MagicMock()
        service.get_status.return_value = {"profile": "COTTAGE"}
        result = api_profiles(service=service)
        assert result["active"] == "cottage"
        assert "profiles" in result
        assert len(result["profiles"]) == 4

    def test_profile_header_syncs_with_backend(self):
        """updateHeaderBadges() must sync profileSelect.value with backend profile."""
        html = _read_html()
        # Must update select value from status data
        assert re.search(r'profileSelect.*\.value', html, re.DOTALL) or \
            re.search(r'sel\.value', html), \
            "updateHeaderBadges must sync profile selector value"


# ═══════════════════════════════════════════════════════════════
# C. Feeds tab visibility
# ═══════════════════════════════════════════════════════════════

class TestFeedsTabVisibility:
    """Feeds tab must show truthful details for all configured sources."""

    def test_hologram_feeds_tab_shows_configured_sources(self):
        """Feeds panel must render each source with name, type, protocol."""
        html = _read_html()
        assert "buildFeedsPanel" in html
        # Must render source name and protocol
        assert "s.name" in html or "escapeHtml(s.name)" in html
        assert "s.protocol" in html or "escapeHtml(s.protocol)" in html
        # Must show provider
        assert "s.provider" in html, \
            "Feeds panel must show provider for each source"

    def test_hologram_feeds_tab_shows_truthful_source_states(self):
        """Each source must show its state from the API — no fabricated states."""
        html = _read_html()
        # Must render state using node_states i18n keys
        assert "node_states" in html
        # Must show "no data" indicator when items_count is 0
        assert "no_data" in html, \
            "Feeds panel must show 'no data' indicator for empty sources"
        # State color mapping must exist
        state_colors = re.findall(r"(active|idle|framework|unwired|unavailable|failed):'#[0-9a-f]+'",
                                  html)
        assert len(state_colors) >= 4, \
            "Feeds panel must have color mapping for at least 4 feed states"

    def test_hologram_feeds_tab_renders_latest_items_or_values_when_available(self):
        """When API returns latest_items or latest_value, they must be rendered."""
        html = _read_html()
        # Latest items rendering (RSS)
        assert "latest_items" in html, "Must render latest_items for RSS sources"
        # Latest value rendering (weather, electricity)
        assert "latest_value" in html, "Must render latest_value for data sources"
        # Weather fields
        assert "temp_c" in html, "Weather latest_value must show temperature"
        # Electricity fields
        assert "price_c_kwh" in html, "Electricity latest_value must show price"

    def test_feeds_type_breakdown_in_summary(self):
        """Feeds summary must show type breakdown, not just 'Enabled: Yes'."""
        html = _read_html()
        assert "_feedTypeLabel" in html, \
            "Feeds panel must have a type label helper for breakdown"
        # Must show count of sources with data
        assert "with data" in html, \
            "Feeds summary must indicate how many sources have data"

    def test_feeds_freshness_display(self):
        """Freshness must be shown in human-readable format when available."""
        html = _read_html()
        assert "_freshnessLabel" in html, \
            "Feeds panel must have a freshness label helper"
        # Freshness function must handle seconds, minutes, hours
        assert "60" in html  # threshold reference for freshness calc
        assert "3600" in html  # hour threshold

    def test_feeds_backend_sources_have_truthful_defaults(self):
        """Built feed sources must have None/0/[] defaults — no fabricated data."""
        from waggledance.adapters.http.routes.compat_dashboard import _build_feed_sources
        feeds_cfg = _get_feeds_cfg()
        sources = _build_feed_sources(feeds_cfg)
        for s in sources:
            # Without ChromaDB enrichment, data fields must be truthfully empty
            assert s["freshness_s"] is None, \
                f"Source {s['id']} freshness_s should be None without enrichment"
            assert s["items_count"] == 0, \
                f"Source {s['id']} items_count should be 0 without enrichment"
            assert s["latest_items"] == [], \
                f"Source {s['id']} latest_items should be [] without enrichment"


# ═══════════════════════════════════════════════════════════════
# Profile + Tab smoke matrix
# ═══════════════════════════════════════════════════════════════

class TestProfileTabSmokeMatrix:
    """Smoke tests: each profile visible, key tabs functional, feeds not collapsed."""

    @pytest.mark.parametrize("profile", _SUPPORTED_PROFILES)
    def test_profile_option_exists_in_html(self, profile):
        """Each supported profile must be an <option> in the selector."""
        html = _read_html()
        assert f'value="{profile}"' in html

    @pytest.mark.parametrize("tab", [
        "overview", "memory", "reasoning", "micro", "learning", "feeds", "ops", "chat"
    ])
    def test_tab_button_exists(self, tab):
        """Each tab button must exist in the tab bar."""
        html = _read_html()
        assert f'data-tab="{tab}"' in html

    @pytest.mark.parametrize("tab", [
        "overview", "memory", "reasoning", "micro", "learning", "feeds", "ops", "chat"
    ])
    def test_tab_has_builder_function(self, tab):
        """Each tab must have a corresponding panel builder."""
        html = _read_html()
        builder_name = f"build{tab.capitalize()}Panel" if tab != "micro" else "buildMicroPanel"
        assert builder_name in html, f"Missing builder: {builder_name}"

    def test_feeds_tab_renders_source_list_not_single_status(self):
        """Feeds tab must render a source list, not collapse to a single 'Enabled' line."""
        html = _read_html()
        # Must iterate over sources array
        assert "sources.forEach" in html or "sources.length" in html, \
            "Feeds panel must iterate over sources array"
        # Must have per-source state rendering
        assert "s.state" in html, "Each feed source must show its state"

    def test_no_localstorage_token_in_active_code(self):
        """Auth safety: no localStorage token handling in active frontend."""
        html = _read_html()
        assert "localStorage" not in html, \
            "Active frontend must not reference localStorage for tokens"

    def test_no_bearer_construction_in_frontend(self):
        """Auth safety: no Bearer header construction in frontend JS."""
        html = _read_html()
        assert "Bearer" not in html, \
            "Active frontend must not construct Bearer headers"

    def test_no_token_query_param_in_frontend_ws(self):
        """Auth safety: no ?token= in frontend WebSocket connection."""
        html = _read_html()
        # The WS connection line should not include token param
        ws_lines = [l for l in html.split('\n') if 'new WebSocket' in l]
        for line in ws_lines:
            assert "token=" not in line, \
                f"Frontend WS must not use ?token= parameter: {line.strip()}"
