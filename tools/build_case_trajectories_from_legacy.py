"""Build case trajectories from legacy Q&A data + audit + telemetry.

Phase 7 migration: Converts existing training pairs, learning ledger events,
corrections, and route telemetry into CaseTrajectory objects for the
new learning pipeline.

Usage:
    python tools/build_case_trajectories_from_legacy.py [--output data/case_trajectories.jsonl]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional

TRAINING_DB = "data/waggle_dance.db"
AUDIT_DB = "data/audit_log.db"
LEDGER_PATH = "data/learning_ledger.jsonl"
CORRECTIONS_COLLECTION = "corrections"
OUTPUT_DEFAULT = "data/case_trajectories.jsonl"


INTENT_MAP = {
    "calculate": "SOLVE",
    "laske": "SOLVE",
    "what is": "OBSERVE",
    "mikä on": "OBSERVE",
    "why": "DIAGNOSE",
    "miksi": "DIAGNOSE",
    "how to": "PLAN",
    "miten": "PLAN",
    "optimize": "OPTIMIZE",
    "optimoi": "OPTIMIZE",
    "check": "VERIFY",
    "tarkista": "VERIFY",
    "protect": "PROTECT",
    "suojaa": "PROTECT",
}


def infer_goal_type(question: str) -> str:
    """Infer goal type from question text."""
    q_lower = question.lower()
    for keyword, goal_type in INTENT_MAP.items():
        if keyword in q_lower:
            return goal_type
    return "OBSERVE"


def infer_quality_grade(entry: dict) -> str:
    """Infer quality grade from legacy data signals."""
    source = entry.get("source", "")
    confidence = entry.get("confidence", 0.0)
    has_correction = entry.get("has_correction", False)

    if has_correction:
        return "quarantine"
    if "solver" in source or "math" in source or "symbolic" in source:
        return "gold" if confidence > 0.8 else "silver"
    if "micromodel" in source or "memory" in source:
        return "silver"
    if confidence > 0.9:
        return "silver"
    return "bronze"


def load_training_pairs(db_path: str) -> List[Dict]:
    """Load Q&A training pairs from SQLite."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    pairs = []
    try:
        rows = conn.execute(
            "SELECT question, answer, confidence, source, timestamp "
            "FROM training_pairs ORDER BY timestamp"
        ).fetchall()
        for q, a, conf, src, ts in rows:
            pairs.append({
                "question": q,
                "answer": a,
                "confidence": conf or 0.0,
                "source": src or "",
                "timestamp": ts or 0.0,
            })
    except Exception:
        pass
    conn.close()
    return pairs


def load_corrections(db_path: str) -> Dict[str, str]:
    """Load corrections from SQLite (if available)."""
    corrections = {}
    if not os.path.exists(db_path):
        return corrections
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT original_text, corrected_text FROM corrections"
        ).fetchall()
        for orig, corr in rows:
            corrections[orig] = corr
    except Exception:
        pass
    conn.close()
    return corrections


def load_ledger_events(path: str) -> List[Dict]:
    """Load learning ledger events."""
    events = []
    if not os.path.exists(path):
        return events
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def build_trajectory(entry: dict, corrections: dict,
                     index: int) -> Dict[str, Any]:
    """Build a CaseTrajectory dict from a single legacy entry."""
    question = entry.get("question", "")
    answer = entry.get("answer", "")
    has_correction = question in corrections

    trajectory_id = hashlib.sha256(
        f"{question}:{answer}:{index}".encode()
    ).hexdigest()[:16]

    goal_type = infer_goal_type(question)
    quality_grade = infer_quality_grade({**entry, "has_correction": has_correction})

    return {
        "trajectory_id": trajectory_id,
        "goal": {
            "goal_id": f"legacy-{trajectory_id[:8]}",
            "type": goal_type,
            "description": question,
            "status": "archived",
        },
        "world_snapshot_before": {
            "snapshot_id": f"ws-before-{trajectory_id[:8]}",
            "timestamp": entry.get("timestamp", 0),
            "entities": {},
            "baselines": {},
            "residuals": {},
        },
        "selected_capabilities": [{
            "capability_id": f"legacy.{entry.get('source', 'llm')}",
            "category": "SOLVE" if goal_type == "SOLVE" else "EXPLAIN",
        }],
        "actions": [{
            "action_id": f"act-{trajectory_id[:8]}",
            "capability_id": f"legacy.{entry.get('source', 'llm')}",
            "payload": {"question": question, "answer": answer},
            "status": "executed",
        }],
        "world_snapshot_after": {
            "snapshot_id": f"ws-after-{trajectory_id[:8]}",
            "timestamp": entry.get("timestamp", 0),
            "entities": {},
            "baselines": {},
            "residuals": {},
        },
        "verifier_result": {
            "passed": not has_correction,
            "confidence": entry.get("confidence", 0.5),
            "has_correction": has_correction,
        },
        "quality_grade": quality_grade,
        "source": "legacy_migration",
        "created_at": entry.get("timestamp", time.time()),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Build case trajectories from legacy data")
    parser.add_argument("--db", default=TRAINING_DB)
    parser.add_argument("--output", default=OUTPUT_DEFAULT)
    args = parser.parse_args()

    print("Loading legacy training pairs...")
    pairs = load_training_pairs(args.db)
    print(f"  {len(pairs)} training pairs loaded")

    print("Loading corrections...")
    corrections = load_corrections(args.db)
    print(f"  {len(corrections)} corrections loaded")

    print("Building case trajectories...")
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    counts = {"gold": 0, "silver": 0, "bronze": 0, "quarantine": 0}

    with open(args.output, "w", encoding="utf-8") as f:
        for i, pair in enumerate(pairs):
            trajectory = build_trajectory(pair, corrections, i)
            grade = trajectory["quality_grade"]
            counts[grade] = counts.get(grade, 0) + 1
            f.write(json.dumps(trajectory, ensure_ascii=False) + "\n")

    total = sum(counts.values())
    print(f"\nDone: {total} case trajectories written to {args.output}")
    for grade, count in counts.items():
        pct = count / total * 100 if total else 0
        print(f"  {grade}: {count} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
