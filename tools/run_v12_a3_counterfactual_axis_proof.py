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
    if receipt_out_dir is not None:
        receipt_now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        demo["receipt_bundle"] = _emit_a3_v1_receipt_bundle(
            demo,
            receipt_out_dir,
            receipt_now,
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
        "ok": bool(counterfactual_delta_proven),
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
        "evidence_sources": [
            "tools/run_pdam_counterfactual_demo.py",
            "schemas/v3_13_0/evaluation_result.v1.json",
            "tools/verify_magma_receipt.py",
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
                    "verdict": evaluation["verdict"],
                }),
                world_snapshot_digest=sha256_digest({
                    "case_id": variant["case_id"],
                    "scenario": side,
                    "subtool_state": scenario["subtool_state"],
                }),
                solver_contract_digest=sha256_digest({
                    "solver_selection": evaluation["solver_selection"],
                    "policy_version": evaluation["policy_version"],
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
