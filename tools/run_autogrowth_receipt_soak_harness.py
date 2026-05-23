# SPDX-License-Identifier: BUSL-1.1
"""Run a local repeated autogrowth receipt soak harness.

This harness repeats the multi-intent AutogrowthScheduler receipt smoke across
several deterministic rounds, verifies each receipt chain through the existing
smoke proof, and summarizes stability metrics. It writes only local artifacts.

This is not release soak evidence and not production auto-promotion authority.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_autogrowth_multi_intent_receipt_smoke import (  # noqa: E402
    DEFAULT_INTENT_COUNT,
    build_autogrowth_multi_intent_receipt_smoke,
)


REPORT_VERSION = "wd.magma.autogrowth_receipt_soak_harness.v0"
CLAIM_LABEL = "MEASURED_LOCAL_PARTIAL"
AXIS_ID = "A4"
DEFAULT_ROUNDS = 3
RUNTIME_PATH = (
    "AutogrowthScheduler.run_until_idle -> LowRiskGrower.grow_from_gap -> "
    "AutoPromotionEngine.evaluate_candidate"
)

RoundBuilder = Callable[..., dict[str, Any]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="New output directory for the soak harness. It must not already exist.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=DEFAULT_ROUNDS,
        help="Number of repeated multi-intent rounds. Default: 3.",
    )
    parser.add_argument(
        "--intent-count",
        type=int,
        default=DEFAULT_INTENT_COUNT,
        help="Number of intents per round. Default covers six low-risk families.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-23T15:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_autogrowth_receipt_soak_harness(
            out_dir=args.out_dir,
            rounds=args.rounds,
            intent_count=args.intent_count,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(f"autogrowth receipt soak harness FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(f"autogrowth receipt soak harness OK: {report['report_path']}")
    else:
        print(
            "autogrowth receipt soak harness FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_autogrowth_receipt_soak_harness(
    *,
    out_dir: Path,
    rounds: int = DEFAULT_ROUNDS,
    intent_count: int = DEFAULT_INTENT_COUNT,
    now_utc: datetime | None = None,
    round_builder: RoundBuilder = build_autogrowth_multi_intent_receipt_smoke,
) -> dict[str, Any]:
    if rounds < 1:
        raise ValueError("rounds must be >= 1")
    if intent_count < 1:
        raise ValueError("intent_count must be >= 1")

    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()

    round_reports: list[dict[str, Any]] = []
    for round_index in range(1, rounds + 1):
        round_dir = out_dir / f"round_{round_index:03d}"
        round_now = generated_at + timedelta(seconds=round_index - 1)
        try:
            report = round_builder(
                out_dir=round_dir,
                intent_count=intent_count,
                now_utc=round_now,
            )
        except (OSError, ValueError) as exc:
            report = {
                "ok": False,
                "generated_at_utc": _format_utc(round_now),
                "blockers": [f"round_builder_exception:{type(exc).__name__}:{exc}"],
                "intent_count": intent_count,
                "receipt_count": 0,
                "verifier_ok": False,
                "sink_none_preserved": False,
                "raw_payload_leak_check": False,
                "transitions": [],
                "families_covered": [],
                "scheduler_smoke": {},
                "receipt_manifest": None,
            }
        round_reports.append(_summarize_round(round_index, round_dir, report))

    blockers = _collect_blockers(
        round_reports=round_reports,
        rounds=rounds,
        intent_count=intent_count,
        out_dir=out_dir,
    )
    total_receipts = sum(
        int(round_report["receipt_count"]) for round_report in round_reports
    )
    ok_rounds = sum(1 for round_report in round_reports if round_report["ok"] is True)
    report_path = out_dir / "autogrowth_receipt_soak_harness.json"
    report = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "axis_id": AXIS_ID,
        "axis_name": "solver_growth_lifecycle",
        "claim_label": CLAIM_LABEL,
        "evidence_scope": (
            "local repeated multi-intent AutogrowthScheduler soak fixture; "
            "not release soak evidence; not long-running production "
            "auto-promotion authority"
        ),
        "runtime_path": RUNTIME_PATH,
        "risk_class": "local_artifact",
        "external_effect_authority_change": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "local_artifacts_written": True,
        "receipt_emission_mode": "opt_in_disk_bundle_sink",
        "round_count": rounds,
        "intent_count_per_round": intent_count,
        "expected_receipt_count": rounds * intent_count,
        "total_receipt_count": total_receipts,
        "ok_rounds": ok_rounds,
        "failed_rounds": rounds - ok_rounds,
        "pass_rate": ok_rounds / rounds,
        "rounds": round_reports,
        "stability_metrics": _stability_metrics(round_reports),
        "families_covered_union": sorted(
            {
                family
                for round_report in round_reports
                for family in round_report["families_covered"]
            }
        ),
        "aggregate_raw_payload_leak_check": _raw_payload_leak_free(out_dir),
        "report_path": str(report_path),
        "no_overclaim_guardrails": {
            "not_release_soak_evidence": True,
            "not_a_competitor_benchmark": True,
            "no_consensus_grade_promotion": True,
            "no_release_boundary_change": True,
            "claim_label_remains_partial": True,
            "not_production_authority": True,
        },
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _summarize_round(
    round_index: int,
    round_dir: Path,
    report: dict[str, Any],
) -> dict[str, Any]:
    scheduler_smoke = dict(report.get("scheduler_smoke") or {})
    no_sink_scheduler_smoke = dict(report.get("no_sink_scheduler_smoke") or {})
    return {
        "round_index": round_index,
        "round_dir": str(round_dir),
        "ok": report.get("ok") is True,
        "blockers": list(report.get("blockers") or []),
        "generated_at_utc": report.get("generated_at_utc"),
        "intent_count": int(report.get("intent_count", 0) or 0),
        "receipt_count": int(report.get("receipt_count", 0) or 0),
        "verifier_ok": report.get("verifier_ok") is True,
        "sink_none_preserved": report.get("sink_none_preserved") is True,
        "raw_payload_leak_check": report.get("raw_payload_leak_check") is True,
        "transitions": list(report.get("transitions") or []),
        "families_covered": list(report.get("families_covered") or []),
        "receipt_manifest": report.get("receipt_manifest"),
        "scheduler": {
            "drained_count": int(scheduler_smoke.get("drained_count", 0) or 0),
            "auto_promoted": int(scheduler_smoke.get("auto_promoted", 0) or 0),
            "rejected": int(scheduler_smoke.get("rejected", 0) or 0),
            "errored": int(scheduler_smoke.get("errored", 0) or 0),
            "ticks_total": int(scheduler_smoke.get("ticks_total", 0) or 0),
            "ticks_idle": int(scheduler_smoke.get("ticks_idle", 0) or 0),
        },
        "no_sink_scheduler": {
            "auto_promoted": int(
                no_sink_scheduler_smoke.get("auto_promoted", 0) or 0
            ),
            "rejected": int(no_sink_scheduler_smoke.get("rejected", 0) or 0),
            "errored": int(no_sink_scheduler_smoke.get("errored", 0) or 0),
        },
    }


def _collect_blockers(
    *,
    round_reports: list[dict[str, Any]],
    rounds: int,
    intent_count: int,
    out_dir: Path,
) -> list[str]:
    blockers: list[str] = []
    failed_rounds = [
        round_report for round_report in round_reports if not round_report["ok"]
    ]
    if failed_rounds:
        blockers.append(f"round_failures:{len(failed_rounds)}")
        for round_report in failed_rounds:
            blockers.append(
                "round_"
                f"{round_report['round_index']:03d}_failed:"
                f"{','.join(round_report['blockers'])}"
            )

    total_receipts = sum(
        int(round_report["receipt_count"]) for round_report in round_reports
    )
    expected_receipts = rounds * intent_count
    if total_receipts != expected_receipts:
        blockers.append(
            "total_receipt_count_mismatch:"
            f"expected={expected_receipts},actual={total_receipts}"
        )

    for round_report in round_reports:
        prefix = f"round_{round_report['round_index']:03d}"
        scheduler = round_report["scheduler"]
        no_sink_scheduler = round_report["no_sink_scheduler"]
        if round_report["intent_count"] != intent_count:
            blockers.append(
                f"{prefix}_intent_count_mismatch:"
                f"expected={intent_count},actual={round_report['intent_count']}"
            )
        if round_report["receipt_count"] != intent_count:
            blockers.append(
                f"{prefix}_receipt_count_mismatch:"
                f"expected={intent_count},actual={round_report['receipt_count']}"
            )
        if scheduler["drained_count"] != intent_count:
            blockers.append(
                f"{prefix}_drained_count_mismatch:"
                f"expected={intent_count},actual={scheduler['drained_count']}"
            )
        if scheduler["auto_promoted"] != intent_count:
            blockers.append(
                f"{prefix}_auto_promoted_mismatch:"
                f"expected={intent_count},actual={scheduler['auto_promoted']}"
            )
        if (
            round_report["ok"] is True
            and no_sink_scheduler["auto_promoted"] != intent_count
        ):
            blockers.append(
                f"{prefix}_no_sink_auto_promoted_mismatch:"
                f"expected={intent_count},actual={no_sink_scheduler['auto_promoted']}"
            )
        elif (
            round_report["ok"] is not True
            and no_sink_scheduler["auto_promoted"] not in (0, intent_count)
        ):
            blockers.append(
                f"{prefix}_no_sink_auto_promoted_mismatch:"
                f"expected=0_or_{intent_count},"
                f"actual={no_sink_scheduler['auto_promoted']}"
            )
        if scheduler["rejected"] != 0 or scheduler["errored"] != 0:
            blockers.append(
                f"{prefix}_scheduler_terminal_failures:"
                f"rejected={scheduler['rejected']},errored={scheduler['errored']}"
            )
        if no_sink_scheduler["rejected"] != 0 or no_sink_scheduler["errored"] != 0:
            blockers.append(
                f"{prefix}_no_sink_scheduler_terminal_failures:"
                f"rejected={no_sink_scheduler['rejected']},"
                f"errored={no_sink_scheduler['errored']}"
            )
        if round_report["verifier_ok"] is not True:
            blockers.append(f"{prefix}_offline_receipt_verifier_failed")
        if round_report["sink_none_preserved"] is not True:
            blockers.append(f"{prefix}_sink_none_not_preserved")
        if round_report["raw_payload_leak_check"] is not True:
            blockers.append(f"{prefix}_raw_payload_marker_leaked")

    if not _raw_payload_leak_free(out_dir):
        blockers.append("aggregate_raw_payload_marker_leaked")
    return blockers


def _stability_metrics(round_reports: list[dict[str, Any]]) -> dict[str, Any]:
    receipt_counts = [int(round_report["receipt_count"]) for round_report in round_reports]
    drained_counts = [
        int(round_report["scheduler"]["drained_count"]) for round_report in round_reports
    ]
    auto_promoted_counts = [
        int(round_report["scheduler"]["auto_promoted"]) for round_report in round_reports
    ]
    rejected_total = sum(
        int(round_report["scheduler"]["rejected"])
        + int(round_report["no_sink_scheduler"]["rejected"])
        for round_report in round_reports
    )
    errored_total = sum(
        int(round_report["scheduler"]["errored"])
        + int(round_report["no_sink_scheduler"]["errored"])
        for round_report in round_reports
    )
    return {
        "receipt_count_min": min(receipt_counts) if receipt_counts else 0,
        "receipt_count_max": max(receipt_counts) if receipt_counts else 0,
        "drained_count_min": min(drained_counts) if drained_counts else 0,
        "drained_count_max": max(drained_counts) if drained_counts else 0,
        "auto_promoted_min": min(auto_promoted_counts)
        if auto_promoted_counts
        else 0,
        "auto_promoted_max": max(auto_promoted_counts)
        if auto_promoted_counts
        else 0,
        "rejected_total": rejected_total,
        "errored_total": errored_total,
        "verifier_failures": sum(
            1 for round_report in round_reports if round_report["verifier_ok"] is not True
        ),
        "sink_none_failures": sum(
            1
            for round_report in round_reports
            if round_report["sink_none_preserved"] is not True
        ),
        "raw_payload_leak_failures": sum(
            1
            for round_report in round_reports
            if round_report["raw_payload_leak_check"] is not True
        ),
    }


def _raw_payload_leak_free(out_dir: Path) -> bool:
    artifact_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(out_dir.rglob("*.json"))
    )
    return (
        "private autogrowth" not in artifact_text
        and "DO_NOT_LEAK" not in artifact_text
    )


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
