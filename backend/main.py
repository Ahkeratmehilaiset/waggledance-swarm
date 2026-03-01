"""
WaggleDance Dashboard — Standalone FastAPI Backend Stub
Serves /api/status, /api/hardware, /api/heartbeat, /api/chat
Works independently of HiveMind for dashboard development.
"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes.status import router as status_router
from routes.heartbeat import router as heartbeat_router
from routes.chat import router as chat_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("waggledance-backend")

app = FastAPI(title="WaggleDance Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(status_router)
app.include_router(heartbeat_router)
app.include_router(chat_router)


@app.on_event("startup")
async def startup():
    log.info("WaggleDance backend stub starting...")
    # Detect hardware at startup
    try:
        import psutil
        cpu_count = psutil.cpu_count()
        ram_gb = round(psutil.virtual_memory().total / (1024**3), 1)
        log.info(f"Hardware: {cpu_count} CPU cores, {ram_gb} GB RAM")
    except ImportError:
        log.warning("psutil not installed — hardware stats unavailable")
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            log.info(f"GPU: {result.stdout.strip()}")
        else:
            log.info("GPU: not detected")
    except (FileNotFoundError, Exception):
        log.info("GPU: nvidia-smi not available")
    log.info("Backend stub ready on http://localhost:8000")
