#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F8: queue transactions: lock order, compare-and-swap, WAL, outbox, recovery.

Dormant and tools-owned: nothing imports this module, it imports no ``waggledance``
code, it has no default runtime root and no default ports. Every call names an explicit
runtime root and injected ports; without a mutex port AND a claim-lock port a mutation
is refused before anything is read or written.

One transaction (``QueueTransactions.transact``):

1. takes the runtime-root mutex (``mutex_name(root)``) FIRST, then the exact legacy
   sibling claim lock ``<claim>.json.lock`` (``Enter-BridgeClaimLock`` spelling, an
   exclusive open retried every 25 ms, 4 s default like PowerShell). A timeout on either
   raises ``LockTimeout`` and mutates nothing;
2. re-reads the claim bytes under both locks and asks the operation's ``plan`` what to
   do. The plan re-checks owner, session, token and the generation (the SHA-256 of the
   bytes it was given); a refusal mutates nothing;
3. writes a WAL record (``prepared``, exclusive create + fsync) that binds the mutation,
   the archive record and the event to publish under one idempotency key;
4. compare-and-swap: the claim bytes must still equal what the plan saw, then the
   archive (``done/``) record is created exclusively and the claim is replaced
   atomically or deleted. A claim that is absent is never recreated by a plan that did
   not expect absence (no resurrection after archive);
5. marks the WAL ``applied``, writes the outbox record keyed by the idempotency key
   (a repeat of the same key is the same publication), and marks the WAL ``outboxed``.

Publication happens later and outside the locks (``publish_pending``) through an
injected, key-idempotent publisher port. The mutex alone is not crash atomic, so
``reconcile`` reads every unfinished WAL record under the same lock order and reports
only what is on disk: ``prepared`` whose after-state is on disk is marked applied and
outboxed; ``prepared`` whose before-state is still on disk is marked ``aborted`` (the
mutation never happened; an orphan archive is reported, never deleted); anything else is
``diverged`` and left untouched for an operator. A corrupt WAL record is reported, never
acted on.

Limits: legacy writers (``waggledance/core/work_queue.py``, the PowerShell claim
scripts) do not take the runtime-root mutex, and the Python legacy writer does not take
the sibling lock; while they run unchanged there is NO mixed-generation safety claim.
The Windows named-mutex adapter and the PowerShell mutex-name twin are not included.
Not runtime-tested: written under the operator's no-runs directive (2026-09-29).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
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
UNFINISHED = ("prepared", "applied")
FINAL = ("outboxed", "aborted", "diverged")


class QueueTransactionError(ValueError):
    """A transaction was refused before any mutation."""


class LockTimeout(QueueTransactionError):
    """A lock was not acquired within its bounded timeout; nothing was mutated."""


class Refused(QueueTransactionError):
    """The operation's plan refused under the locks; nothing was mutated."""


class MutexPort(Protocol):
    def hold(self, name: str, timeout_seconds: float) -> ContextManager[None]: ...


class ClaimLockPort(Protocol):
    def hold(self, lock_path: Path, timeout_seconds: float) -> ContextManager[None]: ...


class PublisherPort(Protocol):
    def publish(self, record: dict) -> None: ...


@dataclass(frozen=True)
class Plan:
    """What one operation does to one claim, decided under both locks."""
    after: dict | None                      # new claim object, or None to delete it
    expect_absent: bool = False             # True only for a create: absence is expected
    archive: tuple[Path, dict] | None = None  # done/ record created exclusively first
    event: dict | None = None               # published after commit, via the outbox
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


def sha256_or_none(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def claim_bytes(obj: dict) -> bytes:
    """Exactly the legacy writer's bytes: indent 2, sorted keys, trailing newline."""
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_bytes_or_none(path: Path, limit: int = MAX_RECORD_BYTES) -> bytes | None:
    try:
        with open(path, "rb") as stream:
            data = stream.read(limit + 1)
    except FileNotFoundError:
        return None
    if len(data) > limit:
        raise QueueTransactionError("record exceeds the size bound")
    return data


def _write_exclusive(path: Path, data: bytes) -> bool:
    """Create with O_EXCL and fsync. Returns False if the same bytes already exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        if read_bytes_or_none(path) == data:
            return False
        raise QueueTransactionError("an existing record differs from the intended bytes") from None
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def _replace_atomic(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".v2tmp." + secrets.token_hex(8))
    try:
        _write_exclusive(temp, data)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


class FileClaimLock:
    """The legacy sibling lock, Python side. Windows: an open handle (a PowerShell
    ``FileShare.None`` open fails while it exists, and ours fails while PowerShell holds
    one) plus a byte lock against other Python holders. POSIX: ``flock``."""

    @contextmanager
    def hold(self, lock_path: Path, timeout_seconds: float) -> Iterator[None]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            stream = None
            try:
                stream = open(lock_path, "a+b")
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if stream is not None:
                    stream.close()
                if time.monotonic() >= deadline:
                    raise LockTimeout("claim lock busy: " + lock_path.name) from None
                time.sleep(LOCK_RETRY_SECONDS)
        try:
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()


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
        # case-sensitive), while the mutex name uses the normalized identity.
        canonical_root(self.runtime_root)
        self.root = Path(str(self.runtime_root))
        self.wal_dir = self.root / "work_queue" / "v2" / "wal"
        self.outbox_dir = self.root / "work_queue" / "v2" / "outbox"

    # -- locks, in the one order ------------------------------------------------------
    @contextmanager
    def locked(self, claim_path: Path) -> Iterator[None]:
        if self.mutex is None or self.claim_lock is None:
            raise QueueTransactionError("queue ports are off: a mutex port and a claim-lock port are required")
        if type(self.lock_timeout_seconds) not in (int, float) or not 0 < self.lock_timeout_seconds <= 60:
            raise QueueTransactionError("lock timeout must be in (0, 60] seconds")
        with self.mutex.hold(mutex_name(self.root), self.lock_timeout_seconds):
            with self.claim_lock.hold(claim_lock_path(claim_path), self.lock_timeout_seconds):
                yield

    # -- one transaction --------------------------------------------------------------
    def transact(self, op: str, claim_path: Path, idempotency_key: str,
                 plan_fn: Callable[[bytes | None], Plan]) -> Any:
        if not isinstance(idempotency_key, str) or not 0 < len(idempotency_key) <= 512:
            raise QueueTransactionError("an idempotency key is required")
        with self.locked(claim_path):
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
            after_bytes = None if plan.after is None else claim_bytes(plan.after)
            archive_bytes = None if plan.archive is None else claim_bytes(plan.archive[1])
            txn = {"schema": TXN_SCHEMA, "txid": self.new_id(), "idempotency_key": idempotency_key,
                   "op": op, "claim_rel": self._rel(claim_path), "before_sha256": sha256_or_none(before),
                   "after": plan.after, "after_sha256": sha256_or_none(after_bytes),
                   "archive_rel": None if plan.archive is None else self._rel(plan.archive[0]),
                   "archive": None if plan.archive is None else plan.archive[1],
                   "event": plan.event, "state": "prepared",
                   "created_utc": self.clock().isoformat()}
            wal_path = self.wal_dir / (txn["txid"] + ".json")
            _write_exclusive(wal_path, claim_bytes(txn))
            if read_bytes_or_none(claim_path) != before:   # compare-and-swap
                self._set_state(wal_path, txn, "aborted", reason="cas_mismatch")
                raise Refused("the claim changed outside the lock; nothing applied")
            if archive_bytes is not None:
                _write_exclusive(plan.archive[0], archive_bytes)
            if after_bytes is not None:
                _replace_atomic(claim_path, after_bytes)
            elif before is not None:
                claim_path.unlink()
            self._set_state(wal_path, txn, "applied")
            self._outbox(txn)
            self._set_state(wal_path, txn, "outboxed")
            return plan.result

    # -- publication, outside the locks ----------------------------------------------
    def publish_pending(self, publisher: PublisherPort | None) -> dict:
        """Publish every unpublished outbox record once; a missing port leaves them pending."""
        report = {"published": 0, "pending": 0, "failed": 0}
        for path in sorted(self.outbox_dir.glob("*.json")) if self.outbox_dir.is_dir() else []:
            marker = path.with_name(path.stem + ".published")
            if marker.exists():
                continue
            if publisher is None:
                report["pending"] += 1
                continue
            try:
                record = json.loads(read_bytes_or_none(path).decode("utf-8"))
                publisher.publish(record)   # the port must be idempotent by idempotency_key
                _write_exclusive(marker, b"published\n")
                report["published"] += 1
            except Exception:  # noqa: BLE001 - a failed publication stays pending, never lost
                report["failed"] += 1
        return report

    # -- crash reconciliation --------------------------------------------------------
    def reconcile(self) -> list[dict]:
        """Resolve unfinished WAL records from what is on disk; never guess."""
        outcomes = []
        for wal_path in sorted(self.wal_dir.glob("*.json")) if self.wal_dir.is_dir() else []:
            try:
                txn = json.loads(read_bytes_or_none(wal_path).decode("utf-8"))
                if not _valid_txn(txn):
                    raise ValueError("shape")
                claim_path = self.root / txn["claim_rel"]
            except Exception:  # noqa: BLE001 - a corrupt record is reported, never acted on
                outcomes.append({"wal": wal_path.name, "outcome": "corrupt"})
                continue
            if txn.get("state") not in UNFINISHED:
                continue
            with self.locked(claim_path):
                outcomes.append({"wal": wal_path.name, "txid": txn.get("txid"),
                                 "outcome": self._resolve(wal_path, txn, claim_path)})
        return outcomes

    def _resolve(self, wal_path: Path, txn: dict, claim_path: Path) -> str:
        if txn["state"] == "applied":
            self._outbox(txn)
            self._set_state(wal_path, txn, "outboxed")
            return "outboxed"
        current = sha256_or_none(read_bytes_or_none(claim_path))
        archive_path = None if txn.get("archive_rel") is None else self.root / txn["archive_rel"]
        archive_present = archive_path is not None and archive_path.exists()
        if current == txn.get("after_sha256") and (archive_path is None or archive_present):
            self._set_state(wal_path, txn, "applied", reason="found_applied_after_crash")
            self._outbox(txn)
            self._set_state(wal_path, txn, "outboxed")
            return "rolled_forward_bookkeeping"
        if current == txn.get("before_sha256"):
            self._set_state(wal_path, txn, "aborted", reason="not_applied_before_crash"
                            + ("_orphan_archive" if archive_present else ""))
            return "aborted_orphan_archive" if archive_present else "aborted"
        self._set_state(wal_path, txn, "diverged", reason="claim_matches_neither_before_nor_after")
        return "diverged"

    # -- helpers -------------------------------------------------------------------------
    def _rel(self, path: Path) -> str:
        # Claim and archive paths are always built under self.root by the work queue.
        return Path(path).relative_to(self.root).as_posix()

    def _set_state(self, wal_path: Path, txn: dict, state: str, reason: str | None = None) -> None:
        txn["state"] = state
        if reason:
            txn["reason"] = reason
        _replace_atomic(wal_path, claim_bytes(txn))

    def _outbox(self, txn: dict) -> None:
        if txn.get("event") is None:
            return
        key = hashlib.sha256(txn["idempotency_key"].encode("utf-8")).hexdigest()
        record = {"schema": OUTBOX_SCHEMA, "idempotency_key": txn["idempotency_key"],
                  "txid": txn["txid"], "op": txn["op"], "event": txn["event"]}
        try:
            _write_exclusive(self.outbox_dir / (key + ".json"), claim_bytes(record))
        except QueueTransactionError:
            # The same key from another transaction: one logical publication, keep the first.
            pass

_REL_CLAIM = re.compile(r"work_queue/claims/[A-Za-z0-9._-]{1,200}\.json")
_REL_ARCHIVE = re.compile(r"work_queue/done/[A-Za-z0-9._-]{1,240}\.json")
_HEX = re.compile(r"[0-9a-f]{64}")


def _valid_txn(txn: Any) -> bool:
    """A WAL record exactly as transact writes it; anything else is corrupt and never acted
    on (a forged record must not steer a path or inject an outbox event)."""
    return (isinstance(txn, dict) and txn.get("schema") == TXN_SCHEMA
            and isinstance(txn.get("txid"), str) and re.fullmatch(r"[0-9a-f]{32}", txn["txid"]) is not None
            and isinstance(txn.get("idempotency_key"), str) and 0 < len(txn["idempotency_key"]) <= 512
            and txn.get("op") in ("claim", "release", "heartbeat", "stale_archive")
            and isinstance(txn.get("claim_rel"), str) and _REL_CLAIM.fullmatch(txn["claim_rel"]) is not None
            and (txn.get("archive_rel") is None
                 or (isinstance(txn["archive_rel"], str) and _REL_ARCHIVE.fullmatch(txn["archive_rel"]) is not None))
            and all(txn.get(k) is None or (isinstance(txn[k], str) and _HEX.fullmatch(txn[k]) is not None)
                    for k in ("before_sha256", "after_sha256"))
            and (txn.get("event") is None or isinstance(txn["event"], dict))
            and txn.get("state") in UNFINISHED + FINAL)
