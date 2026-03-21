"""
MAGMA Layer 4: Semantic communication channels for cross-agent memory sharing.
Groups agents by role or domain for structured message exchange.
"""

import logging
import time
from typing import Dict, List, Optional

log = logging.getLogger("waggledance.agent_channels")

MAX_HISTORY = 200

DEFAULT_ROLE_MAP = {
    "scouts": ["web_researcher", "rss_monitor", "distiller"],
    "workers": ["queen_bee", "nurse_bee", "guard_bee", "forager_bee",
                "wax_builder", "drone", "scout_bee"],
    "judges": ["round_table", "queen_bee"],
}


class AgentChannel:
    """Semantic communication channel grouping agents by role or domain."""

    def __init__(self, name: str, agent_ids: List[str], channel_type: str = "domain"):
        self.name = name
        self.agent_ids = list(agent_ids)
        self.channel_type = channel_type
        self._history: List[dict] = []

    @property
    def members(self) -> List[str]:
        return list(self.agent_ids)

    def post(self, from_agent_id: str, message: str, metadata: Optional[dict] = None):
        entry = {
            "from": from_agent_id,
            "message": message,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }
        self._history.append(entry)
        if len(self._history) > MAX_HISTORY:
            self._history = self._history[-MAX_HISTORY:]

    def get_history(self, limit: int = 20) -> List[dict]:
        return self._history[-limit:]


class ChannelRegistry:
    """Manages named agent channels."""

    def __init__(self):
        self._channels: Dict[str, AgentChannel] = {}

    def create(self, name: str, agent_ids: List[str],
               channel_type: str = "domain") -> AgentChannel:
        ch = AgentChannel(name, agent_ids, channel_type)
        self._channels[name] = ch
        return ch

    def get(self, name: str) -> Optional[AgentChannel]:
        return self._channels.get(name)

    def get_channels_for_agent(self, agent_id: str) -> List[AgentChannel]:
        return [ch for ch in self._channels.values()
                if agent_id in ch.agent_ids]

    def broadcast(self, channel_name: str, from_agent_id: str, message: str):
        ch = self._channels.get(channel_name)
        if ch:
            ch.post(from_agent_id, message)

    def list_all(self) -> dict:
        return {name: {"members": ch.members, "type": ch.channel_type,
                        "history_len": len(ch._history)}
                for name, ch in self._channels.items()}

    def auto_create_role_channels(self, role_map: Optional[dict] = None):
        rm = role_map or DEFAULT_ROLE_MAP
        for role_name, agent_ids in rm.items():
            self.create(role_name, agent_ids, channel_type="role")
        log.info(f"Auto-created {len(rm)} role channels")

    def auto_create_domain_channels(self, routing_keywords: dict):
        """Create domain channels from keyword groups.

        routing_keywords: {"bee_domain": ["queen_bee", "nurse_bee"], ...}
        """
        for domain, agent_ids in routing_keywords.items():
            if isinstance(agent_ids, list) and agent_ids:
                self.create(domain, agent_ids, channel_type="domain")
        log.info(f"Auto-created {len(routing_keywords)} domain channels")
