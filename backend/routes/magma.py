"""MAGMA Layer 3 API endpoints for audit, replay, and overlay inspection."""

import logging
import time

log = logging.getLogger("waggledance.routes.magma")


def register_magma_routes(app, hivemind):
    """Register MAGMA endpoints on the FastAPI app."""

    @app.get("/api/magma/stats")
    async def magma_stats():
        al = getattr(hivemind, '_audit_log', None)
        rs = getattr(hivemind, '_replay_store', None)
        reg = getattr(hivemind, '_overlay_registry', None)
        cg = getattr(hivemind, '_cognitive_graph', None)
        te = getattr(hivemind, '_trust_engine', None)
        result = {
            "audit_wired": al is not None,
            "audit_entries": al.count() if al else 0,
            "replay_wired": rs is not None,
            "overlays": len(reg._overlays) if reg else 0,
        }
        if cg:
            result["cognitive_graph"] = cg.stats()
        if te:
            result["trust_engine"] = {
                "agents_tracked": len(te._scores) if hasattr(te, '_scores') else 0,
            }
        return result

    @app.get("/api/magma/audit")
    async def magma_audit():
        al = getattr(hivemind, '_audit_log', None)
        if not al:
            return {"entries": [], "total": 0}
        now = time.time()
        entries = al.query_by_time_range(now - 86400, now)  # last 24h
        return {"entries": entries[-50:], "total": len(entries)}

    @app.get("/api/magma/audit/agent/{agent_id}")
    async def magma_audit_agent(agent_id: str):
        al = getattr(hivemind, '_audit_log', None)
        if not al:
            return {"entries": []}
        entries = al.query_by_agent(agent_id)
        return {"entries": entries[-50:]}

    @app.get("/api/magma/overlays")
    async def magma_overlays():
        reg = getattr(hivemind, '_overlay_registry', None)
        if not reg:
            return {"overlays": {}}
        return {"overlays": reg.list_all()}

    @app.get("/api/magma/branches")
    async def magma_branches():
        bm = getattr(hivemind, '_branch_manager', None)
        if not bm:
            return {"branches": {}}
        return {"branches": bm.list_all()}

    @app.post("/api/magma/branches/{name}/activate")
    async def magma_branch_activate(name: str):
        bm = getattr(hivemind, '_branch_manager', None)
        if not bm:
            return {"ok": False, "error": "BranchManager not wired"}
        ok = bm.activate(name)
        return {"ok": ok, "active": name if ok else None}

    @app.post("/api/magma/branches/deactivate")
    async def magma_branch_deactivate():
        bm = getattr(hivemind, '_branch_manager', None)
        if not bm:
            return {"ok": False}
        prev = bm.deactivate()
        return {"ok": True, "previous": prev}

    # ── ReplayEngine endpoints ──────────────────────────────

    @app.get("/api/magma/replay/manifest")
    async def magma_replay_manifest(agent_id: str = None):
        re = getattr(hivemind, '_replay_engine', None)
        if not re:
            return {"error": "ReplayEngine not wired"}
        return re.get_manifest(agent_id=agent_id)

    @app.get("/api/magma/replay/deduplicate")
    async def magma_replay_dedup():
        re = getattr(hivemind, '_replay_engine', None)
        if not re:
            return {"error": "ReplayEngine not wired"}
        groups = re.deduplicate()
        return {"duplicate_groups": len(groups),
                "groups": groups[:20]}

    # ── AgentRollback endpoints ─────────────────────────────

    @app.get("/api/magma/rollback/preview/{agent_id}")
    async def magma_rollback_preview(agent_id: str):
        ar = getattr(hivemind, '_agent_rollback', None)
        if not ar:
            return {"error": "AgentRollback not wired"}
        return ar.preview(agent_id)

    @app.post("/api/magma/rollback/{agent_id}")
    async def magma_rollback_execute(agent_id: str):
        ar = getattr(hivemind, '_agent_rollback', None)
        if not ar:
            return {"error": "AgentRollback not wired"}
        result = ar.rollback(agent_id)
        return {"ok": True, "result": result}
