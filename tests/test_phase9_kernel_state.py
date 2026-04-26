# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for waggledance/core/autonomy/kernel_state.py
(Phase 9 §F.kernel_state).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy import kernel_state as ks


def _make_state() -> ks.KernelState:
    return ks.initial_state(
        constitution_id="wd_autonomy_constitution_v1",
        constitution_sha256="sha256:" + "a" * 64,
        pinned_input_manifest_sha256="sha256:test",
        branch_name="phase9/autonomy-fabric",
        base_commit_hash="ddb0821",
    )


def test_initial_state_has_defaults():
    s = _make_state()
    assert s.schema_version == ks.KERNEL_STATE_SCHEMA_VERSION
    assert s.last_tick is None
    assert s.next_tick_id == 1
    assert len(s.budgets) >= 4
    assert len(s.circuit_breakers) >= 4
    assert s.actions_recommended_total == 0
    assert s.persisted_revision == 0


def test_default_breakers_start_closed():
    s = _make_state()
    for b in s.circuit_breakers:
        assert b.state == "closed"
        assert b.consecutive_failures == 0
        assert b.quarantined is False


def test_default_budgets_have_hard_caps():
    s = _make_state()
    for b in s.budgets:
        assert b.hard_cap > 0
        assert b.reserved == 0.0
        assert b.consumed == 0.0
        assert b.remaining() == b.hard_cap


def test_with_tick_advances_counter_and_revision():
    s1 = _make_state()
    s2 = ks.with_tick(s1, ts_iso="2026-04-26T03:00:00+00:00")
    assert s2.last_tick is not None
    assert s2.last_tick.tick_id == 1
    assert s2.next_tick_id == 2
    assert s2.persisted_revision == 1
    s3 = ks.with_tick(s2, ts_iso="2026-04-26T03:01:00+00:00")
    assert s3.last_tick.tick_id == 2
    assert s3.next_tick_id == 3
    assert s3.persisted_revision == 2


def test_with_tick_does_not_mutate_input():
    s1 = _make_state()
    s2 = ks.with_tick(s1, ts_iso="2026-04-26T03:00:00+00:00")
    # original unchanged
    assert s1.next_tick_id == 1
    assert s1.last_tick is None


def test_with_actions_recommended_accumulates():
    s = _make_state()
    s = ks.with_actions_recommended(s, 3)
    s = ks.with_actions_recommended(s, 7)
    assert s.actions_recommended_total == 10
    # Negative inputs are ignored (treated as 0)
    s = ks.with_actions_recommended(s, -5)
    assert s.actions_recommended_total == 10


def test_canonical_json_is_byte_stable():
    s = _make_state()
    j1 = ks.to_canonical_json(s)
    j2 = ks.to_canonical_json(s)
    assert j1 == j2


def test_save_and_load_round_trip(tmp_path):
    s = _make_state()
    s = ks.with_tick(s, ts_iso="2026-04-26T03:00:00+00:00")
    s = ks.with_actions_recommended(s, 4)
    p = tmp_path / "kernel_state.json"
    ks.save_state(s, p)
    s2 = ks.load_state(p)
    assert s2 is not None
    assert s2.last_tick is not None
    assert s2.last_tick.tick_id == 1
    assert s2.next_tick_id == 2
    assert s2.actions_recommended_total == 4
    assert s2.persisted_revision == 1
    assert s2.constitution_sha256 == s.constitution_sha256


def test_load_missing_returns_none(tmp_path):
    assert ks.load_state(tmp_path / "nope.json") is None


def test_save_uses_tmp_plus_replace_atomic(tmp_path, monkeypatch):
    """save_state must write via NamedTemporaryFile + os.replace, not
    open(target, 'w') directly. Verify by capturing os.replace calls
    in the module namespace."""
    real = ks.os.replace
    captured = []

    def cap(src, dst):
        captured.append((Path(src).name, Path(dst).name))
        return real(src, dst)

    monkeypatch.setattr(ks.os, "replace", cap)
    s = _make_state()
    ks.save_state(s, tmp_path / "kernel_state.json")
    assert any(dst == "kernel_state.json" for _, dst in captured)
    assert any(src.startswith(".kernel_state.") for src, _ in captured)


def test_save_failure_cleans_up_tmp(tmp_path, monkeypatch):
    """If os.replace fails, the tmp file must not be left behind."""
    s = _make_state()

    def boom(*a, **kw):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(ks.os, "replace", boom)
    with pytest.raises(OSError):
        ks.save_state(s, tmp_path / "kernel_state.json")
    # No leftover tmp file
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".kernel_state.")]
    assert leftovers == []


def test_load_handles_old_v1_without_optional_fields(tmp_path):
    """Backwards compat: a state written with only required fields
    must still parse."""
    minimal = {
        "schema_version": 1,
        "last_tick": None,
        "next_tick_id": 5,
        "budgets": [],
        "circuit_breakers": [],
        "constitution_id": "wd_autonomy_constitution_v1",
        "constitution_sha256": "sha256:abc",
    }
    p = tmp_path / "old_state.json"
    p.write_text(json.dumps(minimal), encoding="utf-8")
    s = ks.load_state(p)
    assert s is not None
    assert s.next_tick_id == 5
    assert s.capsule_context == "neutral_v1"   # default applied


def test_budget_remaining_clamped_at_zero():
    b = ks.BudgetEntry(name="x", hard_cap=10.0, reserved=2.0, consumed=15.0)
    assert b.remaining() == 0.0


def test_budget_remaining_when_under():
    b = ks.BudgetEntry(name="x", hard_cap=10.0, reserved=2.0, consumed=3.0)
    assert b.remaining() == 7.0


def test_state_is_frozen_dataclass():
    s = _make_state()
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        s.next_tick_id = 999


def test_capsule_context_defaults_to_neutral():
    s = _make_state()
    assert s.capsule_context == "neutral_v1"


def test_with_budgets_replaces_tuple():
    s = _make_state()
    new_budgets = (
        ks.BudgetEntry(name="custom", hard_cap=999.0),
    )
    s2 = ks.with_budgets(s, new_budgets)
    assert s2.budgets == new_budgets
    assert s.budgets != new_budgets   # original untouched


def test_with_breakers_replaces_tuple():
    s = _make_state()
    new_breakers = (
        ks.CircuitBreakerSnapshot(name="custom", state="open",
                                       consecutive_failures=3,
                                       last_transition_tick=42,
                                       quarantined=True),
    )
    s2 = ks.with_breakers(s, new_breakers)
    assert s2.circuit_breakers == new_breakers
    assert s.circuit_breakers != new_breakers
