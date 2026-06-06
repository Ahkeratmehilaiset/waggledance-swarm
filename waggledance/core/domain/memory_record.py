"""Memory record domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PALACE_METADATA_PREFIX = "palace_"


def _clean_path_part(value: str) -> str:
    part = value.strip().strip("/")
    if not part:
        raise ValueError("memory palace path parts must not be empty")
    if "/" in part:
        raise ValueError("memory palace path parts must not contain '/'")
    return part


def normalize_palace_path(path: str) -> str:
    """Normalize a human-facing palace path to a stable slash path."""
    parts = [_clean_path_part(part) for part in path.split("/") if part.strip()]
    if not parts:
        raise ValueError("memory palace path must contain at least one part")
    return "/".join(parts)


@dataclass(frozen=True)
class MemoryLocation:
    """Human-facing memory-palace location metadata.

    This is intentionally metadata only: it does not change content identity,
    provenance, tier assignment, or vector dedup.
    """

    wing: str
    room: str
    closet: str | None = None
    drawer: str | None = None
    assigned_by: str = "auto"
    confidence: float = 1.0
    rule_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "wing", _clean_path_part(self.wing))
        object.__setattr__(self, "room", _clean_path_part(self.room))
        if self.closet is not None:
            object.__setattr__(self, "closet", _clean_path_part(self.closet))
        if self.drawer is not None:
            object.__setattr__(self, "drawer", _clean_path_part(self.drawer))
        object.__setattr__(self, "confidence", max(0.0, min(1.0, self.confidence)))

    @property
    def path(self) -> str:
        parts = [self.wing, self.room, self.closet, self.drawer]
        return "/".join(part for part in parts if part)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "palace_path": self.path,
            "palace_wing": self.wing,
            "palace_room": self.room,
            "palace_closet": self.closet or "",
            "palace_drawer": self.drawer or "",
            "palace_assigned_by": self.assigned_by,
            "palace_confidence": self.confidence,
            "palace_rule_id": self.rule_id,
        }

    @classmethod
    def from_path(
        cls,
        path: str,
        *,
        assigned_by: str = "auto",
        confidence: float = 1.0,
        rule_id: str = "",
    ) -> "MemoryLocation":
        parts = normalize_palace_path(path).split("/")
        if len(parts) < 2:
            raise ValueError("memory palace path must include at least wing/room")
        if len(parts) > 4:
            raise ValueError("memory palace path supports wing/room/closet/drawer")
        return cls(
            wing=parts[0],
            room=parts[1],
            closet=parts[2] if len(parts) > 2 else None,
            drawer=parts[3] if len(parts) > 3 else None,
            assigned_by=assigned_by,
            confidence=confidence,
            rule_id=rule_id,
        )


def palace_metadata_for(location: MemoryLocation | str | None) -> dict[str, Any]:
    if location is None:
        return {}
    if isinstance(location, MemoryLocation):
        return location.to_metadata()
    return MemoryLocation.from_path(location).to_metadata()


def metadata_matches_palace_path(metadata: dict[str, Any], palace_path: str) -> bool:
    wanted = normalize_palace_path(palace_path)
    actual = metadata.get("palace_path")
    if not isinstance(actual, str) or not actual:
        return False
    actual = normalize_palace_path(actual)
    return actual == wanted or actual.startswith(wanted + "/")


@dataclass
class MemoryRecord:
    """A single stored fact or knowledge item."""

    id: str
    content: str
    content_fi: str | None
    source: str
    confidence: float
    tags: list[str] = field(default_factory=list)
    agent_id: str | None = None
    created_at: float = 0.0
    ttl_seconds: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
