# SPDX-License-Identifier: BUSL-1.1
"""Build the MAGMA 100h sprint baseline from current proof surfaces.

This tool is intentionally a coordinator, not a new proof source. It collects
the existing V12/MAGMA proof summary, normalizes the fields that matter for the
100h sprint kickoff, and writes a machine-readable baseline artifact.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.show_v12_proof import collect_proof  # noqa: E402


SCHEMA_VERSION = "waggledance.magma_100h_sprint_baseline.v0"
DEFAULT_SPRINT_ID = "magma-100h-2026-05-23"
DEFAULT_OUTPUT = (
    Path("docs")
    / "runs"
    / "magma_100h_sprint_2026_05_23"
    / "baseline.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--sprint-id", default=DEFAULT_SPRINT_ID)
    parser.add_argument(
        "--since-utc-days",
        type=int,
        default=1,
        help="Merged-PR velocity window forwarded to show_v12_proof.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    v12_proof = collect_proof(
        repo_root=args.repo_root,
        since_days=args.since_utc_days,
    )
    baseline = build_baseline(
        v12_proof=v12_proof,
        sprint_id=args.sprint_id,
    )
    encoded = json.dumps(baseline, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    if args.json:
        print(encoded, end="")
    return 0 if baseline["ok"] else 1


def build_baseline(
    *,
    v12_proof: dict[str, Any],
    sprint_id: str = DEFAULT_SPRINT_ID,
    generated_at_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at_utc = generated_at_utc or datetime.now(timezone.utc)
    blockers = _baseline_blockers(v12_proof)
    required_claude_roles = [
        "independent_audit",
        "adversarial_review",
        "rco_before_merge",
        "competitor_counter_read",
        "phase_synthesis",
    ]
    baseline = {
        "schema_version": SCHEMA_VERSION,
        "sprint_id": sprint_id,
        "generated_at_utc": _format_utc(generated_at_utc),
        "ok": not blockers,
        "blockers": blockers,
        "release_boundary": {
            "stable_release_claim": False,
            "tag_creation": False,
            "docker_latest_move": False,
            "external_effect_authority_change": False,
        },
        "current_state": {
            "receipt_adoption": _receipt_adoption_summary(v12_proof),
            "adversarial_corpus": _adversarial_summary(v12_proof),
            "a3_counterfactual_axis": _axis_summary(
                v12_proof.get("a3_counterfactual_axis", {}),
                proven_field="counterfactual_delta_proven",
            ),
            "a4_solver_growth_axis": _axis_summary(
                v12_proof.get("a4_solver_growth_axis", {}),
                proven_field="solver_growth_proven",
            ),
            "governance_throughput": v12_proof.get("governance_throughput", {}),
            "competitor_pilot": _competitor_summary(v12_proof),
        },
        "next_work_packages": [
            {
                "id": "runtime_receipt_emission_one_path",
                "owner": "codex",
                "peer": "claude",
                "target": "one concrete solver/demo path emits verifiable receipt + EvaluationResult",
                "acceptance": "manifest verifies with tools/verify_magma_receipt.py and tests cover tampered payload/evaluation result",
            },
            {
                "id": "counterfactual_evaluation_demo_a3",
                "owner": "codex",
                "peer": "claude",
                "target": "three deterministic variants show receipt-bound EvaluationResult diffs",
                "acceptance": "diff includes gate, solver selection/verifier path or explicit unavailable guardrail, risk/verdict/reason codes",
            },
            {
                "id": "solver_growth_lifecycle_a4",
                "owner": "codex",
                "peer": "claude",
                "target": "candidate -> shadow evaluation -> receipt-bound promotion evidence",
                "acceptance": "promotion proof fails closed without EvaluationResult, receipt, solver contract digest, and operator gate flag where required",
            },
            {
                "id": "rival_axis_upgrade",
                "owner": "claude",
                "peer": "codex",
                "target": "upgrade competitor matrix labels without claiming consensus-grade rival benchmark",
                "acceptance": "A3/A4 WD evidence linked; rival local checks remain public-doc/not-run unless actually measured",
            },
        ],
        "claude_activation_contract": {
            "required_roles": required_claude_roles,
            "heartbeat_only_timeout_minutes": 5,
            "rco_timeout_minutes_after_ci_green": 10,
            "must_emit_substantive_event_per_phase": True,
        },
        "forbidden_claims": [
            "beats all competitors",
            "world best AI",
            "AGI",
            "consciousness",
            "production-ready fleet learning",
            "public cryptographic verification parity",
            "rival benchmark consensus-grade",
        ],
    }
    return baseline


def _baseline_blockers(v12_proof: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if v12_proof.get("ok") is not True:
        blockers.append("v12_proof_not_ok")

    adoption = v12_proof.get("adoption", {})
    if adoption.get("available") is not True:
        blockers.append("receipt_adoption_unavailable")
    if int(adoption.get("high_criticality_gap_count", 1) or 0) != 0:
        blockers.append("high_criticality_receipt_gaps_present")
    if int(adoption.get("action_required_gap_count", 1) or 0) != 0:
        blockers.append("action_required_receipt_gaps_present")

    adversarial = v12_proof.get("adversarial_eval", {})
    if adversarial.get("ok") is not True:
        blockers.append("adversarial_eval_not_ok")

    a3 = v12_proof.get("a3_counterfactual_axis", {})
    if a3.get("available") is not True:
        blockers.append("a3_counterfactual_axis_unavailable")
    if a3.get("counterfactual_delta_proven") is not True:
        blockers.append("a3_counterfactual_delta_not_proven")
    if a3.get("receipt_chain_verified") is not True:
        blockers.append("a3_receipt_chain_not_verified")

    a4 = v12_proof.get("a4_solver_growth_axis", {})
    if a4.get("available") is not True:
        blockers.append("a4_solver_growth_axis_unavailable")
    if a4.get("solver_growth_proven") is not True:
        blockers.append("a4_solver_growth_not_proven")
    if a4.get("receipt_chain_verified") is not True:
        blockers.append("a4_receipt_chain_not_verified")

    competitor = v12_proof.get("competitor_pilot", {})
    if competitor.get("available") is not True:
        blockers.append("competitor_pilot_unavailable")
    if competitor.get("consensus_grade") is True:
        blockers.append("competitor_pilot_overclaims_consensus_grade")

    return blockers


def _receipt_adoption_summary(v12_proof: dict[str, Any]) -> dict[str, Any]:
    adoption = v12_proof.get("adoption", {})
    return {
        "available": adoption.get("available") is True,
        "high_criticality_gap_count": adoption.get("high_criticality_gap_count"),
        "action_required_gap_count": adoption.get("action_required_gap_count"),
        "accepted_exception_count": adoption.get("accepted_exception_count"),
        "medium_gap_targets": adoption.get("medium_gap_targets", []),
        "status_counts": adoption.get("status_counts", {}),
    }


def _adversarial_summary(v12_proof: dict[str, Any]) -> dict[str, Any]:
    adversarial = v12_proof.get("adversarial_eval", {})
    return {
        "available": adversarial.get("available") is True,
        "ok": adversarial.get("ok") is True,
        "case_count": adversarial.get("case_count"),
        "pass_count": adversarial.get("pass_count"),
        "fail_count": adversarial.get("fail_count"),
        "gate_accuracy": adversarial.get("gate_accuracy"),
        "verdict_accuracy": adversarial.get("verdict_accuracy"),
        "reason_code_accuracy": adversarial.get("reason_code_accuracy"),
    }


def _axis_summary(axis: dict[str, Any], *, proven_field: str) -> dict[str, Any]:
    return {
        "available": axis.get("available") is True,
        "claim_label": axis.get("claim_label"),
        proven_field: axis.get(proven_field) is True,
        "receipt_chain_verified": axis.get("receipt_chain_verified") is True,
    }


def _competitor_summary(v12_proof: dict[str, Any]) -> dict[str, Any]:
    competitor = v12_proof.get("competitor_pilot", {})
    return {
        "available": competitor.get("available") is True,
        "pilot_status": competitor.get("pilot_status"),
        "consensus_grade": competitor.get("consensus_grade") is True,
        "rival_local_checks_status": competitor.get("rival_local_checks_status"),
        "must_win_axes": competitor.get("must_win_axes", []),
        "ceded_axes": competitor.get("ceded_axes", []),
        "rivals": competitor.get("rivals", []),
    }


def _format_utc(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
