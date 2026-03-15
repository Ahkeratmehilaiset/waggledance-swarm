"""Learning task queue — extracted from memory_engine.py v1.18.0.

Provides guided tasks for heartbeat learning: YAML scan, low-confidence
queries, seasonal topics, and random domain exploration.
"""

import json
import logging
import random
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# SEASONAL + DOMAIN TOPIC CONSTANTS
# ═══════════════════════════════════════════════════════════════

SEASONAL_BOOST = {
    1: ["talvehtiminen", "wintering", "talviruokinta", "winter feeding"],
    2: ["kevättarkastus", "spring inspection", "nosema", "emojen tarkistus"],
    3: ["keväthoito", "spring management", "siitepöly", "pollen"],
    4: ["yhdistäminen", "combining", "emojen kasvatus", "queen rearing"],
    5: ["rakennuskehä", "foundation", "parven esto", "swarm prevention"],
    6: ["linkoaminen", "honey extraction", "lisäkorotus", "super"],
    7: ["linkous", "extraction", "hunaja", "honey", "mesikausi"],
    8: ["varroa", "treatment", "muurahaishappo", "formic acid"],
    9: ["syyshoito", "autumn management", "oksaalihappo", "oxalic acid"],
    10: ["talvivalmistelut", "winter preparation", "syysruokinta", "autumn feeding"],
    11: ["talvehtiminen", "wintering", "eristys", "insulation"],
    12: ["talvilepo", "winter rest", "lumitilanne", "monitoring"],
}

DOMAIN_TOPICS = {
    "beekeeper": [
        "varroa treatments", "queen rearing", "swarm prevention",
        "honey harvest", "winter preparation", "spring inspection",
    ],
    "disease_monitor": [
        "AFB detection", "EFB symptoms", "nosema prevention",
        "chalkbrood treatment", "disease reporting",
    ],
    "meteorologist": [
        "weather forecast impact", "temperature thresholds",
        "rain prediction", "frost warning",
    ],
    "horticulturist": [
        "nectar plants", "pollen sources", "bloom calendar",
        "landscape planning", "wildflower meadows",
    ],
    "business": [
        "honey pricing", "VAT rules", "marketing strategy",
        "sales channels", "food safety",
    ],
}


class LearningTaskQueue:
    """Provides guided tasks for heartbeat learning instead of random thinking.

    Priority order:
    1. Unread YAML sections
    2. Low-coverage topics (sparse in memory)
    3. Recent low-confidence user queries
    4. Seasonal topics
    5. Random domain exploration
    """

    def __init__(self, consciousness,
                 scan_progress_path: str = "data/scan_progress.json"):
        self._consciousness = consciousness
        self._scan_progress_path = Path(scan_progress_path)
        self._low_confidence_queries: deque = deque(maxlen=50)
        self._last_tasks: deque = deque(maxlen=20)

    def record_low_confidence_query(self, query: str, confidence: float):
        """Record a query that had no good memory match."""
        self._low_confidence_queries.append({
            "query": query,
            "confidence": confidence,
            "timestamp": time.time(),
        })

    def next_task(self, agent_id: str = None,
                  agent_type: str = None) -> Optional[dict]:
        """Get next guided learning task. Returns dict with task info or None."""
        for fn in [
            self._unread_yaml_task,
            self._low_confidence_task,
            self._seasonal_task,
            lambda at: self._random_task(at),
        ]:
            try:
                task = fn(agent_type)
                if task and task.get("topic") not in self._last_tasks:
                    self._last_tasks.append(task.get("topic", ""))
                    return task
            except Exception:
                continue
        return None

    def _unread_yaml_task(self, agent_type: str = None) -> Optional[dict]:
        """Check scan_progress.json for unscanned YAML files."""
        if not self._scan_progress_path.exists():
            return None
        try:
            with open(self._scan_progress_path, encoding="utf-8") as f:
                progress = json.load(f)
            scanned = set(progress.get("scanned_files", []))
        except Exception:
            return None

        knowledge_dir = Path("knowledge")
        if not knowledge_dir.exists():
            return None
        yaml_files = list(knowledge_dir.rglob("*.yaml")) + list(knowledge_dir.rglob("*.yml"))
        unscanned = [f for f in yaml_files if str(f) not in scanned]
        if not unscanned:
            return None

        target = unscanned[0]
        return {
            "type": "yaml_scan",
            "topic": str(target),
            "prompt": f"Read and learn from knowledge file: {target.name}",
            "priority": 1,
            "source": "yaml_scanner",
        }

    def _low_confidence_task(self, agent_type: str = None) -> Optional[dict]:
        """Return a recent query the system couldn't answer well."""
        if not self._low_confidence_queries:
            return None
        entry = self._low_confidence_queries.popleft()
        query = entry["query"]
        return {
            "type": "research",
            "topic": query[:80],
            "prompt": (f"A user asked: '{query}' but we had no good answer. "
                       f"Research this topic and provide a factual answer."),
            "priority": 3,
            "source": "low_confidence",
        }

    def _seasonal_task(self, agent_type: str = None) -> Optional[dict]:
        """Return a seasonally relevant learning topic."""
        month = datetime.now().month
        keywords = SEASONAL_BOOST.get(month, [])
        if not keywords:
            return None
        topic = random.choice(keywords)
        return {
            "type": "seasonal",
            "topic": topic,
            "prompt": (f"It is month {month}. Research the seasonal topic: '{topic}'. "
                       f"What should a Finnish beekeeper know about this right now?"),
            "priority": 4,
            "source": "seasonal",
        }

    def _random_task(self, agent_type: str = None) -> Optional[dict]:
        """Random domain topic for agent's specialty."""
        topics = DOMAIN_TOPICS.get(agent_type, DOMAIN_TOPICS.get("beekeeper", []))
        if not topics:
            return None
        topic = random.choice(topics)
        return {
            "type": "exploration",
            "topic": topic,
            "prompt": (f"Research and share one new practical fact about: '{topic}'. "
                       f"Focus on Finnish beekeeping conditions."),
            "priority": 5,
            "source": "random_exploration",
        }
