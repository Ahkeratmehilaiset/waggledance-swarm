# SPDX-License-Identifier: BUSL-1.1
"""Build a clean V12 A3 counterfactual-evaluation proof row."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_pdam_counterfactual_demo import build_variant_matrix_report  # noqa: E402
from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.idle_consensus_charter import (  # noqa: E402
    evaluate_diff_content,
    evaluate_paths,
    load_charter,
)
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
        "evidence_sources": [
            "tools/run_pdam_counterfactual_demo.py",
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
        "counterfactual coverage beyond this fixture.",
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


def _build_stored_consensus_replay() -> dict[str, Any]:
    charter = load_charter()
    changed_paths = list(STORED_CONSENSUS_CANDIDATE_CHANGED_PATHS)
    candidate_diff_text = STORED_CONSENSUS_CANDIDATE_DIFF_TEXT
    artifact = _stored_consensus_artifact()
    path_gate = evaluate_paths(charter, changed_paths)
    diff_gate = evaluate_diff_content(charter, candidate_diff_text)
    candidate_diff_allowed = bool(path_gate.allowed and diff_gate.allowed)
    decision = (
        "candidate_diff_charter_passed"
        if candidate_diff_allowed
        else "operator_review_required"
    )
    candidate_diff_digest = sha256_digest({
        "changed_paths": changed_paths,
        "diff_text": candidate_diff_text,
    })

    return {
        "replay_version": STORED_CONSENSUS_REPLAY_VERSION,
        "available": True,
        "decision": decision,
        "dry_run": True,
        "external_effect": False,
        "writes_applied": False,
        "would_create_pr": False,
        "would_merge": False,
        "eligible_for_draft_pr_gate": False,
        "draft_pr_gate_blockers": [
            "live_rate_gate_not_evaluated",
            "operator_review_gate_still_required",
        ],
        "candidate_diff_charter_allowed": candidate_diff_allowed,
        "stored_consensus": {
            "artifact_version": artifact["artifact_version"],
            "artifact_id": artifact["artifact_id"],
            "digest": sha256_digest(artifact),
            "status": artifact["consensus"]["status"],
        },
        "candidate_diff": {
            "changed_paths": changed_paths,
            "digest": candidate_diff_digest,
            "line_count": len(candidate_diff_text.splitlines()),
        },
        "path_gate": _gate_decision_to_dict(path_gate),
        "diff_gate": _gate_decision_to_dict(diff_gate),
        "next_required_gates": [
            "forensic_artifact_receipt",
            "draft_pr_creation",
            "ci_green",
            "mergeable_clean",
            "daily_rate_limit",
            "exact_head_merge",
        ],
    }


def _stored_consensus_artifact() -> dict[str, Any]:
    return {
        "artifact_version": STORED_CONSENSUS_ARTIFACT_VERSION,
        "artifact_id": STORED_CONSENSUS_ARTIFACT_ID,
        "created_at_utc": "2026-05-20T19:50:00Z",
        "source": "deterministic_a3_replay_fixture",
        "consensus": {
            "protocol_version": "idle-protocol.v1",
            "status": "soft_convergence",
            "consensus_target_proposal_id": "a3-counterfactual-delta-replay",
            "support_count": 3,
            "objection_count": 0,
        },
        "operator_review": {
            "required": True,
            "auto_execute": False,
            "reason": "stored consensus can only prepare a candidate diff for review",
        },
        "target": {
            "axis_id": "A3",
            "axis_name": "counterfactual_evaluation_delta",
            "candidate_changed_paths": list(STORED_CONSENSUS_CANDIDATE_CHANGED_PATHS),
        },
    }


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


def _gate_decision_to_dict(gate: Any) -> dict[str, Any]:
    return {
        "allowed": bool(gate.allowed),
        "reason": gate.reason,
        "blocked_paths": list(gate.blocked_paths),
        "unmatched_paths": list(gate.unmatched_paths),
        "code_pattern_hits": list(gate.code_pattern_hits),
    }


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
        "stored_consensus_digest": stored_consensus_replay["stored_consensus"][
            "digest"
        ],
        "candidate_diff_digest": stored_consensus_replay["candidate_diff"]["digest"],
        "candidate_diff_charter_allowed": bool(
            stored_consensus_replay["candidate_diff_charter_allowed"]
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
