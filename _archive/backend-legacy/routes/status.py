"""GET /api/status and GET /api/hardware — system metrics."""
import json
import subprocess
from collections import deque
from pathlib import Path
from fastapi import APIRouter

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

router = APIRouter()


def _gpu_util():
    """Get GPU utilization % via nvidia-smi, 0 on failure."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2
        )
        if r.returncode == 0:
            vals = [int(x.strip()) for x in r.stdout.strip().split("\n") if x.strip()]
            return vals[0] if vals else 0
    except (FileNotFoundError, Exception):
        pass
    return 0


def _vram_used_gb():
    """Get VRAM used in GB via nvidia-smi, 0 on failure."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2
        )
        if r.returncode == 0:
            vals = [int(x.strip()) for x in r.stdout.strip().split("\n") if x.strip()]
            return round(vals[0] / 1024, 1) if vals else 0
    except (FileNotFoundError, Exception):
        pass
    return 0


def _gpu_name():
    """Get GPU name via nvidia-smi."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=2
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (FileNotFoundError, Exception):
        pass
    return "N/A"


def _count_agents():
    """Count agent directories that have a core.yaml."""
    try:
        agents_dir = _PROJECT_ROOT / "agents"
        if agents_dir.is_dir():
            return sum(1 for d in agents_dir.iterdir()
                       if d.is_dir() and (d / "core.yaml").exists())
    except Exception:
        pass
    return 0


def _read_metrics():
    """Read learning_metrics.jsonl → compute summary stats."""
    metrics_path = _PROJECT_ROOT / "data" / "learning_metrics.jsonl"
    if not metrics_path.exists():
        return {}
    try:
        rows = deque(maxlen=500)
        with open(metrics_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        if not rows:
            return {}
        chat_rows = [r for r in rows if r.get("event") == "chat"]
        total = len(chat_rows)
        if total == 0:
            return {}
        cache_hits = sum(1 for r in chat_rows if r.get("cache_hit"))
        avg_ms = round(sum(r.get("response_time_ms", 0) for r in chat_rows) / total)
        halluc = round(sum(1 for r in chat_rows if r.get("was_hallucination")) / total, 3)
        return {
            "total_queries": total,
            "cache_hit_rate": round(cache_hits / total, 3),
            "avg_response_ms": avg_ms,
            "hallucination_rate": halluc,
        }
    except Exception:
        return {}


def _read_weekly_report():
    """Read data/weekly_report.json if present."""
    report_path = _PROJECT_ROOT / "data" / "weekly_report.json"
    if not report_path.exists():
        return {}
    try:
        with open(report_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _chromadb_count():
    """Try to get ChromaDB fact count, fallback to placeholder."""
    try:
        import sys
        sys.path.insert(0, str(_PROJECT_ROOT))
        import chromadb
        client = chromadb.PersistentClient(path=str(_PROJECT_ROOT / "data" / "chroma_db"))
        col = client.get_collection("bee_knowledge")
        return col.count()
    except Exception:
        return 0


@router.get("/api/status")
async def status():
    cpu = 0
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.1)
    except ImportError:
        pass

    gpu = _gpu_util()
    vram = _vram_used_gb()
    facts = _chromadb_count()
    metrics = _read_metrics()
    weekly = _read_weekly_report()

    return {
        "mode": "production",
        "facts": facts,
        "cpu": round(cpu),
        "gpu": gpu,
        "vram": vram,
        "agents_active": _count_agents(),
        "is_thinking": False,
        **metrics,
        "weekly_report": weekly or None,
    }


@router.get("/api/hardware")
async def hardware():
    gpu_name = _gpu_name()
    cpu_model = "N/A"
    ram_total = 0

    try:
        import psutil
        ram_total = round(psutil.virtual_memory().total / (1024**3), 1)
    except ImportError:
        pass

    try:
        import platform
        cpu_model = platform.processor() or "N/A"
    except Exception:
        pass

    return {
        "gpu_name": gpu_name,
        "cpu_model": cpu_model,
        "ram_total_gb": ram_total,
        "cpu": 0,
        "gpu": _gpu_util(),
        "vram": _vram_used_gb(),
    }
