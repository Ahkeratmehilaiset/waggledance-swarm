# SPDX-License-Identifier: BUSL-1.1
"""Emit a local proof that AutonomyRuntime.handle_query can write a MAGMA receipt.

The proof exercises one concrete runtime path with an opt-in receipt sink. It
writes only local artifacts, verifies the receipt bundle offline, and checks
that raw query/context payload markers are absent from emitted JSON.
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
from waggledance.core.autonomy.runtime import AutonomyRuntime  # noqa: E402
from waggledance.core.capabilities.registry import CapabilityRegistry  # noqa: E402
from waggledance.core.domain.autonomy import (  # noqa: E402
    CapabilityCategory,
    CapabilityContract,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.runtime_summary_receipt import (  # noqa: E402
    CHAIN_ID,
    EVALUATION_VERSION_V0,
    EVALUATION_VERSION_V1,
    PAYLOAD_VERSION,
    write_runtime_summary_receipt_bundle,
)


REPORT_VERSION = "wd.magma.runtime_receipt_emission_proof.v0"
RUNTIME_PATH = "AutonomyRuntime.handle_query"
_PRIVATE_QUERY = "private runtime query DO_NOT_LEAK"
_PRIVATE_CONTEXT = {
    "operator_note": "context secret DO_NOT_LEAK",
    "operator_label": "runtime-receipt-proof",
}
_RAW_MARKERS = (
    "private runtime query",
    "context secret",
    "DO_NOT_LEAK",
)


class _Selection:
    def __init__(self, capability: CapabilityContract) -> None:
        self.selected = [capability]


class _RouteResult:
    def __init__(self, capability: CapabilityContract) -> None:
        self.selection = _Selection(capability)
        self.quality_path = "gold"
        self.autonomy_consult = None
        self.autonomy_served = False
        self.solver_call_trace = [
            {
                "stage": "solver_call",
                "status": "selected",
                "intent": "solve",
                "capability_id": capability.capability_id,
                "selected_index": 0,
                "quality_path": "gold",
                "execution_boundary": "safe_action_bus",
            }
        ]


class _Executor:
    available = True

    def execute(self, **_payload: Any) -> dict[str, Any]:
        return {"success": True, "value": 42}


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
        help="Optional UTC timestamp override such as 2026-05-23T07:00:00Z.",
    )
    parser.add_argument(
        "--evaluation-version",
        choices=(EVALUATION_VERSION_V0, EVALUATION_VERSION_V1),
        default=EVALUATION_VERSION_V1,
        help="EvaluationResult version for the generated receipt bundle.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_runtime_receipt_emission_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
            evaluation_version=args.evaluation_version,
        )
    except (OSError, ValueError) as exc:
        print(f"runtime receipt emission proof FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(f"runtime receipt emission proof OK: {report['receipt_manifest']}")
    else:
        print(
            "runtime receipt emission proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_runtime_receipt_emission_proof(
    *,
    out_dir: Path,
    now_utc: datetime | None = None,
    evaluation_version: str = EVALUATION_VERSION_V1,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")

    out_dir.mkdir()
    receipt_dir = out_dir / "runtime_summary_receipts"
    receipt_reports: list[dict[str, Any]] = []

    def sink(summary: dict[str, Any]) -> dict[str, Any]:
        report = write_runtime_summary_receipt_bundle(
            out_dir=receipt_dir,
            summary_payload=summary,
            now_utc=generated_at,
            verify_manifest=verify_manifest,
            evaluation_version=evaluation_version,
        )
        receipt_reports.append(report)
        return report

    runtime = _build_fixture_runtime(runtime_receipt_sink=sink)
    result = runtime.handle_query(_PRIVATE_QUERY, context=dict(_PRIVATE_CONTEXT))
    no_sink_result = _build_fixture_runtime(runtime_receipt_sink=None).handle_query(
        "receipt disabled health check",
        context={"mode": "no_sink"},
    )

    if not receipt_reports:
        raise ValueError("runtime did not emit a receipt report")

    receipt_report = receipt_reports[0]
    manifest_path = Path(str(receipt_report["manifest"]))
    verifier_report = verify_manifest(manifest_path)
    payload = _read_json(receipt_dir / "payload-001-runtime-summary.json")
    evaluation = _read_json(receipt_dir / "evaluation-001-runtime-summary.json")
    receipt = _read_json(receipt_dir / "receipt-001-runtime-summary.json")
    leak_free = _raw_payload_leak_free(out_dir, result)
    solver_call_trace = payload.get("solver_call_trace") or []
    solver_trace_json = json.dumps(solver_call_trace, sort_keys=True)
    solver_trace_privacy_safe = (
        _PRIVATE_QUERY not in solver_trace_json
        and "DO_NOT_LEAK" not in solver_trace_json
        and '"query"' not in solver_trace_json
    )
    solver_trace_digest_bound = (
        payload.get("solver_call_trace_digest")
        == sha256_digest({"solver_call_trace": solver_call_trace})
    )
    solver_trace_receipt_bound = (
        solver_trace_digest_bound
        and receipt.get("canonical_payload_digest") == sha256_digest(payload)
        and verifier_report.get("ok") is True
    )

    blockers: list[str] = []
    if result.get("runtime_receipt") is None:
        blockers.append("runtime_receipt_missing_from_result")
    if result.get("executed") is not True:
        blockers.append("runtime_action_not_executed")
    sink_none_preserved = (
        no_sink_result.get("executed") is True and "runtime_receipt" not in no_sink_result
    )
    if not sink_none_preserved:
        blockers.append("sink_none_opt_in_invariant_failed")
    if verifier_report.get("ok") is not True:
        blockers.append("offline_receipt_verifier_failed")
    if not leak_free:
        blockers.append("raw_payload_marker_leaked")
    if not solver_call_trace:
        blockers.append("solver_call_trace_missing_from_payload")
    if not solver_trace_privacy_safe:
        blockers.append("solver_call_trace_privacy_boundary_failed")
    if not solver_trace_receipt_bound:
        blockers.append("solver_call_trace_not_receipt_bound")
    if payload.get("payload_version") != PAYLOAD_VERSION:
        blockers.append("payload_version_mismatch")
    if evaluation.get("evaluation_version") != evaluation_version:
        blockers.append("evaluation_version_mismatch")

    report = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "runtime_path": RUNTIME_PATH,
        "payload_version": PAYLOAD_VERSION,
        "chain_id": CHAIN_ID,
        "risk_class": "internal_memory",
        "external_effect_authority_change": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "local_artifacts_written": True,
        "receipt_emission_mode": "opt_in_disk_bundle_sink",
        "default_sink_required": False,
        "sink_none_preserved": sink_none_preserved,
        "result_has_runtime_receipt": result.get("runtime_receipt") is not None,
        "result_executed": result.get("executed") is True,
        "actual_gate": payload.get("actual_gate"),
        "verdict": payload.get("verdict"),
        "requested_evaluation_version": evaluation_version,
        "evaluation_version": evaluation.get("evaluation_version"),
        "evaluation_v1_metadata_present": _evaluation_v1_metadata_present(evaluation),
        "reason_codes": evaluation.get("reason_codes", []),
        "receipt_count": int(verifier_report.get("receipt_count", 0) or 0),
        "verifier_ok": verifier_report.get("ok") is True,
        "raw_payload_leak_check": leak_free,
        "solver_call_trace_count": int(
            payload.get("solver_call_trace_count", 0) or 0
        ),
        "solver_call_trace_digest": payload.get("solver_call_trace_digest"),
        "solver_call_trace_digest_bound": solver_trace_digest_bound,
        "solver_call_trace_receipt_bound": solver_trace_receipt_bound,
        "solver_call_trace_privacy_safe": solver_trace_privacy_safe,
        "solver_selection": evaluation.get("solver_selection", []),
        "receipt_out_dir": str(receipt_dir),
        "receipt_manifest": str(manifest_path),
        "result_keys": sorted(str(key) for key in result.keys()),
        "summary_payload_keys": sorted(str(key) for key in payload.keys()),
    }
    (out_dir / "runtime_receipt_emission_proof.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _build_fixture_runtime(*, runtime_receipt_sink) -> AutonomyRuntime:
    registry = CapabilityRegistry(load_builtins=False)
    capability = CapabilityContract(
        capability_id="solve.fixture",
        category=CapabilityCategory.SOLVE,
        description="Fixture solver",
        success_criteria=["success"],
    )
    registry.register(capability)
    registry.register_executor("solve.fixture", _Executor())
    runtime = AutonomyRuntime(
        capability_registry=registry,
        enable_persistence=False,
        runtime_receipt_sink=runtime_receipt_sink,
    )
    runtime.solver_router.route = (
        lambda _intent, _query, _context: _RouteResult(capability)
    )
    runtime.action_bus.register_executor(
        "solve.fixture",
        lambda _action: {"success": True, "value": 42},
    )
    return runtime


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _evaluation_v1_metadata_present(evaluation: dict[str, Any]) -> bool:
    if evaluation.get("evaluation_version") != EVALUATION_VERSION_V1:
        return False
    return all(
        key in evaluation
        for key in (
            "confidence_basis",
            "sanitization_audit",
            "subject_payload_size_bytes",
        )
    )


def _raw_payload_leak_free(out_dir: Path, result: dict[str, Any]) -> bool:
    artifact_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(out_dir.rglob("*.json"))
    )
    result_text = json.dumps(result, sort_keys=True)
    combined = f"{artifact_text}\n{result_text}"
    return not any(marker in combined for marker in _RAW_MARKERS)


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
