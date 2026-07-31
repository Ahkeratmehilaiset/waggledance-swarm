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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import BinaryIO, Iterator, Sequence
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
class _ClaimExpiry:
    anchor_utc: datetime | None
    anchor_field: str
    expires_utc: datetime | None
    effective_lease_seconds: int
    legacy_tokenless: bool


@contextmanager
def _claim_mutation_lock(bridge: Path) -> Iterator[None]:
    """Serialize claim mutations across Python and PowerShell runtimes."""

    work_queue_dir = bridge / "work_queue"
    work_queue_dir.mkdir(parents=True, exist_ok=True)
    lock_path = work_queue_dir / ".claims.mutation.lock"
    handle: BinaryIO = lock_path.open("a+b")
    acquired = False
    deadline = time.monotonic() + MUTATION_LOCK_TIMEOUT_SECONDS
    try:
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
        if claim.lease_seconds > 0
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
        for scope in _normalize_write_scope_entries(write_scope)
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
    claims_dir.mkdir(parents=True, exist_ok=True)
    claim_path = _claim_path_for_task(claims_dir, task_id)

    existing: Claim | None = None
    if claim_path.exists():
        existing = _read_claim_file(claim_path)
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
    _write_claim_file(claim_path, claim, create_new=existing is None)
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
    claim_path = _claim_path_for_task(claims_dir, task_id)
    if not claim_path.exists():
        raise WorkQueueError(f"no active claim for task {task_id}")

    existing = _read_claim_file(claim_path)
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
    done_dir.mkdir(parents=True, exist_ok=True)
    done_path = done_dir / f"{_safe_name(task_id)}-{_safe_name(released_at)}.json"
    _write_release_file(done_path, record)
    try:
        claim_path.unlink()
    except OSError:
        try:
            done_path.unlink()
        except OSError:
            pass
        raise
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
    claim_path = _claim_path_for_task(bridge / "work_queue" / "claims", task_id)
    if not claim_path.exists():
        raise WorkQueueError(f"no active claim for task {task_id}")

    existing = _read_claim_file(claim_path)
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
    * Incomplete legacy claims anchor only at `claimed_at_utc`, use their
      positive stored lease when present, and ignore heartbeat/future-expiry
      fields that an unauthenticated legacy writer could extend.
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
    max_age_seconds = resolve_stale_max_seconds(max_age_seconds)
    bridge = resolve_bridge_root(bridge_root)
    now = now_utc or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    done_dir = bridge / "work_queue" / "done"
    claims = list_claims(bridge_root=bridge)
    claim_paths: dict[str, Path] = {}
    if apply:
        claims_dir = bridge / "work_queue" / "claims"
        # Resolve every logical claim before the first archive write. This
        # catches exact-task duplicates and preferred-path collisions without
        # leaving a partial batch in done/.
        for claim in claims:
            if not claim.task_id:
                continue
            claim_paths[claim.task_id] = _claim_path_for_task(
                claims_dir,
                claim.task_id,
            )

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

    for record in planned:
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
            "released_at_utc": _iso(now),
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
        done_dir.mkdir(parents=True, exist_ok=True)
        _write_json_file(record.archived_path, payload, create_new=True)
        claim_file = claim_paths[claim.task_id]
        try:
            claim_file.unlink()
        except OSError:
            try:
                record.archived_path.unlink()
            except OSError:
                pass
            raise
    return planned


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
        if claim.mode.casefold() != "write":
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


def _claim_path_for_task(claims_dir: Path, task_id: str) -> Path:
    preferred = claims_dir / f"{_safe_name(task_id)}.json"
    if not claims_dir.exists():
        return preferred
    matches: list[Path] = []
    preferred_claim: Claim | None = None
    for path in sorted(claims_dir.glob("*.json")):
        try:
            claim = _read_claim_file(path)
        except WorkQueueError:
            if path == preferred:
                raise WorkQueueError(
                    "claim filename collision at preferred path for "
                    f"task_id {task_id!r}: unreadable record {preferred}"
                )
            continue
        if path == preferred:
            preferred_claim = claim
        if claim.task_id == task_id:
            matches.append(path)
    if preferred_claim is not None and preferred_claim.task_id != task_id:
        raise WorkQueueError(
            "claim filename collision at preferred path for "
            f"task_id {task_id!r}: stored task_id "
            f"{preferred_claim.task_id!r} in {preferred}"
        )
    if len(matches) > 1:
        duplicate_paths = ", ".join(str(path) for path in matches)
        raise WorkQueueError(
            "duplicate active claim records for exact task_id "
            f"{task_id!r}: {duplicate_paths}"
        )
    if matches:
        return matches[0]
    return preferred


def _normalize_scope_entry(scope: str) -> str:
    return scope.replace("\\", "/").strip("/").lower()


def _normalize_write_scope_entries(values: object) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    if isinstance(values, str):
        source: Sequence[object] = (values,)
    elif isinstance(values, Sequence):
        source = values
    else:
        source = ()
    for value in source:
        if not isinstance(value, str):
            continue
        for item in value.split(","):
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


def _read_claim_file(path: Path) -> Claim:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkQueueError(f"unreadable claim file: {path}") from exc
    if not isinstance(data, dict):
        raise WorkQueueError(f"claim file must be JSON object: {path}")
    try:
        return Claim(
            agent=str(data.get("agent", "")),
            task_id=_coerce_text(data.get("task_id", "")),
            summary=str(data.get("summary", "")),
            mode=str(data.get("mode", "read-only")),
            write_scope=_normalize_write_scope_entries(
                data.get("write_scope", [])
            ),
            run_id=str(data.get("run_id", "")),
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
            owner_pid=_coerce_positive_int(
                data.get("owner_pid", 0),
                allow_surrounding_whitespace=False,
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


def _write_claim_file(path: Path, claim: Claim, *, create_new: bool = False) -> None:
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
    _write_json_file(path, payload, create_new=create_new)


def _write_release_file(path: Path, record: ReleaseRecord) -> None:
    payload = {
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
    _write_json_file(path, payload, create_new=True)


def _write_json_file(
    path: Path,
    payload: dict[str, object],
    *,
    create_new: bool = False,
) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if create_new:
        tmp = path.with_name(f"{path.name}.tmp.{uuid4().hex}")
        try:
            tmp.write_text(body, encoding="utf-8")
            try:
                # Publish only a fully closed sibling. A hard link provides
                # atomic create-new semantics without replacing a destination.
                os.link(tmp, path)
            except FileExistsError as exc:
                raise WorkQueueError(
                    f"could not create claim, likely already exists: {path}"
                ) from exc
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        return

    tmp = path.with_name(f"{path.name}.tmp.{uuid4().hex}")
    try:
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


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
