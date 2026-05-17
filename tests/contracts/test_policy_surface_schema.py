from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema

from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas" / "v3_13_0"
FIXTURE = ROOT / "tests" / "fixtures" / "policy_surface_v0.json"


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _validator(name: str) -> jsonschema.Draft7Validator:
    schema = _schema(name)
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _digest(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def _receipt() -> dict:
    return {
        "receipt_version": "magma.receipt.v1",
        "event_id": "magma:receipt:policy-surface-v0",
        "ts_utc": "2026-05-17T08:30:00Z",
        "risk_class": "external_effect",
        "payload_visibility": "digest_only",
        "canonical_payload_digest": _digest("a"),
        "prev_receipt_hash": None,
        "policy_digest": _digest("b"),
        "charter_digest": _digest("c"),
        "rco_decision_digest": _digest("d"),
        "world_snapshot_digest": _digest("e"),
        "solver_contract_digest": _digest("f"),
        "evaluation_result_digest": _digest("1"),
        "approval_id": "bridge:approval:policy-surface-v0",
        "operator_gate_required": True,
        "signature_algorithm": None,
        "signature": None,
        "key_id": None,
        "anchored_at": None,
    }


def test_policy_surface_schema_is_valid_draft7() -> None:
    _validator("policy_surface.v0.json")


def test_policy_surface_fixture_validates() -> None:
    fixture = _fixture()
    _validator("policy_surface.v0.json").validate(fixture)
    assert fixture["digest_bindings"]["canonicalization"] == "magma-jcs-subset-v1"


def test_policy_surface_digest_can_bind_to_magma_receipt() -> None:
    policy = _fixture()
    receipt = _receipt()
    receipt["policy_digest"] = sha256_digest(policy)
    receipt["charter_digest"] = sha256_digest(policy["charter_sections"])

    _validator("magma_receipt.v1.json").validate(receipt)
    assert receipt["policy_digest"].startswith("sha256:")
    assert receipt["charter_digest"].startswith("sha256:")


def test_policy_surface_cannot_claim_runtime_authority() -> None:
    policy = _fixture()
    policy["authority"] = "authoritative_runtime_v1"
    policy["authority_mode"] = "runtime_enforcer"

    assert list(_validator("policy_surface.v0.json").iter_errors(policy))


def test_policy_surface_rejects_missing_required_and_unknown_top_level_fields() -> None:
    policy = _fixture()
    missing = copy.deepcopy(policy)
    del missing["policy_id"]
    unknown = copy.deepcopy(policy)
    unknown["runtime_override"] = True

    validator = _validator("policy_surface.v0.json")
    assert list(validator.iter_errors(missing))
    assert list(validator.iter_errors(unknown))


def test_policy_surface_preserves_kernel_and_operator_gate_constants() -> None:
    policy = _fixture()
    policy["enforcement_boundary"]["kernel_authoritative"] = False
    policy["enforcement_boundary"]["no_auto_execute_grant"] = False

    assert list(_validator("policy_surface.v0.json").iter_errors(policy))


def test_external_effect_policy_requires_operator_approval_gate() -> None:
    policy = _fixture()
    external_effect = next(
        item for item in policy["risk_classes"] if item["risk_class"] == "external_effect"
    )
    external_effect["operator_required"] = False
    external_effect["default_gate"] = "allow"

    assert list(_validator("policy_surface.v0.json").iter_errors(policy))


def test_external_effect_rules_cannot_allow_or_disable_operator_gate() -> None:
    policy = _fixture()
    rule = policy["rule_sets"][0]["rules"][0]
    rule["effect"] = "allow"
    rule["constraint"]["operator_required"] = False
    rule["constraint"]["receipt_required"] = False

    assert list(_validator("policy_surface.v0.json").iter_errors(policy))


def test_policy_surface_requires_all_four_risk_classes() -> None:
    policy = _fixture()
    policy["risk_classes"] = [
        item
        for item in policy["risk_classes"]
        if item["risk_class"] != "local_artifact"
    ]

    assert list(_validator("policy_surface.v0.json").iter_errors(policy))


def test_policy_surface_rejects_unknown_risk_class_and_export_target() -> None:
    policy = _fixture()
    policy["risk_classes"][0]["risk_class"] = "operator_free_external_write"
    policy["planned_export_targets"].append("opaque_runtime_plugin")

    assert list(_validator("policy_surface.v0.json").iter_errors(policy))


def test_policy_surface_rejects_runtime_action_fields_in_rules() -> None:
    policy = _fixture()
    policy["rule_sets"][0]["rules"][0]["constraint"]["auto_execute"] = True

    assert list(_validator("policy_surface.v0.json").iter_errors(policy))


def test_policy_surface_rejects_secret_url_payload_literals_in_rules() -> None:
    validator = _validator("policy_surface.v0.json")

    endpoint_key = _fixture()
    endpoint_key["rule_sets"][1]["rules"][0]["constraint"]["endpoint"] = "offline"
    assert list(validator.iter_errors(endpoint_key))

    secret_key = _fixture()
    secret_key["rule_sets"][1]["rules"][0]["constraint"]["secret"] = "redacted"
    assert list(validator.iter_errors(secret_key))

    payload_key = _fixture()
    payload_key["rule_sets"][1]["rules"][0]["constraint"]["payload"] = "redacted"
    assert list(validator.iter_errors(payload_key))

    url_value = _fixture()
    url_value["rule_sets"][1]["rules"][0]["constraint"]["note"] = "https://example.invalid"
    assert list(validator.iter_errors(url_value))

    secret_value = _fixture()
    secret_value["rule_sets"][1]["rules"][0]["constraint"]["note"] = "sk-test"
    assert list(validator.iter_errors(secret_value))


def test_policy_surface_rule_ids_are_unique_in_fixture() -> None:
    policy = _fixture()
    rule_ids = [
        rule["rule_id"]
        for rule_set in policy["rule_sets"]
        for rule in rule_set["rules"]
    ]

    assert len(rule_ids) == len(set(rule_ids))


def test_policy_surface_fixture_contains_no_local_paths_or_secret_prefixes() -> None:
    raw = FIXTURE.read_text(encoding="utf-8")
    assert "C:\\" not in raw
    assert "U:\\" not in raw
    assert "http://" not in raw
    assert "https://" not in raw
    assert "ghp_" not in raw
    assert "sk-" not in raw
