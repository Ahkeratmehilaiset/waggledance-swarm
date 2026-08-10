#!/usr/bin/env python3
"""Analyze candidate-mode hybrid route-selection traces.

These rows contain route metadata only. They do not contain solver execution,
an independently evaluated task outcome, or a counterfactual oracle. This tool
therefore reports diagnostics and always leaves promotion NOT EVALUATED.

Usage:
    python tools/analyze_hybrid_candidate_trace.py
    python tools/analyze_hybrid_candidate_trace.py --window-hours 24
    python tools/analyze_hybrid_candidate_trace.py --by-day
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path


TRACE_FILE = Path("data/runtime/magma_hybrid_candidate_trace.jsonl")
SOLVER_LAYERS = frozenset(
    {"model_based", "rule_constraints", "statistical"}
)
FALLBACK_LAYERS = frozenset({"retrieval", "llm_reasoning"})
EVIDENCE_KIND = "route_selection_diagnostic_only"
OUTCOME_BLOCKER = "missing_executable_solver_outcome_oracle"


def load_trace(
    path: Path, since: datetime | None = None
) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if type(row) is not dict:
                continue
            if since:
                ts = row.get("ts", "")
                try:
                    row_time = datetime.fromisoformat(
                        ts.replace("Z", "+00:00")
                    )
                    if row_time < since:
                        continue
                except (ValueError, TypeError):
                    continue
            rows.append(row)
    return rows


def classify_row(row: dict) -> str:
    """Bucket route metadata without inferring correctness or outcomes."""
    kw_layer = row.get("keyword_layer", "")
    hybrid_solver = row.get("hybrid_top_solver")
    passed_thresh = row.get("passed_threshold", False)
    passed_qf = row.get("passed_question_frame", False)
    rejected = row.get("hybrid_rejected_off_domain", False)

    if kw_layer not in SOLVER_LAYERS | FALLBACK_LAYERS:
        return "unknown_keyword_route_semantics"

    keyword_chose_solver = kw_layer in SOLVER_LAYERS
    hybrid_has_compatible_candidate = (
        bool(hybrid_solver)
        and passed_thresh is True
        and passed_qf is True
    )

    if keyword_chose_solver and hybrid_has_compatible_candidate:
        return "agreement_both_solver_candidates"
    if keyword_chose_solver and rejected:
        return "hybrid_question_frame_rejection_keyword_solver"
    if keyword_chose_solver:
        return "keyword_solver_hybrid_no_compatible_candidate"
    if hybrid_has_compatible_candidate:
        return "hybrid_compatible_candidate_keyword_fallback"
    if not keyword_chose_solver and rejected:
        return "keyword_fallback_hybrid_question_frame_rejection"
    return "keyword_fallback_hybrid_no_compatible_candidate"


def _base_report(rows: list[dict]) -> dict:
    return {
        "schema_version": "wd.hybrid_route_selection_diagnostic.v1",
        "evidence_kind": EVIDENCE_KIND,
        "outcome_evidence_present": False,
        "promotion_gate_evaluated": False,
        "promotion_gate_pass": False,
        "promotion_blockers": [OUTCOME_BLOCKER],
        # Deliberately retained as null migration guards for legacy parsers.
        "hybrid_unique_correct": None,
        "hybrid_unique_incorrect": None,
        "promotion_ratio": None,
        "total_queries": len(rows),
        "time_range": {
            "first": rows[0].get("ts") if rows else None,
            "last": rows[-1].get("ts") if rows else None,
        },
    }


def analyze(rows: list[dict]) -> dict:
    report = _base_report(rows)
    if not rows:
        return {
            **report,
            "error": "no trace rows",
            "classification": {},
            "route_selection_ratio": None,
            "diagnostics": {
                "threshold_pass_count": 0,
                "question_frame_pass_count": 0,
                "solver_matched_at_all_count": 0,
                "rejected_off_domain_count": 0,
                "avg_above_threshold_score": None,
            },
        }

    counts = defaultdict(int)
    for r in rows:
        counts[classify_row(r)] += 1

    candidate_count = counts.get(
        "hybrid_compatible_candidate_keyword_fallback", 0
    )
    rejection_count = counts.get(
        "hybrid_question_frame_rejection_keyword_solver", 0
    )
    route_ratio = (
        round(candidate_count / rejection_count, 2)
        if rejection_count
        else None
    )

    # Additional diagnostics
    threshold_pass = sum(1 for r in rows if r.get("passed_threshold"))
    qf_pass = sum(1 for r in rows if r.get("passed_question_frame"))
    solver_matched = sum(1 for r in rows if r.get("hybrid_top_solver"))
    rejected = sum(1 for r in rows if r.get("hybrid_rejected_off_domain"))

    # Latency check (embedded in keyword_confidence field? no — we don't track it)
    scores_above_thresh = [
        r.get("hybrid_top_score", 0) for r in rows if r.get("passed_threshold")
    ]

    return {
        **report,
        "classification": dict(counts),
        "hybrid_compatible_candidate_keyword_fallback": candidate_count,
        "hybrid_question_frame_rejection_keyword_solver": rejection_count,
        "route_selection_ratio": route_ratio,
        "diagnostics": {
            "threshold_pass_count": threshold_pass,
            "question_frame_pass_count": qf_pass,
            "solver_matched_at_all_count": solver_matched,
            "rejected_off_domain_count": rejected,
            "avg_above_threshold_score": (
                round(sum(scores_above_thresh) / len(scores_above_thresh), 3)
                if scores_above_thresh else None
            ),
        },
    }


def print_report(analysis: dict, detailed: bool = False) -> None:
    cls = analysis["classification"]
    total = analysis["total_queries"]

    print("=== Phase D hybrid route-selection diagnostic ===")
    print(f"  Queries analyzed: {total}")
    if "error" in analysis:
        print(f"  Trace status: {analysis['error']}")
    first = analysis["time_range"]["first"] or "?"
    last = analysis["time_range"]["last"] or "?"
    print(f"  Time range: {str(first)[:19]} -> {str(last)[:19]}")
    print()
    print(f"{'Classification':<40} {'Count':>6} {'%':>8}")
    print("-" * 56)
    for k, v in sorted(cls.items(), key=lambda x: -x[1]):
        pct = 100 * v / total
        print(f"  {k:<38} {v:>6} {pct:>7.1f}%")
    print()
    print("Outcome/promotion gate:")
    print("  VERDICT = BLOCKED — NOT EVALUATED")
    print(f"  blocker = {analysis['promotion_blockers'][0]}")
    print("  reason  = route metadata contains no executable task outcome")
    print()
    if detailed:
        d = analysis["diagnostics"]
        print("Diagnostics:")
        print(f"  passed_threshold: {d['threshold_pass_count']}")
        print(f"  passed_question_frame: {d['question_frame_pass_count']}")
        print(f"  solver_matched_at_all: {d['solver_matched_at_all_count']}")
        print(f"  rejected_off_domain: {d['rejected_off_domain_count']}")
        print(f"  avg_above_threshold_score: {d['avg_above_threshold_score']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace-file", default=str(TRACE_FILE))
    ap.add_argument("--window-hours", type=float, default=None,
                    help="Analyze only last N hours")
    ap.add_argument("--by-day", action="store_true",
                    help="Break down by day")
    ap.add_argument("--json", action="store_true", help="Output raw JSON")
    ap.add_argument("--detailed", action="store_true")
    args = ap.parse_args()

    path = Path(args.trace_file)
    since = None
    if args.window_hours:
        since = datetime.now(timezone.utc) - timedelta(hours=args.window_hours)
    rows = load_trace(path, since=since)

    if args.by_day:
        by_day = defaultdict(list)
        for r in rows:
            by_day[r.get("ts", "")[:10]].append(r)
        if not by_day:
            analysis = analyze([])
            if args.json:
                print(json.dumps(analysis, indent=2, ensure_ascii=False))
            else:
                print_report(analysis, detailed=args.detailed)
            return 1
        for day in sorted(by_day):
            print(f"\n\n=== Day {day} ===")
            print_report(analyze(by_day[day]), detailed=args.detailed)
    else:
        analysis = analyze(rows)
        if args.json:
            print(json.dumps(analysis, indent=2, ensure_ascii=False))
        else:
            print_report(analysis, detailed=args.detailed)

        if "error" in analysis:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
