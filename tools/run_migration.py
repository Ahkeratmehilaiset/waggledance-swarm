"""Migration orchestrator — runs all migration steps in correct order.

Sequences the Full Autonomy v3 migration pipeline:
  Phase 1a: Build alias registry SQLite DB
  Phase 1b: Backfill canonical_id in SQL tables
  Phase 1c: Reindex ChromaDB with canonical_id metadata
  Phase 3:  Build initial world snapshots from config
  Phase 7a: Build case trajectories from legacy data
  Phase 7b: Backfill procedural memory from trajectories
  Phase 7c: Backfill provenance records from trajectories

Usage:
    python tools/run_migration.py [--dry-run] [--skip-chroma] [--from-phase PHASE]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

log = logging.getLogger("migration.orchestrator")


def run_phase(name: str, fn, dry_run: bool = False) -> dict:
    """Run a single migration phase with timing and error handling."""
    log.info("=" * 60)
    log.info("PHASE: %s", name)
    log.info("=" * 60)
    t0 = time.monotonic()
    try:
        result = fn(dry_run=dry_run)
        elapsed = time.monotonic() - t0
        log.info("  ✓ %s completed in %.1fs", name, elapsed)
        return {"status": "ok", "elapsed_s": round(elapsed, 1), "result": result}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        log.error("  ✗ %s failed after %.1fs: %s", name, elapsed, exc)
        return {"status": "error", "elapsed_s": round(elapsed, 1), "error": str(exc)}


def phase_1a_alias_registry(dry_run: bool = False) -> dict:
    """Build alias registry SQLite DB from YAML."""
    from tools.alias_registry_builder import load_registry, build_db

    yaml_path = "configs/alias_registry.yaml"
    db_path = "data/alias_registry.db"

    if not os.path.exists(yaml_path):
        return {"status": "skipped", "reason": "alias_registry.yaml not found"}

    registry = load_registry(yaml_path)
    if dry_run:
        return {"status": "dry_run", "agents": len(registry)}

    stats = build_db(registry, db_path)
    return stats


def phase_1b_canonical_ids(dry_run: bool = False) -> dict:
    """Backfill canonical_id in SQL tables."""
    from tools.backfill_canonical_ids import load_alias_map, backfill_table

    alias_db = "data/alias_registry.db"
    alias_map = load_alias_map(alias_db)
    if not alias_map:
        return {"status": "skipped", "reason": "no alias mappings"}

    targets = [
        ("data/trust_store.db", "trust_signals", "agent_id"),
        ("data/audit_log.db", "audit_entries", "agent_id"),
    ]

    results = {}
    for db_path, table, col in targets:
        result = backfill_table(db_path, table, col, alias_map)
        results[f"{db_path}:{table}"] = result

    return results


def phase_1c_reindex_chroma(dry_run: bool = False) -> dict:
    """Reindex ChromaDB with canonical_id metadata."""
    try:
        from tools.reindex_chroma_with_canonical import (
            load_alias_map, reindex_collection, COLLECTIONS,
        )
    except ImportError:
        return {"status": "skipped", "reason": "reindex script import failed"}

    alias_db = "data/alias_registry.db"
    alias_map = load_alias_map(alias_db)
    if not alias_map:
        return {"status": "skipped", "reason": "no alias mappings"}

    try:
        import chromadb
        client = chromadb.PersistentClient(path="data/chroma_db")
    except ImportError:
        return {"status": "skipped", "reason": "chromadb not installed"}
    except Exception as exc:
        return {"status": "skipped", "reason": str(exc)}

    results = {}
    for coll_name in COLLECTIONS:
        if not dry_run:
            result = reindex_collection(client, coll_name, alias_map)
        else:
            result = {"collection": coll_name, "status": "dry_run"}
        results[coll_name] = result

    return results


def phase_3_world_snapshots(dry_run: bool = False) -> dict:
    """Build initial world snapshots from configuration."""
    from tools.backfill_world_snapshots import (
        load_yaml, build_entities_from_capsule,
        build_baselines_from_axioms, create_world_store,
    )
    import json

    capsules_dir = "configs/capsules"
    axioms_dir = "configs/axioms"
    db_path = "data/world_store.db"

    if not os.path.isdir(capsules_dir):
        return {"status": "skipped", "reason": "capsules dir not found"}

    if dry_run:
        profiles = [f.replace(".yaml", "") for f in sorted(os.listdir(capsules_dir))
                     if f.endswith(".yaml")]
        return {"status": "dry_run", "profiles": profiles}

    conn = create_world_store(db_path)
    now = time.time()
    total_entities = 0
    total_baselines = 0
    profiles = []

    import hashlib
    for fname in sorted(os.listdir(capsules_dir)):
        if not fname.endswith(".yaml"):
            continue
        profile = fname.replace(".yaml", "")
        capsule = load_yaml(os.path.join(capsules_dir, fname))
        profiles.append(profile)

        entities = build_entities_from_capsule(capsule, profile)
        for ent in entities:
            conn.execute(
                "INSERT OR REPLACE INTO entities "
                "(entity_id, entity_type, attributes, profile, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ent["entity_id"], ent["entity_type"],
                 json.dumps(ent["attributes"]), profile, now, now)
            )
            total_entities += 1

        baselines = build_baselines_from_axioms(axioms_dir, profile)
        for key, value in baselines.items():
            parts = key.split(".", 2)
            entity_id = ".".join(parts[:2]) if len(parts) >= 2 else key
            metric_name = parts[-1] if len(parts) >= 3 else key
            conn.execute(
                "INSERT OR REPLACE INTO baselines "
                "(entity_id, metric_name, baseline_value, confidence, "
                "sample_count, last_updated, source_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entity_id, metric_name, value, 0.5, 1, now, "inferred_by_rule")
            )
            total_baselines += 1

        snapshot_id = hashlib.sha256(
            f"init-{profile}-{now}".encode()
        ).hexdigest()[:16]
        snapshot_data = {
            "entities": {e["entity_id"]: e["attributes"] for e in entities},
            "baselines": baselines,
            "residuals": {},
        }
        conn.execute(
            "INSERT OR REPLACE INTO world_snapshots "
            "(snapshot_id, timestamp, profile, data, source_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (snapshot_id, now, profile, json.dumps(snapshot_data), "inferred_by_rule")
        )

    conn.commit()
    conn.close()
    return {
        "profiles": profiles, "entities": total_entities,
        "baselines": total_baselines,
    }


def phase_7a_case_trajectories(dry_run: bool = False) -> dict:
    """Build case trajectories from legacy Q&A data."""
    from tools.build_case_trajectories_from_legacy import (
        load_training_pairs, load_corrections, build_trajectory,
    )
    import json

    db_path = "data/waggle_dance.db"
    output = "data/case_trajectories.jsonl"

    pairs = load_training_pairs(db_path)
    if not pairs:
        return {"status": "skipped", "reason": "no training pairs"}

    corrections = load_corrections(db_path)
    if dry_run:
        return {"status": "dry_run", "pairs": len(pairs),
                "corrections": len(corrections)}

    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    counts = {"gold": 0, "silver": 0, "bronze": 0, "quarantine": 0}

    with open(output, "w", encoding="utf-8") as f:
        for i, pair in enumerate(pairs):
            trajectory = build_trajectory(pair, corrections, i)
            grade = trajectory["quality_grade"]
            counts[grade] = counts.get(grade, 0) + 1
            f.write(json.dumps(trajectory, ensure_ascii=False) + "\n")

    return {"total": sum(counts.values()), "grades": counts, "output": output}


def phase_7b_procedural_memory(dry_run: bool = False) -> dict:
    """Backfill procedural memory from case trajectories."""
    from tools.backfill_procedural_memory import (
        load_trajectories, extract_capability_chain, build_procedure_id,
    )

    input_path = "data/case_trajectories.jsonl"
    trajectories = load_trajectories(input_path)
    if not trajectories:
        return {"status": "skipped", "reason": "no trajectories"}

    if dry_run:
        return {"status": "dry_run", "trajectories": len(trajectories)}

    try:
        from waggledance.adapters.persistence.sqlite_procedural_store import SQLiteProceduralStore
        store = SQLiteProceduralStore(db_path="data/procedural_store.db")
    except ImportError:
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
                    procedure_id=proc_id, intent=intent,
                    capability_chain=chain, quality_grade=grade,
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
                    pattern_id=proc_id, intent=intent,
                    capability_chain=chain, failure_reason=reason,
                )
            anti_patterns += 1

    if store:
        store.close()
    return {"procedures": procedures, "anti_patterns": anti_patterns}


def phase_7c_provenance(dry_run: bool = False) -> dict:
    """Backfill provenance records from case trajectories."""
    from tools.backfill_provenance import (
        load_trajectories, grade_to_source_type,
    )

    input_path = "data/case_trajectories.jsonl"
    trajectories = load_trajectories(input_path)
    if not trajectories:
        return {"status": "skipped", "reason": "no trajectories"}

    if dry_run:
        return {"status": "dry_run", "trajectories": len(trajectories)}

    try:
        from waggledance.core.magma.provenance import ProvenanceAdapter
        adapter = ProvenanceAdapter()
    except ImportError:
        adapter = None

    created = 0
    skipped = 0
    confidence_map = {"gold": 0.95, "silver": 0.80, "bronze": 0.50, "quarantine": 0.20}

    for traj in trajectories:
        traj_id = traj.get("trajectory_id", "")
        if not traj_id:
            skipped += 1
            continue
        grade = traj.get("quality_grade", "bronze")
        source_type = grade_to_source_type(grade)
        confidence = confidence_map.get(grade, 0.50)
        caps = traj.get("selected_capabilities", [])
        primary_cap = caps[0].get("capability_id", "") if caps else ""
        goal = traj.get("goal", {})

        if adapter:
            try:
                adapter.record_provenance(
                    fact_id=f"traj:{traj_id}",
                    source_type=source_type,
                    capability_id=primary_cap,
                    quality_grade=grade,
                    confidence=confidence,
                    metadata={
                        "trajectory_id": traj_id,
                        "goal_type": goal.get("type", ""),
                    },
                )
            except Exception:
                skipped += 1
                continue
        created += 1

    return {"created": created, "skipped": skipped}


# Migration phase registry (ordered)
PHASES = [
    ("1a_alias_registry", phase_1a_alias_registry),
    ("1b_canonical_ids", phase_1b_canonical_ids),
    ("1c_reindex_chroma", phase_1c_reindex_chroma),
    ("3_world_snapshots", phase_3_world_snapshots),
    ("7a_case_trajectories", phase_7a_case_trajectories),
    ("7b_procedural_memory", phase_7b_procedural_memory),
    ("7c_provenance", phase_7c_provenance),
]


def run_all(dry_run: bool = False, skip_chroma: bool = False,
            from_phase: str = None) -> dict:
    """Run all migration phases in order.

    Args:
        dry_run: If True, show what would be done without writing.
        skip_chroma: If True, skip the ChromaDB reindex phase.
        from_phase: Start from this phase (e.g. "3" to start at world snapshots).

    Returns:
        Dict with results per phase.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    results = {}
    started = from_phase is None
    t0 = time.monotonic()

    log.info("=" * 60)
    log.info("WaggleDance Full Autonomy v3 — Migration Pipeline")
    log.info("Mode: %s", "DRY RUN" if dry_run else "LIVE")
    log.info("=" * 60)

    for name, fn in PHASES:
        # Handle --from-phase
        if not started:
            if from_phase in name:
                started = True
            else:
                log.info("Skipping %s (--from-phase=%s)", name, from_phase)
                continue

        # Handle --skip-chroma
        if skip_chroma and "chroma" in name:
            log.info("Skipping %s (--skip-chroma)", name)
            results[name] = {"status": "skipped"}
            continue

        result = run_phase(name, fn, dry_run=dry_run)
        results[name] = result

        if result["status"] == "error":
            log.error("Pipeline stopped at %s due to error", name)
            break

    elapsed = time.monotonic() - t0
    log.info("\n" + "=" * 60)
    log.info("Migration %s in %.1fs",
             "completed" if all(r.get("status") != "error" for r in results.values())
             else "FAILED",
             elapsed)
    log.info("=" * 60)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Run WaggleDance Full Autonomy v3 migration pipeline")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without writing")
    parser.add_argument("--skip-chroma", action="store_true",
                        help="Skip ChromaDB reindex phase")
    parser.add_argument("--from-phase", default=None,
                        help="Start from phase (e.g. '3', '7a')")
    args = parser.parse_args()
    run_all(dry_run=args.dry_run, skip_chroma=args.skip_chroma,
            from_phase=args.from_phase)


if __name__ == "__main__":
    main()
