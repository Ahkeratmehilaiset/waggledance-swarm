"""MAGMA Layer 4 API endpoints for cross-agent memory sharing."""

import logging
import time

log = logging.getLogger("waggledance.routes.cross_agent")


def register_cross_agent_routes(app, hivemind):
    """Register cross-agent endpoints on the FastAPI app."""

    @app.get("/api/cross/channels")
    async def cross_channels():
        cr = getattr(hivemind, '_channel_registry', None)
        if not cr:
            return {"channels": {}}
        return {"channels": cr.list_all()}

    @app.get("/api/cross/channels/{name}/history")
    async def cross_channel_history(name: str):
        cr = getattr(hivemind, '_channel_registry', None)
        if not cr:
            return {"history": []}
        ch = cr.get(name)
        if not ch:
            return {"history": [], "error": "channel not found"}
        return {"history": ch.get_history(limit=50)}

    @app.get("/api/cross/provenance/{fact_id}")
    async def cross_provenance(fact_id: str):
        prov = getattr(hivemind, '_provenance', None)
        if not prov:
            return {"chain": {}}
        return {"chain": prov.get_provenance_chain(fact_id)}

    @app.get("/api/cross/agent/{agent_id}/contributions")
    async def cross_agent_contributions(agent_id: str):
        prov = getattr(hivemind, '_provenance', None)
        if not prov:
            return {"contributions": {}}
        c = prov.get_agent_contributions(agent_id)
        # Limit output size
        c["originated"] = c["originated"][-50:]
        c["validated"] = c["validated"][-50:]
        return {"contributions": c}

    @app.get("/api/cross/consensus")
    async def cross_consensus():
        prov = getattr(hivemind, '_provenance', None)
        if not prov:
            return {"facts": []}
        return {"facts": prov.get_validated_facts(min_validators=2)}
