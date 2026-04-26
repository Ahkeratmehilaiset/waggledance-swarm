# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for Phase 9 §K Real Hex Runtime Topology."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.hex_topology import (
    LIVE_STATES,
    REQUIRED_SHARDS,
    SUBDIVISION_STATES,
    cell_local_state as cls,
    cell_message_contract as cmc,
    cell_runtime as cr,
    parent_child_relations as pcr,
    ring_messaging as rm,
    subdivision_operator as so,
)


# ═══════════════════ schema enums ══════════════════════════════════

def test_live_states_match_schema():
    schema = json.loads((ROOT / "schemas" / "hex_runtime.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["live_state"]["enum"]) == LIVE_STATES


def test_subdivision_states_match_schema():
    schema = json.loads((ROOT / "schemas" / "hex_runtime.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["subdivision_state"]["enum"]) \
        == SUBDIVISION_STATES


# ═══════════════════ cell_local_state ═════════════════════════════

def test_local_state_default_shards_present():
    s = cls.CellLocalState(cell_id="c1")
    shards = s.shards_present()
    for required in REQUIRED_SHARDS:
        assert shards[required] is True
    assert shards["builder_lane_queue"] is False


def test_local_state_with_builder_lane_queue():
    s = cls.CellLocalState(cell_id="c1", builder_lane_queue=[])
    assert s.shards_present()["builder_lane_queue"] is True


def test_local_state_to_dict_round_trip():
    s = cls.CellLocalState(
        cell_id="c1",
        proposal_queue=[{"id": "p1"}],
        dream_queue=[{"night": 1}],
        local_budget={"calls": 10},
    )
    d = s.to_dict()
    assert d["cell_id"] == "c1"
    assert d["proposal_queue"] == [{"id": "p1"}]


# ═══════════════════ cell_runtime ══════════════════════════════════

def test_make_runtime_shadow_only_default():
    r = cr.make_runtime(cell_id="c1")
    assert r.live_state == "shadow_only"
    assert r.subdivision_state == "leaf"


def test_make_runtime_rejects_self_in_children():
    with pytest.raises(ValueError, match="cannot appear in own"):
        cr.make_runtime(
            cell_id="c1",
            child_cell_ids=("c1", "c2"),
        )


def test_make_runtime_rejects_unknown_live_state():
    s = cls.CellLocalState(cell_id="c1")
    with pytest.raises(ValueError, match="unknown live_state"):
        cr.CellRuntime(
            schema_version=1, cell_id="c1",
            parent_cell_id=None, child_cell_ids=(),
            neighbor_cell_ids=(), shards_present=s.shards_present(),
            live_state="bogus", subdivision_state="leaf",
        )


def test_make_runtime_rejects_missing_required_shard():
    shards_partial = {s: True for s in REQUIRED_SHARDS}
    shards_partial["curiosity"] = False
    with pytest.raises(ValueError, match="required shard missing"):
        cr.CellRuntime(
            schema_version=1, cell_id="c1",
            parent_cell_id=None, child_cell_ids=(),
            neighbor_cell_ids=(), shards_present=shards_partial,
            live_state="shadow_only", subdivision_state="leaf",
        )


def test_make_topology_sorts_by_cell_id():
    runtimes = [
        cr.make_runtime(cell_id="c2"),
        cr.make_runtime(cell_id="c1"),
        cr.make_runtime(cell_id="c3"),
    ]
    topology = cr.make_topology(runtimes)
    assert list(topology["cells"].keys()) == ["c1", "c2", "c3"]


# ═══════════════════ parent_child_relations ═══════════════════════

def _topo() -> dict:
    return {
        "cells": {
            "root": {
                "cell_id": "root", "parent_cell_id": None,
                "child_cell_ids": ["a", "b"],
                "neighbor_cell_ids": [],
            },
            "a": {
                "cell_id": "a", "parent_cell_id": "root",
                "child_cell_ids": ["a1", "a2"],
                "neighbor_cell_ids": ["b"],
            },
            "b": {
                "cell_id": "b", "parent_cell_id": "root",
                "child_cell_ids": [],
                "neighbor_cell_ids": ["a"],
            },
            "a1": {
                "cell_id": "a1", "parent_cell_id": "a",
                "child_cell_ids": [], "neighbor_cell_ids": ["a2"],
            },
            "a2": {
                "cell_id": "a2", "parent_cell_id": "a",
                "child_cell_ids": [], "neighbor_cell_ids": ["a1"],
            },
        },
    }


def test_parent_of():
    t = _topo()
    assert pcr.parent_of(t, "a") == "root"
    assert pcr.parent_of(t, "root") is None
    assert pcr.parent_of(t, "ghost") is None


def test_children_of_sorted():
    t = _topo()
    assert pcr.children_of(t, "root") == ["a", "b"]
    assert pcr.children_of(t, "ghost") == []


def test_siblings_of():
    t = _topo()
    assert pcr.siblings_of(t, "a") == ["b"]
    assert pcr.siblings_of(t, "root") == []


def test_neighbors_of_sorted():
    t = _topo()
    assert pcr.neighbors_of(t, "a1") == ["a2"]


def test_ancestors_of():
    t = _topo()
    assert pcr.ancestors_of(t, "a1") == ["a", "root"]
    assert pcr.ancestors_of(t, "root") == []


def test_descendants_of():
    t = _topo()
    assert pcr.descendants_of(t, "root") == ["a", "a1", "a2", "b"]
    assert pcr.descendants_of(t, "b") == []


# ═══════════════════ cell_message_contract ════════════════════════

def test_cell_message_rejects_self_addressed():
    with pytest.raises(ValueError, match="must differ"):
        cmc.CellMessage(
            schema_version=1, from_cell_id="c1", to_cell_id="c1",
            kind="ring_request", payload={}, no_runtime_mutation=True,
        )


def test_cell_message_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown message kind"):
        cmc.CellMessage(
            schema_version=1, from_cell_id="c1", to_cell_id="c2",
            kind="bogus", payload={}, no_runtime_mutation=True,
        )


def test_make_message_const_no_runtime_mutation():
    m = cmc.make_message(from_cell_id="c1", to_cell_id="c2",
                              kind="ring_request")
    assert m.no_runtime_mutation is True


def test_validate_finds_unknown_cells():
    t = _topo()
    m = cmc.make_message(from_cell_id="ghost", to_cell_id="a",
                              kind="ring_request")
    errors = cmc.validate(m, t)
    assert any("unknown from_cell_id" in e for e in errors)


# ═══════════════════ ring_messaging ════════════════════════════════

def test_deliver_one_neighbor_ok():
    t = _topo()
    m = cmc.make_message(from_cell_id="a", to_cell_id="b",
                              kind="ring_request")
    d = rm.deliver_one(t, m, seq=0)
    assert d.delivered is True


def test_deliver_one_non_neighbor_blocked():
    t = _topo()
    m = cmc.make_message(from_cell_id="a", to_cell_id="a1",
                              kind="ring_request")
    # a1 is a child of a, not a neighbor
    d = rm.deliver_one(t, m, seq=0)
    assert d.delivered is False
    assert "neighbor" in d.blocked_reason


def test_deliver_one_child_to_parent():
    t = _topo()
    m = cmc.make_message(from_cell_id="a1", to_cell_id="a",
                              kind="child_to_parent")
    d = rm.deliver_one(t, m, seq=0)
    assert d.delivered is True


def test_deliver_one_child_to_wrong_parent_blocked():
    t = _topo()
    m = cmc.make_message(from_cell_id="a1", to_cell_id="b",
                              kind="child_to_parent")
    d = rm.deliver_one(t, m, seq=0)
    assert d.delivered is False
    assert "is not parent" in d.blocked_reason


def test_deliver_one_parent_to_child_ok():
    t = _topo()
    m = cmc.make_message(from_cell_id="a", to_cell_id="a1",
                              kind="parent_to_child")
    d = rm.deliver_one(t, m, seq=0)
    assert d.delivered is True


def test_deliver_one_parent_to_non_child_blocked():
    t = _topo()
    m = cmc.make_message(from_cell_id="a", to_cell_id="b",
                              kind="parent_to_child")
    d = rm.deliver_one(t, m, seq=0)
    assert d.delivered is False
    assert "not a child" in d.blocked_reason


def test_deliver_batch_preserves_order():
    t = _topo()
    msgs = [
        cmc.make_message(from_cell_id="a", to_cell_id="b",
                              kind="ring_request"),
        cmc.make_message(from_cell_id="a1", to_cell_id="a2",
                              kind="ring_request"),
    ]
    deliveries = rm.deliver_batch(t, msgs)
    assert [d.message_id_seq for d in deliveries] == [0, 1]
    assert all(d.delivered for d in deliveries)


# ═══════════════════ subdivision_operator ═════════════════════════

def test_plan_id_deterministic():
    a = so.compute_plan_id(parent_cell_id="root",
                                new_child_cell_ids=("a", "b"))
    b = so.compute_plan_id(parent_cell_id="root",
                                new_child_cell_ids=("b", "a"))
    assert a == b   # sorted before hashing


def test_plan_subdivision_basic():
    p = so.plan_subdivision(
        parent_cell_id="root",
        new_child_cell_ids=("a", "b"),
        rationale="testing",
    )
    assert p.parent_cell_id == "root"
    assert p.new_child_cell_ids == ("a", "b")
    assert p.no_runtime_mutation is True
    assert p.target_state == "subdivision_in_shadow"


def test_plan_subdivision_rejects_too_few_children():
    with pytest.raises(ValueError, match="at least 2"):
        so.plan_subdivision(
            parent_cell_id="root",
            new_child_cell_ids=("a",),
        )


def test_plan_subdivision_rejects_parent_in_children():
    with pytest.raises(ValueError, match="cannot appear"):
        so.plan_subdivision(
            parent_cell_id="root",
            new_child_cell_ids=("root", "a"),
        )


def test_plan_subdivision_rejects_duplicate_children():
    with pytest.raises(ValueError, match="must be unique"):
        so.plan_subdivision(
            parent_cell_id="root",
            new_child_cell_ids=("a", "a"),
        )


def test_plan_subdivision_rejects_unknown_target_state():
    with pytest.raises(ValueError, match="unknown target_state"):
        so.plan_subdivision(
            parent_cell_id="root",
            new_child_cell_ids=("a", "b"),
            target_state="bogus",
        )


def test_apply_plan_to_topology_pure():
    t_orig = _topo()
    plan = so.plan_subdivision(
        parent_cell_id="b",
        new_child_cell_ids=("b1", "b2"),
    )
    t_new = so.apply_plan_to_topology(t_orig, plan)
    # Original unchanged
    assert t_orig["cells"]["b"]["child_cell_ids"] == []
    # New topology has children registered
    assert "b1" in t_new["cells"]
    assert "b2" in t_new["cells"]
    assert t_new["cells"]["b"]["subdivision_state"] == \
        "subdivision_in_shadow"
    # New children are shadow_only leaves
    for c in ("b1", "b2"):
        assert t_new["cells"][c]["live_state"] == "shadow_only"
        assert t_new["cells"][c]["parent_cell_id"] == "b"


def test_apply_plan_rejects_unknown_parent():
    plan = so.plan_subdivision(
        parent_cell_id="ghost",
        new_child_cell_ids=("g1", "g2"),
    )
    with pytest.raises(ValueError, match="unknown parent_cell_id"):
        so.apply_plan_to_topology(_topo(), plan)


# ═══════════════════ no runtime mutation in this session ─────────-

def test_subdivision_creates_shadow_only_children():
    t = {"cells": {"root": {
        "cell_id": "root", "parent_cell_id": None,
        "child_cell_ids": [], "neighbor_cell_ids": [],
    }}}
    plan = so.plan_subdivision(
        parent_cell_id="root",
        new_child_cell_ids=("a", "b"),
    )
    t_new = so.apply_plan_to_topology(t, plan)
    # All new children must be shadow_only — no live mutation
    for child_id in plan.new_child_cell_ids:
        assert t_new["cells"][child_id]["live_state"] == "shadow_only"


# ═══════════════════ source safety ═════════════════════════════════

def test_phase_k_source_safety():
    forbidden = ["import faiss", "from waggledance.runtime",
                  "ollama.generate(", "openai.chat",
                  "anthropic.messages", "requests.post(",
                  "axiom_write(", "promote_to_runtime("]
    pkg = ROOT / "waggledance" / "core" / "hex_topology"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"
