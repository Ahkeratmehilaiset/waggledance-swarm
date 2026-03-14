#!/usr/bin/env python3
"""
Overnight Monitor — captures hourly snapshots for morning report.
Writes JSON lines to data/overnight_monitor.jsonl
"""
import json
import time
import sqlite3
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).parent.parent
DATA = PROJECT / "data"
REPORT_FILE = DATA / "overnight_monitor.jsonl"
API_KEY = ""

# Try to read API key from .env
env_path = PROJECT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.startswith("WAGGLE_API_KEY="):
            API_KEY = line.split("=", 1)[1].strip()
            break

BASE_URL = "http://localhost:8000"


def api_get(path: str) -> dict:
    """GET from local API with auth."""
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def get_chromadb_count() -> int:
    """Direct SQLite count of ChromaDB embeddings."""
    db = DATA / "chroma_db" / "chroma.sqlite3"
    if not db.exists():
        return -1
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM embeddings")
        n = c.fetchone()[0]
        conn.close()
        return n
    except Exception:
        return -1


def get_coggraph_stats() -> dict:
    """Read cognitive graph stats."""
    cg = DATA / "cognitive_graph.json"
    if not cg.exists():
        return {"nodes": 0, "edges": 0}
    try:
        with open(cg) as f:
            d = json.load(f)
        return {
            "nodes": len(d.get("nodes", [])),
            "edges": len(d.get("links", d.get("edges", [])))
        }
    except Exception:
        return {"nodes": -1, "edges": -1}


def get_audit_stats() -> dict:
    """Read MAGMA audit stats."""
    db = DATA / "audit_log.db"
    if not db.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        c = conn.cursor()
        result = {}
        for table in ["audit", "validations", "consensus", "trust_signals"]:
            try:
                c.execute(f"SELECT COUNT(*) FROM [{table}]")
                result[table] = c.fetchone()[0]
            except Exception:
                pass
        conn.close()
        return result
    except Exception:
        return {}


def get_disk_free_gb() -> float:
    """Get free disk space in GB."""
    import shutil
    total, used, free = shutil.disk_usage(str(PROJECT))
    return round(free / (1024**3), 2)


def get_learning_metrics_count() -> int:
    """Count lines in learning_metrics.jsonl."""
    f = DATA / "learning_metrics.jsonl"
    if not f.exists():
        return 0
    try:
        with open(f) as fh:
            return sum(1 for _ in fh)
    except Exception:
        return -1


def get_waggle_db_stats() -> dict:
    """Read waggle_dance.db stats."""
    db = DATA / "waggle_dance.db"
    if not db.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        c = conn.cursor()
        result = {}
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in c.fetchall()]
        for t in tables[:10]:
            try:
                c.execute(f"SELECT COUNT(*) FROM [{t}]")
                result[t] = c.fetchone()[0]
            except Exception:
                pass
        conn.close()
        return result
    except Exception:
        return {}


def capture_snapshot() -> dict:
    """Capture a full monitoring snapshot."""
    ts = datetime.now(timezone.utc).isoformat()

    # API status
    status = api_get("/api/status")
    faiss = api_get("/api/faiss/stats")

    # Direct DB reads
    chromadb_count = get_chromadb_count()
    cg = get_coggraph_stats()
    audit = get_audit_stats()
    disk_gb = get_disk_free_gb()
    metrics_lines = get_learning_metrics_count()
    waggle_db = get_waggle_db_stats()

    # Extract key fields from status
    snapshot = {
        "timestamp": ts,
        "server_status": status.get("status", "unknown"),
        "uptime": status.get("uptime", "?"),
        "heartbeat_count": status.get("heartbeat_count", -1),
        "agents_total": status.get("agents", {}).get("total", -1),
        "agents_active": status.get("agents", {}).get("active", -1),
        "night_mode_active": status.get("night_mode", {}).get("active", False),
        "night_facts_learned": status.get("night_mode", {}).get("facts_learned", 0),
        "night_active_source": status.get("night_mode", {}).get("active_source", ""),
        "chromadb_embeddings": chromadb_count,
        "faiss_total_vectors": faiss.get("total_vectors", -1),
        "faiss_collections": {c["name"]: c["count"] for c in faiss.get("collections", [])},
        "cognitive_graph": cg,
        "magma_audit": audit,
        "disk_free_gb": disk_gb,
        "learning_metrics_lines": metrics_lines,
        "waggle_db": waggle_db,
    }

    # Check for errors in status response
    if "error" in status:
        snapshot["api_error"] = status["error"]

    return snapshot


def write_snapshot(snapshot: dict):
    """Append snapshot to JSONL file."""
    REPORT_FILE.parent.mkdir(exist_ok=True)
    with open(REPORT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")


def run_once():
    """Capture and save one snapshot."""
    snap = capture_snapshot()
    write_snapshot(snap)
    ts = snap["timestamp"][:19]
    hb = snap["heartbeat_count"]
    chroma = snap["chromadb_embeddings"]
    faiss = snap["faiss_total_vectors"]
    night = "YES" if snap["night_mode_active"] else "no"
    nfacts = snap["night_facts_learned"]
    disk = snap["disk_free_gb"]
    cg_n = snap["cognitive_graph"]["nodes"]
    print(f"[{ts}] HB={hb} ChromaDB={chroma} FAISS={faiss} "
          f"Night={night}(+{nfacts}) CogGraph={cg_n}n Disk={disk}GB",
          flush=True)
    return snap


def run_loop(interval_seconds=3600, max_hours=12):
    """Run monitoring loop."""
    print(f"=== Overnight Monitor started (interval={interval_seconds}s, max={max_hours}h) ===",
          flush=True)
    checks = 0
    max_checks = max_hours * 3600 // interval_seconds
    while checks < max_checks:
        try:
            run_once()
        except Exception as e:
            print(f"[ERROR] Snapshot failed: {e}", flush=True)
            error_snap = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(e)
            }
            write_snapshot(error_snap)
        checks += 1
        if checks < max_checks:
            time.sleep(interval_seconds)
    print(f"=== Overnight Monitor done ({checks} checks) ===", flush=True)


def generate_morning_report() -> str:
    """Generate a summary report from all snapshots."""
    if not REPORT_FILE.exists():
        return "No monitoring data found."

    snapshots = []
    with open(REPORT_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    snapshots.append(json.loads(line))
                except Exception:
                    pass

    if not snapshots:
        return "No valid snapshots found."

    first = snapshots[0]
    last = snapshots[-1]

    lines = []
    lines.append("=" * 60)
    lines.append("  OVERNIGHT MONITORING REPORT")
    lines.append(f"  Period: {first['timestamp'][:19]} -> {last['timestamp'][:19]}")
    lines.append(f"  Snapshots: {len(snapshots)}")
    lines.append("=" * 60)

    # Server status
    statuses = [s.get("server_status", "?") for s in snapshots]
    errors = [s for s in snapshots if s.get("api_error")]
    lines.append(f"\n  Server: {'ALL OK' if all(s == 'running' for s in statuses) else 'ISSUES DETECTED'}")
    if errors:
        lines.append(f"  API Errors: {len(errors)}")
        for e in errors[:3]:
            lines.append(f"    - {e['timestamp'][:19]}: {e['api_error'][:80]}")

    # Heartbeat
    hb_first = first.get("heartbeat_count", 0)
    hb_last = last.get("heartbeat_count", 0)
    lines.append(f"\n  Heartbeats: {hb_first} ->{hb_last} (+{hb_last - hb_first})")

    # ChromaDB
    ch_first = first.get("chromadb_embeddings", 0)
    ch_last = last.get("chromadb_embeddings", 0)
    delta = ch_last - ch_first if ch_first > 0 and ch_last > 0 else "?"
    lines.append(f"  ChromaDB: {ch_first} ->{ch_last} (+{delta})")

    # FAISS
    fa_first = first.get("faiss_total_vectors", 0)
    fa_last = last.get("faiss_total_vectors", 0)
    lines.append(f"  FAISS: {fa_first} ->{fa_last} (+{fa_last - fa_first})")

    # Night mode
    night_snaps = [s for s in snapshots if s.get("night_mode_active")]
    max_facts = max((s.get("night_facts_learned", 0) for s in snapshots), default=0)
    lines.append(f"\n  Night Mode: active in {len(night_snaps)}/{len(snapshots)} snapshots")
    lines.append(f"  Night Facts Learned: {max_facts}")
    sources = set(s.get("night_active_source", "") for s in snapshots if s.get("night_active_source"))
    if sources:
        lines.append(f"  Night Sources Used: {', '.join(sources)}")

    # Cognitive Graph
    cg_first = first.get("cognitive_graph", {})
    cg_last = last.get("cognitive_graph", {})
    lines.append(f"\n  CogGraph: {cg_first.get('nodes', '?')}n/{cg_first.get('edges', '?')}e ->"
                 f"{cg_last.get('nodes', '?')}n/{cg_last.get('edges', '?')}e")

    # MAGMA Audit
    aud_first = first.get("magma_audit", {}).get("audit", 0)
    aud_last = last.get("magma_audit", {}).get("audit", 0)
    lines.append(f"  MAGMA Audit: {aud_first} ->{aud_last} (+{aud_last - aud_first})")

    # Disk
    disk_first = first.get("disk_free_gb", 0)
    disk_last = last.get("disk_free_gb", 0)
    lines.append(f"\n  Disk: {disk_first} GB ->{disk_last} GB (+{round(disk_last - disk_first, 2)} GB)")

    # Learning metrics
    lm_first = first.get("learning_metrics_lines", 0)
    lm_last = last.get("learning_metrics_lines", 0)
    lines.append(f"  Learning Metrics: {lm_first} ->{lm_last} (+{lm_last - lm_first} lines)")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--report":
        print(generate_morning_report())
    elif len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
    else:
        # Default: hourly checks for 12 hours
        run_loop(interval_seconds=3600, max_hours=12)
