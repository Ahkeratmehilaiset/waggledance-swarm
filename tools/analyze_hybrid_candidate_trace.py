#!/usr/bin/env python3
"""Phase D-3 promotion gate analysis.

Reads docs/runs/magma_hybrid_candidate_trace.jsonl (written by
HybridObserver in candidate mode) and computes:

  hybrid_unique_correct:    keyword chose retrieval/llm, hybrid would
                            have chosen a solver AND the solver passed
                            question_frame check (likely better answer)
  hybrid_unique_incorrect:  keyword chose solver, hybrid would have
                            rejected off-domain (possibly overcautious)
  agreement:                both chose solver
  keyword_only_solver:      keyword chose solver, hybrid below threshold
                            (likely fine — keyword was right)
  neither_chose_solver:     both fell through to retrieval/llm

Phase D-3 promotion gate per v3:
  Proceed to authoritative mode if
    hybrid_unique_correct / max(1, hybrid_unique_incorrect) >= 3.0

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


TRACE_FILE = Path("docs/runs/magma_hybrid_candidate_trace.jsonl")


def load_trace(path: Path, since: datetime = None) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since:
            ts = r.get("ts", "")
            try:
                row_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if row_time < since:
                    continue
            except (ValueError, TypeError):
                continue
        rows.append(r)
    return rows


def classify_row(row: dict) -> str:
    """Bucket each trace row."""
    kw_layer = row.get("keyword_layer", "")
    hybrid_solver = row.get("hybrid_top_solver")
    passed_thresh = row.get("passed_threshold", False)
    passed_qf = row.get("passed_question_frame", False)
    rejected = row.get("hybrid_rejected_off_domain", False)

    keyword_chose_solver = kw_layer in ("model_based", "rule_constraints", "statistical")
    hybrid_chose_solver = bool(hybrid_solver) and passed_thresh and passed_qf

    if keyword_chose_solver and hybrid_chose_solver:
        return "agreement_both_solver"
    if keyword_chose_solver and rejected:
        return "hybrid_unique_incorrect"    # hybrid overcautious
    if keyword_chose_solver and not hybrid_chose_solver and not rejected:
        return "keyword_only_solver"        # hybrid uncertain, keyword confident
    if not keyword_chose_solver and hybrid_chose_solver:
        return "hybrid_unique_correct"      # hybrid would have found a solver
    if not keyword_chose_solver and rejected:
        return "agreement_reject_off_domain"
    return "neither_chose_solver"


def analyze(rows: list[dict]) -> dict:
    if not rows:
        return {"error": "no trace rows"}

    counts = defaultdict(int)
    for r in rows:
        counts[classify_row(r)] += 1

    uc = counts["hybrid_unique_correct"]
    ui = counts["hybrid_unique_incorrect"]
    ratio = uc / max(1, ui)

    # Additional diagnostics
    total = len(rows)
    threshold_pass = sum(1 for r in rows if r.get("passed_threshold"))
    qf_pass = sum(1 for r in rows if r.get("passed_question_frame"))
    solver_matched = sum(1 for r in rows if r.get("hybrid_top_solver"))
    rejected = sum(1 for r in rows if r.get("hybrid_rejected_off_domain"))

    # Latency check (embedded in keyword_confidence field? no — we don't track it)
    scores_above_thresh = [
        r.get("hybrid_top_score", 0) for r in rows if r.get("passed_threshold")
    ]

    return {
        "total_queries": total,
        "time_range": {
            "first": rows[0].get("ts", "?"),
            "last": rows[-1].get("ts", "?"),
        },
        "classification": dict(counts),
        "hybrid_unique_correct": uc,
        "hybrid_unique_incorrect": ui,
        "promotion_ratio": round(ratio, 2),
        "promotion_gate_pass": ratio >= 3.0,
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


def print_report(analysis: dict, detailed: bool = False):
    if "error" in analysis:
        print(f"ERROR: {analysis['error']}")
        return

    cls = analysis["classification"]
    total = analysis["total_queries"]

    print(f"=== Phase D-3 promotion gate analysis ===")
    print(f"  Queries analyzed: {total}")
    print(f"  Time range: {analysis['time_range']['first'][:19]} -> {analysis['time_range']['last'][:19]}")
    print()
    print(f"{'Classification':<40} {'Count':>6} {'%':>8}")
    print("-" * 56)
    for k, v in sorted(cls.items(), key=lambda x: -x[1]):
        pct = 100 * v / total
        marker = "  <-" if k in ("hybrid_unique_correct", "hybrid_unique_incorrect") else ""
        print(f"  {k:<38} {v:>6} {pct:>7.1f}%{marker}")
    print()
    print(f"Promotion gate:")
    print(f"  hybrid_unique_correct   = {analysis['hybrid_unique_correct']}")
    print(f"  hybrid_unique_incorrect = {analysis['hybrid_unique_incorrect']}")
    print(f"  ratio                   = {analysis['promotion_ratio']:.2f} (gate requires >= 3.0)")
    print(f"  VERDICT                 = {'PASS — proceed to Phase D-3 authoritative' if analysis['promotion_gate_pass'] else 'INSUFFICIENT — stay in candidate mode, collect more data'}")
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
        for day in sorted(by_day):
            print(f"\n\n=== Day {day} ===")
            print_report(analyze(by_day[day]), detailed=args.detailed)
    else:
        analysis = analyze(rows)
        if args.json:
            print(json.dumps(analysis, indent=2, ensure_ascii=False))
        else:
            print_report(analysis, detailed=args.detailed)

    return 0


if __name__ == "__main__":
    sys.exit(main())
