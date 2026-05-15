from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas" / "v3_13_0"
GENERATOR_PATH = ROOT / "tools" / "build_v3_13_domain_catalog.py"


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _validator(name: str) -> jsonschema.Draft7Validator:
    schema = _schema(name)
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def _load_generator():
    spec = importlib.util.spec_from_file_location("build_v3_13_domain_catalog", GENERATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def good_tool_descriptor() -> dict:
    return {
        "schema_version": 1,
        "tool_id": "factory_logbook_sync",
        "profile": "factory_anchor",
        "domain": "DOM-011",
        "phase": "sync",
        "read_scopes": ["external:factory-logbook/read-window"],
        "write_scopes": ["external:factory-logbook/logbook_entries"],
        "credential_refs": ["vault://os_keyring/factory_anchor/auth/logbook_session"],
        "connector_refs": ["factory_logbook_connector"],
        "state_refs": ["factory_logbook_state"],
        "checkpoint_refs": ["factory_logbook_recovery"],
        "rate_limits": {"max_workers": 3, "request_delay_s": 0.15},
        "idempotency_key": "factory_logbook_sync:message_id",
        "dry_run_supported": True,
        "shadow_supported": True,
        "rollback_artifact": "factory_logbook_recovery",
        "owner_agent": "codex",
        "promotion_gate_refs": ["anch-04", "anch-05", "anch-06"],
        "write_risk_class": "external_effect",
        "capture_supported": True,
        "capture_policy_ref": "factory_capture_policy",
    }


def good_state_handle() -> dict:
    return {
        "schema_version": 1,
        "state_id": "factory_logbook_state",
        "kind": "external_api",
        "owner_tool": "factory_logbook_sync",
        "plane": "external_system",
        "readers": ["factory_logbook_sync"],
        "writers": ["factory_logbook_sync"],
        "single_writer_required": True,
        "wal_required": False,
        "freshness_query": "last_seen_message_id",
        "integrity_query": "dedupe_key_gap_check",
        "backup_strategy": "provider_native",
        "recovery_strategy": "replay_overlap",
        "sensitive_class": "restricted",
        "read_only_uri": "external:factory-logbook/logbook_entries",
        "projection_of": None,
        "high_watermark_ref": "factory_logbook_high_watermark",
        "source_class": "rest_api",
        "domain_refs": ["DOM-011"],
        "write_modes_allowed": ["post"],
    }


def good_authenticated_connector() -> dict:
    return {
        "schema_version": 1,
        "connector_id": "factory_logbook_connector",
        "profile": "factory_anchor",
        "domain": "DOM-011",
        "auth_mode": "session_cookie",
        "credential_ref": "vault://os_keyring/factory_anchor/auth/logbook_session",
        "mfa_policy_ref": "factory_logbook_mfa",
        "secret_material_never_logged": True,
        "can_run_headless": False,
        "requires_operator_presence": True,
        "session_renewal_strategy": "operator_reauth",
        "revocation_procedure": "Revoke the session in the provider UI and rotate the vault ref.",
        "write_risk": "external_effect",
        "rate_limit": {"max_workers": 3, "request_delay_s": 0.15},
        "state_refs": ["factory_logbook_state"],
        "supported_scopes": ["read", "write", "shadow", "dry_run"],
    }


def good_mfa_policy() -> dict:
    return {
        "schema_version": 1,
        "policy_id": "factory_logbook_mfa",
        "mode": "browser_profile_checkpoint",
        "requires_operator_presence": True,
        "headless_allowed": False,
        "max_wait_seconds": 600,
        "checkpoint_state_ref": "factory_browser_profile",
        "failure_strategy": "fail_closed",
    }


def good_recovery_capsule() -> dict:
    return {
        "schema_version": 1,
        "capsule_id": "factory_logbook_recovery",
        "owner_tool": "factory_logbook_sync",
        "last_success_marker": "last accepted logbook entry id",
        "high_watermark": "factory_logbook_high_watermark",
        "overlap_window": {"unit": "days", "value": 7},
        "source_window": "begin_date overlap",
        "dedupe_key": "message_id",
        "gap_detector": "detect missing message id ranges",
        "quarantine_target": "factory_logbook_quarantine",
        "rebuild_command": "replay_overlap_window",
        "rollback_command": "delete_synthetic_entry_by_idempotency_key",
        "human_review_artifacts": ["factory_logbook_diff_preview"],
        "known_corruption_modes": ["duplicate entry", "missing overlap window"],
        "operator_review_required": True,
    }


def good_provider_registry() -> dict:
    return {
        "schema_version": 1,
        "provider_id": "generic_logbook_provider",
        "display_name": "Generic logbook provider",
        "status": "seed",
        "domains": ["DOM-011"],
        "source_classes": ["rest_api"],
        "auth_modes_supported": ["session_cookie", "interactive_mfa"],
        "connector_templates": ["factory_logbook_connector"],
        "contribution_contract": {
            "requires_docs_source": True,
            "requires_shadow_run": True,
            "requires_operator_scope_policy": True,
            "minimum_soak_hours": 24,
        },
        "rate_limit_defaults": {"max_workers": 3, "request_delay_s": 0.15},
        "data_residency": "operator_defined",
        "risk_notes": ["External writes require WriteRCOGate and rollback capsule."],
    }


def good_profile_config() -> dict:
    return {
        "schema_version": 1,
        "profile_id": "factory_anchor",
        "profile_kind": "factory",
        "country": "FI",
        "region": None,
        "timezone": "Europe/Helsinki",
        "language": "fi-FI",
        "currency": "EUR",
        "climate_zone": None,
        "regulatory_frameworks": ["GDPR"],
        "service_provider_refs": ["generic_logbook_provider"],
        "data_residency_policy": "factory_anchor_residency",
        "locale_formats": {"date": "ISO-8601"},
        "external_write_policy_ref": "factory_anchor_write_policy",
        "redaction_policy_ref": "factory_anchor_redaction",
        "default_risk_policy": "external_effect_requires_rco",
        "operator_review_required": True,
        "credential_vault_impl": "os_keyring",
        "retrieval_overrides": {"context_sim_threshold": 0.58, "context_top_n": 8},
        "embedding_overrides": {"model_id": "intfloat/multilingual-e5-small", "dims": 384},
        "shadow_overrides": {
            "divergence_threshold": 0.4,
            "per_family_thresholds": {"record_reconciler": 0.25},
            "automatic_revocation_divergence_threshold": 0.4,
            "automatic_revocation_consecutive_runs": 5,
        },
        "locale_extensions": {"fi-FI": ["uutiskirje", "automaattinen vastaus"]},
    }


def good_solver_candidate_manifest() -> dict:
    return {
        "schema_version": 1,
        "candidate_id": "electricity_spot_optimizer_home_demo_001",
        "source_docs": ["doc:tariff_structure_pdf", "doc:consumption_sample_csv"],
        "source_tools": [],
        "training_contracts": ["ctr_date", "ctr_search", "ctr_vector"],
        "state_handles": [
            "state:spot_price_store",
            "state:consumption_forecast",
            "state:optimizer_recommendations",
        ],
        "connector_handles": ["conn:spot_price_public_feed"],
        "shadow_inputs": ["synth_24h_winter", "synth_24h_summer"],
        "shadow_expected_outputs": [
            "recommendation_with_savings_estimate_winter",
            "recommendation_with_savings_estimate_summer",
        ],
        "divergence_score": None,
        "accepted_differences": [],
        "rejected_differences": [],
        "promotion_decision": "awaiting_shadow",
        "rollback_plan": "recovery:spot_optimizer_v1",
        "operator_review_id": "op_review_001",
        "provenance_signatures": [
            {
                "signature_id": "sig:owner_001",
                "solver_candidate_id": "electricity_spot_optimizer_home_demo_001",
                "solver_manifest_canonical_json": "{\"schema_version\":1}",
                "manifest_sha256": "a" * 64,
                "signing_agent_id": "claude",
                "signing_role": "owner",
                "signing_timestamp_utc": "2026-05-13T08:30:00Z",
                "bridge_event_ref": "bridge:evt_owner_001",
                "audit_event_ref": "audit:evt_owner_001",
                "operator_scope_policy_ref": "policy:home_no_external_writes",
            },
            {
                "signature_id": "sig:peer_001",
                "solver_candidate_id": "electricity_spot_optimizer_home_demo_001",
                "solver_manifest_canonical_json": "{\"schema_version\":1}",
                "manifest_sha256": "a" * 64,
                "signing_agent_id": "codex",
                "signing_role": "peer",
                "signing_timestamp_utc": "2026-05-13T08:31:00Z",
                "bridge_event_ref": "bridge:evt_peer_001",
                "audit_event_ref": "audit:evt_peer_001",
                "operator_scope_policy_ref": "policy:home_no_external_writes",
            },
        ],
        "activation_state": "signed",
    }


@pytest.mark.parametrize(
    "name",
    [
        "tool_descriptor.schema.json",
        "state_handle.schema.json",
        "authenticated_connector.schema.json",
        "mfa_policy.schema.json",
        "recovery_capsule.schema.json",
        "provider_registry.schema.json",
        "profile_config.schema.json",
        "solver_candidate_manifest.schema.json",
        "domain_catalog.schema.json",
    ],
)
def test_schema_is_valid_draft7(name: str) -> None:
    jsonschema.Draft7Validator.check_schema(_schema(name))


@pytest.mark.parametrize(
    ("schema_name", "factory"),
    [
        ("tool_descriptor.schema.json", good_tool_descriptor),
        ("state_handle.schema.json", good_state_handle),
        ("authenticated_connector.schema.json", good_authenticated_connector),
        ("mfa_policy.schema.json", good_mfa_policy),
        ("recovery_capsule.schema.json", good_recovery_capsule),
        ("provider_registry.schema.json", good_provider_registry),
        ("profile_config.schema.json", good_profile_config),
        ("solver_candidate_manifest.schema.json", good_solver_candidate_manifest),
    ],
)
def test_good_examples_validate(schema_name: str, factory) -> None:
    _validator(schema_name).validate(factory())


def test_tool_descriptor_rejects_inline_secret_material() -> None:
    bad = good_tool_descriptor()
    bad["credential_refs"] = ["sk-not-a-vault-ref"]
    assert list(_validator("tool_descriptor.schema.json").iter_errors(bad))


def test_authenticated_connector_requires_secret_free_flag() -> None:
    bad = good_authenticated_connector()
    bad["secret_material_never_logged"] = False
    assert list(_validator("authenticated_connector.schema.json").iter_errors(bad))


def test_external_readonly_state_cannot_declare_writers() -> None:
    bad = copy.deepcopy(good_state_handle())
    bad["plane"] = "external_readonly"
    bad["writers"] = ["factory_logbook_sync"]
    bad["write_modes_allowed"] = ["post"]
    assert list(_validator("state_handle.schema.json").iter_errors(bad))


def test_informational_artifact_state_handle_validates() -> None:
    advisory = copy.deepcopy(good_state_handle())
    advisory.update({
        "state_id": "eng01_advisory_output",
        "kind": "json",
        "plane": "informational_artifact",
        "owner_tool": "eng01_spot_electricity_monitor",
        "readers": ["eng01_spot_electricity_monitor"],
        "writers": ["eng01_spot_electricity_monitor"],
        "single_writer_required": False,
        "wal_required": False,
        "freshness_query": None,
        "integrity_query": "result_marker_present",
        "backup_strategy": "export_bundle",
        "recovery_strategy": "operator_review",
        "read_only_uri": "artifact:eng01/advisory_output",
        "projection_of": "eng01_spot_price_feed",
        "high_watermark_ref": "eng01_price_feed_hour_utc",
        "source_class": "tests",
        "domain_refs": ["DOM-007"],
        "write_modes_allowed": ["insert", "append"],
    })

    _validator("state_handle.schema.json").validate(advisory)


def test_domain_catalog_projection_is_generated_from_descriptors_and_state_handles() -> None:
    generator = _load_generator()
    catalog = generator.build_domain_catalog(
        [good_tool_descriptor()],
        [good_state_handle()],
        generated_at_utc="2026-05-13T06:00:00Z",
    )
    _validator("domain_catalog.schema.json").validate(catalog)

    assert catalog["source_counts"] == {"tool_descriptors": 1, "state_handles": 1}
    assert catalog["domains"] == [
        {
            "domain_id": "DOM-011",
            "domain": "Factory logbook, MES, shifts, equipment reconciliation",
            "sources": ["external", "rest_api"],
            "primary_risk": "external_effect",
            "sensitive": True,
            "owner_agent": "codex",
            "tool_descriptor_ids": ["factory_logbook_sync"],
            "state_handle_ids": ["factory_logbook_state"],
        }
    ]


def test_solver_candidate_manifest_requires_explicit_signing_role() -> None:
    bad = good_solver_candidate_manifest()
    del bad["provenance_signatures"][0]["signing_role"]
    assert list(
        _validator("solver_candidate_manifest.schema.json").iter_errors(bad)
    )


def test_solver_candidate_manifest_rejects_unknown_activation_state() -> None:
    bad = good_solver_candidate_manifest()
    bad["activation_state"] = "autonomous_without_signature"
    assert list(
        _validator("solver_candidate_manifest.schema.json").iter_errors(bad)
    )


def test_schema_bundle_has_no_absolute_local_paths_or_known_secret_prefixes() -> None:
    for path in SCHEMA_DIR.glob("*.schema.json"):
        raw = path.read_text(encoding="utf-8")
        assert "C:\\" not in raw
        assert "U:\\" not in raw
        assert "ghp_" not in raw
        assert "sk-" not in raw
