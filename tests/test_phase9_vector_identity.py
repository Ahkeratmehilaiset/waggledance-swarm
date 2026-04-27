# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for Phase 9 §H Vector Identity + Universal Ingestion."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.ingestion import (
    INGESTION_MODES,
    link_manager as lm,
    link_watcher as lw,
    universal_ingestor as ui,
)
from waggledance.core.vector_identity import (
    ANCHOR_STATUSES,
    DEDUP_LEVELS,
    LINEAGE_RELATIONS,
    NODE_KINDS,
    identity_anchor as ia,
    ingestion_dedup as dd,
    vector_provenance_graph as vpg,
)


# ═══════════════════ schema enums match constants ══════════════════

def test_node_kinds_match_schema():
    schema = json.loads((ROOT / "schemas" / "vector_node.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["kind"]["enum"]) == NODE_KINDS


def test_anchor_statuses_match_schema():
    schema = json.loads((ROOT / "schemas" / "vector_node.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["anchor_status"]["enum"]) == ANCHOR_STATUSES


def test_lineage_relations_match_schema():
    schema = json.loads((ROOT / "schemas" / "vector_node.schema.json")
                          .read_text(encoding="utf-8"))
    rel_enum = tuple(schema["properties"]["lineage"]["items"]
                       ["properties"]["relation"]["enum"])
    assert rel_enum == LINEAGE_RELATIONS


# ═══════════════════ vector_provenance_graph ══════════════════════-

def test_make_node_content_address_dedups():
    a = vpg.make_node(
        content_bytes=b"hello world",
        kind="document_chunk", source="t", source_kind="local_file",
        ingested_via="copy_mode",
    )
    b = vpg.make_node(
        content_bytes=b"hello world",
        kind="document_chunk", source="different_source",
        source_kind="local_file", ingested_via="copy_mode",
    )
    # Identical content + kind + capsule → same node_id
    assert a.node_id == b.node_id


def test_make_node_capsule_context_changes_id():
    a = vpg.make_node(
        content_bytes=b"hello", kind="document_chunk",
        source="t", source_kind="local_file", ingested_via="copy_mode",
        capsule_context="neutral_v1",
    )
    b = vpg.make_node(
        content_bytes=b"hello", kind="document_chunk",
        source="t", source_kind="local_file", ingested_via="copy_mode",
        capsule_context="factory_v1",
    )
    assert a.node_id != b.node_id


def test_make_node_rejects_unknown_kind():
    with pytest.raises(ValueError):
        vpg.make_node(content_bytes=b"x", kind="bogus",
                          source="t", source_kind="local_file",
                          ingested_via="copy_mode")


def test_make_node_rejects_unknown_ingested_via():
    with pytest.raises(ValueError):
        vpg.make_node(content_bytes=b"x", kind="document_chunk",
                          source="t", source_kind="local_file",
                          ingested_via="bogus_mode")


def test_lineage_edge_rejects_unknown_relation():
    with pytest.raises(ValueError):
        vpg.LineageEdge(target_node_id="abc", relation="unknown")


def test_graph_dedup_on_add():
    g = vpg.VectorProvenanceGraph()
    a = vpg.make_node(content_bytes=b"x", kind="document_chunk",
                          source="t", source_kind="local_file",
                          ingested_via="copy_mode")
    g.add_node(a)
    _, was_new = g.add_node(a)
    assert was_new is False
    assert len(g.nodes) == 1


def test_add_lineage_dedups():
    g = vpg.VectorProvenanceGraph()
    a = vpg.make_node(content_bytes=b"x", kind="document_chunk",
                          source="t", source_kind="local_file",
                          ingested_via="copy_mode")
    g.add_node(a)
    edge = vpg.LineageEdge(target_node_id="b", relation="supports")
    assert g.add_lineage(a.node_id, edge) is True
    assert g.add_lineage(a.node_id, edge) is False


# ═══════════════════ ingestion (file + mentor pack) ════════════════

def test_chunk_text_deterministic():
    text = "abcdef" * 100
    chunks_a = ui.chunk_text(text, chunk_size=10)
    chunks_b = ui.chunk_text(text, chunk_size=10)
    assert chunks_a == chunks_b


def test_chunk_text_handles_unicode_boundaries():
    text = "ä" * 100   # 2-byte UTF-8 each
    chunks = ui.chunk_text(text, chunk_size=3)
    rejoined = "".join(chunks)
    assert rejoined == text


def test_ingest_local_file(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("hello world", encoding="utf-8")
    nodes = ui.ingest_local_file(p, ingested_at_tick=1)
    assert len(nodes) >= 1
    assert nodes[0].kind == "document_chunk"
    assert nodes[0].source == str(p)


def test_ingest_mentor_context_pack():
    pack = {
        "schema_version": 1,
        "pack_id": "test_pack",
        "produced_by": "opus_factory_v1",
        "items": [
            {"item_id": "i1", "kind": "design_note",
             "content": "first design lesson",
             "tags": ["topology", "shadow"]},
            {"item_id": "i2", "kind": "anti_pattern",
             "content": "avoid this pattern",
             "tags": ["topology"]},
        ],
    }
    nodes = ui.ingest_mentor_context_pack(pack)
    assert len(nodes) == 2
    for n in nodes:
        assert n.kind == "mentor_context"
        assert n.anchor_status == "candidate"


def test_ingest_into_graph_dedups():
    g = vpg.VectorProvenanceGraph()
    a = vpg.make_node(content_bytes=b"hello", kind="document_chunk",
                          source="t", source_kind="local_file",
                          ingested_via="copy_mode")
    a2 = vpg.make_node(content_bytes=b"hello", kind="document_chunk",
                            source="t2", source_kind="local_file",
                            ingested_via="copy_mode")
    result = ui.ingest_into_graph(g, [a, a2])
    assert result["nodes_added"] == 1
    assert result["nodes_deduped"].get("exact_content_hash") == 1


# ═══════════════════ ingestion_dedup ═══════════════════════════════

def test_exact_content_match():
    g_node = vpg.make_node(content_bytes=b"x", kind="document_chunk",
                                source="t", source_kind="local_file",
                                ingested_via="copy_mode")
    cand = vpg.make_node(content_bytes=b"x", kind="document_chunk",
                              source="t2", source_kind="local_file",
                              ingested_via="copy_mode")
    r = dd.exact_content_match(cand, [g_node])
    assert r.level == "exact_content_hash"


def test_concept_event_sibling_by_tags():
    g_node = vpg.make_node(content_bytes=b"x", kind="concept",
                                source="t", source_kind="local_file",
                                ingested_via="copy_mode",
                                tags=("topology", "shadow"))
    cand = vpg.make_node(content_bytes=b"y", kind="concept",
                              source="t2", source_kind="local_file",
                              ingested_via="copy_mode",
                              tags=("topology", "shadow"))
    r = dd.concept_event_sibling(cand, [g_node])
    assert r.level == "concept_event_sibling"


def test_contradiction_or_extension_detected():
    pos = vpg.make_node(content_bytes=b"a", kind="claim",
                             source="t", source_kind="local_file",
                             ingested_via="copy_mode",
                             tags=("topology_subdivision",))
    neg = vpg.make_node(content_bytes=b"b", kind="claim",
                             source="t2", source_kind="local_file",
                             ingested_via="copy_mode",
                             tags=("topology_subdivision", "contradicts"))
    r = dd.contradiction_or_extension(neg, [pos])
    assert r.level == "contradiction_or_extension"


def test_dedup_pipeline_falls_through():
    cand = vpg.make_node(content_bytes=b"unique", kind="document_chunk",
                              source="t", source_kind="local_file",
                              ingested_via="copy_mode")
    r = dd.dedup_pipeline(cand, [])
    assert r.level == "no_match"


# ═══════════════════ identity_anchor validation ═══════════════════

def test_evaluate_candidate_no_siblings_remains_candidate():
    cand = vpg.make_node(content_bytes=b"x", kind="document_chunk",
                              source="t", source_kind="local_file",
                              ingested_via="copy_mode")
    v = ia.evaluate_candidate(candidate=cand, siblings_in_graph=[])
    assert v.promoted_to == "candidate"


def test_evaluate_candidate_supports_promote_to_supportive():
    cand = vpg.make_node(content_bytes=b"target", kind="document_chunk",
                              source="t", source_kind="local_file",
                              ingested_via="copy_mode")
    sib = vpg.VectorNode(
        schema_version=1, node_id="sibling12345",
        content_sha256="sha256:" + "a"*64,
        kind="document_chunk", anchor_status="supportive",
        capsule_context="neutral_v1",
        source="x", source_kind="local_file", ingested_via="copy_mode",
        external_path=None, fixture_fallback_used=False,
        ingested_at_tick=0,
        lineage=(vpg.LineageEdge(target_node_id=cand.node_id,
                                       relation="supports"),),
        tags=(),
    )
    v = ia.evaluate_candidate(candidate=cand, siblings_in_graph=[sib])
    assert v.promoted_to == "supportive"


def test_evaluate_candidate_rejects_on_contradictions():
    cand = vpg.make_node(content_bytes=b"target", kind="claim",
                              source="t", source_kind="local_file",
                              ingested_via="copy_mode")
    sibs = [
        vpg.VectorNode(
            schema_version=1, node_id=f"sib_{i:02d}xyzab",
            content_sha256="sha256:" + str(i)*64,
            kind="claim", anchor_status="supportive",
            capsule_context="neutral_v1",
            source="x", source_kind="local_file",
            ingested_via="copy_mode", external_path=None,
            fixture_fallback_used=False, ingested_at_tick=0,
            lineage=(vpg.LineageEdge(target_node_id=cand.node_id,
                                           relation="contradicts"),),
            tags=(),
        )
        for i in range(4)   # ≥ default max_contradictions=3
    ]
    v = ia.evaluate_candidate(candidate=cand, siblings_in_graph=sibs)
    assert v.promoted_to == "rejected"


def test_no_auto_promote_to_foundational_without_human_approval():
    with pytest.raises(PermissionError):
        ia.assert_no_auto_promote_to_foundational(
            from_status="supportive", to_status="foundational",
            human_approval_id=None,
        )


def test_human_approval_allows_foundational_promotion():
    # Doesn't raise when approval supplied
    ia.assert_no_auto_promote_to_foundational(
        from_status="supportive", to_status="foundational",
        human_approval_id="human:reviewer:2026-04-26:abc",
    )


# ═══════════════════ link_manager + link_watcher ══════════════════-

def test_link_manager_round_trip(tmp_path):
    target = tmp_path / "external.faiss"
    target.write_bytes(b"abc" * 100)
    rec = lm.make_link(
        external_path=str(target), source_kind="faiss_db",
    )
    p = tmp_path / "links.json"
    lm.save_links([rec], p)
    loaded = lm.load_links(p)
    assert len(loaded) == 1
    assert loaded[0].external_path == str(target)


def test_link_save_uses_atomic_replace(tmp_path, monkeypatch):
    target = tmp_path / "external.faiss"
    target.write_bytes(b"abc")
    rec = lm.make_link(external_path=str(target), source_kind="faiss_db")
    p = tmp_path / "links.json"
    real = lm.os.replace
    captured = []

    def cap(src, dst):
        captured.append((Path(src).name, Path(dst).name))
        return real(src, dst)

    monkeypatch.setattr(lm.os, "replace", cap)
    lm.save_links([rec], p)
    assert any(dst == "links.json" for _, dst in captured)
    assert any(src.startswith(".links.") for src, _ in captured)


def test_link_watcher_detects_unchanged(tmp_path):
    target = tmp_path / "external.faiss"
    target.write_bytes(b"abc")
    rec = lm.make_link(external_path=str(target), source_kind="faiss_db")
    obs = lw.observe(rec)
    assert obs.state == "ok_unchanged"


def test_link_watcher_detects_growth(tmp_path):
    target = tmp_path / "external.faiss"
    target.write_bytes(b"abc")
    rec = lm.make_link(external_path=str(target), source_kind="faiss_db")
    target.write_bytes(b"abc" + b"def")   # grew
    obs = lw.observe(rec)
    assert obs.state == "ok_growth"


def test_link_watcher_detects_critical_change(tmp_path):
    target = tmp_path / "external.faiss"
    target.write_bytes(b"abc")
    rec = lm.make_link(external_path=str(target), source_kind="faiss_db")
    target.write_bytes(b"xyz")   # same size, different content
    obs = lw.observe(rec)
    assert obs.state == "critical_change"


def test_link_watcher_detects_shrinkage(tmp_path):
    target = tmp_path / "external.faiss"
    target.write_bytes(b"abcdef")
    rec = lm.make_link(external_path=str(target), source_kind="faiss_db")
    target.write_bytes(b"abc")   # shrank
    obs = lw.observe(rec)
    assert obs.state == "critical_change"


def test_link_watcher_detects_missing(tmp_path):
    target = tmp_path / "external.faiss"
    target.write_bytes(b"abc")
    rec = lm.make_link(external_path=str(target), source_kind="faiss_db")
    target.unlink()
    obs = lw.observe(rec)
    assert obs.state == "missing"


# ═══════════════════ ingestion manifest ═══════════════════════════-

def test_make_manifest_rejects_unknown_mode():
    with pytest.raises(ValueError):
        ui.make_manifest(
            ingested_at_iso="t", ingested_at_tick=1, mode="bogus",
            sources=[], nodes_added=0, nodes_deduped={},
        )


def test_manifest_id_deterministic():
    m1 = ui.make_manifest(
        ingested_at_iso="2026-04-26T00:00:00", ingested_at_tick=1,
        mode="copy_mode", sources=[{"source": "x", "source_kind": "local_file"}],
        nodes_added=5, nodes_deduped={"exact_content_hash": 2},
    )
    m2 = ui.make_manifest(
        ingested_at_iso="2026-04-26T00:00:00", ingested_at_tick=1,
        mode="copy_mode", sources=[{"source": "x", "source_kind": "local_file"}],
        nodes_added=5, nodes_deduped={"exact_content_hash": 2},
    )
    assert m1.manifest_id == m2.manifest_id


# ═══════════════════ CLI tools ═════════════════════════════════════

def test_wd_ingest_help():
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "wd_ingest.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    for flag in ("--source", "--mentor-pack", "--vectorize-from",
                  "--mode", "--apply", "--json"):
        assert flag in r.stdout


def test_wd_link_help():
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "wd_link.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    for flag in ("--add", "--source-kind", "--observe", "--apply"):
        assert flag in r.stdout


def test_wd_identity_help():
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "wd_identity.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    for flag in ("--graph-path", "--validate-candidates"):
        assert flag in r.stdout


# ═══════════════════ source safety ═════════════════════════════════

def test_phase_h_source_safety():
    forbidden = ["import faiss", "from waggledance.runtime",
                  "ollama.generate(", "openai.chat",
                  "anthropic.messages", "requests.get(",
                  "requests.post(", "axiom_write(",
                  "promote_to_runtime("]
    forbidden_metaphors = ["bee ", "honeycomb ", "swarm ", "pdam",
                             "beverage"]
    for pkg_name in ("vector_identity", "ingestion"):
        pkg = ROOT / "waggledance" / "core" / pkg_name
        for p in pkg.glob("*.py"):
            text = p.read_text(encoding="utf-8")
            for pat in forbidden:
                assert pat not in text, f"{p.name}: {pat}"
            text_l = text.lower()
            for pat in forbidden_metaphors:
                assert pat not in text_l, f"{p.name}: {pat}"
