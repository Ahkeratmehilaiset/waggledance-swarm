"""GET /api/round-table/* — Round Table consensus transcript data."""
import json
import random
import time
from pathlib import Path
from fastapi import APIRouter

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Simulated Round Table discussions (Finnish) for dashboard dev
# In production, HiveMind streams these via WebSocket
_RT_TOPICS = [
    {
        "topic": "Kevättarkastuksen ajankohta",
        "agents": ["Tarhaaja", "Meteorologi", "Tautivahti", "Kuningatar", "Kasvivahti", "Emovahti"],
        "discussion": [
            {"agent": "Meteorologi", "msg": "FMI-ennuste: +12°C kolme peräkkäistä päivää to-la."},
            {"agent": "Tarhaaja", "msg": "Pesien lentoaukoilla aktiivista liikettä. Siitepölykuormia näkyy."},
            {"agent": "Tautivahti", "msg": "Nosema-riski korkea pitkän talven jälkeen. Tarkastus tärkeää."},
            {"agent": "Emovahti", "msg": "Emojen kunto selvitettävä. Vuoden 2024 emot vaihdettavia."},
            {"agent": "Kasvivahti", "msg": "Voikukka alkaa kukkia Etelä-Suomessa — siitepölyä saatavilla."},
            {"agent": "Kuningatar", "msg": "KONSENSUS: Kevättarkastus aloitetaan torstaina. 5/6 puoltaa."},
        ],
        "consensus": "Kevättarkastus aloitetaan torstaina kun T > +12°C kolme päivää.",
        "agreement": 0.92,
    },
    {
        "topic": "Varroa-hoitosuunnitelma syksy",
        "agents": ["Tarhaaja", "Tautivahti", "Meteorologi", "Kuningatar", "Sähkö", "Rikastus"],
        "discussion": [
            {"agent": "Tautivahti", "msg": "Varroa-taso keskimäärin 2.8/100. Hoitokynnys 3/100 lähellä."},
            {"agent": "Tarhaaja", "msg": "Muurahaishappo elokuussa, oksaalihappo lokakuussa. Perinteinen ohjelma."},
            {"agent": "Meteorologi", "msg": "Syyskuun ennuste: 8-12°C. Optimaalinen muurahaishappohoitoon."},
            {"agent": "Sähkö", "msg": "Oksaalihappohöyrystin: halvin sähkö yöllä 23-04. Ajoitetaan."},
            {"agent": "Rikastus", "msg": "Tutkimusdata: yhdistelmähoito tehokkain — 96% pudotus vs 78% yksittäin."},
            {"agent": "Kuningatar", "msg": "PÄÄTÖS: Muurahaishappo elo-syyskuu + oksaalihappo lokakuu. Kaikki vahvistivat."},
        ],
        "consensus": "Yhdistelmähoito: muurahaishappo elokuussa + oksaalihappo lokakuussa.",
        "agreement": 1.0,
    },
    {
        "topic": "Energian optimointi linkouskausi",
        "agents": ["Sähkö", "Tarhaaja", "Meteorologi", "Kuningatar", "Varastovahti", "Logistiikka"],
        "discussion": [
            {"agent": "Sähkö", "msg": "Spot-hinta vaihtelee 1-15 snt/kWh. Linkous yöllä säästää 40%."},
            {"agent": "Tarhaaja", "msg": "Linkous kestää 4-6h per pesä. 12 pesää = 2-3 yötä."},
            {"agent": "Meteorologi", "msg": "Ensi viikko lämmin — hunaja juoksevampaa, linkous helpompaa."},
            {"agent": "Varastovahti", "msg": "480 purkkia valmiina. Riittää 240kg pakkaamiseen."},
            {"agent": "Logistiikka", "msg": "Helsinki→Kouvola kuljetus perjantaina. 6 pesää per kuorma."},
            {"agent": "Kuningatar", "msg": "SUUNNITELMA: Linkous ti-ke yöllä halvalla sähköllä. Hyväksytty."},
        ],
        "consensus": "Linkous ajoitetaan ti-ke yöhön halvimman sähkön aikaan.",
        "agreement": 0.95,
    },
]

_rt_counter = 0


@router.get("/api/round-table/recent")
async def round_table_recent(limit: int = 5):
    """Return recent Round Table discussions."""
    global _rt_counter
    _rt_counter += 1

    discussions = []
    now = time.time()
    for i, rt in enumerate(_RT_TOPICS[:limit]):
        discussions.append({
            "id": i + 1,
            "topic": rt["topic"],
            "agents": rt["agents"],
            "agent_count": len(rt["agents"]),
            "discussion": rt["discussion"],
            "consensus": rt["consensus"],
            "agreement": rt["agreement"],
            "timestamp": now - (i * 1200),  # 20 min apart
        })

    return {
        "count": len(discussions),
        "discussions": discussions,
    }


@router.get("/api/round-table/stats")
async def round_table_stats():
    """Round Table aggregate stats."""
    return {
        "total_discussions": len(_RT_TOPICS) + random.randint(10, 50),
        "avg_agreement": round(sum(rt["agreement"] for rt in _RT_TOPICS) / len(_RT_TOPICS), 2),
        "avg_agents": round(sum(len(rt["agents"]) for rt in _RT_TOPICS) / len(_RT_TOPICS), 1),
        "most_active_agents": ["Kuningatar", "Tarhaaja", "Tautivahti", "Meteorologi", "Sähkö"],
        "frequency": "every 20 heartbeats",
    }
