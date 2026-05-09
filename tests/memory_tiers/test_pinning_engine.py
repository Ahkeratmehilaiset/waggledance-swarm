# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.memory_tiers.pinning_engine.

Iteration N+2 third scout pick. The pinning engine is the
constitutional guard for foundational identity anchors (Phase 9 §L):
once pinned, an anchor MUST NOT silently demote to cold/glacier
storage; only an explicit unpin (e.g. human archival) may release
it. Pin-status drift would corrupt long-term memory anchors and
let bedrock claims age out into deletion.

Pinned invariants:

- `pin` records a frozen PinRecord under node_id with pinned=True
  and the supplied reason / anchor_status.
- Re-pinning the same node overwrites the prior record (not a
  duplicate or a no-op); reason and anchor_status update.
- `unpin` toggles the record to pinned=False with the canonical
  reason "explicitly_unpinned" and preserves anchor_status.
- `unpin` returns False (no-op) when the node was never pinned or
  was already unpinned — never raises, never inserts a phantom
  record.
- `is_pinned` returns False for unknown ids and for explicitly
  unpinned records; True only when the latest record has
  pinned=True.
- `to_dict` is sorted by node_id and round-trips PinRecord.to_dict.
- `auto_pin_foundational` pins iff anchor_status == "foundational"
  with the canonical "foundational_anchor_auto_pin" reason; returns
  True only when the pin was applied.
- `PinRecord` is a frozen dataclass.
"""
from __future__ import annotations

import dataclasses

import pytest

from waggledance.core.memory_tiers.pinning_engine import (
    PinningEngine,
    PinRecord,
    auto_pin_foundational,
)


# --- pin: insert + record contents --------------------------------

def test_pin_inserts_record_with_pinned_true():
    eng = PinningEngine()
    eng.pin(node_id="anchor-1", reason="seed_identity",
              anchor_status="foundational")
    assert "anchor-1" in eng.pins
    rec = eng.pins["anchor-1"]
    assert rec.pinned is True
    assert rec.pin_reason == "seed_identity"
    assert rec.anchor_status == "foundational"


def test_pin_returns_self_for_chaining():
    """The fluent API allows engine.pin(...).pin(...). Returning
    self is a load-bearing contract."""
    eng = PinningEngine()
    result = eng.pin(node_id="a", reason="r")
    assert result is eng
    result2 = eng.pin(node_id="b", reason="r2").pin(node_id="c", reason="r3")
    assert result2 is eng


def test_pin_anchor_status_optional_defaults_to_none():
    eng = PinningEngine()
    eng.pin(node_id="a", reason="r")
    assert eng.pins["a"].anchor_status is None


def test_pin_overwrites_prior_record_for_same_node():
    """Re-pinning the same node with new reason must update — never
    silently keep the old reason or duplicate."""
    eng = PinningEngine()
    eng.pin(node_id="a", reason="initial",
              anchor_status="supportive")
    eng.pin(node_id="a", reason="upgraded",
              anchor_status="foundational")
    rec = eng.pins["a"]
    assert rec.pin_reason == "upgraded"
    assert rec.anchor_status == "foundational"
    assert len(eng.pins) == 1


# --- unpin --------------------------------------------------------

def test_unpin_returns_true_and_flips_pinned_to_false():
    eng = PinningEngine()
    eng.pin(node_id="a", reason="seed", anchor_status="foundational")
    result = eng.unpin("a")
    assert result is True
    rec = eng.pins["a"]
    assert rec.pinned is False
    assert rec.pin_reason == "explicitly_unpinned"
    # anchor_status preserved across the unpin transition
    assert rec.anchor_status == "foundational"


def test_unpin_unknown_node_returns_false_no_record_inserted():
    """unpin on a never-pinned id must NOT insert a phantom
    pinned=False record — that would pollute to_dict output."""
    eng = PinningEngine()
    result = eng.unpin("never-existed")
    assert result is False
    assert "never-existed" not in eng.pins


def test_unpin_already_unpinned_returns_false():
    """unpin twice in a row: first returns True, second returns
    False because the node is already unpinned."""
    eng = PinningEngine()
    eng.pin(node_id="a", reason="r")
    first = eng.unpin("a")
    second = eng.unpin("a")
    assert first is True
    assert second is False
    # the record stays in the unpinned state — not removed.
    assert "a" in eng.pins
    assert eng.pins["a"].pinned is False


# --- is_pinned ----------------------------------------------------

def test_is_pinned_false_for_unknown_id():
    eng = PinningEngine()
    assert eng.is_pinned("unknown") is False


def test_is_pinned_true_only_when_latest_record_is_pinned():
    eng = PinningEngine()
    eng.pin(node_id="a", reason="r")
    assert eng.is_pinned("a") is True
    eng.unpin("a")
    assert eng.is_pinned("a") is False


# --- to_dict round-trip + sort order ------------------------------

def test_to_dict_sorted_by_node_id():
    """Determinism: to_dict order must be sorted regardless of
    insert order, so consumers (audit logs, snapshots) get stable
    output."""
    eng = PinningEngine()
    for nid in ["c-anchor", "a-anchor", "b-anchor"]:
        eng.pin(node_id=nid, reason="r")
    keys = list(eng.to_dict().keys())
    assert keys == ["a-anchor", "b-anchor", "c-anchor"]


def test_to_dict_round_trip_carries_pinrecord_fields():
    eng = PinningEngine()
    eng.pin(node_id="a", reason="seed", anchor_status="foundational")
    snap = eng.to_dict()
    assert snap == {
        "a": {
            "node_id": "a", "pinned": True,
            "pin_reason": "seed", "anchor_status": "foundational",
        },
    }


def test_to_dict_carries_unpinned_records():
    """Unpinned nodes must STILL appear in to_dict (with pinned=False)
    because audit logs depend on knowing that an anchor was
    intentionally released, not silently dropped."""
    eng = PinningEngine()
    eng.pin(node_id="a", reason="r", anchor_status="supportive")
    eng.unpin("a")
    snap = eng.to_dict()
    assert "a" in snap
    assert snap["a"]["pinned"] is False
    assert snap["a"]["pin_reason"] == "explicitly_unpinned"
    assert snap["a"]["anchor_status"] == "supportive"


# --- auto_pin_foundational ----------------------------------------

def test_auto_pin_foundational_pins_only_for_foundational_status():
    eng = PinningEngine()
    applied = auto_pin_foundational(
        eng, node_id="a", anchor_status="foundational",
    )
    assert applied is True
    assert eng.is_pinned("a") is True
    assert eng.pins["a"].pin_reason == "foundational_anchor_auto_pin"
    assert eng.pins["a"].anchor_status == "foundational"


@pytest.mark.parametrize("status", [
    "candidate",
    "supportive",
    "rejected",
    "archived",
    "",
])
def test_auto_pin_foundational_no_op_for_non_foundational(status):
    """auto_pin must only fire for the literal "foundational" status.
    Any other anchor_status — including the four non-foundational
    statuses defined in vector_identity ANCHOR_STATUSES — must
    leave the engine untouched."""
    eng = PinningEngine()
    applied = auto_pin_foundational(
        eng, node_id="a", anchor_status=status,
    )
    assert applied is False
    assert eng.is_pinned("a") is False
    assert "a" not in eng.pins


def test_auto_pin_foundational_idempotent_for_repeat_calls():
    """Repeatedly auto-pinning the same foundational anchor must
    return True (the pin is applied) but produce a single record;
    no duplicate dict entries."""
    eng = PinningEngine()
    a1 = auto_pin_foundational(eng, node_id="a",
                                       anchor_status="foundational")
    a2 = auto_pin_foundational(eng, node_id="a",
                                       anchor_status="foundational")
    assert a1 is True
    assert a2 is True
    assert len(eng.pins) == 1


# --- PinRecord dataclass contract ---------------------------------

def test_pin_record_is_frozen():
    """Frozen so a stale reference cannot mutate a pin record
    behind the engine's back."""
    rec = PinRecord(node_id="a", pinned=True,
                       pin_reason="r", anchor_status=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        rec.pinned = False  # type: ignore[misc]
