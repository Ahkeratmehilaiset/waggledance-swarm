#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F8: queue transactions: lock order, compare-and-swap, WAL, outbox, recovery.

Dormant and tools-owned: nothing imports this module, it imports no ``waggledance``
code, it has no default runtime root and no default ports. Every call names an explicit
runtime root and injected ports; without a mutex port AND a claim-lock port a mutation
is refused before anything is read or written.

One transaction (``QueueTransactions.transact``):

1. BEFORE any lock, validates the operation, the idempotency key (1..512 characters) and
   the claim path: inside the root, a ``work_queue/claims/<name>.json`` file, no link or
   reparse point on any existing component;
2. takes the runtime-root mutex (``mutex_name(root)``) FIRST, then the exact legacy
   sibling claim lock ``<claim>.json.lock`` (``Enter-BridgeClaimLock`` spelling, an
   exclusive open retried every 25 ms, 4 s default like PowerShell). A timeout on either
   raises ``LockTimeout`` and mutates nothing;
3. RECOVERS the claim first (RCO1 9dd2f32c S1): an earlier WAL record for the same claim
   that is still unfinished is resolved from disk (see recovery) before the new plan reads
   the claim, so a retry always sees the outcome of the attempt it retries. A corrupt or
   diverged record for the claim, or more than one unfinished one, blocks the claim
   (``Blocked``) until an operator reconciles it; nothing is guessed;
4. re-reads the claim bytes under both locks and asks the operation's ``plan`` what to
   do. The plan re-checks owner, session, token and the generation (the SHA-256 of the
   bytes it was given); a refusal mutates nothing;
5. writes the WAL record (``prepared``). It binds the runtime-root identity, the txid (also
   in its file name ``<claim key>.<txid>.json``), the operation, the claim, the before and
   after digests, the archive record and its digest, and the event and its digest; an
   event's type must equal the operation. A malformed record, or one over the 256 KiB
   read bound, is refused before anything is written (S9), so everything written stays
   readable to recovery and to the overlap checks. An existing archive record at the
   target path is a conflict, and a linked or non-regular outbox leaf is refused, both
   before anything is written (RCO1 Q-F3);
6. compare-and-swap: the claim bytes must still equal what the plan saw, then the archive
   (``done/``) record is created and the claim is replaced atomically or deleted. A claim
   that is absent is never recreated by a plan that did not expect absence;
7. marks the WAL ``applied``, creates the outbox record keyed by the idempotency key,
   marks the WAL ``outboxed`` and files it under ``wal/final/``.

Records (WAL, archive, outbox, publication marker) are created whole: written to a
temporary file, fsynced, then hard-linked into place, which never overwrites. A crash
leaves at most a temporary file, never a torn record under the final name. An existing
record with other bytes is a conflict that is never swallowed (S2): the WAL becomes
``diverged`` and the caller is told that the claim change WAS applied while its
publication is blocked.

Recovery reports only what is on disk. An ``applied`` record is outboxed only when the
claim still shows its after-state and its archive holds the exact bytes (a well-formed
forged ``applied`` record cannot inject an event for a mutation that is not on disk, S3).
A ``prepared`` record whose after-state is on disk is rolled forward (bookkeeping). A
``prepared`` record whose claim still holds exactly the before bytes is REDONE: the plan
was decided under the locks against exactly those bytes, so the archive is created if it
is missing and the claim is replaced or deleted, then the record is outboxed; this is
never a false abort, a lost event or a second archive. Anything else is ``diverged``.
``reconcile`` runs the same recovery for every claim that has WAL records.

Publication (``publish_pending``) happens later, outside the locks, AT LEAST ONCE: the
marker is written after a successful publish, so a crash between the two, or two
concurrent callers, can publish a record again. The injected publisher port MUST be
idempotent by ``idempotency_key``; nothing here claims exactly-once delivery (S4). Only
an outbox record that binds to this root and to an ``outboxed`` WAL record with the same
txid, key, operation, claim, digests and event is published; anything else is reported
as rejected and never published (S3). A record whose WAL is still ``applied`` waits.

Paths (Tools 51ada Q-PATH-COVERAGE): the claim, its sibling lock, the archive, the WAL,
final and outbox records and their directories are checked on EVERY component, the leaf
included (no symlink, junction/reparse point, alias segment or '..'; an existing leaf must be
a regular file or directory), at entry AND again in recovery, reconcile and publication,
before any effect. Reads never follow a final symlink where O_NOFOLLOW exists. A
check-then-use (TOCTOU) window remains, and physical aliases (hard links, bind mounts, subst
or mapped drives) are NOT fenced.

Outcomes (Q-OUTCOME-TRUTH, RCO1 Q-F2/Q-F3): an exception refuses this call's OWN mutation,
with two exceptions. The diverged message says the change WAS applied. ``OutcomeUnknown`` covers
any ordinary error after this call's WAL record is on disk, other than the clean
compare-and-swap refusal: the change MAY ALREADY BE APPLIED, or recovery may complete it, and the
txid is named. ``recovered`` on an exception lists only the effect-bearing outcomes of earlier
unfinished work that the call completed first (EFFECT_OUTCOMES). A corrupt, diverged or ambiguous
record that merely blocks the claim is never listed. An ordinary error inside recovery, after a
step that can have an effect began, is OutcomeUnknown naming the EARLIER record; a raw ordinary error
before this call's own WAL record keeps any recovered work (as a QueueTransactionError); an aborted
record that cannot be filed is still Refused (Fable 8c091 S1, N1).

Bounds and durability (Q-WAL-STATE-GROWTH, Q-DURABILITY-BOUND): a record is prepared only if
it still fits the read bound with the longest later state and reason, so every bookkeeping
and recovery write stays readable. A zero-progress write refuses, and a cleanup failure never
masks the primary error. Files are fsynced; on POSIX the directory is fsynced after every
link, replace and unlink. On Windows a directory cannot be fsynced, so a completed rename or
link can be lost on POWER LOSS. The design covers process crashes; power-loss durability of
directory entries is not claimed there, and elsewhere depends on the filesystem.

Limits: legacy writers (``waggledance/core/work_queue.py``, the PowerShell claim
scripts) do not take the runtime-root mutex, and the Python legacy writer does not take
the sibling lock; while they run unchanged there is NO mixed-generation safety claim.
The bindings catch accidents, stale copies and cross-root mix-ups; a writer with the
lane's own file access can still forge a consistent record set. There is no retention
for filed WAL records or published outbox records yet. The Windows named-mutex adapter
and the PowerShell twin are separate (F8 ports).
Not runtime-tested: written under the operator's no-runs directive (2026-09-29).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Any, Callable, ContextManager, Iterator, Protocol

from tools.bridge_v2_resource_scope import ScopeError, _normalize_absolute

TXN_SCHEMA = "wd.bridge-v2-queue-txn.v1"
OUTBOX_SCHEMA = "wd.bridge-v2-queue-outbox.v1"
MUTEX_PREFIX = "Global\\WaggleDanceBridgeV2Queue-"
CLAIM_LOCK_SUFFIX = ".lock"            # legacy Enter-BridgeClaimLock: "$ClaimPath.lock"
LOCK_RETRY_SECONDS = 0.025             # legacy retry interval (25 ms)
DEFAULT_LOCK_TIMEOUT_SECONDS = 4.0     # legacy $script:BridgeClaimLockTimeoutMs = 4000
MAX_RECORD_BYTES = 256 * 1024
MAX_KEY_CHARS = 512
MAX_REJECTED_NAMES = 32
OPS = ("claim", "release", "heartbeat", "stale_archive")
UNFINISHED = ("prepared", "applied")
FINAL = ("outboxed", "aborted", "diverged")
FILED = ("outboxed", "aborted")        # moved to wal/final/; a diverged record stays and blocks
# Recovery outcomes that did work (RCO1 Q-F2): the only ones ``recovered`` reports. Corrupt,
# diverged and ambiguous records are reported by reconcile and block the claim, but did nothing.
EFFECT_OUTCOMES = ("rolled_forward", "rolled_forward_bookkeeping", "outboxed", "rolled_forward_diverged")
# Every reason a WAL record can carry (bounded, Q-WAL-STATE-GROWTH): room for the longest one, with
# the longest state, is reserved when the record is prepared, so no later serialization outgrows the bound.
RECOVERY_REASONS = ("cas_mismatch", "found_applied_after_crash", "redone_after_crash", "archive_record_differs",
                    "applied_record_not_on_disk", "claim_matches_neither_before_nor_after", "outbox_record_conflict")
LONGEST_REASON = max(RECOVERY_REASONS, key=len)
LONGEST_STATE = max(UNFINISHED + FINAL, key=len)
TXN_KEYS = frozenset({"schema", "root_identity", "txid", "idempotency_key", "op", "claim_rel", "before_sha256",
                      "after", "after_sha256", "archive_rel", "archive", "archive_sha256", "event",
                      "event_sha256", "state", "created_utc"})
OUTBOX_KEYS = frozenset({"schema", "root_identity", "idempotency_key", "txid", "op", "claim_rel", "before_sha256",
                         "after_sha256", "event", "event_sha256"})
_REL_CLAIM = re.compile(r"work_queue/claims/[A-Za-z0-9._-]{1,200}\.json")
_REL_ARCHIVE = re.compile(r"work_queue/done/[A-Za-z0-9._-]{1,240}\.json")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_OUTBOX_NAME = re.compile(r"[0-9a-f]{64}\.json")


class QueueTransactionError(ValueError):
    """This call's OWN mutation was refused, unless this is an ``OutcomeUnknown`` or the message
    says the change WAS applied.

    ``recovered`` (Tools 51ada Q-OUTCOME-TRUTH, RCO1 Q-F2) lists only the EFFECT_OUTCOMES of
    earlier unfinished transactions on the same claim that this call completed FIRST, under the
    locks, before refusing: those effects DID happen. A corrupt, diverged or ambiguous record that
    only blocked the claim is not listed."""
    recovered: tuple = ()


class LockTimeout(QueueTransactionError):
    """A lock was not acquired within its bounded timeout; nothing was read or mutated."""


class Refused(QueueTransactionError):
    """The operation's plan (or the claim's state) refused under the locks; its own mutation did
    not happen (see ``recovered`` for earlier work this call completed first)."""


class Blocked(QueueTransactionError):
    """A corrupt, diverged or ambiguous transaction record blocks this claim until an operator
    reconciles it (moves the record out of ``wal/``); the new mutation did not happen."""


class RecordConflict(QueueTransactionError):
    """A record already exists at its path with other (or torn) bytes, or as a link or a non-regular
    file; it is never overwritten."""


class OutcomeUnknown(QueueTransactionError):
    """The outcome of the transaction named by ``txid`` is unknown; the original error is the ``__cause__``.

    Raised for an ordinary error AFTER this call's WAL record was on disk (RCO1 Q-F3), other than the
    clean compare-and-swap refusal: this call's change MAY ALREADY BE APPLIED, or the next transaction's
    recovery (or reconcile) may complete it from what is on disk. Also raised for an ordinary error
    inside RECOVERY after it began to finish an EARLIER record (Fable 8c091 S1): then ``txid`` names that
    earlier record, which MAY have been partly applied, and this call's own mutation did not happen. A
    WAL state that cannot fit the read bound is this type too (N3). A BaseException is never wrapped."""
    txid: str | None = None


class MutexPort(Protocol):
    def hold(self, name: str, timeout_seconds: float) -> ContextManager[None]: ...


class ClaimLockPort(Protocol):
    def hold(self, lock_path: Path, timeout_seconds: float) -> ContextManager[None]: ...


class PublisherPort(Protocol):
    def publish(self, record: dict) -> None: ...   # MUST be idempotent by record["idempotency_key"]


@dataclass(frozen=True)
class Plan:
    """What one operation does to one claim, decided under both locks."""
    after: dict | None                      # new claim object, or None to delete it
    expect_absent: bool = False             # True only for a create: absence is expected
    archive: tuple[Path, dict] | None = None  # done/ record created first
    event: dict | None = None               # published after commit, via the outbox; type == op
    result: Any = None
    keep: bool = False                      # True: nothing to change (a no-op plan)


def canonical_root(runtime_root: str | Path) -> str:
    """The normalized runtime-root identity text (same rule as resource scopes)."""
    try:
        return _normalize_absolute(str(runtime_root), os.lstat)
    except ScopeError as exc:
        raise QueueTransactionError("runtime root: " + str(exc)) from None


def root_identity(runtime_root: str | Path) -> str:
    return hashlib.sha256(canonical_root(runtime_root).encode("ascii")).hexdigest()


def mutex_name(runtime_root: str | Path) -> str:
    """Derived from the normalized root, never one global constant shared by production and tests."""
    return MUTEX_PREFIX + root_identity(runtime_root)[:32]


def claim_lock_path(claim_path: Path) -> Path:
    return Path(str(claim_path) + CLAIM_LOCK_SUFFIX)


def claim_key(claim_rel: str) -> str:
    """The WAL file-name prefix of one claim: its records are found without reading others."""
    return hashlib.sha256(claim_rel.encode("utf-8")).hexdigest()[:32]


def sha256_or_none(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def claim_bytes(obj: dict) -> bytes:
    """The legacy JSON layout (indent 2, sorted keys, trailing newline), LF on every platform.
    The legacy Python writer's text mode writes CRLF on Windows, so there the bytes differ
    while the JSON content is the same (RCO1 N6)."""
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _digest(value: Any) -> str | None:
    """SHA-256 of a record's canonical bytes; None for None; "invalid" for anything but a dict."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return "invalid"
    return hashlib.sha256(claim_bytes(value)).hexdigest()


_os_write = os.write   # the one write primitive; a fixture seam for zero-progress and short writes


def _guard(path: Path, kind: str, leaf: str = "file") -> None:
    """Conservative path check BEFORE any effect (Tools 51ada Q-PATH-COVERAGE): every existing
    component of ``path``, the LEAF included, is lstat-checked: no symlink, no junction or other
    reparse point, no alias segment (a trailing dot or space, a ``~digit`` short name) and no
    '..'; an existing leaf must be a regular file (``leaf="file"``) or a directory
    (``leaf="dir"``). A check-then-use window remains (TOCTOU) between this and the open, and
    physical aliases (hard links, bind mounts, subst or mapped drives) are NOT fenced."""
    try:
        _normalize_absolute(str(path), os.lstat)
    except ScopeError as exc:
        raise QueueTransactionError(kind + " path: " + str(exc)) from None
    except OSError as exc:   # RCO1 N2: ENOTDIR, EACCES or ELOOP on a component is a refusal, never a raw error
        raise QueueTransactionError(kind + " path is unreadable (" + (errno.errorcode.get(exc.errno, "")
                                                                      or type(exc).__name__) + ")") from None
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        raise QueueTransactionError(kind + " path is unreadable") from None
    if not (stat.S_ISREG(info.st_mode) if leaf == "file" else stat.S_ISDIR(info.st_mode)):
        raise QueueTransactionError(kind + " path is not a " + ("regular file" if leaf == "file" else "directory"))


def read_bytes_or_none(path: Path, limit: int | None = None) -> bytes | None:
    """A bounded read of a REGULAR file, never following a final symlink where the platform can
    refuse one (O_NOFOLLOW); None when it is absent. The bound is read at call time. The descriptor
    has ONE owner, this function: the stream wraps it with closefd=False, so neither a failing
    wrapper constructor nor the stream's own close can close it, and it is closed here exactly once
    (the cleanup policy is _close_during's)."""
    limit = MAX_RECORD_BYTES if limit is None else limit
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    try:
        with os.fdopen(fd, "rb", closefd=False) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise QueueTransactionError("record is not a regular file: " + Path(path).name)
            data = stream.read(limit + 1)
    except BaseException as primary:
        _close_during(fd, primary)   # a close failure is recorded on the primary, never dropped
        raise
    os.close(fd)   # no primary error, so a close failure is visible
    if len(data) > limit:
        raise QueueTransactionError("record exceeds the size bound")
    return data


class DescriptorCloseUnknown(RuntimeError):
    """A cleanup step (close or unlock) of an owned descriptor failed while another error propagated,
    and that error refused the record (a sealed class; see _record_cleanup_unknown). The descriptor's
    state is UNKNOWN. ``primary`` (also the __cause__) is the error that was propagating; ``step``,
    ``fd`` and ``cleanup_error`` name the failed step."""

    def __init__(self, step: str, fd: int, cleanup_error: OSError, primary: BaseException) -> None:
        super().__init__("descriptor_close_unknown: " + step + " of fd " + str(fd) + " failed ("
                         + type(cleanup_error).__name__ + ") while " + type(primary).__name__ + " propagated")
        self.step, self.fd, self.cleanup_error, self.primary = step, fd, cleanup_error, primary


def _record_cleanup_unknown(primary: BaseException, fd: int, step: str, error: OSError) -> None:
    """Record on ``primary``, which keeps propagating with its identity, that cleanup ``step`` ("close" or
    "unlock") of descriptor ``fd`` failed with ``error``, so the descriptor's state is UNKNOWN:
    ``primary.descriptor_close_unknown`` gains (step, fd, error) and a note names it. Best effort: a
    primary that refuses the record (a sealed class) cannot carry it. A sealed ordinary Exception is then
    replaced by DescriptorCloseUnknown raised from it, so the failure stays visible (identity is not kept;
    the primary is the __cause__). A sealed primary that is not an Exception (KeyboardInterrupt-like) is
    never replaced and propagates WITHOUT the record: a known limitation. Only an Exception from the
    record attempt is caught."""
    try:
        records = getattr(primary, "descriptor_close_unknown", None)
        if not isinstance(records, list):
            records = []
            primary.descriptor_close_unknown = records
        records.append((step, fd, error))
        primary.add_note("descriptor_close_unknown: " + step + " of fd " + str(fd) + " failed: " + repr(error))
    except Exception:  # noqa: BLE001 - the primary refused the record (a sealed class)
        if isinstance(primary, Exception):
            raise DescriptorCloseUnknown(step, fd, error, primary) from primary


def _close_during(fd: int, primary: BaseException) -> bool:
    """Close a descriptor this module owns while ``primary`` propagates; True when the close succeeded.
    An OSError from the close is never dropped: whether ``fd`` is still open is then UNKNOWN, so the
    failure is recorded on the primary (_record_cleanup_unknown) and False lets a caller fail closed.
    Any other exception from the close (KeyboardInterrupt, ...) is not caught: it propagates with the
    primary as its __context__. With no primary error the callers close with a plain os.close, so a
    close failure is visible."""
    try:
        os.close(fd)
    except OSError as close_error:
        _record_cleanup_unknown(primary, fd, "close", close_error)
        return False
    return True


def _write_exclusive(path: Path, data: bytes) -> None:
    """Create a NEW file (O_EXCL) with exactly these bytes, fsynced. Used for temporary files. A
    zero-progress write refuses (Q-DURABILITY-BOUND); closing never masks the primary error, and a close
    failure during it is recorded on it (_close_during), never dropped."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        view = memoryview(data)
        while view:
            written = _os_write(fd, view)
            if type(written) is not int or written <= 0:
                raise QueueTransactionError("a zero-progress write; the record was not committed")
            view = view[written:]
        os.fsync(fd)
    except BaseException as primary:
        _close_during(fd, primary)   # the primary propagates; a close failure is recorded on it
        raise
    os.close(fd)


def _fsync_directory(directory: Path) -> None:
    """POSIX: make a link, replace or unlink inside ``directory`` durable. Windows cannot open a
    directory for fsync, so there (and on filesystems without directory fsync) a completed
    rename or link can be lost on POWER LOSS: the WAL covers process crashes, and power-loss
    durability of a directory entry is NOT claimed (Q-DURABILITY-BOUND)."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _discard(temp: Path) -> None:
    """Remove a temporary file; a cleanup failure never masks the primary outcome (an unlinked
    temporary name is inert: no reader globs it)."""
    try:
        temp.unlink()
    except OSError:
        pass


def _temp_for(path: Path) -> Path:
    return path.with_name(path.name + ".v2tmp." + secrets.token_hex(8))


def _bound_text() -> str:
    """The read bound as messages state it, read at call time (256 KiB by default, RCO1 Q-F1)."""
    kib, rest = divmod(MAX_RECORD_BYTES, 1024)
    return str(kib) + " KiB" if not rest else str(MAX_RECORD_BYTES) + " bytes"


def _on_disk(path: Path) -> bool | None:
    """Whether anything is at ``path`` (lstat, never following it); None when that is unknown."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return True


def _leaf_conflicts(path: Path) -> bool:
    """An existing leaf that is a link, a reparse point or not a regular file (RCO1 Q-F3)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return not stat.S_ISREG(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse)


def _create_atomic(path: Path, data: bytes) -> bool:
    """Create a whole record that never overwrites: temporary file + fsync + hard link.
    True when created; False when exactly these bytes are already there (an idempotent
    replay); RecordConflict when other or torn bytes are there (never swallowed, S2), or when
    the existing leaf is a link or not a regular file (RCO1 Q-F3). Any other guard failure is
    a plain QueueTransactionError, raised before anything is written."""
    try:
        _guard(path, "record")
    except QueueTransactionError:
        if _leaf_conflicts(path):
            raise RecordConflict("an existing record is a link or not a regular file: " + path.name) from None
        raise
    temp = _temp_for(path)
    try:
        _write_exclusive(temp, data)
        try:
            os.link(temp, path)
        except FileExistsError:
            try:
                same = read_bytes_or_none(path) == data
            except (QueueTransactionError, OSError):
                same = False
            if same:
                return False
            raise RecordConflict("an existing record differs from the intended bytes: " + path.name) from None
        _fsync_directory(path.parent)
        return True
    finally:
        _discard(temp)


def _replace_atomic(path: Path, data: bytes) -> None:
    _guard(path, "record")
    temp = _temp_for(path)
    try:
        _write_exclusive(temp, data)
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        _discard(temp)


def _open_lock(lock_path: Path) -> int:
    """Open (creating if needed) the sibling lock file read/write without truncating it, as a RAW
    descriptor whose one owner is the caller (FileClaimLock.hold), which closes it exactly once: no
    file object wraps it, so no wrapper constructor can take or close it. Where the platform
    supports it (POSIX), O_NOFOLLOW makes a symlink swapped in after the guard fail the open
    instead of being followed."""
    return os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
                   0o600)


def _lock_fd(fd: int) -> None:
    """Take the exclusive lock without waiting: one byte at offset 0 on Windows (msvcrt), flock on
    POSIX. Contention raises OSError."""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    """Release the lock _lock_fd took."""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def _unlock_and_close(fd: int) -> None:
    """With NO error in flight: release the lock, then close the descriptor exactly once. The first
    failure propagates visibly; after an unlock failure the descriptor is still closed, and a close
    failure then is recorded on the unlock error (_close_during), never dropped."""
    try:
        _unlock(fd)
    except BaseException as unlock_error:
        _close_during(fd, unlock_error)
        raise
    os.close(fd)


def _release_during(fd: int, primary: BaseException) -> None:
    """While ``primary`` (an error raised in the lock's body) propagates: release the lock, then close the
    descriptor exactly once. An OSError from the unlock or the close is recorded on the primary
    (_record_cleanup_unknown, steps "unlock" and "close") and never dropped; after an unlock failure the
    descriptor is closed first. Any other exception from either step is not caught: it propagates
    instead, with the primary as its __context__ (after an unlock interrupt the descriptor is still
    closed first)."""
    try:
        _unlock(fd)
    except OSError as unlock_error:
        _close_during(fd, primary)
        _record_cleanup_unknown(primary, fd, "unlock", unlock_error)
        return
    except BaseException as interrupt:
        _close_during(fd, interrupt)
        raise
    _close_during(fd, primary)


class FileClaimLock:
    """The legacy sibling lock, Python side. Windows: an open handle (a PowerShell
    ``FileShare.None`` open fails while it exists, and ours fails while PowerShell holds
    one) plus a byte lock against other Python holders. POSIX: ``flock``.

    The sibling ``<claim>.json.lock`` is its own filesystem object (Tools 51ada W-LOCK-PATH, RCO1
    214bb4f6 notice). Immediately BEFORE EACH open attempt, inside the runtime-root mutex hold when
    used through QueueTransactions, its path is re-walked with ``_guard``: every existing component,
    the leaf included, must be free of links, junctions/reparse points, alias segments and '..',
    and an existing leaf must be a regular file. A directory or linked leaf is refused AT ONCE
    (``QueueTransactionError``, never waited on as contention). Where O_NOFOLLOW exists, a link
    swapped in after the check fails the open and is refused. On Windows (no O_NOFOLLOW) a
    check-then-open race remains; physical aliases (hard links) and fencing of legacy writers are
    NOT proven. Each attempt owns the raw descriptor it opened and closes it once. A failed attempt
    closes it before it refuses, waits or propagates; if that close fails, the descriptor's state is
    UNKNOWN, so the attempt stops at once (no retry and no LockTimeout) and its own error propagates
    with the close failure recorded on it (_close_during). The release unlocks and closes once. With an
    error raised in the body, an OSError from the release is recorded on that error, which keeps
    propagating (best effort: see _record_cleanup_unknown for a sealed error class); any other exception
    from the release (KeyboardInterrupt, ...) is not caught and propagates instead, with the body error
    as its __context__. With no body error, a release failure propagates visibly."""

    @contextmanager
    def hold(self, lock_path: Path, timeout_seconds: float) -> Iterator[None]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            fd = None
            _guard(Path(lock_path), "claim lock")   # every attempt: the leaf and all its ancestors
            try:
                fd = _open_lock(Path(lock_path))
                _lock_fd(fd)
                break
            except BaseException as exc:
                # A failed attempt closes its own descriptor exactly once. If that close fails, whether
                # it is still open is UNKNOWN: fail closed with this attempt's error, never retry past it.
                if fd is not None and not _close_during(fd, exc):
                    raise
                if not isinstance(exc, OSError):
                    raise
                if getattr(exc, "errno", None) == getattr(errno, "ELOOP", None) or isinstance(exc, IsADirectoryError):
                    # O_NOFOLLOW refused a link (or the leaf is a directory) that appeared after the
                    # guard: a refusal, never contention to wait on.
                    raise QueueTransactionError("claim lock path is a link or directory at open time") from None
                if time.monotonic() >= deadline:
                    raise LockTimeout("claim lock busy: " + Path(lock_path).name) from None
                time.sleep(LOCK_RETRY_SECONDS)
        try:
            yield
        except BaseException as body_error:
            _release_during(fd, body_error)   # an OSError is recorded on the body error, never dropped
            raise
        _unlock_and_close(fd)   # no primary error, so a release failure is visible


@dataclass
class QueueTransactions:
    runtime_root: str | Path
    mutex: MutexPort | None = None
    claim_lock: ClaimLockPort | None = None
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    new_id: Callable[[], str] = field(default=lambda: secrets.token_hex(16))

    def __post_init__(self) -> None:
        # Explicit root only, no defaults. canonical_root validates it (local, absolute, no
        # link or reparse point); the filesystem path itself keeps its case (POSIX is
        # case-sensitive), while the mutex name and the record binding use the identity.
        self.root_id = root_identity(self.runtime_root)
        self.root = Path(str(self.runtime_root))
        self.claims_dir = self.root / "work_queue" / "claims"
        self.wal_dir = self.root / "work_queue" / "v2" / "wal"
        self.final_dir = self.wal_dir / "final"
        self.outbox_dir = self.root / "work_queue" / "v2" / "outbox"

    @property
    def ports_on(self) -> bool:
        return self.mutex is not None and self.claim_lock is not None

    # -- locks, in the one order ------------------------------------------------------
    @contextmanager
    def locked(self, claim_path: Path) -> Iterator[None]:
        if not self.ports_on:
            raise QueueTransactionError("queue ports are off: a mutex port and a claim-lock port are required")
        if type(self.lock_timeout_seconds) not in (int, float) or not 0 < self.lock_timeout_seconds <= 60:
            raise QueueTransactionError("lock timeout must be in (0, 60] seconds")
        _guard(claim_lock_path(claim_path), "claim lock")   # a linked lock file would lock something else
        with self.mutex.hold(mutex_name(self.root), self.lock_timeout_seconds):
            with self.claim_lock.hold(claim_lock_path(claim_path), self.lock_timeout_seconds):
                yield

    # -- one transaction --------------------------------------------------------------
    def transact(self, op: str, claim_path: Path, idempotency_key: str,
                 plan_fn: Callable[[bytes | None], Plan]) -> Any:
        if op not in OPS:
            raise QueueTransactionError("unknown queue operation")
        if not isinstance(idempotency_key, str) or not 0 < len(idempotency_key) <= MAX_KEY_CHARS:
            raise QueueTransactionError("an idempotency key of 1..512 characters is required")
        claim_rel = self._relative(claim_path, _REL_CLAIM, "claim")   # before any lock (N4, N5)
        self._guard_state_dirs()                                      # before any lock or effect
        with self.locked(claim_path):
            recovered, blocker = self._recover_claim(claim_rel)
            try:
                if blocker is not None:
                    raise Blocked(blocker + " on this claim; nothing applied until an operator reconciles it")
                before = read_bytes_or_none(claim_path)
                plan = plan_fn(before)   # re-checks owner/session/token/generation; may raise Refused
                if plan.keep:
                    return plan.result
                if plan.expect_absent and plan.after is None:
                    raise Refused("a create needs the claim to create")
                if before is None and not plan.expect_absent:
                    raise Refused("the claim does not exist; it is never recreated")
                if before is not None and plan.expect_absent:
                    raise Refused("a claim already exists at this path")
                txn = self._record(op, claim_rel, idempotency_key, before, plan)   # every pre-effect check
                outcome = self._logged(txn, claim_path, before)
                if outcome == "aborted":
                    raise Refused("the claim changed outside the lock; nothing applied")
                if outcome == "diverged":
                    raise QueueTransactionError("the claim change WAS applied, but an existing outbox record for its "
                                                "key differs; publication is blocked until an operator reconciles "
                                                "txid " + txn["txid"])
                return plan.result
            except QueueTransactionError as exc:
                # Earlier work this call DID complete first, and nothing else (Q-OUTCOME-TRUTH, RCO1 Q-F2).
                exc.recovered = tuple(entry for entry in recovered if entry["outcome"] in EFFECT_OUTCOMES)
                raise
            except Exception as exc:  # noqa: BLE001 - only to keep recovered work visible (Fable 8c091 S1)
                # A raw ordinary error here is BEFORE this call's own WAL record (_logged wraps everything after
                # it), so this call's own mutation did not happen. When recovery first did work, that work is
                # never lost behind the raw error; with no recovered work the error propagates unchanged.
                effects = tuple(entry for entry in recovered if entry["outcome"] in EFFECT_OUTCOMES)
                if not effects:
                    raise
                error = QueueTransactionError("this call's own mutation did not happen (" + type(exc).__name__ + ": "
                                              + str(exc)[:200] + "); recovery first finished earlier work on this "
                                              "claim")
                error.recovered = effects
                raise error from exc

    def _logged(self, txn: dict, claim_path: Path, before: bytes | None) -> str:
        """Steps 5-7 (RCO1 Q-F3). Until the WAL record is on disk nothing has happened, and an error
        propagates as it is. After that the known outcomes are "aborted" (the compare-and-swap refused
        before any effect and the record says so), "diverged" (the change WAS applied; its outbox
        record conflicts) and "outboxed". Any other ordinary error is OutcomeUnknown with the txid,
        because recovery may redo a record left on disk. A BaseException is never wrapped."""
        wal_path = self._wal_path(txn)
        try:
            _create_atomic(wal_path, claim_bytes(txn))
        except RecordConflict:
            raise   # another record already holds this txid's name: this call wrote nothing
        except Exception as exc:  # noqa: BLE001 - classified by what is on disk
            if _on_disk(wal_path) is False:
                raise   # nothing was written: this call's own mutation did not happen
            raise self._unknown(txn, exc) from exc   # linked, but its completion is unconfirmed
        try:
            changed = read_bytes_or_none(claim_path) != before   # compare-and-swap, before any effect
            if changed:
                self._set_state(wal_path, txn, "aborted", reason="cas_mismatch")
            else:
                self._apply(txn, claim_path, claim_present=before is not None)
                return self._commit(wal_path, txn, reason=None)
        except OutcomeUnknown:
            raise
        except Exception as exc:  # noqa: BLE001 - after the WAL record, an ordinary error is outcome-unknown
            raise self._unknown(txn, exc) from exc
        # The record now durably SAYS aborted, and recovery only ever files an aborted record, never redoes
        # it: a failure to file it is still "nothing applied" (Fable 8c091 N1), never "MAY ALREADY BE APPLIED".
        try:
            self._move_to_final(wal_path)
        except Exception as exc:  # noqa: BLE001 - filing is bookkeeping; the change is known NOT applied
            raise Refused("the claim changed outside the lock; nothing applied (its aborted record " + txn["txid"]
                          + " is left for recovery to file: " + type(exc).__name__ + ")") from exc
        return "aborted"

    @staticmethod
    def _unknown(txn: dict, exc: Exception, *, recovery: bool = False) -> OutcomeUnknown:
        if recovery:   # Fable 8c091 S1: an error inside recovery, after it began to finish an EARLIER record
            text = ("outcome unknown: recovery of an earlier transaction on this claim MAY have partly applied it "
                    "(txid " + str(txn.get("txid")) + "); the next transaction or reconcile completes it from disk, "
                    "and this call's own mutation did not happen: ")
        else:
            text = ("outcome unknown: the claim change MAY ALREADY BE APPLIED, or the next transaction or "
                    "reconcile may complete it from disk (txid " + str(txn.get("txid")) + "): ")
        error = OutcomeUnknown(text + type(exc).__name__ + ": " + str(exc)[:200])
        error.txid = txn.get("txid")
        return error

    def _recovering(self, txn: dict, step: Callable[[], str]) -> str:
        """A recovery step that can have effects (a redo's archive or claim write, an outbox record, a WAL
        state): an ordinary error inside it is OutcomeUnknown naming the EARLIER record (Fable 8c091 S1). A
        BaseException is never wrapped; errors before such a step (guards, reads) propagate unchanged."""
        try:
            return step()
        except Exception as exc:  # noqa: BLE001 - after a recovery effect began, the outcome is unknown
            raise self._unknown(txn, exc, recovery=True) from exc   # steps never nest, so no double wrap

    def _guard_state_dirs(self) -> None:
        for directory, kind in ((self.claims_dir, "claims directory"), (self.root / "work_queue" / "done", "archive"
                                 " directory"), (self.wal_dir, "WAL directory"), (self.final_dir, "WAL final directory"),
                                (self.outbox_dir, "outbox directory")):
            _guard(directory, kind, leaf="dir")

    def _record(self, op: str, claim_rel: str, key: str, before: bytes | None, plan: Plan) -> dict:
        archive_rel = None
        if plan.archive is not None:
            archive_rel = self._relative(plan.archive[0], _REL_ARCHIVE, "archive")
            if os.path.lexists(plan.archive[0]):
                raise RecordConflict("an archive record already exists at " + archive_rel + "; nothing was written")
        if plan.event is not None:
            # RCO1 Q-F3: the outbox leaf is checked here, before the WAL record and any effect. Otherwise
            # a linked leaf would surface only after the claim change. An existing REGULAR outbox record
            # for the key stays the S2 conflict: diverged, and the change WAS applied.
            _guard(self._outbox_path(key), "outbox record")
        txn = {"schema": TXN_SCHEMA, "root_identity": self.root_id, "txid": self.new_id(),
               "idempotency_key": key, "op": op, "claim_rel": claim_rel,
               "before_sha256": sha256_or_none(before), "after": plan.after, "after_sha256": _digest(plan.after),
               "archive_rel": archive_rel, "archive": None if plan.archive is None else plan.archive[1],
               "archive_sha256": None if plan.archive is None else _digest(plan.archive[1]),
               "event": plan.event, "event_sha256": _digest(plan.event),
               "state": "prepared", "created_utc": self.clock().isoformat()}
        if not _valid_txn(txn, root_identity=self.root_id, name=self._wal_path(txn).name
                          if isinstance(txn["txid"], str) else None):
            raise QueueTransactionError("the transaction record is malformed (records must be JSON objects and an "
                                        "event's type must equal the operation); nothing was written")
        # Q-WAL-STATE-GROWTH: reserve room for the longest later state AND reason before any effect,
        # so every bookkeeping or recovery serialization of this record stays readable.
        if len(claim_bytes(dict(txn, state=LONGEST_STATE, reason=LONGEST_REASON))) > MAX_RECORD_BYTES:
            raise QueueTransactionError("the transaction record, with room for every later state and reason, "
                                        "exceeds the " + _bound_text() + " read bound; nothing was written")
        return txn

    def _apply(self, txn: dict, claim_path: Path, *, claim_present: bool) -> None:
        if txn["archive_rel"] is not None:
            _create_atomic(self.root / txn["archive_rel"], claim_bytes(txn["archive"]))   # False if exactly there
        if txn["after"] is not None:
            _replace_atomic(claim_path, claim_bytes(txn["after"]))
        elif claim_present:
            _guard(claim_path, "claim")
            claim_path.unlink()
            _fsync_directory(claim_path.parent)

    def _commit(self, wal_path: Path, txn: dict, reason: str | None) -> str:
        """applied -> outbox -> outboxed and filed; an outbox conflict makes it diverged instead."""
        if txn["state"] != "applied":
            self._set_state(wal_path, txn, "applied", reason=reason)
        try:
            self._outbox(txn)
        except RecordConflict:
            self._set_state(wal_path, txn, "diverged", reason="outbox_record_conflict")
            return "diverged"
        self._file(wal_path, txn, "outboxed")
        return "outboxed"

    # -- recovery ------------------------------------------------------------------------
    def _recover_claim(self, claim_rel: str) -> tuple[list[dict], str | None]:
        """Under both locks of the claim. Files finished records, resolves the one unfinished
        record (every transaction recovers its claim first, so there is at most one) and
        returns the outcomes plus a blocker when a record for the claim is corrupt, diverged
        or ambiguous."""
        outcomes: list[dict] = []
        unfinished: list[tuple[Path, dict]] = []
        blocker = None
        prefix = claim_key(claim_rel)
        # Recovery re-checks every path it will touch, before any effect (Q-PATH-COVERAGE).
        _guard(self.root / claim_rel, "claim")
        self._guard_state_dirs()
        for wal_path in sorted(self.wal_dir.glob(prefix + ".*.json")) if self.wal_dir.is_dir() else []:
            try:
                _guard(wal_path, "WAL record")
                txn = self._load(wal_path)
            except QueueTransactionError:
                txn = None   # a linked or odd WAL entry is corrupt: reported, never acted on
            if txn is None or txn["claim_rel"] != claim_rel:
                outcomes.append({"wal": wal_path.name, "outcome": "corrupt"})
                blocker = blocker or "a corrupt transaction record (" + wal_path.name + ")"
            elif txn["state"] == "diverged":
                outcomes.append({"wal": wal_path.name, "txid": txn["txid"], "outcome": "diverged",
                                 "reason": txn.get("reason")})
                blocker = blocker or "diverged transaction " + txn["txid"] + " (" + str(txn.get("reason")) + ")"
            elif txn["state"] in FILED:
                self._move_to_final(wal_path)
            else:
                unfinished.append((wal_path, txn))
        if blocker is None and len(unfinished) > 1:
            outcomes += [{"wal": p.name, "txid": t["txid"], "outcome": "ambiguous"} for p, t in unfinished]
            blocker = "several unfinished transactions"
        if blocker is None:
            for wal_path, txn in unfinished:
                outcome = self._resolve(wal_path, txn, self.root / claim_rel)
                outcomes.append({"wal": wal_path.name, "txid": txn["txid"], "outcome": outcome})
                if outcome in ("diverged", "rolled_forward_diverged"):
                    blocker = "diverged transaction " + txn["txid"] + " (" + str(txn.get("reason")) + ")"
        return outcomes, blocker

    def _resolve(self, wal_path: Path, txn: dict, claim_path: Path) -> str:
        """One unfinished record, from what is on disk; never a guess."""
        current = read_bytes_or_none(claim_path)
        archive = self._archive_state(txn)
        if archive == "differs":
            self._set_state(wal_path, txn, "diverged", reason="archive_record_differs")
            return "diverged"
        after_on_disk = sha256_or_none(current) == txn["after_sha256"] and archive in (None, "exact")
        # Fable 8c091 S1: every step below that can have an effect (a commit's outbox record and WAL states, a
        # redo's archive and claim write) runs through _recovering, so an ordinary error after it began is
        # OutcomeUnknown naming THIS earlier record; the reads and guards above propagate unchanged (no effect).
        if txn["state"] == "applied":
            if not after_on_disk:   # S3: an applied record outboxes only a mutation that is on disk
                self._set_state(wal_path, txn, "diverged", reason="applied_record_not_on_disk")
                return "diverged"
            return self._recovering(txn, lambda: self._commit(wal_path, txn, reason=None))
        if after_on_disk:
            outcome = self._recovering(
                txn, lambda: self._commit(wal_path, txn, reason="found_applied_after_crash"))
            return "rolled_forward_bookkeeping" if outcome == "outboxed" else outcome
        if sha256_or_none(current) == txn["before_sha256"]:
            # Redo: the plan was decided under the locks against exactly these bytes.
            def redo() -> str:
                self._apply(txn, claim_path, claim_present=current is not None)
                return self._commit(wal_path, txn, reason="redone_after_crash")
            outcome = self._recovering(txn, redo)
            # The redo DID apply the change even when its outbox record conflicts (effect-bearing, Q-F2).
            return "rolled_forward" if outcome == "outboxed" else "rolled_forward_diverged"
        self._set_state(wal_path, txn, "diverged", reason="claim_matches_neither_before_nor_after")
        return "diverged"

    def _archive_state(self, txn: dict) -> str | None:
        if txn["archive_rel"] is None:
            return None
        _guard(self.root / txn["archive_rel"], "archive")   # a linked archive refuses before any effect
        try:
            data = read_bytes_or_none(self.root / txn["archive_rel"])
        except (QueueTransactionError, OSError):
            return "differs"
        if data is None:
            return "absent"
        return "exact" if data == claim_bytes(txn["archive"]) else "differs"

    def reconcile(self) -> list[dict]:
        """The recovery every transaction runs first, for every claim with WAL records.
        Corrupt, diverged and ambiguous records are reported and left for an operator."""
        outcomes: list[dict] = []
        claims: set[str] = set()
        self._guard_state_dirs()
        for wal_path in sorted(self.wal_dir.glob("*.json")) if self.wal_dir.is_dir() else []:
            try:
                _guard(wal_path, "WAL record")
                txn = self._load(wal_path)
            except QueueTransactionError:
                txn = None
            if txn is None:
                outcomes.append({"wal": wal_path.name, "outcome": "corrupt"})
            else:
                claims.add(txn["claim_rel"])
        for claim_rel in sorted(claims):
            try:
                with self.locked(self.root / claim_rel):
                    found, blocker = self._recover_claim(claim_rel)   # re-read under the lock (N8)
            except QueueTransactionError as exc:   # a guarded path or a lock refusal: reported, nothing done
                outcomes.append({"claim_rel": claim_rel, "outcome": "blocked", "reason": str(exc)[:200]})
                continue
            outcomes.extend(entry for entry in found if entry["outcome"] != "corrupt")   # reported above
            if blocker is not None:
                outcomes.append({"claim_rel": claim_rel, "outcome": "blocked", "reason": blocker})
        return outcomes

    # -- publication, outside the locks ----------------------------------------------
    def publish_pending(self, publisher: PublisherPort | None) -> dict:
        """AT LEAST ONCE (S4): the publisher must be idempotent by idempotency_key. Only records
        bound to this root and to an outboxed WAL record are published; others are rejected."""
        report: dict[str, Any] = {"published": 0, "pending": 0, "failed": 0, "rejected": 0,
                                  "rejected_records": []}
        self._guard_state_dirs()   # a linked outbox, WAL or final directory refuses the whole run
        for path in sorted(self.outbox_dir.glob("*.json")) if self.outbox_dir.is_dir() else []:
            marker = path.with_name(path.stem + ".published")
            try:
                _guard(path, "outbox record")
                _guard(marker, "publication marker")
            except QueueTransactionError:
                record, state = None, None   # a linked record or marker is never trusted or published
            else:
                if os.path.lexists(marker):
                    continue
                record, state = self._bound_outbox_record(path)
            if record is None:
                report["rejected"] += 1
                if len(report["rejected_records"]) < MAX_REJECTED_NAMES:
                    report["rejected_records"].append(path.name)
                continue
            if publisher is None or state != "outboxed":
                report["pending"] += 1   # no port yet, or the WAL is still being finished
                continue
            try:
                publisher.publish(record)
            except Exception:  # noqa: BLE001 - a failed publication stays pending, never lost
                report["failed"] += 1
                continue
            report["published"] += 1
            try:
                _create_atomic(marker, b"published\n")
            except Exception:  # noqa: BLE001 - no marker: republished next time (at least once)
                pass
        return report

    def _bound_outbox_record(self, path: Path) -> tuple[dict | None, str | None]:
        try:
            if _OUTBOX_NAME.fullmatch(path.name) is None:
                return None, None
            record = json.loads(read_bytes_or_none(path).decode("utf-8"))
            if not isinstance(record, dict) or set(record) != OUTBOX_KEYS:
                return None, None
            if (record["schema"] != OUTBOX_SCHEMA or record["root_identity"] != self.root_id
                    or not isinstance(record["idempotency_key"], str)
                    or hashlib.sha256(record["idempotency_key"].encode("utf-8")).hexdigest() + ".json" != path.name
                    or not isinstance(record["claim_rel"], str) or _REL_CLAIM.fullmatch(record["claim_rel"]) is None
                    or not isinstance(record["txid"], str) or _HEX32.fullmatch(record["txid"]) is None):
                return None, None
            name = claim_key(record["claim_rel"]) + "." + record["txid"] + ".json"
            txn = self._load(self.final_dir / name) or self._load(self.wal_dir / name)
            if txn is None or txn["state"] not in ("applied", "outboxed") or txn["event"] is None:
                return None, None
            if any(txn[k] != record[k] for k in ("idempotency_key", "op", "claim_rel", "before_sha256",
                                                 "after_sha256", "event", "event_sha256")):
                return None, None
            return record, txn["state"]
        except Exception:  # noqa: BLE001 - torn, oversized or foreign: rejected, never published
            return None, None

    # -- helpers -------------------------------------------------------------------------
    def _relative(self, path: Path, pattern: re.Pattern, kind: str) -> str:
        try:
            rel = Path(path).relative_to(self.root).as_posix()
        except ValueError:
            raise QueueTransactionError("the " + kind + " path is outside the runtime root") from None
        if pattern.fullmatch(rel) is None:
            raise QueueTransactionError("the " + kind + " path is not a legal work_queue record name")
        _guard(Path(path), kind)   # every existing component, the leaf included (N5, Q-PATH-COVERAGE)
        return rel

    def _wal_path(self, txn: dict) -> Path:
        return self.wal_dir / (claim_key(txn["claim_rel"]) + "." + txn["txid"] + ".json")

    def _outbox_path(self, key: str) -> Path:
        return self.outbox_dir / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")

    def _load(self, wal_path: Path) -> dict | None:
        """A WAL record bound to this root and to its own file name, or None (corrupt)."""
        try:
            data = read_bytes_or_none(wal_path)
            txn = None if data is None else json.loads(data.decode("utf-8"))
        except Exception:  # noqa: BLE001 - unreadable is corrupt
            return None
        return txn if _valid_txn(txn, root_identity=self.root_id, name=wal_path.name) else None

    def _set_state(self, wal_path: Path, txn: dict, state: str, reason: str | None = None) -> None:
        txn["state"] = state
        if reason:
            txn["reason"] = reason
        data = claim_bytes(txn)
        if len(data) > MAX_RECORD_BYTES:
            # Unreachable after _record's reservation (a forged or older record can still reach it in recovery);
            # if it ever happens the claim change MAY be applied, so it is typed OutcomeUnknown (Fable 8c091 N3).
            error = OutcomeUnknown("the WAL state could not be recorded inside the read bound; the claim change MAY "
                                   "ALREADY BE APPLIED (txid " + str(txn.get("txid")) + ")")
            error.txid = txn.get("txid")
            raise error
        _replace_atomic(wal_path, data)

    def _file(self, wal_path: Path, txn: dict, state: str, reason: str | None = None) -> None:
        self._set_state(wal_path, txn, state, reason)
        self._move_to_final(wal_path)

    def _move_to_final(self, wal_path: Path) -> None:
        _guard(wal_path, "WAL record")
        _guard(self.final_dir, "WAL final directory", leaf="dir")
        self.final_dir.mkdir(parents=True, exist_ok=True)
        target = self.final_dir / wal_path.name
        _guard(target, "WAL record")
        os.replace(wal_path, target)
        _fsync_directory(self.wal_dir)
        _fsync_directory(self.final_dir)

    def _outbox(self, txn: dict) -> None:
        if txn["event"] is None:
            return
        record = {"schema": OUTBOX_SCHEMA, "root_identity": self.root_id, "idempotency_key": txn["idempotency_key"],
                  "txid": txn["txid"], "op": txn["op"], "claim_rel": txn["claim_rel"],
                  "before_sha256": txn["before_sha256"], "after_sha256": txn["after_sha256"],
                  "event": txn["event"], "event_sha256": txn["event_sha256"]}
        _create_atomic(self._outbox_path(txn["idempotency_key"]), claim_bytes(record))


def _hex(value: Any, pattern: re.Pattern) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _valid_txn(txn: Any, *, root_identity: str, name: str | None = None) -> bool:
    """A WAL record exactly as transact writes it, bound to this root, to its file name, and
    to the digests of its after-state, archive and event (whose type must be the operation).
    Anything else is corrupt and never acted on (S3)."""
    try:
        if not isinstance(txn, dict) or not TXN_KEYS <= set(txn) or not set(txn) <= TXN_KEYS | {"reason"}:
            return False
        archive_parts = (txn["archive_rel"] is None, txn["archive"] is None, txn["archive_sha256"] is None)
        event = txn["event"]
        return (txn["schema"] == TXN_SCHEMA and txn["root_identity"] == root_identity
                and _hex(txn["txid"], _HEX32)
                and isinstance(txn["idempotency_key"], str) and 0 < len(txn["idempotency_key"]) <= MAX_KEY_CHARS
                and txn["op"] in OPS and _hex(txn["claim_rel"], _REL_CLAIM)
                and (name is None or name == claim_key(txn["claim_rel"]) + "." + txn["txid"] + ".json")
                and all(txn[k] is None or _hex(txn[k], _HEX64)
                        for k in ("before_sha256", "after_sha256", "archive_sha256", "event_sha256"))
                and _digest(txn["after"]) == txn["after_sha256"]
                and len(set(archive_parts)) == 1
                and (txn["archive_rel"] is None
                     or (_hex(txn["archive_rel"], _REL_ARCHIVE) and _digest(txn["archive"]) == txn["archive_sha256"]))
                and _digest(event) == txn["event_sha256"]
                and (event is None or (event.get("type") == txn["op"]
                                       and event.get("generation_before", txn["before_sha256"]) == txn["before_sha256"]))
                and txn["state"] in UNFINISHED + FINAL
                and isinstance(txn["created_utc"], str) and len(txn["created_utc"]) <= 64
                and (txn.get("reason") is None or txn["reason"] in RECOVERY_REASONS))
    except Exception:  # noqa: BLE001 - unserializable or odd shapes are corrupt
        return False
