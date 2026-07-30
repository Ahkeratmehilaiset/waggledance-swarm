#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Materialize a validated local HEX recovery bundle into an isolated tree.

This command is deliberately *not* a production restore command.  It performs
all contract, supplied trust-anchor, path, blob, and genome checks before
creating output.  It does not verify that the named Git commit is checked out,
perform replay/reprovisioning, or prove an end-to-end blank-disk recovery.  A
successful run materializes only a sibling-staged shadow artifact tree and
atomically renames that tree to the requested destination.  It never starts
the runtime, installs dependencies, opens network paths, extracts an archive,
or applies recovered state to a live repository.  On failure it removes only
an exact directory-only staging tree; if safe cleanup cannot be proved, the
isolated staging tree remains unpromoted for forensic inspection.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import sys
import unicodedata
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from waggledance.core.hex_topology.recovery_contract import (  # noqa: E402
    ContractValidationError,
    strict_json_load_with_digest,
    validate_hex_cell_genome,
    validate_hive_recovery_manifest,
    validate_hive_recovery_manifest_structure,
    validate_repo_relative_path,
)


REPORT_VERSION = "hex_blank_disk_recovery_dry_run.v1"
REPORT_FILENAME = "hex_blank_disk_recovery_report.json"
COMPLETION_FILENAME = "hex_blank_disk_recovery_completion.json"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_BLOB_NAME_RE = re.compile(r"^[0-9a-f]{64}$")
_REPARSE_FLAG = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
_COPY_CHUNK_BYTES = 1024 * 1024
_MAX_ARTIFACTS = 4096
_MAX_SINGLE_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024
_MAX_MATERIALIZED_BYTES = 64 * 1024 * 1024 * 1024
_MIN_FREE_SPACE_RESERVE_BYTES = 512 * 1024 * 1024


class ShadowRecoveryError(RuntimeError):
    """A redaction-safe shadow recovery failure."""


@dataclass(frozen=True)
class ArtifactPlan:
    artifact_id: str
    relative_path: PurePosixPath
    content_digest: str
    byte_size: int
    classification: str
    blob_path: Path


@dataclass(frozen=True)
class RecoveryPlan:
    bundle_root: Path
    manifest_path: Path
    destination: Path
    manifest: Mapping[str, Any]
    manifest_file_digest: str
    artifacts: tuple[ArtifactPlan, ...]
    blob_count: int
    genome_count: int
    materialized_bytes: int


def _fail(code: str) -> None:
    raise ShadowRecoveryError(code)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _lstat_is_reparse(path: Path) -> bool:
    details = os.lstat(path)
    attributes = int(getattr(details, "st_file_attributes", 0))
    return stat.S_ISLNK(details.st_mode) or bool(attributes & _REPARSE_FLAG)


def _guard_no_reparse_components(path: Path) -> None:
    """Reject symlinks and Windows reparse points in every existing component."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if _lstat_is_reparse(current):
                _fail("path_reparse_not_allowed")
        except FileNotFoundError:
            break


def _reject_remote_or_device_path(raw: str, code: str) -> None:
    supplied = os.fspath(raw)
    normalized = supplied.replace("/", "\\")
    if normalized.startswith("\\\\"):
        _fail(code)
    absolute = os.path.abspath(supplied)
    if absolute.replace("/", "\\").startswith("\\\\"):
        _fail(code)
    if os.name == "nt":
        anchor = Path(absolute).anchor
        try:
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(str(anchor))
        except (AttributeError, OSError):
            drive_type = 0
        if drive_type == 4:  # DRIVE_REMOTE
            _fail(code)


def _require_regular_file(path: Path, code: str) -> os.stat_result:
    _guard_no_reparse_components(path)
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        _fail(code)
    if _lstat_is_reparse(path) or not stat.S_ISREG(details.st_mode):
        _fail("nonregular_file_not_allowed")
    if int(details.st_nlink) != 1:
        _fail("multiply_linked_file_not_allowed")
    return details


def _stable_sha256(
    path: Path,
    code: str,
    *,
    expected_size: int | None = None,
) -> tuple[str, os.stat_result]:
    before = _require_regular_file(path, code)
    if expected_size is not None and int(before.st_size) != expected_size:
        _fail("source_size_mismatch")
    hasher = hashlib.sha256()
    observed_size = 0
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                observed_size += len(chunk)
                if expected_size is not None and observed_size > expected_size:
                    _fail("source_size_mismatch")
                hasher.update(chunk)
    except ShadowRecoveryError:
        raise
    except OSError:
        _fail(code)
    if expected_size is not None and observed_size != expected_size:
        _fail("source_size_mismatch")
    digest = f"sha256:{hasher.hexdigest()}"
    after = _require_regular_file(path, code)
    before_signature = (
        int(before.st_dev),
        int(before.st_ino),
        int(before.st_size),
        int(before.st_mtime_ns),
    )
    after_signature = (
        int(after.st_dev),
        int(after.st_ino),
        int(after.st_size),
        int(after.st_mtime_ns),
    )
    if before_signature != after_signature:
        _fail("source_changed_during_preflight")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        _fail("invalid_sha256_result")
    return digest, after


def _bounded_directory_entries(
    path: Path,
    *,
    maximum: int,
    error_code: str,
) -> list[os.DirEntry[str]]:
    entries: list[os.DirEntry[str]] = []
    try:
        with os.scandir(path) as iterator:
            for entry in iterator:
                entries.append(entry)
                if len(entries) > maximum:
                    _fail(error_code)
    except ShadowRecoveryError:
        raise
    except OSError:
        _fail(error_code)
    return entries


def _resolve_bundle_root(raw: str) -> Path:
    _reject_remote_or_device_path(raw, "bundle_must_be_local_filesystem")
    lexical = Path(os.path.abspath(raw))
    _guard_no_reparse_components(lexical)
    try:
        resolved = lexical.resolve(strict=True)
    except (FileNotFoundError, OSError):
        _fail("bundle_root_missing")
    _guard_no_reparse_components(resolved)
    try:
        details = os.lstat(resolved)
    except OSError:
        _fail("bundle_root_unreadable")
    if _lstat_is_reparse(resolved) or not stat.S_ISDIR(details.st_mode):
        _fail("bundle_root_not_regular_directory")
    return resolved


def _resolve_manifest(bundle_root: Path, raw: str) -> Path:
    supplied = Path(raw)
    lexical = supplied if supplied.is_absolute() else bundle_root / supplied
    lexical = Path(os.path.abspath(os.fspath(lexical)))
    _guard_no_reparse_components(lexical)
    try:
        resolved = lexical.resolve(strict=True)
    except (FileNotFoundError, OSError):
        _fail("manifest_missing")
    if not _is_relative_to(resolved, bundle_root):
        _fail("manifest_outside_bundle")
    # V1 has one root manifest plus the content-addressed blob directory.
    if resolved.parent != bundle_root:
        _fail("manifest_must_be_bundle_root_file")
    _require_regular_file(resolved, "manifest_missing")
    return resolved


def _resolve_destination(raw: str, bundle_root: Path) -> Path:
    _reject_remote_or_device_path(raw, "destination_must_be_local_filesystem")
    try:
        validate_repo_relative_path(Path(raw).name, "destination.name")
    except ContractValidationError:
        _fail("unsafe_destination_name")
    lexical = Path(os.path.abspath(raw))
    _guard_no_reparse_components(lexical)
    destination = lexical.resolve(strict=False)
    parent = destination.parent
    _guard_no_reparse_components(parent)
    try:
        parent_details = os.lstat(parent)
    except FileNotFoundError:
        _fail("destination_parent_missing")
    if _lstat_is_reparse(parent) or not stat.S_ISDIR(parent_details.st_mode):
        _fail("destination_parent_not_regular_directory")

    filesystem_root = Path(destination.anchor).resolve(strict=False)
    home = Path.home().resolve(strict=False)
    repo = REPO_ROOT.resolve(strict=True)

    # The destination may not overlap either source tree.  It also may not be
    # a broad filesystem/home target or an ancestor of one.
    if (
        destination == filesystem_root
        or destination == home
        or _is_relative_to(home, destination)
        or destination == bundle_root
        or _is_relative_to(destination, bundle_root)
        or _is_relative_to(bundle_root, destination)
        or destination == repo
        or _is_relative_to(destination, repo)
        or _is_relative_to(repo, destination)
    ):
        _fail("unsafe_destination_scope")

    if destination.exists():
        _guard_no_reparse_components(destination)
        details = os.lstat(destination)
        if _lstat_is_reparse(destination) or not stat.S_ISDIR(details.st_mode):
            _fail("destination_not_regular_directory")
        try:
            next(destination.iterdir())
        except StopIteration:
            pass
        except OSError:
            _fail("destination_unreadable")
        else:
            _fail("destination_not_empty")
        # Replacing even an empty directory is not a portable no-overwrite
        # operation (notably on Windows).  V1 therefore fails closed and
        # requires the final atomic-rename target to be absent.
        _fail("destination_must_not_exist_for_atomic_promotion")
    elif os.path.lexists(os.fspath(destination)):
        _fail("destination_reparse_not_allowed")
    return destination


def _validate_cli_anchors(expected_commit: str, expected_digest: str) -> None:
    if not _COMMIT_RE.fullmatch(expected_commit):
        _fail("expected_commit_invalid")
    if not _SHA256_RE.fullmatch(expected_digest):
        _fail("expected_manifest_digest_invalid")


def _artifact_plans(
    manifest: Mapping[str, Any],
    bundle_root: Path,
) -> tuple[tuple[ArtifactPlan, ...], dict[str, ArtifactPlan]]:
    raw_artifacts = manifest.get("artifacts")
    if (
        not isinstance(raw_artifacts, list)
        or not raw_artifacts
        or len(raw_artifacts) > _MAX_ARTIFACTS
    ):
        _fail("manifest_artifacts_missing")

    plans: list[ArtifactPlan] = []
    by_relative_path: dict[str, ArtifactPlan] = {}
    casefold_paths: set[str] = set()
    materialized_bytes = 0
    for index, artifact in enumerate(raw_artifacts):
        if not isinstance(artifact, Mapping):
            _fail("artifact_not_object")
        relative = validate_repo_relative_path(
            artifact.get("relative_path"),
            f"artifacts[{index}].relative_path",
        )
        if not isinstance(relative, PurePosixPath):
            relative = PurePosixPath(str(relative))
        relative_text = relative.as_posix()
        casefold_key = unicodedata.normalize("NFC", relative_text).casefold()
        first_segment_key = unicodedata.normalize(
            "NFC",
            relative.parts[0],
        ).casefold()
        if first_segment_key in {
            REPORT_FILENAME.casefold(),
            COMPLETION_FILENAME.casefold(),
        }:
            _fail("artifact_collides_with_report")
        if relative_text in by_relative_path or casefold_key in casefold_paths:
            _fail("artifact_path_collision")

        digest = artifact.get("content_digest")
        size = artifact.get("byte_size")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            _fail("artifact_content_digest_invalid")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            _fail("artifact_byte_size_invalid")
        if size > _MAX_SINGLE_ARTIFACT_BYTES:
            _fail("artifact_too_large")
        materialized_bytes += size
        if materialized_bytes > _MAX_MATERIALIZED_BYTES:
            _fail("shadow_materialization_too_large")
        artifact_id = artifact.get("artifact_id")
        classification = artifact.get("classification")
        if not isinstance(artifact_id, str) or not artifact_id:
            _fail("artifact_id_invalid")
        if not isinstance(classification, str) or not classification:
            _fail("artifact_classification_invalid")

        blob = bundle_root / "blobs" / "sha256" / digest.removeprefix("sha256:")
        plan = ArtifactPlan(
            artifact_id=artifact_id,
            relative_path=relative,
            content_digest=digest,
            byte_size=size,
            classification=classification,
            blob_path=blob,
        )
        plans.append(plan)
        by_relative_path[relative_text] = plan
        casefold_paths.add(casefold_key)

    # A file cannot also be the parent directory of another file.
    parts = {p.relative_path.parts for p in plans}
    for value in parts:
        for end in range(1, len(value)):
            if value[:end] in parts:
                _fail("artifact_file_directory_collision")
    return tuple(plans), by_relative_path


def _preflight_blob_directory(
    bundle_root: Path,
    manifest_path: Path,
    artifacts: Sequence[ArtifactPlan],
) -> int:
    blob_root = bundle_root / "blobs" / "sha256"
    _guard_no_reparse_components(blob_root)
    try:
        details = os.lstat(blob_root)
    except FileNotFoundError:
        _fail("blob_root_missing")
    if _lstat_is_reparse(blob_root) or not stat.S_ISDIR(details.st_mode):
        _fail("blob_root_not_regular_directory")

    allowed_top_level = {manifest_path.name, "blobs"}
    try:
        top_level = _bounded_directory_entries(
            bundle_root,
            maximum=2,
            error_code="bundle_contains_unexpected_entries",
        )
    except ShadowRecoveryError:
        raise
    if {entry.name for entry in top_level} != allowed_top_level:
        _fail("bundle_contains_unexpected_entries")

    blobs_parent = bundle_root / "blobs"
    try:
        blobs_children = _bounded_directory_entries(
            blobs_parent,
            maximum=1,
            error_code="blob_namespace_contains_unexpected_entries",
        )
    except ShadowRecoveryError:
        raise
    if (
        len(blobs_children) != 1
        or blobs_children[0].name != "sha256"
        or _lstat_is_reparse(blobs_children[0])
        or not stat.S_ISDIR(os.lstat(blobs_children[0]).st_mode)
    ):
        _fail("blob_namespace_contains_unexpected_entries")

    expected_names = {
        artifact.content_digest.removeprefix("sha256:") for artifact in artifacts
    }
    try:
        actual_entries = _bounded_directory_entries(
            blob_root,
            maximum=len(expected_names),
            error_code="unexpected_blob",
        )
    except ShadowRecoveryError:
        raise
    actual_names: set[str] = set()
    for entry in actual_entries:
        if not _BLOB_NAME_RE.fullmatch(entry.name):
            _fail("unexpected_blob")
        _require_regular_file(entry, "blob_missing")
        actual_names.add(entry.name)
    if actual_names != expected_names:
        if expected_names - actual_names:
            _fail("blob_missing")
        _fail("unexpected_blob")

    expected_size_by_digest: dict[str, int] = {}
    for artifact in artifacts:
        previous = expected_size_by_digest.setdefault(
            artifact.content_digest,
            artifact.byte_size,
        )
        if previous != artifact.byte_size:
            _fail("shared_blob_size_conflict")
    for digest, size in expected_size_by_digest.items():
        blob = blob_root / digest.removeprefix("sha256:")
        observed_digest, details = _stable_sha256(
            blob,
            "blob_missing",
            expected_size=size,
        )
        if observed_digest != digest:
            _fail("blob_digest_mismatch")
        if int(details.st_size) != size:
            _fail("blob_size_mismatch")
    return len(actual_names)


def _genome_documents(
    manifest: Mapping[str, Any],
    plans_by_path: Mapping[str, ArtifactPlan],
) -> dict[str, Mapping[str, Any]]:
    cells_by_ref: dict[str, Mapping[str, Any]] = {}
    topologies = manifest.get("topologies")
    if not isinstance(topologies, list):
        _fail("manifest_topologies_missing")
    for topology in topologies:
        if not isinstance(topology, Mapping):
            _fail("topology_not_object")
        cells = topology.get("cells")
        if not isinstance(cells, list):
            _fail("topology_cells_missing")
        for cell in cells:
            if not isinstance(cell, Mapping):
                _fail("topology_cell_not_object")
            ref_path = validate_repo_relative_path(
                cell.get("genome_ref"),
                "topologies.cells.genome_ref",
            ).as_posix()
            if ref_path in cells_by_ref:
                _fail("genome_ref_not_unique")
            cells_by_ref[ref_path] = cell

    genome_plans = {
        relative: plan
        for relative, plan in plans_by_path.items()
        if plan.classification == "genome"
    }
    if set(genome_plans) != set(cells_by_ref):
        _fail("genome_artifact_reference_mismatch")

    genomes: dict[str, Mapping[str, Any]] = {}
    for ref_path, plan in genome_plans.items():
        raw, parsed_blob_digest = strict_json_load_with_digest(plan.blob_path)
        if parsed_blob_digest != plan.content_digest:
            _fail("genome_blob_changed_after_preflight")
        genome = validate_hex_cell_genome(raw)
        cell = cells_by_ref[ref_path]
        if (
            genome.get("cell_id") != cell.get("cell_id")
            or genome.get("genome_digest") != cell.get("genome_digest")
            or genome.get("expected_cell_state_root")
            != cell.get("expected_cell_state_root")
        ):
            _fail("genome_topology_reference_mismatch")
        genomes[ref_path] = genome
    return genomes


def build_recovery_plan(
    *,
    bundle_root_arg: str,
    manifest_arg: str,
    destination_arg: str,
    expected_commit: str,
    expected_manifest_digest: str,
) -> RecoveryPlan:
    """Complete every read-only preflight and return an immutable copy plan."""
    _validate_cli_anchors(expected_commit, expected_manifest_digest)
    bundle_root = _resolve_bundle_root(bundle_root_arg)
    manifest_path = _resolve_manifest(bundle_root, manifest_arg)
    destination = _resolve_destination(destination_arg, bundle_root)

    raw_manifest, manifest_file_digest = strict_json_load_with_digest(manifest_path)
    if manifest_file_digest != expected_manifest_digest:
        _fail("trusted_manifest_digest_mismatch")

    first_pass = validate_hive_recovery_manifest_structure(
        raw_manifest,
        expected_commit=expected_commit,
    )
    artifacts, plans_by_path = _artifact_plans(first_pass, bundle_root)
    blob_count = _preflight_blob_directory(
        bundle_root,
        manifest_path,
        artifacts,
    )
    genomes = _genome_documents(first_pass, plans_by_path)
    manifest = validate_hive_recovery_manifest(
        raw_manifest,
        genomes_by_ref=genomes,
        expected_commit=expected_commit,
        require_recovery_ready=False,
    )
    return RecoveryPlan(
        bundle_root=bundle_root,
        manifest_path=manifest_path,
        destination=destination,
        manifest=manifest,
        manifest_file_digest=manifest_file_digest,
        artifacts=artifacts,
        blob_count=blob_count,
        genome_count=len(genomes),
        materialized_bytes=sum(artifact.byte_size for artifact in artifacts),
    )


def _open_flags(base: int) -> int:
    return (
        base
        | int(getattr(os, "O_BINARY", 0))
        | int(getattr(os, "O_CLOEXEC", 0))
        | int(getattr(os, "O_NOFOLLOW", 0))
    )


def _mkdir_relative_tree(root: Path, relative_parent: PurePosixPath) -> Path:
    current = root
    for part in relative_parent.parts:
        current = current / part
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            details = os.lstat(current)
            if _lstat_is_reparse(current) or not stat.S_ISDIR(details.st_mode):
                _fail("shadow_parent_collision")
    return current


def _copy_exclusive(
    source: Path,
    target: Path,
    expected_digest: str,
    expected_size: int,
) -> None:
    source_before = _require_regular_file(source, "blob_missing")
    if int(source_before.st_size) != expected_size:
        _fail("source_size_changed_before_copy")
    source_fd = os.open(source, _open_flags(os.O_RDONLY))
    target_fd: int | None = None
    copied_digest = hashlib.sha256()
    copied_bytes = 0
    try:
        source_open = os.fstat(source_fd)
        if (
            int(source_before.st_dev),
            int(source_before.st_ino),
        ) != (
            int(source_open.st_dev),
            int(source_open.st_ino),
        ):
            _fail("source_identity_changed")
        if int(source_open.st_size) != expected_size:
            _fail("source_size_changed_before_copy")
        target_fd = os.open(
            target,
            _open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
            0o600,
        )
        while True:
            chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            copied_bytes += len(chunk)
            if copied_bytes > expected_size:
                _fail("source_size_changed_during_copy")
            copied_digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(target_fd, view)
                if written <= 0:
                    _fail("shadow_copy_write_failed")
                view = view[written:]
        os.fsync(target_fd)
        source_after = os.fstat(source_fd)
        source_path_after = _require_regular_file(source, "blob_missing")
        signatures = {
            (
                int(item.st_dev),
                int(item.st_ino),
                int(item.st_size),
                int(item.st_mtime_ns),
            )
            for item in (
                source_before,
                source_open,
                source_after,
                source_path_after,
            )
        }
        if len(signatures) != 1:
            _fail("source_changed_during_copy")
        if int(source_after.st_size) != expected_size:
            _fail("source_size_changed_during_copy")
    finally:
        if target_fd is not None:
            os.close(target_fd)
        os.close(source_fd)

    if f"sha256:{copied_digest.hexdigest()}" != expected_digest:
        _fail("copied_blob_digest_mismatch")
    if copied_bytes != expected_size:
        _fail("source_size_changed_during_copy")
    if os.lstat(target).st_size != expected_size:
        _fail("shadow_copy_size_mismatch")
    target_digest, _ = _stable_sha256(
        target,
        "shadow_rehash_failed",
        expected_size=expected_size,
    )
    if target_digest != expected_digest:
        _fail("shadow_rehash_mismatch")


def _exclusive_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    raw = (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    fd = os.open(
        path,
        _open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                _fail("report_write_failed")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _base_report() -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "ok": False,
        "restore_state": "shadow_only",
        "restore_applied": False,
        "shadow_rebuild_completed": False,
        "shadow_tree_materialized": False,
        "artifact_materialization_completed": False,
        "promotion_completed": False,
        "source_commit_anchor_matched": False,
        "exact_commit_checkout_verified": False,
        "runtime_started": False,
        "transport_enabled": False,
        "production_ready_claim": False,
        "blank_disk_claim_safe": False,
    }


def _require_recovery_headroom(plan: RecoveryPlan) -> None:
    try:
        free_bytes = shutil.disk_usage(plan.destination.parent).free
    except OSError:
        _fail("destination_free_space_unavailable")
    required = plan.materialized_bytes + _MIN_FREE_SPACE_RESERVE_BYTES
    if free_bytes < required:
        _fail("destination_free_space_insufficient")


def _verify_staging_tree(
    staging: Path,
    plan: RecoveryPlan,
    report: Mapping[str, Any],
) -> None:
    """Recheck exact inventory, type, size, digest, and report before promotion."""
    expected_files = {
        artifact.relative_path.as_posix(): artifact for artifact in plan.artifacts
    }
    expected_file_names = set(expected_files) | {REPORT_FILENAME}
    expected_directories: set[str] = set()
    for relative_text in expected_file_names:
        parts = PurePosixPath(relative_text).parts[:-1]
        for end in range(1, len(parts) + 1):
            expected_directories.add(PurePosixPath(*parts[:end]).as_posix())

    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    stack: list[tuple[Path, PurePosixPath]] = [(staging, PurePosixPath())]
    while stack:
        current, relative_parent = stack.pop()
        if _lstat_is_reparse(current):
            _fail("staging_reparse_not_allowed")
        try:
            iterator = os.scandir(current)
        except OSError:
            _fail("staging_inventory_unreadable")
        with iterator:
            for entry in iterator:
                if len(actual_files) + len(actual_directories) >= (
                    len(expected_file_names) + len(expected_directories)
                ):
                    _fail("staging_inventory_mismatch")
                child = Path(entry.path)
                details = os.lstat(child)
                attributes = int(getattr(details, "st_file_attributes", 0))
                if stat.S_ISLNK(details.st_mode) or bool(attributes & _REPARSE_FLAG):
                    _fail("staging_reparse_not_allowed")
                relative = relative_parent / entry.name
                relative_text = relative.as_posix()
                if stat.S_ISDIR(details.st_mode):
                    actual_directories.add(relative_text)
                    stack.append((child, relative))
                elif stat.S_ISREG(details.st_mode) and int(details.st_nlink) == 1:
                    actual_files.add(relative_text)
                else:
                    _fail("staging_nonregular_entry")

    if actual_files != expected_file_names or actual_directories != expected_directories:
        _fail("staging_inventory_mismatch")
    for relative_text, artifact in expected_files.items():
        target = staging.joinpath(*PurePosixPath(relative_text).parts)
        observed_digest, details = _stable_sha256(
            target,
            "staging_artifact_missing",
            expected_size=artifact.byte_size,
        )
        if int(details.st_size) != artifact.byte_size:
            _fail("staging_artifact_size_mismatch")
        if observed_digest != artifact.content_digest:
            _fail("staging_artifact_digest_mismatch")
    observed_report, _ = strict_json_load_with_digest(staging / REPORT_FILENAME)
    if observed_report != dict(report):
        _fail("staging_report_mismatch")


def _cleanup_directory_only_owned_staging(
    staging: Path,
    destination: Path,
) -> None:
    """Remove an owned staging tree only when it contains directories, no files."""
    expected_prefix = f".{destination.name}.shadow-stage-"
    try:
        staging_abs = Path(os.path.abspath(os.fspath(staging)))
        parent_abs = Path(os.path.abspath(os.fspath(destination.parent)))
        if (
            staging_abs.parent != parent_abs
            or not staging_abs.name.startswith(expected_prefix)
            or len(staging_abs.name) != len(expected_prefix) + 16
            or not staging_abs.exists()
            or _lstat_is_reparse(staging_abs)
        ):
            return
        _guard_no_reparse_components(staging_abs)
        directories: list[Path] = []
        for root, child_dirs, files in os.walk(staging_abs, topdown=False):
            root_path = Path(root)
            if files or _lstat_is_reparse(root_path):
                return
            for child in child_dirs:
                child_path = root_path / child
                if _lstat_is_reparse(child_path):
                    return
                directories.append(child_path)
        for child in directories:
            os.rmdir(child)
        os.rmdir(staging_abs)
    except OSError:
        # Never recurse into a non-empty or changed path.  A partial isolated
        # staging tree remains unpromoted for manual inspection.
        return


def materialize_shadow(plan: RecoveryPlan) -> dict[str, Any]:
    """Copy a fully preflighted plan via sibling staging and atomic rename."""
    if not isinstance(plan, RecoveryPlan) or not isinstance(plan.manifest, Mapping):
        _fail("invalid_recovery_plan")
    source = plan.manifest.get("source_repository")
    if not isinstance(source, Mapping):
        _fail("invalid_recovery_plan")
    expected_commit = source.get("commit_sha")
    if not isinstance(expected_commit, str) or not _COMMIT_RE.fullmatch(expected_commit):
        _fail("invalid_recovery_plan")
    # The write boundary does not trust caller-constructed dataclasses.  Rebuild
    # the plan from its content-addressed bundle and discard all supplied
    # ArtifactPlan paths before any directory is created.
    plan = build_recovery_plan(
        bundle_root_arg=str(plan.bundle_root),
        manifest_arg=str(plan.manifest_path),
        destination_arg=str(plan.destination),
        expected_commit=expected_commit,
        expected_manifest_digest=plan.manifest_file_digest,
    )
    destination = plan.destination
    _guard_no_reparse_components(destination.parent)
    if os.path.lexists(os.fspath(destination)):
        _fail("destination_reparse_not_allowed")
    _require_recovery_headroom(plan)

    staging: Path | None = None
    for _ in range(16):
        candidate = destination.parent / (
            f".{destination.name}.shadow-stage-{secrets.token_hex(8)}"
        )
        try:
            os.mkdir(candidate, 0o700)
        except FileExistsError:
            continue
        staging = candidate
        break
    if staging is None:
        _fail("staging_name_exhausted")

    try:
        for artifact in plan.artifacts:
            parent = _mkdir_relative_tree(
                staging,
                PurePosixPath(*artifact.relative_path.parts[:-1]),
            )
            target = parent / artifact.relative_path.name
            _copy_exclusive(
                artifact.blob_path,
                target,
                artifact.content_digest,
                artifact.byte_size,
            )

        staging_report = {
            **_base_report(),
            "manifest_file_digest": plan.manifest_file_digest,
            "manifest_digest": plan.manifest.get("manifest_digest"),
            "source_commit": plan.manifest.get("source_repository", {}).get(
                "commit_sha"
            ),
            "artifact_count": len(plan.artifacts),
            "blob_count": plan.blob_count,
            "genome_count": plan.genome_count,
            "shadow_tree_materialized": True,
            "artifact_materialization_completed": True,
            "source_commit_anchor_matched": True,
        }
        _exclusive_write_json(staging / REPORT_FILENAME, staging_report)
        _verify_staging_tree(staging, plan, staging_report)

        # Re-check the final target at the last possible moment.  On Windows,
        # os.rename does not replace an existing destination.  Combined with
        # the lexists check this is the strongest portable no-overwrite
        # directory promotion available in the Python standard library.
        _guard_no_reparse_components(destination.parent)
        if os.path.lexists(os.fspath(destination)):
            _fail("destination_changed_before_promotion")
        os.rename(staging, destination)
        report = {
            **staging_report,
            "ok": True,
            "promotion_completed": True,
        }
        _exclusive_write_json(destination / COMPLETION_FILENAME, report)
        return report
    except BaseException:
        _cleanup_directory_only_owned_staging(staging, destination)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-manifest-digest", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = build_recovery_plan(
            bundle_root_arg=args.bundle_root,
            manifest_arg=args.manifest,
            destination_arg=args.destination,
            expected_commit=args.expected_commit,
            expected_manifest_digest=args.expected_manifest_digest,
        )
        report = materialize_shadow(plan)
    except ContractValidationError:
        report = {**_base_report(), "error_code": "contract_validation_failed"}
    except ShadowRecoveryError as exc:
        report = {**_base_report(), "error_code": str(exc)}
    except (OSError, ValueError, TypeError):
        report = {**_base_report(), "error_code": "shadow_rebuild_failed"}
    else:
        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print("HEX shadow recovery dry-run: PASS")
        return 0

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            f"HEX shadow recovery dry-run: FAIL ({report['error_code']})",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
