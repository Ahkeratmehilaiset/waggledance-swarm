from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from waggledance.core.magma.share_manifest import validate_magma_share_manifest


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas" / "v3_13_0"
SCHEMA_NAME = "magma_share_manifest.v0.json"
FORBIDDEN_MATERIAL = [
    "raw_payload",
    "replacement_map",
    "raw_context",
    "raw_solver_output",
    "raw_query_digest",
]


def _schema() -> dict:
    return json.loads((SCHEMA_DIR / SCHEMA_NAME).read_text(encoding="utf-8"))


def _validator() -> jsonschema.Draft7Validator:
    schema = _schema()
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )


def _digest(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def good_magma_share_manifest() -> dict:
    return {
        "manifest_version": "magma.share_manifest.v0",
        "share_id": "magma:share:fixture:001",
        "created_at_utc": "2026-05-28T06:40:00Z",
        "producer": {
            "agent_id": "codex-lead-1",
            "role": "lead",
            "bridge_event_ref": "bridge:wd-image1-share-contract",
        },
        "purpose": "cross_instance_replay",
        "runtime_export_enabled": False,
        "sanitized_source_manifest_digest": _digest("a"),
        "export_policy": {
            "contract": "sanitization_v0",
            "payload_visibility": "no_payload",
            "allow_payload_digests": False,
            "allow_raw_payloads": False,
            "allow_replacement_maps": False,
            "allow_raw_context": False,
            "allow_raw_solver_outputs": False,
            "allow_deterministic_query_digests": False,
        },
        "artifact_counts": {
            "entries": 1,
            "receipts": 1,
            "evaluation_results": 1,
            "payload_files": 0,
        },
        "forbidden_material_absent": list(FORBIDDEN_MATERIAL),
        "entries": [
            {
                "entry_id": "magma:share:entry:001",
                "receipt_digest": _digest("b"),
                "evaluation_result_digest": _digest("c"),
                "subject_type": "counterfactual",
                "risk_class": "internal_memory",
                "expected_gate": "review",
                "actual_gate": "review",
                "verdict": "pass",
                "sanitization": {
                    "contract": "sanitization_v0",
                    "redaction_count": 3,
                    "raw_material_removed": list(FORBIDDEN_MATERIAL),
                    "payload_digest_exported": False,
                    "replacement_map_exported": False,
                },
            }
        ],
    }


def test_magma_share_manifest_schema_is_valid_draft7() -> None:
    _validator()


def test_good_magma_share_manifest_validates() -> None:
    _validator().validate(good_magma_share_manifest())


def test_share_manifest_is_contract_only_until_runtime_export_pr() -> None:
    manifest = good_magma_share_manifest()
    manifest["runtime_export_enabled"] = True

    assert list(_validator().iter_errors(manifest))


def test_share_manifest_rejects_invalid_created_at_format() -> None:
    manifest = good_magma_share_manifest()
    manifest["created_at_utc"] = "not-a-date"

    with pytest.raises(ValueError, match="created_at_utc"):
        validate_magma_share_manifest(manifest)


def test_share_manifest_rejects_payload_exports_and_payload_digests() -> None:
    validator = _validator()
    raw_payload = good_magma_share_manifest()
    raw_payload["raw_payload"] = {"private": "not allowed"}
    payload_digest = good_magma_share_manifest()
    payload_digest["entries"][0]["canonical_payload_digest"] = _digest("d")
    payload_file = good_magma_share_manifest()
    payload_file["artifact_counts"]["payload_files"] = 1

    assert list(validator.iter_errors(raw_payload))
    assert list(validator.iter_errors(payload_digest))
    assert list(validator.iter_errors(payload_file))


def test_share_manifest_rejects_raw_context_solver_output_and_query_digest() -> None:
    validator = _validator()
    raw_context = good_magma_share_manifest()
    raw_context["entries"][0]["raw_context"] = {"query": "private prompt"}
    raw_solver_output = good_magma_share_manifest()
    raw_solver_output["entries"][0]["raw_solver_output"] = "private answer"
    raw_query_digest = good_magma_share_manifest()
    raw_query_digest["entries"][0]["raw_query_digest"] = _digest("e")

    assert list(validator.iter_errors(raw_context))
    assert list(validator.iter_errors(raw_solver_output))
    assert list(validator.iter_errors(raw_query_digest))


def test_share_manifest_rejects_replacement_maps_and_policy_relaxation() -> None:
    validator = _validator()
    replacement_map = good_magma_share_manifest()
    replacement_map["entries"][0]["sanitization"]["replacement_map"] = {
        "alice": "<person-1>",
    }
    relaxed_policy = good_magma_share_manifest()
    relaxed_policy["export_policy"]["allow_replacement_maps"] = True
    relaxed_payload_digest = good_magma_share_manifest()
    relaxed_payload_digest["export_policy"]["allow_payload_digests"] = True

    assert list(validator.iter_errors(replacement_map))
    assert list(validator.iter_errors(relaxed_policy))
    assert list(validator.iter_errors(relaxed_payload_digest))


def test_share_manifest_requires_complete_forbidden_material_inventory() -> None:
    validator = _validator()
    manifest = good_magma_share_manifest()
    manifest["forbidden_material_absent"].remove("raw_query_digest")
    entry = good_magma_share_manifest()
    entry["entries"][0]["sanitization"]["raw_material_removed"].remove(
        "raw_payload"
    )

    assert list(validator.iter_errors(manifest))
    assert list(validator.iter_errors(entry))


def test_share_manifest_errors_do_not_need_to_echo_raw_values() -> None:
    manifest = good_magma_share_manifest()
    manifest["entries"][0]["raw_context"] = {
        "query": "PRIVATE_QUERY_MARKER secret-token"
    }

    paths = [
        ".".join(str(part) for part in error.path) or "<root>"
        for error in _validator().iter_errors(manifest)
    ]

    assert paths
    assert "PRIVATE_QUERY_MARKER" not in str(paths)
    assert "secret-token" not in str(paths)


def test_good_fixture_contains_no_forbidden_raw_keys() -> None:
    serialized = json.dumps(good_magma_share_manifest(), sort_keys=True)

    for token in (
        "raw_payload",
        "replacement_map",
        "raw_context",
        "raw_solver_output",
        "raw_query_digest",
        "canonical_payload_digest",
    ):
        if token.startswith("raw_") or token == "replacement_map":
            assert f'"{token}":' not in serialized
        else:
            assert token not in serialized
