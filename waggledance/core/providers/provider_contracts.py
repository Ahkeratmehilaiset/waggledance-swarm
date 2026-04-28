# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Validated request/response contracts for the provider plane.

This module loads the canonical JSON schemas at
``schemas/provider_request.schema.json`` and
``schemas/provider_response.schema.json`` (both shipped in Phase 9)
and exposes:

* :class:`ProviderRequest` and :class:`ProviderResponse` dataclasses;
* :func:`validate_request` and :func:`validate_response` which raise
  :class:`ProviderContractError` on a contract violation (RULE 14:
  fail-loud).

The validators use :mod:`jsonschema` if available; otherwise they fall
back to a small structural validator for the required fields. We do
not silently skip — if neither path works, validation raises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


PROVIDER_TYPES: tuple[str, ...] = (
    "claude_code_builder_lane",
    "anthropic_api",
    "gpt_api",
    "local_model_service",
    "dry_run_stub",
)

TASK_CLASSES: tuple[str, ...] = (
    "code_or_repair",
    "spec_or_critique",
    "bulk_classification",
)

TRUST_LAYERS: tuple[str, ...] = (
    "raw_quarantine",
    "internal_consistency_passed",
    "cross_check_passed",
    "corroborated",
    "calibration_threshold_passed",
    "human_gated",
)


class ProviderContractError(ValueError):
    """Raised on schema-violating provider request/response payloads."""


@dataclass(frozen=True)
class ProviderRequest:
    """Validated provider request pack.

    Mirrors ``schemas/provider_request.schema.json`` (Phase 9 contract).
    """

    schema_version: int
    request_id: str
    task_class: str
    provider_priority_list: tuple[str, ...]
    intent: str
    input_payload: Mapping[str, Any]
    budget: Mapping[str, float]
    no_runtime_mutation: bool
    provenance: Mapping[str, str]
    agent_id_hint: Optional[str] = None
    capsule_context: str = "neutral_v1"
    section: Optional[str] = None
    purpose: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "task_class": self.task_class,
            "provider_priority_list": list(self.provider_priority_list),
            "intent": self.intent,
            "input_payload": dict(self.input_payload),
            "budget": dict(self.budget),
            "no_runtime_mutation": self.no_runtime_mutation,
            "provenance": dict(self.provenance),
            "agent_id_hint": self.agent_id_hint,
            "capsule_context": self.capsule_context,
        }
        return out


@dataclass(frozen=True)
class ProviderResponse:
    """Validated provider response pack.

    Mirrors ``schemas/provider_response.schema.json`` (Phase 9 contract).
    """

    schema_version: int
    response_id: str
    request_id: str
    provider_used: str
    raw_payload: Mapping[str, Any]
    ts_iso: str
    trust_layer_state: str
    no_direct_mutation: bool
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "response_id": self.response_id,
            "request_id": self.request_id,
            "provider_used": self.provider_used,
            "raw_payload": dict(self.raw_payload),
            "ts_iso": self.ts_iso,
            "latency_ms": self.latency_ms,
            "trust_layer_state": self.trust_layer_state,
            "no_direct_mutation": self.no_direct_mutation,
        }


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _REPO_ROOT / "schemas"
_REQUEST_SCHEMA_PATH = _SCHEMA_DIR / "provider_request.schema.json"
_RESPONSE_SCHEMA_PATH = _SCHEMA_DIR / "provider_response.schema.json"


def _load_schema(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise ProviderContractError(f"schema file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _try_jsonschema_validate(payload: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        return  # caller will fall through to structural validation
    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.exceptions.ValidationError as exc:  # type: ignore[attr-defined]
        raise ProviderContractError(f"schema validation failed: {exc.message}") from exc


def _structural_validate_request(payload: Mapping[str, Any]) -> None:
    required = (
        "schema_version", "request_id", "task_class",
        "provider_priority_list", "intent", "input_payload",
        "budget", "no_runtime_mutation", "provenance",
    )
    missing = [k for k in required if k not in payload]
    if missing:
        raise ProviderContractError(f"request missing required fields: {missing}")
    if payload["task_class"] not in TASK_CLASSES:
        raise ProviderContractError(
            f"task_class {payload['task_class']!r} not in {TASK_CLASSES}"
        )
    chain = payload["provider_priority_list"]
    if not isinstance(chain, Sequence) or not all(p in PROVIDER_TYPES for p in chain):
        raise ProviderContractError(
            f"provider_priority_list contains unknown provider type: {chain}"
        )
    if payload["no_runtime_mutation"] is not True:
        raise ProviderContractError("no_runtime_mutation must be True")
    prov = payload["provenance"]
    for key in ("branch_name", "base_commit_hash", "pinned_input_manifest_sha256"):
        if key not in prov:
            raise ProviderContractError(f"provenance missing {key}")
    budget = payload["budget"]
    if "max_calls" not in budget:
        raise ProviderContractError("budget.max_calls is required")


def _structural_validate_response(payload: Mapping[str, Any]) -> None:
    required = (
        "schema_version", "response_id", "request_id", "provider_used",
        "raw_payload", "ts_iso", "trust_layer_state", "no_direct_mutation",
    )
    missing = [k for k in required if k not in payload]
    if missing:
        raise ProviderContractError(f"response missing required fields: {missing}")
    if payload["trust_layer_state"] not in TRUST_LAYERS:
        raise ProviderContractError(
            f"trust_layer_state {payload['trust_layer_state']!r} not in {TRUST_LAYERS}"
        )
    if payload["no_direct_mutation"] is not True:
        raise ProviderContractError("no_direct_mutation must be True")


def validate_request(payload: Mapping[str, Any]) -> ProviderRequest:
    """Validate ``payload`` against the Phase 9 request schema and
    return a :class:`ProviderRequest`. Raises
    :class:`ProviderContractError` on any violation."""

    if not isinstance(payload, Mapping):
        raise ProviderContractError(f"request payload must be a Mapping, got {type(payload)!r}")
    schema = _load_schema(_REQUEST_SCHEMA_PATH)
    _try_jsonschema_validate(payload, schema)
    _structural_validate_request(payload)
    return ProviderRequest(
        schema_version=int(payload["schema_version"]),
        request_id=str(payload["request_id"]),
        task_class=str(payload["task_class"]),
        provider_priority_list=tuple(str(p) for p in payload["provider_priority_list"]),
        intent=str(payload["intent"]),
        input_payload=dict(payload["input_payload"]),
        budget={k: float(v) for k, v in payload["budget"].items()},
        no_runtime_mutation=bool(payload["no_runtime_mutation"]),
        provenance={k: str(v) for k, v in payload["provenance"].items()},
        agent_id_hint=(
            str(payload["agent_id_hint"]) if payload.get("agent_id_hint") else None
        ),
        capsule_context=str(payload.get("capsule_context", "neutral_v1")),
        section=(str(payload["section"]) if payload.get("section") else None),
        purpose=(str(payload["purpose"]) if payload.get("purpose") else None),
    )


def validate_response(payload: Mapping[str, Any]) -> ProviderResponse:
    """Validate ``payload`` against the Phase 9 response schema and
    return a :class:`ProviderResponse`."""

    if not isinstance(payload, Mapping):
        raise ProviderContractError(f"response payload must be a Mapping, got {type(payload)!r}")
    schema = _load_schema(_RESPONSE_SCHEMA_PATH)
    _try_jsonschema_validate(payload, schema)
    _structural_validate_response(payload)
    return ProviderResponse(
        schema_version=int(payload["schema_version"]),
        response_id=str(payload["response_id"]),
        request_id=str(payload["request_id"]),
        provider_used=str(payload["provider_used"]),
        raw_payload=dict(payload["raw_payload"]),
        ts_iso=str(payload["ts_iso"]),
        latency_ms=float(payload.get("latency_ms", 0.0)),
        trust_layer_state=str(payload["trust_layer_state"]),
        no_direct_mutation=bool(payload["no_direct_mutation"]),
    )


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
