# SPDX-License-Identifier: BUSL-1.1
"""Build a clean V12 A3 counterfactual-evaluation proof row."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.idle_consensus_artifact import (  # noqa: E402
    build_idle_consensus_candidate_diff_replay_admission,
    build_idle_consensus_replay_seed,
)
from tools.run_pdam_counterfactual_demo import build_variant_matrix_report  # noqa: E402
from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.autonomy_growth.counterfactual_replay import (  # noqa: E402
    A3_LABEL_MEASURED_LOCAL_PARTIAL,
    DEFAULT_A3_MIN_SAMPLES,
    compute_counterfactual_delta,
    summarize_counterfactual_observability,
)
from waggledance.core.leak_policy import looks_like_leak_simple  # noqa: E402
from waggledance.core.magma.canonical import (  # noqa: E402
    canonical_json_bytes,
    sha256_digest,
)
from waggledance.core.magma.evaluation_result import build_evaluation_result_v1  # noqa: E402
from waggledance.core.magma.receipt import build_magma_receipt  # noqa: E402
from waggledance.core.magma.receipt_bundle import (  # noqa: E402
    ReceiptBundleEntry,
    write_receipt_bundle,
)
from waggledance.core.solver_synthesis.declarative_solver_spec import SolverSpec  # noqa: E402


REPORT_VERSION = "wd.v12.a3_counterfactual_axis_proof.v0"
EVALUATION_RESULT_VERSION = "magma.evaluation_result.v1"
CHAIN_ID = "magma:v12_a3_counterfactual_axis:v1"
STORED_CONSENSUS_REPLAY_VERSION = "wd.v12.a3_stored_consensus_replay.v0"
STORED_CONSENSUS_ARTIFACT_VERSION = "idle_consensus_operator_review.v1"
STORED_CONSENSUS_ARTIFACT_ID = "idle-consensus-a3-counterfactual-delta-replay"
STORED_CONSENSUS_CANDIDATE_CHANGED_PATHS = (
    "docs/architecture/consensus_artifacts/a3_counterfactual_delta_replay.md",
)
STORED_CONSENSUS_CANDIDATE_DIFF_TEXT = """diff --git a/docs/architecture/consensus_artifacts/a3_counterfactual_delta_replay.md b/docs/architecture/consensus_artifacts/a3_counterfactual_delta_replay.md
new file mode 100644
--- /dev/null
+++ b/docs/architecture/consensus_artifacts/a3_counterfactual_delta_replay.md
@@ -0,0 +1,4 @@
+# A3 Counterfactual Delta Replay
+
+Stored consensus replay proposes documenting only the local A3 candidate diff.
+No write, PR creation, merge, or external effect is executed by this replay.
"""
RUNTIME_CONDITION_SMOKE_VERSION = "wd.v12.a3_runtime_condition_replay_smoke.v0"
RUNTIME_SMOKE_PRIVACY_CANARY = "operator_secret_goal_marker_DO_NOT_LEAK"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a clean A3 counterfactual-evaluation proof row from the "
            "local PDAM factual->counterfactual demo."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Optional new output directory for the underlying MAGMA receipt bundle.",
    )
    parser.add_argument(
        "--markdown-out",
        type=Path,
        default=None,
        help="Optional markdown report path to write.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic receipt output.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_a3_counterfactual_axis_proof(
            receipt_out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except ValueError as exc:
        print(f"A3 counterfactual axis proof FAILED: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(report)
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown, encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(markdown, end="")
    return 0 if report["ok"] else 1


def build_a3_counterfactual_axis_proof(
    *,
    receipt_out_dir: Path | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    demo = _bind_a3_evaluations_v1(build_variant_matrix_report())
    stored_consensus_replay = _build_stored_consensus_replay()
    if receipt_out_dir is not None:
        receipt_now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        demo["receipt_bundle"] = _emit_a3_v1_receipt_bundle(
            demo,
            receipt_out_dir,
            receipt_now,
            stored_consensus_replay=stored_consensus_replay,
        )
    factual = demo["factual"]
    counterfactual = demo["counterfactual"]
    delta = demo["delta"]
    runtime_smoke = _build_runtime_condition_replay_smoke()
    delta_fields = [
        field
        for field, values in sorted(delta.items())
        if isinstance(values, list) and len(values) == 2 and values[0] != values[1]
    ]
    required_delta_fields = {"actual_gate", "kind"}
    receipt_bundle = demo.get("receipt_bundle")
    receipt_chain_verified = bool(
        receipt_bundle and receipt_bundle["verifier_report"]["ok"]
    )
    stored_consensus_replay = _with_receipt_binding_state(
        stored_consensus_replay,
        receipt_chain_verified=receipt_chain_verified,
    )
    variant_summaries = [_variant_summary(variant) for variant in demo["variants"]]
    variant_count = len(variant_summaries)
    variants_with_kind_delta = sum(
        1 for variant in variant_summaries if "kind" in variant["delta_fields"]
    )
    variants_with_gate_delta = sum(
        1 for variant in variant_summaries if "actual_gate" in variant["delta_fields"]
    )
    counterfactual_delta_proven = (
        demo["writes_applied"] is False
        and variant_count >= 3
        and variants_with_kind_delta == variant_count
        and variants_with_gate_delta >= 2
        and required_delta_fields.issubset(delta_fields)
        and all(
            variant["factual"]["risk_class"] == "internal_memory"
            and variant["counterfactual"]["risk_class"] == "internal_memory"
            for variant in variant_summaries
        )
    )

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "ok": bool(
            counterfactual_delta_proven
            and stored_consensus_replay["candidate_diff_charter_allowed"]
            and runtime_smoke["ok"]
        ),
        "axis_id": "A3",
        "axis_name": "counterfactual_evaluation_delta",
        "claim_label": "MEASURED_LOCAL_PARTIAL",
        "evaluation_result_version": EVALUATION_RESULT_VERSION,
        "receipt_chain_id": CHAIN_ID,
        "case_id": demo["case_id"],
        "source_demo_version": demo["demo_version"],
        "writes_applied": demo["writes_applied"],
        "counterfactual_delta_proven": bool(counterfactual_delta_proven),
        "variant_count": variant_count,
        "variants_with_kind_delta": variants_with_kind_delta,
        "variants_with_gate_delta": variants_with_gate_delta,
        "delta_field_count": len(delta_fields),
        "delta_fields": delta_fields,
        "delta": delta,
        "factual": _scenario_summary(factual),
        "counterfactual": _scenario_summary(counterfactual),
        "variants": variant_summaries,
        "receipt_chain_verified": receipt_chain_verified,
        "receipt_bundle": _receipt_summary(receipt_bundle),
        "stored_consensus_replay_verified": bool(
            stored_consensus_replay["candidate_diff_charter_allowed"]
        ),
        "receipt_bound_stored_consensus_replay": bool(
            stored_consensus_replay["receipt_bound"]
        ),
        "stored_consensus_replay": stored_consensus_replay,
        "runtime_condition_replay_smoke": runtime_smoke,
        "evidence_sources": [
            "tools/run_pdam_counterfactual_demo.py",
            "waggledance/core/autonomy_growth/counterfactual_replay.py",
            "schemas/v3_13_0/evaluation_result.v1.json",
            "tools/verify_magma_receipt.py",
            "docs/architecture/IDLE_AUTONOMY_CHARTER.md",
            "waggledance/core/idle_consensus_charter.py",
        ],
        "no_overclaim_guardrails": {
            "not_a_rival_benchmark": True,
            "does_not_claim_external_effect_execution": True,
            "does_not_apply_writes": True,
            "measures_one_local_domain_fixture": True,
            "measures_three_deterministic_variants": True,
            "runtime_smoke_is_not_axis_claim_upgrade": True,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    delta = report["delta"]
    receipt_state = str(report["receipt_chain_verified"]).lower()
    lines = [
        "# V12 A3 Counterfactual Axis Proof",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- axis: `{report['axis_id']} {report['axis_name']}`",
        f"- claim_label: `{report['claim_label']}`",
        f"- evaluation_result_version: `{report['evaluation_result_version']}`",
        f"- counterfactual_delta_proven: `{str(report['counterfactual_delta_proven']).lower()}`",
        f"- variant_count: `{report['variant_count']}`",
        f"- variants_with_gate_delta: `{report['variants_with_gate_delta']}`",
        f"- writes_applied: `{str(report['writes_applied']).lower()}`",
        f"- receipt_chain_verified: `{receipt_state}`",
        f"- stored_consensus_replay_verified: `{str(report['stored_consensus_replay_verified']).lower()}`",
        f"- stored_consensus_replay_receipt_bound: `{str(report['receipt_bound_stored_consensus_replay']).lower()}`",
        f"- stored_consensus_replay_decision: `{report['stored_consensus_replay']['decision']}`",
        f"- runtime_condition_replay_smoke: `{str(report['runtime_condition_replay_smoke']['ok']).lower()}`",
        f"- runtime_replay_claim_label: `{report['runtime_condition_replay_smoke']['claim_label']}`",
        f"- runtime_replay_sample_count: `{report['runtime_condition_replay_smoke']['sample_count']}`",
        "",
        "| Field | Factual | Counterfactual |",
        "|---|---|---|",
        f"| action kind | `{delta['kind'][0]}` | `{delta['kind'][1]}` |",
        f"| actual gate | `{delta['actual_gate'][0]}` | `{delta['actual_gate'][1]}` |",
        f"| verdict | `{delta['verdict'][0]}` | `{delta['verdict'][1]}` |",
        "",
        "| Variant | Action delta | Gate delta | Verdict delta |",
        "|---|---|---|---|",
        *[
            "| "
            f"`{variant['variant_id']}` | "
            f"`{variant['delta']['kind'][0]}` -> `{variant['delta']['kind'][1]}` | "
            f"`{variant['delta']['actual_gate'][0]}` -> `{variant['delta']['actual_gate'][1]}` | "
            f"`{variant['delta']['verdict'][0]}` -> `{variant['delta']['verdict'][1]}` |"
            for variant in report["variants"]
        ],
        "",
        "This is a three-variant local measured counterfactual row. It is not a rival benchmark,",
        "does not execute an external effect, and does not claim broad semantic",
        "counterfactual coverage beyond this fixture. The runtime-condition smoke only",
        "proves the sample-floor, same-sample-set, deterministic, and privacy guards",
        "on a deterministic local fixture; it does not upgrade the top-level axis claim.",
        "",
    ]
    return "\n".join(lines)


def _variant_summary(variant: dict[str, Any]) -> dict[str, Any]:
    delta = variant["delta"]
    return {
        "variant_id": variant["variant_id"],
        "case_id": variant["case_id"],
        "selected_entry_id": variant["selected_entry_id"],
        "delta_fields": [
            field
            for field, values in sorted(delta.items())
            if values[0] != values[1]
        ],
        "delta": delta,
        "factual": _scenario_summary(variant["factual"]),
        "counterfactual": _scenario_summary(variant["counterfactual"]),
    }


def _scenario_summary(scenario: dict[str, Any]) -> dict[str, Any]:
    evaluation = scenario["evaluation_result"]
    return {
        "label": scenario["label"],
        "subtool_state": scenario["subtool_state"],
        "action_kind": scenario["action"]["kind"],
        "evaluation_version": evaluation["evaluation_version"],
        "actual_gate": evaluation["actual_gate"],
        "expected_gate": evaluation["expected_gate"],
        "verdict": evaluation["verdict"],
        "risk_class": evaluation["risk_class"],
        "operator_required": evaluation["operator_required"],
        "competitor_axis_reference": evaluation.get("competitor_axis_reference"),
        "confidence_basis": evaluation.get("confidence_basis"),
        "sanitization_audit": evaluation.get("sanitization_audit"),
        "subject_payload_size_bytes": evaluation.get("subject_payload_size_bytes"),
        "target_digest": evaluation["target_digest"],
        "evaluation_result_digest": sha256_digest(evaluation),
        "reason_codes": evaluation["reason_codes"],
    }


def _receipt_summary(receipt_bundle: dict[str, Any] | None) -> dict[str, Any]:
    if not receipt_bundle:
        return {
            "available": False,
            "receipt_count": 0,
            "verifier_ok": False,
        }
    verifier = receipt_bundle["verifier_report"]
    return {
        "available": True,
        "out_dir": receipt_bundle["out_dir"],
        "manifest": receipt_bundle["manifest"],
        "receipt_count": receipt_bundle["receipt_count"],
        "verifier_ok": bool(verifier["ok"]),
        "verifier_error_count": len(verifier["errors"]),
    }


def _build_runtime_condition_replay_smoke() -> dict[str, Any]:
    samples = [
        {"x": float(index), "note": RUNTIME_SMOKE_PRIVACY_CANARY}
        for index in range(DEFAULT_A3_MIN_SAMPLES + 4)
    ]
    delta = compute_counterfactual_delta(
        shadow_samples=samples,
        candidate=_runtime_smoke_spec("candidate_kelvin", 273.15),
        incumbent=_runtime_smoke_spec("incumbent_identity", 0.0),
        oracle=_runtime_smoke_oracle,
        oracle_kind="deterministic_formula_recompute",
    )
    summary = summarize_counterfactual_observability(delta)
    rendered_summary = repr(summary)
    privacy_canary_absent = RUNTIME_SMOKE_PRIVACY_CANARY not in rendered_summary
    raw_fields_absent = all(
        field not in summary
        for field in ("per_arm", "divergences", "candidate_hash", "incumbent_hash")
    )
    emitted_text_fields = (
        "scalar_unit_conversion_24_same_sample_set",
        "runtime_condition_smoke_only_not_axis_claim_upgrade",
    )
    emitted_text_passes_leak_policy = all(
        not looks_like_leak_simple(value) for value in emitted_text_fields
    )
    runtime_conditions_met = (
        summary["sample_count"] >= DEFAULT_A3_MIN_SAMPLES
        and summary["same_sample_set"] is True
        and summary["deterministic"] is True
        and summary["delta_digest_present"] is True
    )
    ok = (
        runtime_conditions_met
        and summary["runtime_authority_granted"] is False
        and summary["external_writes_applied"] is False
        and summary["payload_fields_exported"] is False
        and privacy_canary_absent
        and raw_fields_absent
        and emitted_text_passes_leak_policy
    )
    return {
        "schema_version": RUNTIME_CONDITION_SMOKE_VERSION,
        "ok": bool(ok),
        "sample_family": "scalar_unit_conversion_24_same_sample_set",
        "min_samples": DEFAULT_A3_MIN_SAMPLES,
        "sample_count": summary["sample_count"],
        "compute_status": summary["compute_status"],
        "observability_status": "measured_local_partial",
        "claim_label": A3_LABEL_MEASURED_LOCAL_PARTIAL,
        "runtime_conditions_met": runtime_conditions_met,
        "divergence_count": summary["divergence_count"],
        "same_sample_set": summary["same_sample_set"],
        "deterministic": summary["deterministic"],
        "no_delta": summary["no_delta"],
        "delta_digest_present": summary["delta_digest_present"],
        "source_available": summary["source_available"],
        "claim_gate_satisfied": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "required_runtime_evidence_present": False,
        "controls_present": summary["controls_present"],
        "runtime_authority_granted": summary["runtime_authority_granted"],
        "external_writes_applied": summary["external_writes_applied"],
        "payload_fields_exported": summary["payload_fields_exported"],
        "raw_fields_exported": False,
        "privacy_canary_absent": privacy_canary_absent,
        "emitted_text_passes_leak_policy": emitted_text_passes_leak_policy,
        "claim_boundary": "runtime_condition_smoke_only_not_axis_claim_upgrade",
    }


def _runtime_smoke_spec(name: str, offset: float) -> SolverSpec:
    return SolverSpec(
        schema_version=1,
        spec_id=f"a3_runtime_smoke_{name}",
        family_kind="scalar_unit_conversion",
        solver_name=name,
        cell_id="general",
        spec={"from_unit": "C", "to_unit": "K", "factor": 1.0, "offset": offset},
        source="v12_a3_runtime_smoke",
        source_kind="hand_authored_fixture",
    )


def _runtime_smoke_oracle(inputs: Mapping[str, Any], artifact: Mapping[str, Any]) -> float:
    return float(inputs["x"]) * float(artifact["factor"]) + float(
        artifact.get("offset", 0.0)
    )


def _build_stored_consensus_replay() -> dict[str, Any]:
    changed_paths = list(STORED_CONSENSUS_CANDIDATE_CHANGED_PATHS)
    candidate_diff_text = STORED_CONSENSUS_CANDIDATE_DIFF_TEXT
    artifact = _stored_consensus_artifact()
    replay_seed = build_idle_consensus_replay_seed(artifact)
    admission = build_idle_consensus_candidate_diff_replay_admission(
        replay_seed=replay_seed,
        changed_paths=changed_paths,
        candidate_diff_text=candidate_diff_text,
        counterfactual_eval_receipt=_stored_consensus_counterfactual_eval_receipt(),
    )

    return {
        "replay_version": STORED_CONSENSUS_REPLAY_VERSION,
        "admission_report_version": admission["report_version"],
        "available": True,
        "decision": admission["decision"],
        "dry_run": admission["dry_run"],
        "external_effect": admission["external_effect"],
        "writes_applied": admission["writes_applied"],
        "would_create_task": admission["would_create_task"],
        "would_create_branch": admission["would_create_branch"],
        "would_create_pr": admission["would_create_pr"],
        "would_merge": admission["would_merge"],
        "eligible_for_draft_pr_gate": admission["eligible_for_draft_pr_gate"],
        "draft_pr_gate_blockers": [
            *admission["draft_pr_gate_blockers"],
            "live_rate_gate_not_evaluated",
        ],
        "candidate_diff_charter_allowed": admission["candidate_diff_charter_allowed"],
        "stored_consensus": {
            "artifact_version": artifact["artifact_version"],
            "artifact_id": artifact["artifact_id"],
            "digest": replay_seed["consensus_artifact"]["digest"],
            "status": artifact["convergence"]["status"],
            "replay_seed_digest": admission["replay_seed"]["digest"],
            "transcript_digest": admission["replay_seed"]["transcript_digest"],
            "convergence_digest": admission["replay_seed"]["convergence_digest"],
        },
        "replay_seed": admission["replay_seed"],
        "candidate_diff": admission["candidate_diff"],
        "counterfactual_eval": admission["counterfactual_eval"],
        "path_gate": admission["path_gate"],
        "diff_gate": admission["diff_gate"],
        "next_required_gates": _stored_consensus_next_required_gates(admission),
    }


def _stored_consensus_artifact() -> dict[str, Any]:
    return {
        "artifact_version": STORED_CONSENSUS_ARTIFACT_VERSION,
        "artifact_id": STORED_CONSENSUS_ARTIFACT_ID,
        "created_at_utc": "2026-05-20T19:50:00Z",
        "source": "deterministic_a3_replay_fixture",
        "operator_gate_required": True,
        "auto_execute": False,
        "convergence": {
            "status": "soft_convergence",
            "target_proposal_id": "a3-counterfactual-delta-replay",
            "support_count": 3,
            "objection_count": 0,
        },
        "transcript": [
            {
                "protocol_version": "idle-protocol.v1",
                "event_type": "idle_consensus_reached",
                "proposal_id": "a3-counterfactual-delta-replay",
                "round_number": 5,
                "operator_gate_required": True,
                "auto_execute": False,
            }
        ],
        "target": {
            "axis_id": "A3",
            "axis_name": "counterfactual_evaluation_delta",
        },
    }


def _stored_consensus_counterfactual_eval_receipt() -> dict[str, Any]:
    return {
        "schema_version": "magma.counterfactual_promotion_summary.v0",
        "status": "computed",
        "a3_label": A3_LABEL_MEASURED_LOCAL_PARTIAL,
        "sample_count": DEFAULT_A3_MIN_SAMPLES,
        "divergence_count": 3,
        "same_sample_set": True,
        "deterministic": True,
        "no_delta": False,
        "delta_digest": sha256_digest(
            {
                "axis_id": "A3",
                "fixture": "stored_consensus_replay",
                "sample_count": DEFAULT_A3_MIN_SAMPLES,
            }
        ),
    }


def _stored_consensus_next_required_gates(admission: Mapping[str, Any]) -> list[str]:
    gates = ["forensic_artifact_receipt"]
    for gate in admission["next_required_gates"]:
        if gate not in gates:
            gates.append(gate)
    if "daily_rate_limit" not in gates:
        try:
            exact_head_index = gates.index("exact_head_merge")
        except ValueError:
            exact_head_index = len(gates)
        gates.insert(exact_head_index, "daily_rate_limit")
    return gates


def _with_receipt_binding_state(
    stored_consensus_replay: dict[str, Any],
    *,
    receipt_chain_verified: bool,
) -> dict[str, Any]:
    replay = deepcopy(stored_consensus_replay)
    replay["receipt_bound"] = bool(receipt_chain_verified)
    replay["receipt_chain_id"] = CHAIN_ID if receipt_chain_verified else None
    replay["satisfied_gates"] = []
    if receipt_chain_verified:
        replay["satisfied_gates"].append("forensic_artifact_receipt")
        replay["next_required_gates"] = [
            gate
            for gate in replay["next_required_gates"]
            if gate != "forensic_artifact_receipt"
        ]
    return replay


def _bind_a3_evaluations_v1(demo: dict[str, Any]) -> dict[str, Any]:
    bound = deepcopy(demo)
    for variant in bound["variants"]:
        for side in ("factual", "counterfactual"):
            _upgrade_scenario_to_v1(variant[side])
    primary = bound["variants"][0]
    bound["factual"] = primary["factual"]
    bound["counterfactual"] = primary["counterfactual"]
    return bound


def _upgrade_scenario_to_v1(scenario: dict[str, Any]) -> None:
    payload = scenario["action"]
    previous = scenario["evaluation_result"]
    scenario["evaluation_result"] = build_evaluation_result_v1(
        case_id=previous["case_id"],
        subject_type=previous["subject_type"],
        target_payload=payload,
        risk_class=previous["risk_class"],
        expected_gate=previous["expected_gate"],
        actual_gate=previous["actual_gate"],
        verifier_path=[
            "v12_a3_counterfactual_axis_proof",
            "pdam_close_solver",
            "evaluation_result_schema_v1",
            "operator_gate_model",
        ],
        solver_selection=previous["solver_selection"],
        policy_version=previous["policy_version"],
        charter_version=previous["charter_version"],
        domain_threshold_version=previous["domain_threshold_version"],
        verdict=previous["verdict"],
        reason_codes=[
            *previous["reason_codes"],
            "axis:A3_counterfactual_evaluation_delta",
            "claim_label:MEASURED_LOCAL_PARTIAL",
            "stored_consensus_replay:candidate_diff_charter_gate",
        ],
        confidence_score=previous["confidence_score"],
        uncertainty_sources=[
            {
                "kind": "limited_evidence",
                "detail": "A3 proof is a deterministic local three-variant fixture, not a statistical benchmark.",
            }
        ],
        confidence_basis={
            "method": "point_estimate",
            "sample_count": 1,
            "methodology_reference": "tools/run_v12_a3_counterfactual_axis_proof.py",
        },
        sanitization_audit={
            "applied": ["locale_normalization"],
            "redaction_count": 0,
        },
        competitor_axis_reference="A3",
        subject_payload_size_bytes=len(canonical_json_bytes(payload)),
    )


def _emit_a3_v1_receipt_bundle(
    demo: dict[str, Any],
    out_dir: Path,
    now_utc: datetime,
    *,
    stored_consensus_replay: dict[str, Any],
) -> dict[str, Any]:
    entries: list[ReceiptBundleEntry] = []
    previous_receipt: dict[str, Any] | None = None
    index = 0
    for variant in demo["variants"]:
        variant_id = variant["variant_id"]
        for side in ("factual", "counterfactual"):
            index += 1
            scenario = variant[side]
            payload = scenario["action"]
            evaluation = scenario["evaluation_result"]
            replay_binding = _receipt_replay_binding(stored_consensus_replay)
            receipt = build_magma_receipt(
                event_id=(
                    f"magma:v12_a3_counterfactual_axis:{index:03d}:"
                    f"{variant_id}:{side}"
                ),
                ts_utc=_iso(now_utc + timedelta(seconds=index)),
                risk_class=evaluation["risk_class"],
                payload=payload,
                evaluation_result=evaluation,
                previous_receipt=previous_receipt,
                policy_digest=sha256_digest({
                    "policy_version": evaluation["policy_version"],
                }),
                charter_digest=sha256_digest({
                    "charter_version": evaluation["charter_version"],
                }),
                rco_decision_digest=sha256_digest({
                    "actual_gate": evaluation["actual_gate"],
                    "case_id": evaluation["case_id"],
                    "competitor_axis_reference": evaluation.get(
                        "competitor_axis_reference"
                    ),
                    "stored_consensus_replay": replay_binding,
                    "verdict": evaluation["verdict"],
                }),
                world_snapshot_digest=sha256_digest({
                    "case_id": variant["case_id"],
                    "scenario": side,
                    "stored_consensus_replay": replay_binding,
                    "subtool_state": scenario["subtool_state"],
                }),
                solver_contract_digest=sha256_digest({
                    "solver_selection": evaluation["solver_selection"],
                    "policy_version": evaluation["policy_version"],
                    "stored_consensus_replay_version": stored_consensus_replay[
                        "replay_version"
                    ],
                }),
            )
            previous_receipt = receipt
            entries.append(
                ReceiptBundleEntry(
                    label=f"{variant_id}-{side}",
                    payload=payload,
                    evaluation_result=evaluation,
                    receipt=receipt,
                )
            )

    return write_receipt_bundle(
        out_dir=out_dir,
        chain_id=CHAIN_ID,
        entries=entries,
        verify_manifest=verify_manifest,
    )


def _receipt_replay_binding(stored_consensus_replay: dict[str, Any]) -> dict[str, Any]:
    return {
        "replay_version": stored_consensus_replay["replay_version"],
        "admission_report_version": stored_consensus_replay[
            "admission_report_version"
        ],
        "stored_consensus_digest": stored_consensus_replay["stored_consensus"][
            "digest"
        ],
        "replay_seed_digest": stored_consensus_replay["stored_consensus"][
            "replay_seed_digest"
        ],
        "candidate_diff_digest": stored_consensus_replay["candidate_diff"]["digest"],
        "candidate_diff_charter_allowed": bool(
            stored_consensus_replay["candidate_diff_charter_allowed"]
        ),
        "counterfactual_eval_satisfies_replay_gate": bool(
            stored_consensus_replay["counterfactual_eval"]["satisfies_replay_gate"]
        ),
        "replay_decision": stored_consensus_replay["decision"],
    }


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("--now requires a UTC timestamp with Z or +00:00 suffix")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
