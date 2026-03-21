"""Continuous monitoring for autonomous session."""
import requests
import time
import json
import os
import sqlite3
import datetime

API = "http://localhost:8000"
KEY = os.environ.get("WAGGLE_API_KEY", "")
if not KEY:
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            if line.startswith("WAGGLE_API_KEY="):
                KEY = line.split("=", 1)[1].strip()
HEADERS = {"Authorization": f"Bearer {KEY}"}
LOG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "monitor_log.jsonl")


def check_health():
    try:
        r = requests.get(f"{API}/health", timeout=5)
        return r.json()
    except Exception:
        return {"status": "unreachable"}


def check_status():
    try:
        r = requests.get(f"{API}/api/status", timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def check_databases():
    result = {}
    base = os.path.dirname(os.path.dirname(__file__))
    for db in ["data/audit_log.db", "data/chat_history.db", "data/waggle_dance.db"]:
        full = os.path.join(base, db)
        if os.path.exists(full):
            try:
                conn = sqlite3.connect(full)
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                for t in tables:
                    if t[0].startswith("sqlite_"):
                        continue
                    count = conn.execute(
                        f"SELECT COUNT(*) FROM [{t[0]}]"
                    ).fetchone()[0]
                    result[f"{db}:{t[0]}"] = count
                conn.close()
            except Exception as e:
                result[db] = f"error: {e}"
        else:
            result[db] = "missing"
    return result


def check_chromadb():
    try:
        import chromadb
        base = os.path.dirname(os.path.dirname(__file__))
        client = chromadb.PersistentClient(
            path=os.path.join(base, "data", "chroma_db")
        )
        result = {}
        for c in client.list_collections():
            col = client.get_collection(c.name)
            result[c.name] = col.count()
        return result
    except Exception as e:
        return {"error": str(e)}


def check_file_sizes():
    result = {}
    base = os.path.dirname(os.path.dirname(__file__))
    for f in [
        "data/finetune_live.jsonl",
        "data/replay_store.jsonl",
        "data/learning_metrics.jsonl",
    ]:
        full = os.path.join(base, f)
        if os.path.exists(full):
            result[f] = {
                "size_mb": round(os.path.getsize(full) / 1024 / 1024, 2),
                "lines": sum(
                    1
                    for _ in open(full, encoding="utf-8", errors="ignore")
                ),
            }
    return result


def full_check():
    ts = datetime.datetime.now().isoformat()
    report = {
        "timestamp": ts,
        "health": check_health(),
        "status_summary": {},
        "databases": check_databases(),
        "chromadb": check_chromadb(),
        "files": check_file_sizes(),
    }
    # Extract key status fields
    status = check_status()
    for k in [
        "mode", "uptime", "heartbeat_count",
        "night_mode", "learning", "enrichment",
        "micro_model", "elastic_scaler", "magma",
        "consciousness",
    ]:
        if k in status:
            report["status_summary"][k] = status[k]

    # Append to JSONL log
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(report, default=str) + "\n")
    return report


if __name__ == "__main__":
    report = full_check()
    print(json.dumps(report, indent=2, default=str))
