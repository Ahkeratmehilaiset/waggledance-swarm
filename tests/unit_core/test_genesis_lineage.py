# SPDX-License-Identifier: BUSL-1.1
"""Adversarial matrix for GenesisLineageV1 (W2A pure contract).

Locks the hash-chain both ways: SURJECTIVE-side liveness (a genuine
root->child->grandchild chain verifies end to end, fan-out allowed) and the
INJECTIVE rule (one lineage entry per cell_id). Root markers are checked in
every direction, tamper breaks the entry hash, replay shapes are rejected.
"""

from __future__ import annotations

from collections.abc import Sequence

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
    """An entry resubmitted 'one deeper' under itself is a forged shape.
    derive_entry_hash now rejects it directly; a forger who hand-computes the
    canonical digest to bypass that is still caught by verify_lineage_entry."""
    from waggledance.core.genesis_lineage import DIGEST_DOMAIN
    from waggledance.core.magma.canonical import sha256_digest

    root = _root()
    # derive rejects the self-parent shape at digest time (earliest defence).
    with pytest.raises(GenesisLineageError, match="own parent"):
        derive_entry_hash(
            cell_id=root.cell_id,
            parent_cell_id=root.cell_id,
            lineage_prev_hash=root.entry_hash,
            depth=1,
            inherited_goal_slice_digest=_GOAL,
            inherited_budget_slice_digest=_BUDGET,
        )

    # A forger hand-rolls a matching entry_hash to skip derive's validation;
    # verify_lineage_entry must still reject the self-parent.
    replay = dict(
        root.to_mapping(),
        parent_cell_id=root.cell_id,
        lineage_prev_hash=root.entry_hash,
        depth=1,
    )
    replay["entry_hash"] = sha256_digest(
        {
            "domain": DIGEST_DOMAIN,
            "schema_version": SCHEMA_VERSION,
            "cell_id": replay["cell_id"],
            "parent_cell_id": replay["parent_cell_id"],
            "lineage_prev_hash": replay["lineage_prev_hash"],
            "depth": 1,
            "inherited_goal_slice_digest": _GOAL,
            "inherited_budget_slice_digest": _BUDGET,
        }
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


class _FlappingSequence(Sequence):
    """A malicious Sequence SUBCLASS whose child-index item flaps between reads.
    The exact-list/tuple gate rejects it outright -- its overridden __getitem__/
    __len__ are never invoked, so a flapping (or non-returning) Sequence cannot
    reach any pass at all."""

    def __init__(self, root_map, bad_child_map):
        self._root = root_map
        self._bad = bad_child_map
        self.reads = 0

    def __len__(self):
        raise AssertionError("len must never be called")

    def __getitem__(self, index):
        self.reads += 1
        if index == 0:
            return self._root
        return self._bad if self.reads == 1 else self._root


def test_registry_flapping_sequence_rejected_without_invoking_protocol():
    """The lead's TOCTOU repro is closed by construction: an arbitrary Sequence
    subclass is rejected as not_sequence BEFORE its len/getitem run, so a
    flapping or non-returning Sequence can never present data to any pass."""
    root = _root()
    bad = _build_forged_child_with_wrong_prev(root)
    flapping = _FlappingSequence(root.to_mapping(), bad)
    assert verify_lineage_registry(flapping) == (False, "not_sequence")
    assert flapping.reads == 0  # overridden Sequence protocol never touched


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


class _EqAnyStr(str):
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    def __hash__(self):
        return 0


class _LyingDepth(int):
    """An int subclass; exact-type checks must reject it as depth."""


@pytest.mark.parametrize(
    "key",
    ["cell_id", "parent_cell_id", "lineage_prev_hash", "entry_hash",
     "inherited_goal_slice_digest", "inherited_budget_slice_digest"],
)
def test_eq_any_str_subclass_field_rejected(key):
    broken = _child().to_mapping()
    broken[key] = _EqAnyStr(broken[key])
    ok, _ = verify_lineage_entry(broken)
    assert ok is False


def test_lying_depth_int_subclass_rejected():
    broken = _child().to_mapping()
    broken["depth"] = _LyingDepth(1)
    ok, _ = verify_lineage_entry(broken)
    assert ok is False


def test_eq_any_str_schema_rejected():
    broken = _child().to_mapping()
    broken["schema_version"] = _EqAnyStr("anything")
    assert verify_lineage_entry(broken) == (False, "schema_version")


def test_link_rejects_dict_subclass_parent_without_invoking_protocol():
    """A parent that is a dict SUBCLASS with an overridden __getitem__ (which
    would flip parent identity between reads) is rejected outright as
    parent:not_mapping -- the exact-dict gate never invokes the overridden
    protocol, so a flapping/hostile parent can never even reach the link check."""
    root, child = _root(), _child()
    good_parent = root.to_mapping()
    invoked = {"getitem": False}

    class _FlipParentId(dict):
        def __getitem__(self, key):
            invoked["getitem"] = True
            if key == "cell_id":
                return "sha256:" + "9" * 64
            return super().__getitem__(key)

    ok, reason = verify_lineage_link(child.to_mapping(), _FlipParentId(good_parent))
    assert (ok, reason) == (False, "parent:not_mapping")
    assert invoked["getitem"] is False  # overridden protocol never touched


def test_alias_key_mapping_boundary_fail_closed():
    good = _child().to_mapping()
    alias = {k: v for k, v in good.items() if k != "cell_id"}
    alias[_EqAnyStr("cell_id")] = good["cell_id"]
    assert verify_lineage_entry(alias) == (False, "not_mapping")


def test_wire_dict_returns_an_isolated_builtin_copy():
    """The exact-dict boundary copies once: the returned snapshot equals the
    input but is a distinct object, and mutating either side afterwards does not
    affect the other."""
    from waggledance.core.genesis_lineage import _require_wire_dict

    src = {"a": 1, "b": 2}
    snap = _require_wire_dict(src)
    assert snap == src and snap is not src
    snap["a"] = 999
    assert src["a"] == 1
    src["c"] = 3
    assert "c" not in snap


def test_registry_snapshots_input_list_before_processing():
    """A plain list is accepted (liveness) and processed from a private tuple
    snapshot: mutating the original list AFTER the call cannot retro-change the
    already-returned verdict, and re-verifying the mutated list reflects it."""
    root, child = _root(), _child()
    entries = [root.to_mapping(), child.to_mapping()]
    assert verify_lineage_registry(entries) == (True, None)
    entries.append({"garbage": True})  # mutate the original container
    # the prior verdict stands; a fresh call sees the mutated (now-invalid) list
    ok, _ = verify_lineage_registry(entries)
    assert ok is False


def test_frozen_is_not_a_trust_boundary_forced_mutation_caught_by_verifier():
    """frozen=True is ergonomic only: object.__setattr__ bypasses it and mutates
    a validated entry, and to_mapping emits the forged field. The real,
    verification-time guarantee holds: verify_lineage_entry re-derives
    entry_hash and rejects the mutated mapping under its old hash."""
    child = _child()
    object.__setattr__(child, "inherited_goal_slice_digest", "sha256:" + "e" * 64)
    forged = child.to_mapping()  # emits the mutated (still shape-valid) field
    assert forged["inherited_goal_slice_digest"] == "sha256:" + "e" * 64
    assert verify_lineage_entry(forged) == (False, "entry_hash_mismatch")


def test_derive_entry_hash_validates_inputs():
    """The public digest fn follows the exact-primitive contract: a subclass
    field cannot be folded into a lineage digest."""
    with pytest.raises(GenesisLineageError):
        derive_entry_hash(
            cell_id=_EqAnyStr("sha256:" + "1" * 64),
            parent_cell_id=GENESIS_ROOT_PARENT,
            lineage_prev_hash=GENESIS_PREV_HASH,
            depth=0,
            inherited_goal_slice_digest=_GOAL,
            inherited_budget_slice_digest=_BUDGET,
        )
    with pytest.raises(GenesisLineageError):
        derive_entry_hash(
            cell_id="sha256:" + "1" * 64,
            parent_cell_id=GENESIS_ROOT_PARENT,
            lineage_prev_hash=GENESIS_PREV_HASH,
            depth=_LyingDepth(0),
            inherited_goal_slice_digest=_GOAL,
            inherited_budget_slice_digest=_BUDGET,
        )


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
