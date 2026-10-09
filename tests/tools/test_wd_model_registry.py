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
    assert REGISTRY["schema"] == "wd.model-registry.v2" and len(REGISTRY_SHA) == 64
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
    assert load_registry(path)[0]["schema"] == "wd.model-registry.v2"


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


# RCO1 LOWREG-R1 C1: a cache that names no model proved nothing, yet every Codex row was reported unavailable.
@pytest.mark.parametrize("models", [[], ["junk", 7, None, {"slug": 5}, {"name": "gpt-6-sol"}]],
                         ids=["empty-list", "all-junk"])
def test_a_cache_that_names_no_model_is_unknown_everywhere(tmp_path, capsys, models):
    path = cache(tmp_path, models)
    assert codex_cli_models(path) is None
    current = {"codex-lead-1": "gpt-6-sol:high"}
    assert lane_value_table(REGISTRY, CATALOG, current, codex_models=codex_cli_models(path))[
        "codex-lead-1"]["current"]["cli_available"] is None
    assert report(REGISTRY, REGISTRY_SHA, CATALOG, CATALOG_SHA, current,
                  codex_models=codex_cli_models(path))["codex_cli_models"] == "unknown"
    assert main(["--codex-models-cache", str(path), "--current-profiles", json.dumps(current)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["codex_cli_models"] == "unknown" and out["lanes"]["codex-lead-1"]["current"]["cli_available"] is None


def test_cache_controls_are_unchanged_by_the_empty_cache_rule(tmp_path):
    current = {"codex-lead-1": "gpt-6-sol:high"}

    def lead(models):
        return lane_value_table(REGISTRY, CATALOG, current, codex_models=models)["codex-lead-1"]["current"]

    assert lead(codex_cli_models(tmp_path / "none.json"))["cli_available"] is None             # missing cache
    fresh = codex_cli_models(cache(tmp_path, [{"slug": "gpt-6-sol",
                                               "supported_reasoning_levels": [{"effort": "high"}]}]))
    assert lead(fresh)["cli_available"] is True                                                 # present
    no_effort = {"gpt-6-sol": ["low"]}
    assert lead(no_effort)["cli_available"] is False                                            # valid, effort missing
    assert codex_cli_models(write(tmp_path, {"models": [{"slug": "x"}]}, "levels.json")) == {"x": []}   # kept as is


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


# ---------------------------------------------------------------- registry v2 (Bridge v2 F3 foundation)
# Authored under the 2026-09-29 operator no-runs directive: NOT executed by the author.

from datetime import datetime, timedelta, timezone  # noqa: E402

from tools.wd_model_registry import model_table, observation_state  # noqa: E402

T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
EXACT_NOTE = "exact token count read from the provider documentation by the operator"


def v1_projection(registry=None) -> dict:
    """The same measured tables as a v1 document (what an old reader or writer holds)."""
    source = copy.deepcopy(REGISTRY if registry is None else registry)
    projected = {key: source[key] for key in reg_module.TOP_KEYS}
    projected["schema"] = "wd.model-registry.v1"
    return projected


def observation(**changes) -> dict:
    base = {"id": "obs-test", "subject": {"provider": "codex", "model": "gpt-6-sol", "effort": "high", "pool": None},
            "kind": "context", "class": None, "value": 400000, "unit": "tokens", "status": "measured",
            "provenance": {"kind": "operator_reading", "reference": "operator reading 2026-10-01", "observer": "operator"},
            "measured_at": "2026-10-01T11:00:00Z", "ttl_seconds": 7200,
            "uncertainty": {"kind": "exact", "low": None, "high": None, "note": EXACT_NOTE}}
    base.update(changes)
    return base


def with_v2(change) -> dict:
    registry = copy.deepcopy(REGISTRY)
    change(registry)
    return registry


VERIFIED_POOL = {"provider": "codex", "limit_id": "codex-weekly", "window": "weekly", "tier": "unknown",
                 "verification": "verified", "measured_at": "2026-10-01T11:00:00Z", "ttl_seconds": 7200,
                 "provenance": {"kind": "operator_reading", "reference": "operator reading 2026-10-01",
                                "observer": "operator"}}


def test_v1_files_still_load_and_analyse_identically():
    v1 = validate_registry(v1_projection())
    assert v1["schema"] == "wd.model-registry.v1"
    assert rows(v1) == rows(REGISTRY)
    assert frontier(rows(v1)) == frontier(rows(REGISTRY))
    assert lane_value_table(v1, CATALOG, CURRENT) == lane_value_table(REGISTRY, CATALOG, CURRENT)
    assert catalog_diff(v1, CATALOG) == catalog_diff(REGISTRY, CATALOG)


def test_v1_with_v2_tables_and_v2_without_them_are_refused():
    mixed = v1_projection()
    mixed["pools"] = {}
    with pytest.raises(RegistryError, match="keys must be exactly"):
        validate_registry(mixed)
    bare = v1_projection()
    bare["schema"] = "wd.model-registry.v2"
    with pytest.raises(RegistryError, match="keys must be exactly"):
        validate_registry(bare)


def test_shipped_v2_marks_measured_rows_historical_and_adds_no_admission():
    assert REGISTRY["historical"]["applies_to"] == ["benchmark", "coding_benchmark", "models"]
    assert REGISTRY["historical"]["cost_basis"] == "api_price_not_quota"
    assert REGISTRY["historical"]["source_measured_at"] == REGISTRY["benchmark"]["fetched_at"]
    assert {c["admission"] for c in REGISTRY["candidates"].values()} == {"none"}
    assert {c["capability"] for c in REGISTRY["candidates"].values()} == {"unknown"}
    assert set(REGISTRY["candidates"]) == {"claude/claude-haiku-4-5", "grok/grok-4.7"}
    assert all(pool["verification"] == "unverified" for pool in REGISTRY["pools"].values())
    assert all(obs["status"] in ("historical", "unverified") for obs in REGISTRY["observations"])
    assert not any(obs["subject"]["model"] == "claude-haiku-4-5" for obs in REGISTRY["observations"])


@pytest.mark.parametrize("change", [
    lambda r: r["historical"].update(status="measured"),
    lambda r: r["historical"].update(applies_to=["models"]),
    lambda r: r["historical"].update(cost_basis="usd"),
    lambda r: r["historical"].update(source_measured_at="yesterday"),
    lambda r: r["historical"].update(extra=1),
    lambda r: r.update(pools=[]),
    lambda r: r["pools"].update({"Grok Weekly": dict(VERIFIED_POOL, provider="grok")}),
    lambda r: r["pools"]["grok-weekly-shared"].update(provider="gemini"),
    lambda r: r["pools"]["grok-weekly-shared"].update(window="hourly"),
    lambda r: r["pools"]["grok-weekly-shared"].update(tier="gold"),
    lambda r: r["pools"]["grok-weekly-shared"].update(verification="verified"),        # plan text never verifies
    lambda r: r["pools"].update({"codex-x": dict(VERIFIED_POOL, limit_id=None)}),     # verified needs a limit_id
    lambda r: r["pools"].update({"codex-x": dict(VERIFIED_POOL, measured_at="unknown")}),  # ... a date
    lambda r: r["pools"].update({"codex-x": dict(VERIFIED_POOL, ttl_seconds=None)}),       # ... and a TTL
    lambda r: r["pools"]["grok-weekly-shared"].update(ttl_seconds=0),
    lambda r: r["pools"]["grok-weekly-shared"].update(measured_at="yesterday"),
    lambda r: r["pools"]["grok-weekly-shared"].pop("measured_at"),
    lambda r: r["pools"]["grok-weekly-shared"].update(extra=1),
    lambda r: r["candidates"]["grok/grok-4.7"].update(admission="catalog"),
    lambda r: r["candidates"]["grok/grok-4.7"].update(capability="strong"),
    lambda r: r["candidates"]["grok/grok-4.7"].update(model="grok-5"),                # key mismatch
    lambda r: r["candidates"]["grok/grok-4.7"].update(pool=["grok-weekly-shared"]),   # unhashable: refused, no crash
    lambda r: r["candidates"].update({"codex/gpt-6-sol": {"provider": "codex", "model": "gpt-6-sol",
                                                          "admission": "none", "capability": "unknown",
                                                          "pool": None, "note": None}}),  # a rated model
    lambda r: r["candidates"]["claude/claude-haiku-4-5"].update(pool="grok-weekly-shared"),  # other provider
    lambda r: r.update(observations={}),
    lambda r: r["observations"].append(copy.deepcopy(r["observations"][0])),          # duplicate id
])
def test_every_malformed_v2_table_is_refused(change):
    with pytest.raises(RegistryError):
        validate_registry(with_v2(change))


@pytest.mark.parametrize("changes", [
    {"kind": "speed"},
    {"kind": ["context"]},                                                          # unhashable kind
    {"unit": "usd_per_task_api_price"},                                             # wrong unit for context
    {"value": 400000.5},
    {"value": True},
    {"status": "unknown"},                                                          # unknown needs null
    {"value": None},                                                                # null needs unknown
    {"provenance": {"kind": "external_benchmark", "reference": "https://x.example", "observer": None}},
    {"ttl_seconds": None},
    {"ttl_seconds": 0},
    {"measured_at": "unknown"},
    {"measured_at": "2026-13-01"},
    {"uncertainty": {"kind": "unknown", "low": None, "high": None, "note": None}},
    {"uncertainty": {"kind": "interval", "low": 1, "high": 2, "note": None}},        # does not contain the value
    {"uncertainty": {"kind": "none_stated", "low": 1, "high": None, "note": None}},
    {"uncertainty": {"kind": "none_stated", "low": None, "high": None, "note": None}},   # measured needs a stated one
    {"uncertainty": {"kind": "exact", "low": None, "high": None, "note": None}},         # exact needs a justification
    {"uncertainty": {"kind": "exact", "low": None, "high": None, "note": "   "}},
    {"class": "general"},                                                           # class only for quality
    {"subject": {"provider": "codex", "model": "gpt-9", "effort": "high", "pool": None}},
    {"subject": {"provider": "codex", "model": None, "effort": None, "pool": None}},
    {"subject": {"provider": "codex", "model": "gpt-6-sol", "effort": "ultra", "pool": None}},
    {"subject": {"provider": "codex", "model": "gpt-6-sol", "effort": "high", "pool": "grok-weekly-shared"}},
    {"subject": {"provider": "codex", "model": "gpt-6-sol", "effort": "high", "pool": ["x"]}},
    {"extra": 1},
])
def test_every_malformed_observation_is_refused(changes):
    with pytest.raises(RegistryError):
        validate_registry(with_v2(lambda r: r["observations"].append(observation(**changes))))


def test_a_valid_measured_observation_is_accepted_and_is_fresh_then_stale():
    registry = validate_registry(with_v2(lambda r: r["observations"].append(observation())))
    obs = registry["observations"][-1]
    assert observation_state(obs, T0) == "fresh"
    assert observation_state(obs, T0 + timedelta(hours=2)) == "stale"
    assert observation_state(obs, T0 - timedelta(hours=2)) == "unknown"              # dated 1 h in the future
    row = next(r for r in model_table(registry, T0)["rows"] if (r["model"], r["effort"]) == ("gpt-6-sol", "high"))
    assert row["context_tokens"]["state"] == "fresh" and row["context_tokens"]["value"] == 400000
    stale = next(r for r in model_table(registry, T0 + timedelta(hours=3))["rows"]
                 if (r["model"], r["effort"]) == ("gpt-6-sol", "high"))
    assert stale["context_tokens"] == {"value": None, "state": "stale", "source": "operator reading 2026-10-01",
                                       "measured_at": "2026-10-01T11:00:00Z", "note": EXACT_NOTE}


def test_api_dollars_are_never_quota_units():
    pooled_dollars = observation(kind="cost", unit="usd_per_task_api_price", value=1.0,
                                 subject={"provider": "codex", "model": "gpt-6-sol", "effort": "high",
                                          "pool": "codex-weekly"})
    with pytest.raises(RegistryError, match="API-dollar"):
        validate_registry(with_v2(lambda r: (r["pools"].update({"codex-weekly": VERIFIED_POOL}),
                                             r["observations"].append(pooled_dollars))))
    unpooled_quota = observation(kind="cost", unit="pool_points_per_mtok", value=3.5)
    with pytest.raises(RegistryError, match="quota unit"):
        validate_registry(with_v2(lambda r: r["observations"].append(unpooled_quota)))
    pooled_quota = observation(kind="cost", unit="pool_points_per_mtok", value=3.5,
                               subject={"provider": "codex", "model": "gpt-6-sol", "effort": "high",
                                        "pool": "codex-weekly"})
    registry = validate_registry(with_v2(lambda r: (r["pools"].update({"codex-weekly": VERIFIED_POOL}),
                                                    r["observations"].append(pooled_quota))))
    row = next(r for r in model_table(registry, T0)["rows"] if (r["model"], r["effort"]) == ("gpt-6-sol", "high"))
    assert row["cost_pool_points_per_mtok"]["value"] == 3.5
    assert row["cost_api_usd_per_task"]["note"] == "api_price_not_quota"          # the two never merge


def test_an_unverified_pool_is_never_verified_by_an_observation():
    measured_membership = observation(id="grok-pool-measured", kind="pool", unit="pool_id",
                                      value="grok-weekly-shared",
                                      subject={"provider": "grok", "model": "grok-4.7", "effort": None, "pool": None})
    with pytest.raises(RegistryError, match="never verifies"):
        validate_registry(with_v2(lambda r: r["observations"].append(measured_membership)))
    verified = with_v2(lambda r: (r["pools"].update({"codex-weekly": VERIFIED_POOL}), r["observations"].append(
        observation(id="codex-pool", kind="pool", unit="pool_id", value="codex-weekly",
                    subject={"provider": "codex", "model": "gpt-6-sol", "effort": None, "pool": None}))))
    assert validate_registry(verified)["pools"]["codex-weekly"]["verification"] == "verified"


def test_a_limit_is_a_pool_level_observation():
    limit = observation(id="codex-weekly-limit", kind="limit", unit="percent_of_pool", value=42.0,
                        subject={"provider": "codex", "model": None, "effort": None, "pool": "codex-weekly"})
    assert validate_registry(with_v2(lambda r: (r["pools"].update({"codex-weekly": VERIFIED_POOL}),
                                                r["observations"].append(limit))))
    with pytest.raises(RegistryError, match="needs subject.pool"):
        validate_registry(with_v2(lambda r: r["observations"].append(
            dict(limit, subject={"provider": "codex", "model": None, "effort": None, "pool": None}))))


def test_the_model_table_shows_unknown_cells_and_candidates_without_admission():
    table = model_table(REGISTRY, T0)
    assert table["execution_allowed"] is False and table["schema"] == "wd.model-table.v1"
    by_key = {(r["provider"], r["model"], r["effort"]): r for r in table["rows"]}
    haiku = by_key[("claude", "claude-haiku-4-5", None)]
    assert haiku["admission"] == "none" and haiku["pool_verification"] == "unknown"
    assert all(haiku[column] == {"value": None, "state": "unknown", "source": None, "measured_at": None,
                                 "note": None} for column in reg_module.TABLE_COLUMNS)
    grok_high, grok_xhigh = by_key[("grok", "grok-4.7", "high")], by_key[("grok", "grok-4.7", "xhigh")]
    assert grok_high["quality_general"]["value"] == 46.33 and grok_high["quality_general"]["state"] == "historical"
    assert grok_xhigh["quality_coding_agent"]["value"] == 56 and grok_high["quality_coding_agent"]["state"] == "unknown"
    assert grok_high["pool"]["value"] == "grok-weekly-shared" and grok_high["pool"]["state"] == "unverified"
    assert grok_high["pool_verification"] == "membership_unverified" and grok_high["admission"] == "none"
    opus = by_key[("claude", "claude-opus-5-5", "medium")]
    assert opus["quality_general"] == {"value": 51, "state": "historical",
                                       "source": "Artificial Analysis Intelligence Index v4.3.2",
                                       "measured_at": "2026-09-27", "note": "with_fallback"}
    assert opus["cost_pool_points_per_mtok"]["state"] == "unknown" and opus["context_tokens"]["state"] == "unknown"
    assert opus["admission"] == "see_signed_catalog"


def test_the_model_table_needs_an_aware_time_and_works_for_v1():
    with pytest.raises(RegistryError, match="timezone-aware"):
        model_table(REGISTRY, datetime(2026, 10, 1))
    rows_v1 = model_table(validate_registry(v1_projection()), T0)["rows"]
    assert {r["pool_verification"] for r in rows_v1} == {"unknown"}
    assert not any(r["admission"] == "none" for r in rows_v1)                     # v1 has no candidates


def test_the_report_carries_the_model_table_and_stays_advisory():
    result = report(REGISTRY, REGISTRY_SHA, CATALOG, CATALOG_SHA, CURRENT, now=T0)
    assert result["execution_allowed"] is False and result["registry_schema"] == "wd.model-registry.v2"
    assert result["model_table"]["execution_allowed"] is False
    assert json.loads(json.dumps(result)) == result                                # plain JSON, no datetimes


# ---------------------------------------------------------------- RCO2 review fixes (S1-S3, nits)

def test_a_verified_pool_expires_like_an_observation():
    from tools.wd_model_registry import pool_state
    assert pool_state(VERIFIED_POOL, T0) == "verified"
    assert pool_state(VERIFIED_POOL, T0 + timedelta(hours=2)) == "stale"
    assert pool_state(VERIFIED_POOL, T0 - timedelta(hours=2)) == "unknown"        # dated in the future
    assert pool_state(VERIFIED_POOL, None) == "unknown"
    assert pool_state(REGISTRY["pools"]["grok-weekly-shared"], T0) == "unverified"


def test_pool_verification_speaks_only_through_a_fresh_membership():
    membership = observation(id="codex-pool", kind="pool", unit="pool_id", value="codex-weekly",
                             subject={"provider": "codex", "model": "gpt-6-sol", "effort": None, "pool": None})
    registry = validate_registry(with_v2(lambda r: (r["pools"].update({"codex-weekly": VERIFIED_POOL}),
                                                    r["observations"].append(membership))))
    def row_at(when):
        return next(r for r in model_table(registry, when)["rows"] if (r["model"], r["effort"]) == ("gpt-6-sol", "high"))
    assert row_at(T0)["pool_verification"] == "verified"
    assert row_at(T0 + timedelta(hours=3))["pool_verification"] == "membership_stale"   # membership expired too


@pytest.mark.parametrize("kind,unit,value", [("tier", "label", "strong"),
                                             ("pool", "pool_id", "grok-weekly-shared")])
def test_an_interval_on_a_categorical_kind_is_refused(kind, unit, value):
    subject = {"provider": "grok", "model": "grok-4.7", "effort": None, "pool": None}
    bad = observation(id="categorical", kind=kind, unit=unit, value=value, status="unverified", subject=subject,
                      provenance={"kind": "plan_transcription", "reference": "plan", "observer": None},
                      ttl_seconds=None, uncertainty={"kind": "interval", "low": 0, "high": 1, "note": None})
    with pytest.raises(RegistryError, match="only for numeric kinds"):
        validate_registry(with_v2(lambda r: r["observations"].append(bad)))


def _two(first: dict, second: dict) -> dict:
    return validate_registry(with_v2(lambda r: r["observations"].extend([first, second])))


def _context_cell(registry, when=T0) -> dict:
    return next(r for r in model_table(registry, when)["rows"]
                if (r["model"], r["effort"]) == ("gpt-6-sol", "high"))["context_tokens"]


def test_equal_state_disagreement_at_the_same_time_is_a_conflict_not_a_first_win():
    cell = _context_cell(_two(observation(id="a"), observation(id="b", value=200000)))
    assert cell["state"] == "conflict" and cell["value"] is None


def test_the_latest_equal_state_value_wins_explicitly_and_counts_the_superseded():
    older = observation(id="a", value=200000, measured_at="2026-10-01T10:30:00Z")
    cell = _context_cell(_two(older, observation(id="b")))
    assert cell["value"] == 400000 and cell["superseded"] == 1 and cell["state"] == "fresh"
    same = _context_cell(_two(observation(id="a"), observation(id="b")))
    assert same["value"] == 400000 and "superseded" not in same


def test_an_unrepresentable_now_makes_freshness_unknown_not_an_error():
    registry = validate_registry(with_v2(lambda r: r["observations"].append(observation())))
    extreme = datetime.max.replace(tzinfo=timezone(-timedelta(hours=23)))
    table = model_table(registry, extreme)
    assert table["generated_for_utc"] is None
    row = next(r for r in table["rows"] if (r["model"], r["effort"]) == ("gpt-6-sol", "high"))
    assert row["context_tokens"]["state"] == "unknown" and row["context_tokens"]["value"] is None
    assert row["quality_general"]["state"] == "historical"                        # time-independent evidence stays


def test_a_directory_or_fifo_registry_is_refused_without_blocking(tmp_path):
    import os
    with pytest.raises(RegistryError):
        load_registry(tmp_path)
    if not hasattr(os, "mkfifo"):
        return
    fifo = tmp_path / "registry.fifo"
    os.mkfifo(fifo)
    with pytest.raises(RegistryError, match="not a regular file"):
        load_registry(fifo)


def test_input_keys_in_error_messages_are_bounded():
    key = "codex/" + "x" * 500
    with pytest.raises(RegistryError) as caught:
        validate_registry(with_v2(lambda r: r["models"].update({key: {"provider": "codex"}})))
    assert "x" * 100 not in str(caught.value)


# ---------------------------------------------------------------- RCO2 fa7da7b1 R1: the public clock

from datetime import tzinfo  # noqa: E402

from tools.wd_model_registry import pool_state  # noqa: E402


class _NoOffset(tzinfo):
    """A tzinfo that names no offset: the datetime is naive and must never be read as local time."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


BAD_CLOCKS = {
    "none": None,
    "naive": datetime(2026, 10, 1, 12, 0, 0),
    "offsetless_tzinfo": T0.replace(tzinfo=_NoOffset()),
    "string": "2026-10-01T12:00:00Z",
    "epoch_seconds": T0.timestamp(),
    "date": T0.date(),
    "bool": True,
    "extreme_max": datetime.max.replace(tzinfo=timezone(-timedelta(hours=23))),
    "extreme_min": datetime.min.replace(tzinfo=timezone(timedelta(hours=23))),
}


@pytest.mark.parametrize("name", sorted(BAD_CLOCKS))
def test_a_missing_naive_wrong_type_or_extreme_clock_is_a_conservative_unknown(name):
    now = BAD_CLOCKS[name]
    assert observation_state(observation(), now) == "unknown"                     # never a TypeError/OverflowError
    assert pool_state(VERIFIED_POOL, now) == "unknown"
    for status in ("historical", "unverified", "unknown"):                         # these never read the clock
        assert observation_state(observation(status=status), now) == status
    assert pool_state(dict(VERIFIED_POOL, verification="unverified"), now) == "unverified"


class _Stateful(tzinfo):
    """Answers its offset once, then None: only a single read can use it consistently."""

    def __init__(self, first):
        self.answers, self.calls = [first, None], 0

    def utcoffset(self, dt):
        self.calls += 1
        return self.answers[min(self.calls, 2) - 1]

    def dst(self, dt):
        return None


class _Fixed(tzinfo):
    def __init__(self, offset):
        self.offset = offset

    def utcoffset(self, dt):
        return self.offset

    def dst(self, dt):
        return None


class _SubDatetime(datetime):
    pass


class _SubDelta(timedelta):
    pass


@pytest.mark.parametrize("now", [
    _SubDatetime(2026, 10, 1, 12, tzinfo=timezone.utc),                           # not exactly a datetime
    T0.replace(tzinfo=tzinfo()),                                                  # utcoffset NotImplementedError
    T0.replace(tzinfo=_Fixed(timedelta(hours=25))),                               # out of range
    T0.replace(tzinfo=_Fixed(3600)),                                              # not a timedelta
    T0.replace(tzinfo=_Fixed(_SubDelta(hours=3))),                                # not EXACTLY a timedelta
    T0.replace(tzinfo=_Stateful(None)),                                           # names no offset on its read
    datetime.max.replace(tzinfo=_Fixed(-timedelta(hours=23, minutes=59))),        # unrepresentable in UTC
    datetime.min.replace(tzinfo=_Fixed(timedelta(hours=23, minutes=59))),
], ids=["subclass", "unimplemented", "25h", "int", "delta_subclass", "stateful_none", "extreme_max", "extreme_min"])
def test_an_invalid_offset_or_foreign_clock_is_never_a_success(now):
    assert reg_module._evaluation_time(now) is None                              # the conservative unknown
    assert observation_state(observation(), now) == "unknown" and pool_state(VERIFIED_POOL, now) == "unknown"


def test_the_offset_is_read_once_and_never_as_local_time():
    stateful = _Stateful(timedelta(hours=3))                                     # 3 h once, then None
    wall = datetime(2026, 10, 1, 15, 0, 0)
    assert reg_module._evaluation_time(wall.replace(tzinfo=stateful)) == T0     # 15:00+03:00 == 12:00Z
    assert stateful.calls == 1                                                   # no second read, no local fallback


@pytest.mark.parametrize("offset", [timedelta(hours=3), -timedelta(hours=7, minutes=30), timedelta(0),
                                    timedelta(hours=23, minutes=59)])
def test_an_ordinary_non_utc_clock_normalizes_to_exact_utc(offset):
    for zone in (timezone(offset), _Fixed(offset)):
        normalized = reg_module._evaluation_time(T0.astimezone(timezone(offset)).replace(tzinfo=zone))
        assert normalized == T0 and type(normalized) is datetime and normalized.tzinfo is timezone.utc


def test_an_aware_clock_in_any_offset_is_normalized_to_utc():
    helsinki = timezone(timedelta(hours=3))
    obs = observation()                                     # measured 11:00Z with a 2 h TTL, like VERIFIED_POOL
    for now, state, pool in ((T0, "fresh", "verified"),
                             (T0 + timedelta(hours=2), "stale", "stale"),
                             (T0 - timedelta(hours=2), "unknown", "unknown")):       # dated in the future
        for clock in (now, now.astimezone(helsinki)):
            assert observation_state(obs, clock) == state
            assert pool_state(VERIFIED_POOL, clock) == pool


def test_the_ttl_and_future_skew_boundaries_are_exact():
    tick = timedelta(microseconds=1)
    edge = datetime(2026, 10, 1, 13, 0, 0, tzinfo=timezone.utc)                  # 11:00Z + 7200 s: still fresh
    assert observation_state(observation(), edge) == "fresh" and pool_state(VERIFIED_POOL, edge) == "verified"
    assert observation_state(observation(), edge + tick) == "stale"
    assert pool_state(VERIFIED_POOL, edge + tick) == "stale"
    skew = datetime(2026, 10, 1, 10, 55, 0, tzinfo=timezone.utc)                 # measured_at 5 min ahead: tolerated
    assert observation_state(observation(), skew) == "fresh" and pool_state(VERIFIED_POOL, skew) == "verified"
    assert observation_state(observation(), skew - tick) == "unknown"
    assert pool_state(VERIFIED_POOL, skew - tick) == "unknown"


def test_the_model_table_never_reads_an_offsetless_tzinfo_as_local_time():
    registry = validate_registry(with_v2(lambda r: r["observations"].append(observation())))
    table = model_table(registry, T0.replace(tzinfo=_NoOffset()))
    assert table["generated_for_utc"] is None
    row = next(r for r in table["rows"] if (r["model"], r["effort"]) == ("gpt-6-sol", "high"))
    assert row["context_tokens"]["state"] == "unknown" and row["context_tokens"]["value"] is None
    assert row["quality_general"]["state"] == "historical"                        # time-independent evidence stays
    with pytest.raises(RegistryError, match="timezone-aware"):
        model_table(registry, datetime(2026, 10, 1, 12))                          # a plain naive time is still refused


def test_a_symlink_swapped_in_after_the_check_fails_the_open_on_posix(tmp_path, monkeypatch):
    import os
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("no O_NOFOLLOW: the Windows reparse race is disclosed in BRIDGE_MODEL_REGISTRY.md, not closed")
    target = write(tmp_path, REGISTRY, "target.json")
    link = tmp_path / "registry.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    load_registry(target)                                                          # success twin: a regular file
    # The race: the pre-open check saw a regular file, then the path became a symlink.
    monkeypatch.setattr(type(link), "is_symlink", lambda self: False)
    monkeypatch.setattr(type(link), "lstat", lambda self: target.stat())
    with pytest.raises(RegistryError, match="unreadable"):
        load_registry(link)


# ---------------------------------------------------------------- range before isfinite (codex-tools-1 0F41B0C1)
# math.isfinite(10**1000) raises OverflowError, which escaped validation and the CLI's JSON refusal (exit 1).

HUGE = 10 ** 1000


@pytest.mark.parametrize("value", [HUGE, -HUGE, 10 ** 309, 2 ** 1024, float("nan"), float("inf"), float("-inf"),
                                   True, False, "1", None, [1], 101, -1, 100.000001],
                         ids=["huge", "-huge", "1e309-int", "2**1024", "nan", "inf", "-inf", "True", "False", "str",
                              "None", "list", "101", "-1", "just-above"])
def test_score_refuses_out_of_range_non_finite_and_mistyped_values_without_overflow(value):
    with pytest.raises(RegistryError, match=r"finite number in 0\.\.100"):
        reg_module._score(value, "x", 100)


@pytest.mark.parametrize("value", [0, 100, 0.0, 99.5])
def test_score_accepts_the_boundaries_and_returns_a_float(value):
    result = reg_module._score(value, "x", 100)
    assert type(result) is float and result == value


@pytest.mark.parametrize("value", [HUGE, -HUGE, 10 ** 12 + 1, -(10 ** 12) - 1, 1e12 * (1 + 2 ** -52), float("nan"),
                                   float("inf"), float("-inf"), True, "0", None],
                         ids=["huge", "-huge", "int-above", "int-below", "float-above", "nan", "inf", "-inf", "True",
                              "str", "None"])
def test_number_refuses_out_of_range_non_finite_and_mistyped_values_without_overflow(value):
    with pytest.raises(RegistryError, match="finite number in"):
        reg_module._number(value, "x", -1e12, 1e12)


@pytest.mark.parametrize("value", [10 ** 12, -(10 ** 12), 1e12, -1e12, 0])
def test_number_accepts_the_exact_boundaries_and_returns_a_float(value):
    result = reg_module._number(value, "x", -1e12, 1e12)
    assert type(result) is float and result == value


@pytest.mark.parametrize("change", [
    lambda r: r["models"]["codex/gpt-6-sol"]["efforts"]["low"].update(intelligence_index=HUGE),
    lambda r: r["models"]["codex/gpt-6-sol"]["efforts"]["low"].update(usd_per_task=-HUGE),
    lambda r: r["models"]["codex/gpt-6-sol"].update(coding_agent_index=HUGE),
    lambda r: r["observations"].append(observation(kind="cost", unit="usd_per_task_api_price", value=HUGE)),
    lambda r: r["observations"].append(
        observation(uncertainty={"kind": "interval", "low": -HUGE, "high": HUGE, "note": None})),
], ids=["v1-intelligence", "v1-usd", "v1-coding-agent", "v2-observation-value", "v2-uncertainty-interval"])
def test_a_huge_integer_is_a_registry_error_not_an_overflow(change):
    with pytest.raises(RegistryError, match="finite number"):
        validate_registry(mutate(change))


def test_the_huge_integer_cases_have_valid_twins():
    validate_registry(mutate(lambda r: r["observations"].append(
        observation(kind="cost", unit="usd_per_task_api_price", value=1.0))))
    validate_registry(mutate(lambda r: r["observations"].append(
        observation(uncertainty={"kind": "interval", "low": 0, "high": 10 ** 6, "note": None}))))


@pytest.mark.parametrize("change,swap", [
    (lambda r: r["models"]["codex/gpt-6-sol"]["efforts"]["low"].update(intelligence_index=HUGE), None),
    (lambda r: r["observations"].append(observation(kind="cost", unit="usd_per_task_api_price", value=HUGE)), None),
    (lambda r: r["models"]["codex/gpt-6-sol"]["efforts"]["low"].update(intelligence_index=12.345678),
     ("12.345678", "1e400")),                                                      # parses to inf, not via NaN/Infinity
], ids=["v1-huge-int", "v2-huge-int", "v1-1e400"])
def test_cli_refuses_a_huge_number_with_a_json_error_not_a_traceback(tmp_path, capsys, change, swap):
    text = json.dumps(mutate(change))
    if swap is not None:
        assert text.count(swap[0]) == 1
        text = text.replace(*swap)
    code = main(["--registry", str(write(tmp_path, text)), "--codex-models-cache", str(tmp_path / "none.json")])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["execution_allowed"] is False and out["error"].startswith("RegistryError: ")
