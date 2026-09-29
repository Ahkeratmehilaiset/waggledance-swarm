# SPDX-License-Identifier: BUSL-1.1
"""Pure adapter: F0 Decision + F3 source bytes -> F24 composer evidence.

Authored under the operator's no-test-run directive (2026-09-29): not yet executed.
The integration fixtures below run the REAL F0 evaluator on files written under
tmp_path and feed its Decision to the adapter.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tools.bridge_v2_activation as activation
import tools.bridge_v2_composer_evidence as adapter
import tools.wd_composer_select as cs
from tools.bridge_v2_activation import Decision
from tools.bridge_v2_composer_evidence import Refusal, compose

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)
HEAD, TREE = "1" * 40, "2" * 40
INDEX = "fixture intelligence_index rerun r1"  # the signed index_version is the exact provenance.reference
CODING = "fixture coding_agent_index rerun r1"
OPUS, SONNET, CANDIDATE = "claude/claude-opus-5-5", "claude/claude-sonnet-5", "grok/grok-4.7"
POOL = "claude-max-5h"


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


RECENT = iso(NOW - timedelta(minutes=1))


def quality(obs_id, key, value, *, effort="high", cls="general", unit="intelligence_index", reference=INDEX,
            status="measured", provenance="local_measurement", measured_at=None, uncertainty=None):
    """A fresh, measured, exact-effort quality observation with an interval: the only kind that maps."""
    provider, model = key.split("/", 1)
    return {"id": obs_id, "subject": {"provider": provider, "model": model, "effort": effort, "pool": None},
            "kind": "quality", "class": cls, "value": value, "unit": unit, "status": status,
            "provenance": {"kind": provenance, "reference": reference, "observer": "fixture"},
            "measured_at": measured_at or iso(NOW - timedelta(hours=1)), "ttl_seconds": 7 * 86400,
            "uncertainty": uncertainty or {"kind": "interval", "low": value - 1.0, "high": value + 2.0,
                                           "note": None}}


def pool(provider, verification):
    return {"provider": provider, "limit_id": provider + "-5h", "window": "5h", "tier": "unknown",
            "verification": verification,
            "provenance": {"kind": "operator_reading", "reference": "fixture reading", "observer": None}}


def registry(*observations) -> dict:
    """The shipped F3 v2 registry plus fixture pools and observations."""
    doc = json.loads((ROOT / "configs" / "model_registry.json").read_text(encoding="utf-8"))
    doc["pools"].update({POOL: pool("claude", "verified"), "claude-unverified": pool("claude", "unverified"),
                         "codex-plus-5h": pool("codex", "verified")})
    doc["observations"] += list(observations)
    return doc


def source(doc: dict) -> bytes:
    return json.dumps(doc, indent=2).encode("utf-8")


DEFAULT_OBS = (quality("opus-high", OPUS, 60.0), quality("sonnet-high", SONNET, 50.0),
               quality("opus-high-coding", OPUS, 70.0, cls="coding_agent", unit="coding_agent_index",
                       reference=CODING))


def signed_policy(data: bytes, edit=None, **param_over) -> dict:
    """The shipped policy with F24 and its bit switched on and the F24 parameters signed in."""
    config = json.loads((ROOT / "configs" / "bridge_v2_activation.json").read_text(encoding="utf-8"))
    policy = config["policy"]
    policy["features"]["F24"].update(enabled=True, stage=1)
    policy["policy_bits"]["f24_composer_rule"] = True
    parameters = {"index_name": "intelligence_index", "index_version": INDEX, "epsilon": 0.5,
                  "plausibility_bound": 10.0, "max_evidence_age_seconds": 600, "max_score_age_days": 30,
                  "max_wait_seconds": 600, "budget_mode": "steady", "planning_efforts": ["high", "xhigh"],
                  "registry_source_sha256": hashlib.sha256(data).hexdigest(), "coding_index_version": CODING}
    parameters.update(param_over)
    policy.update(expires_utc=iso(NOW + timedelta(days=10)), revocation_max_age_seconds=3600,
                  parameters={"F24": parameters})
    if edit is not None:
        edit(policy)
    return policy


def profile(pid, key, effort="high", pool_id=POOL):
    provider, model = key.split("/", 1)
    stamp = {"observed_utc": RECENT}
    return {"profile_id": pid, "provider": provider, "model": model, "effort": effort, "signed_in_envelope": True,
            "identity": dict(stamp, verified=True, profile_id=pid, provider=provider, model=model, effort=effort),
            "auth": dict(stamp, verified=True, profile_id=pid),
            "turn": dict(stamp, ok=True, profile_id=pid),
            "pool": dict(stamp, profile_id=pid, provider=provider, pool_id=pool_id, state="available",
                         provider_up=True, projected_used_percent=40.0),
            "measured_quota_cost": dict(stamp, unit="percent_of_pool", pool_id=pool_id, window="5h",
                                        workload="task:compose", value=1.0)}


def inputs(observations=DEFAULT_OBS, *, data=None, edit=None, profiles=None, **param_over) -> dict:
    """compose() keyword arguments: an honestly bound Decision, pins and policy over the given registry bytes."""
    data = source(registry(*observations)) if data is None else data
    policy = signed_policy(data, edit, **param_over)
    digest = activation.canonical_sha256(policy)
    return {"decision": Decision("F24", True, "enabled by the signed policy", digest, 3),
            "pins": {"trusted_policy_sha256": digest, "min_revocation_version": 3},
            "policy": policy, "registry_source": data,
            "profiles": [profile("opus", OPUS), profile("sonnet", SONNET)] if profiles is None else profiles,
            "task": {"task_id": "task-1", "created_utc": iso(NOW)}, "now": NOW}


def refusal(kwargs: dict) -> str:
    with pytest.raises(Refusal) as caught:
        compose(**kwargs)
    return caught.value.code


def models(evidence: dict) -> list:
    return [e["model"] for e in evidence["registry_snapshot"]["entries"]]


def test_success_twin_maps_fresh_cells_binds_both_digests_and_ranks():
    kwargs = inputs()
    out = compose(**kwargs)
    ev, digest = out["evidence"], kwargs["pins"]["trusted_policy_sha256"]
    assert out["registry_source_sha256"] == hashlib.sha256(kwargs["registry_source"]).hexdigest()
    assert out["registry_snapshot_sha256"] == cs.digest(ev["registry_snapshot"]) == ev["parameters"]["registry_sha256"]
    assert out["registry_snapshot_sha256"] != out["registry_source_sha256"]
    assert ev["f0"] == {"feature": "F24", "enabled": True, "reason": "enabled by the signed policy",
                        "policy_sha256": digest, "revocation_version": 3}
    assert ev["policy_bits"] == {"policy_sha256": digest, "f24_composer_rule": True}
    assert ev["parameters"]["policy_sha256"] == digest and "registry_source_sha256" not in ev["parameters"]
    assert ev["now_utc"] == "2026-09-29T21:00:00Z" and ev["task"] == kwargs["task"]
    assert ev["registry_snapshot"]["entries"] == [
        {"provider": "claude", "model": "claude-opus-5-5", "effort": "high", "score": 60.0, "uncertainty": 2.0,
         "measured_on": "2026-09-29", "coding_score": 70.0,
         "source": {"score_observation": "opus-high", "coding_observation": "opus-high-coding"}},
        {"provider": "claude", "model": "claude-sonnet-5", "effort": "high", "score": 50.0, "uncertainty": 2.0,
         "measured_on": "2026-09-29", "coding_score": None,
         "source": {"score_observation": "sonnet-high", "coding_observation": None}}]
    result = cs.select(ev)
    assert result["verdict"] == cs.COMPOSER and result["selected_profile"] == "opus"
    assert result["registry_sha256"] == out["registry_snapshot_sha256"] and result["policy_sha256"] == digest


def test_the_shipped_registry_has_no_current_cell_so_nothing_is_ranked():
    """Today's F3 file holds only historical and unverified values: every cell and every pool stays unknown."""
    real = (ROOT / "configs" / "model_registry.json").read_bytes()
    ev = compose(**inputs(data=real))["evidence"]
    assert ev["registry_snapshot"]["entries"] == [] and [p["pool"] for p in ev["profiles"]] == [None, None]
    result = cs.select(ev)
    assert result["verdict"] == cs.HOLD and result["reasons"] == ["no_eligible_profile"]
    assert result["ineligible"]["opus"] == ["quota_unknown_or_stale"]
    # With a verified pool but still no measured cell, the historical values stay unknown, never ranked.
    ev = compose(**inputs(()))["evidence"]
    result = cs.select(ev)
    assert ev["registry_snapshot"]["entries"] == [] and result["verdict"] == cs.UNKNOWN
    assert "unranked:opus:no_registry_entry" in result["reasons"] and result["selected_profile"] is None


UNKNOWN_SOURCES = {
    "historical": dict(status="historical", provenance="external_benchmark"),
    "unverified": dict(status="unverified", provenance="external_benchmark"),
    "stale": dict(measured_at=iso(NOW - timedelta(days=8))),
    "future": dict(measured_at=iso(NOW + timedelta(hours=1))),
    "model_level": dict(effort=None),
    "none_stated": dict(uncertainty={"kind": "none_stated", "low": None, "high": None, "note": None}),
    "task_class": dict(cls="task:compose"),
    "other_unit": dict(unit="score_0_100"),
    "other_version": dict(reference="fixture intelligence_index rerun r0"),
}


@pytest.mark.parametrize("change", sorted(UNKNOWN_SOURCES))
def test_unknown_or_unmatched_sources_never_become_entries(change):
    sonnet = quality("sonnet-high", SONNET, 50.0, **UNKNOWN_SOURCES[change])
    ev = compose(**inputs((quality("opus-high", OPUS, 60.0), sonnet)))["evidence"]
    assert models(ev) == ["claude-opus-5-5"]
    result = cs.select(ev)
    assert result["verdict"] == cs.UNKNOWN and "unranked:sonnet:no_registry_entry" in result["reasons"]


def test_two_current_values_for_one_cell_are_unknown_not_a_pick():
    ev = compose(**inputs((quality("opus-high", OPUS, 60.0), quality("opus-high-2", OPUS, 61.0),
                           quality("sonnet-high", SONNET, 50.0))))["evidence"]
    assert models(ev) == ["claude-sonnet-5"]


@pytest.mark.parametrize("change", ["stale", "model_level", "none_stated", "historical", "other_version"])
def test_a_coding_score_follows_the_same_rules(change):
    over = dict(UNKNOWN_SOURCES[change])
    if change == "other_version":
        over["reference"] = "fixture coding_agent_index rerun r0"
    coding = quality("opus-high-coding", OPUS, 70.0, cls="coding_agent", unit="coding_agent_index",
                     **{"reference": CODING, **over})
    ev = compose(**inputs((quality("opus-high", OPUS, 60.0), coding)))["evidence"]
    assert ev["registry_snapshot"]["entries"][0]["coding_score"] is None


def test_no_signed_coding_index_means_no_coding_score():
    ev = compose(**inputs(coding_index_version=None))["evidence"]
    assert [e["coding_score"] for e in ev["registry_snapshot"]["entries"]] == [None, None]


def test_a_placeholder_candidate_is_never_rated():
    rows = [profile("opus", OPUS), profile("grok", CANDIDATE, pool_id=POOL)]
    ev = compose(**inputs(DEFAULT_OBS + (quality("grok-high", CANDIDATE, 90.0),), profiles=rows))["evidence"]
    assert "grok-4.7" not in models(ev)
    result = cs.select(ev)
    assert "grok" not in (result["selected_profile"], result["provisional_profile"])


def test_source_bytes_not_a_parsed_dict_are_bound_to_the_signed_digest():
    kwargs = inputs()
    data = kwargs["registry_source"]
    for other, code in ((data + b"\n", "f3_source_unbound"), (data.decode("utf-8"), "f3_source_missing"),
                        (json.loads(data), "f3_source_missing"), (bytearray(data), "f3_source_missing"),
                        (None, "f3_source_missing")):
        assert refusal(dict(kwargs, registry_source=other)) == code


def test_bound_bytes_must_still_be_a_valid_v2_registry():
    good = source(registry(*DEFAULT_OBS))
    v1 = json.loads(good)
    for key in ("historical", "pools", "candidates", "observations"):
        del v1[key]
    v1["schema"] = "wd.model-registry.v1"
    for data, code in ((b'{"schema": "wd.model-registry.v2", "schema": "x"}', "f3_registry_invalid"),
                       (b"\xff", "f3_registry_invalid"), (b'{"a": NaN}', "f3_registry_invalid"),
                       (json.dumps(v1).encode("utf-8"), "f3_registry_not_v2"),
                       (good + b" " * (adapter.MAX_REGISTRY_BYTES + 1 - len(good)), "f3_source_missing")):
        assert refusal(inputs(data=data)) == code, code


def test_the_snapshot_digest_is_derived_from_now_while_the_source_digest_is_fixed():
    kwargs = inputs()
    early = compose(**kwargs)
    late = compose(**dict(kwargs, now=NOW + timedelta(days=7)))  # the cells measured at 20:00 are past their TTL
    assert early["registry_source_sha256"] == late["registry_source_sha256"]
    assert late["evidence"]["registry_snapshot"]["entries"] == []
    assert early["registry_snapshot_sha256"] != late["registry_snapshot_sha256"]


def test_f0_refusals_on_the_injected_decision_pins_and_time():
    kwargs = inputs()
    decision, pins, other = kwargs["decision"], kwargs["pins"], "b" * 64
    cases = [
        (dict(kwargs, decision=asdict(decision)), "f0_decision_missing"),  # a dict is not F0's Decision
        (dict(kwargs, decision=replace(decision, feature="F15")), "f0_feature_mismatch"),
        (dict(kwargs, decision=replace(decision, enabled=False)), "f0_decision_disabled"),
        (dict(kwargs, decision=replace(decision, enabled=1)), "f0_decision_disabled"),
        (dict(kwargs, decision=Decision("F24", False, "local kill switch denies")), "f0_decision_disabled"),
        (dict(kwargs, pins={**pins, "expected_head": HEAD}), "f0_pins_missing"),
        (dict(kwargs, pins={"trusted_policy_sha256": pins["trusted_policy_sha256"]}), "f0_pins_missing"),
        (dict(kwargs, pins={**pins, "trusted_policy_sha256": pins["trusted_policy_sha256"].upper()}),
         "f0_pins_missing"),
        (dict(kwargs, pins={**pins, "min_revocation_version": 0}), "f0_pins_missing"),
        (dict(kwargs, pins={**pins, "min_revocation_version": True}), "f0_pins_missing"),
        (dict(kwargs, policy={**kwargs["policy"], "extra": 1}), "f0_policy_invalid"),
        (dict(kwargs, pins={**pins, "trusted_policy_sha256": other}), "f0_policy_unbound"),
        (dict(kwargs, decision=replace(decision, policy_sha256=other)), "f0_policy_unbound"),
        (dict(kwargs, pins={**pins, "min_revocation_version": 4}), "f0_revocation_rollback"),
        (dict(kwargs, decision=replace(decision, revocation_version=None)), "f0_revocation_rollback"),
        (dict(kwargs, now=NOW + timedelta(days=10)), "f0_policy_expired"),
        (dict(kwargs, now=NOW.replace(tzinfo=None)), "time_unknown"),
        (dict(kwargs, now=iso(NOW)), "time_unknown"),
        (dict(kwargs, profiles={"opus": profile("opus", OPUS)}), "profiles_malformed"),
    ]
    for case, code in cases:
        assert refusal(case) == code, code


@pytest.mark.parametrize("edit, code", [
    (lambda p: p["features"]["F24"].update(enabled=False, stage=None), "f0_feature_off"),
    (lambda p: p["policy_bits"].update(f24_composer_rule=False), "f0_policy_bit_off"),
    (lambda p: p["parameters"].pop("F24"), "f0_parameters_unknown"),
    (lambda p: p["parameters"]["F24"].update(extra=1), "f0_parameters_unknown"),
    (lambda p: p["parameters"]["F24"].update(index_name="usd_per_task_api_price"), "f0_parameters_invalid"),
    (lambda p: p["parameters"]["F24"].update(index_name="coding_agent_index"), "f0_parameters_invalid"),
    (lambda p: p["parameters"]["F24"].update(index_version=""), "f0_parameters_invalid"),
    (lambda p: p["parameters"]["F24"].update(coding_index_version=""), "f0_parameters_invalid"),
    (lambda p: p["parameters"]["F24"].update(registry_source_sha256="A" * 64), "f0_parameters_invalid"),
])
def test_policy_side_refusals_after_an_honest_rebind(edit, code):
    """The Decision, the pin and the policy agree, so the check under test is the one that refuses."""
    assert refusal(inputs(edit=edit)) == code


def test_the_shipped_policy_never_yields_evidence():
    config = json.loads((ROOT / "configs" / "bridge_v2_activation.json").read_text(encoding="utf-8"))
    digest = activation.canonical_sha256(config["policy"])
    kwargs = dict(inputs(), policy=config["policy"], decision=Decision("F24", True, "forged", digest, 3),
                  pins={"trusted_policy_sha256": digest, "min_revocation_version": 3})
    assert config["signature"] is None and refusal(kwargs) == "f0_policy_unbounded"


def test_quota_counts_only_on_a_verified_f3_pool_of_the_same_provider():
    rows = [profile("opus", OPUS), profile("unverified", SONNET, pool_id="claude-unverified"),
            profile("other-provider", SONNET, pool_id="codex-plus-5h"),
            profile("unknown-pool", SONNET, pool_id="no-such-pool")]
    ev = compose(**inputs(profiles=rows))["evidence"]
    assert [p["pool"] is None for p in ev["profiles"]] == [False, True, True, True]
    result = cs.select(ev)
    assert result["selected_profile"] == "opus"
    for pid in ("unverified", "other-provider", "unknown-pool"):
        assert result["ineligible"][pid] == ["quota_unknown_or_stale"]


@pytest.mark.parametrize("unit", ["percent_of_pool", "pool_points", "pool_points_per_mtok",
                                  "usd_per_task_api_price", "usd_per_mtok_api_price"])
def test_no_caller_cost_survives_because_f3_has_no_workload_basis(unit):
    rows = [profile("opus", OPUS), profile("sonnet", SONNET)]
    for row in rows:
        row["measured_quota_cost"]["unit"] = unit
    ev = compose(**inputs(profiles=rows))["evidence"]
    assert [p["measured_quota_cost"] for p in ev["profiles"]] == [None, None]
    assert rows[0]["measured_quota_cost"]["unit"] == unit  # the caller's profiles are not mutated


def test_a_previous_snapshot_is_passed_through_and_can_only_unrank():
    kwargs = inputs()
    assert "previous_snapshot" not in compose(**kwargs)["evidence"]
    previous = copy.deepcopy(compose(**kwargs)["evidence"]["registry_snapshot"])
    previous["entries"][0]["score"] = 59.0  # the same measured_on with another value
    ev = compose(**dict(kwargs, previous_snapshot=previous))["evidence"]
    assert ev["previous_snapshot"] == previous
    result = cs.select(ev)
    assert result["verdict"] == cs.UNKNOWN and "unranked:opus:refresh_without_new_measurement" in result["reasons"]


def test_inputs_are_not_mutated():
    kwargs = inputs()
    before = copy.deepcopy({k: v for k, v in kwargs.items() if k != "decision"})
    compose(**kwargs)
    assert {k: v for k, v in kwargs.items() if k != "decision"} == before


def write_f0(tmp_path: Path, policy: dict) -> dict:
    digest = activation.canonical_sha256(policy)
    config = tmp_path / "bridge_v2_activation.json"
    config.write_text(json.dumps({"policy": policy, "signature": {
        "schema": activation.SIGNATURE_SCHEMA, "policy_sha256": digest, "head": HEAD, "tree": TREE,
        "signed_utc": iso(NOW - timedelta(hours=1)), "expires_utc": iso(NOW + timedelta(days=5))}}),
        encoding="utf-8")
    state = tmp_path / "runtime" / activation.REVOCATION_RELATIVE
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"schema": activation.REVOCATION_SCHEMA, "version": 3, "policy_sha256": digest,
                                 "frozen": False, "revoked": [], "updated_utc": iso(NOW - timedelta(minutes=5))}),
                     encoding="utf-8")
    return {"config_path": config, "runtime_root": tmp_path / "runtime", "trusted_policy_sha256": digest,
            "now": NOW, "environ": {}, "min_revocation_version": 3, "expected_head": HEAD, "expected_tree": TREE}


def test_integration_the_real_f0_decision_is_consumed(tmp_path):
    kwargs = inputs()
    decision = activation.evaluate("F24", **write_f0(tmp_path, kwargs["policy"]))
    assert decision.enabled is True, decision.reason
    assert cs.select(compose(**dict(kwargs, decision=decision))["evidence"])["verdict"] == cs.COMPOSER


def test_integration_the_kill_switch_is_carried_by_the_decision(tmp_path):
    kwargs = inputs()
    f0_kwargs = write_f0(tmp_path, kwargs["policy"])
    f0_kwargs["environ"] = {activation.KILL_SWITCH_ENV: "0"}
    decision = activation.evaluate("F24", **f0_kwargs)
    assert decision.enabled is False
    assert refusal(dict(kwargs, decision=decision)) == "f0_decision_disabled"


FORBIDDEN_IMPORTS = {"os", "sys", "pathlib", "subprocess", "socket", "time", "urllib", "sqlite3", "shutil", "io"}
FORBIDDEN_NAMES = {"open", "print", "input", "evaluate", "feature_enabled", "load_policy", "load_revocation",
                   "kill_switch_denies", "_read_json", "load_registry", "model_table", "now", "utcnow", "today",
                   "environ", "getenv", "read_bytes", "read_text"}


def test_adapter_is_pure_by_construction():
    """No I/O, clock, environment, F0 evaluator or F3 file loader in the adapter's own source."""
    tree = ast.parse(Path(adapter.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {alias.name.split(".")[0] for alias in node.names} & FORBIDDEN_IMPORTS
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in FORBIDDEN_IMPORTS
            assert not {alias.name for alias in node.names} & FORBIDDEN_NAMES
        if isinstance(node, ast.Attribute):
            assert node.attr not in FORBIDDEN_NAMES
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in FORBIDDEN_NAMES
