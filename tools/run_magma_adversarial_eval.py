# SPDX-License-Identifier: BUSL-1.1
"""Run a read-only MAGMA adversarial corpus evaluation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
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
from waggledance.core.magma.adversarial_corpus_eval import (  # noqa: E402
    build_per_case_coverage_report,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.demo_policy import (  # noqa: E402
    DEMO_POLICY_VERSION,
    demo_policy_for_case,
)
from waggledance.core.magma.evaluation_result import build_evaluation_result  # noqa: E402
from waggledance.core.magma.receipt import build_magma_receipt  # noqa: E402
from waggledance.core.magma.receipt_bundle import (  # noqa: E402
    ReceiptBundleEntry,
    write_receipt_bundle,
)
from tools.verify_magma_receipt import verify_manifest  # noqa: E402


EVAL_VERSION = "magma.adversarial_eval.v1"
ADVERSARIAL_EVAL_RECEIPT_POLICY_VERSION = "policy:magma_adversarial_eval:v0"
ADVERSARIAL_EVAL_RECEIPT_THRESHOLD_VERSION = "threshold:synthetic_adversarial:v0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score MAGMA synthetic adversarial cases against expectations.",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--bound-solver-hash",
        default=None,
        help=(
            "Optional exact solver artifact hash to bind into the report for "
            "promotion-gate verification."
        ),
    )
    parser.add_argument(
        "--receipt-out-dir",
        type=Path,
        default=None,
        help="Optional empty output directory for a verified MAGMA receipt bundle.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for receipt bundle emission.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_adversarial_eval_report(
            corpus_path=args.corpus,
            expectations_path=args.expectations,
            bound_solver_hash=args.bound_solver_hash,
            receipt_out_dir=args.receipt_out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
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
        if "receipt_bundle" in report:
            print(
                "MAGMA receipt bundle: "
                f"{report['receipt_bundle']['receipt_count']} receipt in "
                f"{report['receipt_bundle']['out_dir']}"
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
    bound_solver_hash: str | None = None,
    receipt_out_dir: Path | None = None,
    now_utc: datetime | None = None,
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
            "defect_class": case["defect_type"],
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
    report = {
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
        "coverage": _coverage_for_cases(corpus["cases"]),
        "per_case_coverage": build_per_case_coverage_report(cases),
        "cases": cases,
        "failures": failures,
    }
    if bound_solver_hash is not None:
        if not isinstance(bound_solver_hash, str) or not bound_solver_hash.strip():
            raise ValueError("bound_solver_hash must be a non-empty string")
        report["bound_solver_hash"] = bound_solver_hash
    if receipt_out_dir is not None:
        report["receipt_bundle"] = _emit_receipt_bundle(
            report=report,
            out_dir=receipt_out_dir,
            now_utc=now_utc or datetime.now(timezone.utc),
        )
    return report


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


def _emit_receipt_bundle(
    *,
    report: dict[str, Any],
    out_dir: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    payload = _receipt_payload_for_report(report)
    evaluation = build_evaluation_result(
        case_id="case:adversarial_eval:report:v0",
        subject_type="peer_review",
        target_payload=payload,
        risk_class="local_artifact",
        expected_gate="allow",
        actual_gate="allow" if report["ok"] else "review",
        verifier_path=[
            "synthetic_adversarial_eval_v0",
            "evaluation_result_schema_v0",
            "magma_receipt_v1",
        ],
        solver_selection=["synthetic_adversarial_demo_policy_v0"],
        policy_version=ADVERSARIAL_EVAL_RECEIPT_POLICY_VERSION,
        charter_version="charter:v1",
        domain_threshold_version=ADVERSARIAL_EVAL_RECEIPT_THRESHOLD_VERSION,
        verdict="pass" if report["ok"] else "fail",
        reason_codes=_receipt_reason_codes(report),
        confidence_score=1.0,
        uncertainty_sources=[],
    )
    receipt = build_magma_receipt(
        event_id="magma:adversarial_eval:report:v0",
        ts_utc=_iso_utc(now_utc),
        risk_class="local_artifact",
        payload=payload,
        evaluation_result=evaluation,
        policy_digest=sha256_digest({
            "policy_version": ADVERSARIAL_EVAL_RECEIPT_POLICY_VERSION,
        }),
        charter_digest=sha256_digest({"charter_version": "charter:v1"}),
        rco_decision_digest=sha256_digest({
            "actual_gate": evaluation["actual_gate"],
            "case_id": evaluation["case_id"],
            "verdict": evaluation["verdict"],
        }),
        world_snapshot_digest=sha256_digest({
            "corpus_digest": report["corpus_digest"],
            "expectations_digest": report["expectations_digest"],
            "case_count": report["case_count"],
            "writes_applied": report["writes_applied"],
        }),
        solver_contract_digest=sha256_digest({
            "solver_selection": evaluation["solver_selection"],
            "policy_version": DEMO_POLICY_VERSION,
        }),
    )
    return write_receipt_bundle(
        out_dir=out_dir,
        chain_id="magma:adversarial_eval:v0",
        entries=[
            ReceiptBundleEntry(
                label="report",
                payload=payload,
                evaluation_result=evaluation,
                receipt=receipt,
            )
        ],
        verify_manifest=verify_manifest,
    )


def _receipt_payload_for_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "payload_version": "magma.adversarial_eval_receipt_payload.v0",
        "eval_version": report["eval_version"],
        "ok": report["ok"],
        "writes_applied": report["writes_applied"],
        "corpus_digest": report["corpus_digest"],
        "expectations_digest": report["expectations_digest"],
        "case_count": report["case_count"],
        "pass_count": report["pass_count"],
        "fail_count": report["fail_count"],
        "gate_accuracy": report["gate_accuracy"],
        "verdict_accuracy": report["verdict_accuracy"],
        "reason_code_accuracy": report["reason_code_accuracy"],
        "coverage": report["coverage"],
        "per_case_coverage": report["per_case_coverage"],
        "case_evaluation_result_digests": [
            {
                "case_id": case["case_id"],
                "defect_class": case["defect_class"],
                "evaluation_result_digest": case["evaluation_result_digest"],
                "ok": case["ok"],
                "status": case["status"],
            }
            for case in report["cases"]
        ],
    }


def _receipt_reason_codes(report: dict[str, Any]) -> list[str]:
    codes = [
        "adversarial_eval:report",
        "adversarial_eval:writes_applied:false",
        (
            "adversarial_eval:pass"
            if report["ok"]
            else "adversarial_eval:fail"
        ),
        f"adversarial_eval:cases:{report['case_count']}",
    ]
    if report["fail_count"]:
        codes.append(f"adversarial_eval:failures:{report['fail_count']}")
    return codes


def _coverage_for_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    defect_counts = Counter(str(case.get("defect_type", "")) for case in cases)
    risk_counts = Counter(str(case.get("risk_class", "")) for case in cases)

    def tagged_count(*tags: str) -> int:
        required = set(tags)
        return sum(1 for case in cases if required <= set(case.get("tags") or []))

    def tagged_any_count(*tag_sets: tuple[str, ...]) -> int:
        return sum(
            1
            for case in cases
            if any(set(tags) <= set(case.get("tags") or []) for tags in tag_sets)
        )

    return {
        "defect_type_counts": dict(sorted(defect_counts.items())),
        "risk_class_counts": dict(sorted(risk_counts.items())),
        "privacy_canary_case_count": sum(
            1 for case in cases if isinstance(case.get("privacy_canary"), str)
        ),
        "receipt_binding_case_count": tagged_count("receipt")
        + tagged_count("receipt_digest"),
        "evaluation_result_case_count": tagged_count("evaluation_result"),
        "counterfactual_case_count": tagged_count("counterfactual"),
        "operator_gate_case_count": tagged_count("operator_gate"),
        "hidden_tool_case_count": tagged_count("hidden_tools"),
        "clean_baseline_case_count": tagged_any_count(
            ("clean_baseline",),
            ("false_positive", "allow_gate"),
        ),
    }


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


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("--now requires a UTC timestamp with Z or +00:00 suffix")
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


if __name__ == "__main__":
    raise SystemExit(main())
