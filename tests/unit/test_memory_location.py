"""Unit tests for MemoryLocation metadata helpers."""

import pytest

from waggledance.core.domain.memory_record import (
    MemoryLocation,
    metadata_matches_palace_path,
    normalize_palace_path,
    palace_metadata_for,
)


def test_memory_location_builds_stable_path_and_metadata() -> None:
    loc = MemoryLocation(
        wing="systems",
        room="runtime",
        closet="routing",
        drawer="hex",
        assigned_by="rule",
        confidence=0.8,
        rule_id="hex-cell-rule",
    )

    assert loc.path == "systems/runtime/routing/hex"
    assert loc.to_metadata() == {
        "palace_path": "systems/runtime/routing/hex",
        "palace_wing": "systems",
        "palace_room": "runtime",
        "palace_closet": "routing",
        "palace_drawer": "hex",
        "palace_assigned_by": "rule",
        "palace_confidence": 0.8,
        "palace_rule_id": "hex-cell-rule",
    }


def test_memory_location_from_path_requires_wing_and_room() -> None:
    with pytest.raises(ValueError, match="wing/room"):
        MemoryLocation.from_path("systems")


def test_palace_metadata_for_accepts_string_path() -> None:
    metadata = palace_metadata_for(" systems / runtime ")

    assert metadata["palace_path"] == "systems/runtime"
    assert metadata["palace_wing"] == "systems"
    assert metadata["palace_room"] == "runtime"


def test_normalize_palace_path_rejects_empty_path() -> None:
    with pytest.raises(ValueError, match="at least one"):
        normalize_palace_path(" / ")


def test_metadata_matches_exact_room_or_descendant() -> None:
    metadata = {"palace_path": "systems/runtime/routing"}

    assert metadata_matches_palace_path(metadata, "systems/runtime")
    assert metadata_matches_palace_path(metadata, "systems/runtime/routing")
    assert not metadata_matches_palace_path(metadata, "systems/design")
