# SPDX-License-Identifier: BUSL-1.1
"""Run an opt-in MAGMA composition demo from corpus to verified receipts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.demo_policy import (  # noqa: E402
    DEMO_POLICY_VERSION,
    demo_policy_for_case,
    demo_policy_supports_case,
)
from waggledance.core.magma.evaluation_result import build_evaluation_result  # noqa: E402
from waggledance.core.magma.receipt import build_magma_receipt  # noqa: E402


DEFAULT_CORPUS = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0.json"
CASE_SCHEMA = ROOT / "schemas" / "v3_13_0" / "synthetic_adversarial_case.v0.json"
DEMO_VERSION = "magma.composition_demo.v0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and verify a local MAGMA composition demo chain.",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--case-limit", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_composition_demo(
            corpus_path=args.corpus,
            out_dir=args.out_dir,
            case_limit=args.case_limit,
        )
    except ValueError as exc:
        print(f"magma composition demo FAILED: {exc}", file=sys.stderr)
        return 1

    verifier_ok = bool(report["verifier_report"]["ok"])
    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif verifier_ok:
        print(
            "magma composition demo OK: "
            f"{report['verifier_report']['receipt_count']} receipts in {report['out_dir']}"
        )
    else:
        print("magma composition demo FAILED: verifier errors", file=sys.stderr)
        return 1
    return 0 if verifier_ok else 1


def build_composition_demo(
    *,
    corpus_path: Path = DEFAULT_CORPUS,
    out_dir: Path,
    case_limit: int = 3,
) -> dict[str, Any]:
    if case_limit < 1:
        raise ValueError("case_limit must be at least 1")
    _prepare_out_dir(out_dir)

    cases = _load_corpus_cases(corpus_path)
    skipped_external_effect_count = sum(
        1 for case in cases if case["risk_class"] == "external_effect"
    )
    eligible_cases = [
        case
        for case in cases
        if case["risk_class"] != "external_effect"
        and demo_policy_supports_case(case)
    ][:case_limit]
    if not eligible_cases:
        raise ValueError("corpus contains no non-external_effect demo-eligible cases")

    entries: list[dict[str, str]] = []
    case_ids: list[str] = []
    previous_receipt: dict[str, Any] | None = None

    for index, case in enumerate(eligible_cases, 1):
        payload = _payload_for_case(case)
        evaluation = _evaluation_for_case(case, payload)
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
        case_ids.append(case["case_id"])

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
        "case_count": len(case_ids),
        "chain_length": len(entries),
        "case_ids": case_ids,
        "skipped_external_effect_count": skipped_external_effect_count,
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


def _load_corpus_cases(corpus_path: Path) -> list[dict[str, Any]]:
    corpus = _read_json(corpus_path)
    if corpus.get("corpus_version") != "magma.synthetic_adversarial_corpus.v0":
        raise ValueError("corpus_version must be magma.synthetic_adversarial_corpus.v0")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("corpus cases must be a non-empty array")
    validator = _case_validator()
    errors: list[str] = []
    for index, case in enumerate(cases, 1):
        for error in sorted(validator.iter_errors(case), key=lambda item: list(item.path)):
            path = ".".join(str(part) for part in error.path) or "<root>"
            errors.append(f"case {index}: schema error at {path}")
    if errors:
        raise ValueError("; ".join(errors))
    return cases


def _case_validator() -> jsonschema.Draft7Validator:
    schema = _read_json(CASE_SCHEMA)
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


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
    payload: dict[str, Any],
) -> dict[str, Any]:
    policy = demo_policy_for_case(case)
    return build_evaluation_result(
        case_id=case["case_id"],
        subject_type="peer_review",
        target_payload=payload,
        risk_class=case["risk_class"],
        expected_gate=policy["actual_gate"],
        actual_gate=policy["actual_gate"],
        verifier_path=[
            "synthetic_adversarial_corpus_v0",
            "magma_evaluation_result_v0",
            "magma_receipt_v1",
            "offline_receipt_verifier",
        ],
        solver_selection=["synthetic_adversarial_oracle_v0"],
        policy_version=DEMO_POLICY_VERSION,
        charter_version="charter:v1",
        domain_threshold_version="threshold:synthetic_adversarial:v0",
        verdict=policy["verdict"],
        reason_codes=policy["reason_codes"],
        confidence_score=1.0,
        uncertainty_sources=[],
    )


def _receipt_for_case(
    index: int,
    case: dict[str, Any],
    payload: dict[str, Any],
    evaluation: dict[str, Any],
    previous_receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    risk_class = case["risk_class"]
    return build_magma_receipt(
        event_id=f"magma:composition:{index:03d}",
        ts_utc=f"2026-05-17T07:{index:02d}:00Z",
        risk_class=risk_class,
        payload=payload,
        evaluation_result=evaluation,
        previous_receipt=previous_receipt,
        policy_digest=sha256_digest({"policy_version": DEMO_POLICY_VERSION}),
        charter_digest=sha256_digest({"charter": "v1"}),
        rco_decision_digest=sha256_digest({"rco": "composition_demo", "index": index}),
        world_snapshot_digest=sha256_digest({"case_id": case["case_id"], "v": 0}),
        solver_contract_digest=sha256_digest({"solver": "synthetic_adversarial_oracle_v0"}),
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
