# SPDX-License-Identifier: BUSL-1.1
"""Multi-agent work-queue primitive for the agent bridge.

The bridge already ships PowerShell entry points
(`.agent-bridge/bin/Claim-AgentTask.ps1` and `Release-AgentTask.ps1`) that
manage per-task claim files under `.agent-bridge/work_queue/claims/`. This
module exposes the same primitive as a Python API that agents can call without
spawning PowerShell, plus extra helpers (heartbeat, list, stale detection)
needed for true multi-agent parallel operation.

The claim file schema is:

```json
{
  "agent": "claude-1",
  "task_id": "slice-foo",
  "summary": "...",
  "mode": "write" | "read-only",
  "write_scope": ["tools/foo.py"],
  "run_id": "",
  "claimed_at_utc": "2026-05-18T07:50:00Z",
  "last_heartbeat_utc": "2026-05-18T07:50:00Z",
  "lease_seconds": 300
}
```

The module is intentionally read/write with respect to the work-queue
directory only. It does not touch git, the bridge event stream, or any
external service. It is the substrate primitive that the higher-level
`tools/work_queue.py` CLI and future active-task generator (Slice 8c) compose
with.

Charter alignment: work-queue operations are operator-bounded autonomy
authorized via `IDLE_AUTONOMY_CHARTER.md` — allow agents to claim and release
substrate work in parallel, but never bypass the file allowlist or denylist
checked by `tools/idle_consensus_to_pr.py` (Slice 5b).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import warnings
from typing import Iterator, Sequence
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRIDGE_ROOT = ROOT / ".agent-bridge"
DEFAULT_CLAIMS_DIR = DEFAULT_BRIDGE_ROOT / "work_queue" / "claims"
DEFAULT_DONE_DIR = DEFAULT_BRIDGE_ROOT / "work_queue" / "done"
DEFAULT_LEASE_SECONDS = 300
DEFAULT_STALE_MAX_SECONDS = 300
BRIDGE_ROOT_ENV_NAMES = ("AGENT_BRIDGE_RUNTIME_ROOT", "AGENT_BRIDGE_ROOT")
WORK_QUEUE_MUTEX_NAME = r"Global\WaggleDanceBridgeWorkQueueV1"
WORK_QUEUE_MUTEX_TIMEOUT_MS = 10_000
_ASCII_LOWER = "abcdefghijklmnopqrstuvwxyz"
_ASCII_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ASCII_UPPER_TRANSLATION = str.maketrans(_ASCII_LOWER, _ASCII_UPPER)

AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,120}$")
ALLOWED_MODES = ("read-only", "write")


class WorkQueueError(ValueError):
    """Recoverable work-queue contract violation."""


@dataclass(frozen=True)
class Claim:
    """One active work-queue claim."""

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


@dataclass(frozen=True)
class ReleaseRecord:
    """Result of a released claim, persisted under done/."""

    agent: str
    task_id: str
    summary: str
    release_status: str
    release_message: str
    claimed_at_utc: str
    released_at_utc: str


@dataclass(frozen=True)
class ArchivedClaim:
    """Outcome of a stale-claim sweep entry (dry-run or applied)."""

    claim: Claim
    archived_path: Path
    age_seconds: int
    release_reason: str
    applied: bool


def resolve_bridge_root(bridge_root: Path | None = None) -> Path:
    """Resolve the runtime bridge root.

    Explicit callers win. Otherwise agent sessions may point at a shared
    persistent bridge through environment, while repo worktrees still carry a
    sidecar ``.agent-bridge`` for docs and scripts.
    """
    if bridge_root is not None:
        return Path(bridge_root)
    for env_name in BRIDGE_ROOT_ENV_NAMES:
        value = os.environ.get(env_name, "").strip()
        if value:
            return Path(value)
    return DEFAULT_BRIDGE_ROOT


@contextmanager
def _windows_work_queue_transaction(
    bridge_root: Path,
    timeout_ms: int = WORK_QUEUE_MUTEX_TIMEOUT_MS,
) -> Iterator[bool]:
    """Acquire the same crash-releasing Windows mutex as the PS writers."""
    import ctypes
    from ctypes import wintypes

    if os.environ.get(
        "AGENT_BRIDGE_TEST_WORK_QUEUE_MUTEX_CONSTRUCTION_FAILURE", ""
    ) == "1":
        raise WorkQueueError("simulated WorkQueueV1 mutex construction failure")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    wait_object_0 = 0x00000000
    wait_abandoned = 0x00000080
    wait_timeout = 0x00000102
    wait_failed = 0xFFFFFFFF

    mutex_name = work_queue_mutex_name(bridge_root)
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        error = ctypes.get_last_error()
        raise WorkQueueError(
            f"WorkQueueV1 mutex construction failed with WinError {error}"
        )

    acquired = False
    abandoned = False
    try:
        result = int(kernel32.WaitForSingleObject(handle, timeout_ms))
        if result == wait_object_0:
            acquired = True
        elif result == wait_abandoned:
            acquired = True
            abandoned = True
        elif result == wait_timeout:
            raise WorkQueueError(
                f"WorkQueueV1 mutex timeout after {timeout_ms} ms"
            )
        elif result == wait_failed:
            error = ctypes.get_last_error()
            raise WorkQueueError(
                f"WorkQueueV1 mutex wait failed with WinError {error}"
            )
        else:
            raise WorkQueueError(
                "WorkQueueV1 mutex wait returned unexpected status "
                f"0x{result:08x}"
            )
        yield abandoned
    finally:
        if acquired and not kernel32.ReleaseMutex(handle):
            warnings.warn(
                "WorkQueueV1 mutation committed but mutex release failed; "
                "the handle will be closed",
                RuntimeWarning,
                stacklevel=2,
            )
        if not kernel32.CloseHandle(handle):
            warnings.warn(
                "WorkQueueV1 mutex handle close failed",
                RuntimeWarning,
                stacklevel=2,
            )


@contextmanager
def _posix_work_queue_transaction(
    bridge_root: Path,
    timeout_ms: int = WORK_QUEUE_MUTEX_TIMEOUT_MS,
) -> Iterator[bool]:
    """Keep non-Windows Python callers serialized without an unlocked fallback."""
    import fcntl

    work_queue_dir = bridge_root / "work_queue"
    work_queue_dir.mkdir(parents=True, exist_ok=True)
    lock_path = work_queue_dir / ".transaction.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + (timeout_ms / 1000)
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise WorkQueueError(
                        f"WorkQueueV1 file-lock timeout after {timeout_ms} ms"
                    )
                time.sleep(0.01)
        yield False
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _work_queue_transaction(
    bridge_root: Path,
    timeout_ms: int = WORK_QUEUE_MUTEX_TIMEOUT_MS,
) -> Iterator[bool]:
    if timeout_ms <= 0:
        raise WorkQueueError("work-queue mutex timeout must be positive")
    if os.name == "nt":
        with _windows_work_queue_transaction(bridge_root, timeout_ms) as abandoned:
            yield abandoned
        return
    with _posix_work_queue_transaction(bridge_root, timeout_ms) as abandoned:
        yield abandoned


def _serialized_work_queue_mutation(operation):
    """Wrap one public mutator in the complete cross-process transaction."""

    @wraps(operation)
    def wrapped(*args, **kwargs):
        bridge = resolve_bridge_root(kwargs.get("bridge_root"))
        with _work_queue_transaction(bridge) as abandoned:
            if abandoned:
                _assert_claim_set_valid(bridge / "work_queue" / "claims")
            return operation(*args, **kwargs)

    return wrapped


def work_queue_mutex_name(bridge_root: Path) -> str:
    """Return the cross-runtime Windows mutex identity for one bridge root."""
    normalized = (
        os.path.abspath(os.fspath(bridge_root))
        .replace("/", "\\")
        .rstrip("\\")
        .translate(_ASCII_UPPER_TRANSLATION)
    )
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"{WORK_QUEUE_MUTEX_NAME}-{digest}"


def _resolve_claim_lease_seconds(lease_seconds: int | None) -> int:
    """Resolve an explicit lease or the bridge-wide environment default."""
    if lease_seconds is not None:
        try:
            resolved = int(lease_seconds)
        except (TypeError, ValueError) as exc:
            raise WorkQueueError("lease_seconds must be positive") from exc
        if resolved <= 0:
            raise WorkQueueError("lease_seconds must be positive")
        return resolved

    configured = os.environ.get("AGENT_BRIDGE_STALE_LEASE_SECONDS", "").strip()
    if configured:
        try:
            resolved = int(configured)
        except ValueError:
            resolved = 0
        if resolved > 0:
            return resolved
    return DEFAULT_LEASE_SECONDS


def resolve_stale_fallback_seconds(max_age_seconds: int | None) -> int:
    """Resolve the legacy-claim stale fallback shared with PowerShell."""
    if max_age_seconds is not None:
        try:
            resolved = int(max_age_seconds)
        except (TypeError, ValueError) as exc:
            raise WorkQueueError("max_age_seconds must be positive") from exc
        if resolved <= 0:
            raise WorkQueueError("max_age_seconds must be positive")
        return resolved

    configured = os.environ.get("AGENT_BRIDGE_STALE_LEASE_SECONDS", "").strip()
    if configured:
        try:
            resolved = int(configured)
        except ValueError:
            resolved = 0
        if resolved > 0:
            return resolved
    return DEFAULT_STALE_MAX_SECONDS


@_serialized_work_queue_mutation
def claim_task(
    *,
    agent: str,
    task_id: str,
    summary: str,
    mode: str = "read-only",
    write_scope: Sequence[str] = (),
    run_id: str = "",
    lease_seconds: int | None = None,
    bridge_root: Path | None = None,
    now_utc: datetime | None = None,
    force: bool = False,
) -> Claim:
    """Atomically claim a task for the given agent.

    Raises WorkQueueError if a claim already exists for this task_id (unless
    ``force=True`` and the existing claim belongs to the same agent — that
    case is treated as a refresh and the file is overwritten).
    """
    _validate_agent(agent)
    _validate_task_id(task_id)
    if not summary or not summary.strip():
        raise WorkQueueError("summary required")
    if mode not in ALLOWED_MODES:
        raise WorkQueueError(f"mode must be one of {ALLOWED_MODES}, got {mode!r}")
    normalized_write_scope = _normalize_write_scope_entries(write_scope)
    if mode == "write" and not normalized_write_scope:
        raise WorkQueueError("write claims require at least one write_scope path")
    resolved_lease_seconds = _resolve_claim_lease_seconds(lease_seconds)

    bridge = resolve_bridge_root(bridge_root)
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    _assert_claim_set_valid(claims_dir)
    claim_path = _claim_path_for_task(claims_dir, task_id)

    existing: Claim | None = None
    if claim_path.exists():
        existing = _read_claim_file(claim_path)
        if existing.agent != agent and not force:
            raise WorkQueueError(
                f"task {task_id} already claimed by {existing.agent}"
            )
        if existing.agent != agent and force:
            raise WorkQueueError(
                f"force claim across agents refused: existing={existing.agent}"
            )
    if mode == "write":
        conflicts = [
            claim
            for claim in check_scope_overlap(
                bridge_root=bridge,
                write_scope=normalized_write_scope,
            )
            if claim.task_id != task_id
        ]
        if conflicts:
            conflict = conflicts[0]
            raise WorkQueueError(
                "write-scope conflict with active claim "
                f"{conflict.task_id} by {conflict.agent}: "
                f"{', '.join(conflict.write_scope)}"
            )

    timestamp = _iso(now_utc or datetime.now(timezone.utc))
    lease_expires = _iso(
        _parse_utc(timestamp) + timedelta(seconds=resolved_lease_seconds)
    )
    claim = Claim(
        agent=agent,
        task_id=task_id,
        summary=summary.strip(),
        mode=mode,
        write_scope=normalized_write_scope,
        run_id=run_id,
        claimed_at_utc=timestamp,
        last_heartbeat_utc=timestamp,
        lease_seconds=resolved_lease_seconds,
        claim_lease_expires_utc=lease_expires,
    )
    _write_claim_file(claim_path, claim, create_new=existing is None)
    return claim


@_serialized_work_queue_mutation
def release_task(
    *,
    agent: str,
    task_id: str,
    release_status: str = "done",
    release_message: str = "",
    bridge_root: Path | None = None,
    now_utc: datetime | None = None,
) -> ReleaseRecord:
    """Release a previously claimed task and archive the record under done/."""
    _validate_agent(agent)
    _validate_task_id(task_id)
    if not release_status or not release_status.strip():
        raise WorkQueueError("release_status required")

    bridge = resolve_bridge_root(bridge_root)
    claims_dir = bridge / "work_queue" / "claims"
    done_dir = bridge / "work_queue" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    _assert_claim_set_valid(claims_dir)
    claim_path = _claim_path_for_task(claims_dir, task_id)
    if not claim_path.exists():
        raise WorkQueueError(f"no active claim for task {task_id}")

    existing = _read_claim_file(claim_path)
    if existing.agent != agent:
        raise WorkQueueError(
            f"release rejected: claim held by {existing.agent}, not {agent}"
        )

    released_at = _iso(now_utc or datetime.now(timezone.utc))
    record = ReleaseRecord(
        agent=agent,
        task_id=task_id,
        summary=existing.summary,
        release_status=release_status.strip(),
        release_message=release_message.strip(),
        claimed_at_utc=existing.claimed_at_utc,
        released_at_utc=released_at,
    )
    done_path = done_dir / f"{_safe_name(task_id)}-{_safe_name(released_at)}.json"
    payload = _claim_payload(existing)
    payload.update(
        {
            "released_at_utc": released_at,
            "release_status": record.release_status,
            "release_message": record.release_message,
        }
    )
    _move_no_replace(claim_path, done_path)
    try:
        _write_json_file(done_path, payload)
    except OSError as exc:
        warnings.warn(
            "claim reached done/ and release committed, but metadata update "
            f"failed: {done_path}: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
    return record


@_serialized_work_queue_mutation
def heartbeat(
    *,
    agent: str,
    task_id: str,
    bridge_root: Path | None = None,
    now_utc: datetime | None = None,
    lease_seconds: int | None = None,
) -> Claim:
    """Refresh the lease on an existing claim."""
    _validate_agent(agent)
    _validate_task_id(task_id)
    bridge = resolve_bridge_root(bridge_root)
    claims_dir = bridge / "work_queue" / "claims"
    _assert_claim_set_valid(claims_dir)
    claim_path = _claim_path_for_task(claims_dir, task_id)
    if not claim_path.exists():
        raise WorkQueueError(f"no active claim for task {task_id}")

    existing = _read_claim_file(claim_path)
    if existing.agent != agent:
        raise WorkQueueError(
            f"heartbeat rejected: claim held by {existing.agent}, not {agent}"
        )

    timestamp = _iso(now_utc or datetime.now(timezone.utc))
    refreshed_lease_seconds = (
        _resolve_claim_lease_seconds(lease_seconds)
        if lease_seconds is not None
        else existing.lease_seconds or _resolve_claim_lease_seconds(None)
    )
    lease_expires = _iso(
        _parse_utc(timestamp) + timedelta(seconds=refreshed_lease_seconds)
    )
    refreshed = Claim(
        agent=existing.agent,
        task_id=existing.task_id,
        summary=existing.summary,
        mode=existing.mode,
        write_scope=existing.write_scope,
        run_id=existing.run_id,
        claimed_at_utc=existing.claimed_at_utc,
        last_heartbeat_utc=timestamp,
        lease_seconds=refreshed_lease_seconds,
        claim_lease_expires_utc=lease_expires,
        role=existing.role,
        agent_uuid=existing.agent_uuid,
        capabilities=existing.capabilities,
    )
    _write_claim_file(claim_path, refreshed)
    return refreshed


def list_claims(bridge_root: Path | None = None) -> list[Claim]:
    """Return all active claims in the work-queue."""
    bridge = resolve_bridge_root(bridge_root)
    claims_dir = bridge / "work_queue" / "claims"
    if not claims_dir.exists():
        return []
    claims: list[Claim] = []
    for path in sorted(claims_dir.glob("*.json")):
        try:
            claims.append(_read_claim_file(path))
        except WorkQueueError:
            continue
    return claims


PRIVILEGED_AGENTS = frozenset({"operator", "system"})


def _effective_claim_expiry(
    claim: Claim,
    fallback_seconds: int,
) -> tuple[datetime, datetime, int] | None:
    """Return the shared Python/PowerShell stale-lease interpretation."""
    candidates: list[str] = []
    if claim.last_heartbeat_utc:
        candidates.append(claim.last_heartbeat_utc)
    if claim.claimed_at_utc and claim.claimed_at_utc not in candidates:
        candidates.append(claim.claimed_at_utc)

    last: datetime | None = None
    for candidate in candidates:
        try:
            last = _parse_utc(candidate)
            break
        except (ValueError, TypeError):
            continue
    if last is None:
        return None

    claim_lease_seconds = (
        claim.lease_seconds if claim.lease_seconds > 0 else fallback_seconds
    )
    effective_expiry = last + timedelta(seconds=claim_lease_seconds)
    if claim.claim_lease_expires_utc:
        try:
            explicit_expiry = _parse_utc(claim.claim_lease_expires_utc)
        except (ValueError, TypeError):
            explicit_expiry = None
        if explicit_expiry is not None and explicit_expiry > effective_expiry:
            effective_expiry = explicit_expiry
    effective_lease_seconds = max(
        1,
        math.ceil((effective_expiry - last).total_seconds()),
    )
    return last, effective_expiry, effective_lease_seconds


def detect_stale_claims(
    *,
    bridge_root: Path | None = None,
    now_utc: datetime | None = None,
    max_age_seconds: int | None = None,
) -> list[Claim]:
    """Return claims at or beyond their effective lease expiry."""
    fallback_seconds = resolve_stale_fallback_seconds(max_age_seconds)
    now = now_utc or datetime.now(timezone.utc)
    stale: list[Claim] = []
    for claim in list_claims(bridge_root=bridge_root):
        if claim.agent in PRIVILEGED_AGENTS:
            continue
        expiry = _effective_claim_expiry(claim, fallback_seconds)
        if expiry is None:
            continue
        _, effective_expiry, _ = expiry
        if now >= effective_expiry:
            stale.append(claim)
    return stale


def archive_stale_claims(
    *,
    bridge_root: Path | None = None,
    now_utc: datetime | None = None,
    max_age_seconds: int | None = None,
    apply: bool = False,
) -> list[ArchivedClaim]:
    """Sweep stale claims; dry-run unless apply=True.

    Parity with `.agent-bridge/bin/Invoke-StaleClaimSweep.ps1`:

    * Effective expiry starts at `last_heartbeat_utc` (falling back to
      `claimed_at_utc`) plus a positive per-claim `lease_seconds`, or
      ``max_age_seconds`` for a legacy claim. A later parseable
      `claim_lease_expires_utc` extends that expiry.
    * Claims owned by ``operator`` or ``system`` are never swept.
    * Each swept claim is archived to
      ``work_queue/done/<safe_task>.<utc_stamp>.stale_lease.json`` with
      ``release_status="stale_lease"``, ``release_reason`` describing the
      lease age, and ``released_at_utc`` set to ``now_utc``.
    * With ``apply=False`` (the default) no files are moved or written;
      the returned ``ArchivedClaim`` records describe the planned action.

    The primitive intentionally does not emit bridge events; the CLI
    wrapper in ``tools/work_queue_sweep_stale.py`` is responsible for
    observability.
    """
    fallback_seconds = resolve_stale_fallback_seconds(max_age_seconds)
    bridge = resolve_bridge_root(bridge_root)
    if not apply:
        return _archive_stale_claims_impl(
            bridge_root=bridge,
            now_utc=now_utc,
            max_age_seconds=fallback_seconds,
            apply=False,
        )

    with _work_queue_transaction(bridge) as abandoned:
        if abandoned:
            _assert_claim_set_valid(bridge / "work_queue" / "claims")
        return _archive_stale_claims_impl(
            bridge_root=bridge,
            now_utc=now_utc,
            max_age_seconds=fallback_seconds,
            apply=True,
        )


def _archive_stale_claims_impl(
    *,
    bridge_root: Path,
    now_utc: datetime | None,
    max_age_seconds: int,
    apply: bool,
) -> list[ArchivedClaim]:
    """Implement one stale-claim snapshot or an already-locked applied sweep."""
    if max_age_seconds <= 0:
        raise WorkQueueError(
            f"max_age_seconds must be positive, got {max_age_seconds}"
        )
    bridge = bridge_root
    now = now_utc or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    claims_dir = bridge / "work_queue" / "claims"
    _assert_claim_set_valid(claims_dir)
    done_dir = bridge / "work_queue" / "done"
    archived: list[ArchivedClaim] = []
    for claim in list_claims(bridge_root=bridge):
        if claim.agent in PRIVILEGED_AGENTS:
            continue
        expiry = _effective_claim_expiry(claim, max_age_seconds)
        if expiry is None:
            continue
        last, effective_expiry, effective_lease_seconds = expiry
        if now < effective_expiry:
            continue
        age_seconds = int((now - last).total_seconds())

        safe_task = _safe_name(claim.task_id)
        if not safe_task:
            continue
        archive_path = done_dir / f"{safe_task}.{stamp}.stale_lease.json"
        reason = (
            f"last_heartbeat_utc was {age_seconds}s old; "
            f"lease threshold {effective_lease_seconds}s"
        )
        if apply:
            done_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "agent": claim.agent,
                "task_id": claim.task_id,
                "summary": claim.summary,
                "mode": claim.mode,
                "write_scope": list(claim.write_scope),
                "run_id": claim.run_id,
                "claimed_at_utc": claim.claimed_at_utc,
                "last_heartbeat_utc": claim.last_heartbeat_utc,
                "lease_seconds": claim.lease_seconds,
                "claim_lease_expires_utc": claim.claim_lease_expires_utc,
                "released_at_utc": _iso(now),
                "release_status": "stale_lease",
                "release_reason": reason,
            }
            if claim.role:
                payload["role"] = claim.role
            if claim.agent_uuid:
                payload["agent_uuid"] = claim.agent_uuid
            if claim.capabilities:
                payload["capabilities"] = list(claim.capabilities)
            claim_file = _claim_path_for_task(
                claims_dir,
                claim.task_id,
            )
            _move_no_replace(claim_file, archive_path)
            try:
                _write_json_file(archive_path, payload)
            except OSError as exc:
                warnings.warn(
                    "claim reached done/ and stale release committed, but "
                    f"metadata update failed: {archive_path}: {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )
        archived.append(
            ArchivedClaim(
                claim=claim,
                archived_path=archive_path,
                age_seconds=age_seconds,
                release_reason=reason,
                applied=apply,
            )
        )
    return archived


def check_scope_overlap(
    bridge_root: Path | None = None,
    write_scope: Sequence[str] = (),
) -> list[Claim]:
    """Return active write-mode claims that overlap with the given write_scope."""
    normalized_scope = _normalize_write_scope_entries(write_scope)
    if not normalized_scope:
        return []
    normalized_request = {_normalize_scope_entry(s) for s in normalized_scope if s}
    if not normalized_request:
        return []
    overlapping: list[Claim] = []
    for claim in list_claims(bridge_root=bridge_root):
        if claim.mode != "write":
            continue
        existing_scope = {
            _normalize_scope_entry(s)
            for s in _normalize_write_scope_entries(claim.write_scope)
            if s
        }
        if any(
            _scope_entries_overlap(existing, requested)
            for existing in existing_scope
            for requested in normalized_request
        ):
            overlapping.append(claim)
    return overlapping


def _validate_agent(agent: str) -> None:
    if not agent or not AGENT_ID_PATTERN.fullmatch(agent):
        raise WorkQueueError(f"agent must match {AGENT_ID_PATTERN.pattern}, got {agent!r}")


def _validate_task_id(task_id: str) -> None:
    if not task_id or not TASK_ID_PATTERN.fullmatch(task_id):
        raise WorkQueueError(f"task_id invalid: {task_id!r}")
    segments = task_id.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise WorkQueueError(f"task_id invalid: {task_id!r}")


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", value).strip("_") or "claim"
    if safe == value:
        return safe
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{safe}-{digest}"


def _claim_path_for_task(claims_dir: Path, task_id: str) -> Path:
    preferred = claims_dir / f"{_safe_name(task_id)}.json"
    if not claims_dir.exists():
        return preferred
    matches: list[Path] = []
    for path in sorted(claims_dir.glob("*.json")):
        try:
            claim = _read_claim_file(path)
        except WorkQueueError:
            continue
        if claim.task_id == task_id:
            matches.append(path)
    if len(matches) > 1:
        rendered = ", ".join(str(path) for path in matches)
        raise WorkQueueError(
            f"multiple active claims for exact task {task_id}: {rendered}"
        )
    if matches:
        return matches[0]
    if preferred.exists():
        raise WorkQueueError(
            f"claim path collision for task {task_id}: {preferred}"
        )
    return preferred


def _normalize_scope_entry(scope: str) -> str:
    return scope.replace("\\", "/").strip("/").lower()


def _normalize_write_scope_entries(values: Sequence[object]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    source = (values,) if isinstance(values, str) else values
    for value in source:
        for item in str(value).split(","):
            scope = item.strip()
            if not scope or scope in seen:
                continue
            seen.add(scope)
            normalized.append(scope)
    return tuple(normalized)


def _scope_entries_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == "*" or right == "*":
        return True
    if left == right:
        return True
    return left.startswith(right + "/") or right.startswith(left + "/")


def _read_claim_file(path: Path) -> Claim:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkQueueError(f"unreadable claim file: {path}") from exc
    if not isinstance(data, dict):
        raise WorkQueueError(f"claim file must be JSON object: {path}")
    if "release_status" in data or "released_at_utc" in data:
        raise WorkQueueError(f"active claim file contains terminal markers: {path}")
    agent = str(data.get("agent", ""))
    task_id = str(data.get("task_id", ""))
    mode = str(data.get("mode", ""))
    write_scope = _normalize_write_scope_entries(data.get("write_scope", []))
    if not agent.strip():
        raise WorkQueueError(f"claim file has no agent: {path}")
    if not task_id.strip():
        raise WorkQueueError(f"claim file has no task_id: {path}")
    if mode not in ALLOWED_MODES:
        raise WorkQueueError(f"claim file has invalid mode: {path}")
    if mode == "write" and not write_scope:
        raise WorkQueueError(f"write claim file has no write_scope: {path}")
    try:
        raw_lease_seconds = data["lease_seconds"] if "lease_seconds" in data else 0
        if isinstance(raw_lease_seconds, bool) or not isinstance(
            raw_lease_seconds,
            (int, str),
        ):
            raise TypeError
        lease_seconds = int(raw_lease_seconds)
    except (TypeError, ValueError) as exc:
        raise WorkQueueError(f"claim file has invalid lease_seconds: {path}") from exc
    if lease_seconds < 0:
        raise WorkQueueError(f"claim file has invalid lease_seconds: {path}")
    raw_capabilities = data.get("capabilities", [])
    if isinstance(raw_capabilities, str):
        capability_values = [
            item.strip() for item in raw_capabilities.split(",") if item.strip()
        ]
    elif isinstance(raw_capabilities, (list, tuple)):
        capability_values = [str(item) for item in raw_capabilities if item]
    else:
        raise WorkQueueError(f"claim file has invalid capabilities: {path}")
    return Claim(
        agent=agent,
        task_id=task_id,
        summary=str(data.get("summary", "")),
        mode=mode,
        write_scope=write_scope,
        run_id=str(data.get("run_id", "")),
        claimed_at_utc=str(data.get("claimed_at_utc", "")),
        last_heartbeat_utc=str(data.get("last_heartbeat_utc", "")),
        lease_seconds=lease_seconds,
        claim_lease_expires_utc=str(data.get("claim_lease_expires_utc", "")),
        role=str(data.get("role", "")),
        agent_uuid=str(data.get("agent_uuid", "")),
        capabilities=tuple(capability_values),
    )


def _assert_claim_set_valid(claims_dir: Path) -> None:
    """Fail mutations closed when any canonical claim cannot authorize safely."""
    if not claims_dir.exists():
        return
    for path in sorted(claims_dir.glob("*.json")):
        _read_claim_file(path)


def _claim_payload(claim: Claim) -> dict[str, object]:
    payload: dict[str, object] = {
        "agent": claim.agent,
        "task_id": claim.task_id,
        "summary": claim.summary,
        "mode": claim.mode,
        "write_scope": list(claim.write_scope),
        "run_id": claim.run_id,
        "claimed_at_utc": claim.claimed_at_utc,
        "last_heartbeat_utc": claim.last_heartbeat_utc,
        "lease_seconds": claim.lease_seconds,
        "claim_lease_expires_utc": claim.claim_lease_expires_utc,
    }
    if claim.role:
        payload["role"] = claim.role
    if claim.agent_uuid:
        payload["agent_uuid"] = claim.agent_uuid
    if claim.capabilities:
        payload["capabilities"] = list(claim.capabilities)
    return payload


def _write_claim_file(path: Path, claim: Claim, *, create_new: bool = False) -> None:
    _write_json_file(path, _claim_payload(claim), create_new=create_new)


def _move_no_replace(source: Path, target: Path) -> None:
    """Publish or transition one complete file without clobbering a target."""
    try:
        if os.name == "nt":
            source.rename(target)
        else:
            os.link(source, target)
            try:
                source.unlink()
            except OSError:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise
    except FileExistsError as exc:
        raise WorkQueueError(f"work-queue target already exists: {target}") from exc


def _write_json_file(
    path: Path,
    payload: dict[str, object],
    *,
    create_new: bool = False,
) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(f"{path.name}.tmp.{uuid4().hex}")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        if create_new:
            try:
                _move_no_replace(tmp, path)
            except WorkQueueError as exc:
                raise WorkQueueError(
                    f"could not create claim, likely already exists: {path}"
                ) from exc
        else:
            tmp.replace(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
