"""Targeted tests for Phase 9 §L Memory Tiering + Invariant Extraction."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.memory_tiers import (
    TIERS,
    access_pattern_tracker as apt,
    cold_tier as ct,
    glacier_tier as gt,
    hot_tier as ht,
    invariant_extractor as ie,
    pinning_engine as pe,
    tier_manager as tm,
    warm_tier as wt,
)


# ═══════════════════ schema enums ══════════════════════════════════

def test_tiers_match_schema():
    schema = json.loads((ROOT / "schemas" / "memory_tiering.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["tier"]["enum"]) == TIERS


# ═══════════════════ tier classes ══════════════════════════════════

def test_hot_tier_basic():
    h = ht.HotTier()
    h.put("n1", {"x": 1})
    assert h.get("n1") == {"x": 1}
    assert h.size() == 1
    h.evict("n1")
    assert h.get("n1") is None


def test_all_tiers_have_consistent_api():
    for tier_class in (ht.HotTier, wt.WarmTier,
                          ct.ColdTier, gt.GlacierTier):
        instance = tier_class()
        instance.put("n", {"k": "v"})
        assert instance.get("n") == {"k": "v"}
        assert instance.size() == 1
        instance.evict("n")
        assert instance.get("n") is None


# ═══════════════════ access_pattern_tracker ═══════════════════════

def test_access_tracker_increments():
    t = apt.AccessPatternTracker()
    t.record_access("n1", "t1")
    t.record_access("n1", "t2")
    t.record_access("n2", "t3")
    assert t.get("n1").access_count == 2
    assert t.get("n1").last_access_iso == "t2"
    assert t.get("n2").access_count == 1


def test_access_tracker_does_not_rewrite_meaning():
    """Critical: source must NEVER alter payload content."""
    src = (ROOT / "waggledance" / "core" / "memory_tiers"
            / "access_pattern_tracker.py").read_text(encoding="utf-8")
    forbidden = ("compress(", "summarize(", "paraphrase(", "rewrite(")
    for pat in forbidden:
        assert pat not in src


# ═══════════════════ pinning_engine ════════════════════════════════

def test_pin_unpin_lifecycle():
    p = pe.PinningEngine()
    p.pin(node_id="n1", reason="foundational",
          anchor_status="foundational")
    assert p.is_pinned("n1") is True
    assert p.unpin("n1") is True
    assert p.is_pinned("n1") is False


def test_unpin_nonexistent_returns_false():
    p = pe.PinningEngine()
    assert p.unpin("ghost") is False


def test_auto_pin_foundational_triggers_pin():
    p = pe.PinningEngine()
    assert pe.auto_pin_foundational(p, "n1", "foundational") is True
    assert p.is_pinned("n1") is True


def test_auto_pin_supportive_does_not_pin():
    p = pe.PinningEngine()
    # Per Prompt_1_Master §L only foundational is auto-pinned;
    # supportive is allowed to demote
    assert pe.auto_pin_foundational(p, "n2", "supportive") is False
    assert p.is_pinned("n2") is False


# ═══════════════════ invariant_extractor ══════════════════════════

def test_extract_explicit_invariants():
    invs = ie.extract_invariants(
        node_id="n1",
        payload={"invariants": ["x >= 0", "out > 0"]},
    )
    assert len(invs) == 2
    for inv in invs:
        assert inv.kind == "constraint"
        assert inv.source_node_id == "n1"


def test_extract_schema_declaration():
    invs = ie.extract_invariants(
        node_id="n1", payload={"schema_version": 1},
    )
    assert any(i.kind == "schema" for i in invs)


def test_extract_lineage_relations():
    invs = ie.extract_invariants(
        node_id="n1",
        payload={"lineage": [
            {"target_node_id": "n2", "relation": "supports"},
            {"target_node_id": "n3", "relation": "extends"},
        ]},
    )
    rel_invs = [i for i in invs if i.kind == "relation"]
    assert len(rel_invs) == 2


def test_extract_skips_invalid_invariants():
    invs = ie.extract_invariants(
        node_id="n1",
        payload={"invariants": ["valid one", "", 42, None]},
    )
    valid = [i for i in invs if i.kind == "constraint"]
    assert len(valid) == 1


def test_invariant_store_groups_by_node():
    store = ie.InvariantStore()
    store.add(ie.ExtractedInvariant(
        invariant_id="n1_inv_001", source_node_id="n1",
        statement="x", kind="constraint",
    ))
    store.add(ie.ExtractedInvariant(
        invariant_id="n2_inv_001", source_node_id="n2",
        statement="y", kind="constraint",
    ))
    assert len(store.for_node("n1")) == 1
    assert len(store.for_node("n2")) == 1


def test_invariant_extractor_no_generative_compression():
    """CRITICAL: §L OUT-OF-SCOPE rule — no generative compression."""
    src = (ROOT / "waggledance" / "core" / "memory_tiers"
            / "invariant_extractor.py").read_text(encoding="utf-8")
    forbidden = ("compress_with_llm(", "summarize_via_model(",
                  "paraphrase_with_provider(", "ollama.",
                  "anthropic.messages", "openai.chat")
    for pat in forbidden:
        assert pat not in src


# ═══════════════════ tier_manager ══════════════════════════════════

def test_assign_places_node_and_extracts_invariants():
    mgr = tm.TierManager()
    a = mgr.assign(
        node_id="n1", payload={"invariants": ["x >= 0"]},
        tier="hot", ts_iso="t",
    )
    assert a.tier == "hot"
    assert a.invariant_count == 1
    assert mgr.fetch("n1") == {"invariants": ["x >= 0"]}


def test_foundational_anchor_auto_pinned_on_assign():
    mgr = tm.TierManager()
    a = mgr.assign(
        node_id="n1", payload={"k": "v"}, tier="hot", ts_iso="t",
        anchor_status="foundational",
    )
    assert a.pinned is True
    assert mgr.pins.is_pinned("n1") is True


def test_pinned_node_cannot_demote_to_cold():
    mgr = tm.TierManager()
    mgr.assign(
        node_id="n1", payload={"k": "v"}, tier="hot", ts_iso="t",
        anchor_status="foundational",
    )
    with pytest.raises(tm.TierViolation, match="cannot demote"):
        mgr.demote("n1", "cold", "t2")


def test_pinned_node_cannot_demote_to_glacier():
    mgr = tm.TierManager()
    mgr.assign(
        node_id="n1", payload={"k": "v"}, tier="warm", ts_iso="t",
        anchor_status="foundational",
    )
    with pytest.raises(tm.TierViolation):
        mgr.demote("n1", "glacier", "t2")


def test_unpinned_node_can_demote():
    mgr = tm.TierManager()
    mgr.assign(node_id="n1", payload={}, tier="hot", ts_iso="t")
    a = mgr.demote("n1", "warm", "t2")
    assert a.tier == "warm"
    a = mgr.demote("n1", "cold", "t3")
    assert a.tier == "cold"
    assert a.last_demoted_iso == "t3"


def test_promote_moves_to_hotter_tier():
    mgr = tm.TierManager()
    mgr.assign(node_id="n1", payload={}, tier="cold", ts_iso="t")
    a = mgr.promote("n1", "warm", "t2")
    assert a.tier == "warm"
    assert a.last_promoted_iso == "t2"


def test_promote_rejects_non_hotter_tier():
    mgr = tm.TierManager()
    mgr.assign(node_id="n1", payload={}, tier="warm", ts_iso="t")
    with pytest.raises(tm.TierViolation, match="not hotter"):
        mgr.promote("n1", "cold", "t2")


def test_demote_rejects_non_colder_tier():
    mgr = tm.TierManager()
    mgr.assign(node_id="n1", payload={}, tier="cold", ts_iso="t")
    with pytest.raises(tm.TierViolation, match="not colder"):
        mgr.demote("n1", "hot", "t2")


def test_assign_unknown_tier_raises():
    mgr = tm.TierManager()
    with pytest.raises(tm.TierViolation, match="unknown tier"):
        mgr.assign(node_id="n1", payload={}, tier="bogus", ts_iso="t")


def test_record_access_updates_assignment():
    mgr = tm.TierManager()
    mgr.assign(node_id="n1", payload={}, tier="hot", ts_iso="t1")
    mgr.record_access("n1", "t2")
    a = mgr.assignments["n1"]
    assert a.access_count == 1
    assert a.last_access_iso == "t2"


def test_no_data_loss_under_movement():
    mgr = tm.TierManager()
    mgr.assign(node_id="n1", payload={"valuable": "data"},
                tier="hot", ts_iso="t")
    mgr.demote("n1", "warm", "t2")
    assert mgr.fetch("n1") == {"valuable": "data"}
    mgr.demote("n1", "cold", "t3")
    assert mgr.fetch("n1") == {"valuable": "data"}
    mgr.promote("n1", "hot", "t4")
    assert mgr.fetch("n1") == {"valuable": "data"}


def test_tier_snapshot_byte_stable():
    mgr = tm.TierManager()
    mgr.assign(node_id="n1", payload={"k": "v"}, tier="warm",
                ts_iso="t1", anchor_status="supportive")
    mgr.assign(node_id="n2", payload={"invariants": ["x"]},
                tier="hot", ts_iso="t1")
    s1 = mgr.tier_snapshot()
    s2 = mgr.tier_snapshot()
    assert json.dumps(s1, sort_keys=True) == \
        json.dumps(s2, sort_keys=True)


def test_deterministic_rebuild_produces_same_layout():
    mgr = tm.TierManager()
    mgr.assign(node_id="n1", payload={"k": 1}, tier="hot", ts_iso="t",
                anchor_status="foundational")
    mgr.assign(node_id="n2", payload={"k": 2}, tier="warm", ts_iso="t")
    mgr.assign(node_id="n3", payload={"k": 3}, tier="cold", ts_iso="t")
    rebuilt = mgr.deterministic_rebuild()
    assert json.dumps(mgr.tier_snapshot(), sort_keys=True) == \
        json.dumps(rebuilt.tier_snapshot(), sort_keys=True)


def test_invariant_extracted_before_glacier_demotion():
    """CRITICAL §L rule: invariants must be extracted BEFORE deep
    tiering."""
    mgr = tm.TierManager()
    mgr.assign(
        node_id="n1",
        payload={"invariants": ["x >= 0", "out > 0"]},
        tier="cold", ts_iso="t",
    )
    # Even though node is cold, invariants are in the store
    invs = mgr.invariants.for_node("n1")
    assert len(invs) == 2


# ═══════════════════ no generative compression ════════════════════

def test_no_generative_compression_in_memory_tiers():
    """OUT-OF-SCOPE per §L: generative memory compression deferred
    to Phase 12+."""
    pkg = ROOT / "waggledance" / "core" / "memory_tiers"
    forbidden = ("compress_with_llm(", "summarize_via_model(",
                  "paraphrase_with_provider(",
                  "anthropic.messages.create",
                  "openai.chat", "ollama.generate(")
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"


# ═══════════════════ CLI ═══════════════════════════════════════════

def test_cli_help():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "rebuild_memory_tiers.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    assert "--snapshot-path" in r.stdout


def test_cli_dry_run_no_snapshot():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "rebuild_memory_tiers.py"), "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["assignments_total"] == 0
    assert out["deterministic_rebuild_supported"] is True


# ═══════════════════ source safety ═════════════════════════════════

def test_phase_l_source_safety():
    forbidden = ["import faiss", "from waggledance.runtime",
                  "ollama.generate(", "openai.chat",
                  "anthropic.messages", "requests.post(",
                  "axiom_write(", "promote_to_runtime("]
    pkg = ROOT / "waggledance" / "core" / "memory_tiers"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"
