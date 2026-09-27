# SPDX-License-Identifier: BUSL-1.1
"""Model registry and read-only value analysis (PR-6)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import tools.wd_model_registry as reg_module
from tools.lane_profile_catalog import load_catalog
from tools.wd_model_registry import (
    RegistryError,
    catalog_diff,
    codex_cli_models,
    dominated_by,
    frontier,
    lane_value_table,
    load_registry,
    main,
    parse_current,
    report,
    rows,
    validate_registry,
    value_for,
)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY, REGISTRY_SHA = load_registry(ROOT / "configs" / "model_registry.json")
CATALOG, CATALOG_SHA = load_catalog(ROOT / "tests" / "fixtures" / "lane_profile_catalog_frozen_20260927.json")
CURRENT = {"codex-lead-1": "gpt-5.6-terra:medium", "codex-tools-1": "gpt-6-luna:low",
           "claude-rco-1": "claude-sonnet-5:xhigh", "claude-rco-2": "claude-sonnet-5:xhigh",
           "fable-5": "claude-opus-5-5:medium"}


def row(provider, model, effort, quality, cost):
    return {"provider": provider, "model": model, "effort": effort, "quality": float(quality),
            "cost": float(cost), "coding_agent_index": None}


def write(tmp_path, value, name="registry.json") -> Path:
    path = tmp_path / name
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


# ---------------------------------------------------------------- the shipped registry

def test_the_shipped_registry_validates_and_is_sourced():
    assert REGISTRY["schema"] == "wd.model-registry.v1" and len(REGISTRY_SHA) == 64
    assert all(url.startswith("https://") for url in REGISTRY["benchmark"]["sources"])
    assert {entry["provider"] for entry in REGISTRY["models"].values()} == {"codex", "claude"}
    assert REGISTRY["models"]["claude/claude-opus-5-5"]["efforts"]["medium"] == \
        {"intelligence_index": 51, "usd_per_task": 1.34}


# ---------------------------------------------------------------- validation

def mutate(change):
    registry = copy.deepcopy(REGISTRY)
    change(registry)
    return registry


@pytest.mark.parametrize("change", [
    lambda r: r.update(schema="wd.model-registry.v0"),
    lambda r: r.update(extra=1),
    lambda r: r.pop("coding_benchmark"),
    lambda r: r["benchmark"].update(extra=1),
    lambda r: r["benchmark"].update(sources=[]),
    lambda r: r["benchmark"].update(sources=["http://insecure.example"]),
    lambda r: r["coding_benchmark"].update(name=""),
    lambda r: r.update(models={}),
    lambda r: r["models"]["codex/gpt-6-sol"].update(provider="gemini"),
    lambda r: r["models"]["codex/gpt-6-sol"].update(model="gpt-6-luna"),       # key no longer matches
    lambda r: r["models"]["codex/gpt-6-sol"].update(extra=1),
    lambda r: r["models"]["codex/gpt-6-sol"].update(coding_agent_index=101),
    lambda r: r["models"]["codex/gpt-6-sol"].update(efforts={}),
    lambda r: r["models"]["codex/gpt-6-sol"]["efforts"].update(ultra={"intelligence_index": 1, "usd_per_task": 1}),
    lambda r: r["models"]["codex/gpt-6-sol"]["efforts"]["low"].update(intelligence_index=-1),
    lambda r: r["models"]["codex/gpt-6-sol"]["efforts"]["low"].update(intelligence_index=True),
    lambda r: r["models"]["codex/gpt-6-sol"]["efforts"]["low"].update(usd_per_task="0.1"),
    lambda r: r["models"]["codex/gpt-6-sol"]["efforts"]["low"].update(usd_per_task=1000.5),
    lambda r: r["models"]["codex/gpt-6-sol"]["efforts"]["low"].pop("usd_per_task"),
    lambda r: r["models"]["codex/gpt-6-sol"]["efforts"]["low"].update(note="x"),
])
def test_every_malformed_registry_is_refused(change):
    with pytest.raises(RegistryError):
        validate_registry(mutate(change))


@pytest.mark.parametrize("value", [None, [], "x", 1])
def test_a_non_object_registry_is_refused(value):
    with pytest.raises(RegistryError):
        validate_registry(value)


@pytest.mark.parametrize("text,match", [
    ('{"schema": 1, "schema": 2}', "duplicate key"),
    ('{"a": NaN}', "non-finite"),
    ('{"a": Infinity}', "non-finite"),
    ("[" * 100000, "not UTF-8 JSON"),
], ids=["duplicate-key", "nan", "infinity", "deep-nesting"])
def test_unsafe_json_is_refused(tmp_path, text, match):
    with pytest.raises(RegistryError, match=match):
        load_registry(write(tmp_path, text))


def test_a_registry_over_the_size_bound_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(reg_module, "MAX_REGISTRY_BYTES", 100)
    with pytest.raises(RegistryError, match="size bound"):
        load_registry(write(tmp_path, REGISTRY))


def test_a_registry_exactly_at_the_size_bound_loads(tmp_path, monkeypatch):
    path = write(tmp_path, REGISTRY)
    monkeypatch.setattr(reg_module, "MAX_REGISTRY_BYTES", path.stat().st_size)
    assert load_registry(path)[0]["schema"] == "wd.model-registry.v1"


def test_a_missing_registry_is_a_registry_error(tmp_path):
    with pytest.raises(RegistryError, match="unreadable"):
        load_registry(tmp_path / "missing.json")


# ---------------------------------------------------------------- frontier and dominance

def test_dominance_needs_at_least_as_good_and_at_most_as_costly_and_one_strict():
    a, b = row("codex", "a", "low", 30, 1.0), row("codex", "b", "low", 20, 1.0)
    twin = row("codex", "c", "low", 30, 1.0)
    cheaper = row("codex", "d", "low", 30, 0.5)
    other_provider = row("claude", "e", "low", 99, 0.01)
    table = [a, b, twin, cheaper, other_provider]
    assert dominated_by(b, table)[0] == cheaper                   # best quality first, then cheapest
    assert dominated_by(a, table) == [cheaper]                    # a twin never dominates
    assert dominated_by(cheaper, table) == []                     # another provider never dominates
    front = frontier(table)
    assert front == {"claude": [other_provider], "codex": [cheaper]}


def test_the_shipped_frontier_drops_dominated_models():
    front = frontier(rows(REGISTRY))
    claude = {(r["model"], r["effort"]) for r in front["claude"]}
    codex = {(r["model"], r["effort"]) for r in front["codex"]}
    assert ("claude-fable-5-1", "high") not in claude and ("claude-opus-5-5", "medium") in claude
    assert not any(model.startswith("gpt-5.6") for model, _ in codex)
    assert ("gpt-6-sol", "high") in codex and ("gpt-6-luna", "xhigh") in codex


# ---------------------------------------------------------------- value per lane

def test_value_for_a_dominated_current_offers_both_directions():
    table = [row("codex", "cur", "low", 30, 1.0), row("codex", "better", "low", 40, 0.9),
             row("codex", "cheap", "low", 31, 0.2), row("codex", "pricey", "low", 60, 5.0)]
    value = value_for(table[0], table)
    assert value["dominated"] is True
    assert (value["more_for_same_money"]["model"], value["more_for_same_money"]["quality_delta"]) == ("better", 10.0)
    assert (value["same_for_less"]["model"], value["same_for_less"]["cost_delta_percent"]) == ("cheap", -80.0)


def test_value_for_a_frontier_current_offers_nothing():
    table = [row("codex", "cur", "low", 30, 1.0), row("codex", "weaker", "low", 20, 0.5),
             row("codex", "pricier", "low", 40, 2.0)]
    value = value_for(table[0], table)
    assert value == {"more_for_same_money": None, "same_for_less": None, "dominated": False}


def test_a_free_current_has_no_percentage():
    table = [row("codex", "cur", "low", 30, 0.0), row("codex", "better", "low", 35, 0.0)]
    assert value_for(table[0], table)["more_for_same_money"]["cost_delta_percent"] is None


def test_reviewer_lanes_are_never_offered_lower_quality():
    table = lane_value_table(REGISTRY, CATALOG, CURRENT)
    for lane, entry in table.items():
        for key in ("more_for_same_money", "same_for_less"):
            suggestion = entry[key]
            if suggestion is not None:
                assert suggestion["quality_delta"] >= 0, (lane, key)
                assert suggestion["cost_delta_percent"] is None or suggestion["cost_delta_percent"] <= 0


def test_the_shipped_value_table_matches_the_hand_comparison():
    table = lane_value_table(REGISTRY, CATALOG, CURRENT)
    rco = table["claude-rco-1"]
    assert rco["dominated"] is True and rco["current"]["catalog_profile"] == "claude-sonnet-5-xhigh"
    assert (rco["more_for_same_money"]["model"], rco["more_for_same_money"]["effort"]) == ("claude-opus-5-5", "high")
    assert rco["same_for_less"]["model"] == "claude-opus-5-5"
    lead = table["codex-lead-1"]
    assert lead["dominated"] is True and lead["more_for_same_money"]["coding_agent_index"] == 41.1


@pytest.mark.parametrize("value,status", [(None, "current_unknown"), ("gpt-6-sol", "current_unknown"),
                                          ("gpt-6-sol:ultra", "current_unknown"),
                                          ("gpt-9-future:high", "current_unrated"),
                                          ("claude-opus-5-5:high", "current_unrated")])  # wrong provider for Lead
def test_unknown_or_unrated_current_is_reported_not_guessed(value, status):
    entry = lane_value_table(REGISTRY, CATALOG, {"codex-lead-1": value})["codex-lead-1"]
    assert entry["status"] == status and entry["more_for_same_money"] is None


@pytest.mark.parametrize("value,expected", [("gpt-6-sol:high", ("gpt-6-sol", "high")), ("a:b:c", None),
                                            (":high", None), ("x:", None), (5, None)])
def test_parse_current(value, expected):
    assert parse_current(value) == expected


# ---------------------------------------------------------------- catalog diff

def test_catalog_diff_on_the_shipped_catalog():
    diff = catalog_diff(REGISTRY, CATALOG)
    dominated = {item["catalog_profile"] for item in diff["dominated"]}
    assert dominated == {"claude-sonnet-5-xhigh", "codex-gpt-5.6-sol-medium", "codex-gpt-5.6-terra-medium"}
    added = {(item["model"], item["effort"]) for item in diff["propose_add"]}
    assert ("gpt-6-sol", "high") not in added                     # already in the catalog
    assert ("gpt-6-sol", "medium") in added and ("claude-opus-5-5", "high") in added
    assert diff["unrated"] == []


def test_propose_add_is_bounded_to_the_frontier_never_a_dominated_row():
    diff = catalog_diff(REGISTRY, CATALOG)
    front_keys = {(r["model"], r["effort"]) for rows_ in frontier(rows(REGISTRY)).values() for r in rows_}
    added = [(item["model"], item["effort"]) for item in diff["propose_add"]]
    assert added and len(added) == len(set(added))                 # no duplicates
    assert all(key in front_keys for key in added)                 # never a dominated model


def test_a_catalog_profile_without_a_registry_row_is_unrated():
    registry = mutate(lambda r: r["models"].pop("codex/gpt-5.6-terra"))
    assert catalog_diff(registry, CATALOG)["unrated"] == ["codex-gpt-5.6-terra-medium"]


# ---------------------------------------------------------------- CLI availability

def cache(tmp_path, models) -> Path:
    return write(tmp_path, {"fetched_at": "x", "models": models}, "models_cache.json")


def test_codex_cache_marks_availability(tmp_path):
    path = cache(tmp_path, [{"slug": "gpt-6-sol", "supported_reasoning_levels": [{"effort": "high"}, {"effort": "low"}]},
                            {"slug": "broken"}, "junk"])
    models = codex_cli_models(path)
    assert models == {"gpt-6-sol": ["high", "low"], "broken": []}
    table = lane_value_table(REGISTRY, CATALOG, {"codex-lead-1": "gpt-6-sol:high", "claude-rco-1": "claude-sonnet-5:xhigh"},
                             codex_models=models)
    assert table["codex-lead-1"]["current"]["cli_available"] is True
    assert table["claude-rco-1"]["current"]["cli_available"] is None      # Claude: unverified, never guessed
    missing = lane_value_table(REGISTRY, CATALOG, {"codex-lead-1": "gpt-6-sol:medium"}, codex_models=models)
    assert missing["codex-lead-1"]["current"]["cli_available"] is False


@pytest.mark.parametrize("content", ["not json", "[]", '{"models": "x"}'])
def test_an_unusable_codex_cache_is_unknown(tmp_path, content):
    assert codex_cli_models(write(tmp_path, content, "models_cache.json")) is None


def test_a_missing_codex_cache_is_unknown(tmp_path):
    assert codex_cli_models(tmp_path / "none.json") is None


# ---------------------------------------------------------------- report and CLI

def test_report_is_advisory_and_pins_its_inputs():
    result = report(REGISTRY, REGISTRY_SHA, CATALOG, CATALOG_SHA, CURRENT)
    assert result["execution_allowed"] is False
    assert (result["registry_sha256"], result["catalog_sha256"]) == (REGISTRY_SHA, CATALOG_SHA)
    assert result["codex_cli_models"] == "unknown"


def test_cli_prints_one_report(tmp_path, capsys):
    code = main(["--codex-models-cache", str(tmp_path / "none.json"),
                 "--current-profiles", json.dumps({"claude-rco-1": "claude-sonnet-5:xhigh"})])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["lanes"]["claude-rco-1"]["dominated"] is True


@pytest.mark.parametrize("args", [["--current-profiles", "[]"], ["--current-profiles", '{"nobody": "x:low"}'],
                                  ["--current-profiles", "not json"], ["--registry", "missing.json"]])
def test_cli_refuses_bad_input_with_a_json_error(tmp_path, capsys, args):
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["execution_allowed"] is False


def test_an_unknown_provider_is_refused_even_with_a_consistent_key():
    def add_gemini(r):
        r["models"]["gemini/g-1"] = {"provider": "gemini", "model": "g-1", "coding_agent_index": None,
                                     "efforts": {"high": {"intelligence_index": 50, "usd_per_task": 1.0}}}
    with pytest.raises(RegistryError, match="provider must be one of"):
        validate_registry(mutate(add_gemini))


def test_grok_is_a_known_provider():
    def add_grok(r):
        r["models"]["grok/grok-4-7"] = {"provider": "grok", "model": "grok-4-7", "coding_agent_index": 56.3,
                                        "efforts": {"xhigh": {"intelligence_index": 50, "usd_per_task": 1.0}}}
    assert "grok/grok-4-7" in validate_registry(mutate(add_grok))["models"]


def test_a_symlinked_registry_is_refused(tmp_path):
    target = write(tmp_path, REGISTRY, "real.json")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(RegistryError, match="symlink or reparse point"):
        load_registry(link)



# ---------------------------------------------------------------- CODEX_HOME (codex-tools-1 N1 on #1743)

def _two_homes(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".codex").mkdir(parents=True)
    (tmp_path / "codexhome").mkdir()
    cache(tmp_path / "home" / ".codex", [{"slug": "gpt-6-sol", "supported_reasoning_levels": [{"effort": "low"}]}])
    cache(tmp_path / "codexhome", [{"slug": "gpt-6-sol", "supported_reasoning_levels": [{"effort": "high"}]}])


def test_the_default_cache_follows_codex_home_at_call_time(tmp_path, monkeypatch):
    from tools.wd_model_registry import default_codex_cache
    _two_homes(tmp_path, monkeypatch)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert default_codex_cache() == tmp_path / "home" / ".codex" / "models_cache.json"
    assert codex_cli_models() == {"gpt-6-sol": ["low"]}
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codexhome"))       # set after import: still followed
    assert default_codex_cache() == tmp_path / "codexhome" / "models_cache.json"
    assert codex_cli_models() == {"gpt-6-sol": ["high"]}


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_codex_home_means_the_default(tmp_path, monkeypatch, value):
    from tools.wd_model_registry import default_codex_cache
    _two_homes(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_HOME", value)
    assert default_codex_cache() == tmp_path / "home" / ".codex" / "models_cache.json"


def test_the_cli_reads_codex_home_and_an_explicit_cache_overrides_it(tmp_path, monkeypatch, capsys):
    _two_homes(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codexhome"))
    current = json.dumps({"codex-lead-1": "gpt-6-sol:high"})
    assert main(["--current-profiles", current]) == 0
    lead = json.loads(capsys.readouterr().out)["lanes"]["codex-lead-1"]["current"]
    assert lead["cli_available"] is True                                  # the CODEX_HOME cache has high
    explicit = str(tmp_path / "home" / ".codex" / "models_cache.json")
    assert main(["--current-profiles", current, "--codex-models-cache", explicit]) == 0
    lead = json.loads(capsys.readouterr().out)["lanes"]["codex-lead-1"]["current"]
    assert lead["cli_available"] is False                                 # the explicit cache has only low


# ---------------------------------------------------------------- benchmark variant provenance (codex-tools-1 N2)

def test_fallback_variant_scores_are_labelled_in_the_registry():
    for key in ("claude/claude-opus-5-5", "claude/claude-fable-5-1"):
        assert REGISTRY["models"][key]["benchmark_variant"] == "with_fallback"
    labelled = {(r["model"], r["benchmark_variant"]) for r in rows(REGISTRY) if r["benchmark_variant"]}
    assert labelled == {("claude-opus-5-5", "with_fallback"), ("claude-fable-5-1", "with_fallback")}


def test_the_variant_travels_with_every_suggestion():
    result = report(REGISTRY, REGISTRY_SHA, CATALOG, CATALOG_SHA, {"claude-rco-1": "claude-sonnet-5:xhigh"})
    claude_front = result["frontier"]["claude"]
    assert all("benchmark_variant" in row for row in claude_front)
    assert any(row["benchmark_variant"] == "with_fallback" for row in claude_front)
    replacement = result["lanes"]["claude-rco-1"]
    assert "dominance_and_value_are_benchmark_only" in result["limitations"]
    assert "benchmark_variant_labels_are_per_model_see_rows" in result["limitations"]
    assert replacement["dominated"] is True


@pytest.mark.parametrize("bad", [5, "", "   ", None, ["with_fallback"]])
def test_a_malformed_benchmark_variant_is_refused(bad):
    broken = copy.deepcopy(REGISTRY)
    broken["models"]["claude/claude-opus-5-5"]["benchmark_variant"] = bad
    with pytest.raises(RegistryError, match="benchmark_variant"):
        validate_registry(broken)


def test_only_the_benchmark_variant_is_optional():
    broken = copy.deepcopy(REGISTRY)
    broken["models"]["claude/claude-opus-5-5"]["notes"] = "x"
    with pytest.raises(RegistryError, match="keys must be exactly"):
        validate_registry(broken)
    missing = copy.deepcopy(REGISTRY)
    del missing["models"]["claude/claude-opus-5-5"]["coding_agent_index"]
    with pytest.raises(RegistryError, match="keys must be exactly"):
        validate_registry(missing)
