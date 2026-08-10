# SPDX-License-Identifier: BUSL-1.1
"""Strict MAGMA contracts for candidate-only FAISS materialization.

FAISS is a disposable projection, never the authority.  This module defines
the small allowlisted document that may cross from a validated solver contract
into the vector event stream, together with the embedding and retrieval
topology bindings required to rebuild it deterministically.

No function in this module writes files, imports FAISS, embeds text, mutates a
runtime topology, or grants routing authority. Receipt-bound v2 validation
reuses the existing MAGMA JSON-schema validators.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from waggledance.core.magma.canonical import sha256_digest


SOLVER_PROJECTION_VERSION = "magma.faiss.solver_projection.v1"
PROJECTION_SOURCE_IDENTITY_V1_VERSION = "magma.faiss.source_identity.v1"
PROJECTION_SOURCE_IDENTITY_V2_VERSION = "magma.faiss.source_identity.v2"
# Backward-compatible name used by existing unreceipted projection manifests.
PROJECTION_SOURCE_IDENTITY_VERSION = PROJECTION_SOURCE_IDENTITY_V1_VERSION
PROJECTION_SOURCE_IDENTITY_VERSIONS = frozenset(
    {PROJECTION_SOURCE_IDENTITY_V1_VERSION, PROJECTION_SOURCE_IDENTITY_V2_VERSION}
)
PROJECTION_RECEIPT_PROOF_VERSION = "magma.faiss.projection_receipt_proof.v2"
PROJECTION_ADMISSION_PAYLOAD_VERSION = (
    "magma.faiss.projection_admission_payload.v2"
)
PROJECTION_ADMISSION_EVALUATOR_VERSION = (
    "magma.faiss.projection_admission_evaluator.v1"
)
PROJECTION_ADMISSION_EVALUATOR_SPEC = {
    "schema_version": PROJECTION_ADMISSION_EVALUATOR_VERSION,
    "deterministic": True,
    "network_access": False,
    "checks": [
        "solver_projection_contract",
        "embedding_contract",
        "projection_receipt_payload",
        "evaluation_result_binding",
        "magma_receipt_binding",
        "self_certified_receipt_scope",
        "candidate_only_authority",
    ],
}
PROJECTION_ADMISSION_EVALUATOR_DIGEST = sha256_digest(
    PROJECTION_ADMISSION_EVALUATOR_SPEC
)
PROJECTION_RECEIPT_CONTEXT_VERSION = (
    "magma.faiss.self_certified_receipt_context.v1"
)
EMBEDDING_CONTRACT_VERSION = "magma.faiss.embedding_contract.v1"
RETRIEVAL_TOPOLOGY_VERSION = "waggledance.retrieval_topology.v1"

_SAFE_CELL = re.compile(
    r"^[a-z][a-z0-9_-]{0,63}(?:\.[a-z][a-z0-9_-]{0,63})*$"
)
_SAFE_SOLVER = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_LEAK_MARKERS = (
    "private_marker",
    "_do_not_leak",
    "authorization: bearer",
    "api_key=",
    "apikey=",
    "password=",
    "secret=",
    "raw_query_payload",
    "raw_response_payload",
)

_PROJECTION_KEYS = frozenset(
    {
        "schema_version",
        "projection_id",
        "projection_digest",
        "document_key",
        "canonical_solver_id",
        "cell_id",
        "source_kind",
        "source_digest",
        "solver_contract_digest",
        "topology_digest",
        "contract_fields",
        "embedding_text",
        "payload_visibility",
        "quality_gate",
    }
)
_CONTRACT_FIELD_KEYS = frozenset(
    {
        "model_id",
        "model_name",
        "description",
        "variables",
        "outputs",
        "formulas",
        "capabilities",
        "tags",
    }
)
_NAMED_UNIT_KEYS = frozenset({"name", "unit"})
_SOURCE_IDENTITY_V1_KEYS = frozenset(
    {
        "schema_version",
        "canonical_solver_id",
        "solver_contract_digest",
        "source_digest",
        "receipt_event_id",
        "receipt_digest",
        "receipt_bound",
        "identity_digest",
    }
)
_SOURCE_IDENTITY_V2_KEYS = frozenset(
    {
        "schema_version",
        "projection_id",
        "projection_digest",
        "canonical_solver_id",
        "cell_id",
        "solver_contract_digest",
        "source_digest",
        "topology_digest",
        "embedding_contract_digest",
        "receipt_event_id",
        "receipt_digest",
        "receipt_proof_digest",
        "receipt_bound",
        "receipt_authenticity_verified",
        "external_authority_artifacts_verified",
        "solver_outcome_verified",
        "runtime_authority_granted",
        "receipt_proof",
        "identity_digest",
    }
)
_PROJECTION_RECEIPT_PROOF_KEYS = frozenset(
    {
        "schema_version",
        "payload",
        "receipt_context",
        "evaluation_result",
        "receipt",
        "proof_digest",
    }
)
_PROJECTION_RECEIPT_CONTEXT_KEYS = frozenset(
    {
        "schema_version",
        "policy_artifact",
        "charter_artifact",
        "rco_decision_artifact",
        "world_snapshot_artifact",
    }
)
_LOCAL_POLICY_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "scope",
        "receipt_semantics",
        "admission_evaluator_contract_digest",
        "runtime_authority_granted",
    }
)
_LOCAL_CHARTER_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "authority",
        "external_governance_verified",
        "solver_outcome_verified",
    }
)
_LOCAL_RCO_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "review_performed",
        "decision",
        "authority_granted",
    }
)
_LOCAL_WORLD_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "observation_scope",
        "external_state_observed",
        "network_access",
    }
)
_PROJECTION_ADMISSION_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "operation",
        "projection_id",
        "projection_digest",
        "canonical_solver_id",
        "cell_id",
        "solver_contract_digest",
        "source_digest",
        "topology_digest",
        "embedding_contract_digest",
        "admission_evaluator_contract_digest",
        "authority_scope",
        "receipt_authenticity_verified",
        "runtime_authority_granted",
        "external_authority_artifacts_verified",
        "solver_outcome_verified",
    }
)
_EMBEDDING_KEYS = frozenset(
    {
        "schema_version",
        "model_id",
        "model_version",
        "normalization",
        "dimension",
        "document_prefix",
        "query_prefix",
        "contract_digest",
    }
)
_TOPOLOGY_KEYS = frozenset({"schema_version", "cells"})
_TOPOLOGY_CELL_KEYS = frozenset(
    {
        "cell_id",
        "parent_cell_id",
        "child_cell_ids",
        "neighbor_cell_ids",
        "live",
        "subdivision_state",
    }
)


def _require_plain_mapping(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be a plain object")
    return value


def _require_exact_keys(value: dict[str, Any], keys: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        raise ValueError(f"{label} keys mismatch: missing={missing}, extra={extra}")


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a full sha256 digest")
    return value


def _normalize_text(value: Any, label: str, *, maximum: int, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = " ".join(unicodedata.normalize("NFC", value).split())
    if not normalized and not allow_empty:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    lowered = normalized.casefold()
    if any(marker in lowered for marker in _LEAK_MARKERS):
        raise ValueError(f"{label} contains a prohibited payload marker")
    return normalized


def _bounded_prefix(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if len(normalized) > 128:
        raise ValueError(f"{label} exceeds 128 characters")
    lowered = normalized.casefold()
    if any(marker in lowered for marker in _LEAK_MARKERS):
        raise ValueError(f"{label} contains a prohibited payload marker")
    return normalized


def validate_vector_cell_id(value: Any) -> str:
    """Validate a root or dotted child cell without permitting a path."""
    if not isinstance(value, str) or not _SAFE_CELL.fullmatch(value):
        raise ValueError("cell_id must be a filename-safe retrieval cell identifier")
    if ".." in value:
        raise ValueError("cell_id must not contain empty hierarchy segments")
    return value


def validate_solver_id(value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_SOLVER.fullmatch(value):
        raise ValueError("solver_id must be a canonical filename-safe identifier")
    return value


def _named_units(value: Any, label: str, *, maximum: int) -> list[dict[str, str]]:
    if type(value) is not list:
        raise ValueError(f"{label} must be a list")
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds {maximum} entries")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        row = _require_plain_mapping(item, f"{label}[{index}]")
        _require_exact_keys(row, _NAMED_UNIT_KEYS, f"{label}[{index}]")
        name = _normalize_text(
            row["name"], f"{label}[{index}].name", maximum=128, allow_empty=False
        )
        unit = _normalize_text(
            row["unit"], f"{label}[{index}].unit", maximum=64, allow_empty=True
        )
        if name in seen:
            raise ValueError(f"{label} contains duplicate name {name!r}")
        seen.add(name)
        result.append({"name": name, "unit": unit})
    return sorted(result, key=lambda row: (row["name"], row["unit"]))


def _tokens(value: Any, label: str, *, maximum: int) -> list[str]:
    if type(value) is not list:
        raise ValueError(f"{label} must be a list")
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds {maximum} entries")
    normalized = [
        _normalize_text(item, f"{label}[]", maximum=128, allow_empty=False)
        for item in value
    ]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} contains duplicates")
    return sorted(normalized)


def _extract_named_units(mapping: Any, label: str) -> list[dict[str, str]]:
    if mapping is None:
        return []
    source = _require_plain_mapping(mapping, label)
    rows: list[dict[str, str]] = []
    for raw_name, raw_spec in source.items():
        name = _normalize_text(raw_name, f"{label}.name", maximum=128, allow_empty=False)
        spec = _require_plain_mapping(raw_spec, f"{label}.{name}")
        unit = _normalize_text(
            spec.get("unit", ""), f"{label}.{name}.unit", maximum=64, allow_empty=True
        )
        rows.append({"name": name, "unit": unit})
    return _named_units(rows, label, maximum=256)


def _extract_outputs(schema: Any) -> list[dict[str, str]]:
    if schema is None:
        return []
    source = _require_plain_mapping(schema, "solver_output_schema")
    rows: list[dict[str, str]] = []
    primary = source.get("primary_value")
    if primary is not None:
        primary_map = _require_plain_mapping(primary, "solver_output_schema.primary_value")
        if primary_map.get("name"):
            rows.append(
                {"name": primary_map["name"], "unit": primary_map.get("unit", "")}
            )
    comparable = source.get("comparable_fields", [])
    if comparable is None:
        comparable = []
    if type(comparable) is not list:
        raise ValueError("solver_output_schema.comparable_fields must be a list")
    for index, item in enumerate(comparable):
        row = _require_plain_mapping(item, f"comparable_fields[{index}]")
        if row.get("name"):
            rows.append({"name": row["name"], "unit": row.get("unit", "")})
    if len(rows) > 128:
        raise ValueError("outputs exceeds 128 entries")
    dedup: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows):
        name = _normalize_text(
            row["name"], f"outputs[{index}].name", maximum=128, allow_empty=False
        )
        unit = _normalize_text(
            row["unit"], f"outputs[{index}].unit", maximum=64, allow_empty=True
        )
        normalized = {"name": name, "unit": unit}
        previous = dedup.get(name)
        if previous is not None and previous != normalized:
            raise ValueError(f"outputs disagree on unit for {name!r}")
        dedup[name] = normalized
    return [dedup[name] for name in sorted(dedup)]


def _extract_formulas(formulas: Any) -> list[dict[str, str]]:
    if formulas is None:
        return []
    if type(formulas) is not list:
        raise ValueError("formulas must be a list")
    rows: list[dict[str, str]] = []
    for index, item in enumerate(formulas):
        row = _require_plain_mapping(item, f"formulas[{index}]")
        if row.get("name"):
            rows.append({"name": row["name"], "unit": row.get("output_unit", "")})
    return _named_units(rows, "formulas", maximum=128)


def _extract_tokens(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if type(value) is not list:
        raise ValueError(f"{label} must be a list of scalar strings")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must contain only strings")
    return _tokens(list(value), label, maximum=128)


def _embedding_text(fields: dict[str, Any]) -> str:
    parts = [
        f"solver {fields['model_id']}",
        f"name {fields['model_name']}" if fields["model_name"] else "",
        f"description {fields['description']}" if fields["description"] else "",
    ]
    for label in ("variables", "outputs", "formulas"):
        rows = fields[label]
        if rows:
            rendered = ", ".join(
                f"{row['name']} [{row['unit']}]" if row["unit"] else row["name"]
                for row in rows
            )
            parts.append(f"{label} {rendered}")
    for label in ("capabilities", "tags"):
        if fields[label]:
            parts.append(f"{label} {', '.join(fields[label])}")
    return "; ".join(part for part in parts if part)


def _canonical_contract_fields(value: Any) -> dict[str, Any]:
    fields = _require_plain_mapping(value, "contract_fields")
    _require_exact_keys(fields, _CONTRACT_FIELD_KEYS, "contract_fields")
    model_id = validate_solver_id(fields["model_id"])
    return {
        "model_id": model_id,
        "model_name": _normalize_text(
            fields["model_name"], "model_name", maximum=256, allow_empty=True
        ),
        "description": _normalize_text(
            fields["description"], "description", maximum=2048, allow_empty=True
        ),
        "variables": _named_units(fields["variables"], "variables", maximum=256),
        "outputs": _named_units(fields["outputs"], "outputs", maximum=128),
        "formulas": _named_units(fields["formulas"], "formulas", maximum=128),
        "capabilities": _tokens(fields["capabilities"], "capabilities", maximum=128),
        "tags": _tokens(fields["tags"], "tags", maximum=128),
    }


def build_solver_contract_projection(
    axiom: Mapping[str, Any],
    *,
    source_digest: str,
    topology_digest: str,
) -> dict[str, Any]:
    """Build the only solver document shape permitted into vector events."""
    source = _require_plain_mapping(axiom, "axiom")
    model_id = validate_solver_id(source.get("model_id"))
    cell_id = validate_vector_cell_id(source.get("cell_id"))
    _require_digest(source_digest, "source_digest")
    _require_digest(topology_digest, "topology_digest")
    fields = _canonical_contract_fields(
        {
            "model_id": model_id,
            "model_name": source.get("model_name", ""),
            "description": source.get("description", ""),
            "variables": _extract_named_units(source.get("variables"), "variables"),
            "outputs": _extract_outputs(source.get("solver_output_schema")),
            "formulas": _extract_formulas(source.get("formulas")),
            "capabilities": _extract_tokens(source.get("capabilities"), "capabilities"),
            "tags": _extract_tokens(source.get("tags"), "tags"),
        }
    )
    contract_digest = sha256_digest(
        {"schema_version": SOLVER_PROJECTION_VERSION, "contract_fields": fields}
    )
    projection_id = sha256_digest(
        {
            "schema_version": SOLVER_PROJECTION_VERSION,
            "canonical_solver_id": model_id,
            "solver_contract_digest": contract_digest,
        }
    )
    document: dict[str, Any] = {
        "schema_version": SOLVER_PROJECTION_VERSION,
        "projection_id": projection_id,
        "projection_digest": "",
        "document_key": f"solver:{model_id}",
        "canonical_solver_id": model_id,
        "cell_id": cell_id,
        "source_kind": "solver_contract",
        "source_digest": source_digest,
        "solver_contract_digest": contract_digest,
        "topology_digest": topology_digest,
        "contract_fields": fields,
        "embedding_text": _embedding_text(fields),
        "payload_visibility": "allowlisted_solver_contract",
        "quality_gate": "validated_solver_contract",
    }
    document["projection_digest"] = sha256_digest(
        {key: value for key, value in document.items() if key != "projection_digest"}
    )
    return validate_solver_contract_projection(document)


def validate_solver_contract_projection(value: Any) -> dict[str, Any]:
    document = _require_plain_mapping(value, "projection_document")
    _require_exact_keys(document, _PROJECTION_KEYS, "projection_document")
    if document["schema_version"] != SOLVER_PROJECTION_VERSION:
        raise ValueError("unsupported solver projection schema")
    model_id = validate_solver_id(document["canonical_solver_id"])
    if document["document_key"] != f"solver:{model_id}":
        raise ValueError("document_key does not match canonical_solver_id")
    cell_id = validate_vector_cell_id(document["cell_id"])
    if document["source_kind"] != "solver_contract":
        raise ValueError("source_kind must be solver_contract")
    if document["payload_visibility"] != "allowlisted_solver_contract":
        raise ValueError("payload_visibility is not allowlisted")
    if document["quality_gate"] != "validated_solver_contract":
        raise ValueError("quality_gate is not validated")
    source_digest = _require_digest(document["source_digest"], "source_digest")
    topology_digest = _require_digest(document["topology_digest"], "topology_digest")
    fields = _canonical_contract_fields(document["contract_fields"])
    if fields["model_id"] != model_id:
        raise ValueError("contract model_id does not match canonical_solver_id")
    embedding_text = _embedding_text(fields)
    if document["embedding_text"] != embedding_text:
        raise ValueError("embedding_text is not the derived allowlisted text")
    contract_digest = sha256_digest(
        {"schema_version": SOLVER_PROJECTION_VERSION, "contract_fields": fields}
    )
    if document["solver_contract_digest"] != contract_digest:
        raise ValueError("solver_contract_digest mismatch")
    projection_id = sha256_digest(
        {
            "schema_version": SOLVER_PROJECTION_VERSION,
            "canonical_solver_id": model_id,
            "solver_contract_digest": contract_digest,
        }
    )
    if document["projection_id"] != projection_id:
        raise ValueError("projection_id mismatch")
    canonical = {
        **document,
        "canonical_solver_id": model_id,
        "cell_id": cell_id,
        "source_digest": source_digest,
        "topology_digest": topology_digest,
        "contract_fields": fields,
        "embedding_text": embedding_text,
    }
    expected_digest = sha256_digest(
        {key: item for key, item in canonical.items() if key != "projection_digest"}
    )
    if document["projection_digest"] != expected_digest:
        raise ValueError("projection_digest mismatch")
    return canonical


def build_projection_source_identity(
    projection_document: Mapping[str, Any],
    *,
    receipt_event_id: str | None = None,
    receipt_digest: str | None = None,
) -> dict[str, Any]:
    document = validate_solver_contract_projection(projection_document)
    if (receipt_event_id is None) != (receipt_digest is None):
        raise ValueError("receipt_event_id and receipt_digest are all-or-none")
    if receipt_event_id is not None:
        raise ValueError(
            "receipt-bound source identity requires verified receipt evidence; "
            "source_identity.v1 is unreceipted only"
        )
    identity: dict[str, Any] = {
        "schema_version": PROJECTION_SOURCE_IDENTITY_VERSION,
        "canonical_solver_id": document["canonical_solver_id"],
        "solver_contract_digest": document["solver_contract_digest"],
        "source_digest": document["source_digest"],
        "receipt_event_id": receipt_event_id,
        "receipt_digest": receipt_digest,
        "receipt_bound": receipt_event_id is not None,
        "identity_digest": "",
    }
    identity["identity_digest"] = sha256_digest(
        {key: item for key, item in identity.items() if key != "identity_digest"}
    )
    return validate_projection_source_identity(identity)


def build_projection_receipt_context() -> dict[str, Any]:
    """Build the exact local artifacts behind a self-certified receipt.

    These artifacts satisfy the generic MAGMA receipt envelope without
    pretending that an external policy authority, RCO reviewer, or world-state
    observer participated. Their negative posture is data, not documentation.
    """
    return {
        "schema_version": PROJECTION_RECEIPT_CONTEXT_VERSION,
        "policy_artifact": {
            "schema_version": "magma.faiss.local_policy_declaration.v1",
            "scope": "candidate_projection_only",
            "receipt_semantics": "self_certified_structure_only",
            "admission_evaluator_contract_digest": (
                PROJECTION_ADMISSION_EVALUATOR_DIGEST
            ),
            "runtime_authority_granted": False,
        },
        "charter_artifact": {
            "schema_version": "magma.faiss.local_charter_declaration.v1",
            "authority": "none",
            "external_governance_verified": False,
            "solver_outcome_verified": False,
        },
        "rco_decision_artifact": {
            "schema_version": "magma.faiss.local_rco_status.v1",
            "review_performed": False,
            "decision": "not_evaluated",
            "authority_granted": False,
        },
        "world_snapshot_artifact": {
            "schema_version": "magma.faiss.local_world_snapshot.v1",
            "observation_scope": "projection_contract_only",
            "external_state_observed": False,
            "network_access": False,
        },
    }


def validate_projection_receipt_context(value: Any) -> dict[str, Any]:
    context = _require_plain_mapping(value, "projection receipt context")
    _require_exact_keys(
        context,
        _PROJECTION_RECEIPT_CONTEXT_KEYS,
        "projection receipt context",
    )
    expected = build_projection_receipt_context()
    artifact_keys = {
        "policy_artifact": _LOCAL_POLICY_ARTIFACT_KEYS,
        "charter_artifact": _LOCAL_CHARTER_ARTIFACT_KEYS,
        "rco_decision_artifact": _LOCAL_RCO_ARTIFACT_KEYS,
        "world_snapshot_artifact": _LOCAL_WORLD_ARTIFACT_KEYS,
    }
    for name, keys in artifact_keys.items():
        artifact = _require_plain_mapping(context[name], name.replace("_", " "))
        _require_exact_keys(artifact, keys, name.replace("_", " "))
    if context != expected:
        raise ValueError(
            "projection receipt context must remain explicitly non-authoritative"
        )
    return expected


def projection_receipt_context_digests(
    value: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    context = validate_projection_receipt_context(
        value if value is not None else build_projection_receipt_context()
    )
    return {
        "policy_digest": sha256_digest(context["policy_artifact"]),
        "charter_digest": sha256_digest(context["charter_artifact"]),
        "rco_decision_digest": sha256_digest(context["rco_decision_artifact"]),
        "world_snapshot_digest": sha256_digest(
            context["world_snapshot_artifact"]
        ),
    }


def build_projection_admission_payload(
    projection_document: Mapping[str, Any],
    embedding_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the privacy-safe payload admitted by a projection receipt."""
    document = validate_solver_contract_projection(projection_document)
    embedding = validate_embedding_contract(embedding_contract)
    return {
        "schema_version": PROJECTION_ADMISSION_PAYLOAD_VERSION,
        "operation": "admit_solver_contract_projection",
        "projection_id": document["projection_id"],
        "projection_digest": document["projection_digest"],
        "canonical_solver_id": document["canonical_solver_id"],
        "cell_id": document["cell_id"],
        "solver_contract_digest": document["solver_contract_digest"],
        "source_digest": document["source_digest"],
        "topology_digest": document["topology_digest"],
        "embedding_contract_digest": embedding["contract_digest"],
        "admission_evaluator_contract_digest": PROJECTION_ADMISSION_EVALUATOR_DIGEST,
        "authority_scope": "candidate_projection_only",
        "receipt_authenticity_verified": False,
        "runtime_authority_granted": False,
        "external_authority_artifacts_verified": False,
        "solver_outcome_verified": False,
    }


def _projection_admission_event_id(payload: Mapping[str, Any]) -> str:
    return "magma:faiss:projection:" + sha256_digest(payload).split(":", 1)[1]


def projection_admission_event_id(
    projection_document: Mapping[str, Any],
    embedding_contract: Mapping[str, Any],
) -> str:
    payload = build_projection_admission_payload(
        projection_document, embedding_contract
    )
    return _projection_admission_event_id(payload)


def build_projection_receipt_proof(
    projection_document: Mapping[str, Any],
    embedding_contract: Mapping[str, Any],
    *,
    evaluation_result: Mapping[str, Any],
    receipt: Mapping[str, Any],
    receipt_context: Mapping[str, Any],
) -> dict[str, Any]:
    context = validate_projection_receipt_context(receipt_context)
    proof: dict[str, Any] = {
        "schema_version": PROJECTION_RECEIPT_PROOF_VERSION,
        "payload": build_projection_admission_payload(
            projection_document, embedding_contract
        ),
        "receipt_context": context,
        "evaluation_result": dict(evaluation_result),
        "receipt": dict(receipt),
        "proof_digest": "",
    }
    proof["proof_digest"] = sha256_digest(
        {key: item for key, item in proof.items() if key != "proof_digest"}
    )
    return validate_projection_receipt_proof(
        proof, projection_document, embedding_contract
    )


def validate_projection_receipt_proof(
    value: Any,
    projection_document: Mapping[str, Any],
    embedding_contract: Mapping[str, Any],
) -> dict[str, Any]:
    expected_payload = build_projection_admission_payload(
        projection_document, embedding_contract
    )
    return _validate_projection_receipt_proof_against_payload(
        value, expected_payload
    )


def _validate_projection_receipt_proof_against_payload(
    value: Any,
    expected_payload: dict[str, Any],
) -> dict[str, Any]:
    from waggledance.core.magma.evaluation_result import (
        validate_evaluation_result,
    )
    from waggledance.core.magma.receipt import validate_magma_receipt

    proof = _require_plain_mapping(value, "projection receipt proof")
    _require_exact_keys(
        proof, _PROJECTION_RECEIPT_PROOF_KEYS, "projection receipt proof"
    )
    if proof["schema_version"] != PROJECTION_RECEIPT_PROOF_VERSION:
        raise ValueError("unsupported projection receipt proof schema")
    payload = _require_plain_mapping(
        proof["payload"], "projection admission payload"
    )
    _require_exact_keys(
        payload,
        _PROJECTION_ADMISSION_PAYLOAD_KEYS,
        "projection admission payload",
    )
    if (
        payload != expected_payload
        or payload["receipt_authenticity_verified"] is not False
        or payload["runtime_authority_granted"] is not False
        or payload["external_authority_artifacts_verified"] is not False
        or payload["solver_outcome_verified"] is not False
    ):
        raise ValueError("projection receipt payload does not match projection contracts")

    receipt_context = validate_projection_receipt_context(
        proof["receipt_context"]
    )
    evaluation = validate_evaluation_result(proof["evaluation_result"])
    receipt = validate_magma_receipt(proof["receipt"])
    payload_digest = sha256_digest(expected_payload)
    solver_id = expected_payload["canonical_solver_id"]
    expected_event_id = _projection_admission_event_id(expected_payload)
    if evaluation["target_digest"] != payload_digest:
        raise ValueError("projection evaluation target digest mismatch")
    if evaluation["risk_class"] != "local_artifact":
        raise ValueError("projection evaluation must be local_artifact")
    if (
        evaluation["subject_type"] != "solver"
        or evaluation["expected_gate"] != "allow"
        or evaluation["actual_gate"] != "allow"
        or evaluation["verdict"] != "pass"
        or evaluation["operator_required"] is not False
        or evaluation["solver_selection"] != [solver_id]
        or evaluation["verifier_path"]
        != [PROJECTION_ADMISSION_EVALUATOR_VERSION]
        or evaluation["policy_version"] != "policy:magma_faiss_projection:v1"
        or evaluation["charter_version"]
        != "charter:candidate_projection_only:v1"
        or evaluation["domain_threshold_version"]
        != "threshold:projection_contract:v1"
        or evaluation["reason_codes"]
        != ["magma:faiss:projection_contract_valid"]
    ):
        raise ValueError("projection evaluation does not admit the exact solver")
    if receipt["event_id"] != expected_event_id:
        raise ValueError("projection receipt event id mismatch")
    if receipt["risk_class"] != "local_artifact":
        raise ValueError("projection receipt must be local_artifact")
    if receipt["payload_visibility"] != "full_payload":
        raise ValueError("projection receipt must declare its embedded full payload")
    if receipt["canonical_payload_digest"] != payload_digest:
        raise ValueError("projection receipt payload digest mismatch")
    if receipt["evaluation_result_digest"] != sha256_digest(evaluation):
        raise ValueError("projection receipt evaluation digest mismatch")
    if receipt["solver_contract_digest"] != expected_payload["solver_contract_digest"]:
        raise ValueError("projection receipt solver contract mismatch")
    context_digests = projection_receipt_context_digests(receipt_context)
    if any(receipt[key] != digest for key, digest in context_digests.items()):
        raise ValueError("projection receipt context digest mismatch")
    if (
        receipt["operator_gate_required"] is not False
        or receipt["approval_id"] is not None
    ):
        raise ValueError("projection receipt must not claim an operator approval gate")
    if (
        receipt["prev_receipt_hash"] is not None
        or receipt["signature_algorithm"] is not None
        or receipt["signature"] is not None
        or receipt["key_id"] is not None
        or receipt["anchored_at"] is not None
    ):
        raise ValueError(
            "projection receipt proof cannot claim an unverified chain, signature, "
            "or anchor"
        )

    canonical: dict[str, Any] = {
        "schema_version": PROJECTION_RECEIPT_PROOF_VERSION,
        "payload": expected_payload,
        "receipt_context": receipt_context,
        "evaluation_result": evaluation,
        "receipt": receipt,
        "proof_digest": _require_digest(
            proof["proof_digest"], "projection receipt proof_digest"
        ),
    }
    expected_proof_digest = sha256_digest(
        {key: item for key, item in canonical.items() if key != "proof_digest"}
    )
    if proof["proof_digest"] != expected_proof_digest:
        raise ValueError("projection receipt proof digest mismatch")
    return canonical


def build_self_certified_projection_receipt_proof(
    projection_document: Mapping[str, Any],
    embedding_contract: Mapping[str, Any],
    *,
    ts_utc: str,
) -> dict[str, Any]:
    """Build deterministic local admission evidence with zero external authority."""
    from waggledance.core.magma.evaluation_result import build_evaluation_result
    from waggledance.core.magma.receipt import build_magma_receipt

    document = validate_solver_contract_projection(projection_document)
    embedding = validate_embedding_contract(embedding_contract)
    payload = build_projection_admission_payload(document, embedding)
    context = build_projection_receipt_context()
    context_digests = projection_receipt_context_digests(context)
    evaluation = build_evaluation_result(
        case_id=(
            "case:magma_faiss_projection:" + document["canonical_solver_id"]
        ),
        subject_type="solver",
        target_payload=payload,
        risk_class="local_artifact",
        expected_gate="allow",
        actual_gate="allow",
        verifier_path=[PROJECTION_ADMISSION_EVALUATOR_VERSION],
        solver_selection=[document["canonical_solver_id"]],
        policy_version="policy:magma_faiss_projection:v1",
        charter_version="charter:candidate_projection_only:v1",
        domain_threshold_version="threshold:projection_contract:v1",
        verdict="pass",
        reason_codes=["magma:faiss:projection_contract_valid"],
        confidence_score=1.0,
        uncertainty_sources=[
            {
                "kind": "limited_evidence",
                "detail": (
                    "Self-certified projection structure; no receipt authenticity, "
                    "solver outcome, external governance, or runtime authority."
                ),
            }
        ],
    )
    receipt = build_magma_receipt(
        event_id=projection_admission_event_id(document, embedding),
        ts_utc=ts_utc,
        risk_class="local_artifact",
        payload=payload,
        evaluation_result=evaluation,
        solver_contract_digest=document["solver_contract_digest"],
        payload_visibility="full_payload",
        **context_digests,
    )
    return build_projection_receipt_proof(
        document,
        embedding,
        evaluation_result=evaluation,
        receipt=receipt,
        receipt_context=context,
    )


def build_receipt_bound_projection_source_identity(
    projection_document: Mapping[str, Any],
    embedding_contract: Mapping[str, Any],
    receipt_proof: Mapping[str, Any],
) -> dict[str, Any]:
    """Build v2 from a structurally verified, self-certified receipt proof.

    ``receipt_bound`` means that the exact local receipt envelope is embedded
    and re-derivable. It never means that an external authority authenticated
    the receipt, that solver output was evaluated, or that runtime use is
    authorized; those negative capabilities are explicit top-level fields.
    """
    document = validate_solver_contract_projection(projection_document)
    embedding = validate_embedding_contract(embedding_contract)
    proof = validate_projection_receipt_proof(
        receipt_proof, document, embedding
    )
    receipt = proof["receipt"]
    identity: dict[str, Any] = {
        "schema_version": PROJECTION_SOURCE_IDENTITY_V2_VERSION,
        "projection_id": document["projection_id"],
        "projection_digest": document["projection_digest"],
        "canonical_solver_id": document["canonical_solver_id"],
        "cell_id": document["cell_id"],
        "solver_contract_digest": document["solver_contract_digest"],
        "source_digest": document["source_digest"],
        "topology_digest": document["topology_digest"],
        "embedding_contract_digest": embedding["contract_digest"],
        "receipt_event_id": receipt["event_id"],
        "receipt_digest": sha256_digest(receipt),
        "receipt_proof_digest": proof["proof_digest"],
        "receipt_bound": True,
        "receipt_authenticity_verified": False,
        "external_authority_artifacts_verified": False,
        "solver_outcome_verified": False,
        "runtime_authority_granted": False,
        "receipt_proof": proof,
        "identity_digest": "",
    }
    identity["identity_digest"] = sha256_digest(
        {key: item for key, item in identity.items() if key != "identity_digest"}
    )
    validated = validate_projection_source_identity(identity)
    return validate_projection_source_binding(
        document, validated, embedding
    )


def validate_projection_source_identity(value: Any) -> dict[str, Any]:
    identity = _require_plain_mapping(value, "source_identity")
    version = identity.get("schema_version")
    if version == PROJECTION_SOURCE_IDENTITY_V1_VERSION:
        return _validate_projection_source_identity_v1(identity)
    if version == PROJECTION_SOURCE_IDENTITY_V2_VERSION:
        return _validate_projection_source_identity_v2(identity)
    raise ValueError("unsupported projection source identity schema")


def _validate_projection_source_identity_v1(
    identity: dict[str, Any],
) -> dict[str, Any]:
    _require_exact_keys(identity, _SOURCE_IDENTITY_V1_KEYS, "source_identity")
    if identity["schema_version"] != PROJECTION_SOURCE_IDENTITY_V1_VERSION:
        raise ValueError("unsupported projection source identity schema")
    solver_id = validate_solver_id(identity["canonical_solver_id"])
    contract_digest = _require_digest(
        identity["solver_contract_digest"], "solver_contract_digest"
    )
    source_digest = _require_digest(identity["source_digest"], "source_digest")
    event_id = identity["receipt_event_id"]
    receipt_digest = identity["receipt_digest"]
    if (event_id is None) != (receipt_digest is None):
        raise ValueError("receipt identity pair is incomplete")
    if type(identity["receipt_bound"]) is not bool:
        raise ValueError("receipt_bound must be a boolean")
    if identity["receipt_bound"] is not (event_id is not None):
        raise ValueError("receipt_bound does not match receipt identity")
    if event_id is not None:
        raise ValueError(
            "receipt-bound source identity requires verified receipt evidence; "
            "source_identity.v1 is unreceipted only"
        )
    canonical = {
        **identity,
        "canonical_solver_id": solver_id,
        "solver_contract_digest": contract_digest,
        "source_digest": source_digest,
        "receipt_event_id": event_id,
        "receipt_digest": receipt_digest,
    }
    expected = sha256_digest(
        {key: item for key, item in canonical.items() if key != "identity_digest"}
    )
    identity_digest = _require_digest(identity["identity_digest"], "identity_digest")
    if identity_digest != expected:
        raise ValueError("source identity digest mismatch")
    return canonical


def _validate_projection_source_identity_v2(
    identity: dict[str, Any],
) -> dict[str, Any]:
    _require_exact_keys(identity, _SOURCE_IDENTITY_V2_KEYS, "source_identity")
    projection_id = _require_digest(identity["projection_id"], "projection_id")
    projection_digest = _require_digest(
        identity["projection_digest"], "projection_digest"
    )
    solver_id = validate_solver_id(identity["canonical_solver_id"])
    cell_id = validate_vector_cell_id(identity["cell_id"])
    contract_digest = _require_digest(
        identity["solver_contract_digest"], "solver_contract_digest"
    )
    source_digest = _require_digest(identity["source_digest"], "source_digest")
    topology_digest = _require_digest(
        identity["topology_digest"], "topology_digest"
    )
    embedding_digest = _require_digest(
        identity["embedding_contract_digest"], "embedding_contract_digest"
    )
    expected_payload = {
        "schema_version": PROJECTION_ADMISSION_PAYLOAD_VERSION,
        "operation": "admit_solver_contract_projection",
        "projection_id": projection_id,
        "projection_digest": projection_digest,
        "canonical_solver_id": solver_id,
        "cell_id": cell_id,
        "solver_contract_digest": contract_digest,
        "source_digest": source_digest,
        "topology_digest": topology_digest,
        "embedding_contract_digest": embedding_digest,
        "admission_evaluator_contract_digest": PROJECTION_ADMISSION_EVALUATOR_DIGEST,
        "authority_scope": "candidate_projection_only",
        "receipt_authenticity_verified": False,
        "runtime_authority_granted": False,
        "external_authority_artifacts_verified": False,
        "solver_outcome_verified": False,
    }
    proof = _validate_projection_receipt_proof_against_payload(
        identity["receipt_proof"], expected_payload
    )
    receipt = proof["receipt"]
    event_id = _normalize_text(
        identity["receipt_event_id"],
        "receipt_event_id",
        maximum=180,
        allow_empty=False,
    )
    receipt_digest = _require_digest(identity["receipt_digest"], "receipt_digest")
    proof_digest = _require_digest(
        identity["receipt_proof_digest"], "receipt_proof_digest"
    )
    if identity["receipt_bound"] is not True:
        raise ValueError("source_identity.v2 must be receipt-bound")
    if (
        identity["receipt_authenticity_verified"] is not False
        or identity["external_authority_artifacts_verified"] is not False
        or identity["solver_outcome_verified"] is not False
        or identity["runtime_authority_granted"] is not False
    ):
        raise ValueError("source_identity.v2 cannot grant authority or authenticity")
    if event_id != receipt["event_id"]:
        raise ValueError("source identity receipt event mismatch")
    if receipt_digest != sha256_digest(receipt):
        raise ValueError("source identity receipt digest mismatch")
    if proof_digest != proof["proof_digest"]:
        raise ValueError("source identity receipt proof digest mismatch")
    canonical: dict[str, Any] = {
        **identity,
        "projection_id": projection_id,
        "projection_digest": projection_digest,
        "canonical_solver_id": solver_id,
        "cell_id": cell_id,
        "solver_contract_digest": contract_digest,
        "source_digest": source_digest,
        "topology_digest": topology_digest,
        "embedding_contract_digest": embedding_digest,
        "receipt_event_id": event_id,
        "receipt_digest": receipt_digest,
        "receipt_proof_digest": proof_digest,
        "receipt_proof": proof,
    }
    identity_digest = _require_digest(identity["identity_digest"], "identity_digest")
    expected_identity_digest = sha256_digest(
        {key: item for key, item in canonical.items() if key != "identity_digest"}
    )
    if identity_digest != expected_identity_digest:
        raise ValueError("source identity digest mismatch")
    return canonical


def validate_projection_source_binding(
    projection_document: Mapping[str, Any],
    source_identity: Mapping[str, Any],
    embedding_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Cross-check an identity against its adjacent immutable contracts."""
    document = validate_solver_contract_projection(projection_document)
    identity = validate_projection_source_identity(source_identity)
    if identity["canonical_solver_id"] != document["canonical_solver_id"]:
        raise ValueError("source identity solver does not match projection")
    if identity["solver_contract_digest"] != document["solver_contract_digest"]:
        raise ValueError("source identity contract digest does not match projection")
    if identity["source_digest"] != document["source_digest"]:
        raise ValueError("source identity source digest does not match projection")
    embedding = (
        validate_embedding_contract(embedding_contract)
        if embedding_contract is not None
        else None
    )
    if identity["schema_version"] == PROJECTION_SOURCE_IDENTITY_V2_VERSION:
        if embedding is None:
            raise ValueError("source_identity.v2 requires an embedding contract")
        expected = {
            "projection_id": document["projection_id"],
            "projection_digest": document["projection_digest"],
            "cell_id": document["cell_id"],
            "topology_digest": document["topology_digest"],
            "embedding_contract_digest": embedding["contract_digest"],
        }
        if any(identity[key] != item for key, item in expected.items()):
            raise ValueError("source_identity.v2 does not match projection contracts")
    return identity


def build_embedding_contract(
    *,
    model_id: str,
    model_version: str,
    dimension: int,
    normalization: str = "l2-v1",
    document_prefix: str = "",
    query_prefix: str = "search_query: ",
) -> dict[str, Any]:
    contract: dict[str, Any] = {
        "schema_version": EMBEDDING_CONTRACT_VERSION,
        "model_id": model_id,
        "model_version": model_version,
        "normalization": normalization,
        "dimension": dimension,
        "document_prefix": document_prefix,
        "query_prefix": query_prefix,
        "contract_digest": "",
    }
    contract["contract_digest"] = sha256_digest(
        {key: item for key, item in contract.items() if key != "contract_digest"}
    )
    return validate_embedding_contract(contract)


def validate_embedding_contract(value: Any) -> dict[str, Any]:
    contract = _require_plain_mapping(value, "embedding_contract")
    _require_exact_keys(contract, _EMBEDDING_KEYS, "embedding_contract")
    if contract["schema_version"] != EMBEDDING_CONTRACT_VERSION:
        raise ValueError("unsupported embedding contract schema")
    canonical = {
        **contract,
        "model_id": _normalize_text(
            contract["model_id"], "embedding model_id", maximum=256, allow_empty=False
        ),
        "model_version": _normalize_text(
            contract["model_version"],
            "embedding model_version",
            maximum=128,
            allow_empty=False,
        ),
        "normalization": _normalize_text(
            contract["normalization"],
            "embedding normalization",
            maximum=64,
            allow_empty=False,
        ),
        "document_prefix": _bounded_prefix(
            contract["document_prefix"], "embedding document_prefix"
        ),
        "query_prefix": _bounded_prefix(
            contract["query_prefix"], "embedding query_prefix"
        ),
    }
    if type(contract["dimension"]) is not int or contract["dimension"] <= 0:
        raise ValueError("embedding dimension must be a positive integer")
    canonical["dimension"] = contract["dimension"]
    expected = sha256_digest(
        {key: item for key, item in canonical.items() if key != "contract_digest"}
    )
    if contract["contract_digest"] != expected:
        raise ValueError("embedding contract digest mismatch")
    return canonical


def build_retrieval_topology_contract() -> dict[str, Any]:
    """Build the current eight-cell solver retrieval topology contract."""
    from waggledance.core.hex_cell_topology import ALL_CELLS, HexCellTopology

    topology = HexCellTopology()
    contract = {
        "schema_version": RETRIEVAL_TOPOLOGY_VERSION,
        "cells": [
            {
                "cell_id": cell_id,
                "parent_cell_id": None,
                "child_cell_ids": [],
                "neighbor_cell_ids": topology.get_neighbors(cell_id, max_ring=1),
                "live": True,
                "subdivision_state": "leaf",
            }
            for cell_id in sorted(ALL_CELLS)
        ],
    }
    return validate_retrieval_topology_contract(contract)


def validate_retrieval_topology_contract(value: Any) -> dict[str, Any]:
    contract = _require_plain_mapping(value, "retrieval_topology")
    _require_exact_keys(contract, _TOPOLOGY_KEYS, "retrieval_topology")
    if contract["schema_version"] != RETRIEVAL_TOPOLOGY_VERSION:
        raise ValueError("unsupported retrieval topology schema")
    if type(contract["cells"]) is not list or not contract["cells"]:
        raise ValueError("retrieval topology cells must be a non-empty list")
    records: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(contract["cells"]):
        row = _require_plain_mapping(raw, f"retrieval_topology.cells[{index}]")
        _require_exact_keys(row, _TOPOLOGY_CELL_KEYS, f"retrieval_topology.cells[{index}]")
        cell_id = validate_vector_cell_id(row["cell_id"])
        if cell_id in records:
            raise ValueError(f"duplicate retrieval cell {cell_id!r}")
        parent = row["parent_cell_id"]
        if parent is not None:
            parent = validate_vector_cell_id(parent)
        if type(row["child_cell_ids"]) is not list:
            raise ValueError("retrieval child_cell_ids must be a list")
        if type(row["neighbor_cell_ids"]) is not list:
            raise ValueError("retrieval neighbor_cell_ids must be a list")
        children = [validate_vector_cell_id(item) for item in row["child_cell_ids"]]
        neighbors = [validate_vector_cell_id(item) for item in row["neighbor_cell_ids"]]
        if len(set(children)) != len(children) or len(set(neighbors)) != len(neighbors):
            raise ValueError(f"retrieval cell {cell_id!r} has duplicate references")
        if cell_id in children or cell_id in neighbors or parent == cell_id:
            raise ValueError(f"retrieval cell {cell_id!r} references itself")
        if type(row["live"]) is not bool:
            raise ValueError("retrieval cell live must be a boolean")
        state = row["subdivision_state"]
        if state not in {"leaf", "subdivided", "shadow"}:
            raise ValueError("unknown subdivision_state")
        if children and state == "leaf":
            raise ValueError("leaf retrieval cell cannot have children")
        if not children and state == "subdivided":
            raise ValueError("subdivided retrieval cell must have children")
        records[cell_id] = {
            "cell_id": cell_id,
            "parent_cell_id": parent,
            "child_cell_ids": sorted(children),
            "neighbor_cell_ids": sorted(neighbors),
            "live": row["live"],
            "subdivision_state": state,
        }
    ids = set(records)
    for cell_id, row in records.items():
        refs = set(row["child_cell_ids"]) | set(row["neighbor_cell_ids"])
        if row["parent_cell_id"] is not None:
            refs.add(row["parent_cell_id"])
        missing = refs - ids
        if missing:
            raise ValueError(f"retrieval cell {cell_id!r} has dangling references {sorted(missing)}")
        for neighbor in row["neighbor_cell_ids"]:
            if cell_id not in records[neighbor]["neighbor_cell_ids"]:
                raise ValueError("retrieval topology neighbor relation is not reciprocal")
        for child in row["child_cell_ids"]:
            if records[child]["parent_cell_id"] != cell_id:
                raise ValueError("retrieval topology parent/child relation is not reciprocal")
        parent = row["parent_cell_id"]
        if parent is not None and cell_id not in records[parent]["child_cell_ids"]:
            raise ValueError("retrieval topology child/parent relation is not reciprocal")
        seen: set[str] = set()
        cursor: str | None = cell_id
        while cursor is not None:
            if cursor in seen:
                raise ValueError("retrieval topology parent graph contains a cycle")
            seen.add(cursor)
            cursor = records[cursor]["parent_cell_id"]
    return {
        "schema_version": RETRIEVAL_TOPOLOGY_VERSION,
        "cells": [records[cell_id] for cell_id in sorted(records)],
    }


def retrieval_topology_digest(contract: Mapping[str, Any]) -> str:
    return sha256_digest(validate_retrieval_topology_contract(contract))


def validate_rebalanced_projection_partition(
    before_projection_ids: Iterable[str],
    documents_by_cell: Mapping[str, list[Mapping[str, Any]]],
    topology_contract: Mapping[str, Any],
) -> dict[str, Any]:
    topology = validate_retrieval_topology_contract(topology_contract)
    topology_digest = retrieval_topology_digest(topology)
    cells = {row["cell_id"]: row for row in topology["cells"]}
    before = list(before_projection_ids)
    if len(before) != len(set(before)):
        raise ValueError("before projection ids contain duplicates")
    for projection_id in before:
        _require_digest(projection_id, "projection_id")
    observed: list[str] = []
    for raw_cell, raw_documents in documents_by_cell.items():
        cell_id = validate_vector_cell_id(raw_cell)
        if cell_id not in cells:
            raise ValueError(f"projection partition references unknown cell {cell_id!r}")
        cell = cells[cell_id]
        if cell["child_cell_ids"] or not cell["live"]:
            raise ValueError("projection partition must target live leaf cells")
        if type(raw_documents) is not list:
            raise ValueError("projection partition values must be lists")
        for raw_document in raw_documents:
            document = validate_solver_contract_projection(raw_document)
            if document["cell_id"] != cell_id:
                raise ValueError("projection document cell does not match partition")
            if document["topology_digest"] != topology_digest:
                raise ValueError("projection document uses a stale topology digest")
            observed.append(document["projection_id"])
    counts = Counter(observed)
    duplicates = sorted(key for key, count in counts.items() if count != 1)
    missing = sorted(set(before) - set(observed))
    orphaned = sorted(set(observed) - set(before))
    if duplicates or missing or orphaned:
        raise ValueError(
            "projection rebalance is incomplete: "
            f"duplicates={duplicates}, missing={missing}, orphaned={orphaned}"
        )
    return {
        "before_count": len(before),
        "after_count": len(observed),
        "duplicates": 0,
        "missing": 0,
        "orphaned": 0,
        "topology_digest": topology_digest,
    }
