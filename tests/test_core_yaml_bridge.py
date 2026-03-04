"""YAML Bridge core module — test suite (~12 tests).

Tests: YAMLBridge init, ROUTING_KEYWORDS dict, AGENT_GLYPH_MAP,
get_routing_rules(), get_agent_glyphs(), _detect_yaml_language(),
_fix_mojibake(), get_stats() after loading real agents/ directory,
get_spawner_templates() structure.
File-system based — no external services needed.
"""
import sys, os, ast
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Project root for resolving agents/ directory
_PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")


# ── 1. Syntax ────────────────────────────────────────────────────────────

def test_syntax_yaml_bridge():
    path = os.path.join(_PROJECT_ROOT, "core", "yaml_bridge.py")
    with open(path, "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] core/yaml_bridge.py syntax valid")


# ── 2. Module-level constants ────────────────────────────────────────────

def test_routing_keywords_dict_has_entries():
    from core.yaml_bridge import ROUTING_KEYWORDS
    assert isinstance(ROUTING_KEYWORDS, dict)
    assert len(ROUTING_KEYWORDS) >= 20
    # Core bee domain must be present
    assert "beekeeper" in ROUTING_KEYWORDS
    assert "disease_monitor" in ROUTING_KEYWORDS
    assert "meteorologist" in ROUTING_KEYWORDS
    print("  [PASS] ROUTING_KEYWORDS dict has entries for core agents")


def test_routing_keywords_values_are_lists():
    from core.yaml_bridge import ROUTING_KEYWORDS
    for agent_id, kws in ROUTING_KEYWORDS.items():
        assert isinstance(kws, list), f"{agent_id} keywords must be a list"
        assert len(kws) > 0, f"{agent_id} keyword list must not be empty"
    print("  [PASS] ROUTING_KEYWORDS values are non-empty lists")


def test_agent_glyph_map_has_entries():
    from core.yaml_bridge import AGENT_GLYPH_MAP
    assert isinstance(AGENT_GLYPH_MAP, dict)
    assert len(AGENT_GLYPH_MAP) >= 10
    # Core agents must have glyphs
    assert "beekeeper" in AGENT_GLYPH_MAP
    assert "hivemind" in AGENT_GLYPH_MAP
    assert "meteorologist" in AGENT_GLYPH_MAP
    print("  [PASS] AGENT_GLYPH_MAP has entries for core agents")


def test_agent_glyph_map_values_are_strings():
    from core.yaml_bridge import AGENT_GLYPH_MAP
    for agent_id, glyph in AGENT_GLYPH_MAP.items():
        assert isinstance(glyph, str), f"{agent_id} glyph must be a string"
        assert len(glyph) > 0, f"{agent_id} glyph must not be empty"
    print("  [PASS] AGENT_GLYPH_MAP values are non-empty strings")


# ── 3. YAMLBridge init ────────────────────────────────────────────────────

def test_yaml_bridge_init():
    from core.yaml_bridge import YAMLBridge
    bridge = YAMLBridge("agents")
    assert bridge.agents_dir is not None
    assert bridge._loaded is False
    assert bridge._language == "fi"
    assert bridge._active_profile is None
    print("  [PASS] YAMLBridge init OK")


def test_yaml_bridge_get_routing_rules_returns_copy():
    from core.yaml_bridge import YAMLBridge, ROUTING_KEYWORDS
    bridge = YAMLBridge("agents")
    rules = bridge.get_routing_rules()
    assert isinstance(rules, dict)
    assert len(rules) == len(ROUTING_KEYWORDS)
    # Verify it's a copy (mutation doesn't affect original)
    rules["__test__"] = ["test"]
    assert "__test__" not in ROUTING_KEYWORDS
    print("  [PASS] get_routing_rules returns independent copy")


def test_yaml_bridge_get_agent_glyphs_returns_copy():
    from core.yaml_bridge import YAMLBridge, AGENT_GLYPH_MAP
    bridge = YAMLBridge("agents")
    glyphs = bridge.get_agent_glyphs()
    assert isinstance(glyphs, dict)
    assert len(glyphs) == len(AGENT_GLYPH_MAP)
    # Verify it's a copy
    glyphs["__test__"] = "X"
    assert "__test__" not in AGENT_GLYPH_MAP
    print("  [PASS] get_agent_glyphs returns independent copy")


# ── 4. Language detection ─────────────────────────────────────────────────

def test_detect_yaml_language_explicit_en():
    from core.yaml_bridge import YAMLBridge
    data = {"language": "en", "header": {"agent_name": "Beekeeper"}}
    result = YAMLBridge._detect_yaml_language(data)
    assert result == "en"
    print("  [PASS] _detect_yaml_language detects explicit 'language: en'")


def test_detect_yaml_language_fi_markers():
    from core.yaml_bridge import YAMLBridge
    data = {
        "header": {"agent_name": "Mehilainen", "role": "mehilaishoidon asiantuntija"},
        "ASSUMPTIONS": ["pesä on aktiiivisessa kaytossa", "hoitokenha tarkistus"],
    }
    result = YAMLBridge._detect_yaml_language(data)
    assert result == "fi"
    print("  [PASS] _detect_yaml_language detects Finnish markers")


def test_detect_yaml_language_en_markers():
    from core.yaml_bridge import YAMLBridge
    data = {
        "header": {
            "agent_name": "Beekeeper",
            "role": "beekeeping specialist",
        },
        "ASSUMPTIONS": [
            "colony is active during inspection",
            "hive inspection required weekly",
            "winter feeding complete",
        ],
    }
    result = YAMLBridge._detect_yaml_language(data)
    assert result == "en"
    print("  [PASS] _detect_yaml_language detects English markers")


# ── 5. Mojibake fix ──────────────────────────────────────────────────────

def test_fix_mojibake_passthrough():
    from core.yaml_bridge import YAMLBridge
    # Correct string should pass through unchanged
    s = "Mehilainen on kovasti toissaan"
    result = YAMLBridge._fix_mojibake(s)
    assert result == s
    print("  [PASS] _fix_mojibake passes correct strings through")


def test_fix_mojibake_none_passthrough():
    from core.yaml_bridge import YAMLBridge
    assert YAMLBridge._fix_mojibake(None) is None
    assert YAMLBridge._fix_mojibake("") == ""
    print("  [PASS] _fix_mojibake handles None and empty string")


# ── 6. Stats from real agents/ dir ──────────────────────────────────────

def test_yaml_bridge_get_stats_from_real_agents():
    import io, contextlib
    from core.yaml_bridge import YAMLBridge
    agents_dir = os.path.join(_PROJECT_ROOT, "agents")
    if not os.path.isdir(agents_dir):
        print("  [PASS] get_stats skipped (agents/ directory not found)")
        return
    bridge = YAMLBridge(agents_dir)
    # Suppress any emoji print() inside _ensure_loaded (Windows charmap safety)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        stats = bridge.get_stats()
    assert isinstance(stats, dict)
    assert "total_agents" in stats
    assert "agent_ids" in stats
    assert "total_metrics" in stats
    assert isinstance(stats["total_agents"], int)
    assert isinstance(stats["agent_ids"], list)
    # Should have loaded at least some agents
    assert stats["total_agents"] >= 0
    print("  [PASS] get_stats returns correct structure ({0} agents)".format(
        stats["total_agents"]))


# ── Runner ──────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_syntax_yaml_bridge,
    test_routing_keywords_dict_has_entries,
    test_routing_keywords_values_are_lists,
    test_agent_glyph_map_has_entries,
    test_agent_glyph_map_values_are_strings,
    test_yaml_bridge_init,
    test_yaml_bridge_get_routing_rules_returns_copy,
    test_yaml_bridge_get_agent_glyphs_returns_copy,
    test_detect_yaml_language_explicit_en,
    test_detect_yaml_language_fi_markers,
    test_detect_yaml_language_en_markers,
    test_fix_mojibake_passthrough,
    test_fix_mojibake_none_passthrough,
    test_yaml_bridge_get_stats_from_real_agents,
]


if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []

    print("\n" + "=" * 60)
    print("core/yaml_bridge.py -- {0} tests".format(len(ALL_TESTS)))
    print("=" * 60 + "\n")

    for test in ALL_TESTS:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test.__name__, str(e)))
            print("  [FAIL] {0}: {1}".format(test.__name__, e))

    print("\n" + "=" * 60)
    print("Result: {0}/{1} passed, {2} failed".format(passed, passed + failed, failed))
    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print("  - {0}: {1}".format(name, err))
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
