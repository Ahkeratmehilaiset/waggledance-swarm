# SPDX-License-Identifier: BUSL-1.1
"""Emit a local proof that AutoPromotionEngine can write MAGMA receipts.

The proof exercises the real promote -> rollback path with the optional
emit_receipt_bundle sink wired in. It writes only local artifacts, verifies the
receipt chain offline, and confirms sink=None preserves the existing behavior.
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
from waggledance.core.autonomy_growth.auto_promotion_engine import (  # noqa: E402
    AutoPromotionEngine,
    PromotionRequest,
)
from waggledance.core.magma.receipt_bundle import (  # noqa: E402
    ReceiptBundleEntry,
    write_receipt_bundle,
)
from waggledance.core.solver_synthesis.declarative_solver_spec import (  # noqa: E402
    SolverSpec,
)
from waggledance.core.storage.control_plane import ControlPlaneDB  # noqa: E402


REPORT_VERSION = "wd.magma.auto_promotion_receipt_emission_proof.v0"
CHAIN_ID = "magma:v12_a4_auto_promotion_engine_axis:v1"
CLAIM_LABEL = "MEASURED_LOCAL_PARTIAL"
AXIS_ID = "A4"
RUNTIME_PATH = "AutoPromotionEngine.{evaluate_candidate,rollback}"
_FAMILY_KIND = "scalar_unit_conversion"
_SOLVER_NAME = "auto_promotion_receipt_solver"
_PRIVATE_SPEC_MARKER = "private auto promotion spec marker DO_NOT_LEAK"
_PRIVATE_ROLLBACK_REASON = "private rollback reason DO_NOT_LEAK"
_RAW_MARKERS = ("private auto promotion", "private rollback", "DO_NOT_LEAK")


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
        help="Optional UTC timestamp override such as 2026-05-23T11:20:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_auto_promotion_receipt_emission_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(f"auto promotion receipt emission proof FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(f"auto promotion receipt emission proof OK: {report['receipt_manifest']}")
    else:
        print(
            "auto promotion receipt emission proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_auto_promotion_receipt_emission_proof(
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
    transitions = _run_promote_rollback_sequence(
        db_path=out_dir / "control_plane.sqlite",
        bundles=bundles,
    )
    no_sink_bundles = _run_no_sink_health_check(
        db_path=out_dir / "control_plane_no_sink.sqlite"
    )

    receipt_dir = out_dir / "auto_promotion_receipts"
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

    expected_transitions = ["auto_promoted", "rolled_back"]
    blockers: list[str] = []
    if transitions != expected_transitions:
        blockers.append(
            "transition_sequence_mismatch:"
            f"expected={expected_transitions},actual={transitions}"
        )
    if len(bundles) != 2:
        blockers.append(f"expected_2_bundles_got_{len(bundles)}")
    if no_sink_bundles:
        blockers.append("sink_none_emitted_bundles_invariant_failed")
    if verifier_report.get("ok") is not True:
        blockers.append("offline_receipt_verifier_failed")
    if not leak_free:
        blockers.append("raw_payload_marker_leaked")

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
        "solver_name": _SOLVER_NAME,
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
        "raw_payload_leak_check": leak_free,
        "receipt_out_dir": str(receipt_dir),
        "receipt_manifest": str(manifest_path),
        "evaluation_result_version": "magma.evaluation_result.v0",
        "no_overclaim_guardrails": {
            "not_a_competitor_benchmark": True,
            "no_consensus_grade_promotion": True,
            "no_release_boundary_change": True,
            "claim_label_remains_partial": True,
        },
    }
    (out_dir / "auto_promotion_receipt_emission_proof.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _run_promote_rollback_sequence(
    *,
    db_path: Path,
    bundles: list[dict[str, Any]],
) -> list[str]:
    cp = ControlPlaneDB(db_path)
    cp.migrate()
    try:
        cp.upsert_family_policy(_FAMILY_KIND, is_low_risk=True)
        engine = AutoPromotionEngine(
            cp,
            emit_receipt_bundle=lambda bundle: bundles.append(bundle),
        )
        promoted = engine.evaluate_candidate(_promotion_request(_SOLVER_NAME))
        rolled_back = engine.rollback(
            _SOLVER_NAME,
            rollback_reason=_PRIVATE_ROLLBACK_REASON,
        )
        if promoted.decision != "auto_promoted":
            raise ValueError(f"expected auto_promoted, got {promoted.decision}")
        if rolled_back.decision != "rolled_back":
            raise ValueError(f"expected rolled_back, got {rolled_back.decision}")
        return [str(bundle["payload"]["decision"]) for bundle in bundles]
    finally:
        cp.close()


def _run_no_sink_health_check(*, db_path: Path) -> list[dict[str, Any]]:
    cp = ControlPlaneDB(db_path)
    cp.migrate()
    bundles: list[dict[str, Any]] = []
    try:
        cp.upsert_family_policy(_FAMILY_KIND, is_low_risk=True)
        engine = AutoPromotionEngine(cp, emit_receipt_bundle=None)
        promoted = engine.evaluate_candidate(
            _promotion_request("auto_promotion_no_sink_solver")
        )
        if promoted.decision != "auto_promoted":
            raise ValueError(f"no-sink promotion diverged: {promoted.decision}")
        rolled_back = engine.rollback(
            "auto_promotion_no_sink_solver",
            rollback_reason="no_sink_health_check_reason",
        )
        if rolled_back.decision != "rolled_back":
            raise ValueError(f"no-sink rollback diverged: {rolled_back.decision}")
        return bundles
    finally:
        cp.close()


def _promotion_request(solver_name: str) -> PromotionRequest:
    return PromotionRequest(
        spec=_scalar_unit_conversion_spec(solver_name),
        validation_cases=_validation_cases(),
        shadow_samples=_shadow_samples(),
        oracle=_scalar_unit_conversion_oracle,
        oracle_kind="formula_recompute",
    )


def _scalar_unit_conversion_spec(solver_name: str) -> SolverSpec:
    return SolverSpec(
        schema_version=1,
        spec_id=f"spec_{solver_name}",
        family_kind=_FAMILY_KIND,
        solver_name=solver_name,
        cell_id="general",
        spec={
            "from_unit": "C",
            "to_unit": "K",
            "factor": 1.0,
            "offset": 273.15,
            "private_note": _PRIVATE_SPEC_MARKER,
        },
        source="c5_auto_promotion_receipt_proof",
        source_kind="hand_authored",
    )


def _validation_cases() -> list[dict[str, Any]]:
    return [
        {"inputs": {"x": 0.0}, "expected": 273.15},
        {"inputs": {"x": 100.0}, "expected": 373.15},
        {"inputs": {"x": -40.0}, "expected": 233.15},
        {"inputs": {"x": 25.0}, "expected": 298.15},
    ]


def _shadow_samples() -> list[dict[str, Any]]:
    return [{"x": float(i) * 1.7} for i in range(20)]


def _scalar_unit_conversion_oracle(inputs: dict[str, Any], artifact: dict[str, Any]):
    return float(inputs["x"]) * float(artifact["factor"]) + float(
        artifact.get("offset", 0.0)
    )


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
