"""Build procedural memory from successful action chains.

Phase 7 migration: Reads case trajectories (from build_case_trajectories_from_legacy.py)
and extracts proven procedures (GOLD/SILVER chains) and anti-patterns (QUARANTINE chains)
into the procedural store.

Usage:
    python tools/backfill_procedural_memory.py [--input data/case_trajectories.jsonl]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys


DEFAULT_INPUT = "data/case_trajectories.jsonl"
DEFAULT_DB = "data/procedural_store.db"


def load_trajectories(path: str) -> list:
    """Load case trajectories from JSONL."""
    if not os.path.exists(path):
        return []
    trajectories = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trajectories.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return trajectories


def extract_capability_chain(trajectory: dict) -> list:
    """Extract capability IDs used in a trajectory."""
    caps = trajectory.get("selected_capabilities", [])
    return [c.get("capability_id", "") for c in caps if c.get("capability_id")]


def build_procedure_id(intent: str, chain: list) -> str:
    """Generate deterministic procedure ID."""
    key = f"{intent}:{':'.join(chain)}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def main():
    parser = argparse.ArgumentParser(
        description="Backfill procedural memory from case trajectories")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--db", default=DEFAULT_DB)
    args = parser.parse_args()

    print(f"Loading case trajectories from {args.input}...")
    trajectories = load_trajectories(args.input)
    print(f"  {len(trajectories)} trajectories loaded")

    if not trajectories:
        print("No trajectories found. Run build_case_trajectories_from_legacy.py first.")
        sys.exit(0)

    # Use the SQLiteProceduralStore
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from waggledance.adapters.persistence.sqlite_procedural_store import SQLiteProceduralStore
        store = SQLiteProceduralStore(db_path=args.db)
    except ImportError:
        print("WARNING: SQLiteProceduralStore not available, using direct SQLite")
        store = None

    procedures = 0
    anti_patterns = 0

    for traj in trajectories:
        grade = traj.get("quality_grade", "bronze")
        chain = extract_capability_chain(traj)
        if not chain:
            continue

        goal = traj.get("goal", {})
        intent = goal.get("type", "OBSERVE")
        proc_id = build_procedure_id(intent, chain)

        if grade in ("gold", "silver"):
            if store:
                store.store_procedure(
                    procedure_id=proc_id,
                    intent=intent,
                    capability_chain=chain,
                    quality_grade=grade,
                    source_case_id=traj.get("trajectory_id", ""),
                )
            procedures += 1

        elif grade == "quarantine":
            verifier = traj.get("verifier_result", {})
            reason = ""
            if verifier.get("has_correction"):
                reason = "corrected_by_user"
            elif not verifier.get("passed", True):
                reason = "verifier_failed"
            if store:
                store.store_anti_pattern(
                    pattern_id=proc_id,
                    intent=intent,
                    capability_chain=chain,
                    failure_reason=reason,
                )
            anti_patterns += 1

    if store:
        stats = store.stats()
        store.close()
        print(f"\nProcedural store: {stats}")
    print(f"\nDone: {procedures} procedures, {anti_patterns} anti-patterns")


if __name__ == "__main__":
    main()
