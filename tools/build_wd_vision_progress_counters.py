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
            "first_hop_denominator_scope": first_hop.get(
                "first_hop_denominator_scope"
            ),
            "first_hop_denominator_count": _int_value(
                first_hop.get("first_hop_denominator_count")
            ),
            "first_hop_gap_count": _int_value(
                first_hop.get("authoritative_first_hop_gap_count")
            ),
            "first_hop_declares_order": (
                first_hop.get("capsule_declares_authoritative_order") is True
            ),
            "first_hop_denominator_integrity_ok": (
                first_hop.get("denominator_is_all_non_cached_first_hops") is True
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
        # Deterministic real-loop manifest contribution: safe scalar
        # evidence-vs-authority counters from run_low_risk_autogrowth_real_loop_proof.
        # Measurement-only; the milestone counter re-derives availability fail-closed
        # and never upgrades claim_safe.
        real_loop_manifest = _mapping(
            proof.get("real_loop_manifest_contribution")
        )
        # Repeat-window trend measurement (DEFAULT-ON; opt OUT via the manifest env
        # flag). Present whenever the manifest ran the proof. Surface the safe
        # scalar fields; the trend counter derives availability fail-closed and
        # NEVER upgrades the low-risk claim from these.
        trend = _mapping(proof.get("repeat_window_trend"))
        # Path-free reviewer summary of the trend (merged #1284 renderer), present only
        # when the manifest stored it. Measurement-only safe scalars; consumer re-derives.
        trend_reviewer = _mapping(proof.get("repeat_window_trend_reviewer_summary"))
        # Path-free cross-consistency digest confirming the three low-risk views AGREE,
        # present only when the manifest stored it. Measurement-only safe scalars (derived
        # booleans only); the consumer re-derives cross_consistent from its COMPONENTS.
        cross = _mapping(proof.get("cross_consistency_digest"))
        # Path-free bridge-event TEMPLATE summary (#1291 wiring): a curated content-safe
        # projection (derived booleans only) of the cross-consistency digest bridge-event
        # template; present only when the manifest stored it. The consumer re-derives
        # template_clean from its COMPONENTS (never the template's own composite).
        cross_tpl = _mapping(
            proof.get("cross_consistency_digest_bridge_event_template")
        )
        # The recursive _nested_flag root authority scan MUST exclude the measurement-only
        # trend, trend-reviewer-summary, cross-consistency-digest AND its bridge-event
        # template-summary subtrees: otherwise a bare authority key nested anywhere under
        # them (e.g. a forged per-window authority_boundary) would flip the real low-risk
        # gate (#1271 tools forge: the renamed surfaced field was not enough - the whole
        # subtree must be out of scope of the recursive scan).
        proof_for_authority = (
            {
                k: v
                for k, v in proof.items()
                if k
                not in (
                    "real_loop_manifest_contribution",
                    "repeat_window_trend",
                    "repeat_window_trend_reviewer_summary",
                    "cross_consistency_digest",
                    "cross_consistency_digest_bridge_event_template",
                )
            }
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
            "real_loop_manifest_contribution_present": isinstance(
                proof.get("real_loop_manifest_contribution"), Mapping
            ),
            "real_loop_manifest_contribution_ok": (
                real_loop_manifest.get("ok") is True
            ),
            "real_loop_manifest_contribution_deterministic": (
                real_loop_manifest.get("deterministic") is True
            ),
            "real_loop_manifest_contribution_evidence_present": (
                real_loop_manifest.get("evidence_present") is True
            ),
            "real_loop_manifest_contribution_runtime_authority_granted": (
                real_loop_manifest.get("runtime_authority_granted") is True
            ),
            "real_loop_manifest_contribution_external_writes_applied": (
                real_loop_manifest.get("external_writes_applied") is True
            ),
            "real_loop_manifest_contribution_scheduler_enqueue": (
                real_loop_manifest.get("scheduler_enqueue") is True
            ),
            "real_loop_manifest_contribution_production_flip": (
                real_loop_manifest.get("production_flip") is True
            ),
            "real_loop_manifest_contribution_production_authority_granted": (
                real_loop_manifest.get("production_authority_granted") is True
            ),
            "real_loop_manifest_contribution_provider_calls": (
                real_loop_manifest.get("provider_calls")
            ),
            "real_loop_manifest_contribution_claim_safe": (
                real_loop_manifest.get("claim_safe") is True
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
            # Reviewer summary (#1284 renderer) COMPONENT scalars (strict is True) so the
            # consumer can RE-DERIVE its clean flag - never the summary's own
            # trend_review_clean composite (#1274). The summary is the CONSUMED aggregate
            # here, so its OWN components are surfaced (a forged summary inconsistent with
            # the trend must fail closed).
            "repeat_window_reviewer_summary_present": bool(
                proof.get("repeat_window_trend_reviewer_summary")
            ),
            "repeat_window_reviewer_summary_path_free_verified": (
                trend_reviewer.get("path_free_verified") is True
            ),
            "repeat_window_reviewer_summary_trend_present": (
                trend_reviewer.get("trend_present") is True
            ),
            "repeat_window_reviewer_summary_all_runs_ok": (
                trend_reviewer.get("all_runs_ok") is True
            ),
            "repeat_window_reviewer_summary_deterministic": (
                trend_reviewer.get("deterministic") is True
            ),
            "repeat_window_reviewer_summary_promotion_count_stable": (
                trend_reviewer.get("promotion_count_stable") is True
            ),
            "repeat_window_reviewer_summary_evidence_present": (
                trend_reviewer.get("evidence_present") is True
            ),
            "repeat_window_reviewer_summary_no_guardrail_tripped": (
                trend_reviewer.get("no_guardrail_tripped") is True
            ),
            "repeat_window_reviewer_summary_no_runtime_authority_granted": (
                trend_reviewer.get("no_runtime_authority_granted") is True
            ),
            "repeat_window_reviewer_summary_no_external_writes": (
                trend_reviewer.get("no_external_writes") is True
            ),
            "repeat_window_reviewer_summary_window_size_valid": (
                trend_reviewer.get("window_size_valid") is True
            ),
            "repeat_window_reviewer_summary_promotion_count_positive": (
                trend_reviewer.get("promotion_count_positive") is True
            ),
            "repeat_window_reviewer_summary_window_size": trend_reviewer.get(
                "window_size"
            ),
            "repeat_window_reviewer_summary_promotion_count_min": (
                trend_reviewer.get("promoted_solver_count_min")
            ),
            "repeat_window_reviewer_summary_promotion_count_max": (
                trend_reviewer.get("promoted_solver_count_max")
            ),
            # Refuse-to-certify if the summary self-declares claim_safe True.
            "repeat_window_reviewer_summary_self_claim_safe": (
                trend_reviewer.get("claim_safe") is True
            ),
            # Cross-consistency digest COMPONENT scalars (strict is True) so the consumer
            # can RE-DERIVE cross_consistent itself - it must NOT trust the digest's own
            # cross_consistent aggregate (#1274). A forged/inconsistent digest must fail
            # closed.
            "cross_consistency_digest_present": bool(
                proof.get("cross_consistency_digest")
            ),
            "cross_consistency_path_free_verified": (
                cross.get("path_free_verified") is True
            ),
            "cross_consistency_all_views_present": (
                cross.get("all_views_present") is True
            ),
            "cross_consistency_real_loop_clean": (
                cross.get("real_loop_clean") is True
            ),
            "cross_consistency_trend_clean": (
                cross.get("trend_clean") is True
            ),
            "cross_consistency_reviewer_clean": (
                cross.get("reviewer_clean") is True
            ),
            "cross_consistency_reviewer_matches_trend": (
                cross.get("reviewer_matches_trend") is True
            ),
            # Defensive: the digest's allowlist hardcodes claim_safe False, but a
            # forged/inconsistent digest self-declaring claim_safe True must
            # refuse-to-certify (measurement-only never upgrades a claim).
            "cross_consistency_self_claim_safe": (
                cross.get("claim_safe") is True
            ),
            # #1291 bridge-event template-summary components (re-derived; never the
            # template's own composite). The manifest stores no-authority axes as no_*
            # derived booleans (present-and-True == that axis was strictly False).
            "xcons_template_present": bool(
                proof.get("cross_consistency_digest_bridge_event_template")
            ),
            "xcons_template_available": cross_tpl.get("template_available") is True,
            "xcons_template_only": cross_tpl.get("template_only") is True,
            "xcons_template_no_runtime_authority_granted": (
                cross_tpl.get("no_runtime_authority_granted") is True
            ),
            "xcons_template_no_direct_bridge_write": (
                cross_tpl.get("no_direct_bridge_write") is True
            ),
            "xcons_template_no_bridge_event_written": (
                cross_tpl.get("no_bridge_event_written") is True
            ),
            "xcons_template_no_approval_granted": (
                cross_tpl.get("no_approval_granted") is True
            ),
            "xcons_template_cross_consistent": (
                cross_tpl.get("cross_consistent") is True
            ),
            "xcons_template_all_views_present": (
                cross_tpl.get("all_views_present") is True
            ),
            # the per-view component verdicts the template carries - required in
            # template_clean so a forged composite (cross_consistent True while a view
            # verdict is False) fails closed (#1274 inconsistent-aggregate).
            "xcons_template_real_loop_clean": (
                cross_tpl.get("real_loop_clean") is True
            ),
            "xcons_template_trend_clean": cross_tpl.get("trend_clean") is True,
            "xcons_template_reviewer_clean": cross_tpl.get("reviewer_clean") is True,
            "xcons_template_reviewer_matches_trend": (
                cross_tpl.get("reviewer_matches_trend") is True
            ),
            "xcons_template_path_free_verified": (
                cross_tpl.get("path_free_verified") is True
            ),
            "xcons_template_self_claim_safe": cross_tpl.get("claim_safe") is True,
        }
    if capability_id == "hexagonal_upgrades":
        # Path-free reviewer summary (merged renderer), present only when the
        # manifest stored it. Measurement-only safe scalars.
        reviewer = _mapping(proof.get("reviewer_summary"))
        # Measurement-only shadow-only invariant proof (AFFIRMATIVE evidence the
        # subdivision stays shadow-only). Surface the COMPONENT scalars so the
        # consumer can RE-DERIVE shadow_only_enforced itself - it must NOT trust the
        # proof's own invariant_holds aggregate.
        shadow_inv = _mapping(proof.get("shadow_only_invariant"))
        shadow_inv_block = _mapping(shadow_inv.get("invariant"))
        shadow_inv_replay = _mapping(shadow_inv.get("deterministic_replay"))
        # Path-free FINAL chain summary (merged #1276 renderer), present only when the
        # manifest stored it. Measurement-only safe scalars; the consumer re-derives.
        chain = _mapping(proof.get("chain_final_summary"))
        # Path-free cross-consistency digest (merged #1278), present only when the
        # manifest stored it. Measurement-only safe scalars; the consumer re-derives.
        cross = _mapping(proof.get("cross_consistency_digest"))
        # Curated ring-messaging + parent-child hierarchy summary (merged ring/hierarchy
        # wiring), present only when the manifest stored it. The manifest stores a
        # content-safe-by-construction summary (strict-bool coercions + an int), never
        # the raw proof; the consumer re-derives.
        ring = _mapping(proof.get("ring_hierarchy_summary"))
        # Exclude the measurement-only reviewer_summary, shadow_only_invariant,
        # chain_final_summary, cross_consistency_digest AND ring_hierarchy_summary
        # subtrees from the recursive authority/mutation scans so a nested field there
        # can never couple into the real hex-upgrade flags (recursive-scan coupling
        # safety, #1271).
        proof_for_authority = (
            {
                k: v
                for k, v in proof.items()
                if k
                not in (
                    "reviewer_summary",
                    "shadow_only_invariant",
                    "chain_final_summary",
                    "cross_consistency_digest",
                    "ring_hierarchy_summary",
                )
            }
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
            # Shadow-only invariant COMPONENT scalars (strict is True), plus the raw
            # transition count so the consumer can re-derive the strict-int-0 itself.
            # The aggregate is surfaced only so the consumer can prove it IGNORES it
            # (an inconsistent aggregate must still fail closed).
            "shadow_only_invariant_present": bool(proof.get("shadow_only_invariant")),
            "shadow_only_invariant_ok": shadow_inv.get("ok") is True,
            "shadow_only_invariant_holds": (
                shadow_inv_block.get("invariant_holds") is True
            ),
            "shadow_only_invariant_deterministic": (
                shadow_inv_replay.get("stable_identical") is True
            ),
            "shadow_only_artifact_ok": shadow_inv_block.get("artifact_ok") is True,
            "shadow_only_target_state_is_shadow": (
                shadow_inv_block.get("target_state_is_shadow") is True
            ),
            "shadow_only_no_runtime_mutation": (
                shadow_inv_block.get("no_runtime_mutation") is True
            ),
            "shadow_only_guardrails_all_clean": (
                shadow_inv_block.get("guardrails_all_clean") is True
            ),
            "shadow_only_transition_occurred": (
                shadow_inv_block.get("transition_occurred") is True
            ),
            "shadow_only_transition_count": shadow_inv_block.get(
                "shadow_to_candidate_subdivision_transitions_total"
            ),
            "shadow_only_invariant_claim_safe": (
                shadow_inv_block.get("claim_safe") is True
            ),
            # FINAL chain summary COMPONENT scalars (strict is True), plus level
            # counts and the raw blocker count so the consumer can RE-DERIVE
            # chain_clean itself - it must NOT trust the aggregate's own chain_clean
            # (#1274: an inconsistent aggregate with a component False but chain_clean
            # True must fail closed).
            "chain_final_summary_present": bool(proof.get("chain_final_summary")),
            "chain_final_summary_path_free_verified": (
                chain.get("path_free_verified") is True
            ),
            "chain_final_summary_levels_all_ok": (
                chain.get("chain_levels_all_ok") is True
            ),
            "chain_final_summary_levels_shape_ok": (
                chain.get("chain_levels_shape_ok") is True
            ),
            "chain_final_summary_artifact_verify_clean": (
                chain.get("artifact_verify_clean") is True
            ),
            "chain_final_summary_index_entry_verify_clean": (
                chain.get("index_entry_verify_clean") is True
            ),
            "chain_final_summary_deepest_verify_clean": (
                chain.get("deepest_verify_clean") is True
            ),
            "chain_final_summary_levels_total": chain.get("chain_levels_total"),
            "chain_final_summary_levels_present": chain.get("chain_levels_present"),
            "chain_final_summary_blocker_count": chain.get("total_blocker_count"),
            "chain_final_summary_warning_count": chain.get("total_warning_count"),
            # Defensive: the merged renderer's allowlist cannot emit claim_safe, but a
            # forged/inconsistent summary that self-declares claim_safe True must
            # refuse-to-certify (measurement-only never upgrades a claim).
            "chain_final_summary_self_claim_safe": (
                chain.get("claim_safe") is True
            ),
            # Cross-consistency digest (#1278) COMPONENT scalars (strict is True) so the
            # consumer can RE-DERIVE cross_consistent itself - it must NOT trust the
            # digest's own cross_consistent aggregate (#1274).
            "cross_consistency_digest_present": bool(
                proof.get("cross_consistency_digest")
            ),
            "cross_consistency_path_free_verified": (
                cross.get("path_free_verified") is True
            ),
            "cross_consistency_all_views_present": (
                cross.get("all_views_present") is True
            ),
            "cross_consistency_reviewer_clean": (
                cross.get("reviewer_clean") is True
            ),
            "cross_consistency_shadow_only_clean": (
                cross.get("shadow_only_clean") is True
            ),
            "cross_consistency_chain_summary_clean": (
                cross.get("chain_summary_clean") is True
            ),
            # Defensive: the digest's allowlist hardcodes claim_safe False, but a
            # forged/inconsistent digest self-declaring claim_safe True must
            # refuse-to-certify (measurement-only never upgrades a claim).
            "cross_consistency_self_claim_safe": (
                cross.get("claim_safe") is True
            ),
            # Ring-messaging + parent-child hierarchy summary COMPONENT scalars (strict
            # is True) so the consumer can RE-DERIVE the clean flag itself - it must NOT
            # trust the proof's own ok aggregate (#1274).
            "ring_hierarchy_present": isinstance(
                proof.get("ring_hierarchy_summary"), Mapping
            ),
            "ring_hierarchy_path_free_verified": (
                ring.get("path_free_verified") is True
            ),
            "ring_hierarchy_hierarchy_ok": ring.get("hierarchy_ok") is True,
            "ring_hierarchy_ring_boundary_ok": ring.get("ring_boundary_ok") is True,
            "ring_hierarchy_no_runtime_mutation": (
                ring.get("no_runtime_mutation") is True
            ),
            "ring_hierarchy_no_invalid_boundary_delivery": (
                ring.get("no_invalid_boundary_delivery") is True
            ),
            "ring_hierarchy_deterministic": ring.get("deterministic") is True,
            "ring_hierarchy_blocker_count": ring.get("blocker_count"),
            # Defensive: the curated summary never self-declares claim_safe, but a
            # forged/inconsistent summary that does must refuse-to-certify
            # (measurement-only never upgrades a claim) - mirrors the digest guard.
            "ring_hierarchy_self_claim_safe": ring.get("claim_safe") is True,
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
        and hex_mesh.get("first_hop_denominator_scope")
        == "all_non_cached_first_hops"
        and hex_mesh.get("first_hop_denominator_integrity_ok") is True
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
    # Deterministic real-loop manifest contribution: LOCAL measurement evidence from
    # the dedicated proof. Count evidence only when the proof is present, deterministic,
    # evidence_present, and every authority/write/scheduler/provider/claim-safe axis
    # stays closed. This is deliberately decoupled from
    # end_to_end_gated_promotions_total; it never grants runtime authority or upgrades
    # a literal claim.
    real_loop_manifest_provider_calls = low_risk.get(
        "real_loop_manifest_contribution_provider_calls"
    )
    real_loop_manifest_provider_calls_valid = (
        isinstance(real_loop_manifest_provider_calls, int)
        and not isinstance(real_loop_manifest_provider_calls, bool)
        and real_loop_manifest_provider_calls >= 0
    )
    real_loop_manifest_guardrail_tripped = bool(
        low_risk.get("real_loop_manifest_contribution_runtime_authority_granted")
        is True
        or low_risk.get("real_loop_manifest_contribution_external_writes_applied")
        is True
        or low_risk.get("real_loop_manifest_contribution_scheduler_enqueue")
        is True
        or low_risk.get("real_loop_manifest_contribution_production_flip") is True
        or low_risk.get(
            "real_loop_manifest_contribution_production_authority_granted"
        )
        is True
        or (
            real_loop_manifest_provider_calls_valid
            and real_loop_manifest_provider_calls > 0
        )
        or low_risk.get("real_loop_manifest_contribution_claim_safe") is True
    )
    real_loop_manifest_contribution_available = bool(
        low_risk.get("real_loop_manifest_contribution_present") is True
        and low_risk.get("real_loop_manifest_contribution_ok") is True
        and low_risk.get("real_loop_manifest_contribution_deterministic") is True
        and low_risk.get("real_loop_manifest_contribution_evidence_present") is True
        and real_loop_manifest_provider_calls_valid
        and not real_loop_manifest_guardrail_tripped
    )
    # Repeat-window trend is a LOCAL measurement (DEFAULT-ON; opt-out via env),
    # surfaced as measurement-only evidence DERIVED fail-closed. It NEVER influences
    # the end_to_end_gated_promotions_total satisfied/current_value above - a stable
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
    # Repeat-window trend REVIEWER SUMMARY (merged #1284 renderer, now manifest-stored).
    # The consumer RE-DERIVES its clean flag from the summary's COMPONENT booleans -
    # never the summary's own trend_review_clean composite (#1274). Refuse-to-certify on
    # a self-declared claim_safe. Measurement-only: NEVER upgrades the low-risk claim.
    rw_reviewer_present = (
        low_risk.get("repeat_window_reviewer_summary_present") is True
    )
    rw_reviewer_path_free = (
        low_risk.get("repeat_window_reviewer_summary_path_free_verified") is True
    )
    rw_reviewer_self_claim_safe = (
        low_risk.get("repeat_window_reviewer_summary_self_claim_safe") is True
    )
    rw_reviewer_available = (
        rw_reviewer_present and rw_reviewer_path_free and not rw_reviewer_self_claim_safe
    )
    rw_reviewer_window_size = low_risk.get("repeat_window_reviewer_summary_window_size")
    rw_reviewer_count_min = low_risk.get(
        "repeat_window_reviewer_summary_promotion_count_min"
    )
    rw_reviewer_count_max = low_risk.get(
        "repeat_window_reviewer_summary_promotion_count_max"
    )
    rw_reviewer_window_valid = (
        isinstance(rw_reviewer_window_size, int)
        and not isinstance(rw_reviewer_window_size, bool)
        and 2 <= rw_reviewer_window_size <= _trend_max_window
    )
    rw_reviewer_count_valid = (
        isinstance(rw_reviewer_count_min, int)
        and not isinstance(rw_reviewer_count_min, bool)
        and isinstance(rw_reviewer_count_max, int)
        and not isinstance(rw_reviewer_count_max, bool)
        and 1 <= rw_reviewer_count_min <= rw_reviewer_count_max
    )
    rw_reviewer_clean = bool(
        rw_reviewer_available
        and low_risk.get("repeat_window_reviewer_summary_trend_present") is True
        and low_risk.get("repeat_window_reviewer_summary_all_runs_ok") is True
        and low_risk.get("repeat_window_reviewer_summary_deterministic") is True
        and low_risk.get("repeat_window_reviewer_summary_promotion_count_stable") is True
        and low_risk.get("repeat_window_reviewer_summary_evidence_present") is True
        and low_risk.get("repeat_window_reviewer_summary_no_guardrail_tripped") is True
        and low_risk.get(
            "repeat_window_reviewer_summary_no_runtime_authority_granted"
        ) is True
        and low_risk.get("repeat_window_reviewer_summary_no_external_writes") is True
        and low_risk.get("repeat_window_reviewer_summary_window_size_valid") is True
        and rw_reviewer_window_valid
        and low_risk.get(
            "repeat_window_reviewer_summary_promotion_count_positive"
        ) is True
        and rw_reviewer_count_valid
    )
    # Low-risk cross-consistency digest: confirms the three low-risk measurement views
    # (real-loop dry-run, repeat-window trend, reviewer summary) AGREE. The consumer
    # RE-DERIVES cross_consistent from the digest's COMPONENT booleans - it does NOT trust
    # the digest's own cross_consistent aggregate (#1274). Refuse-to-certify on a
    # self-declared claim_safe. Measurement-only: NEVER upgrades the low-risk claim.
    lr_xcons_present = low_risk.get("cross_consistency_digest_present") is True
    lr_xcons_path_free = (
        low_risk.get("cross_consistency_path_free_verified") is True
    )
    lr_xcons_self_claim_safe = (
        low_risk.get("cross_consistency_self_claim_safe") is True
    )
    lr_xcons_available = (
        lr_xcons_present and lr_xcons_path_free and not lr_xcons_self_claim_safe
    )
    lr_cross_consistent = bool(
        lr_xcons_available
        and low_risk.get("cross_consistency_all_views_present") is True
        and low_risk.get("cross_consistency_real_loop_clean") is True
        and low_risk.get("cross_consistency_trend_clean") is True
        and low_risk.get("cross_consistency_reviewer_clean") is True
        and low_risk.get("cross_consistency_reviewer_matches_trend") is True
    )
    # #1291 cross-consistency digest bridge-event TEMPLATE summary: a curated content-safe
    # projection. Availability requires present AND path-free AND NOT self-claim_safe.
    lr_xcons_tpl_present = low_risk.get("xcons_template_present") is True
    lr_xcons_tpl_path_free = (
        low_risk.get("xcons_template_path_free_verified") is True
    )
    lr_xcons_tpl_self_claim_safe = (
        low_risk.get("xcons_template_self_claim_safe") is True
    )
    lr_xcons_tpl_available = (
        lr_xcons_tpl_present
        and lr_xcons_tpl_path_free
        and not lr_xcons_tpl_self_claim_safe
    )
    # RE-DERIVE template_clean from the summary's COMPONENT booleans (#1274): build
    # verdicts + every no-authority/approval axis present-and-True (refuse-to-certify on
    # self-approval) + the cross-consistency verdicts it carries. Never a template composite.
    lr_xcons_template_clean = bool(
        lr_xcons_tpl_available
        and low_risk.get("xcons_template_available") is True
        and low_risk.get("xcons_template_only") is True
        and low_risk.get("xcons_template_no_runtime_authority_granted") is True
        and low_risk.get("xcons_template_no_direct_bridge_write") is True
        and low_risk.get("xcons_template_no_bridge_event_written") is True
        and low_risk.get("xcons_template_no_approval_granted") is True
        and low_risk.get("xcons_template_cross_consistent") is True
        and low_risk.get("xcons_template_all_views_present") is True
        and low_risk.get("xcons_template_real_loop_clean") is True
        and low_risk.get("xcons_template_trend_clean") is True
        and low_risk.get("xcons_template_reviewer_clean") is True
        and low_risk.get("xcons_template_reviewer_matches_trend") is True
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
    # Shadow-only invariant: AFFIRMATIVE measurement-only proof that hex
    # subdivision stays shadow-only and NO runtime shadow->candidate promotion
    # occurs. The consumer RE-DERIVES shadow_only_enforced from the COMPONENT
    # booleans (it does NOT trust the proof's own invariant_holds aggregate - an
    # inconsistent aggregate with a component False but invariant_holds True must
    # fail closed). It NEVER upgrades a claim and NEVER raises the honest-zero
    # shadow_to_candidate_subdivision_transitions_total counter below.
    shadow_only_present = (
        hex_upgrades.get("shadow_only_invariant_present") is True
    )
    # transition count must be a STRICT int 0 (0.0 / 0j / bool / None fail closed).
    _shadow_only_transition_count = hex_upgrades.get("shadow_only_transition_count")
    _shadow_only_transition_count_strict_zero = (
        isinstance(_shadow_only_transition_count, int)
        and not isinstance(_shadow_only_transition_count, bool)
        and _shadow_only_transition_count == 0
    )
    shadow_only_enforced = bool(
        shadow_only_present
        and hex_upgrades.get("shadow_only_invariant_ok") is True
        and hex_upgrades.get("shadow_only_invariant_deterministic") is True
        and hex_upgrades.get("shadow_only_artifact_ok") is True
        and hex_upgrades.get("shadow_only_target_state_is_shadow") is True
        and hex_upgrades.get("shadow_only_no_runtime_mutation") is True
        and hex_upgrades.get("shadow_only_guardrails_all_clean") is True
        and hex_upgrades.get("shadow_only_transition_occurred") is False
        and _shadow_only_transition_count_strict_zero
        # A measurement-only proof must NOT self-declare claim_safe; if the
        # surfaced proof ever flips claim_safe True, refuse to certify enforcement.
        and hex_upgrades.get("shadow_only_invariant_claim_safe") is not True
    )
    # Path-free FINAL chain summary: end-to-end roll-up over every shadow-subdivision
    # verifier chain level (merged #1276 renderer). The consumer RE-DERIVES chain_clean
    # from the COMPONENT booleans - it does NOT trust the aggregate's own chain_clean
    # (#1274: an inconsistent aggregate with a component False but chain_clean True must
    # fail closed). Measurement-only: it NEVER upgrades a hex claim.
    # Expected full chain depth (the merged renderer's _CHAIN_LEVEL_KEYS); the honest
    # milestone is "10/10 levels". Kept as a local literal (the counter is a pure
    # consumer of safe scalars and does not import the renderer).
    _HEX_CHAIN_EXPECTED_LEVELS = 10
    hex_chain_present = hex_upgrades.get("chain_final_summary_present") is True
    hex_chain_path_free = (
        hex_upgrades.get("chain_final_summary_path_free_verified") is True
    )
    # Refuse-to-certify if the summary self-declares claim_safe True: measurement-only
    # never upgrades a claim, so an inconsistent self-declaration fails closed.
    hex_chain_self_claim_safe = (
        hex_upgrades.get("chain_final_summary_self_claim_safe") is True
    )
    hex_chain_available = (
        hex_chain_present and hex_chain_path_free and not hex_chain_self_claim_safe
    )
    # levels_total/present must be EQUAL strict positive ints AND equal the expected
    # full chain depth (10/10); a malformed/short/over count fails closed.
    _hex_chain_total = hex_upgrades.get("chain_final_summary_levels_total")
    _hex_chain_present_count = hex_upgrades.get("chain_final_summary_levels_present")
    _hex_chain_levels_complete = (
        isinstance(_hex_chain_total, int)
        and not isinstance(_hex_chain_total, bool)
        and _hex_chain_total == _HEX_CHAIN_EXPECTED_LEVELS
        and isinstance(_hex_chain_present_count, int)
        and not isinstance(_hex_chain_present_count, bool)
        and _hex_chain_present_count == _hex_chain_total
    )
    # blocker_count must be a STRICT int zero (0.0 / bool / None fail closed).
    _hex_chain_blocker_count = hex_upgrades.get("chain_final_summary_blocker_count")
    _hex_chain_blocker_zero = (
        isinstance(_hex_chain_blocker_count, int)
        and not isinstance(_hex_chain_blocker_count, bool)
        and _hex_chain_blocker_count == 0
    )
    def _strict_int_or_none(value: Any) -> int | None:
        return (
            value if isinstance(value, int) and not isinstance(value, bool) else None
        )

    # warning_count is non-fatal but surfaced as a STRICT int (else reported as None).
    _hex_chain_warning_strict = _strict_int_or_none(
        hex_upgrades.get("chain_final_summary_warning_count")
    )
    # blocker_count surfaced as a STRICT int (else None); gating uses the strict ==0
    # derivation above, surfacing reports the real count even when non-zero.
    _hex_chain_blocker_strict = _strict_int_or_none(_hex_chain_blocker_count)
    hex_chain_clean = bool(
        hex_chain_available
        and _hex_chain_levels_complete
        and _hex_chain_blocker_zero
        and hex_upgrades.get("chain_final_summary_levels_all_ok") is True
        and hex_upgrades.get("chain_final_summary_levels_shape_ok") is True
        and hex_upgrades.get("chain_final_summary_artifact_verify_clean") is True
        and hex_upgrades.get("chain_final_summary_index_entry_verify_clean") is True
        and hex_upgrades.get("chain_final_summary_deepest_verify_clean") is True
    )
    # Cross-consistency digest (#1278): confirms the three measurement views AGREE. The
    # consumer RE-DERIVES cross_consistent from the digest's COMPONENT booleans - it
    # does NOT trust the digest's own cross_consistent aggregate (#1274). Refuse-to-
    # certify on a self-declared claim_safe. Measurement-only: NEVER upgrades a claim.
    hex_xcons_present = hex_upgrades.get("cross_consistency_digest_present") is True
    hex_xcons_path_free = (
        hex_upgrades.get("cross_consistency_path_free_verified") is True
    )
    hex_xcons_self_claim_safe = (
        hex_upgrades.get("cross_consistency_self_claim_safe") is True
    )
    hex_xcons_available = (
        hex_xcons_present and hex_xcons_path_free and not hex_xcons_self_claim_safe
    )
    hex_cross_consistent = bool(
        hex_xcons_available
        and hex_upgrades.get("cross_consistency_all_views_present") is True
        and hex_upgrades.get("cross_consistency_reviewer_clean") is True
        and hex_upgrades.get("cross_consistency_shadow_only_clean") is True
        and hex_upgrades.get("cross_consistency_chain_summary_clean") is True
    )
    # Ring-messaging + parent-child hierarchy: the consumer RE-DERIVES the clean flag
    # from the COMPONENT booleans + a strict-int-0 blocker count - it does NOT trust the
    # proof's own ok aggregate (#1274). Measurement-only: NEVER upgrades a claim.
    hex_ring_present = hex_upgrades.get("ring_hierarchy_present") is True
    # Refuse-to-certify if the summary self-declares claim_safe True (measurement-only
    # never upgrades a claim) - mirrors the cross_consistency_self_claim_safe guard.
    hex_ring_self_claim_safe = (
        hex_upgrades.get("ring_hierarchy_self_claim_safe") is True
    )
    hex_ring_path_free = (
        hex_upgrades.get("ring_hierarchy_path_free_verified") is True
    )
    hex_ring_available = (
        hex_ring_present and hex_ring_path_free and not hex_ring_self_claim_safe
    )
    _hex_ring_blocker_count = hex_upgrades.get("ring_hierarchy_blocker_count")
    _hex_ring_blocker_zero = (
        isinstance(_hex_ring_blocker_count, int)
        and not isinstance(_hex_ring_blocker_count, bool)
        and _hex_ring_blocker_count == 0
    )
    hex_ring_hierarchy_clean = bool(
        hex_ring_available
        and _hex_ring_blocker_zero
        and hex_upgrades.get("ring_hierarchy_hierarchy_ok") is True
        and hex_upgrades.get("ring_hierarchy_ring_boundary_ok") is True
        and hex_upgrades.get("ring_hierarchy_no_runtime_mutation") is True
        and hex_upgrades.get("ring_hierarchy_no_invalid_boundary_delivery") is True
        and hex_upgrades.get("ring_hierarchy_deterministic") is True
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
            "measurement_denominator_scope": (
                hex_mesh.get("first_hop_denominator_scope")
                if first_hop_measurement_available
                else None
            ),
            "measurement_denominator_count": (
                hex_mesh.get("first_hop_denominator_count")
                if first_hop_measurement_available
                else None
            ),
            "measurement_gap_count": (
                hex_mesh.get("first_hop_gap_count")
                if first_hop_measurement_available
                else None
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
        "low_risk_real_loop_manifest_contribution": {
            # Measurement-only evidence-vs-authority contribution from the
            # deterministic real-loop proof. DERIVED fail-closed and fully
            # decoupled from runtime authority and literal claim safety.
            "contribution_available": real_loop_manifest_contribution_available,
            "evidence_count": 1 if real_loop_manifest_contribution_available else 0,
            "evidence_present": bool(
                real_loop_manifest_contribution_available
                and low_risk.get(
                    "real_loop_manifest_contribution_evidence_present"
                )
                is True
            ),
            "deterministic": bool(
                real_loop_manifest_contribution_available
                and low_risk.get("real_loop_manifest_contribution_deterministic")
                is True
            ),
            "guardrail_tripped": real_loop_manifest_guardrail_tripped,
            "provider_calls": (
                real_loop_manifest_provider_calls
                if real_loop_manifest_provider_calls_valid
                else None
            ),
            "production_authority_granted": bool(
                low_risk.get(
                    "real_loop_manifest_contribution_production_authority_granted"
                )
                is True
                or low_risk.get(
                    "real_loop_manifest_contribution_runtime_authority_granted"
                )
                is True
            ),
            "measurement_basis": (
                "v1_low_risk_real_loop_manifest_contribution"
                if real_loop_manifest_contribution_available
                else "manifest_real_loop_flags"
            ),
            "claim_safe": False,
        },
        "low_risk_real_loop_repeat_window_trend": {
            # Measurement-only reproducibility evidence (default-on; opt-out via env).
            # DERIVED fail-closed and fully decoupled from end_to_end_gated_promotions_total
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
        "low_risk_repeat_window_trend_reviewer_summary": {
            # Measurement-only path-free reviewer summary of the repeat-window trend
            # (merged #1284 renderer, now manifest-stored). DERIVED fail-closed and fully
            # decoupled - it NEVER upgrades any low-risk claim. review_clean is RE-DERIVED
            # from the summary's COMPONENT booleans, never its own trend_review_clean.
            "reviewer_summary_available": rw_reviewer_available,
            "review_clean": rw_reviewer_clean,
            "path_free_verified": bool(
                rw_reviewer_present
                and low_risk.get("repeat_window_reviewer_summary_path_free_verified")
                is True
            ),
            "measurement_basis": (
                "v1_low_risk_repeat_window_trend_reviewer_summary"
                if rw_reviewer_available
                else "manifest_real_loop_flags"
            ),
            "claim_safe": False,
        },
        "low_risk_cross_consistency_digest": {
            # Measurement-only path-free digest confirming the three low-risk views
            # (real-loop dry-run, repeat-window trend, reviewer summary) AGREE; DERIVED
            # fail-closed and fully decoupled - it NEVER upgrades any low-risk claim.
            # cross_consistent is RE-DERIVED from the digest's COMPONENT booleans, never
            # the digest's own cross_consistent aggregate.
            "digest_available": lr_xcons_available,
            "cross_consistent": lr_cross_consistent,
            "path_free_verified": bool(
                lr_xcons_present
                and low_risk.get("cross_consistency_path_free_verified") is True
            ),
            "all_views_present": bool(
                lr_xcons_available
                and low_risk.get("cross_consistency_all_views_present") is True
            ),
            "real_loop_clean": bool(
                lr_xcons_available
                and low_risk.get("cross_consistency_real_loop_clean") is True
            ),
            "trend_clean": bool(
                lr_xcons_available
                and low_risk.get("cross_consistency_trend_clean") is True
            ),
            "reviewer_clean": bool(
                lr_xcons_available
                and low_risk.get("cross_consistency_reviewer_clean") is True
            ),
            "reviewer_matches_trend": bool(
                lr_xcons_available
                and low_risk.get("cross_consistency_reviewer_matches_trend") is True
            ),
            "measurement_basis": (
                "v1_low_risk_cross_consistency_digest"
                if lr_xcons_available
                else "manifest_real_loop_flags"
            ),
            "claim_safe": False,
        },
        "low_risk_cross_consistency_digest_bridge_event_template": {
            # Measurement-only path-free SUMMARY of the #1291 cross-consistency digest
            # bridge-event template (a reviewer-handoff artifact). DERIVED fail-closed and
            # fully decoupled - it NEVER upgrades any low-risk claim. template_clean is
            # RE-DERIVED from the summary's COMPONENT booleans, never a template composite;
            # refuse-to-certify on self-claim_safe / self-approval.
            "template_available": lr_xcons_tpl_available,
            "template_clean": lr_xcons_template_clean,
            "template_only": bool(
                lr_xcons_tpl_available
                and low_risk.get("xcons_template_only") is True
            ),
            "path_free_verified": bool(
                lr_xcons_tpl_present
                and low_risk.get("xcons_template_path_free_verified") is True
            ),
            "no_runtime_authority_granted": bool(
                lr_xcons_tpl_available
                and low_risk.get("xcons_template_no_runtime_authority_granted") is True
            ),
            "no_direct_bridge_write": bool(
                lr_xcons_tpl_available
                and low_risk.get("xcons_template_no_direct_bridge_write") is True
            ),
            "no_approval_granted": bool(
                lr_xcons_tpl_available
                and low_risk.get("xcons_template_no_approval_granted") is True
            ),
            "cross_consistent": bool(
                lr_xcons_tpl_available
                and low_risk.get("xcons_template_cross_consistent") is True
            ),
            "measurement_basis": (
                "v1_low_risk_cross_consistency_digest_bridge_event_template"
                if lr_xcons_tpl_available
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
        "hex_subdivision_shadow_only_invariant": {
            # AFFIRMATIVE measurement-only proof that hex subdivision stays
            # shadow-only and NO runtime shadow->candidate promotion occurs.
            # shadow_only_enforced is RE-DERIVED fail-closed from the proof's
            # COMPONENT booleans (never its invariant_holds aggregate) and is fully
            # decoupled: it NEVER upgrades a hex claim and NEVER raises the
            # honest-zero shadow_to_candidate_subdivision_transitions_total counter.
            "invariant_proof_available": shadow_only_present,
            "shadow_only_enforced": shadow_only_enforced,
            "target_state_is_shadow": bool(
                shadow_only_present
                and hex_upgrades.get("shadow_only_target_state_is_shadow") is True
            ),
            "no_runtime_mutation": bool(
                shadow_only_present
                and hex_upgrades.get("shadow_only_no_runtime_mutation") is True
            ),
            "guardrails_all_clean": bool(
                shadow_only_present
                and hex_upgrades.get("shadow_only_guardrails_all_clean") is True
            ),
            # A POSITIVELY-detected transition is a violation, surfaced honestly;
            # it does NOT feed the progress counter (no fake transition path).
            "transition_occurred": (
                hex_upgrades.get("shadow_only_transition_occurred") is True
            ),
            "transition_count_strict_zero": _shadow_only_transition_count_strict_zero,
            "deterministic_replay_stable": bool(
                shadow_only_present
                and hex_upgrades.get("shadow_only_invariant_deterministic") is True
            ),
            "measurement_basis": (
                "v1_shadow_only_invariant"
                if shadow_only_enforced
                else "manifest_hex_upgrade_flags"
            ),
            "claim_safe": False,
        },
        "hex_subdivision_chain_final_summary": {
            # Measurement-only path-free FINAL roll-up over the WHOLE shadow-
            # subdivision verifier chain; DERIVED fail-closed and fully decoupled - it
            # NEVER upgrades any hexagonal claim. chain_clean is RE-DERIVED from the
            # COMPONENT booleans, never the aggregate's own chain_clean.
            "chain_summary_available": hex_chain_available,
            "chain_clean": hex_chain_clean,
            "path_free_verified": bool(
                hex_chain_present
                and hex_upgrades.get("chain_final_summary_path_free_verified") is True
            ),
            "levels_all_ok": bool(
                hex_chain_available
                and hex_upgrades.get("chain_final_summary_levels_all_ok") is True
            ),
            "levels_shape_ok": bool(
                hex_chain_available
                and hex_upgrades.get("chain_final_summary_levels_shape_ok") is True
            ),
            # Honest 10/10 milestone: surface present/total only when the level counts
            # are complete-and-consistent, else None (never a misleading partial).
            "levels_present": (
                _hex_chain_present_count if _hex_chain_levels_complete else None
            ),
            "levels_total": (
                _hex_chain_total if _hex_chain_levels_complete else None
            ),
            "levels_complete_10_of_10": _hex_chain_levels_complete,
            # Surface the strict-int blocker/warning counts (blocker None when not a
            # strict int; warning None when not a strict int). Non-fatal warnings are
            # reported but never gate chain_clean.
            "blocker_count": _hex_chain_blocker_strict,
            "warning_count": _hex_chain_warning_strict,
            "measurement_basis": (
                "v1_shadow_subdivision_verifier_chain_final_summary"
                if hex_chain_available
                else "manifest_hex_upgrade_flags"
            ),
            "claim_safe": False,
        },
        "hex_subdivision_cross_consistency_digest": {
            # Measurement-only path-free digest confirming the three hex-upgrade views
            # AGREE; DERIVED fail-closed and fully decoupled - it NEVER upgrades any
            # hexagonal claim. cross_consistent is RE-DERIVED from the digest's
            # COMPONENT booleans, never the digest's own cross_consistent aggregate.
            "digest_available": hex_xcons_available,
            "cross_consistent": hex_cross_consistent,
            "path_free_verified": bool(
                hex_xcons_present
                and hex_upgrades.get("cross_consistency_path_free_verified") is True
            ),
            "all_views_present": bool(
                hex_xcons_available
                and hex_upgrades.get("cross_consistency_all_views_present") is True
            ),
            "reviewer_clean": bool(
                hex_xcons_available
                and hex_upgrades.get("cross_consistency_reviewer_clean") is True
            ),
            "shadow_only_clean": bool(
                hex_xcons_available
                and hex_upgrades.get("cross_consistency_shadow_only_clean") is True
            ),
            "chain_summary_clean": bool(
                hex_xcons_available
                and hex_upgrades.get("cross_consistency_chain_summary_clean") is True
            ),
            "measurement_basis": (
                "v1_hex_upgrade_cross_consistency_digest"
                if hex_xcons_available
                else "manifest_hex_upgrade_flags"
            ),
            "claim_safe": False,
        },
        "hex_subdivision_ring_hierarchy": {
            # Measurement-only ring-messaging + parent-child hierarchy roll-up; DERIVED
            # fail-closed and fully decoupled - it NEVER upgrades any hexagonal claim.
            # ring_hierarchy_clean is RE-DERIVED from the COMPONENT booleans, never the
            # proof's own ok aggregate. Availability requires the summary present AND no
            # self-declared claim_safe (refuse-to-certify) - the curated summary is
            # otherwise content-safe by construction.
            "ring_hierarchy_available": hex_ring_available,
            "ring_hierarchy_clean": hex_ring_hierarchy_clean,
            "hierarchy_ok": bool(
                hex_ring_available
                and hex_upgrades.get("ring_hierarchy_hierarchy_ok") is True
            ),
            "ring_boundary_ok": bool(
                hex_ring_available
                and hex_upgrades.get("ring_hierarchy_ring_boundary_ok") is True
            ),
            "no_runtime_mutation": bool(
                hex_ring_available
                and hex_upgrades.get("ring_hierarchy_no_runtime_mutation") is True
            ),
            "no_invalid_boundary_delivery": bool(
                hex_ring_available
                and hex_upgrades.get("ring_hierarchy_no_invalid_boundary_delivery")
                is True
            ),
            "deterministic": bool(
                hex_ring_available
                and hex_upgrades.get("ring_hierarchy_deterministic") is True
            ),
            "measurement_basis": (
                "v1_ring_messaging_hierarchy_proof"
                if hex_ring_available
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
