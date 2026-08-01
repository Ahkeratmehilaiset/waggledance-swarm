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
  "lease_seconds": 900,
  "session_id": "claude-1-20260518T075000Z",
  "owner_session_id": "claude-1-20260518T075000Z",
  "owner_token_sha256": "<sha256 of the raw session owner token>",
  "owner_pid": 1234,
  "owner_process_start_utc": "2026-05-18T07:49:00Z"
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
import ctypes
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import warnings
from typing import BinaryIO, Iterator, Mapping, Sequence
from uuid import uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRIDGE_ROOT = ROOT / ".agent-bridge"
DEFAULT_CLAIMS_DIR = DEFAULT_BRIDGE_ROOT / "work_queue" / "claims"
DEFAULT_DONE_DIR = DEFAULT_BRIDGE_ROOT / "work_queue" / "done"
DEFAULT_LEASE_SECONDS = 900
DEFAULT_STALE_MAX_SECONDS = 300
BRIDGE_ROOT_ENV_NAMES = ("AGENT_BRIDGE_RUNTIME_ROOT", "AGENT_BRIDGE_ROOT")
SESSION_AGENT_ENV = "AGENT_BRIDGE_AGENT"
STALE_LEASE_ENV_NAME = "AGENT_BRIDGE_STALE_LEASE_SECONDS"
MAX_OWNER_PID = (1 << 31) - 1
MAX_LEASE_SECONDS = (1 << 31) - 1
MUTATION_LOCK_TIMEOUT_SECONDS = 30.0
MUTATION_LOCK_RETRY_SECONDS = 0.025

AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,120}$")
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
AGENT_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{1,64}$")
OWNER_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CANONICAL_UTC_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})$"
)
ALLOWED_MODES = ("read-only", "write")


def _stat_is_reparse_point(value: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(value.st_mode) or bool(
        getattr(value, "st_file_attributes", 0) & reparse_flag
    )


def _require_plain_directory(path: Path, *, label: str) -> None:
    try:
        path_stat = os.lstat(path)
    except OSError as exc:
        raise WorkQueueError(f"{label} is missing or unreadable: {path}") from exc
    if not stat.S_ISDIR(path_stat.st_mode):
        raise WorkQueueError(f"{label} must be a directory: {path}")
    if _stat_is_reparse_point(path_stat):
        raise WorkQueueError(f"{label} must not be a reparse link: {path}")


def _ensure_plain_directory(path: Path, *, label: str) -> None:
    if os.path.lexists(path):
        _require_plain_directory(path, label=label)
        return
    try:
        path.mkdir(parents=True)
    except FileExistsError:
        pass
    except OSError as exc:
        raise WorkQueueError(f"could not create {label}: {path}") from exc
    _require_plain_directory(path, label=label)


def _require_mutation_lock_identity(
    path: Path,
    *,
    handle: BinaryIO | None = None,
) -> None:
    """Require one plain, single-link lock file bound to its opened handle."""

    try:
        path_stat = os.lstat(path)
    except OSError as exc:
        raise WorkQueueError(
            f"claim mutation lock is missing or unreadable: {path}"
        ) from exc
    if not stat.S_ISREG(path_stat.st_mode):
        raise WorkQueueError(f"claim mutation lock must be a regular file: {path}")
    if _stat_is_reparse_point(path_stat):
        raise WorkQueueError(
            f"claim mutation lock must not be a reparse link: {path}"
        )
    if path_stat.st_nlink != 1:
        raise WorkQueueError(
            "claim mutation lock must have exactly one filesystem link: "
            f"{path}"
        )
    if handle is None:
        return

    try:
        opened_stat = os.fstat(handle.fileno())
    except OSError as exc:
        raise WorkQueueError(
            f"claim mutation lock opened handle is unreadable: {path}"
        ) from exc
    if not stat.S_ISREG(opened_stat.st_mode) or _stat_is_reparse_point(
        opened_stat
    ):
        raise WorkQueueError(
            f"claim mutation lock opened handle must be a regular file: {path}"
        )
    if opened_stat.st_nlink != 1:
        raise WorkQueueError(
            "claim mutation lock opened handle must have exactly one "
            f"filesystem link: {path}"
        )
    if (opened_stat.st_dev, opened_stat.st_ino) != (
        path_stat.st_dev,
        path_stat.st_ino,
    ):
        raise WorkQueueError(
            f"claim mutation lock path changed after opening: {path}"
        )


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
    session_id: str = ""
    owner_session_id: str = ""
    owner_token_sha256: str = ""
    owner_pid: int = 0
    owner_process_start_utc: str = ""
    role: str = ""
    agent_uuid: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    writer_pid: int = 0
    writer_pid_semantics: str = ""
    cwd: str = ""
    git_branch: str = ""


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
    mode: str = "read-only"
    write_scope: tuple[str, ...] = field(default_factory=tuple)
    run_id: str = ""
    last_heartbeat_utc: str = ""
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    claim_lease_expires_utc: str = ""
    session_id: str = ""
    owner_session_id: str = ""
    owner_token_sha256: str = ""
    owner_pid: int = 0
    owner_process_start_utc: str = ""
    role: str = ""
    agent_uuid: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    writer_pid: int = 0
    writer_pid_semantics: str = ""
    cwd: str = ""
    git_branch: str = ""


@dataclass(frozen=True)
class ArchivedClaim:
    """Outcome of a stale-claim sweep entry (dry-run or applied)."""

    claim: Claim
    archived_path: Path
    age_seconds: int
    release_reason: str
    applied: bool


@dataclass(frozen=True)
class _ClaimOwnerContext:
    session_id: str
    owner_session_id: str
    owner_token_sha256: str
    owner_pid: int
    owner_process_start_utc: str
    role: str
    agent_uuid: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class _ActiveClaimSnapshot:
    """One parsed claim bound to the exact raw bytes that authorized it."""

    path: Path
    claim: Claim
    sha256: str
    size: int


@dataclass(frozen=True)
class _ClaimExpiry:
    anchor_utc: datetime | None
    anchor_field: str
    expires_utc: datetime | None
    effective_lease_seconds: int
    legacy_tokenless: bool


@dataclass
class _StaleApplyPlan:
    record: ArchivedClaim
    source_path: Path
    archive_temp_path: Path
    source_backup_path: Path
    source_quarantine_path: Path
    archive_body: bytes | None = None
    expected_archive_sha256: str | None = None
    expected_archive_size: int | None = None
    expected_source_sha256: str | None = None
    expected_source_size: int | None = None
    archive_published: bool = False
    source_removed: bool = False
    source_quarantine_body: bytes | None = None
    source_quarantine_sha256: str | None = None
    source_quarantine_size: int | None = None
    source_quarantine_verified: bool = False
    rollback_source_verified: bool = False


@contextmanager
def _claim_mutation_lock(bridge: Path) -> Iterator[None]:
    """Serialize claim mutations across Python and PowerShell runtimes."""

    _ensure_plain_directory(bridge, label="bridge root")
    work_queue_dir = bridge / "work_queue"
    _ensure_plain_directory(work_queue_dir, label="work queue directory")
    for state_dir, label in (
        (work_queue_dir / "claims", "active claims directory"),
        (work_queue_dir / "done", "completed claims directory"),
    ):
        if os.path.lexists(state_dir):
            _require_plain_directory(state_dir, label=label)
    lock_path = work_queue_dir / ".claims.mutation.lock"
    if os.path.lexists(lock_path):
        _require_mutation_lock_identity(lock_path)
    try:
        handle: BinaryIO = lock_path.open("a+b")
    except OSError as exc:
        raise WorkQueueError(
            f"could not open claim mutation lock: {lock_path}"
        ) from exc
    acquired = False
    deadline = time.monotonic() + MUTATION_LOCK_TIMEOUT_SECONDS
    try:
        _require_mutation_lock_identity(lock_path, handle=handle)
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.lockf(
                        handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                        1,
                        0,
                        os.SEEK_SET,
                    )
                acquired = True
                break
            except OSError as exc:
                if os.name == "nt":
                    contended = (
                        getattr(exc, "winerror", None) in {33, 36}
                        or exc.errno in {errno.EACCES, errno.EAGAIN}
                    )
                else:
                    contended = exc.errno in {errno.EACCES, errno.EAGAIN}
                if not contended:
                    raise WorkQueueError(
                        f"could not acquire claim mutation lock: {lock_path}"
                    ) from exc
                if time.monotonic() >= deadline:
                    raise WorkQueueError(
                        f"timed out acquiring claim mutation lock: {lock_path}"
                    ) from exc
                time.sleep(MUTATION_LOCK_RETRY_SECONDS)
        _require_mutation_lock_identity(lock_path, handle=handle)
        _require_plain_directory(bridge, label="bridge root")
        _require_plain_directory(work_queue_dir, label="work queue directory")
        for state_dir, label in (
            (work_queue_dir / "claims", "active claims directory"),
            (work_queue_dir / "done", "completed claims directory"),
        ):
            if os.path.lexists(state_dir):
                _require_plain_directory(state_dir, label=label)
        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.lockf(
                        handle.fileno(),
                        fcntl.LOCK_UN,
                        1,
                        0,
                        os.SEEK_SET,
                    )
            finally:
                handle.close()
        else:
            handle.close()


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


def _assert_bound_session_agent(agent: str) -> None:
    """Mirror the public bridge identity boundary for direct mutating calls."""

    session_agent = os.environ.get(SESSION_AGENT_ENV, "")
    if not session_agent.strip():
        if agent in {"operator", "system"}:
            raise WorkQueueError(
                "identity_mismatch: "
                f"reserved agent {agent!r} requires a verified bound caller"
            )
        return
    if not AGENT_ID_PATTERN.fullmatch(session_agent):
        raise WorkQueueError(
            "identity_mismatch: "
            f"{SESSION_AGENT_ENV} {session_agent!r} is malformed"
        )
    if agent == "system":
        raise WorkQueueError(
            "identity_mismatch: system agent has no public bridge authority"
        )
    if session_agent != agent:
        raise WorkQueueError(
            "identity_mismatch: "
            f"requested agent {agent!r} does not match "
            f"{SESSION_AGENT_ENV} {session_agent!r}"
        )


def _claim_owner_context(
    *,
    include_claim_metadata: bool = False,
) -> _ClaimOwnerContext:
    owner_session_id = os.environ.get("AGENT_BRIDGE_OWNER_SESSION_ID", "")
    if not SESSION_ID_PATTERN.fullmatch(owner_session_id):
        raise WorkQueueError(
            "claim_owner_context_invalid: "
            "AGENT_BRIDGE_OWNER_SESSION_ID is missing or malformed"
        )

    owner_token = os.environ.get("AGENT_BRIDGE_OWNER_TOKEN", "")
    if not OWNER_TOKEN_PATTERN.fullmatch(owner_token):
        raise WorkQueueError(
            "claim_owner_context_invalid: "
            "AGENT_BRIDGE_OWNER_TOKEN is missing or malformed"
        )

    owner_pid_text = os.environ.get("AGENT_BRIDGE_OWNER_PID", "")
    if re.fullmatch(r"[0-9]+", owner_pid_text) is None:
        raise WorkQueueError(
            "claim_owner_context_invalid: "
            "AGENT_BRIDGE_OWNER_PID is missing or malformed"
        )
    try:
        owner_pid = int(owner_pid_text)
    except (TypeError, ValueError) as exc:
        raise WorkQueueError(
            "claim_owner_context_invalid: "
            "AGENT_BRIDGE_OWNER_PID is missing or malformed"
        ) from exc
    if owner_pid <= 0 or owner_pid > MAX_OWNER_PID:
        raise WorkQueueError(
            "claim_owner_context_invalid: "
            "AGENT_BRIDGE_OWNER_PID is missing or malformed"
        )

    owner_process_start = os.environ.get(
        "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
        "",
    )
    try:
        owner_process_start_utc = _iso(_parse_utc(owner_process_start))
    except (AttributeError, TypeError, ValueError) as exc:
        raise WorkQueueError(
            "claim_owner_context_invalid: "
            "AGENT_BRIDGE_OWNER_PROCESS_START_UTC is missing or malformed"
        ) from exc

    session_id = ""
    role = ""
    agent_uuid = ""
    capabilities: list[str] = []
    if include_claim_metadata:
        ambient_session_id = os.environ.get("AGENT_BRIDGE_SESSION_ID", "")
        if ambient_session_id and not SESSION_ID_PATTERN.fullmatch(
            ambient_session_id
        ):
            raise WorkQueueError(
                "claim_owner_context_invalid: "
                "AGENT_BRIDGE_SESSION_ID is malformed"
            )
        session_id = ambient_session_id or owner_session_id

        role = os.environ.get("AGENT_BRIDGE_ROLE", "")
        if role and not ROLE_PATTERN.fullmatch(role):
            raise WorkQueueError(
                "claim_owner_context_invalid: AGENT_BRIDGE_ROLE is malformed"
            )

        agent_uuid = os.environ.get("AGENT_BRIDGE_AGENT_UUID", "")
        if agent_uuid and not AGENT_UUID_PATTERN.fullmatch(agent_uuid):
            raise WorkQueueError(
                "claim_owner_context_invalid: "
                "AGENT_BRIDGE_AGENT_UUID is malformed"
            )
        agent_uuid = agent_uuid.lower()

        seen_capabilities: set[str] = set()
        for capability in re.split(
            r"[,;]",
            os.environ.get("AGENT_BRIDGE_CAPABILITIES", ""),
        ):
            normalized = capability.strip()
            if not normalized:
                continue
            if not CAPABILITY_PATTERN.fullmatch(normalized):
                raise WorkQueueError(
                    "claim_owner_context_invalid: "
                    "AGENT_BRIDGE_CAPABILITIES contains a malformed capability"
                )
            if normalized not in seen_capabilities:
                seen_capabilities.add(normalized)
                capabilities.append(normalized)

    owner_token_sha256 = hashlib.sha256(owner_token.encode("utf-8")).hexdigest()
    return _ClaimOwnerContext(
        session_id=session_id,
        owner_session_id=owner_session_id,
        owner_token_sha256=owner_token_sha256,
        owner_pid=owner_pid,
        owner_process_start_utc=owner_process_start_utc,
        role=role,
        agent_uuid=agent_uuid,
        capabilities=tuple(capabilities),
    )


def _claim_is_legacy_tokenless(claim: Claim) -> bool:
    if not isinstance(claim.owner_session_id, str) or not (
        SESSION_ID_PATTERN.fullmatch(claim.owner_session_id)
    ):
        return True
    if not isinstance(claim.owner_token_sha256, str) or not (
        OWNER_TOKEN_PATTERN.fullmatch(claim.owner_token_sha256)
    ):
        return True
    if (
        not isinstance(claim.owner_pid, int)
        or isinstance(claim.owner_pid, bool)
        or claim.owner_pid <= 0
        or claim.owner_pid > MAX_OWNER_PID
    ):
        return True
    try:
        _parse_utc(claim.owner_process_start_utc)
    except (AttributeError, TypeError, ValueError):
        return True
    return False


def _claim_expiry(
    claim: Claim,
    *,
    fallback_lease_seconds: int,
) -> _ClaimExpiry:
    legacy_tokenless = _claim_is_legacy_tokenless(claim)
    lease_seconds = (
        claim.lease_seconds
        if claim.lease_seconds > 0 and not legacy_tokenless
        else fallback_lease_seconds
    )
    anchor_utc: datetime | None = None
    anchor_field = "claimed_at_utc" if legacy_tokenless else "last_heartbeat_utc"
    anchor_candidates = (
        (("claimed_at_utc", claim.claimed_at_utc),)
        if legacy_tokenless
        else (
            ("last_heartbeat_utc", claim.last_heartbeat_utc),
            ("claimed_at_utc", claim.claimed_at_utc),
        )
    )
    for candidate_field, candidate in anchor_candidates:
        try:
            anchor_utc = _parse_utc(candidate)
            anchor_field = candidate_field
            break
        except (AttributeError, TypeError, ValueError):
            continue

    if anchor_utc is None:
        return _ClaimExpiry(
            anchor_utc=None,
            anchor_field=anchor_field,
            expires_utc=None,
            effective_lease_seconds=lease_seconds,
            legacy_tokenless=legacy_tokenless,
        )

    expires_utc = _add_seconds_clamped(anchor_utc, lease_seconds)
    if not legacy_tokenless:
        try:
            explicit_expires_utc = _parse_utc(claim.claim_lease_expires_utc)
        except (AttributeError, TypeError, ValueError):
            explicit_expires_utc = None
        if (
            explicit_expires_utc is not None
            and explicit_expires_utc > expires_utc
        ):
            expires_utc = explicit_expires_utc

    effective_lease_seconds = max(
        lease_seconds,
        int(math.ceil((expires_utc - anchor_utc).total_seconds())),
    )
    return _ClaimExpiry(
        anchor_utc=anchor_utc,
        anchor_field=anchor_field,
        expires_utc=expires_utc,
        effective_lease_seconds=effective_lease_seconds,
        legacy_tokenless=legacy_tokenless,
    )


def _assert_claim_owner(
    claim: Claim,
    owner_context: _ClaimOwnerContext,
    *,
    operation: str,
) -> None:
    if _claim_is_legacy_tokenless(claim):
        raise WorkQueueError(
            "claim_owner_legacy_tokenless: "
            f"current session cannot {operation} a legacy tokenless claim"
        )
    if (
        claim.owner_session_id != owner_context.owner_session_id
        or claim.owner_token_sha256 != owner_context.owner_token_sha256
    ):
        raise WorkQueueError(
            "claim_owner_wrong_generation: "
            f"current session cannot {operation} a claim owned by another generation"
        )


def claim_task(
    *,
    agent: str,
    task_id: str,
    summary: str,
    mode: str = "read-only",
    write_scope: Sequence[str] = (),
    run_id: str = "",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
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
    _assert_bound_session_agent(agent)
    _validate_task_id(task_id)
    if not summary or not summary.strip():
        raise WorkQueueError("summary required")
    if mode not in ALLOWED_MODES:
        raise WorkQueueError(f"mode must be one of {ALLOWED_MODES}, got {mode!r}")
    normalized_write_scope = tuple(
        scope
        for scope in _normalize_write_scope_entries(
            write_scope,
            reject_non_strings=True,
        )
        if _normalize_scope_entry(scope)
    )
    if mode == "write" and not normalized_write_scope:
        raise WorkQueueError("write claims require at least one write_scope path")
    lease_seconds = _require_positive_int32(
        lease_seconds,
        field_name="lease_seconds",
    )

    owner_context = _claim_owner_context(include_claim_metadata=True)
    bridge = resolve_bridge_root(bridge_root)
    with _claim_mutation_lock(bridge):
        return _claim_task_locked(
            agent=agent,
            task_id=task_id,
            summary=summary,
            mode=mode,
            normalized_write_scope=normalized_write_scope,
            run_id=run_id,
            lease_seconds=lease_seconds,
            bridge=bridge,
            now_utc=now_utc,
            force=force,
            owner_context=owner_context,
        )


def _claim_task_locked(
    *,
    agent: str,
    task_id: str,
    summary: str,
    mode: str,
    normalized_write_scope: tuple[str, ...],
    run_id: str,
    lease_seconds: int,
    bridge: Path,
    now_utc: datetime | None,
    force: bool,
    owner_context: _ClaimOwnerContext,
) -> Claim:
    claims_dir = bridge / "work_queue" / "claims"
    _ensure_plain_directory(claims_dir, label="active claims directory")
    active_claims = _strict_active_claim_snapshot(claims_dir)
    claim_path, existing_snapshot = _claim_for_task_from_snapshot(
        claims_dir,
        task_id,
        active_claims,
    )
    existing = (
        existing_snapshot.claim if existing_snapshot is not None else None
    )

    if existing is not None:
        if not force:
            raise WorkQueueError(
                f"task {task_id} already claimed by {existing.agent}"
            )
        if existing.agent != agent:
            raise WorkQueueError(
                f"force claim across agents refused: existing={existing.agent}"
            )
        _assert_claim_owner(
            existing,
            owner_context,
            operation="force-update",
        )
    if mode == "write":
        conflicts = [
            claim
            for claim in _scope_overlap_for_claims(
                (snapshot.claim for snapshot in active_claims),
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
    lease_expires = _iso(_add_seconds_clamped(_parse_utc(timestamp), lease_seconds))
    claim = Claim(
        agent=agent,
        task_id=task_id,
        summary=summary.strip(),
        mode=mode,
        write_scope=normalized_write_scope,
        run_id=run_id,
        claimed_at_utc=timestamp,
        last_heartbeat_utc=timestamp,
        lease_seconds=lease_seconds,
        claim_lease_expires_utc=lease_expires,
        session_id=owner_context.session_id,
        owner_session_id=owner_context.owner_session_id,
        owner_token_sha256=owner_context.owner_token_sha256,
        owner_pid=owner_context.owner_pid,
        owner_process_start_utc=owner_context.owner_process_start_utc,
        role=owner_context.role,
        agent_uuid=owner_context.agent_uuid,
        capabilities=owner_context.capabilities,
        writer_pid=existing.writer_pid if existing is not None else 0,
        writer_pid_semantics=(
            existing.writer_pid_semantics if existing is not None else ""
        ),
        cwd=existing.cwd if existing is not None else "",
        git_branch=existing.git_branch if existing is not None else "",
    )
    _write_claim_file(
        claim_path,
        claim,
        create_new=existing_snapshot is None,
        expected_source_sha256=(
            existing_snapshot.sha256
            if existing_snapshot is not None
            else None
        ),
        expected_source_size=(
            existing_snapshot.size
            if existing_snapshot is not None
            else None
        ),
        operation="force claim update",
    )
    return claim


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
    _assert_bound_session_agent(agent)
    _validate_task_id(task_id)
    if not release_status or not release_status.strip():
        raise WorkQueueError("release_status required")

    owner_context = _claim_owner_context()
    bridge = resolve_bridge_root(bridge_root)
    claims_dir = bridge / "work_queue" / "claims"
    if not claims_dir.is_dir():
        raise WorkQueueError(f"no active claim for task {task_id}")
    with _claim_mutation_lock(bridge):
        return _release_task_locked(
            agent=agent,
            task_id=task_id,
            release_status=release_status,
            release_message=release_message,
            bridge=bridge,
            now_utc=now_utc,
            owner_context=owner_context,
        )


def _release_task_locked(
    *,
    agent: str,
    task_id: str,
    release_status: str,
    release_message: str,
    bridge: Path,
    now_utc: datetime | None,
    owner_context: _ClaimOwnerContext,
) -> ReleaseRecord:
    claims_dir = bridge / "work_queue" / "claims"
    done_dir = bridge / "work_queue" / "done"
    claim_path, existing_snapshot = _mutation_claim_snapshot_for_task(
        claims_dir,
        task_id,
    )
    if existing_snapshot is None:
        raise WorkQueueError(f"no active claim for task {task_id}")
    existing = existing_snapshot.claim
    expected_source_sha256 = existing_snapshot.sha256
    expected_source_size = existing_snapshot.size
    if existing.task_id != task_id:
        raise WorkQueueError(
            "release rejected: active claim changed during lookup; "
            f"expected task_id {task_id!r}, found {existing.task_id!r}"
        )
    if existing.agent != agent:
        raise WorkQueueError(
            f"release rejected: claim held by {existing.agent}, not {agent}"
        )
    _assert_claim_owner(existing, owner_context, operation="release")

    released_at = _iso(now_utc or datetime.now(timezone.utc))
    record = ReleaseRecord(
        agent=agent,
        task_id=task_id,
        summary=existing.summary,
        release_status=release_status.strip(),
        release_message=release_message.strip(),
        claimed_at_utc=existing.claimed_at_utc,
        released_at_utc=released_at,
        mode=existing.mode,
        write_scope=existing.write_scope,
        run_id=existing.run_id,
        last_heartbeat_utc=existing.last_heartbeat_utc,
        lease_seconds=existing.lease_seconds,
        claim_lease_expires_utc=existing.claim_lease_expires_utc,
        session_id=existing.session_id,
        owner_session_id=existing.owner_session_id,
        owner_token_sha256=existing.owner_token_sha256,
        owner_pid=existing.owner_pid,
        owner_process_start_utc=existing.owner_process_start_utc,
        role=existing.role,
        agent_uuid=existing.agent_uuid,
        capabilities=existing.capabilities,
        writer_pid=existing.writer_pid,
        writer_pid_semantics=existing.writer_pid_semantics,
        cwd=existing.cwd,
        git_branch=existing.git_branch,
    )
    _ensure_plain_directory(done_dir, label="completed claims directory")
    done_path = done_dir / f"{_safe_name(task_id)}-{_safe_name(released_at)}.json"
    _commit_json_against_claim_snapshot(
        source_path=claim_path,
        destination_path=done_path,
        payload=_release_payload(record),
        expected_source_sha256=expected_source_sha256,
        expected_source_size=expected_source_size,
        operation="release",
    )
    return record


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
    _assert_bound_session_agent(agent)
    _validate_task_id(task_id)
    requested_lease_seconds = (
        _require_positive_int32(lease_seconds, field_name="lease_seconds")
        if lease_seconds is not None
        else None
    )
    owner_context = _claim_owner_context()
    bridge = resolve_bridge_root(bridge_root)
    claims_dir = bridge / "work_queue" / "claims"
    if not claims_dir.is_dir():
        raise WorkQueueError(f"no active claim for task {task_id}")
    with _claim_mutation_lock(bridge):
        return _heartbeat_locked(
            agent=agent,
            task_id=task_id,
            bridge=bridge,
            now_utc=now_utc,
            lease_seconds=requested_lease_seconds,
            owner_context=owner_context,
        )


def _heartbeat_locked(
    *,
    agent: str,
    task_id: str,
    bridge: Path,
    now_utc: datetime | None,
    lease_seconds: int | None,
    owner_context: _ClaimOwnerContext,
) -> Claim:
    claims_dir = bridge / "work_queue" / "claims"
    claim_path, existing_snapshot = _mutation_claim_snapshot_for_task(
        claims_dir,
        task_id,
    )
    if existing_snapshot is None:
        raise WorkQueueError(f"no active claim for task {task_id}")
    existing = existing_snapshot.claim
    expected_source_sha256 = existing_snapshot.sha256
    expected_source_size = existing_snapshot.size
    if existing.task_id != task_id:
        raise WorkQueueError(
            "heartbeat rejected: active claim changed during lookup; "
            f"expected task_id {task_id!r}, found {existing.task_id!r}"
        )
    if existing.agent != agent:
        raise WorkQueueError(
            f"heartbeat rejected: claim held by {existing.agent}, not {agent}"
        )
    _assert_claim_owner(existing, owner_context, operation="heartbeat")

    timestamp = _iso(now_utc or datetime.now(timezone.utc))
    refreshed_lease_seconds = (
        lease_seconds
        if lease_seconds is not None
        else existing.lease_seconds or DEFAULT_LEASE_SECONDS
    )
    lease_expires = _iso(
        _add_seconds_clamped(
            _parse_utc(timestamp),
            refreshed_lease_seconds,
        )
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
        session_id=existing.session_id,
        owner_session_id=existing.owner_session_id,
        owner_token_sha256=existing.owner_token_sha256,
        owner_pid=existing.owner_pid,
        owner_process_start_utc=existing.owner_process_start_utc,
        role=existing.role,
        agent_uuid=existing.agent_uuid,
        capabilities=existing.capabilities,
        writer_pid=existing.writer_pid,
        writer_pid_semantics=existing.writer_pid_semantics,
        cwd=existing.cwd,
        git_branch=existing.git_branch,
    )
    _write_claim_file(
        claim_path,
        refreshed,
        expected_source_sha256=expected_source_sha256,
        expected_source_size=expected_source_size,
        operation="heartbeat",
    )
    return refreshed


def list_claims(bridge_root: Path | None = None) -> list[Claim]:
    """Return all active claims, refusing an incomplete claim snapshot."""
    bridge = resolve_bridge_root(bridge_root)
    work_queue_dir = bridge / "work_queue"
    claims_dir = work_queue_dir / "claims"
    if os.path.lexists(bridge):
        _require_plain_directory(bridge, label="bridge root")
    if os.path.lexists(work_queue_dir):
        _require_plain_directory(
            work_queue_dir,
            label="work queue directory",
        )
    if not os.path.lexists(claims_dir):
        return []
    _require_plain_directory(claims_dir, label="active claims directory")
    claims: list[Claim] = []
    unreadable: list[str] = []
    for path in sorted(claims_dir.glob("*.json")):
        try:
            claims.append(_read_claim_file(path))
        except WorkQueueError as exc:
            unreadable.append(f"{path}: {exc}")
    if unreadable:
        raise WorkQueueError(
            "active claim file could not be read "
            f"(unreadable_count={len(unreadable)}); refusing to report an "
            f"incomplete claim set: {' | '.join(unreadable)}"
        )
    return claims


def detect_stale_claims(
    *,
    bridge_root: Path | None = None,
    now_utc: datetime | None = None,
    max_age_seconds: int | None = None,
) -> list[Claim]:
    """Return claims whose centralized effective lease has expired."""
    max_age_seconds = resolve_stale_max_seconds(max_age_seconds)
    now = now_utc or datetime.now(timezone.utc)
    stale: list[Claim] = []
    for claim in list_claims(bridge_root=bridge_root):
        expiry = _claim_expiry(
            claim,
            fallback_lease_seconds=max_age_seconds,
        )
        if expiry.expires_utc is None or now >= expiry.expires_utc:
            stale.append(claim)
    return stale


PRIVILEGED_AGENTS = frozenset({"operator", "system"})


def _stale_archive_payload(
    record: ArchivedClaim,
    *,
    now_utc: datetime,
) -> dict[str, object]:
    claim = record.claim
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
        "released_at_utc": _iso(now_utc),
        "release_status": "stale_lease",
        "release_reason": record.release_reason,
    }
    if claim.session_id:
        payload["session_id"] = claim.session_id
    if claim.owner_session_id:
        payload["owner_session_id"] = claim.owner_session_id
    if claim.owner_token_sha256:
        payload["owner_token_sha256"] = claim.owner_token_sha256
    if claim.owner_pid:
        payload["owner_pid"] = claim.owner_pid
    if claim.owner_process_start_utc:
        payload["owner_process_start_utc"] = claim.owner_process_start_utc
    if claim.role:
        payload["role"] = claim.role
    if claim.agent_uuid:
        payload["agent_uuid"] = claim.agent_uuid
    if claim.capabilities:
        payload["capabilities"] = list(claim.capabilities)
    if claim.writer_pid:
        payload["writer_pid"] = claim.writer_pid
    if claim.writer_pid_semantics:
        payload["writer_pid_semantics"] = claim.writer_pid_semantics
    if claim.cwd:
        payload["cwd"] = claim.cwd
    if claim.git_branch:
        payload["git_branch"] = claim.git_branch
    return payload


def _copy_file_create_new(source: Path, destination: Path) -> tuple[str, int]:
    """Copy exact source bytes and return their SHA-256 identity and size."""

    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as source_handle:
        with destination.open("xb") as destination_handle:
            while chunk := source_handle.read(1024 * 1024):
                destination_handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    return digest.hexdigest(), size


def _rename_file_create_new(source: Path, destination: Path) -> None:
    """Atomically rename one file without ever replacing ``destination``."""

    if os.name == "nt":
        # Python guarantees FileExistsError when dst exists on Windows.
        os.rename(source, destination)
        return

    encoded_source = os.fsencode(source)
    encoded_destination = os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(
                errno.ENOTSUP,
                "atomic no-replace rename is unavailable on this Linux libc",
                str(destination),
            )
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,  # AT_FDCWD
            encoded_source,
            -100,
            encoded_destination,
            1,  # RENAME_NOREPLACE
        )
    elif sys.platform == "darwin":
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise OSError(
                errno.ENOTSUP,
                "atomic exclusive rename is unavailable on this macOS libc",
                str(destination),
            )
        renamex_np.argtypes = (
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renamex_np.restype = ctypes.c_int
        result = renamex_np(
            encoded_source,
            encoded_destination,
            0x00000004,  # RENAME_EXCL
        )
    else:
        raise OSError(
            errno.ENOTSUP,
            f"atomic no-replace rename is unsupported on {sys.platform}",
            str(destination),
        )

    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            str(destination),
        )


def _verify_stale_source_identity(
    path: Path,
    plan: _StaleApplyPlan,
    *,
    label: str,
) -> tuple[bool, str | None]:
    """Verify a rollback file against the plan-owned source identity."""

    expected_digest = plan.expected_source_sha256
    expected_size = plan.expected_source_size
    if expected_digest is None or expected_size is None:
        return (
            False,
            f"{label} identity verification unavailable for {path}: "
            "expected source identity was not prepared",
        )

    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        return (
            False,
            f"{label} identity verification failed for {path}: {exc}",
        )

    actual_digest = digest.hexdigest()
    if size != expected_size or actual_digest != expected_digest:
        return (
            False,
            f"{label} identity mismatch for {path}: "
            f"expected sha256={expected_digest} size={expected_size}, "
            f"actual sha256={actual_digest} size={size}",
        )
    return True, None


def _verify_stale_archive_identity(
    path: Path,
    plan: _StaleApplyPlan,
    *,
    label: str,
) -> tuple[bool, str | None]:
    """Verify a cleanup target against the plan-owned archive bytes."""

    expected_digest = plan.expected_archive_sha256
    expected_size = plan.expected_archive_size
    if expected_digest is None or expected_size is None:
        return (
            False,
            f"{label} identity verification unavailable for {path}: "
            "expected archive identity was not prepared",
        )

    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        return (
            False,
            f"{label} identity verification failed for {path}: {exc}",
        )

    actual_digest = digest.hexdigest()
    if size != expected_size or actual_digest != expected_digest:
        return (
            False,
            f"{label} identity mismatch for {path}: "
            f"expected sha256={expected_digest} size={expected_size}, "
            f"actual sha256={actual_digest} size={size}",
        )
    return True, None


def _stale_apply_state(plans: Sequence[_StaleApplyPlan]) -> str:
    states: list[str] = []
    for plan in plans:
        states.append(
            (
                f"task={plan.record.claim.task_id!r} "
                f"source={'retained' if plan.source_path.is_file() else 'missing'}:"
                f"{plan.source_path} "
                f"archive={'retained' if plan.record.archived_path.exists() else 'missing'}:"
                f"{plan.record.archived_path} "
                f"backup={'retained' if plan.source_backup_path.is_file() else 'missing'}:"
                f"{plan.source_backup_path} "
                f"quarantine={'retained' if plan.source_quarantine_path.is_file() else 'missing'}:"
                f"{plan.source_quarantine_path} "
                f"temp={'retained' if plan.archive_temp_path.exists() else 'missing'}:"
                f"{plan.archive_temp_path}"
            )
        )
    return " | ".join(states)


def _warn_stale_cleanup_nonthrowing(message: str) -> None:
    """Emit a committed-batch cleanup warning without changing the outcome."""

    try:
        with warnings.catch_warnings():
            # A caller may promote RuntimeWarning to an exception. Cleanup is
            # ancillary after the active claims and archives have committed,
            # so locally force delivery without inheriting that failure mode.
            warnings.simplefilter("always", RuntimeWarning)
            warnings.warn(
                message,
                RuntimeWarning,
                stacklevel=3,
            )
    except Exception:
        # Custom warning delivery hooks can still fail. Keep the committed
        # return authoritative and make one best-effort direct diagnostic.
        try:
            sys.stderr.write(f"RuntimeWarning: {message}\n")
        except Exception:
            pass


def _rollback_stale_apply(
    plans: Sequence[_StaleApplyPlan],
    *,
    done_dir: Path,
    remove_done_dir: bool,
) -> list[str]:
    """Best-effort whole-batch rollback with complete error collection."""

    rollback_errors: list[str] = []

    # A completed source->quarantine move revokes the eligibility-time backup
    # as automatic write authority. The moved generation may be newer than the
    # eligibility snapshot. Restore only bytes captured from that moved file;
    # if capture failed, retain every artifact and leave the canonical path
    # absent rather than republishing a stale generation.
    for plan in reversed(plans):
        plan.rollback_source_verified = False
        if plan.source_path.is_file():
            if plan.source_quarantine_body is not None:
                if (
                    plan.source_quarantine_sha256 is None
                    or plan.source_quarantine_size is None
                ):
                    rollback_errors.append(
                        "captured moved source identity is incomplete; "
                        f"preserved active source {plan.source_path}"
                    )
                    continue
                source_verified, source_error = (
                    _verify_single_link_regular_file_identity(
                        plan.source_path,
                        expected_sha256=plan.source_quarantine_sha256,
                        expected_size=plan.source_quarantine_size,
                        label="active captured source",
                    )
                )
                if not source_verified:
                    rollback_errors.append(
                        source_error
                        or (
                            "active source no longer matches the captured "
                            f"moved generation: {plan.source_path}"
                        )
                    )
                    continue
                plan.rollback_source_verified = True
                plan.source_removed = False
                continue
            if plan.source_removed:
                rollback_errors.append(
                    "active source generation is unknown because capture "
                    "failed after quarantine; preserved concurrently "
                    f"recreated source {plan.source_path}"
                )
                continue
            if (
                plan.expected_source_sha256 is None
                or plan.expected_source_size is None
            ):
                # Preparation never mutates an active source. If it failed
                # before this plan obtained an identity and no archive was
                # published, the still-present source needs no rollback.
                if not plan.archive_published and not plan.source_removed:
                    plan.rollback_source_verified = True
                    continue
                rollback_errors.append(
                    "active source identity verification unavailable after "
                    f"mutation: {plan.source_path}"
                )
                continue
            source_verified, source_error = _verify_stale_source_identity(
                plan.source_path,
                plan,
                label="active source",
            )
            if not source_verified:
                rollback_errors.append(
                    source_error
                    or (
                        "active source identity verification failed: "
                        f"{plan.source_path}"
                    )
                )
                continue
            plan.rollback_source_verified = True
            plan.source_removed = False
            continue
        if plan.source_quarantine_body is None:
            rollback_errors.append(
                "active source restore skipped because no immutable bytes "
                "were captured after quarantine; eligibility backup is not "
                f"write authority: {plan.source_path}; retained quarantine "
                f"{plan.source_quarantine_path} and backup "
                f"{plan.source_backup_path}"
            )
            continue
        if (
            plan.source_quarantine_sha256 is None
            or plan.source_quarantine_size is None
        ):
            rollback_errors.append(
                "active source restore skipped because captured moved source "
                f"identity is incomplete: {plan.source_path}"
            )
            continue
        try:
            _publish_prepared_file_create_new(
                plan.source_quarantine_body,
                plan.source_path,
                expected_sha256=plan.source_quarantine_sha256,
                expected_size=plan.source_quarantine_size,
                operation="captured stale active source rollback",
            )
        except WorkQueueError as exc:
            rollback_errors.append(
                "captured active source restore failed "
                f"{plan.source_path} from immutable quarantine snapshot "
                f"{plan.source_quarantine_path}: {exc}"
            )
            continue
        if not plan.source_path.is_file():
            rollback_errors.append(
                "captured source restore did not produce an active claim "
                f"file: {plan.source_path}"
            )
            continue
        source_verified, source_error = (
            _verify_single_link_regular_file_identity(
                plan.source_path,
                expected_sha256=plan.source_quarantine_sha256,
                expected_size=plan.source_quarantine_size,
                label="restored captured active source",
            )
        )
        if not source_verified:
            rollback_errors.append(
                source_error
                or (
                    "restored active source does not match the immutable "
                    f"quarantine snapshot: {plan.source_path}"
                )
            )
            continue
        plan.rollback_source_verified = True
        plan.source_removed = False

    # A pathname can be replaced after any identity check and before unlink.
    # Keep every published archive during a failed batch instead of risking
    # deletion of a concurrently published foreign generation. The active
    # source and exact-byte recovery artifacts make the duplicate state
    # explicit and recoverable.
    for plan in reversed(plans):
        if plan.archive_published:
            rollback_errors.append(
                "archive rollback skipped to avoid an unbound pathname "
                f"deletion; retained archive {plan.record.archived_path}, "
                f"batch archive temp {plan.archive_temp_path}, and source "
                f"backup {plan.source_backup_path}"
            )

    # Recovery artifacts are intentionally retained. Portable filesystems do
    # not provide an unlink-by-verified-handle primitive, so verify them only
    # for diagnostics and never authorize deletion with a stale boolean.
    for plan in reversed(plans):
        if plan.source_quarantine_path.exists():
            if not plan.rollback_source_verified:
                rollback_errors.append(
                    "source quarantine cleanup skipped because active source "
                    "rollback was not verified; retained exact recovery "
                    f"artifact {plan.source_quarantine_path}"
                )
            elif not plan.source_quarantine_verified:
                rollback_errors.append(
                    "source quarantine cleanup skipped because batch "
                    "ownership was not verified; retained "
                    f"{plan.source_quarantine_path}"
                )
            else:
                quarantine_verified, quarantine_error = (
                    _verify_stale_source_identity(
                        plan.source_quarantine_path,
                        plan,
                        label="source quarantine cleanup",
                    )
                )
                if not quarantine_verified:
                    rollback_errors.append(
                        quarantine_error
                        or (
                            "source quarantine cleanup identity "
                            f"verification failed: {plan.source_quarantine_path}"
                        )
                    )
        if plan.archive_temp_path.exists():
            temp_verified, temp_error = _verify_stale_archive_identity(
                plan.archive_temp_path,
                plan,
                label="archive temp",
            )
            if not temp_verified:
                rollback_errors.append(
                    temp_error
                    or (
                        "archive temp identity verification failed: "
                        f"{plan.archive_temp_path}"
                    )
                )
                continue
        if (
            plan.source_backup_path.exists()
            and plan.rollback_source_verified
        ):
            backup_verified, backup_error = _verify_stale_source_identity(
                plan.source_backup_path,
                plan,
                label="source backup cleanup",
            )
            if not backup_verified:
                rollback_errors.append(
                    backup_error
                    or (
                        "source backup cleanup identity verification failed: "
                        f"{plan.source_backup_path}"
                    )
                )

    # Never remove even a newly created empty directory during rollback. A
    # foreign directory can replace the pathname after an emptiness check and
    # before rmdir, while retaining an empty work-queue directory is harmless.
    del remove_done_dir
    return rollback_errors


def _stale_apply_failure(
    *,
    primary_error: Exception,
    rollback_errors: Sequence[str],
    plans: Sequence[_StaleApplyPlan],
) -> WorkQueueError:
    rollback_summary = (
        " | ".join(rollback_errors) if rollback_errors else "<none>"
    )
    return WorkQueueError(
        "stale claim batch apply failed; primary: "
        f"{type(primary_error).__name__}: {primary_error}; "
        f"rollback failures: {rollback_summary}; "
        f"state: {_stale_apply_state(plans)}"
    )


def archive_stale_claims(
    *,
    bridge_root: Path | None = None,
    now_utc: datetime | None = None,
    max_age_seconds: int | None = None,
    apply: bool = False,
) -> list[ArchivedClaim]:
    """Sweep stale claims, serializing the full apply scan and mutation."""

    max_age_seconds = resolve_stale_max_seconds(max_age_seconds)
    bridge = resolve_bridge_root(bridge_root)
    now = now_utc or datetime.now(timezone.utc)
    if apply:
        with _claim_mutation_lock(bridge):
            return _archive_stale_claims_unlocked(
                bridge_root=bridge,
                now_utc=now,
                max_age_seconds=max_age_seconds,
                apply=True,
            )
    return _archive_stale_claims_unlocked(
        bridge_root=bridge,
        now_utc=now,
        max_age_seconds=max_age_seconds,
        apply=False,
    )


def _archive_stale_claims_unlocked(
    *,
    bridge_root: Path | None = None,
    now_utc: datetime | None = None,
    max_age_seconds: int | None = None,
    apply: bool = False,
) -> list[ArchivedClaim]:
    """Sweep stale claims; dry-run unless apply=True.

    Parity with `.agent-bridge/bin/Invoke-StaleClaimSweep.ps1`:

    * Complete owner-bound claims anchor at `last_heartbeat_utc` (falling
      back to `claimed_at_utc`), use their positive stored lease when present,
      and honor a later valid `claim_lease_expires_utc`.
    * Incomplete legacy claims anchor only at `claimed_at_utc`, use the
      caller's fallback threshold, and ignore stored lease, heartbeat, and
      future-expiry fields that an unauthenticated legacy writer could extend.
    * Claims owned by ``operator`` or ``system`` are never swept.
    * Each swept claim is archived to
      ``work_queue/done/<safe_task>.<utc_stamp>.stale_lease.json`` with
      ``release_status="stale_lease"``, ``release_reason`` describing the
      lease age, and ``released_at_utc`` set to ``now_utc``.
    * With ``apply=False`` (the default) no files are moved or written;
      the returned ``ArchivedClaim`` records describe the planned action.
    * With ``apply=True`` the complete batch is preflighted and prepared
      under the shared mutation lock before the first active claim is
      removed. A handled apply failure triggers best-effort active-source
      restoration. Published archives remain as an explicit recoverable
      duplicate state because portable pathname deletion cannot be bound to
      a previously verified file object.
    * Archive temps, source quarantines, and source backups are intentionally
      retained after both successful commits and failed batches. They are
      independent files, never mutable hard-link aliases. A separately
      designed, ownership-bound garbage collector is required before these
      recovery artifacts may be removed.

    This is handled-exception atomicity across cooperative lock users, not a
    crash- or power-loss journal. Abrupt-process durability would require a
    separately designed journal and directory/file synchronization protocol.

    The primitive intentionally does not emit bridge events; the CLI
    wrapper in ``tools/work_queue_sweep_stale.py`` is responsible for
    observability.
    """
    max_age_seconds = resolve_stale_max_seconds(max_age_seconds)
    bridge = resolve_bridge_root(bridge_root)
    now = now_utc or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    done_dir = bridge / "work_queue" / "done"
    claims_dir = bridge / "work_queue" / "claims"
    claims: list[Claim] = []
    claim_snapshots: list[_ActiveClaimSnapshot] = []
    claim_snapshot_identities: dict[int, tuple[str, int]] = {}
    claim_files = sorted(claims_dir.glob("*.json")) if claims_dir.exists() else []
    for path in claim_files:
        try:
            claim, snapshot_sha256, snapshot_size = _read_claim_file_snapshot(
                path,
                # Stale recovery intentionally classifies malformed owner
                # generations as legacy tokenless claims. It still uses the
                # same strict file, UTF-8, JSON, and snapshot-identity checks,
                # but must not let the ordinary mutation authority gate hide
                # a recoverable legacy record.
                validate_raw_authority=False,
            )
        except WorkQueueError:
            continue
        try:
            _validate_task_id(claim.task_id)
        except WorkQueueError:
            # Retain malformed active records for explicit operator recovery.
            # Never sanitize a task identity that the public mutators reject.
            continue
        claims.append(claim)
        claim_snapshots.append(
            _ActiveClaimSnapshot(
                path=path,
                claim=claim,
                sha256=snapshot_sha256,
                size=snapshot_size,
            )
        )
        claim_snapshot_identities[id(claim)] = (
            snapshot_sha256,
            snapshot_size,
        )
    claim_paths: dict[str, Path] = {}
    if apply:
        # Resolve every logical claim before the first archive write. This
        # catches exact-task duplicates and preferred-path collisions without
        # leaving a partial batch in done/.
        for claim in claims:
            if not claim.task_id:
                continue
            claim_path, existing_snapshot = _claim_for_task_from_snapshot(
                claims_dir,
                claim.task_id,
                claim_snapshots,
            )
            if existing_snapshot is None:
                raise WorkQueueError(
                    "stale claim disappeared from the captured active "
                    f"snapshot: {claim.task_id!r}"
                )
            claim_paths[claim.task_id] = claim_path

    planned: list[ArchivedClaim] = []
    for claim in claims:
        if claim.agent in PRIVILEGED_AGENTS:
            continue
        if not claim.task_id:
            # Identity-bearing JSON scalars are never stringified. Retain a
            # malformed active record for operator recovery instead of
            # inventing a task identity or archive filename.
            continue
        expiry = _claim_expiry(
            claim,
            fallback_lease_seconds=max_age_seconds,
        )
        if expiry.expires_utc is not None and now < expiry.expires_utc:
            continue
        if expiry.anchor_utc is None:
            age_seconds = expiry.effective_lease_seconds
        else:
            age_seconds = int((now - expiry.anchor_utc).total_seconds())
        if expiry.legacy_tokenless:
            reason = (
                f"legacy tokenless claim {expiry.anchor_field} was "
                f"{age_seconds}s old; lease threshold "
                f"{expiry.effective_lease_seconds}s"
            )
        else:
            reason = (
                f"{expiry.anchor_field} was {age_seconds}s old; "
                f"lease threshold {expiry.effective_lease_seconds}s"
            )

        safe_task = _safe_name(claim.task_id)
        if not safe_task:
            continue
        archive_path = done_dir / f"{safe_task}.{stamp}.stale_lease.json"
        planned.append(
            ArchivedClaim(
                claim=claim,
                archived_path=archive_path,
                age_seconds=age_seconds,
                release_reason=reason,
                applied=apply,
            )
        )

    if not apply:
        return planned

    # Windows archive names are case-insensitive even though task IDs are
    # ordinal. Preflight the complete batch, including existing done entries,
    # before creating a directory or publishing the first archive.
    archive_names: dict[str, Path] = {}
    for record in planned:
        key = record.archived_path.name.casefold()
        previous = archive_names.get(key)
        if previous is not None:
            raise WorkQueueError(
                "stale archive destination collision: "
                f"{previous.name}, {record.archived_path.name}"
            )
        archive_names[key] = record.archived_path
    if done_dir.is_dir():
        existing_names = {
            path.name.casefold(): path
            for path in done_dir.iterdir()
        }
        for key, path in archive_names.items():
            existing = existing_names.get(key)
            if existing is not None:
                raise WorkQueueError(
                    "stale archive destination already exists: "
                    f"{existing}"
                )

    if not planned:
        return planned

    apply_plans = [
        _StaleApplyPlan(
            record=record,
            source_path=claim_paths[record.claim.task_id],
            archive_temp_path=record.archived_path.with_name(
                f"{record.archived_path.name}.tmp.{uuid4().hex}"
            ),
            source_backup_path=claim_paths[record.claim.task_id].with_name(
                f".{claim_paths[record.claim.task_id].name}."
                f"stale-backup.{uuid4().hex}"
            ),
            source_quarantine_path=claim_paths[
                record.claim.task_id
            ].with_name(
                f".{claim_paths[record.claim.task_id].name}."
                f"stale-quarantine.{uuid4().hex}"
            ),
            expected_source_sha256=claim_snapshot_identities[
                id(record.claim)
            ][0],
            expected_source_size=claim_snapshot_identities[
                id(record.claim)
            ][1],
        )
        for record in planned
    ]
    if os.path.lexists(done_dir):
        _require_plain_directory(
            done_dir,
            label="stale archive destination parent",
        )
    for plan in apply_plans:
        if not plan.source_path.is_file():
            raise WorkQueueError(
                "stale claim source disappeared before batch preparation: "
                f"{plan.source_path}"
            )
    done_dir_created = False

    # Prepare every fully closed archive and every exact-byte source backup
    # before publishing or removing the first active record.
    try:
        done_dir_existed = os.path.lexists(done_dir)
        _ensure_plain_directory(
            done_dir,
            label="stale archive destination parent",
        )
        done_dir_created = not done_dir_existed
        for plan in apply_plans:
            body = (
                json.dumps(
                    _stale_archive_payload(plan.record, now_utc=now),
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            plan.archive_body = body.encode("utf-8")
            plan.expected_archive_sha256 = hashlib.sha256(
                plan.archive_body
            ).hexdigest()
            plan.expected_archive_size = len(plan.archive_body)
            _publish_prepared_file_create_new(
                plan.archive_body,
                plan.archive_temp_path,
                expected_sha256=plan.expected_archive_sha256,
                expected_size=plan.expected_archive_size,
                operation="stale archive recovery temp",
            )
            archive_bytes = plan.archive_temp_path.read_bytes()
            if archive_bytes != plan.archive_body:
                raise WorkQueueError(
                    "stale archive temp changed during preparation: "
                    f"{plan.archive_temp_path}"
                )
            backup_sha256, backup_size = _copy_file_create_new(
                plan.source_path,
                plan.source_backup_path,
            )
            if (
                backup_sha256 != plan.expected_source_sha256
                or backup_size != plan.expected_source_size
            ):
                raise WorkQueueError(
                    "stale claim source changed since eligibility snapshot: "
                    f"{plan.source_path}; "
                    f"snapshot={plan.expected_source_sha256}:"
                    f"{plan.expected_source_size}; "
                    f"backup={backup_sha256}:{backup_size}"
                )
    except Exception as primary_error:
        rollback_errors = _rollback_stale_apply(
            apply_plans,
            done_dir=done_dir,
            remove_done_dir=done_dir_created,
        )
        raise _stale_apply_failure(
            primary_error=primary_error,
            rollback_errors=rollback_errors,
            plans=apply_plans,
        ) from primary_error

    try:
        for plan in apply_plans:
            if (
                plan.archive_body is None
                or plan.expected_archive_sha256 is None
                or plan.expected_archive_size is None
            ):
                raise WorkQueueError(
                    "stale archive publication body was not prepared: "
                    f"{plan.record.archived_path}"
                )
            try:
                _publish_prepared_file_create_new(
                    plan.archive_body,
                    plan.record.archived_path,
                    expected_sha256=plan.expected_archive_sha256,
                    expected_size=plan.expected_archive_size,
                    operation="stale archive",
                )
            except WorkQueueError:
                raise
            plan.archive_published = True
            _rename_file_create_new(
                plan.source_path,
                plan.source_quarantine_path,
            )
            plan.source_removed = True
            (
                quarantine_body,
                quarantine_sha256,
                quarantine_size,
            ) = _capture_raw_file_snapshot(plan.source_quarantine_path)
            plan.source_quarantine_body = quarantine_body
            plan.source_quarantine_sha256 = quarantine_sha256
            plan.source_quarantine_size = quarantine_size
            quarantine_verified, quarantine_error = (
                _verify_single_link_regular_file_identity(
                    plan.source_quarantine_path,
                    expected_sha256=quarantine_sha256,
                    expected_size=quarantine_size,
                    label="quarantined active source",
                )
            )
            quarantine_authorized = (
                quarantine_sha256 == plan.expected_source_sha256
                and quarantine_size == plan.expected_source_size
            )
            if not quarantine_verified or not quarantine_authorized:
                restore_error = ""
                if plan.source_path.exists():
                    restore_error = (
                        "active source path was concurrently recreated; "
                        "foreign quarantine retained"
                    )
                else:
                    try:
                        _publish_prepared_file_create_new(
                            quarantine_body,
                            plan.source_path,
                            expected_sha256=quarantine_sha256,
                            expected_size=quarantine_size,
                            operation="captured active source restore",
                        )
                        plan.source_removed = False
                    except (OSError, WorkQueueError) as exc:
                        restore_error = (
                            "captured active source restore failed "
                            f"{plan.source_path} from "
                            f"{plan.source_quarantine_path}: {exc}"
                        )
                if quarantine_authorized:
                    rejection_error = quarantine_error
                else:
                    rejection_error = (
                        "quarantined active source identity mismatch for "
                        f"{plan.source_quarantine_path}: expected "
                        f"sha256={plan.expected_source_sha256} "
                        f"size={plan.expected_source_size}, actual "
                        f"sha256={quarantine_sha256} size={quarantine_size}"
                    )
                raise WorkQueueError(
                    (
                        rejection_error
                        or (
                            "quarantined active source identity "
                            f"verification failed: {plan.source_quarantine_path}"
                        )
                    )
                    + (
                        f"; {restore_error}"
                        if restore_error
                        else "; captured active source restored"
                    )
                )
            plan.source_quarantine_verified = True
    except Exception as primary_error:
        rollback_errors = _rollback_stale_apply(
            apply_plans,
            done_dir=done_dir,
            remove_done_dir=done_dir_created,
        )
        raise _stale_apply_failure(
            primary_error=primary_error,
            rollback_errors=rollback_errors,
            plans=apply_plans,
        ) from primary_error

    cleanup_errors: list[str] = []
    # Retain the exact source quarantine, source backup, and archive temp as
    # recovery evidence. A verify-then-unlink sequence can delete a foreign
    # file that replaces the pathname between those operations. Validation is
    # still useful for diagnostics, but never grants deletion authority.
    for plan in apply_plans:
        if plan.source_quarantine_path.exists():
            if not plan.source_quarantine_verified:
                cleanup_errors.append(
                    "source quarantine cleanup skipped because batch "
                    "ownership was not verified; retained "
                    f"{plan.source_quarantine_path}"
                )
            else:
                quarantine_verified, quarantine_error = (
                    _verify_stale_source_identity(
                        plan.source_quarantine_path,
                        plan,
                        label="source quarantine cleanup",
                    )
                )
                if not quarantine_verified:
                    cleanup_errors.append(
                        quarantine_error
                        or (
                            "source quarantine cleanup identity verification "
                            f"failed: {plan.source_quarantine_path}"
                        )
                    )
        if plan.archive_temp_path.exists():
            temp_verified, temp_error = _verify_stale_archive_identity(
                plan.archive_temp_path,
                plan,
                label="archive temp cleanup",
            )
            if not temp_verified:
                cleanup_errors.append(
                    temp_error
                    or (
                        "archive temp cleanup identity verification "
                        f"failed: {plan.archive_temp_path}"
                    )
                )

        if plan.source_backup_path.exists():
            backup_verified, backup_error = _verify_stale_source_identity(
                plan.source_backup_path,
                plan,
                label="source backup cleanup",
            )
            if not backup_verified:
                cleanup_errors.append(
                    backup_error
                    or (
                        "source backup cleanup identity verification failed: "
                        f"{plan.source_backup_path}"
                    )
                )
    if cleanup_errors:
        _warn_stale_cleanup_nonthrowing(
            (
                "stale claim batch committed but cleanup failed; "
                f"cleanup failures: {' | '.join(cleanup_errors)}; "
                f"state: {_stale_apply_state(apply_plans)}"
            )
        )
    return planned


def check_scope_overlap(
    bridge_root: Path | None = None,
    write_scope: Sequence[str] = (),
) -> list[Claim]:
    """Return active claims that may conflict with the given write_scope.

    ``read-only`` is the only stored mode that is safe to ignore.  A missing,
    non-string, or otherwise unknown mode may represent a corrupted writer,
    so fail closed and treat it as a wildcard write claim.
    """
    return _scope_overlap_for_claims(
        list_claims(bridge_root=bridge_root),
        write_scope=write_scope,
    )


def _scope_overlap_for_claims(
    claims: Sequence[Claim] | Iterator[Claim],
    *,
    write_scope: Sequence[str],
) -> list[Claim]:
    normalized_scope = _normalize_write_scope_entries(write_scope)
    if not normalized_scope:
        return []
    normalized_request = {_normalize_scope_entry(s) for s in normalized_scope if s}
    if not normalized_request:
        return []
    overlapping: list[Claim] = []
    for claim in claims:
        if claim.mode == "read-only":
            continue
        if claim.mode != "write":
            overlapping.append(claim)
            continue
        existing_scope = {
            _normalize_scope_entry(s)
            for s in _normalize_write_scope_entries(claim.write_scope)
            if s
        }
        if not existing_scope:
            # Historical writers could persist write claims without a usable
            # scope. Fail closed by treating that active claim as a wildcard.
            overlapping.append(claim)
            continue
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


def _strict_active_claim_snapshot(
    claims_dir: Path,
) -> list[_ActiveClaimSnapshot]:
    """Read every active JSON record exactly once for claim acquisition."""

    _require_plain_directory(claims_dir, label="active claims directory")
    try:
        claim_paths = sorted(claims_dir.glob("*.json"))
        with os.scandir(claims_dir) as entries:
            scanned_entries = sorted(
                (
                    Path(entry.path),
                    entry.is_file(),
                )
                for entry in entries
                if Path(entry.name).match("*.json")
            )
    except OSError as exc:
        raise WorkQueueError(
            f"cannot enumerate active claim records: {claims_dir}"
        ) from exc

    scanned_paths = [path for path, _ in scanned_entries]
    if scanned_paths != claim_paths:
        raise WorkQueueError(
            "active claim enumeration changed while mutation lock was held: "
            f"{claims_dir}"
        )

    snapshot: list[_ActiveClaimSnapshot] = []
    task_paths: dict[str, Path] = {}
    for path, is_file in scanned_entries:
        if not is_file:
            raise WorkQueueError(
                f"active claim record must be a file: {path}"
            )

        claim, raw_sha256, raw_size = _read_claim_file_snapshot(path)
        if not claim.task_id:
            # _read_claim_file preserves strings exactly and maps a missing or
            # non-string identity to "". Acquisition cannot distinguish that
            # unknown record from the requested logical task, so fail closed.
            raise WorkQueueError(
                "active claim task_id must be a non-empty string: "
                f"{path}"
            )
        previous_path = task_paths.get(claim.task_id)
        if previous_path is not None:
            raise WorkQueueError(
                "duplicate active claim records for exact task_id "
                f"{claim.task_id!r}: {previous_path}, {path}"
            )
        task_paths[claim.task_id] = path
        snapshot.append(
            _ActiveClaimSnapshot(
                path=path,
                claim=claim,
                sha256=raw_sha256,
                size=raw_size,
            )
        )
    return snapshot


def _claim_for_task_from_snapshot(
    claims_dir: Path,
    task_id: str,
    active_claims: Sequence[_ActiveClaimSnapshot],
) -> tuple[Path, _ActiveClaimSnapshot | None]:
    """Resolve one task solely from a strict, lock-held active snapshot."""

    preferred = claims_dir / f"{_safe_name(task_id)}.json"
    matches: list[_ActiveClaimSnapshot] = []
    preferred_claim: Claim | None = None
    for snapshot in active_claims:
        if snapshot.path == preferred:
            preferred_claim = snapshot.claim
        if snapshot.claim.task_id == task_id:
            matches.append(snapshot)
    if preferred_claim is not None and preferred_claim.task_id != task_id:
        raise WorkQueueError(
            "claim filename collision at preferred path for "
            f"task_id {task_id!r}: stored task_id "
            f"{preferred_claim.task_id!r} in {preferred}"
        )
    if len(matches) > 1:
        duplicate_paths = ", ".join(
            str(snapshot.path) for snapshot in matches
        )
        raise WorkQueueError(
            "duplicate active claim records for exact task_id "
            f"{task_id!r}: {duplicate_paths}"
        )
    if matches:
        return matches[0].path, matches[0]
    return preferred, None


def _mutation_claim_snapshot_for_task(
    claims_dir: Path,
    task_id: str,
) -> tuple[Path, _ActiveClaimSnapshot | None]:
    """Resolve one mutation target while rejecting global parsed duplicates."""

    preferred = claims_dir / f"{_safe_name(task_id)}.json"
    if not claims_dir.exists():
        return preferred, None

    snapshots: list[_ActiveClaimSnapshot] = []
    task_paths: dict[str, Path] = {}
    for path in sorted(claims_dir.glob("*.json")):
        try:
            claim, raw_sha256, raw_size = _read_claim_file_snapshot(path)
        except WorkQueueError:
            if path == preferred:
                raise WorkQueueError(
                    "claim filename collision at preferred path for "
                    f"task_id {task_id!r}: unreadable record {preferred}"
                )
            # Legacy release/heartbeat lookup deliberately tolerates an
            # unrelated malformed record. It never treats that record as a
            # narrowed authority or removes it.
            continue
        if claim.task_id:
            previous_path = task_paths.get(claim.task_id)
            if previous_path is not None:
                raise WorkQueueError(
                    "duplicate active claim records for exact task_id "
                    f"{claim.task_id!r}: {previous_path}, {path}"
                )
            task_paths[claim.task_id] = path
        snapshots.append(
            _ActiveClaimSnapshot(
                path=path,
                claim=claim,
                sha256=raw_sha256,
                size=raw_size,
            )
        )
    return _claim_for_task_from_snapshot(claims_dir, task_id, snapshots)


def _normalize_scope_entry(scope: str) -> str:
    if not isinstance(scope, str):
        raise WorkQueueError("write_scope entries must be strings")
    if not scope.isascii() or any(
        ord(character) < 0x20 or ord(character) > 0x7E
        for character in scope
    ):
        raise WorkQueueError(
            "write_scope paths must contain printable ASCII characters only"
        )
    slash_normalized = scope.replace("\\", "/")
    if slash_normalized.startswith("/"):
        raise WorkQueueError(
            "write_scope paths must be repository-relative"
        )
    if ":" in slash_normalized:
        raise WorkQueueError(
            "write_scope paths must not contain ':'"
        )
    normalized = slash_normalized.lower()
    segments = normalized.split("/")
    if normalized and any(segment in {"", ".", ".."} for segment in segments):
        raise WorkQueueError(
            "write_scope paths must not contain empty, '.' or '..' segments"
        )
    if any(segment.endswith((".", " ")) for segment in segments):
        raise WorkQueueError(
            "write_scope path segments must not end in '.' or space"
        )
    return normalized


def _normalize_write_scope_entries(
    values: object,
    *,
    reject_non_strings: bool = False,
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    if isinstance(values, str):
        source: Sequence[object] = (values,)
    elif isinstance(values, Sequence):
        source = values
    else:
        if reject_non_strings:
            raise WorkQueueError(
                "write_scope must be a string or a sequence of strings"
            )
        source = ()
    for value in source:
        if not isinstance(value, str):
            if reject_non_strings:
                raise WorkQueueError("write_scope entries must be strings")
            continue
        for item in value.split(","):
            scope = item.strip()
            if not scope:
                if reject_non_strings:
                    raise WorkQueueError(
                        "write_scope entries must be non-empty paths"
                    )
                continue
            _normalize_scope_entry(scope)
            if scope in seen:
                continue
            seen.add(scope)
            normalized.append(scope)
    return tuple(normalized)


def _normalize_stored_write_scope_entries(values: object) -> tuple[str, ...]:
    try:
        return _normalize_write_scope_entries(
            values,
            reject_non_strings=True,
        )
    except WorkQueueError:
        # A malformed stored write scope is a wildcard, never a safely
        # narrowed subset. Returning empty preserves that fail-closed meaning
        # in _scope_overlap_for_claims.
        return ()


def _scope_entries_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == "*" or right == "*":
        return True
    if left == right:
        return True
    return left.startswith(right + "/") or right.startswith(left + "/")


def _coerce_positive_int(
    value: object,
    *,
    allow_surrounding_whitespace: bool = True,
) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if 0 < value <= MAX_LEASE_SECONDS else 0
    if not isinstance(value, str):
        return 0
    normalized = value.strip() if allow_surrounding_whitespace else value
    if not normalized or re.fullmatch(r"[0-9]+", normalized) is None:
        return 0
    significant = normalized.lstrip("0")
    if not significant:
        return 0
    maximum_text = str(MAX_LEASE_SECONDS)
    if len(significant) > len(maximum_text) or (
        len(significant) == len(maximum_text)
        and significant > maximum_text
    ):
        return 0
    positive = int(significant)
    return positive if positive > 0 else 0


def _coerce_positive_json_int(value: object, *, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > maximum
    ):
        return 0
    return value


def _require_positive_int32(value: object, *, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > MAX_LEASE_SECONDS
    ):
        raise WorkQueueError(
            f"{field_name} must be positive; expected a positive Int32, "
            f"got {value!r}"
        )
    return value


def resolve_stale_max_seconds(value: int | None = None) -> int:
    """Resolve an explicit or environment-backed stale fallback."""

    if value is not None:
        return _require_positive_int32(
            value,
            field_name="max_age_seconds",
        )
    configured = _coerce_positive_int(os.environ.get(STALE_LEASE_ENV_NAME, ""))
    return configured or DEFAULT_STALE_MAX_SECONDS


def _coerce_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _normalize_capabilities(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        source: Sequence[object] = (values,)
    elif isinstance(values, Sequence):
        source = values
    else:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for value in source:
        text = _coerce_text(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _reject_nonfinite_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not permitted: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number is not permitted: {value}")
    return parsed


def _reject_duplicate_json_object_fields(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    seen_casefolded: dict[str, str] = {}
    for key, value in pairs:
        folded = key.casefold()
        if folded in seen_casefolded:
            prior = seen_casefolded[folded]
            raise ValueError(
                "duplicate JSON object field or case collision: "
                f"{prior!r} and {key!r}"
            )
        seen_casefolded[folded] = key
        result[key] = value
    return result


def _read_single_link_regular_file_snapshot(
    path: Path,
    *,
    label: str,
) -> tuple[bytes, str, int]:
    digest = hashlib.sha256()
    body = bytearray()
    try:
        with path.open("rb") as handle:
            opened_stat = os.fstat(handle.fileno())
            while chunk := handle.read(1024 * 1024):
                body.extend(chunk)
                digest.update(chunk)
            path_stat = os.lstat(path)
    except OSError as exc:
        raise WorkQueueError(f"{label} is unreadable: {path}") from exc
    if not stat.S_ISREG(opened_stat.st_mode) or not stat.S_ISREG(
        path_stat.st_mode
    ):
        raise WorkQueueError(f"{label} must be a regular file: {path}")
    if _stat_is_reparse_point(path_stat):
        raise WorkQueueError(f"{label} must not be a reparse link: {path}")
    if (
        opened_stat.st_dev,
        opened_stat.st_ino,
    ) != (
        path_stat.st_dev,
        path_stat.st_ino,
    ):
        raise WorkQueueError(f"{label} path changed while reading: {path}")
    if opened_stat.st_nlink != 1 or path_stat.st_nlink != 1:
        raise WorkQueueError(
            f"{label} must have exactly one filesystem link: {path}"
        )
    return bytes(body), digest.hexdigest(), len(body)


def _validate_claim_raw_authority_fields(
    data: Mapping[str, object],
    *,
    path: Path,
) -> None:
    mode = data.get("mode")
    if not isinstance(mode, str) or mode not in ALLOWED_MODES:
        raise WorkQueueError(
            f"claim field 'mode' must be exact and canonical: {path}"
        )

    raw_scope = data.get("write_scope")
    if not isinstance(raw_scope, list) or any(
        not isinstance(item, str) for item in raw_scope
    ):
        raise WorkQueueError(
            f"claim field 'write_scope' must be an array of strings: {path}"
        )
    try:
        normalized_scope = _normalize_write_scope_entries(
            raw_scope,
            reject_non_strings=True,
        )
    except WorkQueueError as exc:
        raise WorkQueueError(
            f"claim field 'write_scope' is malformed: {path}: {exc}"
        ) from exc
    if mode == "write" and not normalized_scope:
        raise WorkQueueError(
            f"active write claim requires a usable write_scope: {path}"
        )

    lease_seconds = data.get("lease_seconds")
    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int)
        or lease_seconds <= 0
        or lease_seconds > MAX_LEASE_SECONDS
    ):
        raise WorkQueueError(
            f"claim field 'lease_seconds' must be a positive Int32: {path}"
        )

    for field_name in ("claimed_at_utc", "last_heartbeat_utc"):
        value = data.get(field_name)
        if not isinstance(value, str):
            raise WorkQueueError(
                f"claim field {field_name!r} must be an exact string: {path}"
            )
        try:
            _parse_utc(value)
        except (AttributeError, TypeError, ValueError) as exc:
            raise WorkQueueError(
                f"claim field {field_name!r} must be canonical UTC: {path}"
            ) from exc

    if "claim_lease_expires_utc" in data:
        lease_expires = data["claim_lease_expires_utc"]
        if not isinstance(lease_expires, str):
            raise WorkQueueError(
                "claim field 'claim_lease_expires_utc' must be an exact "
                f"string: {path}"
            )
        try:
            _parse_utc(lease_expires)
        except (AttributeError, TypeError, ValueError) as exc:
            raise WorkQueueError(
                "claim field 'claim_lease_expires_utc' must be canonical "
                f"UTC: {path}"
            ) from exc

    owner_fields = (
        "owner_session_id",
        "owner_token_sha256",
        "owner_pid",
        "owner_process_start_utc",
    )
    owner_fields_present = [field in data for field in owner_fields]
    if any(owner_fields_present):
        if not all(owner_fields_present):
            raise WorkQueueError(
                f"claim owner generation fields must be complete: {path}"
            )
        owner_session_id = data["owner_session_id"]
        owner_token_sha256 = data["owner_token_sha256"]
        owner_pid = data["owner_pid"]
        owner_process_start = data["owner_process_start_utc"]
        if not isinstance(owner_session_id, str) or not (
            SESSION_ID_PATTERN.fullmatch(owner_session_id)
        ):
            raise WorkQueueError(
                f"claim field 'owner_session_id' is malformed: {path}"
            )
        if not isinstance(owner_token_sha256, str) or not (
            OWNER_TOKEN_PATTERN.fullmatch(owner_token_sha256)
        ):
            raise WorkQueueError(
                f"claim field 'owner_token_sha256' is malformed: {path}"
            )
        if (
            isinstance(owner_pid, bool)
            or not isinstance(owner_pid, int)
            or owner_pid <= 0
            or owner_pid > MAX_OWNER_PID
        ):
            raise WorkQueueError(
                f"claim field 'owner_pid' must be a positive Int32: {path}"
            )
        if not isinstance(owner_process_start, str):
            raise WorkQueueError(
                "claim field 'owner_process_start_utc' must be an exact "
                f"string: {path}"
            )
        try:
            _parse_utc(owner_process_start)
        except (AttributeError, TypeError, ValueError) as exc:
            raise WorkQueueError(
                "claim field 'owner_process_start_utc' must be canonical "
                f"UTC: {path}"
            ) from exc


def _read_claim_file_snapshot(
    path: Path,
    *,
    validate_raw_authority: bool = True,
) -> tuple[Claim, str, int]:
    try:
        raw, raw_sha256, raw_size = _read_single_link_regular_file_snapshot(
            path,
            label="active claim file",
        )
        data = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_nonfinite_json_constant,
            parse_float=_parse_finite_json_float,
            object_pairs_hook=_reject_duplicate_json_object_fields,
        )
    except WorkQueueError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise WorkQueueError(f"unreadable claim file: {path}") from exc
    if not isinstance(data, dict):
        raise WorkQueueError(f"claim file must be JSON object: {path}")
    if validate_raw_authority:
        _validate_claim_raw_authority_fields(data, path=path)
    try:
        claim = Claim(
            agent=_coerce_text(data.get("agent", "")),
            task_id=_coerce_text(data.get("task_id", "")),
            summary=_coerce_text(data.get("summary", "")),
            mode=_coerce_text(data.get("mode", "")),
            write_scope=_normalize_stored_write_scope_entries(
                data.get("write_scope", [])
            ),
            run_id=_coerce_text(data.get("run_id", "")),
            claimed_at_utc=_coerce_text(data.get("claimed_at_utc", "")),
            last_heartbeat_utc=_coerce_text(
                data.get("last_heartbeat_utc", "")
            ),
            lease_seconds=_coerce_positive_int(
                data.get("lease_seconds", 0)
            ),
            claim_lease_expires_utc=_coerce_text(
                data.get("claim_lease_expires_utc", "")
            ),
            session_id=_coerce_text(data.get("session_id", "")),
            owner_session_id=_coerce_text(
                data.get("owner_session_id", "")
            ),
            owner_token_sha256=_coerce_text(
                data.get("owner_token_sha256", "")
            ),
            owner_pid=_coerce_positive_json_int(
                data.get("owner_pid", 0),
                maximum=MAX_OWNER_PID,
            ),
            owner_process_start_utc=_coerce_text(
                data.get("owner_process_start_utc", "")
            ),
            role=_coerce_text(data.get("role", "")),
            agent_uuid=_coerce_text(data.get("agent_uuid", "")),
            capabilities=_normalize_capabilities(
                data.get("capabilities", [])
            ),
            writer_pid=_coerce_positive_int(data.get("writer_pid", 0)),
            writer_pid_semantics=_coerce_text(
                data.get("writer_pid_semantics", "")
            ),
            cwd=_coerce_text(data.get("cwd", "")),
            git_branch=_coerce_text(data.get("git_branch", "")),
        )
    except (OverflowError, TypeError, ValueError) as exc:
        raise WorkQueueError(f"invalid claim record: {path}") from exc
    return claim, raw_sha256, raw_size


def _read_claim_file(path: Path) -> Claim:
    return _read_claim_file_snapshot(path)[0]


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
        "session_id": claim.session_id,
        "owner_session_id": claim.owner_session_id,
        "owner_token_sha256": claim.owner_token_sha256,
        "owner_pid": claim.owner_pid,
        "owner_process_start_utc": claim.owner_process_start_utc,
    }
    if claim.role:
        payload["role"] = claim.role
    if claim.agent_uuid:
        payload["agent_uuid"] = claim.agent_uuid
    if claim.capabilities:
        payload["capabilities"] = list(claim.capabilities)
    if claim.writer_pid:
        payload["writer_pid"] = claim.writer_pid
    if claim.writer_pid_semantics:
        payload["writer_pid_semantics"] = claim.writer_pid_semantics
    if claim.cwd:
        payload["cwd"] = claim.cwd
    if claim.git_branch:
        payload["git_branch"] = claim.git_branch
    return payload


def _release_payload(record: ReleaseRecord) -> dict[str, object]:
    payload: dict[str, object] = {
        "agent": record.agent,
        "task_id": record.task_id,
        "summary": record.summary,
        "release_status": record.release_status,
        "release_message": record.release_message,
        "claimed_at_utc": record.claimed_at_utc,
        "released_at_utc": record.released_at_utc,
        "mode": record.mode,
        "write_scope": list(record.write_scope),
        "run_id": record.run_id,
        "last_heartbeat_utc": record.last_heartbeat_utc,
        "lease_seconds": record.lease_seconds,
        "claim_lease_expires_utc": record.claim_lease_expires_utc,
        "session_id": record.session_id,
        "owner_session_id": record.owner_session_id,
        "owner_token_sha256": record.owner_token_sha256,
        "owner_pid": record.owner_pid,
        "owner_process_start_utc": record.owner_process_start_utc,
    }
    if record.role:
        payload["role"] = record.role
    if record.agent_uuid:
        payload["agent_uuid"] = record.agent_uuid
    if record.capabilities:
        payload["capabilities"] = list(record.capabilities)
    if record.writer_pid:
        payload["writer_pid"] = record.writer_pid
    if record.writer_pid_semantics:
        payload["writer_pid_semantics"] = record.writer_pid_semantics
    if record.cwd:
        payload["cwd"] = record.cwd
    if record.git_branch:
        payload["git_branch"] = record.git_branch
    return payload


def _write_claim_file(
    path: Path,
    claim: Claim,
    *,
    create_new: bool = False,
    expected_source_sha256: str | None = None,
    expected_source_size: int | None = None,
    operation: str = "claim update",
) -> None:
    payload = _claim_payload(claim)
    if create_new:
        _write_json_file(path, payload, create_new=True)
        return
    if expected_source_sha256 is None or expected_source_size is None:
        raise WorkQueueError(
            f"{operation} requires an exact source snapshot identity"
        )
    _commit_json_against_claim_snapshot(
        source_path=path,
        destination_path=path,
        payload=payload,
        expected_source_sha256=expected_source_sha256,
        expected_source_size=expected_source_size,
        operation=operation,
    )


def _write_release_file(path: Path, record: ReleaseRecord) -> None:
    payload = _release_payload(record)
    _write_json_file(path, payload, create_new=True)


def _read_raw_file_identity(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _verify_raw_file_identity(
    path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    label: str,
) -> tuple[bool, str | None]:
    try:
        actual_sha256, actual_size = _read_raw_file_identity(path)
    except OSError as exc:
        return (
            False,
            f"{label} identity verification failed for {path}: {exc}",
        )
    if (
        actual_sha256 != expected_sha256
        or actual_size != expected_size
    ):
        return (
            False,
            f"{label} identity mismatch for {path}: "
            f"expected sha256={expected_sha256} size={expected_size}, "
            f"actual sha256={actual_sha256} size={actual_size}",
        )
    return True, None


def _prepare_json_mutation(
    payload: dict[str, object],
) -> tuple[bytes, str, int]:
    body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    return body, hashlib.sha256(body).hexdigest(), len(body)


def _restore_quarantined_claim_create_new(
    quarantine_path: Path,
    source_path: Path,
    *,
    recovery_body: bytes,
    expected_sha256: str,
    expected_size: int,
) -> tuple[bool, str]:
    """Restore captured bytes without rereading a mutable recovery pathname."""

    actual_sha256 = hashlib.sha256(recovery_body).hexdigest()
    if actual_sha256 != expected_sha256 or len(recovery_body) != expected_size:
        return (
            False,
            "active claim restore refused a changed in-memory snapshot: "
            f"expected sha256={expected_sha256} size={expected_size}, "
            f"actual sha256={actual_sha256} size={len(recovery_body)}",
        )
    try:
        _publish_prepared_file_create_new(
            recovery_body,
            source_path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            operation="active claim restore",
        )
    except WorkQueueError as exc:
        return (
            False,
            f"active claim restore failed {source_path} from "
            f"{quarantine_path}: {exc}",
        )
    return True, f"active claim restored exactly at {source_path}"


def _capture_raw_file_snapshot(path: Path) -> tuple[bytes, str, int]:
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise WorkQueueError(
            f"could not capture recovery artifact {path}: {exc}"
        ) from exc
    return body, hashlib.sha256(body).hexdigest(), len(body)


def _quarantine_exact_claim_snapshot(
    source_path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    operation: str,
) -> tuple[Path, bytes, str, int]:
    quarantine_path = source_path.with_name(
        f".{source_path.name}.mutation-quarantine.{uuid4().hex}"
    )
    try:
        _rename_file_create_new(source_path, quarantine_path)
    except Exception as exc:
        restore_state = "active claim path remained in place"
        try:
            quarantine_present = quarantine_path.is_file()
        except OSError:
            quarantine_present = True
        if quarantine_present:
            try:
                (
                    recovery_body,
                    recovery_sha256,
                    recovery_size,
                ) = _capture_raw_file_snapshot(quarantine_path)
            except WorkQueueError as recovery_error:
                restore_state = str(recovery_error)
            else:
                _, restore_state = _restore_quarantined_claim_create_new(
                    quarantine_path,
                    source_path,
                    recovery_body=recovery_body,
                    expected_sha256=recovery_sha256,
                    expected_size=recovery_size,
                )
        raise WorkQueueError(
            f"{operation} source quarantine failed for {source_path}: "
            f"{exc}; recovery state: {restore_state}"
        ) from exc

    try:
        (
            recovery_body,
            recovery_sha256,
            recovery_size,
        ) = _capture_raw_file_snapshot(quarantine_path)
    except WorkQueueError as exc:
        raise WorkQueueError(
            f"{operation} could not capture quarantined active claim; "
            f"recovery retained at {quarantine_path}: {exc}"
        ) from exc

    verified, verification_error = _verify_single_link_regular_file_identity(
        quarantine_path,
        expected_sha256=recovery_sha256,
        expected_size=recovery_size,
        label="quarantined active claim",
    )
    authorized = (
        recovery_sha256 == expected_sha256
        and recovery_size == expected_size
    )
    if not verified or not authorized:
        restored, restore_state = _restore_quarantined_claim_create_new(
            quarantine_path,
            source_path,
            recovery_body=recovery_body,
            expected_sha256=recovery_sha256,
            expected_size=recovery_size,
        )
        if authorized:
            rejection_error = verification_error
        else:
            rejection_error = (
                f"quarantined active claim identity mismatch for "
                f"{quarantine_path}: expected sha256={expected_sha256} "
                f"size={expected_size}, actual sha256={recovery_sha256} "
                f"size={recovery_size}"
            )
        raise WorkQueueError(
            (
                rejection_error
                or (
                    "quarantined active claim identity verification failed "
                    f"for {quarantine_path}"
                )
            )
            + f"; recovery state: {restore_state}; "
            + (
                "captured claim snapshot restored exactly"
                if restored
                else "captured claim snapshot was not restored; "
                "retained recovery locations are reported in the recovery state"
            )
        )
    return (
        quarantine_path,
        recovery_body,
        recovery_sha256,
        recovery_size,
    )


def _verify_single_link_regular_file_identity(
    path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    label: str,
) -> tuple[bool, str | None]:
    """Verify final bytes and reject every surviving hard-link/reparse alias."""

    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            opened_stat = os.fstat(handle.fileno())
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
            path_stat = os.lstat(path)
    except OSError as exc:
        return False, f"{label} final identity check failed for {path}: {exc}"

    if not stat.S_ISREG(opened_stat.st_mode) or not stat.S_ISREG(
        path_stat.st_mode
    ):
        return False, f"{label} is not a regular file: {path}"
    if (
        opened_stat.st_dev,
        opened_stat.st_ino,
    ) != (
        path_stat.st_dev,
        path_stat.st_ino,
    ):
        return False, f"{label} canonical path changed during verification: {path}"
    if opened_stat.st_nlink != 1 or path_stat.st_nlink != 1:
        return (
            False,
            f"{label} has a surviving hard-link alias at {path}: "
            f"opened_nlink={opened_stat.st_nlink}, "
            f"path_nlink={path_stat.st_nlink}",
        )
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256 or size != expected_size:
        return (
            False,
            f"{label} identity mismatch for {path}: "
            f"expected sha256={expected_sha256} size={expected_size}, "
            f"actual sha256={actual_sha256} size={size}",
        )
    return True, None


def _publish_prepared_file_create_new(
    prepared_body: bytes,
    destination_path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    operation: str,
) -> None:
    actual_sha256 = hashlib.sha256(prepared_body).hexdigest()
    if actual_sha256 != expected_sha256 or len(prepared_body) != expected_size:
        raise WorkQueueError(
            f"{operation} prepared mutation changed before publish: "
            f"expected sha256={expected_sha256} size={expected_size}, "
            f"actual sha256={actual_sha256} size={len(prepared_body)}"
        )

    try:
        # A direct exclusive create gives portable create-new semantics without
        # ever unlinking a mutable preparation pathname. A crash may leave a
        # partial canonical file, but it remains fail-closed and recoverable
        # alongside the exact source snapshot.
        with destination_path.open("xb") as handle:
            written = handle.write(prepared_body)
            if written != len(prepared_body):
                raise OSError(
                    f"short publish write: {written}/{len(prepared_body)} bytes"
                )
    except Exception as exc:
        raise WorkQueueError(
            f"{operation} publish failed for {destination_path}: {exc}"
        ) from exc

    published, verification_error = _verify_single_link_regular_file_identity(
        destination_path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
        label="published mutation",
    )
    if published:
        return
    recovery_state = _preserve_untrusted_publish(
        destination_path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
        operation=operation,
    )
    raise WorkQueueError(
        f"{operation} published mutation changed before final verification: "
        f"{verification_error}; {recovery_state}"
    )


def _preserve_untrusted_publish(
    destination_path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    operation: str,
) -> str:
    """Describe and preserve an ambiguous canonical publication in place."""

    still_owned, ownership_error = _verify_raw_file_identity(
        destination_path,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
        label="candidate untrusted publication",
    )
    if still_owned:
        return (
            f"{operation} ambiguous publication retained at canonical path "
            f"{destination_path}; bytes still match this writer, but portable "
            "ownership-bound removal is unavailable"
        )
    return (
        f"{operation} canonical destination was preserved because it no "
        f"longer matches this writer's publication: {ownership_error}"
    )


def _commit_json_against_claim_snapshot(
    *,
    source_path: Path,
    destination_path: Path,
    payload: dict[str, object],
    expected_source_sha256: str,
    expected_source_size: int,
    operation: str,
) -> None:
    """Quarantine one exact claim snapshot, then publish without replacement."""

    (
        prepared_body,
        expected_temp_sha256,
        expected_temp_size,
    ) = _prepare_json_mutation(payload)
    try:
        (
            quarantine_path,
            recovery_body,
            recovery_sha256,
            recovery_size,
        ) = _quarantine_exact_claim_snapshot(
            source_path,
            expected_sha256=expected_source_sha256,
            expected_size=expected_source_size,
            operation=operation,
        )
    except Exception:
        raise

    try:
        _publish_prepared_file_create_new(
            prepared_body,
            destination_path,
            expected_sha256=expected_temp_sha256,
            expected_size=expected_temp_size,
            operation=operation,
        )
    except Exception as publish_error:
        try:
            canonical_destination_present = os.path.lexists(destination_path)
        except OSError:
            canonical_destination_present = True
        if canonical_destination_present and destination_path == source_path:
            restored = False
            restore_state = (
                "publish destination remains canonical and was preserved; "
                f"exact source recovery retained at {quarantine_path}"
            )
        else:
            restored, restore_state = _restore_quarantined_claim_create_new(
                quarantine_path,
                source_path,
                recovery_body=recovery_body,
                expected_sha256=recovery_sha256,
                expected_size=recovery_size,
            )
        raise WorkQueueError(
            f"{operation} publish failed; {publish_error}; "
            f"rollback state: {restore_state}; "
            + (
                "verified original restored; source quarantine retained "
                "after exact rollback"
                if restored
                else "verified original not restored; retained recovery "
                "locations are reported in the rollback state"
            )
        ) from publish_error

    # The exact pre-mutation snapshot already has a randomized, non-canonical
    # quarantine name. Retain it after commit: unlinking it portably would
    # require a vulnerable identity-check/delete sequence.


def _write_json_file(
    path: Path,
    payload: dict[str, object],
    *,
    create_new: bool = False,
) -> None:
    if create_new:
        prepared_body, expected_sha256, expected_size = (
            _prepare_json_mutation(payload)
        )
        try:
            _publish_prepared_file_create_new(
                prepared_body,
                path,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
                operation="claim create-new",
            )
        except WorkQueueError as exc:
            if isinstance(exc.__cause__, FileExistsError):
                raise WorkQueueError(
                    f"could not create claim, likely already exists: {path}"
                ) from exc
            raise
        return

    raise WorkQueueError(
        "blind JSON overwrite refused; an exact source snapshot is required"
    )


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError("UTC timestamp must be a string")
    normalized = value
    if CANONICAL_UTC_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"UTC timestamp is not canonical: {value!r}")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
        offset = parsed.utcoffset()
        if offset is None or abs(offset) > timedelta(hours=14):
            raise ValueError(
                f"UTC timestamp offset is out of range: {value!r}"
            )
        return parsed.astimezone(timezone.utc)
    except OverflowError as exc:
        raise ValueError(
            f"UTC timestamp cannot be normalized: {value!r}"
        ) from exc


def _add_seconds_clamped(value: datetime, seconds: int) -> datetime:
    try:
        return value + timedelta(seconds=seconds)
    except OverflowError:
        return datetime.max.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
