#!/usr/bin/env python3
"""
PHASE1 TASK5: YAML Knowledge Scanner
=====================================
Parses ALL YAML files in knowledge/ and agents/ directories.
Extracts facts as natural language and stores via consciousness.learn().
NO LLM NEEDED — just parse YAML structure and format as text.

Usage:
  python tools/scan_knowledge.py              # Scan and store facts
  python tools/scan_knowledge.py --dry-run    # Print facts without storing
  python tools/scan_knowledge.py --count      # Just show fact count
"""

import sys
import os
import json
import time
import logging
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Windows UTF-8
if sys.platform == "win32":
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import yaml

log = logging.getLogger("scan_knowledge")

PROGRESS_FILE = PROJECT_ROOT / "data" / "scan_progress.json"


def _load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"processed_files": {}, "total_facts": 0}


def _save_progress(progress: dict):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def _fix_mojibake(s: str) -> str:
    """Fix double-encoded UTF-8 from Windows YAML generation."""
    if not s or not isinstance(s, str):
        return s or ""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return s


def _flatten_yaml(data, prefix="", agent_id="") -> list:
    """Recursively flatten YAML structure into natural language facts."""
    facts = []
    if isinstance(data, dict):
        for key, value in data.items():
            # Skip metadata keys
            if key in ("header", "version", "last_updated", "agent_id", "agent_name"):
                continue
            new_prefix = f"{prefix} {key}" if prefix else key
            if isinstance(value, dict):
                # Check if this is a metric entry with value/action/source
                if "value" in value:
                    fact_parts = [f"{_fix_mojibake(key)}"]
                    val = value.get("value")
                    unit = value.get("unit", "")
                    action = _fix_mojibake(str(value.get("action", "")))
                    source = value.get("source", "")
                    measurement = _fix_mojibake(str(value.get("measurement", "")))
                    season = _fix_mojibake(str(value.get("season", "")))

                    if val is not None:
                        fact = f"{_fix_mojibake(key)}: {_fix_mojibake(str(val))}"
                        if unit:
                            fact += f" {_fix_mojibake(str(unit))}"
                        if action:
                            fact += f". {action}"
                        if source:
                            fact += f" [{source}]"
                        facts.append(fact)

                    if measurement:
                        facts.append(f"{_fix_mojibake(key)} measurement: {measurement}")
                    if season:
                        facts.append(f"{_fix_mojibake(key)} season: {season}")
                else:
                    facts.extend(_flatten_yaml(value, new_prefix, agent_id))
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        # Seasonal rules, failure modes, etc.
                        if "season" in item and "rule" in item:
                            season = _fix_mojibake(str(item["season"]))
                            rule = _fix_mojibake(str(item["rule"]))
                            if agent_id:
                                facts.append(f"[{agent_id}] {season}: {rule}")
                            else:
                                facts.append(f"{season}: {rule}")
                        elif "mode" in item or "failure" in item or "detection" in item:
                            parts = []
                            for k, v in item.items():
                                parts.append(f"{k}: {_fix_mojibake(str(v))}")
                            facts.append(f"[{agent_id}] " + ", ".join(parts))
                        elif "question" in item or "q" in item:
                            q = _fix_mojibake(str(item.get("question") or item.get("q", "")))
                            a = _fix_mojibake(str(item.get("answer") or item.get("a", "")))
                            if q and a:
                                facts.append(f"Q: {q} A: {a}")
                            elif q:
                                facts.append(f"Q: {q}")
                        else:
                            facts.extend(_flatten_yaml(item, new_prefix, agent_id))
                    elif isinstance(item, str):
                        item_fixed = _fix_mojibake(item)
                        if len(item_fixed) > 15:
                            if agent_id:
                                facts.append(f"[{agent_id}] {_fix_mojibake(key)}: {item_fixed}")
                            else:
                                facts.append(f"{_fix_mojibake(key)}: {item_fixed}")
            elif isinstance(value, str) and len(value) > 10:
                val_fixed = _fix_mojibake(value)
                key_fixed = _fix_mojibake(key)
                if agent_id:
                    facts.append(f"[{agent_id}] {key_fixed}: {val_fixed}")
                else:
                    facts.append(f"{key_fixed}: {val_fixed}")
            elif isinstance(value, (int, float)):
                key_fixed = _fix_mojibake(key)
                if agent_id:
                    facts.append(f"[{agent_id}] {key_fixed}: {value}")
                else:
                    facts.append(f"{key_fixed}: {value}")
    return facts


def extract_facts_from_file(yaml_path: Path) -> list:
    """Extract facts from a single YAML file."""
    try:
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        log.warning(f"Cannot parse {yaml_path}: {e}")
        return []

    if not data or not isinstance(data, dict):
        return []

    # Determine agent_id from path
    agent_id = yaml_path.parent.name

    facts = _flatten_yaml(data, agent_id=agent_id)

    # Filter: remove very short or empty facts
    facts = [f.strip() for f in facts if f and len(f.strip()) >= 15]

    return facts


def find_yaml_files() -> list:
    """Find all YAML files in knowledge/ and agents/ directories."""
    dirs = [PROJECT_ROOT / "knowledge", PROJECT_ROOT / "agents"]
    yaml_files = []
    for d in dirs:
        if d.exists():
            yaml_files.extend(d.rglob("*.yaml"))
    # Deduplicate by filename (knowledge/ mirrors agents/)
    seen = set()
    unique = []
    for f in sorted(yaml_files):
        key = f"{f.parent.name}/{f.name}"
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def scan_all(consciousness=None, dry_run=False, count_only=False) -> int:
    """Scan all YAML files and store facts.

    Args:
        consciousness: Consciousness instance (None for dry-run/count)
        dry_run: Print facts without storing
        count_only: Just return count

    Returns:
        Number of facts extracted/stored
    """
    yaml_files = find_yaml_files()
    progress = _load_progress()
    all_facts = []
    new_files = 0

    for yaml_path in yaml_files:
        file_key = str(yaml_path.relative_to(PROJECT_ROOT))
        file_mtime = str(yaml_path.stat().st_mtime)

        # Skip already-processed files (idempotent)
        if (file_key in progress["processed_files"]
                and progress["processed_files"][file_key] == file_mtime):
            continue

        facts = extract_facts_from_file(yaml_path)
        if facts:
            all_facts.extend(facts)
            new_files += 1
            progress["processed_files"][file_key] = file_mtime

    if count_only:
        print(f"YAML files found: {len(yaml_files)}")
        print(f"New/modified files: {new_files}")
        print(f"Facts to extract: {len(all_facts)}")
        return len(all_facts)

    if dry_run:
        print(f"=== DRY RUN: {len(all_facts)} facts from {new_files} files ===\n")
        for i, fact in enumerate(all_facts[:50], 1):
            print(f"  {i:3d}. {fact[:120]}")
        if len(all_facts) > 50:
            print(f"  ... and {len(all_facts) - 50} more facts")
        return len(all_facts)

    # Store facts via consciousness.learn()
    if not consciousness:
        print("ERROR: No consciousness instance provided for storing facts")
        return 0

    stored = 0
    total = len(all_facts)
    t0 = time.time()
    for i, fact in enumerate(all_facts, 1):
        try:
            ok = consciousness.learn(
                fact,
                agent_id="yaml_scanner",
                source_type="yaml_seed",
                confidence=0.90,
                validated=True,
            )
            if ok:
                stored += 1
        except Exception as e:
            log.warning(f"Failed to store fact: {e}")
        if i % 200 == 0:
            print(f"  🌱 Seeding: {i}/{total} facts ({stored} stored)...", flush=True)

    # PHASE2: Flush remaining queued items
    if hasattr(consciousness, 'flush'):
        flushed = consciousness.flush()
        if flushed:
            stored += flushed

    elapsed = time.time() - t0
    progress["total_facts"] = progress.get("total_facts", 0) + stored
    _save_progress(progress)

    print(f"Scanned {new_files} files, extracted {len(all_facts)} facts, "
          f"stored {stored} new (dedup filtered {len(all_facts) - stored}), "
          f"took {elapsed:.1f}s")
    return stored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="  %(message)s")

    dry_run = "--dry-run" in sys.argv
    count_only = "--count" in sys.argv

    if count_only:
        scan_all(count_only=True)
    elif dry_run:
        scan_all(dry_run=True)
    else:
        from consciousness import Consciousness
        c = Consciousness(db_path="data/chroma_db")
        count = scan_all(c)
        print(f"\nTotal facts in memory: {c.memory.count}")
