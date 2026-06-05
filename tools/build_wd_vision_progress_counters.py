# SPDX-License-Identifier: BUSL-1.1
"""Read-only progress counters for the WD Image #1 vision.

The Image #1 capability manifest is deliberately conservative: it separates
storyboard wording from claim-safe repository evidence. This tool turns that
manifest into small, stable counters that can be used by bridge agents and
operators without repeating the literal storyboard claims as facts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.wd_image1_capability_manifest import build_manifest  # noqa: E402


SCHEMA_VERSION = "wd_image1_vision_progress_counters.v1"
CAPABILITY_PANEL_ORDER = {
    "hex_mesh_entry": 1,
    "deterministic_solver_first": 2,
    "magma_audit_log": 2,
    "low_risk_autonomy_loop": 3,
    "hexagonal_upgrades": 4,
    "future_waggledance_swarm": 6,
}


def build_vision_progress_counters(
    manifest: Mapping[str, Any],
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build stable operator counters from a capability manifest mapping."""

    blockers = _manifest_blockers(manifest)
    capabilities = [
        item for item in manifest.get("capabilities", [])
        if isinstance(item, Mapping)
    ]
    capability_count = len(capabilities)
    status_counts = _status_counts(manifest, capabilities)
    claim_safe_count = sum(
        1 for capability in capabilities
        if capability.get("claim_safe") is True
    )
    proof_ok_count = sum(
        1 for capability in capabilities
        if _proof_ok(capability)
    )
    unsafe_literal_claim_ids = [
        str(capability.get("capability_id"))
        for capability in capabilities
        if capability.get("claim_safe") is not True
    ]

    panel_counters = [
        _capability_counter(capability)
        for capability in capabilities
    ]
    milestone_counters = _milestone_counters(panel_counters)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc or _utc_now(),
        "source_schema_version": manifest.get("schema_version"),
        "ok": not blockers,
        "blockers": blockers,
        "summary": {
            "capability_count": capability_count,
            "status_counts": status_counts,
            "claim_safe_count": claim_safe_count,
            "unsafe_literal_claim_count": len(unsafe_literal_claim_ids),
            "unsafe_literal_claim_ids": unsafe_literal_claim_ids,
            "all_literal_claims_safe": claim_safe_count == capability_count
            and capability_count > 0,
            "proof_ok_count": proof_ok_count,
            "proof_ok_ratio": _ratio(proof_ok_count, capability_count),
            "literal_claim_safe_ratio": _ratio(
                claim_safe_count,
                capability_count,
            ),
            "production_safe_capability_count": claim_safe_count,
            "implemented_capability_count": int(status_counts.get("implemented", 0)),
        },
        "panel_counters": panel_counters,
        "milestone_counters": milestone_counters,
        "next_smallest_pr_count": sum(
            1 for capability in capabilities
            if bool(str(capability.get("next_smallest_pr") or "").strip())
        ),
        "guardrails": {
            "read_only": True,
            "external_writes_applied": False,
            "runtime_authority_changed": False,
            "claim_safe_flip_applied": False,
            "bridge_event_written": False,
            "github_mutation_performed": False,
        },
    }


def _capability_counter(capability: Mapping[str, Any]) -> dict[str, Any]:
    capability_id = str(capability.get("capability_id") or "unknown")
    proof = capability.get("proof")
    proof = proof if isinstance(proof, Mapping) else {}
    return {
        "capability_id": capability_id,
        "panel": CAPABILITY_PANEL_ORDER.get(capability_id),
        "status": str(capability.get("status") or "unknown"),
        "claim_safe": capability.get("claim_safe") is True,
        "proof_ok": _proof_ok(capability),
        "evidence_present_count": _evidence_present_count(capability),
        "evidence_total_count": _evidence_total_count(capability),
        "gap_count": _sequence_count(capability.get("gaps")),
        "next_smallest_pr_present": bool(
            str(capability.get("next_smallest_pr") or "").strip()
        ),
        "milestones": _extract_milestone_values(capability_id, proof),
    }


def _extract_milestone_values(
    capability_id: str,
    proof: Mapping[str, Any],
) -> dict[str, Any]:
    if capability_id == "hex_mesh_entry":
        config = _mapping(proof.get("current_config"))
        return {
            "authoritative_first_hop_safe": bool(
                proof.get("proves_every_query_first_enters_mesh")
            ),
            "literal_claim_safe": bool(proof.get("literal_claim_safe")),
            "pre_hex_step_count": _sequence_count(proof.get("pre_hex_steps")),
            "hybrid_retrieval_mode": config.get("hybrid_retrieval_mode"),
            "hex_mesh_enabled": config.get("hex_mesh_enabled") is True,
            "hybrid_retrieval_authoritative": (
                config.get("hybrid_retrieval_authoritative") is True
            ),
        }
    if capability_id == "deterministic_solver_first":
        return {
            "selected_solver_count": _sequence_count(
                proof.get("selected_solver_ids")
            ),
            "fallback_used": proof.get("fallback_used") is True,
            "magma_execution_receipt_claimed": (
                proof.get("magma_execution_receipt_claimed") is True
            ),
        }
    if capability_id == "magma_audit_log":
        return {
            "solver_call_trace_receipt_bound": (
                proof.get("solver_call_trace_receipt_bound") is True
            ),
            "receipt_count": _int_value(proof.get("receipt_count")),
            "default_sink_required": proof.get("default_sink_required") is True,
        }
    if capability_id == "low_risk_autonomy_loop":
        return {
            "runtime_authority_granted": _nested_flag(
                proof,
                "runtime_authority_granted",
            ),
            "external_writes_applied": _nested_flag(
                proof,
                "external_writes_applied",
            ),
            "operator_visible_metrics": _nested_flag(
                proof,
                "operator_visible_metrics",
            ),
        }
    if capability_id == "hexagonal_upgrades":
        return {
            "no_runtime_mutation": _nested_flag(proof, "no_runtime_mutation"),
            "runtime_authority_changed": _nested_flag(
                proof,
                "runtime_authority_changed",
            ),
            "shadow_to_candidate_transition_count": 0,
        }
    if capability_id == "future_waggledance_swarm":
        runtime_summary = _mapping(proof.get("runtime_evidence_summary"))
        return {
            "literal_future_claim_safe": (
                proof.get("literal_future_claim_safe") is True
            ),
            "future_claim_gate_satisfied": (
                proof.get("claim_gate_satisfied") is True
            ),
            "axis_count": _int_value(proof.get("axis_count")),
            "required_runtime_evidence_present": (
                runtime_summary.get("required_runtime_evidence_present") is True
            ),
            "runtime_evidence_axis_count": _int_value(
                runtime_summary.get("runtime_evidence_axis_count")
            ),
        }
    return {}


def _milestone_counters(panel_counters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_id = {
        str(counter.get("capability_id")): counter
        for counter in panel_counters
    }

    def milestones(capability_id: str) -> Mapping[str, Any]:
        counter = by_id.get(capability_id) or {}
        return _mapping(counter.get("milestones"))

    hex_mesh = milestones("hex_mesh_entry")
    deterministic = milestones("deterministic_solver_first")
    magma = milestones("magma_audit_log")
    low_risk = milestones("low_risk_autonomy_loop")
    hex_upgrades = milestones("hexagonal_upgrades")
    future = milestones("future_waggledance_swarm")
    return {
        "authoritative_first_hop_route_order_coverage": {
            "current_value": 1.0
            if hex_mesh.get("authoritative_first_hop_safe") is True
            else 0.0,
            "target_value": 1.0,
            "satisfied": hex_mesh.get("authoritative_first_hop_safe") is True,
        },
        "per_query_receipt_coverage_percent": {
            "current_value": 100.0
            if (
                deterministic.get("magma_execution_receipt_claimed") is True
                and magma.get("solver_call_trace_receipt_bound") is True
                and magma.get("default_sink_required") is True
            )
            else 0.0,
            "target_value": 100.0,
            "satisfied": magma.get("default_sink_required") is True,
        },
        "end_to_end_gated_promotions_total": {
            "current_value": 0,
            "target_value": 1,
            "satisfied": False,
            "guardrail_runtime_authority_granted": (
                low_risk.get("runtime_authority_granted") is True
            ),
        },
        "shadow_to_candidate_subdivision_transitions_total": {
            "current_value": _int_value(
                hex_upgrades.get("shadow_to_candidate_transition_count")
            ),
            "target_value": 1,
            "satisfied": False,
        },
        "future_claim_gate_satisfied": {
            "current_value": bool(future.get("future_claim_gate_satisfied")),
            "target_value": True,
            "satisfied": bool(future.get("future_claim_gate_satisfied")),
        },
    }


def _status_counts(
    manifest: Mapping[str, Any],
    capabilities: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    summary = manifest.get("summary")
    summary = summary if isinstance(summary, Mapping) else {}
    raw_counts = summary.get("status_counts")
    if isinstance(raw_counts, Mapping):
        return {str(key): _int_value(value) for key, value in raw_counts.items()}
    counter = Counter(str(item.get("status") or "unknown") for item in capabilities)
    return dict(sorted(counter.items()))


def _manifest_blockers(manifest: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if manifest.get("schema_version") != "wd_image1_capability_manifest.v1":
        blockers.append("unexpected_manifest_schema_version")
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list):
        blockers.append("capabilities_not_list")
        return blockers
    if not capabilities:
        blockers.append("capabilities_empty")
    summary = manifest.get("summary")
    if not isinstance(summary, Mapping):
        blockers.append("summary_not_mapping")
    elif not isinstance(summary.get("status_counts"), Mapping):
        blockers.append("summary_status_counts_not_mapping")
    for index, capability in enumerate(capabilities):
        if not isinstance(capability, Mapping):
            blockers.append(f"capability_{index}_not_mapping")
            continue
        capability_id = capability.get("capability_id")
        prefix = str(capability_id) if capability_id else f"capability_{index}"
        if not isinstance(capability_id, str) or not capability_id.strip():
            blockers.append(f"{prefix}_missing_capability_id")
        if not isinstance(capability.get("status"), str):
            blockers.append(f"{prefix}_missing_status")
        if not isinstance(capability.get("claim_safe"), bool):
            blockers.append(f"{prefix}_claim_safe_not_bool")
        proof = capability.get("proof")
        if not isinstance(proof, Mapping):
            blockers.append(f"{prefix}_proof_not_mapping")
        elif not isinstance(proof.get("ok"), bool):
            blockers.append(f"{prefix}_proof_ok_not_bool")
        evidence = capability.get("evidence")
        if not isinstance(evidence, list):
            blockers.append(f"{prefix}_evidence_not_list")
    return blockers


def _proof_ok(capability: Mapping[str, Any]) -> bool:
    proof = capability.get("proof")
    if not isinstance(proof, Mapping):
        return False
    return proof.get("ok") is True


def _evidence_total_count(capability: Mapping[str, Any]) -> int:
    evidence = capability.get("evidence")
    return len(evidence) if isinstance(evidence, list) else 0


def _evidence_present_count(capability: Mapping[str, Any]) -> int:
    evidence = capability.get("evidence")
    if not isinstance(evidence, list):
        return 0
    return sum(
        1 for item in evidence
        if isinstance(item, Mapping) and item.get("present") is True
    )


def _nested_flag(value: Any, field_name: str) -> bool:
    if isinstance(value, Mapping):
        if value.get(field_name) is True:
            return True
        return any(_nested_flag(item, field_name) for item in value.values())
    if isinstance(value, list):
        return any(_nested_flag(item, field_name) for item in value)
    return False


def _sequence_count(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int_value(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )


def _load_manifest(args: argparse.Namespace) -> Mapping[str, Any]:
    if args.manifest == "-":
        return json.loads(sys.stdin.read())
    if args.manifest:
        return json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    return build_manifest(Path(args.root))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit WD Image #1 vision progress counters as JSON.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root to inspect when --manifest is not supplied.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Optional manifest JSON path, or '-' to read from stdin.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON. Present for explicitness; JSON is the only output.",
    )
    parser.add_argument(
        "--strict-claims",
        action="store_true",
        help="Exit 2 if any literal image claim remains unsafe.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    counters = build_vision_progress_counters(_load_manifest(args))
    print(json.dumps(counters, indent=2, sort_keys=True))
    if args.strict_claims and not counters["summary"]["all_literal_claims_safe"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
