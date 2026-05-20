# SPDX-License-Identifier: BUSL-1.1
"""Build a clean V12 A3 counterfactual-evaluation proof row."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_pdam_counterfactual_demo import build_demo_report  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


REPORT_VERSION = "wd.v12.a3_counterfactual_axis_proof.v0"


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
    demo = build_demo_report(out_dir=receipt_out_dir, now_utc=now_utc)
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
    counterfactual_delta_proven = (
        demo["writes_applied"] is False
        and required_delta_fields.issubset(delta_fields)
        and factual["evaluation_result"]["risk_class"] == "internal_memory"
        and counterfactual["evaluation_result"]["risk_class"] == "internal_memory"
    )

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "ok": bool(counterfactual_delta_proven),
        "axis_id": "A3",
        "axis_name": "counterfactual_evaluation_delta",
        "claim_label": "MEASURED_LOCAL_PARTIAL",
        "case_id": demo["case_id"],
        "source_demo_version": demo["demo_version"],
        "writes_applied": demo["writes_applied"],
        "counterfactual_delta_proven": bool(counterfactual_delta_proven),
        "delta_field_count": len(delta_fields),
        "delta_fields": delta_fields,
        "delta": delta,
        "factual": _scenario_summary(factual),
        "counterfactual": _scenario_summary(counterfactual),
        "receipt_chain_verified": receipt_chain_verified,
        "receipt_bundle": _receipt_summary(receipt_bundle),
        "evidence_sources": [
            "tools/run_pdam_counterfactual_demo.py",
            "schemas/v3_13_0/evaluation_result.v0.json",
            "tools/verify_magma_receipt.py",
        ],
        "no_overclaim_guardrails": {
            "not_a_rival_benchmark": True,
            "does_not_claim_external_effect_execution": True,
            "does_not_apply_writes": True,
            "measures_one_local_fixture": True,
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
        f"- counterfactual_delta_proven: `{str(report['counterfactual_delta_proven']).lower()}`",
        f"- writes_applied: `{str(report['writes_applied']).lower()}`",
        f"- receipt_chain_verified: `{receipt_state}`",
        "",
        "| Field | Factual | Counterfactual |",
        "|---|---|---|",
        f"| action kind | `{delta['kind'][0]}` | `{delta['kind'][1]}` |",
        f"| actual gate | `{delta['actual_gate'][0]}` | `{delta['actual_gate'][1]}` |",
        f"| verdict | `{delta['verdict'][0]}` | `{delta['verdict'][1]}` |",
        "",
        "This is one local measured counterfactual row. It is not a rival benchmark,",
        "does not execute an external effect, and does not claim broad semantic",
        "counterfactual coverage beyond this fixture.",
        "",
    ]
    return "\n".join(lines)


def _scenario_summary(scenario: dict[str, Any]) -> dict[str, Any]:
    evaluation = scenario["evaluation_result"]
    return {
        "label": scenario["label"],
        "subtool_state": scenario["subtool_state"],
        "action_kind": scenario["action"]["kind"],
        "actual_gate": evaluation["actual_gate"],
        "expected_gate": evaluation["expected_gate"],
        "verdict": evaluation["verdict"],
        "risk_class": evaluation["risk_class"],
        "operator_required": evaluation["operator_required"],
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


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("--now requires a UTC timestamp with Z or +00:00 suffix")
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
