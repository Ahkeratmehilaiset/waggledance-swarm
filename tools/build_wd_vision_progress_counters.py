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
import math
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
    manifest: Any,
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build stable operator counters from a capability manifest mapping."""

    blockers = _manifest_blockers(manifest)
    manifest_mapping = manifest if isinstance(manifest, Mapping) else {}
    raw_capabilities = manifest_mapping.get("capabilities", [])
    capabilities = (
        [item for item in raw_capabilities if isinstance(item, Mapping)]
        if isinstance(raw_capabilities, list)
        else []
    )
    capability_count = len(capabilities)
    status_counts = _status_counts(manifest_mapping, capabilities)
    raw_claim_safe_count = sum(
        1 for capability in capabilities
        if capability.get("claim_safe") is True
    )
    claim_safe_count = raw_claim_safe_count if not blockers else 0
    proof_ok_count = sum(
        1 for capability in capabilities
        if _proof_ok(capability)
    )
    if blockers:
        unsafe_literal_claim_ids = [
            str(capability.get("capability_id") or "unknown")
            for capability in capabilities
        ]
    else:
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
        "source_schema_version": manifest_mapping.get("schema_version"),
        "ok": not blockers,
        "blockers": blockers,
        "summary": {
            "capability_count": capability_count,
            "status_counts": status_counts,
            "claim_safe_count": claim_safe_count,
            "unsafe_literal_claim_count": len(unsafe_literal_claim_ids),
            "unsafe_literal_claim_ids": unsafe_literal_claim_ids,
            "all_literal_claims_safe": not blockers
            and claim_safe_count == capability_count
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
        "evidence_paths": _evidence_paths(capability),
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
        # Optional local opt-in first-hop coverage measurement. Present only when
        # the manifest ran the proof (env flag on); absent by default. Surface the
        # safe scalar fields; the route-order counter derives measurement
        # availability fail-closed and never upgrades the claim from these.
        first_hop = _mapping(proof.get("first_hop_coverage"))
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
            "first_hop_coverage_present": bool(proof.get("first_hop_coverage")),
            "first_hop_coverage_available": (
                first_hop.get("coverage_measurement_available") is True
            ),
            "first_hop_coverage_ratio": first_hop.get(
                "authoritative_first_hop_coverage"
            ),
            "first_hop_declares_order": (
                first_hop.get("capsule_declares_authoritative_order") is True
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
        # Optional local opt-in per-query receipt coverage measurement. Present
        # only when the manifest ran the proof (env flag on); absent by default.
        # Surface the safe scalar fields; the claim gate derives
        # measurement_available fail-closed from these (never upgrades satisfied).
        coverage = _mapping(proof.get("per_query_receipt_coverage"))
        return {
            "solver_call_trace_receipt_bound": (
                proof.get("solver_call_trace_receipt_bound") is True
            ),
            "receipt_count": _int_value(proof.get("receipt_count")),
            "default_sink_required": proof.get("default_sink_required") is True,
            "per_query_receipt_coverage_present": bool(
                proof.get("per_query_receipt_coverage")
            ),
            "per_query_receipt_coverage_ok": coverage.get("ok") is True,
            "per_query_receipt_coverage_raw_payload_leak_check": (
                coverage.get("raw_payload_leak_check") is True
            ),
            "per_query_receipt_coverage_ratio": coverage.get(
                "receipt_coverage_ratio"
            ),
            "per_query_receipt_coverage_all_bound": (
                coverage.get("all_queries_receipt_bound") is True
            ),
            "per_query_receipt_coverage_default_sink_required": (
                coverage.get("default_sink_required") is True
            ),
            "per_query_receipt_coverage_default_runtime_emission_changed": (
                coverage.get("default_runtime_receipt_emission_changed") is True
            ),
        }
    if capability_id == "low_risk_autonomy_loop":
        real_loop = _mapping(proof.get("real_loop_dry_run"))
        chain = _mapping(real_loop.get("chain"))
        authority = _mapping(real_loop.get("authority_boundary"))
        control_plane = _mapping(real_loop.get("control_plane"))
        table_counts = _mapping(control_plane.get("table_counts"))
        # Optional local opt-in repeat-window trend measurement. Present only when
        # the manifest ran the proof (env flag on); absent by default. Surface the
        # safe scalar fields; the trend counter derives availability fail-closed
        # and NEVER upgrades the low-risk claim from these.
        trend = _mapping(proof.get("repeat_window_trend"))
        # The recursive _nested_flag root authority scan MUST exclude the
        # measurement-only trend subtree: otherwise a bare authority key nested
        # anywhere under repeat_window_trend (e.g. a forged per-window
        # authority_boundary) would flip the real low-risk gate (#1271 tools forge:
        # the renamed surfaced field was not enough - the whole subtree must be out
        # of scope of the recursive scan).
        proof_for_authority = (
            {k: v for k, v in proof.items() if k != "repeat_window_trend"}
            if isinstance(proof, Mapping)
            else proof
        )
        return {
            "runtime_authority_granted": _nested_flag(
                proof_for_authority,
                "runtime_authority_granted",
            ),
            "external_writes_applied": _nested_flag(
                proof_for_authority,
                "external_writes_applied",
            ),
            "operator_visible_metrics": _nested_flag(
                proof_for_authority,
                "operator_visible_metrics",
            ),
            "real_loop_report_ok": real_loop.get("ok") is True,
            "real_loop_claim_label": str(real_loop.get("claim_label") or ""),
            "measured_auto_promoted_solver_count": _int_value(
                chain.get("auto_promoted_solver_count")
            ),
            "measured_auto_promoted_run_count": _int_value(
                chain.get("auto_promoted_run_count")
            ),
            "measured_provider_jobs_created": _int_value(
                table_counts.get("provider_jobs")
            ),
            "measured_builder_jobs_created": _int_value(
                table_counts.get("builder_jobs")
            ),
            "dry_run_runtime_authority_granted": (
                authority.get("runtime_authority_granted") is True
            ),
            "dry_run_external_writes_applied": (
                authority.get("external_writes_applied") is True
            ),
            # Full-coverage guard: ANY authority_boundary axis being True (covers
            # all emitted axes - production_control_plane_touched,
            # production_scheduler_enqueue, gate_skip_authority,
            # operator_gate_bypassed, fast_track_priority, etc.) so no
            # hand-enumerated subset can leave a fail-open gap.
            "dry_run_any_authority_flag": any(
                v is True for v in authority.values()
            ),
            "repeat_window_trend_present": bool(proof.get("repeat_window_trend")),
            "repeat_window_trend_ok": trend.get("ok") is True,
            "repeat_window_trend_deterministic": trend.get("deterministic") is True,
            "repeat_window_trend_evidence_present": (
                trend.get("evidence_present") is True
            ),
            "repeat_window_trend_all_runs_ok": trend.get("all_runs_ok") is True,
            "repeat_window_trend_any_guardrail_tripped": (
                trend.get("any_guardrail_tripped") is True
            ),
            "repeat_window_trend_promotion_stable": (
                trend.get("promoted_solver_count_stable") is True
            ),
            # PREFIXED authority flags (the trend aggregate prefixes them so the
            # root authority scan above never sees them); re-derived here so trend
            # availability independently requires no authority was granted.
            "repeat_window_trend_runtime_authority_granted": (
                trend.get("trend_runtime_authority_granted") is True
            ),
            "repeat_window_trend_external_writes_applied": (
                trend.get("trend_external_writes_applied") is True
            ),
            "repeat_window_trend_window_size": trend.get("window_size"),
            "repeat_window_trend_promotion_count_min": trend.get(
                "promoted_solver_count_min"
            ),
        }
    if capability_id == "hexagonal_upgrades":
        # Path-free reviewer summary (merged renderer), present only when the
        # manifest stored it. Measurement-only safe scalars.
        reviewer = _mapping(proof.get("reviewer_summary"))
        # Exclude the measurement-only reviewer_summary subtree from the recursive
        # authority/mutation scans so a nested field there can never couple into the
        # real hex-upgrade flags (recursive-scan-coupling safety, #1271).
        proof_for_authority = (
            {k: v for k, v in proof.items() if k != "reviewer_summary"}
            if isinstance(proof, Mapping)
            else proof
        )
        return {
            "no_runtime_mutation": _nested_flag(
                proof_for_authority, "no_runtime_mutation"
            ),
            "runtime_authority_changed": _nested_flag(
                proof_for_authority,
                "runtime_authority_changed",
            ),
            "shadow_to_candidate_transition_count": 0,
            "reviewer_summary_present": bool(proof.get("reviewer_summary")),
            # Surface the COMPONENT booleans (strict is True) so the consumer can
            # RE-DERIVE the composites itself - it must NOT trust the aggregate's
            # own review_clean/all_checks_match (#1274 tools forge: an inconsistent
            # aggregate with a component False but composite True must fail closed).
            "reviewer_summary_verdict_ok": reviewer.get("verdict_ok") is True,
            "reviewer_summary_path_free_verified": (
                reviewer.get("path_free_verified") is True
            ),
            "reviewer_summary_source_contract_match": (
                reviewer.get("source_contract_match") is True
            ),
            "reviewer_summary_rebuilt_index_entry_match": (
                reviewer.get("rebuilt_index_entry_match") is True
            ),
            "reviewer_summary_digest_all_match": (
                reviewer.get("digest_all_match") is True
            ),
            "reviewer_summary_size_all_match": (
                reviewer.get("size_all_match") is True
            ),
            "reviewer_summary_schema_version_all_match": (
                reviewer.get("schema_version_all_match") is True
            ),
            "reviewer_summary_blocker_count": reviewer.get("blocker_count"),
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
    receipt_claim_gate_satisfied = (
        deterministic.get("magma_execution_receipt_claimed") is True
        and magma.get("solver_call_trace_receipt_bound") is True
        and magma.get("default_sink_required") is True
    )
    # Per-query receipt coverage is a LOCAL opt-in measurement (off by default).
    # It is surfaced as measurement-only evidence and is DERIVED fail-closed; it
    # NEVER influences satisfied/current_value (those stay on the real claim
    # gate), so a 100% local measurement cannot upgrade the claim.
    coverage_ratio = magma.get("per_query_receipt_coverage_ratio")
    coverage_ratio_valid = (
        isinstance(coverage_ratio, (int, float))
        and not isinstance(coverage_ratio, bool)
        and math.isfinite(coverage_ratio)
        and 0.0 <= float(coverage_ratio) <= 1.0
    )
    coverage_measurement_available = (
        magma.get("per_query_receipt_coverage_present") is True
        and magma.get("per_query_receipt_coverage_ok") is True
        and magma.get("per_query_receipt_coverage_raw_payload_leak_check") is True
        and coverage_ratio_valid
    )
    measured_coverage_percent = (
        round(float(coverage_ratio) * 100.0, 2)
        if coverage_measurement_available
        else None
    )
    # First-hop authoritative coverage is a LOCAL opt-in measurement (off by
    # default), surfaced as measurement-only evidence and DERIVED fail-closed; it
    # NEVER influences current_value/satisfied (those stay on the existing
    # hex-mesh authoritative_first_hop_safe claim), so a 100% local measurement
    # cannot upgrade the claim. capsule with no declared order -> unavailable.
    first_hop_ratio = hex_mesh.get("first_hop_coverage_ratio")
    first_hop_ratio_valid = (
        isinstance(first_hop_ratio, (int, float))
        and not isinstance(first_hop_ratio, bool)
        and math.isfinite(first_hop_ratio)
        and 0.0 <= float(first_hop_ratio) <= 1.0
    )
    first_hop_measurement_available = (
        hex_mesh.get("first_hop_coverage_present") is True
        and hex_mesh.get("first_hop_coverage_available") is True
        and hex_mesh.get("first_hop_declares_order") is True
        and first_hop_ratio_valid
    )
    measured_first_hop_authoritative_percent = (
        round(float(first_hop_ratio) * 100.0, 2)
        if first_hop_measurement_available
        else None
    )
    low_risk_real_loop_guardrail_tripped = (
        low_risk.get("runtime_authority_granted") is True
        # Full authority_boundary coverage (any axis True) - replaces the
        # hand-enumerated subset so no ungated axis can leave a fail-open.
        or low_risk.get("dry_run_any_authority_flag") is True
        or low_risk.get("dry_run_runtime_authority_granted") is True
        or low_risk.get("dry_run_external_writes_applied") is True
        or _int_value(low_risk.get("measured_provider_jobs_created")) > 0
        or _int_value(low_risk.get("measured_builder_jobs_created")) > 0
    )
    low_risk_real_loop_promotions = _int_value(
        low_risk.get("measured_auto_promoted_solver_count")
    )
    low_risk_real_loop_satisfied = (
        low_risk.get("real_loop_report_ok") is True
        and low_risk_real_loop_promotions >= 1
        and not low_risk_real_loop_guardrail_tripped
    )
    # Repeat-window trend is a LOCAL opt-in measurement (off by default), surfaced
    # as measurement-only evidence DERIVED fail-closed. It NEVER influences the
    # end_to_end_gated_promotions_total satisfied/current_value above - a stable
    # 100% trend is reproducibility evidence only, never a claim upgrade.
    from tools.run_low_risk_real_loop_repeat_window_trend import (
        MAX_WINDOW as _trend_max_window,
    )

    trend_window_size = low_risk.get("repeat_window_trend_window_size")
    trend_window_valid = (
        isinstance(trend_window_size, int)
        and not isinstance(trend_window_size, bool)
        and 2 <= trend_window_size <= _trend_max_window
    )
    repeat_window_trend_available = (
        low_risk.get("repeat_window_trend_present") is True
        and low_risk.get("repeat_window_trend_ok") is True
        and low_risk.get("repeat_window_trend_deterministic") is True
        and low_risk.get("repeat_window_trend_evidence_present") is True
        and low_risk.get("repeat_window_trend_all_runs_ok") is True
        and low_risk.get("repeat_window_trend_promotion_stable") is True
        and low_risk.get("repeat_window_trend_any_guardrail_tripped") is not True
        # Independently re-derive (do not trust evidence_present alone): a forged
        # aggregate that decouples these must still fail closed.
        and low_risk.get("repeat_window_trend_runtime_authority_granted") is not True
        and low_risk.get("repeat_window_trend_external_writes_applied") is not True
        and trend_window_valid
    )
    trend_promotion_min = low_risk.get("repeat_window_trend_promotion_count_min")
    measured_stable_promotion_count = (
        trend_promotion_min
        if (repeat_window_trend_available and isinstance(trend_promotion_min, int)
            and not isinstance(trend_promotion_min, bool))
        else None
    )
    # Hex-subdivision reviewer summary: a path-free measurement-only surface from
    # the merged renderer. Consumer re-derives each field fail-closed (does NOT
    # blindly trust the manifest aggregate); it NEVER upgrades the hexagonal
    # claim. Availability requires the summary present AND its own path-free check.
    hex_reviewer_present = hex_upgrades.get("reviewer_summary_present") is True
    hex_reviewer_path_free = (
        hex_upgrades.get("reviewer_summary_path_free_verified") is True
    )
    hex_reviewer_available = hex_reviewer_present and hex_reviewer_path_free
    # RE-DERIVE the composites from the COMPONENT booleans (do NOT read the
    # aggregate's own review_clean/all_checks_match - an inconsistent aggregate
    # with a component False but composite True must fail closed). blocker_count
    # must be a strict 0 int (a malformed/non-zero value is not clean).
    # all_checks_match is the COMPLETE check composite: the three artifact-check
    # maps AND the source-contract AND rebuilt-index-entry component checks.
    hex_reviewer_all_checks_match = bool(
        hex_upgrades.get("reviewer_summary_digest_all_match") is True
        and hex_upgrades.get("reviewer_summary_size_all_match") is True
        and hex_upgrades.get("reviewer_summary_schema_version_all_match") is True
        and hex_upgrades.get("reviewer_summary_source_contract_match") is True
        and hex_upgrades.get("reviewer_summary_rebuilt_index_entry_match") is True
    )
    # blocker_count must be a STRICT int zero - 0.0 / 0j / bool must fail closed.
    _hex_blocker_count = hex_upgrades.get("reviewer_summary_blocker_count")
    _hex_blocker_zero = (
        isinstance(_hex_blocker_count, int)
        and not isinstance(_hex_blocker_count, bool)
        and _hex_blocker_count == 0
    )
    hex_reviewer_review_clean = bool(
        hex_reviewer_available
        and hex_upgrades.get("reviewer_summary_verdict_ok") is True
        and hex_reviewer_all_checks_match
        and _hex_blocker_zero
    )
    return {
        "authoritative_first_hop_route_order_coverage": {
            "current_value": 1.0
            if hex_mesh.get("authoritative_first_hop_safe") is True
            else 0.0,
            "target_value": 1.0,
            "satisfied": hex_mesh.get("authoritative_first_hop_safe") is True,
            "coverage_measurement_available": first_hop_measurement_available,
            "measured_first_hop_authoritative_percent": (
                measured_first_hop_authoritative_percent
            ),
            "measurement_basis": (
                "v1_first_hop_authoritative_order"
                if first_hop_measurement_available
                else "manifest_hex_mesh_flags"
            ),
        },
        "per_query_receipt_claim_gate": {
            "current_value": receipt_claim_gate_satisfied,
            "target_value": True,
            "satisfied": receipt_claim_gate_satisfied,
            "coverage_measurement_available": coverage_measurement_available,
            "measured_coverage_percent": measured_coverage_percent,
            "measurement_basis": (
                "v12_per_query_receipt_coverage_proof"
                if coverage_measurement_available
                else "manifest_claim_gate_flags"
            ),
        },
        "end_to_end_gated_promotions_total": {
            # Fail-closed: count promotions ONLY when the report is ok AND no
            # authority guardrail tripped (== low_risk_real_loop_satisfied). A
            # guardrail leak must drive current_value to 0, never a misleading 1.
            "current_value": (
                low_risk_real_loop_promotions
                if low_risk_real_loop_satisfied
                else 0
            ),
            "target_value": 1,
            "satisfied": low_risk_real_loop_satisfied,
            "guardrail_runtime_authority_granted": (
                low_risk.get("runtime_authority_granted") is True
            ),
            "guardrail_tripped": low_risk_real_loop_guardrail_tripped,
            "measurement_basis": "local_ephemeral_control_plane_real_loop",
            "claim_label": str(low_risk.get("real_loop_claim_label") or ""),
            # Derived from observed evidence (not a hardcoded "safe" literal).
            "production_authority_granted": bool(
                low_risk.get("runtime_authority_granted") is True
                or low_risk.get("dry_run_runtime_authority_granted") is True
            ),
        },
        "low_risk_real_loop_repeat_window_trend": {
            # Measurement-only reproducibility evidence (off by default). DERIVED
            # fail-closed and fully decoupled from end_to_end_gated_promotions_total
            # above - a stable 100% trend NEVER upgrades satisfied/current_value or
            # claim_safe.
            "trend_measurement_available": repeat_window_trend_available,
            "measured_window_size": (
                trend_window_size if repeat_window_trend_available else None
            ),
            "measured_stable_promotion_count": measured_stable_promotion_count,
            "promotion_count_stable": bool(
                repeat_window_trend_available
                and low_risk.get("repeat_window_trend_promotion_stable") is True
            ),
            "measurement_basis": (
                "v1_low_risk_real_loop_repeat_window"
                if repeat_window_trend_available
                else "manifest_real_loop_flags"
            ),
            "claim_safe": False,
        },
        "shadow_to_candidate_subdivision_transitions_total": {
            "current_value": _int_value(
                hex_upgrades.get("shadow_to_candidate_transition_count")
            ),
            "target_value": 1,
            "satisfied": False,
        },
        "hex_subdivision_reviewer_summary": {
            # Measurement-only path-free reviewer summary surface; DERIVED
            # fail-closed and fully decoupled - it NEVER upgrades any hexagonal
            # claim (no satisfied/current_value here that gates a claim).
            "reviewer_summary_available": hex_reviewer_available,
            "review_clean": hex_reviewer_review_clean,
            "path_free_verified": bool(
                hex_reviewer_present
                and hex_upgrades.get("reviewer_summary_path_free_verified") is True
            ),
            "verdict_ok": bool(
                hex_reviewer_available
                and hex_upgrades.get("reviewer_summary_verdict_ok") is True
            ),
            "all_checks_match": bool(
                hex_reviewer_available and hex_reviewer_all_checks_match
            ),
            "measurement_basis": (
                "v1_hex_subdivision_reviewer_summary"
                if hex_reviewer_available
                else "manifest_hex_upgrade_flags"
            ),
            "claim_safe": False,
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
    if isinstance(raw_counts, Mapping) and all(
        _is_non_negative_int(value) for value in raw_counts.values()
    ):
        return {str(key): _int_value(value) for key, value in raw_counts.items()}
    counter = Counter(str(item.get("status") or "unknown") for item in capabilities)
    return dict(sorted(counter.items()))


def _manifest_blockers(manifest: Any) -> list[str]:
    blockers: list[str] = []
    if not isinstance(manifest, Mapping):
        return ["manifest_not_mapping"]
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
    else:
        for key, value in summary["status_counts"].items():
            if not _is_non_negative_int(value):
                blockers.append(
                    f"summary_status_counts_{key}_not_non_negative_int"
                )
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


def _evidence_paths(capability: Mapping[str, Any]) -> list[str]:
    evidence = capability.get("evidence")
    if not isinstance(evidence, list):
        return []
    return [
        str(item["path"])
        for item in evidence
        if (
            isinstance(item, Mapping)
            and isinstance(item.get("path"), str)
            and item["path"].strip()
        )
    ]


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


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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
    if args.strict_claims and (
        not counters["ok"]
        or not counters["summary"]["all_literal_claims_safe"]
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
