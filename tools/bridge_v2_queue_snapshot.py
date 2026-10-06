# SPDX-License-Identifier: BUSL-1.1
"""Read-only v2 queue claims snapshot for W3 (RCO1 2026-09-30, Lead request f8d485a1).

One call takes the runtime root's shared queue mutex through the caller's port (the same name every v2
writer takes first), reads the caller's clock port once, and re-reads every active claim file and every
unfinished transaction record (prepared or applied, not yet filed) of that root into
``wd.queue-claims-snapshot.v1``, the exact schema tools/wd_routing_load.py (W3) consumes.

It never sweeps, recovers, publishes, releases, creates or dispatches, never creates a directory or a lock
file, and takes no claim lock. Anything it cannot read or prove is counted as unreadable or makes the
snapshot incomplete; it is never reported as an empty queue. A holder is passed through as written (W3
decides membership); a claim or plan without exact agent, task and session evidence is unreadable.

Claim files and WAL records are parsed as strict JSON: a duplicate key at any depth, NaN, Infinity or a
number that overflows to infinity makes the record unreadable, never "the last duplicate wins" (RCO2 S1).
Claims keep the legacy writers' UTF-8 byte-order-mark tolerance; WAL records are UTF-8 as the queue writes
them. A path check that fails with an OSError counts as unreadable for a WAL record exactly as for a claim
(RCO2 S3); cancellation (KeyboardInterrupt, SystemExit) is never caught.

Advisory: the timestamp says what the queue held while the mutex was held. It does not prove continuous
readiness and does not make a worker exclusive (the worker's own keyed claim is the only atomic step).
``complete`` is complete for the writers that take this mutex first. In the #1756 composition every
in-repo claims, done and WAL writer does: the v2 writers by design, the legacy PowerShell writers
(Claim-AgentTask, Release-AgentTask, Invoke-StaleClaimSweep and ClaimLeaseHeartbeat's lease refresh, through
Enter-BridgeQueueRootMutex) and the legacy Python ones (tools/work_queue.py claim, release and heartbeat, and
tools/work_queue_sweep_stale.py --apply). That holds on Windows only: the mutex is a Windows named mutex, and
off Windows the legacy writers take none (tools/work_queue.py _root_mutex, Enter-BridgeQueueRootMutex), so
there a snapshot excludes no legacy writer. Before that participation (S2), a claim written after this snapshot
listed the claims directory could be missing while ``complete`` stayed True (RCO2 S2, reproduced 21:24:58Z).
Not covered (claude-rco-2 integration review, 2026-10-01): a process that writes a claim file directly
instead of through those helpers (the Codex lane's writable directories include work_queue), a writer
reached by a runtime-built name, and a writer that is neither PowerShell nor Python. Session heartbeat
files are written without the mutex; this module does not read them. So a complete snapshot still does not
by itself prove a lane idle for W3 idle dispatch: that stays a separate, gated decision, which this module
neither proves nor makes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Callable

from tools.bridge_v2_queue_transactions import (FINAL, QueueTransactionError, QueueTransactions, _guard,
                                                _valid_txn, mutex_name, read_bytes_or_none)

SNAPSHOT_SCHEMA = "wd.queue-claims-snapshot.v1"
MAX_ENTRIES = 4096            # W3's bound on claims plus pending records
MAX_FIELD_TEXT = 512          # W3's bound on agent, task and session text
OWNER_IDENTITY_NONE = "none"


class QueueSnapshotError(ValueError):
    """A caller contract was violated; no snapshot was taken."""


def _strict_object(pairs: list) -> dict:
    # Keys that differ only by case are duplicates too (RCO1 F642-N1): PowerShell 5.1 and 7 refuse them, so the
    # snapshot never picks one. casefold() is at least as broad as PowerShell's ordinal ignore-case (conservative).
    keys = [key.casefold() for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _non_finite(token: str) -> Any:
    raise ValueError("non-finite number")


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError("non-finite number")      # 1e400: NaN's twin by another route
    return value


def _strict_json(text: str) -> Any:
    """Strict JSON (RCO2 S1): a duplicate key at any depth or a non-finite number raises ValueError."""
    return json.loads(text, object_pairs_hook=_strict_object, parse_constant=_non_finite, parse_float=_finite_float)


def _wal_record(txns: QueueTransactions, path: Path) -> dict | None:
    """The WAL record at ``path``: the checks of QueueTransactions._load (bound to this root and to its own
    file name) with a strict parse, or None when it is unreadable. Only read and parse errors are caught."""
    try:
        data = read_bytes_or_none(path)
        txn = None if data is None else _strict_json(data.decode("utf-8"))
    except (QueueTransactionError, OSError, ValueError, RecursionError):
        return None
    return txn if _valid_txn(txn, root_identity=txns.root_id, name=path.name) else None


def _listed(directory: Path, kind: str) -> tuple[list[Path], str]:
    """Sorted *.json names directly in ``directory`` and "ok", "absent" or "unprovable" (a link or reparse
    point on any component, a non-directory, or an I/O error while listing)."""
    try:
        _guard(directory, kind, leaf="dir")
        if not directory.is_dir():
            return [], "absent"
        return sorted(path for path in directory.iterdir() if path.name.endswith(".json")), "ok"
    except (QueueTransactionError, OSError):
        return [], "unprovable"


def _entry(source: str, obj: Any) -> dict | None:
    """The W3 entry for one claim object, or None when its agent, task or session evidence is not exact."""
    if type(obj) is not dict:
        return None
    agent, task_id = obj.get("agent"), obj.get("task_id")
    if type(agent) is not str or not 0 < len(agent) <= MAX_FIELD_TEXT:
        return None
    if type(task_id) is not str or not 0 < len(task_id) <= MAX_FIELD_TEXT:
        return None
    session, token = obj.get("owner_session_id"), obj.get("owner_token_sha256")
    if session is None and token is None and obj.get("owner_identity") == OWNER_IDENTITY_NONE:
        owner = None                                         # an identity-less (B7-era) claim, explicitly
    elif type(session) is str and 0 < len(session) <= MAX_FIELD_TEXT and type(token) is str and token:
        owner = session
    else:
        return None                                          # missing or foreign session evidence
    return {"source": source, "agent": agent, "task_id": task_id, "owner_session_id": owner}


def _read(txns: QueueTransactions, now: datetime) -> dict:
    complete, unreadable = True, 0
    claims: list[dict] = []
    pending: list[dict] = []
    held: dict[str, tuple[str, str]] = {}
    claim_paths, claim_state = _listed(txns.claims_dir, "claims directory")
    wal_paths, wal_state = _listed(txns.wal_dir, "WAL directory")
    if claim_state != "ok" or wal_state == "unprovable":
        complete = False                                     # never an empty queue from what cannot be read
    if len(claim_paths) + len(wal_paths) > MAX_ENTRIES:
        return _snapshot(now, False, 0, [], [])              # bounded work; unknown, never empty
    for path in claim_paths:
        try:
            _guard(path, "claim")
            data = read_bytes_or_none(path)
        except (QueueTransactionError, OSError):
            unreadable += 1
            continue
        if data is None:                                     # listed, then gone: not one consistent state
            complete = False
            continue
        try:
            obj = _strict_json(data.decode("utf-8-sig"))
        except (ValueError, RecursionError):
            unreadable += 1
            continue
        entry = _entry("claim", obj)
        if entry is None:
            unreadable += 1
            continue
        claims.append(entry)
        held[path.name] = (entry["agent"], entry["task_id"])
    for path in wal_paths:
        try:
            _guard(path, "WAL record")
        except (QueueTransactionError, OSError):             # as for a claim (RCO2 S3); cancellation propagates
            unreadable += 1
            continue
        txn = _wal_record(txns, path)                        # strict, bound to this root and its own name, or None
        if txn is None:
            unreadable += 1
            continue
        if txn["state"] in FINAL or txn["after"] is None:    # filed, diverged (applied) or a release in flight
            continue
        entry = _entry("pending", txn["after"])
        if entry is None:
            unreadable += 1
            continue
        current = held.get(Path(txn["claim_rel"]).name)
        if current is not None and current != (entry["agent"], entry["task_id"]):
            complete = False                                 # a plan for X beside an active Y at one claim
        pending.append(entry)
    return _snapshot(now, complete, unreadable, claims, pending)


def _snapshot(now: datetime, complete: bool, unreadable: int, claims: list, pending: list) -> dict:
    return {"schema": SNAPSHOT_SCHEMA, "observed_utc": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "complete": complete, "unreadable": unreadable, "claims": claims, "pending": pending}


def queue_claims_snapshot(runtime_root: str | Path, *, mutex: Any, clock: Callable[[], datetime],
                          lock_timeout_seconds: float = 4.0) -> dict:
    """One wd.queue-claims-snapshot.v1 of ``runtime_root``, read entirely under its queue mutex."""
    txns = QueueTransactions(runtime_root)                   # validates the root; creates nothing
    if mutex is None or not callable(getattr(mutex, "hold", None)):
        raise QueueSnapshotError("a mutex port is required: the snapshot is read under the root's queue mutex")
    if not callable(clock):
        raise QueueSnapshotError("a clock port is required")
    if type(lock_timeout_seconds) not in (int, float) or not 0 < lock_timeout_seconds <= 60:
        raise QueueSnapshotError("lock timeout must be in (0, 60] seconds")
    with mutex.hold(mutex_name(txns.root), lock_timeout_seconds):
        now = clock()
        if type(now) is not datetime or now.tzinfo is not timezone.utc:
            raise QueueSnapshotError("the clock port must return an aware UTC datetime")
        return _read(txns, now)
