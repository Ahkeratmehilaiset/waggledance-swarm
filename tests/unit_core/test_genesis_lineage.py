# SPDX-License-Identifier: BUSL-1.1
"""Adversarial matrix for GenesisLineageV1 (W2A pure contract).

Locks the hash-chain both ways: SURJECTIVE-side liveness (a genuine
root->child->grandchild chain verifies end to end, fan-out allowed) and the
INJECTIVE rule (one lineage entry per cell_id). Root markers are checked in
every direction, tamper breaks the entry hash, replay shapes are rejected.
"""

from __future__ import annotations

import pytest

from waggledance.core.genesis_lineage import (
    GENESIS_PREV_HASH,
    GENESIS_ROOT_PARENT,
    LINEAGE_KEYS,
    SCHEMA_VERSION,
    GenesisLineageError,
    GenesisLineageV1,
    build_child_entry,
    build_root_entry,
    derive_entry_hash,
    verify_lineage_entry,
    verify_lineage_link,
    verify_lineage_registry,
)

_ROOT_ID = "sha256:" + "1" * 64
_CHILD_ID = "sha256:" + "2" * 64
_GRANDCHILD_ID = "sha256:" + "3" * 64
_SIBLING_ID = "sha256:" + "4" * 64
_GOAL = "sha256:" + "d" * 64
_BUDGET = "sha256:" + "e" * 64


def _root():
    return build_root_entry(
        cell_id=_ROOT_ID,
        inherited_goal_slice_digest=_GOAL,
        inherited_budget_slice_digest=_BUDGET,
    )


def _child(parent=None, cell_id=_CHILD_ID):
    parent = parent if parent is not None else _root()
    return build_child_entry(
        cell_id=cell_id,
        parent_entry=parent.to_mapping(),
        inherited_goal_slice_digest=_GOAL,
        inherited_budget_slice_digest=_BUDGET,
    )


def test_root_entry_builds_and_verifies():
    root = _root()
    assert root.parent_cell_id == GENESIS_ROOT_PARENT
    assert root.depth == 0
    assert root.lineage_prev_hash == GENESIS_PREV_HASH
    assert verify_lineage_entry(root.to_mapping()) == (True, None)


def test_three_deep_chain_verifies_end_to_end():
    root, child = _root(), _child()
    grandchild = build_child_entry(
        cell_id=_GRANDCHILD_ID,
        parent_entry=child.to_mapping(),
        inherited_goal_slice_digest=_GOAL,
        inherited_budget_slice_digest=_BUDGET,
    )
    assert grandchild.depth == 2
    assert verify_lineage_link(child.to_mapping(), root.to_mapping()) == (
        True,
        None,
    )
    assert verify_lineage_link(
        grandchild.to_mapping(), child.to_mapping()
    ) == (True, None)


def test_parent_fanout_is_legal_but_duplicate_cell_id_is_not():
    """The bijection split: many children per parent OK (surjective side);
    two lineage entries for ONE cell forged (injective side)."""
    root = _root()
    first = _child(root, _CHILD_ID)
    second = _child(root, _SIBLING_ID)
    ok, reason = verify_lineage_registry(
        [root.to_mapping(), first.to_mapping(), second.to_mapping()]
    )
    assert (ok, reason) == (True, None)

    duplicate = _child(root, _CHILD_ID).to_mapping()
    ok, reason = verify_lineage_registry(
        [root.to_mapping(), first.to_mapping(), duplicate]
    )
    assert ok is False
    assert reason is not None and reason.endswith("duplicate_cell_id")


@pytest.mark.parametrize("field", sorted(LINEAGE_KEYS - {"schema_version"}))
def test_tamper_any_field_breaks_entry_hash_or_shape(field):
    tampered = _child().to_mapping()
    if field == "depth":
        tampered[field] = 5
    elif field == "entry_hash":
        tampered[field] = "sha256:" + "f" * 64
    else:
        tampered[field] = "sha256:" + "9" * 64
    ok, _ = verify_lineage_entry(tampered)
    assert ok is False


@pytest.mark.parametrize("key", sorted(LINEAGE_KEYS))
def test_absent_key_rejected(key):
    broken = _child().to_mapping()
    del broken[key]
    assert verify_lineage_entry(broken) == (False, "keyset")


@pytest.mark.parametrize("key", sorted(LINEAGE_KEYS))
def test_present_null_rejected_distinct_from_absent(key):
    broken = _child().to_mapping()
    broken[key] = None
    ok, reason = verify_lineage_entry(broken)
    assert ok is False
    assert reason != "keyset"


@pytest.mark.parametrize("key", sorted(LINEAGE_KEYS))
def test_present_wrong_type_rejected(key):
    broken = _child().to_mapping()
    broken[key] = ["wrong"]
    ok, _ = verify_lineage_entry(broken)
    assert ok is False


def test_root_markers_must_agree_in_all_directions():
    root_map = _root().to_mapping()
    child_map = _child().to_mapping()

    # Non-root wearing ONE root marker at a time -> rejected.
    sentinel_only = dict(child_map, parent_cell_id=GENESIS_ROOT_PARENT)
    depth_only = dict(child_map, depth=0)
    prev_only = dict(child_map, lineage_prev_hash=GENESIS_PREV_HASH)
    # Root missing ONE marker at a time -> rejected.
    root_wrong_depth = dict(root_map, depth=1)
    root_wrong_prev = dict(root_map, lineage_prev_hash="sha256:" + "8" * 64)
    for broken in (
        sentinel_only,
        depth_only,
        prev_only,
        root_wrong_depth,
        root_wrong_prev,
    ):
        ok, _ = verify_lineage_entry(broken)
        assert ok is False


def test_self_parent_rejected():
    root = _root()
    with pytest.raises(GenesisLineageError, match="own parent"):
        build_child_entry(
            cell_id=root.cell_id,
            parent_entry=root.to_mapping(),
            inherited_goal_slice_digest=_GOAL,
            inherited_budget_slice_digest=_BUDGET,
        )


def test_child_from_forged_parent_rejected():
    forged_parent = _root().to_mapping()
    forged_parent["entry_hash"] = "sha256:" + "f" * 64
    with pytest.raises(GenesisLineageError, match="parent entry rejected"):
        build_child_entry(
            cell_id=_CHILD_ID,
            parent_entry=forged_parent,
            inherited_goal_slice_digest=_GOAL,
            inherited_budget_slice_digest=_BUDGET,
        )


def test_link_rejects_wrong_parent_and_skipped_depth():
    root, child = _root(), _child()
    other_root = build_root_entry(
        cell_id=_SIBLING_ID,
        inherited_goal_slice_digest=_GOAL,
        inherited_budget_slice_digest=_BUDGET,
    )
    ok, reason = verify_lineage_link(
        child.to_mapping(), other_root.to_mapping()
    )
    assert (ok, reason) == (False, "parent_cell_id_mismatch")

    # Hand-built depth-skip: correct prev/parent but depth jumps by 2.
    skipped = dict(child.to_mapping(), depth=2)
    skipped["entry_hash"] = derive_entry_hash(
        cell_id=skipped["cell_id"],
        parent_cell_id=skipped["parent_cell_id"],
        lineage_prev_hash=skipped["lineage_prev_hash"],
        depth=2,
        inherited_goal_slice_digest=_GOAL,
        inherited_budget_slice_digest=_BUDGET,
    )
    ok, reason = verify_lineage_link(skipped, root.to_mapping())
    assert (ok, reason) == (False, "depth_mismatch")


def test_replay_parent_as_its_own_child_rejected():
    """An entry resubmitted 'one deeper' under itself is a forged shape."""
    root = _root()
    replay = dict(
        root.to_mapping(),
        parent_cell_id=root.cell_id,
        lineage_prev_hash=root.entry_hash,
        depth=1,
    )
    replay["entry_hash"] = derive_entry_hash(
        cell_id=replay["cell_id"],
        parent_cell_id=replay["parent_cell_id"],
        lineage_prev_hash=replay["lineage_prev_hash"],
        depth=1,
        inherited_goal_slice_digest=_GOAL,
        inherited_budget_slice_digest=_BUDGET,
    )
    ok, reason = verify_lineage_entry(replay)
    assert ok is False
    assert reason is not None and "own parent" in reason


def test_registry_closure_empty_fails_closed():
    assert verify_lineage_registry([]) == (False, "empty_registry")


def test_registry_closure_rootless_orphans_rejected():
    """An orphan-only registry (children, no root) must not read as valid."""
    root, child = _root(), _child()
    ok, reason = verify_lineage_registry([child.to_mapping()])
    assert (ok, reason) == (False, "no_root")
    grandchild = build_child_entry(
        cell_id=_GRANDCHILD_ID,
        parent_entry=child.to_mapping(),
        inherited_goal_slice_digest=_GOAL,
        inherited_budget_slice_digest=_BUDGET,
    )
    ok, reason = verify_lineage_registry(
        [child.to_mapping(), grandchild.to_mapping()]
    )
    assert (ok, reason) == (False, "no_root")
    del root  # silence unused warning


def test_registry_closure_two_roots_rejected():
    other_root = build_root_entry(
        cell_id=_SIBLING_ID,
        inherited_goal_slice_digest=_GOAL,
        inherited_budget_slice_digest=_BUDGET,
    )
    ok, reason = verify_lineage_registry(
        [_root().to_mapping(), other_root.to_mapping()]
    )
    assert (ok, reason) == (False, "multiple_roots")


def test_registry_closure_orphan_parent_not_in_registry_rejected():
    root, child = _root(), _child()
    grandchild = build_child_entry(
        cell_id=_GRANDCHILD_ID,
        parent_entry=child.to_mapping(),
        inherited_goal_slice_digest=_GOAL,
        inherited_budget_slice_digest=_BUDGET,
    )
    # child (grandchild's parent) omitted -> orphan even though root exists
    ok, reason = verify_lineage_registry(
        [root.to_mapping(), grandchild.to_mapping()]
    )
    assert ok is False
    assert reason is not None and reason.endswith(
        "orphan_parent_not_in_registry"
    )


def test_registry_closure_rehashed_child_link_failure_rejected():
    """The lead's repro: a child naming the real in-registry parent but
    REHASHED over a wrong prev -- self-consistent per entry, broken as a
    link -- must fail the registry."""
    root = _root()
    forged = _build_forged_child_with_wrong_prev(root)
    assert verify_lineage_entry(forged) == (True, None)  # self-consistent...
    ok, reason = verify_lineage_registry([root.to_mapping(), forged])
    assert ok is False  # ...but the registry closure catches the broken link
    assert reason is not None and "link:prev_hash_mismatch" in reason


def _build_forged_child_with_wrong_prev(root):
    wrong_prev = "sha256:" + "9" * 64
    return {
        "schema_version": SCHEMA_VERSION,
        "cell_id": _CHILD_ID,
        "parent_cell_id": root.cell_id,
        "lineage_prev_hash": wrong_prev,
        "depth": 1,
        "inherited_goal_slice_digest": _GOAL,
        "inherited_budget_slice_digest": _BUDGET,
        "entry_hash": derive_entry_hash(
            cell_id=_CHILD_ID,
            parent_cell_id=root.cell_id,
            lineage_prev_hash=wrong_prev,
            depth=1,
            inherited_goal_slice_digest=_GOAL,
            inherited_budget_slice_digest=_BUDGET,
        ),
    }


def test_registry_closure_depth_skip_rejected():
    root = _root()
    skipped = {
        "schema_version": SCHEMA_VERSION,
        "cell_id": _CHILD_ID,
        "parent_cell_id": root.cell_id,
        "lineage_prev_hash": root.entry_hash,
        "depth": 2,
        "inherited_goal_slice_digest": _GOAL,
        "inherited_budget_slice_digest": _BUDGET,
    }
    skipped["entry_hash"] = derive_entry_hash(
        cell_id=_CHILD_ID,
        parent_cell_id=root.cell_id,
        lineage_prev_hash=root.entry_hash,
        depth=2,
        inherited_goal_slice_digest=_GOAL,
        inherited_budget_slice_digest=_BUDGET,
    )
    ok, reason = verify_lineage_registry([root.to_mapping(), skipped])
    assert ok is False
    assert reason is not None and "link:depth_mismatch" in reason


def test_registry_rejects_one_shot_iterables_fail_closed():
    """The declared contract is Sequence. A generator/iterator is rejected
    fail-closed (not_sequence) -- NOT materialized -- so a forged child cannot
    slip past an exhausted second pass, and a hostile/infinite generator can
    never hang or OOM the verifier."""
    root = _root()
    forged = _build_forged_child_with_wrong_prev(root)

    def gen_valid():
        yield root.to_mapping()
        yield _child().to_mapping()

    def gen_forged():
        yield root.to_mapping()
        yield forged

    # Even a would-be-VALID generator is rejected: the contract is Sequence.
    assert verify_lineage_registry(gen_valid()) == (False, "not_sequence")
    assert verify_lineage_registry(gen_forged()) == (False, "not_sequence")
    assert verify_lineage_registry(iter([root.to_mapping()])) == (
        False,
        "not_sequence",
    )


def test_registry_accepts_tuple_sequence():
    """A tuple is a valid re-iterable Sequence and must verify like a list."""
    root, child = _root(), _child()
    assert verify_lineage_registry(
        (root.to_mapping(), child.to_mapping())
    ) == (True, None)


@pytest.mark.parametrize("value", ["", "abc", b"bytes", None, 7, {"a": 1}])
def test_registry_non_sequence_inputs_rejected(value):
    assert verify_lineage_registry(value) == (False, "not_sequence")


def test_registry_closure_is_order_independent():
    """A child listed BEFORE its parent must still verify (no order coupling)."""
    root, child = _root(), _child()
    grandchild = build_child_entry(
        cell_id=_GRANDCHILD_ID,
        parent_entry=child.to_mapping(),
        inherited_goal_slice_digest=_GOAL,
        inherited_budget_slice_digest=_BUDGET,
    )
    shuffled = [grandchild.to_mapping(), root.to_mapping(), child.to_mapping()]
    assert verify_lineage_registry(shuffled) == (True, None)


def test_schema_version_pinned():
    wrong = _child().to_mapping()
    wrong["schema_version"] = "wd.genesis_lineage.v2"
    assert verify_lineage_entry(wrong) == (False, "schema_version")


def test_bool_depth_rejected():
    """bool is an int subclass; a True depth must not sneak past."""
    broken = _child().to_mapping()
    broken["depth"] = True
    ok, _ = verify_lineage_entry(broken)
    assert ok is False


def test_non_mapping_inputs_fail_closed():
    for value in (None, "entry", 7, ["x"]):
        assert verify_lineage_entry(value) == (False, "not_mapping")


def test_forged_instance_cannot_be_constructed():
    with pytest.raises(GenesisLineageError, match="derived entry digest"):
        GenesisLineageV1(
            cell_id=_CHILD_ID,
            parent_cell_id=_ROOT_ID,
            lineage_prev_hash="sha256:" + "7" * 64,
            depth=1,
            inherited_goal_slice_digest=_GOAL,
            inherited_budget_slice_digest=_BUDGET,
            entry_hash="sha256:" + "f" * 64,
        )


def test_registry_rejects_malformed_member():
    root = _root()
    broken = _child().to_mapping()
    broken["depth"] = None
    ok, reason = verify_lineage_registry([root.to_mapping(), broken])
    assert ok is False
    assert reason is not None and reason.startswith("entry_1:")


def test_determinism_same_inputs_same_hashes():
    assert all(
        build_root_entry(
            cell_id=_ROOT_ID,
            inherited_goal_slice_digest=_GOAL,
            inherited_budget_slice_digest=_BUDGET,
        ).entry_hash
        == _root().entry_hash
        for _ in range(100)
    )
