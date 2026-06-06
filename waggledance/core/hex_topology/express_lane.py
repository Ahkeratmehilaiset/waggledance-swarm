# SPDX-License-Identifier: BUSL-1.1
"""Simulation-only hex express-lane route planning.

Express lanes are long-range advisory edges from one cell to a distant
specialist cell. They do not mutate topology, call solvers, write MAGMA, or
skip gates. The output is a deterministic route-choice document intended for a
future MAGMA receipt producer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence


HEX_EXPRESS_LANE_PLAN_SCHEMA_VERSION = "hex_express_lane_plan.v1"

COST_CLASSES = ("low", "medium", "high")
_COST_RANK = {"low": 0, "medium": 1, "high": 2}
_CELL_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_EDGE_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_TAG_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class HexExpressLaneError(ValueError):
    """Raised when express-lane planning would be unsafe or ambiguous."""


@dataclass(frozen=True)
class ExpressLaneEdge:
    edge_id: str
    source_cell_id: str
    target_cell_id: str
    capability_tags: Sequence[str]
    trust_score: float
    freshness_age_days: int
    cost_class: str = "medium"
    rationale: str = ""
    receipt_required: bool = True
    no_runtime_mutation: bool = True
    gate_skip_authority: bool = False
    solver_call_authority: bool = False
    clinical_decision_authority: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        _validate_edge(self)
        return {
            "edge_id": self.edge_id,
            "source_cell_id": self.source_cell_id,
            "target_cell_id": self.target_cell_id,
            "capability_tags": list(_normalize_tags(self.capability_tags)),
            "trust_score": float(self.trust_score),
            "freshness_age_days": int(self.freshness_age_days),
            "cost_class": self.cost_class,
            "rationale": self.rationale,
            "receipt_required": self.receipt_required,
            "no_runtime_mutation": self.no_runtime_mutation,
            "gate_skip_authority": self.gate_skip_authority,
            "solver_call_authority": self.solver_call_authority,
            "clinical_decision_authority": self.clinical_decision_authority,
            "metadata": _json_safe_mapping(self.metadata),
        }


@dataclass(frozen=True)
class ExpressLaneRequest:
    source_cell_id: str
    intent_tags: Sequence[str]
    required_capability_tags: Sequence[str]
    trust_min: float = 0.85
    freshness_max_age_days: int = 30
    cost_cap: str = "medium"
    allow_clinical_decision: bool = False

    def to_dict(self) -> dict[str, Any]:
        _validate_request(self)
        return {
            "source_cell_id": self.source_cell_id,
            "intent_tags": list(_normalize_tags(self.intent_tags)),
            "required_capability_tags": list(
                _normalize_tags(self.required_capability_tags)
            ),
            "trust_min": float(self.trust_min),
            "freshness_max_age_days": int(self.freshness_max_age_days),
            "cost_cap": self.cost_cap,
            "allow_clinical_decision": self.allow_clinical_decision,
        }


def plan_express_lane(
    request: ExpressLaneRequest | Mapping[str, Any],
    edges: Sequence[ExpressLaneEdge | Mapping[str, Any]],
    *,
    known_cell_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Select a deterministic long-range edge or explain why none is usable."""

    req = _coerce_request(request)
    _validate_request(req)
    known = _normalize_known_cells(known_cell_ids)
    if known is not None and req.source_cell_id not in known:
        raise HexExpressLaneError(f"unknown source_cell_id: {req.source_cell_id}")

    normalized_edges = [_coerce_edge(edge) for edge in edges]
    seen: set[str] = set()
    candidates: list[ExpressLaneEdge] = []
    rejected: list[dict[str, Any]] = []
    for edge in normalized_edges:
        _validate_edge(edge)
        if edge.edge_id in seen:
            raise HexExpressLaneError(f"duplicate edge_id: {edge.edge_id}")
        seen.add(edge.edge_id)
        if known is not None:
            for field_name, cell_id in (
                ("source_cell_id", edge.source_cell_id),
                ("target_cell_id", edge.target_cell_id),
            ):
                if cell_id not in known:
                    raise HexExpressLaneError(f"unknown {field_name}: {cell_id}")
        reason = _edge_rejection_reason(req, edge)
        if reason:
            rejected.append(
                {
                    "edge_id": edge.edge_id,
                    "target_cell_id": edge.target_cell_id,
                    "reason": reason,
                }
            )
        else:
            candidates.append(edge)

    selected = _select_best(candidates)
    plan = {
        "schema_version": HEX_EXPRESS_LANE_PLAN_SCHEMA_VERSION,
        "source_cell_id": req.source_cell_id,
        "selected": selected is not None,
        "route": _route_for(req, selected) if selected else None,
        "rejected_edges": sorted(rejected, key=lambda item: item["edge_id"]),
        "request": req.to_dict(),
        "no_runtime_mutation": True,
        "gate_skip_authority": False,
        "solver_call_authority": False,
        "clinical_decision_authority": False,
        "receipt_required": True,
    }
    plan["plan_id"] = _compute_plan_id(plan)
    return plan


def _route_for(req: ExpressLaneRequest, edge: ExpressLaneEdge) -> dict[str, Any]:
    matched_tags = sorted(
        set(_normalize_tags(edge.capability_tags))
        & (
            set(_normalize_tags(req.intent_tags))
            | set(_normalize_tags(req.required_capability_tags))
        )
    )
    return {
        "edge_id": edge.edge_id,
        "source_cell_id": edge.source_cell_id,
        "target_cell_id": edge.target_cell_id,
        "matched_capability_tags": matched_tags,
        "trust_score": float(edge.trust_score),
        "freshness_age_days": int(edge.freshness_age_days),
        "cost_class": edge.cost_class,
        "rationale": edge.rationale,
        "selection_reason": "express_lane_constraints_satisfied",
        "receipt_required": True,
        "no_runtime_mutation": True,
        "gate_skip_authority": False,
        "solver_call_authority": False,
        "clinical_decision_authority": False,
    }


def _edge_rejection_reason(req: ExpressLaneRequest, edge: ExpressLaneEdge) -> str:
    if edge.source_cell_id != req.source_cell_id:
        return "source_cell_mismatch"
    if edge.target_cell_id == req.source_cell_id:
        return "target_is_source_cell"
    if not edge.receipt_required:
        return "receipt_not_required"
    if not edge.no_runtime_mutation:
        return "runtime_mutation_authority"
    if edge.gate_skip_authority:
        return "gate_skip_authority"
    if edge.solver_call_authority:
        return "solver_call_authority"
    if edge.clinical_decision_authority or req.allow_clinical_decision:
        return "clinical_decision_authority"
    if edge.trust_score < req.trust_min:
        return "trust_below_minimum"
    if edge.freshness_age_days > req.freshness_max_age_days:
        return "freshness_too_old"
    if _COST_RANK[edge.cost_class] > _COST_RANK[req.cost_cap]:
        return "cost_above_cap"
    required = set(_normalize_tags(req.required_capability_tags))
    available = set(_normalize_tags(edge.capability_tags))
    if required and not required.issubset(available):
        return "missing_required_capability"
    intent = set(_normalize_tags(req.intent_tags))
    if intent and not (intent & available):
        return "intent_capability_mismatch"
    return ""


def _select_best(candidates: Sequence[ExpressLaneEdge]) -> ExpressLaneEdge | None:
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda edge: (
            -float(edge.trust_score),
            int(edge.freshness_age_days),
            _COST_RANK[edge.cost_class],
            edge.target_cell_id,
            edge.edge_id,
        ),
    )[0]


def _coerce_request(value: ExpressLaneRequest | Mapping[str, Any]) -> ExpressLaneRequest:
    if isinstance(value, ExpressLaneRequest):
        return value
    if not isinstance(value, Mapping):
        raise HexExpressLaneError("express lane request must be an object")
    return ExpressLaneRequest(
        source_cell_id=str(value.get("source_cell_id") or ""),
        intent_tags=_optional_sequence(value, "intent_tags"),
        required_capability_tags=_optional_sequence(value, "required_capability_tags"),
        trust_min=_coerce_float(value.get("trust_min", 0.85), "trust_min"),
        freshness_max_age_days=_coerce_non_negative_int(
            value.get("freshness_max_age_days", 30),
            "freshness_max_age_days",
        ),
        cost_cap=str(value.get("cost_cap") or "medium"),
        allow_clinical_decision=bool(value.get("allow_clinical_decision", False)),
    )


def _coerce_edge(value: ExpressLaneEdge | Mapping[str, Any]) -> ExpressLaneEdge:
    if isinstance(value, ExpressLaneEdge):
        return value
    if not isinstance(value, Mapping):
        raise HexExpressLaneError("express lane edge must be an object")
    metadata = value.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, Mapping):
        raise HexExpressLaneError("metadata must be an object")
    return ExpressLaneEdge(
        edge_id=str(value.get("edge_id") or ""),
        source_cell_id=str(value.get("source_cell_id") or ""),
        target_cell_id=str(value.get("target_cell_id") or ""),
        capability_tags=_optional_sequence(value, "capability_tags"),
        trust_score=_coerce_float(value.get("trust_score"), "trust_score"),
        freshness_age_days=_coerce_non_negative_int(
            value.get("freshness_age_days"),
            "freshness_age_days",
        ),
        cost_class=str(value.get("cost_class") or "medium"),
        rationale=str(value.get("rationale") or ""),
        receipt_required=bool(value.get("receipt_required", True)),
        no_runtime_mutation=bool(value.get("no_runtime_mutation", True)),
        gate_skip_authority=bool(value.get("gate_skip_authority", False)),
        solver_call_authority=bool(value.get("solver_call_authority", False)),
        clinical_decision_authority=bool(
            value.get("clinical_decision_authority", False)
        ),
        metadata=metadata,
    )


def _validate_request(request: ExpressLaneRequest) -> None:
    _validate_cell_id(request.source_cell_id, "source_cell_id")
    _normalize_tags(request.intent_tags)
    required = _normalize_tags(request.required_capability_tags)
    if not required:
        raise HexExpressLaneError("required_capability_tags must not be empty")
    _validate_unit_float(request.trust_min, "trust_min")
    if request.freshness_max_age_days < 0:
        raise HexExpressLaneError("freshness_max_age_days must be non-negative")
    if request.cost_cap not in COST_CLASSES:
        raise HexExpressLaneError(f"unknown cost_cap: {request.cost_cap}")
    if request.allow_clinical_decision:
        raise HexExpressLaneError(
            "express lanes cannot carry clinical decision authority"
        )


def _validate_edge(edge: ExpressLaneEdge) -> None:
    if not _EDGE_ID_RE.fullmatch(edge.edge_id):
        raise HexExpressLaneError(f"invalid edge_id: {edge.edge_id}")
    _validate_cell_id(edge.source_cell_id, "source_cell_id")
    _validate_cell_id(edge.target_cell_id, "target_cell_id")
    if edge.source_cell_id == edge.target_cell_id:
        raise HexExpressLaneError("target_cell_id must differ from source_cell_id")
    if not _normalize_tags(edge.capability_tags):
        raise HexExpressLaneError("capability_tags must not be empty")
    _validate_unit_float(edge.trust_score, "trust_score")
    if edge.freshness_age_days < 0:
        raise HexExpressLaneError("freshness_age_days must be non-negative")
    if edge.cost_class not in COST_CLASSES:
        raise HexExpressLaneError(f"unknown cost_class: {edge.cost_class}")
    if not edge.receipt_required:
        raise HexExpressLaneError("receipt_required must be true")
    if not edge.no_runtime_mutation:
        raise HexExpressLaneError("no_runtime_mutation must be true")
    if edge.gate_skip_authority:
        raise HexExpressLaneError("gate_skip_authority must be false")
    if edge.solver_call_authority:
        raise HexExpressLaneError("solver_call_authority must be false")
    if edge.clinical_decision_authority:
        raise HexExpressLaneError("clinical_decision_authority must be false")
    for flag in (
        "runtime_authority",
        "storage_write_authority",
        "bridge_write_authority",
        "promotion_authority",
    ):
        if edge.metadata.get(flag) is True:
            raise HexExpressLaneError(f"metadata cannot carry {flag}=true")


def _validate_cell_id(value: str, label: str) -> None:
    if not _CELL_ID_RE.fullmatch(value):
        raise HexExpressLaneError(f"invalid {label}: {value}")


def _normalize_tags(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise HexExpressLaneError("tags must be a list of strings")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise HexExpressLaneError("tags must contain only strings")
        tag = value.strip().lower()
        if not _TAG_RE.fullmatch(tag):
            raise HexExpressLaneError(f"invalid tag: {value}")
        normalized.append(tag)
    return tuple(sorted(dict.fromkeys(normalized)))


def _optional_sequence(value: Mapping[str, Any], key: str) -> Sequence[str]:
    raw = value.get(key, ())
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise HexExpressLaneError(f"{key} must be a list of strings")
    return raw


def _coerce_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise HexExpressLaneError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise HexExpressLaneError(f"{label} must be finite")
    return result


def _validate_unit_float(value: float, label: str) -> None:
    if not (math.isfinite(float(value)) and 0.0 <= float(value) <= 1.0):
        raise HexExpressLaneError(f"{label} must be 0..1")


def _coerce_non_negative_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise HexExpressLaneError(f"{label} must be an integer") from exc
    if result < 0:
        raise HexExpressLaneError(f"{label} must be non-negative")
    return result


def _normalize_known_cells(values: Sequence[str] | None) -> set[str] | None:
    if values is None:
        return None
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise HexExpressLaneError("known_cell_ids must be a list of strings")
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise HexExpressLaneError("known_cell_ids must contain only strings")
        _validate_cell_id(value, "known_cell_id")
        result.add(value)
    return result


def _compute_plan_id(plan: Mapping[str, Any]) -> str:
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return "hexexpr_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _json_safe_mapping(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe_value(value) for key, value in metadata.items()}


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    return value
