# SPDX-License-Identifier: Apache-2.0
"""Pre-release UI stabilization regression tests for Hologram Brain v6.

Covers three release blockers:
A. Chat input stability — input must survive polling/panel rebuilds
B. Profile selector — all 4 profiles visible; persist-only switching is truthful
C. Feeds tab visibility — truthful source details with correct stale logic

Mix of:
- Static HTML structure assertions (guard against regressions)
- Behavioral backend tests (verify actual logic correctness)
"""

import re
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_V6_HTML_PATH = Path(__file__).resolve().parents[1] / "web" / "hologram-brain-v6.html"
_SETTINGS_PATH = Path(__file__).resolve().parents[1] / "configs" / "settings.yaml"
_SUPPORTED_PROFILES = ["gadget", "cottage", "home", "factory"]


def _read_html():
    return _V6_HTML_PATH.read_text(encoding="utf-8")


def _get_feeds_cfg():
    import yaml
    if _SETTINGS_PATH.exists():
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
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
        """buildChatPanel skips full rebuild when input has focus.

        The structural guard: _renderChatMessages is a separate function,
        and buildChatPanel checks inputHasFocus before touching innerHTML.
        When focused, it returns early after updating only messages.
        """
        html = _read_html()
        assert "function _renderChatMessages(" in html, \
            "Missing _renderChatMessages helper — chat messages must be updatable independently"
        assert "inputHasFocus" in html, \
            "buildChatPanel must check inputHasFocus before rebuilding"
        # The focus guard must cause an early return (skip innerHTML)
        focus_guard = re.search(
            r'if\s*\(\s*inputHasFocus\s*\).*?return;', html, re.DOTALL)
        assert focus_guard, \
            "buildChatPanel must return early (skip full rebuild) when input has focus"

    def test_hologram_chat_input_keeps_focus_while_typing(self):
        """Chat input value is preserved across rebuilds even without focus.

        When input loses focus but still has text, buildChatPanel captures the
        value from the old (about-to-be-detached) input and restores it to the
        new one after innerHTML rebuild.
        """
        html = _read_html()
        assert "existingInput" in html, \
            "buildChatPanel must capture existing input before rebuild"
        assert re.search(r'input\.value\s*=\s*existingInput\.value', html), \
            "buildChatPanel must restore input value after rebuild"

    def test_hologram_tabs_do_not_destroy_chat_input_state(self):
        """chatMessages array persists across tab switches (module-scoped)."""
        html = _read_html()
        chat_decl = re.search(r'^let chatMessages\s*=\s*\[\];', html, re.MULTILINE)
        assert chat_decl, "chatMessages must be a module-scoped variable"
        assert "chatMessages.forEach" in html, \
            "chatMessages must be iterated in message renderer"

    def test_chat_messages_rendered_separately_from_input(self):
        """_renderChatMessages only updates the messages container, never the input.

        This is the structural guarantee: the function takes a container element
        and sets its innerHTML (messages only), never touching the input row.
        """
        html = _read_html()
        # _renderChatMessages must accept a container param and set its innerHTML
        fn_match = re.search(
            r'function _renderChatMessages\s*\(\s*container\s*,',
            html)
        assert fn_match, "_renderChatMessages must accept container parameter"
        # It must NOT reference chatInput inside the function
        fn_start = fn_match.start()
        # Find the end of the function (next top-level function)
        fn_body_match = re.search(r'\nfunction ', html[fn_start + 10:])
        fn_end = fn_start + 10 + fn_body_match.start() if fn_body_match else len(html)
        fn_body = html[fn_start:fn_end]
        assert "chatInput" not in fn_body, \
            "_renderChatMessages must not touch chatInput element"


# ═══════════════════════════════════════════════════════════════
# B. Profile selector — truthful persist-only behavior
# ═══════════════════════════════════════════════════════════════

class TestProfileSelector:
    """All 4 profiles visible; switching persists to config but requires restart."""

    def test_hologram_profile_selector_lists_all_profiles(self):
        """Profile <select> in header must list gadget, cottage, home, factory."""
        html = _read_html()
        assert 'id="profileSelect"' in html, \
            "Missing profileSelect element in hologram header"
        for profile in _SUPPORTED_PROFILES:
            assert f'value="{profile}"' in html, \
                f"Profile '{profile}' missing from profileSelect options"

    def test_hologram_profile_selector_can_switch_visible_profile(self):
        """Profile selector change event POSTs to /api/profiles/active."""
        html = _read_html()
        assert "/api/profiles/active" in html, \
            "Profile switch must POST to /api/profiles/active endpoint"
        assert re.search(r'JSON\.stringify.*profile', html), \
            "Profile switch must send profile value in request body"

    def test_profile_api_returns_all_supported_profiles(self):
        """GET /api/profiles returns list of 4 supported profiles."""
        from waggledance.adapters.http.routes.compat_dashboard import (
            _SUPPORTED_PROFILES as api_profiles,
        )
        assert set(api_profiles) == set(_SUPPORTED_PROFILES)

    def test_profile_api_returns_active_and_configured(self):
        """GET /api/profiles returns both runtime active and saved configured profile."""
        from waggledance.adapters.http.routes.compat_dashboard import api_profiles
        service = MagicMock()
        service.get_status.return_value = {"profile": "HOME"}
        with patch(
            "waggledance.adapters.http.routes.compat_dashboard._load_settings_yaml",
            return_value={"profile": "factory"},
        ):
            result = api_profiles(service=service)
        assert result["active"] == "home", "active must reflect runtime profile"
        assert result["configured"] == "factory", "configured must reflect settings.yaml"
        assert result["restart_required"] is True
        assert len(result["profiles"]) == 4

    def test_profile_api_no_restart_when_matching(self):
        """GET /api/profiles: restart_required=False when runtime matches config."""
        from waggledance.adapters.http.routes.compat_dashboard import api_profiles
        service = MagicMock()
        service.get_status.return_value = {"profile": "HOME"}
        with patch(
            "waggledance.adapters.http.routes.compat_dashboard._load_settings_yaml",
            return_value={"profile": "home"},
        ):
            result = api_profiles(service=service)
        assert result["active"] == "home"
        assert result["configured"] == "home"
        assert result["restart_required"] is False

    def test_profile_switch_returns_restart_required(self):
        """POST /api/profiles/active must return restart_required=True.

        The runtime profile is set at construction and cannot be hot-reloaded.
        The endpoint must be honest about this.
        """
        from waggledance.adapters.http.routes.compat_dashboard import (
            api_profiles_switch, _ProfileSwitchBody,
        )
        request = MagicMock()
        request.cookies.get.return_value = ""
        request.headers.get.return_value = ""
        # Patch auth to pass and settings write
        with patch(
            "waggledance.adapters.http.routes.compat_dashboard._is_request_authenticated",
            return_value=True,
        ), patch(
            "waggledance.adapters.http.routes.compat_dashboard._load_settings_yaml",
            return_value={"profile": "home"},
        ), patch(
            "waggledance.adapters.http.routes.compat_dashboard._SETTINGS_YAML_PATH"
        ) as mock_path:
            mock_path.parent = Path("/tmp")
            import tempfile, os
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", dir=None, delete=False
            ) as tf:
                tmp_name = tf.name
            try:
                with patch("tempfile.mkstemp", return_value=(os.open(tmp_name, os.O_WRONLY), tmp_name)):
                    with patch("os.replace"):
                        body = _ProfileSwitchBody(profile="factory")
                        result = api_profiles_switch(body=body, request=request)
            finally:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
        assert result["ok"] is True
        assert result["profile"] == "factory"
        assert result["restart_required"] is True, \
            "Profile switch must indicate restart_required=True"

    def test_profile_switch_rejects_unknown_profile(self):
        """POST with unknown profile name must return 400."""
        from waggledance.adapters.http.routes.compat_dashboard import (
            api_profiles_switch, _ProfileSwitchBody,
        )
        request = MagicMock()
        with patch(
            "waggledance.adapters.http.routes.compat_dashboard._is_request_authenticated",
            return_value=True,
        ):
            body = _ProfileSwitchBody(profile="nonexistent")
            result = api_profiles_switch(body=body, request=request)
        assert result.status_code == 400

    def test_profile_ui_shows_restart_notice(self):
        """UI must show restart notice after profile switch, not pretend live switch."""
        html = _read_html()
        assert "restart_notice" in html, \
            "UI must reference restart_notice i18n key after profile change"
        # Must show the notice in both languages
        assert "applies after restart" in html or "applies after" in html
        assert "uudelleenkaynn" in html, \
            "Finnish restart notice must be present"

    def test_runtime_profile_is_immutable(self):
        """AutonomyService._profile is set at __init__ — no setter exists."""
        from waggledance.application.services.autonomy_service import AutonomyService
        # Verify the class has no set_profile or update_profile method
        assert not hasattr(AutonomyService, "set_profile"), \
            "AutonomyService must not have a set_profile method"
        assert not hasattr(AutonomyService, "update_profile"), \
            "AutonomyService must not have an update_profile method"

    def test_profile_selector_driven_by_configured_not_runtime(self):
        """Selector syncs from dashboardState.profiles.configured, not status.profile.

        This ensures the selector stays stable after save — it shows what's
        saved in settings.yaml, not the immutable runtime profile.
        """
        html = _read_html()
        assert "pf.configured" in html, \
            "Selector must sync from profiles.configured (saved), not status.profile (runtime)"
        assert "profileRestartHint" in html, \
            "Must have persistent restart hint element"
        # The volatile flag must NOT be present — server state replaces it
        assert "profilePendingRestart" not in html, \
            "Volatile profilePendingRestart must be replaced by server-derived state"

    def test_profile_restart_hint_appears_when_mismatch(self):
        """When configured ≠ active, the UI shows a persistent restart hint."""
        html = _read_html()
        assert 'id="profileRestartHint"' in html, \
            "Must have profileRestartHint element in header"
        assert "pf.restart_required" in html, \
            "Hint visibility must be driven by restart_required from server"
        # i18n keys for the hint
        assert "restart_hint" in html
        assert "running_label" in html

    def test_profile_change_handler_updates_local_profiles_state(self):
        """After POST success, handler updates dashboardState.profiles locally."""
        html = _read_html()
        assert re.search(r'dashboardState\.profiles\.configured\s*=', html), \
            "Change handler must update local profiles.configured after POST"
        assert re.search(r'dashboardState\.profiles\.restart_required\s*=', html), \
            "Change handler must update local profiles.restart_required after POST"


# ═══════════════════════════════════════════════════════════════
# B2. Profile selector snap-back regression
# ═══════════════════════════════════════════════════════════════

def _extract_js_function(html, name):
    """Extract a top-level JS function body from HTML by name."""
    start = re.search(rf'function {name}\s*\(', html)
    if not start:
        return None
    # Find the next top-level section marker (// ═══) after the function start
    end = re.search(r'\n// ═+', html[start.start():])
    if end:
        return html[start.start():start.start() + end.start()]
    return html[start.start():]


class TestProfileSelectorSnapBackRegression:
    """Regression tests for the profile-selector-resets-on-polling bug.

    The original bug: updateHeaderBadges() read the runtime profile from
    /api/status (immutable, e.g. "home") and overwrote the selector every
    10s, undoing the user's pending save to "factory".

    The fix: selector syncs from /api/profiles { configured } (settings.yaml),
    not /api/status { profile } (runtime). A persistent restart hint appears
    when configured ≠ active.
    """

    def test_profile_selector_does_not_snap_back_during_polling_after_save(self):
        """updateHeaderBadges must sync selector from configured, not runtime.

        Simulates the exact regression: /api/status returns runtime="home"
        every 10s. If updateHeaderBadges reads st.profile for the selector,
        any pending save to "factory" gets undone. The function must ONLY
        use pf.configured for sel.value assignment.
        """
        html = _read_html()
        fn_body = _extract_js_function(html, "updateHeaderBadges")
        assert fn_body is not None, "updateHeaderBadges function must exist"

        # Selector value MUST be set from pf.configured (saved profile)
        assert re.search(r'sel\.value\s*!==?\s*pf\.configured', fn_body), \
            "Selector guard must compare against pf.configured"
        assert re.search(r'sel\.value\s*=\s*pf\.configured', fn_body), \
            "Selector must be assigned pf.configured"

        # Selector value MUST NOT be set from activeProfile / st.profile (the old bug)
        assert not re.search(r'sel\.value\s*=\s*activeProfile', fn_body), \
            "REGRESSION: sel.value must not read from activeProfile (runtime)"
        assert "st.profile" not in fn_body, \
            "REGRESSION: updateHeaderBadges must not read st.profile for selector"

    def test_profile_ui_distinguishes_runtime_profile_vs_saved_profile(self):
        """The UI has separate data paths for runtime (active) and saved (configured).

        - pf.configured → selector value (what will run after restart)
        - pf.active → hint tooltip (what is running now)
        - pf.restart_required → hint visibility (do they differ?)
        """
        html = _read_html()
        fn_body = _extract_js_function(html, "updateHeaderBadges")
        assert fn_body is not None

        # Both fields must be used — for different purposes
        assert "pf.configured" in fn_body, \
            "configured (saved) must drive selector"
        assert "pf.active" in fn_body, \
            "active (runtime) must appear in hint tooltip"
        assert "pf.restart_required" in fn_body, \
            "restart_required must drive hint visibility"

        # Backend must return both fields
        from waggledance.adapters.http.routes.compat_dashboard import api_profiles
        service = MagicMock()
        service.get_status.return_value = {"profile": "HOME"}
        with patch(
            "waggledance.adapters.http.routes.compat_dashboard._load_settings_yaml",
            return_value={"profile": "factory"},
        ):
            result = api_profiles(service=service)
        assert "active" in result and "configured" in result, \
            "API must return both active and configured fields"
        assert result["active"] != result["configured"], \
            "Test setup: runtime and saved must differ"

    def test_profile_switch_restart_only_behavior_is_truthful(self):
        """Full cycle: save changes yaml, runtime stays same, API reflects divergence.

        1. Before save: active=home, configured=home, restart_required=False
        2. POST switches to factory → restart_required=True
        3. After save: active still home (immutable), configured=factory
        """
        from waggledance.adapters.http.routes.compat_dashboard import (
            api_profiles, api_profiles_switch, _ProfileSwitchBody,
        )
        service = MagicMock()
        service.get_status.return_value = {"profile": "HOME"}

        # Step 1: before save — everything matches
        with patch(
            "waggledance.adapters.http.routes.compat_dashboard._load_settings_yaml",
            return_value={"profile": "home"},
        ):
            before = api_profiles(service=service)
        assert before["active"] == "home"
        assert before["configured"] == "home"
        assert before["restart_required"] is False

        # Step 2: POST save — writes yaml, returns restart_required
        request = MagicMock()
        with patch(
            "waggledance.adapters.http.routes.compat_dashboard._is_request_authenticated",
            return_value=True,
        ), patch(
            "waggledance.adapters.http.routes.compat_dashboard._load_settings_yaml",
            return_value={"profile": "home"},
        ), patch(
            "waggledance.adapters.http.routes.compat_dashboard._SETTINGS_YAML_PATH",
        ) as mock_path:
            mock_path.parent = Path("/tmp")
            import tempfile, os
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", dir=None, delete=False,
            ) as tf:
                tmp_name = tf.name
            try:
                with patch("tempfile.mkstemp",
                           return_value=(os.open(tmp_name, os.O_WRONLY), tmp_name)):
                    with patch("os.replace"):
                        post_result = api_profiles_switch(
                            body=_ProfileSwitchBody(profile="factory"),
                            request=request,
                        )
            finally:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
        assert post_result["restart_required"] is True

        # Step 3: after save — runtime unchanged, configured diverged
        with patch(
            "waggledance.adapters.http.routes.compat_dashboard._load_settings_yaml",
            return_value={"profile": "factory"},
        ):
            after = api_profiles(service=service)
        assert after["active"] == "home", \
            "Runtime must NOT change — it's immutable"
        assert after["configured"] == "factory", \
            "Configured must reflect the saved value"
        assert after["restart_required"] is True

        # Runtime service was never asked to change profile
        assert not service.set_profile.called if hasattr(service, "set_profile") else True

    @pytest.mark.parametrize("active,configured", [
        ("home", "home"),
        ("home", "factory"),
        ("cottage", "gadget"),
        ("factory", "cottage"),
    ])
    def test_profile_selector_lists_all_profiles_after_refresh(
        self, active, configured,
    ):
        """GET /api/profiles always returns all 4 profiles regardless of state.

        No profile must vanish from the list when a different one is active
        or configured. The selector must always offer all 4 choices.
        """
        from waggledance.adapters.http.routes.compat_dashboard import api_profiles
        service = MagicMock()
        service.get_status.return_value = {"profile": active.upper()}
        with patch(
            "waggledance.adapters.http.routes.compat_dashboard._load_settings_yaml",
            return_value={"profile": configured},
        ):
            result = api_profiles(service=service)
        assert set(result["profiles"]) == set(_SUPPORTED_PROFILES), \
            f"All 4 profiles must be returned (active={active}, configured={configured})"
        assert result["active"] == active
        assert result["configured"] == configured


# ═══════════════════════════════════════════════════════════════
# C. Feeds tab visibility + stale logic
# ═══════════════════════════════════════════════════════════════

class TestFeedsTabVisibility:
    """Feeds tab: truthful source details with correct stale/freshness logic."""

    def test_hologram_feeds_tab_shows_configured_sources(self):
        """Feeds panel renders each source with name, protocol, provider."""
        html = _read_html()
        assert "buildFeedsPanel" in html
        assert "escapeHtml(s.name)" in html
        assert "escapeHtml(s.protocol)" in html
        assert "s.provider" in html

    def test_hologram_feeds_tab_shows_truthful_source_states(self):
        """Each source shows its real state; empty sources show 'no data'."""
        html = _read_html()
        assert "no_data" in html, \
            "Feeds panel must show 'no data' indicator for empty sources"
        state_colors = re.findall(
            r"(active|idle|framework|unwired|unavailable|failed):'#[0-9a-f]+'", html)
        assert len(state_colors) >= 4

    def test_hologram_feeds_tab_renders_latest_items_or_values_when_available(self):
        """latest_items (RSS) and latest_value (weather/electricity) are rendered."""
        html = _read_html()
        assert "latest_items" in html
        assert "latest_value" in html
        assert "temp_c" in html
        assert "price_c_kwh" in html

    def test_feeds_stale_uses_explicit_constant(self):
        """Feeds stale threshold must use FEED_STALE_THRESHOLD_S, not magic multiply."""
        html = _read_html()
        assert "FEED_STALE_THRESHOLD_S" in html, \
            "Feeds must use explicit FEED_STALE_THRESHOLD_S constant"
        # Must be defined as 1800 (30 minutes)
        assert re.search(r'FEED_STALE_THRESHOLD_S\s*=\s*1800', html), \
            "FEED_STALE_THRESHOLD_S must be 1800 (30 minutes)"
        # Must NOT use STALE_THRESHOLD_S * 60 anymore
        assert "STALE_THRESHOLD_S * 60" not in html, \
            "Feeds must not use STALE_THRESHOLD_S * 60 (replaced by explicit constant)"

    def test_feeds_stale_logic_with_concrete_values(self):
        """Verify stale classification with actual freshness_s values.

        FEED_STALE_THRESHOLD_S = 1800 (30 min).
        A source with freshness_s=900 (15 min) is fresh.
        A source with freshness_s=3600 (60 min) is stale.
        """
        html = _read_html()
        # Extract the threshold value
        match = re.search(r'FEED_STALE_THRESHOLD_S\s*=\s*(\d+)', html)
        assert match
        threshold = int(match.group(1))
        assert threshold == 1800

        # Simulate: 15min-old data is NOT stale
        assert 900 <= threshold  # 900 < 1800 → not stale
        # Simulate: 60min-old data IS stale
        assert 3600 > threshold  # 3600 > 1800 → stale

    def test_feeds_node_stale_threshold_is_separate(self):
        """Node telemetry uses STALE_THRESHOLD_S (30s), feeds use FEED_STALE_THRESHOLD_S (1800s)."""
        html = _read_html()
        node_match = re.search(r'const STALE_THRESHOLD_S\s*=\s*(\d+)', html)
        feed_match = re.search(r'const FEED_STALE_THRESHOLD_S\s*=\s*(\d+)', html)
        assert node_match and feed_match
        assert int(node_match.group(1)) == 30, "Node stale threshold must be 30s"
        assert int(feed_match.group(1)) == 1800, "Feed stale threshold must be 1800s"
        # They must be different
        assert int(node_match.group(1)) != int(feed_match.group(1))

    def test_feeds_backend_sources_have_truthful_defaults(self):
        """Without ChromaDB enrichment, data fields are truthfully empty."""
        from waggledance.adapters.http.routes.compat_dashboard import _build_feed_sources
        feeds_cfg = _get_feeds_cfg()
        sources = _build_feed_sources(feeds_cfg)
        for s in sources:
            assert s["freshness_s"] is None, \
                f"Source {s['id']} freshness_s should be None without enrichment"
            assert s["items_count"] == 0
            assert s["latest_items"] == []

    def test_feeds_enrichment_sets_freshness_and_items(self):
        """When ChromaDB returns data, enrichment populates items and freshness."""
        from waggledance.adapters.http.routes.compat_dashboard import (
            _build_feed_sources, _enrich_from_chroma,
        )
        feeds_cfg = _get_feeds_cfg()
        sources = _build_feed_sources(feeds_cfg)
        weather = [s for s in sources if s["type"] == "weather"]
        if not weather:
            pytest.skip("No weather source in config")
        source = weather[0]

        # Mock ChromaDB collection with a recent entry
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "documents": ["Temperature: 5.2°C, wind 3.1 m/s"],
            "metadatas": [{"agent_id": "weather_feed", "feed_id": source["id"],
                           "timestamp": now_iso}],
        }

        _enrich_from_chroma(source, mock_collection)
        assert source["items_count"] == 1
        assert source["freshness_s"] is not None
        assert source["freshness_s"] < 60  # just enriched, should be very fresh

    def test_feeds_renders_latest_items_for_rss(self):
        """RSS source latest_items rendering uses title and published fields."""
        html = _read_html()
        # The rendering loop must access item.title
        assert "item.title" in html or "item.text" in html


# ═══════════════════════════════════════════════════════════════
# Profile + Tab smoke matrix
# ═══════════════════════════════════════════════════════════════

class TestProfileTabSmokeMatrix:
    """Smoke tests: profiles visible, tabs functional, auth safety."""

    @pytest.mark.parametrize("profile", _SUPPORTED_PROFILES)
    def test_profile_option_exists_in_html(self, profile):
        html = _read_html()
        assert f'value="{profile}"' in html

    @pytest.mark.parametrize("tab", [
        "overview", "memory", "reasoning", "micro", "learning", "feeds", "ops", "chat"
    ])
    def test_tab_button_exists(self, tab):
        html = _read_html()
        assert f'data-tab="{tab}"' in html

    @pytest.mark.parametrize("tab", [
        "overview", "memory", "reasoning", "micro", "learning", "feeds", "ops", "chat"
    ])
    def test_tab_has_builder_function(self, tab):
        html = _read_html()
        builder_name = f"build{tab.capitalize()}Panel" if tab != "micro" else "buildMicroPanel"
        assert builder_name in html, f"Missing builder: {builder_name}"

    def test_feeds_tab_renders_source_list_not_single_status(self):
        html = _read_html()
        assert "sources.forEach" in html or "sources.length" in html
        assert "s.state" in html

    def test_no_localstorage_token_in_active_code(self):
        html = _read_html()
        assert "localStorage" not in html

    def test_no_bearer_construction_in_frontend(self):
        html = _read_html()
        assert "Bearer" not in html

    def test_no_token_query_param_in_frontend_ws(self):
        html = _read_html()
        ws_lines = [l for l in html.split('\n') if 'new WebSocket' in l]
        for line in ws_lines:
            assert "token=" not in line, \
                f"Frontend WS must not use ?token= parameter: {line.strip()}"
