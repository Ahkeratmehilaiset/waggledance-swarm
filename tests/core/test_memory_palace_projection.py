import json
from pathlib import Path

import jsonschema
import pytest

from waggledance.core.memory_palace import (
    MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
    MemoryPalaceProjectionError,
    MemoryPlacement,
    PalaceNode,
    build_memory_palace_projection,
    derive_candidate_placements,
    validate_palace_hierarchy,
)


ROOT = Path(__file__).resolve().parents[2]


def test_projection_builds_authority_free_document() -> None:
    nodes = [
        PalaceNode(node_id="wing.energy", kind="wing", label="Energy"),
        PalaceNode(
            node_id="room.energy.solar",
            kind="room",
            label="Solar",
            parent_id="wing.energy",
            selectors={"tags": ["solar"], "cell_id": ["energy"]},
            source_refs=["vector:abc123"],
        ),
    ]
    placements = [
        MemoryPlacement(
            memory_id="mem-1",
            vector_node_id="vector-node-1",
            palace_node_id="room.energy.solar",
            confidence=0.75,
            placement_source="tag_selector",
            dedup_anchor="sha256:abc",
        )
    ]

    projection = build_memory_palace_projection(nodes, placements)

    assert projection["schema_version"] == MEMORY_PALACE_PROJECTION_SCHEMA_VERSION
    assert projection["source_of_truth"] == "projection_only"
    assert projection["runtime_authority"] is False
    assert projection["storage_write_authority"] is False
    assert projection["bridge_write_authority"] is False
    assert projection["gate_skip_authority"] is False
    assert projection["promotion_authority"] is False
    assert projection["nodes"][0]["node_id"] == "wing.energy"
    assert projection["nodes"][1]["node_id"] == "room.energy.solar"
    assert projection["placements"][0]["palace_node_id"] == "room.energy.solar"


def test_derives_candidates_from_existing_metadata_selectors() -> None:
    nodes = [
        PalaceNode(node_id="wing.thermal", kind="wing", label="Thermal"),
        PalaceNode(
            node_id="room.thermal.heatpump",
            kind="room",
            label="Heat pump",
            parent_id="wing.thermal",
            selectors={
                "tags": ["heatpump"],
                "cell_id": ["thermal"],
                "capsule_context": ["home"],
            },
        ),
    ]

    placements = derive_candidate_placements(
        memory_id="memory-thermal-1",
        vector_node_id="node-thermal-1",
        dedup_anchor="node-thermal-1",
        metadata={
            "tags": ["heatpump", "hvac"],
            "cell_id": "thermal",
            "capsule_context": "home",
        },
        nodes=nodes,
    )

    assert len(placements) == 1
    assert placements[0].palace_node_id == "room.thermal.heatpump"
    assert placements[0].placement_source == "tag_selector"
    assert placements[0].confidence == pytest.approx(0.8)
    assert placements[0].metadata["matched_selector_keys"] == (
        "capsule_context",
        "cell_id",
        "tags",
    )


def test_hierarchy_rejects_unknown_parent_and_cycles() -> None:
    with pytest.raises(MemoryPalaceProjectionError, match="unknown parent"):
        validate_palace_hierarchy(
            [
                PalaceNode(
                    node_id="room.orphan",
                    kind="room",
                    label="Orphan",
                    parent_id="wing.missing",
                )
            ]
        )

    with pytest.raises(MemoryPalaceProjectionError, match="cycle detected"):
        validate_palace_hierarchy(
            [
                PalaceNode(
                    node_id="hall.loop.a",
                    kind="hall",
                    label="A",
                    parent_id="hall.loop.b",
                ),
                PalaceNode(
                    node_id="hall.loop.b",
                    kind="hall",
                    label="B",
                    parent_id="hall.loop.a",
                ),
            ]
        )


def test_projection_rejects_authority_flags_and_unknown_placements() -> None:
    with pytest.raises(MemoryPalaceProjectionError, match="authority flag"):
        build_memory_palace_projection(
            [
                PalaceNode(
                    node_id="wing.ops",
                    kind="wing",
                    label="Ops",
                    metadata={"runtime_authority": True},
                )
            ]
        )

    with pytest.raises(MemoryPalaceProjectionError, match="unknown palace node"):
        build_memory_palace_projection(
            [PalaceNode(node_id="wing.ops", kind="wing", label="Ops")],
            [
                MemoryPlacement(
                    memory_id="mem-1",
                    palace_node_id="room.missing",
                    confidence=0.5,
                    placement_source="manual",
                )
            ],
        )


def test_schema_contract_declares_projection_only_authority_flags() -> None:
    schema_path = ROOT / "schemas" / "memory_palace_projection.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["properties"]["schema_version"]["const"] == (
        MEMORY_PALACE_PROJECTION_SCHEMA_VERSION
    )
    assert schema["properties"]["source_of_truth"]["const"] == "projection_only"
    for field in (
        "runtime_authority",
        "storage_write_authority",
        "bridge_write_authority",
        "gate_skip_authority",
        "promotion_authority",
    ):
        assert schema["properties"][field]["const"] is False


def test_projection_document_validates_against_schema() -> None:
    schema_path = ROOT / "schemas" / "memory_palace_projection.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    projection = build_memory_palace_projection(
        [
            PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
            PalaceNode(
                node_id="room.learning.cases",
                kind="room",
                label="Cases",
                parent_id="wing.learning",
                selectors={"vector_kind": ["claim"]},
            ),
        ],
        [
            MemoryPlacement(
                memory_id="memory-1",
                vector_node_id="vector-1",
                palace_node_id="room.learning.cases",
                confidence=0.6,
                placement_source="vector_kind",
            )
        ],
    )

    jsonschema.Draft7Validator(schema).validate(projection)
