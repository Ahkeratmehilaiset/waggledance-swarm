# SPDX-License-Identifier: BUSL-1.1
"""Local append-only storage for verified activation snapshot publications.

The store accepts only immutable canonical bytes produced by the activation
snapshot publication contract.  It is a content-addressed MAGMA artifact
sink, not a current-pointer database: it authenticates no caller, grants no
authority, and never changes runtime routing.
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

from waggledance.core.capabilities.activation_snapshot import (
    ACTIVATION_SNAPSHOT_BUNDLE_CORE_KEYS,
    ACTIVATION_SNAPSHOT_BUNDLE_DIGEST_DOMAIN,
    ACTIVATION_SNAPSHOT_BUNDLE_KEYS,
    ActivationSnapshotContractError,
    canonicalize_activation_snapshot_publication,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest

MAX_ACTIVATION_SNAPSHOT_ARTIFACT_BYTES = 64 * 1024 * 1024

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_NON_AUTHORITY_FLAGS = {
    "provider_authentication_verified": False,
    "runtime_authority_granted": False,
    "routing_influence_applied": False,
    "execution_permission_granted": False,
}


class ActivationSnapshotArtifactStoreError(RuntimeError):
    """The artifact store refused an unsafe or inconsistent operation."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _refuse(reason: str, message: str) -> None:
    raise ActivationSnapshotArtifactStoreError(reason, message)


@dataclass(frozen=True)
class ActivationSnapshotArtifactRecord:
    """Immutable description of one content-addressed publication."""

    bundle_digest: str
    path: Path
    size: int


def _digest(value: object) -> str:
    if type(value) is not str or not _DIGEST.fullmatch(value):
        _refuse(
            "bundle_digest",
            "bundle_digest must be lowercase sha256:<64 hex>",
        )
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
        _refuse(f"{label}_symlink", f"{label} may not be a symlink/reparse point")
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
    """Persist a published directory entry where the platform permits it."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        # Windows does not generally allow fsync on directory handles.  The
        # artifact file itself has already crossed a mandatory fsync boundary.
        pass
    finally:
        os.close(descriptor)


def _decode_canonical_json(data: bytes, *, label: str) -> object:
    try:
        decoded = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ActivationSnapshotArtifactStoreError(
            f"{label}_json", f"{label} is not strict JSON"
        ) from exc
    try:
        canonical = canonical_json_bytes(decoded)
    except (TypeError, ValueError) as exc:
        raise ActivationSnapshotArtifactStoreError(
            f"{label}_json", f"{label} is outside canonical JSON"
        ) from exc
    if canonical != data:
        _refuse(f"{label}_noncanonical", f"{label} is not canonical JSON bytes")
    return decoded


class ActivationSnapshotArtifactStore:
    """Explicit-root, content-addressed activation publication store."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        if type(root) is str and not root:
            _refuse("root", "artifact root may not be empty")
        try:
            self._root = Path(os.path.abspath(os.fspath(root)))
        except (TypeError, ValueError) as exc:
            raise ActivationSnapshotArtifactStoreError(
                "root", "artifact root must be an explicit filesystem path"
            ) from exc
        try:
            os.mkdir(self._root)
        except FileExistsError:
            pass
        except FileNotFoundError:
            # The caller supplied the root, but ordinary nested explicit roots
            # remain useful.  Revalidate the completed root before using it.
            self._root.mkdir(parents=True, exist_ok=True)
        _directory_metadata(self._root, "root")

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def _publication_kwargs(
        *,
        cell_identity: object,
        expected_deployment_scope_digest: str,
        expected_profile_head_digest: str,
        expected_policy_head_digest: str,
        expected_resource_head_digest: str,
        expected_domain_head_digest: str,
        expected_environment_head_digest: str,
        expected_charter_ceiling_digest: str,
        expected_expressed_ceiling_digest: str,
    ) -> dict[str, object]:
        return {
            "cell_identity": cell_identity,
            "expected_deployment_scope_digest": (
                expected_deployment_scope_digest
            ),
            "expected_profile_head_digest": expected_profile_head_digest,
            "expected_policy_head_digest": expected_policy_head_digest,
            "expected_resource_head_digest": expected_resource_head_digest,
            "expected_domain_head_digest": expected_domain_head_digest,
            "expected_environment_head_digest": (
                expected_environment_head_digest
            ),
            "expected_charter_ceiling_digest": (
                expected_charter_ceiling_digest
            ),
            "expected_expressed_ceiling_digest": (
                expected_expressed_ceiling_digest
            ),
        }

    def _artifact_path(self, bundle_digest: str) -> Path:
        digest = _digest(bundle_digest)
        hexadecimal = digest.removeprefix("sha256:")
        return (
            self._root
            / hexadecimal[:2]
            / hexadecimal[2:4]
            / f"{hexadecimal}.json"
        )

    def _ensure_shards(self, target: Path) -> None:
        _directory_metadata(self._root, "root")
        first = target.parent.parent
        second = target.parent
        _ensure_directory(first, "first_shard")
        _ensure_directory(second, "second_shard")

    @staticmethod
    def _target_metadata(path: Path) -> Optional[os.stat_result]:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
            _refuse("target_symlink", "artifact target may not be a symlink")
        if not stat.S_ISREG(metadata.st_mode):
            _refuse("target_not_regular", "artifact target must be a regular file")
        if not 0 < metadata.st_size <= MAX_ACTIVATION_SNAPSHOT_ARTIFACT_BYTES:
            _refuse("stored_size", "stored artifact violates the 64 MiB bound")
        return metadata

    @staticmethod
    def _read_regular(path: Path, metadata: os.stat_result) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            _refuse("target_raced", "artifact disappeared during read")
        except OSError as exc:
            raise ActivationSnapshotArtifactStoreError(
                "target_open", "artifact target could not be opened safely"
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
    def _validate_stored_content(data: bytes, bundle_digest: str) -> None:
        decoded = _decode_canonical_json(data, label="stored_artifact")
        if type(decoded) is not dict or set(decoded) != (
            ACTIVATION_SNAPSHOT_BUNDLE_KEYS
        ):
            _refuse("stored_keyset", "stored artifact bundle keyset is invalid")
        claimed = decoded.get("bundle_digest")
        if claimed != bundle_digest or type(claimed) is not str:
            _refuse("stored_name_binding", "stored artifact name/digest mismatch")
        for field, expected in _NON_AUTHORITY_FLAGS.items():
            if decoded.get(field) is not expected:
                _refuse("stored_authority", "stored artifact carries authority")
        core = {
            key: decoded[key] for key in ACTIVATION_SNAPSHOT_BUNDLE_CORE_KEYS
        }
        expected_digest = sha256_digest(
            {
                "domain": ACTIVATION_SNAPSHOT_BUNDLE_DIGEST_DOMAIN,
                "bundle": core,
            }
        )
        if expected_digest != bundle_digest:
            _refuse("stored_digest", "stored artifact content digest mismatch")

    def _read_existing(self, path: Path, bundle_digest: str) -> Optional[bytes]:
        metadata = self._target_metadata(path)
        if metadata is None:
            return None
        data = self._read_regular(path, metadata)
        self._validate_stored_content(data, bundle_digest)
        return data

    def append(
        self,
        canonical_bundle: bytes,
        *,
        cell_identity: object,
        expected_deployment_scope_digest: str,
        expected_profile_head_digest: str,
        expected_policy_head_digest: str,
        expected_resource_head_digest: str,
        expected_domain_head_digest: str,
        expected_environment_head_digest: str,
        expected_charter_ceiling_digest: str,
        expected_expressed_ceiling_digest: str,
    ) -> ActivationSnapshotArtifactRecord:
        """Verify and atomically publish one immutable canonical bundle."""

        if type(canonical_bundle) is not bytes:
            _refuse("canonical_bundle_type", "canonical_bundle must be exact bytes")
        if not canonical_bundle or len(canonical_bundle) > (
            MAX_ACTIVATION_SNAPSHOT_ARTIFACT_BYTES
        ):
            _refuse("canonical_bundle_size", "bundle violates the 64 MiB bound")

        decoded = _decode_canonical_json(canonical_bundle, label="canonical_bundle")
        publication_kwargs = self._publication_kwargs(
            cell_identity=cell_identity,
            expected_deployment_scope_digest=expected_deployment_scope_digest,
            expected_profile_head_digest=expected_profile_head_digest,
            expected_policy_head_digest=expected_policy_head_digest,
            expected_resource_head_digest=expected_resource_head_digest,
            expected_domain_head_digest=expected_domain_head_digest,
            expected_environment_head_digest=expected_environment_head_digest,
            expected_charter_ceiling_digest=expected_charter_ceiling_digest,
            expected_expressed_ceiling_digest=expected_expressed_ceiling_digest,
        )
        try:
            verified = canonicalize_activation_snapshot_publication(
                decoded, **publication_kwargs
            )
        except ActivationSnapshotContractError as exc:
            raise ActivationSnapshotArtifactStoreError(
                f"publication:{exc.reason}",
                f"activation snapshot publication refused: {exc.reason}",
            ) from exc
        if verified != canonical_bundle:
            _refuse(
                "canonical_bundle_noncanonical",
                "bundle bytes differ from the verified canonical publication",
            )
        verified_mapping = json.loads(verified)
        bundle_digest = _digest(verified_mapping.get("bundle_digest"))
        target = self._artifact_path(bundle_digest)
        self._ensure_shards(target)

        existing = self._read_existing(target, bundle_digest)
        if existing is not None:
            if existing != canonical_bundle:
                _refuse(
                    "existing_content_mismatch",
                    "bundle digest already names different artifact bytes",
                )
            return ActivationSnapshotArtifactRecord(
                bundle_digest=bundle_digest,
                path=target,
                size=len(canonical_bundle),
            )

        descriptor = -1
        temporary_name: Optional[str] = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".activation-snapshot-",
                suffix=".pending",
                dir=target.parent,
            )
            view = memoryview(canonical_bundle)
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
                concurrent = self._read_existing(target, bundle_digest)
                if concurrent != canonical_bundle:
                    _refuse(
                        "existing_content_mismatch",
                        "concurrent artifact differs for the same digest",
                    )
            except OSError as exc:
                raise ActivationSnapshotArtifactStoreError(
                    "publish", "artifact could not be published without overwrite"
                ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

        # Re-open by its final name to catch any impossible publication race.
        published = self._read_existing(target, bundle_digest)
        if published != canonical_bundle:
            _refuse("published_content_mismatch", "published artifact mismatched")
        return ActivationSnapshotArtifactRecord(
            bundle_digest=bundle_digest,
            path=target,
            size=len(canonical_bundle),
        )

    def read(self, bundle_digest: str) -> Optional[bytes]:
        """Return verified immutable bytes, or ``None`` when absent."""

        digest = _digest(bundle_digest)
        target = self._artifact_path(digest)
        _directory_metadata(self._root, "root")
        first = target.parent.parent
        second = target.parent
        try:
            _directory_metadata(first, "first_shard")
        except ActivationSnapshotArtifactStoreError as exc:
            if exc.reason == "first_shard_missing":
                return None
            raise
        try:
            _directory_metadata(second, "second_shard")
        except ActivationSnapshotArtifactStoreError as exc:
            if exc.reason == "second_shard_missing":
                return None
            raise
        return self._read_existing(target, digest)
