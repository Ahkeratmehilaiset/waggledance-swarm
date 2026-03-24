"""GET /api/models — Ollama model status (stub returns fake data)."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/models")
async def models_status():
    """Stub: return realistic fake model data for dashboard development."""
    return {
        "models": [
            {
                "name": "phi4-mini",
                "role": "chat",
                "loaded": True,
                "size_gb": 2.3,
                "vram_mb": 2400,
            },
            {
                "name": "llama3.2:1b",
                "role": "background_learning",
                "loaded": True,
                "size_gb": 0.7,
                "vram_mb": 800,
            },
            {
                "name": "nomic-embed-text",
                "role": "embedding",
                "loaded": True,
                "size_gb": 0.3,
                "vram_mb": 350,
            },
            {
                "name": "all-minilm",
                "role": "evaluation",
                "loaded": False,
                "size_gb": 0.1,
                "vram_mb": 0,
            },
        ],
        "vram_total_mb": 8192,
        "vram_used_mb": 3550,
        "vram_percent": 43.3,
        "ollama_available": True,
    }


@router.get("/api/profile")
async def get_profile():
    """Stub: return default profile."""
    return {
        "active_profile": "cottage",
        "profiles": ["gadget", "cottage", "home", "factory"],
    }


@router.post("/api/profile")
async def set_profile():
    """Stub: accept profile switch (no-op)."""
    return {"profile": "cottage", "message": "Stub mode — profile not persisted."}


@router.get("/api/history")
async def history_list():
    """Stub: return empty history."""
    return {"conversations": []}


@router.get("/api/history/recent/messages")
async def history_recent():
    """Stub: return empty recent messages."""
    return {"messages": []}


@router.post("/api/feedback")
async def feedback():
    """Stub: accept feedback (no-op)."""
    return {"status": "recorded", "feedback_id": 0}
