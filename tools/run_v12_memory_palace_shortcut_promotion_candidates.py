# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only V12 Memory Palace shortcut promotion-candidate report.

The report derives candidate rows from the local Memory Palace shortcut proof.
It intentionally stops before any runtime promotion boundary: it does not
modify routing, dispatch solvers, enqueue scheduler work, append bridge events,
write storage, or grant promotion authority.
"""
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

from tools.run_v12_memory_palace_shortcut_proof import (  # noqa: E402
    build_memory_palace_shortcut_proof,
)


REPORT_VERSION = "wd.v12.memory_palace_shortcut_promotion_candidates.v0"
CLAIM_LABEL = "MEASURED_LOCAL_PROMOTION_CANDIDATE_REPORT"
DEFAULT_MIN_RANK_SCORE = 0.6
DEFAULT_MIN_INTERMEDIATE_HOPS_SKIPPED = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a read-only promotion-candidate report from the V12 Memory "
            "Palace shortcut proof."
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
        report = build_memory_palace_shortcut_promotion_candidate_report(
            now_utc=_parse_utc(args.now) if args.now else None,
            min_rank_score=args.min_rank_score,
            min_intermediate_hops_skipped=args.min_intermediate_hops_skipped,
        )
    except ValueError as exc:
        print(
            f"Memory Palace shortcut promotion-candidate report FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_memory_palace_shortcut_promotion_candidate_report(
    *,
    now_utc: datetime | None = None,
    min_rank_score: float = DEFAULT_MIN_RANK_SCORE,
    min_intermediate_hops_skipped: int = DEFAULT_MIN_INTERMEDIATE_HOPS_SKIPPED,
) -> dict[str, Any]:
    _validate_thresholds(
        min_rank_score=min_rank_score,
        min_intermediate_hops_skipped=min_intermediate_hops_skipped,
    )
    source = build_memory_palace_shortcut_proof(now_utc=now_utc)
    candidates = [
        _promotion_candidate(
            candidate,
            min_rank_score=min_rank_score,
            min_intermediate_hops_skipped=min_intermediate_hops_skipped,
        )
        for candidate in source["ranked_shortcuts"]["candidates"]
    ]
    observable = [
        candidate for candidate in candidates if candidate["promotion_observable"]
    ]
    boundary = _authority_boundary(source, candidates)
    ok = (
        source.get("ok") is True
        and bool(observable)
        and _authority_boundary_ok(boundary)
    )

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": source["generated_at_utc"],
        "ok": ok,
        "claim_label": CLAIM_LABEL,
        "source_report_version": source["report_version"],
        "source_claim_label": source["claim_label"],
        "memory_id": source["memory_id"],
        "source_of_truth": source["source_of_truth"],
        "thresholds": {
            "min_rank_score": float(min_rank_score),
            "min_intermediate_hops_skipped": int(
                min_intermediate_hops_skipped
            ),
        },
        "candidate_summary": {
            "source_candidate_count": len(candidates),
            "promotion_observable_count": len(observable),
            "blocked_count": len(candidates) - len(observable),
            "top_candidate_target": (
                observable[0]["target_node_id"] if observable else "none"
            ),
        },
        "promotion_candidates": candidates,
        "authority_boundary": boundary,
        "no_overclaim_guardrails": {
            "not_router_dispatch": True,
            "not_solver_call": True,
            "not_storage_write": True,
            "not_bridge_append": True,
            "not_scheduler_enqueue": True,
            "not_gate_skip": True,
            "not_promotion_action": True,
            "operator_gate_required_for_runtime_promotion": True,
            "deterministic_local_fixture": True,
        },
        "operator_interpretation": (
            "These rows identify shortcut candidates worth later promotion "
            "design/review. They are not promoted routes and do not skip any "
            "runtime, scheduler, bridge, solver, storage, or operator gate."
        ),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["candidate_summary"]
    thresholds = report["thresholds"]
    lines = [
        "# V12 Memory Palace Shortcut Promotion Candidates",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- ok: `{str(report['ok']).lower()}`",
        f"- claim_label: `{report['claim_label']}`",
        f"- source_report_version: `{report['source_report_version']}`",
        f"- source_of_truth: `{report['source_of_truth']}`",
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
        (
            "- source_candidate_count: `"
            + str(summary["source_candidate_count"])
            + "`"
        ),
        (
            "- promotion_observable_count: `"
            + str(summary["promotion_observable_count"])
            + "`"
        ),
        f"- blocked_count: `{summary['blocked_count']}`",
        f"- top_candidate_target: `{summary['top_candidate_target']}`",
        "",
        "## Candidates",
        "",
        (
            "| target_node_id | rank_score | hops_skipped | "
            "observable | action_allowed |"
        ),
        "| --- | ---: | ---: | --- | --- |",
    ]
    for candidate in report["promotion_candidates"]:
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{candidate['target_node_id']}`",
                    f"`{candidate['rank_score']}`",
                    f"`{candidate['intermediate_hops_skipped']}`",
                    f"`{str(candidate['promotion_observable']).lower()}`",
                    f"`{str(candidate['promotion_action_allowed']).lower()}`",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
        ]
    )
    for key, value in sorted(report["authority_boundary"].items()):
        lines.append(f"- {key}: `{str(value).lower()}`")
    lines.extend(
        [
            "",
            "## What This Proves",
            "",
            (
                "Shortcut proof rows can be converted into deterministic "
                "promotion-candidate observations for later review without "
                "loading intermediate hierarchy nodes or changing runtime "
                "routing."
            ),
            "",
            "## What This Does Not Do",
            "",
            (
                "This does not promote a route, dispatch a solver, enqueue "
                "scheduler work, append bridge events, write storage, skip a "
                "gate, or grant runtime authority."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _promotion_candidate(
    candidate: Mapping[str, Any],
    *,
    min_rank_score: float,
    min_intermediate_hops_skipped: int,
) -> dict[str, Any]:
    hierarchy_hops = int(candidate.get("hierarchy_hops") or 0)
    rank_score = float(candidate.get("rank_score") or 0.0)
    intermediate_hops_skipped = max(0, hierarchy_hops - 1)
    authority_flags_false = _candidate_authority_flags_false(candidate)
    promotion_observable = (
        rank_score >= float(min_rank_score)
        and intermediate_hops_skipped >= int(min_intermediate_hops_skipped)
        and authority_flags_false
        and candidate.get("no_runtime_mutation") is True
    )
    return {
        "memory_id": candidate.get("memory_id", ""),
        "source_node_id": candidate.get("source_node_id", ""),
        "target_node_id": candidate.get("target_node_id", ""),
        "shortcut_id": candidate.get("shortcut_id", ""),
        "rank_score": rank_score,
        "placement_confidence": float(candidate.get("placement_confidence") or 0.0),
        "shortcut_confidence": float(candidate.get("shortcut_confidence") or 0.0),
        "hierarchy_hops": hierarchy_hops,
        "projected_shortcut_hops": 1 if hierarchy_hops else 0,
        "intermediate_hops_skipped": intermediate_hops_skipped,
        "matched_selector_keys": list(candidate.get("matched_selector_keys") or []),
        "promotion_observable": promotion_observable,
        "promotion_action_allowed": False,
        "candidate_status": (
            "observable_pending_operator_design"
            if promotion_observable
            else "blocked_by_threshold_or_authority_boundary"
        ),
        "authority_flags_false": authority_flags_false,
        "runtime_route_changed": False,
        "solver_call_performed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "scheduler_enqueue_performed": False,
        "gate_skip_performed": False,
        "promotion_performed": False,
    }


def _authority_boundary(
    source: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, bool]:
    source_boundary = source.get("authority_boundary") or {}
    return {
        "source_proof_ok": source.get("ok") is True,
        "source_authority_boundary_ok": _source_authority_boundary_ok(
            source_boundary,
        ),
        "read_side_report_only": True,
        "all_candidates_authority_flags_false": all(
            candidate.get("authority_flags_false") is True
            for candidate in candidates
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
    }


def _authority_boundary_ok(boundary: Mapping[str, bool]) -> bool:
    required_true = (
        "source_proof_ok",
        "source_authority_boundary_ok",
        "read_side_report_only",
        "all_candidates_authority_flags_false",
    )
    required_false = (
        "runtime_route_changed",
        "storage_write_performed",
        "bridge_append_performed",
        "solver_call_performed",
        "scheduler_enqueue_performed",
        "promotion_performed",
        "promotion_action_allowed",
        "gate_skip_performed",
        "network_access_performed",
    )
    return (
        all(boundary.get(field) is True for field in required_true)
        and all(boundary.get(field) is False for field in required_false)
    )


def _source_authority_boundary_ok(boundary: Mapping[str, Any]) -> bool:
    required_true = (
        "read_side_projection_only",
        "projection_authority_flags_false",
        "candidate_no_runtime_mutation",
        "candidate_authority_flags_false",
    )
    required_false = (
        "runtime_route_changed",
        "storage_write_performed",
        "bridge_append_performed",
        "solver_call_performed",
        "scheduler_enqueue_performed",
        "promotion_performed",
        "gate_skip_performed",
        "network_access_performed",
    )
    return (
        all(boundary.get(field) is True for field in required_true)
        and all(boundary.get(field) is False for field in required_false)
    )


def _candidate_authority_flags_false(candidate: Mapping[str, Any]) -> bool:
    false_fields = (
        "runtime_authority",
        "storage_write_authority",
        "bridge_write_authority",
        "gate_skip_authority",
        "promotion_authority",
        "solver_call_authority",
    )
    return all(candidate.get(field) is False for field in false_fields)


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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
