# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only V12 solver-growth family coverage summary."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_v12_a4_solver_growth_axis_proof import (  # noqa: E402
    build_a4_solver_growth_axis_proof,
)


REPORT_VERSION = "wd.v12.solver_growth_family_coverage_summary.v0"
SOURCE_REPORT_VERSION = "wd.v12.a4_solver_growth_axis_proof.v0"
REQUIRED_GUARDRAILS = (
    "not_a_rival_benchmark",
    "does_not_claim_frontier_model_superiority",
    "does_not_claim_learned_authority",
    "does_not_touch_production_control_plane",
    "does_not_execute_live_builder",
    "does_not_pull_model",
    "does_not_call_cloud",
    "does_not_collect_human_approval",
    "no_stage2_atomic_flip",
    "measures_synthetic_fixture",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a path-free, read-only solver-growth family coverage summary "
            "from the local V12 A4 solver-growth axis proof."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument(
        "--min-dispatch-per-family",
        type=int,
        default=3,
        help="Minimum synthetic dispatch cases expected per covered family.",
    )
    parser.add_argument(
        "--max-growth-targets",
        type=int,
        default=3,
        help="Maximum weakest-family growth targets to include.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_solver_growth_family_coverage_summary(
            now_utc=_parse_utc(args.now) if args.now else None,
            min_dispatch_per_family=args.min_dispatch_per_family,
            max_growth_targets=args.max_growth_targets,
        )
    except ValueError as exc:
        print(f"solver-growth family coverage summary FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_solver_growth_family_coverage_summary(
    *,
    now_utc: datetime | None = None,
    min_dispatch_per_family: int = 3,
    max_growth_targets: int = 3,
    a4_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if min_dispatch_per_family < 1:
        raise ValueError("--min-dispatch-per-family must be >= 1")
    if max_growth_targets < 1:
        raise ValueError("--max-growth-targets must be >= 1")

    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at_utc = generated_at.isoformat(timespec="seconds").replace("+00:00", "Z")
    source = dict(
        a4_report
        if a4_report is not None
        else build_a4_solver_growth_axis_proof(now_utc=generated_at)
    )

    blockers = _source_blockers(source)
    dispatch = _mapping(source.get("dispatch"))
    registration = _mapping(source.get("registration"))
    per_family_counts = _int_mapping(dispatch.get("per_family_dispatch_counts"))
    family_rows = _family_rows(
        per_family_counts,
        min_dispatch_per_family=min_dispatch_per_family,
    )
    growth_targets = _growth_targets(
        family_rows,
        max_growth_targets=max_growth_targets,
    )

    if not family_rows:
        blockers.append("no_family_dispatch_counts")
    if any(not row["meets_min_dispatch"] for row in family_rows):
        blockers.append("family_dispatch_below_minimum")

    registered_count = _as_int(registration.get("registered_solver_count"))
    rejected_count = _as_int(registration.get("rejected_registration_count"))
    candidate_total = registered_count + rejected_count
    rejection_share = (
        round(rejected_count / candidate_total, 4) if candidate_total else 0.0
    )

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at_utc,
        "ok": not blockers,
        "blockers": blockers,
        "source": {
            "report_version": str(source.get("report_version", "")),
            "axis_id": str(source.get("axis_id", "")),
            "axis_name": str(source.get("axis_name", "")),
            "claim_label": str(source.get("claim_label", "")),
            "source_benchmark_version": str(source.get("source_benchmark_version", "")),
            "base_main_sha": str(source.get("base_main_sha", "")),
            "base_main_sha_source": str(source.get("base_main_sha_source", "")),
            "receipt_chain_verified": source.get("receipt_chain_verified") is True,
        },
        "portfolio_pressure": {
            "registered_solver_count": registered_count,
            "rejected_registration_count": rejected_count,
            "candidate_total": candidate_total,
            "rejection_share": rejection_share,
            "registered_more_than_rejected": registered_count > rejected_count,
        },
        "coverage": {
            "families_covered": _as_int(dispatch.get("families_covered")),
            "dispatch_case_count": _as_int(dispatch.get("dispatch_case_count")),
            "dispatch_success_count": _as_int(dispatch.get("dispatch_success_count")),
            "dispatch_failure_count": _as_int(dispatch.get("dispatch_failure_count")),
            "min_dispatch_per_family": min_dispatch_per_family,
            "families": family_rows,
            "weakest_family_count": (
                min(row["dispatch_count"] for row in family_rows)
                if family_rows
                else 0
            ),
        },
        "growth_targets": growth_targets,
        "recommended_next_slice": _recommended_next_slice(blockers, growth_targets),
        "authority_boundary": _authority_boundary(),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    coverage = _mapping(report.get("coverage"))
    pressure = _mapping(report.get("portfolio_pressure"))
    authority = _mapping(report.get("authority_boundary"))
    lines = [
        "# V12 Solver-Growth Family Coverage Summary",
        "",
        f"ok: `{_bool_text(report.get('ok'))}`",
        f"blockers: `{len(list(report.get('blockers') or []))}`",
        f"families covered: `{coverage.get('families_covered', 0)}`",
        (
            "dispatch successes: "
            f"`{coverage.get('dispatch_success_count', 0)}/"
            f"{coverage.get('dispatch_case_count', 0)}`"
        ),
        (
            "registered/rejected candidates: "
            f"`{pressure.get('registered_solver_count', 0)}/"
            f"{pressure.get('rejected_registration_count', 0)}`"
        ),
        "",
        "## Growth Targets",
    ]
    for target in list(report.get("growth_targets") or []):
        lines.append(
            "- "
            f"{target['family']}: dispatch_count="
            f"`{target['dispatch_count']}`, reason=`{target['reason']}`"
        )
    if not report.get("growth_targets"):
        lines.append("- none")

    lines.extend([
        "",
        "## Authority Boundary",
        f"runtime authority: `{_bool_text(authority.get('runtime_authority'))}`",
        f"promotion authority: `{_bool_text(authority.get('promotion_authority'))}`",
        f"scheduler authority: `{_bool_text(authority.get('scheduler_authority'))}`",
        f"bridge write authority: `{_bool_text(authority.get('bridge_write_authority'))}`",
        f"network authority: `{_bool_text(authority.get('network_authority'))}`",
        "",
        "This summary ranks local synthetic coverage only. It does not promote "
        "solvers, execute live builders, enqueue schedulers, append bridge "
        "events, or grant runtime authority.",
    ])
    return "\n".join(lines) + "\n"


def _source_blockers(source: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if source.get("report_version") != SOURCE_REPORT_VERSION:
        blockers.append("source_report_version_mismatch")
    if source.get("axis_id") != "A4":
        blockers.append("source_axis_not_a4")
    if source.get("ok") is not True:
        blockers.append("source_ok_not_true")
    if source.get("solver_growth_proven") is not True:
        blockers.append("solver_growth_proven_not_true")
    if source.get("release_gate_pass") is not True:
        blockers.append("release_gate_pass_not_true")
    guardrails = _mapping(source.get("no_overclaim_guardrails"))
    for key in REQUIRED_GUARDRAILS:
        if guardrails.get(key) is not True:
            blockers.append(f"guardrail_{key}_not_true")
    return blockers


def _family_rows(
    per_family_counts: Mapping[str, int],
    *,
    min_dispatch_per_family: int,
) -> list[dict[str, Any]]:
    rows = []
    for family, count in sorted(per_family_counts.items()):
        rows.append({
            "family": family,
            "dispatch_count": count,
            "meets_min_dispatch": count >= min_dispatch_per_family,
            "deficit_to_min": max(0, min_dispatch_per_family - count),
        })
    return rows


def _growth_targets(
    family_rows: Sequence[Mapping[str, Any]],
    *,
    max_growth_targets: int,
) -> list[dict[str, Any]]:
    if not family_rows:
        return []
    weakest = min(_as_int(row.get("dispatch_count")) for row in family_rows)
    targets = [
        {
            "family": str(row["family"]),
            "dispatch_count": _as_int(row.get("dispatch_count")),
            "reason": (
                "below_minimum_dispatch_coverage"
                if _as_int(row.get("deficit_to_min")) > 0
                else "tie_for_lowest_dispatch_coverage"
            ),
            "suggested_next_action": (
                "add held-out synthetic dispatch cases and keep runtime "
                "promotion operator-gated"
            ),
        }
        for row in family_rows
        if _as_int(row.get("dispatch_count")) == weakest
    ]
    return sorted(targets, key=lambda item: item["family"])[:max_growth_targets]


def _recommended_next_slice(
    blockers: Sequence[str],
    growth_targets: Sequence[Mapping[str, Any]],
) -> str:
    if blockers:
        return "fix_source_a4_proof_or_guardrail_blockers_before_growth_planning"
    if not growth_targets:
        return "no_family_target_available"
    families = ", ".join(str(target["family"]) for target in growth_targets)
    return (
        "expand_solver_growth_held_out_cases_for_lowest_coverage_families:"
        f"{families}"
    )


def _authority_boundary() -> dict[str, bool]:
    return {
        "read_only_summary": True,
        "runtime_authority": False,
        "promotion_authority": False,
        "scheduler_authority": False,
        "bridge_write_authority": False,
        "network_authority": False,
        "solver_execution_authority": False,
        "storage_write_authority": False,
        "operator_gate_required_for_runtime_promotion": True,
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key, raw in value.items():
        if isinstance(key, str):
            result[key] = _as_int(raw)
    return result


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return 0


def _bool_text(value: Any) -> str:
    return "true" if value is True else "false"


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
