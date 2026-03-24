"""Cognitive graph introspection endpoints — hexagonal runtime backed.

Exposes node, path, and stats queries through the CognitiveGraph
attached to AutonomyRuntime.world_model.
"""

import logging

from fastapi import APIRouter, Depends

from waggledance.adapters.http.deps import get_autonomy_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/graph", tags=["graph"])


def _graph(svc):
    """Extract the CognitiveGraph from AutonomyService → runtime → world_model."""
    rt = getattr(svc, "_runtime", None)
    if rt is None:
        return None
    wm = getattr(rt, "world_model", None)
    if wm is None:
        return None
    return getattr(wm, "graph", None)


@router.get("/node/{node_id}")
async def graph_node(node_id: str, svc=Depends(get_autonomy_service)):
    cg = _graph(svc)
    if not cg:
        return {"node": None, "edges": []}
    node = cg.get_node(node_id)
    edges = cg.get_edges(node_id)
    return {"node": node, "edges": edges}


@router.get("/path/{source}/{target}")
async def graph_path(source: str, target: str,
                     svc=Depends(get_autonomy_service)):
    cg = _graph(svc)
    if not cg:
        return {"path": None}
    path = cg.shortest_path(source, target)
    return {"path": path}


@router.get("/stats")
async def graph_stats(svc=Depends(get_autonomy_service)):
    cg = _graph(svc)
    if not cg:
        return {"nodes": 0, "edges": 0, "edge_types": {}}
    return cg.stats()
