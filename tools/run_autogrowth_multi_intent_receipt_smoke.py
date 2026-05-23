# SPDX-License-Identifier: BUSL-1.1
"""Emit a local multi-intent autogrowth receipt smoke proof.

This proof exercises several queued low-risk intents through the real
AutogrowthScheduler.run_until_idle -> LowRiskGrower -> AutoPromotionEngine path
with the optional receipt sink wired in. It writes only local artifacts,
verifies the receipt chain offline, and confirms sink=None preserves existing
behavior.

This is local smoke evidence, not production auto-promotion authority.
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

from tools.run_mass_autogrowth_proof import all_seeds  # noqa: E402
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


REPORT_VERSION = "wd.magma.autogrowth_multi_intent_receipt_smoke.v0"
CHAIN_ID = "magma:v12_a4_autogrowth_multi_intent_smoke:v1"
CLAIM_LABEL = "MEASURED_LOCAL_PARTIAL"
AXIS_ID = "A4"
RUNTIME_PATH = (
    "AutogrowthScheduler.run_until_idle -> LowRiskGrower.grow_from_gap -> "
    "AutoPromotionEngine.evaluate_candidate"
)
DEFAULT_INTENT_COUNT = 6
_PRIVATE_SOURCE_MARKER = "private autogrowth multi intent source DO_NOT_LEAK"
_RAW_MARKERS = ("private autogrowth", "DO_NOT_LEAK")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="New output directory for the smoke proof. It must not already exist.",
    )
    parser.add_argument(
        "--intent-count",
        type=int,
        default=DEFAULT_INTENT_COUNT,
        help="Number of distinct family seeds to queue. Default covers six families.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-23T14:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_autogrowth_multi_intent_receipt_smoke(
            out_dir=args.out_dir,
            intent_count=args.intent_count,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(f"autogrowth multi-intent smoke FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(f"autogrowth multi-intent smoke OK: {report['receipt_manifest']}")
    else:
        print(
            "autogrowth multi-intent smoke FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_autogrowth_multi_intent_receipt_smoke(
    *,
    out_dir: Path,
    intent_count: int = DEFAULT_INTENT_COUNT,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if intent_count < 1:
        raise ValueError("intent_count must be >= 1")
    seeds = _select_distinct_family_seeds(intent_count)
    if len(seeds) != intent_count:
        raise ValueError(
            f"requested {intent_count} intents but only {len(seeds)} seeds available"
        )

    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()

    bundles: list[dict[str, Any]] = []
    smoke_summary = _run_scheduler_sequence(
        db_path=out_dir / "control_plane.sqlite",
        seeds=seeds,
        bundles=bundles,
        emit_sink=True,
    )
    no_sink_bundles: list[dict[str, Any]] = []
    no_sink_summary = _run_scheduler_sequence(
        db_path=out_dir / "control_plane_no_sink.sqlite",
        seeds=seeds,
        bundles=no_sink_bundles,
        emit_sink=False,
    )

    receipt_dir = out_dir / "autogrowth_multi_intent_receipts"
    entries = [
        ReceiptBundleEntry(
            label=f"{index:03d}-{bundle['payload']['decision']}",
            payload=bundle["payload"],
            evaluation_result=bundle["evaluation_result"],
            receipt=bundle["receipt"],
        )
        for index, bundle in enumerate(bundles, start=1)
    ]
    bundle_report = write_receipt_bundle(
        out_dir=receipt_dir,
        chain_id=CHAIN_ID,
        entries=entries,
        verify_manifest=verify_manifest,
    )
    manifest_path = Path(str(bundle_report["manifest"]))
    verifier_report = verify_manifest(manifest_path)
    transitions = [str(bundle["payload"]["decision"]) for bundle in bundles]
    leak_free = _raw_payload_leak_free(out_dir)

    expected_transitions = [OUTCOME_AUTO_PROMOTED] * intent_count
    blockers: list[str] = []
    if smoke_summary["drained_count"] != intent_count:
        blockers.append(
            "drained_count_mismatch:"
            f"expected={intent_count},actual={smoke_summary['drained_count']}"
        )
    if smoke_summary["auto_promoted"] != intent_count:
        blockers.append(
            "auto_promoted_count_mismatch:"
            f"expected={intent_count},actual={smoke_summary['auto_promoted']}"
        )
    if no_sink_summary["auto_promoted"] != intent_count:
        blockers.append(
            "no_sink_auto_promoted_count_mismatch:"
            f"expected={intent_count},actual={no_sink_summary['auto_promoted']}"
        )
    if transitions != expected_transitions:
        blockers.append(
            "transition_sequence_mismatch:"
            f"expected={expected_transitions},actual={transitions}"
        )
    if len(bundles) != intent_count:
        blockers.append(f"expected_{intent_count}_bundles_got_{len(bundles)}")
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
        "evidence_scope": (
            "local multi-intent AutogrowthScheduler smoke; not long-running "
            "production auto-promotion authority"
        ),
        "runtime_path": RUNTIME_PATH,
        "chain_id": CHAIN_ID,
        "risk_class": "local_artifact",
        "external_effect_authority_change": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "local_artifacts_written": True,
        "receipt_emission_mode": "opt_in_disk_bundle_sink",
        "default_sink_required": False,
        "sink_none_preserved": not no_sink_bundles,
        "intent_count": intent_count,
        "families_covered": sorted({seed["family_kind"] for seed in seeds}),
        "transitions": transitions,
        "receipt_count": int(verifier_report.get("receipt_count", 0) or 0),
        "verifier_ok": verifier_report.get("ok") is True,
        "raw_payload_leak_check": leak_free,
        "receipt_out_dir": str(receipt_dir),
        "receipt_manifest": str(manifest_path),
        "evaluation_result_version": "magma.evaluation_result.v0",
        "scheduler_smoke": smoke_summary,
        "no_sink_scheduler_smoke": no_sink_summary,
        "no_overclaim_guardrails": {
            "not_a_competitor_benchmark": True,
            "no_consensus_grade_promotion": True,
            "no_release_boundary_change": True,
            "claim_label_remains_partial": True,
            "not_production_authority": True,
        },
    }
    (out_dir / "autogrowth_multi_intent_receipt_smoke.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _run_scheduler_sequence(
    *,
    db_path: Path,
    seeds: list[dict[str, Any]],
    bundles: list[dict[str, Any]],
    emit_sink: bool,
) -> dict[str, Any]:
    cp = ControlPlaneDB(db_path)
    cp.migrate()
    try:
        LowRiskGrower(cp).ensure_low_risk_policies()
        intent_ids: list[int] = []
        for index, seed_info in enumerate(seeds, start=1):
            intent = cp.upsert_growth_intent(
                family_kind=str(seed_info["family_kind"]),
                intent_key=(
                    "proof:autogrowth:multi:"
                    f"{index}:{seed_info['family_kind']}:{seed_info['seed_name']}"
                ),
                cell_coord=str(seed_info["cell"]),
                priority=100 - index,
                spec_seed_json=json.dumps(seed_info["seed"], sort_keys=True),
            )
            cp.enqueue_growth_intent(intent.id, priority=100 - index)
            intent_ids.append(intent.id)
        scheduler = AutogrowthScheduler(
            cp,
            scheduler_id=(
                "autogrowth_multi_intent_receipt_smoke"
                if emit_sink
                else "autogrowth_multi_intent_no_sink_smoke"
            ),
            emit_receipt_bundle=(
                (lambda bundle: bundles.append(bundle)) if emit_sink else None
            ),
        )
        drained = scheduler.run_until_idle(max_ticks=len(seeds) + 2)
        solvers = list(cp.iter_solvers(status="auto_promoted"))
        return {
            "intent_ids": intent_ids,
            "drained_count": drained,
            "ticks_total": scheduler.stats.ticks_total,
            "ticks_idle": scheduler.stats.ticks_idle,
            "auto_promoted": scheduler.stats.auto_promoted,
            "rejected": scheduler.stats.rejected,
            "errored": scheduler.stats.errored,
            "by_family_promoted": dict(scheduler.stats.by_family_promoted),
            "auto_promoted_solver_names": [solver.name for solver in solvers],
            "growth_intents_fulfilled": cp.count_growth_intents(status="fulfilled"),
            "autogrowth_runs_total": cp.stats().table_counts["autogrowth_runs"],
            "promotion_decisions_total": cp.stats().table_counts[
                "promotion_decisions"
            ],
        }
    finally:
        cp.close()


def _select_distinct_family_seeds(intent_count: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_families: set[str] = set()
    for family_kind, seed_name, cell, seed in all_seeds():
        if family_kind in seen_families:
            continue
        seen_families.add(family_kind)
        seed_copy = dict(seed)
        seed_copy["source"] = _PRIVATE_SOURCE_MARKER
        selected.append({
            "family_kind": family_kind,
            "seed_name": seed_name,
            "cell": cell,
            "seed": seed_copy,
        })
        if len(selected) == intent_count:
            return selected
    if len(selected) < intent_count:
        for family_kind, seed_name, cell, seed in all_seeds():
            if any(
                item["family_kind"] == family_kind and item["seed_name"] == seed_name
                for item in selected
            ):
                continue
            seed_copy = dict(seed)
            seed_copy["source"] = _PRIVATE_SOURCE_MARKER
            selected.append({
                "family_kind": family_kind,
                "seed_name": seed_name,
                "cell": cell,
                "seed": seed_copy,
            })
            if len(selected) == intent_count:
                break
    return selected


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
