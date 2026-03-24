"""Trust engine introspection endpoints — hexagonal runtime backed.

Exposes ranking, per-target scores, domain experts, and signal
history through the TrustAdapter on AutonomyRuntime.
"""

import logging

from fastapi import APIRouter, Depends

from waggledance.adapters.http.deps import get_autonomy_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trust", tags=["trust"])


def _trust(svc):
    """Extract the TrustAdapter from AutonomyService → runtime."""
    rt = getattr(svc, "_runtime", None)
    return getattr(rt, "trust", None) if rt else None


@router.get("/ranking")
async def trust_ranking(svc=Depends(get_autonomy_service)):
    ta = _trust(svc)
    if not ta:
        return {"ranking": []}
    # Combine rankings across primary target types
    combined = []
    for target_type in ("capability", "solver", "route", "specialist"):
        try:
            combined.extend(ta.get_ranking(target_type, limit=10))
        except Exception:
            pass
    # Sort by score descending, take top 20
    combined.sort(key=lambda x: x.get("score", 0), reverse=True)
    return {"ranking": combined[:20]}


@router.get("/agent/{agent_id}")
async def trust_agent(agent_id: str, svc=Depends(get_autonomy_service)):
    ta = _trust(svc)
    if not ta:
        return {"reputation": {}}
    scores = {}
    for target_type in ("capability", "solver", "specialist"):
        try:
            score = ta.get_trust_score(target_type, agent_id)
            if score > 0:
                scores[target_type] = round(score, 4)
        except Exception:
            pass
    trend = "\u2014"
    try:
        trend = ta.get_trend("capability", agent_id)
    except Exception:
        pass
    return {"reputation": {"agent_id": agent_id, "scores": scores, "trend": trend}}


@router.get("/domain/{domain}")
async def trust_domain(domain: str, svc=Depends(get_autonomy_service)):
    ta = _trust(svc)
    if not ta:
        return {"experts": []}
    # Domain maps to a target_type if it matches; otherwise return empty
    valid_types = {"capability", "solver", "route", "specialist",
                   "baseline", "executor", "profile"}
    if domain in valid_types:
        try:
            return {"experts": ta.get_ranking(domain, limit=10)}
        except Exception:
            pass
    return {"experts": [], "domain": domain,
            "note": "No trust data for this domain"}


@router.get("/signals/{agent_id}")
async def trust_signals(agent_id: str, svc=Depends(get_autonomy_service)):
    ta = _trust(svc)
    if not ta:
        return {"signals": []}
    # TrustAdapter tracks observations, not raw signals.
    # Return available stats for this target.
    result = {"signals": [], "agent_id": agent_id}
    for target_type in ("capability", "solver", "specialist"):
        try:
            score = ta.get_trust_score(target_type, agent_id)
            trend = ta.get_trend(target_type, agent_id)
            if score > 0:
                result["signals"].append({
                    "type": target_type,
                    "score": round(score, 4),
                    "trend": trend,
                })
        except Exception:
            pass
    return result
