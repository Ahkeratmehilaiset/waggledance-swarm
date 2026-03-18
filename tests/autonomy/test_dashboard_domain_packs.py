"""
Tests for dashboard domain packs and profile-dependent rendering.

Validates that:
- All 5 profiles are present in domainPacks.js
- No bee terminology leaks into non-apiary profiles
- Apiary content is preserved
- ReasoningDashboard.jsx includes all 5 profiles
- Profile/color/icon/label consistency across files
"""

import re
import sys
from pathlib import Path

import pytest

DASHBOARD_SRC = Path(__file__).resolve().parents[2] / "dashboard" / "src"


@pytest.fixture(scope="module")
def domain_packs_js():
    """Load domainPacks.js content."""
    path = DASHBOARD_SRC / "domainPacks.js"
    assert path.exists(), f"domainPacks.js not found at {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_jsx():
    """Load App.jsx content."""
    path = DASHBOARD_SRC / "App.jsx"
    assert path.exists(), f"App.jsx not found at {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def reasoning_jsx():
    """Load ReasoningDashboard.jsx content."""
    path = DASHBOARD_SRC / "ReasoningDashboard.jsx"
    assert path.exists(), f"ReasoningDashboard.jsx not found at {path}"
    return path.read_text(encoding="utf-8")


ALL_PROFILES = ["gadget", "cottage", "home", "factory", "apiary"]

# Bee-specific terms that should NOT appear in non-apiary contexts
BEE_TERMS = [
    "bee", "hive", "varroa", "foulbrood", "queen piping",
    "honey flow", "swarming", "beekeeping",
    "mehiläi", "pesä", "parveilu", "esikotelomätä",
    "mehiläishoitaj",
]


# ── domainPacks.js structure ──────────────────────────────────

class TestDomainPacksStructure:
    """Verify domainPacks.js has all 5 profiles in every section."""

    def test_hw_specs_all_profiles(self, domain_packs_js):
        for p in ALL_PROFILES:
            assert f"{p}:" in domain_packs_js or f'"{p}"' in domain_packs_js, \
                f"HW_SPECS missing profile: {p}"

    def test_domain_labels_en_all_profiles(self, domain_packs_js):
        for p in ALL_PROFILES:
            # Check within DOMAIN_LABELS section
            assert re.search(rf'{p}:\s*\{{\s*label:', domain_packs_js), \
                f"DOMAIN_LABELS.en missing profile: {p}"

    def test_profile_icons_all_profiles(self, domain_packs_js):
        for p in ALL_PROFILES:
            assert re.search(rf'{p}:\s*"[^"]+?"', domain_packs_js), \
                f"PROFILE_ICONS may be missing profile: {p}"

    def test_domain_ids_all_profiles(self, domain_packs_js):
        # DOMAIN_IDS should list all 5
        domain_ids_match = re.search(r'DOMAIN_IDS\s*=\s*\[([^\]]+)\]', domain_packs_js)
        assert domain_ids_match, "DOMAIN_IDS not found"
        ids_str = domain_ids_match.group(1)
        for p in ALL_PROFILES:
            assert f'"{p}"' in ids_str, f"DOMAIN_IDS missing: {p}"

    def test_exports_all_expected(self, domain_packs_js):
        expected_exports = [
            "HW_SPECS", "DOMAIN_LABELS", "PROFILE_ICONS", "PROFILE_COLORS",
            "PROFILE_COLORS_RGB", "FEATS_EN", "FEATS_FI", "HEARTBEATS_EN",
            "HEARTBEATS_FI", "DEMO_HB_EN", "DEMO_HB_FI", "PREDICTION_MODELS",
            "QUICK_ACTIONS", "PURPOSE", "DOMAIN_IDS",
        ]
        for exp in expected_exports:
            assert exp in domain_packs_js, f"Export missing: {exp}"


# ── Bee terminology isolation ──────────────────────────────────

class TestBeeTerminologyIsolation:
    """Ensure bee-specific terms only appear in apiary sections."""

    def _extract_profile_section(self, js_content, section_name, profile):
        """Extract content for a specific profile from a named section."""
        # This is a heuristic - find profile key within the section
        pattern = rf'{section_name}.*?{profile}:\s*[\[{{]'
        match = re.search(pattern, js_content, re.DOTALL)
        if not match:
            return ""
        start = match.end()
        # Find the next profile key or section end
        other_profiles = [p for p in ALL_PROFILES if p != profile]
        end_patterns = [rf'\n\s*{p}:\s*[\[{{]' for p in other_profiles]
        end_patterns.append(r'\n\s*\};')
        end_patterns.append(r'\nconst ')
        min_end = len(js_content)
        for ep in end_patterns:
            m = re.search(ep, js_content[start:])
            if m and start + m.start() < min_end:
                min_end = start + m.start()
        return js_content[start:min_end]

    @pytest.mark.parametrize("profile", ["gadget", "cottage", "home", "factory"])
    def test_feats_en_no_bee_terms(self, domain_packs_js, profile):
        section = self._extract_profile_section(domain_packs_js, "FEATS_EN", profile)
        for term in BEE_TERMS:
            assert term.lower() not in section.lower(), \
                f"Bee term '{term}' found in FEATS_EN.{profile}"

    @pytest.mark.parametrize("profile", ["gadget", "cottage", "home", "factory"])
    def test_heartbeats_en_no_bee_terms(self, domain_packs_js, profile):
        section = self._extract_profile_section(domain_packs_js, "HEARTBEATS_EN", profile)
        for term in BEE_TERMS:
            assert term.lower() not in section.lower(), \
                f"Bee term '{term}' found in HEARTBEATS_EN.{profile}"

    @pytest.mark.parametrize("profile", ["gadget", "cottage", "home", "factory"])
    def test_demo_hb_en_no_bee_terms(self, domain_packs_js, profile):
        section = self._extract_profile_section(domain_packs_js, "DEMO_HB_EN", profile)
        for term in BEE_TERMS:
            assert term.lower() not in section.lower(), \
                f"Bee term '{term}' found in DEMO_HB_EN.{profile}"

    def test_apiary_preserves_bee_terms(self, domain_packs_js):
        """Apiary profile SHOULD contain bee-specific terminology."""
        apiary_feats = self._extract_profile_section(domain_packs_js, "FEATS_EN", "apiary")
        # At least some bee terms should be present
        found = any(term.lower() in apiary_feats.lower()
                    for term in ["hive", "bee", "varroa", "honey"])
        assert found, "Apiary FEATS_EN should contain bee-specific terms"


# ── App.jsx imports ────────────────────────────────────────────

class TestAppJsxImports:
    """Verify App.jsx imports from domainPacks instead of inline data."""

    def test_imports_from_domain_packs(self, app_jsx):
        assert 'from "./domainPacks"' in app_jsx

    def test_uses_hw_specs(self, app_jsx):
        assert "HW_SPECS" in app_jsx

    def test_uses_domain_labels(self, app_jsx):
        assert "DOMAIN_LABELS.en" in app_jsx
        assert "DOMAIN_LABELS.fi" in app_jsx

    def test_uses_feats_imports(self, app_jsx):
        assert "FEATS_EN" in app_jsx
        assert "FEATS_FI" in app_jsx

    def test_uses_heartbeats_imports(self, app_jsx):
        assert "HEARTBEATS_EN" in app_jsx
        assert "HEARTBEATS_FI" in app_jsx

    def test_uses_demo_hb_imports(self, app_jsx):
        assert "DEMO_HB_EN" in app_jsx
        assert "DEMO_HB_FI" in app_jsx

    def test_uses_domain_ids_import(self, app_jsx):
        assert "DOMAIN_IDS" in app_jsx

    def test_no_inline_cottage_bee_content(self, app_jsx):
        """Cottage content in L object should not contain bee terms."""
        # The L object's cottage content should now come from imports
        # Check that the inline cottage data with bee terms was removed
        assert "Disease alerts + bee news" not in app_jsx
        assert "Becomes your bee expert" not in app_jsx
        assert "Listen to your bees" not in app_jsx


# ── App.jsx architecture text ──────────────────────────────────

class TestArchitectureText:
    """Verify architecture text is domain-agnostic."""

    def test_no_bee_audio_analyzer(self, app_jsx):
        assert "BeeAudioAnalyzer" not in app_jsx

    def test_no_foulbrood_in_info(self, app_jsx):
        # "foulbrood" should not appear in the info/architecture text
        # It can appear in the apiary demoHb via imports, but not inline
        assert "foulbrood → CRITICAL" not in app_jsx
        assert "esikotelomätä → KRIITTINEN" not in app_jsx

    def test_audio_analyzer_generic(self, app_jsx):
        assert "AudioAnalyzer" in app_jsx


# ── ReasoningDashboard.jsx profiles ────────────────────────────

class TestReasoningDashboardProfiles:
    """Verify ReasoningDashboard includes all 5 profiles."""

    def test_profiles_array_has_apiary(self, reasoning_jsx):
        profiles_match = re.search(r'PROFILES\s*=\s*\[([^\]]+)\]', reasoning_jsx)
        assert profiles_match, "PROFILES const not found"
        profiles_str = profiles_match.group(1)
        for p in ALL_PROFILES:
            assert f'"{p}"' in profiles_str, f"PROFILES missing: {p}"

    def test_profile_colors_has_apiary(self, reasoning_jsx):
        assert "apiary:" in reasoning_jsx or '"apiary"' in reasoning_jsx

    def test_purpose_en_has_apiary(self, reasoning_jsx):
        # Check apiary purpose exists in English
        assert re.search(r'apiary:\s*".*hive', reasoning_jsx), \
            "EN purpose missing apiary"

    def test_purpose_fi_has_apiary(self, reasoning_jsx):
        assert re.search(r'apiary:\s*".*pesi', reasoning_jsx, re.IGNORECASE), \
            "FI purpose missing apiary"

    def test_quick_actions_en_has_apiary(self, reasoning_jsx):
        assert re.search(r'apiary:\s*\[.*hive', reasoning_jsx), \
            "EN quickActions missing apiary"

    def test_quick_actions_fi_has_apiary(self, reasoning_jsx):
        assert re.search(r'apiary:\s*\[.*pesä', reasoning_jsx, re.IGNORECASE), \
            "FI quickActions missing apiary"

    def test_model_map_has_apiary(self, reasoning_jsx):
        assert "hive_weight_trend" in reasoning_jsx, \
            "modelMap missing apiary model"


# ── Profile consistency ────────────────────────────────────────

class TestProfileConsistency:
    """Cross-file consistency checks."""

    def test_same_profile_count(self, domain_packs_js, reasoning_jsx):
        """Both files should reference all 5 profiles."""
        for p in ALL_PROFILES:
            assert p in domain_packs_js, f"domainPacks.js missing {p}"
            assert p in reasoning_jsx, f"ReasoningDashboard.jsx missing {p}"

    def test_prediction_models_all_profiles(self, domain_packs_js):
        for p in ALL_PROFILES:
            pattern = rf'{p}:\s*"[^"]+"'
            assert re.search(pattern, domain_packs_js), \
                f"PREDICTION_MODELS missing: {p}"

    def test_quick_actions_all_profiles(self, domain_packs_js):
        quick_section = domain_packs_js[domain_packs_js.index("QUICK_ACTIONS"):]
        for p in ALL_PROFILES:
            assert f"{p}:" in quick_section, f"QUICK_ACTIONS missing: {p}"

    def test_purpose_all_profiles(self, domain_packs_js):
        purpose_section = domain_packs_js[domain_packs_js.index("PURPOSE"):]
        for p in ALL_PROFILES:
            assert f"{p}:" in purpose_section, f"PURPOSE missing: {p}"
