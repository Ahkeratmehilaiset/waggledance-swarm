"""Targeted fixtures for tools/bridge_v2_activation.py (F0).

Authored under the 2026-09-29 operator no-runs directive: NOT executed by the
author. Independent review and CI run them.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from tools import bridge_v2_activation as act

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
TREE = "b" * 40


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _policy(**overrides) -> dict:
    policy = json.loads((REPO / "configs" / "bridge_v2_activation.json").read_text(encoding="utf-8"))["policy"]
    policy = copy.deepcopy(policy)
    policy["expires_utc"] = _stamp(NOW + timedelta(days=14))
    policy["revocation_max_age_seconds"] = 86400
    policy["features"]["F1"].update(enabled=True, stage=1)
    policy.update(overrides)
    return policy


def _write(tmp_path: Path, policy: dict, *, signature: dict | None | str = "auto",
           revocation: dict | None | str = "auto") -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    digest = act.canonical_sha256(policy)
    if signature == "auto":
        signature = {"schema": act.SIGNATURE_SCHEMA, "policy_sha256": digest, "head": HEAD, "tree": TREE,
                     "signed_utc": _stamp(NOW - timedelta(hours=1)),
                     "expires_utc": _stamp(NOW + timedelta(days=14))}
    config = tmp_path / "bridge_v2_activation.json"
    config.write_text(json.dumps({"policy": policy, "signature": signature}), encoding="utf-8")
    runtime = tmp_path / "runtime"
    (runtime / "bridge_v2").mkdir(parents=True)
    if revocation == "auto":
        revocation = {"schema": act.REVOCATION_SCHEMA, "version": 3, "policy_sha256": digest,
                      "frozen": False, "revoked": [], "updated_utc": _stamp(NOW - timedelta(minutes=1))}
    if revocation is not None:
        (runtime / "bridge_v2" / "revocation.json").write_text(json.dumps(revocation), encoding="utf-8")
    return {"config_path": config, "runtime_root": runtime, "trusted_policy_sha256": digest,
            "now": NOW, "environ": {}, "expected_head": HEAD, "expected_tree": TREE,
            "min_revocation_version": 1}


def test_shipped_default_config_is_valid_all_off_and_unsigned():
    document = json.loads((REPO / "configs" / "bridge_v2_activation.json").read_text(encoding="utf-8"))
    assert document["signature"] is None
    policy = act.validate_policy(document["policy"])
    assert policy["features"] and not any(spec["enabled"] for spec in policy["features"].values())
    assert not any(policy["policy_bits"].values())


def test_shipped_default_config_disables_every_feature(tmp_path):
    document = json.loads((REPO / "configs" / "bridge_v2_activation.json").read_text(encoding="utf-8"))
    digest = act.canonical_sha256(document["policy"])
    for name in document["policy"]["features"]:
        decision = act.evaluate(name, config_path=REPO / "configs" / "bridge_v2_activation.json",
                                runtime_root=tmp_path, trusted_policy_sha256=digest, now=NOW, environ={})
        assert decision.enabled is False
        assert "unsigned" in decision.reason


def test_fully_bound_policy_enables_only_the_granted_feature(tmp_path):
    kwargs = _write(tmp_path, _policy())
    assert act.feature_enabled("F1", **kwargs) is True
    assert act.evaluate("F2", **kwargs).reason == "feature off in the signed policy"
    assert act.feature_enabled("F2", **kwargs) is False


@pytest.mark.parametrize("trusted", [None, "", "0" * 64, "A" * 64, 123])
def test_missing_or_wrong_trusted_digest_disables(tmp_path, trusted):
    kwargs = _write(tmp_path, _policy())
    kwargs["trusted_policy_sha256"] = trusted
    assert act.feature_enabled("F1", **kwargs) is False


def test_signature_must_bind_the_policy_digest(tmp_path):
    kwargs = _write(tmp_path, _policy())
    document = json.loads(kwargs["config_path"].read_text(encoding="utf-8"))
    document["signature"]["policy_sha256"] = "c" * 64
    kwargs["config_path"].write_text(json.dumps(document), encoding="utf-8")
    assert act.feature_enabled("F1", **kwargs) is False


def test_policy_edit_after_signing_disables(tmp_path):
    kwargs = _write(tmp_path, _policy())
    document = json.loads(kwargs["config_path"].read_text(encoding="utf-8"))
    document["policy"]["features"]["F2"].update(enabled=True, stage=1)
    kwargs["config_path"].write_text(json.dumps(document), encoding="utf-8")
    assert act.feature_enabled("F2", **kwargs) is False
    assert act.feature_enabled("F1", **kwargs) is False


def test_expected_head_and_tree_are_enforced(tmp_path):
    kwargs = _write(tmp_path, _policy())
    assert act.feature_enabled("F1", **kwargs) is True
    assert act.feature_enabled("F1", **{**kwargs, "expected_head": "d" * 40}) is False
    assert act.feature_enabled("F1", **{**kwargs, "expected_tree": "d" * 40}) is False


@pytest.mark.parametrize("mutate", [
    lambda text: text[:-5],
    lambda text: text.replace('"policy_version": 1', '"policy_version": 1, "policy_version": 1'),
    lambda text: text.replace('"policy_version": 1', '"policy_version": NaN'),
    lambda text: "﻿" + text,
])
def test_corrupt_config_disables(tmp_path, mutate):
    kwargs = _write(tmp_path, _policy())
    text = json.dumps(json.loads(kwargs["config_path"].read_text(encoding="utf-8")), indent=1).replace(
        '"policy_version":1', '"policy_version": 1')
    kwargs["config_path"].write_text(mutate(text), encoding="utf-8")
    assert act.feature_enabled("F1", **kwargs) is False


def test_missing_config_disables(tmp_path):
    kwargs = _write(tmp_path, _policy())
    kwargs["config_path"].unlink()
    assert act.evaluate("F1", **kwargs).enabled is False


@pytest.mark.parametrize("change", [
    {"schema": "wd.bridge-v2-activation.v2"},
    {"policy_version": 0},
    {"policy_version": True},
    {"expires_utc": "2026-10-15T12:00:00+00:00"},
    {"revocation_max_age_seconds": 10},
    {"parameters": []},
    {"unknown_key": 1},
])
def test_strict_policy_schema(change):
    policy = _policy()
    policy.update(change)
    with pytest.raises(act.ActivationError):
        act.validate_policy(policy)


def test_feature_spec_is_strict():
    policy = _policy()
    policy["features"]["F2"]["enabled"] = 1
    with pytest.raises(act.ActivationError):
        act.validate_policy(policy)
    policy = _policy()
    policy["features"]["F2"].update(enabled=True, stage=None)
    with pytest.raises(act.ActivationError):
        act.validate_policy(policy)
    policy = _policy()
    policy["features"]["F99"] = {"enabled": False, "stage": None, "requires": [], "requires_bits": []}
    with pytest.raises(act.ActivationError):
        act.validate_policy(policy)


def test_dependencies_between_features_and_bits_are_validated():
    policy = _policy()
    policy["features"]["F2"].update(enabled=True, stage=2, requires=["F3"])
    with pytest.raises(act.ActivationError):
        act.validate_policy(policy)
    policy = _policy()
    policy["features"]["F2"].update(enabled=True, stage=2, requires_bits=["f19_routing_policy"])
    with pytest.raises(act.ActivationError):
        act.validate_policy(policy)
    policy = _policy(policy_bit_requires={"learning_2_11": ["f19_routing_policy"]})
    policy["policy_bits"]["learning_2_11"] = True
    with pytest.raises(act.ActivationError):
        act.validate_policy(policy)
    policy["policy_bits"]["f19_routing_policy"] = True
    act.validate_policy(policy)


def test_policy_and_signature_expiry_disable(tmp_path):
    kwargs = _write(tmp_path, _policy())
    assert act.feature_enabled("F1", **{**kwargs, "now": NOW + timedelta(days=14)}) is False
    kwargs = _write(tmp_path / "b", _policy(expires_utc=None))
    decision = act.evaluate("F1", **kwargs)
    assert decision.enabled is False and "expires_utc" in decision.reason


def test_naive_decision_time_disables(tmp_path):
    kwargs = _write(tmp_path, _policy())
    kwargs["now"] = NOW.replace(tzinfo=None)
    assert act.feature_enabled("F1", **kwargs) is False


def test_missing_revocation_state_disables(tmp_path):
    kwargs = _write(tmp_path, _policy(), revocation=None)
    assert act.feature_enabled("F1", **kwargs) is False


def test_freeze_and_revocation_disable(tmp_path):
    policy = _policy()
    digest = act.canonical_sha256(policy)
    base = {"schema": act.REVOCATION_SCHEMA, "version": 3, "policy_sha256": digest, "frozen": True,
            "revoked": [], "updated_utc": _stamp(NOW)}
    assert act.evaluate("F1", **_write(tmp_path / "a", policy, revocation=base)).reason == "fleet freeze is active"
    revoked = dict(base, frozen=False, revoked=["F1"])
    assert act.evaluate("F1", **_write(tmp_path / "b", policy, revocation=revoked)).reason == "feature revoked"


@pytest.mark.parametrize("change", [
    {"policy_sha256": "e" * 64},
    {"version": 0},
    {"frozen": "false"},
    {"revoked": "F1"},
    {"updated_utc": _stamp(NOW + timedelta(hours=1))},
    {"schema": "wd.bridge-v2-revocation.v0"},
    {"extra": True},
])
def test_invalid_or_mismatched_revocation_disables(tmp_path, change):
    policy = _policy()
    state = {"schema": act.REVOCATION_SCHEMA, "version": 3, "policy_sha256": act.canonical_sha256(policy),
             "frozen": False, "revoked": [], "updated_utc": _stamp(NOW)}
    state.update(change)
    assert act.feature_enabled("F1", **_write(tmp_path, policy, revocation=state)) is False


def test_revocation_version_rollback_disables(tmp_path):
    kwargs = _write(tmp_path, _policy())
    assert act.feature_enabled("F1", **{**kwargs, "min_revocation_version": 3}) is True
    assert act.feature_enabled("F1", **{**kwargs, "min_revocation_version": 4}) is False


def test_stale_revocation_disables_when_policy_sets_max_age(tmp_path):
    policy = _policy(revocation_max_age_seconds=3600)
    state = {"schema": act.REVOCATION_SCHEMA, "version": 1, "policy_sha256": act.canonical_sha256(policy),
             "frozen": False, "revoked": [], "updated_utc": _stamp(NOW - timedelta(hours=2))}
    assert act.evaluate("F1", **_write(tmp_path, policy, revocation=state)).enabled is False


def test_revocation_is_reread_on_every_decision(tmp_path):
    kwargs = _write(tmp_path, _policy())
    assert act.feature_enabled("F1", **kwargs) is True
    path = kwargs["runtime_root"] / "bridge_v2" / "revocation.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(version=4, frozen=True)
    path.write_text(json.dumps(state), encoding="utf-8")
    assert act.feature_enabled("F1", **kwargs) is False


@pytest.mark.parametrize("value,enabled", [
    ("0", False), ("false", False), ("off", False), ("", False), ("garbage", False),
    ("1", True), ("true", True), (" ON ", True),
])
def test_env_kill_switch_is_deny_only(tmp_path, value, enabled):
    kwargs = _write(tmp_path, _policy())
    kwargs["environ"] = {act.KILL_SWITCH_ENV: value}
    assert act.feature_enabled("F1", **kwargs) is enabled
    kwargs["environ"] = {act.KILL_SWITCH_ENV: "1"}
    assert act.feature_enabled("F2", **kwargs) is False  # the switch never grants


def test_stage_state_never_grants(tmp_path):
    kwargs = _write(tmp_path, _policy())
    stage = kwargs["runtime_root"] / "bridge_v2" / "stage_state.json"
    stage.write_text(json.dumps({"features": {"F2": {"enabled": True, "stage": 1}}, "stage": 5}), encoding="utf-8")
    assert act.feature_enabled("F2", **kwargs) is False


def test_revoked_requirement_disables_dependent(tmp_path):
    policy = _policy()
    policy["features"]["F2"].update(enabled=True, stage=1, requires=["F1"])
    state = {"schema": act.REVOCATION_SCHEMA, "version": 1, "policy_sha256": act.canonical_sha256(policy),
             "frozen": False, "revoked": ["F1"], "updated_utc": _stamp(NOW)}
    decision = act.evaluate("F2", **_write(tmp_path, policy, revocation=state))
    assert decision.enabled is False and "required feature F1" in decision.reason


@pytest.mark.parametrize("name", ["F0", "F31", "f1", "F01", "", None, "F1 "])
def test_unknown_feature_names_disable(tmp_path, name):
    kwargs = _write(tmp_path, _policy())
    assert act.feature_enabled(name, **kwargs) is False


def test_module_is_pure_no_writes(tmp_path):
    kwargs = _write(tmp_path, _policy())
    before = sorted((p.relative_to(tmp_path).as_posix(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*"))
    act.evaluate("F1", **kwargs)
    act.evaluate("F2", **kwargs)
    after = sorted((p.relative_to(tmp_path).as_posix(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*"))
    assert before == after


# ---------------------------------------------------------------------------
# RCO2 review fixes (F0-1 .. F0-6)
# ---------------------------------------------------------------------------

def _chain_policy() -> dict:
    policy = _policy()
    policy["features"]["F2"].update(enabled=True, stage=1, requires=["F1"])
    policy["features"]["F3"].update(enabled=True, stage=1, requires=["F2"])
    return policy


def _state(policy, revoked, version=1):
    return {"schema": act.REVOCATION_SCHEMA, "version": version, "policy_sha256": act.canonical_sha256(policy),
            "frozen": False, "revoked": revoked, "updated_utc": _stamp(NOW)}


def test_transitive_revocation_disables_descendants(tmp_path):
    policy = _chain_policy()
    kwargs = _write(tmp_path / "a", policy, revocation=_state(policy, ["F1"]))
    for name in ("F1", "F2", "F3"):
        assert act.feature_enabled(name, **kwargs) is False
    assert "F1" in act.evaluate("F3", **kwargs).reason
    kwargs = _write(tmp_path / "b", policy, revocation=_state(policy, ["F2"]))
    assert act.feature_enabled("F1", **kwargs) is True
    assert act.feature_enabled("F3", **kwargs) is False
    kwargs = _write(tmp_path / "c", policy, revocation=_state(policy, []))
    assert all(act.feature_enabled(name, **kwargs) for name in ("F1", "F2", "F3"))


@pytest.mark.parametrize("edges", [
    {"F1": ["F2"], "F2": ["F1"]},
    {"F1": ["F2"], "F2": ["F3"], "F3": ["F1"]},
])
def test_feature_dependency_cycles_are_rejected_even_when_off(edges):
    policy = _policy()
    policy["features"]["F1"].update(enabled=False, stage=None)
    for name, requires in edges.items():
        policy["features"][name]["requires"] = requires
    with pytest.raises(act.ActivationError, match="cycle"):
        act.validate_policy(policy)


def test_policy_bit_cycles_are_rejected():
    policy = _policy(policy_bit_requires={"learning_2_11": ["f19_routing_policy"],
                                          "f19_routing_policy": ["learning_2_11"]})
    with pytest.raises(act.ActivationError, match="cycle"):
        act.validate_policy(policy)


def test_enabling_policy_requires_expiry_and_freshness():
    with pytest.raises(act.ActivationError, match="revocation_max_age_seconds"):
        act.validate_policy(_policy(revocation_max_age_seconds=None))
    with pytest.raises(act.ActivationError, match="expires_utc"):
        act.validate_policy(_policy(expires_utc=None))
    bits_only = json.loads((REPO / "configs" / "bridge_v2_activation.json").read_text(encoding="utf-8"))["policy"]
    act.validate_policy(copy.deepcopy(bits_only))  # all off: null expiry and max age are fine
    bits_only["policy_bits"]["rule8_amendment"] = True
    with pytest.raises(act.ActivationError, match="an enabling policy"):
        act.validate_policy(bits_only)


@pytest.mark.parametrize("change", [
    {"expected_head": None}, {"expected_tree": None}, {"min_revocation_version": None},
    {"expected_head": "A" * 40}, {"min_revocation_version": True}, {"min_revocation_version": 0},
    {"min_revocation_version": "1"},
])
def test_enabled_outcome_requires_caller_pins(tmp_path, change):
    kwargs = _write(tmp_path, _policy())
    assert act.feature_enabled("F1", **kwargs) is True
    assert act.feature_enabled("F1", **{**kwargs, **change}) is False


@pytest.mark.parametrize("signed,expires,fragment", [
    (NOW + timedelta(hours=1), NOW + timedelta(days=14), "future"),
    (NOW + timedelta(days=14), NOW + timedelta(days=14), "earlier than"),
    (NOW - timedelta(hours=1), NOW + timedelta(days=15), "outlives"),
])
def test_signature_time_consistency(tmp_path, signed, expires, fragment):
    policy = _policy()
    signature = {"schema": act.SIGNATURE_SCHEMA, "policy_sha256": act.canonical_sha256(policy), "head": HEAD,
                 "tree": TREE, "signed_utc": _stamp(signed), "expires_utc": _stamp(expires)}
    decision = act.evaluate("F1", **_write(tmp_path, policy, signature=signature))
    assert decision.enabled is False and fragment in decision.reason


def test_config_read_is_bounded(tmp_path, monkeypatch):
    kwargs = _write(tmp_path, _policy())
    kwargs["config_path"].write_bytes(b" " * (act.MAX_FILE_BYTES + 1))

    def unbounded(_self):
        raise AssertionError("unbounded read_bytes used")

    monkeypatch.setattr(Path, "read_bytes", unbounded)
    decision = act.evaluate("F1", **kwargs)
    assert decision.enabled is False and "exceeds" in decision.reason

# --- the exact-bytes entry point (RCO1 N9, Lead 89514277) ---------------------------------------------------

def _bytes_kwargs(kwargs: dict) -> dict:
    """evaluate_bytes arguments for the very inputs _write laid out on disk."""
    revocation_path = Path(kwargs["runtime_root"]) / act.REVOCATION_RELATIVE
    rest = {key: value for key, value in kwargs.items() if key not in ("config_path", "runtime_root")}
    return {"config_bytes": Path(kwargs["config_path"]).read_bytes(),
            "revocation_bytes": revocation_path.read_bytes(), **rest}


def _scenario(tmp_path: Path, case: str) -> tuple[str, dict]:
    policy = _policy()
    digest = act.canonical_sha256(policy)
    state = {"schema": act.REVOCATION_SCHEMA, "version": 3, "policy_sha256": digest, "frozen": False, "revoked": [],
             "updated_utc": _stamp(NOW - timedelta(minutes=1))}
    if case == "frozen":
        state["frozen"] = True
    if case == "revoked":
        state["revoked"] = ["F1"]
    if case == "stale":
        state["updated_utc"] = _stamp(NOW - timedelta(days=2))
    kwargs = _write(tmp_path, policy, signature=None if case == "unsigned" else "auto", revocation=state)
    if case == "rollback":
        kwargs["min_revocation_version"] = 5
    if case == "kill":
        kwargs["environ"] = {act.KILL_SWITCH_ENV: "0"}
    if case == "wrong_digest":
        kwargs["trusted_policy_sha256"] = "0" * 64
    return ("F2" if case == "feature_off" else "F1"), kwargs


@pytest.mark.parametrize("case", ["enabled", "feature_off", "frozen", "revoked", "stale", "rollback", "unsigned",
                                  "kill", "wrong_digest"])
def test_evaluate_bytes_decides_exactly_like_evaluate_on_the_same_bytes(tmp_path, case):
    feature, kwargs = _scenario(tmp_path, case)
    by_path = act.evaluate(feature, **kwargs)
    assert act.evaluate_bytes(feature, **_bytes_kwargs(kwargs)) == by_path
    assert by_path.enabled is (case == "enabled")


def test_the_path_api_reads_each_file_once_and_the_bytes_api_reads_none(tmp_path, monkeypatch):
    feature, kwargs = _scenario(tmp_path, "enabled")
    reads = []
    real = act._read_raw
    monkeypatch.setattr(act, "_read_raw", lambda path, what: (reads.append(what), real(path, what))[1])
    assert act.evaluate(feature, **kwargs).enabled is True
    assert reads == ["activation config", "revocation state"]
    reads.clear()
    arguments = _bytes_kwargs(kwargs)   # captured before the files go away
    Path(kwargs["config_path"]).unlink()
    (Path(kwargs["runtime_root"]) / act.REVOCATION_RELATIVE).unlink()
    assert act.evaluate_bytes(feature, **arguments).enabled is True and reads == []


@pytest.mark.parametrize("config, revocation, fragment", [
    ("{}", b"{}", "policy: activation config must be exact bytes"),
    (bytearray(b"{}"), b"{}", "policy: activation config must be exact bytes"),
    (b"x" * (act.MAX_FILE_BYTES + 1), b"{}", "policy: activation config exceeds"),
    (None, b"{}", "policy: activation config must be exact bytes"),
], ids=["str", "bytearray", "oversized", "none"])
def test_evaluate_bytes_refuses_anything_but_bounded_exact_bytes(tmp_path, config, revocation, fragment):
    feature, kwargs = _scenario(tmp_path, "enabled")
    arguments = dict(_bytes_kwargs(kwargs), config_bytes=config, revocation_bytes=revocation)
    decision = act.evaluate_bytes(feature, **arguments)
    assert decision.enabled is False and decision.reason.startswith(fragment)


def test_evaluate_bytes_refuses_a_non_bytes_revocation_after_the_policy_binds(tmp_path):
    feature, kwargs = _scenario(tmp_path, "enabled")
    decision = act.evaluate_bytes(feature, **dict(_bytes_kwargs(kwargs), revocation_bytes="{}"))
    assert decision.enabled is False and decision.reason == "revocation/expiry: revocation state must be exact bytes"