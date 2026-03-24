"""Cross-agent memory sharing endpoints — hexagonal runtime backed.

Exposes channel listing, provenance chains, and consensus queries
through the ProvenanceAdapter on AutonomyRuntime.
"""

import logging

from fastapi import APIRouter, Depends

from waggledance.adapters.http.deps import get_autonomy_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cross", tags=["cross-agent"])


def _provenance(svc):
    """Extract the ProvenanceAdapter from AutonomyService → runtime."""
    rt = getattr(svc, "_runtime", None)
    return getattr(rt, "provenance", None) if rt else None


@router.get("/channels")
async def cross_channels(svc=Depends(get_autonomy_service)):
    # No channel_registry exists in hexagonal runtime
    return {"channels": {}, "available": False,
            "reason": "ChannelRegistry not ported to hexagonal runtime"}


@router.get("/provenance/{fact_id}")
async def cross_provenance(fact_id: str, svc=Depends(get_autonomy_service)):
    prov = _provenance(svc)
    if not prov:
        return {"chain": {}}
    record = prov.get_provenance(fact_id)
    if record is None:
        return {"chain": {}, "fact_id": fact_id, "found": False}
    return {"chain": record.to_dict() if hasattr(record, "to_dict") else record,
            "fact_id": fact_id, "found": True}


@router.get("/consensus")
async def cross_consensus(svc=Depends(get_autonomy_service)):
    prov = _provenance(svc)
    if not prov:
        return {"facts": []}
    verified = prov.get_verified_facts()
    facts = []
    for r in verified:
        if hasattr(r, "to_dict"):
            facts.append(r.to_dict())
        elif isinstance(r, dict):
            facts.append(r)
    return {"facts": facts[:50]}
