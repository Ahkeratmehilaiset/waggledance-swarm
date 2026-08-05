# SPDX-License-Identifier: BUSL-1.1
"""Immutable local MAGMA storage for scope-bound attestation-log artifacts.

Artifacts are verified canonical bytes and published without overwrite under
their content digest.  This is deliberately a per-scope partition store; the
global base log's mixed-scope snapshots are outside its contract.  The store
has no mutable current-head pointer, authenticates no writer, and grants no
admission, routing, activation, or execution authority.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from waggledance.core.orchestration.attestation_log_artifact import (
    AttestationLogArtifactError,
    canonicalize_attestation_log_artifact,
    parse_attestation_log_artifact,
)

MAX_ATTESTATION_LOG_ARTIFACT_BYTES = 16 * 1024 * 1024

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class AttestationLogArtifactStoreError(RuntimeError):
    """The artifact store refused an unsafe or inconsistent operation."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _refuse(reason: str, message: str) -> None:
    raise AttestationLogArtifactStoreError(reason, message)


def _digest(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        _refuse(label, f"{label} must be a lowercase sha256 digest")
    return value


def _is_reparse_point(metadata: os.stat_result) -> bool:
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & marker)


def _directory_metadata(path: Path, label: str) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        _refuse(f"{label}_missing", f"{label} does not exist")
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
        _refuse(f"{label}_symlink", f"{label} may not be a link/reparse point")
    if not stat.S_ISDIR(metadata.st_mode):
        _refuse(f"{label}_not_directory", f"{label} must be a directory")
    return metadata


def _ensure_directory(path: Path, label: str) -> None:
    try:
        os.mkdir(path)
    except FileExistsError:
        pass
    except FileNotFoundError:
        _refuse(f"{label}_parent_missing", f"{label} parent does not exist")
    _directory_metadata(path, label)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        # Windows normally refuses fsync on directory handles.  The artifact
        # file itself has already crossed a mandatory fsync boundary.
        pass
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class AttestationLogArtifactRecord:
    artifact_digest: str
    log_head_digest: str
    activation_scope_digest: str
    path: Path
    size: int


class AttestationLogArtifactStore:
    """Explicit-root content-addressed attestation-log artifact store."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        try:
            raw_root = os.fspath(root)
        except TypeError as exc:
            raise AttestationLogArtifactStoreError(
                "root", "artifact root must be an explicit filesystem path"
            ) from exc
        if raw_root in ("", b""):
            _refuse("root", "artifact root may not be empty")
        try:
            self._root = Path(os.path.abspath(raw_root))
        except (TypeError, ValueError) as exc:
            raise AttestationLogArtifactStoreError(
                "root", "artifact root must be an explicit filesystem path"
            ) from exc
        try:
            os.mkdir(self._root)
        except FileExistsError:
            pass
        except FileNotFoundError:
            try:
                self._root.mkdir(parents=True, exist_ok=True)
            except (OSError, ValueError) as exc:
                raise AttestationLogArtifactStoreError(
                    "root_create", "artifact root could not be created"
                ) from exc
        except (OSError, ValueError) as exc:
            raise AttestationLogArtifactStoreError(
                "root_create", "artifact root could not be created"
            ) from exc
        _directory_metadata(self._root, "root")

    @property
    def root(self) -> Path:
        return self._root

    def _artifact_path(self, artifact_digest: str) -> Path:
        digest = _digest(artifact_digest, "artifact_digest")
        hexadecimal = digest.removeprefix("sha256:")
        return (
            self._root
            / hexadecimal[:2]
            / hexadecimal[2:4]
            / f"{hexadecimal}.json"
        )

    def _ensure_shards(self, target: Path) -> None:
        _directory_metadata(self._root, "root")
        _ensure_directory(target.parent.parent, "first_shard")
        _ensure_directory(target.parent, "second_shard")

    @staticmethod
    def _target_metadata(path: Path) -> Optional[os.stat_result]:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
            _refuse("target_symlink", "artifact target may not be a link")
        if not stat.S_ISREG(metadata.st_mode):
            _refuse("target_not_regular", "artifact target must be regular")
        if not 0 < metadata.st_size <= MAX_ATTESTATION_LOG_ARTIFACT_BYTES:
            _refuse("stored_size", "stored artifact violates its byte bound")
        return metadata

    @staticmethod
    def _read_regular(path: Path, metadata: os.stat_result) -> bytes:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            _refuse("target_raced", "artifact disappeared during read")
        except OSError as exc:
            raise AttestationLogArtifactStoreError(
                "target_open", "artifact could not be opened safely"
            ) from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                _refuse("target_not_regular", "artifact target is not regular")
            if (opened.st_dev, opened.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                _refuse("target_raced", "artifact target changed during open")
            if opened.st_size != metadata.st_size:
                _refuse("target_raced", "artifact size changed during open")
            chunks: list[bytes] = []
            remaining = opened.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    _refuse("stored_truncated", "stored artifact is truncated")
                chunks.append(chunk)
                remaining -= len(chunk)
            finished = os.fstat(descriptor)
            stable_fields = (
                "st_dev",
                "st_ino",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if any(
                getattr(finished, field) != getattr(opened, field)
                for field in stable_fields
            ):
                _refuse("target_raced", "artifact changed during read")
            data = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(data) != metadata.st_size:
            _refuse("stored_truncated", "stored artifact size changed")
        return data

    @staticmethod
    def _decode_and_verify(
        data: bytes,
        *,
        expected_activation_scope_digest: str,
        expected_log_head_digest: str,
        expected_artifact_digest: Optional[str] = None,
        label: str,
    ) -> dict[str, object]:
        try:
            decoded = json.loads(data)
        except (ValueError, RecursionError) as exc:
            raise AttestationLogArtifactStoreError(
                f"{label}_json", f"{label} is not strict JSON"
            ) from exc
        try:
            canonical = canonicalize_attestation_log_artifact(
                decoded,
                expected_activation_scope_digest=(
                    expected_activation_scope_digest
                ),
                expected_log_head_digest=expected_log_head_digest,
            )
            parsed = parse_attestation_log_artifact(
                decoded,
                expected_activation_scope_digest=(
                    expected_activation_scope_digest
                ),
                expected_log_head_digest=expected_log_head_digest,
            )
        except AttestationLogArtifactError as exc:
            raise AttestationLogArtifactStoreError(
                f"{label}:{exc.reason}", f"{label} contract refused"
            ) from exc
        if canonical != data:
            _refuse(f"{label}_noncanonical", f"{label} is not canonical bytes")
        if (
            expected_artifact_digest is not None
            and parsed["artifact_digest"] != expected_artifact_digest
        ):
            _refuse(
                f"{label}_name_binding",
                f"{label} digest differs from its storage name",
            )
        return parsed

    def _read_existing(
        self,
        path: Path,
        *,
        expected_artifact_digest: str,
        expected_activation_scope_digest: str,
        expected_log_head_digest: str,
    ) -> Optional[bytes]:
        metadata = self._target_metadata(path)
        if metadata is None:
            return None
        data = self._read_regular(path, metadata)
        self._decode_and_verify(
            data,
            expected_activation_scope_digest=expected_activation_scope_digest,
            expected_log_head_digest=expected_log_head_digest,
            expected_artifact_digest=expected_artifact_digest,
            label="stored_artifact",
        )
        return data

    def append(
        self,
        canonical_artifact: bytes,
        *,
        expected_activation_scope_digest: str,
        expected_log_head_digest: str,
    ) -> AttestationLogArtifactRecord:
        """Verify and atomically publish one immutable artifact envelope."""

        scope = _digest(
            expected_activation_scope_digest,
            "expected_activation_scope_digest",
        )
        head = _digest(expected_log_head_digest, "expected_log_head_digest")
        if type(canonical_artifact) is not bytes:
            _refuse(
                "canonical_artifact_type",
                "canonical_artifact must be exact bytes",
            )
        if not canonical_artifact or len(canonical_artifact) > (
            MAX_ATTESTATION_LOG_ARTIFACT_BYTES
        ):
            _refuse(
                "canonical_artifact_size",
                "artifact is empty or exceeds its byte bound",
            )
        parsed = self._decode_and_verify(
            canonical_artifact,
            expected_activation_scope_digest=scope,
            expected_log_head_digest=head,
            label="canonical_artifact",
        )
        artifact_digest = _digest(parsed["artifact_digest"], "artifact_digest")
        target = self._artifact_path(artifact_digest)
        self._ensure_shards(target)

        existing = self._read_existing(
            target,
            expected_artifact_digest=artifact_digest,
            expected_activation_scope_digest=scope,
            expected_log_head_digest=head,
        )
        if existing is not None:
            if existing != canonical_artifact:
                _refuse(
                    "existing_content_mismatch",
                    "artifact digest names different stored bytes",
                )
            return AttestationLogArtifactRecord(
                artifact_digest=artifact_digest,
                log_head_digest=head,
                activation_scope_digest=scope,
                path=target,
                size=len(canonical_artifact),
            )

        descriptor = -1
        temporary_name: Optional[str] = None
        primary_error = False
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".attestation-log-",
                suffix=".pending",
                dir=target.parent,
            )
            view = memoryview(canonical_artifact)
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written:])
                if count <= 0:
                    _refuse("temporary_write", "artifact write made no progress")
                written += count
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                os.link(temporary_name, target)
                _fsync_directory(target.parent)
            except FileExistsError:
                concurrent = self._read_existing(
                    target,
                    expected_artifact_digest=artifact_digest,
                    expected_activation_scope_digest=scope,
                    expected_log_head_digest=head,
                )
                if concurrent != canonical_artifact:
                    _refuse(
                        "existing_content_mismatch",
                        "concurrent artifact differs for the same digest",
                    )
            except OSError as exc:
                raise AttestationLogArtifactStoreError(
                    "publish", "artifact could not be published without overwrite"
                ) from exc
        except BaseException:
            primary_error = True
            raise
        finally:
            cleanup_error: Optional[OSError] = None
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    cleanup_error = exc
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            if cleanup_error is not None and not primary_error:
                raise AttestationLogArtifactStoreError(
                    "post_publish_cleanup",
                    "artifact may be published but temporary cleanup failed",
                ) from cleanup_error

        published = self._read_existing(
            target,
            expected_artifact_digest=artifact_digest,
            expected_activation_scope_digest=scope,
            expected_log_head_digest=head,
        )
        if published != canonical_artifact:
            _refuse("published_content_mismatch", "published artifact mismatched")
        return AttestationLogArtifactRecord(
            artifact_digest=artifact_digest,
            log_head_digest=head,
            activation_scope_digest=scope,
            path=target,
            size=len(canonical_artifact),
        )

    def read(
        self,
        artifact_digest: str,
        *,
        expected_activation_scope_digest: str,
        expected_log_head_digest: str,
    ) -> Optional[bytes]:
        """Return verified immutable bytes or ``None`` when absent."""

        digest = _digest(artifact_digest, "artifact_digest")
        scope = _digest(
            expected_activation_scope_digest,
            "expected_activation_scope_digest",
        )
        head = _digest(expected_log_head_digest, "expected_log_head_digest")
        target = self._artifact_path(digest)
        _directory_metadata(self._root, "root")
        try:
            _directory_metadata(target.parent.parent, "first_shard")
        except AttestationLogArtifactStoreError as exc:
            if exc.reason == "first_shard_missing":
                return None
            raise
        try:
            _directory_metadata(target.parent, "second_shard")
        except AttestationLogArtifactStoreError as exc:
            if exc.reason == "second_shard_missing":
                return None
            raise
        return self._read_existing(
            target,
            expected_artifact_digest=digest,
            expected_activation_scope_digest=scope,
            expected_log_head_digest=head,
        )


__all__ = [
    "MAX_ATTESTATION_LOG_ARTIFACT_BYTES",
    "AttestationLogArtifactRecord",
    "AttestationLogArtifactStore",
    "AttestationLogArtifactStoreError",
]
