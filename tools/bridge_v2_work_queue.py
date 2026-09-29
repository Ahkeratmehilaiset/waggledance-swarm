#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F8/F8a/F10: the work queue on an explicit runtime root, over queue transactions.

Same wire as ``waggledance/core/work_queue.py`` and the PowerShell claim scripts: claim
files ``<root>/work_queue/claims/<safe>.json`` (indent 2, sorted keys), release records
``done/<safe task>-<safe released_at>.json``, stale archives
``done/<safe task>.<stamp>.stale_lease.json``, the same field names, the same validation
patterns and the same B7 ownership rules. It never imports ``waggledance``, never reads
the environment (the owner identity and the clock are passed in), and has no default
root. Every mutation goes through ``QueueTransactions`` (root mutex, then the legacy
``<claim>.json.lock``, re-check under the locks, compare-and-swap, WAL and outbox).

B7 ownership (F10 alignment): the authority is the owner session id plus the SHA-256 of
the owner token; pid and process-start fields are informational and never checked here.
An owned claim is refreshed, heartbeated or released only by its owning identity; an
``owner_identity: none`` claim only by an identity-less caller; a pre-B7 unowned claim is
released only with ``allow_legacy_unowned_claim``. Heartbeat never recreates an archived
claim. Owned claims are never swept here: the legacy sweep also consults the session
heartbeat file, which this dormant slice does not port, so it refuses rather than guess.
Not runtime-tested: written under the operator's no-runs directive (2026-09-29).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Sequence

from tools.bridge_v2_queue_transactions import (Plan, QueueTransactions, Refused, read_bytes_or_none,
                                                sha256_or_none)
from tools.bridge_v2_resource_scope import ScopeError, resolve_scopes, resources_overlap

AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,120}$")
ALLOWED_MODES = ("read-only", "write")
OWNER_IDENTITY_NONE = "none"
DEFAULT_LEASE_SECONDS = 900
DEFAULT_STALE_MAX_SECONDS = 12 * 60 * 60
HEX64 = re.compile(r"[0-9a-f]{64}")


class WorkQueueError(ValueError):
    """Refused; nothing was mutated."""


@dataclass(frozen=True)
class OwnerIdentity:
    """Session id plus SHA-256 of the owner token (never the token itself)."""
    owner_session_id: str
    owner_token_sha256: str


# -- legacy-compatible names and parsing ------------------------------------------------

def safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", value).strip("_") or "claim"
    if safe == value:
        return safe
    return f"{safe}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate(agent: str, task_id: str) -> None:
    if not isinstance(agent, str) or not AGENT_ID_PATTERN.fullmatch(agent):
        raise WorkQueueError("agent invalid")
    if (not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id)
            or any(part in {"", ".", ".."} for part in task_id.split("/"))):
        raise WorkQueueError("task_id invalid")


def _identity(value: OwnerIdentity | None) -> OwnerIdentity | None:
    if value is None:
        return None
    if (type(value) is not OwnerIdentity or not isinstance(value.owner_session_id, str)
            or not value.owner_session_id or not HEX64.fullmatch(str(value.owner_token_sha256))):
        raise WorkQueueError("owner identity must be a session id and a token SHA-256")
    return value


def _claim_object(data: bytes | None) -> dict | None:
    if data is None:
        return None
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Refused("unreadable claim file") from None
    if not isinstance(obj, dict):
        raise Refused("claim file must be a JSON object")
    return obj


def _owned(claim: dict) -> bool:
    return bool(claim.get("owner_session_id") and claim.get("owner_token_sha256"))


def _owns(claim: dict, identity: OwnerIdentity | None) -> bool:
    return (identity is not None and _owned(claim)
            and claim.get("owner_session_id") == identity.owner_session_id
            and claim.get("owner_token_sha256") == identity.owner_token_sha256)


def _identityless_pair(claim: dict, identity: OwnerIdentity | None) -> bool:
    return identity is None and not _owned(claim) and claim.get("owner_identity") == OWNER_IDENTITY_NONE


def _claims_dir(txns: QueueTransactions) -> Path:
    return txns.root / "work_queue" / "claims"


def _done_dir(txns: QueueTransactions) -> Path:
    return txns.root / "work_queue" / "done"


def list_claims(txns: QueueTransactions) -> list[tuple[Path, dict]]:
    """Read-only: every parseable claim (sorted by file name), as the legacy lister."""
    directory = _claims_dir(txns)
    entries = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            obj = _claim_object(read_bytes_or_none(path))
        except Exception:  # noqa: BLE001 - an unreadable claim is skipped, as legacy does
            continue
        if obj is not None:
            entries.append((path, obj))
    return entries


def find_claim(txns: QueueTransactions, task_id: str) -> Path | None:
    """The claim file whose task_id is EXACTLY task_id; a file name is never trusted."""
    preferred = _claims_dir(txns) / f"{safe_name(task_id)}.json"
    for path, obj in list_claims(txns):
        if path == preferred and obj.get("task_id") == task_id:
            return path
    for path, obj in list_claims(txns):
        if obj.get("task_id") == task_id:
            return path
    return None


def _new_claim_path(txns: QueueTransactions, task_id: str) -> Path:
    preferred = _claims_dir(txns) / f"{safe_name(task_id)}.json"
    if not preferred.exists():
        return preferred
    base = re.sub(r"[^A-Za-z0-9._-]", "_", task_id).strip("_") or "claim"
    return _claims_dir(txns) / f"{base}-{hashlib.sha256(task_id.encode('utf-8')).hexdigest()[:12]}.json"


def _key(op: str, task_id: str, before: bytes | None, now: datetime) -> str:
    return f"{op}:{task_id}:{sha256_or_none(before)}:{iso(now)}"


# -- operations ---------------------------------------------------------------------------

def claim_task(txns: QueueTransactions, *, agent: str, task_id: str, summary: str, mode: str = "read-only",
               write_scope: Sequence[str] = (), run_id: str = "", lease_seconds: int = DEFAULT_LEASE_SECONDS,
               identity: OwnerIdentity | None, cwd: str, now: datetime, force: bool = False) -> dict:
    _validate(agent, task_id)
    identity = _identity(identity)
    if not isinstance(summary, str) or not summary.strip():
        raise WorkQueueError("summary required")
    if mode not in ALLOWED_MODES:
        raise WorkQueueError("mode must be read-only or write")
    if type(lease_seconds) is not int or lease_seconds <= 0:
        raise WorkQueueError("lease_seconds must be a positive integer")
    try:
        scopes = resolve_scopes(list(write_scope), worktree=cwd, bridge_root=str(txns.root))
    except ScopeError as exc:
        raise WorkQueueError(str(exc)) from None
    if mode == "write" and not scopes:
        raise WorkQueueError("write claims require at least one write_scope path")
    entries = [entry.strip() for scope in write_scope for entry in str(scope).split(",") if entry.strip()]
    normalized = tuple(dict.fromkeys(entries))
    existing = find_claim(txns, task_id)
    _claims_dir(txns).mkdir(parents=True, exist_ok=True)  # as legacy: the sibling lock needs the directory
    claim_path = existing or _new_claim_path(txns, task_id)
    stamp = iso(now)

    def plan(before: bytes | None) -> Plan:
        current = _claim_object(before)
        if current is not None:
            if current.get("task_id") != task_id:
                raise Refused("the claim file now holds another task")
            if current.get("agent") != agent:
                raise Refused("task already claimed by another agent" if not force
                              else "force claim across agents refused")
            if not (_owns(current, identity) or _identityless_pair(current, identity)):
                raise Refused("claim is held by another session; only the owning session refreshes it")
        elif existing is not None:
            raise Refused("the claim was archived meanwhile; a refresh never recreates it")
        if mode == "write":
            for other_path, other in list_claims(txns):
                if other_path == claim_path or other.get("task_id") == task_id:
                    continue
                try:
                    other_scopes = resolve_scopes([str(s) for s in other.get("write_scope", [])],
                                                  worktree=str(other.get("cwd", "")), bridge_root=str(txns.root))
                except ScopeError:
                    raise Refused("an active claim has an unresolvable write scope; overlap unknown") from None
                if any(resources_overlap(a, b) for a in scopes for b in other_scopes):
                    raise Refused("write-scope conflict with active claim " + str(other.get("task_id"))[:128])
        claim = {"agent": agent, "task_id": task_id, "summary": summary.strip(), "mode": mode,
                 "write_scope": list(normalized), "run_id": run_id, "claimed_at_utc": stamp,
                 "last_heartbeat_utc": stamp, "lease_seconds": lease_seconds,
                 "claim_lease_expires_utc": iso(now + timedelta(seconds=lease_seconds)), "cwd": cwd}
        if identity is not None:
            claim.update(owner_session_id=identity.owner_session_id,
                         owner_token_sha256=identity.owner_token_sha256)
        else:
            claim["owner_identity"] = OWNER_IDENTITY_NONE
        return Plan(after=claim, expect_absent=current is None, result=claim,
                    event={"type": "claim", "agent": agent, "task_id": task_id, "status": "active",
                           "generation_before": sha256_or_none(before)})

    return txns.transact("claim", claim_path, _key("claim", task_id, read_bytes_or_none(claim_path), now), plan)


def release_task(txns: QueueTransactions, *, agent: str, task_id: str, release_status: str = "done",
                 release_message: str = "", identity: OwnerIdentity | None, now: datetime,
                 allow_legacy_unowned_claim: bool = False) -> dict:
    _validate(agent, task_id)
    identity = _identity(identity)
    if not isinstance(release_status, str) or not release_status.strip():
        raise WorkQueueError("release_status required")
    claim_path = find_claim(txns, task_id)
    if claim_path is None:
        raise WorkQueueError("no active claim for task")
    released_at = iso(now)
    done_path = _done_dir(txns) / f"{safe_name(task_id)}-{safe_name(released_at)}.json"

    def plan(before: bytes | None) -> Plan:
        current = _claim_object(before)
        if current is None or current.get("task_id") != task_id:
            raise Refused("no active claim for task (archived or replaced meanwhile)")
        if current.get("agent") != agent:
            raise Refused("release rejected: claim held by another agent")
        if _owned(current):
            if not _owns(current, identity):
                raise Refused("release rejected: claim is owned by another session")
        elif not _identityless_pair(current, identity) and not allow_legacy_unowned_claim:
            raise Refused("release rejected: pre-B7 claim; adopt it with allow_legacy_unowned_claim")
        record = {"agent": agent, "task_id": task_id, "summary": current.get("summary", ""),
                  "release_status": release_status.strip(), "release_message": release_message.strip(),
                  "claimed_at_utc": current.get("claimed_at_utc", ""), "released_at_utc": released_at}
        return Plan(after=None, archive=(done_path, record), result=record,
                    event={"type": "release", "agent": agent, "task_id": task_id,
                           "status": record["release_status"], "generation_before": sha256_or_none(before)})

    return txns.transact("release", claim_path, _key("release", task_id, read_bytes_or_none(claim_path), now), plan)


def heartbeat(txns: QueueTransactions, *, agent: str, task_id: str, identity: OwnerIdentity | None,
              now: datetime, lease_seconds: int | None = None) -> dict:
    _validate(agent, task_id)
    identity = _identity(identity)
    claim_path = find_claim(txns, task_id)
    if claim_path is None:
        raise WorkQueueError("no active claim for task")

    def plan(before: bytes | None) -> Plan:
        current = _claim_object(before)
        # F8a: if a release won the lock first, the claim is gone and is never resurrected.
        if current is None or current.get("task_id") != task_id:
            raise Refused("heartbeat rejected: the claim was released or replaced meanwhile")
        if current.get("agent") != agent:
            raise Refused("heartbeat rejected: claim held by another agent")
        if not _owns(current, identity):
            raise Refused("heartbeat rejected: only the owning session extends a lease")
        seconds = lease_seconds if lease_seconds else current.get("lease_seconds", DEFAULT_LEASE_SECONDS)
        if type(seconds) is not int or seconds <= 0:
            raise Refused("lease_seconds must be a positive integer")
        refreshed = dict(current)   # every other field, including PowerShell-only ones, is kept
        refreshed.update(last_heartbeat_utc=iso(now), lease_seconds=seconds,
                         claim_lease_expires_utc=iso(now + timedelta(seconds=seconds)))
        return Plan(after=refreshed, result=refreshed, event=None)

    return txns.transact("heartbeat", claim_path, _key("heartbeat", task_id, read_bytes_or_none(claim_path), now), plan)


def detect_stale_claims(txns: QueueTransactions, *, now: datetime,
                        max_age_seconds: int = DEFAULT_STALE_MAX_SECONDS) -> list[dict]:
    """Read-only: claims whose last heartbeat is older than max_age (unparseable = stale)."""
    cutoff = now - timedelta(seconds=max_age_seconds)
    stale = []
    for _, claim in list_claims(txns):
        try:
            last = parse_utc(str(claim.get("last_heartbeat_utc", "")))
        except (ValueError, TypeError):
            stale.append(claim)
            continue
        if last < cutoff:
            stale.append(claim)
    return stale


def archive_stale_claims(txns: QueueTransactions, *, now: datetime, apply: bool = False,
                         max_age_seconds: int = DEFAULT_STALE_MAX_SECONDS) -> list[dict]:
    """Archive unowned stale claims to done/*.stale_lease.json. Owned claims are refused
    here (the legacy rule also needs the session heartbeat, not ported in this slice)."""
    results = []
    stamp = iso(now).replace(":", "").replace("-", "")
    for claim in detect_stale_claims(txns, now=now, max_age_seconds=max_age_seconds):
        task_id = str(claim.get("task_id", ""))
        entry = {"task_id": task_id[:128], "agent": str(claim.get("agent", ""))[:64], "applied": False}
        if _owned(claim):
            results.append(dict(entry, outcome="owned_claim_needs_session_heartbeat_port"))
            continue
        claim_path = find_claim(txns, task_id) if TASK_ID_PATTERN.fullmatch(task_id) else None
        if claim_path is None:
            results.append(dict(entry, outcome="not_found_or_invalid"))
            continue
        archive_path = _done_dir(txns) / f"{safe_name(task_id)}.{stamp}.stale_lease.json"
        if not apply:
            results.append(dict(entry, outcome="planned", archive=archive_path.name))
            continue

        def plan(before: bytes | None, task_id=task_id, archive_path=archive_path) -> Plan:
            current = _claim_object(before)
            if current is None or current.get("task_id") != task_id or _owned(current):
                raise Refused("the stale claim changed meanwhile")
            try:
                if parse_utc(str(current.get("last_heartbeat_utc", ""))) >= now - timedelta(seconds=max_age_seconds):
                    raise Refused("the claim was refreshed meanwhile")
            except (ValueError, TypeError):
                pass
            payload = dict(current, released_at_utc=iso(now), release_status="stale_lease",
                           release_reason="stale_lease_archived")
            return Plan(after=None, archive=(archive_path, payload), result=archive_path.name,
                        event={"type": "stale_archive", "agent": current.get("agent"), "task_id": task_id,
                               "status": "stale_lease", "generation_before": sha256_or_none(before)})

        try:
            name = txns.transact("stale_archive", claim_path,
                                 _key("stale_archive", task_id, read_bytes_or_none(claim_path), now), plan)
            results.append(dict(entry, outcome="archived", applied=True, archive=name))
        except Refused as refusal:
            results.append(dict(entry, outcome="skipped", reason=str(refusal)[:160]))
    return results
