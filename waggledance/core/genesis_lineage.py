# SPDX-License-Identifier: BUSL-1.1
"""W2A GenesisLineageV1 -- tamper-evident cell lineage hash-chain (pure contract).

Records WHERE a cell came from: the root cell descends from the GENESIS
sentinel; every subdivided child binds to its parent's entry hash. The chain
is evidence, never a grant:

* **Records, never grants (Law 7).** Inherited goal/budget slices appear only
  as digests. A lineage entry cannot confer authority, and the exact-keyset
  check rejects any smuggled grant field. Slice ceilings (child <= parent) are
  enforced by the signed charter layer, which is where the raw values live.
* **Tamper-evident.** ``entry_hash`` is a domain-separated digest over the
  canonical entry bytes (excluding itself); any field edit breaks it, and a
  child binds ``lineage_prev_hash`` to the parent's ``entry_hash``, so history
  cannot be rewritten under a live descendant.
* **Root rules are bidirectional.** ``parent_cell_id == GENESIS_ROOT_PARENT``,
  ``depth == 0`` and ``lineage_prev_hash == GENESIS_PREV_HASH`` must agree in
  ALL directions -- a non-root wearing any single root marker is rejected.
* **One lineage entry per cell (injective), one rooted tree (closure).**
  A cell has exactly one origin; a registry with two entries for one
  ``cell_id`` is forged/ambiguous and fails closed. A valid REGISTRY is
  additionally a single rooted tree: exactly one root, no orphans, and every
  child link proven with ``verify_lineage_link`` against its in-registry
  parent -- per-entry self-consistency alone is NOT ancestry. Parent fan-out
  (many children, one parent) stays legal -- subdivision is the point.
* **No self-parenting.** ``cell_id == parent_cell_id`` is rejected, closing
  the replay shape where an entry is resubmitted as its own child.

Verifiers recompute everything from primitive fields; no stored flag or hash
is trusted. No clock, no randomness -- derivation is replayable, which is what
lets a rebuilt cell (same genesis facts) prove the SAME lineage position.
"""

from __future__ import annotations

import re
from collections.abc import Sequence as _SequenceABC
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from waggledance.core.magma.canonical import sha256_digest

SCHEMA_VERSION = "wd.genesis_lineage.v1"
DIGEST_DOMAIN = "wd.genesis_lineage.digest.v1"

# Root sentinel parent + the well-known genesis predecessor hash (the
# chat_served_ledger convention: self-describing all-zero digest).
GENESIS_ROOT_PARENT = "genesis:root"
GENESIS_PREV_HASH = "sha256:" + "0" * 64

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_DEPTH = 1_000_000  # generous bound; a depth beyond this is malformed, not deep

LINEAGE_KEYS = frozenset(
    {
        "schema_version",
        "cell_id",
        "parent_cell_id",
        "lineage_prev_hash",
        "depth",
        "inherited_goal_slice_digest",
        "inherited_budget_slice_digest",
        "entry_hash",
    }
)


class GenesisLineageError(ValueError):
    """The value is outside the GenesisLineageV1 contract."""


def _freeze_mapping(value: object) -> Optional[dict]:
    """Snapshot an untrusted Mapping into a plain dict, reading each key once,
    so a flapping/live Mapping cannot present different values across the shape
    check and the digest recompute. Fail-closed (returns None) on: non-Mapping,
    any keys()/getitem/hash protocol failure, a non-exact-str key (an EqAnyStr/
    alias key that hashes/compares to impersonate a canonical key), or a
    duplicate key (which a dict comprehension would silently collapse)."""
    if not isinstance(value, Mapping):
        return None
    try:
        raw_keys = list(value.keys())
    except Exception:
        return None
    seen: set = set()
    snapshot: dict = {}
    for key in raw_keys:
        if type(key) is not str:
            return None
        try:
            if key in seen:
                return None
        except Exception:
            return None
        seen.add(key)
        try:
            snapshot[key] = value[key]
        except Exception:
            return None
    return snapshot


def _require_sha256(value: object, label: str) -> str:
    # EXACT str only: a str subclass overriding __eq__ (compare-equal-to-
    # anything) would defeat the later == recompute, so reject subclasses here.
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise GenesisLineageError(f"{label} must be a sha256:<64 hex> digest")
    return value


def derive_entry_hash(
    *,
    cell_id: str,
    parent_cell_id: str,
    lineage_prev_hash: str,
    depth: int,
    inherited_goal_slice_digest: str,
    inherited_budget_slice_digest: str,
) -> str:
    """Digest over the canonical entry content (entry_hash itself excluded).

    Validates its inputs under the same exact-primitive contract as the rest
    of the module (via ``_validate_fields``) so a public caller cannot fold a
    permissive-__eq__ subclass or non-canonical value into a digest; internal
    callers pass already-validated fields, so the recheck is cheap."""

    _validate_fields(
        cell_id=cell_id,
        parent_cell_id=parent_cell_id,
        lineage_prev_hash=lineage_prev_hash,
        depth=depth,
        inherited_goal_slice_digest=inherited_goal_slice_digest,
        inherited_budget_slice_digest=inherited_budget_slice_digest,
    )
    return sha256_digest(
        {
            "domain": DIGEST_DOMAIN,
            "schema_version": SCHEMA_VERSION,
            "cell_id": cell_id,
            "parent_cell_id": parent_cell_id,
            "lineage_prev_hash": lineage_prev_hash,
            "depth": depth,
            "inherited_goal_slice_digest": inherited_goal_slice_digest,
            "inherited_budget_slice_digest": inherited_budget_slice_digest,
        }
    )


def _validate_fields(
    *,
    cell_id: str,
    parent_cell_id: str,
    lineage_prev_hash: str,
    depth: int,
    inherited_goal_slice_digest: str,
    inherited_budget_slice_digest: str,
) -> None:
    _require_sha256(cell_id, "cell_id")
    # Exact str before ANY == against the sentinel, so a permissive-__eq__
    # subclass cannot masquerade as the root parent and skip sha256 validation.
    if type(parent_cell_id) is not str:
        raise GenesisLineageError("parent_cell_id must be a str")
    if parent_cell_id != GENESIS_ROOT_PARENT:
        _require_sha256(parent_cell_id, "parent_cell_id")
    _require_sha256(lineage_prev_hash, "lineage_prev_hash")
    _require_sha256(inherited_goal_slice_digest, "inherited_goal_slice_digest")
    _require_sha256(
        inherited_budget_slice_digest, "inherited_budget_slice_digest"
    )
    # EXACT int: bool is an int subclass, and an int subclass could override
    # comparisons; type() is int rejects both.
    if type(depth) is not int:
        raise GenesisLineageError("depth must be an integer")
    if not 0 <= depth <= MAX_DEPTH:
        raise GenesisLineageError(f"depth must be within 0..{MAX_DEPTH}")
    if cell_id == parent_cell_id:
        raise GenesisLineageError("a cell cannot be its own parent")
    is_root = parent_cell_id == GENESIS_ROOT_PARENT
    if is_root != (depth == 0) or is_root != (
        lineage_prev_hash == GENESIS_PREV_HASH
    ):
        raise GenesisLineageError(
            "root markers (sentinel parent, depth 0, genesis prev hash) "
            "must agree in all directions"
        )


@dataclass(frozen=True)
class GenesisLineageV1:
    """Immutable lineage entry. Construction re-derives ``entry_hash`` and
    enforces this entry's OWN root/child field rules -- i.e. entry-level
    SELF-CONSISTENCY. It does NOT prove ancestry: that this entry's parent
    actually exists or is itself valid. Ancestry is proven pairwise with
    ``verify_lineage_link`` and registry-wide with ``verify_lineage_registry``
    closure."""

    cell_id: str
    parent_cell_id: str
    lineage_prev_hash: str
    depth: int
    inherited_goal_slice_digest: str
    inherited_budget_slice_digest: str
    entry_hash: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or (
            self.schema_version != SCHEMA_VERSION
        ):
            raise GenesisLineageError("lineage schema_version refused")
        _validate_fields(
            cell_id=self.cell_id,
            parent_cell_id=self.parent_cell_id,
            lineage_prev_hash=self.lineage_prev_hash,
            depth=self.depth,
            inherited_goal_slice_digest=self.inherited_goal_slice_digest,
            inherited_budget_slice_digest=self.inherited_budget_slice_digest,
        )
        # entry_hash exact str before the == recompute (permissive-__eq__ guard).
        _require_sha256(self.entry_hash, "entry_hash")
        expected = derive_entry_hash(
            cell_id=self.cell_id,
            parent_cell_id=self.parent_cell_id,
            lineage_prev_hash=self.lineage_prev_hash,
            depth=self.depth,
            inherited_goal_slice_digest=self.inherited_goal_slice_digest,
            inherited_budget_slice_digest=self.inherited_budget_slice_digest,
        )
        if self.entry_hash != expected:
            raise GenesisLineageError(
                "entry_hash does not match the derived entry digest"
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "cell_id": self.cell_id,
            "parent_cell_id": self.parent_cell_id,
            "lineage_prev_hash": self.lineage_prev_hash,
            "depth": self.depth,
            "inherited_goal_slice_digest": self.inherited_goal_slice_digest,
            "inherited_budget_slice_digest": self.inherited_budget_slice_digest,
            "entry_hash": self.entry_hash,
        }


def _build(
    *,
    cell_id: str,
    parent_cell_id: str,
    lineage_prev_hash: str,
    depth: int,
    inherited_goal_slice_digest: str,
    inherited_budget_slice_digest: str,
) -> GenesisLineageV1:
    return GenesisLineageV1(
        cell_id=cell_id,
        parent_cell_id=parent_cell_id,
        lineage_prev_hash=lineage_prev_hash,
        depth=depth,
        inherited_goal_slice_digest=inherited_goal_slice_digest,
        inherited_budget_slice_digest=inherited_budget_slice_digest,
        entry_hash=derive_entry_hash(
            cell_id=cell_id,
            parent_cell_id=parent_cell_id,
            lineage_prev_hash=lineage_prev_hash,
            depth=depth,
            inherited_goal_slice_digest=inherited_goal_slice_digest,
            inherited_budget_slice_digest=inherited_budget_slice_digest,
        ),
    )


def build_root_entry(
    *,
    cell_id: str,
    inherited_goal_slice_digest: str,
    inherited_budget_slice_digest: str,
) -> GenesisLineageV1:
    return _build(
        cell_id=cell_id,
        parent_cell_id=GENESIS_ROOT_PARENT,
        lineage_prev_hash=GENESIS_PREV_HASH,
        depth=0,
        inherited_goal_slice_digest=inherited_goal_slice_digest,
        inherited_budget_slice_digest=inherited_budget_slice_digest,
    )


def build_child_entry(
    *,
    cell_id: str,
    parent_entry: Mapping[str, object],
    inherited_goal_slice_digest: str,
    inherited_budget_slice_digest: str,
) -> GenesisLineageV1:
    """A child binds to a SELF-CONSISTENT parent entry (``verify_lineage_entry``
    passes): a malformed parent cannot mint descendants. This is entry-level
    validation only -- it does NOT prove the parent's own ancestry. Full
    ancestry is proven pairwise with ``verify_lineage_link`` and
    registry-wide with ``verify_lineage_registry`` closure."""

    parent = _freeze_mapping(parent_entry)
    if parent is None:
        raise GenesisLineageError("parent entry rejected: not_mapping")
    parent_ok, parent_reason = verify_lineage_entry(parent)
    if not parent_ok:
        raise GenesisLineageError(f"parent entry rejected: {parent_reason}")
    return _build(
        cell_id=cell_id,
        parent_cell_id=parent["cell_id"],
        lineage_prev_hash=parent["entry_hash"],
        depth=parent["depth"] + 1,
        inherited_goal_slice_digest=inherited_goal_slice_digest,
        inherited_budget_slice_digest=inherited_budget_slice_digest,
    )


def verify_lineage_entry(value: object) -> tuple[bool, Optional[str]]:
    """Fail-closed single-entry verifier: exact keyset, per-field shapes, the
    bidirectional root rules, no self-parent, and an internal ``entry_hash``
    recompute. Returns ``(ok, reason)``."""

    snapshot = _freeze_mapping(value)
    if snapshot is None:
        return False, "not_mapping"
    if set(snapshot.keys()) != LINEAGE_KEYS:
        return False, "keyset"
    if type(snapshot.get("schema_version")) is not str or (
        snapshot["schema_version"] != SCHEMA_VERSION
    ):
        return False, "schema_version"
    try:
        _validate_fields(
            cell_id=snapshot.get("cell_id"),  # type: ignore[arg-type]
            parent_cell_id=snapshot.get("parent_cell_id"),  # type: ignore[arg-type]
            lineage_prev_hash=snapshot.get("lineage_prev_hash"),  # type: ignore[arg-type]
            depth=snapshot.get("depth"),  # type: ignore[arg-type]
            inherited_goal_slice_digest=snapshot.get(
                "inherited_goal_slice_digest"
            ),  # type: ignore[arg-type]
            inherited_budget_slice_digest=snapshot.get(
                "inherited_budget_slice_digest"
            ),  # type: ignore[arg-type]
        )
    except GenesisLineageError as exc:
        return False, str(exc)
    entry_hash = snapshot.get("entry_hash")
    if type(entry_hash) is not str or not _SHA256.fullmatch(entry_hash):
        return False, "entry_hash"
    expected = derive_entry_hash(
        cell_id=snapshot["cell_id"],
        parent_cell_id=snapshot["parent_cell_id"],
        lineage_prev_hash=snapshot["lineage_prev_hash"],
        depth=snapshot["depth"],
        inherited_goal_slice_digest=snapshot["inherited_goal_slice_digest"],
        inherited_budget_slice_digest=snapshot["inherited_budget_slice_digest"],
    )
    if entry_hash != expected:
        return False, "entry_hash_mismatch"
    return True, None


def verify_lineage_link(
    child: Mapping[str, object], parent: Mapping[str, object]
) -> tuple[bool, Optional[str]]:
    """Both entries verify AND the child genuinely descends from the parent."""

    # Freeze both once, then verify AND compare against the SAME frozen values,
    # so a live Mapping cannot flip parent_cell_id (etc.) after it verifies.
    child = _freeze_mapping(child)
    parent = _freeze_mapping(parent)
    if child is None:
        return False, "child:not_mapping"
    if parent is None:
        return False, "parent:not_mapping"
    child_ok, child_reason = verify_lineage_entry(child)
    if not child_ok:
        return False, f"child:{child_reason}"
    parent_ok, parent_reason = verify_lineage_entry(parent)
    if not parent_ok:
        return False, f"parent:{parent_reason}"
    if child["parent_cell_id"] != parent["cell_id"]:
        return False, "parent_cell_id_mismatch"
    if child["lineage_prev_hash"] != parent["entry_hash"]:
        return False, "prev_hash_mismatch"
    if child["depth"] != parent["depth"] + 1:
        return False, "depth_mismatch"
    return True, None


def verify_lineage_registry(
    entries: Sequence[Mapping[str, object]],
) -> tuple[bool, Optional[str]]:
    """Registry CLOSURE check: a valid registry is a single rooted lineage
    tree, not merely a set of self-consistent entries.

    Enforced, order-independently: every entry verifies; each ``cell_id``
    appears EXACTLY once (injective); EXACTLY ONE root exists (empty and
    rootless/orphan-only registries fail closed, as do two roots); and every
    non-root's parent must BE IN the registry with the full
    ``verify_lineage_link`` proof passing -- a rehashed child whose link to
    the real parent fails, or a child whose parent is absent, is rejected.
    Parent fan-out stays legal (many children may link to one parent).
    Cycles are impossible once links verify: depth strictly increases."""

    # Require a declared Sequence (bounded, indexable) -- NOT an arbitrary
    # iterator (a generator would be consumed by pass one and hang/OOM if
    # materialized). str/bytes are Sequences but never a registry.
    if not isinstance(entries, _SequenceABC) or isinstance(entries, (str, bytes)):
        return False, "not_sequence"
    # SNAPSHOT via the Sequence protocol (len + getitem), and FREEZE EACH ENTRY
    # to a plain dict, reading each index/key exactly once. Both passes below
    # operate only on these frozen dicts -- never the live Sequence or live
    # Mappings -- so neither a flapping Sequence nor a Mapping that flips a
    # field after it verifies can present different data across passes. Bounded
    # by the declared length; any protocol error fails closed.
    try:
        frozen = tuple(_freeze_mapping(entries[index]) for index in range(len(entries)))
    except Exception:
        return False, "unstable_sequence"
    if not frozen:
        return False, "empty_registry"
    by_cell: dict[str, dict] = {}
    root_count = 0
    for index, entry in enumerate(frozen):
        ok, reason = verify_lineage_entry(entry)
        if not ok:
            return False, f"entry_{index}:{reason}"
        cell_id = entry["cell_id"]  # exact str, verified
        if cell_id in by_cell:
            return False, f"entry_{index}:duplicate_cell_id"
        by_cell[cell_id] = entry
        if entry["parent_cell_id"] == GENESIS_ROOT_PARENT:
            root_count += 1
    if root_count == 0:
        return False, "no_root"
    if root_count > 1:
        return False, "multiple_roots"
    for index, entry in enumerate(frozen):
        if entry["parent_cell_id"] == GENESIS_ROOT_PARENT:
            continue
        parent = by_cell.get(entry["parent_cell_id"])
        if parent is None:
            return False, f"entry_{index}:orphan_parent_not_in_registry"
        ok, reason = verify_lineage_link(entry, parent)
        if not ok:
            return False, f"entry_{index}:link:{reason}"
    return True, None
