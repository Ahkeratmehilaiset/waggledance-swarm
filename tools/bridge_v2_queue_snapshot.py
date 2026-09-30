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

Advisory: the timestamp says what the queue held while the mutex was held. It does not prove continuous
readiness and does not make a worker exclusive (the worker's own keyed claim is the only atomic step). The
legacy PowerShell claim writer does not take this mutex yet: its concurrent write shows as unreadable or is
seen before or after.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

from tools.bridge_v2_queue_transactions import (FINAL, QueueTransactionError, QueueTransactions, _guard,
                                                mutex_name, read_bytes_or_none)

SNAPSHOT_SCHEMA = "wd.queue-claims-snapshot.v1"
MAX_ENTRIES = 4096            # W3's bound on claims plus pending records
MAX_FIELD_TEXT = 512          # W3's bound on agent, task and session text
OWNER_IDENTITY_NONE = "none"


class QueueSnapshotError(ValueError):
    """A caller contract was violated; no snapshot was taken."""


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
            obj = json.loads(data.decode("utf-8-sig"))
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
        except QueueTransactionError:
            unreadable += 1
            continue
        txn = txns._load(path)                               # bound to this root and its own name, or None
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
