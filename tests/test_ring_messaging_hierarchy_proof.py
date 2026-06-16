"""Offline tests for tools/run_ring_messaging_hierarchy_proof.py.

Uses the real in-repo hex-topology ring messaging + hierarchy functions
(deterministic, pure). Asserts hierarchy consistency, ring delivery boundary
enforcement (valid delivers, invalid blocked), determinism, and that no message
crosses an invalid boundary.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "run_ring_messaging_hierarchy_proof",
    REPO_ROOT / "tools" / "run_ring_messaging_hierarchy_proof.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


def test_proof_ok():
    report = mod.build_ring_messaging_hierarchy_proof()
    assert report["ok"] is True
    assert report["blockers"] == []


def test_hierarchy_consistency():
    h = mod.build_ring_messaging_hierarchy_proof()["hierarchy"]
    assert h["ok"] is True
    assert all(h["checks"].values())


def test_ring_boundary_all_cases_enforced():
    r = mod.build_ring_messaging_hierarchy_proof()["ring_boundary"]
    assert r["ok"] is True
    assert r["no_runtime_mutation"] is True
    # every case matched its expected delivered/blocked outcome
    assert all(c["ok"] for c in r["cases"])
    # no message delivered across an invalid boundary
    for c in r["cases"]:
        if c["expected_delivered"] is False:
            assert c["delivered"] is False


def test_determinism():
    d = mod.build_ring_messaging_hierarchy_proof()["deterministic_replay"]
    assert d["batch_identical"] is True
    assert d["delivered_count"] >= 1


def test_invariants_and_clean_summary():
    report = mod.build_ring_messaging_hierarchy_proof()
    inv = report["invariants"]
    for flag in ("deterministic_offline", "no_runtime_mutation",
                 "no_invalid_boundary_delivery"):
        assert inv[flag] is True
    summary = mod.render_summary(report)
    # word-boundary guard: must not false-trip on "messaging"
    mod.assert_vocabulary_clean(summary)
    assert "messaging" in summary.lower()


def test_main_json_exit0():
    assert mod.main(["--json"]) == 0


def test_out_dir_writes_artifact(tmp_path):
    out = tmp_path / "proof_out"
    assert mod.main(["--out-dir", str(out)]) == 0
    artifact = out / "ring_messaging_hierarchy_proof.json"
    assert artifact.is_file()
    assert json.loads(artifact.read_text(encoding="utf-8"))["ok"] is True


def test_proof_fails_closed_if_delivery_layer_delivers_everything(monkeypatch):
    # Adversarial forge (per the #1253 happy-path lesson): a broken delivery
    # layer that delivers EVERY message - including those that should be blocked
    # at an invalid boundary - must make the proof fail CLOSED, not pass.
    from waggledance.core.hex_topology.ring_messaging import RingDelivery

    def _always_deliver(topology, msg, seq):
        return RingDelivery(
            message_id_seq=seq, delivered=True, blocked_reason=None,
            msg=msg, blocked_category=None,
        )

    monkeypatch.setattr(mod, "deliver_one", _always_deliver)
    report = mod.build_ring_messaging_hierarchy_proof()
    assert report["ok"] is False
    assert "ring_boundary_violation" in report["blockers"]
    assert report["invariants"]["no_invalid_boundary_delivery"] is False


def test_proof_fails_closed_if_delivery_layer_blocks_everything(monkeypatch):
    # Mirror forge: a layer that blocks EVERYTHING (even valid neighbor/parent/
    # child messages) must also fail the proof closed.
    from waggledance.core.hex_topology.ring_messaging import (
        RING_BLOCK_SCHEMA_INVALID,
        RingDelivery,
    )

    def _always_block(topology, msg, seq):
        return RingDelivery(
            message_id_seq=seq, delivered=False, blocked_reason="forged",
            msg=msg, blocked_category=RING_BLOCK_SCHEMA_INVALID,
        )

    monkeypatch.setattr(mod, "deliver_one", _always_block)
    report = mod.build_ring_messaging_hierarchy_proof()
    assert report["ok"] is False
    assert "ring_boundary_violation" in report["blockers"]
