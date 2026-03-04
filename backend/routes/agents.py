"""GET /api/agents/* — Agent trust levels and leaderboard."""
import random
from pathlib import Path
from fastapi import APIRouter

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Level names
_LEVEL_NAMES = {1: "NOVICE", 2: "APPRENTICE", 3: "JOURNEYMAN", 4: "EXPERT", 5: "MASTER"}

# Agent roster — populated on first call from agents/ directory
_agent_cache: list[dict] | None = None


def _load_agents() -> list[dict]:
    """Build agent list from agents/ directory with simulated levels."""
    global _agent_cache
    if _agent_cache is not None:
        return _agent_cache

    agents_dir = _PROJECT_ROOT / "agents"
    agents = []
    if agents_dir.is_dir():
        for d in sorted(agents_dir.iterdir()):
            if d.is_dir() and (d / "core.yaml").exists():
                agent_id = d.name
                # Simulate levels — production reads from ChromaDB agent_stats
                # Bee-related agents tend to be higher level (more training data)
                if agent_id in ("beekeeper", "disease_monitor", "swarm_watcher",
                                "nectar_scout", "hive_temperature"):
                    level = random.choice([4, 5])
                elif agent_id in ("meteorologist", "flight_weather", "phenologist",
                                  "electrician", "energy_advisor"):
                    level = random.choice([3, 4])
                else:
                    level = random.choice([1, 2, 3])

                trust = round(min(1.0, 0.1 + level * 0.18 + random.uniform(-0.05, 0.05)), 2)
                total_resp = level * random.randint(30, 120)
                halluc_rate = round(max(0, 0.15 - level * 0.03 + random.uniform(-0.01, 0.01)), 3)

                agents.append({
                    "agent_id": agent_id,
                    "level": level,
                    "level_name": _LEVEL_NAMES[level],
                    "trust_score": trust,
                    "total_responses": total_resp,
                    "hallucination_rate": halluc_rate,
                })

    _agent_cache = agents
    return agents


@router.get("/api/agents/levels")
async def agents_levels():
    """All agents with their current level, trust, halluc rate."""
    agents = _load_agents()

    # Level distribution
    level_dist = {name: 0 for name in _LEVEL_NAMES.values()}
    for a in agents:
        level_dist[a["level_name"]] += 1

    return {
        "total": len(agents),
        "level_distribution": level_dist,
        "agents": agents,
    }


@router.get("/api/agents/leaderboard")
async def agents_leaderboard():
    """Top agents by trust and queries handled."""
    agents = _load_agents()

    by_trust = sorted(agents, key=lambda a: -a["trust_score"])[:10]
    by_queries = sorted(agents, key=lambda a: -a["total_responses"])[:10]
    by_reliability = sorted(agents, key=lambda a: a["hallucination_rate"])[:10]

    return {
        "top_trust": by_trust,
        "top_queries": by_queries,
        "most_reliable": by_reliability,
    }
