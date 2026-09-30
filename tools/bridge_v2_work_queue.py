#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F8/F8a/F10: the work queue on an explicit runtime root, over queue transactions.

Same wire as ``waggledance/core/work_queue.py`` and the PowerShell claim scripts: claim
files ``<root>/work_queue/claims/<safe>.json`` (indent 2, sorted keys), release records
``done/<safe task>-<safe released_at>.json``, stale archives
``done/<safe task>.<stamp>.stale_lease.json``, the same field names, the same validation
patterns and the same B7 ownership rules. It never imports ``waggledance``. The core
operations take the owner identity and the clock explicitly and have no default root;
only the legacy-compatible facade at the end (for the consumer cutover) resolves a root,
and ``resolve_bridge_root`` there is the single documented environment reader. Every mutation goes through ``QueueTransactions`` (root mutex, then the legacy
``<claim>.json.lock``, re-check under the locks, compare-and-swap, WAL and outbox).

B7 ownership (F10 alignment): the authority is the owner session id plus the SHA-256 of
the owner token; pid and process-start fields are informational and never checked here.
The caller presents the RAW token it holds (``OwnerIdentity``) and only its SHA-256 is
written or compared, exactly as core derives it; the SHA-256 readable in a claim file is
never a credential (RCO1 S8). An owned claim is refreshed, heartbeated or released only
by its owning identity; an ``owner_identity: none`` claim only by an identity-less
caller; a pre-B7 unowned claim is released only with ``allow_legacy_unowned_claim``.
Heartbeat never recreates an archived claim. A write claim is refused while any active
claim is unreadable or over the size bound (overlap unknown, never skipped, S9); only
other WRITE claims conflict, and a stored string scope is one entry list, never its
characters (S7). The facade's ``archive_stale_claims`` keeps the core selection rules
(operator and system never swept; an owned claim only when its lease expired AND its
session heartbeat is provably not live; last heartbeat falling back to claimed_at) and
applies only through injected transactions: without ports a sweep is refused, never
defaulted. A changed claim is skipped as in core; every other transaction refusal (lock
timeout, a blocked claim, a record conflict, a bound) is a ``WorkQueueError`` (S6). At apply
time, under the locks, an owned claim's session heartbeat and lease are re-read with the
transactions' own clock; live or unknown skips it (Tools 51ada Q-SWEEP-LIVE-RECHECK). That
re-read and the delete are one FENCED step (F8 session-heartbeat fence, RCO1 2026-09-30): the
PowerShell session-heartbeat writer takes neither the claim lock nor the runtime mutex, only the
beat's own sibling lock ``<beat>.json.lock``, so the sweep names the owner's beat as a fence and
holds that same lock (innermost) from before the re-read through the delete. A beat attempted
meanwhile waits or is skipped by its writer; a writer holding the lock past the timeout fails the
sweep closed (WorkQueueError, nothing archived).

Consumer composition is DEFERRED (Lead's B1 decision): the consumers keep core until
validated ports, publication and fencing exist. This module never falls back to or
imports core, and its ports-less apply refusal must not be hidden by weakening a core
CLI test.
Written under the operator's no-runs directive (2026-09-29); its fixtures were run on 2026-09-30
(RCO1, Windows, isolated tmp roots) and never against a live root.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Sequence

from tools.bridge_v2_queue_transactions import (Plan, QueueTransactionError, QueueTransactions, Refused,
                                                read_bytes_or_none, sha256_or_none)
from tools.bridge_v2_resource_scope import ScopeError, resolve_scopes, resources_overlap

AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,120}$")
ALLOWED_MODES = ("read-only", "write")
OWNER_IDENTITY_NONE = "none"
DEFAULT_LEASE_SECONDS = 900
DEFAULT_STALE_MAX_SECONDS = 12 * 60 * 60
MAX_SUMMARY_CHARS = 16 * 1024
MAX_SESSION_CHARS = 256
MAX_TOKEN_CHARS = 4096


class WorkQueueError(ValueError):
    """Refused: this call's own mutation did not happen. The message says otherwise in two cases:
    "outcome unknown ... MAY ALREADY BE APPLIED (txid ...)" (an OutcomeUnknown after its WAL record
    was on disk), or "the claim change WAS applied" (its publication is blocked). Recovery under the
    locks may first have finished earlier unfinished work on the same claim, and the message names
    only those effect-bearing outcomes (Tools 51ada Q-OUTCOME-TRUTH, RCO1 Q-F2/Q-F3).

    Only archive_stale_claims translates transaction errors into WorkQueueError (Fable 8c091 N6).
    claim_task, release_task and heartbeat raise the QueueTransactionError itself: its ``recovered`` lists that
    earlier work, and an OutcomeUnknown's ``txid`` names this call's record, or an earlier one when
    the error came from recovery."""


@dataclass(frozen=True)
class OwnerIdentity:
    """The owner session id plus the RAW owner token the caller holds (RCO1 S8). Only the
    token's SHA-256 (of its UTF-8 bytes, as core ``owner_identity_from_env``) is ever
    written or compared. The SHA-256 readable in a claim file is never a credential: given
    as a token, it hashes to something else and matches no claim."""
    owner_session_id: str
    owner_token: str = field(repr=False)

    @property
    def owner_token_sha256(self) -> str:
        return hashlib.sha256(self.owner_token.encode("utf-8")).hexdigest()


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
            or not 0 < len(value.owner_session_id) <= MAX_SESSION_CHARS):
        raise WorkQueueError("owner identity must carry a session id")
    if not isinstance(value.owner_token, str) or not 0 < len(value.owner_token) <= MAX_TOKEN_CHARS:
        raise WorkQueueError("owner identity needs the raw owner token the caller holds; "
                             "a SHA-256 read from a claim file is never a credential")
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


def list_claim_entries(txns: QueueTransactions) -> list[tuple[Path, dict]]:
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


def _strict_claim_entries(txns: QueueTransactions) -> list[tuple[Path, dict | None]]:
    """Every claim file for the overlap check; an unreadable, oversized or non-object one is
    kept as None (overlap unknown) instead of being skipped (RCO1 S9)."""
    directory = _claims_dir(txns)
    entries: list[tuple[Path, dict | None]] = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            data = read_bytes_or_none(path)
            if data is None:
                continue   # removed meanwhile
            entries.append((path, _claim_object(data)))
        except Exception:  # noqa: BLE001 - unreadable: overlap unknown
            entries.append((path, None))
    return entries


def _requested_scope(values: object) -> tuple[str, ...]:
    """The caller's write_scope: one string is ONE entry (never its characters); a list or
    tuple must hold strings only (RCO1 S7)."""
    if isinstance(values, str):
        return (values,)
    if not isinstance(values, (list, tuple)) or not all(isinstance(value, str) for value in values):
        raise WorkQueueError("write_scope must be a string or a list of strings")
    return tuple(values)


def _stored_scope(value: object) -> tuple[str, ...] | None:
    """Another claim's stored write_scope: absent or null is empty, a string is one
    comma-separated entry list, a list must hold strings; any other shape is None
    (overlap unknown, fail closed) (RCO1 S7)."""
    if value is None:
        return ()
    if isinstance(value, str) or (isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)):
        return _scope_entries(value)
    return None


def find_claim(txns: QueueTransactions, task_id: str) -> Path | None:
    """The claim file whose task_id is EXACTLY task_id; a file name is never trusted."""
    preferred = _claims_dir(txns) / f"{safe_name(task_id)}.json"
    for path, obj in list_claim_entries(txns):
        if path == preferred and obj.get("task_id") == task_id:
            return path
    for path, obj in list_claim_entries(txns):
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
    if len(summary) > MAX_SUMMARY_CHARS:
        raise WorkQueueError("summary exceeds 16384 characters")   # S9: bounded before any mutation
    if mode not in ALLOWED_MODES:
        raise WorkQueueError("mode must be read-only or write")
    if type(lease_seconds) is not int or lease_seconds <= 0:
        raise WorkQueueError("lease_seconds must be a positive integer")
    requested = _requested_scope(write_scope)
    try:
        scopes = resolve_scopes(list(requested), worktree=cwd, bridge_root=str(txns.root))
    except ScopeError as exc:
        raise WorkQueueError(str(exc)) from None
    if mode == "write" and not scopes:
        raise WorkQueueError("write claims require at least one write_scope path")
    normalized = _scope_entries(requested)
    if not txns.ports_on:   # N3: nothing, not even the claims directory, is created without ports
        raise WorkQueueError("queue ports are off: a mutex port and a claim-lock port are required")
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
            for other_path, other in _strict_claim_entries(txns):
                if other_path == claim_path:
                    continue
                if other is None:
                    raise Refused("an active claim is unreadable or over the size bound; overlap unknown")
                if other.get("task_id") == task_id or str(other.get("mode", "read-only")) != "write":
                    continue   # as core: only other WRITE claims conflict
                other_entries = _stored_scope(other.get("write_scope"))
                if other_entries is None:
                    raise Refused("an active claim has an unresolvable write scope; overlap unknown")
                try:
                    other_scopes = resolve_scopes(list(other_entries), worktree=str(other.get("cwd", "")),
                                                  bridge_root=str(txns.root))
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
    for _, claim in list_claim_entries(txns):
        try:
            last = parse_utc(str(claim.get("last_heartbeat_utc", "")))
        except (ValueError, TypeError):
            stale.append(claim)
            continue
        if last < cutoff:
            stale.append(claim)
    return stale


# -- legacy-compatible facade (consumer cutover, RCO1 9f974e60) ---------------------------
# The consumers import AGENT_ID_PATTERN, DEFAULT_BRIDGE_ROOT, ArchivedClaim, Claim,
# WorkQueueError, archive_stale_claims, list_claims and resolve_bridge_root with the core
# names and signatures. resolve_bridge_root is the ONLY environment reader in the v2 queue
# (the consumer boundary, core-equal semantics); everything above stays explicit.

DEFAULT_BRIDGE_ROOT = Path(__file__).resolve().parents[1] / ".agent-bridge"
BRIDGE_ROOT_ENV_NAMES = ("AGENT_BRIDGE_RUNTIME_ROOT", "AGENT_BRIDGE_ROOT")
PRIVILEGED_AGENTS = frozenset({"operator", "system"})
SESSION_HEARTBEAT_TTL_DEFAULT = 180
SESSION_HEARTBEAT_TTL_MAX = 900


def resolve_bridge_root(bridge_root: Path | None = None) -> Path:
    """Core-equal: an explicit root wins, then the environment, then the repo sidecar."""
    if bridge_root is not None:
        return Path(bridge_root)
    for env_name in BRIDGE_ROOT_ENV_NAMES:
        value = os.environ.get(env_name, "").strip()
        if value:
            return Path(value)
    return DEFAULT_BRIDGE_ROOT


@dataclass(frozen=True)
class Claim:
    """One active claim, field for field as core ``Claim``."""
    agent: str
    task_id: str
    summary: str
    mode: str
    write_scope: tuple[str, ...]
    run_id: str
    claimed_at_utc: str
    last_heartbeat_utc: str
    lease_seconds: int
    claim_lease_expires_utc: str = ""
    role: str = ""
    agent_uuid: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    cwd: str = ""
    owner_session_id: str = ""
    owner_token_sha256: str = ""
    owner_identity: str = ""


@dataclass(frozen=True)
class ArchivedClaim:
    """Outcome of one stale-sweep entry (dry run or applied), as core ``ArchivedClaim``."""
    claim: Claim
    archived_path: Path
    age_seconds: int
    release_reason: str
    applied: bool


def _scope_entries(values: object) -> tuple[str, ...]:
    """Core ``_normalize_write_scope_entries``: comma-split, stripped, first occurrence kept.
    The dedupe is set-based and linear (RCO1 S5); the 256 KiB read bound caps the input."""
    source = (values,) if isinstance(values, str) else values if isinstance(values, (list, tuple)) else ()
    seen: set[str] = set()
    result: list[str] = []
    for value in source:
        for item in str(value).split(","):
            item = item.strip()
            if item and item not in seen:
                seen.add(item)
                result.append(item)
    return tuple(result)


def claim_from_object(data: dict) -> Claim:
    """Core ``_claim_from_object``; a non-integer lease is refused (core would raise ValueError)."""
    try:
        lease = int(data.get("lease_seconds", DEFAULT_LEASE_SECONDS))
    except (TypeError, ValueError):
        raise WorkQueueError("claim lease_seconds is not an integer") from None
    return Claim(agent=str(data.get("agent", "")), task_id=str(data.get("task_id", "")),
                 summary=str(data.get("summary", "")), mode=str(data.get("mode", "read-only")),
                 write_scope=_scope_entries(data.get("write_scope", [])), run_id=str(data.get("run_id", "")),
                 claimed_at_utc=str(data.get("claimed_at_utc", "")),
                 last_heartbeat_utc=str(data.get("last_heartbeat_utc", "")), lease_seconds=lease,
                 claim_lease_expires_utc=str(data.get("claim_lease_expires_utc", "")),
                 role=str(data.get("role", "")), agent_uuid=str(data.get("agent_uuid", "")),
                 capabilities=tuple(str(s) for s in data.get("capabilities", []) or [] if s),
                 cwd=str(data.get("cwd", "")), owner_session_id=str(data.get("owner_session_id", "") or ""),
                 owner_token_sha256=str(data.get("owner_token_sha256", "") or ""),
                 owner_identity=str(data.get("owner_identity", "") or ""))


def _claim_entries(bridge: Path) -> list[tuple[Path, Claim]]:
    directory = bridge / "work_queue" / "claims"
    entries = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            obj = _claim_object(read_bytes_or_none(path))
            if obj is not None:
                entries.append((path, claim_from_object(obj)))
        except Exception:  # noqa: BLE001 - an unreadable claim is skipped, as core does
            continue
    return entries


def list_claims(bridge_root: Path | None = None) -> list[Claim]:
    """Core signature, read-only: every parseable active claim."""
    return [claim for _, claim in _claim_entries(resolve_bridge_root(bridge_root))]


def _session_heartbeat_path(bridge: Path, claim: Claim) -> Path | None:
    """The owner's session-heartbeat artifact (core and ``Get-BridgeSessionHeartbeatPath``: the SHA-256 of
    session and token hash), or None for a claim without both owner fields."""
    if not claim.owner_session_id or not claim.owner_token_sha256:
        return None
    digest = hashlib.sha256(f"{claim.owner_session_id}\n{claim.owner_token_sha256}".encode("utf-8")).hexdigest()
    return bridge / "work_queue" / "heartbeats" / f"{digest}.json"


def _session_heartbeat_state(bridge: Path, claim: Claim, now: datetime) -> str:
    """Core ``_session_heartbeat_state``: live, dead or unknown (an unreadable artifact is unknown)."""
    path = _session_heartbeat_path(bridge, claim)
    if path is None:
        return "dead"
    if not path.exists():
        return "dead"
    try:   # bounded like every other record; a deep nesting is unknown, never a crash (N7)
        data = read_bytes_or_none(path)
        beat = None if data is None else json.loads(data.decode("utf-8"))
    except (QueueTransactionError, OSError, UnicodeDecodeError, ValueError, RecursionError):
        return "unknown"
    if beat is None:
        return "dead"
    if not isinstance(beat, dict) or any(name not in beat for name in
                                         ("owner_session_id", "owner_token_sha256", "last_beat_utc")):
        return "unknown"
    if (str(beat["owner_session_id"]) != claim.owner_session_id
            or str(beat["owner_token_sha256"]) != claim.owner_token_sha256):
        return "unknown"
    try:
        parsed_ttl = int(str(beat.get("ttl_seconds", "")))
    except ValueError:
        parsed_ttl = 0
    ttl = min(parsed_ttl if parsed_ttl > 0 else SESSION_HEARTBEAT_TTL_DEFAULT, SESSION_HEARTBEAT_TTL_MAX)
    try:
        beat_utc = parse_utc(str(beat["last_beat_utc"]))
    except (ValueError, TypeError):
        return "unknown"
    if beat_utc > now + timedelta(seconds=60):
        return "dead"  # a future-dated beat never keeps a claim alive
    return "live" if (now - beat_utc).total_seconds() <= ttl else "dead"


def _apply_time(txns: QueueTransactions) -> datetime | None:
    """The transactions' own clock at apply time as aware UTC, or None (unknown): exactly a datetime,
    one offset read that is exactly a timedelta, subtracted (never astimezone, never local time)."""
    try:
        moment = txns.clock()
        if type(moment) is not datetime:
            return None
        offset = moment.utcoffset()
        if type(offset) is not timedelta:
            return None
        return (moment.replace(tzinfo=None) - offset).replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001 - an unreadable clock is unknown, and unknown refuses the sweep
        return None


def _owned_claim_sweepable(bridge: Path, claim: Claim, now: datetime) -> bool:
    """Core rule: the lease expired AND the owner's session heartbeat is provably not live."""
    try:
        base = parse_utc(claim.last_heartbeat_utc or claim.claimed_at_utc)
    except (ValueError, TypeError):
        return False
    expires = base + timedelta(seconds=max(int(claim.lease_seconds), 1))
    if claim.claim_lease_expires_utc:
        try:
            recorded = parse_utc(claim.claim_lease_expires_utc)
        except (ValueError, TypeError):
            return False
        if recorded > expires:
            expires = recorded
    if now < expires:
        return False
    return _session_heartbeat_state(bridge, claim, now) == "dead"


def _stale_payload(claim: Claim, now: datetime, reason: str) -> dict:
    payload = {"agent": claim.agent, "task_id": claim.task_id, "summary": claim.summary, "mode": claim.mode,
               "write_scope": list(claim.write_scope), "run_id": claim.run_id,
               "claimed_at_utc": claim.claimed_at_utc, "last_heartbeat_utc": claim.last_heartbeat_utc,
               "lease_seconds": claim.lease_seconds, "claim_lease_expires_utc": claim.claim_lease_expires_utc,
               "released_at_utc": iso(now), "release_status": "stale_lease", "release_reason": reason}
    if claim.role:
        payload["role"] = claim.role
    if claim.agent_uuid:
        payload["agent_uuid"] = claim.agent_uuid
    if claim.capabilities:
        payload["capabilities"] = list(claim.capabilities)
    if claim.owner_session_id:
        payload["owner_session_id"] = claim.owner_session_id
    if claim.owner_token_sha256:
        payload["owner_token_sha256"] = claim.owner_token_sha256
    return payload


def archive_stale_claims(*, bridge_root: Path | None = None, now_utc: datetime | None = None,
                         max_age_seconds: int = DEFAULT_STALE_MAX_SECONDS, apply: bool = False,
                         transactions: QueueTransactions | None = None) -> list[ArchivedClaim]:
    """Core signature and selection rules. A dry run is read-only. ``apply=True`` mutates only
    through injected ``transactions`` (root mutex, then the claim lock, compare-and-swap, WAL,
    outbox) for the SAME root; without them the sweep is refused: there is no default port."""
    if type(max_age_seconds) is not int or max_age_seconds <= 0:
        raise WorkQueueError(f"max_age_seconds must be positive, got {max_age_seconds}")
    bridge = resolve_bridge_root(bridge_root)
    # The consumer prefixes "sweep refused: " itself, so these messages do not (RCO1 N2).
    if apply and (transactions is None or not transactions.ports_on):
        raise WorkQueueError("the v2 queue has no injected ports (dormant); dry run only")
    if apply and Path(transactions.root) != bridge:
        raise WorkQueueError("the transactions are for another runtime root")
    now = now_utc or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=max_age_seconds)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    archived: list[ArchivedClaim] = []
    for claim_file, claim in _claim_entries(bridge):
        if claim.agent in PRIVILEGED_AGENTS:
            continue
        if claim.owner_session_id and claim.owner_token_sha256 and not _owned_claim_sweepable(bridge, claim, now):
            continue
        candidates = [value for value in (claim.last_heartbeat_utc, claim.claimed_at_utc) if value]
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            continue
        last = None
        for candidate in candidates:
            try:
                last = parse_utc(candidate)
                break
            except (ValueError, TypeError):
                continue
        if last is None:
            age_seconds = max_age_seconds
        else:
            if last >= cutoff:
                continue
            age_seconds = int((now - last).total_seconds())
        archive_path = bridge / "work_queue" / "done" / f"{safe_name(claim.task_id)}.{stamp}.stale_lease.json"
        reason = f"last_heartbeat_utc was {age_seconds}s old; lease threshold {max_age_seconds}s"
        if apply:
            def plan(before: bytes | None, claim=claim, archive_path=archive_path, reason=reason) -> Plan:
                current = _claim_object(before)
                # Exactly the claim this decision was made on; a successor claim is never deleted.
                if current is None or claim_from_object(current) != claim:
                    raise Refused("the claim changed since the listing")
                # Q-SWEEP-LIVE-RECHECK: the owner's session heartbeat and lease are re-read NOW, under
                # the locks, at the transactions' own apply time. Live or unknown refuses (skipped).
                if claim.owner_session_id and claim.owner_token_sha256:
                    applied_at = _apply_time(transactions)
                    if applied_at is None or not _owned_claim_sweepable(bridge, claim, applied_at):
                        raise Refused("the owner session is live or unknown at apply time")
                return Plan(after=None, archive=(archive_path, _stale_payload(claim, now, reason)),
                            event={"type": "stale_archive", "agent": claim.agent, "task_id": claim.task_id,
                                   "status": "stale_lease", "generation_before": sha256_or_none(before)})
            # F8 fence: the owner's beat lock is held from before the plan's re-read through the delete.
            beat = _session_heartbeat_path(bridge, claim)
            try:
                transactions.transact("stale_archive", claim_file,
                                      _key("stale_archive", claim.task_id, read_bytes_or_none(claim_file), now), plan,
                                      fences=() if beat is None else (beat,))
            except Refused:
                continue  # as core: a changed claim is skipped, not reported as archived
            except QueueTransactionError as exc:   # timeout, blocked, conflict, bound, unknown: one type (S6)
                # exc.recovered holds only effect-bearing outcomes (RCO1 Q-F2), so this never over-claims.
                completed = (" (recovery first finished earlier work on this claim: "
                             + ", ".join(str(entry.get("outcome")) for entry in exc.recovered) + ")"
                             if exc.recovered else "")
                raise WorkQueueError(claim.task_id[:128] + ": " + str(exc) + completed) from None
        archived.append(ArchivedClaim(claim=claim, archived_path=archive_path, age_seconds=age_seconds,
                                      release_reason=reason, applied=apply))
    return archived
