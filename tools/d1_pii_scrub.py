#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Produce fail-closed, read-only evidence for the D1 preparation phase.

This module deliberately has no history-rewrite, replacement-file, push,
signature, release, or production-clean capability. It observes an
operator-authoritative inventory against an independently prepared mirror and
always reports a blocked preparation state. Later phases remain separate,
explicitly authorized procedures.

PREP redacts only the two settings paths; the other 203 matched current-tree
paths remain unresolved, unclassified, and unchanged, so status is always
``prepared_blocked``.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
INVENTORY_KIND = "waggledance.d1_sensitive_inventory"
REFS_KIND = "waggledance.d1_expected_refs"
REPORT_KIND = "waggledance.d1_pii_scrub_preparation"
REPORT_STATUS = "prepared_blocked"
MAX_INVENTORY_BYTES = 64 * 1024
MAX_REF_MANIFEST_BYTES = 256 * 1024
MAX_SETTINGS_BYTES = 256 * 1024
MAX_VARIANTS_PER_FIELD = 32
MAX_VARIANT_BYTES = 1024
READ_CHUNK_BYTES = 1024 * 1024

PII_FIELDS: dict[str, str] = {
    "business_name": "REDACTED_BUSINESS",
    "owner": "REDACTED_OWNER",
    "y_tunnus": "REDACTED_BUSINESS_ID",
}
SETTINGS_PATHS = (
    "configs/settings.yaml",
    "backup/2026-04-23/settings.yaml.pre-hybrid",
)
LEGAL_KEEP_PATHS = frozenset({"LICENSE", "LICENSE-BUSL.txt", "NOTICE"})
ALLOWED_BLOB_MODES = frozenset({"100644", "100755"})
ALLOWED_OBJECT_TYPES = frozenset({"blob", "commit", "tag", "tree"})
FALSE_AUTHORITY = {
    "scope": False,
    "legal": False,
    "release": False,
    "production": False,
    "execution": False,
}
_HEX_OID = re.compile(r"^[0-9a-f]{40}$")
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REF_FORBIDDEN = re.compile(r"[\x00-\x20~^:?*\\[]")
class InventoryError(RuntimeError):
    """A fail-closed inventory or inventory-location failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class InspectionError(RuntimeError):
    """A fail-closed mirror, settings, encoding, or inspection failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, repr=False)
class SensitiveInventory:
    """Validated sensitive bytes; values must never enter reports or argv."""

    fields: Mapping[str, tuple[bytes, ...]]

    @property
    def variant_count(self) -> int:
        return sum(len(values) for values in self.fields.values())

    @property
    def needles(self) -> tuple[bytes, ...]:
        return tuple(value for values in self.fields.values() for value in values)

    def __repr__(self) -> str:
        return (
            "SensitiveInventory("
            f"field_count={len(self.fields)}, variant_count={self.variant_count})"
        )


@dataclass(frozen=True)
class ExpectedRefs:
    refs: Mapping[str, str]


def _reject_nonfinite(value: str) -> None:
    raise ValueError("nonfinite_number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _strict_json(data: bytes, error: Callable[[str], RuntimeError]) -> Any:
    if data.startswith(b"\xef\xbb\xbf"):
        raise error("utf8_bom_forbidden")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise error("unverifiable_encoding") from exc
    decoder = json.JSONDecoder(
        object_pairs_hook=_unique_object,
        parse_constant=_reject_nonfinite,
    )
    try:
        value, end = decoder.raw_decode(text)
    except (ValueError, MemoryError, RecursionError) as exc:
        raise error("invalid_json") from exc
    if end != len(text):
        raise error("trailing_json_content")
    return value


def _stat_is_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _is_within(path: Path, root: Path) -> bool:
    path_key = _path_key(path)
    root_key = _path_key(root)
    try:
        return os.path.commonpath((path_key, root_key)) == root_key
    except ValueError:
        return False


def _has_windows_ads(path: Path) -> bool:
    if os.name != "nt":
        return False
    _drive, tail = os.path.splitdrive(os.path.abspath(os.fspath(path)))
    return ":" in tail


def _windows_handle_stream_inventory(descriptor: int) -> tuple[str, ...]:
    """Return the canonical stream inventory for a locked Windows handle.

    A colon-free pathname only proves that the caller did not request an ADS.
    The underlying file can still carry named streams, so enumerate stream
    metadata through the already locked handle. An unsupported or malformed
    response is an inspection failure, not evidence that no ADS exists.
    """

    if os.name != "nt":
        return ()

    import ctypes
    from ctypes import wintypes
    import msvcrt

    file_stream_info = 7
    error_insufficient_buffer = 122
    error_more_data = 234
    stream_header_bytes = 24
    max_stream_info_bytes = 1024 * 1024

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))

    buffer_size = 4096
    while buffer_size <= max_stream_info_bytes:
        buffer = ctypes.create_string_buffer(buffer_size)
        ctypes.set_last_error(0)
        if kernel32.GetFileInformationByHandleEx(
            handle,
            file_stream_info,
            buffer,
            buffer_size,
        ):
            break
        code = ctypes.get_last_error()
        if code not in (error_insufficient_buffer, error_more_data):
            raise OSError(code, "stream_inventory_failed")
        buffer_size *= 2
    else:
        raise OSError(error_insufficient_buffer, "stream_inventory_too_large")

    entries: list[str] = []
    offset = 0
    while True:
        if offset > buffer_size - stream_header_bytes:
            raise OSError(13, "stream_inventory_malformed")
        address = ctypes.addressof(buffer) + offset
        next_offset = ctypes.c_uint32.from_address(address).value
        name_bytes = ctypes.c_uint32.from_address(address + 4).value
        if (
            name_bytes == 0
            or name_bytes % ctypes.sizeof(wintypes.WCHAR) != 0
            or name_bytes > buffer_size - offset - stream_header_bytes
        ):
            raise OSError(13, "stream_inventory_malformed")
        name = ctypes.wstring_at(
            address + stream_header_bytes,
            name_bytes // ctypes.sizeof(wintypes.WCHAR),
        )
        if "\x00" in name:
            raise OSError(13, "stream_inventory_malformed")
        entries.append(name)
        if next_offset == 0:
            break
        if (
            next_offset % 8 != 0
            or next_offset < stream_header_bytes + name_bytes
            or next_offset > buffer_size - offset
        ):
            raise OSError(13, "stream_inventory_malformed")
        offset += next_offset

    return tuple(name.casefold() for name in entries)


def _windows_handle_change_token(descriptor: int) -> tuple[int, int] | None:
    """Capture Windows carrier change-time and attributes from a locked handle."""

    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes
    import msvcrt

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
    info = FileBasicInfo()
    ctypes.set_last_error(0)
    if not kernel32.GetFileInformationByHandleEx(
        handle,
        0,  # FileBasicInfo
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        code = ctypes.get_last_error()
        raise OSError(code, "carrier_change_token_failed")
    return int(info.ChangeTime), int(info.FileAttributes)


def _plain_parent_snapshot(
    path: Path,
    error: Callable[[str], RuntimeError],
) -> list[tuple[Path, os.stat_result]]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    chain: list[Path] = []
    cursor = absolute.parent
    while True:
        chain.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    result: list[tuple[Path, os.stat_result]] = []
    for candidate in reversed(chain):
        try:
            info = os.lstat(candidate)
        except OSError as exc:
            raise error("unsafe_parent_chain") from exc
        is_junction = getattr(os.path, "isjunction", None)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or _stat_is_reparse(info)
            or (callable(is_junction) and is_junction(candidate))
        ):
            raise error("unsafe_parent_chain")
        result.append((candidate, info))
    return result


def _open_read_locked(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if os.name != "nt":
        return os.open(path, flags)

    # A read-only handle sharing only reads prevents concurrent replacement,
    # writes, and deletion throughout the proof capture. OPEN_REPARSE_POINT
    # prevents a last-instant link swap from being followed.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        os.fspath(path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ
        None,
        3,  # OPEN_EXISTING
        0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        code = ctypes.get_last_error()
        raise OSError(code, "read_lease_failed")
    try:
        return msvcrt.open_osfhandle(handle, flags)
    except Exception:
        kernel32.CloseHandle(handle)
        raise


def _stable_capture(
    path: Path,
    *,
    max_bytes: int,
    error: Callable[[str], RuntimeError],
) -> bytes:
    if _has_windows_ads(path):
        raise error("alternate_data_stream_forbidden")
    parents = _plain_parent_snapshot(path, error)
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise error("input_unreadable") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or _stat_is_reparse(before)
        or before.st_nlink != 1
        or before.st_size > max_bytes
    ):
        raise error("input_not_plain_bounded_file")

    try:
        descriptor = _open_read_locked(path)
    except OSError as exc:
        raise error("input_read_lease_failed") from exc
    try:
        try:
            carrier_streams = _windows_handle_stream_inventory(descriptor)
            carrier_change_token = _windows_handle_change_token(descriptor)
        except OSError as exc:
            raise error("alternate_data_stream_inspection_failed") from exc
        if os.name == "nt" and carrier_streams != ("::$data",):
            raise error("alternate_data_stream_forbidden")
        try:
            opened = os.fstat(descriptor)
            after_open = os.lstat(path)
            if (
                not os.path.samestat(before, opened)
                or not os.path.samestat(opened, after_open)
                or _stat_is_reparse(opened)
                or _stat_is_reparse(after_open)
                or opened.st_nlink != before.st_nlink
                or after_open.st_nlink != opened.st_nlink
                or opened.st_size != before.st_size
                or after_open.st_size != opened.st_size
                or opened.st_mtime_ns != before.st_mtime_ns
                or after_open.st_mtime_ns != opened.st_mtime_ns
                or opened.st_size > max_bytes
            ):
                raise error("input_changed_before_read")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(
                    descriptor,
                    min(READ_CHUNK_BYTES, max_bytes + 1 - total),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise error("input_too_large")
            after_read = os.fstat(descriptor)
            final_path = os.lstat(path)
            try:
                final_carrier_streams = _windows_handle_stream_inventory(descriptor)
                final_carrier_change_token = _windows_handle_change_token(descriptor)
            except OSError as exc:
                raise error("alternate_data_stream_inspection_failed") from exc
            if os.name == "nt" and final_carrier_streams != ("::$data",):
                raise error("alternate_data_stream_forbidden")
            if (
                total != opened.st_size
                or not os.path.samestat(opened, after_read)
                or not os.path.samestat(after_read, final_path)
                or _stat_is_reparse(after_read)
                or _stat_is_reparse(final_path)
                or after_read.st_nlink != opened.st_nlink
                or final_path.st_nlink != after_read.st_nlink
                or after_read.st_size != opened.st_size
                or final_path.st_size != after_read.st_size
                or after_read.st_mtime_ns != opened.st_mtime_ns
                or final_path.st_mtime_ns != after_read.st_mtime_ns
                or final_carrier_streams != carrier_streams
                or final_carrier_change_token != carrier_change_token
            ):
                raise error("input_changed_during_read")
        except OSError as exc:
            raise error("input_read_failed") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError as exc:
            raise error("input_read_lease_close_failed") from exc

    final_parents = _plain_parent_snapshot(path, error)
    if len(parents) != len(final_parents) or any(
        left_path != right_path or not os.path.samestat(left_info, right_info)
        for (left_path, left_info), (right_path, right_info) in zip(
            parents,
            final_parents,
        )
    ):
        raise error("parent_chain_changed_during_read")
    return b"".join(chunks)


def _clean_git_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith("GIT_"):
            continue
        env[key] = value
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_REF_PARANOIA"] = "1"
    env["LC_ALL"] = "C"
    return env


def _git(
    repository: Path,
    args: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    inventory_phase: bool = False,
) -> bytes:
    error: Callable[[str], RuntimeError] = (
        InventoryError if inventory_phase else InspectionError
    )
    command = [
        "git",
        "--no-replace-objects",
        "-C",
        os.fspath(repository),
        *args,
    ]
    try:
        completed = subprocess.run(
            command,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_clean_git_env(),
            check=False,
        )
    except (MemoryError, OSError, subprocess.SubprocessError) as exc:
        raise error("git_unavailable") from exc
    if completed.returncode != 0:
        raise error("git_command_failed")
    return completed.stdout


def _decode_utf8(data: bytes, error: Callable[[str], RuntimeError]) -> str:
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise error("unverifiable_encoding") from exc


def _discover_worktrees(repo_root: Path) -> tuple[Path, ...]:
    output = _git(
        repo_root,
        ["worktree", "list", "--porcelain", "-z"],
        inventory_phase=True,
    )
    text = _decode_utf8(output, InventoryError)
    roots: list[Path] = []
    fields = text.split("\x00")
    if not fields or fields[-1] != "":
        raise InventoryError("worktree_inventory_unverifiable")
    for field in fields:
        if field.startswith("worktree "):
            if "\n" in field or len(field) <= len("worktree "):
                raise InventoryError("worktree_inventory_unverifiable")
            roots.append(Path(field[len("worktree ") :]))
    if not roots:
        raise InventoryError("worktree_inventory_empty")
    return tuple(roots)


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    error: Callable[[str], RuntimeError],
) -> None:
    if set(value) != expected:
        raise error("schema_keys_invalid")


def load_sensitive_inventory(
    path: Path | str,
    repo_root: Path | str,
    mirror_root: Path | str,
    worktree_roots: Iterable[Path | str] = (),
) -> SensitiveInventory:
    """Load the exact UTF-8 needle authority from outside every Git tree."""

    inventory_path = Path(path)
    repo = Path(repo_root)
    mirror = Path(mirror_root)
    discovered = _discover_worktrees(repo)
    forbidden_roots = (
        repo,
        mirror,
        *discovered,
        *(Path(item) for item in worktree_roots),
    )
    absolute_inventory = Path(os.path.abspath(os.fspath(inventory_path)))
    root_snapshots: list[list[tuple[Path, os.stat_result]]] = []
    resolved_roots: list[Path] = []
    try:
        resolved_inventory = absolute_inventory.resolve(strict=True)
        for root in forbidden_roots:
            absolute_root = Path(os.path.abspath(os.fspath(root)))
            snapshot = _plain_parent_snapshot(
                absolute_root / "__d1_location_probe__",
                InventoryError,
            )
            root_snapshots.append(snapshot)
            resolved_roots.append(absolute_root.resolve(strict=True))
    except OSError as exc:
        raise InventoryError("location_context_unverifiable") from exc
    if any(_is_within(resolved_inventory, root) for root in resolved_roots):
        raise InventoryError("inventory_inside_git_storage")

    data = _stable_capture(
        absolute_inventory,
        max_bytes=MAX_INVENTORY_BYTES,
        error=InventoryError,
    )
    for before, root in zip(root_snapshots, forbidden_roots):
        absolute_root = Path(os.path.abspath(os.fspath(root)))
        after = _plain_parent_snapshot(
            absolute_root / "__d1_location_probe__",
            InventoryError,
        )
        if len(before) != len(after) or any(
            left_path != right_path or not os.path.samestat(left_info, right_info)
            for (left_path, left_info), (right_path, right_info) in zip(before, after)
        ):
            raise InventoryError("location_context_changed")
    parsed = _strict_json(data, InventoryError)
    if not isinstance(parsed, dict):
        raise InventoryError("inventory_schema_invalid")
    _require_exact_keys(
        parsed,
        {"schema_version", "kind", "fields"},
        InventoryError,
    )
    if (
        type(parsed["schema_version"]) is not int
        or parsed["schema_version"] != SCHEMA_VERSION
    ):
        raise InventoryError("inventory_schema_version_invalid")
    if parsed["kind"] != INVENTORY_KIND or not isinstance(parsed["kind"], str):
        raise InventoryError("inventory_kind_invalid")
    fields = parsed["fields"]
    if not isinstance(fields, dict) or set(fields) != set(PII_FIELDS):
        raise InventoryError("inventory_fields_invalid")

    seen: set[bytes] = set()
    normalized: dict[str, tuple[bytes, ...]] = {}
    for field in PII_FIELDS:
        values = fields[field]
        if (
            not isinstance(values, list)
            or not values
            or len(values) > MAX_VARIANTS_PER_FIELD
        ):
            raise InventoryError("inventory_variant_count_invalid")
        encoded_values: list[bytes] = []
        for value in values:
            if not isinstance(value, str) or not value or value != value.strip():
                raise InventoryError("inventory_variant_invalid")
            if any(
                ord(character) < 0x20 or ord(character) == 0x7F
                for character in value
            ):
                raise InventoryError("inventory_control_character")
            lowered = value.casefold()
            if (
                value.startswith("REDACTED_")
                or lowered.startswith(("regex:", "glob:", "literal:"))
                or "==>" in value
            ):
                raise InventoryError("inventory_variant_grammar_forbidden")
            try:
                encoded = value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise InventoryError("inventory_variant_encoding_invalid") from exc
            if len(encoded) > MAX_VARIANT_BYTES:
                raise InventoryError("inventory_variant_too_large")
            if encoded in seen:
                raise InventoryError("inventory_variant_duplicate")
            seen.add(encoded)
            encoded_values.append(encoded)
        normalized[field] = tuple(encoded_values)
    if not seen:
        raise InventoryError("inventory_empty")
    return SensitiveInventory(fields=normalized)


def _valid_ref_name(name: str) -> bool:
    if not name.startswith("refs/") or name.endswith(("/", ".")):
        return False
    if (
        "//" in name
        or ".." in name
        or "@{" in name
        or _REF_FORBIDDEN.search(name)
    ):
        return False
    return all(
        component
        and not component.startswith(".")
        and not component.endswith(".lock")
        for component in name.split("/")
    )


def load_expected_refs(path: Path | str) -> ExpectedRefs:
    data = _stable_capture(
        Path(path),
        max_bytes=MAX_REF_MANIFEST_BYTES,
        error=InspectionError,
    )
    parsed = _strict_json(data, InspectionError)
    if not isinstance(parsed, dict):
        raise InspectionError("refs_schema_invalid")
    _require_exact_keys(
        parsed,
        {"schema_version", "kind", "refs"},
        InspectionError,
    )
    if (
        type(parsed["schema_version"]) is not int
        or parsed["schema_version"] != SCHEMA_VERSION
    ):
        raise InspectionError("refs_schema_version_invalid")
    if parsed["kind"] != REFS_KIND or not isinstance(parsed["kind"], str):
        raise InspectionError("refs_kind_invalid")
    refs = parsed["refs"]
    if not isinstance(refs, dict) or not refs:
        raise InspectionError("refs_empty")
    normalized: dict[str, str] = {}
    for name, oid in refs.items():
        if not isinstance(name, str) or not _valid_ref_name(name):
            raise InspectionError("ref_name_invalid")
        if not isinstance(oid, str) or not _HEX_OID.fullmatch(oid):
            raise InspectionError("ref_oid_invalid")
        normalized[name] = oid
    return ExpectedRefs(refs=normalized)


def validate_redacted_settings(path: Path | str) -> dict[str, Any]:
    """Validate a plain direct facts mapping containing only placeholders."""

    data = _stable_capture(
        Path(path),
        max_bytes=MAX_SETTINGS_BYTES,
        error=InspectionError,
    )
    if data.startswith(b"\xef\xbb\xbf"):
        raise InspectionError("settings_bom_forbidden")
    text = _decode_utf8(data, InspectionError)
    try:
        import yaml
        from yaml.nodes import MappingNode, ScalarNode, SequenceNode
        from yaml.tokens import AliasToken, AnchorToken, DirectiveToken, TagToken
    except ImportError as exc:
        raise InspectionError("settings_parser_unavailable") from exc
    try:
        tokens = yaml.scan(text, Loader=yaml.BaseLoader)
        if any(
            isinstance(token, (AliasToken, AnchorToken, DirectiveToken, TagToken))
            for token in tokens
        ):
            raise InspectionError("settings_yaml_feature_forbidden")
        root = yaml.compose(text, Loader=yaml.BaseLoader)
    except (MemoryError, RecursionError, yaml.YAMLError) as exc:
        raise InspectionError("settings_mapping_invalid") from exc
    if not isinstance(root, MappingNode) or root.flow_style is not False:
        raise InspectionError("settings_root_mapping_invalid")

    def validate_mapping_tree(node: Any) -> None:
        if isinstance(node, MappingNode):
            seen: set[str] = set()
            for key_node, value_node in node.value:
                if not isinstance(key_node, ScalarNode):
                    raise InspectionError("settings_mapping_key_invalid")
                key = key_node.value
                if key in seen:
                    raise InspectionError("settings_duplicate_key")
                if key == "<<":
                    raise InspectionError("settings_merge_forbidden")
                seen.add(key)
                validate_mapping_tree(value_node)
        elif isinstance(node, SequenceNode):
            for item in node.value:
                validate_mapping_tree(item)
        elif not isinstance(node, ScalarNode):
            raise InspectionError("settings_node_invalid")

    try:
        validate_mapping_tree(root)
    except (MemoryError, RecursionError) as exc:
        raise InspectionError("settings_mapping_invalid") from exc
    facts_pairs = [
        (key_node, value_node)
        for key_node, value_node in root.value
        if isinstance(key_node, ScalarNode) and key_node.value == "facts"
    ]
    if len(facts_pairs) != 1:
        raise InspectionError("settings_facts_incomplete")
    facts_key, facts_node = facts_pairs[0]
    if (
        facts_key.style is not None
        or not isinstance(facts_node, MappingNode)
        or facts_node.flow_style is not False
    ):
        raise InspectionError("settings_facts_not_direct_mapping")
    values: dict[str, str] = {}
    for key_node, value_node in facts_node.value:
        if (
            not isinstance(key_node, ScalarNode)
            or key_node.style is not None
            or key_node.value not in PII_FIELDS
            or not _FIELD_NAME.fullmatch(key_node.value)
        ):
            raise InspectionError("settings_unknown_fact")
        if not isinstance(value_node, ScalarNode) or value_node.style is not None:
            raise InspectionError("settings_scalar_form_unsupported")
        values[key_node.value] = value_node.value
    if set(values) != set(PII_FIELDS):
        raise InspectionError("settings_facts_incomplete")
    if any(
        values[field] != placeholder
        for field, placeholder in PII_FIELDS.items()
    ):
        raise InspectionError("settings_not_redacted")
    return {"field_count": len(values)}


def _mirror_root(path: Path | str) -> Path:
    mirror = Path(os.path.abspath(os.fspath(path)))
    try:
        chain = _plain_parent_snapshot(
            mirror / "__d1_mirror_probe__",
            InspectionError,
        )
    except OSError as exc:
        raise InspectionError("mirror_unreadable") from exc
    if not chain or chain[-1][0] != mirror:
        raise InspectionError("mirror_not_plain_directory")
    return mirror


def _mirror_shape_snapshot(mirror: Path) -> dict[str, tuple[int, ...]]:
    """Reject filesystem indirection and fingerprint the bare mirror shape."""

    snapshot: dict[str, tuple[int, ...]] = {}
    pending = [mirror]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise InspectionError("mirror_filesystem_unreadable") from exc
        for entry in entries:
            try:
                info = os.lstat(entry.path)
            except OSError as exc:
                raise InspectionError("mirror_filesystem_unreadable") from exc
            is_junction = getattr(os.path, "isjunction", None)
            if (
                entry.is_symlink()
                or _stat_is_reparse(info)
                or (callable(is_junction) and is_junction(entry.path))
            ):
                raise InspectionError("mirror_filesystem_indirection")
            relative = Path(entry.path).relative_to(mirror).as_posix()
            if stat.S_ISDIR(info.st_mode):
                snapshot[f"d:{relative}"] = (
                    info.st_dev,
                    info.st_ino,
                    info.st_mtime_ns,
                )
                pending.append(Path(entry.path))
            elif stat.S_ISREG(info.st_mode) and info.st_nlink >= 1:
                snapshot[f"f:{relative}"] = (
                    info.st_dev,
                    info.st_ino,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_nlink,
                )
            else:
                raise InspectionError("mirror_filesystem_non_plain")
    if not snapshot:
        raise InspectionError("mirror_filesystem_empty")
    return snapshot


def _git_text(repository: Path, args: Sequence[str]) -> str:
    return _decode_utf8(_git(repository, args), InspectionError)


def _actual_refs(mirror: Path) -> dict[str, str]:
    output = _git(
        mirror,
        ["for-each-ref", "--format=%(refname)%00%(objectname)%00"],
    )
    parts = output.split(b"\x00")
    if parts and parts[-1] in (b"", b"\n"):
        parts.pop()
    if len(parts) % 2:
        raise InspectionError("refs_output_invalid")
    refs: dict[str, str] = {}
    for index in range(0, len(parts), 2):
        raw_name = parts[index].lstrip(b"\n")
        raw_oid = parts[index + 1]
        name = _decode_utf8(raw_name, InspectionError)
        oid = _decode_utf8(raw_oid, InspectionError)
        if (
            not _valid_ref_name(name)
            or not _HEX_OID.fullmatch(oid)
            or name in refs
        ):
            raise InspectionError("refs_output_invalid")
        refs[name] = oid
    if not refs:
        raise InspectionError("refs_empty")
    return refs


def _local_config_names(mirror: Path) -> set[str]:
    output = _git(
        mirror,
        ["config", "--local", "--name-only", "--null", "--list"],
    )
    text = _decode_utf8(output, InspectionError)
    names = {name.casefold() for name in text.split("\x00") if name}
    if not names:
        raise InspectionError("mirror_config_unverifiable")
    return names


def _mirror_preflight(
    mirror: Path,
    expected: ExpectedRefs,
) -> dict[str, str]:
    if _git_text(mirror, ["rev-parse", "--is-bare-repository"]).strip() != "true":
        raise InspectionError("mirror_not_bare")
    if _git_text(mirror, ["rev-parse", "--is-shallow-repository"]).strip() != "false":
        raise InspectionError("mirror_shallow")
    for relative in (Path("objects/info/alternates"), Path("info/grafts")):
        if (mirror / relative).exists():
            raise InspectionError("mirror_alternate_or_graft")
    if (mirror / "refs/replace").exists():
        raise InspectionError("mirror_replace_refs")
    try:
        if any((mirror / "objects").rglob("*.promisor")):
            raise InspectionError("mirror_partial_clone")
    except OSError as exc:
        raise InspectionError("mirror_objects_unreadable") from exc
    config_names = _local_config_names(mirror)
    if any(
        name == "extensions.partialclone"
        or (
            name.startswith("remote.")
            and name.endswith((".promisor", ".partialclonefilter"))
        )
        for name in config_names
    ):
        raise InspectionError("mirror_partial_clone")
    refs = _actual_refs(mirror)
    if any(name.startswith("refs/replace/") for name in refs):
        raise InspectionError("mirror_replace_refs")
    if any(
        name.startswith(("fsck.", "fetch.fsck.", "receive.fsck."))
        or name == "transfer.fsckobjects"
        for name in config_names
    ):
        raise InspectionError("mirror_fsck_override")
    if refs != dict(expected.refs):
        raise InspectionError("refs_authority_mismatch")
    _git(mirror, ["fsck", "--full", "--strict", "--no-dangling"])
    return refs


def _object_inventory(
    mirror: Path,
    ref_names: Sequence[str],
) -> tuple[dict[str, str], set[str]]:
    stored_text = _git_text(
        mirror,
        [
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype)",
        ],
    )
    stored: dict[str, str] = {}
    for line in stored_text.splitlines():
        parts = line.split(" ")
        if (
            len(parts) != 2
            or not _HEX_OID.fullmatch(parts[0])
            or parts[1] not in ALLOWED_OBJECT_TYPES
            or parts[0] in stored
        ):
            raise InspectionError("object_inventory_invalid")
        stored[parts[0]] = parts[1]
    if not stored:
        raise InspectionError("object_inventory_empty")
    reachable_text = _decode_utf8(
        _git(
            mirror,
            ["rev-list", "--objects", "--no-object-names", "--stdin"],
            input_bytes=("\n".join(ref_names) + "\n").encode("utf-8"),
        ),
        InspectionError,
    )
    reachable_lines = [line for line in reachable_text.splitlines() if line]
    reachable = set(reachable_lines)
    if (
        not reachable
        or len(reachable) != len(reachable_lines)
        or any(not _HEX_OID.fullmatch(oid) for oid in reachable)
    ):
        raise InspectionError("reachable_inventory_invalid")
    if reachable != set(stored):
        raise InspectionError("stored_reachable_object_mismatch")
    return stored, reachable


def _matches(data: bytes, needles: Sequence[bytes]) -> int:
    return sum(1 for needle in needles if needle in data)


class _NeedleScanner:
    """Bounded exact-byte matcher that preserves cross-chunk matches."""

    def __init__(self, needles: Sequence[bytes]) -> None:
        if not needles:
            raise InspectionError("inventory_empty")
        self._needles = tuple(needles)
        self._matched: set[int] = set()
        self._carry = b""
        self._carry_size = max(len(needle) for needle in needles) - 1

    def feed(self, data: bytes) -> None:
        if not data:
            return
        window = self._carry + data
        for index, needle in enumerate(self._needles):
            if index not in self._matched and needle in window:
                self._matched.add(index)
        self._carry = window[-self._carry_size :] if self._carry_size else b""

    @property
    def count(self) -> int:
        return len(self._matched)


class _MetadataStreamScanner:
    """Incrementally validate and classify commit/tag metadata bytes."""

    _HEADER_CATEGORIES = {
        b"author": "author",
        b"committer": "committer",
        b"tagger": "tagger",
    }
    _MAX_HEADER_NAME_BYTES = max(len(name) for name in _HEADER_CATEGORIES)

    def __init__(self, needles: Sequence[bytes]) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self._scanners = {
            category: _NeedleScanner(needles)
            for category in ("author", "committer", "tagger", "message", "other")
        }
        self._in_headers = True
        self._line_prefix = b""
        self._line_category: str | None = None
        self._line_has_data = False

    def _feed_header_fragment(self, fragment: bytes) -> None:
        if fragment:
            self._line_has_data = True
        if self._line_category is not None:
            self._scanners[self._line_category].feed(fragment)
            return
        combined = self._line_prefix + fragment
        separator = combined.find(b" ")
        if separator >= 0 or len(combined) > self._MAX_HEADER_NAME_BYTES:
            name = combined[:separator] if separator >= 0 else b""
            self._line_category = self._HEADER_CATEGORIES.get(name, "other")
            self._scanners[self._line_category].feed(combined)
            self._line_prefix = b""
        else:
            self._line_prefix = combined

    def _finish_header_line(self) -> None:
        if not self._line_has_data:
            self._in_headers = False
            return
        if self._line_category is None:
            self._line_category = "other"
            self._scanners["other"].feed(self._line_prefix)
        self._scanners[self._line_category].feed(b"\n")
        self._line_prefix = b""
        self._line_category = None
        self._line_has_data = False

    def feed(self, data: bytes) -> None:
        try:
            self._decoder.decode(data, final=False)
        except UnicodeDecodeError as exc:
            raise InspectionError("unverifiable_encoding") from exc
        position = 0
        while self._in_headers and position < len(data):
            newline = data.find(b"\n", position)
            if newline < 0:
                self._feed_header_fragment(data[position:])
                return
            self._feed_header_fragment(data[position:newline])
            self._finish_header_line()
            position = newline + 1
        if not self._in_headers and position < len(data):
            self._scanners["message"].feed(data[position:])

    def finish(self) -> dict[str, int]:
        try:
            self._decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise InspectionError("unverifiable_encoding") from exc
        if self._in_headers:
            raise InspectionError("metadata_object_invalid")
        return {name: scanner.count for name, scanner in self._scanners.items()}


def _stream_git_object(
    mirror: Path,
    object_type: str,
    oid: str,
    consumer: Callable[[bytes], None],
) -> int:
    if object_type not in {"blob", "commit", "tag"} or not _HEX_OID.fullmatch(oid):
        raise InspectionError("object_request_invalid")
    command = [
        "git",
        "--no-replace-objects",
        "-C",
        os.fspath(mirror),
        "cat-file",
        object_type,
        oid,
    ]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_clean_git_env(),
            bufsize=0,
        )
    except OSError as exc:
        raise InspectionError("git_unavailable") from exc

    def stop_process() -> None:
        try:
            if process.stdout is not None:
                process.stdout.close()
        except OSError:
            pass
        try:
            if process.poll() is None:
                process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass

    total = 0
    try:
        if process.stdout is None:
            raise InspectionError("object_stream_unavailable")
        while True:
            chunk = process.stdout.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            consumer(chunk)
        process.stdout.close()
        return_code = process.wait()
        if return_code != 0:
            raise InspectionError("git_command_failed")
    except InspectionError:
        stop_process()
        raise
    except (MemoryError, OSError, subprocess.SubprocessError) as exc:
        stop_process()
        raise InspectionError("object_stream_failed") from exc
    return total


def _scan_blob_object(
    mirror: Path,
    oid: str,
    needles: Sequence[bytes],
) -> tuple[int, int]:
    scanner = _NeedleScanner(needles)
    size = _stream_git_object(mirror, "blob", oid, scanner.feed)
    return size, scanner.count


def _scan_metadata_object(
    mirror: Path,
    object_type: str,
    oid: str,
    needles: Sequence[bytes],
) -> tuple[int, dict[str, int]]:
    scanner = _MetadataStreamScanner(needles)
    size = _stream_git_object(mirror, object_type, oid, scanner.feed)
    return size, scanner.finish()


def _parse_tree(output: bytes) -> list[tuple[str, str, str, bytes]]:
    entries: list[tuple[str, str, str, bytes]] = []
    for row in output.split(b"\x00"):
        if not row:
            continue
        try:
            metadata, path = row.split(b"\t", 1)
            mode, object_type, oid = metadata.decode("ascii").split(" ")
        except (ValueError, UnicodeDecodeError) as exc:
            raise InspectionError("tree_output_invalid") from exc
        if not _HEX_OID.fullmatch(oid):
            raise InspectionError("tree_entry_unsupported")
        if object_type == "tree" and mode == "040000":
            pass
        elif object_type == "blob" and mode in ALLOWED_BLOB_MODES:
            pass
        else:
            raise InspectionError("tree_mode_unsupported")
        # Decoding is mandatory even though matching remains exact bytes: an
        # unreportable path makes the observation incomplete.
        _decode_utf8(path, InspectionError)
        entries.append((mode, object_type, oid, path))
    return entries


def inspect_repository_snapshot(
    mirror: Path | str,
    expected_refs: ExpectedRefs | Mapping[str, str],
    inventory: SensitiveInventory,
) -> dict[str, Any]:
    """Exhaustively observe exact refs and all stored/reachable objects."""

    mirror_path = _mirror_root(mirror)
    mirror_chain_before = _plain_parent_snapshot(
        mirror_path / "__d1_mirror_probe__",
        InspectionError,
    )
    mirror_shape_before = _mirror_shape_snapshot(mirror_path)
    expected = (
        expected_refs
        if isinstance(expected_refs, ExpectedRefs)
        else ExpectedRefs(dict(expected_refs))
    )
    refs_before = _mirror_preflight(mirror_path, expected)
    stored, _reachable = _object_inventory(
        mirror_path,
        tuple(sorted(refs_before)),
    )
    needles = inventory.needles
    if not needles:
        raise InspectionError("inventory_empty")

    ref_match_count = sum(
        _matches(name.encode("utf-8"), needles) for name in refs_before
    )
    commit_text = _decode_utf8(
        _git(
            mirror_path,
            ["rev-list", "--topo-order", "--reverse", "--stdin"],
            input_bytes=("\n".join(sorted(refs_before)) + "\n").encode("utf-8"),
        ),
        InspectionError,
    )
    commit_lines = [line for line in commit_text.splitlines() if line]
    commits = tuple(commit_lines)
    if (
        not commits
        or len(set(commits)) != len(commits)
        or any(not _HEX_OID.fullmatch(oid) for oid in commits)
    ):
        raise InspectionError("commit_inventory_invalid")

    blob_occurrences: dict[str, list[tuple[str, str, bytes]]] = {}
    observed_tree_oids: set[str] = set()
    tree_entry_count = 0
    path_match_count = 0
    for commit in commits:
        root_tree = _git_text(
            mirror_path,
            ["rev-parse", f"{commit}^{{tree}}"],
        ).strip()
        if not _HEX_OID.fullmatch(root_tree):
            raise InspectionError("commit_root_tree_invalid")
        observed_tree_oids.add(root_tree)
        tree = _git(
            mirror_path,
            ["ls-tree", "-r", "-t", "-z", "--full-tree", commit],
        )
        entries = _parse_tree(tree)
        if not entries:
            raise InspectionError("tree_inventory_empty")
        tree_entry_count += len(entries)
        for mode, object_type, oid, path_bytes in entries:
            if object_type == "tree":
                observed_tree_oids.add(oid)
                continue
            path_match_count += _matches(path_bytes, needles)
            blob_occurrences.setdefault(oid, []).append(
                (commit, mode, path_bytes)
            )
    if not blob_occurrences or tree_entry_count == 0:
        raise InspectionError("blob_inventory_empty")
    stored_commits = {oid for oid, kind in stored.items() if kind == "commit"}
    stored_trees = {oid for oid, kind in stored.items() if kind == "tree"}
    stored_blobs = {oid for oid, kind in stored.items() if kind == "blob"}
    if (
        stored_commits != set(commits)
        or stored_trees != observed_tree_oids
        or stored_blobs != set(blob_occurrences)
    ):
        raise InspectionError("object_context_unsupported")

    categories = {
        "settings": 0,
        "legal_keep": 0,
        "unexpected_scope": 0,
        "path": path_match_count,
        "ref": ref_match_count,
        "metadata": 0,
    }
    metadata_categories = {
        "author": 0,
        "committer": 0,
        "tagger": 0,
        "message": 0,
        "other": 0,
    }
    matched_paths: set[str] = set()
    unexpected_paths: set[str] = set()
    scanned_blob_bytes = 0
    content_match_occurrences = 0
    for oid, occurrences in blob_occurrences.items():
        blob_size, match_count = _scan_blob_object(mirror_path, oid, needles)
        scanned_blob_bytes += blob_size
        if not match_count:
            continue
        for _commit, _mode, path_bytes in occurrences:
            path = _decode_utf8(path_bytes, InspectionError)
            normalized = path
            matched_paths.add(normalized)
            content_match_occurrences += match_count
            if normalized in SETTINGS_PATHS:
                categories["settings"] += match_count
            elif normalized in LEGAL_KEEP_PATHS:
                categories["legal_keep"] += match_count
            else:
                categories["unexpected_scope"] += match_count
                unexpected_paths.add(normalized)

    for oid, object_type in stored.items():
        if object_type not in {"commit", "tag"}:
            continue
        _metadata_size, object_counts = _scan_metadata_object(
            mirror_path,
            object_type,
            oid,
            needles,
        )
        for category, category_count in object_counts.items():
            metadata_categories[category] += category_count
        count = sum(object_counts.values())
        categories["metadata"] += count
        categories["unexpected_scope"] += count
    categories["unexpected_scope"] += categories["path"] + categories["ref"]

    refs_after = _mirror_preflight(mirror_path, expected)
    if refs_after != refs_before:
        raise InspectionError("refs_changed_during_inspection")
    stored_after, reachable_after = _object_inventory(
        mirror_path,
        tuple(sorted(refs_after)),
    )
    if stored_after != stored or reachable_after != _reachable:
        raise InspectionError("objects_changed_during_inspection")
    if _mirror_shape_snapshot(mirror_path) != mirror_shape_before:
        raise InspectionError("mirror_filesystem_changed_during_inspection")
    mirror_chain_after = _plain_parent_snapshot(
        mirror_path / "__d1_mirror_probe__",
        InspectionError,
    )
    if len(mirror_chain_before) != len(mirror_chain_after) or any(
        left_path != right_path or not os.path.samestat(left_info, right_info)
        for (left_path, left_info), (right_path, right_info) in zip(
            mirror_chain_before,
            mirror_chain_after,
        )
    ):
        raise InspectionError("mirror_path_changed_during_inspection")
    if (
        len(refs_before) == 0
        or len(stored) == 0
        or len(commits) == 0
        or len(blob_occurrences) == 0
        or tree_entry_count == 0
        or scanned_blob_bytes == 0
        or (
            categories["settings"]
            + categories["legal_keep"]
            + categories["unexpected_scope"]
            == 0
        )
    ):
        raise InspectionError("vacuous_observation")

    return {
        "observation": "snapshot_inspected",
        "ref_count": len(refs_before),
        "stored_object_count": len(stored),
        "commit_count": len(commits),
        "unique_blob_count": len(blob_occurrences),
        "tree_entry_count": tree_entry_count,
        "scanned_blob_bytes": scanned_blob_bytes,
        "content_match_occurrences": content_match_occurrences,
        "matched_path_count": len(matched_paths),
        "unexpected_path_count": len(unexpected_paths),
        "categories": categories,
        "metadata_categories": metadata_categories,
    }


def build_preparation_report(
    snapshot: Mapping[str, Any],
    *,
    prepared_from_commit: str,
    settings_results: Sequence[Mapping[str, Any]],
    inventory: SensitiveInventory,
) -> dict[str, Any]:
    if not _HEX_OID.fullmatch(prepared_from_commit):
        raise InspectionError("prepared_commit_invalid")
    if len(settings_results) != len(SETTINGS_PATHS):
        raise InspectionError("settings_evidence_incomplete")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "status": REPORT_STATUS,
        "inspection_status": "complete",
        "blocked_scope": True,
        "authority": dict(FALSE_AUTHORITY),
        "prepared_from_commit": prepared_from_commit,
        "inventory": {
            "field_count": len(inventory.fields),
            "variant_count": inventory.variant_count,
        },
        "settings": {
            "validated_file_count": len(settings_results),
            "validated_field_count": sum(
                int(item["field_count"]) for item in settings_results
            ),
        },
        "history_observation": dict(snapshot),
        "blockers": [
            "scope_authority_absent",
            "legal_authority_absent",
            "execution_authority_absent",
            "separate_destructive_phase_required",
        ],
    }


def _refusal_report(mode: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "status": REPORT_STATUS,
        "inspection_status": "incomplete",
        "blocked_scope": True,
        "authority": dict(FALSE_AUTHORITY),
        "requested_mode": mode,
        "reason": "execution_unavailable_in_prep",
    }


def _error_report(category: str, code: str) -> str:
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": REPORT_KIND,
            "status": REPORT_STATUS,
            "inspection_status": "incomplete",
            "blocked_scope": True,
            "authority": dict(FALSE_AUTHORITY),
            "error_category": category,
            "error_code": code,
        },
        sort_keys=True,
    )


class _PreparationArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise InventoryError("cli_arguments_invalid")


def _build_parser() -> argparse.ArgumentParser:
    parser = _PreparationArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version="d1_pii_scrub PREP-v4",
    )
    parser.add_argument(
        "mode",
        choices=(
            "inspect",
            "detect",
            "plan",
            "dry-run",
            "push",
            "force-push",
        ),
    )
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--mirror", type=Path)
    parser.add_argument("--expected-refs", type=Path)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--settings", type=Path, action="append")
    return parser


def _selected_settings_paths(
    repo: Path,
    supplied: Sequence[Path] | None,
) -> tuple[Path, ...]:
    expected = tuple(
        Path(os.path.abspath(os.fspath(repo / relative)))
        for relative in SETTINGS_PATHS
    )
    if supplied is None:
        return expected
    selected = tuple(
        Path(os.path.abspath(os.fspath(candidate))) for candidate in supplied
    )
    if len(selected) != len(expected) or {
        _path_key(path) for path in selected
    } != {_path_key(path) for path in expected}:
        raise InspectionError("settings_selection_invalid")
    return expected


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except InventoryError as exc:
        print(_error_report("inventory", exc.code), file=sys.stderr)
        return 3
    if args.mode in {"dry-run", "push", "force-push"}:
        print(json.dumps(_refusal_report(args.mode), indent=2, sort_keys=True))
        return 2
    if args.inventory is None or args.mirror is None or args.expected_refs is None:
        print(
            _error_report("inventory", "required_authority_missing"),
            file=sys.stderr,
        )
        return 3
    try:
        settings_paths = _selected_settings_paths(args.repo, args.settings)
        inventory = load_sensitive_inventory(
            args.inventory,
            args.repo,
            args.mirror,
        )
        expected = load_expected_refs(args.expected_refs)
        settings_results = [
            validate_redacted_settings(path) for path in settings_paths
        ]
        snapshot = inspect_repository_snapshot(args.mirror, expected, inventory)
        main_oid = expected.refs.get("refs/heads/main")
        if main_oid is None:
            raise InspectionError("expected_main_ref_missing")
        report = build_preparation_report(
            snapshot,
            prepared_from_commit=main_oid,
            settings_results=settings_results,
            inventory=inventory,
        )
    except InventoryError as exc:
        print(_error_report("inventory", exc.code), file=sys.stderr)
        return 3
    except InspectionError as exc:
        print(_error_report("inspection", exc.code), file=sys.stderr)
        return 4
    except (MemoryError, RecursionError):
        print(
            _error_report("inspection", "resource_limit_exceeded"),
            file=sys.stderr,
        )
        return 4
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
