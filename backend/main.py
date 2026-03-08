"""
WaggleDance Dashboard — Standalone FastAPI Backend Stub
Serves /api/status, /api/hardware, /api/heartbeat, /api/chat, /api/sensors
Works independently of HiveMind for dashboard development.
"""
import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routes.status import router as status_router
from backend.routes.heartbeat import router as heartbeat_router
from backend.routes.chat import router as chat_router
from backend.routes.sensors import router as sensors_router
from backend.routes.voice import router as voice_router
from backend.routes.audio import router as audio_router
from backend.routes.code_review import router as code_review_router
from backend.routes.analytics import router as analytics_router
from backend.routes.round_table import router as round_table_router
from backend.routes.agents import router as agents_router
from backend.routes.settings import router as settings_router
from backend.routes.models import router as models_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("waggledance-backend")

app = FastAPI(title="WaggleDance Dashboard API")

# ── Auth middleware (Bearer token) ────────────────────────
from backend.auth import get_or_create_api_key, BearerAuthMiddleware

_api_key = get_or_create_api_key()
app.add_middleware(BearerAuthMiddleware, api_key=_api_key)

_cors_origins = os.environ.get(
    "CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(status_router)
app.include_router(heartbeat_router)
app.include_router(chat_router)
app.include_router(sensors_router)
app.include_router(voice_router)
app.include_router(audio_router)
app.include_router(code_review_router)
app.include_router(analytics_router)
app.include_router(round_table_router)
app.include_router(agents_router)
app.include_router(settings_router)
app.include_router(models_router)


@app.get("/api/auth/token")
async def auth_token():
    """Return API key for localhost dashboard auto-login."""
    return {"token": _api_key}


@app.get("/health")
async def health():
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/ready")
async def readiness():
    """Readiness probe — checks backend is serving."""
    return {"status": "ready", "backend": "online"}


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
