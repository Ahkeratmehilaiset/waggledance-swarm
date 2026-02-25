"""GET /api/status and GET /api/hardware — system metrics."""
import subprocess
from fastapi import APIRouter

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


def _chromadb_count():
    """Try to get ChromaDB fact count, fallback to placeholder."""
    try:
        import sys
        sys.path.insert(0, "U:/project")
        import chromadb
        client = chromadb.PersistentClient(path="U:/project/data/chroma_db")
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

    return {
        "facts": facts,
        "cpu": round(cpu),
        "gpu": gpu,
        "vram": vram,
        "agents_active": 6,
        "is_thinking": False,
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
