# SPDX-License-Identifier: BUSL-1.1
"""Emit a local PDAM close-solver counterfactual EvaluationResult demo."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.evaluation_result import build_evaluation_result  # noqa: E402
from waggledance.core.magma.receipt import build_magma_receipt  # noqa: E402
from waggledance.core.pdam_close_solver import (  # noqa: E402
    LogbookEntry,
    MesComment,
    ToolState,
    plan_close_actions,
)


BASE = datetime(2026, 5, 15, 8, 0)
PRIVATE_MARKER = "operator_secret_goal_marker_DO_NOT_LEAK"
PDAM_GATE_BY_KIND = {
    "CLOSE_OK": "allow",
    "CLOSE_DUPLICATE": "allow",
    "KEEP_WIP": "review",
    "REVIEW": "review",
}
PDAM_CONFIDENCE_BY_KIND = {
    "CLOSE_OK": 1.0,
    "CLOSE_DUPLICATE": 0.9,
    "KEEP_WIP": 0.7,
    "REVIEW": 0.4,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local PDAM counterfactual EvaluationResult demo.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Optional empty output directory for a MAGMA receipt bundle.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_demo_report(out_dir=args.out_dir)
    except ValueError as exc:
        print(f"PDAM counterfactual demo FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            "PDAM counterfactual: "
            f"{report['factual']['action']['kind']} -> "
            f"{report['counterfactual']['action']['kind']}"
        )
        if "receipt_bundle" in report:
            print(
                "MAGMA receipt bundle: "
                f"{report['receipt_bundle']['receipt_count']} receipts in "
                f"{report['receipt_bundle']['out_dir']}"
            )
    return 0


def build_demo_report(*, out_dir: Path | None = None) -> dict[str, Any]:
    # Privacy canary: this operator-local metadata must never enter output.
    _private_metadata = {"operator_note": PRIVATE_MARKER}
    factual = _run_scenario(
        label="factual",
        depb_state=ToolState("SPUT_02_DEPB", "DOWNTIME", comment="DepB still locked"),
    )
    counterfactual = _run_scenario(
        label="counterfactual",
        depb_state=ToolState("SPUT_02_DEPB", "IDLE"),
        expected_gate=factual["evaluation_result"]["actual_gate"],
        mutation_reason="mutation:subtool_state:DOWNTIME_to_IDLE",
    )
    report = {
        "demo_version": "pdam.counterfactual_evaluation.v0",
        "case_id": "case:pdam:counterfactual:001",
        "writes_applied": False,
        "factual": factual,
        "counterfactual": counterfactual,
        "delta": {
            "kind": [
                factual["action"]["kind"],
                counterfactual["action"]["kind"],
            ],
            "actual_gate": [
                factual["evaluation_result"]["actual_gate"],
                counterfactual["evaluation_result"]["actual_gate"],
            ],
            "verdict": [
                factual["evaluation_result"]["verdict"],
                counterfactual["evaluation_result"]["verdict"],
            ],
        },
    }
    if out_dir is not None:
        report["receipt_bundle"] = _emit_receipt_bundle(report, out_dir)
    return report


def _run_scenario(
    *,
    label: str,
    depb_state: ToolState,
    expected_gate: str | None = None,
    mutation_reason: str | None = None,
) -> dict[str, Any]:
    entry = LogbookEntry(
        entry_id=5101,
        local_id=5101,
        log_code="em-repair-wp1",
        device="SPUT_02",
        status="WIP",
        created_at=BASE,
        issue="DepB chamber fault",
    )
    actions = plan_close_actions(
        entries=[entry],
        repair_timeline=[entry],
        tool_states={
            "SPUT_02": ToolState("SPUT_02", "IDLE"),
            "SPUT_02_DEPB": depb_state,
        },
        comments=[
            MesComment(
                "SPUT_02_DEPB",
                BASE + timedelta(hours=1),
                "HSS",
                "DepB repair evidence selected for the incident window.",
            )
        ],
        subtools={"SPUT_02": ["SPUT_02_DEPB"]},
        now=BASE + timedelta(hours=6),
    )
    if len(actions) != 1:
        raise RuntimeError(f"expected exactly one PDAM action, got {len(actions)}")
    action = actions[0]
    payload = asdict(action)
    return {
        "label": label,
        "subtool_state": depb_state.state,
        "action": payload,
        "evaluation_result": _evaluation_for_action(
            action,
            payload=payload,
            case_id=f"case:pdam:counterfactual:001:{label}",
            expected_gate=expected_gate,
            mutation_reason=mutation_reason,
        ),
    }


def _evaluation_for_action(
    action: Any,
    *,
    payload: dict[str, Any],
    case_id: str,
    expected_gate: str | None = None,
    mutation_reason: str | None = None,
) -> dict[str, Any]:
    actual_gate = PDAM_GATE_BY_KIND.get(action.kind, "review")
    expected = expected_gate or actual_gate
    verdict = "pass" if expected == actual_gate else "review"
    reason_codes = [
        f"pdam:{action.kind.lower()}",
        f"gate:{actual_gate}",
        "evidence:windowed_comments",
    ]
    if mutation_reason:
        reason_codes.append(mutation_reason)
        reason_codes.append(f"gate_drift:{expected}_to_{actual_gate}")
    return build_evaluation_result(
        case_id=case_id,
        subject_type="counterfactual",
        target_payload=payload,
        risk_class="internal_memory",
        expected_gate=expected,
        actual_gate=actual_gate,
        verifier_path=[
            "pdam_close_solver",
            "evaluation_result_schema_v0",
            "operator_gate_model",
        ],
        solver_selection=["pdam_close_solver"],
        policy_version="policy:pdam_close_solver:v1",
        charter_version="charter:v1",
        domain_threshold_version="threshold:pdam_close_solver:v1",
        verdict=verdict,
        reason_codes=reason_codes,
        confidence_score=PDAM_CONFIDENCE_BY_KIND.get(action.kind, 0.4),
        uncertainty_sources=[],
    )


def _emit_receipt_bundle(report: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    _prepare_out_dir(out_dir)
    entries: list[dict[str, str]] = []
    previous_receipt: dict[str, Any] | None = None
    for index, label in enumerate(("factual", "counterfactual"), 1):
        scenario = report[label]
        payload = scenario["action"]
        evaluation = scenario["evaluation_result"]
        receipt = build_magma_receipt(
            event_id=f"magma:pdam_counterfactual:{index:03d}:{label}",
            ts_utc=f"2026-05-17T12:{index:02d}:00Z",
            risk_class=evaluation["risk_class"],
            payload=payload,
            evaluation_result=evaluation,
            previous_receipt=previous_receipt,
            policy_digest=sha256_digest({"policy_version": evaluation["policy_version"]}),
            charter_digest=sha256_digest({"charter_version": evaluation["charter_version"]}),
            rco_decision_digest=sha256_digest({
                "actual_gate": evaluation["actual_gate"],
                "case_id": evaluation["case_id"],
                "verdict": evaluation["verdict"],
            }),
            world_snapshot_digest=sha256_digest({
                "case_id": report["case_id"],
                "scenario": label,
                "subtool_state": scenario["subtool_state"],
            }),
            solver_contract_digest=sha256_digest({
                "solver_selection": evaluation["solver_selection"],
                "policy_version": evaluation["policy_version"],
            }),
        )
        previous_receipt = receipt
        payload_name = f"payload-{index:03d}-{label}.json"
        evaluation_name = f"evaluation-{index:03d}-{label}.json"
        receipt_name = f"receipt-{index:03d}-{label}.json"
        _write_json(out_dir / payload_name, payload)
        _write_json(out_dir / evaluation_name, evaluation)
        _write_json(out_dir / receipt_name, receipt)
        entries.append({
            "payload": payload_name,
            "evaluation_result": evaluation_name,
            "receipt": receipt_name,
        })

    manifest = {
        "chain_id": "magma:pdam_counterfactual:v0",
        "entries": entries,
    }
    manifest_path = out_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    verifier_report = verify_manifest(manifest_path)
    return {
        "out_dir": str(out_dir),
        "manifest": str(manifest_path),
        "receipt_count": len(entries),
        "verifier_report": {
            "ok": verifier_report["ok"],
            "receipt_count": verifier_report["receipt_count"],
            "errors": verifier_report["errors"],
        },
    }


def _prepare_out_dir(out_dir: Path) -> None:
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=False)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
