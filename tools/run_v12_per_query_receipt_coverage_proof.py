# SPDX-License-Identifier: BUSL-1.1
"""Measure local per-query MAGMA runtime receipt coverage.

This proof exercises a deterministic corpus through
``AutonomyRuntime.handle_query`` with an opt-in runtime receipt sink. It writes
only explicit local artifacts under ``--out-dir`` and summarizes whether each
query produced a verified MAGMA runtime summary receipt with a receipt-bound
solver-call trace.

It does not enable default runtime receipt emission, scheduler work, solver
promotion, bridge writes, network access, or production authority.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


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
    EVALUATION_VERSION_V0,
    EVALUATION_VERSION_V1,
    PAYLOAD_VERSION,
    write_runtime_summary_receipt_bundle,
)


REPORT_VERSION = "wd.v12.per_query_receipt_coverage_proof.v0"
CLAIM_LABEL = "MEASURED_LOCAL_PARTIAL"
RUNTIME_PATH = "AutonomyRuntime.handle_query"
DEFAULT_EVALUATION_VERSION = EVALUATION_VERSION_V1
_RAW_MARKERS = (
    "DO_NOT_LEAK",
    "private oncology query",
    "private thermodynamics query",
    "private control query",
    "context secret",
)


@dataclass(frozen=True)
class QueryCase:
    query_id: str
    query: str
    context: Mapping[str, Any]


DEFAULT_QUERY_CASES = (
    QueryCase(
        query_id="query.local.oncology.fixture",
        query="private oncology query DO_NOT_LEAK",
        context={"domain": "oncology", "operator_note": "context secret DO_NOT_LEAK"},
    ),
    QueryCase(
        query_id="query.local.thermodynamics.fixture",
        query="private thermodynamics query DO_NOT_LEAK",
        context={"domain": "thermodynamics", "operator_note": "context secret"},
    ),
    QueryCase(
        query_id="query.local.control.fixture",
        query="private control query DO_NOT_LEAK",
        context={"domain": "control", "operator_note": "context secret"},
    ),
)


class _Selection:
    def __init__(self, capability: CapabilityContract) -> None:
        self.selected = [capability]


class _RouteResult:
    def __init__(self, capability: CapabilityContract, *, quality_path: str) -> None:
        self.selection = _Selection(capability)
        self.quality_path = quality_path
        self.autonomy_consult = None
        self.autonomy_served = False
        self.solver_call_trace = [
            {
                "stage": "solver_call",
                "status": "selected",
                "intent": "solve",
                "capability_id": capability.capability_id,
                "selected_index": 0,
                "quality_path": quality_path,
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
        help="New output directory for local proof artifacts.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-07T17:00:00Z.",
    )
    parser.add_argument(
        "--evaluation-version",
        choices=(EVALUATION_VERSION_V0, EVALUATION_VERSION_V1),
        default=DEFAULT_EVALUATION_VERSION,
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_v12_per_query_receipt_coverage_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
            evaluation_version=args.evaluation_version,
        )
    except (OSError, ValueError) as exc:
        print(f"per-query receipt coverage proof FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(
            "per-query receipt coverage proof OK: "
            f"{report['receipt_coverage_ratio']:.2f}"
        )
    else:
        print(
            "per-query receipt coverage proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_v12_per_query_receipt_coverage_proof(
    *,
    out_dir: Path,
    now_utc: datetime | None = None,
    evaluation_version: str = DEFAULT_EVALUATION_VERSION,
    query_cases: Sequence[QueryCase] = DEFAULT_QUERY_CASES,
) -> dict[str, Any]:
    if not query_cases:
        raise ValueError("query_cases must not be empty")
    query_ids = [item.query_id for item in query_cases]
    if len(set(query_ids)) != len(query_ids):
        raise ValueError("query_cases must have unique query_id values")
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()

    query_reports: list[dict[str, Any]] = []
    for index, query_case in enumerate(query_cases, start=1):
        query_reports.append(
            _run_query_case(
                out_dir=out_dir / f"query_{index:03d}",
                query_case=query_case,
                now_utc=generated_at + timedelta(seconds=index - 1),
                evaluation_version=evaluation_version,
            )
        )

    no_sink_report = _run_no_sink_control()
    blockers = _collect_blockers(query_reports, no_sink_report)
    query_count = len(query_reports)
    verified_receipts = sum(
        1 for query_report in query_reports if query_report["verified_receipt"] is True
    )
    receipt_bound_traces = sum(
        1
        for query_report in query_reports
        if query_report["solver_call_trace_receipt_bound"] is True
    )
    total_receipts = sum(int(item["receipt_count"]) for item in query_reports)
    leak_free = (
        all(item["raw_payload_leak_check"] is True for item in query_reports)
        and no_sink_report["raw_payload_leak_check"] is True
        and _raw_payload_leak_free(out_dir, {})
    )
    if not leak_free and "raw_payload_marker_leaked" not in blockers:
        blockers.append("raw_payload_marker_leaked")

    report_path = out_dir / "v12_per_query_receipt_coverage_proof.json"
    report = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "claim_label": CLAIM_LABEL,
        "runtime_path": RUNTIME_PATH,
        "payload_version": PAYLOAD_VERSION,
        "evaluation_version": evaluation_version,
        "evidence_scope": (
            "local deterministic AutonomyRuntime.handle_query corpus with "
            "opt-in runtime summary receipt sink; not production default "
            "receipt emission"
        ),
        "query_count": query_count,
        "query_ids": [item["query_id"] for item in query_reports],
        "receipt_count_total": total_receipts,
        "queries_with_verified_receipt": verified_receipts,
        "queries_with_solver_trace_receipt_bound": receipt_bound_traces,
        "receipt_coverage_ratio": verified_receipts / query_count,
        "solver_trace_receipt_bound_ratio": receipt_bound_traces / query_count,
        "all_queries_receipt_bound": verified_receipts == query_count,
        "all_solver_traces_receipt_bound": receipt_bound_traces == query_count,
        "raw_payload_leak_check": leak_free,
        "query_reports": query_reports,
        "no_sink_control": no_sink_report,
        "authority_boundary": {
            "local_artifacts_written": True,
            "receipt_emission_mode": "opt_in_disk_bundle_sink",
            "default_sink_required": False,
            "sink_none_preserved": no_sink_report["sink_none_preserved"],
            "default_runtime_receipt_emission_changed": False,
            "runtime_authority_changed": False,
            "external_effect_authority_change": False,
            "operator_gate_required": False,
            "external_writes_applied": False,
            "bridge_append": False,
            "solver_call_authority_granted": False,
            "scheduler_enqueue": False,
            "promotion": False,
            "gate_skip": False,
            "network": False,
            "production_memory_migration": False,
        },
        "no_overclaim_guardrails": {
            "claim_label_remains_partial": True,
            "not_a_production_coverage_claim": True,
            "not_default_runtime_receipt_emission": True,
            "not_a_competitor_benchmark": True,
            "no_release_boundary_change": True,
        },
        "report_path": str(report_path),
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _run_query_case(
    *,
    out_dir: Path,
    query_case: QueryCase,
    now_utc: datetime,
    evaluation_version: str,
) -> dict[str, Any]:
    out_dir.mkdir()
    receipt_dir = out_dir / "runtime_summary_receipts"
    receipt_reports: list[dict[str, Any]] = []

    def sink(summary: dict[str, Any]) -> dict[str, Any]:
        report = write_runtime_summary_receipt_bundle(
            out_dir=receipt_dir,
            summary_payload=summary,
            now_utc=now_utc,
            verify_manifest=verify_manifest,
            evaluation_version=evaluation_version,
        )
        receipt_reports.append(report)
        return report

    runtime = _build_fixture_runtime(runtime_receipt_sink=sink)
    result = runtime.handle_query(query_case.query, context=dict(query_case.context))
    if not receipt_reports:
        raise ValueError(f"{query_case.query_id}: runtime did not emit receipt report")

    receipt_report = receipt_reports[0]
    manifest_path = Path(str(receipt_report["manifest"]))
    verifier_report = verify_manifest(manifest_path)
    payload = _read_json(receipt_dir / "payload-001-runtime-summary.json")
    evaluation = _read_json(receipt_dir / "evaluation-001-runtime-summary.json")
    receipt = _read_json(receipt_dir / "receipt-001-runtime-summary.json")
    solver_call_trace = payload.get("solver_call_trace") or []
    solver_trace_digest_bound = (
        payload.get("solver_call_trace_digest")
        == sha256_digest({"solver_call_trace": solver_call_trace})
    )
    solver_trace_receipt_bound = (
        solver_trace_digest_bound
        and receipt.get("canonical_payload_digest") == sha256_digest(payload)
        and verifier_report.get("ok") is True
    )
    leak_free = _raw_payload_leak_free(out_dir, result)
    verified_receipt = (
        result.get("runtime_receipt") is not None
        and verifier_report.get("ok") is True
        and int(verifier_report.get("receipt_count", 0) or 0) == 1
        and evaluation.get("evaluation_version") == evaluation_version
        and payload.get("payload_version") == PAYLOAD_VERSION
        and solver_trace_receipt_bound
        and leak_free
    )

    return {
        "query_id": query_case.query_id,
        "ok": verified_receipt and result.get("executed") is True,
        "generated_at_utc": _format_utc(now_utc),
        "result_executed": result.get("executed") is True,
        "result_has_runtime_receipt": result.get("runtime_receipt") is not None,
        "verified_receipt": verified_receipt,
        "receipt_count": int(verifier_report.get("receipt_count", 0) or 0),
        "verifier_ok": verifier_report.get("ok") is True,
        "actual_gate": payload.get("actual_gate"),
        "verdict": payload.get("verdict"),
        "evaluation_version": evaluation.get("evaluation_version"),
        "solver_call_trace_count": int(
            payload.get("solver_call_trace_count", 0) or 0
        ),
        "solver_call_trace_digest": payload.get("solver_call_trace_digest"),
        "solver_call_trace_digest_bound": solver_trace_digest_bound,
        "solver_call_trace_receipt_bound": solver_trace_receipt_bound,
        "solver_selection": evaluation.get("solver_selection", []),
        "raw_payload_leak_check": leak_free,
        "receipt_manifest": str(manifest_path),
        "result_keys": sorted(str(key) for key in result.keys()),
    }


def _run_no_sink_control() -> dict[str, Any]:
    result = _build_fixture_runtime(runtime_receipt_sink=None).handle_query(
        "private no-sink control DO_NOT_LEAK",
        context={"operator_note": "context secret DO_NOT_LEAK"},
    )
    leak_free = _raw_payload_leak_free(None, result)
    return {
        "result_executed": result.get("executed") is True,
        "result_has_runtime_receipt": result.get("runtime_receipt") is not None,
        "sink_none_preserved": (
            result.get("executed") is True and "runtime_receipt" not in result
        ),
        "raw_payload_leak_check": leak_free,
        "result_keys": sorted(str(key) for key in result.keys()),
    }


def _collect_blockers(
    query_reports: Sequence[Mapping[str, Any]],
    no_sink_report: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    for item in query_reports:
        prefix = str(item.get("query_id") or "unknown_query")
        if item.get("result_executed") is not True:
            blockers.append(f"{prefix}:runtime_action_not_executed")
        if item.get("result_has_runtime_receipt") is not True:
            blockers.append(f"{prefix}:runtime_receipt_missing_from_result")
        if item.get("verified_receipt") is not True:
            blockers.append(f"{prefix}:verified_receipt_missing")
        if item.get("solver_call_trace_count") != 1:
            blockers.append(f"{prefix}:solver_call_trace_count_mismatch")
        if item.get("solver_call_trace_digest_bound") is not True:
            blockers.append(f"{prefix}:solver_call_trace_digest_unbound")
        if item.get("solver_call_trace_receipt_bound") is not True:
            blockers.append(f"{prefix}:solver_call_trace_not_receipt_bound")
        if item.get("raw_payload_leak_check") is not True:
            blockers.append(f"{prefix}:raw_payload_marker_leaked")
    if no_sink_report.get("sink_none_preserved") is not True:
        blockers.append("sink_none_opt_in_invariant_failed")
    if no_sink_report.get("raw_payload_leak_check") is not True:
        blockers.append("no_sink_raw_payload_marker_leaked")
    return blockers


def _build_fixture_runtime(*, runtime_receipt_sink) -> AutonomyRuntime:
    registry = CapabilityRegistry(load_builtins=False)
    capability = CapabilityContract(
        capability_id="solve.v12_fixture",
        category=CapabilityCategory.SOLVE,
        description="V12 per-query receipt coverage fixture solver",
        success_criteria=["success"],
    )
    registry.register(capability)
    registry.register_executor("solve.v12_fixture", _Executor())
    runtime = AutonomyRuntime(
        capability_registry=registry,
        enable_persistence=False,
        runtime_receipt_sink=runtime_receipt_sink,
    )
    runtime.solver_router.route = (
        lambda _intent, _query, _context: _RouteResult(
            capability,
            quality_path="gold",
        )
    )
    runtime.action_bus.register_executor(
        "solve.v12_fixture",
        lambda _action: {"success": True, "value": 42},
    )
    return runtime


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_payload_leak_free(
    out_dir: Path | None,
    result: Mapping[str, Any],
) -> bool:
    artifact_text = ""
    if out_dir is not None and out_dir.exists():
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
