"""
WaggleDance — Agent Level System v3.0
======================================
Phase 3: Social Learning

Agents earn levels (1-5) based on performance.
Higher levels grant more capabilities (write swarm_facts, consult agents, web search).
Demotion on sustained hallucination rate over a 50-response sliding window.

Stats stored in ChromaDB "agent_stats" collection.
"""

import json
import time
import logging
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("agent_levels")


# ═══════════════════════════════════════════════════════════════
# ENUMS & CONSTANTS
# ═══════════════════════════════════════════════════════════════

class AgentLevel(IntEnum):
    NOVICE = 1
    APPRENTICE = 2
    JOURNEYMAN = 3
    EXPERT = 4
    MASTER = 5


# Promotion thresholds: what you need to REACH the next level
LEVEL_CRITERIA = {
    AgentLevel.APPRENTICE: {
        "correct_responses": 50,
        "max_hallucination_rate": 0.15,
        "min_trust": 0.3,
    },
    AgentLevel.JOURNEYMAN: {
        "correct_responses": 200,
        "max_hallucination_rate": 0.08,
        "min_trust": 0.6,
    },
    AgentLevel.EXPERT: {
        "correct_responses": 500,
        "max_hallucination_rate": 0.03,
        "min_trust": 0.8,
    },
    AgentLevel.MASTER: {
        "correct_responses": 1000,
        "max_hallucination_rate": 0.01,
        "min_trust": 0.95,
    },
}

# Per-level capabilities
LEVEL_CAPABILITIES = {
    AgentLevel.NOVICE: {
        "max_tokens": 200,
        "can_read_swarm_facts": False,
        "can_write_swarm_facts": False,
        "can_consult_agents": 0,
        "can_web_search": False,
    },
    AgentLevel.APPRENTICE: {
        "max_tokens": 300,
        "can_read_swarm_facts": True,
        "can_write_swarm_facts": False,
        "can_consult_agents": 0,
        "can_web_search": False,
    },
    AgentLevel.JOURNEYMAN: {
        "max_tokens": 400,
        "can_read_swarm_facts": True,
        "can_write_swarm_facts": True,
        "can_consult_agents": 1,
        "can_web_search": False,
    },
    AgentLevel.EXPERT: {
        "max_tokens": 500,
        "can_read_swarm_facts": True,
        "can_write_swarm_facts": True,
        "can_consult_agents": 3,
        "can_web_search": True,
    },
    AgentLevel.MASTER: {
        "max_tokens": 800,
        "can_read_swarm_facts": True,
        "can_write_swarm_facts": True,
        "can_consult_agents": 6,
        "can_web_search": True,
    },
}

# Demotion: halluc rate over last N responses exceeds threshold → drop 1 level
DEMOTION_WINDOW = 50

# Demotion hallucination thresholds per level
DEMOTION_THRESHOLDS = {
    AgentLevel.APPRENTICE: 0.20,
    AgentLevel.JOURNEYMAN: 0.12,
    AgentLevel.EXPERT: 0.06,
    AgentLevel.MASTER: 0.03,
}


# ═══════════════════════════════════════════════════════════════
# AGENT STATS
# ═══════════════════════════════════════════════════════════════

@dataclass
class AgentStats:
    agent_id: str
    agent_type: str = ""
    level: int = AgentLevel.NOVICE
    total_responses: int = 0
    correct_responses: int = 0
    hallucination_count: int = 0
    user_corrections: int = 0
    trust_score: float = 0.1
    specialties: List[str] = field(default_factory=list)
    recent_hallucinations: deque = field(
        default_factory=lambda: deque(maxlen=DEMOTION_WINDOW))
    last_updated: str = ""

    def hallucination_rate(self) -> float:
        if self.total_responses == 0:
            return 0.0
        return self.hallucination_count / self.total_responses

    def recent_hallucination_rate(self) -> float:
        if len(self.recent_hallucinations) == 0:
            return 0.0
        return sum(self.recent_hallucinations) / len(self.recent_hallucinations)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "level": self.level,
            "level_name": AgentLevel(self.level).name,
            "total_responses": self.total_responses,
            "correct_responses": self.correct_responses,
            "hallucination_count": self.hallucination_count,
            "user_corrections": self.user_corrections,
            "trust_score": round(self.trust_score, 4),
            "specialties": self.specialties,
            "hallucination_rate": round(self.hallucination_rate(), 4),
            "recent_hallucination_rate": round(self.recent_hallucination_rate(), 4),
            "last_updated": self.last_updated,
        }

    def to_json(self) -> str:
        d = self.to_dict()
        d["recent_hallucinations"] = list(self.recent_hallucinations)
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "AgentStats":
        d = json.loads(data)
        stats = cls(
            agent_id=d["agent_id"],
            agent_type=d.get("agent_type", ""),
            level=d.get("level", AgentLevel.NOVICE),
            total_responses=d.get("total_responses", 0),
            correct_responses=d.get("correct_responses", 0),
            hallucination_count=d.get("hallucination_count", 0),
            user_corrections=d.get("user_corrections", 0),
            trust_score=d.get("trust_score", 0.1),
            specialties=d.get("specialties", []),
            last_updated=d.get("last_updated", ""),
        )
        for h in d.get("recent_hallucinations", []):
            stats.recent_hallucinations.append(h)
        return stats


# ═══════════════════════════════════════════════════════════════
# AGENT LEVEL MANAGER
# ═══════════════════════════════════════════════════════════════

class AgentLevelManager:
    def __init__(self, db_path: str = "data/chroma_db"):
        import chromadb
        Path(db_path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=db_path)
        self._collection = self._client.get_or_create_collection(
            name="agent_stats",
            metadata={"hnsw:space": "cosine"}
        )
        self._cache: Dict[str, AgentStats] = {}
        log.info(f"AgentLevelManager: {self._collection.count()} agents tracked")

    def _load_stats(self, agent_id: str) -> AgentStats:
        if agent_id in self._cache:
            return self._cache[agent_id]

        try:
            result = self._collection.get(ids=[agent_id], include=["documents"])
            if result["documents"] and result["documents"][0]:
                stats = AgentStats.from_json(result["documents"][0])
                self._cache[agent_id] = stats
                return stats
        except Exception:
            pass

        # Default new agent
        stats = AgentStats(agent_id=agent_id)
        self._cache[agent_id] = stats
        return stats

    def _save_stats(self, stats: AgentStats):
        stats.last_updated = time.strftime("%Y-%m-%dT%H:%M:%S")
        doc = stats.to_json()
        meta = {
            "level": stats.level,
            "trust_score": stats.trust_score,
            "total_responses": stats.total_responses,
            "agent_type": stats.agent_type,
        }
        # ChromaDB needs a dummy embedding for doc-only collections
        # Use a simple fixed-size vector
        dummy_emb = [0.0] * 10
        self._collection.upsert(
            ids=[stats.agent_id],
            documents=[doc],
            metadatas=[meta],
            embeddings=[dummy_emb],
        )
        self._cache[stats.agent_id] = stats

    def record_response(self, agent_id: str, agent_type: str = "",
                        was_correct: bool = True,
                        was_hallucination: bool = False,
                        was_corrected: bool = False) -> dict:
        """Record an agent response and update stats. Returns level change info."""
        stats = self._load_stats(agent_id)
        if agent_type:
            stats.agent_type = agent_type

        stats.total_responses += 1
        if was_correct:
            stats.correct_responses += 1
        if was_hallucination:
            stats.hallucination_count += 1
        if was_corrected:
            stats.user_corrections += 1
            stats.trust_score = max(0.0, stats.trust_score - 0.05)

        # Track in sliding window (1 = hallucination, 0 = clean)
        stats.recent_hallucinations.append(1 if was_hallucination else 0)

        # Trust EMA: trust = trust * 0.95 + quality * 0.05
        quality = 1.0 if was_correct and not was_hallucination else 0.0
        stats.trust_score = stats.trust_score * 0.95 + quality * 0.05

        # Check promotion/demotion
        old_level = stats.level
        result = {"agent_id": agent_id, "old_level": old_level, "new_level": old_level,
                  "changed": False, "direction": None}

        if self._check_demotion(stats):
            stats.level = max(AgentLevel.NOVICE, stats.level - 1)
            log.warning(f"Agent {agent_id} DEMOTED: L{old_level} -> L{stats.level} "
                        f"(recent halluc rate: {stats.recent_hallucination_rate():.0%})")
        elif self._check_promotion(stats):
            stats.level = min(AgentLevel.MASTER, stats.level + 1)
            log.info(f"Agent {agent_id} PROMOTED: L{old_level} -> L{stats.level} "
                     f"(correct={stats.correct_responses}, trust={stats.trust_score:.2f})")

        if stats.level != old_level:
            result["new_level"] = stats.level
            result["changed"] = True
            result["direction"] = "up" if stats.level > old_level else "down"

        self._save_stats(stats)
        return result

    def _check_promotion(self, stats: AgentStats) -> bool:
        next_level = stats.level + 1
        if next_level > AgentLevel.MASTER:
            return False
        criteria = LEVEL_CRITERIA.get(AgentLevel(next_level))
        if not criteria:
            return False
        return (stats.correct_responses >= criteria["correct_responses"]
                and stats.hallucination_rate() <= criteria["max_hallucination_rate"]
                and stats.trust_score >= criteria["min_trust"])

    def _check_demotion(self, stats: AgentStats) -> bool:
        if stats.level <= AgentLevel.NOVICE:
            return False
        if len(stats.recent_hallucinations) < DEMOTION_WINDOW:
            return False
        threshold = DEMOTION_THRESHOLDS.get(AgentLevel(stats.level), 0.20)
        return stats.recent_hallucination_rate() > threshold

    def get_level(self, agent_id: str) -> int:
        return self._load_stats(agent_id).level

    def get_capabilities(self, agent_id: str) -> dict:
        level = self.get_level(agent_id)
        return dict(LEVEL_CAPABILITIES.get(AgentLevel(level),
                                           LEVEL_CAPABILITIES[AgentLevel.NOVICE]))

    def get_stats(self, agent_id: str) -> dict:
        return self._load_stats(agent_id).to_dict()

    def get_all_stats(self) -> Dict[str, dict]:
        """Get stats for all tracked agents."""
        result = {}
        try:
            all_data = self._collection.get(include=["documents"])
            if all_data["ids"]:
                for aid, doc in zip(all_data["ids"], all_data["documents"]):
                    if doc:
                        try:
                            stats = AgentStats.from_json(doc)
                            self._cache[aid] = stats
                            result[aid] = stats.to_dict()
                        except Exception:
                            pass
        except Exception as e:
            log.error(f"get_all_stats error: {e}")
        return result
