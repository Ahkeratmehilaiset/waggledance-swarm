# SPDX-License-Identifier: BUSL-1.1
"""Run an opt-in MAGMA composition demo from corpus to verified receipts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_synthetic_adversarial_corpus import validate_corpus  # noqa: E402
from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.evaluation_result import build_evaluation_result  # noqa: E402
from waggledance.core.magma.receipt import build_magma_receipt  # noqa: E402


DEFAULT_CORPUS = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0.json"
DEFAULT_EXPECTATIONS = (
    ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0_expectations.json"
)
DEMO_VERSION = "magma.composition_demo.v0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and verify a local MAGMA composition demo chain.",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--case-limit", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_composition_demo(
            corpus_path=args.corpus,
            expectations_path=args.expectations,
            out_dir=args.out_dir,
            case_limit=args.case_limit,
        )
    except ValueError as exc:
        print(f"magma composition demo FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif report["verify_ok"]:
        print(
            "magma composition demo OK: "
            f"{report['receipt_count']} receipts in {report['out_dir']}"
        )
    else:
        print("magma composition demo FAILED: verifier errors", file=sys.stderr)
        return 1
    return 0


def build_composition_demo(
    *,
    corpus_path: Path = DEFAULT_CORPUS,
    expectations_path: Path = DEFAULT_EXPECTATIONS,
    out_dir: Path,
    case_limit: int = 3,
) -> dict[str, Any]:
    if case_limit < 1:
        raise ValueError("case_limit must be at least 1")
    _prepare_out_dir(out_dir)

    validation = validate_corpus(corpus_path, expectations_path)
    if not validation["ok"]:
        raise ValueError("; ".join(validation["errors"]))

    corpus = _read_json(corpus_path)
    expectations = _expectations_by_case(_read_json(expectations_path))
    entries: list[dict[str, str]] = []
    cases_report: list[dict[str, Any]] = []
    previous_receipt: dict[str, Any] | None = None

    for index, case in enumerate(corpus["cases"][:case_limit], 1):
        expectation = expectations[case["case_id"]]
        payload = _payload_for_case(case)
        evaluation = _evaluation_for_case(case, expectation, payload)
        receipt = _receipt_for_case(index, case, payload, evaluation, previous_receipt)
        previous_receipt = receipt

        payload_name = f"payload-{index:03d}.json"
        evaluation_name = f"evaluation-{index:03d}.json"
        receipt_name = f"receipt-{index:03d}.json"
        _write_json(out_dir / payload_name, payload)
        _write_json(out_dir / evaluation_name, evaluation)
        _write_json(out_dir / receipt_name, receipt)
        entries.append(
            {
                "payload": payload_name,
                "evaluation_result": evaluation_name,
                "receipt": receipt_name,
            }
        )
        cases_report.append(
            {
                "case_id": case["case_id"],
                "risk_class": case["risk_class"],
                "expected_gate": expectation["expected_gate"],
                "verdict": expectation["expected_verdict"],
                "operator_gate_required": receipt["operator_gate_required"],
            }
        )

    manifest = {
        "chain_id": "magma:composition_demo:v0",
        "entries": entries,
    }
    manifest_path = out_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    verifier_report = verify_manifest(manifest_path)

    return {
        "demo_version": DEMO_VERSION,
        "writes_applied": False,
        "out_dir": str(out_dir),
        "manifest": str(manifest_path),
        "case_count": len(cases_report),
        "receipt_count": verifier_report["receipt_count"],
        "verify_ok": verifier_report["ok"],
        "verifier_errors": verifier_report["errors"],
        "operator_gate_required_count": sum(
            1 for case in cases_report if case["operator_gate_required"]
        ),
        "cases": cases_report,
    }


def _prepare_out_dir(out_dir: Path) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"out_dir must be empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)


def _payload_for_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "payload_version": "magma.composition_payload.v0",
        "case_id": case["case_id"],
        "defect_type": case["defect_type"],
        "risk_class": case["risk_class"],
        "intent_digest": sha256_digest({"intent": case["intent"]}),
        "tags": sorted(case["tags"]),
    }


def _evaluation_for_case(
    case: dict[str, Any],
    expectation: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return build_evaluation_result(
        case_id=case["case_id"],
        subject_type="peer_review",
        target_payload=payload,
        risk_class=case["risk_class"],
        expected_gate=expectation["expected_gate"],
        actual_gate=expectation["expected_gate"],
        verifier_path=[
            "synthetic_adversarial_corpus_v0",
            "magma_evaluation_result_v0",
            "magma_receipt_v1",
            "offline_receipt_verifier",
        ],
        solver_selection=["synthetic_adversarial_oracle_v0"],
        policy_version="policy:synthetic_adversarial_oracle:v0",
        charter_version="charter:v1",
        domain_threshold_version="threshold:synthetic_adversarial:v0",
        verdict=expectation["expected_verdict"],
        reason_codes=expectation["expected_reason_codes"],
        confidence_score=1.0,
        uncertainty_sources=[],
        allow_external_effect=case["risk_class"] == "external_effect",
    )


def _receipt_for_case(
    index: int,
    case: dict[str, Any],
    payload: dict[str, Any],
    evaluation: dict[str, Any],
    previous_receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    risk_class = case["risk_class"]
    approval_id = None
    if risk_class == "external_effect":
        approval_id = f"bridge:demo_approval_required:{index:03d}"
    return build_magma_receipt(
        event_id=f"magma:composition:{index:03d}",
        ts_utc=f"2026-05-17T07:{index:02d}:00Z",
        risk_class=risk_class,
        payload=payload,
        evaluation_result=evaluation,
        previous_receipt=previous_receipt,
        policy_digest=sha256_digest({"policy": "synthetic_adversarial_oracle", "v": 0}),
        charter_digest=sha256_digest({"charter": "v1"}),
        rco_decision_digest=sha256_digest({"rco": "composition_demo", "index": index}),
        world_snapshot_digest=sha256_digest({"case_id": case["case_id"], "v": 0}),
        solver_contract_digest=sha256_digest({"solver": "synthetic_adversarial_oracle_v0"}),
        approval_id=approval_id,
        allow_external_effect=risk_class == "external_effect",
    )


def _expectations_by_case(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {expectation["case_id"]: expectation for expectation in doc["expectations"]}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
