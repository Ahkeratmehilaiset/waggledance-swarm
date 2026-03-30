"""Storage health introspection endpoints."""

from fastapi import APIRouter, Depends

from waggledance.adapters.http.deps import get_container
from waggledance.adapters.http.routes.auth_session import validate_session

router = APIRouter(prefix="/api/storage", tags=["storage"])


@router.get("/health")
async def storage_health(
    _session=Depends(validate_session),
    container=Depends(get_container),
):
    """Return per-database size, WAL status, row counts, and growth warnings."""
    svc = container.storage_health
    report = svc.check_health()
    return report.to_dict()


@router.post("/wal-checkpoint")
async def wal_checkpoint(
    _session=Depends(validate_session),
    container=Depends(get_container),
):
    """Trigger WAL checkpoint (TRUNCATE) on all databases."""
    svc = container.storage_health
    results = svc.wal_checkpoint(mode="TRUNCATE")
    return {"results": results}
