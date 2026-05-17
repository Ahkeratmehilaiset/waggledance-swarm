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

from waggledance.core.magma.evaluation_result import build_evaluation_result  # noqa: E402
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
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_demo_report()
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            "PDAM counterfactual: "
            f"{report['factual']['action']['kind']} -> "
            f"{report['counterfactual']['action']['kind']}"
        )
    return 0


def build_demo_report() -> dict[str, Any]:
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
    return {
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


if __name__ == "__main__":
    raise SystemExit(main())
