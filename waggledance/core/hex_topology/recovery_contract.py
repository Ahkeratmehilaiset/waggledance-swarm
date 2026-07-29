# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed HEX cell and aggregate recovery contracts.

The contracts in this module are pure reconstruction evidence.  They do not
start a runtime, move traffic, mutate a live topology, transport data, or grant
activation/claim-safety authority.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from waggledance.core.magma.canonical import sha256_digest


CELL_CONTRACT_VERSION = "hex_cell_genome.v1"
MANIFEST_CONTRACT_VERSION = "hive_recovery_manifest.v1"
CANONICALIZATION_VERSION = "magma-jcs-subset-v1"

MESH_KINDS = frozenset(
    {
        "axial_agent_mesh",
        "logical_solver_overlay",
        "hierarchical_runtime_shadow",
    }
)
CAPABILITY_KINDS = frozenset({"solver", "agent", "router"})
INPUT_KINDS = frozenset(
    {
        "repo_artifact",
        "magma_ledger",
        "snapshot_manifest",
        "model_manifest",
        "external_export",
    }
)
REBUILD_STRATEGIES = frozenset(
    {"git_checkout", "replay", "verified_copy", "external_reprovision"}
)
ARTIFACT_CLASSIFICATIONS = frozenset(
    {"genome", "mutable_state", "rebuildable_cache", "external_dependency"}
)
ARTIFACT_RESTORE_STRATEGIES = frozenset(
    {"git_checkout", "verified_copy", "rebuild", "external_reprovision"}
)

_CELL_KEYS = frozenset(
    {
        "contract_version",
        "canonicalization",
        "cell_id",
        "mesh_id",
        "mesh_kind",
        "topology_epoch",
        "axial_coord",
        "parent_cell_id",
        "child_cell_ids",
        "neighbor_cell_ids",
        "repair_peer_cell_ids",
        "capabilities",
        "durable_inputs",
        "expected_cell_state_root",
        "restore_state",
        "runtime_activation_authority_granted",
        "genome_digest",
    }
)
_CAPABILITY_KEYS = frozenset(
    {"kind", "capability_id", "source_ref", "source_digest", "required"}
)
_INPUT_KEYS = frozenset(
    {
        "input_id",
        "kind",
        "source_ref",
        "source_digest",
        "rebuild_strategy",
        "replay_checkpoint",
        "required",
    }
)
_MANIFEST_KEYS = frozenset(
    {
        "contract_version",
        "canonicalization",
        "manifest_id",
        "created_at_utc",
        "source_repository",
        "topologies",
        "artifacts",
        "replicas",
        "recovery_policy",
        "genome_root",
        "memory_root",
        "hive_state_root",
        "external_replication_verified",
        "blank_disk_dry_run_verified",
        "production_ready_claim",
        "previous_manifest_digest",
        "manifest_digest",
    }
)
_SOURCE_REPOSITORY_KEYS = frozenset(
    {
        "repository_ref",
        "commit_sha",
        "source_of_truth",
        "require_clean_clone",
        "backup_as_primary",
    }
)
_TOPOLOGY_KEYS = frozenset(
    {
        "mesh_id",
        "mesh_kind",
        "topology_epoch",
        "cells",
        "routing_invariants",
        "topology_digest",
    }
)
_CELL_REF_KEYS = frozenset(
    {"cell_id", "genome_ref", "genome_digest", "expected_cell_state_root"}
)
_ROUTING_KEYS = frozenset(
    {
        "connected",
        "bidirectional_neighbors",
        "survive_single_cell_loss",
        "max_route_hops",
    }
)
_ARTIFACT_KEYS = frozenset(
    {
        "artifact_id",
        "relative_path",
        "content_digest",
        "byte_size",
        "classification",
        "required",
        "restore_strategy",
    }
)
_REPLICA_KEYS = frozenset(
    {
        "artifact_id",
        "replica_id",
        "failure_domain",
        "locator_ref",
        "verified_at_utc",
        "content_digest",
    }
)
_POLICY_KEYS = frozenset(
    {
        "restore_state",
        "require_exact_commit",
        "require_all_digests",
        "require_off_host_replica_for_non_git",
        "transport_enabled",
        "runtime_activation_authority_granted",
        "operator_gate_required_for_activation",
        "claim_safe_upgrade",
    }
)

_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_RELATIVE_PATH_RE = re.compile(
    r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"
)
_OPAQUE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_UTC_Z_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_CELLS_PER_TOPOLOGY = 256


class ContractValidationError(ValueError):
    """A redaction-safe validation failure.

    Messages contain only a stable error code and a schema-style field path;
    raw paths, locators, or other caller-controlled values are never echoed.
    """

    def __init__(self, code: str, field_path: str = "$") -> None:
        self.code = code
        self.field_path = field_path
        super().__init__(f"{code}:{field_path}")


def _fail(code: str, path: str = "$") -> None:
    raise ContractValidationError(code, path)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("expected_object", path)
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], path: str) -> None:
    if set(value) != set(expected):
        _fail("key_set_mismatch", path)


def _token(value: object, path: str) -> str:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        _fail("invalid_id_token", path)
    return value


def _digest(value: object, path: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        _fail("invalid_sha256_digest", path)
    return value


def _bounded_int(value: object, path: str, *, minimum: int, maximum: int) -> int:
    if not _is_int(value) or value < minimum or value > maximum:
        _fail("invalid_integer", path)
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        _fail("invalid_boolean", path)
    return value


def _utc_z(value: object, path: str) -> str:
    if not isinstance(value, str) or _UTC_Z_RE.fullmatch(value) is None:
        _fail("invalid_utc_timestamp", path)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail("invalid_utc_timestamp", path)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _fail("invalid_utc_timestamp", path)
    return value


def _opaque_ref(value: object, path: str) -> str:
    if not isinstance(value, str) or _OPAQUE_REF_RE.fullmatch(value) is None:
        _fail("invalid_opaque_ref", path)
    if value.startswith(("/", "\\")) or "\\" in value or "\x00" in value:
        _fail("invalid_opaque_ref", path)
    if re.match(r"^[A-Za-z]:/", value):
        _fail("invalid_opaque_ref", path)
    return value


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key", "$")
        result[key] = value
    return result


def _reject_nonfinite_json(token: str) -> None:
    del token
    _fail("nonfinite_json_number", "$")


def _strict_json_bytes(raw: bytes) -> dict[str, Any]:
    if len(raw) > _MAX_JSON_BYTES:
        _fail("json_file_too_large", "$")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_rejecting_object,
            parse_constant=_reject_nonfinite_json,
        )
    except ContractValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        _fail("invalid_json", "$")
    if not isinstance(value, dict):
        _fail("json_root_not_object", "$")
    return value


def strict_json_load_with_digest(
    path: str | os.PathLike[str],
) -> tuple[dict[str, Any], str]:
    """Parse and hash the same stable file snapshot.

    This closes the trust-anchor gap that would exist if a manifest were
    hashed and then reopened separately for parsing.
    """
    source = Path(path)
    try:
        before = os.lstat(source)
    except OSError:
        _fail("json_file_unreadable", "$")
    attributes = int(getattr(before, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if (
        stat.S_ISLNK(before.st_mode)
        or bool(attributes & reparse)
        or not stat.S_ISREG(before.st_mode)
    ):
        _fail("json_file_not_regular", "$")
    if before.st_size > _MAX_JSON_BYTES:
        _fail("json_file_too_large", "$")
    hasher = hashlib.sha256()
    chunks: list[bytes] = []
    total_bytes = 0
    try:
        with open(source, "rb") as handle:
            opened = os.fstat(handle.fileno())
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                hasher.update(chunk)
                total_bytes += len(chunk)
                if total_bytes > _MAX_JSON_BYTES:
                    _fail("json_file_too_large", "$")
            after_open = os.fstat(handle.fileno())
        after_path = os.lstat(source)
    except ContractValidationError:
        raise
    except OSError:
        _fail("json_file_unreadable", "$")
    after_attributes = int(getattr(after_path, "st_file_attributes", 0))
    if (
        not stat.S_ISREG(opened.st_mode)
        or stat.S_ISLNK(after_path.st_mode)
        or bool(after_attributes & reparse)
        or not stat.S_ISREG(after_path.st_mode)
    ):
        _fail("json_file_changed_during_read", "$")
    signatures = {
        (
            int(item.st_dev),
            int(item.st_ino),
            int(item.st_size),
            int(item.st_mtime_ns),
        )
        for item in (before, opened, after_open, after_path)
    }
    if len(signatures) != 1:
        _fail("json_file_changed_during_read", "$")
    raw = b"".join(chunks)
    return _strict_json_bytes(raw), f"sha256:{hasher.hexdigest()}"


def strict_json_load(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load a bounded stable UTF-8 JSON object with strict JSON semantics."""
    value, _ = strict_json_load_with_digest(path)
    return value


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Return a full raw-byte SHA-256 digest for ``path``."""
    hasher = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        _fail("file_hash_failed", "$")
    return f"sha256:{hasher.hexdigest()}"


def validate_repo_relative_path(
    value: object,
    field_path: str = "path",
) -> PurePosixPath:
    """Return a safe, portable POSIX-relative path or fail closed."""
    if (
        not isinstance(value, str)
        or len(value) > 240
        or _RELATIVE_PATH_RE.fullmatch(value) is None
        or "\\" in value
        or ":" in value
        or "\x00" in value
    ):
        _fail("unsafe_relative_path", field_path)
    parts = value.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        _fail("unsafe_relative_path", field_path)
    for part in parts:
        if part.endswith((".", " ")):
            _fail("unsafe_relative_path", field_path)
        stem = part.split(".", 1)[0].casefold()
        if stem in _WINDOWS_RESERVED:
            _fail("unsafe_relative_path", field_path)
    return PurePosixPath(*parts)


def _id_list(
    value: object,
    path: str,
    *,
    minimum: int = 0,
    maximum: int = 64,
    forbidden: str | None = None,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _fail("invalid_id_list", path)
    result = [_token(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if len(set(result)) != len(result):
        _fail("duplicate_id", path)
    if forbidden is not None and forbidden in result:
        _fail("self_reference", path)
    return result


def _validate_capabilities(value: object, path: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 1024:
        _fail("invalid_capability_list", path)
    result: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        item_path = f"{path}[{index}]"
        item = _mapping(raw, item_path)
        _exact_keys(item, _CAPABILITY_KEYS, item_path)
        kind = item.get("kind")
        if kind not in CAPABILITY_KINDS:
            _fail("invalid_capability_kind", f"{item_path}.kind")
        capability_id = _token(item.get("capability_id"), f"{item_path}.capability_id")
        identity = (kind, capability_id)
        if identity in identities:
            _fail("duplicate_capability", item_path)
        identities.add(identity)
        validate_repo_relative_path(item.get("source_ref"), f"{item_path}.source_ref")
        _digest(item.get("source_digest"), f"{item_path}.source_digest")
        _boolean(item.get("required"), f"{item_path}.required")
        result.append(dict(item))
    return result


def _validate_inputs(value: object, path: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 1024:
        _fail("invalid_durable_input_list", path)
    result: list[dict[str, Any]] = []
    identities: set[str] = set()
    for index, raw in enumerate(value):
        item_path = f"{path}[{index}]"
        item = _mapping(raw, item_path)
        _exact_keys(item, _INPUT_KEYS, item_path)
        input_id = _token(item.get("input_id"), f"{item_path}.input_id")
        if input_id in identities:
            _fail("duplicate_durable_input", item_path)
        identities.add(input_id)
        kind = item.get("kind")
        if kind not in INPUT_KINDS:
            _fail("invalid_input_kind", f"{item_path}.kind")
        validate_repo_relative_path(item.get("source_ref"), f"{item_path}.source_ref")
        _digest(item.get("source_digest"), f"{item_path}.source_digest")
        strategy = item.get("rebuild_strategy")
        if strategy not in REBUILD_STRATEGIES:
            _fail("invalid_rebuild_strategy", f"{item_path}.rebuild_strategy")
        checkpoint = item.get("replay_checkpoint")
        if checkpoint is not None:
            _bounded_int(
                checkpoint,
                f"{item_path}.replay_checkpoint",
                minimum=0,
                maximum=2**63 - 1,
            )
        if strategy == "replay" and checkpoint is None:
            _fail("replay_checkpoint_required", f"{item_path}.replay_checkpoint")
        if strategy != "replay" and checkpoint is not None:
            _fail("replay_checkpoint_not_allowed", f"{item_path}.replay_checkpoint")
        _boolean(item.get("required"), f"{item_path}.required")
        result.append(dict(item))
    if not any(item["required"] is True for item in result):
        _fail("required_durable_input_missing", path)
    return result


def _cell_state_projection(genome: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "contract": "hex.cell_state_root.v1",
        "cell": {
            key: deepcopy(value)
            for key, value in genome.items()
            if key not in {"expected_cell_state_root", "genome_digest"}
        },
    }


def compute_cell_state_root(genome_without_self_digests: Mapping[str, Any]) -> str:
    """Compute the machine-independent semantic root for one cell."""
    if not isinstance(genome_without_self_digests, Mapping):
        _fail("expected_object", "$")
    return sha256_digest(_cell_state_projection(genome_without_self_digests))


def _validate_cell_shape(
    doc: Mapping[str, Any],
    *,
    verify_digests: bool,
) -> dict[str, Any]:
    _exact_keys(doc, _CELL_KEYS, "$")
    if doc.get("contract_version") != CELL_CONTRACT_VERSION:
        _fail("contract_version", "$.contract_version")
    if doc.get("canonicalization") != CANONICALIZATION_VERSION:
        _fail("canonicalization", "$.canonicalization")
    cell_id = _token(doc.get("cell_id"), "$.cell_id")
    _token(doc.get("mesh_id"), "$.mesh_id")
    mesh_kind = doc.get("mesh_kind")
    if mesh_kind not in MESH_KINDS:
        _fail("mesh_kind", "$.mesh_kind")
    _bounded_int(
        doc.get("topology_epoch"),
        "$.topology_epoch",
        minimum=0,
        maximum=2**63 - 1,
    )

    coord = doc.get("axial_coord")
    if mesh_kind == "axial_agent_mesh":
        coord_map = _mapping(coord, "$.axial_coord")
        _exact_keys(coord_map, frozenset({"q", "r"}), "$.axial_coord")
        _bounded_int(
            coord_map.get("q"), "$.axial_coord.q", minimum=-1_000_000, maximum=1_000_000
        )
        _bounded_int(
            coord_map.get("r"), "$.axial_coord.r", minimum=-1_000_000, maximum=1_000_000
        )
    elif coord is not None:
        _fail("axial_coord_not_allowed", "$.axial_coord")

    parent = doc.get("parent_cell_id")
    if parent is not None:
        _token(parent, "$.parent_cell_id")
        if parent == cell_id:
            _fail("self_reference", "$.parent_cell_id")
    children = _id_list(
        doc.get("child_cell_ids"), "$.child_cell_ids", forbidden=cell_id
    )
    neighbors = _id_list(
        doc.get("neighbor_cell_ids"), "$.neighbor_cell_ids", forbidden=cell_id
    )
    repair_peers = _id_list(
        doc.get("repair_peer_cell_ids"),
        "$.repair_peer_cell_ids",
        minimum=2,
        forbidden=cell_id,
    )
    if not set(repair_peers).issubset(neighbors):
        _fail("repair_peer_not_neighbor", "$.repair_peer_cell_ids")
    if mesh_kind != "hierarchical_runtime_shadow" and (parent is not None or children):
        _fail("hierarchy_not_allowed_for_mesh_kind", "$.parent_cell_id")

    capabilities = _validate_capabilities(doc.get("capabilities"), "$.capabilities")
    required_capability_kinds = {
        "logical_solver_overlay": {"solver"},
        "axial_agent_mesh": {"agent", "router"},
        "hierarchical_runtime_shadow": {"agent", "router"},
    }[mesh_kind]
    if not any(
        item["kind"] in required_capability_kinds and item["required"] is True
        for item in capabilities
    ):
        _fail("required_mesh_capability_missing", "$.capabilities")
    durable_inputs = _validate_inputs(doc.get("durable_inputs"), "$.durable_inputs")
    durable_source_bindings = {
        (item["source_ref"], item["source_digest"])
        for item in durable_inputs
        if item["required"] is True
    }
    for index, capability in enumerate(capabilities):
        if (
            capability["required"] is True
            and (capability["source_ref"], capability["source_digest"])
            not in durable_source_bindings
        ):
            _fail(
                "required_capability_source_unbound",
                f"$.capabilities[{index}].source_ref",
            )

    _digest(doc.get("expected_cell_state_root"), "$.expected_cell_state_root")
    if doc.get("restore_state") != "shadow_only":
        _fail("restore_state", "$.restore_state")
    if doc.get("runtime_activation_authority_granted") is not False:
        _fail(
            "runtime_authority_forbidden",
            "$.runtime_activation_authority_granted",
        )
    _digest(doc.get("genome_digest"), "$.genome_digest")

    if verify_digests:
        expected_state = compute_cell_state_root(doc)
        if doc.get("expected_cell_state_root") != expected_state:
            _fail("cell_state_root_mismatch", "$.expected_cell_state_root")
        expected_genome = sha256_digest(
            {key: deepcopy(value) for key, value in doc.items() if key != "genome_digest"}
        )
        if doc.get("genome_digest") != expected_genome:
            _fail("genome_digest_mismatch", "$.genome_digest")
    return deepcopy(dict(doc))


def validate_hex_cell_genome(doc: object) -> dict[str, Any]:
    """Validate a complete cell genome and return a detached copy."""
    return _validate_cell_shape(_mapping(doc, "$"), verify_digests=True)


def build_hex_cell_genome(
    *,
    cell_id: str,
    mesh_id: str,
    mesh_kind: str,
    topology_epoch: int,
    axial_coord: Mapping[str, int] | None,
    parent_cell_id: str | None = None,
    child_cell_ids: Sequence[str] = (),
    neighbor_cell_ids: Sequence[str],
    repair_peer_cell_ids: Sequence[str],
    capabilities: Sequence[Mapping[str, Any]],
    durable_inputs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build and self-validate a deterministic, shadow-only cell genome."""
    doc: dict[str, Any] = {
        "contract_version": CELL_CONTRACT_VERSION,
        "canonicalization": CANONICALIZATION_VERSION,
        "cell_id": cell_id,
        "mesh_id": mesh_id,
        "mesh_kind": mesh_kind,
        "topology_epoch": topology_epoch,
        "axial_coord": deepcopy(dict(axial_coord)) if axial_coord is not None else None,
        "parent_cell_id": parent_cell_id,
        "child_cell_ids": list(child_cell_ids),
        "neighbor_cell_ids": list(neighbor_cell_ids),
        "repair_peer_cell_ids": list(repair_peer_cell_ids),
        "capabilities": [deepcopy(dict(item)) for item in capabilities],
        "durable_inputs": [deepcopy(dict(item)) for item in durable_inputs],
        "expected_cell_state_root": "sha256:" + ("0" * 64),
        "restore_state": "shadow_only",
        "runtime_activation_authority_granted": False,
        "genome_digest": "sha256:" + ("0" * 64),
    }
    _validate_cell_shape(doc, verify_digests=False)
    doc["expected_cell_state_root"] = compute_cell_state_root(doc)
    doc["genome_digest"] = sha256_digest(
        {key: deepcopy(value) for key, value in doc.items() if key != "genome_digest"}
    )
    return validate_hex_cell_genome(doc)


def _validate_source_repository(value: object, path: str) -> dict[str, Any]:
    source = _mapping(value, path)
    _exact_keys(source, _SOURCE_REPOSITORY_KEYS, path)
    _opaque_ref(source.get("repository_ref"), f"{path}.repository_ref")
    commit = source.get("commit_sha")
    if not isinstance(commit, str) or _COMMIT_RE.fullmatch(commit) is None:
        _fail("invalid_commit_sha", f"{path}.commit_sha")
    if source.get("source_of_truth") != "git_primary":
        _fail("source_of_truth", f"{path}.source_of_truth")
    if source.get("require_clean_clone") is not True:
        _fail("clean_clone_required", f"{path}.require_clean_clone")
    if source.get("backup_as_primary") is not False:
        _fail("backup_cannot_be_primary", f"{path}.backup_as_primary")
    return deepcopy(dict(source))


def _validate_routing(value: object, path: str) -> dict[str, Any]:
    routing = _mapping(value, path)
    _exact_keys(routing, _ROUTING_KEYS, path)
    for key in ("connected", "bidirectional_neighbors", "survive_single_cell_loss"):
        if routing.get(key) is not True:
            _fail("routing_invariant_required", f"{path}.{key}")
    _bounded_int(
        routing.get("max_route_hops"),
        f"{path}.max_route_hops",
        minimum=1,
        maximum=65535,
    )
    return deepcopy(dict(routing))


def _validate_cell_ref(value: object, path: str) -> dict[str, Any]:
    cell = _mapping(value, path)
    _exact_keys(cell, _CELL_REF_KEYS, path)
    _token(cell.get("cell_id"), f"{path}.cell_id")
    validate_repo_relative_path(cell.get("genome_ref"), f"{path}.genome_ref")
    _digest(cell.get("genome_digest"), f"{path}.genome_digest")
    _digest(cell.get("expected_cell_state_root"), f"{path}.expected_cell_state_root")
    return deepcopy(dict(cell))


def compute_topology_digest(topology: Mapping[str, Any]) -> str:
    """Digest topology identity, invariants, and sorted cell-genome references."""
    value = _mapping(topology, "$")
    projection = {
        "contract": "hex.topology_root.v1",
        "mesh_id": value.get("mesh_id"),
        "mesh_kind": value.get("mesh_kind"),
        "topology_epoch": value.get("topology_epoch"),
        "cells": sorted(
            (
                {
                    "cell_id": item.get("cell_id"),
                    "genome_ref": item.get("genome_ref"),
                    "genome_digest": item.get("genome_digest"),
                    "expected_cell_state_root": item.get("expected_cell_state_root"),
                }
                for item in value.get("cells", [])
            ),
            key=lambda item: (str(item["cell_id"]), str(item["genome_ref"])),
        ),
        "routing_invariants": deepcopy(value.get("routing_invariants")),
    }
    return sha256_digest(projection)


def _validate_topology(value: object, path: str) -> dict[str, Any]:
    topology = _mapping(value, path)
    _exact_keys(topology, _TOPOLOGY_KEYS, path)
    _token(topology.get("mesh_id"), f"{path}.mesh_id")
    if topology.get("mesh_kind") not in MESH_KINDS:
        _fail("mesh_kind", f"{path}.mesh_kind")
    _bounded_int(
        topology.get("topology_epoch"),
        f"{path}.topology_epoch",
        minimum=0,
        maximum=2**63 - 1,
    )
    raw_cells = topology.get("cells")
    if (
        not isinstance(raw_cells, list)
        or not 3 <= len(raw_cells) <= _MAX_CELLS_PER_TOPOLOGY
    ):
        _fail("invalid_topology_cells", f"{path}.cells")
    cells = [
        _validate_cell_ref(item, f"{path}.cells[{index}]")
        for index, item in enumerate(raw_cells)
    ]
    if len({item["cell_id"] for item in cells}) != len(cells):
        _fail("duplicate_cell_id", f"{path}.cells")
    if len({item["genome_ref"] for item in cells}) != len(cells):
        _fail("duplicate_genome_ref", f"{path}.cells")
    _validate_routing(topology.get("routing_invariants"), f"{path}.routing_invariants")
    _digest(topology.get("topology_digest"), f"{path}.topology_digest")
    if topology.get("topology_digest") != compute_topology_digest(topology):
        _fail("topology_digest_mismatch", f"{path}.topology_digest")
    return deepcopy(dict(topology))


def _validate_artifact(value: object, path: str) -> dict[str, Any]:
    artifact = _mapping(value, path)
    _exact_keys(artifact, _ARTIFACT_KEYS, path)
    _token(artifact.get("artifact_id"), f"{path}.artifact_id")
    validate_repo_relative_path(artifact.get("relative_path"), f"{path}.relative_path")
    _digest(artifact.get("content_digest"), f"{path}.content_digest")
    _bounded_int(
        artifact.get("byte_size"),
        f"{path}.byte_size",
        minimum=0,
        maximum=2**63 - 1,
    )
    if artifact.get("classification") not in ARTIFACT_CLASSIFICATIONS:
        _fail("artifact_classification", f"{path}.classification")
    if artifact.get("restore_strategy") not in ARTIFACT_RESTORE_STRATEGIES:
        _fail("artifact_restore_strategy", f"{path}.restore_strategy")
    _boolean(artifact.get("required"), f"{path}.required")
    return deepcopy(dict(artifact))


def _validate_replica(value: object, path: str) -> dict[str, Any]:
    replica = _mapping(value, path)
    _exact_keys(replica, _REPLICA_KEYS, path)
    _token(replica.get("artifact_id"), f"{path}.artifact_id")
    _token(replica.get("replica_id"), f"{path}.replica_id")
    _token(replica.get("failure_domain"), f"{path}.failure_domain")
    _opaque_ref(replica.get("locator_ref"), f"{path}.locator_ref")
    _utc_z(replica.get("verified_at_utc"), f"{path}.verified_at_utc")
    _digest(replica.get("content_digest"), f"{path}.content_digest")
    return deepcopy(dict(replica))


def _validate_policy(value: object, path: str) -> dict[str, Any]:
    policy = _mapping(value, path)
    _exact_keys(policy, _POLICY_KEYS, path)
    expected = {
        "restore_state": "shadow_only",
        "require_exact_commit": True,
        "require_all_digests": True,
        "require_off_host_replica_for_non_git": True,
        "transport_enabled": False,
        "runtime_activation_authority_granted": False,
        "operator_gate_required_for_activation": True,
        "claim_safe_upgrade": False,
    }
    for key, required in expected.items():
        if policy.get(key) is not required and policy.get(key) != required:
            _fail("recovery_policy_invariant", f"{path}.{key}")
        if isinstance(required, bool) and not isinstance(policy.get(key), bool):
            _fail("recovery_policy_invariant", f"{path}.{key}")
    return deepcopy(dict(policy))


def compute_genome_root(topologies: Sequence[Mapping[str, Any]]) -> str:
    """Compute a machine-independent root over topology and cell genome roots."""
    projection = []
    for topology in topologies:
        projection.append(
            {
                "mesh_id": topology.get("mesh_id"),
                "mesh_kind": topology.get("mesh_kind"),
                "topology_epoch": topology.get("topology_epoch"),
                "topology_digest": topology.get("topology_digest"),
                "cells": sorted(
                    (
                        {
                            "cell_id": cell.get("cell_id"),
                            "genome_digest": cell.get("genome_digest"),
                            "expected_cell_state_root": cell.get(
                                "expected_cell_state_root"
                            ),
                        }
                        for cell in topology.get("cells", [])
                    ),
                    key=lambda item: str(item["cell_id"]),
                ),
            }
        )
    return sha256_digest(
        {
            "contract": "hex.genome_root.v1",
            "topologies": sorted(
                projection,
                key=lambda item: (
                    str(item["mesh_id"]),
                    str(item["mesh_kind"]),
                    int(item["topology_epoch"]),
                ),
            ),
        }
    )


def compute_memory_root(artifacts: Sequence[Mapping[str, Any]]) -> str:
    """Compute the durable non-genome state root from required artifacts."""
    projection = [
        {
            "artifact_id": item.get("artifact_id"),
            "relative_path": item.get("relative_path"),
            "content_digest": item.get("content_digest"),
            "byte_size": item.get("byte_size"),
            "classification": item.get("classification"),
            "required": True,
            "restore_strategy": item.get("restore_strategy"),
        }
        for item in artifacts
        if item.get("required") is True and item.get("classification") != "genome"
    ]
    return sha256_digest(
        {
            "contract": "hex.memory_root.v1",
            "artifacts": sorted(
                projection,
                key=lambda item: (
                    str(item["artifact_id"]),
                    str(item["content_digest"]),
                ),
            ),
        }
    )


def compute_hive_state_root(
    source_repository: Mapping[str, Any],
    genome_root: str,
    memory_root: str,
) -> str:
    """Bind exact Git genome and durable state roots without machine paths."""
    return sha256_digest(
        {
            "contract": "hex.hive_state_root.v1",
            "commit_sha": source_repository.get("commit_sha"),
            "source_of_truth": source_repository.get("source_of_truth"),
            "genome_root": genome_root,
            "memory_root": memory_root,
        }
    )


def _connected(adjacency: Mapping[str, set[str]], removed: str | None = None) -> bool:
    remaining = [node for node in adjacency if node != removed]
    if not remaining:
        return False
    seen = {remaining[0]}
    frontier = deque([remaining[0]])
    while frontier:
        node = frontier.popleft()
        for neighbor in adjacency[node]:
            if neighbor == removed or neighbor in seen:
                continue
            seen.add(neighbor)
            frontier.append(neighbor)
    return len(seen) == len(remaining)


def _diameter(adjacency: Mapping[str, set[str]]) -> int:
    maximum = 0
    for origin in adjacency:
        distances = {origin: 0}
        frontier = deque([origin])
        while frontier:
            node = frontier.popleft()
            for neighbor in adjacency[node]:
                if neighbor not in distances:
                    distances[neighbor] = distances[node] + 1
                    frontier.append(neighbor)
        if len(distances) != len(adjacency):
            return 2**31
        maximum = max(maximum, max(distances.values(), default=0))
    return maximum


def _axial_distance(left: Mapping[str, int], right: Mapping[str, int]) -> int:
    dq = left["q"] - right["q"]
    dr = left["r"] - right["r"]
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


def _verify_topology_genomes(
    topology: Mapping[str, Any],
    genomes_by_ref: Mapping[str, Mapping[str, Any]],
    path: str,
) -> None:
    cells = topology["cells"]
    cell_ids = {cell["cell_id"] for cell in cells}
    genomes: dict[str, dict[str, Any]] = {}
    for index, cell in enumerate(cells):
        ref = cell["genome_ref"]
        if ref not in genomes_by_ref:
            _fail("genome_document_missing", f"{path}.cells[{index}].genome_ref")
        genome = validate_hex_cell_genome(genomes_by_ref[ref])
        comparisons = {
            "cell_id": cell["cell_id"],
            "mesh_id": topology["mesh_id"],
            "mesh_kind": topology["mesh_kind"],
            "topology_epoch": topology["topology_epoch"],
            "genome_digest": cell["genome_digest"],
            "expected_cell_state_root": cell["expected_cell_state_root"],
        }
        for key, expected in comparisons.items():
            if genome.get(key) != expected:
                _fail("genome_reference_mismatch", f"{path}.cells[{index}].{key}")
        genomes[cell["cell_id"]] = genome

    adjacency: dict[str, set[str]] = {}
    coordinates: set[tuple[int, int]] = set()
    for cell_id, genome in genomes.items():
        neighbors = genome["neighbor_cell_ids"]
        unknown = set(neighbors) - cell_ids
        if unknown:
            _fail("unknown_neighbor", f"{path}.cells")
        repair_unknown = set(genome["repair_peer_cell_ids"]) - cell_ids
        if repair_unknown:
            _fail("unknown_repair_peer", f"{path}.cells")
        adjacency[cell_id] = set(neighbors)
        if topology["mesh_kind"] == "axial_agent_mesh":
            coord = genome["axial_coord"]
            coord_key = (coord["q"], coord["r"])
            if coord_key in coordinates:
                _fail("duplicate_axial_coord", f"{path}.cells")
            coordinates.add(coord_key)

    for cell_id, neighbors in adjacency.items():
        for neighbor in neighbors:
            if cell_id not in adjacency[neighbor]:
                _fail("asymmetric_neighbor", f"{path}.cells")
    if not _connected(adjacency):
        _fail("topology_disconnected", f"{path}.cells")
    if len(adjacency) < 3 or any(
        not _connected(adjacency, removed=cell_id) for cell_id in adjacency
    ):
        _fail("single_cell_loss_not_survivable", f"{path}.cells")
    if _diameter(adjacency) > topology["routing_invariants"]["max_route_hops"]:
        _fail("max_route_hops_exceeded", f"{path}.routing_invariants.max_route_hops")

    if topology["mesh_kind"] == "axial_agent_mesh":
        for cell_id, neighbors in adjacency.items():
            if len(neighbors) > 6:
                _fail("axial_degree_exceeded", f"{path}.cells")
            for neighbor in neighbors:
                if _axial_distance(
                    genomes[cell_id]["axial_coord"],
                    genomes[neighbor]["axial_coord"],
                ) != 1:
                    _fail("nonadjacent_axial_neighbor", f"{path}.cells")

    for cell_id, genome in genomes.items():
        parent = genome["parent_cell_id"]
        if parent is not None:
            if parent not in genomes:
                _fail("unknown_parent", f"{path}.cells")
            if cell_id not in genomes[parent]["child_cell_ids"]:
                _fail("parent_child_not_reciprocal", f"{path}.cells")
        for child in genome["child_cell_ids"]:
            if child not in genomes or genomes[child]["parent_cell_id"] != cell_id:
                _fail("parent_child_not_reciprocal", f"{path}.cells")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(cell_id: str) -> None:
        if cell_id in visiting:
            _fail("parent_cycle", f"{path}.cells")
        if cell_id in visited:
            return
        visiting.add(cell_id)
        parent = genomes[cell_id]["parent_cell_id"]
        if parent is not None:
            visit(parent)
        visiting.remove(cell_id)
        visited.add(cell_id)

    for cell_id in genomes:
        visit(cell_id)
    if topology["mesh_kind"] == "hierarchical_runtime_shadow":
        roots = [
            cell_id
            for cell_id, genome in genomes.items()
            if genome["parent_cell_id"] is None
        ]
        if len(roots) != 1:
            _fail("hierarchy_requires_single_root", f"{path}.cells")
        if not any(genome["child_cell_ids"] for genome in genomes.values()):
            _fail("hierarchy_relation_missing", f"{path}.cells")


def _verify_required_inputs_bound(
    genomes_by_ref: Mapping[str, Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
) -> None:
    """Bind every required non-Git cell input to a manifest artifact.

    Exact-commit ``repo_artifact`` inputs rebuilt with ``git_checkout`` are
    supplied by the primary Git genome and may be absent from the bundle.
    Every other required input must be content-identical to a required
    manifest artifact, so readiness cannot be asserted around omitted mutable
    state.
    """
    artifacts_by_path = {item["relative_path"]: item for item in artifacts}
    required_sources: dict[str, str] = {}
    for raw_genome in genomes_by_ref.values():
        genome = validate_hex_cell_genome(raw_genome)
        for index, durable_input in enumerate(genome["durable_inputs"]):
            if durable_input["required"] is not True:
                continue
            source_ref = durable_input["source_ref"]
            source_digest = durable_input["source_digest"]
            previous = required_sources.setdefault(source_ref, source_digest)
            if previous != source_digest:
                _fail(
                    "required_input_digest_conflict",
                    f"$.topologies.durable_inputs[{index}]",
                )
            git_primary_input = (
                durable_input["kind"] == "repo_artifact"
                and durable_input["rebuild_strategy"] == "git_checkout"
            )
            artifact = artifacts_by_path.get(source_ref)
            if git_primary_input and artifact is None:
                continue
            if artifact is None:
                _fail(
                    "required_input_artifact_missing",
                    f"$.topologies.durable_inputs[{index}].source_ref",
                )
            if (
                artifact["required"] is not True
                or artifact["content_digest"] != source_digest
            ):
                _fail(
                    "required_input_artifact_mismatch",
                    f"$.topologies.durable_inputs[{index}].source_digest",
                )
            if not git_primary_input:
                allowed_classifications = {
                    "magma_ledger": {"mutable_state"},
                    "snapshot_manifest": {"mutable_state"},
                    "external_export": {"mutable_state"},
                    "model_manifest": {"external_dependency"},
                    "repo_artifact": {
                        "mutable_state",
                        "rebuildable_cache",
                        "external_dependency",
                    },
                }[durable_input["kind"]]
                if artifact["classification"] not in allowed_classifications:
                    _fail(
                        "required_input_artifact_classification_mismatch",
                        f"$.topologies.durable_inputs[{index}].kind",
                    )
            expected_restore = {
                "git_checkout": "git_checkout",
                "replay": "rebuild",
                "verified_copy": "verified_copy",
                "external_reprovision": "external_reprovision",
            }[durable_input["rebuild_strategy"]]
            if artifact["restore_strategy"] != expected_restore:
                _fail(
                    "required_input_restore_strategy_mismatch",
                    f"$.topologies.durable_inputs[{index}].rebuild_strategy",
                )


def _manifest_digest(doc: Mapping[str, Any]) -> str:
    return sha256_digest(
        {key: deepcopy(value) for key, value in doc.items() if key != "manifest_digest"}
    )


def _validate_hive_recovery_manifest(
    doc: object,
    genomes_by_ref: Mapping[str, Mapping[str, Any]] | None = None,
    expected_commit: str | None = None,
    require_recovery_ready: bool = False,
    *,
    allow_structure_only: bool = False,
) -> dict[str, Any]:
    """Validate a recovery manifest, optionally including every referenced genome."""
    manifest = _mapping(doc, "$")
    _exact_keys(manifest, _MANIFEST_KEYS, "$")
    if manifest.get("contract_version") != MANIFEST_CONTRACT_VERSION:
        _fail("contract_version", "$.contract_version")
    if manifest.get("canonicalization") != CANONICALIZATION_VERSION:
        _fail("canonicalization", "$.canonicalization")
    _token(manifest.get("manifest_id"), "$.manifest_id")
    _utc_z(manifest.get("created_at_utc"), "$.created_at_utc")
    source = _validate_source_repository(manifest.get("source_repository"), "$.source_repository")
    if expected_commit is not None:
        if not isinstance(expected_commit, str) or _COMMIT_RE.fullmatch(expected_commit) is None:
            _fail("invalid_expected_commit", "$.source_repository.commit_sha")
        if source["commit_sha"] != expected_commit:
            _fail("exact_commit_mismatch", "$.source_repository.commit_sha")

    raw_topologies = manifest.get("topologies")
    if not isinstance(raw_topologies, list) or not 1 <= len(raw_topologies) <= 64:
        _fail("invalid_topologies", "$.topologies")
    topologies = [
        _validate_topology(item, f"$.topologies[{index}]")
        for index, item in enumerate(raw_topologies)
    ]
    topology_ids = [item["mesh_id"] for item in topologies]
    if len(set(topology_ids)) != len(topology_ids):
        _fail("duplicate_topology", "$.topologies")
    all_cell_refs = [
        (topology["mesh_id"], cell["cell_id"])
        for topology in topologies
        for cell in topology["cells"]
    ]
    if len(set(all_cell_refs)) != len(all_cell_refs):
        _fail("duplicate_mesh_cell", "$.topologies")

    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list) or not 1 <= len(raw_artifacts) <= 10000:
        _fail("invalid_artifacts", "$.artifacts")
    artifacts = [
        _validate_artifact(item, f"$.artifacts[{index}]")
        for index, item in enumerate(raw_artifacts)
    ]
    artifact_ids = [item["artifact_id"] for item in artifacts]
    artifact_paths = [item["relative_path"] for item in artifacts]
    if len(set(artifact_ids)) != len(artifact_ids):
        _fail("duplicate_artifact_id", "$.artifacts")
    if len(set(artifact_paths)) != len(artifact_paths):
        _fail("duplicate_artifact_path", "$.artifacts")
    folded_paths = [path.casefold() for path in artifact_paths]
    if len(set(folded_paths)) != len(folded_paths):
        _fail("artifact_path_case_collision", "$.artifacts")
    path_parts = {PurePosixPath(path).parts for path in artifact_paths}
    for parts in path_parts:
        if any(parts[:end] in path_parts for end in range(1, len(parts))):
            _fail("artifact_file_directory_collision", "$.artifacts")

    genome_paths = {
        item["relative_path"] for item in artifacts if item["classification"] == "genome"
    }
    referenced_genome_paths = {
        cell["genome_ref"] for topology in topologies for cell in topology["cells"]
    }
    if genome_paths != referenced_genome_paths:
        _fail("genome_artifact_reference_mismatch", "$.artifacts")
    if any(
        item["classification"] == "genome" and item["required"] is not True
        for item in artifacts
    ):
        _fail("genome_artifact_must_be_required", "$.artifacts")

    raw_replicas = manifest.get("replicas")
    if not isinstance(raw_replicas, list) or len(raw_replicas) > 10000:
        _fail("invalid_replicas", "$.replicas")
    replicas = [
        _validate_replica(item, f"$.replicas[{index}]")
        for index, item in enumerate(raw_replicas)
    ]
    replica_ids = [item["replica_id"] for item in replicas]
    if len(set(replica_ids)) != len(replica_ids):
        _fail("duplicate_replica_id", "$.replicas")
    artifacts_by_id = {item["artifact_id"]: item for item in artifacts}
    for index, replica in enumerate(replicas):
        artifact = artifacts_by_id.get(replica["artifact_id"])
        if artifact is None:
            _fail("replica_artifact_missing", f"$.replicas[{index}].artifact_id")
        if artifact["content_digest"] != replica["content_digest"]:
            _fail("replica_digest_mismatch", f"$.replicas[{index}].content_digest")

    _validate_policy(manifest.get("recovery_policy"), "$.recovery_policy")
    genome_root = _digest(manifest.get("genome_root"), "$.genome_root")
    memory_root = _digest(manifest.get("memory_root"), "$.memory_root")
    hive_root = _digest(manifest.get("hive_state_root"), "$.hive_state_root")
    external_verified = _boolean(
        manifest.get("external_replication_verified"),
        "$.external_replication_verified",
    )
    blank_disk_verified = _boolean(
        manifest.get("blank_disk_dry_run_verified"),
        "$.blank_disk_dry_run_verified",
    )
    if external_verified is not False:
        _fail(
            "v1_external_replication_claim_forbidden",
            "$.external_replication_verified",
        )
    if blank_disk_verified is not False:
        _fail(
            "v1_blank_disk_claim_forbidden",
            "$.blank_disk_dry_run_verified",
        )
    if manifest.get("production_ready_claim") is not False:
        _fail("production_ready_claim_forbidden", "$.production_ready_claim")
    previous = manifest.get("previous_manifest_digest")
    if previous is not None:
        _digest(previous, "$.previous_manifest_digest")
    _digest(manifest.get("manifest_digest"), "$.manifest_digest")

    if genome_root != compute_genome_root(topologies):
        _fail("genome_root_mismatch", "$.genome_root")
    if memory_root != compute_memory_root(artifacts):
        _fail("memory_root_mismatch", "$.memory_root")
    if hive_root != compute_hive_state_root(source, genome_root, memory_root):
        _fail("hive_state_root_mismatch", "$.hive_state_root")
    if manifest.get("manifest_digest") != _manifest_digest(manifest):
        _fail("manifest_digest_mismatch", "$.manifest_digest")

    if require_recovery_ready:
        _fail("recovery_readiness_not_available_in_v1", "$")

    if genomes_by_ref is None and not allow_structure_only:
        _fail("genome_documents_required", "$.topologies")
    if genomes_by_ref is not None:
        if not isinstance(genomes_by_ref, Mapping):
            _fail("genomes_by_ref_not_mapping", "$.topologies")
        if set(genomes_by_ref) != referenced_genome_paths:
            _fail("genome_document_set_mismatch", "$.topologies")
        for index, topology in enumerate(topologies):
            _verify_topology_genomes(
                topology,
                genomes_by_ref,
                f"$.topologies[{index}]",
            )
        _verify_required_inputs_bound(genomes_by_ref, artifacts)

    return deepcopy(dict(manifest))


def validate_hive_recovery_manifest(
    doc: object,
    genomes_by_ref: Mapping[str, Mapping[str, Any]],
    expected_commit: str | None = None,
    require_recovery_ready: bool = False,
) -> dict[str, Any]:
    """Fully validate a manifest and all referenced cell genomes."""
    return _validate_hive_recovery_manifest(
        doc,
        genomes_by_ref=genomes_by_ref,
        expected_commit=expected_commit,
        require_recovery_ready=require_recovery_ready,
        allow_structure_only=False,
    )


def validate_hive_recovery_manifest_structure(
    doc: object,
    *,
    expected_commit: str | None = None,
) -> dict[str, Any]:
    """Validate manifest structure/digests before its genome blobs are loaded.

    This result is not graph, recovery, or readiness evidence.
    """
    return _validate_hive_recovery_manifest(
        doc,
        genomes_by_ref=None,
        expected_commit=expected_commit,
        require_recovery_ready=False,
        allow_structure_only=True,
    )


def build_hive_recovery_manifest(
    *,
    manifest_id: str,
    created_at_utc: str,
    source_repository: Mapping[str, Any],
    topologies: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    genomes_by_ref: Mapping[str, Mapping[str, Any]],
    replicas: Sequence[Mapping[str, Any]] = (),
    external_replication_verified: bool = False,
    blank_disk_dry_run_verified: bool = False,
    previous_manifest_digest: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic, no-authority recovery manifest."""
    if genomes_by_ref is None:
        _fail("genome_documents_required", "$.topologies")
    if external_replication_verified is not False:
        _fail(
            "v1_external_replication_claim_forbidden",
            "$.external_replication_verified",
        )
    if blank_disk_dry_run_verified is not False:
        _fail(
            "v1_blank_disk_claim_forbidden",
            "$.blank_disk_dry_run_verified",
        )
    normalized_topologies: list[dict[str, Any]] = []
    for topology in topologies:
        raw = deepcopy(dict(topology))
        raw["cells"] = sorted(
            [deepcopy(dict(cell)) for cell in raw.get("cells", [])],
            key=lambda item: (str(item.get("cell_id")), str(item.get("genome_ref"))),
        )
        raw["topology_digest"] = compute_topology_digest(raw)
        normalized_topologies.append(raw)
    normalized_topologies.sort(
        key=lambda item: (
            str(item.get("mesh_id")),
            str(item.get("mesh_kind")),
            int(item.get("topology_epoch", -1)),
        )
    )
    normalized_artifacts = sorted(
        [deepcopy(dict(item)) for item in artifacts],
        key=lambda item: (str(item.get("artifact_id")), str(item.get("relative_path"))),
    )
    normalized_replicas = sorted(
        [deepcopy(dict(item)) for item in replicas],
        key=lambda item: (str(item.get("artifact_id")), str(item.get("replica_id"))),
    )
    source = deepcopy(dict(source_repository))
    genome_root = compute_genome_root(normalized_topologies)
    memory_root = compute_memory_root(normalized_artifacts)
    doc: dict[str, Any] = {
        "contract_version": MANIFEST_CONTRACT_VERSION,
        "canonicalization": CANONICALIZATION_VERSION,
        "manifest_id": manifest_id,
        "created_at_utc": created_at_utc,
        "source_repository": source,
        "topologies": normalized_topologies,
        "artifacts": normalized_artifacts,
        "replicas": normalized_replicas,
        "recovery_policy": {
            "restore_state": "shadow_only",
            "require_exact_commit": True,
            "require_all_digests": True,
            "require_off_host_replica_for_non_git": True,
            "transport_enabled": False,
            "runtime_activation_authority_granted": False,
            "operator_gate_required_for_activation": True,
            "claim_safe_upgrade": False,
        },
        "genome_root": genome_root,
        "memory_root": memory_root,
        "hive_state_root": compute_hive_state_root(source, genome_root, memory_root),
        "external_replication_verified": False,
        "blank_disk_dry_run_verified": False,
        "production_ready_claim": False,
        "previous_manifest_digest": previous_manifest_digest,
        "manifest_digest": "sha256:" + ("0" * 64),
    }
    doc["manifest_digest"] = _manifest_digest(doc)
    return validate_hive_recovery_manifest(
        doc,
        genomes_by_ref=genomes_by_ref,
        expected_commit=source.get("commit_sha"),
        require_recovery_ready=False,
    )


__all__ = [
    "CELL_CONTRACT_VERSION",
    "MANIFEST_CONTRACT_VERSION",
    "CANONICALIZATION_VERSION",
    "MESH_KINDS",
    "ContractValidationError",
    "strict_json_load",
    "strict_json_load_with_digest",
    "sha256_file",
    "validate_repo_relative_path",
    "compute_cell_state_root",
    "build_hex_cell_genome",
    "validate_hex_cell_genome",
    "compute_topology_digest",
    "compute_genome_root",
    "compute_memory_root",
    "compute_hive_state_root",
    "build_hive_recovery_manifest",
    "validate_hive_recovery_manifest",
    "validate_hive_recovery_manifest_structure",
]
