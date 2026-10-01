#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""F26 S3: the routing push receipt ``wd.push-receipt.v1`` (lane-observed evidence, authority none).

A push receipt records that, at a caller-stated time, the remote branch named exactly one commit and
that commit is the lane's local branch head. It is the input of the attempt producer's
``remote_verified`` (fable-5 design c96124d0, section 3c). It is NOT authorship proof: a lane writes its
own receipt, so the record says ``evidence: lane_observed_not_authorship`` and ``authority: none``.

Producer (pure over an injected Git port; this module never runs git, reads a clock or the network):

    build_receipt(attempt_id=, task_id=, worker=, branch=, observed_remote_utc=, git=port) -> dict

* ``git`` is a port with ``run(args: tuple[str, ...]) -> (returncode: int, stdout: str, stderr: str)``.
  The caller wires it to ``git -C <worktree>`` and owns the CLAUDE.md rule-7 wait: it calls this only
  after its own ls-remote observation window. Nothing here retries, sleeps or guesses a time.
* Steps: ``rev-parse --verify --end-of-options refs/heads/<branch>^{commit}`` (exactly 40 hex),
  ``rev-parse --verify --end-of-options <commit>^{tree}`` (exactly 40 hex), then
  ``ls-remote --refs origin refs/heads/<branch>``, which must return exactly ONE line
  ``<sha>\\trefs/heads/<branch>`` with the exact ref and ``sha == commit``.
* Any refusal raises ``PushReceiptRefused`` with a stable ``reason``; no receipt exists then. A port
  error (an ``Exception``) is a refusal; a cancellation (``KeyboardInterrupt``, ``SystemExit``) propagates.

Persistence (opt-in; only with a caller-explicit approved directory):

    persist_receipt(receipt, directory, approved_root=) -> {"path", "status": "created" | "unchanged",
                                                             "cleanup": "ok" | "close_failed:<Type>"}
    load_receipt(path) -> dict

* The file is ``<receipt_digest>.json``: the canonical UTF-8 JSON (sorted keys, no whitespace) plus a
  newline, at most 4096 bytes. It is published atomically: written to a unique temporary file in the
  same directory, fsynced, then hard-linked to the final name, which fails if the name exists. So a
  reader sees the whole file or nothing under the final name.
* An identical retry is a no-op (``unchanged``); other bytes under the same name are ``receipt_conflict``.
  The existing name is read relative to the held directory handle without following a reparse point, so
  a link under that name is a conflict, never read through.
* ``directory``, ``approved_root`` and the ``load_receipt`` path must be exactly the concrete ``Path``
  class (no subclass, whose ``str`` and ``parts`` could disagree). The directory must exist, be absolute
  on drive C:, equal or lie inside ``approved_root`` and contain no ``..`` segment. Temporary roots (``tempfile.gettempdir()``, resolved) are refused (CLAUDE.md rule 1).
* Containment is enforced, not only checked (Windows only; elsewhere ``platform_unsupported``): every
  component from the drive root to the directory is opened as a handle without the reparse point being
  followed and without delete sharing, so for the whole operation no component can be renamed, removed
  or replaced by a junction. Each handle must be a directory with no reparse point whose final path is
  exactly the given component (so no link, junction or 8.3 alias). The temporary file is created and
  hard-linked RELATIVE to the directory handle (NtCreateFile / FileLinkInformation), never by path, and
  is opened delete-on-close, so closing it, or the process ending, removes it without a path-based
  unlink. Its final path is checked against the directory before any byte is written.
* Cleanup never masks the outcome: a close failure is reported as ``cleanup`` on success, as
  ``.cleanup`` on a refusal, and as an exception note on a cancellation, which always propagates.
* Limitations (named, not closed): the directory itself stays shareable for writing (the hard link needs
  it), so a writer inside ``approved_root`` CAN turn a still-empty directory into a junction in place
  between the lock and the temporary create. Measured (elevated and Basic-User tokens, real twin): the
  create relative to the held handle is then refused by NTFS (``receipt_create_failed:c0000280``,
  STATUS_REPARSE_POINT_NOT_RESOLVED) and no name or byte lands anywhere; the final-path check
  (``directory_drifted``) stays as a second line. The enforcement relies on that NTFS behaviour, on NTFS
  refusing a reparse point on a non-empty directory and on share-mode semantics. The volatile-root
  check on the held final path overlaps the lexical check (the component final-path check already refuses
  any path whose final form differs) and is kept as a second line. A process already holding delete
  access on a component makes this refuse
  (``directory_lock_failed``). An OS crash or power loss can leave an orphan ``.tmp`` file. The hash
  proves content, not who wrote it. ``load_receipt`` is a content check only: bytes, canonical form and
  name, never location or containment.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Protocol
import uuid

SCHEMA = "wd.push-receipt.v1"
EVIDENCE = "lane_observed_not_authorship"
AUTHORITY = "none"
REMOTE = "origin"
MAX_RECEIPT_BYTES = 4096
RECEIPT_KEYS = frozenset({"schema", "attempt_id", "task_id", "worker", "branch", "commit", "tree", "remote_ref",
                          "ls_remote_sha", "observed_remote_utc", "evidence", "authority", "receipt_digest"})
_SHA = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_TASK = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}")
_WORKER = re.compile(r"[a-z][a-z0-9_-]{1,32}")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_STAMP = "%Y-%m-%dT%H:%M:%S.%fZ"
_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


class PushReceiptRefused(Exception):
    """No receipt: ``reason`` is a stable code; ``cleanup`` reports a temporary-file close failure."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
        self.cleanup = "ok"


class GitPort(Protocol):
    def run(self, args: tuple[str, ...]) -> tuple[int, str, str]: ...


def _refuse(reason: str):
    raise PushReceiptRefused(reason)


def _exact(value: Any, pattern: re.Pattern) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _branch_ok(branch: Any) -> bool:
    return (_exact(branch, _BRANCH) and ".." not in branch and "//" not in branch and not branch.endswith("/")
            and not branch.endswith(".lock") and not branch.startswith("refs/") and "/." not in branch)


def _utc_text(value: Any) -> str:
    if type(value) is not str:
        _refuse("observed_remote_utc_invalid")
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None or moment.utcoffset() is None:
            _refuse("observed_remote_utc_invalid")
        return moment.astimezone(timezone.utc).strftime(_STAMP)
    except (ValueError, OverflowError):   # an offset can carry 0001-01-01 or 9999-12-31 out of range
        _refuse("observed_remote_utc_invalid")


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _digest(fields: dict) -> str:
    return hashlib.sha256(canonical_bytes({k: v for k, v in fields.items() if k != "receipt_digest"})).hexdigest()


def _git(git: Any, step: str, args: tuple[str, ...]) -> str:
    try:
        result = git.run(args)
    except Exception as exc:  # noqa: BLE001 - a port failure is a refusal; cancellation (BaseException) propagates
        _refuse("git_port_error:%s:%s" % (step, type(exc).__name__))
    if (type(result) is not tuple or len(result) != 3 or type(result[0]) is not int
            or type(result[1]) is not str or type(result[2]) is not str):
        _refuse("git_port_malformed:" + step)
    code, out, _err = result
    if code != 0:
        _refuse("git_failed:" + step)
    return out


def _one_sha(out: str, step: str) -> str:
    lines = out.splitlines()
    if len(lines) != 1 or not _exact(lines[0], _SHA) or out not in (lines[0], lines[0] + "\n"):
        _refuse("git_output_malformed:" + step)
    return lines[0]


def build_receipt(*, attempt_id: Any, task_id: Any, worker: Any, branch: Any, observed_remote_utc: Any,
                  git: Any) -> dict:
    """The receipt for one remote-verified push, or PushReceiptRefused. Every identity field is caller-explicit."""
    if not _exact(attempt_id, _HEX64):
        _refuse("attempt_id_invalid")
    if not _exact(task_id, _TASK):
        _refuse("task_id_invalid")
    if not _exact(worker, _WORKER):
        _refuse("worker_invalid")
    if not _branch_ok(branch):
        _refuse("branch_invalid")
    observed = _utc_text(observed_remote_utc)
    remote_ref = "refs/heads/" + branch
    commit = _one_sha(_git(git, "local_head", ("rev-parse", "--verify", "--end-of-options", remote_ref + "^{commit}")),
                      "local_head")
    tree = _one_sha(_git(git, "tree", ("rev-parse", "--verify", "--end-of-options", commit + "^{tree}")), "tree")
    listing = _git(git, "ls_remote", ("ls-remote", "--refs", REMOTE, remote_ref))
    lines = [line for line in listing.splitlines() if line != ""]
    if not lines:
        _refuse("remote_ref_absent")
    if len(lines) != 1:
        _refuse("ls_remote_ambiguous")
    parts = lines[0].split("\t")
    if len(parts) != 2 or not _exact(parts[0], _SHA):
        _refuse("ls_remote_malformed")
    if parts[1] != remote_ref:
        _refuse("ls_remote_ref_mismatch")
    if parts[0] != commit:
        _refuse("remote_moved")
    receipt = {"schema": SCHEMA, "attempt_id": attempt_id, "task_id": task_id, "worker": worker, "branch": branch,
               "commit": commit, "tree": tree, "remote_ref": remote_ref, "ls_remote_sha": parts[0],
               "observed_remote_utc": observed, "evidence": EVIDENCE, "authority": AUTHORITY}
    receipt["receipt_digest"] = _digest(receipt)
    return receipt


def validate_receipt(receipt: Any) -> dict:
    """The receipt if every field is exact and the digest recomputes; else PushReceiptRefused."""
    # Exact built-in types BEFORE any hash or equality, so a caller's str subclass never runs its hooks.
    if type(receipt) is not dict or not all(type(key) is str for key in receipt) or set(receipt) != RECEIPT_KEYS:
        _refuse("receipt_malformed")
    if not (type(receipt["schema"]) is str and receipt["schema"] == SCHEMA
            and type(receipt["evidence"]) is str and receipt["evidence"] == EVIDENCE
            and type(receipt["authority"]) is str and receipt["authority"] == AUTHORITY):
        _refuse("receipt_malformed")
    if not (_exact(receipt["attempt_id"], _HEX64) and _exact(receipt["task_id"], _TASK)
            and _exact(receipt["worker"], _WORKER) and _branch_ok(receipt["branch"])
            and _exact(receipt["commit"], _SHA) and _exact(receipt["tree"], _SHA)
            and _exact(receipt["ls_remote_sha"], _SHA) and _exact(receipt["receipt_digest"], _HEX64)
            and type(receipt["remote_ref"]) is str and receipt["remote_ref"] == "refs/heads/" + receipt["branch"]
            and receipt["ls_remote_sha"] == receipt["commit"]):
        _refuse("receipt_malformed")
    if _utc_text(receipt["observed_remote_utc"]) != receipt["observed_remote_utc"]:
        _refuse("receipt_malformed")
    if _digest(receipt) != receipt["receipt_digest"]:
        _refuse("receipt_digest_mismatch")
    return receipt


def _forbidden_roots() -> list[Path]:
    return [Path(tempfile.gettempdir())]


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _inside(child: Path, parent: Path) -> bool:
    child_text, parent_text = _norm(child), _norm(parent)
    try:
        return os.path.commonpath([child_text, parent_text]) == parent_text
    except ValueError:
        return False


def _volatile(path: Path) -> bool:
    for root in _forbidden_roots():
        if _inside(path, root) or _inside(path, Path(os.path.realpath(root))):   # realpath expands 8.3 aliases
            return True
    return False


_PATH = type(Path())   # the exact concrete Path class; a subclass can make str() and parts disagree (RCO1 E1)


def _safe_directory(directory: Any, approved_root: Any) -> Path:
    if type(directory) is not _PATH or type(approved_root) is not _PATH:
        _refuse("directory_invalid")
    for candidate in (directory, approved_root):
        if not candidate.is_absolute() or ".." in candidate.parts or candidate.drive.upper() != "C:":
            _refuse("directory_invalid")
    if not _inside(directory, approved_root):
        _refuse("directory_outside_approved_root")
    if _volatile(directory):
        _refuse("directory_volatile")
    return directory


_WINDOWS = os.name == "nt"
if _WINDOWS:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _HANDLE = wintypes.HANDLE
    _INVALID_HANDLE = _HANDLE(-1).value

    class _UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR)]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", _HANDLE),
                    ("ObjectName", ctypes.POINTER(_UnicodeString)), ("Attributes", wintypes.ULONG),
                    ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [("Pointer", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    class _AttributeTagInfo(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    _kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, _HANDLE]
    _kernel32.CreateFileW.restype = _HANDLE
    _kernel32.CloseHandle.argtypes = [_HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetFinalPathNameByHandleW.argtypes = [_HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _kernel32.GetFileInformationByHandleEx.argtypes = [_HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _ntdll.NtCreateFile.argtypes = [ctypes.POINTER(_HANDLE), wintypes.ULONG, ctypes.POINTER(_ObjectAttributes),
                                    ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
                                    wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    _ntdll.NtCreateFile.restype = ctypes.c_long
    _ntdll.NtSetInformationFile.argtypes = [_HANDLE, ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p,
                                            wintypes.ULONG, ctypes.c_int]
    _ntdll.NtSetInformationFile.restype = ctypes.c_long

# Directory handles share read and write but NOT delete, so no component can be renamed or removed while
# held, and open the reparse point itself. Ancestors take only traverse + read attributes + synchronize
# (a non-elevated token cannot open C:\ for adding files; traverse still takes part in sharing). Only
# the target directory takes list + add file, which the relative create and link need.
_ANCESTOR_ACCESS = 0x20 | 0x80 | 0x100000
_DIR_ACCESS = 0x1 | 0x2 | 0x20 | 0x80 | 0x100000
_DIR_SHARE = 0x1 | 0x2
_DIR_FLAGS = 0x02000000 | 0x00200000          # FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT
_DIRECTORY = 0x10
_FILE_ATTRIBUTE_TAG_INFO = 9
# Temporary file: generic write + delete + synchronize + read attributes, exclusive, FILE_CREATE,
# non-directory + synchronous I/O + delete-on-close.
_TMP_ACCESS = 0x40000000 | 0x10000 | 0x100000 | 0x80
_TMP_OPTIONS = 0x40 | 0x20 | 0x1000
_FILE_LINK_INFORMATION = 11
_STATUS_OBJECT_NAME_COLLISION = 0xC0000035


def _final_path(handle: Any) -> str:
    buffer = ctypes.create_unicode_buffer(32768)   # the longest \\?\ path
    length = _kernel32.GetFinalPathNameByHandleW(handle, buffer, 32768, 0)
    text = buffer.value if 0 < length < 32768 else ""
    return text[4:] if text.startswith("\\\\?\\") and not text.startswith("\\\\?\\UNC\\") else ""


def _close_handles(handles: list) -> None:
    for handle in reversed(handles):
        _kernel32.CloseHandle(handle)


def _lock_directory(folder: Path) -> list:
    """Handles from the drive root to ``folder``, each verified; the caller closes them."""
    handles: list = []
    try:
        probe = Path(folder.anchor)
        parts = ("",) + folder.parts[1:]
        for index, part in enumerate(parts):
            probe = probe / part if part else probe
            access = _DIR_ACCESS if index == len(parts) - 1 else _ANCESTOR_ACCESS
            handle = _kernel32.CreateFileW(str(probe), access, _DIR_SHARE, None, 3, _DIR_FLAGS, None)
            if handle in (None, _INVALID_HANDLE):
                code = ctypes.get_last_error()
                _refuse("directory_missing" if code in (2, 3, 267) else "directory_lock_failed:%d" % code)
            handles.append(handle)
            info = _AttributeTagInfo()
            if not _kernel32.GetFileInformationByHandleEx(handle, _FILE_ATTRIBUTE_TAG_INFO, ctypes.byref(info),
                                                          ctypes.sizeof(info)):
                _refuse("directory_lock_failed:%d" % ctypes.get_last_error())
            if not info.FileAttributes & _DIRECTORY:
                _refuse("directory_missing")
            if info.FileAttributes & _REPARSE or _norm(Path(_final_path(handle) or "?")) != _norm(probe):
                _refuse("path_has_link_or_reparse")
        if _volatile(Path(_final_path(handles[-1]))):
            _refuse("directory_volatile")
        return handles
    except BaseException:
        _close_handles(handles)
        raise


def _create_relative(directory: Any, name: str) -> Any:
    text = _UnicodeString(len(name) * 2, len(name) * 2, name)
    attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), directory, ctypes.pointer(text), 0x40, None, None)
    status_block, handle = _IoStatusBlock(), _HANDLE()
    status = _ntdll.NtCreateFile(ctypes.byref(handle), _TMP_ACCESS, ctypes.byref(attributes), ctypes.byref(status_block),
                                 None, 0x80, 0, 2, _TMP_OPTIONS, None, 0) & 0xFFFFFFFF
    if status != 0:
        _refuse("receipt_create_failed:%08x" % status)
    return handle.value


def _read_relative(directory: Any, name: str) -> bytes | None:
    """The bytes of the existing ``name`` in the held directory, opened relative to its handle without
    following a reparse point (RCO1 E2); None (so a conflict) for a link, a non-file or an oversized file."""
    text = _UnicodeString(len(name) * 2, len(name) * 2, name)
    attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), directory, ctypes.pointer(text), 0x40, None, None)
    handle = _HANDLE()
    # read data + read attributes + synchronize; share read; FILE_OPEN; non-directory + synchronous I/O +
    # FILE_OPEN_REPARSE_POINT.
    status = _ntdll.NtCreateFile(ctypes.byref(handle), 0x1 | 0x80 | 0x100000, ctypes.byref(attributes),
                                 ctypes.byref(_IoStatusBlock()), None, 0, 0x1, 1, 0x40 | 0x20 | 0x200000, None,
                                 0) & 0xFFFFFFFF
    if status != 0:
        _refuse("receipt_collision_unreadable:%08x" % status)
    try:
        info = _AttributeTagInfo()
        if (not _kernel32.GetFileInformationByHandleEx(handle, _FILE_ATTRIBUTE_TAG_INFO, ctypes.byref(info),
                                                       ctypes.sizeof(info))
                or info.FileAttributes & (_REPARSE | _DIRECTORY)):
            return None
        descriptor = msvcrt.open_osfhandle(handle.value, os.O_RDONLY)
        handle = None   # the descriptor owns it now
        try:
            data = os.read(descriptor, MAX_RECEIPT_BYTES + 1)
        finally:
            try:
                os.close(descriptor)
            except OSError:   # a read-only close failure never replaces the conflict outcome (F4)
                pass
        return data if len(data) <= MAX_RECEIPT_BYTES else None
    finally:
        if handle is not None:
            _kernel32.CloseHandle(handle)


def _link_relative(handle: Any, directory: Any, name: str) -> int:
    class _LinkInformation(ctypes.Structure):
        _fields_ = [("Flags", wintypes.ULONG), ("RootDirectory", _HANDLE), ("FileNameLength", wintypes.ULONG),
                    ("FileName", wintypes.WCHAR * len(name))]
    information = _LinkInformation(0, directory, len(name) * 2, name)   # Flags 0: never replace an existing name
    return _ntdll.NtSetInformationFile(handle, ctypes.byref(_IoStatusBlock()), ctypes.byref(information),
                                       ctypes.sizeof(information), _FILE_LINK_INFORMATION) & 0xFFFFFFFF


def _read_bounded(path: Path) -> bytes:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & _REPARSE) or not stat.S_ISREG(info.st_mode):
        _refuse("receipt_file_not_regular")
    if info.st_size > MAX_RECEIPT_BYTES:
        _refuse("receipt_file_oversized")
    with open(path, "rb") as handle:
        data = handle.read(MAX_RECEIPT_BYTES + 1)
    if len(data) > MAX_RECEIPT_BYTES:
        _refuse("receipt_file_oversized")
    return data


def persist_receipt(receipt: Any, directory: Any, *, approved_root: Any) -> dict:
    """Publish one validated receipt under <receipt_digest>.json in a caller-approved directory (opt-in)."""
    receipt = validate_receipt(receipt)
    folder = _safe_directory(directory, approved_root)
    data = canonical_bytes(receipt) + b"\n"
    if len(data) > MAX_RECEIPT_BYTES:
        _refuse("receipt_oversized")
    if not _WINDOWS:
        _refuse("platform_unsupported")   # the containment below needs Windows handles; no unsafe fallback
    name = receipt["receipt_digest"] + ".json"
    temporary = ".%s.%s.tmp" % (receipt["receipt_digest"], uuid.uuid4().hex)
    handles = _lock_directory(folder)
    try:
        directory_handle = handles[-1]
        handle = _create_relative(directory_handle, temporary)
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY)
        except BaseException:
            _kernel32.CloseHandle(handle)   # delete-on-close removes the temporary file
            raise
        status, cleanup = None, "ok"
        try:
            if _norm(Path(_final_path(handle) or "?")) != _norm(Path(_final_path(directory_handle)) / temporary):
                _refuse("directory_drifted")
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if type(written) is not int or not 0 < written <= len(view):
                    _refuse("receipt_write_stalled")
                view = view[written:]
            os.fsync(descriptor)
            linked = _link_relative(handle, directory_handle, name)
            if linked == 0:
                status = "created"
            elif linked == _STATUS_OBJECT_NAME_COLLISION:
                if _read_relative(directory_handle, name) != data:
                    _refuse("receipt_conflict")
                status = "unchanged"
            else:
                _refuse("receipt_link_failed:%08x" % linked)
        except BaseException as primary:
            try:
                os.close(descriptor)
            except OSError as exc:   # never replaces the refusal or the cancellation
                cleanup = "close_failed:" + type(exc).__name__
                primary.add_note("push receipt cleanup: " + cleanup)
                if isinstance(primary, PushReceiptRefused):
                    primary.cleanup = cleanup
            raise
        try:
            os.close(descriptor)
        except OSError as exc:   # the receipt is published; report the cleanup, do not hide the status
            cleanup = "close_failed:" + type(exc).__name__
    finally:
        _close_handles(handles)
    return {"path": str(folder / name), "status": status, "cleanup": cleanup}


def _unique_pairs(pairs: list) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def load_receipt(path: Any) -> dict:
    """Read and verify one persisted receipt: bounded, regular file, strict JSON, name equals its digest."""
    if type(path) is not _PATH:
        _refuse("receipt_path_invalid")
    data = _read_bounded(path)
    try:
        value = json.loads(data.decode("ascii"), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda name: (_ for _ in ()).throw(ValueError(name)))
    except (ValueError, UnicodeDecodeError, RecursionError):
        _refuse("receipt_file_malformed")
    receipt = validate_receipt(value)
    if data != canonical_bytes(receipt) + b"\n":
        _refuse("receipt_file_not_canonical")
    if path.name != receipt["receipt_digest"] + ".json":
        _refuse("receipt_name_mismatch")
    return receipt
