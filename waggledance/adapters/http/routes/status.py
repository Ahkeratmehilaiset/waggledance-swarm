"""Health and readiness HTTP routes."""

from dataclasses import asdict

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from waggledance.adapters.http.deps import get_readiness_service

router = APIRouter()


@router.get("/health")
async def health():
    """Simple liveness check -- always returns ok."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    svc=Depends(get_readiness_service),
):
    """Readiness check -- queries all components via ReadinessService."""
    status = await svc.check()
    code = 200 if status.ready else 503
    return JSONResponse(status_code=code, content=asdict(status))
