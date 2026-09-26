# SPDX-License-Identifier: BUSL-1.1
"""The lane profile catalog validator: fail closed on every unsafe shape."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from tools.bridge_capacity_advisor import _profile_checks
from tools.lane_profile_catalog import (
    CatalogError,
    is_signed,
    classify_transition,
    effective_mode,
    load_catalog,
    validate_catalog,
)

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ROOT / "configs" / "lane_profile_catalog.json"


def shipped() -> dict:
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def write(tmp_path: Path, catalog: dict) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


def test_shipped_catalog_validates_in_shadow_with_its_hash():
    catalog, digest = load_catalog(SHIPPED)
    assert digest == hashlib.sha256(SHIPPED.read_bytes()).hexdigest()
    assert catalog["fleet"]["mode"] == "shadow"
    assert catalog["capacity_policy"]["mode"] == "shadow"
    assert catalog["operator_signature"].startswith("UNSIGNED-DEFAULT")


def test_shipped_reviewer_floors_allow_only_raise_or_same():
    catalog = shipped()
    for lane in ("claude-rco-1", "claude-rco-2"):
        spec = catalog["lanes"][lane]
        assert spec["reviewer"] is True
        # The floor is the current (default) profile: nothing weaker is reachable.
        assert spec["floor"] == spec["allowed_profiles"].index(spec["default"])


def mutate(path_expr, value=None, *, delete=False):
    def apply(catalog):
        node = catalog
        for key in path_expr[:-1]:
            node = node[key]
        if delete:
            del node[path_expr[-1]]
        else:
            node[path_expr[-1]] = value
        return catalog
    return apply


BAD = {
    "wrong schema": mutate(["schema"], "wd.lane-profile-catalog.v0"),
    "extra top-level key": mutate(["extra"], 1),
    "lanes smuggled into capacity policy": mutate(["capacity_policy", "lanes"], {}),
    "field smuggled into a profile": mutate(["capacity_policy", "profiles", "claude-opus-5-5-medium", "floor"], 0),
    "field smuggled into an agent binding": mutate(["capacity_policy", "agents", "fable-5", "floor"], 0),
    "missing signature": mutate(["operator_signature"], "  "),
    "unknown mode": mutate(["fleet", "mode"], "yolo"),
    "lane extra key": mutate(["lanes", "fable-5", "extra"], 1),
    "unknown profile": mutate(["lanes", "fable-5", "allowed_profiles"], ["nope"]),
    "duplicate profile": mutate(["lanes", "fable-5", "allowed_profiles"],
                                ["claude-opus-5-5-xhigh", "claude-opus-5-5-xhigh"]),
    "reordered vs policy": mutate(["lanes", "fable-5", "allowed_profiles"],
                                  ["claude-opus-5-5-medium", "claude-opus-5-5-xhigh"]),
    "floor out of range": mutate(["lanes", "fable-5", "floor"], 2),
    "floor not int": mutate(["lanes", "fable-5", "floor"], True),
    "default not allowed": mutate(["lanes", "fable-5", "default"], "claude-sonnet-5-xhigh"),
    "zero budget": mutate(["lanes", "fable-5", "max_relaunches_per_hour"], 0),
    "lane budget above fleet": mutate(["lanes", "fable-5", "max_relaunches_per_hour"], 5),
    "reviewer flag cleared": mutate(["lanes", "claude-rco-1", "reviewer"], False),
    "reviewer flag forged": mutate(["lanes", "fable-5", "reviewer"], True),
    "effort outside enum": mutate(["capacity_policy", "profiles", "claude-opus-5-5-medium", "effort"], "ultra"),
    "undeclared provider": mutate(["providers", "codex"], delete=True),
    "embedded policy not shadow": mutate(["capacity_policy", "mode"], "live"),
    "profile widened beyond policy": mutate(["capacity_policy", "agents", "fable-5", "profiles"],
                                            ["claude-opus-5-5-xhigh"]),
    "no lane policy binding": mutate(["capacity_policy", "agents", "fable-5"], delete=True),
    "exit criterion missing": mutate(["fleet", "shadow_exit", "min_days"], delete=True),
    "verify timeout zero": mutate(["fleet", "verify_timeout_seconds"], 0),
    "approved in an unsigned catalog": mutate(["capacity_policy", "profiles", "claude-opus-5-5-medium", "approved"], True),
    "field smuggled into a provider": mutate(["providers", "claude", "ignored"], 1),
    "field smuggled into a quota limit": mutate(["capacity_policy", "profiles", "claude-opus-5-5-medium", "limits"],
                                                [{"id": "claude", "windows": ["five_hour"], "ignored": 1}]),
    "blank qualification_ref": mutate(["capacity_policy", "profiles", "claude-opus-5-5-medium", "qualification_ref"], " "),
    "no qualification classes": mutate(["capacity_policy", "profiles", "claude-opus-5-5-medium", "qualified_for"], []),
    "role not qualified": mutate(["capacity_policy", "profiles", "claude-opus-5-5-medium", "roles"], ["lead"]),
    "paid api billing": mutate(["capacity_policy", "profiles", "claude-opus-5-5-medium", "billing"], "api"),
    "cross account pool": mutate(["capacity_policy", "profiles", "claude-opus-5-5-medium", "account_pool"], "other-pool"),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_unsafe_catalogs_are_refused(tmp_path, name):
    catalog = BAD[name](shipped())
    with pytest.raises(CatalogError):
        load_catalog(write(tmp_path, catalog))


def test_lane_outside_the_fleet_is_refused_even_when_fully_bound():
    # grok-scout-1 is a valid advisor role, so only the lane allowlist refuses it.
    catalog = shipped()
    catalog["capacity_policy"]["agents"]["grok-scout-1"] = {
        "role": "grok", "profiles": ["claude-opus-5-5-xhigh"]}
    catalog["lanes"]["grok-scout-1"] = {
        "allowed_profiles": ["claude-opus-5-5-xhigh"], "floor": 0,
        "default": "claude-opus-5-5-xhigh", "max_relaunches_per_hour": 1,
        "cooldown_seconds": 60, "reviewer": False}
    with pytest.raises(CatalogError, match="unknown lane"):
        validate_catalog(catalog)


def test_default_below_floor_is_refused():
    catalog = shipped()
    lane = catalog["lanes"]["fable-5"]
    lane["floor"] = 0
    lane["default"] = "claude-opus-5-5-medium"
    with pytest.raises(CatalogError, match="below its floor"):
        validate_catalog(catalog)


def test_non_finite_json_and_oversize_are_refused(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(SHIPPED.read_text(encoding="utf-8").replace('"floor": 1', '"floor": NaN', 1),
                    encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(path)
    path.write_bytes(b" " * (256 * 1024 + 1))
    with pytest.raises(CatalogError, match="size bound"):
        load_catalog(path)


def three_step_catalog() -> dict:
    """A non-reviewer lane with three profiles and the floor at the weakest."""
    catalog = shipped()
    profiles = catalog["capacity_policy"]["profiles"]
    profiles["claude-opus-5-5-low"] = dict(profiles["claude-opus-5-5-medium"], effort="low")
    order = ["claude-opus-5-5-xhigh", "claude-opus-5-5-medium", "claude-opus-5-5-low"]
    catalog["capacity_policy"]["agents"]["fable-5"]["profiles"] = order
    catalog["lanes"]["fable-5"].update(allowed_profiles=order, floor=1,
                                       default="claude-opus-5-5-medium")
    return validate_catalog(catalog)


@pytest.mark.parametrize("lane,current,target,verdict,reason", [
    ("claude-rco-1", "claude-sonnet-5-xhigh", "claude-opus-5-5-xhigh", "raise", "stronger_profile"),
    ("claude-rco-1", "claude-sonnet-5-xhigh", "claude-sonnet-5-xhigh", "same", "no_change"),
    ("claude-rco-1", "claude-opus-5-5-xhigh", "claude-sonnet-5-xhigh", "park", "reviewer_lowering"),
    ("fable-5", "claude-opus-5-5-xhigh", "claude-opus-5-5-medium", "lower", "weaker_profile_within_floor"),
    ("fable-5", "claude-opus-5-5-medium", "claude-opus-5-5-low", "park", "below_floor"),
    ("fable-5", "claude-opus-5-5-medium", "claude-sonnet-5-xhigh", "park", "target_not_allowed"),
    ("fable-5", None, "claude-opus-5-5-xhigh", "park", "current_profile_unknown"),
    ("fable-5", "something-else", "claude-opus-5-5-xhigh", "park", "current_profile_unknown"),
    ("grok-scout-1", None, "claude-opus-5-5-xhigh", "park", "lane_not_in_catalog"),
])
def test_classify_transition(lane, current, target, verdict, reason):
    result = classify_transition(three_step_catalog(), lane, current, target)
    assert result["verdict"] == verdict
    assert result["reason"] == reason
    assert result["operator_ack_required"] is (verdict == "park")


def test_validation_does_not_mutate_the_catalog():
    catalog = shipped()
    before = copy.deepcopy(catalog)
    validate_catalog(catalog)
    assert catalog == before


@pytest.mark.parametrize("fleet_mode", ["shadow", "approve", "auto"])
def test_fleet_mode_is_not_a_second_switch(fleet_mode):
    # The advisor accepts only a shadow policy, so the effective mode stays shadow.
    catalog = shipped()
    catalog["fleet"]["mode"] = fleet_mode
    assert effective_mode(validate_catalog(catalog)) == "shadow"


@pytest.mark.parametrize("policy_mode,fleet_mode,expected", [
    ("shadow", "auto", "shadow"), ("approve", "auto", "approve"),
    ("auto", "approve", "approve"), ("auto", "auto", "auto"), ("live", "auto", "shadow")])
def test_effective_mode_is_the_minimum(policy_mode, fleet_mode, expected):
    catalog = {"capacity_policy": {"mode": policy_mode}, "fleet": {"mode": fleet_mode}}
    assert effective_mode(catalog) == expected


def test_cross_provider_lane_is_refused():
    catalog = shipped()
    policy = catalog["capacity_policy"]
    policy["agents"]["fable-5"]["profiles"] = ["claude-opus-5-5-xhigh", "codex-gpt-6-sol-high"]
    policy["profiles"]["codex-gpt-6-sol-high"]["roles"].append("fable")
    catalog["lanes"]["fable-5"].update(allowed_profiles=["claude-opus-5-5-xhigh", "codex-gpt-6-sol-high"],
                                       default="codex-gpt-6-sol-high")
    with pytest.raises(CatalogError, match="mixes providers"):
        validate_catalog(catalog)


def signed_catalog() -> dict:
    catalog = shipped()
    catalog["operator_signature"] = "operator 2026-09-26 reviewed PR"
    for number, profile in enumerate(catalog["capacity_policy"]["profiles"].values()):
        profile["approved"] = True
        profile["qualification_ref"] = f"qual-2026-09-26-{number:03d}"
    return catalog


def test_signed_state_is_read_from_the_signature():
    assert is_signed(shipped()) is False
    assert is_signed(signed_catalog()) is True
    assert validate_catalog(signed_catalog())


@pytest.mark.parametrize("field,value", [
    ("approved", False),
    ("qualification_ref", "OPERATOR-SIGNATURE-REQUIRED"),
    ("qualification_ref", "placeholder-ref"),
    ("qualification_ref", "SYNTHETIC-NOT-A-LIVE-APPROVAL"),
])
def test_signed_catalog_needs_real_approvals(field, value):
    catalog = signed_catalog()
    catalog["capacity_policy"]["profiles"]["claude-opus-5-5-medium"][field] = value
    with pytest.raises(CatalogError, match="not approved with a real qualification_ref"):
        validate_catalog(catalog)


def test_shipped_unsigned_catalog_yields_no_admissible_candidate():
    # Lead review PR1736-B2: run the advisor's own runtime check on every lane profile.
    catalog = shipped()
    policy = catalog["capacity_policy"]
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    for lane, spec in catalog["lanes"].items():
        binding = policy["agents"][lane]
        for profile_id in spec["allowed_profiles"]:
            profile = policy["profiles"][profile_id]
            agent = {"provider": profile["provider"], "current_profile": spec["default"],
                     "catalog": [{"model": profile["model"], "effort": profile["effort"],
                                  "source_ref": "fixture", "observed_at": now.isoformat()}]}
            task = {"qualification_class": profile["qualified_for"][0]}
            issues = _profile_checks(profile, binding, agent, task, {"state": "available"}, policy, now)
            assert "qualification_not_approved" in issues, (lane, profile_id, issues)
