# SPDX-License-Identifier: BUSL-1.1
"""Pure F0 -> F15 switch-evidence adapter.

Authored under the operator's no-test-run directive (2026-09-29): not yet executed.
The integration fixtures below run the REAL F0 evaluator on files written under
tmp_path and feed its Decision to the adapter, to show that the two agree.
"""
from __future__ import annotations

import ast
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

import tools.bridge_v2_activation as activation
import tools.bridge_v2_switch_evidence as adapter
from tools.bridge_v2_switch_evidence import Refusal, switch_activation

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)
HEAD, TREE = "1" * 40, "2" * 40
PARAMETERS = {"tick_seconds": 300, "hysteresis_percent": 5, "evidence_max_age_seconds": 600, "budget_mode": "steady"}


def iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def ago(**delta) -> str:
    return iso(NOW - timedelta(**delta))


def f0_inputs(parameters: dict | None = None, *, enabled: bool = True) -> dict:
    """The caller-verified F0 inputs that enable F15: the shipped policy with F15 switched on."""
    config = json.loads((ROOT / "configs" / "bridge_v2_activation.json").read_text(encoding="utf-8"))
    signed = config["policy"]
    signed["features"]["F15"].update(enabled=enabled, stage=1 if enabled else None)
    signed.update(expires_utc=iso(NOW + timedelta(days=10)), revocation_max_age_seconds=3600,
                  parameters={"F15": dict(PARAMETERS if parameters is None else parameters)})
    digest = activation.canonical_sha256(signed)
    return {
        "pins": {"trusted_policy_sha256": digest, "expected_head": HEAD, "expected_tree": TREE,
                 "min_revocation_version": 3},
        "document": {"policy": signed,
                     "signature": {"schema": activation.SIGNATURE_SCHEMA, "policy_sha256": digest, "head": HEAD,
                                   "tree": TREE, "signed_utc": ago(hours=1),
                                   "expires_utc": iso(NOW + timedelta(days=5))}},
        "revocation": {"schema": activation.REVOCATION_SCHEMA, "version": 3, "policy_sha256": digest,
                       "frozen": False, "revoked": [], "updated_utc": ago(minutes=5)},
        "decision": {"feature": "F15", "enabled": True, "reason": "enabled by the signed policy",
                     "policy_sha256": digest, "revocation_version": 3},
    }


def refusal(f0, now=NOW) -> str:
    with pytest.raises(Refusal) as caught:
        switch_activation(f0, now)
    return caught.value.code


def test_success_twin_returns_the_signed_f15_parameters():
    f0 = f0_inputs()
    got = switch_activation(f0, NOW)
    assert got == {"feature": "F15", "policy_sha256": f0["pins"]["trusted_policy_sha256"], "revocation_version": 3,
                   "head": HEAD, "tree": TREE, "parameters": PARAMETERS}
    assert adapter.DECISION_KEYS == {"feature", "enabled", "reason", "policy_sha256", "revocation_version"}


def policy_of(f0: dict) -> dict:
    return f0["document"]["policy"]


def rebind(f0: dict) -> dict:
    """Re-pin every digest after a policy change, as an honest caller with a new signed packet would."""
    digest = activation.canonical_sha256(policy_of(f0))
    f0["pins"]["trusted_policy_sha256"] = f0["document"]["signature"]["policy_sha256"] = digest
    f0["revocation"]["policy_sha256"] = f0["decision"]["policy_sha256"] = digest
    return f0


@pytest.mark.parametrize("change, code", [
    (lambda f: f.pop("decision"), "f0_inputs_missing"),
    (lambda f: f.update(extra=1), "f0_inputs_missing"),
    (lambda f: f["decision"].pop("reason"), "f0_decision_missing"),
    (lambda f: f["decision"].update(feature="F16"), "f0_feature_mismatch"),
    (lambda f: f["decision"].update(enabled=False), "f0_decision_disabled"),
    (lambda f: f["decision"].update(enabled=1), "f0_decision_disabled"),
    (lambda f: f["pins"].update(trusted_policy_sha256=None), "f0_pins_missing"),
    (lambda f: f["pins"].update(expected_tree="X" * 40), "f0_pins_missing"),
    # A valid 40-hex tree that differs from the signed one reaches the signature binding.
    (lambda f: f["pins"].update(expected_tree="4" * 40), "f0_signature_invalid"),
    (lambda f: f["pins"].update(min_revocation_version=0), "f0_pins_missing"),
    (lambda f: f["pins"].update(min_revocation_version=True), "f0_pins_missing"),
    (lambda f: f["document"].pop("signature"), "f0_document_malformed"),
    (lambda f: policy_of(f).update(schema="wd.bridge-v2-activation.v0"), "f0_policy_invalid"),
    # A caller must never take the trusted digest from the file; a mismatching pin refuses.
    (lambda f: f["pins"].update(trusted_policy_sha256="d" * 64), "f0_policy_unbound"),
    (lambda f: policy_of(f)["parameters"]["F15"].update(tick_seconds=60), "f0_policy_unbound"),
    (lambda f: f["document"].update(signature=None), "f0_policy_unsigned"),
    (lambda f: f["pins"].update(expected_head="3" * 40), "f0_signature_invalid"),
    (lambda f: f["document"]["signature"].update(policy_sha256="e" * 64), "f0_signature_invalid"),
    (lambda f: f["document"]["signature"].update(expires_utc=iso(NOW + timedelta(days=20))),
     "f0_signature_outlives_policy"),
    (lambda f: f["document"]["signature"].update(expires_utc=ago(minutes=1), signed_utc=ago(hours=2)), "f0_expired"),
    (lambda f: f["document"]["signature"].update(signed_utc=iso(NOW + timedelta(hours=1))),
     "f0_signature_from_the_future"),
    (lambda f: f["decision"].update(policy_sha256="f" * 64), "f0_decision_unbound"),
    (lambda f: f["decision"].update(revocation_version=4), "f0_decision_unbound"),
    (lambda f: f["revocation"].pop("frozen"), "f0_revocation_malformed"),
    (lambda f: f["revocation"].update(revoked=["switch_policy"]), "f0_revocation_malformed"),
    (lambda f: f["revocation"].update(updated_utc="2026-09-29T20:55:00"), "f0_revocation_malformed"),
    (lambda f: f["pins"].update(min_revocation_version=4), "f0_revocation_rollback"),
    (lambda f: f["revocation"].update(policy_sha256="c" * 64), "f0_revocation_unbound"),
    (lambda f: f["revocation"].update(updated_utc=iso(NOW + timedelta(minutes=10))), "f0_revocation_from_the_future"),
    (lambda f: f["revocation"].update(updated_utc=ago(hours=2)), "f0_revocation_stale"),
    (lambda f: f["revocation"].update(frozen=True), "f0_frozen"),
    (lambda f: f["revocation"].update(revoked=["F15"]), "f0_revoked"),
])
def test_each_unbound_stale_or_malformed_input_refuses(change, code):
    f0 = f0_inputs()
    change(f0)
    assert refusal(f0) == code


def test_policy_side_refusals_after_an_honest_rebind():
    f0 = f0_inputs(enabled=False)
    assert refusal(f0) == "f0_feature_off"  # F0 said enabled, but the signed policy has F15 off
    f0 = f0_inputs()
    policy_of(f0)["features"]["F16"].update(enabled=True, stage=1)
    policy_of(f0)["features"]["F15"]["requires"] = ["F16"]
    f0["revocation"]["revoked"] = ["F16"]
    assert refusal(rebind(f0)) == "f0_dependency_blocked"
    f0 = f0_inputs({k: v for k, v in PARAMETERS.items() if k != "tick_seconds"})
    assert refusal(f0) == "f0_parameters_unknown"
    f0 = f0_inputs(dict(PARAMETERS, extra=1))
    assert refusal(f0) == "f0_parameters_unknown"
    f0 = f0_inputs()
    policy_of(f0)["parameters"] = {}
    assert refusal(rebind(f0)) == "f0_parameters_unknown"
    # A policy that enables nothing may omit its expiry; it never yields F15 parameters.
    f0 = f0_inputs(enabled=False)
    policy_of(f0).update(expires_utc=None, revocation_max_age_seconds=None)
    assert refusal(rebind(f0)) == "f0_policy_unbounded"
    # Transitive requires, two levels deep: F15 -> F16 -> F17, and F17 is revoked.
    f0 = f0_inputs()
    for name, needs in (("F16", ["F17"]), ("F17", [])):
        policy_of(f0)["features"][name].update(enabled=True, stage=1, requires=needs)
    policy_of(f0)["features"]["F15"]["requires"] = ["F16"]
    f0["revocation"]["revoked"] = ["F17"]
    assert refusal(rebind(f0)) == "f0_dependency_blocked"


@pytest.mark.parametrize("now", [None, "2026-09-29T21:00:00Z", datetime(2026, 9, 29, 21, 0)])
def test_time_must_be_an_aware_datetime(now):
    assert refusal(f0_inputs(), now) == "f0_time_unknown"


def test_f0_refusal_wins_before_anything_else_is_read():
    f0 = {"decision": {"feature": "F15", "enabled": False, "reason": "local kill switch denies",
                       "policy_sha256": None, "revocation_version": None},
          "pins": None, "document": None, "revocation": None}
    assert refusal(f0) == "f0_decision_disabled"


def test_input_is_not_mutated():
    f0 = f0_inputs()
    before = json.dumps(f0, sort_keys=True)
    switch_activation(f0, NOW)
    assert json.dumps(f0, sort_keys=True) == before


# --- Integration fixtures: the real F0 evaluator on files, then the adapter on the same inputs.

def write_f0(tmp_path: Path, f0: dict) -> dict:
    config = tmp_path / "bridge_v2_activation.json"
    config.write_text(json.dumps(f0["document"]), encoding="utf-8")
    state = tmp_path / "runtime" / activation.REVOCATION_RELATIVE
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps(f0["revocation"]), encoding="utf-8")
    pins = f0["pins"]
    return {"config_path": config, "runtime_root": tmp_path / "runtime",
            "trusted_policy_sha256": pins["trusted_policy_sha256"], "now": NOW, "environ": {},
            "min_revocation_version": pins["min_revocation_version"], "expected_head": pins["expected_head"],
            "expected_tree": pins["expected_tree"]}


def test_integration_real_f0_decision_is_accepted(tmp_path):
    f0 = f0_inputs()
    decision = activation.evaluate("F15", **write_f0(tmp_path, f0))
    assert decision.enabled is True, decision.reason
    f0["decision"] = asdict(decision)
    assert switch_activation(f0, NOW)["parameters"] == PARAMETERS


@pytest.mark.parametrize("change, code", [
    (lambda f: f["revocation"].update(frozen=True), "f0_frozen"),
    (lambda f: f["revocation"].update(revoked=["F15"]), "f0_revoked"),
    (lambda f: f["revocation"].update(updated_utc=ago(hours=2)), "f0_revocation_stale"),
    (lambda f: f["pins"].update(min_revocation_version=4), "f0_revocation_rollback"),
    (lambda f: f["pins"].update(expected_head="3" * 40), "f0_signature_invalid"),
    (lambda f: f["pins"].update(expected_tree="4" * 40), "f0_signature_invalid"),
    (lambda f: f["pins"].update(trusted_policy_sha256="d" * 64), "f0_policy_unbound"),
    (lambda f: f["document"]["signature"].update(expires_utc=ago(minutes=1), signed_utc=ago(hours=2)), "f0_expired"),
])
def test_integration_both_refuse_the_same_inputs(tmp_path, change, code):
    f0 = f0_inputs()
    change(f0)
    decision = activation.evaluate("F15", **write_f0(tmp_path, f0))
    assert decision.enabled is False
    f0["decision"] = asdict(decision)
    assert refusal(f0) == "f0_decision_disabled"  # F0's refusal is carried, never overridden
    # A forged enable BOUND to the real policy digest and revocation version. F0's disabled Decision
    # has a None digest or version, so an unbound forgery would stop at f0_decision_unbound instead.
    f0["decision"] = {"feature": "F15", "enabled": True, "reason": "forged",
                      "policy_sha256": activation.canonical_sha256(policy_of(f0)),
                      "revocation_version": f0["revocation"]["version"]}
    assert refusal(f0) == code


def test_integration_kill_switch_is_carried_by_the_decision(tmp_path):
    f0 = f0_inputs()
    kwargs = write_f0(tmp_path, f0)
    kwargs["environ"] = {activation.KILL_SWITCH_ENV: "0"}
    decision = activation.evaluate("F15", **kwargs)
    assert decision.enabled is False
    f0["decision"] = asdict(decision)
    assert refusal(f0) == "f0_decision_disabled"


def test_shipped_policy_is_default_off():
    config = json.loads((ROOT / "configs" / "bridge_v2_activation.json").read_text(encoding="utf-8"))
    assert config["signature"] is None and config["policy"]["features"]["F15"]["enabled"] is False


FORBIDDEN_IMPORTS = {"os", "sys", "pathlib", "subprocess", "socket", "time", "urllib", "sqlite3", "shutil", "io"}
FORBIDDEN_NAMES = {"open", "print", "input", "evaluate", "feature_enabled", "load_policy", "load_revocation",
                   "kill_switch_denies", "_read_json", "now", "utcnow", "today", "environ", "getenv"}


def test_adapter_is_pure_by_construction():
    """No I/O, clock, environment or F0 file-reading entry point in the adapter's own source."""
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
