"""Hybrid retrieval API routes — /api/hybrid/*.

Exposes hybrid FAISS + hex-cell retrieval status, cell topology,
and retrieval metrics. Feature-flagged — returns minimal response
when hybrid is disabled.
"""

import logging

from fastapi import APIRouter, Depends, Request

from waggledance.adapters.http.deps import get_container, require_auth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hybrid"])


@router.get("/api/hybrid/status")
def hybrid_status(container=Depends(get_container), _auth=Depends(require_auth)):
    """Return hybrid retrieval status and metrics."""
    try:
        hr = container.hybrid_retrieval
        return {
            "enabled": hr.enabled,
            "retrieval_mode": "hybrid" if hr.enabled else "global_only",
            "ring2_enabled": hr._ring2_enabled,
            "stats": hr.stats(),
        }
    except Exception as e:
        logger.debug("Hybrid status error: %s", e)
        return {"enabled": False, "retrieval_mode": "global_only", "stats": {}}


@router.get("/api/hybrid/topology")
def hybrid_topology(container=Depends(get_container), _auth=Depends(require_auth)):
    """Return hex-cell topology: all cells, their neighbors, and assignment stats."""
    try:
        from waggledance.core.hex_cell_topology import ALL_CELLS, _ADJACENCY
        topo = container.hex_cell_topology
        cells = {}
        for cell_id in ALL_CELLS:
            neighbors = sorted(_ADJACENCY.get(cell_id, set()))
            # Get FAISS collection size for this cell
            try:
                col = container.faiss_registry.get_or_create(f"cell_{cell_id}")
                doc_count = col.count
            except Exception:
                doc_count = 0
            cells[cell_id] = {
                "neighbors_ring1": neighbors,
                "documents": doc_count,
            }

        return {
            "cells": cells,
            "total_cells": len(ALL_CELLS),
            "assignment_stats": topo.stats(),
        }
    except Exception as e:
        logger.debug("Hybrid topology error: %s", e)
        return {"cells": {}, "total_cells": 0, "assignment_stats": {}}


@router.get("/api/hybrid/cells")
def hybrid_cells(container=Depends(get_container), _auth=Depends(require_auth)):
    """Return per-cell FAISS collection sizes."""
    try:
        from waggledance.core.hex_cell_topology import ALL_CELLS
        registry = container.faiss_registry
        cells = {}
        for cell_id in ALL_CELLS:
            try:
                col = registry.get_or_create(f"cell_{cell_id}")
                cells[cell_id] = col.count
            except Exception:
                cells[cell_id] = 0
        return {"cells": cells, "total_documents": sum(cells.values())}
    except Exception as e:
        logger.debug("Hybrid cells error: %s", e)
        return {"cells": {}, "total_documents": 0}


@router.get("/api/hybrid/test-assign")
def hybrid_test_assign(
    query: str = "test",
    intent: str = "chat",
    container=Depends(get_container),
    _auth=Depends(require_auth),
):
    """Test cell assignment for a given query and intent. Debug endpoint."""
    try:
        topo = container.hex_cell_topology
        assignment = topo.assign_cell(intent, query)
        return {
            "cell_id": assignment.cell_id,
            "intent": assignment.intent,
            "method": assignment.method,
            "neighbors_ring1": assignment.neighbors_ring1,
            "neighbors_ring2": assignment.neighbors_ring2,
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/hybrid/backfill/status")
def backfill_status(container=Depends(get_container), _auth=Depends(require_auth)):
    """Return backfill service status."""
    try:
        bf = container.hybrid_backfill
        return bf.status()
    except Exception as e:
        logger.debug("Backfill status error: %s", e)
        return {"running": False, "total_runs": 0, "indexed_ids_count": 0, "last_result": None}


@router.post("/api/hybrid/backfill/run")
async def backfill_run(
    request: Request,
    container=Depends(get_container),
    _auth=Depends(require_auth),
):
    """Trigger a backfill run. Optional body: {"dry_run": true, "limit": 5000}."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    dry_run = body.get("dry_run", False)
    limit = body.get("limit", 5000)

    bf = container.hybrid_backfill
    if bf.is_running:
        return {"error": "Backfill already in progress", "running": True}

    result = await bf.run(dry_run=bool(dry_run), limit=int(limit))
    return result.to_dict()
