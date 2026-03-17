"""
Entity Registry — typed catalogue of all entities in the world model.

Entities are the "things" the system reasons about:
  - beehives, sensors, devices, areas, processes, users, weather stations
  - each has a stable entity_id, a type, and arbitrary attributes
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.world.entities")


@dataclass
class Entity:
    """A named thing in the world model."""
    entity_id: str
    entity_type: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def update(self, **attrs):
        """Merge new attributes."""
        self.attributes.update(attrs)
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "attributes": dict(self.attributes),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class EntityRegistry:
    """In-memory registry of world model entities."""

    def __init__(self):
        self._entities: Dict[str, Entity] = {}

    def register(
        self,
        entity_id: str,
        entity_type: str,
        attributes: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Entity:
        """Register or update an entity."""
        existing = self._entities.get(entity_id)
        if existing:
            if attributes:
                existing.update(**attributes)
            if kwargs:
                existing.update(**kwargs)
            log.debug("Entity updated: %s", entity_id)
            return existing

        entity = Entity(
            entity_id=entity_id,
            entity_type=entity_type,
            attributes=attributes or {},
        )
        if kwargs:
            entity.attributes.update(kwargs)
        self._entities[entity_id] = entity
        log.debug("Entity registered: %s (type=%s)", entity_id, entity_type)
        return entity

    def get(self, entity_id: str) -> Optional[Entity]:
        return self._entities.get(entity_id)

    def list(self, entity_type: Optional[str] = None) -> List[Entity]:
        if entity_type is None:
            return list(self._entities.values())
        return [e for e in self._entities.values() if e.entity_type == entity_type]

    def remove(self, entity_id: str) -> bool:
        if entity_id in self._entities:
            del self._entities[entity_id]
            return True
        return False

    def count(self, entity_type: Optional[str] = None) -> int:
        if entity_type is None:
            return len(self._entities)
        return sum(1 for e in self._entities.values() if e.entity_type == entity_type)

    def has(self, entity_id: str) -> bool:
        return entity_id in self._entities

    def clear(self):
        self._entities.clear()

    def to_dict(self) -> Dict[str, dict]:
        return {eid: e.to_dict() for eid, e in self._entities.items()}
