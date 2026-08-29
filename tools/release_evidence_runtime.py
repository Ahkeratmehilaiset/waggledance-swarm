#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed runtime shared by canonical release-evidence producers.

This module is deliberately stdlib-only.  It does not decide whether product
evidence passes; it binds that decision to one clean Git snapshot and publishes
the resulting completion envelope without leaving a stale pass after a known
durability failure.

Release producers must validate their sealed argument grammar before loading
this module.  The helpers here repeat the validation at the shared boundary,
bind the executing source to frozen Git blobs, and map every integrity failure
to exit 2.  A complete, trustworthy product/security hold is the only path to
exit 1.
"""

from __future__ import annotations

import contextlib
import ctypes
import base64
import hashlib
import json
import math
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping, Sequence


EXIT_PASS = 0
EXIT_HOLD_NONPASS = 1
EXIT_INTEGRITY = 2

# These values are deliberately outside the public 0/1/2 domain.  The outer
# process never treats a raw conventional process exit as release evidence.
PRIVATE_EXIT_PASS = 40
PRIVATE_EXIT_HOLD = 41
PRIVATE_EXIT_INTEGRITY = 42

ENVELOPE_SCHEMA_VERSION = "waggledance.release_evidence_envelope.v1"
SOURCE_NORMALIZATION = "exact_utf8_no_nul_no_cr_v1"
RUNTIME_RELPATH = "tools/release_evidence_runtime.py"

CANONICAL_OUTPUTS = MappingProxyType(
    {
        "axis_a_solver_scale": (
            "docs/runs/release_soak_evidence/"
            "v3.12.0_axis_a_solver_scale/solver_scale_proof.json"
        ),
        "axis_b_hex_aligned_eval": (
            "docs/runs/release_soak_evidence/"
            "v3.12.0_axis_b_hex_aligned_eval.json"
        ),
        "soak_log_audit": (
            "docs/runs/release_soak_evidence/v3.12.0_soak_log_audit.json"
        ),
        "lock_osv_audit": (
            "docs/runs/release_soak_evidence/"
            "v3.12.0_pip_audit_report_lock_after_prune_osv.json"
        ),
        "bandit_release": (
            "docs/runs/release_soak_evidence/"
            "v3.12.0_bandit_release_evidence.json"
        ),
    }
)

_FULL_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,95}$")
_PRODUCER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_WINDOWS_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{value}" for value in range(1, 10)}
    | {f"lpt{value}" for value in range(1, 10)}
)
_FRAME_SCHEMA = "waggledance.release_evidence_child_frame.v1"
_RECEIPT_SCHEMA = "waggledance.release_evidence_child_receipt.v1"
_TRANSACTION_SCHEMA = "waggledance.release_evidence_path_transaction.v1"
_FRAME_LIMIT = 16 * 1024 * 1024
_PRIVATE_FAIL_STOP = 70
_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")


class EvidenceIntegrityError(RuntimeError):
    """A path, source, envelope, or durability ambiguity.

    The exception text is intentionally a stable, path-free reason code.  A
    producer's outermost boundary must map this and all unexpected exceptions
    to ``EXIT_INTEGRITY``.
    """

    def __init__(
        self,
        reason_code: str,
        *,
        phase: str = "integrity",
        recovery_paths: Sequence[str] = (),
    ) -> None:
        if _REASON_CODE_RE.fullmatch(reason_code) is None:
            reason_code = "invalid_integrity_reason"
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.phase = phase
        self.recovery_paths = tuple(recovery_paths)


class EvidenceFailStop(BaseException):
    """An ambiguous canonical state for which no public result is permitted."""


@dataclass(frozen=True)
class SealedArgvContract:
    """Closed argument grammar used before any repository or temp access."""

    mode: str
    exact_tokens: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"empty", "exact", "source_pairs"}:
            raise ValueError("sealed argv contract has an unknown mode")
        if self.mode == "exact" and not self.exact_tokens:
            raise ValueError("exact sealed argv contract requires tokens")
        if self.mode != "exact" and self.exact_tokens:
            raise ValueError("only exact sealed argv contracts accept tokens")


@dataclass(frozen=True)
class ProducerSpec:
    producer_id: str
    producer_relpath: str
    canonical_output_relpath: str
    argv_contract: SealedArgvContract
    allowed_hold_reason_sets: frozenset[frozenset[str]]

    def __post_init__(self) -> None:
        if _PRODUCER_ID_RE.fullmatch(self.producer_id) is None:
            raise ValueError("producer id is invalid")
        producer_relpath = validate_repo_relpath(self.producer_relpath)
        output_relpath = validate_repo_relpath(self.canonical_output_relpath)
        if producer_relpath != self.producer_relpath:
            raise ValueError("producer path is not canonical")
        if output_relpath != self.canonical_output_relpath:
            raise ValueError("canonical output path is not canonical")
        expected = CANONICAL_OUTPUTS.get(self.producer_id)
        if expected is None or expected != output_relpath:
            raise ValueError("producer output is not the fixed canonical literal")
        if not self.allowed_hold_reason_sets:
            raise ValueError("producer requires at least one complete hold reason set")
        for reason_set in self.allowed_hold_reason_sets:
            if not reason_set:
                raise ValueError("empty hold reason set is invalid")
            for reason in reason_set:
                _validate_reason_code(reason)


@dataclass(frozen=True)
class ProducerOutcome:
    status: str
    reason_codes: tuple[str, ...]
    evidence: Mapping[str, Any] | None
    findings: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class FileIdentity:
    scheme: str
    volume: int
    file_id: bytes | int
    size: int
    mtime_ns: int
    nlink: int


@dataclass(frozen=True)
class TrackedBlob:
    relpath: str
    mode: str
    oid: str
    sha256: str
    content: bytes
    checkout_identity: FileIdentity


@dataclass(frozen=True)
class RepositorySnapshot:
    root: Path
    startup_cwd: Path
    git_executable: Path
    head: str
    tree: str
    index_path: Path
    index_lock_path: Path
    index_sha256: str
    index_file_sha256: str
    index_identity: FileIdentity
    tracked_blobs: tuple[TrackedBlob, ...]
    git_sha256: str = ""

    def blob(self, relpath: str) -> TrackedBlob:
        canonical = validate_repo_relpath(relpath)
        matches = [item for item in self.tracked_blobs if item.relpath == canonical]
        if len(matches) != 1:
            raise EvidenceIntegrityError("required_source_not_frozen")
        return matches[0]


def _integrity(reason_code: str, *, phase: str = "integrity") -> None:
    raise EvidenceIntegrityError(reason_code, phase=phase)


def _validate_unicode_text(value: object, *, reason: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        _integrity(reason)
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        _integrity(reason)
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        _integrity(reason)
    return value


def validate_repo_relpath(value: object) -> str:
    """Return one canonical POSIX repository-relative path or fail closed.

    This is the single predicate reused by general-output denial and canonical
    producer paths.  It rejects Windows aliases even when running on POSIX so
    a reviewed path cannot change meaning on another release host.
    """

    text = _validate_unicode_text(value, reason="invalid_repository_relative_path")
    if "\\" in text or ":" in text or text.startswith(("/", "//")):
        _integrity("invalid_repository_relative_path")
    windows = PureWindowsPath(text)
    if windows.drive or windows.root:
        _integrity("invalid_repository_relative_path")
    pure = PurePosixPath(text)
    parts = pure.parts
    if not parts or str(pure) != text:
        _integrity("invalid_repository_relative_path")
    for part in parts:
        if part in {"", ".", ".."} or part.endswith((".", " ")):
            _integrity("invalid_repository_relative_path")
        stem = part.split(".", 1)[0].casefold()
        if stem in _WINDOWS_RESERVED_STEMS:
            _integrity("invalid_repository_relative_path")
    return text


def validate_repo_relpath_list(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        _integrity("invalid_repository_path_list")
    raw = tuple(values)
    canonical = tuple(validate_repo_relpath(value) for value in raw)
    if len(set(canonical)) != len(canonical):
        _integrity("duplicate_repository_path")
    if len({value.casefold() for value in canonical}) != len(canonical):
        _integrity("repository_path_alias")
    return canonical


def validate_sealed_argv(
    raw_argv: Sequence[str],
    contract: SealedArgvContract,
) -> tuple[str, ...]:
    """Validate a closed CLI grammar without reading Git or the filesystem."""

    if isinstance(raw_argv, (str, bytes)):
        _integrity("invalid_sealed_argv", phase="argv")
    argv = tuple(raw_argv)
    if len(argv) > 128:
        _integrity("invalid_sealed_argv", phase="argv")
    total = 0
    for token in argv:
        token = _validate_unicode_text(token, reason="invalid_sealed_argv")
        total += len(token.encode("utf-8"))
        if total > 65536:
            _integrity("invalid_sealed_argv", phase="argv")
    if contract.mode == "empty":
        if argv:
            _integrity("invalid_sealed_argv", phase="argv")
        return ()
    if contract.mode == "exact":
        if argv != contract.exact_tokens:
            _integrity("invalid_sealed_argv", phase="argv")
        return argv
    if not argv or len(argv) % 2:
        _integrity("invalid_sealed_argv", phase="argv")
    sources: list[str] = []
    for index in range(0, len(argv), 2):
        if argv[index] != "--source":
            _integrity("invalid_sealed_argv", phase="argv")
        source = argv[index + 1]
        if source.startswith("-"):
            _integrity("invalid_sealed_argv", phase="argv")
        sources.append(source)
    if len(set(sources)) != len(sources):
        _integrity("duplicate_source_argument", phase="argv")
    return argv


def p1_release_mode_requested(raw_argv: Sequence[str]) -> bool:
    """Select sealed P1 mode without letting malformed tokens fall through."""

    return any(
        type(token) is str
        and (token == "--release-evidence" or token.startswith("--release-evidence="))
        for token in raw_argv
    )


def _validate_reason_code(value: object) -> str:
    if type(value) is not str or _REASON_CODE_RE.fullmatch(value) is None:
        _integrity("invalid_reason_code", phase="envelope")
    return value


def _canonical_source_bytes(raw: bytes) -> bytes:
    if b"\x00" in raw:
        _integrity("source_contains_nul", phase="source")
    try:
        raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _integrity("source_is_not_utf8", phase="source")
    if b"\r" in raw:
        _integrity("source_contains_cr", phase="source")
    return raw


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _windows_directory() -> str:
    if os.name != "nt":
        _integrity("windows_directory_requested_off_windows")
    from ctypes import wintypes

    buffer = ctypes.create_unicode_buffer(32768)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel32.GetWindowsDirectoryW.restype = wintypes.UINT
    length = int(kernel32.GetWindowsDirectoryW(buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        _integrity("windows_directory_unavailable")
    return buffer.value


def fresh_process_environment() -> dict[str, str]:
    """Construct the complete Git/child environment from an empty mapping."""

    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }
    if os.name == "nt":
        windows = _windows_directory()
        environment["SystemRoot"] = windows
        environment["WINDIR"] = windows
    return environment


def _trusted_git_literal() -> Path:
    if sys.platform == "win32":
        return Path(r"C:\Program Files\Git\mingw64\bin\git.exe")
    if sys.platform == "linux":
        return Path("/usr/bin/git")
    if sys.platform == "darwin":
        return Path("/usr/bin/git")
    _integrity("unsupported_release_platform", phase="git")


def _stat_is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _lexical_path_error(path: Path) -> bool:
    raw = os.fspath(path)
    if not raw or "\x00" in raw:
        return True
    try:
        raw.encode("utf-8", errors="strict")
    except UnicodeError:
        return True
    if os.name == "nt" and raw.startswith(("\\\\", "//")):
        return True
    parts = PureWindowsPath(raw).parts if os.name == "nt" else Path(raw).parts
    for position, part in enumerate(parts):
        if part in {".", ".."}:
            return True
        if os.name == "nt" and position > 0:
            if part.endswith((".", " ")) or ":" in part:
                return True
            if part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_STEMS:
                return True
    return False


def _windows_assert_not_short_alias(path: Path) -> None:
    from ctypes import wintypes

    existing = path
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    if not existing.exists():
        _integrity("path_identity_unavailable", phase="path")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetLongPathNameW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetLongPathNameW.restype = wintypes.DWORD
    size = 32768
    buffer = ctypes.create_unicode_buffer(size)
    length = int(kernel32.GetLongPathNameW(os.fspath(existing), buffer, size))
    if length <= 0 or length >= size:
        _integrity("path_long_name_unavailable", phase="path")
    supplied = os.path.normcase(os.path.normpath(os.fspath(existing)))
    expanded = os.path.normcase(os.path.normpath(buffer.value))
    if supplied != expanded:
        _integrity("windows_short_path_alias", phase="path")


def _absolute_lexical(path: Path | str) -> Path:
    candidate = Path(path)
    if _lexical_path_error(candidate):
        _integrity("ambiguous_filesystem_path", phase="path")
    absolute = Path(os.path.abspath(os.path.normpath(os.fspath(candidate))))
    if not absolute.is_absolute():
        _integrity("filesystem_path_is_not_absolute", phase="path")
    if os.name == "nt":
        if not re.fullmatch(r"[A-Za-z]:", absolute.drive):
            _integrity("filesystem_path_is_not_local_drive", phase="path")
        _windows_assert_not_short_alias(absolute)
    return absolute


def _directory_chain(path: Path) -> tuple[Path, ...]:
    absolute = _absolute_lexical(path)
    chain: list[Path] = []
    cursor = absolute
    while True:
        chain.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    return tuple(reversed(chain))


def _assert_plain_directory_chain(path: Path) -> None:
    for candidate in _directory_chain(path):
        try:
            info = os.lstat(candidate)
        except OSError:
            _integrity("directory_chain_unreadable", phase="path")
        if not stat.S_ISDIR(info.st_mode) or _stat_is_reparse(info):
            _integrity("directory_chain_not_plain", phase="path")
        if os.path.islink(candidate):
            _integrity("directory_chain_not_plain", phase="path")
        junction = getattr(os.path, "isjunction", None)
        if callable(junction) and junction(candidate):
            _integrity("directory_chain_not_plain", phase="path")


def _same_identity_path(left: Path, right: Path) -> bool:
    left_abs = _absolute_lexical(left)
    right_abs = _absolute_lexical(right)
    if os.path.normcase(os.fspath(left_abs)) != os.path.normcase(os.fspath(right_abs)):
        return False
    try:
        return os.path.samefile(left_abs, right_abs)
    except OSError:
        return False


def path_is_within(candidate: Path | str, evidence_root: Path | str) -> bool:
    """Identity-normalized component ancestry; never string-prefix ancestry."""

    candidate_abs = _absolute_lexical(candidate)
    root_abs = _absolute_lexical(evidence_root)
    _assert_plain_directory_chain(root_abs)
    candidate_chain = candidate_abs if candidate_abs.is_dir() else candidate_abs.parent
    _assert_plain_directory_chain(candidate_chain)
    if candidate_abs.exists() and not candidate_abs.is_dir():
        info = os.lstat(candidate_abs)
        if (
            not stat.S_ISREG(info.st_mode)
            or _stat_is_reparse(info)
            or info.st_nlink != 1
        ):
            _integrity("candidate_path_not_plain", phase="path")
    try:
        candidate_resolved = candidate_abs.resolve(strict=True)
        root_resolved = root_abs.resolve(strict=True)
    except OSError:
        _integrity("path_identity_unavailable", phase="path")
    candidate_key = tuple(os.path.normcase(part) for part in candidate_resolved.parts)
    root_key = tuple(os.path.normcase(part) for part in root_resolved.parts)
    return candidate_key == root_key or (
        len(candidate_key) > len(root_key)
        and candidate_key[: len(root_key)] == root_key
    )


def _trusted_git_executable() -> Path:
    return _bind_trusted_git().path


@dataclass(frozen=True)
class _ExecutableBinding:
    path: Path
    identity: FileIdentity
    sha256: str
    aliases: tuple[str, ...]


def _windows_acl_has_unprotected_write(path: Path) -> bool:
    """Inspect the DACL; privileged platform principals may retain mutation."""

    from ctypes import wintypes

    class _ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    class _ACCESS_ALLOWED_ACE(ctypes.Structure):
        _fields_ = [
            ("Header", _ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_int,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = int(
        advapi32.GetNamedSecurityInfoW(
            os.fspath(path),
            1,
            0x00000004,
            None,
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
    )
    if status != 0 or not dacl or not descriptor:
        _integrity("trusted_git_acl_unavailable", phase="git")
    protected = {
        "S-1-5-18",  # LOCAL_SYSTEM
        "S-1-5-32-544",  # BUILTIN\\Administrators
        # NT SERVICE\\TrustedInstaller
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
    }
    write_mask = 0x00000002 | 0x00000004 | 0x00000010 | 0x00000100
    write_mask |= 0x00010000 | 0x00040000 | 0x00080000 | 0x10000000 | 0x40000000
    try:
        info = _ACL_SIZE_INFORMATION()
        if not advapi32.GetAclInformation(
            dacl, ctypes.byref(info), ctypes.sizeof(info), 2
        ):
            _integrity("trusted_git_acl_unavailable", phase="git")
        for index in range(int(info.AceCount)):
            pointer = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(pointer)):
                _integrity("trusted_git_acl_unavailable", phase="git")
            ace = ctypes.cast(pointer, ctypes.POINTER(_ACCESS_ALLOWED_ACE)).contents
            if ace.Header.AceFlags & 0x08:
                continue
            if not (int(ace.Mask) & write_mask):
                continue
            if ace.Header.AceType != 0:
                return True
            sid_pointer = ctypes.c_void_p(
                int(pointer.value) + _ACCESS_ALLOWED_ACE.SidStart.offset
            )
            rendered = ctypes.c_wchar_p()
            if not advapi32.ConvertSidToStringSidW(
                sid_pointer, ctypes.byref(rendered)
            ):
                _integrity("trusted_git_acl_unavailable", phase="git")
            try:
                if rendered.value not in protected:
                    return True
            finally:
                kernel32.LocalFree(ctypes.cast(rendered, wintypes.HLOCAL))
        return False
    finally:
        kernel32.LocalFree(ctypes.cast(descriptor, wintypes.HLOCAL))


def _windows_hardlink_names(path: Path, expected_links: int) -> tuple[Path, ...]:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.FindFirstFileNameW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
    ]
    kernel32.FindFirstFileNameW.restype = wintypes.HANDLE
    kernel32.FindNextFileNameW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
    ]
    kernel32.FindNextFileNameW.restype = wintypes.BOOL
    kernel32.FindClose.argtypes = [wintypes.HANDLE]
    kernel32.FindClose.restype = wintypes.BOOL
    capacity = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(capacity.value)
    handle = kernel32.FindFirstFileNameW(
        os.fspath(path), 0, ctypes.byref(capacity), buffer
    )
    if handle == wintypes.HANDLE(-1).value:
        _integrity("trusted_git_hardlinks_unavailable", phase="git")
    names: list[Path] = []
    try:
        while True:
            relative = buffer.value
            if not relative.startswith("\\") or "\x00" in relative:
                _integrity("trusted_git_hardlink_invalid", phase="git")
            names.append(_absolute_lexical(Path(path.anchor) / relative.lstrip("\\")))
            capacity = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(capacity.value)
            if kernel32.FindNextFileNameW(handle, ctypes.byref(capacity), buffer):
                continue
            if ctypes.get_last_error() != 38:  # ERROR_HANDLE_EOF
                _integrity("trusted_git_hardlinks_unavailable", phase="git")
            break
    finally:
        if not kernel32.FindClose(handle):
            _integrity("trusted_git_hardlinks_unavailable", phase="git")
    unique = {os.path.normcase(os.path.normpath(os.fspath(item))) for item in names}
    if len(names) != expected_links or len(unique) != expected_links:
        _integrity("trusted_git_hardlink_count_mismatch", phase="git")
    return tuple(sorted(names, key=lambda item: os.path.normcase(os.fspath(item))))


def _bind_trusted_git() -> _ExecutableBinding:
    candidate = _absolute_lexical(_trusted_git_literal())
    _assert_plain_directory_chain(candidate.parent)
    try:
        info = os.lstat(candidate)
    except OSError:
        _integrity("trusted_git_unavailable", phase="git")
    if not stat.S_ISREG(info.st_mode) or _stat_is_reparse(info) or os.path.islink(candidate):
        _integrity("trusted_git_not_plain", phase="git")
    if sys.platform == "win32":
        protected = _absolute_lexical(Path(r"C:\Program Files\Git\mingw64"))
        protected_key = tuple(
            os.path.normcase(part) for part in protected.resolve(strict=True).parts
        )
        aliases = _windows_hardlink_names(candidate, int(info.st_nlink))
        fixed_present = False
        first_identity: FileIdentity | None = None
        digest: str | None = None
        checked_directories: set[str] = set()
        for alias in aliases:
            alias_resolved = alias.resolve(strict=True)
            alias_key = tuple(os.path.normcase(part) for part in alias_resolved.parts)
            if alias_key[: len(protected_key)] != protected_key:
                _integrity("trusted_git_hardlink_outside_protected_prefix", phase="git")
            if os.path.normcase(os.fspath(alias)) == os.path.normcase(os.fspath(candidate)):
                fixed_present = True
            content, identity = _read_plain_file(alias, require_single_link=False)
            if first_identity is None:
                first_identity = identity
                digest = _sha256_bytes(content)
            elif identity != first_identity or _sha256_bytes(content) != digest:
                _integrity("trusted_git_hardlink_identity_mismatch", phase="git")
            if _windows_acl_has_unprotected_write(alias):
                _integrity("trusted_git_is_writable", phase="git")
            cursor = alias.parent
            while True:
                key = os.path.normcase(os.fspath(cursor))
                if key not in checked_directories:
                    if _windows_acl_has_unprotected_write(cursor):
                        _integrity("trusted_git_prefix_is_writable", phase="git")
                    checked_directories.add(key)
                if key == os.path.normcase(os.fspath(protected)):
                    break
                if cursor.parent == cursor:
                    _integrity("trusted_git_hardlink_outside_protected_prefix", phase="git")
                cursor = cursor.parent
        if not fixed_present or first_identity is None or digest is None:
            _integrity("trusted_git_fixed_name_missing", phase="git")
        return _ExecutableBinding(
            candidate,
            first_identity,
            digest,
            tuple(os.fspath(item) for item in aliases),
        )
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
        _integrity("trusted_git_is_not_os_protected", phase="git")
    for directory in _directory_chain(candidate.parent):
        directory_info = os.lstat(directory)
        if directory_info.st_uid != 0 or stat.S_IMODE(directory_info.st_mode) & 0o022:
            _integrity("trusted_git_prefix_is_not_os_protected", phase="git")
    content, identity = _read_plain_file(candidate, require_single_link=False)
    return _ExecutableBinding(candidate, identity, _sha256_bytes(content), (os.fspath(candidate),))


def _git_base_argv(executable: Path) -> list[str]:
    return [
        os.fspath(executable),
        "--no-pager",
        "--no-replace-objects",
        "--no-lazy-fetch",
        "--literal-pathspecs",
        "-c",
        "core.hooksPath=",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.attributesFile=",
    ]


def _index_lock_absent(path: Path) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        _integrity("git_index_lock_state_unreadable", phase="git")
    _integrity("git_index_lock_present", phase="git")


def _run_git(
    executable: Path,
    cwd: Path,
    *arguments: str,
    index_lock_path: Path | None = None,
) -> bytes:
    if index_lock_path is not None:
        _index_lock_absent(index_lock_path)
    binding = _bind_trusted_git()
    if not _same_identity_path(executable, binding.path):
        _integrity("trusted_git_argument_mismatch", phase="git")
    try:
        process = subprocess.Popen(
            [*_git_base_argv(executable), *arguments],
            cwd=cwd,
            env=fresh_process_environment(),
            shell=False,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        image = _process_image_path(process)
        if os.path.normcase(os.path.normpath(os.fspath(image))) != os.path.normcase(
            os.path.normpath(os.fspath(binding.path))
        ):
            process.kill()
            process.communicate()
            _integrity("trusted_git_process_image_mismatch", phase="git")
        image_bytes, image_identity = _read_plain_file(image, require_single_link=False)
        if image_identity != binding.identity or _sha256_bytes(image_bytes) != binding.sha256:
            process.kill()
            process.communicate()
            _integrity("trusted_git_process_image_changed", phase="git")
        stdout, _stderr = process.communicate(timeout=60)
        completed_returncode = process.returncode
    except BaseException as exc:
        if "process" in locals():
            with contextlib.suppress(BaseException):
                if process.poll() is None:
                    process.kill()
            with contextlib.suppress(BaseException):
                process.communicate()
        if isinstance(exc, EvidenceIntegrityError):
            raise
        raise EvidenceIntegrityError("trusted_git_execution_failed", phase="git") from exc
    after = _bind_trusted_git()
    if after != binding:
        _integrity("trusted_git_binding_changed", phase="git")
    if index_lock_path is not None:
        _index_lock_absent(index_lock_path)
    if type(completed_returncode) is not int or completed_returncode != 0:
        _integrity("trusted_git_command_failed", phase="git")
    return bytes(stdout)


def _process_image_path(process: subprocess.Popen[bytes]) -> Path:
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
        for attempt in range(4):
            # QueryFullProcessImageNameW can transiently fail immediately
            # after CreateProcess.  Each attempt owns fresh writable state so
            # the API cannot reuse a mutated length/buffer from a failed call.
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            ctypes.set_last_error(0)
            if kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                if size.value <= 0 or size.value >= len(buffer) or not buffer.value:
                    _integrity("process_image_unavailable", phase="isolation")
                return _absolute_lexical(buffer.value)
            if attempt != 3:
                time.sleep(0.002)
        _integrity("process_image_unavailable", phase="isolation")
    if sys.platform == "linux":
        try:
            return _absolute_lexical(os.readlink(f"/proc/{process.pid}/exe"))
        except OSError:
            _integrity("process_image_unavailable", phase="isolation")
    if sys.platform == "darwin":
        libc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        buffer = ctypes.create_string_buffer(4096)
        libc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        libc.proc_pidpath.restype = ctypes.c_int
        length = int(libc.proc_pidpath(process.pid, buffer, len(buffer)))
        if length <= 0:
            _integrity("process_image_unavailable", phase="isolation")
        return _absolute_lexical(buffer.value.decode("utf-8", errors="strict"))
    _integrity("unsupported_release_platform", phase="isolation")


def _one_git_line(value: bytes, *, reason: str) -> str:
    try:
        decoded = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _integrity(reason, phase="git")
    if not decoded.endswith("\n") or "\n" in decoded[:-1] or "\r" in decoded:
        _integrity(reason, phase="git")
    result = decoded[:-1]
    if not result:
        _integrity(reason, phase="git")
    return result


def _git_sha1_object_oid(kind: str, content: bytes) -> str:
    if kind not in {"blob", "tree", "commit"}:
        _integrity("unsupported_git_object_type", phase="git")
    header = f"{kind} {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _read_verified_git_object(
    executable: Path,
    root: Path,
    oid: str,
    kind: str,
    *,
    index_lock_path: Path,
) -> bytes:
    if _FULL_GIT_SHA_RE.fullmatch(oid) is None:
        _integrity("git_object_oid_invalid", phase="git")
    declared = _one_git_line(
        _run_git(
            executable,
            root,
            "cat-file",
            "-t",
            oid,
            index_lock_path=index_lock_path,
        ),
        reason="git_object_type_invalid",
    )
    if declared != kind:
        _integrity("git_object_type_mismatch", phase="git")
    content = _run_git(
        executable,
        root,
        "cat-file",
        kind,
        oid,
        index_lock_path=index_lock_path,
    )
    if _git_sha1_object_oid(kind, content) != oid:
        _integrity("git_object_oid_mismatch", phase="git")
    return content


def _validate_raw_index(index_bytes: bytes) -> None:
    """Validate the complete SHA-1 index v2/v3 structure and entry flags."""

    if len(index_bytes) < 12 + 20 or index_bytes[:4] != b"DIRC":
        _integrity("git_index_format_invalid", phase="git")
    version = int.from_bytes(index_bytes[4:8], "big")
    count = int.from_bytes(index_bytes[8:12], "big")
    if version not in {2, 3} or count > 10_000_000:
        _integrity("git_index_version_invalid", phase="git")
    if hashlib.sha1(index_bytes[:-20], usedforsecurity=False).digest() != index_bytes[-20:]:
        _integrity("git_index_checksum_invalid", phase="git")
    limit = len(index_bytes) - 20
    offset = 12
    seen: set[bytes] = set()
    for _ in range(count):
        start = offset
        if offset + 62 > limit:
            _integrity("git_index_entry_truncated", phase="git")
        mode = int.from_bytes(index_bytes[offset + 24 : offset + 28], "big")
        oid = index_bytes[offset + 40 : offset + 60]
        flags = int.from_bytes(index_bytes[offset + 60 : offset + 62], "big")
        offset += 62
        if mode not in {0o100644, 0o100755, 0o120000, 0o160000} or oid == b"\0" * 20:
            _integrity("git_index_entry_mode_invalid", phase="git")
        if flags & 0x8000 or flags & 0x3000:
            _integrity("git_index_entry_flags_invalid", phase="git")
        extended = bool(flags & 0x4000)
        if extended:
            if version < 3 or offset + 2 > limit:
                _integrity("git_index_extended_flags_invalid", phase="git")
            extended_flags = int.from_bytes(index_bytes[offset : offset + 2], "big")
            offset += 2
            # No skip-worktree, intent-to-add, or future extended semantics.
            if extended_flags != 0:
                _integrity("git_index_extended_flags_invalid", phase="git")
        nul = index_bytes.find(b"\0", offset, limit)
        if nul < 0:
            _integrity("git_index_path_invalid", phase="git")
        path = index_bytes[offset:nul]
        declared_length = flags & 0x0FFF
        if (
            not path
            or b"\0" in path
            or path in seen
            or (declared_length < 0x0FFF and declared_length != len(path))
        ):
            _integrity("git_index_path_invalid", phase="git")
        try:
            validate_repo_relpath(path.decode("utf-8", errors="strict"))
        except (UnicodeError, EvidenceIntegrityError):
            _integrity("git_index_path_invalid", phase="git")
        seen.add(path)
        offset = nul + 1
        padding = (8 - ((offset - start) % 8)) % 8
        if offset + padding > limit or any(index_bytes[offset : offset + padding]):
            _integrity("git_index_padding_invalid", phase="git")
        offset += padding
    known_extensions = {b"TREE", b"REUC", b"UNTR", b"FSMN", b"EOIE", b"IEOT"}
    forbidden_extensions = {b"link", b"sdir"}
    while offset < limit:
        if offset + 8 > limit:
            _integrity("git_index_extension_truncated", phase="git")
        signature = index_bytes[offset : offset + 4]
        length = int.from_bytes(index_bytes[offset + 4 : offset + 8], "big")
        offset += 8
        if offset + length > limit:
            _integrity("git_index_extension_truncated", phase="git")
        if signature in forbidden_extensions:
            _integrity("git_index_sparse_or_split", phase="git")
        if signature not in known_extensions and signature[:1].islower():
            _integrity("git_index_unknown_mandatory_extension", phase="git")
        offset += length
    if offset != limit:
        _integrity("git_index_format_invalid", phase="git")


def _reject_git_alternates(root: Path, git_executable: Path) -> None:
    git_dir_text = _one_git_line(
        _run_git(
            git_executable,
            root,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ),
        reason="git_common_dir_invalid",
    )
    git_dir = _absolute_lexical(git_dir_text)
    _assert_plain_directory_chain(git_dir)
    alternates = git_dir / "objects" / "info" / "alternates"
    try:
        os.lstat(alternates)
    except FileNotFoundError:
        return
    except OSError:
        _integrity("git_alternates_state_unknown", phase="git")
    _integrity("git_alternates_forbidden", phase="git")


def _reject_git_drivers_and_promisors(root: Path, git_executable: Path) -> None:
    raw = _run_git(git_executable, root, "config", "--local", "--null", "--list")
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    for record in records:
        try:
            decoded = record.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            _integrity("git_config_output_invalid", phase="git")
        key = decoded.split("\n", 1)[0].casefold()
        if (
            key.startswith("filter.")
            or (key.startswith("diff.") and key.endswith((".command", ".textconv")))
            or key == "extensions.partialclone"
            or (key.startswith("remote.") and key.endswith(".promisor"))
        ):
            _integrity("git_driver_or_promisor_forbidden", phase="git")


def _reject_source_attributes(
    root: Path,
    git_executable: Path,
    relpath: str,
    *,
    index_lock_path: Path,
) -> None:
    raw = _run_git(
        git_executable,
        root,
        "check-attr",
        "-z",
        "--all",
        "--",
        relpath,
        index_lock_path=index_lock_path,
    )
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    if len(records) % 3:
        _integrity("git_attributes_output_invalid", phase="source")
    forbidden = {
        "filter",
        "diff",
        "text",
        "eol",
        "working-tree-encoding",
    }
    for position in range(0, len(records), 3):
        try:
            path = records[position].decode("utf-8", errors="strict")
            name = records[position + 1].decode("utf-8", errors="strict")
            value = records[position + 2].decode("utf-8", errors="strict")
        except UnicodeError:
            _integrity("git_attributes_output_invalid", phase="source")
        if path != relpath:
            _integrity("git_attributes_output_invalid", phase="source")
        if name in forbidden and value not in {"unspecified", "unset"}:
            _integrity("tracked_source_attributes_forbidden", phase="source")


def _portable_identity(info: os.stat_result) -> FileIdentity:
    return FileIdentity(
        scheme="posix-v1" if os.name != "nt" else "windows-stat-v1",
        volume=int(info.st_dev),
        file_id=int(info.st_ino),
        size=int(info.st_size),
        mtime_ns=int(info.st_mtime_ns),
        nlink=int(info.st_nlink),
    )


def _read_plain_file(path: Path, *, require_single_link: bool = True) -> tuple[bytes, FileIdentity]:
    if os.name == "nt":
        return _read_plain_file_windows(path, require_single_link=require_single_link)
    _assert_plain_directory_chain(path.parent)
    try:
        before = os.lstat(path)
    except OSError:
        _integrity("plain_file_unreadable", phase="path")
    if (
        not stat.S_ISREG(before.st_mode)
        or _stat_is_reparse(before)
        or os.path.islink(path)
        or (require_single_link and before.st_nlink != 1)
    ):
        _integrity("file_not_plain_single_link", phase="path")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _integrity("plain_file_open_failed", phase="path")
    try:
        opened = os.fstat(descriptor)
        after_open = os.lstat(path)
        if (
            not os.path.samestat(before, opened)
            or not os.path.samestat(opened, after_open)
            or _stat_is_reparse(after_open)
            or (require_single_link and opened.st_nlink != 1)
        ):
            _integrity("plain_file_identity_changed", phase="path")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_read = os.fstat(descriptor)
        final_path = os.lstat(path)
        if (
            not os.path.samestat(opened, after_read)
            or not os.path.samestat(after_read, final_path)
            or after_read.st_size != opened.st_size
            or after_read.st_mtime_ns != opened.st_mtime_ns
        ):
            _integrity("plain_file_changed_during_read", phase="path")
        return b"".join(chunks), _portable_identity(opened)
    finally:
        os.close(descriptor)


def _read_plain_file_windows(
    path: Path,
    *,
    require_single_link: bool,
) -> tuple[bytes, FileIdentity]:
    from ctypes import wintypes

    _assert_plain_directory_chain(path.parent)
    absolute = _absolute_lexical(path)
    try:
        before = os.lstat(absolute)
    except OSError:
        _integrity("plain_file_unreadable", phase="path")
    if not stat.S_ISREG(before.st_mode) or _stat_is_reparse(before):
        _integrity("file_not_plain_single_link", phase="path")
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
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        os.fspath(absolute),
        0x80000000,
        0x00000001,
        None,
        3,
        0x00200000,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        _integrity("plain_file_open_failed", phase="path")
    try:
        identity, attributes, links = _windows_handle_identity(handle)
        if attributes & 0x10 or attributes & 0x400 or (require_single_link and links != 1):
            _integrity("file_not_plain_single_link", phase="path")
        final_name = _windows_final_path(handle)
        if os.path.normcase(final_name) != os.path.normcase(os.path.normpath(os.fspath(absolute))):
            _integrity("plain_file_path_alias", phase="path")
        chunks: list[bytes] = []
        while True:
            buffer = ctypes.create_string_buffer(1024 * 1024)
            read = wintypes.DWORD()
            if not kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(read), None):
                _integrity("plain_file_read_failed", phase="path")
            if read.value == 0:
                break
            chunks.append(buffer.raw[: read.value])
        identity_after, attributes_after, links_after = _windows_handle_identity(handle)
        final = os.lstat(absolute)
        if (
            identity_after != identity
            or attributes_after != attributes
            or links_after != links
            or not os.path.samestat(before, final)
            or final.st_size != before.st_size
            or final.st_mtime_ns != before.st_mtime_ns
        ):
            _integrity("plain_file_changed_during_read", phase="path")
        content = b"".join(chunks)
        if len(content) != before.st_size:
            _integrity("plain_file_changed_during_read", phase="path")
        return content, FileIdentity(
            scheme="windows-file-id-v2",
            volume=identity[0],
            file_id=identity[1],
            size=int(before.st_size),
            mtime_ns=int(before.st_mtime_ns),
            nlink=links,
        )
    finally:
        if not kernel32.CloseHandle(handle) and sys.exc_info()[0] is None:
            _integrity("plain_file_close_failed", phase="path")


def _parse_stage_record(raw: bytes, expected_relpath: str) -> tuple[str, str]:
    records = [record for record in raw.split(b"\x00") if record]
    if len(records) != 1 or b"\t" not in records[0]:
        _integrity("tracked_source_stage_is_ambiguous", phase="source")
    metadata, raw_path = records[0].split(b"\t", 1)
    fields = metadata.split(b" ")
    if len(fields) != 3:
        _integrity("tracked_source_stage_is_malformed", phase="source")
    try:
        mode = fields[0].decode("ascii")
        oid = fields[1].decode("ascii")
        stage = fields[2].decode("ascii")
        relpath = raw_path.decode("utf-8", errors="strict")
    except UnicodeError:
        _integrity("tracked_source_stage_is_malformed", phase="source")
    if mode != "100644" or stage != "0":
        _integrity("tracked_source_mode_or_stage_invalid", phase="source")
    if _FULL_GIT_SHA_RE.fullmatch(oid) is None or relpath != expected_relpath:
        _integrity("tracked_source_stage_is_malformed", phase="source")
    return mode, oid


def _freeze_tracked_blob(
    *,
    root: Path,
    git_executable: Path,
    index_lock_path: Path,
    relpath: str,
) -> TrackedBlob:
    stage = _run_git(
        git_executable,
        root,
        "ls-files",
        "--stage",
        "-z",
        "--",
        relpath,
        index_lock_path=index_lock_path,
    )
    mode, oid = _parse_stage_record(stage, relpath)
    _reject_source_attributes(
        root,
        git_executable,
        relpath,
        index_lock_path=index_lock_path,
    )
    blob = _read_verified_git_object(
        git_executable,
        root,
        oid,
        "blob",
        index_lock_path=index_lock_path,
    )
    canonical_blob = _canonical_source_bytes(blob)
    if canonical_blob.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
        _integrity("git_lfs_pointer_forbidden", phase="source")
    checkout, identity = _read_plain_file(root / Path(*PurePosixPath(relpath).parts))
    if _canonical_source_bytes(checkout) != blob:
        _integrity("tracked_source_differs_from_frozen_blob", phase="source")
    return TrackedBlob(
        relpath=relpath,
        mode=mode,
        oid=oid,
        sha256=_sha256_bytes(blob),
        content=blob,
        checkout_identity=identity,
    )


def _nul_git_paths(raw: bytes) -> tuple[str, ...]:
    records = raw.split(b"\x00")
    if records and records[-1] == b"":
        records.pop()
    paths: list[str] = []
    for record in records:
        try:
            decoded = record.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            _integrity("git_path_output_malformed", phase="git")
        paths.append(validate_repo_relpath(decoded))
    return tuple(paths)


def _worktree_changed_paths(
    executable: Path,
    root: Path,
    *,
    index_lock_path: Path,
) -> tuple[str, ...]:
    commands = (
        ("diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv", "--"),
        (
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        ),
        ("ls-files", "--others", "--exclude-standard", "-z", "--"),
    )
    paths: list[str] = []
    for command in commands:
        paths.extend(
            _nul_git_paths(
                _run_git(
                    executable,
                    root,
                    *command,
                    index_lock_path=index_lock_path,
                )
            )
        )
    if len(set(paths)) != len(paths):
        paths = list(dict.fromkeys(paths))
    return tuple(paths)


def _relative_to_root(root: Path, path: Path) -> str:
    absolute = _absolute_lexical(path)
    try:
        relative = absolute.relative_to(root)
    except ValueError:
        _integrity("path_is_outside_repository", phase="path")
    return validate_repo_relpath(PurePosixPath(*relative.parts).as_posix())


def capture_repository_snapshot(
    *,
    startup_cwd: Path | str,
    required_files: Sequence[str],
) -> RepositorySnapshot:
    """Bind one clean worktree, index, and exact set of tracked source blobs."""

    git_executable = _trusted_git_executable()
    cwd = _absolute_lexical(startup_cwd)
    _assert_plain_directory_chain(cwd)
    root_text = _one_git_line(
        _run_git(git_executable, cwd, "rev-parse", "--show-toplevel"),
        reason="git_toplevel_is_invalid",
    )
    root = _absolute_lexical(root_text)
    _assert_plain_directory_chain(root)
    if not path_is_within(cwd, root):
        _integrity("startup_cwd_is_outside_repository", phase="git")
    index_text = _one_git_line(
        _run_git(
            git_executable,
            root,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "index",
        ),
        reason="git_index_path_is_invalid",
    )
    lock_text = _one_git_line(
        _run_git(
            git_executable,
            root,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "index.lock",
        ),
        reason="git_index_lock_path_is_invalid",
    )
    index_path = _absolute_lexical(index_text)
    index_lock_path = _absolute_lexical(lock_text)
    if index_path.parent != index_lock_path.parent:
        _integrity("git_index_paths_disagree", phase="git")
    _assert_plain_directory_chain(index_path.parent)
    _index_lock_absent(index_lock_path)
    object_format = _one_git_line(
        _run_git(git_executable, root, "rev-parse", "--show-object-format"),
        reason="git_object_format_invalid",
    )
    if object_format != "sha1":
        _integrity("git_object_format_not_sha1", phase="git")
    _reject_git_alternates(root, git_executable)
    _reject_git_drivers_and_promisors(root, git_executable)
    head = _one_git_line(
        _run_git(
            git_executable,
            root,
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
            index_lock_path=index_lock_path,
        ),
        reason="git_head_is_invalid",
    )
    tree = _one_git_line(
        _run_git(
            git_executable,
            root,
            "rev-parse",
            "--verify",
            "HEAD^{tree}",
            index_lock_path=index_lock_path,
        ),
        reason="git_tree_is_invalid",
    )
    if _FULL_GIT_SHA_RE.fullmatch(head) is None or _FULL_GIT_SHA_RE.fullmatch(tree) is None:
        _integrity("git_snapshot_ids_invalid", phase="git")
    raw_head = _one_git_line(
        _run_git(
            git_executable,
            root,
            "rev-parse",
            "--verify",
            "HEAD",
            index_lock_path=index_lock_path,
        ),
        reason="git_head_is_invalid",
    )
    if raw_head != head:
        _integrity("git_head_is_not_exact_commit", phase="git")
    commit_content = _read_verified_git_object(
        git_executable,
        root,
        head,
        "commit",
        index_lock_path=index_lock_path,
    )
    first_line = commit_content.split(b"\n", 1)[0]
    if first_line != b"tree " + tree.encode("ascii"):
        _integrity("git_commit_tree_mismatch", phase="git")
    _read_verified_git_object(
        git_executable,
        root,
        tree,
        "tree",
        index_lock_path=index_lock_path,
    )
    manifest = _run_git(
        git_executable,
        root,
        "ls-files",
        "--stage",
        "-z",
        index_lock_path=index_lock_path,
    )
    if _worktree_changed_paths(
        git_executable, root, index_lock_path=index_lock_path
    ):
        _integrity("repository_is_not_clean", phase="git")
    index_bytes, index_identity = _read_plain_file(index_path)
    _validate_raw_index(index_bytes)
    paths = validate_repo_relpath_list(tuple(required_files))
    if RUNTIME_RELPATH not in paths:
        _integrity("runtime_source_not_required", phase="source")
    blobs = tuple(
        _freeze_tracked_blob(
            root=root,
            git_executable=git_executable,
            index_lock_path=index_lock_path,
            relpath=relpath,
        )
        for relpath in sorted(paths)
    )
    snapshot = RepositorySnapshot(
        root=root,
        startup_cwd=cwd,
        git_executable=git_executable,
        head=head,
        tree=tree,
        index_path=index_path,
        index_lock_path=index_lock_path,
        index_sha256=_sha256_bytes(manifest),
        index_file_sha256=_sha256_bytes(index_bytes),
        index_identity=index_identity,
        tracked_blobs=blobs,
        git_sha256=_bind_trusted_git().sha256,
    )
    revalidate_repository_snapshot(snapshot)
    return snapshot


def revalidate_repository_snapshot(
    snapshot: RepositorySnapshot,
    *,
    allowed_changed_paths: Sequence[Path | str] = (),
) -> None:
    git_binding = _bind_trusted_git()
    if (
        not _same_identity_path(git_binding.path, snapshot.git_executable)
        or git_binding.sha256 != snapshot.git_sha256
    ):
        _integrity("trusted_git_binding_changed", phase="git")
    _reject_git_alternates(snapshot.root, snapshot.git_executable)
    _reject_git_drivers_and_promisors(snapshot.root, snapshot.git_executable)
    _index_lock_absent(snapshot.index_lock_path)
    head = _one_git_line(
        _run_git(
            snapshot.git_executable,
            snapshot.root,
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
            index_lock_path=snapshot.index_lock_path,
        ),
        reason="git_head_is_invalid",
    )
    tree = _one_git_line(
        _run_git(
            snapshot.git_executable,
            snapshot.root,
            "rev-parse",
            "--verify",
            "HEAD^{tree}",
            index_lock_path=snapshot.index_lock_path,
        ),
        reason="git_tree_is_invalid",
    )
    if head != snapshot.head or tree != snapshot.tree:
        _integrity("repository_head_or_tree_changed", phase="git")
    commit_content = _read_verified_git_object(
        snapshot.git_executable,
        snapshot.root,
        head,
        "commit",
        index_lock_path=snapshot.index_lock_path,
    )
    if commit_content.split(b"\n", 1)[0] != b"tree " + tree.encode("ascii"):
        _integrity("git_commit_tree_mismatch", phase="git")
    _read_verified_git_object(
        snapshot.git_executable,
        snapshot.root,
        tree,
        "tree",
        index_lock_path=snapshot.index_lock_path,
    )
    manifest = _run_git(
        snapshot.git_executable,
        snapshot.root,
        "ls-files",
        "--stage",
        "-z",
        index_lock_path=snapshot.index_lock_path,
    )
    if _sha256_bytes(manifest) != snapshot.index_sha256:
        _integrity("git_index_manifest_changed", phase="git")
    index_bytes, index_identity = _read_plain_file(snapshot.index_path)
    _validate_raw_index(index_bytes)
    if (
        index_identity != snapshot.index_identity
        or _sha256_bytes(index_bytes) != snapshot.index_file_sha256
    ):
        _integrity("git_index_changed", phase="git")
    allowed = {
        _relative_to_root(snapshot.root, _absolute_lexical(path))
        for path in allowed_changed_paths
    }
    changed = set(
        _worktree_changed_paths(
            snapshot.git_executable,
            snapshot.root,
            index_lock_path=snapshot.index_lock_path,
        )
    )
    if not changed.issubset(allowed):
        _integrity("repository_worktree_changed", phase="git")
    for frozen in snapshot.tracked_blobs:
        checkout, identity = _read_plain_file(
            snapshot.root / Path(*PurePosixPath(frozen.relpath).parts)
        )
        if identity != frozen.checkout_identity:
            _integrity("tracked_source_identity_changed", phase="source")
        if _canonical_source_bytes(checkout) != frozen.content:
            _integrity("tracked_source_bytes_changed", phase="source")
    _index_lock_absent(snapshot.index_lock_path)


def verify_executing_source(
    snapshot: RepositorySnapshot,
    *,
    declared_relpath: str,
    executing_file: Path | str,
) -> TrackedBlob:
    relpath = validate_repo_relpath(declared_relpath)
    expected = snapshot.root / Path(*PurePosixPath(relpath).parts)
    actual = _absolute_lexical(executing_file)
    if not _same_identity_path(actual, expected):
        _integrity("executing_source_path_mismatch", phase="source")
    frozen = snapshot.blob(relpath)
    checkout, identity = _read_plain_file(actual)
    if identity != frozen.checkout_identity or _canonical_source_bytes(checkout) != frozen.content:
        _integrity("executing_source_not_frozen", phase="source")
    return frozen


def exec_verified_source(
    snapshot: RepositorySnapshot,
    *,
    relpath: str,
    module_name: str,
) -> dict[str, Any]:
    """Compile and execute verified Git-blob bytes without import/pyc fallback."""

    frozen = snapshot.blob(relpath)
    filename = os.fspath(snapshot.root / Path(*PurePosixPath(relpath).parts))
    try:
        code = compile(frozen.content, filename, "exec", dont_inherit=True, optimize=0)
        namespace: dict[str, Any] = {
            "__name__": module_name,
            "__file__": filename,
            "__package__": module_name.rpartition(".")[0],
            "__builtins__": __builtins__,
        }
        exec(code, namespace, namespace)
    except BaseException as exc:
        raise EvidenceIntegrityError("verified_source_execution_failed", phase="source") from exc
    return namespace


def _plain_json(value: Any) -> Any:
    if value is None or type(value) in {str, bool, int}:
        if type(value) is str:
            _validate_unicode_text(value, reason="invalid_json_text")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _integrity("nonfinite_json_number", phase="envelope")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key = _validate_unicode_text(key, reason="invalid_json_key")
            if key in result:
                _integrity("duplicate_json_key", phase="envelope")
            result[key] = _plain_json(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    _integrity("non_json_value", phase="envelope")


def build_completion_envelope(
    *,
    snapshot: RepositorySnapshot,
    spec: ProducerSpec,
    outcome: ProducerOutcome,
) -> dict[str, Any]:
    if outcome.status not in {"pass", "hold_nonpass"}:
        _integrity("invalid_completion_status", phase="envelope")
    reasons = tuple(_validate_reason_code(reason) for reason in outcome.reason_codes)
    if tuple(sorted(set(reasons))) != reasons:
        _integrity("reason_codes_not_sorted_unique", phase="envelope")
    findings = tuple(_plain_json(finding) for finding in outcome.findings)
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {"reason_code", "details"}:
            _integrity("invalid_finding_shape", phase="envelope")
        _validate_reason_code(finding["reason_code"])
        if not isinstance(finding["details"], dict) or not finding["details"]:
            _integrity("invalid_finding_shape", phase="envelope")
    finding_reasons = tuple(finding["reason_code"] for finding in findings)
    if outcome.status == "pass":
        if (
            reasons
            or findings
            or not isinstance(outcome.evidence, Mapping)
            or not outcome.evidence
        ):
            _integrity("invalid_pass_completion", phase="envelope")
        evidence: dict[str, Any] | None = _plain_json(outcome.evidence)
    else:
        if (
            not reasons
            or frozenset(reasons) not in spec.allowed_hold_reason_sets
            or len(findings) != len(reasons)
            or finding_reasons != reasons
            or outcome.evidence is not None
        ):
            _integrity("invalid_hold_completion", phase="envelope")
        evidence = None
    source_files = [
        {
            "path": item.relpath,
            "mode": item.mode,
            "blob_oid": item.oid,
            "sha256": item.sha256,
        }
        for item in sorted(snapshot.tracked_blobs, key=lambda item: item.relpath)
    ]
    envelope = {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "producer_id": spec.producer_id,
        "status": outcome.status,
        "reason_codes": list(reasons),
        "source": {
            "commit": snapshot.head,
            "tree": snapshot.tree,
            "index_sha256": snapshot.index_sha256,
            "text_normalization": SOURCE_NORMALIZATION,
            "files": source_files,
        },
        "evidence": evidence,
        "findings": list(findings),
    }
    return _plain_json(envelope)


def serialize_completion_envelope(envelope: Mapping[str, Any]) -> bytes:
    plain = _plain_json(envelope)
    try:
        encoded = json.dumps(
            plain,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError, UnicodeError) as exc:
        raise EvidenceIntegrityError("completion_serialization_failed", phase="envelope") from exc
    return encoded


def _strict_json_loads(raw: bytes, *, reason: str) -> Any:
    if type(raw) is not bytes or not raw or len(raw) > _FRAME_LIMIT:
        _integrity(reason, phase="envelope")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _integrity(reason, phase="envelope")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if type(key) is not str or key in result:
                raise ValueError("duplicate or invalid key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("nonfinite number")

    try:
        return json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise EvidenceIntegrityError(reason, phase="envelope") from exc


def _expected_source(snapshot: RepositorySnapshot) -> dict[str, Any]:
    return {
        "commit": snapshot.head,
        "tree": snapshot.tree,
        "index_sha256": snapshot.index_sha256,
        "text_normalization": SOURCE_NORMALIZATION,
        "files": [
            {
                "path": item.relpath,
                "mode": item.mode,
                "blob_oid": item.oid,
                "sha256": item.sha256,
            }
            for item in sorted(snapshot.tracked_blobs, key=lambda item: item.relpath)
        ],
    }


def _validate_completion_bytes(
    content: bytes,
    snapshot: RepositorySnapshot,
    spec: ProducerSpec,
) -> dict[str, Any]:
    """Independently validate canonical bytes at the parent trust boundary."""

    value = _strict_json_loads(content, reason="completion_json_invalid")
    if type(value) is not dict or set(value) != {
        "schema_version",
        "producer_id",
        "status",
        "reason_codes",
        "source",
        "evidence",
        "findings",
    }:
        _integrity("completion_shape_invalid", phase="envelope")
    if (
        value["schema_version"] != ENVELOPE_SCHEMA_VERSION
        or value["producer_id"] != spec.producer_id
        or value["source"] != _expected_source(snapshot)
    ):
        _integrity("completion_binding_invalid", phase="envelope")
    reasons_raw = value["reason_codes"]
    findings_raw = value["findings"]
    if type(reasons_raw) is not list or type(findings_raw) is not list:
        _integrity("completion_shape_invalid", phase="envelope")
    reasons = tuple(_validate_reason_code(item) for item in reasons_raw)
    if reasons != tuple(sorted(set(reasons))):
        _integrity("reason_codes_not_sorted_unique", phase="envelope")
    if value["status"] == "pass":
        if (
            reasons
            or findings_raw
            or type(value["evidence"]) is not dict
            or not value["evidence"]
        ):
            _integrity("invalid_pass_completion", phase="envelope")
    elif value["status"] == "hold_nonpass":
        if (
            not reasons
            or frozenset(reasons) not in spec.allowed_hold_reason_sets
            or value["evidence"] is not None
            or len(findings_raw) != len(reasons)
        ):
            _integrity("invalid_hold_completion", phase="envelope")
        for reason, finding in zip(reasons, findings_raw, strict=True):
            if (
                type(finding) is not dict
                or set(finding) != {"reason_code", "details"}
                or finding["reason_code"] != reason
                or type(finding["details"]) is not dict
                or not finding["details"]
            ):
                _integrity("invalid_finding_shape", phase="envelope")
    else:
        _integrity("invalid_completion_status", phase="envelope")
    _plain_json(value)
    if serialize_completion_envelope(value) != content:
        _integrity("completion_not_canonical_json", phase="envelope")
    return value


def _windows_known_local_appdata() -> Path:
    if os.name != "nt":
        _integrity("windows_known_folder_requested_off_windows")
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    folder_id = _GUID(
        0xF1B32785,
        0x6FBA,
        0x4FCF,
        (ctypes.c_ubyte * 8)(0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91),
    )
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    result = ctypes.c_wchar_p()
    status = int(shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(result)))
    if status != 0 or not result.value:
        _integrity("local_appdata_unavailable", phase="isolation")
    try:
        return _absolute_lexical(result.value)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(result, ctypes.c_void_p))


def _windows_current_user_sid_buffer() -> tuple[ctypes.Array[Any], ctypes.c_void_p]:
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        _integrity("current_user_sid_unavailable", phase="isolation")
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if needed.value == 0:
            _integrity("current_user_sid_unavailable", phase="isolation")
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token,
            1,
            buffer,
            needed,
            ctypes.byref(needed),
        ):
            _integrity("current_user_sid_unavailable", phase="isolation")
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not sid_pointer:
            _integrity("current_user_sid_unavailable", phase="isolation")
        return buffer, ctypes.c_void_p(sid_pointer)
    finally:
        kernel32.CloseHandle(token)


def _windows_sid_string(sid_pointer: ctypes.c_void_p) -> str:
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    rendered = ctypes.c_wchar_p()
    if not advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(rendered)):
        _integrity("current_user_sid_unavailable", phase="isolation")
    try:
        if not rendered.value:
            _integrity("current_user_sid_unavailable", phase="isolation")
        return rendered.value
    finally:
        kernel32.LocalFree(ctypes.cast(rendered, wintypes.HLOCAL))


def _windows_create_owner_only_directory(path: Path) -> bool:
    from ctypes import wintypes

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    sid_buffer, sid_pointer = _windows_current_user_sid_buffer()
    sid = _windows_sid_string(sid_pointer)
    del sid_buffer  # the rendered SID above is now an owned Python value
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel32.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_SECURITY_ATTRIBUTES)]
    kernel32.CreateDirectoryW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    descriptor = wintypes.LPVOID()
    descriptor_size = wintypes.DWORD()
    sddl = f"O:{sid}D:P(A;;FA;;;{sid})"
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        _integrity("owner_only_acl_creation_failed", phase="isolation")
    try:
        attributes = _SECURITY_ATTRIBUTES(
            ctypes.sizeof(_SECURITY_ATTRIBUTES),
            descriptor,
            False,
        )
        created = bool(
            kernel32.CreateDirectoryW(os.fspath(path), ctypes.byref(attributes))
        )
        if not created:
            error = ctypes.get_last_error()
            if error != 183:  # ERROR_ALREADY_EXISTS
                _integrity("owner_only_directory_creation_failed", phase="isolation")
        return created
    finally:
        kernel32.LocalFree(descriptor)


def _windows_owner_only_directory(path: Path) -> bool:
    from ctypes import wintypes

    class _ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    class _ACCESS_ALLOWED_ACE(ctypes.Structure):
        _fields_ = [
            ("Header", _ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_int,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    advapi32.EqualSid.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    sid_buffer, current_sid = _windows_current_user_sid_buffer()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    security_descriptor = ctypes.c_void_p()
    status = int(
        advapi32.GetNamedSecurityInfoW(
            os.fspath(path),
            1,
            0x00000001 | 0x00000004,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(security_descriptor),
        )
    )
    if status != 0 or not security_descriptor or not owner or not dacl:
        del sid_buffer
        return False
    try:
        if not advapi32.EqualSid(owner, current_sid):
            return False
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(
            security_descriptor,
            ctypes.byref(control),
            ctypes.byref(revision),
        ):
            return False
        if not (control.value & 0x1000):  # SE_DACL_PROTECTED
            return False
        acl_info = _ACL_SIZE_INFORMATION()
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(acl_info),
            ctypes.sizeof(acl_info),
            2,
        ):
            return False
        if acl_info.AceCount != 1:
            return False
        ace_pointer = ctypes.c_void_p()
        if not advapi32.GetAce(dacl, 0, ctypes.byref(ace_pointer)):
            return False
        ace = ctypes.cast(ace_pointer, ctypes.POINTER(_ACCESS_ALLOWED_ACE)).contents
        ace_sid = ctypes.c_void_p(
            int(ace_pointer.value) + _ACCESS_ALLOWED_ACE.SidStart.offset
        )
        return (
            ace.Header.AceType == 0
            and (int(ace.Mask) & 0x001F01FF) == 0x001F01FF
            and bool(advapi32.EqualSid(ace_sid, current_sid))
        )
    finally:
        del sid_buffer
        kernel32.LocalFree(ctypes.cast(security_descriptor, wintypes.HLOCAL))


def _ensure_owner_only_directory(path: Path) -> None:
    if os.name == "nt":
        if not path.exists():
            _windows_create_owner_only_directory(path)
        _assert_plain_directory_chain(path)
        if not _windows_owner_only_directory(path):
            _integrity("scratch_directory_not_owner_only", phase="isolation")
        return
    try:
        os.mkdir(path, mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        _integrity("owner_only_directory_creation_failed", phase="isolation")
    _assert_plain_directory_chain(path)
    info = os.lstat(path)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        _integrity("scratch_directory_not_owner_only", phase="isolation")


def _fixed_scratch_base(bound_root: Path) -> Path:
    if sys.platform == "win32":
        local_appdata = _windows_known_local_appdata()
        _assert_plain_directory_chain(local_appdata)
        vendor = local_appdata / "WaggleDance"
        if not vendor.exists():
            _windows_create_owner_only_directory(vendor)
        _assert_plain_directory_chain(vendor)
        base = vendor / "ReleaseEvidenceTmp"
    elif sys.platform == "linux":
        base = Path("/var/tmp/waggledance-release-evidence")
    elif sys.platform == "darwin":
        base = Path("/private/var/tmp/waggledance-release-evidence")
    else:
        _integrity("unsupported_release_platform", phase="isolation")
    _ensure_owner_only_directory(base)
    if path_is_within(base, bound_root) or _same_identity_path(base, bound_root):
        _integrity("scratch_directory_inside_repository", phase="isolation")
    return base


def create_isolation_prefix(bound_root: Path) -> Path:
    base = _fixed_scratch_base(bound_root)
    for _ in range(32):
        candidate = base / f"run-{secrets.token_hex(16)}"
        try:
            if os.name == "nt":
                if not _windows_create_owner_only_directory(candidate):
                    continue
            else:
                os.mkdir(candidate, mode=0o700)
        except FileExistsError:
            continue
        _ensure_owner_only_directory(candidate)
        if any(candidate.iterdir()):
            _integrity("isolation_prefix_not_empty", phase="isolation")
        return candidate
    _integrity("isolation_prefix_collision", phase="isolation")


def scrub_and_validate_child_environment() -> None:
    """Require the exact empty-built environment; never silently scrub poison."""

    expected = fresh_process_environment()
    if os.name == "nt":
        actual = {key.upper(): value for key, value in os.environ.items()}
        normalized = {key.upper(): value for key, value in expected.items()}
    else:
        actual = dict(os.environ)
        normalized = expected
    if actual != normalized:
        _integrity("child_environment_not_fresh", phase="isolation")


def _identity_mapping(identity: FileIdentity) -> dict[str, Any]:
    file_id: int | str
    if type(identity.file_id) is bytes:
        file_id = base64.b64encode(identity.file_id).decode("ascii")
    elif type(identity.file_id) is int:
        file_id = identity.file_id
    else:
        _integrity("file_identity_invalid", phase="path")
    return {
        "scheme": identity.scheme,
        "volume": identity.volume,
        "file_id": file_id,
        "size": identity.size,
        "mtime_ns": identity.mtime_ns,
        "nlink": identity.nlink,
    }


def _exact_int(value: object, *, reason: str) -> int:
    if type(value) is not int:
        _integrity(reason, phase="isolation")
    return value


def _identity_from_mapping(value: object) -> FileIdentity:
    if type(value) is not dict or set(value) != {
        "scheme",
        "volume",
        "file_id",
        "size",
        "mtime_ns",
        "nlink",
    }:
        _integrity("file_identity_invalid", phase="path")
    scheme = _validate_unicode_text(value["scheme"], reason="file_identity_invalid")
    volume = _exact_int(value["volume"], reason="file_identity_invalid")
    size = _exact_int(value["size"], reason="file_identity_invalid")
    mtime_ns = _exact_int(value["mtime_ns"], reason="file_identity_invalid")
    nlink = _exact_int(value["nlink"], reason="file_identity_invalid")
    raw_id = value["file_id"]
    if type(raw_id) is int:
        file_id: int | bytes = _exact_int(raw_id, reason="file_identity_invalid")
    elif type(raw_id) is str:
        try:
            file_id = base64.b64decode(raw_id.encode("ascii"), validate=True)
        except (ValueError, UnicodeError):
            _integrity("file_identity_invalid", phase="path")
        if not file_id:
            _integrity("file_identity_invalid", phase="path")
    else:
        _integrity("file_identity_invalid", phase="path")
    if volume < 0 or size < 0 or mtime_ns < 0 or nlink < 1:
        _integrity("file_identity_invalid", phase="path")
    return FileIdentity(scheme, volume, file_id, size, mtime_ns, nlink)


def _current_process_image() -> Path:
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleFileNameW.argtypes = [
            wintypes.HMODULE,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        kernel32.GetModuleFileNameW.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = int(kernel32.GetModuleFileNameW(None, buffer, len(buffer)))
        if length <= 0 or length >= len(buffer):
            _integrity("python_process_image_unavailable", phase="isolation")
        return _absolute_lexical(buffer.value)
    if sys.platform == "linux":
        try:
            return _absolute_lexical(os.readlink("/proc/self/exe"))
        except OSError:
            _integrity("python_process_image_unavailable", phase="isolation")
    if sys.platform == "darwin":
        libc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        buffer = ctypes.create_string_buffer(4096)
        libc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        libc.proc_pidpath.restype = ctypes.c_int
        if int(libc.proc_pidpath(os.getpid(), buffer, len(buffer))) <= 0:
            _integrity("python_process_image_unavailable", phase="isolation")
        return _absolute_lexical(buffer.value.decode("utf-8", errors="strict"))
    _integrity("unsupported_release_platform", phase="isolation")


def _bind_current_interpreter() -> dict[str, Any]:
    image = _current_process_image()
    content, identity = _read_plain_file(image, require_single_link=False)
    version = [
        _exact_int(sys.version_info.major, reason="python_version_invalid"),
        _exact_int(sys.version_info.minor, reason="python_version_invalid"),
        _exact_int(sys.version_info.micro, reason="python_version_invalid"),
    ]
    cache_tag = getattr(sys.implementation, "cache_tag", None)
    if type(cache_tag) is not str or not cache_tag:
        _integrity("python_cache_tag_invalid", phase="isolation")
    return {
        "path": os.fspath(image),
        "identity": _identity_mapping(identity),
        "sha256": _sha256_bytes(content),
        "version": version,
        "base_prefix": os.fspath(_absolute_lexical(sys.base_prefix)),
        "cache_tag": cache_tag,
    }


def _spec_mapping(spec: ProducerSpec) -> dict[str, Any]:
    return {
        "producer_id": spec.producer_id,
        "producer_relpath": spec.producer_relpath,
        "canonical_output_relpath": spec.canonical_output_relpath,
        "argv_contract": {
            "mode": spec.argv_contract.mode,
            "exact_tokens": list(spec.argv_contract.exact_tokens),
        },
        "allowed_hold_reason_sets": [
            sorted(item)
            for item in sorted(
                spec.allowed_hold_reason_sets,
                key=lambda item: tuple(sorted(item)),
            )
        ],
    }


def _spec_from_mapping(value: object) -> ProducerSpec:
    if type(value) is not dict or set(value) != {
        "producer_id",
        "producer_relpath",
        "canonical_output_relpath",
        "argv_contract",
        "allowed_hold_reason_sets",
    }:
        _integrity("child_spec_invalid", phase="isolation")
    contract = value["argv_contract"]
    if type(contract) is not dict or set(contract) != {"mode", "exact_tokens"}:
        _integrity("child_spec_invalid", phase="isolation")
    tokens = contract["exact_tokens"]
    sets = value["allowed_hold_reason_sets"]
    if type(tokens) is not list or type(sets) is not list:
        _integrity("child_spec_invalid", phase="isolation")
    try:
        result = ProducerSpec(
            producer_id=value["producer_id"],
            producer_relpath=value["producer_relpath"],
            canonical_output_relpath=value["canonical_output_relpath"],
            argv_contract=SealedArgvContract(contract["mode"], tuple(tokens)),
            allowed_hold_reason_sets=frozenset(
                frozenset(_validate_reason_code(reason) for reason in reason_set)
                for reason_set in sets
                if type(reason_set) is list
            ),
        )
    except (TypeError, ValueError) as exc:
        raise EvidenceIntegrityError("child_spec_invalid", phase="isolation") from exc
    if _spec_mapping(result) != value:
        _integrity("child_spec_invalid", phase="isolation")
    return result


def _snapshot_frame_mapping(snapshot: RepositorySnapshot) -> dict[str, Any]:
    return {
        "root": os.fspath(snapshot.root),
        "startup_cwd": os.fspath(snapshot.startup_cwd),
        "git_sha256": snapshot.git_sha256,
        "source": _expected_source(snapshot),
        "blobs": [
            {
                "path": item.relpath,
                "content_b64": base64.b64encode(item.content).decode("ascii"),
            }
            for item in sorted(snapshot.tracked_blobs, key=lambda item: item.relpath)
        ],
    }


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    plain = _plain_json(value)
    try:
        return json.dumps(
            plain,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError, UnicodeError) as exc:
        raise EvidenceIntegrityError("canonical_json_failed", phase="isolation") from exc


def _prestate_mapping(state: tuple[bytes, FileIdentity] | None) -> dict[str, Any]:
    if state is None:
        return {"present": False, "content_b64": None, "identity": None}
    return {
        "present": True,
        "content_b64": base64.b64encode(state[0]).decode("ascii"),
        "identity": _identity_mapping(state[1]),
    }


def _prestate_from_mapping(value: object) -> tuple[bytes, FileIdentity] | None:
    if type(value) is not dict or set(value) != {"present", "content_b64", "identity"}:
        _integrity("canonical_prestate_invalid", phase="isolation")
    if type(value["present"]) is not bool:
        _integrity("canonical_prestate_invalid", phase="isolation")
    if not value["present"]:
        if value["content_b64"] is not None or value["identity"] is not None:
            _integrity("canonical_prestate_invalid", phase="isolation")
        return None
    if type(value["content_b64"]) is not str:
        _integrity("canonical_prestate_invalid", phase="isolation")
    try:
        content = base64.b64decode(value["content_b64"].encode("ascii"), validate=True)
    except (ValueError, UnicodeError):
        _integrity("canonical_prestate_invalid", phase="isolation")
    return content, _identity_from_mapping(value["identity"])


def _build_child_frame(
    *,
    snapshot: RepositorySnapshot,
    spec: ProducerSpec,
    validated_argv: Sequence[str],
    canonical_prestate: tuple[bytes, FileIdentity] | None = None,
    nonce: str | None = None,
    interpreter_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    nonce = secrets.token_hex(16) if nonce is None else nonce
    if type(nonce) is not str or _NONCE_RE.fullmatch(nonce) is None:
        _integrity("child_nonce_invalid", phase="isolation")
    frame = {
        "schema": _FRAME_SCHEMA,
        "nonce": nonce,
        "argv": list(validated_argv),
        "required_files": [
            item.relpath for item in sorted(snapshot.tracked_blobs, key=lambda item: item.relpath)
        ],
        "spec": _spec_mapping(spec),
        "snapshot": _snapshot_frame_mapping(snapshot),
        "interpreter": dict(
            _bind_current_interpreter()
            if interpreter_binding is None
            else interpreter_binding
        ),
        "environment": fresh_process_environment(),
        "canonical_prestate": _prestate_mapping(canonical_prestate),
    }
    encoded = _canonical_json_bytes(frame)
    if len(encoded) > _FRAME_LIMIT:
        _integrity("child_frame_too_large", phase="isolation")
    return frame


_ISOLATED_CHILD_LOADER = r'''
import base64,hashlib,json,os,sys,types
LIMIT=16777216
def fail(): os._exit(42)
frame=None
raw=b""
receipt_fd=None
try:
    f=sys.flags
    if not (f.isolated==1 and f.no_site==1 and f.ignore_environment==1 and f.safe_path==1 and f.dont_write_bytecode==1 and f.utf8_mode==1 and f.optimize==0): fail()
    if sys.argv != ['-c'] or not sys.pycache_prefix or '' in sys.path: fail()
    raw=sys.stdin.buffer.read(LIMIT+1)
    if not raw or len(raw)>LIMIT: fail()
    def pairs(items):
        out={}
        for k,v in items:
            if type(k) is not str or k in out: raise ValueError()
            out[k]=v
        return out
    frame=json.loads(raw.decode('utf-8','strict'),object_pairs_hook=pairs,parse_constant=lambda _x:(_ for _ in ()).throw(ValueError()))
    canonical=(json.dumps(frame,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf-8')+b'\n')
    if canonical!=raw or type(frame) is not dict: fail()
    if set(frame)!={'schema','nonce','argv','required_files','spec','snapshot','interpreter','environment','canonical_prestate'}: fail()
    if frame['schema']!='waggledance.release_evidence_child_frame.v1': fail()
    root=frame['snapshot']['root']
    if type(root) is not str or not root or not os.path.isabs(root): fail()
    expected=frame['environment']
    if type(expected) is not dict: fail()
    if os.name=='nt':
        wanted={k.upper():v for k,v in expected.items()}
        extras=[key for key in os.environ if key.upper() not in wanted]
        if any(key.upper()!='PYTHONUSERBASE' for key in extras) or len(extras)>1: fail()
        if extras:
            key=extras[0]; value=os.environ[key]
            if type(value) is not str or not value or '\x00' in value or not os.path.isabs(value) or value.startswith(('\\\\','//')): fail()
            normalized=os.path.normpath(value)
            drive,tail=os.path.splitdrive(normalized)
            if not drive or not tail.startswith(('\\','/')) or normalized!=value: fail()
            try:
                if os.path.normcase(os.path.commonpath((normalized,os.path.abspath(root))))==os.path.normcase(os.path.abspath(root)): fail()
            except ValueError:
                pass
            del os.environ[key]
        actual={k.upper():v for k,v in os.environ.items()}
    else:
        wanted=expected
        actual=dict(os.environ)
    if actual!=wanted: fail()
    root_key=os.path.normcase(os.path.abspath(root))
    for entry in sys.path:
        if type(entry) is not str or os.path.normcase(os.path.abspath(entry))==root_key: fail()
    os.set_inheritable(sys.stdout.fileno(),False)
    receipt_fd=os.dup(sys.stdout.fileno())
    os.set_inheritable(receipt_fd,False)
    null_fd=os.open(os.devnull,os.O_WRONLY)
    try: os.dup2(null_fd,sys.stdout.fileno(),inheritable=False)
    finally: os.close(null_fd)
    blobs=frame['snapshot']['blobs']
    hits=[x for x in blobs if type(x) is dict and x.get('path')=='tools/release_evidence_runtime.py']
    if len(hits)!=1: fail()
    source=base64.b64decode(hits[0]['content_b64'].encode('ascii'),validate=True)
    if b'\x00' in source or b'\r' in source: fail()
    if 'tools' in sys.modules or 'tools.release_evidence_runtime' in sys.modules: fail()
    package=types.ModuleType('tools'); package.__path__=(); package.__package__='tools'
    module=types.ModuleType('tools.release_evidence_runtime')
    module.__file__=os.path.join(root,'tools','release_evidence_runtime.py'); module.__package__='tools'
    sys.modules['tools']=package; sys.modules['tools.release_evidence_runtime']=module
    exec(compile(source,module.__file__,'exec',dont_inherit=True,optimize=0),module.__dict__,module.__dict__)
    code,receipt=module._isolated_child_entry(frame,'sha256:'+hashlib.sha256(raw).hexdigest())
    if type(code) is not int or code not in (40,41,42): fail()
    encoded=json.dumps(receipt,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf-8')+b'\n'
    os.write(receipt_fd,encoded)
    os._exit(code)
except BaseException:
    if type(receipt_fd) is int and type(frame) is dict and type(frame.get('nonce')) is str:
        try:
            receipt={'schema':'waggledance.release_evidence_child_receipt.v1','nonce':frame['nonce'],'frame_sha256':'sha256:'+hashlib.sha256(raw).hexdigest(),'private_exit':42,'status':'integrity','canonical_output':None,'canonical_sha256':None,'canonical_identity':None}
            os.write(receipt_fd,json.dumps(receipt,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf-8')+b'\n')
        except BaseException: pass
    os._exit(42)
'''.strip()


def _validate_interpreter_binding(expected: object) -> None:
    if type(expected) is not dict or set(expected) != {
        "path",
        "identity",
        "sha256",
        "version",
        "base_prefix",
        "cache_tag",
    }:
        _integrity("python_binding_invalid", phase="isolation")
    current = _bind_current_interpreter()
    if current != expected:
        _integrity("python_binding_changed", phase="isolation")


def _validate_child_frame(frame: object) -> tuple[ProducerSpec, tuple[str, ...], tuple[bytes, FileIdentity] | None]:
    if type(frame) is not dict or set(frame) != {
        "schema",
        "nonce",
        "argv",
        "required_files",
        "spec",
        "snapshot",
        "interpreter",
        "environment",
        "canonical_prestate",
    }:
        _integrity("child_frame_invalid", phase="isolation")
    if frame["schema"] != _FRAME_SCHEMA or _NONCE_RE.fullmatch(frame["nonce"] or "") is None:
        _integrity("child_frame_invalid", phase="isolation")
    if frame["environment"] != fresh_process_environment():
        _integrity("child_environment_not_fresh", phase="isolation")
    scrub_and_validate_child_environment()
    _validate_interpreter_binding(frame["interpreter"])
    spec = _spec_from_mapping(frame["spec"])
    if type(frame["argv"]) is not list:
        _integrity("child_frame_invalid", phase="isolation")
    argv = validate_sealed_argv(tuple(frame["argv"]), spec.argv_contract)
    required = validate_repo_relpath_list(frame["required_files"])
    snapshot_value = frame["snapshot"]
    if type(snapshot_value) is not dict or set(snapshot_value) != {
        "root",
        "startup_cwd",
        "git_sha256",
        "source",
        "blobs",
    }:
        _integrity("child_snapshot_invalid", phase="isolation")
    if sorted(required) != sorted(item["path"] for item in snapshot_value["source"]["files"]):
        _integrity("child_snapshot_invalid", phase="isolation")
    return spec, argv, _prestate_from_mapping(frame["canonical_prestate"])


def _receipt_mapping(
    *,
    nonce: str,
    frame_sha256: str,
    private_exit: int,
    status: str,
    target: Path | None = None,
) -> dict[str, Any]:
    if type(private_exit) is not int or private_exit not in {
        PRIVATE_EXIT_PASS,
        PRIVATE_EXIT_HOLD,
        PRIVATE_EXIT_INTEGRITY,
    }:
        _integrity("private_exit_invalid", phase="isolation")
    if target is None:
        canonical_output = canonical_sha256 = canonical_identity = None
    else:
        state = _capture_canonical_prestate(target)
        if state is None:
            _integrity("canonical_receipt_missing", phase="isolation")
        content, identity = state
        canonical_output = target.name
        canonical_sha256 = _sha256_bytes(content)
        canonical_identity = _identity_mapping(identity)
    return {
        "schema": _RECEIPT_SCHEMA,
        "nonce": nonce,
        "frame_sha256": frame_sha256,
        "private_exit": private_exit,
        "status": status,
        "canonical_output": canonical_output,
        "canonical_sha256": canonical_sha256,
        "canonical_identity": canonical_identity,
    }


def _isolated_child_entry(frame: object, frame_sha256: str) -> tuple[int, dict[str, Any]]:
    try:
        spec, argv, expected_prestate = _validate_child_frame(frame)
        snapshot_value = frame["snapshot"]
        root = _absolute_lexical(snapshot_value["root"])
        validate_isolated_child(bound_root=root)
        if not _same_identity_path(Path.cwd(), root):
            _integrity("child_cwd_mismatch", phase="isolation")
        snapshot = capture_repository_snapshot(
            startup_cwd=root,
            required_files=tuple(frame["required_files"]),
        )
        if _snapshot_frame_mapping(snapshot) != snapshot_value:
            _integrity("child_snapshot_changed", phase="isolation")
        if _capture_canonical_prestate(canonical_output_path(snapshot, spec)) != expected_prestate:
            _integrity("canonical_prestate_changed", phase="isolation")
        namespace = exec_verified_source(
            snapshot,
            relpath=spec.producer_relpath,
            module_name=f"_waggledance_frozen_{spec.producer_id}",
        )
        if namespace.get("PRODUCER_SPEC") != spec:
            _integrity("frozen_producer_spec_mismatch", phase="producer")
        produce = namespace.get("produce")
        if not callable(produce):
            _integrity("frozen_producer_entry_missing", phase="producer")
        outcome = produce(snapshot, argv)
        if type(outcome) is not ProducerOutcome:
            _integrity("frozen_producer_outcome_invalid", phase="producer")
        public = _publish_completion_transaction(
            snapshot=snapshot,
            spec=spec,
            outcome=outcome,
            defer_commit_cleanup=True,
        )
        target = canonical_output_path(snapshot, spec)
        private = PRIVATE_EXIT_PASS if public == EXIT_PASS else PRIVATE_EXIT_HOLD
        return private, _receipt_mapping(
            nonce=frame["nonce"],
            frame_sha256=frame_sha256,
            private_exit=private,
            status=outcome.status,
            target=target,
        )
    except EvidenceFailStop:
        os._exit(_PRIVATE_FAIL_STOP)
    except BaseException:
        nonce = frame.get("nonce") if type(frame) is dict else None
        if type(nonce) is not str or _NONCE_RE.fullmatch(nonce) is None:
            os._exit(_PRIVATE_FAIL_STOP)
        return PRIVATE_EXIT_INTEGRITY, _receipt_mapping(
            nonce=nonce,
            frame_sha256=frame_sha256,
            private_exit=PRIVATE_EXIT_INTEGRITY,
            status="integrity",
        )


def _validate_child_receipt(
    raw: bytes,
    *,
    expected_nonce: str,
    expected_frame_sha256: str,
    private_exit: int,
    snapshot: RepositorySnapshot,
    spec: ProducerSpec,
    canonical_prestate: tuple[bytes, FileIdentity] | None,
) -> int:
    value = _strict_json_loads(raw, reason="child_receipt_invalid")
    if type(value) is not dict or set(value) != {
        "schema",
        "nonce",
        "frame_sha256",
        "private_exit",
        "status",
        "canonical_output",
        "canonical_sha256",
        "canonical_identity",
    }:
        _integrity("child_receipt_invalid", phase="isolation")
    if (
        value["schema"] != _RECEIPT_SCHEMA
        or value["nonce"] != expected_nonce
        or value["frame_sha256"] != expected_frame_sha256
        or type(value["private_exit"]) is not int
        or value["private_exit"] != private_exit
        or _canonical_json_bytes(value) != raw
    ):
        _integrity("child_receipt_invalid", phase="isolation")
    if private_exit == PRIVATE_EXIT_INTEGRITY:
        if (
            value["status"] != "integrity"
            or value["canonical_output"] is not None
            or value["canonical_sha256"] is not None
            or value["canonical_identity"] is not None
        ):
            _integrity("child_receipt_invalid", phase="isolation")
        return EXIT_INTEGRITY
    expected_status = "pass" if private_exit == PRIVATE_EXIT_PASS else "hold_nonpass"
    target = canonical_output_path(snapshot, spec)
    canonical_state = _capture_canonical_prestate(target)
    if canonical_state is None:
        _integrity("child_receipt_binding_invalid", phase="isolation")
    content, identity = canonical_state
    envelope = _validate_completion_bytes(content, snapshot, spec)
    if (
        value["status"] != expected_status
        or envelope["status"] != expected_status
        or value["canonical_output"] != target.name
        or value["canonical_sha256"] != _sha256_bytes(content)
        or _identity_from_mapping(value["canonical_identity"]) != identity
        or (canonical_prestate is not None and identity == canonical_prestate[1])
    ):
        _integrity("child_receipt_binding_invalid", phase="isolation")
    return EXIT_PASS if private_exit == PRIVATE_EXIT_PASS else EXIT_HOLD_NONPASS


def validate_isolated_child(*, bound_root: Path) -> Path:
    if not (
        sys.flags.isolated
        and sys.flags.no_site
        and sys.flags.ignore_environment
        and sys.flags.safe_path
        and sys.flags.dont_write_bytecode
        and sys.flags.utf8_mode
        and sys.flags.optimize == 0
    ):
        _integrity("producer_process_not_isolated", phase="isolation")
    prefix_text = sys.pycache_prefix
    if not prefix_text:
        _integrity("isolation_prefix_missing", phase="isolation")
    prefix = _absolute_lexical(prefix_text)
    base = _fixed_scratch_base(bound_root)
    if prefix.parent != base or not path_is_within(prefix, base):
        _integrity("isolation_prefix_not_fixed", phase="isolation")
    _ensure_owner_only_directory(prefix)
    scrub_and_validate_child_environment()
    return prefix


def _cleanup_isolation_prefix(prefix: Path) -> bool:
    try:
        if any(prefix.iterdir()):
            return False
        prefix.rmdir()
        return True
    except OSError:
        return False


def _capture_canonical_prestate(
    target: Path,
) -> tuple[bytes, FileIdentity] | None:
    phase = _CommitPhase()
    with _open_directory_leases(target.parent, phase) as leases:
        parent_lease = leases[-1]
        with _publication_lock(parent_lease, target, phase):
            return _dir_read_plain(parent_lease, target.name)


def ensure_isolated_once(
    *,
    snapshot: RepositorySnapshot,
    executing_file: Path | str,
    producer_relpath: str,
    validated_argv: Sequence[str],
    bootstrap_spec: ProducerSpec | None = None,
) -> int:
    """Run the sole frozen isolated child and authenticate its private receipt."""

    if bootstrap_spec is None or bootstrap_spec.producer_relpath != producer_relpath:
        _integrity("child_spec_missing", phase="isolation")
    if sys.flags.isolated:
        _integrity("nested_isolated_execution_forbidden", phase="isolation")
    verify_executing_source(
        snapshot,
        declared_relpath=producer_relpath,
        executing_file=executing_file,
    )
    target = canonical_output_path(snapshot, bootstrap_spec)
    prestate = _capture_canonical_prestate(target)
    interpreter = _bind_current_interpreter()
    frame = _build_child_frame(
        snapshot=snapshot,
        spec=bootstrap_spec,
        validated_argv=validated_argv,
        canonical_prestate=prestate,
        interpreter_binding=interpreter,
    )
    frame_bytes = _canonical_json_bytes(frame)
    frame_digest = _sha256_bytes(frame_bytes)
    prefix = create_isolation_prefix(snapshot.root)
    command = [
        interpreter["path"],
        "-I",
        "-S",
        "-B",
        "-X",
        "utf8",
        "-X",
        f"pycache_prefix={prefix}",
        "-c",
        _ISOLATED_CHILD_LOADER,
    ]
    process: subprocess.Popen[bytes] | None = None
    stdout = b""
    private_exit: int | None = None
    public: int | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=snapshot.root,
            env=fresh_process_environment(),
            shell=False,
            close_fds=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        child_image = _process_image_path(process)
        if os.path.normcase(os.path.normpath(os.fspath(child_image))) != os.path.normcase(
            os.path.normpath(interpreter["path"])
        ):
            _integrity("python_child_image_mismatch", phase="isolation")
        image_bytes, image_identity = _read_plain_file(child_image, require_single_link=False)
        if (
            _identity_mapping(image_identity) != interpreter["identity"]
            or _sha256_bytes(image_bytes) != interpreter["sha256"]
        ):
            _integrity("python_child_image_changed", phase="isolation")
        stdout, _stderr = process.communicate(frame_bytes, timeout=300)
        private_exit = process.returncode
    except BaseException:
        if process is not None:
            with contextlib.suppress(BaseException):
                if process.poll() is None:
                    process.kill()
            with contextlib.suppress(BaseException):
                process.communicate()
        private_exit = None

    # From this point the child may have mutated the canonical path.  Every
    # parent-side verification failure is converted into an abort decision;
    # nothing between child execution and the resolver may escape around path
    # transaction recovery.
    try:
        interpreter_after = _bind_current_interpreter()
        binding_stable = interpreter_after == interpreter
        if binding_stable and type(private_exit) is int and private_exit in {
            PRIVATE_EXIT_PASS,
            PRIVATE_EXIT_HOLD,
            PRIVATE_EXIT_INTEGRITY,
        }:
            public = _validate_child_receipt(
                stdout,
                expected_nonce=frame["nonce"],
                expected_frame_sha256=frame_digest,
                private_exit=private_exit,
                snapshot=snapshot,
                spec=bootstrap_spec,
                canonical_prestate=prestate,
            )
    except BaseException:
        public = None

    prefer_abort = public is None or public == EXIT_INTEGRITY
    state = _resolve_path_transaction_step(
        target=target,
        snapshot=snapshot,
        spec=bootstrap_spec,
        expected_prestate=prestate,
        prefer_abort=prefer_abort,
    )
    if state == "blocked":
        raise EvidenceFailStop()
    if prefer_abort:
        if state != "aborted":
            raise EvidenceFailStop()
        if not _cleanup_isolation_prefix(prefix):
            return EXIT_INTEGRITY
        return EXIT_INTEGRITY
    if state != "committed" or public not in {EXIT_PASS, EXIT_HOLD_NONPASS}:
        raise EvidenceFailStop()
    # E1: after a verified durable commit, isolation cleanup cannot convert the
    # canonical result into public 2.
    _cleanup_isolation_prefix(prefix)
    return public


class _PosixDirectoryLease:
    def __init__(self, path: Path, descriptor: int, identity: FileIdentity) -> None:
        self.path = path
        self.descriptor = descriptor
        self.identity = identity
        self.closed = False

    def revalidate(self) -> None:
        if self.closed:
            _integrity("directory_lease_closed", phase="durability")
        try:
            opened = os.fstat(self.descriptor)
            current = os.lstat(self.path)
        except OSError:
            _integrity("directory_lease_revalidation_failed", phase="durability")
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _stat_is_reparse(current)
            or not os.path.samestat(opened, current)
            or int(opened.st_dev) != self.identity.volume
            or int(opened.st_ino) != self.identity.file_id
        ):
            _integrity("directory_lease_identity_changed", phase="durability")

    def flush(self) -> None:
        self.revalidate()
        try:
            os.fsync(self.descriptor)
        except OSError:
            _integrity("directory_flush_failed", phase="durability")

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            os.close(self.descriptor)


class _WindowsDirectoryLease:
    def __init__(
        self,
        path: Path,
        handle: int,
        identity: tuple[int, bytes],
        final_name: str,
        *,
        writable: bool,
    ) -> None:
        self.path = path
        self.handle = handle
        self.identity = identity
        self.final_name = final_name
        self.writable = writable
        self.closed = False

    def revalidate(self) -> None:
        if self.closed:
            _integrity("directory_lease_closed", phase="durability")
        identity, attributes, _links = _windows_handle_identity(self.handle)
        if identity != self.identity or not (attributes & 0x10) or attributes & 0x400:
            _integrity("directory_lease_identity_changed", phase="durability")
        current_name = _windows_final_path(self.handle)
        if os.path.normcase(current_name) != os.path.normcase(self.final_name):
            _integrity("directory_lease_path_changed", phase="durability")

    def flush(self) -> None:
        if not self.writable:
            _integrity("directory_lease_not_writable", phase="durability")
        self.revalidate()
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        kernel32.FlushFileBuffers.restype = wintypes.BOOL
        if not kernel32.FlushFileBuffers(self.handle):
            _integrity("directory_flush_failed", phase="durability")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        if not kernel32.CloseHandle(self.handle):
            _integrity("directory_lease_close_failed", phase="durability")


def _windows_handle_identity(handle: int) -> tuple[tuple[int, bytes], int, int]:
    from ctypes import wintypes

    class _FILE_ID_128(ctypes.Structure):
        _fields_ = [("Identifier", ctypes.c_ubyte * 16)]

    class _FILE_ID_INFO(ctypes.Structure):
        _fields_ = [
            ("VolumeSerialNumber", ctypes.c_ulonglong),
            ("FileId", _FILE_ID_128),
        ]

    class _FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", _FILETIME),
            ("ftLastAccessTime", _FILETIME),
            ("ftLastWriteTime", _FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    file_id = _FILE_ID_INFO()
    if not kernel32.GetFileInformationByHandleEx(
        handle,
        0x12,
        ctypes.byref(file_id),
        ctypes.sizeof(file_id),
    ):
        _integrity("windows_file_id_unavailable", phase="path")
    basic = _BY_HANDLE_FILE_INFORMATION()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(basic)):
        _integrity("windows_file_information_unavailable", phase="path")
    identity = (
        int(file_id.VolumeSerialNumber),
        bytes(file_id.FileId.Identifier),
    )
    return identity, int(basic.dwFileAttributes), int(basic.nNumberOfLinks)


def _windows_final_path(handle: int) -> str:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    size = 32768
    buffer = ctypes.create_unicode_buffer(size)
    length = int(kernel32.GetFinalPathNameByHandleW(handle, buffer, size, 0))
    if length <= 0 or length >= size:
        _integrity("windows_final_path_unavailable", phase="path")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normpath(value)


def _open_windows_directory(path: Path, *, writable: bool) -> _WindowsDirectoryLease:
    from ctypes import wintypes

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
    access = 0x80000000 | (0x40000000 if writable else 0)
    handle = kernel32.CreateFileW(
        os.fspath(path),
        access,
        0x00000001 | 0x00000002,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        _integrity("directory_lease_open_failed", phase="durability")
    try:
        identity, attributes, _links = _windows_handle_identity(handle)
        if not (attributes & 0x10) or attributes & 0x400:
            _integrity("directory_chain_not_plain", phase="durability")
        final_name = _windows_final_path(handle)
        expected = os.path.normpath(os.fspath(_absolute_lexical(path)))
        if os.path.normcase(final_name) != os.path.normcase(expected):
            _integrity("directory_lease_path_alias", phase="durability")
        return _WindowsDirectoryLease(
            path,
            handle,
            identity,
            final_name,
            writable=writable,
        )
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


@dataclass
class _CommitPhase:
    committed: bool = False


@contextlib.contextmanager
def _open_directory_leases(
    parent: Path,
    phase: _CommitPhase | None = None,
) -> Iterator[tuple[Any, ...]]:
    chain = _directory_chain(parent)
    leases: list[Any] = []
    try:
        if os.name == "nt":
            for index, candidate in enumerate(chain):
                leases.append(
                    _open_windows_directory(candidate, writable=index == len(chain) - 1)
                )
        else:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            previous_fd: int | None = None
            for index, candidate in enumerate(chain):
                try:
                    if index == 0:
                        descriptor = os.open(candidate, flags)
                    else:
                        descriptor = os.open(candidate.name, flags, dir_fd=previous_fd)
                except OSError:
                    _integrity("directory_lease_open_failed", phase="durability")
                opened = os.fstat(descriptor)
                current = os.lstat(candidate)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or _stat_is_reparse(current)
                    or not os.path.samestat(opened, current)
                ):
                    os.close(descriptor)
                    _integrity("directory_chain_not_plain", phase="durability")
                leases.append(_PosixDirectoryLease(candidate, descriptor, _portable_identity(opened)))
                previous_fd = descriptor
        for lease in leases:
            lease.revalidate()
        yield tuple(leases)
    finally:
        close_error = False
        for lease in reversed(leases):
            try:
                lease.close()
            except BaseException:
                close_error = True
        if close_error and sys.exc_info()[0] is None and not (phase and phase.committed):
            _integrity("directory_lease_close_failed", phase="durability")


def _windows_mutex_acl_restricted(handle: int, current_sid: str) -> bool:
    from ctypes import wintypes

    class _ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    class _ACCESS_ALLOWED_ACE(ctypes.Structure):
        _fields_ = [
            ("Header", _ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetSecurityInfo.restype = wintypes.DWORD
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_int,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    dacl = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    if advapi32.GetSecurityInfo(
        handle,
        6,
        0x00000001 | 0x00000004,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    ) != 0 or not owner or not dacl or not descriptor:
        return False
    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ) or not (control.value & 0x1000):
            return False
        info = _ACL_SIZE_INFORMATION()
        if not advapi32.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
            return False
        if info.AceCount != 2:
            return False
        rendered_owner = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(
            owner, ctypes.byref(rendered_owner)
        ):
            return False
        try:
            if rendered_owner.value != current_sid:
                return False
        finally:
            kernel32.LocalFree(ctypes.cast(rendered_owner, wintypes.HLOCAL))
        seen: set[str] = set()
        for index in range(2):
            pointer = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(pointer)):
                return False
            ace = ctypes.cast(pointer, ctypes.POINTER(_ACCESS_ALLOWED_ACE)).contents
            # CreateMutex maps GENERIC_ALL from the SDDL to the mutex-specific
            # MUTEX_ALL_ACCESS mask (STANDARD_RIGHTS_REQUIRED | SYNCHRONIZE |
            # MUTEX_MODIFY_STATE).  Require that exact normalized mask: a
            # generic-bit check rejects the intended ACL, while a subset check
            # could silently admit widened rights.
            if (
                ace.Header.AceType != 0
                or ace.Header.AceFlags != 0
                or int(ace.Mask) != 0x001F0001
                or int(ace.Header.AceSize) < ctypes.sizeof(_ACCESS_ALLOWED_ACE)
            ):
                return False
            sid_pointer = ctypes.c_void_p(
                int(pointer.value) + _ACCESS_ALLOWED_ACE.SidStart.offset
            )
            rendered = ctypes.c_wchar_p()
            if not advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(rendered)):
                return False
            try:
                if rendered.value not in {"S-1-5-18", current_sid}:
                    return False
                seen.add(rendered.value)
            finally:
                kernel32.LocalFree(ctypes.cast(rendered, wintypes.HLOCAL))
        return seen == {"S-1-5-18", current_sid}
    finally:
        kernel32.LocalFree(ctypes.cast(descriptor, wintypes.HLOCAL))


@contextlib.contextmanager
def _publication_lock(
    parent_lease: Any,
    target: Path,
    phase: _CommitPhase | None = None,
) -> Iterator[None]:
    if os.name != "nt":
        try:
            import fcntl

            fcntl.flock(parent_lease.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError):
            _integrity("publication_lock_unavailable", phase="durability")
        try:
            yield
        finally:
            try:
                fcntl.flock(parent_lease.descriptor, fcntl.LOCK_UN)
            except OSError:
                if phase and phase.committed:
                    return
                _integrity("publication_lock_release_failed", phase="durability")
        return

    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    sid_buffer, sid_pointer = _windows_current_user_sid_buffer()
    sid = _windows_sid_string(sid_pointer)
    del sid_buffer
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    descriptor = wintypes.LPVOID()
    descriptor_size = wintypes.DWORD()
    sddl = f"O:{sid}D:P(A;;GA;;;SY)(A;;GA;;;{sid})"
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), ctypes.byref(descriptor_size)
    ):
        _integrity("publication_lock_acl_failed", phase="durability")
    attributes = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False
    )
    digest = hashlib.sha256(
        os.path.normcase(os.fspath(target)).encode("utf-8")
    ).hexdigest()
    handle = kernel32.CreateMutexW(
        ctypes.byref(attributes),
        False,
        f"Global\\WaggleDance.ReleaseEvidence.{sid}.{digest}",
    )
    kernel32.LocalFree(descriptor)
    if not handle:
        _integrity("publication_lock_unavailable", phase="durability")
    if not _windows_mutex_acl_restricted(handle, sid):
        kernel32.CloseHandle(handle)
        _integrity("publication_lock_acl_invalid", phase="durability")
    acquired = False
    abandoned = False
    try:
        wait = int(kernel32.WaitForSingleObject(handle, 5000))
        if wait == 0:
            acquired = True
        elif wait == 0x80:
            acquired = True
            abandoned = True
        else:
            _integrity("publication_lock_busy", phase="durability")
        if abandoned:
            _integrity("publication_lock_abandoned", phase="durability")
        yield
    finally:
        release_failed = acquired and not kernel32.ReleaseMutex(handle)
        close_failed = not kernel32.CloseHandle(handle)
        if (
            (release_failed or close_failed)
            and sys.exc_info()[0] is None
            and not (phase and phase.committed)
        ):
            _integrity("publication_lock_release_failed", phase="durability")


def _name_only(value: str) -> str:
    if (
        type(value) is not str
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        _integrity("transaction_name_invalid", phase="durability")
    return value


def _dir_lstat(parent_lease: Any, name: str) -> os.stat_result | None:
    name = _name_only(name)
    parent_lease.revalidate()
    try:
        if os.name == "nt":
            return os.lstat(parent_lease.path / name)
        return os.stat(name, dir_fd=parent_lease.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        _integrity("transaction_state_unreadable", phase="durability")


def _dir_read_plain(
    parent_lease: Any,
    name: str,
    *,
    require_single_link: bool = True,
) -> tuple[bytes, FileIdentity] | None:
    name = _name_only(name)
    info = _dir_lstat(parent_lease, name)
    if info is None:
        return None
    if os.name == "nt":
        return _read_plain_file(
            parent_lease.path / name,
            require_single_link=require_single_link,
        )
    if (
        not stat.S_ISREG(info.st_mode)
        or _stat_is_reparse(info)
        or (require_single_link and info.st_nlink != 1)
    ):
        _integrity("transaction_file_not_plain", phase="durability")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_lease.descriptor)
    except OSError:
        _integrity("transaction_file_open_failed", phase="durability")
    try:
        opened = os.fstat(descriptor)
        if (
            not os.path.samestat(info, opened)
            or not stat.S_ISREG(opened.st_mode)
            or (require_single_link and opened.st_nlink != 1)
        ):
            _integrity("transaction_file_identity_changed", phase="durability")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        final = _dir_lstat(parent_lease, name)
        if (
            final is None
            or not os.path.samestat(opened, after)
            or not os.path.samestat(after, final)
            or opened.st_size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns
        ):
            _integrity("transaction_file_changed_during_read", phase="durability")
        content = b"".join(chunks)
        if len(content) != after.st_size:
            _integrity("transaction_file_changed_during_read", phase="durability")
        return content, _portable_identity(after)
    finally:
        os.close(descriptor)


def _dir_write_exclusive(
    parent_lease: Any,
    name: str,
    content: bytes,
) -> FileIdentity:
    name = _name_only(name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        if os.name == "nt":
            descriptor = os.open(parent_lease.path / name, flags, 0o600)
        else:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_lease.descriptor)
    except OSError:
        _integrity("exclusive_candidate_creation_failed", phase="durability")
    try:
        view = memoryview(content)
        position = 0
        while position < len(content):
            count = os.write(descriptor, view[position:])
            if type(count) is not int or count <= 0:
                _integrity("candidate_write_made_no_progress", phase="durability")
            position += count
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _integrity("candidate_not_plain_single_link", phase="durability")
        identity = _portable_identity(info)
    finally:
        os.close(descriptor)
    observed = _dir_read_plain(parent_lease, name)
    if observed is None or observed[0] != content:
        _integrity("candidate_verification_failed", phase="durability")
    if os.name != "nt" and observed[1] != identity:
        _integrity("candidate_verification_failed", phase="durability")
    return observed[1]


def _dir_unlink_known(parent_lease: Any, name: str, content: bytes) -> None:
    observed = _dir_read_plain(parent_lease, name, require_single_link=False)
    if observed is None:
        return
    if observed[0] != content or observed[1].nlink != 1:
        _integrity("transaction_file_state_unknown", phase="durability")
    try:
        if os.name == "nt":
            os.unlink(parent_lease.path / _name_only(name))
        else:
            os.unlink(_name_only(name), dir_fd=parent_lease.descriptor)
    except OSError:
        _integrity("transaction_cleanup_failed", phase="durability")


def _dir_flush_file(parent_lease: Any, name: str) -> None:
    observed = _dir_read_plain(parent_lease, name)
    if observed is None:
        _integrity("canonical_file_missing", phase="durability")
    if os.name == "nt":
        _flush_plain_file_windows(parent_lease.path / _name_only(name))
        return
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(_name_only(name), flags, dir_fd=parent_lease.descriptor)
        os.fsync(descriptor)
    except OSError:
        _integrity("canonical_file_flush_failed", phase="durability")
    finally:
        if "descriptor" in locals():
            os.close(descriptor)


def _cas_exchange_existing(
    parent_lease: Any,
    target_name: str,
    candidate_name: str,
    backup_name: str,
    *,
    expected_target: tuple[bytes, FileIdentity],
    expected_candidate: tuple[bytes, FileIdentity],
) -> None:
    """Atomically promote candidate while capturing the actual displaced target."""

    target_name = _name_only(target_name)
    candidate_name = _name_only(candidate_name)
    backup_name = _name_only(backup_name)
    parent_lease.revalidate()
    try:
        candidate_state = _dir_read_plain(parent_lease, candidate_name)
    except EvidenceIntegrityError as exc:
        raise EvidenceIntegrityError(
            "candidate_binding_invalid", phase="durability"
        ) from exc
    if (
        type(expected_target) is not tuple
        or len(expected_target) != 2
        or type(expected_candidate) is not tuple
        or len(expected_candidate) != 2
        or candidate_state is None
        or candidate_state[1].nlink != 1
        or candidate_state != expected_candidate
    ):
        _integrity("candidate_binding_invalid", phase="durability")
    try:
        target_state = _dir_read_plain(parent_lease, target_name)
        backup_state = _dir_read_plain(parent_lease, backup_name)
    except EvidenceIntegrityError as exc:
        raise EvidenceIntegrityError("cas_path_binding_invalid", phase="durability") from exc
    if target_state != expected_target or backup_state is not None:
        _integrity("cas_path_binding_invalid", phase="durability")
    if os.name == "nt":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReplaceFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        kernel32.ReplaceFileW.restype = wintypes.BOOL
        if not kernel32.ReplaceFileW(
            os.fspath(parent_lease.path / target_name),
            os.fspath(parent_lease.path / candidate_name),
            os.fspath(parent_lease.path / backup_name),
            0,
            None,
            None,
        ):
            _integrity("atomic_exchange_failed", phase="durability")
        return
    encoded_target = os.fsencode(target_name)
    encoded_candidate = os.fsencode(candidate_name)
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            _integrity("atomic_exchange_unavailable", phase="durability")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        if renameat2(
            parent_lease.descriptor,
            encoded_candidate,
            parent_lease.descriptor,
            encoded_target,
            0x2,
        ) != 0:
            _integrity("atomic_exchange_failed", phase="durability")
        _cas_promote_absent(
            parent_lease,
            backup_name,
            candidate_name,
            expected_candidate=target_state,
        )
        return
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        function = getattr(libc, "renameatx_np", None)
        if function is None:
            _integrity("atomic_exchange_unavailable", phase="durability")
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        if function(
            parent_lease.descriptor,
            encoded_candidate,
            parent_lease.descriptor,
            encoded_target,
            0x2 | 0x10,
        ) != 0:
            _integrity("atomic_exchange_failed", phase="durability")
        _cas_promote_absent(
            parent_lease,
            backup_name,
            candidate_name,
            expected_candidate=target_state,
        )
        return
    _integrity("unsupported_release_platform", phase="durability")


def _cas_promote_absent(
    parent_lease: Any,
    target_name: str,
    candidate_name: str,
    *,
    expected_candidate: tuple[bytes, FileIdentity],
) -> None:
    target_name = _name_only(target_name)
    candidate_name = _name_only(candidate_name)
    parent_lease.revalidate()
    try:
        candidate_state = _dir_read_plain(parent_lease, candidate_name)
        target_state = _dir_read_plain(parent_lease, target_name)
    except EvidenceIntegrityError as exc:
        raise EvidenceIntegrityError(
            "candidate_binding_invalid", phase="durability"
        ) from exc
    if (
        type(expected_candidate) is not tuple
        or len(expected_candidate) != 2
        or candidate_state is None
        or candidate_state[1].nlink != 1
        or target_state is not None
        or candidate_state != expected_candidate
    ):
        _integrity("candidate_binding_invalid", phase="durability")
    if os.name == "nt":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.MoveFileExW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        ]
        kernel32.MoveFileExW.restype = wintypes.BOOL
        if not kernel32.MoveFileExW(
            os.fspath(parent_lease.path / candidate_name),
            os.fspath(parent_lease.path / target_name),
            0x00000008,
        ):
            _integrity("atomic_noreplace_failed", phase="durability")
        return
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        function = getattr(libc, "renameat2", None)
        if function is None:
            _integrity("atomic_noreplace_unavailable", phase="durability")
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        if function(
            parent_lease.descriptor,
            os.fsencode(candidate_name),
            parent_lease.descriptor,
            os.fsencode(target_name),
            0x1,
        ) != 0:
            _integrity("atomic_noreplace_failed", phase="durability")
        return
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        function = getattr(libc, "renameatx_np", None)
        if function is None:
            _integrity("atomic_noreplace_unavailable", phase="durability")
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        if function(
            parent_lease.descriptor,
            os.fsencode(candidate_name),
            parent_lease.descriptor,
            os.fsencode(target_name),
            0x4 | 0x10,
        ) != 0:
            _integrity("atomic_noreplace_failed", phase="durability")
        return
    _integrity("unsupported_release_platform", phase="durability")


def _read_optional_plain(path: Path) -> tuple[bytes, FileIdentity] | None:
    """Read-only compatibility seam; publication itself uses directory leases."""

    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        _integrity("canonical_state_unreadable", phase="durability")
    return _read_plain_file(path)


def _flush_plain_file_windows(path: Path) -> None:
    from ctypes import wintypes

    absolute = _absolute_lexical(path)
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
    kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel32.FlushFileBuffers.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        os.fspath(absolute),
        0x80000000 | 0x40000000,
        0x00000001,
        None,
        3,
        0x00200000,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        _integrity("canonical_file_flush_failed", phase="durability")
    try:
        _identity, attributes, links = _windows_handle_identity(handle)
        if attributes & 0x10 or attributes & 0x400 or links != 1:
            _integrity("canonical_file_not_plain", phase="durability")
        if not kernel32.FlushFileBuffers(handle):
            _integrity("canonical_file_flush_failed", phase="durability")
    finally:
        if not kernel32.CloseHandle(handle) and sys.exc_info()[0] is None:
            _integrity("canonical_file_close_failed", phase="durability")


def _transaction_names(target_name: str, nonce: str) -> dict[str, str]:
    target_name = _name_only(target_name)
    if type(nonce) is not str or _NONCE_RE.fullmatch(nonce) is None:
        _integrity("transaction_nonce_invalid", phase="durability")
    stem = f".wdre.{target_name}.{nonce}"
    return {
        "descriptor": f"{stem}.descriptor.json",
        "candidate": f"{stem}.candidate",
        "backup": f"{stem}.backup",
        "quarantine": f"{stem}.quarantine",
    }


def _reserved_transaction_names(parent_lease: Any, target_name: str) -> tuple[str, ...]:
    prefix = f".wdre.{_name_only(target_name)}."
    parent_lease.revalidate()
    try:
        if os.name == "nt":
            names = tuple(entry.name for entry in os.scandir(parent_lease.path))
        else:
            names = tuple(os.listdir(parent_lease.descriptor))
    except OSError:
        _integrity("transaction_enumeration_failed", phase="durability")
    selected = tuple(sorted(name for name in names if name.startswith(prefix)))
    for name in selected:
        _name_only(name)
    return selected


def _transaction_descriptor(
    *,
    target: Path,
    nonce: str,
    names: Mapping[str, str],
    prestate: tuple[bytes, FileIdentity] | None,
    new_content: bytes,
    new_identity: FileIdentity,
    snapshot: RepositorySnapshot,
    spec: ProducerSpec,
    status: str,
    public_exit: int,
) -> dict[str, Any]:
    if type(public_exit) is not int or public_exit not in {EXIT_PASS, EXIT_HOLD_NONPASS}:
        _integrity("transaction_result_invalid", phase="durability")
    return {
        "schema": _TRANSACTION_SCHEMA,
        "target_name": target.name,
        "nonce": nonce,
        "names": dict(names),
        "prestate": _prestate_mapping(prestate),
        "new": {
            "sha256": _sha256_bytes(new_content),
            "identity": _identity_mapping(new_identity),
        },
        "source": _expected_source(snapshot),
        "spec": _spec_mapping(spec),
        "status": status,
        "public_exit": public_exit,
    }


def _parse_transaction_descriptor(
    content: bytes,
    *,
    target: Path,
    snapshot: RepositorySnapshot,
    spec: ProducerSpec,
) -> tuple[dict[str, Any], tuple[bytes, FileIdentity] | None]:
    value = _strict_json_loads(content, reason="transaction_descriptor_invalid")
    if type(value) is not dict or set(value) != {
        "schema",
        "target_name",
        "nonce",
        "names",
        "prestate",
        "new",
        "source",
        "spec",
        "status",
        "public_exit",
    }:
        _integrity("transaction_descriptor_invalid", phase="durability")
    if (
        value["schema"] != _TRANSACTION_SCHEMA
        or value["target_name"] != target.name
        or type(value["nonce"]) is not str
        or _NONCE_RE.fullmatch(value["nonce"]) is None
        or type(value["names"]) is not dict
        or value["names"] != _transaction_names(target.name, value["nonce"])
        or value["source"] != _expected_source(snapshot)
        or value["spec"] != _spec_mapping(spec)
        or value["status"] not in {"pass", "hold_nonpass"}
        or type(value["public_exit"]) is not int
        or value["public_exit"]
        != (EXIT_PASS if value["status"] == "pass" else EXIT_HOLD_NONPASS)
        or type(value["new"]) is not dict
        or set(value["new"]) != {"sha256", "identity"}
        or _SHA256_RE.fullmatch(value["new"]["sha256"] or "") is None
        or _canonical_json_bytes(value) != content
    ):
        _integrity("transaction_descriptor_invalid", phase="durability")
    _identity_from_mapping(value["new"]["identity"])
    return value, _prestate_from_mapping(value["prestate"])


def _same_volume(parent_lease: Any, identity: FileIdentity) -> bool:
    if os.name == "nt":
        return identity.volume == int(parent_lease.identity[0])
    try:
        return identity.volume == int(os.fstat(parent_lease.descriptor).st_dev)
    except OSError:
        _integrity("directory_lease_revalidation_failed", phase="durability")


def _move_target_to_quarantine(
    parent_lease: Any,
    target_name: str,
    quarantine_name: str,
    *,
    expected_target: tuple[bytes, FileIdentity],
) -> None:
    _cas_promote_absent(
        parent_lease,
        quarantine_name,
        target_name,
        expected_candidate=expected_target,
    )


def _restore_displaced_target(
    parent_lease: Any,
    *,
    target_name: str,
    displaced_name: str,
    quarantine_name: str,
    expected_target: tuple[bytes, FileIdentity],
    expected_displaced: tuple[bytes, FileIdentity],
) -> None:
    if os.name != "nt":
        _cas_exchange_existing(
            parent_lease,
            target_name,
            displaced_name,
            quarantine_name,
            expected_target=expected_target,
            expected_candidate=expected_displaced,
        )
        return
    try:
        current_target = _dir_read_plain(parent_lease, target_name)
        current_displaced = _dir_read_plain(parent_lease, displaced_name)
        current_quarantine = _dir_read_plain(parent_lease, quarantine_name)
    except EvidenceIntegrityError as exc:
        raise EvidenceIntegrityError(
            "canonical_restore_binding_invalid", phase="rollback"
        ) from exc
    if (
        current_target != expected_target
        or current_displaced != expected_displaced
        or current_quarantine is not None
    ):
        _integrity("canonical_restore_binding_invalid", phase="rollback")
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ReplaceFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    kernel32.ReplaceFileW.restype = wintypes.BOOL
    if not kernel32.ReplaceFileW(
        os.fspath(parent_lease.path / _name_only(target_name)),
        os.fspath(parent_lease.path / _name_only(displaced_name)),
        os.fspath(parent_lease.path / _name_only(quarantine_name)),
        0,
        None,
        None,
    ):
        _integrity("canonical_restore_failed", phase="rollback")


def _resolve_path_transaction_held(
    *,
    parent_lease: Any,
    target: Path,
    snapshot: RepositorySnapshot,
    spec: ProducerSpec,
    expected_prestate: tuple[bytes, FileIdentity] | None,
    prefer_abort: bool,
) -> str:
    reserved = _reserved_transaction_names(parent_lease, target.name)
    try:
        revalidate_repository_snapshot(
            snapshot,
            allowed_changed_paths=tuple(
                target.parent / name for name in (target.name, *reserved)
            ),
        )
    except EvidenceIntegrityError:
        return "blocked"
    if not reserved:
        current = _dir_read_plain(parent_lease, target.name)
        return "aborted" if current == expected_prestate else "blocked"
    descriptor_names = tuple(name for name in reserved if name.endswith(".descriptor.json"))
    if len(descriptor_names) != 1:
        return "blocked"
    descriptor_name = descriptor_names[0]
    descriptor_state = _dir_read_plain(parent_lease, descriptor_name)
    if descriptor_state is None:
        return "blocked"
    try:
        descriptor, described_prestate = _parse_transaction_descriptor(
            descriptor_state[0],
            target=target,
            snapshot=snapshot,
            spec=spec,
        )
    except EvidenceIntegrityError:
        return "blocked"
    if (
        _prestate_mapping(described_prestate) != _prestate_mapping(expected_prestate)
        and prefer_abort
    ):
        return "blocked"
    names = descriptor["names"]
    allowed_names = set(names.values())
    if not set(reserved).issubset(allowed_names) or descriptor_name != names["descriptor"]:
        return "blocked"
    candidate = _dir_read_plain(parent_lease, names["candidate"])
    backup = _dir_read_plain(parent_lease, names["backup"])
    quarantine = _dir_read_plain(parent_lease, names["quarantine"])
    canonical = _dir_read_plain(parent_lease, target.name)
    new_identity = _identity_from_mapping(descriptor["new"]["identity"])
    new_digest = descriptor["new"]["sha256"]

    def is_new(state: tuple[bytes, FileIdentity] | None) -> bool:
        return (
            state is not None
            and state[1] == new_identity
            and state[1].nlink == 1
            and _sha256_bytes(state[0]) == new_digest
        )

    def is_prestate(state: tuple[bytes, FileIdentity] | None) -> bool:
        if described_prestate is None:
            return state is None
        return state == described_prestate

    # Prepared but not promoted: remove the known candidate, descriptor last.
    if is_prestate(canonical) and is_new(candidate) and backup is None and quarantine is None:
        try:
            _dir_unlink_known(parent_lease, names["candidate"], candidate[0])
            _dir_unlink_known(parent_lease, names["descriptor"], descriptor_state[0])
            parent_lease.flush()
        except EvidenceIntegrityError:
            return "blocked"
        return "aborted" if not _reserved_transaction_names(parent_lease, target.name) else "blocked"

    # A foreign writer won immediately before a no-replace/exchange attempt;
    # the still-exact candidate proves our CAS did not consume it.  Preserve
    # the foreign canonical bytes and close only our exact prepared residue.
    if (
        not is_new(canonical)
        and is_new(candidate)
        and backup is None
        and quarantine is None
    ):
        try:
            _dir_unlink_known(parent_lease, names["candidate"], candidate[0])
            _dir_unlink_known(parent_lease, names["descriptor"], descriptor_state[0])
            parent_lease.flush()
        except EvidenceIntegrityError:
            return "blocked"
        return "aborted" if not _reserved_transaction_names(parent_lease, target.name) else "blocked"

    if not is_new(canonical) or quarantine is not None:
        return "blocked"

    if described_prestate is None:
        displaced_name = None
        displaced = None
        if candidate is not None or backup is not None:
            return "blocked"
    elif os.name == "nt":
        displaced_name = names["backup"]
        displaced = backup
        if candidate is not None or displaced is None:
            return "blocked"
    else:
        if (candidate is None) == (backup is None):
            return "blocked"
        displaced_name = names["candidate"] if candidate is not None else names["backup"]
        displaced = candidate if candidate is not None else backup

    # A restore is itself a privileged mutation.  Bind it to the descriptor's
    # exact expected prestate before either the abort or commit path can use
    # the observed displaced file; an attacker-controlled replacement must
    # never become the restored canonical file merely because it was observed
    # immediately before the CAS.
    if described_prestate is not None and displaced != described_prestate:
        return "blocked"

    if prefer_abort:
        try:
            if described_prestate is None:
                _move_target_to_quarantine(
                    parent_lease,
                    target.name,
                    names["quarantine"],
                    expected_target=canonical,
                )
            else:
                assert displaced_name is not None and displaced is not None
                _restore_displaced_target(
                    parent_lease,
                    target_name=target.name,
                    displaced_name=displaced_name,
                    quarantine_name=names["quarantine"],
                    expected_target=canonical,
                    expected_displaced=displaced,
                )
            restored = _dir_read_plain(parent_lease, target.name)
            if described_prestate is None:
                if restored is not None:
                    return "blocked"
            elif displaced is None or restored != displaced:
                return "blocked"
            known_new = _dir_read_plain(parent_lease, names["quarantine"])
            if known_new is None and os.name != "nt" and described_prestate is not None:
                known_new = _dir_read_plain(parent_lease, names["candidate"])
                cleanup_name = names["candidate"]
            else:
                cleanup_name = names["quarantine"]
            if not is_new(known_new):
                return "blocked"
            _dir_unlink_known(parent_lease, cleanup_name, known_new[0])
            for residue_name in (names["candidate"], names["backup"]):
                residue = _dir_read_plain(parent_lease, residue_name)
                if residue is not None:
                    # Only the displaced bytes may remain after a platform
                    # call reported an ambiguous effect.
                    if displaced is None or residue != displaced:
                        return "blocked"
                    _dir_unlink_known(parent_lease, residue_name, residue[0])
            _dir_unlink_known(parent_lease, names["descriptor"], descriptor_state[0])
            parent_lease.flush()
        except EvidenceIntegrityError:
            return "blocked"
        return "aborted" if not _reserved_transaction_names(parent_lease, target.name) else "blocked"

    # A committed recovery is accepted only with the exact displaced prestate
    # (or an exact absence transaction), current source/spec, and valid new
    # envelope.  Foreign displacement is never committed.
    try:
        _validate_completion_bytes(canonical[0], snapshot, spec)
        _dir_flush_file(parent_lease, target.name)
        parent_lease.flush()
    except EvidenceIntegrityError:
        return "blocked"

    def exact_committed_residue_proof() -> bool:
        """Prove that a failed cleanup remains wholly recoverable.

        A descriptor by itself is not recovery evidence for an existing-file
        transaction: the exact displaced prestate must still be present too.
        This proof is deliberately repeated after an ambiguous cleanup error,
        because an unlink implementation may have removed a name before
        reporting failure.
        """

        try:
            current_canonical = _dir_read_plain(parent_lease, target.name)
            current_descriptor = _dir_read_plain(parent_lease, names["descriptor"])
            current_candidate = _dir_read_plain(parent_lease, names["candidate"])
            current_backup = _dir_read_plain(parent_lease, names["backup"])
            current_quarantine = _dir_read_plain(parent_lease, names["quarantine"])
            current_reserved = _reserved_transaction_names(parent_lease, target.name)
        except EvidenceIntegrityError:
            return False
        if current_canonical != canonical or current_descriptor != descriptor_state:
            return False
        if current_quarantine is not None:
            return False
        if described_prestate is None:
            expected_reserved = (names["descriptor"],)
            return (
                current_candidate is None
                and current_backup is None
                and current_reserved == tuple(sorted(expected_reserved))
            )
        assert displaced_name is not None
        displaced_states = {
            names["candidate"]: current_candidate,
            names["backup"]: current_backup,
        }
        other_displaced_name = (
            names["backup"]
            if displaced_name == names["candidate"]
            else names["candidate"]
        )
        expected_reserved = (displaced_name, names["descriptor"])
        return (
            displaced_states[displaced_name] == described_prestate
            and displaced_states[other_displaced_name] is None
            and current_reserved == tuple(sorted(expected_reserved))
        )

    # E1 cleanup may leave a recoverable diagnostic transaction only while
    # the complete descriptor + displaced proof is still intact.  Once any
    # residue has been removed, an error is fail-stop rather than a public
    # committed result.  The descriptor is always removed last.
    if not exact_committed_residue_proof():
        return "blocked"
    removed_any = False
    try:
        for residue_name in (names["candidate"], names["backup"], names["quarantine"]):
            residue = _dir_read_plain(parent_lease, residue_name)
            if residue is not None:
                if described_prestate is None or residue != described_prestate:
                    return "blocked"
                _dir_unlink_known(parent_lease, residue_name, residue[0])
                removed_any = True
        _dir_unlink_known(parent_lease, names["descriptor"], descriptor_state[0])
        removed_any = True
        parent_lease.flush()
    except EvidenceIntegrityError:
        return "committed" if not removed_any and exact_committed_residue_proof() else "blocked"
    try:
        final_reserved = _reserved_transaction_names(parent_lease, target.name)
        final_canonical = _dir_read_plain(parent_lease, target.name)
    except EvidenceIntegrityError:
        return "blocked"
    return "committed" if not final_reserved and final_canonical == canonical else "blocked"


def _resolve_path_transaction_step(
    *,
    target: Path,
    snapshot: RepositorySnapshot,
    spec: ProducerSpec,
    expected_prestate: tuple[bytes, FileIdentity] | None,
    prefer_abort: bool,
) -> str:
    """Perform one bounded path-scoped recovery decision."""

    phase = _CommitPhase()
    try:
        with _open_directory_leases(target.parent, phase) as leases:
            parent_lease = leases[-1]
            with _publication_lock(parent_lease, target, phase):
                state = _resolve_path_transaction_held(
                    parent_lease=parent_lease,
                    target=target,
                    snapshot=snapshot,
                    spec=spec,
                    expected_prestate=expected_prestate,
                    prefer_abort=prefer_abort,
                )
                if state == "committed":
                    phase.committed = True
                return state if state in {"committed", "aborted"} else "blocked"
    except BaseException:
        return "blocked"


def canonical_output_path(snapshot: RepositorySnapshot, spec: ProducerSpec) -> Path:
    expected = CANONICAL_OUTPUTS.get(spec.producer_id)
    if expected != spec.canonical_output_relpath:
        _integrity("canonical_output_literal_mismatch", phase="path")
    target = snapshot.root / Path(*PurePosixPath(expected).parts)
    if not path_is_within(target.parent, snapshot.root):
        _integrity("canonical_output_outside_repository", phase="path")
    _assert_plain_directory_chain(target.parent)
    return target


def _publish_completion_transaction(
    *,
    snapshot: RepositorySnapshot,
    spec: ProducerSpec,
    outcome: ProducerOutcome,
    defer_commit_cleanup: bool,
) -> int:
    # Canonical release evidence is writable only from the validated frozen
    # child.  Keeping this assertion at the mutation primitive prevents an
    # imported outer process from reaching publication even through a private
    # symbol.
    validate_isolated_child(bound_root=snapshot.root)
    envelope = build_completion_envelope(snapshot=snapshot, spec=spec, outcome=outcome)
    new_content = serialize_completion_envelope(envelope)
    _validate_completion_bytes(new_content, snapshot, spec)
    target = canonical_output_path(snapshot, spec)
    public_exit = EXIT_PASS if outcome.status == "pass" else EXIT_HOLD_NONPASS
    phase = _CommitPhase()
    with _open_directory_leases(target.parent, phase) as leases:
        parent_lease = leases[-1]
        with _publication_lock(parent_lease, target, phase):
            for lease in leases:
                lease.revalidate()
            parent_lease.flush()
            revalidate_repository_snapshot(snapshot)
            prestate = _dir_read_plain(parent_lease, target.name)
            prior_state = _resolve_path_transaction_held(
                parent_lease=parent_lease,
                target=target,
                snapshot=snapshot,
                spec=spec,
                expected_prestate=prestate,
                prefer_abort=False,
            )
            if prior_state == "committed":
                existing = _dir_read_plain(parent_lease, target.name)
                if existing is None:
                    raise EvidenceFailStop()
                existing_envelope = _validate_completion_bytes(existing[0], snapshot, spec)
                phase.committed = True
                return (
                    EXIT_PASS
                    if existing_envelope["status"] == "pass"
                    else EXIT_HOLD_NONPASS
                )
            if prior_state != "aborted" or _reserved_transaction_names(
                parent_lease, target.name
            ):
                raise EvidenceFailStop()
            nonce = secrets.token_hex(16)
            names = _transaction_names(target.name, nonce)
            descriptor_content: bytes | None = None
            try:
                candidate_identity = _dir_write_exclusive(
                    parent_lease, names["candidate"], new_content
                )
                if candidate_identity.nlink != 1 or not _same_volume(
                    parent_lease, candidate_identity
                ):
                    _integrity("candidate_volume_or_link_invalid", phase="durability")
                descriptor = _transaction_descriptor(
                    target=target,
                    nonce=nonce,
                    names=names,
                    prestate=prestate,
                    new_content=new_content,
                    new_identity=candidate_identity,
                    snapshot=snapshot,
                    spec=spec,
                    status=outcome.status,
                    public_exit=public_exit,
                )
                descriptor_content = _canonical_json_bytes(descriptor)
                _dir_write_exclusive(
                    parent_lease, names["descriptor"], descriptor_content
                )
            except BaseException as exc:
                try:
                    descriptor_state = _dir_read_plain(
                        parent_lease, names["descriptor"]
                    )
                    if descriptor_state is not None:
                        if (
                            descriptor_content is None
                            or descriptor_state[0] != descriptor_content
                        ):
                            raise EvidenceFailStop()
                        _dir_unlink_known(
                            parent_lease,
                            names["descriptor"],
                            descriptor_state[0],
                        )
                    candidate_state = _dir_read_plain(
                        parent_lease, names["candidate"]
                    )
                    if candidate_state is not None:
                        if candidate_state[0] != new_content:
                            raise EvidenceFailStop()
                        _dir_unlink_known(
                            parent_lease,
                            names["candidate"],
                            candidate_state[0],
                        )
                    parent_lease.flush()
                    if _reserved_transaction_names(parent_lease, target.name):
                        raise EvidenceFailStop()
                except EvidenceFailStop:
                    raise
                except BaseException as cleanup_error:
                    raise EvidenceFailStop() from cleanup_error
                if isinstance(exc, EvidenceFailStop):
                    raise
                raise EvidenceIntegrityError(
                    "transaction_preparation_aborted", phase="durability"
                ) from exc
            try:
                for lease in leases:
                    lease.revalidate()
                parent_lease.flush()
                revalidate_repository_snapshot(
                    snapshot,
                    allowed_changed_paths=(
                        target,
                        target.parent / names["candidate"],
                        target.parent / names["descriptor"],
                    ),
                )
                # Final candidate/prestate checks are immediately adjacent to
                # the CAS.  The CAS captures a foreign winner rather than
                # overwriting it invisibly.
                candidate = _dir_read_plain(parent_lease, names["candidate"])
                current = _dir_read_plain(parent_lease, target.name)
                if (
                    candidate is None
                    or candidate[0] != new_content
                    or candidate[1] != candidate_identity
                    or candidate[1].nlink != 1
                    or current != prestate
                ):
                    _integrity("canonical_changed_before_replace", phase="durability")
                _index_lock_absent(snapshot.index_lock_path)
                if prestate is None:
                    _cas_promote_absent(
                        parent_lease,
                        target.name,
                        names["candidate"],
                        expected_candidate=candidate,
                    )
                else:
                    _cas_exchange_existing(
                        parent_lease,
                        target.name,
                        names["candidate"],
                        names["backup"],
                        expected_target=current,
                        expected_candidate=candidate,
                    )
                canonical = _dir_read_plain(parent_lease, target.name)
                if (
                    canonical is None
                    or canonical[0] != new_content
                    or canonical[1] != candidate_identity
                    or canonical[1].nlink != 1
                ):
                    _integrity("canonical_promotion_verification_failed", phase="durability")
                displaced = _dir_read_plain(
                    parent_lease,
                    names["backup"],
                )
                if prestate is None:
                    if displaced is not None:
                        _integrity("unexpected_displaced_target", phase="durability")
                elif displaced != prestate:
                    _integrity("foreign_target_displaced", phase="durability")
                _dir_flush_file(parent_lease, target.name)
                for lease in leases:
                    lease.revalidate()
                parent_lease.flush()
                revalidate_repository_snapshot(
                    snapshot,
                    allowed_changed_paths=tuple(
                        target.parent / name
                        for name in (target.name, *names.values())
                    ),
                )
                _validate_completion_bytes(canonical[0], snapshot, spec)
                phase.committed = True
            except BaseException as exc:
                state = _resolve_path_transaction_held(
                    parent_lease=parent_lease,
                    target=target,
                    snapshot=snapshot,
                    spec=spec,
                    expected_prestate=prestate,
                    prefer_abort=True,
                )
                if state != "aborted":
                    raise EvidenceFailStop() from exc
                if isinstance(exc, EvidenceFailStop):
                    raise
                raise EvidenceIntegrityError(
                    "canonical_publication_aborted", phase="durability"
                ) from exc
            if defer_commit_cleanup:
                with contextlib.suppress(EvidenceIntegrityError):
                    _reserved_transaction_names(parent_lease, target.name)
                return public_exit
            state = _resolve_path_transaction_held(
                parent_lease=parent_lease,
                target=target,
                snapshot=snapshot,
                spec=spec,
                expected_prestate=prestate,
                prefer_abort=False,
            )
            if state != "committed":
                raise EvidenceFailStop()
            final = _dir_read_plain(parent_lease, target.name)
            if final is None or final[0] != new_content:
                raise EvidenceFailStop()
            return public_exit


def run_integrity_boundary(operation: Callable[[], int]) -> int:
    """Return only 0/1 from explicit completions; map every exception to 2."""

    try:
        result = operation()
    except EvidenceFailStop:
        raise
    except BaseException:
        return EXIT_INTEGRITY
    return (
        result
        if type(result) is int and result in {EXIT_PASS, EXIT_HOLD_NONPASS}
        else EXIT_INTEGRITY
    )


def run_sealed_producer(
    *,
    executing_file: Path | str,
    raw_argv: Sequence[str],
    bootstrap_spec: ProducerSpec,
    required_files: Sequence[str],
) -> int:
    """Run the closed producer lifecycle and return only 0, 1, or 2.

    The tiny outer producer must select its sealed mode before importing this
    module.  This shared boundary repeats that check as its first operation,
    freezes the runtime and producer, performs at most one isolated reexec,
    then compiles the producer from the verified Git blob under a non-``main``
    module name.  The frozen module must expose the identical ``PRODUCER_SPEC``
    and a ``produce(snapshot, argv)`` callable returning ``ProducerOutcome``.
    """

    try:
        validated_argv = validate_sealed_argv(raw_argv, bootstrap_spec.argv_contract)
        if sys.flags.isolated:
            _integrity("outer_process_must_not_be_isolated_child", phase="isolation")
        required = validate_repo_relpath_list(
            (
                RUNTIME_RELPATH,
                bootstrap_spec.producer_relpath,
                *tuple(required_files),
            )
        )
        snapshot = capture_repository_snapshot(
            startup_cwd=Path.cwd(),
            required_files=required,
        )
        verify_executing_source(
            snapshot,
            declared_relpath=RUNTIME_RELPATH,
            executing_file=__file__,
        )
        verify_executing_source(
            snapshot,
            declared_relpath=bootstrap_spec.producer_relpath,
            executing_file=executing_file,
        )
        parent_result = ensure_isolated_once(
            snapshot=snapshot,
            executing_file=executing_file,
            producer_relpath=bootstrap_spec.producer_relpath,
            validated_argv=validated_argv,
            bootstrap_spec=bootstrap_spec,
        )
        return (
            parent_result
            if type(parent_result) is int
            and parent_result in {EXIT_PASS, EXIT_HOLD_NONPASS, EXIT_INTEGRITY}
            else EXIT_INTEGRITY
        )
    except EvidenceFailStop:
        raise
    except BaseException:
        return EXIT_INTEGRITY


__all__ = [
    "CANONICAL_OUTPUTS",
    "ENVELOPE_SCHEMA_VERSION",
    "EXIT_HOLD_NONPASS",
    "EXIT_INTEGRITY",
    "EXIT_PASS",
    "PRIVATE_EXIT_HOLD",
    "PRIVATE_EXIT_INTEGRITY",
    "PRIVATE_EXIT_PASS",
    "EvidenceFailStop",
    "EvidenceIntegrityError",
    "FileIdentity",
    "ProducerOutcome",
    "ProducerSpec",
    "RUNTIME_RELPATH",
    "RepositorySnapshot",
    "SOURCE_NORMALIZATION",
    "SealedArgvContract",
    "TrackedBlob",
    "build_completion_envelope",
    "canonical_output_path",
    "capture_repository_snapshot",
    "create_isolation_prefix",
    "ensure_isolated_once",
    "exec_verified_source",
    "fresh_process_environment",
    "p1_release_mode_requested",
    "path_is_within",
    "revalidate_repository_snapshot",
    "run_integrity_boundary",
    "run_sealed_producer",
    "scrub_and_validate_child_environment",
    "serialize_completion_envelope",
    "validate_isolated_child",
    "validate_repo_relpath",
    "validate_repo_relpath_list",
    "validate_sealed_argv",
    "verify_executing_source",
]
