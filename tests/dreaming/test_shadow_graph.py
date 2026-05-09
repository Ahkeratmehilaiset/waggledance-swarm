# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.dreaming.shadow_graph."""
from __future__ import annotations

from waggledance.core.dreaming import DREAMING_SCHEMA_VERSION
from waggledance.core.dreaming.shadow_graph import (
    ShadowEdge,
    add_shadow_proposals,
    build_live_graph,
    diff_graphs,
    diff_to_dict,
    graph_to_dict,
)


def test_build_live_graph_sorts_nodes_and_filters_empty_solver_ids() -> None:
    graph = build_live_graph([
        {"solver_name": "solver_b", "cell_id": "cell:b"},
        {"solver_id": "solver_a", "cell_id": "cell:a"},
        {"solver_id": ""},
        {"cell_id": "missing"},
    ])

    assert graph.schema_version == DREAMING_SCHEMA_VERSION
    assert [n.solver_id for n in graph.nodes] == ["solver_a", "solver_b"]
    assert [n.source for n in graph.nodes] == ["library", "library"]
    assert graph.node_ids() == {"solver_a", "solver_b"}
    assert graph.shadow_node_ids() == set()


def test_build_live_graph_keeps_only_valid_relations_and_sorts_edges() -> None:
    graph = build_live_graph(
        [{"solver_id": "a"}, {"solver_id": "b"}, {"solver_id": "c"}],
        [
            {"solver_a": "c", "solver_b": "a", "relation_type": "bogus"},
            {"solver_a": "b", "solver_b": "c", "relation_type": "refines"},
            {"solver_a": "a", "solver_b": "b", "relation_type": "depends_on"},
        ],
    )

    assert [e.key() for e in graph.edges] == [
        ("a", "b", "depends_on"),
        ("b", "c", "refines"),
    ]
    assert graph.edge_keys() == {
        ("a", "b", "depends_on"),
        ("b", "c", "refines"),
    }


def test_add_shadow_proposals_skips_duplicate_solver_ids_and_uses_fallback_id() -> None:
    live = build_live_graph([{"solver_id": "live_solver", "cell_id": "cell:0"}])

    graph = add_shadow_proposals(
        live,
        [
            {"solver_name": "live_solver", "cell_id": "ignored"},
            {"solver_name": "shadow_b", "cell_id": "cell:b"},
            {"proposal_id": "proposal_c", "cell_id": "cell:c"},
            {"solver_name": ""},
        ],
    )

    assert [n.solver_id for n in graph.nodes] == [
        "live_solver",
        "proposal_c",
        "shadow_b",
    ]
    assert graph.shadow_node_ids() == {"proposal_c", "shadow_b"}


def test_add_shadow_proposals_deduplicates_edges_and_filters_invalid_relations() -> None:
    live = build_live_graph(
        [{"solver_id": "live"}, {"solver_id": "shadow"}],
        [{"solver_a": "live", "solver_b": "shadow", "relation_type": "depends_on"}],
    )

    graph = add_shadow_proposals(
        live,
        [{"solver_name": "new_shadow"}],
        [
            {"solver_a": "live", "solver_b": "shadow", "relation_type": "depends_on"},
            {"solver_a": "new_shadow", "solver_b": "live", "relation_type": "composes_with"},
            {"solver_a": "new_shadow", "solver_b": "live", "relation_type": "bogus"},
        ],
    )

    assert [e.key() for e in graph.edges] == [
        ("live", "shadow", "depends_on"),
        ("new_shadow", "live", "composes_with"),
    ]


def test_diff_graphs_classifies_new_nodes_edges_bridges_and_rescale() -> None:
    live = build_live_graph(
        [{"solver_id": "live_a", "cell_id": "cell:a"}],
        [{"solver_a": "live_a", "solver_b": "live_a", "relation_type": "depends_on"}],
    )
    shadow = add_shadow_proposals(
        live,
        [
            {"solver_name": "shadow_b", "cell_id": "cell:b"},
            {"solver_name": "shadow_c", "cell_id": "cell:c"},
        ],
        [
            {"solver_a": "shadow_b", "solver_b": "live_a", "relation_type": "composes_with"},
            {"solver_a": "shadow_c", "solver_b": "live_a", "relation_type": "refines"},
            {"solver_a": "live_a", "solver_b": "shadow_c", "relation_type": "depends_on"},
        ],
    )

    diff = diff_graphs(live, shadow, proposal_solver_hashes=["h2", "h1"])

    assert diff.new_nodes == ("shadow_b", "shadow_c")
    assert diff.new_structural_edges == (
        ("live_a", "shadow_c", "depends_on"),
        ("shadow_b", "live_a", "composes_with"),
        ("shadow_c", "live_a", "refines"),
    )
    assert diff.new_bridge_candidates == (("shadow_b", "live_a", "composes_with"),)
    assert diff.new_rescale_opportunities == (("shadow_c", "live_a", "refines"),)
    assert diff.affected_cells == ("cell:b", "cell:c")
    assert diff.proposal_solver_hashes == ("h1", "h2")


def test_diff_graphs_bridge_candidates_must_touch_shadow_nodes() -> None:
    live = build_live_graph([{"solver_id": "a"}, {"solver_id": "b"}])
    shadow = add_shadow_proposals(
        live,
        [{"solver_name": "shadow"}],
        [
            {"solver_a": "a", "solver_b": "b", "relation_type": "composes_with"},
            {"solver_a": "shadow", "solver_b": "a", "relation_type": "composes_with"},
        ],
    )

    diff = diff_graphs(live, shadow)

    assert ("a", "b", "composes_with") in diff.new_structural_edges
    assert diff.new_bridge_candidates == (("shadow", "a", "composes_with"),)


def test_graph_to_dict_and_diff_to_dict_emit_json_ready_shapes() -> None:
    live = build_live_graph([{"solver_id": "live"}])
    shadow = add_shadow_proposals(
        live,
        [{"solver_name": "shadow", "cell_id": "cell:s"}],
        [{"solver_a": "shadow", "solver_b": "live", "relation_type": "refines"}],
    )
    diff = diff_graphs(live, shadow)

    graph_dict = graph_to_dict(shadow)
    diff_dict = diff_to_dict(diff)

    assert graph_dict["nodes"] == [
        {"solver_id": "live", "is_live": True, "source": "library", "cell_id": None},
        {
            "solver_id": "shadow",
            "is_live": False,
            "source": "shadow_proposal",
            "cell_id": "cell:s",
        },
    ]
    assert graph_dict["edges"] == [
        {"solver_a": "shadow", "solver_b": "live", "relation_type": "refines"}
    ]
    assert diff_dict["new_structural_edges"] == [["shadow", "live", "refines"]]
    assert diff_dict["affected_cells"] == ["cell:s"]


def test_shadow_edge_key_is_stable_tuple_contract() -> None:
    edge = ShadowEdge("a", "b", "depends_on")

    assert edge.key() == ("a", "b", "depends_on")
