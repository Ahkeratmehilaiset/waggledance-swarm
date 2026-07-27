# SPDX-License-Identifier: BUSL-1.1
"""Receiver-side JSON verifier for one exact chat-served production window."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.canonical import (  # noqa: E402
    canonical_json_bytes,
    sha256_digest,
)
from waggledance.core.magma.chat_served_claim_window_evidence import (  # noqa: E402
    PRODUCTION_WINDOW_VERIFICATION_SCHEMA,
    ProductionWindowVerification,
    derive_production_window_registry_binding_digest,
    verify_production_window,
)
from waggledance.core.magma.chat_served_window_registry import (  # noqa: E402
    ChatServedWindowRegistry,
    RegistryVerificationApproval,
    WindowRegistryBinding,
    WindowRegistryCorruptionError,
    WindowRegistryError,
    WindowRegistryLockError,
    WindowRegistryPathError,
    WindowRegistryReplayError,
    WindowRegistryStateError,
    WindowRegistryVerificationError,
    derive_window_marker_path_digest,
)

INPUT_SCHEMA = "magma.chat_served_production_window_verifier_input.v1"
_INPUT_KEYS = frozenset({
    "schema_version",
    "start_boundary",
    "final_boundary",
    "clean_shutdown_marker",
    "ledger_entries",
    "enabled_samples",
    "pending_failures",
    "receipt_index",
    "served_point_observations",
})
_MANIFEST_KEYS = frozenset({"chain_id", "entries"})
_MANIFEST_ENTRY_KEYS = frozenset({"receipt", "payload", "evaluation_result"})
_CHAT_SERVED_CHAIN_ID = "magma:chat_service:served:v0"
MAX_JSON_INPUT_BYTES = 64 * 1024 * 1024
MAX_CLEAN_MARKER_BYTES = 64 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a closed chat-served production-window evidence envelope. "
            "The command finalizes an existing reservation in an explicit "
            "receiver-owned replay registry. It remains measurement-only and "
            "never grants runtime authority."
        ),
    )
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--receipt-root", required=True, type=Path)
    parser.add_argument(
        "--clean-shutdown-marker",
        required=True,
        type=Path,
        help=(
            "Absolute receiver-pinned path to the final durable clean marker."
        ),
    )
    parser.add_argument(
        "--verified-window-registry",
        required=True,
        type=Path,
        help=(
            "Absolute path to the receiver-owned append-only replay registry. "
            "A matching pre-marker reservation must already exist."
        ),
    )
    parser.add_argument("--expected-window-id", required=True)
    parser.add_argument("--expected-source-head", required=True)
    return parser


def _invalid_result(
    reason: str = "cli_input_invalid",
) -> ProductionWindowVerification:
    return ProductionWindowVerification(
        ok=False,
        phase="pre_marker_rejected",
        reason=reason,
        marker_verified=False,
        ledger_entries=0,
        enabled_samples=0,
        pending_failures=0,
        receipt_index_entries=0,
        served_point_observations=0,
        receipt_terminals=0,
    )


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON value")


def _read_json(path: Path) -> Any:
    if path.stat().st_size > MAX_JSON_INPUT_BYTES:
        raise ValueError("JSON input exceeds bound")
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_object_without_duplicates,
        parse_constant=_reject_constant,
    )


def _path_is_link(path: Path) -> bool:
    details = os.lstat(path)
    attributes = int(getattr(details, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _guard_no_reparse_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if _path_is_link(current):
                raise ValueError("reparse path component is not allowed")
        except FileNotFoundError:
            break


def _open_flags(base: int) -> int:
    return (
        base
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _regular_single_link_identity(
    details: os.stat_result,
) -> tuple[int, int]:
    if not stat.S_ISREG(details.st_mode) or int(details.st_nlink) != 1:
        raise ValueError("clean marker must be a single-link regular file")
    return int(details.st_dev), int(details.st_ino)


def _stable_stat_signature(
    details: os.stat_result,
) -> tuple[int, int, int, int]:
    identity = _regular_single_link_identity(details)
    return (
        *identity,
        int(details.st_size),
        int(details.st_mtime_ns),
    )


def _read_clean_shutdown_marker(path: Path) -> Mapping[str, Any]:
    if not path.is_absolute():
        raise ValueError("clean marker path must be absolute")
    _guard_no_reparse_components(path)
    if _path_is_link(path):
        raise ValueError("linked clean marker is not allowed")
    fd = os.open(path, _open_flags(os.O_RDONLY))
    try:
        fd_before = os.fstat(fd)
        path_before = os.lstat(path)
        fd_signature = _stable_stat_signature(fd_before)
        if _stable_stat_signature(path_before) != fd_signature:
            raise ValueError("clean marker identity mismatch")
        if (
            fd_before.st_size <= 0
            or fd_before.st_size > MAX_CLEAN_MARKER_BYTES
        ):
            raise ValueError("clean marker size is invalid")
        raw = bytearray()
        while len(raw) <= MAX_CLEAN_MARKER_BYTES:
            chunk = os.read(
                fd,
                min(
                    64 * 1024,
                    MAX_CLEAN_MARKER_BYTES + 1 - len(raw),
                ),
            )
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > MAX_CLEAN_MARKER_BYTES:
            raise ValueError("clean marker exceeds bound")
        fd_after = os.fstat(fd)
        _guard_no_reparse_components(path)
        if _path_is_link(path):
            raise ValueError("linked clean marker is not allowed")
        path_after = os.lstat(path)
        if (
            _stable_stat_signature(fd_after) != fd_signature
            or _stable_stat_signature(path_after) != fd_signature
            or len(raw) != fd_before.st_size
        ):
            raise ValueError("clean marker changed during read")
    finally:
        os.close(fd)
    value = json.loads(
        bytes(raw).decode("utf-8", errors="strict"),
        object_pairs_hook=_object_without_duplicates,
        parse_constant=_reject_constant,
    )
    if type(value) is not dict:
        raise ValueError("clean marker must be a JSON object")
    if bytes(raw) != canonical_json_bytes(value) + b"\n":
        raise ValueError("clean marker must be canonical JSON plus LF")
    return value


def _safe_relative(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
    ):
        return False
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


class _ReceiptResolver:
    """Contained, no-follow receipt loader plus canonical-verifier callback."""

    def __init__(self, receipt_root: Path) -> None:
        self._root_lexical = Path(os.path.abspath(os.fspath(receipt_root)))
        if (
            not self._root_lexical.is_dir()
            or _path_is_link(self._root_lexical)
        ):
            raise ValueError("invalid receipt root")
        self._root_resolved = self._root_lexical.resolve(strict=True)
        self._bundle_paths: dict[str, Path] = {}

    def _contained_file(self, relative: object, *, parent: Path | None = None) -> Path:
        if not _safe_relative(relative):
            raise ValueError("unsafe relative artifact reference")
        base = self._root_lexical if parent is None else parent
        candidate = base.joinpath(*str(relative).split("/"))
        lexical = Path(os.path.abspath(os.fspath(candidate)))
        try:
            lexical.relative_to(self._root_lexical)
        except ValueError as exc:
            raise ValueError("artifact escapes receipt root") from exc
        cursor = self._root_lexical
        for part in lexical.relative_to(self._root_lexical).parts:
            cursor = cursor / part
            if _path_is_link(cursor):
                raise ValueError("linked receipt artifact")
        resolved = lexical.resolve(strict=True)
        try:
            resolved.relative_to(self._root_resolved)
        except ValueError as exc:
            raise ValueError("resolved artifact escapes receipt root") from exc
        if not resolved.is_file():
            raise ValueError("receipt artifact is not a file")
        return resolved

    def resolve(self, manifest_ref: str) -> Mapping[str, Any] | None:
        manifest_path = self._contained_file(manifest_ref)
        manifest = _read_json(manifest_path)
        if (
            type(manifest) is not dict
            or set(manifest) != _MANIFEST_KEYS
            or manifest.get("chain_id") != _CHAT_SERVED_CHAIN_ID
        ):
            return None
        entries = manifest.get("entries")
        if not isinstance(entries, list) or len(entries) != 1:
            return None
        entry = entries[0]
        if type(entry) is not dict or set(entry) != _MANIFEST_ENTRY_KEYS:
            return None
        receipt_path = self._contained_file(
            entry["receipt"],
            parent=manifest_path.parent,
        )
        payload_path = self._contained_file(
            entry["payload"],
            parent=manifest_path.parent,
        )
        # Resolve evaluation too: a missing/escaping sidecar must fail before the
        # canonical verifier is considered.
        self._contained_file(
            entry["evaluation_result"],
            parent=manifest_path.parent,
        )
        bundle = {
            "receipt": _read_json(receipt_path),
            "payload": _read_json(payload_path),
        }
        self._bundle_paths[sha256_digest(bundle)] = manifest_path
        return bundle

    def verify(self, bundle: Mapping[str, Any]) -> bool:
        try:
            manifest_path = self._bundle_paths.get(sha256_digest(bundle))
            if manifest_path is None:
                return False
            report = verify_manifest(manifest_path)
            return bool(
                isinstance(report, Mapping)
                and report.get("ok") is True
                and type(report.get("receipt_count")) is int
                and report.get("receipt_count") == 1
                and report.get("errors") == []
            )
        except Exception:  # noqa: BLE001 - canonical verification fails closed
            return False


def _verify(
    envelope: object,
    receipt_root: Path,
    *,
    expected_window_id: str,
    expected_source_head: str,
    previously_verified_window_ids: Sequence[str],
) -> ProductionWindowVerification:
    if (
        type(envelope) is not dict
        or set(envelope) != _INPUT_KEYS
        or envelope.get("schema_version") != INPUT_SCHEMA
    ):
        return _invalid_result()
    try:
        resolver = _ReceiptResolver(receipt_root)
        return verify_production_window(
            expected_window_id=expected_window_id,
            expected_source_head=expected_source_head,
            start_boundary=envelope["start_boundary"],
            final_boundary=envelope["final_boundary"],
            clean_shutdown_marker=envelope["clean_shutdown_marker"],
            ledger_entries=envelope["ledger_entries"],
            enabled_samples=envelope["enabled_samples"],
            pending_failures=envelope["pending_failures"],
            receipt_index=envelope["receipt_index"],
            served_point_observations=envelope[
                "served_point_observations"
            ],
            resolve_receipt_bundle=resolver.resolve,
            verify_receipt_bundle=resolver.verify,
            content_address_receipt=sha256_digest,
            previously_verified_window_ids=previously_verified_window_ids,
        )
    except Exception:  # noqa: BLE001 - CLI reduces all errors to a fixed verdict
        return _invalid_result()


def _registry_binding(
    envelope: object,
    *,
    expected_window_id: str,
    expected_source_head: str,
    clean_shutdown_marker_path: Path,
) -> WindowRegistryBinding:
    if (
        type(envelope) is not dict
        or set(envelope) != _INPUT_KEYS
        or envelope.get("schema_version") != INPUT_SCHEMA
    ):
        raise ValueError("invalid evidence envelope")
    start_boundary = envelope["start_boundary"]
    final_boundary = envelope["final_boundary"]
    marker = envelope["clean_shutdown_marker"]
    if not all(
        isinstance(value, Mapping)
        for value in (start_boundary, final_boundary, marker)
    ):
        raise ValueError("invalid lifecycle evidence")
    start_boundary_digest = start_boundary.get("boundary_hash")
    final_boundary_digest = final_boundary.get("boundary_hash")
    marker_digest = marker.get("marker_hash")
    final_ledger_head = final_boundary.get("final_ledger_head")
    if not all(
        isinstance(value, str)
        for value in (
            start_boundary_digest,
            final_boundary_digest,
            marker_digest,
            final_ledger_head,
        )
    ):
        raise ValueError("invalid lifecycle digest")
    binding_digest = derive_production_window_registry_binding_digest(
        expected_window_id=expected_window_id,
        expected_source_head=expected_source_head,
        start_boundary=start_boundary,
        final_boundary=final_boundary,
        clean_shutdown_marker=marker,
        ledger_entries=envelope["ledger_entries"],
        enabled_samples=envelope["enabled_samples"],
        pending_failures=envelope["pending_failures"],
        receipt_index=envelope["receipt_index"],
        served_point_observations=envelope["served_point_observations"],
    )
    return WindowRegistryBinding(
        window_id=expected_window_id,
        source_head=expected_source_head,
        binding_digest=binding_digest,
        start_boundary_digest=start_boundary_digest,
        final_boundary_digest=final_boundary_digest,
        marker_digest=marker_digest,
        marker_path_digest=derive_window_marker_path_digest(
            clean_shutdown_marker_path
        ),
        final_ledger_head=final_ledger_head,
    )


def _normalized_path(path: Path) -> Path:
    return Path(os.path.normcase(os.path.abspath(os.fspath(path))))


def _path_variants(path: Path) -> tuple[Path, Path]:
    return (
        _normalized_path(path),
        _normalized_path(path.resolve(strict=False)),
    )


def _same_or_within(path: Path, root: Path) -> bool:
    path_value = os.path.normcase(os.path.abspath(os.fspath(path)))
    root_value = os.path.normcase(os.path.abspath(os.fspath(root)))
    try:
        return os.path.commonpath((path_value, root_value)) == root_value
    except ValueError:
        return False


def _validate_registry_separation(
    registry: ChatServedWindowRegistry,
    *,
    evidence_path: Path,
    clean_shutdown_marker_path: Path,
    receipt_root: Path,
) -> None:
    registry_paths = (registry.path, registry.lock_path)
    producer_paths = (
        *_path_variants(evidence_path),
        *_path_variants(clean_shutdown_marker_path),
    )
    producer_roots = (
        *_path_variants(evidence_path.parent),
        *_path_variants(clean_shutdown_marker_path.parent),
    )
    receipt_roots = _path_variants(receipt_root)
    registry_roots = _path_variants(registry.path.parent)
    for registry_path in registry_paths:
        candidates = _path_variants(registry_path)
        if any(
            candidate == producer_path
            for candidate in candidates
            for producer_path in producer_paths
        ):
            raise WindowRegistryPathError("registry_evidence_path_overlap")
        if any(
            _same_or_within(candidate, receipt_root_value)
            for candidate in candidates
            for receipt_root_value in receipt_roots
        ):
            raise WindowRegistryPathError("registry_receipt_root_overlap")
        if any(
            _same_or_within(candidate, producer_root)
            for candidate in candidates
            for producer_root in producer_roots
        ):
            raise WindowRegistryPathError(
                "registry_evidence_namespace_overlap"
            )
    if any(
        _same_or_within(producer_path, registry_root)
        for producer_path in (*producer_paths, *receipt_roots)
        for registry_root in registry_roots
    ):
        raise WindowRegistryPathError("producer_registry_namespace_overlap")


def _registry_failure_result(
    error: WindowRegistryError,
) -> ProductionWindowVerification:
    if isinstance(error, WindowRegistryReplayError):
        return _invalid_result("window_id_reused")
    if isinstance(error, WindowRegistryStateError):
        if str(error) == "reservation_missing":
            return _invalid_result("window_registry_reservation_missing")
        return _invalid_result("window_registry_binding_mismatch")
    if isinstance(error, WindowRegistryPathError):
        return _invalid_result("window_registry_path_invalid")
    if isinstance(error, WindowRegistryCorruptionError):
        return _invalid_result("window_registry_invalid")
    if isinstance(error, WindowRegistryLockError):
        return _invalid_result("window_registry_lock_failed")
    return _invalid_result("window_registry_rejected")


def _safe_failed_verification_result(
    result: object,
) -> bool:
    if (
        type(result) is not ProductionWindowVerification
        or result.ok is not False
        or result.phase not in {"pre_marker_rejected", "final_rejected"}
        or not isinstance(result.reason, str)
        or re.fullmatch(r"[a-z0-9_:]+", result.reason) is None
        or result.marker_verified is not False
        or result.measurement_only is not True
        or type(result.claim_safe_count) is not int
        or result.claim_safe_count != 0
        or result.schema_version != PRODUCTION_WINDOW_VERIFICATION_SCHEMA
    ):
        return False
    for field in (
        "ledger_entries",
        "enabled_samples",
        "pending_failures",
        "receipt_index_entries",
        "served_point_observations",
        "receipt_terminals",
    ):
        value = getattr(result, field)
        if type(value) is not int or value < 0:
            return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    callback_result: ProductionWindowVerification | None = None
    try:
        if not args.clean_shutdown_marker.is_absolute():
            raise WindowRegistryPathError(
                "clean_marker_path_must_be_absolute"
            )
        registry = ChatServedWindowRegistry(args.verified_window_registry)
        _validate_registry_separation(
            registry,
            evidence_path=args.evidence,
            clean_shutdown_marker_path=args.clean_shutdown_marker,
            receipt_root=args.receipt_root,
        )
        envelope = _read_json(args.evidence)
        binding = _registry_binding(
            envelope,
            expected_window_id=args.expected_window_id,
            expected_source_head=args.expected_source_head,
            clean_shutdown_marker_path=args.clean_shutdown_marker,
        )

        def verify_locked(
            previously_verified_window_ids: tuple[str, ...],
        ) -> RegistryVerificationApproval:
            nonlocal callback_result
            _validate_registry_separation(
                registry,
                evidence_path=args.evidence,
                clean_shutdown_marker_path=args.clean_shutdown_marker,
                receipt_root=args.receipt_root,
            )
            locked_envelope = _read_json(args.evidence)
            locked_binding = _registry_binding(
                locked_envelope,
                expected_window_id=args.expected_window_id,
                expected_source_head=args.expected_source_head,
                clean_shutdown_marker_path=args.clean_shutdown_marker,
            )
            if locked_binding != binding:
                raise ValueError("evidence changed before locked verification")
            try:
                durable_marker = _read_clean_shutdown_marker(
                    args.clean_shutdown_marker
                )
            except Exception:  # noqa: BLE001 - marker failures stay path-free
                callback_result = _invalid_result(
                    "clean_shutdown_marker_invalid"
                )
                return RegistryVerificationApproval(
                    binding=locked_binding,
                    verdict=callback_result,
                )
            if (
                durable_marker
                != locked_envelope["clean_shutdown_marker"]
                or durable_marker.get("marker_hash")
                != binding.marker_digest
            ):
                callback_result = _invalid_result(
                    "clean_shutdown_marker_mismatch"
                )
                return RegistryVerificationApproval(
                    binding=locked_binding,
                    verdict=callback_result,
                )
            locked_envelope = {
                **locked_envelope,
                "clean_shutdown_marker": durable_marker,
            }
            callback_result = _verify(
                locked_envelope,
                args.receipt_root,
                expected_window_id=args.expected_window_id,
                expected_source_head=args.expected_source_head,
                previously_verified_window_ids=(
                    previously_verified_window_ids
                ),
            )
            try:
                durable_marker_after = _read_clean_shutdown_marker(
                    args.clean_shutdown_marker
                )
            except Exception:  # noqa: BLE001 - marker failures stay path-free
                callback_result = _invalid_result(
                    "clean_shutdown_marker_changed_during_verification"
                )
                return RegistryVerificationApproval(
                    binding=locked_binding,
                    verdict=callback_result,
                )
            if (
                durable_marker_after != durable_marker
                or durable_marker_after
                != locked_envelope["clean_shutdown_marker"]
                or durable_marker_after.get("marker_hash")
                != binding.marker_digest
            ):
                callback_result = _invalid_result(
                    "clean_shutdown_marker_changed_during_verification"
                )
            return RegistryVerificationApproval(
                binding=locked_binding,
                verdict=callback_result,
            )

        registry.finalize_after_verification(binding, verify_locked)
        if callback_result is None:
            raise WindowRegistryVerificationError(
                "verification_callback_result_missing"
            )
        result = callback_result
    except WindowRegistryVerificationError:
        if _safe_failed_verification_result(callback_result):
            assert callback_result is not None
            result = callback_result
        else:
            result = _invalid_result(
                "window_registry_verification_failed"
            )
    except WindowRegistryError as exc:
        result = _registry_failure_result(exc)
    except Exception:  # noqa: BLE001 - no paths or exception text enter JSON
        result = _invalid_result()
    payload = result._asdict()
    # Defensive contract assertions keep the stable output non-authorizing.
    assert payload["schema_version"] == PRODUCTION_WINDOW_VERIFICATION_SCHEMA
    assert payload["measurement_only"] is True
    assert payload["claim_safe_count"] == 0
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
