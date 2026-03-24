"""MAGMA introspection endpoints — hexagonal runtime backed.

Exposes audit, replay, overlay, branch, and rollback inspection
through the AutonomyRuntime's MAGMA adapters.
"""

import logging

from fastapi import APIRouter, Depends

from waggledance.adapters.http.deps import get_autonomy_service, require_auth

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/magma", tags=["magma"])


def _runtime(svc):
    """Extract the AutonomyRuntime from AutonomyService."""
    return getattr(svc, "_runtime", None)


def _entry_list(entries):
    """Serialize a list of AuditEntry (or dicts) to JSON-safe dicts."""
    out = []
    for e in entries:
        if hasattr(e, "to_dict"):
            out.append(e.to_dict())
        elif isinstance(e, dict):
            out.append(e)
    return out


@router.get("/stats")
async def magma_stats(svc=Depends(get_autonomy_service)):
    rt = _runtime(svc)
    if rt is None:
        return {"error": "runtime not available"}

    result = {}

    audit = getattr(rt, "audit", None)
    result["audit_wired"] = audit is not None
    if audit:
        result["audit_stats"] = audit.stats()

    replay = getattr(rt, "replay", None)
    result["replay_wired"] = replay is not None
    if replay:
        result["replay_stats"] = replay.stats()

    trust = getattr(rt, "trust", None)
    result["trust_wired"] = trust is not None
    if trust:
        result["trust_stats"] = trust.stats()

    prov = getattr(rt, "provenance", None)
    result["provenance_wired"] = prov is not None
    if prov:
        result["provenance_stats"] = prov.stats()

    gb = getattr(rt, "graph_builder", None)
    result["graph_builder_wired"] = gb is not None
    if gb:
        result["graph_stats"] = gb.stats()

    return result


@router.get("/audit")
async def magma_audit(svc=Depends(get_autonomy_service)):
    rt = _runtime(svc)
    audit = getattr(rt, "audit", None) if rt else None
    if not audit:
        return {"entries": [], "total": 0}
    entries = audit.query_recent(limit=50)
    return {"entries": _entry_list(entries), "total": len(entries)}


@router.get("/audit/agent/{agent_id}")
async def magma_audit_agent(agent_id: str, svc=Depends(get_autonomy_service)):
    rt = _runtime(svc)
    audit = getattr(rt, "audit", None) if rt else None
    if not audit:
        return {"entries": []}
    # AuditProjector stores goal_id — use as closest match for agent queries
    entries = audit.query_by_goal(agent_id)
    return {"entries": _entry_list(entries)}


@router.get("/overlays")
async def magma_overlays(svc=Depends(get_autonomy_service)):
    # No overlay_registry exists in hexagonal runtime
    return {"overlays": {}, "available": False,
            "reason": "OverlayRegistry not ported to hexagonal runtime"}


@router.get("/branches")
async def magma_branches(svc=Depends(get_autonomy_service)):
    # No branch_manager exists in hexagonal runtime
    return {"branches": {}, "available": False,
            "reason": "BranchManager not ported to hexagonal runtime"}


@router.post("/branches/{name}/activate")
async def magma_branch_activate(name: str, _=Depends(require_auth),
                                svc=Depends(get_autonomy_service)):
    return {"ok": False, "supported": False,
            "reason": "BranchManager not ported to hexagonal runtime"}


@router.get("/replay/manifest")
async def magma_replay_manifest(svc=Depends(get_autonomy_service)):
    rt = _runtime(svc)
    replay = getattr(rt, "replay", None) if rt else None
    if not replay:
        return {"missions": [], "available": False,
                "reason": "ReplayAdapter not wired"}
    missions = replay.list_missions(limit=50)
    return {"missions": missions, "total": len(missions)}


@router.get("/replay/deduplicate")
async def magma_replay_dedup(svc=Depends(get_autonomy_service)):
    # ReplayAdapter has no deduplicate method
    return {"duplicate_groups": 0, "groups": [], "available": False,
            "reason": "Deduplication not available in hexagonal ReplayAdapter"}


@router.get("/rollback/preview/{agent_id}")
async def magma_rollback_preview(agent_id: str,
                                 svc=Depends(get_autonomy_service)):
    # No AgentRollback in hexagonal runtime
    return {"available": False,
            "reason": "AgentRollback not ported to hexagonal runtime"}


@router.post("/rollback/{agent_id}")
async def magma_rollback_execute(agent_id: str, _=Depends(require_auth),
                                 svc=Depends(get_autonomy_service)):
    return {"ok": False, "supported": False,
            "reason": "AgentRollback not ported to hexagonal runtime"}
