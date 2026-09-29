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
   target path is a conflict, refused before anything is written;
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
MAX_KEY_CHARS = 512
MAX_REJECTED_NAMES = 32
OPS = ("claim", "release", "heartbeat", "stale_archive")
UNFINISHED = ("prepared", "applied")
FINAL = ("outboxed", "aborted", "diverged")
FILED = ("outboxed", "aborted")        # moved to wal/final/; a diverged record stays and blocks
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
    """A transaction was refused before any mutation (unless the message says otherwise)."""


class LockTimeout(QueueTransactionError):
    """A lock was not acquired within its bounded timeout; nothing was mutated."""


class Refused(QueueTransactionError):
    """The operation's plan (or the claim's state) refused under the locks; nothing was mutated."""


class Blocked(QueueTransactionError):
    """A corrupt, diverged or ambiguous transaction record blocks this claim until an operator
    reconciles it (moves the record out of ``wal/``); nothing was mutated."""


class RecordConflict(QueueTransactionError):
    """A record already exists at its path with other (or torn) bytes; it is never overwritten."""


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


def read_bytes_or_none(path: Path, limit: int = MAX_RECORD_BYTES) -> bytes | None:
    try:
        with open(path, "rb") as stream:
            data = stream.read(limit + 1)
    except FileNotFoundError:
        return None
    if len(data) > limit:
        raise QueueTransactionError("record exceeds the size bound")
    return data


def _write_exclusive(path: Path, data: bytes) -> None:
    """Create a NEW file (O_EXCL) with exactly these bytes, fsynced. Used for temporary files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)


def _temp_for(path: Path) -> Path:
    return path.with_name(path.name + ".v2tmp." + secrets.token_hex(8))


def _create_atomic(path: Path, data: bytes) -> bool:
    """Create a whole record that never overwrites: temporary file + fsync + hard link.
    True when created; False when exactly these bytes are already there (an idempotent
    replay); RecordConflict when other or torn bytes are there (never swallowed, S2)."""
    temp = _temp_for(path)
    try:
        _write_exclusive(temp, data)
        try:
            os.link(temp, path)
        except FileExistsError:
            try:
                same = read_bytes_or_none(path) == data
            except QueueTransactionError:
                same = False
            if same:
                return False
            raise RecordConflict("an existing record differs from the intended bytes: " + path.name) from None
        return True
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _replace_atomic(path: Path, data: bytes) -> None:
    temp = _temp_for(path)
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
        with self.locked(claim_path):
            _, blocker = self._recover_claim(claim_rel)
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
            txn = self._record(op, claim_rel, idempotency_key, before, plan)
            wal_path = self._wal_path(txn)
            _create_atomic(wal_path, claim_bytes(txn))
            if read_bytes_or_none(claim_path) != before:   # compare-and-swap
                self._file(wal_path, txn, "aborted", reason="cas_mismatch")
                raise Refused("the claim changed outside the lock; nothing applied")
            self._apply(txn, claim_path, claim_present=before is not None)
            if self._commit(wal_path, txn, reason=None) == "diverged":
                raise QueueTransactionError("the claim change WAS applied, but an existing outbox record for its "
                                            "key differs; publication is blocked until an operator reconciles "
                                            "txid " + txn["txid"])
            return plan.result

    def _record(self, op: str, claim_rel: str, key: str, before: bytes | None, plan: Plan) -> dict:
        archive_rel = None
        if plan.archive is not None:
            archive_rel = self._relative(plan.archive[0], _REL_ARCHIVE, "archive")
            if os.path.lexists(plan.archive[0]):
                raise RecordConflict("an archive record already exists at " + archive_rel + "; nothing was written")
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
        if len(claim_bytes(txn)) > MAX_RECORD_BYTES:
            raise QueueTransactionError("the transaction record exceeds the 256 KiB read bound; nothing was written")
        return txn

    def _apply(self, txn: dict, claim_path: Path, *, claim_present: bool) -> None:
        if txn["archive_rel"] is not None:
            _create_atomic(self.root / txn["archive_rel"], claim_bytes(txn["archive"]))   # False if exactly there
        if txn["after"] is not None:
            _replace_atomic(claim_path, claim_bytes(txn["after"]))
        elif claim_present:
            claim_path.unlink()

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
        for wal_path in sorted(self.wal_dir.glob(prefix + ".*.json")) if self.wal_dir.is_dir() else []:
            txn = self._load(wal_path)
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
                if outcome == "diverged":
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
        if txn["state"] == "applied":
            if not after_on_disk:   # S3: an applied record outboxes only a mutation that is on disk
                self._set_state(wal_path, txn, "diverged", reason="applied_record_not_on_disk")
                return "diverged"
            return self._commit(wal_path, txn, reason=None)
        if after_on_disk:
            outcome = self._commit(wal_path, txn, reason="found_applied_after_crash")
            return "rolled_forward_bookkeeping" if outcome == "outboxed" else outcome
        if sha256_or_none(current) == txn["before_sha256"]:
            # Redo: the plan was decided under the locks against exactly these bytes.
            self._apply(txn, claim_path, claim_present=current is not None)
            outcome = self._commit(wal_path, txn, reason="redone_after_crash")
            return "rolled_forward" if outcome == "outboxed" else outcome
        self._set_state(wal_path, txn, "diverged", reason="claim_matches_neither_before_nor_after")
        return "diverged"

    def _archive_state(self, txn: dict) -> str | None:
        if txn["archive_rel"] is None:
            return None
        try:
            data = read_bytes_or_none(self.root / txn["archive_rel"])
        except QueueTransactionError:
            return "differs"
        if data is None:
            return "absent"
        return "exact" if data == claim_bytes(txn["archive"]) else "differs"

    def reconcile(self) -> list[dict]:
        """The recovery every transaction runs first, for every claim with WAL records.
        Corrupt, diverged and ambiguous records are reported and left for an operator."""
        outcomes: list[dict] = []
        claims: set[str] = set()
        for wal_path in sorted(self.wal_dir.glob("*.json")) if self.wal_dir.is_dir() else []:
            txn = self._load(wal_path)
            if txn is None:
                outcomes.append({"wal": wal_path.name, "outcome": "corrupt"})
            else:
                claims.add(txn["claim_rel"])
        for claim_rel in sorted(claims):
            with self.locked(self.root / claim_rel):
                found, blocker = self._recover_claim(claim_rel)   # re-read under the lock (N8)
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
        for path in sorted(self.outbox_dir.glob("*.json")) if self.outbox_dir.is_dir() else []:
            marker = path.with_name(path.stem + ".published")
            if marker.exists():
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
        canonical_root(Path(path).parent)   # every existing component: no link or reparse point (N5)
        return rel

    def _wal_path(self, txn: dict) -> Path:
        return self.wal_dir / (claim_key(txn["claim_rel"]) + "." + txn["txid"] + ".json")

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
        _replace_atomic(wal_path, claim_bytes(txn))

    def _file(self, wal_path: Path, txn: dict, state: str, reason: str | None = None) -> None:
        self._set_state(wal_path, txn, state, reason)
        self._move_to_final(wal_path)

    def _move_to_final(self, wal_path: Path) -> None:
        self.final_dir.mkdir(parents=True, exist_ok=True)
        os.replace(wal_path, self.final_dir / wal_path.name)

    def _outbox(self, txn: dict) -> None:
        if txn["event"] is None:
            return
        record = {"schema": OUTBOX_SCHEMA, "root_identity": self.root_id, "idempotency_key": txn["idempotency_key"],
                  "txid": txn["txid"], "op": txn["op"], "claim_rel": txn["claim_rel"],
                  "before_sha256": txn["before_sha256"], "after_sha256": txn["after_sha256"],
                  "event": txn["event"], "event_sha256": txn["event_sha256"]}
        name = hashlib.sha256(txn["idempotency_key"].encode("utf-8")).hexdigest() + ".json"
        _create_atomic(self.outbox_dir / name, claim_bytes(record))


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
                and (txn.get("reason") is None or (isinstance(txn["reason"], str) and len(txn["reason"]) <= 200)))
    except Exception:  # noqa: BLE001 - unserializable or odd shapes are corrupt
        return False
