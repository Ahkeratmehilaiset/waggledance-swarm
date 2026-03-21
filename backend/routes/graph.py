"""MAGMA Cognitive Graph API endpoints."""

import logging
from fastapi import Path

log = logging.getLogger("waggledance.routes.graph")


def register_graph_routes(app, hivemind):
    """Register cognitive graph endpoints on the FastAPI app."""

    @app.get("/api/graph/node/{node_id}")
    async def graph_node(node_id: str = Path(max_length=256)):
        cg = getattr(hivemind, '_cognitive_graph', None)
        if not cg:
            return {"node": None, "edges": []}
        node = cg.get_node(node_id)
        edges = cg.get_edges(node_id)
        return {"node": node, "edges": edges}

    @app.get("/api/graph/path/{source}/{target}")
    async def graph_path(source: str = Path(max_length=256), target: str = Path(max_length=256)):
        cg = getattr(hivemind, '_cognitive_graph', None)
        if not cg:
            return {"path": None}
        path = cg.shortest_path(source, target)
        return {"path": path}

    @app.get("/api/graph/stats")
    async def graph_stats():
        cg = getattr(hivemind, '_cognitive_graph', None)
        if not cg:
            return {"nodes": 0, "edges": 0, "edge_types": {}}
        return cg.stats()
