"""MAGMA Layer 5 API endpoints for trust & reputation."""

import logging

log = logging.getLogger("waggledance.routes.trust")


def register_trust_routes(app, hivemind):
    """Register trust engine endpoints on the FastAPI app."""

    @app.get("/api/trust/ranking")
    async def trust_ranking():
        te = getattr(hivemind, '_trust_engine', None)
        if not te:
            return {"ranking": []}
        return {"ranking": te.get_ranking()}

    @app.get("/api/trust/agent/{agent_id}")
    async def trust_agent(agent_id: str):
        te = getattr(hivemind, '_trust_engine', None)
        if not te:
            return {"reputation": {}}
        rep = te.compute_reputation(agent_id)
        return {"reputation": rep.to_dict()}

    @app.get("/api/trust/domain/{domain}")
    async def trust_domain(domain: str):
        te = getattr(hivemind, '_trust_engine', None)
        if not te:
            return {"experts": []}
        return {"experts": te.get_domain_experts(domain)}

    @app.get("/api/trust/signals/{agent_id}")
    async def trust_signals(agent_id: str):
        te = getattr(hivemind, '_trust_engine', None)
        if not te:
            return {"signals": []}
        return {"signals": te.get_signal_history(agent_id)}
