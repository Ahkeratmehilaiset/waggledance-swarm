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


def binding_only_profile(catalog: dict, **fields) -> dict:
    """Lead PR1736-B3 reproducer: a profile reachable only through an advisor binding."""
    policy = catalog["capacity_policy"]
    clone = dict(policy["profiles"]["claude-opus-5-5-medium"], **fields)
    policy["profiles"]["claude-opus-5-5-shadowed"] = clone
    policy["agents"]["fable-5"]["profiles"].append("claude-opus-5-5-shadowed")
    return catalog


def test_unsigned_catalog_refuses_an_approved_profile_reachable_only_by_binding():
    catalog = binding_only_profile(shipped(), approved=True, qualification_ref="REAL-QUAL-REF")
    with pytest.raises(CatalogError, match="approved in an unsigned catalog"):
        validate_catalog(catalog)


def test_unsigned_catalog_refuses_an_approved_profile_no_binding_reaches():
    catalog = shipped()
    catalog["capacity_policy"]["profiles"]["claude-opus-5-5-medium"]["approved"] = True
    catalog["capacity_policy"]["agents"]["fable-5"]["profiles"] = ["claude-opus-5-5-xhigh"]
    catalog["lanes"]["fable-5"].update(allowed_profiles=["claude-opus-5-5-xhigh"], floor=0,
                                       default="claude-opus-5-5-xhigh")
    with pytest.raises(CatalogError, match="approved in an unsigned catalog"):
        validate_catalog(catalog)


def test_signed_catalog_refuses_a_placeholder_reachable_only_by_binding():
    catalog = binding_only_profile(signed_catalog(), approved=True,
                                   qualification_ref="OPERATOR-SIGNATURE-REQUIRED")
    with pytest.raises(CatalogError, match="not approved with a real qualification_ref"):
        validate_catalog(catalog)


def test_signed_catalog_accepts_a_real_binding_only_profile():
    catalog = binding_only_profile(signed_catalog(), approved=True, qualification_ref="qual-real-099")
    assert validate_catalog(catalog)


def test_loader_refuses_duplicate_keys(tmp_path):
    text = SHIPPED.read_text(encoding="utf-8").replace(
        '"verify_timeout_seconds": 900,', '"verify_timeout_seconds": 900, "mode": "auto",', 1)
    path = tmp_path / "catalog.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CatalogError, match="duplicate JSON key"):
        load_catalog(path)


def test_loader_maps_a_nesting_bomb_to_catalog_error(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(path)


def test_loader_never_reads_the_whole_file_before_the_size_bound(tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    path.write_bytes(b" " * (2 * 256 * 1024))
    monkeypatch.setattr(Path, "read_bytes",
                        lambda self: (_ for _ in ()).throw(AssertionError("unbounded read")))
    with pytest.raises(CatalogError, match="size bound"):
        load_catalog(path)



# ---- claude-rco-1 NB3 test gaps and NB4 exit floors (review of 45b95c13)

@pytest.mark.parametrize("name,apply", [
    ("cooldown zero", mutate(["lanes", "fable-5", "cooldown_seconds"], 0)),
    ("cooldown above a day", mutate(["lanes", "fable-5", "cooldown_seconds"], 86_401)),
    ("reviewer flag as int", mutate(["lanes", "claude-rco-1", "reviewer"], 1)),
    ("extra fleet key", mutate(["fleet", "extra"], 1)),
    ("missing fleet key", mutate(["fleet", "verify_timeout_seconds"], delete=True)),
    ("approve_exit missing", mutate(["fleet", "approve_exit"], delete=True)),
    ("approve_exit wrong type", mutate(["fleet", "approve_exit", "min_transitions"], "10")),
    ("duplicate effort name", mutate(["providers", "claude", "efforts"], ["low", "low", "xhigh", "medium"])),
    ("limits not a list", mutate(["capacity_policy", "profiles", "claude-opus-5-5-medium", "limits"], {})),
    ("shadow exit below spec", mutate(["fleet", "shadow_exit", "min_decisions"], 19)),
    ("shadow days below spec", mutate(["fleet", "shadow_exit", "min_days"], 4)),
    ("shadow tolerates wrong decisions", mutate(["fleet", "shadow_exit", "max_operator_marked_wrong"], 1)),
    ("approve exit below spec", mutate(["fleet", "approve_exit", "min_transitions"], 9)),
    ("approve without an induced rollback", mutate(["fleet", "approve_exit", "min_induced_rollbacks"], 0)),
    ("approve tolerates wrong kills", mutate(["fleet", "approve_exit", "max_wrong_process_kills"], 1)),
])
def test_nb3_and_exit_floor_gaps_are_refused(name, apply):
    with pytest.raises(CatalogError):
        validate_catalog(apply(shipped()))


def test_exit_criteria_may_be_stricter_than_the_spec():
    catalog = shipped()
    catalog["fleet"]["shadow_exit"].update(min_decisions=40, min_days=10)
    catalog["fleet"]["approve_exit"].update(min_transitions=20, min_induced_rollbacks=3)
    assert validate_catalog(catalog)


@pytest.mark.parametrize("reference", ["TODO-qual", "unsigned-qual"])
def test_todo_and_unsigned_placeholders_are_refused_when_signed(reference):
    catalog = signed_catalog()
    catalog["capacity_policy"]["profiles"]["claude-opus-5-5-medium"]["qualification_ref"] = reference
    with pytest.raises(CatalogError, match="not approved with a real qualification_ref"):
        validate_catalog(catalog)


@pytest.mark.parametrize("signature,signed", [
    ("unsigned-default", False), ("  UNSIGNED-DEFAULT", False), ("Unsigned", False),
    ("operator 2026-09-26", True)])
def test_is_signed_is_case_and_whitespace_insensitive(signature, signed):
    assert is_signed({"operator_signature": signature}) is signed


def test_blank_signature_is_refused_by_the_signature_guard_itself():
    # An unsigned (approved:false) catalog, so the approval rule cannot mask this guard.
    catalog = shipped()
    catalog["operator_signature"] = "   "
    with pytest.raises(CatalogError, match="operator_signature required"):
        validate_catalog(catalog)


def test_nan_anywhere_is_refused_at_parse_time(tmp_path):
    text = SHIPPED.read_text(encoding="utf-8").replace('"cooldown_seconds": 3600', '"cooldown_seconds": NaN', 1)
    path = tmp_path / "catalog.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CatalogError, match="non-finite JSON number"):
        load_catalog(path)


def test_loader_refuses_a_symlinked_catalog(tmp_path):
    link = tmp_path / "link.json"
    try:
        link.symlink_to(SHIPPED)
    except OSError:
        pytest.skip("symlinks unavailable to this user")
    with pytest.raises(CatalogError, match="symlink or reparse point"):
        load_catalog(link)


def test_loader_requests_at_most_the_bound_plus_one_byte(tmp_path, monkeypatch):
    # Pins the read size itself, not just the absence of Path.read_bytes.
    path = tmp_path / "catalog.json"
    path.write_bytes(b" " * (2 * 256 * 1024))
    requested = []
    real_open = Path.open

    class Recording:
        def __init__(self, stream):
            self._stream = stream

        def read(self, size=-1):
            requested.append(size)
            return self._stream.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._stream.close()

    monkeypatch.setattr(Path, "open", lambda self, *a, **k: Recording(real_open(self, *a, **k)))
    with pytest.raises(CatalogError, match="size bound"):
        load_catalog(path)
    assert requested == [256 * 1024 + 1]
