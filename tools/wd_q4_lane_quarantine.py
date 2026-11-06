"""Persistent, fail-closed coordination primitive for a cooperative Q4 caller.

The trusted caller supplies the SAME already-provisioned local state directory on
every invocation, validates the lane and inputs, and calls ``acquire`` BEFORE
reading a query or starting an adapter. A per-lane exclusive-create pending file
serializes calls. It remains on UNKNOWN, exceptions and process crashes; there is
no timeout, automatic cleanup or reconciliation API. An ordinary confirmed
outcome can clear only its own unmodified marker after the chain's cleanup is
verified by the caller. This module does not inspect or attest child processes.

This coordinates cooperating processes, not hostile processes running as the
same OS user. The directory must be trusted, stable across restarts and outside
untrusted writer control; do not use a changing worktree-relative root without
an independently pinned mapping. The caller must pass only actual adapter
outcomes, not arbitrary input. No query, token, answer or path enters a marker.
The file is flushed with fsync before use; directory-entry durability after
power loss is filesystem-dependent and is not proven by this primitive. Path
checks do not defeat concurrent hostile filesystem replacement.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat


_LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_MAX_RECORD = 4096


class CallerUnavailable(RuntimeError):
    """The lane is busy, quarantined, or the marker cannot be verified."""

    def __init__(self):
        super().__init__("caller_unavailable")


def _regular_non_link(info):
    return (stat.S_ISREG(info.st_mode)
            and not (getattr(info, "st_file_attributes", 0) & _REPARSE))


def _trusted_directory(value):
    root = Path(value)
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("an absolute stable state directory is required")
    if root == root.parent:
        raise ValueError("a non-root state directory is required")
    for part in (root, *root.parents):
        info = part.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or (getattr(info, "st_file_attributes", 0) & _REPARSE)):
            raise ValueError("state directory must not traverse links or reparse points")
    return root


def _sha(value):
    if type(value) is not str or _HEX.fullmatch(value) is None:
        raise ValueError("a lowercase SHA256 is required")
    return value


class _Lease:
    def __init__(self, marker, expected):
        self._marker = marker
        self._expected = expected
        self._closed = False

    def complete_confirmed(self, result):
        """Clear only after the caller has independently verified chain cleanup.

        No exception or UNKNOWN result is a confirmation. This deliberately
        supplies no API to clear an older process's marker. If verification,
        reading or deletion fails, the marker remains or the outcome is unknown.
        """
        if (self._closed or type(result) is not dict
                or result.get("status") not in {"VALIDATED", "REFUSED", "TIMEOUT"}
                or result.get("cleanup") != "chain_confirmed"):
            raise CallerUnavailable()
        try:
            before = self._marker.lstat()
            if not _regular_non_link(before):
                raise CallerUnavailable()
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self._marker, flags)
            try:
                opened = os.fstat(fd)
                if not _regular_non_link(opened) or not os.path.samestat(before, opened):
                    raise CallerUnavailable()
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    actual = stream.read(_MAX_RECORD + 1)
            finally:
                os.close(fd)
            after = self._marker.lstat()
            if (not _regular_non_link(after) or not os.path.samestat(before, after)
                    or actual != self._expected):
                raise CallerUnavailable()
            self._marker.unlink()
            self._closed = True
        except (OSError, ValueError) as exc:
            raise CallerUnavailable() from exc


def acquire(state_root, lane, *, identity_sha256, descriptor_sha256):
    """Create a durable pending record before any query read or transport call.

    FileExistsError means busy OR quarantined; no stale-process guess is made.
    Filesystem errors must be treated as refusal by the caller. A partial record
    left by failed I/O also blocks future calls. The state root is not created.
    """
    if type(lane) is not str or lane not in _LANES:
        raise ValueError("unrecognized lane")
    identity = _sha(identity_sha256)
    descriptor = _sha(descriptor_sha256)
    root = _trusted_directory(state_root)
    marker = root / (lane + ".pending.json")
    record = {
        "schema": "wd.q4-caller-pending.v1", "lane": lane,
        "call_id": secrets.token_hex(16),
        "started_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pid": os.getpid(), "identity_sha256": identity, "descriptor_sha256": descriptor,
    }
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOINHERIT", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(marker, flags, 0o600)
    except FileExistsError as exc:
        raise CallerUnavailable() from exc
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            if stream.write(raw) != len(raw):
                raise OSError("short pending-record write")
            stream.flush()
        os.fsync(fd)
    finally:
        os.close(fd)
    return _Lease(marker, raw)
