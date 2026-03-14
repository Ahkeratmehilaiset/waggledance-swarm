"""Bootstrap service — agent loading and cache warming.

Replaces startup logic from main.py and hivemind.py.
Does NOT install packages, does NOT write .env files.
Does NOT write to hot cache directly — returns candidates
for ChatService to populate per STATE_OWNERSHIP.md.
"""

import logging
from pathlib import Path

from waggledance.core.domain.agent import AgentDefinition
from waggledance.core.orchestration.lifecycle import AgentLifecycleManager
from waggledance.core.ports.config_port import ConfigPort

log = logging.getLogger(__name__)


class BootstrapService:
    """Loads agents from YAML and prepares cache warming candidates."""

    def __init__(
        self,
        config: ConfigPort,
        lifecycle: AgentLifecycleManager,
    ) -> None:
        self._config = config
        self._lifecycle = lifecycle

    async def load_agents(
        self,
        agents_dir: Path,
    ) -> list[AgentDefinition]:
        """Load all YAML agent definitions for current profile.

        Scans agents_dir for .yaml files, parses agent structure,
        filters by active profile, and returns activated agents.
        """
        import yaml

        profile = self._config.get_profile()
        agents: list[AgentDefinition] = []

        if not agents_dir.exists():
            log.warning("Agents directory not found: %s", agents_dir)
            return agents

        for yaml_file in sorted(agents_dir.rglob("*.yaml")):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f)

                if not data or not isinstance(data, dict):
                    continue

                header = data.get("header", {})
                profiles = data.get("profiles", ["ALL"])

                agent = AgentDefinition(
                    id=header.get("agent_id", yaml_file.stem),
                    name=header.get("agent_name", yaml_file.stem),
                    domain=header.get("domain", "general"),
                    tags=data.get("tags", []),
                    skills=list(
                        data.get("DECISION_METRICS_AND_THRESHOLDS", {}).keys()
                    ),
                    trust_level=0,
                    specialization_score=0.0,
                    active=False,
                    profile=profiles[0] if profiles else "ALL",
                )
                agents.append(agent)
            except Exception as e:
                log.warning("Failed to load %s: %s", yaml_file, e)

        activated = self._lifecycle.spawn_for_profile(agents, profile)
        log.info(
            "Loaded %d agents for profile %s (%d total)",
            len(activated), profile, len(agents),
        )
        return activated

    async def warm_cache_candidates(
        self,
        memory_service: "MemoryService",  # noqa: F821
    ) -> list[tuple[str, str, int | None]]:
        """Return (key, value, ttl) tuples for cache preloading.

        Does NOT write to hot cache directly — ChatService is the
        sole hot cache writer per STATE_OWNERSHIP.md.
        """
        results = await memory_service.retrieve_context(
            query="common questions",
            language="en",
            limit=20,
        )
        candidates: list[tuple[str, str, int | None]] = []
        for record in results:
            if record.confidence >= 0.8:
                key = record.content[:50].strip().lower()
                candidates.append((key, record.content, 7200))
        return candidates
