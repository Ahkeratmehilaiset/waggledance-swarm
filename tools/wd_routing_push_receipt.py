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

    persist_receipt(receipt, directory, approved_root=) -> {"path", "status": "created" | "unchanged"}
    load_receipt(path) -> dict

* The file is ``<receipt_digest>.json``: the canonical UTF-8 JSON (sorted keys, no whitespace) plus a
  newline, at most 4096 bytes. It is published atomically: written to a unique temporary file in the
  same directory, fsynced, then hard-linked to the final name, which fails if the name exists. So a
  reader sees the whole file or nothing under the final name.
* An identical retry is a no-op (``unchanged``); other bytes under the same name are ``receipt_conflict``.
* The directory must exist, be absolute on drive C:, equal or lie inside ``approved_root``, contain no
  ``..`` segment, resolve to itself (no symlink or junction) and have no reparse point on any existing
  component. Temporary roots (``tempfile.gettempdir()``) are refused (CLAUDE.md rule 1).
* Limitations (named, not closed): a crash after the temporary write and before the link leaves an
  orphan ``.tmp`` file and no receipt; the hash proves content, not who wrote it; a writer with raw file
  access can delete or add files; filesystem atomicity is NTFS hard-link creation, not a transaction.
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
    """No receipt: ``reason`` is a stable code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


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
    except ValueError:
        _refuse("observed_remote_utc_invalid")
    if moment.tzinfo is None or moment.utcoffset() is None:
        _refuse("observed_remote_utc_invalid")
    return moment.astimezone(timezone.utc).strftime(_STAMP)


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
    if type(receipt) is not dict or set(receipt) != RECEIPT_KEYS:
        _refuse("receipt_malformed")
    if not (receipt["schema"] == SCHEMA and type(receipt["schema"]) is str and receipt["evidence"] == EVIDENCE
            and type(receipt["evidence"]) is str and receipt["authority"] == AUTHORITY
            and type(receipt["authority"]) is str):
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


def _no_reparse(path: Path) -> None:
    probe = Path(path.anchor)
    for part in path.parts[1:]:
        probe = probe / part
        try:
            info = os.lstat(probe)
        except FileNotFoundError:
            _refuse("directory_missing")
        if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & _REPARSE):
            _refuse("path_has_link_or_reparse")


def _safe_directory(directory: Any, approved_root: Any) -> Path:
    if not isinstance(directory, Path) or not isinstance(approved_root, Path):
        _refuse("directory_invalid")
    for candidate in (directory, approved_root):
        if not candidate.is_absolute() or ".." in candidate.parts or candidate.drive.upper() != "C:":
            _refuse("directory_invalid")
    if not _inside(directory, approved_root):
        _refuse("directory_outside_approved_root")
    if any(_inside(directory, root) for root in _forbidden_roots()):
        _refuse("directory_volatile")
    _no_reparse(directory)
    if not directory.is_dir():
        _refuse("directory_missing")
    if _norm(directory.resolve(strict=True)) != _norm(directory):
        _refuse("path_has_link_or_reparse")
    return directory


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
    final = folder / (receipt["receipt_digest"] + ".json")
    temporary = folder / (".%s.%s.tmp" % (receipt["receipt_digest"], uuid.uuid4().hex))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temporary, flags, 0o644)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(descriptor, view):]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, final)
            status = "created"
        except FileExistsError:
            if _read_bounded(final) != data:
                _refuse("receipt_conflict")
            status = "unchanged"
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return {"path": str(final), "status": status}


def _unique_pairs(pairs: list) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def load_receipt(path: Any) -> dict:
    """Read and verify one persisted receipt: bounded, regular file, strict JSON, name equals its digest."""
    if not isinstance(path, Path):
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
