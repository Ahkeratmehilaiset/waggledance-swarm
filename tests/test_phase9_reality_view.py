# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for Phase 9 §P Reality View (legacy hologram upgrade)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.ui.hologram import (
    hologram_adapter as ha,
    hologram_snapshot as hs,
    reality_view as rv,
)
PANELS = rv.PANELS


# ═══════════════════ panel construction ═══════════════════════════-

def test_all_inputs_missing_yields_explicit_unavailable_panels():
    panels = rv.build_panels_from_state()
    # All standard panels exist; all are unavailable; all carry rationale
    panel_ids = {p.panel_id for p in panels}
    for required in ("mission_queue_top", "attention_focus",
                      "top_tensions", "top_blind_spots",
                      "self_model_summary", "world_model_summary",
                      "dream_queue", "meta_proposal_queue",
                      "vector_tier_heat", "cell_topology",
                      "builder_lane_status"):
        assert required in panel_ids
    for p in panels:
        if not p.available:
            assert p.rationale_if_unavailable, (
                f"unavailable panel {p.panel_id} must carry rationale"
            )


def test_no_fake_data_when_inputs_missing():
    """Critical: panels with no input data must NOT contain
    fabricated items."""
    panels = rv.build_panels_from_state()
    for p in panels:
        if not p.available:
            assert p.items == ()


# ═══════════════════ real-data paths ══════════════════════════════-

def test_self_model_summary_from_real_artifact():
    sm = {
        "schema_version": 1,
        "snapshot_id": "abc123def456",
        "cells": [{"cell_id": "thermal"}, {"cell_id": "energy"}],
        "workspace_tensions": [{"tension_id": "t1", "type": "x",
                                  "severity": "high",
                                  "lifecycle_status": "persisting"}],
        "blind_spots": [{"domain": "safety", "severity": "high",
                          "detectors": ["coverage_negative_space"]}],
        "real_data_coverage_ratio": 0.89,
        "attention_focus": [
            {"curiosity_id": "cur_1", "candidate_cell": "thermal",
             "estimated_value": 9.5, "rank": 1},
        ],
    }
    panels = rv.build_panels_from_state(self_model=sm)
    sm_panel = next(p for p in panels if p.panel_id == "self_model_summary")
    assert sm_panel.available
    summary = sm_panel.items[0]
    assert summary["cells"] == 2
    assert summary["tensions"] == 1
    assert summary["blind_spots"] == 1
    assert summary["real_data_coverage_ratio"] == 0.89


def test_top_tensions_uses_real_data():
    sm = {
        "workspace_tensions": [
            {"tension_id": "t1", "type": "scorecard_drift",
             "severity": "high", "lifecycle_status": "persisting"},
        ],
    }
    panels = rv.build_panels_from_state(self_model=sm)
    p = next(x for x in panels if x.panel_id == "top_tensions")
    assert p.available
    assert p.items[0]["tension_id"] == "t1"
    assert p.items[0]["type"] == "scorecard_drift"


def test_world_model_summary_from_real_snapshot():
    wm = {
        "snapshot_id": "wm_abc123ef",
        "external_facts": [{"fact_id": "f1"}, {"fact_id": "f2"}],
        "causal_relations": [{"cause_fact_id": "f1",
                                "effect_fact_id": "f2"}],
        "predictions": [{"prediction_id": "p1"}],
        "uncertainty_summary": {"facts_with_low_confidence": 0,
                                  "predictions_pending_eval": 1},
    }
    panels = rv.build_panels_from_state(world_model_snapshot=wm)
    p = next(x for x in panels if x.panel_id == "world_model_summary")
    assert p.available
    s = p.items[0]
    assert s["facts"] == 2
    assert s["causes"] == 1
    assert s["predictions"] == 1


def test_dream_queue_uses_real_curriculum():
    curr = {
        "nights": [
            {"night_index": 1, "mode": "base_solver_growth",
             "uncertainty": "medium",
             "target_items": [{"source_id": "ten_001"}]},
            {"night_index": 2, "mode": "wait", "uncertainty": "high",
             "target_items": []},
        ],
    }
    panels = rv.build_panels_from_state(dream_curriculum=curr)
    p = next(x for x in panels if x.panel_id == "dream_queue")
    assert p.available
    assert len(p.items) == 2
    assert p.items[0]["target_count"] == 1


def test_meta_proposal_queue_uses_review_bundle():
    bundle = {
        "proposals": [
            {"meta_proposal_id": "abc123def456",
             "proposal_type": "introspection_gap",
             "proposal_priority": 0.6,
             "recommended_next_human_action": "review_for_future_PR"},
        ],
    }
    panels = rv.build_panels_from_state(hive_review_bundle=bundle)
    p = next(x for x in panels if x.panel_id == "meta_proposal_queue")
    assert p.available
    assert p.items[0]["recommended_next_human_action"] == "review_for_future_PR"


def test_vector_tier_heat_counts_by_anchor_status():
    g = {
        "nodes": {
            "n1": {"anchor_status": "candidate"},
            "n2": {"anchor_status": "candidate"},
            "n3": {"anchor_status": "supportive"},
        },
    }
    panels = rv.build_panels_from_state(vector_graph=g)
    p = next(x for x in panels if x.panel_id == "vector_tier_heat")
    assert p.available
    counts = p.items[0]["counts"]
    assert counts["candidate"] == 2
    assert counts["supportive"] == 1


def test_mission_queue_top_sorts_by_priority():
    missions = [
        {"mission_id": "a", "priority": 0.1, "kind": "noop", "lane": "wait"},
        {"mission_id": "b", "priority": 0.9, "kind": "noop", "lane": "wait"},
        {"mission_id": "c", "priority": 0.5, "kind": "noop", "lane": "wait"},
    ]
    panels = rv.build_panels_from_state(mission_queue=missions)
    p = next(x for x in panels if x.panel_id == "mission_queue_top")
    assert p.available
    assert p.items[0]["mission_id"] == "b"
    assert p.items[1]["mission_id"] == "c"


def test_builder_lane_status_unavailable_until_phase_u2():
    panels = rv.build_panels_from_state()
    p = next(x for x in panels if x.panel_id == "builder_lane_status")
    assert not p.available
    assert "Phase U2" in p.rationale_if_unavailable


# ═══════════════════ snapshot construction ════════════════════════-

def test_make_reality_snapshot_byte_stable():
    panels = rv.build_panels_from_state()
    s1 = rv.make_reality_snapshot(
        produced_at_iso="t",
        panels=panels,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
    )
    s2 = rv.make_reality_snapshot(
        produced_at_iso="t",
        panels=panels,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
    )
    assert json.dumps(s1.to_dict(), sort_keys=True) == \
        json.dumps(s2.to_dict(), sort_keys=True)


# ═══════════════════ atomic snapshot persistence ══════════════════-

def test_save_snapshot_atomic(tmp_path, monkeypatch):
    panels = rv.build_panels_from_state()
    s = rv.make_reality_snapshot(
        produced_at_iso="t", panels=panels,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
    )
    p = tmp_path / "reality.json"
    real = hs.os.replace
    captured = []

    def cap(src, dst):
        captured.append((Path(src).name, Path(dst).name))
        return real(src, dst)

    monkeypatch.setattr(hs.os, "replace", cap)
    hs.save_snapshot(s, p)
    assert any(dst == "reality.json" for _, dst in captured)
    assert any(src.startswith(".reality.") for src, _ in captured)


def test_save_snapshot_failure_cleans_tmp(tmp_path, monkeypatch):
    panels = rv.build_panels_from_state()
    s = rv.make_reality_snapshot(
        produced_at_iso="t", panels=panels,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
    )
    monkeypatch.setattr(hs.os, "replace",
                          lambda *a, **kw: (_ for _ in ()).throw(
                              OSError("simulated")))
    with pytest.raises(OSError):
        hs.save_snapshot(s, tmp_path / "reality.json")
    leftovers = [x for x in tmp_path.iterdir()
                  if x.name.startswith(".reality.")]
    assert leftovers == []


def test_load_snapshot_dict_round_trip(tmp_path):
    panels = rv.build_panels_from_state()
    s = rv.make_reality_snapshot(
        produced_at_iso="t", panels=panels,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
    )
    p = tmp_path / "reality.json"
    hs.save_snapshot(s, p)
    d = hs.load_snapshot_dict(p)
    assert d is not None
    assert d["schema_version"] == rv.REALITY_VIEW_SCHEMA_VERSION


# ═══════════════════ adapter from artifacts ═══════════════════════-

def test_render_from_artifacts_handles_missing_inputs():
    """All inputs missing → snapshot still returns; all panels
    marked unavailable; no crashes."""
    s = ha.render_from_artifacts(
        produced_at_iso="t",
        self_model_path=None,
        world_model_path=None,
        dream_curriculum_path=None,
        hive_review_bundle_path=None,
        vector_graph_path=None,
        missions_path=None,
    )
    assert isinstance(s, rv.RealitySnapshot)
    available = sum(1 for p in s.panels if p.available)
    assert available == 0


def test_render_from_real_self_model_artifact(tmp_path):
    sm = {
        "schema_version": 1,
        "snapshot_id": "real_snap",
        "cells": [{"cell_id": "thermal"}],
        "workspace_tensions": [],
        "blind_spots": [],
        "attention_focus": [],
        "real_data_coverage_ratio": 0.5,
    }
    p = tmp_path / "self_model.json"
    p.write_text(json.dumps(sm), encoding="utf-8")
    s = ha.render_from_artifacts(
        produced_at_iso="t", self_model_path=p,
    )
    sm_panel = next(x for x in s.panels if x.panel_id == "self_model_summary")
    assert sm_panel.available
    assert sm_panel.items[0]["snapshot_id"] == "real_snap"


# ═══════════════════ CLI ═══════════════════════════════════════════

def test_cli_help_exits_zero():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "render_hologram_reality.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    for flag in ("--self-model-path", "--world-model-path",
                  "--dream-curriculum-path", "--hive-review-bundle-path",
                  "--vector-graph-path", "--missions-path",
                  "--apply", "--json"):
        assert flag in r.stdout


def test_cli_dry_run_with_no_inputs(tmp_path):
    """Smoke: empty run renders; all panels unavailable; --json
    output is valid JSON."""
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "render_hologram_reality.py"),
         "--ts", "2026-04-26T03:00:00+00:00", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["panels_total"] >= 11
    assert out["panels_available"] == 0


# ═══════════════════ no secrets / no abs paths in snapshot ═══════-

def test_no_secrets_in_snapshot():
    panels = rv.build_panels_from_state(self_model={
        "schema_version": 1, "snapshot_id": "x",
        "cells": [], "workspace_tensions": [], "blind_spots": [],
        "attention_focus": [],
    })
    s = rv.make_reality_snapshot(
        produced_at_iso="t", panels=panels,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
    )
    blob = json.dumps(s.to_dict()).lower()
    for pat in ("password", "api_key", "secret", "token=",
                 "private key", "begin rsa"):
        assert pat not in blob


def test_no_abs_paths_in_snapshot():
    panels = rv.build_panels_from_state()
    s = rv.make_reality_snapshot(
        produced_at_iso="t", panels=panels,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
    )
    blob = json.dumps(s.to_dict())
    assert "C:\\" not in blob
    assert "/home/" not in blob


# ═══════════════════ source safety ═════════════════════════════════

def test_phase_p_source_safety():
    forbidden = ["import faiss", "from waggledance.runtime",
                  "ollama.generate(", "openai.chat",
                  "anthropic.messages", "requests.post(",
                  "axiom_write(", "promote_to_runtime("]
    pkg = ROOT / "waggledance" / "ui" / "hologram"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"
