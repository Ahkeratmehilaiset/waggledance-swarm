"""Backfill provenance records from case trajectories.

Phase 7 migration: Reads case trajectories (from build_case_trajectories_from_legacy.py)
and creates provenance records with tiered source types for each fact.

Usage:
    python tools/backfill_provenance.py [--input data/case_trajectories.jsonl]
"""

from __future__ import annotations

import argparse
import json
import os
import sys


DEFAULT_INPUT = "data/case_trajectories.jsonl"

# Quality grade → provenance source_type mapping
GRADE_TO_SOURCE_TYPE = {
    "gold": "confirmed_by_verifier",
    "silver": "inferred_by_solver",
    "bronze": "proposed_by_llm",
    "quarantine": "proposed_by_llm",
}


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


def grade_to_source_type(grade: str) -> str:
    """Map quality grade to provenance source type."""
    return GRADE_TO_SOURCE_TYPE.get(grade, "proposed_by_llm")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill provenance records from case trajectories")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    args = parser.parse_args()

    print(f"Loading case trajectories from {args.input}...")
    trajectories = load_trajectories(args.input)
    print(f"  {len(trajectories)} trajectories loaded")

    if not trajectories:
        print("No trajectories found. Run build_case_trajectories_from_legacy.py first.")
        sys.exit(0)

    # Import ProvenanceAdapter
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from waggledance.core.magma.provenance import ProvenanceAdapter
        adapter = ProvenanceAdapter()
    except ImportError:
        print("WARNING: ProvenanceAdapter not available")
        adapter = None

    created = 0
    skipped = 0

    for traj in trajectories:
        grade = traj.get("quality_grade", "bronze")
        source_type = grade_to_source_type(grade)
        traj_id = traj.get("trajectory_id", "")
        if not traj_id:
            skipped += 1
            continue

        # Extract capability IDs used
        caps = traj.get("selected_capabilities", [])
        capability_ids = [c.get("capability_id", "") for c in caps if c.get("capability_id")]
        primary_cap = capability_ids[0] if capability_ids else ""

        # Derive confidence from grade
        confidence_map = {"gold": 0.95, "silver": 0.80, "bronze": 0.50, "quarantine": 0.20}
        confidence = confidence_map.get(grade, 0.50)

        goal = traj.get("goal", {})
        fact_id = f"traj:{traj_id}"

        if adapter:
            try:
                adapter.record_provenance(
                    fact_id=fact_id,
                    source_type=source_type,
                    capability_id=primary_cap,
                    quality_grade=grade,
                    confidence=confidence,
                    metadata={
                        "trajectory_id": traj_id,
                        "goal_type": goal.get("type", ""),
                        "capability_chain": capability_ids,
                    },
                )
            except Exception as exc:
                print(f"  WARNING: Failed to record {fact_id}: {exc}")
                skipped += 1
                continue

        created += 1

    print(f"\nDone: {created} provenance records created, {skipped} skipped")


if __name__ == "__main__":
    main()
