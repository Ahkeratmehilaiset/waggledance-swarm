# SPDX-License-Identifier: BUSL-1.1
"""Emit a local proof for autogrowth promotion receipt wiring.

The proof exercises the real queue -> scheduler -> grower -> engine path with
the optional receipt sink wired in. It writes only local artifacts, verifies the
receipt chain offline, and confirms sink=None preserves existing behavior.
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

from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.autonomy_growth import (  # noqa: E402
    AutogrowthScheduler,
    LowRiskGrower,
    OUTCOME_AUTO_PROMOTED,
)
from waggledance.core.magma.receipt_bundle import (  # noqa: E402
    ReceiptBundleEntry,
    write_receipt_bundle,
)
from waggledance.core.storage.control_plane import ControlPlaneDB  # noqa: E402


REPORT_VERSION = "wd.magma.autogrowth_promotion_receipt_emission_proof.v0"
CHAIN_ID = "magma:v12_a4_autogrowth_scheduler_axis:v1"
CLAIM_LABEL = "MEASURED_LOCAL_PARTIAL"
AXIS_ID = "A4"
RUNTIME_PATH = (
    "AutogrowthScheduler.tick -> LowRiskGrower.grow_from_gap -> "
    "AutoPromotionEngine.evaluate_candidate"
)
_FAMILY_KIND = "scalar_unit_conversion"
_SOLVER_NAME_SEED = "autogrowth_receipt_solver"
_PRIVATE_SOURCE_MARKER = "private autogrowth seed source DO_NOT_LEAK"
_RAW_MARKERS = ("private autogrowth", "DO_NOT_LEAK")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="New output directory for the proof. It must not already exist.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-23T13:30:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_autogrowth_promotion_receipt_emission_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(f"autogrowth receipt emission proof FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(f"autogrowth receipt emission proof OK: {report['receipt_manifest']}")
    else:
        print(
            "autogrowth receipt emission proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_autogrowth_promotion_receipt_emission_proof(
    *,
    out_dir: Path,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()

    bundles: list[dict[str, Any]] = []
    tick_summary = _run_scheduler_sequence(
        db_path=out_dir / "control_plane.sqlite",
        bundles=bundles,
        emit_sink=True,
    )
    no_sink_bundles: list[dict[str, Any]] = []
    no_sink_summary = _run_scheduler_sequence(
        db_path=out_dir / "control_plane_no_sink.sqlite",
        bundles=no_sink_bundles,
        emit_sink=False,
    )

    receipt_dir = out_dir / "autogrowth_promotion_receipts"
    entries = [
        ReceiptBundleEntry(
            label=str(bundle["payload"]["decision"]),
            payload=bundle["payload"],
            evaluation_result=bundle["evaluation_result"],
            receipt=bundle["receipt"],
        )
        for bundle in bundles
    ]
    bundle_report = write_receipt_bundle(
        out_dir=receipt_dir,
        chain_id=CHAIN_ID,
        entries=entries,
        verify_manifest=verify_manifest,
    )
    manifest_path = Path(str(bundle_report["manifest"]))
    verifier_report = verify_manifest(manifest_path)
    leak_free = _raw_payload_leak_free(out_dir)

    transitions = [str(bundle["payload"]["decision"]) for bundle in bundles]
    counterfactual = _counterfactual_from_bundles(bundles)
    expected_transitions = [OUTCOME_AUTO_PROMOTED]
    blockers: list[str] = []
    if tick_summary["outcome"] != OUTCOME_AUTO_PROMOTED:
        blockers.append(f"scheduler_outcome_mismatch:{tick_summary['outcome']}")
    if no_sink_summary["outcome"] != OUTCOME_AUTO_PROMOTED:
        blockers.append(f"no_sink_outcome_mismatch:{no_sink_summary['outcome']}")
    if transitions != expected_transitions:
        blockers.append(
            "transition_sequence_mismatch:"
            f"expected={expected_transitions},actual={transitions}"
        )
    if len(bundles) != 1:
        blockers.append(f"expected_1_bundle_got_{len(bundles)}")
    if no_sink_bundles:
        blockers.append("sink_none_emitted_bundles_invariant_failed")
    if verifier_report.get("ok") is not True:
        blockers.append("offline_receipt_verifier_failed")
    if not leak_free:
        blockers.append("raw_payload_marker_leaked")
    if (
        not isinstance(counterfactual, dict)
        or counterfactual.get("status") != "computed"
    ):
        blockers.append("counterfactual_summary_not_computed")

    report = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "axis_id": AXIS_ID,
        "axis_name": "solver_growth_lifecycle",
        "claim_label": CLAIM_LABEL,
        "runtime_path": RUNTIME_PATH,
        "chain_id": CHAIN_ID,
        "risk_class": "local_artifact",
        "family_kind": _FAMILY_KIND,
        "solver_name": tick_summary["solver_name"],
        "external_effect_authority_change": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "local_artifacts_written": True,
        "receipt_emission_mode": "opt_in_disk_bundle_sink",
        "default_sink_required": False,
        "sink_none_preserved": not no_sink_bundles,
        "transitions": transitions,
        "receipt_count": int(verifier_report.get("receipt_count", 0) or 0),
        "verifier_ok": verifier_report.get("ok") is True,
        "counterfactual": counterfactual,
        "raw_payload_leak_check": leak_free,
        "receipt_out_dir": str(receipt_dir),
        "receipt_manifest": str(manifest_path),
        "evaluation_result_version": "magma.evaluation_result.v0",
        "scheduler_outcome": tick_summary,
        "no_sink_scheduler_outcome": no_sink_summary,
        "no_overclaim_guardrails": {
            "not_a_competitor_benchmark": True,
            "no_consensus_grade_promotion": True,
            "no_release_boundary_change": True,
            "claim_label_remains_partial": True,
        },
    }
    (out_dir / "autogrowth_promotion_receipt_emission_proof.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _run_scheduler_sequence(
    *,
    db_path: Path,
    bundles: list[dict[str, Any]],
    emit_sink: bool,
) -> dict[str, Any]:
    cp = ControlPlaneDB(db_path)
    cp.migrate()
    try:
        LowRiskGrower(cp).ensure_low_risk_policies()
        intent = cp.upsert_growth_intent(
            family_kind=_FAMILY_KIND,
            intent_key=(
                "proof:autogrowth:receipt"
                if emit_sink
                else "proof:autogrowth:no_sink"
            ),
            cell_coord="thermal",
            priority=10,
            spec_seed_json=json.dumps(_scalar_seed(), sort_keys=True),
        )
        cp.enqueue_growth_intent(intent.id, priority=10)
        scheduler = AutogrowthScheduler(
            cp,
            scheduler_id=(
                "autogrowth_receipt_proof_scheduler"
                if emit_sink
                else "autogrowth_receipt_no_sink_scheduler"
            ),
            emit_receipt_bundle=(
                (lambda bundle: bundles.append(bundle)) if emit_sink else None
            ),
        )
        result = scheduler.tick()
        if result.outcome != OUTCOME_AUTO_PROMOTED:
            raise ValueError(f"expected auto_promoted, got {result.outcome}")
        solver_name = (
            cp.get_solver_name(result.solver_id) if result.solver_id else None
        )
        return {
            "claimed": result.claimed,
            "outcome": result.outcome,
            "intent_id": result.intent_id,
            "queue_row_id": result.queue_row_id,
            "promotion_decision_id": result.promotion_decision_id,
            "solver_id": result.solver_id,
            "solver_name": solver_name,
            "autogrowth_run_id": result.autogrowth_run_id,
        }
    finally:
        cp.close()


def _scalar_seed() -> dict[str, Any]:
    return {
        "spec": {
            "from_unit": "C",
            "to_unit": "K",
            "factor": 1.0,
            "offset": 273.15,
        },
        "validation_cases": [
            {"inputs": {"x": 0.0}, "expected": 273.15},
            {"inputs": {"x": 100.0}, "expected": 373.15},
            {"inputs": {"x": -40.0}, "expected": 233.15},
            {"inputs": {"x": 25.0}, "expected": 298.15},
        ],
        "shadow_samples": [{"x": float(i) * 1.7} for i in range(20)],
        "counterfactual_incumbent": {
            "solver_name": "autogrowth_receipt_incumbent",
            "cell_id": "thermal",
            "spec": {
                "from_unit": "C",
                "to_unit": "K",
                "factor": 1.0,
                "offset": 0.0,
            },
            "source": "private incumbent source DO_NOT_LEAK",
            "source_kind": "local_proof_counterfactual_incumbent",
        },
        "solver_name_seed": _SOLVER_NAME_SEED,
        "cell_id": "thermal",
        "source": _PRIVATE_SOURCE_MARKER,
        "source_kind": "local_proof",
    }


def _counterfactual_from_bundles(
    bundles: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not bundles:
        return None
    counterfactual = bundles[0].get("payload", {}).get("counterfactual")
    return counterfactual if isinstance(counterfactual, dict) else None


def _raw_payload_leak_free(out_dir: Path) -> bool:
    artifact_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(out_dir.rglob("*.json"))
    )
    return not any(marker in artifact_text for marker in _RAW_MARKERS)


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise ValueError("--now must be a UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"invalid --now timestamp: {raw}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("--now must be in UTC")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


if __name__ == "__main__":
    raise SystemExit(main())
