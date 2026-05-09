# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.vector_identity.vector_provenance_graph.

Iteration N+2 fifth scout pick. The provenance graph is the
content-addressed substrate underneath all of WD's local memory:
every ingested artifact (file, vector, mentor pack, URL, linked DB)
becomes a vector_node with a content hash, lineage edges, identity
anchor status, and capsule_context. The dedup_pipeline tests in
tests/vector_identity/test_ingestion_dedup.py pin the dedup
contract; this file pins the graph store underneath.

Pinned invariants (Phase 9 §H):

- LineageEdge.__post_init__ rejects unknown relation values; only
  LINEAGE_RELATIONS pass.
- VectorNode.__post_init__ rejects unknown kind, unknown
  anchor_status, and ingested_via outside {"copy_mode","link_mode"}.
- compute_node_id is deterministic on (content_sha, kind,
  capsule_context); changing any one of the three changes the id.
  Output is exactly 12 hex chars.
- make_node populates content_sha256 from sha256(content_bytes)
  prefixed with "sha256:" and node_id from compute_node_id.
- VectorProvenanceGraph.add_node returns (self, True) on first
  insert and (self, False) on duplicate node_id (idempotent).
- add_lineage returns False when source node missing; True on
  successful add; False on (target, relation) duplicate.
- by_capsule filters on capsule_context only.
- to_dict carries schema_version, sorted nodes by id, and round-
  trips VectorNode.to_dict for each node.
"""
from __future__ import annotations

import hashlib

import pytest

from waggledance.core.vector_identity.vector_provenance_graph import (
    LineageEdge,
    VectorNode,
    VectorProvenanceGraph,
    compute_node_id,
    make_node,
)


# --- LineageEdge.__post_init__ ------------------------------------

def test_lineage_edge_accepts_known_relations():
    edge = LineageEdge(target_node_id="t1", relation="supports", confidence=0.9)
    assert edge.target_node_id == "t1"
    assert edge.relation == "supports"
    assert edge.confidence == 0.9


def test_lineage_edge_rejects_unknown_relation():
    with pytest.raises(ValueError) as ei:
        LineageEdge(target_node_id="t1", relation="not_a_real_relation")
    assert "unknown lineage relation" in str(ei.value)


def test_lineage_edge_to_dict_round_trips_fields():
    edge = LineageEdge(target_node_id="t1", relation="contradicts",
                          confidence=0.5)
    d = edge.to_dict()
    assert d == {"target_node_id": "t1", "relation": "contradicts",
                  "confidence": 0.5}


def test_lineage_edge_default_confidence_is_one():
    edge = LineageEdge(target_node_id="t1", relation="extends")
    assert edge.confidence == 1.0


# --- VectorNode.__post_init__ ------------------------------------

def test_vector_node_rejects_unknown_kind():
    with pytest.raises(ValueError) as ei:
        VectorNode(
            schema_version=1, node_id="n", content_sha256="x",
            kind="not_a_kind", anchor_status="candidate",
            capsule_context="c", source="s", source_kind="sk",
            ingested_via="copy_mode", external_path=None,
            fixture_fallback_used=False, ingested_at_tick=0,
            lineage=(),
        )
    assert "unknown node kind" in str(ei.value)


def test_vector_node_rejects_unknown_anchor_status():
    with pytest.raises(ValueError) as ei:
        VectorNode(
            schema_version=1, node_id="n", content_sha256="x",
            kind="claim", anchor_status="not_a_status",
            capsule_context="c", source="s", source_kind="sk",
            ingested_via="copy_mode", external_path=None,
            fixture_fallback_used=False, ingested_at_tick=0,
            lineage=(),
        )
    assert "unknown anchor_status" in str(ei.value)


def test_vector_node_rejects_invalid_ingested_via():
    with pytest.raises(ValueError) as ei:
        VectorNode(
            schema_version=1, node_id="n", content_sha256="x",
            kind="claim", anchor_status="candidate",
            capsule_context="c", source="s", source_kind="sk",
            ingested_via="something_else", external_path=None,
            fixture_fallback_used=False, ingested_at_tick=0,
            lineage=(),
        )
    assert "ingested_via must be copy_mode or link_mode" in str(ei.value)


def test_vector_node_to_dict_carries_provenance_block():
    node = VectorNode(
        schema_version=1, node_id="n", content_sha256="sha256:abc",
        kind="claim", anchor_status="foundational",
        capsule_context="capsule:test", source="s", source_kind="sk",
        ingested_via="link_mode",
        external_path="/tmp/x.txt",
        fixture_fallback_used=True, ingested_at_tick=42,
        lineage=(LineageEdge(target_node_id="t1", relation="supports"),),
        tags=("foo", "bar"),
    )
    d = node.to_dict()
    assert d["node_id"] == "n"
    assert d["content_sha256"] == "sha256:abc"
    assert d["anchor_status"] == "foundational"
    assert d["capsule_context"] == "capsule:test"
    assert d["provenance"] == {
        "source": "s", "source_kind": "sk",
        "ingested_via": "link_mode",
        "external_path": "/tmp/x.txt",
        "fixture_fallback_used": True,
    }
    assert d["lineage"] == [
        {"target_node_id": "t1", "relation": "supports", "confidence": 1.0},
    ]
    assert d["tags"] == ["foo", "bar"]


# --- compute_node_id ----------------------------------------------

def test_compute_node_id_is_deterministic_on_same_inputs():
    a = compute_node_id("sha256:abc", "claim", "capsule:A")
    b = compute_node_id("sha256:abc", "claim", "capsule:A")
    assert a == b
    assert len(a) == 12


def test_compute_node_id_changes_when_content_sha_changes():
    a = compute_node_id("sha256:aaa", "claim", "capsule:A")
    b = compute_node_id("sha256:bbb", "claim", "capsule:A")
    assert a != b


def test_compute_node_id_changes_when_kind_changes():
    a = compute_node_id("sha256:abc", "claim", "capsule:A")
    b = compute_node_id("sha256:abc", "event", "capsule:A")
    assert a != b


def test_compute_node_id_changes_when_capsule_changes():
    """Same content in different capsules MUST produce different
    ids — capsules are isolation boundaries for the dedup logic."""
    a = compute_node_id("sha256:abc", "claim", "capsule:A")
    b = compute_node_id("sha256:abc", "claim", "capsule:B")
    assert a != b


# --- make_node helper --------------------------------------------

def test_make_node_computes_content_sha_with_prefix():
    node = make_node(
        content_bytes=b"hello world",
        kind="claim", source="test", source_kind="document",
        ingested_via="copy_mode",
    )
    expected_hash = hashlib.sha256(b"hello world").hexdigest()
    assert node.content_sha256 == "sha256:" + expected_hash


def test_make_node_node_id_matches_compute_node_id():
    node = make_node(
        content_bytes=b"x", kind="claim", source="test",
        source_kind="document", ingested_via="copy_mode",
        capsule_context="capsule:Q",
    )
    assert node.node_id == compute_node_id(
        node.content_sha256, "claim", "capsule:Q",
    )


def test_make_node_defaults_capsule_anchor_and_lineage():
    node = make_node(
        content_bytes=b"x", kind="claim", source="t",
        source_kind="d", ingested_via="copy_mode",
    )
    assert node.capsule_context == "neutral_v1"
    assert node.anchor_status == "candidate"
    assert node.lineage == ()
    assert node.tags == ()
    assert node.fixture_fallback_used is False
    assert node.ingested_at_tick == 0


def test_make_node_preserves_explicit_overrides():
    node = make_node(
        content_bytes=b"y", kind="event", source="t",
        source_kind="d", ingested_via="link_mode",
        capsule_context="capsule:Z", anchor_status="supportive",
        external_path="/etc/x", ingested_at_tick=7,
        tags=["t1", "t2"], fixture_fallback_used=True,
    )
    assert node.kind == "event"
    assert node.ingested_via == "link_mode"
    assert node.capsule_context == "capsule:Z"
    assert node.anchor_status == "supportive"
    assert node.external_path == "/etc/x"
    assert node.ingested_at_tick == 7
    assert node.tags == ("t1", "t2")
    assert node.fixture_fallback_used is True


# --- VectorProvenanceGraph.add_node ------------------------------

def test_add_node_inserts_new_returns_self_and_true():
    g = VectorProvenanceGraph()
    node = make_node(content_bytes=b"x", kind="claim", source="t",
                          source_kind="d", ingested_via="copy_mode")
    g2, was_new = g.add_node(node)
    assert g2 is g
    assert was_new is True
    assert node.node_id in g.nodes


def test_add_node_duplicate_returns_self_and_false():
    """add_node is idempotent: same node added twice returns
    (self, False) the second time and does NOT replace the existing
    node."""
    g = VectorProvenanceGraph()
    node = make_node(content_bytes=b"x", kind="claim", source="t",
                          source_kind="d", ingested_via="copy_mode")
    g.add_node(node)
    g2, was_new = g.add_node(node)
    assert g2 is g
    assert was_new is False
    assert len(g.nodes) == 1


# --- VectorProvenanceGraph.add_lineage ---------------------------

def test_add_lineage_returns_false_when_source_missing():
    g = VectorProvenanceGraph()
    edge = LineageEdge(target_node_id="t1", relation="supports")
    result = g.add_lineage("missing-source", edge)
    assert result is False


def test_add_lineage_appends_edge_to_node_and_returns_true():
    g = VectorProvenanceGraph()
    src = make_node(content_bytes=b"x", kind="claim", source="t",
                       source_kind="d", ingested_via="copy_mode")
    g.add_node(src)
    edge = LineageEdge(target_node_id="t1", relation="supports")
    result = g.add_lineage(src.node_id, edge)
    assert result is True
    updated = g.nodes[src.node_id]
    assert updated.lineage == (edge,)


def test_add_lineage_dedup_on_same_target_and_relation():
    g = VectorProvenanceGraph()
    src = make_node(content_bytes=b"x", kind="claim", source="t",
                       source_kind="d", ingested_via="copy_mode")
    g.add_node(src)
    edge1 = LineageEdge(target_node_id="t1", relation="supports",
                            confidence=0.5)
    edge2 = LineageEdge(target_node_id="t1", relation="supports",
                            confidence=0.9)
    assert g.add_lineage(src.node_id, edge1) is True
    # second add with same (target, relation) — even with different
    # confidence — must be a duplicate no-op
    assert g.add_lineage(src.node_id, edge2) is False
    assert len(g.nodes[src.node_id].lineage) == 1
    # the original (lower-confidence) edge wins; second is rejected
    assert g.nodes[src.node_id].lineage[0].confidence == 0.5


def test_add_lineage_distinct_relations_to_same_target_both_kept():
    g = VectorProvenanceGraph()
    src = make_node(content_bytes=b"x", kind="claim", source="t",
                       source_kind="d", ingested_via="copy_mode")
    g.add_node(src)
    e1 = LineageEdge(target_node_id="t1", relation="supports")
    e2 = LineageEdge(target_node_id="t1", relation="extends")
    assert g.add_lineage(src.node_id, e1) is True
    assert g.add_lineage(src.node_id, e2) is True
    assert len(g.nodes[src.node_id].lineage) == 2


# --- by_capsule ---------------------------------------------------

def test_by_capsule_filters_to_matching_capsule_only():
    g = VectorProvenanceGraph()
    a = make_node(content_bytes=b"a", kind="claim", source="t",
                      source_kind="d", ingested_via="copy_mode",
                      capsule_context="capsule:A")
    b = make_node(content_bytes=b"b", kind="claim", source="t",
                      source_kind="d", ingested_via="copy_mode",
                      capsule_context="capsule:B")
    a2 = make_node(content_bytes=b"a2", kind="claim", source="t",
                       source_kind="d", ingested_via="copy_mode",
                       capsule_context="capsule:A")
    g.add_node(a); g.add_node(b); g.add_node(a2)
    capsule_a = g.by_capsule("capsule:A")
    capsule_b = g.by_capsule("capsule:B")
    capsule_x = g.by_capsule("capsule:does-not-exist")
    assert {n.node_id for n in capsule_a} == {a.node_id, a2.node_id}
    assert {n.node_id for n in capsule_b} == {b.node_id}
    assert capsule_x == []


# --- to_dict ------------------------------------------------------

def test_to_dict_carries_schema_version_and_sorted_nodes():
    """Node dict order must be deterministic (sorted by node_id)
    so audit snapshots are byte-stable across runs."""
    g = VectorProvenanceGraph()
    nodes = [
        make_node(content_bytes=bytes([b]), kind="claim",
                       source="t", source_kind="d",
                       ingested_via="copy_mode",
                       capsule_context="capsule:Z")
        for b in (3, 1, 2)
    ]
    for n in nodes:
        g.add_node(n)
    d = g.to_dict()
    assert "schema_version" in d
    keys = list(d["nodes"].keys())
    assert keys == sorted(keys)


def test_to_dict_round_trips_each_node_to_dict():
    g = VectorProvenanceGraph()
    n = make_node(content_bytes=b"x", kind="claim", source="t",
                       source_kind="d", ingested_via="copy_mode")
    g.add_node(n)
    d = g.to_dict()
    assert d["nodes"][n.node_id] == n.to_dict()


def test_to_dict_empty_graph_has_no_nodes():
    g = VectorProvenanceGraph()
    d = g.to_dict()
    assert d["nodes"] == {}
