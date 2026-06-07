# SPDX-License-Identifier: BUSL-1.1
"""Emit a local low-risk autogrowth chain dry-run proof.

The proof exercises the existing detector -> digest -> scheduler -> candidate
consult path against a new local ControlPlaneDB. It is intentionally a dry-run
artifact: no production control plane is opened, no provider/builder job is
created, and no gate/authority flag is changed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy_growth import (  # noqa: E402
    AutogrowthScheduler,
    DispatchQuery,
    GapSignal,
    LowRiskGrower,
    LowRiskSolverDispatcher,
    OUTCOME_AUTO_PROMOTED,
    RuntimeGapDetector,
    digest_signals_into_intents,
)
from waggledance.core.storage.control_plane import ControlPlaneDB  # noqa: E402


REPORT_VERSION = "wd.low_risk_autogrowth_chain_dry_run.v0"
AXIS_ID = "M4"
CLAIM_LABEL = "MEASURED_LOCAL_DRY_RUN"
RUNTIME_PATH = (
    "RuntimeGapDetector.record -> digest_signals_into_intents -> "
    "AutogrowthScheduler.tick -> LowRiskSolverDispatcher.dispatch"
)
REPORT_FILENAME = "low_risk_autogrowth_chain_dry_run.json"
_FAMILY_KIND = "scalar_unit_conversion"
_CELL_COORD = "thermal"
_SCHEDULER_ID = "low_risk_autogrowth_chain_dry_run"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Optional new output directory for the report and local SQLite "
            "control-plane dry-run database. It must not already exist."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-07T12:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_low_risk_autogrowth_chain_dry_run(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(f"low-risk autogrowth chain dry-run FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(render_markdown(report), end="")
    else:
        print(
            "low-risk autogrowth chain dry-run FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_low_risk_autogrowth_chain_dry_run(
    *,
    out_dir: Path | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)

    if out_dir is None:
        with tempfile.TemporaryDirectory(prefix="wd_low_risk_autogrowth_dry_run_") as tmp:
            return _build_report(
                out_dir=None,
                db_path=Path(tmp) / "control_plane.sqlite",
                generated_at=generated_at,
            )

    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()
    report = _build_report(
        out_dir=out_dir,
        db_path=out_dir / "control_plane.sqlite",
        generated_at=generated_at,
    )
    (out_dir / REPORT_FILENAME).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def render_markdown(report: dict[str, Any]) -> str:
    chain = report["chain"]
    dispatch = report["dispatch"]
    lines = [
        "# Low-Risk Autogrowth Chain Dry Run",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- claim_label: `{report['claim_label']}`",
        f"- ok: `{str(report['ok']).lower()}`",
        f"- runtime_path: `{report['runtime_path']}`",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| detector signals recorded | {chain['detector_signals_recorded']} |",
        f"| persisted runtime-gap signals | {chain['persisted_runtime_gap_signals']} |",
        f"| intents created | {chain['intents_created']} |",
        f"| intents enqueued | {chain['intents_enqueued']} |",
        f"| scheduler outcome | {chain['scheduler_outcome']} |",
        f"| auto-promoted solvers in local DB | {chain['auto_promoted_solver_count']} |",
        f"| dispatcher matched | {str(dispatch['matched']).lower()} |",
        "",
        "This is a local dry-run proof. It does not touch the production control",
        "plane, does not create provider or builder jobs, does not skip gates,",
        "and does not grant runtime authority.",
        "",
    ]
    return "\n".join(lines)


def _build_report(
    *,
    out_dir: Path | None,
    db_path: Path,
    generated_at: datetime,
) -> dict[str, Any]:
    summary = _run_chain(db_path=db_path)
    blockers = _collect_blockers(summary)
    report_path = str(out_dir / REPORT_FILENAME) if out_dir is not None else None
    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "axis_id": AXIS_ID,
        "axis_name": "low_risk_autogrowth_chain_dry_run",
        "claim_label": CLAIM_LABEL,
        "runtime_path": RUNTIME_PATH,
        "family_kind": _FAMILY_KIND,
        "cell_coord": _CELL_COORD,
        "chain": summary["chain"],
        "dispatch": summary["dispatch"],
        "control_plane": {
            "scope": "new_local_control_plane",
            "db_path": str(db_path if out_dir is not None else "<temporary>"),
            "schema_version": summary["schema_version"],
            "table_counts": summary["table_counts"],
        },
        "report_path": report_path,
        "local_artifacts_written": out_dir is not None,
        "authority_boundary": {
            "external_writes_applied": False,
            "production_control_plane_touched": False,
            "production_scheduler_enqueue": False,
            "provider_jobs_created": False,
            "builder_jobs_created": False,
            "gate_skip_authority": False,
            "operator_gate_bypassed": False,
            "runtime_authority_granted": False,
            "fast_track_priority": False,
        },
        "no_overclaim_guardrails": {
            "not_a_competitor_benchmark": True,
            "not_production_autogrowth_authority": True,
            "claim_label_remains_dry_run": True,
            "no_release_boundary_change": True,
            "uses_existing_low_risk_allowlist": True,
        },
    }


def _run_chain(*, db_path: Path) -> dict[str, Any]:
    cp = ControlPlaneDB(db_path)
    try:
        LowRiskGrower(cp).ensure_low_risk_policies()
        detector = RuntimeGapDetector(cp)
        signal = GapSignal(
            kind="miss",
            family_kind=_FAMILY_KIND,
            cell_coord=_CELL_COORD,
            intent_seed="c_to_k_dry_run",
            weight=2.0,
            payload={
                "reason": "dry_run_no_solver",
                "query_shape": "temperature_conversion",
            },
            spec_seed=_scalar_seed(),
        )
        detector.record(signal)
        intake = digest_signals_into_intents(
            cp,
            candidate_signals=[signal],
            min_signals_per_intent=1,
            autoenqueue=True,
            base_priority=10,
        )
        queued_before_tick = cp.count_queue_rows(status="queued")
        scheduler = AutogrowthScheduler(cp, scheduler_id=_SCHEDULER_ID)
        tick = scheduler.tick()
        dispatcher = LowRiskSolverDispatcher(cp)
        dispatch = dispatcher.dispatch(
            DispatchQuery(family_kind=_FAMILY_KIND, inputs={"x": 25.0})
        )
        stats = cp.stats()
        table_counts = dict(stats.table_counts)
        chain = {
            "detector_signals_recorded": detector.stats.signals_recorded,
            "persisted_runtime_gap_signals": cp.count_runtime_gap_signals(),
            "intents_created": intake.intents_created,
            "intents_enqueued": intake.intents_enqueued,
            "queued_before_tick": queued_before_tick,
            "queued_after_tick": cp.count_queue_rows(status="queued"),
            "completed_queue_rows": cp.count_queue_rows(status="completed"),
            "tick_claimed": tick.claimed,
            "scheduler_outcome": tick.outcome,
            "scheduler_id": scheduler.scheduler_id,
            "intent_id": tick.intent_id,
            "queue_row_id": tick.queue_row_id,
            "promotion_decision_id": tick.promotion_decision_id,
            "solver_id": tick.solver_id,
            "autogrowth_run_id": tick.autogrowth_run_id,
            "auto_promoted_solver_count": cp.count_solvers(status="auto_promoted"),
            "auto_promoted_run_count": len(
                cp.list_autogrowth_runs(outcome=OUTCOME_AUTO_PROMOTED)
            ),
            "growth_events": {
                "signal_recorded": cp.count_growth_events(
                    event_kind="signal_recorded"
                ),
                "intent_created": cp.count_growth_events(
                    event_kind="intent_created"
                ),
                "intent_enqueued": cp.count_growth_events(
                    event_kind="intent_enqueued"
                ),
                "solver_auto_promoted": cp.count_growth_events(
                    event_kind="solver_auto_promoted"
                ),
            },
        }
        return {
            "chain": chain,
            "dispatch": {
                "matched": dispatch.matched,
                "reason": dispatch.reason,
                "family_kind": dispatch.family_kind,
                "solver_id": dispatch.solver_id,
                "solver_name": dispatch.solver_name,
                "artifact_id": dispatch.artifact_id,
                "output": dispatch.output,
                "expected_output": 298.15,
            },
            "schema_version": stats.schema_version,
            "table_counts": {
                "provider_jobs": int(table_counts.get("provider_jobs", 0)),
                "builder_jobs": int(table_counts.get("builder_jobs", 0)),
                "runtime_gap_signals": int(table_counts.get("runtime_gap_signals", 0)),
                "growth_intents": int(table_counts.get("growth_intents", 0)),
                "autogrowth_queue": int(table_counts.get("autogrowth_queue", 0)),
                "autogrowth_runs": int(table_counts.get("autogrowth_runs", 0)),
                "solvers": int(table_counts.get("solvers", 0)),
            },
        }
    finally:
        cp.close()


def _collect_blockers(summary: dict[str, Any]) -> list[str]:
    chain = summary["chain"]
    dispatch = summary["dispatch"]
    tables = summary["table_counts"]
    blockers: list[str] = []
    expected_chain = {
        "detector_signals_recorded": 1,
        "persisted_runtime_gap_signals": 1,
        "intents_created": 1,
        "intents_enqueued": 1,
        "queued_before_tick": 1,
        "queued_after_tick": 0,
        "completed_queue_rows": 1,
        "auto_promoted_solver_count": 1,
        "auto_promoted_run_count": 1,
    }
    for key, expected in expected_chain.items():
        if chain.get(key) != expected:
            blockers.append(
                f"{key}_mismatch:expected={expected},actual={chain.get(key)}"
            )
    if chain.get("tick_claimed") is not True:
        blockers.append("scheduler_did_not_claim_queue_row")
    if chain.get("scheduler_outcome") != OUTCOME_AUTO_PROMOTED:
        blockers.append(f"scheduler_outcome_mismatch:{chain.get('scheduler_outcome')}")
    if dispatch.get("matched") is not True:
        blockers.append(f"dispatcher_miss:{dispatch.get('reason')}")
    output = dispatch.get("output")
    if not isinstance(output, (int, float)) or abs(float(output) - 298.15) > 1e-9:
        blockers.append(f"dispatch_output_mismatch:{output}")
    if tables.get("provider_jobs") != 0:
        blockers.append(f"provider_jobs_created:{tables.get('provider_jobs')}")
    if tables.get("builder_jobs") != 0:
        blockers.append(f"builder_jobs_created:{tables.get('builder_jobs')}")
    return blockers


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
        ],
        "shadow_samples": [{"x": float(i)} for i in range(20)],
        "solver_name_seed": "dry_run_celsius_to_kelvin",
        "cell_id": _CELL_COORD,
        "source": "low_risk_autogrowth_chain_dry_run",
        "source_kind": "local_dry_run",
    }


def _parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
