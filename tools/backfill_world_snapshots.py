"""Build initial world snapshots from configuration data.

Phase 3 migration: Creates baseline world snapshots from existing
configs (profiles, capsules, seasonal rules, agent templates) and
stores them in the world store.

Usage:
    python tools/backfill_world_snapshots.py [--db data/world_store.db]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time

import yaml

CAPSULES_DIR = "configs/capsules"
AXIOMS_DIR = "configs/axioms"
SETTINGS_PATH = "configs/settings.yaml"
DEFAULT_DB = "data/world_store.db"


def load_yaml(path: str) -> dict:
    """Load a YAML file."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_entities_from_capsule(capsule: dict, profile: str) -> list:
    """Extract entities from a capsule config."""
    entities = []
    for source in capsule.get("data_sources", []):
        entities.append({
            "entity_id": f"{profile}.{source['id']}",
            "entity_type": source.get("type", "sensor"),
            "attributes": {
                "source_type": source.get("type", ""),
                "unit": source.get("unit", ""),
                "profile": profile,
            },
        })
    for decision in capsule.get("key_decisions", []):
        entities.append({
            "entity_id": f"{profile}.decision.{decision['id']}",
            "entity_type": "decision",
            "attributes": {
                "primary_layer": decision.get("primary_layer", ""),
                "model": decision.get("model", ""),
                "profile": profile,
            },
        })
    return entities


def build_baselines_from_axioms(axioms_dir: str, profile: str) -> dict:
    """Extract baseline values from axiom YAML files.

    Axiom YAMLs use 'model_id' for the model identifier and 'variables'
    as a dict mapping variable_name → {default, unit, range, ...}.
    """
    baselines = {}
    profile_dir = os.path.join(axioms_dir, profile)
    if not os.path.isdir(profile_dir):
        return baselines

    for fname in sorted(os.listdir(profile_dir)):
        if not fname.endswith(".yaml"):
            continue
        axiom = load_yaml(os.path.join(profile_dir, fname))
        model_id = axiom.get("model_id", axiom.get("id", fname.replace(".yaml", "")))

        # Variables stored as dict: {var_name: {default: val, ...}}
        variables = axiom.get("variables", {})
        if isinstance(variables, dict):
            for var_name, var_info in variables.items():
                if not isinstance(var_info, dict):
                    continue
                default = var_info.get("default")
                if default is not None and isinstance(default, (int, float)):
                    key = f"{profile}.{model_id}.{var_name}"
                    baselines[key] = float(default)
        # Legacy format: parameters as list of dicts
        elif isinstance(variables, list):
            for param in variables:
                default = param.get("default")
                if default is not None and isinstance(default, (int, float)):
                    key = f"{profile}.{model_id}.{param.get('name', 'unknown')}"
                    baselines[key] = float(default)

        # Also handle older format with "parameters" key
        for param in axiom.get("parameters", []):
            if not isinstance(param, dict):
                continue
            default = param.get("default")
            if default is not None and isinstance(default, (int, float)):
                key = f"{profile}.{model_id}.{param.get('name', 'unknown')}"
                baselines[key] = float(default)

    return baselines


def create_world_store(db_path: str):
    """Create world store tables if they don't exist."""
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS baselines (
            entity_id       TEXT NOT NULL,
            metric_name     TEXT NOT NULL,
            baseline_value  REAL NOT NULL,
            confidence      REAL NOT NULL DEFAULT 0.5,
            sample_count    INTEGER NOT NULL DEFAULT 1,
            last_updated    REAL NOT NULL,
            source_type     TEXT NOT NULL DEFAULT 'observed',
            PRIMARY KEY (entity_id, metric_name)
        );
        CREATE TABLE IF NOT EXISTS entities (
            entity_id   TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            attributes  TEXT NOT NULL DEFAULT '{}',
            profile     TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS world_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            timestamp   REAL NOT NULL,
            profile     TEXT NOT NULL DEFAULT '',
            data        TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'observed'
        );
    """)
    conn.commit()
    return conn


def main():
    parser = argparse.ArgumentParser(
        description="Backfill world snapshots from config")
    parser.add_argument("--db", default=DEFAULT_DB)
    args = parser.parse_args()

    conn = create_world_store(args.db)
    now = time.time()

    total_entities = 0
    total_baselines = 0
    profiles_processed = []

    for fname in sorted(os.listdir(CAPSULES_DIR)):
        if not fname.endswith(".yaml"):
            continue
        profile = fname.replace(".yaml", "")
        capsule = load_yaml(os.path.join(CAPSULES_DIR, fname))

        print(f"\nProcessing profile: {profile}")
        profiles_processed.append(profile)

        # Entities
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
        print(f"  {len(entities)} entities")

        # Baselines
        baselines = build_baselines_from_axioms(AXIOMS_DIR, profile)
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
        print(f"  {len(baselines)} baselines")

        # Snapshot
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

    print(f"\nDone: {len(profiles_processed)} profiles, "
          f"{total_entities} entities, {total_baselines} baselines")
    print(f"Database: {args.db}")


if __name__ == "__main__":
    main()
