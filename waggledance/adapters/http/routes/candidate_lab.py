"""Candidate Lab API routes — /api/candidate_lab/*.

Exposes solver candidate lab status and recent candidates for observability.
All routes require authentication.
"""

import logging

from fastapi import APIRouter, Depends

from waggledance.adapters.http.deps import get_container, require_auth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["candidate_lab"])


@router.get("/api/candidate_lab/status")
def candidate_lab_status(container=Depends(get_container), _auth=Depends(require_auth)):
    """Return solver candidate lab status and registry stats."""
    try:
        lab = container.solver_candidate_lab
        return lab.status()
    except Exception as e:
        logger.debug("Candidate lab status error: %s", e)
        return {"total_analyses": 0, "llm_available": False, "registry": {"total": 0, "by_state": {}}}


@router.get("/api/candidate_lab/recent")
def candidate_lab_recent(
    limit: int = 10,
    container=Depends(get_container),
    _auth=Depends(require_auth),
):
    """Return recent solver candidates from the registry."""
    try:
        lab = container.solver_candidate_lab
        candidates = lab.registry.list_all()
        # Sort by created_at descending, take limit
        candidates.sort(key=lambda c: c.created_at, reverse=True)
        return {
            "candidates": [c.to_dict() for c in candidates[:limit]],
            "total": lab.registry.count(),
        }
    except Exception as e:
        logger.debug("Candidate lab recent error: %s", e)
        return {"candidates": [], "total": 0}


@router.get("/api/learning/accelerator")
def learning_accelerator_status(container=Depends(get_container), _auth=Depends(require_auth)):
    """Return synthetic training accelerator status."""
    try:
        acc = container.synthetic_accelerator
        return acc.status()
    except Exception as e:
        logger.debug("Accelerator status error: %s", e)
        return {
            "total_runs": 0,
            "total_real_rows": 0,
            "total_synthetic_rows": 0,
            "gpu_available": False,
            "gpu_enabled": False,
            "device_used": "cpu",
            "last_metrics": None,
        }
