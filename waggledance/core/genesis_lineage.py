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


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
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
    """Digest over the canonical entry content (entry_hash itself excluded)."""

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
    if parent_cell_id != GENESIS_ROOT_PARENT:
        _require_sha256(parent_cell_id, "parent_cell_id")
    _require_sha256(lineage_prev_hash, "lineage_prev_hash")
    _require_sha256(inherited_goal_slice_digest, "inherited_goal_slice_digest")
    _require_sha256(
        inherited_budget_slice_digest, "inherited_budget_slice_digest"
    )
    if isinstance(depth, bool) or not isinstance(depth, int):
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
    enforces the root/child rules, so a forged instance cannot exist."""

    cell_id: str
    parent_cell_id: str
    lineage_prev_hash: str
    depth: int
    inherited_goal_slice_digest: str
    inherited_budget_slice_digest: str
    entry_hash: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise GenesisLineageError("lineage schema_version refused")
        _validate_fields(
            cell_id=self.cell_id,
            parent_cell_id=self.parent_cell_id,
            lineage_prev_hash=self.lineage_prev_hash,
            depth=self.depth,
            inherited_goal_slice_digest=self.inherited_goal_slice_digest,
            inherited_budget_slice_digest=self.inherited_budget_slice_digest,
        )
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

    parent_ok, parent_reason = verify_lineage_entry(parent_entry)
    if not parent_ok:
        raise GenesisLineageError(f"parent entry rejected: {parent_reason}")
    return _build(
        cell_id=cell_id,
        parent_cell_id=str(parent_entry["cell_id"]),
        lineage_prev_hash=str(parent_entry["entry_hash"]),
        depth=int(parent_entry["depth"]) + 1,  # type: ignore[call-overload]
        inherited_goal_slice_digest=inherited_goal_slice_digest,
        inherited_budget_slice_digest=inherited_budget_slice_digest,
    )


def verify_lineage_entry(value: object) -> tuple[bool, Optional[str]]:
    """Fail-closed single-entry verifier: exact keyset, per-field shapes, the
    bidirectional root rules, no self-parent, and an internal ``entry_hash``
    recompute. Returns ``(ok, reason)``."""

    if not isinstance(value, Mapping):
        return False, "not_mapping"
    if set(value.keys()) != LINEAGE_KEYS:
        return False, "keyset"
    if value.get("schema_version") != SCHEMA_VERSION:
        return False, "schema_version"
    try:
        _validate_fields(
            cell_id=value.get("cell_id"),  # type: ignore[arg-type]
            parent_cell_id=value.get("parent_cell_id"),  # type: ignore[arg-type]
            lineage_prev_hash=value.get("lineage_prev_hash"),  # type: ignore[arg-type]
            depth=value.get("depth"),  # type: ignore[arg-type]
            inherited_goal_slice_digest=value.get(
                "inherited_goal_slice_digest"
            ),  # type: ignore[arg-type]
            inherited_budget_slice_digest=value.get(
                "inherited_budget_slice_digest"
            ),  # type: ignore[arg-type]
        )
    except GenesisLineageError as exc:
        return False, str(exc)
    entry_hash = value.get("entry_hash")
    if not isinstance(entry_hash, str) or not _SHA256.fullmatch(entry_hash):
        return False, "entry_hash"
    expected = derive_entry_hash(
        cell_id=value["cell_id"],  # type: ignore[arg-type]
        parent_cell_id=value["parent_cell_id"],  # type: ignore[arg-type]
        lineage_prev_hash=value["lineage_prev_hash"],  # type: ignore[arg-type]
        depth=value["depth"],  # type: ignore[arg-type]
        inherited_goal_slice_digest=value["inherited_goal_slice_digest"],  # type: ignore[arg-type]
        inherited_budget_slice_digest=value["inherited_budget_slice_digest"],  # type: ignore[arg-type]
    )
    if entry_hash != expected:
        return False, "entry_hash_mismatch"
    return True, None


def verify_lineage_link(
    child: Mapping[str, object], parent: Mapping[str, object]
) -> tuple[bool, Optional[str]]:
    """Both entries verify AND the child genuinely descends from the parent."""

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
    if child["depth"] != int(parent["depth"]) + 1:  # type: ignore[call-overload]
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

    # Require a re-iterable, bounded Sequence (list/tuple) as declared -- NOT
    # an arbitrary iterator. This makes two passes (index parents, then verify
    # links); a one-shot generator would be exhausted after pass one and every
    # non-root link check would silently pass. Rejecting (rather than
    # materializing an unknown iterable) also refuses to hang/OOM on a hostile
    # or infinite generator. str/bytes are Sequences but never a registry.
    if not isinstance(entries, _SequenceABC) or isinstance(entries, (str, bytes)):
        return False, "not_sequence"
    if not entries:
        return False, "empty_registry"
    by_cell: dict[str, Mapping[str, object]] = {}
    root_count = 0
    for index, entry in enumerate(entries):
        ok, reason = verify_lineage_entry(entry)
        if not ok:
            return False, f"entry_{index}:{reason}"
        cell_id = str(entry["cell_id"])
        if cell_id in by_cell:
            return False, f"entry_{index}:duplicate_cell_id"
        by_cell[cell_id] = entry
        if entry["parent_cell_id"] == GENESIS_ROOT_PARENT:
            root_count += 1
    if root_count == 0:
        return False, "no_root"
    if root_count > 1:
        return False, "multiple_roots"
    for index, entry in enumerate(entries):
        if entry["parent_cell_id"] == GENESIS_ROOT_PARENT:
            continue
        parent = by_cell.get(str(entry["parent_cell_id"]))
        if parent is None:
            return False, f"entry_{index}:orphan_parent_not_in_registry"
        ok, reason = verify_lineage_link(entry, parent)
        if not ok:
            return False, f"entry_{index}:link:{reason}"
    return True, None
