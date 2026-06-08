import json
from pathlib import Path

import jsonschema
import pytest

from waggledance.core.memory_palace import (
    MEMORY_PALACE_NAVIGATION_INDEX_SCHEMA_VERSION,
    MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
    MemoryPalaceProjectionError,
    MemoryPlacement,
    PalaceShortcutHint,
    PalaceNode,
    build_memory_palace_navigation_index,
    build_memory_palace_projection,
    derive_candidate_placements,
    derive_shortcut_hints,
    rank_shortcut_candidates_for_metadata,
    rank_shortcut_candidates_for_memory,
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
    assert projection["shortcuts"] == []


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


def test_derives_cross_wing_shortcut_hints_from_shared_selectors() -> None:
    nodes = [
        PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
        PalaceNode(
            node_id="room.learning.imaging",
            kind="room",
            label="Imaging cases",
            parent_id="wing.learning",
            selectors={
                "tags": ["segmentation", "cell_imaging"],
                "vector_kind": ["claim"],
            },
        ),
        PalaceNode(node_id="wing.research", kind="wing", label="Research"),
        PalaceNode(
            node_id="room.research.pathology",
            kind="room",
            label="Pathology expertise",
            parent_id="wing.research",
            selectors={
                "tags": ["segmentation", "pathology"],
                "vector_kind": ["claim"],
            },
        ),
    ]

    shortcuts = derive_shortcut_hints(nodes, min_hierarchy_hops=3)
    projection = build_memory_palace_projection(nodes, shortcuts=shortcuts)

    selected = [
        shortcut
        for shortcut in projection["shortcuts"]
        if shortcut["source_node_id"] == "room.learning.imaging"
        and shortcut["target_node_id"] == "room.research.pathology"
    ]
    assert len(selected) == 1
    hint = selected[0]
    assert hint["matched_selector_keys"] == ["tags", "vector_kind"]
    assert hint["matched_values"] == {
        "tags": ["segmentation"],
        "vector_kind": ["claim"],
    }
    assert hint["hierarchy_hops"] == 3
    assert hint["no_runtime_mutation"] is True
    assert hint["runtime_authority"] is False
    assert hint["gate_skip_authority"] is False
    assert hint["promotion_authority"] is False
    assert hint["solver_call_authority"] is False


def test_builds_navigation_index_from_validated_hierarchy() -> None:
    nodes = [
        PalaceNode(
            node_id="room.research.pathology",
            kind="room",
            label="Pathology expertise",
            parent_id="hall.research.methods",
        ),
        PalaceNode(node_id="wing.system", kind="wing", label="System"),
        PalaceNode(node_id="wing.research", kind="wing", label="Research"),
        PalaceNode(
            node_id="hall.research.methods",
            kind="hall",
            label="Research methods",
            parent_id="wing.research",
        ),
    ]

    index = build_memory_palace_navigation_index(nodes)

    assert index["schema_version"] == MEMORY_PALACE_NAVIGATION_INDEX_SCHEMA_VERSION
    assert index["source_of_truth"] == "projection_only"
    for field in (
        "runtime_authority",
        "storage_write_authority",
        "bridge_write_authority",
        "gate_skip_authority",
        "promotion_authority",
        "solver_call_authority",
    ):
        assert index[field] is False
    assert index["node_count"] == 4
    assert index["root_node_ids"] == ["wing.research", "wing.system"]
    assert index["max_depth"] == 2
    assert index["paths_by_node"]["room.research.pathology"] == [
        "wing.research",
        "hall.research.methods",
        "room.research.pathology",
    ]
    assert index["children_by_node"]["wing.research"] == [
        "hall.research.methods"
    ]
    assert index["children_by_node"]["wing.system"] == []
    pathology = next(
        node for node in index["nodes"]
        if node["node_id"] == "room.research.pathology"
    )
    assert pathology == {
        "node_id": "room.research.pathology",
        "kind": "room",
        "label": "Pathology expertise",
        "parent_id": "hall.research.methods",
        "depth": 2,
        "path_node_ids": [
            "wing.research",
            "hall.research.methods",
            "room.research.pathology",
        ],
        "path_labels": [
            "Research",
            "Research methods",
            "Pathology expertise",
        ],
        "child_node_ids": [],
    }


def test_navigation_index_reuses_authority_free_node_validation() -> None:
    with pytest.raises(MemoryPalaceProjectionError, match="authority flag"):
        build_memory_palace_navigation_index(
            [
                PalaceNode(
                    node_id="wing.ops",
                    kind="wing",
                    label="Ops",
                    metadata={"runtime_authority": False},
                )
            ]
        )


def test_ranks_read_side_shortcut_candidates_for_memory() -> None:
    nodes = [
        PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
        PalaceNode(
            node_id="room.learning.imaging",
            kind="room",
            label="Imaging cases",
            parent_id="wing.learning",
        ),
        PalaceNode(node_id="wing.research", kind="wing", label="Research"),
        PalaceNode(
            node_id="room.research.pathology",
            kind="room",
            label="Pathology expertise",
            parent_id="wing.research",
        ),
        PalaceNode(node_id="wing.system", kind="wing", label="System"),
        PalaceNode(
            node_id="room.system.statistics",
            kind="room",
            label="Statistics expertise",
            parent_id="wing.system",
        ),
    ]
    projection = build_memory_palace_projection(
        nodes,
        placements=[
            MemoryPlacement(
                memory_id="memory-imaging-1",
                palace_node_id="room.learning.imaging",
                confidence=0.8,
                placement_source="manual",
            )
        ],
        shortcuts=[
            PalaceShortcutHint(
                shortcut_id="shortcut.imaging.to.pathology",
                source_node_id="room.learning.imaging",
                target_node_id="room.research.pathology",
                matched_selector_keys=["tags", "vector_kind"],
                matched_values={
                    "tags": ["segmentation"],
                    "vector_kind": ["claim"],
                },
                confidence=0.9,
                hierarchy_hops=3,
            ),
            PalaceShortcutHint(
                shortcut_id="shortcut.imaging.to.statistics",
                source_node_id="room.learning.imaging",
                target_node_id="room.system.statistics",
                matched_selector_keys=["tags"],
                matched_values={"tags": ["segmentation"]},
                confidence=0.7,
                hierarchy_hops=3,
            ),
        ],
    )

    candidates = rank_shortcut_candidates_for_memory(
        projection,
        "memory-imaging-1",
    )

    assert [candidate["target_node_id"] for candidate in candidates] == [
        "room.research.pathology",
        "room.system.statistics",
    ]
    assert candidates[0]["rank_score"] == pytest.approx(0.72)
    assert candidates[0]["placement_confidence"] == pytest.approx(0.8)
    assert candidates[0]["shortcut_confidence"] == pytest.approx(0.9)
    assert candidates[0]["matched_selector_keys"] == ["tags", "vector_kind"]
    assert candidates[0]["no_runtime_mutation"] is True
    assert candidates[0]["runtime_authority"] is False
    assert candidates[0]["storage_write_authority"] is False
    assert candidates[0]["bridge_write_authority"] is False
    assert candidates[0]["gate_skip_authority"] is False
    assert candidates[0]["promotion_authority"] is False
    assert candidates[0]["solver_call_authority"] is False


def test_shortcut_candidate_ranking_filters_without_runtime_authority() -> None:
    projection = build_memory_palace_projection(
        [
            PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
            PalaceNode(
                node_id="room.learning.imaging",
                kind="room",
                label="Imaging cases",
                parent_id="wing.learning",
            ),
            PalaceNode(node_id="wing.research", kind="wing", label="Research"),
            PalaceNode(
                node_id="room.research.pathology",
                kind="room",
                label="Pathology expertise",
                parent_id="wing.research",
            ),
        ],
        placements=[
            MemoryPlacement(
                memory_id="memory-imaging-1",
                palace_node_id="room.learning.imaging",
                confidence=0.5,
                placement_source="manual",
            )
        ],
        shortcuts=[
            PalaceShortcutHint(
                shortcut_id="shortcut.imaging.to.pathology",
                source_node_id="room.learning.imaging",
                target_node_id="room.research.pathology",
                matched_selector_keys=["tags"],
                matched_values={"tags": ["segmentation"]},
                confidence=0.8,
                hierarchy_hops=3,
            )
        ],
    )

    assert rank_shortcut_candidates_for_memory(
        projection,
        "memory-imaging-1",
        min_rank_score=0.5,
    ) == ()
    assert rank_shortcut_candidates_for_memory(
        projection,
        "memory-missing",
    ) == ()

    with pytest.raises(MemoryPalaceProjectionError, match="max_candidates"):
        rank_shortcut_candidates_for_memory(
            projection,
            "memory-imaging-1",
            max_candidates=0,
        )


def test_shortcut_candidate_ranking_fails_closed_on_authority_flags() -> None:
    projection = build_memory_palace_projection(
        [
            PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
            PalaceNode(
                node_id="room.learning.imaging",
                kind="room",
                label="Imaging cases",
                parent_id="wing.learning",
            ),
            PalaceNode(node_id="wing.research", kind="wing", label="Research"),
            PalaceNode(
                node_id="room.research.pathology",
                kind="room",
                label="Pathology expertise",
                parent_id="wing.research",
            ),
        ],
        placements=[
            MemoryPlacement(
                memory_id="memory-imaging-1",
                palace_node_id="room.learning.imaging",
                confidence=0.8,
                placement_source="manual",
            )
        ],
        shortcuts=[
            PalaceShortcutHint(
                shortcut_id="shortcut.imaging.to.pathology",
                source_node_id="room.learning.imaging",
                target_node_id="room.research.pathology",
                matched_selector_keys=["tags"],
                matched_values={"tags": ["segmentation"]},
                confidence=0.9,
                hierarchy_hops=3,
            )
        ],
    )

    unsafe_projection = dict(projection, runtime_authority=True)
    with pytest.raises(MemoryPalaceProjectionError, match="runtime_authority"):
        rank_shortcut_candidates_for_memory(
            unsafe_projection,
            "memory-imaging-1",
        )

    unsafe_projection = dict(projection)
    unsafe_projection["shortcuts"] = [
        dict(projection["shortcuts"][0], solver_call_authority=True)
    ]
    with pytest.raises(MemoryPalaceProjectionError, match="solver_call_authority"):
        rank_shortcut_candidates_for_memory(
            unsafe_projection,
            "memory-imaging-1",
        )


def test_shortcut_candidate_ranking_validates_mutated_projection_refs() -> None:
    projection = build_memory_palace_projection(
        [
            PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
            PalaceNode(
                node_id="room.learning.imaging",
                kind="room",
                label="Imaging cases",
                parent_id="wing.learning",
            ),
            PalaceNode(node_id="wing.research", kind="wing", label="Research"),
            PalaceNode(
                node_id="room.research.pathology",
                kind="room",
                label="Pathology expertise",
                parent_id="wing.research",
            ),
        ],
        placements=[
            MemoryPlacement(
                memory_id="memory-imaging-1",
                palace_node_id="room.learning.imaging",
                confidence=0.8,
                placement_source="manual",
            )
        ],
        shortcuts=[
            PalaceShortcutHint(
                shortcut_id="shortcut.imaging.to.pathology",
                source_node_id="room.learning.imaging",
                target_node_id="room.research.pathology",
                matched_selector_keys=["tags"],
                matched_values={"tags": ["segmentation"]},
                confidence=0.9,
                hierarchy_hops=3,
            )
        ],
    )

    unknown_target = dict(projection)
    unknown_target["shortcuts"] = [
        dict(projection["shortcuts"][0], target_node_id="room.unknown.injected")
    ]
    with pytest.raises(MemoryPalaceProjectionError, match="unknown target_node_id"):
        rank_shortcut_candidates_for_memory(
            unknown_target,
            "memory-imaging-1",
        )

    unknown_placement = dict(projection)
    unknown_placement["placements"] = [
        dict(projection["placements"][0], palace_node_id="room.unknown.injected")
    ]
    with pytest.raises(MemoryPalaceProjectionError, match="unknown palace node"):
        rank_shortcut_candidates_for_memory(
            unknown_placement,
            "memory-imaging-1",
        )

    duplicate_shortcut = dict(projection)
    duplicate_shortcut["shortcuts"] = [
        dict(projection["shortcuts"][0]),
        dict(projection["shortcuts"][0]),
    ]
    with pytest.raises(MemoryPalaceProjectionError, match="duplicate shortcut_id"):
        rank_shortcut_candidates_for_memory(
            duplicate_shortcut,
            "memory-imaging-1",
        )


def test_metadata_shortcut_ranking_builds_product_substrate_candidates() -> None:
    nodes = [
        PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
        PalaceNode(
            node_id="room.learning.imaging",
            kind="room",
            label="Imaging cases",
            parent_id="wing.learning",
            selectors={
                "tags": ["segmentation", "cell_imaging"],
                "vector_kind": ["claim"],
                "capsule_context": ["research"],
            },
        ),
        PalaceNode(node_id="wing.research", kind="wing", label="Research"),
        PalaceNode(
            node_id="room.research.pathology",
            kind="room",
            label="Pathology expertise",
            parent_id="wing.research",
            selectors={
                "tags": ["segmentation", "pathology"],
                "vector_kind": ["claim"],
                "capsule_context": ["research"],
            },
        ),
        PalaceNode(node_id="wing.system", kind="wing", label="System"),
        PalaceNode(
            node_id="room.system.statistics",
            kind="room",
            label="Statistics expertise",
            parent_id="wing.system",
            selectors={"tags": ["segmentation"]},
        ),
    ]

    candidates = rank_shortcut_candidates_for_metadata(
        memory_id="memory-imaging-liveish-1",
        source_node_id="room.learning.imaging",
        vector_node_id="vector-imaging-liveish-1",
        dedup_anchor="sha256:imaging-liveish-1",
        metadata={
            "tags": ["segmentation", "cell_imaging"],
            "vector_kind": "claim",
            "capsule_context": "research",
        },
        nodes=nodes,
        min_hierarchy_hops=3,
    )

    assert [candidate["target_node_id"] for candidate in candidates] == [
        "room.research.pathology",
        "room.system.statistics",
    ]
    assert candidates[0]["memory_id"] == "memory-imaging-liveish-1"
    assert candidates[0]["source_node_id"] == "room.learning.imaging"
    assert candidates[0]["rank_score"] > candidates[1]["rank_score"]
    assert candidates[0]["placement_confidence"] == pytest.approx(0.8)
    assert candidates[0]["shortcut_confidence"] > candidates[1]["shortcut_confidence"]
    assert candidates[0]["matched_selector_keys"] == [
        "capsule_context",
        "tags",
        "vector_kind",
    ]
    assert candidates[0]["no_runtime_mutation"] is True
    assert candidates[0]["runtime_authority"] is False
    assert candidates[0]["storage_write_authority"] is False
    assert candidates[0]["bridge_write_authority"] is False
    assert candidates[0]["gate_skip_authority"] is False
    assert candidates[0]["promotion_authority"] is False
    assert candidates[0]["solver_call_authority"] is False


def test_metadata_shortcut_ranking_returns_empty_without_matching_placement() -> None:
    nodes = [
        PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
        PalaceNode(
            node_id="room.learning.imaging",
            kind="room",
            label="Imaging cases",
            parent_id="wing.learning",
            selectors={"tags": ["cell_imaging"]},
        ),
        PalaceNode(node_id="wing.research", kind="wing", label="Research"),
        PalaceNode(
            node_id="room.research.pathology",
            kind="room",
            label="Pathology expertise",
            parent_id="wing.research",
            selectors={"tags": ["pathology"]},
        ),
    ]

    assert (
        rank_shortcut_candidates_for_metadata(
            memory_id="memory-no-match",
            metadata={"tags": ["thermodynamics"]},
            nodes=nodes,
        )
        == ()
    )


def test_metadata_shortcut_ranking_fails_closed_on_unsafe_inputs() -> None:
    nodes = [
        PalaceNode(node_id="wing.learning", kind="wing", label="Learning"),
        PalaceNode(
            node_id="room.learning.imaging",
            kind="room",
            label="Imaging cases",
            parent_id="wing.learning",
            selectors={"tags": ["cell_imaging"]},
        ),
        PalaceNode(node_id="wing.research", kind="wing", label="Research"),
        PalaceNode(
            node_id="room.research.pathology",
            kind="room",
            label="Pathology expertise",
            parent_id="wing.research",
            selectors={"tags": ["cell_imaging"]},
        ),
    ]

    with pytest.raises(MemoryPalaceProjectionError, match="metadata"):
        rank_shortcut_candidates_for_metadata(
            memory_id="memory-unsafe",
            metadata=["cell_imaging"],
            nodes=nodes,
        )

    with pytest.raises(MemoryPalaceProjectionError, match="memory_id"):
        rank_shortcut_candidates_for_metadata(
            memory_id="../../etc/passwd\n",
            metadata={"tags": ["cell_imaging"]},
            nodes=nodes,
        )

    with pytest.raises(MemoryPalaceProjectionError, match="max_candidates"):
        rank_shortcut_candidates_for_metadata(
            memory_id="memory-unsafe",
            source_node_id="room.learning.imaging",
            metadata={"tags": ["cell_imaging"]},
            nodes=nodes,
            max_candidates=0,
        )

    with pytest.raises(MemoryPalaceProjectionError, match="unknown palace node"):
        rank_shortcut_candidates_for_metadata(
            memory_id="memory-unsafe",
            source_node_id="room.unknown.injected",
            metadata={"tags": ["cell_imaging"]},
            nodes=nodes,
        )

    with pytest.raises(MemoryPalaceProjectionError, match="authority flag"):
        rank_shortcut_candidates_for_metadata(
            memory_id="memory-unsafe",
            metadata={"tags": ["cell_imaging"]},
            nodes=[
                PalaceNode(
                    node_id="wing.learning",
                    kind="wing",
                    label="Learning",
                    metadata={"runtime_authority": False},
                ),
                PalaceNode(
                    node_id="room.learning.imaging",
                    kind="room",
                    label="Imaging cases",
                    parent_id="wing.learning",
                    selectors={"tags": ["cell_imaging"]},
                ),
            ],
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

    with pytest.raises(MemoryPalaceProjectionError, match="unknown target_node_id"):
        build_memory_palace_projection(
            [PalaceNode(node_id="wing.ops", kind="wing", label="Ops")],
            shortcuts=[
                PalaceShortcutHint(
                    shortcut_id="shortcut.deadbeef",
                    source_node_id="wing.ops",
                    target_node_id="room.missing",
                    matched_selector_keys=["tags"],
                    matched_values={"tags": ["ops"]},
                    confidence=0.7,
                    hierarchy_hops=2,
                )
            ],
        )


def test_projection_rejects_authority_flag_keys_at_any_depth() -> None:
    for metadata in (
        {"runtime_authority": "true"},
        {"runtime_authority": 1},
        {"runtime_authority": False},
        {"nested": {"gate_skip_authority": True}},
        {"items": [{"bridge_write_authority": "false"}]},
    ):
        with pytest.raises(MemoryPalaceProjectionError, match="authority flag"):
            build_memory_palace_projection(
                [
                    PalaceNode(
                        node_id="wing.ops",
                        kind="wing",
                        label="Ops",
                        metadata=metadata,
                    )
                ]
            )

    for metadata in (
        {"gate_skip_authority": "true"},
        {"promotion_authority": 0},
        {"solver_call_authority": False},
        {"nested": {"storage_write_authority": False}},
        {"items": [{"runtime_authority": 1}]},
    ):
        with pytest.raises(MemoryPalaceProjectionError, match="authority flag"):
            build_memory_palace_projection(
                [PalaceNode(node_id="wing.ops", kind="wing", label="Ops")],
                [
                    MemoryPlacement(
                        memory_id="mem-1",
                        palace_node_id="wing.ops",
                        confidence=0.5,
                        placement_source="manual",
                        metadata=metadata,
                    )
                ],
            )

    with pytest.raises(MemoryPalaceProjectionError, match="gate_skip_authority"):
        build_memory_palace_projection(
            [
                PalaceNode(node_id="wing.ops", kind="wing", label="Ops"),
                PalaceNode(node_id="wing.research", kind="wing", label="Research"),
            ],
            shortcuts=[
                PalaceShortcutHint(
                    shortcut_id="shortcut.unsafe",
                    source_node_id="wing.ops",
                    target_node_id="wing.research",
                    matched_selector_keys=["tags"],
                    matched_values={"tags": ["ops"]},
                    confidence=0.7,
                    hierarchy_hops=1,
                    gate_skip_authority=True,
                )
            ],
        )

    with pytest.raises(MemoryPalaceProjectionError, match="unlisted selector key"):
        build_memory_palace_projection(
            [
                PalaceNode(node_id="wing.ops", kind="wing", label="Ops"),
                PalaceNode(node_id="wing.research", kind="wing", label="Research"),
            ],
            shortcuts=[
                PalaceShortcutHint(
                    shortcut_id="shortcut.mismatch",
                    source_node_id="wing.ops",
                    target_node_id="wing.research",
                    matched_selector_keys=["tags"],
                    matched_values={"tags": ["ops"], "vector_kind": ["claim"]},
                    confidence=0.7,
                    hierarchy_hops=1,
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
        shortcuts=[
            PalaceShortcutHint(
                shortcut_id="shortcut.learning.to.ops",
                source_node_id="wing.learning",
                target_node_id="room.learning.cases",
                matched_selector_keys=["vector_kind"],
                matched_values={"vector_kind": ["claim"]},
                confidence=0.6,
                hierarchy_hops=1,
            )
        ],
    )

    jsonschema.Draft7Validator(schema).validate(projection)


def test_schema_rejects_authority_flag_keys_at_any_metadata_depth() -> None:
    schema_path = ROOT / "schemas" / "memory_palace_projection.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)

    projection = build_memory_palace_projection(
        [
            PalaceNode(node_id="wing.ops", kind="wing", label="Ops"),
            PalaceNode(node_id="wing.research", kind="wing", label="Research"),
        ],
        [
            MemoryPlacement(
                memory_id="memory-1",
                palace_node_id="wing.ops",
                confidence=0.6,
                placement_source="manual",
            )
        ],
        shortcuts=[
            PalaceShortcutHint(
                shortcut_id="shortcut.ops.to.research",
                source_node_id="wing.ops",
                target_node_id="wing.research",
                matched_selector_keys=["tags"],
                matched_values={"tags": ["ops"]},
                confidence=0.6,
                hierarchy_hops=1,
            )
        ],
    )

    for metadata in (
        {"runtime_authority": False},
        {"runtime_authority": "true"},
        {"nested": {"gate_skip_authority": True}},
    ):
        document = dict(projection)
        document["nodes"] = [dict(projection["nodes"][0], metadata=metadata)]
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(document)

    for metadata in (
        {"gate_skip_authority": "true"},
        {"nested": {"promotion_authority": 1}},
        {"nested": {"solver_call_authority": 0}},
    ):
        document = dict(projection)
        document["placements"] = [
            dict(projection["placements"][0], metadata=metadata)
        ]
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(document)

    shortcut_document = dict(projection)
    shortcut_document["shortcuts"] = [
        dict(
            projection["shortcuts"][0],
            gate_skip_authority=True,
        )
    ]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(shortcut_document)
