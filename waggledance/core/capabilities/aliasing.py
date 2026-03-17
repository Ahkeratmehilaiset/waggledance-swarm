"""
AliasRegistry — resolves legacy agent_id ↔ canonical ID.

Phase 1 of Full Autonomy v3: every agent gets a stable canonical ID
(e.g. 'domain.apiary.beekeeper') while all legacy IDs and Finnish
aliases continue to resolve transparently.

Usage:
    registry = AliasRegistry.from_yaml("configs/alias_registry.yaml")
    canonical = registry.resolve("beekeeper")      # → "domain.apiary.beekeeper"
    canonical = registry.resolve("tarhaaja")        # → "domain.apiary.beekeeper"
    legacy    = registry.primary_legacy("domain.apiary.beekeeper")  # → "beekeeper"
    agents    = registry.by_profile("cottage")      # → [AgentAlias(...), ...]
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml


@dataclass(frozen=True)
class AgentAlias:
    """Immutable record for one agent's identity mapping."""
    legacy_id: str
    canonical: str
    aliases: tuple  # frozen tuple of all known names
    profiles: tuple  # frozen tuple of profile strings

    @classmethod
    def from_dict(cls, legacy_id: str, data: dict) -> "AgentAlias":
        aliases_list = data.get("aliases", [legacy_id])
        # Ensure legacy_id is always in aliases
        all_aliases = list(dict.fromkeys([legacy_id] + aliases_list))
        return cls(
            legacy_id=legacy_id,
            canonical=data["canonical"],
            aliases=tuple(all_aliases),
            profiles=tuple(data.get("profiles", [])),
        )


class AliasRegistry:
    """
    Bidirectional lookup between legacy agent IDs, aliases, and canonical IDs.

    Thread-safe after construction (all data is immutable).
    """

    def __init__(self, agents: List[AgentAlias]):
        self._agents: List[AgentAlias] = list(agents)

        # legacy_id / alias → canonical
        self._to_canonical: Dict[str, str] = {}
        # canonical → AgentAlias
        self._by_canonical: Dict[str, AgentAlias] = {}
        # profile → list of AgentAlias
        self._by_profile: Dict[str, List[AgentAlias]] = {}

        for agent in self._agents:
            self._by_canonical[agent.canonical] = agent
            for alias in agent.aliases:
                lower = alias.lower()
                self._to_canonical[lower] = agent.canonical
            # Also allow resolving by canonical itself
            self._to_canonical[agent.canonical.lower()] = agent.canonical
            for profile in agent.profiles:
                self._by_profile.setdefault(profile.lower(), []).append(agent)

    # ── Factory ──────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AliasRegistry":
        """Load registry from configs/alias_registry.yaml."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Alias registry not found: {path}")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        agents = [
            AgentAlias.from_dict(legacy_id, entry)
            for legacy_id, entry in data.items()
            if isinstance(entry, dict) and "canonical" in entry
        ]
        return cls(agents)

    @classmethod
    def from_yaml_default(cls) -> "AliasRegistry":
        """Load from the standard project location."""
        # Try multiple locations (repo root vs package)
        candidates = [
            Path("configs/alias_registry.yaml"),
            Path(__file__).resolve().parents[3] / "configs" / "alias_registry.yaml",
        ]
        for p in candidates:
            if p.exists():
                return cls.from_yaml(p)
        raise FileNotFoundError(
            f"alias_registry.yaml not found in: {[str(c) for c in candidates]}"
        )

    # ── Core lookups ─────────────────────────────────────────

    def resolve(self, name: str) -> Optional[str]:
        """
        Resolve any name (legacy_id, alias, or canonical) to canonical ID.
        Returns None if not found.
        """
        return self._to_canonical.get(name.lower())

    def resolve_strict(self, name: str) -> str:
        """Like resolve() but raises KeyError if not found."""
        result = self.resolve(name)
        if result is None:
            raise KeyError(f"Unknown agent: {name!r}")
        return result

    def get(self, canonical: str) -> Optional[AgentAlias]:
        """Get full AgentAlias by canonical ID."""
        return self._by_canonical.get(canonical)

    def primary_legacy(self, canonical: str) -> Optional[str]:
        """Get the primary legacy_id for a canonical ID."""
        agent = self._by_canonical.get(canonical)
        return agent.legacy_id if agent else None

    def all_legacy_ids(self, canonical: str) -> List[str]:
        """Get all known aliases for a canonical ID."""
        agent = self._by_canonical.get(canonical)
        return list(agent.aliases) if agent else []

    # ── Filtering ────────────────────────────────────────────

    def by_profile(self, profile: str) -> List[AgentAlias]:
        """Get all agents belonging to a profile."""
        return list(self._by_profile.get(profile.lower(), []))

    def canonicals_for_profile(self, profile: str) -> List[str]:
        """Get canonical IDs for all agents in a profile."""
        return [a.canonical for a in self.by_profile(profile)]

    def legacy_ids_for_profile(self, profile: str) -> List[str]:
        """Get legacy IDs for all agents in a profile."""
        return [a.legacy_id for a in self.by_profile(profile)]

    # ── Bulk operations (for migration scripts) ──────────────

    def build_legacy_to_canonical_map(self) -> Dict[str, str]:
        """
        Return a flat dict mapping every known alias → canonical.
        Useful for SQL UPDATE statements during migration.
        """
        return dict(self._to_canonical)

    def all_canonical_ids(self) -> List[str]:
        """Return all canonical IDs."""
        return list(self._by_canonical.keys())

    def all_agents(self) -> List[AgentAlias]:
        """Return all AgentAlias records."""
        return list(self._agents)

    # ── Info ─────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: str) -> bool:
        return self.resolve(name) is not None

    def __repr__(self) -> str:
        return f"AliasRegistry({len(self._agents)} agents)"
