# SPDX-License-Identifier: BUSL-1.1
"""Run a read-only MAGMA adversarial corpus evaluation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_synthetic_adversarial_corpus import (  # noqa: E402
    DEFAULT_CORPUS,
    DEFAULT_EXPECTATIONS,
    validate_corpus,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.demo_policy import (  # noqa: E402
    DEMO_POLICY_VERSION,
    demo_policy_for_case,
)
from waggledance.core.magma.evaluation_result import build_evaluation_result  # noqa: E402


EVAL_VERSION = "magma.adversarial_eval.v0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score MAGMA synthetic adversarial cases against expectations.",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_adversarial_eval_report(
            corpus_path=args.corpus,
            expectations_path=args.expectations,
        )
        if args.out is not None:
            _write_report(args.out, report)
    except ValueError as exc:
        print(f"magma adversarial eval FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif report["ok"]:
        print(
            "magma adversarial eval OK: "
            f"{report['pass_count']}/{report['case_count']} cases passed"
        )
    else:
        print(
            "magma adversarial eval FAILED: "
            f"{report['fail_count']} failures",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_adversarial_eval_report(
    *,
    corpus_path: Path = DEFAULT_CORPUS,
    expectations_path: Path = DEFAULT_EXPECTATIONS,
) -> dict[str, Any]:
    validation = validate_corpus(corpus_path, expectations_path)
    if not validation["ok"]:
        raise ValueError(_redacted_validation_message(validation))

    corpus = _read_json(corpus_path)
    expectations = _expectations_by_case(_read_json(expectations_path))
    cases = []
    failures = []
    gate_matches = 0
    verdict_matches = 0
    reason_matches = 0

    for case in corpus["cases"]:
        expectation = expectations[case["case_id"]]
        actual = _actual_for_case(case)
        payload = _payload_for_case(case)
        evaluation_result = _evaluation_for_case(case, expectation, actual, payload)
        expected_reasons = set(expectation["expected_reason_codes"])
        actual_reasons = set(actual["reason_codes"])
        gate_ok = actual["actual_gate"] == expectation["expected_gate"]
        verdict_ok = actual["verdict"] == expectation["expected_verdict"]
        reasons_ok = actual_reasons == expected_reasons
        case_ok = gate_ok and verdict_ok and reasons_ok
        status = _case_status(gate_ok, verdict_ok, reasons_ok)

        gate_matches += int(gate_ok)
        verdict_matches += int(verdict_ok)
        reason_matches += int(reasons_ok)
        case_report = {
            "case_id": case["case_id"],
            "risk_class": case["risk_class"],
            "status": status,
            "gate_mismatch": not gate_ok,
            "verdict_mismatch": not verdict_ok,
            "reason_codes_mismatch": not reasons_ok,
            "evaluation_result_digest": sha256_digest(evaluation_result),
            "ok": case_ok,
            "operator_required": evaluation_result["operator_required"],
        }
        cases.append(case_report)
        if not case_ok:
            failures.append(case_report)

    case_count = len(cases)
    fail_count = len(failures)
    return {
        "eval_version": EVAL_VERSION,
        "ok": fail_count == 0,
        "writes_applied": False,
        "corpus_digest": sha256_digest(corpus),
        "expectations_digest": sha256_digest(_read_json(expectations_path)),
        "case_count": case_count,
        "pass_count": case_count - fail_count,
        "fail_count": fail_count,
        "full_match_count": sum(1 for case in cases if case["status"] == "full_match"),
        "partial_match_count": sum(1 for case in cases if case["status"] == "partial_match"),
        "mismatch_count": sum(1 for case in cases if case["status"] == "mismatch"),
        "gate_accuracy": _ratio(gate_matches, case_count),
        "verdict_accuracy": _ratio(verdict_matches, case_count),
        "reason_code_accuracy": _ratio(reason_matches, case_count),
        "catch_agent_bucket_status": "redacted_hidden_expectations_v0",
        "cases": cases,
        "failures": failures,
    }


def _actual_for_case(case: dict[str, Any]) -> dict[str, Any]:
    return demo_policy_for_case(case)


def _payload_for_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "payload_version": "magma.adversarial_eval_payload.v0",
        "case_id": case["case_id"],
        "defect_type": case["defect_type"],
        "risk_class": case["risk_class"],
        "intent_digest": sha256_digest({"intent": case["intent"]}),
        "tags": sorted(case["tags"]),
    }


def _evaluation_for_case(
    case: dict[str, Any],
    expectation: dict[str, Any],
    actual: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return build_evaluation_result(
        case_id=case["case_id"],
        subject_type="peer_review",
        target_payload=payload,
        risk_class=case["risk_class"],
        expected_gate=expectation["expected_gate"],
        actual_gate=actual["actual_gate"],
        verifier_path=["synthetic_adversarial_eval_v0", "evaluation_result_schema_v0"],
        solver_selection=["synthetic_adversarial_demo_policy_v0"],
        policy_version=DEMO_POLICY_VERSION,
        charter_version="charter:v1",
        domain_threshold_version="threshold:synthetic_adversarial:v0",
        verdict=actual["verdict"],
        reason_codes=actual["reason_codes"],
        confidence_score=1.0,
        uncertainty_sources=[],
        allow_external_effect=case["risk_class"] == "external_effect",
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


def _case_status(gate_ok: bool, verdict_ok: bool, reasons_ok: bool) -> str:
    if not gate_ok:
        return "mismatch"
    if verdict_ok and reasons_ok:
        return "full_match"
    return "partial_match"


def _redacted_validation_message(validation: dict[str, Any]) -> str:
    error_count = len(validation.get("errors") or [])
    case_count = validation.get("case_count", 0)
    return f"corpus validation failed: {error_count} errors across {case_count} cases"


def _expectations_by_case(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {expectation["case_id"]: expectation for expectation in doc["expectations"]}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_report(path: Path, report: dict[str, Any]) -> None:
    if path.exists():
        raise ValueError(f"out report already exists: {path}")
    if not path.parent.exists():
        raise ValueError(f"out report parent does not exist: {path.parent}")
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
