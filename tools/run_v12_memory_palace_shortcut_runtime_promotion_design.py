# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only V12 Memory Palace shortcut runtime-promotion design.

This report is deliberately design-only. It turns already-verified shortcut
promotion-candidate observations into operator-gated design rows, but it does
not promote a route, dispatch a solver, enqueue scheduler work, append bridge
events, write storage, skip gates, or grant runtime authority.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_v12_memory_palace_shortcut_promotion_candidates import (  # noqa: E402
    DEFAULT_MIN_INTERMEDIATE_HOPS_SKIPPED,
    DEFAULT_MIN_RANK_SCORE,
    build_memory_palace_shortcut_promotion_candidate_report,
)
from tools.verify_v12_memory_palace_shortcut_promotion_candidates import (  # noqa: E402
    verify_memory_palace_shortcut_promotion_candidate_report,
)


REPORT_VERSION = "wd.v12.memory_palace_shortcut_runtime_promotion_design.v0"
CLAIM_LABEL = "DESIGN_ONLY_OPERATOR_GATED_RUNTIME_PROMOTION"

_DESIGN_FALSE_FIELDS = (
    "runtime_route_changed",
    "storage_write_performed",
    "bridge_append_performed",
    "solver_call_performed",
    "scheduler_enqueue_performed",
    "promotion_performed",
    "promotion_action_allowed",
    "gate_skip_performed",
    "network_access_performed",
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
)
_SOURCE_CANDIDATE_FALSE_FIELDS = (
    "runtime_route_changed",
    "storage_write_performed",
    "bridge_append_performed",
    "solver_call_performed",
    "scheduler_enqueue_performed",
    "promotion_performed",
    "promotion_action_allowed",
    "gate_skip_performed",
)
_REQUIRED_OPERATOR_CONTROLS = (
    "fresh_navigation_index_digest",
    "verified_shortcut_candidate_report",
    "operator_authorization",
    "shadow_replay_before_runtime_route",
    "rollback_plan",
)
_REQUIRED_PREFLIGHT_CHECKS = (
    "source_candidate_verification_ok",
    "candidate_action_boundary_false",
    "no_gate_skip",
    "no_solver_dispatch",
    "no_storage_or_bridge_write",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a read-only operator-gated runtime-promotion design from "
            "V12 Memory Palace shortcut candidates."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument(
        "--min-rank-score",
        type=float,
        default=DEFAULT_MIN_RANK_SCORE,
        help="Minimum shortcut rank score for candidate observability.",
    )
    parser.add_argument(
        "--min-intermediate-hops-skipped",
        type=int,
        default=DEFAULT_MIN_INTERMEDIATE_HOPS_SKIPPED,
        help="Minimum projected hierarchy hops skipped.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_memory_palace_shortcut_runtime_promotion_design(
            now_utc=_parse_utc(args.now) if args.now else None,
            min_rank_score=args.min_rank_score,
            min_intermediate_hops_skipped=args.min_intermediate_hops_skipped,
        )
    except ValueError as exc:
        print(
            f"Memory Palace shortcut runtime-promotion design FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_memory_palace_shortcut_runtime_promotion_design(
    *,
    now_utc: datetime | None = None,
    min_rank_score: float = DEFAULT_MIN_RANK_SCORE,
    min_intermediate_hops_skipped: int = DEFAULT_MIN_INTERMEDIATE_HOPS_SKIPPED,
    source_report: Mapping[str, Any] | None = None,
    source_verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a design-only runtime-promotion report."""

    _validate_thresholds(
        min_rank_score=min_rank_score,
        min_intermediate_hops_skipped=min_intermediate_hops_skipped,
    )
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidate_report = source_report or build_memory_palace_shortcut_promotion_candidate_report(
        now_utc=generated_at,
        min_rank_score=min_rank_score,
        min_intermediate_hops_skipped=min_intermediate_hops_skipped,
    )
    verification = source_verification or verify_memory_palace_shortcut_promotion_candidate_report(
        candidate_report
    )
    source_verified = _source_verified(candidate_report, verification)
    candidate_rows = list(candidate_report.get("promotion_candidates") or [])
    designs = (
        [
            _design_row(candidate)
            for candidate in candidate_rows
            if isinstance(candidate, Mapping) and _candidate_designable(candidate)
        ]
        if source_verified
        else []
    )
    designable_source_count = sum(
        1
        for candidate in candidate_rows
        if isinstance(candidate, Mapping) and _candidate_designable(candidate)
    )
    blockers = _blockers(
        candidate_report=candidate_report,
        verification=verification,
        source_verified=source_verified,
        designs=designs,
    )
    boundary = _authority_boundary(
        source_verified=source_verified,
        designs=designs,
    )
    ok = not blockers and _authority_boundary_ok(boundary)

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _utc_text(generated_at),
        "ok": ok,
        "blockers": sorted(set(blockers)),
        "claim_label": CLAIM_LABEL,
        "source_report_version": str(candidate_report.get("report_version", "")),
        "source_claim_label": str(candidate_report.get("claim_label", "")),
        "source_verification_version": str(
            verification.get("verification_version", "")
        ),
        "source_verification_ok": verification.get("ok") is True,
        "source_of_truth": str(candidate_report.get("source_of_truth", "")),
        "memory_id": str(candidate_report.get("memory_id", "")),
        "thresholds": {
            "min_rank_score": float(min_rank_score),
            "min_intermediate_hops_skipped": int(
                min_intermediate_hops_skipped
            ),
        },
        "design_summary": {
            "source_candidate_count": len(candidate_rows),
            "designable_source_candidate_count": designable_source_count,
            "runtime_promotion_design_count": len(designs),
            "top_design_target": (
                designs[0]["target_node_id"] if designs else "none"
            ),
        },
        "runtime_promotion_designs": designs,
        "authority_boundary": boundary,
        "no_overclaim_guardrails": {
            "design_only": True,
            "manual_review_required": True,
            "operator_gate_required_for_runtime_promotion": True,
            "not_router_dispatch": True,
            "not_solver_call": True,
            "not_storage_write": True,
            "not_bridge_append": True,
            "not_scheduler_enqueue": True,
            "not_gate_skip": True,
            "not_promotion_action": True,
            "not_networked_retrieval": True,
            "source_verification_required": True,
            "deterministic_local_fixture": True,
        },
        "operator_interpretation": (
            "These rows are an operator-gated design preflight for possible "
            "future Memory Palace shortcut runtime promotion. They do not "
            "change runtime routing and do not authorize promotion."
        ),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["design_summary"]
    thresholds = report["thresholds"]
    lines = [
        "# V12 Memory Palace Shortcut Runtime-Promotion Design",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- ok: `{str(report['ok']).lower()}`",
        f"- claim_label: `{report['claim_label']}`",
        f"- source_report_version: `{report['source_report_version']}`",
        f"- source_verification_version: `{report['source_verification_version']}`",
        f"- memory_id: `{report['memory_id']}`",
        "",
        "## Thresholds",
        "",
        f"- min_rank_score: `{thresholds['min_rank_score']}`",
        (
            "- min_intermediate_hops_skipped: `"
            + str(thresholds["min_intermediate_hops_skipped"])
            + "`"
        ),
        "",
        "## Summary",
        "",
        f"- source_candidate_count: `{summary['source_candidate_count']}`",
        (
            "- designable_source_candidate_count: `"
            + str(summary["designable_source_candidate_count"])
            + "`"
        ),
        (
            "- runtime_promotion_design_count: `"
            + str(summary["runtime_promotion_design_count"])
            + "`"
        ),
        f"- top_design_target: `{summary['top_design_target']}`",
        "",
        "## Design Rows",
        "",
        (
            "| target_node_id | rank_score | hops_skipped | "
            "operator_gate | action_allowed |"
        ),
        "| --- | ---: | ---: | --- | --- |",
    ]
    for design in report["runtime_promotion_designs"]:
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{design['target_node_id']}`",
                    f"`{design['rank_score']}`",
                    f"`{design['intermediate_hops_skipped']}`",
                    f"`{str(design['operator_gate_required']).lower()}`",
                    f"`{str(design['promotion_action_allowed']).lower()}`",
                )
            )
            + " |"
        )
    lines.extend(["", "## Boundary", ""])
    for key, value in sorted(report["authority_boundary"].items()):
        lines.append(f"- {key}: `{str(value).lower()}`")
    lines.extend(
        [
            "",
            "## What This Proves",
            "",
            (
                "Verified shortcut candidates can be translated into "
                "operator-gated runtime-promotion design rows without "
                "activating a runtime route or skipping intermediate proof "
                "gates."
            ),
            "",
            "## What This Does Not Do",
            "",
            (
                "This does not promote a route, dispatch a solver, enqueue "
                "scheduler work, append bridge events, write storage, skip a "
                "gate, make a release decision, or grant runtime authority."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _design_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    hierarchy_hops = int(candidate.get("hierarchy_hops") or 0)
    return {
        "design_id": _design_id(str(candidate.get("shortcut_id", ""))),
        "memory_id": str(candidate.get("memory_id", "")),
        "source_node_id": str(candidate.get("source_node_id", "")),
        "target_node_id": str(candidate.get("target_node_id", "")),
        "shortcut_id": str(candidate.get("shortcut_id", "")),
        "rank_score": float(candidate.get("rank_score") or 0.0),
        "placement_confidence": float(candidate.get("placement_confidence") or 0.0),
        "shortcut_confidence": float(candidate.get("shortcut_confidence") or 0.0),
        "hierarchy_hops": hierarchy_hops,
        "projected_shortcut_hops": 1 if hierarchy_hops else 0,
        "intermediate_hops_skipped": max(0, hierarchy_hops - 1),
        "matched_selector_keys": list(candidate.get("matched_selector_keys") or []),
        "design_status": "operator_gate_required_before_runtime_promotion",
        "required_operator_controls": list(_REQUIRED_OPERATOR_CONTROLS),
        "required_preflight_checks": list(_REQUIRED_PREFLIGHT_CHECKS),
        "operator_gate_required": True,
        "manual_review_required": True,
        "runtime_route_changed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "solver_call_performed": False,
        "scheduler_enqueue_performed": False,
        "promotion_performed": False,
        "promotion_action_allowed": False,
        "gate_skip_performed": False,
        "network_access_performed": False,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
    }


def _blockers(
    *,
    candidate_report: Mapping[str, Any],
    verification: Mapping[str, Any],
    source_verified: bool,
    designs: Sequence[Mapping[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    if candidate_report.get("ok") is not True:
        blockers.append("source_candidate_report_not_ok")
    if verification.get("ok") is not True:
        blockers.append("source_candidate_verification_not_ok")
    for blocker in list(verification.get("blockers") or []):
        blockers.append(f"source_candidate_verification_blocker:{blocker}")
    if not source_verified:
        blockers.append("source_candidate_report_not_verified")
    if not designs:
        blockers.append("no_operator_gated_runtime_design_candidates")
    for index, design in enumerate(designs):
        if not _design_row_boundary_ok(design):
            blockers.append(f"runtime_design_{index}_authority_boundary_not_ok")
    return blockers


def _authority_boundary(
    *,
    source_verified: bool,
    designs: Sequence[Mapping[str, Any]],
) -> dict[str, bool]:
    return {
        "source_candidate_report_ok": source_verified,
        "source_candidate_verification_ok": source_verified,
        "design_only": True,
        "manual_review_required": True,
        "operator_gate_required_for_runtime_promotion": True,
        "all_design_rows_action_free": all(
            _design_row_boundary_ok(design) for design in designs
        ),
        "runtime_route_changed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "solver_call_performed": False,
        "scheduler_enqueue_performed": False,
        "promotion_performed": False,
        "promotion_action_allowed": False,
        "gate_skip_performed": False,
        "network_access_performed": False,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
    }


def _authority_boundary_ok(boundary: Mapping[str, Any]) -> bool:
    required_true = (
        "source_candidate_report_ok",
        "source_candidate_verification_ok",
        "design_only",
        "manual_review_required",
        "operator_gate_required_for_runtime_promotion",
        "all_design_rows_action_free",
    )
    return all(boundary.get(field) is True for field in required_true) and all(
        boundary.get(field) is False for field in _DESIGN_FALSE_FIELDS
    )


def _source_verified(
    candidate_report: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> bool:
    return (
        candidate_report.get("ok") is True
        and verification.get("ok") is True
        and not list(verification.get("blockers") or [])
    )


def _candidate_designable(candidate: Mapping[str, Any]) -> bool:
    return (
        candidate.get("promotion_observable") is True
        and candidate.get("promotion_action_allowed") is False
        and candidate.get("authority_flags_false") is True
        and all(
            candidate.get(field) is False for field in _SOURCE_CANDIDATE_FALSE_FIELDS
        )
    )


def _design_row_boundary_ok(design: Mapping[str, Any]) -> bool:
    return (
        design.get("operator_gate_required") is True
        and design.get("manual_review_required") is True
        and all(design.get(field) is False for field in _DESIGN_FALSE_FIELDS)
    )


def _design_id(shortcut_id: str) -> str:
    digest = hashlib.sha256(shortcut_id.encode("utf-8")).hexdigest()[:16]
    return f"runtime_design.{digest}"


def _validate_thresholds(
    *,
    min_rank_score: float,
    min_intermediate_hops_skipped: int,
) -> None:
    if not (0.0 <= float(min_rank_score) <= 1.0):
        raise ValueError("--min-rank-score must be between 0 and 1")
    if min_intermediate_hops_skipped < 0:
        raise ValueError("--min-intermediate-hops-skipped must be >= 0")


def _parse_utc(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("--now must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("--now must include timezone information")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
