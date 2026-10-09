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
  "lease_seconds": 900
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

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Sequence
from uuid import uuid4
from waggledance.core.bridge_resource_scope import resolve_resources, resources_overlap


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRIDGE_ROOT = ROOT / ".agent-bridge"
DEFAULT_CLAIMS_DIR = DEFAULT_BRIDGE_ROOT / "work_queue" / "claims"
DEFAULT_DONE_DIR = DEFAULT_BRIDGE_ROOT / "work_queue" / "done"
DEFAULT_LEASE_SECONDS = 900
DEFAULT_STALE_MAX_SECONDS = 12 * 60 * 60  # 12h matches bridge-event waiver window
BRIDGE_ROOT_ENV_NAMES = ("AGENT_BRIDGE_RUNTIME_ROOT", "AGENT_BRIDGE_ROOT")
OWNER_SESSION_ENV = "AGENT_BRIDGE_OWNER_SESSION_ID"
RUN_ID_ENV = "AGENT_BRIDGE_RUN_ID"
OWNER_TOKEN_ENV = "AGENT_BRIDGE_OWNER_TOKEN"
BOUND_AGENT_ENV = "AGENT_BRIDGE_AGENT"
# Marker Claim-AgentTask.ps1 writes on a claim made without an identity.
OWNER_IDENTITY_NONE = "none"
# Session heartbeat TTLs, as in ClaimLeaseHeartbeat.ps1.
SESSION_HEARTBEAT_TTL_DEFAULT = 180
SESSION_HEARTBEAT_TTL_MAX = 900

AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,120}$")
ALLOWED_MODES = ("read-only", "write")


class WorkQueueError(ValueError):
    """Recoverable work-queue contract violation."""


class WorkQueueIOError(OSError):
    """QB-L1 (RCO2 FF7BC9A6): a writer's own I/O failure, with what is known about its effects. An OSError subclass,
    so every ``except OSError`` keeps working; raised ``from`` the original error, whose errno/strerror/filename it
    keeps. Never retried here.

    * ``applied``: True = a change landed and stays; False = PROVEN nothing of this call is left behind;
      None = unknown (a rollback failed or a write may be partial).
    * ``completed``: the sweep's ArchivedClaim records fully applied before the failure ([] elsewhere).
    * ``rollback_errors``: one "Type: text" per failed rollback step.
    * ``residual``: paths whose state is unknown or left behind.
    """

    def __init__(self, cause: OSError, *, applied: bool | None, completed: Sequence[object] = (),
                 rollback_errors: Sequence[str] = (), residual: Sequence[str] = ()) -> None:
        super().__init__(*cause.args)
        for name in ("errno", "strerror", "filename", "filename2", "winerror"):   # Grok 5321c511 #4: keep them all
            if getattr(cause, name, None) is not None:   # a None set explicitly would change str(self)
                setattr(self, name, getattr(cause, name))
        self.applied = applied
        self.completed = list(completed)
        self.rollback_errors = list(rollback_errors)
        self.residual = list(residual)


def _undo_record(error: OSError, record: Path, completed: Sequence[object] = ()) -> WorkQueueIOError:
    """QB-L1: remove the record THIS call just created, because the claim it describes could not be removed; the
    claim stays active and no done record claims otherwise. A failed undo leaves the outcome unknown (applied=None)."""
    try:
        record.unlink()
    except OSError as undo:
        return WorkQueueIOError(error, applied=None, completed=completed,
                                rollback_errors=[f"{type(undo).__name__}: {undo}"[:300]], residual=[str(record)])
    return WorkQueueIOError(error, applied=bool(completed), completed=completed)


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
    cwd: str = ""
    owner_session_id: str = ""
    owner_token_sha256: str = ""
    owner_identity: str = ""


@dataclass(frozen=True)
class OwnerIdentity:
    """The session identity a B7 claim is bound to.

    Same contract as ``Get-BridgeOwnerIdentity`` in
    ``.agent-bridge/bin/ClaimLeaseHeartbeat.ps1``: the owner session id and
    the SHA-256 of the per-session owner token, never the token itself.
    """

    owner_session_id: str
    owner_token_sha256: str


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


def current_owner_identity(
    environ: dict[str, str] | None = None,
) -> OwnerIdentity | None:
    """Resolve this process's B7 owner identity, or None.

    Mirrors ``Get-BridgeOwnerIdentity``: the owner session id wins over the
    run id, and when both are set but disagree there is no identity at all.
    None means "cannot act on an owned claim"; callers must not invent a
    weaker identity.
    """
    env = os.environ if environ is None else environ
    owner_session = env.get(OWNER_SESSION_ENV, "")
    run_session = env.get(RUN_ID_ENV, "")
    if owner_session and run_session and owner_session != run_session:
        session = ""
    else:
        session = owner_session or run_session
    token = env.get(OWNER_TOKEN_ENV, "")
    if not session or not token:
        return None
    return OwnerIdentity(
        owner_session_id=session,
        owner_token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
    )


def _assert_session_agent(agent: str, environ: dict[str, str] | None = None) -> None:
    """Refuse to act under a label this session is not bound to.

    Mirrors ``Assert-AgentBridgeSessionIdentity`` (AgentBridgeSessionIdentity.ps1)
    for the public entry points: a session bound through AGENT_BRIDGE_AGENT
    acts only as that agent, ``system`` has no public authority, and the
    reserved ``operator``/``system`` labels are refused to an unbound caller.
    """
    env = os.environ if environ is None else environ
    bound = env.get(BOUND_AGENT_ENV, "")
    if not bound:
        if agent in PRIVILEGED_AGENTS:
            raise WorkQueueError(
                f"identity_mismatch: reserved agent {agent!r} is refused "
                "without a bound session for it"
            )
        return
    if not AGENT_ID_PATTERN.fullmatch(bound):
        raise WorkQueueError(f"identity_mismatch: {BOUND_AGENT_ENV} is malformed")
    if agent == "system":
        raise WorkQueueError("identity_mismatch: system agent has no public bridge authority")
    if bound != agent:
        raise WorkQueueError(
            f"identity_mismatch: session agent {bound!r} cannot act as {agent!r}"
        )


def _claim_is_owned(claim: Claim) -> bool:
    return bool(claim.owner_session_id and claim.owner_token_sha256)


def _identity_owns(claim: Claim, identity: OwnerIdentity | None) -> bool:
    """The identity half of the B7 compare-and-swap (``Test-BridgeClaimOwner``)."""
    if identity is None or not _claim_is_owned(claim):
        return False
    return (
        claim.owner_session_id == identity.owner_session_id
        and claim.owner_token_sha256 == identity.owner_token_sha256
    )


def _identityless_pair(claim: Claim, identity: OwnerIdentity | None) -> bool:
    """A claim made without an identity, handled by a caller with none.

    The agent label is the only authority either side has, exactly as
    before B7. Any other unowned claim is a pre-B7 claim and is never
    adopted implicitly.
    """
    return (
        identity is None
        and not _claim_is_owned(claim)
        and claim.owner_identity == OWNER_IDENTITY_NONE
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
    _validate_task_id(task_id)
    if not summary or not summary.strip():
        raise WorkQueueError("summary required")
    if mode not in ALLOWED_MODES:
        raise WorkQueueError(f"mode must be one of {ALLOWED_MODES}, got {mode!r}")
    normalized_write_scope = _normalize_write_scope_entries(write_scope)
    try:
        resolve_resources(normalized_write_scope, cwd=str(Path.cwd()), bridge_root=str(resolve_bridge_root(bridge_root)))
    except (OSError, ValueError) as exc:
        raise WorkQueueError(str(exc)) from exc
    if mode == "write" and not normalized_write_scope:
        raise WorkQueueError("write claims require at least one write_scope path")
    if lease_seconds <= 0:
        raise WorkQueueError("lease_seconds must be positive")

    _assert_session_agent(agent)
    identity = current_owner_identity()

    bridge = resolve_bridge_root(bridge_root)
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)

    existing: Claim | None = None
    found = _find_claim(claims_dir, task_id)
    if found is not None:
        claim_path, existing = found
        if existing.agent != agent and not force:
            raise WorkQueueError(
                f"task {task_id} already claimed by {existing.agent}"
            )
        if existing.agent != agent and force:
            raise WorkQueueError(
                f"force claim across agents refused: existing={existing.agent}"
            )
        # B7: the label is not authority. Only the owning session refreshes
        # its claim; there is no takeover of a live owner, with or without
        # force. A claim held by another session frees only through its
        # owner's release or the stale sweep.
        if not (_identity_owns(existing, identity) or _identityless_pair(existing, identity)):
            raise WorkQueueError(
                f"claim refused: task {task_id} is held by another session of "
                f"{existing.agent}; only the owning session can refresh it"
            )
    else:
        claim_path = _new_claim_path(claims_dir, task_id)
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
    lease_expires = _iso(_parse_utc(timestamp) + timedelta(seconds=int(lease_seconds)))
    claim = Claim(
        agent=agent,
        task_id=task_id,
        summary=summary.strip(),
        mode=mode,
        write_scope=normalized_write_scope,
        run_id=run_id,
        claimed_at_utc=timestamp,
        last_heartbeat_utc=timestamp,
        lease_seconds=int(lease_seconds),
        claim_lease_expires_utc=lease_expires,
        cwd=str(Path.cwd()),
        owner_session_id=identity.owner_session_id if identity else "",
        owner_token_sha256=identity.owner_token_sha256 if identity else "",
        owner_identity="" if identity else OWNER_IDENTITY_NONE,
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
    allow_legacy_unowned_claim: bool = False,
) -> ReleaseRecord:
    """Release a previously claimed task and archive the record under done/.

    B7 parity with ``Release-AgentTask.ps1``: an owned claim is released
    only by its owning session; a claim marked ``owner_identity=none`` by an
    identity-less caller; any other unowned (pre-B7) claim only with
    ``allow_legacy_unowned_claim=True``. An owned claim is never released
    by anyone else, whatever the flag.
    """
    _validate_agent(agent)
    _validate_task_id(task_id)
    if not release_status or not release_status.strip():
        raise WorkQueueError("release_status required")
    _assert_session_agent(agent)
    identity = current_owner_identity()

    bridge = resolve_bridge_root(bridge_root)
    claims_dir = bridge / "work_queue" / "claims"
    done_dir = bridge / "work_queue" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    found = _find_claim(claims_dir, task_id)
    if found is None:
        raise WorkQueueError(f"no active claim for task {task_id}")

    claim_path, existing = found
    if existing.agent != agent:
        raise WorkQueueError(
            f"release rejected: claim held by {existing.agent}, not {agent}"
        )
    if _claim_is_owned(existing):
        if not _identity_owns(existing, identity):
            raise WorkQueueError(
                "release rejected: claim is owned by another session "
                "(owner_session_id/owner_token mismatch); only the owning "
                "session can release it"
            )
    elif not _identityless_pair(existing, identity) and not allow_legacy_unowned_claim:
        raise WorkQueueError(
            "release rejected: claim carries no owner identity (pre-B7 claim); "
            "adopt it explicitly with allow_legacy_unowned_claim"
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
    _write_release_file(done_path, record)
    try:
        claim_path.unlink()
    except FileNotFoundError:   # removed meanwhile: the done record stands, as in the sweep (Grok 5321c511 #1)
        pass
    except OSError as error:   # QB-L1-b: no done record beside a still-active claim
        raise _undo_record(error, done_path) from error
    return record


def heartbeat(
    *,
    agent: str,
    task_id: str,
    bridge_root: Path | None = None,
    now_utc: datetime | None = None,
    lease_seconds: int | None = None,
) -> Claim:
    """Refresh the lease on an existing claim.

    B7 parity with ``Update-BridgeClaimLease``: only the owning session
    extends a lease. A claim without an owner identity is never extended; it
    ages out normally. Every other field of the claim file, including the
    owner fields and anything only the PowerShell writer records, is kept.
    """
    _validate_agent(agent)
    _validate_task_id(task_id)
    _assert_session_agent(agent)
    identity = current_owner_identity()
    bridge = resolve_bridge_root(bridge_root)
    found = _find_claim(bridge / "work_queue" / "claims", task_id)
    if found is None:
        raise WorkQueueError(f"no active claim for task {task_id}")

    claim_path, existing = found
    if existing.agent != agent:
        raise WorkQueueError(
            f"heartbeat rejected: claim held by {existing.agent}, not {agent}"
        )
    if not _identity_owns(existing, identity):
        raise WorkQueueError(
            "heartbeat rejected: only the owning session extends a lease "
            "(owner_session_id/owner_token mismatch, or a claim without an "
            "owner identity, which ages out)"
        )

    timestamp = _iso(now_utc or datetime.now(timezone.utc))
    refreshed_lease_seconds = (
        int(lease_seconds) if lease_seconds else existing.lease_seconds
    )
    lease_expires = _iso(
        _parse_utc(timestamp) + timedelta(seconds=refreshed_lease_seconds)
    )
    raw = _read_claim_object(claim_path)
    raw["last_heartbeat_utc"] = timestamp
    raw["lease_seconds"] = refreshed_lease_seconds
    raw["claim_lease_expires_utc"] = lease_expires
    refreshed = _claim_from_object(raw)
    if (refreshed.task_id != task_id or not _identity_owns(refreshed, identity)):
        # Re-checked on the content actually being rewritten: the file may
        # have been replaced by a successor claim since the lookup.
        raise WorkQueueError(
            f"heartbeat rejected: claim for task {task_id} changed during the update"
        )
    _write_json_file(claim_path, raw)
    return refreshed


def list_claims(bridge_root: Path | None = None) -> list[Claim]:
    """Return all active claims in the work-queue."""
    bridge = resolve_bridge_root(bridge_root)
    return [claim for _, claim in _list_claim_entries(bridge / "work_queue" / "claims")]


def detect_stale_claims(
    *,
    bridge_root: Path | None = None,
    now_utc: datetime | None = None,
    max_age_seconds: int = DEFAULT_STALE_MAX_SECONDS,
) -> list[Claim]:
    """Return claims whose last heartbeat is older than max_age_seconds."""
    cutoff = (now_utc or datetime.now(timezone.utc)) - timedelta(seconds=max_age_seconds)
    stale: list[Claim] = []
    for claim in list_claims(bridge_root=bridge_root):
        try:
            last = _parse_utc(claim.last_heartbeat_utc)
        except (ValueError, TypeError):
            stale.append(claim)
            continue
        if last < cutoff:
            stale.append(claim)
    return stale


PRIVILEGED_AGENTS = frozenset({"operator", "system"})


def archive_stale_claims(
    *,
    bridge_root: Path | None = None,
    now_utc: datetime | None = None,
    max_age_seconds: int = DEFAULT_STALE_MAX_SECONDS,
    apply: bool = False,
) -> list[ArchivedClaim]:
    """Sweep stale claims; dry-run unless apply=True.

    Parity with `.agent-bridge/bin/Invoke-StaleClaimSweep.ps1`:

    * Claims whose `last_heartbeat_utc` (falling back to `claimed_at_utc`)
      is older than ``max_age_seconds`` relative to ``now_utc`` are
      candidates.
    * Claims owned by ``operator`` or ``system`` are never swept.
    * Each swept claim is archived to
      ``work_queue/done/<safe_task>.<utc_stamp>.stale_lease.json`` with
      ``release_status="stale_lease"``, ``release_reason`` describing the
      lease age, and ``released_at_utc`` set to ``now_utc``.
    * With ``apply=False`` (the default) no files are moved or written;
      the returned ``ArchivedClaim`` records describe the planned action.
    * B7: a claim bound to an owner session (``owner_session_id`` plus
      ``owner_token_sha256``) is swept only when its own lease has expired
      AND its owner's session heartbeat is provably not live, as in
      ``Invoke-StaleClaimSweep.ps1``. A heartbeat artifact that exists but
      cannot be read or validated counts as "cannot prove the owner is
      gone", and the claim is kept.

    The primitive intentionally does not emit bridge events; the CLI
    wrapper in ``tools/work_queue_sweep_stale.py`` is responsible for
    observability.
    """
    if max_age_seconds <= 0:
        raise WorkQueueError(
            f"max_age_seconds must be positive, got {max_age_seconds}"
        )
    bridge = resolve_bridge_root(bridge_root)
    now = now_utc or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=max_age_seconds)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    done_dir = bridge / "work_queue" / "done"
    claims_dir = bridge / "work_queue" / "claims"
    archived: list[ArchivedClaim] = []
    for claim_file, claim in _list_claim_entries(claims_dir):
        if claim.agent in PRIVILEGED_AGENTS:
            continue
        if _claim_is_owned(claim) and not _owned_claim_sweepable(bridge, claim, now):
            continue
        candidates: list[str] = []
        if claim.last_heartbeat_utc:
            candidates.append(claim.last_heartbeat_utc)
        if claim.claimed_at_utc and claim.claimed_at_utc not in candidates:
            candidates.append(claim.claimed_at_utc)
        if not candidates:
            continue
        last: datetime | None = None
        for candidate in candidates:
            try:
                last = _parse_utc(candidate)
                break
            except (ValueError, TypeError):
                continue
        if last is None:
            age_seconds = max_age_seconds
        else:
            if last >= cutoff:
                continue
            age_seconds = int((now - last).total_seconds())

        safe_task = _safe_name(claim.task_id)
        if not safe_task:
            continue
        archive_path = done_dir / f"{safe_task}.{stamp}.stale_lease.json"
        reason = (
            f"last_heartbeat_utc was {age_seconds}s old; "
            f"lease threshold {max_age_seconds}s"
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
            if claim.owner_session_id:
                payload["owner_session_id"] = claim.owner_session_id
            if claim.owner_token_sha256:
                payload["owner_token_sha256"] = claim.owner_token_sha256
            # Remove exactly the file this decision was made on, and only if
            # it still holds the same claim: a successor claim written since
            # the listing is never deleted.
            try:
                current = _read_claim_file(claim_file)
            except WorkQueueError:
                continue
            if current != claim:
                continue
            # QB-L1-a: a failure here keeps the archives already completed, and never deletes a file this call did
            # not create (an existing archive is a collision, untouched; a partial write is reported, not removed).
            try:
                _write_json_file(archive_path, payload, create_new=True)
            except WorkQueueError as error:
                if not isinstance(error.__cause__, FileExistsError):
                    raise
                raise WorkQueueIOError(error.__cause__, applied=bool(archived), completed=archived) from error
            except OSError as error:   # a partial write: only a definite "absent" proves nothing was left
                try:
                    archive_path.lstat()
                except FileNotFoundError:
                    raise WorkQueueIOError(error, applied=bool(archived), completed=archived) from error
                except OSError:
                    pass
                raise WorkQueueIOError(error, applied=None, completed=archived, residual=[str(archive_path)]) from error
            try:
                claim_file.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                raise _undo_record(error, archive_path, archived) from error
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
    try:
        normalized_request = resolve_resources(normalized_scope, cwd=str(Path.cwd()), bridge_root=str(resolve_bridge_root(bridge_root)))
    except (OSError, ValueError) as exc:
        raise WorkQueueError(str(exc)) from exc
    if not normalized_request:
        return []
    overlapping: list[Claim] = []
    for claim in list_claims(bridge_root=bridge_root):
        if claim.mode != "write":
            continue
        try:
            existing_scope = resolve_resources(claim.write_scope, cwd=claim.cwd, bridge_root=str(resolve_bridge_root(bridge_root)))
        except (OSError, ValueError) as exc:
            raise WorkQueueError(f"unverifiable resource scope in {claim.task_id}: {exc}") from exc
        if any(
            resources_overlap(existing, requested)
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


def _list_claim_entries(claims_dir: Path) -> list[tuple[Path, Claim]]:
    if not claims_dir.exists():
        return []
    entries: list[tuple[Path, Claim]] = []
    for path in sorted(claims_dir.glob("*.json")):
        try:
            entries.append((path, _read_claim_file(path)))
        except WorkQueueError:
            continue
    return entries


def _find_claim(claims_dir: Path, task_id: str) -> tuple[Path, Claim] | None:
    """The active claim whose task_id is EXACTLY ``task_id``, or None.

    A file name is never trusted: the PowerShell writer names files by a
    lossy sanitization (``a/b`` and ``a_b`` both give ``a_b.json``), so the
    preferred name can hold a different task's claim.
    """
    preferred = claims_dir / f"{_safe_name(task_id)}.json"
    if preferred.exists():
        try:
            claim = _read_claim_file(preferred)
        except WorkQueueError:
            claim = None
        if claim is not None and claim.task_id == task_id:
            return preferred, claim
    for path, claim in _list_claim_entries(claims_dir):
        if claim.task_id == task_id:
            return path, claim
    return None


def _new_claim_path(claims_dir: Path, task_id: str) -> Path:
    """A file name for a new claim that no other task's claim occupies."""
    preferred = claims_dir / f"{_safe_name(task_id)}.json"
    if not preferred.exists():
        return preferred
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]
    base = re.sub(r"[^A-Za-z0-9._-]", "_", task_id).strip("_") or "claim"
    return claims_dir / f"{base}-{digest}.json"


def _claim_path_for_task(claims_dir: Path, task_id: str) -> Path:
    found = _find_claim(claims_dir, task_id)
    if found is not None:
        return found[0]
    return _new_claim_path(claims_dir, task_id)


def _session_heartbeat_path(bridge: Path, session_id: str, token_sha256: str) -> Path | None:
    """``Get-BridgeSessionHeartbeatPath``: keyed by session AND token hash."""
    if not session_id or not token_sha256:
        return None
    digest = hashlib.sha256(f"{session_id}\n{token_sha256}".encode("utf-8")).hexdigest()
    return bridge / "work_queue" / "heartbeats" / f"{digest}.json"


def _session_heartbeat_state(bridge: Path, claim: Claim, now: datetime) -> str:
    """'live', 'dead' or 'unknown' for the session that owns ``claim``.

    Same rules as ``Test-BridgeSessionHeartbeatLive``, except that an
    artifact which exists but cannot be read or validated is 'unknown'
    rather than 'dead': this sweeper keeps a claim it cannot prove
    abandoned.
    """
    path = _session_heartbeat_path(bridge, claim.owner_session_id, claim.owner_token_sha256)
    if path is None or not path.exists():
        return "dead"
    try:
        beat = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "unknown"
    if not isinstance(beat, dict):
        return "unknown"
    if any(field_name not in beat for field_name in ("owner_session_id", "owner_token_sha256", "last_beat_utc")):
        return "unknown"
    if (str(beat["owner_session_id"]) != claim.owner_session_id
            or str(beat["owner_token_sha256"]) != claim.owner_token_sha256):
        return "unknown"
    ttl = SESSION_HEARTBEAT_TTL_DEFAULT
    try:
        parsed_ttl = int(str(beat.get("ttl_seconds", "")))
    except ValueError:
        parsed_ttl = 0
    if parsed_ttl > 0:
        ttl = parsed_ttl
    ttl = min(ttl, SESSION_HEARTBEAT_TTL_MAX)
    try:
        beat_utc = _parse_utc(str(beat["last_beat_utc"]))
    except (ValueError, TypeError):
        return "unknown"
    # A future-dated beat never keeps a claim alive (clock skew is not a
    # way to pin a claim open), as in PowerShell.
    if beat_utc > now + timedelta(seconds=60):
        return "dead"
    return "live" if (now - beat_utc).total_seconds() <= ttl else "dead"


def _owned_claim_sweepable(bridge: Path, claim: Claim, now: datetime) -> bool:
    """An owner-bound claim is sweepable only when its lease expired AND its
    owner's session heartbeat is provably not live."""
    try:
        base = _parse_utc(claim.last_heartbeat_utc or claim.claimed_at_utc)
    except (ValueError, TypeError):
        return False
    expires = base + timedelta(seconds=max(int(claim.lease_seconds), 1))
    if claim.claim_lease_expires_utc:
        try:
            recorded = _parse_utc(claim.claim_lease_expires_utc)
        except (ValueError, TypeError):
            return False
        if recorded > expires:
            expires = recorded
    if now < expires:
        return False
    return _session_heartbeat_state(bridge, claim, now) == "dead"


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


def _read_claim_object(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkQueueError(f"unreadable claim file: {path}") from exc
    if not isinstance(data, dict):
        raise WorkQueueError(f"claim file must be JSON object: {path}")
    return data


def _read_claim_file(path: Path) -> Claim:
    return _claim_from_object(_read_claim_object(path))


def _claim_from_object(data: dict[str, object]) -> Claim:
    return Claim(
        agent=str(data.get("agent", "")),
        task_id=str(data.get("task_id", "")),
        summary=str(data.get("summary", "")),
        mode=str(data.get("mode", "read-only")),
        write_scope=_normalize_write_scope_entries(data.get("write_scope", [])),
        run_id=str(data.get("run_id", "")),
        claimed_at_utc=str(data.get("claimed_at_utc", "")),
        last_heartbeat_utc=str(data.get("last_heartbeat_utc", "")),
        lease_seconds=int(data.get("lease_seconds", DEFAULT_LEASE_SECONDS)),
        claim_lease_expires_utc=str(data.get("claim_lease_expires_utc", "")),
        role=str(data.get("role", "")),
        agent_uuid=str(data.get("agent_uuid", "")),
        capabilities=tuple(str(s) for s in data.get("capabilities", []) if s),
        cwd=str(data.get("cwd", "")),
        owner_session_id=str(data.get("owner_session_id", "") or ""),
        owner_token_sha256=str(data.get("owner_token_sha256", "") or ""),
        owner_identity=str(data.get("owner_identity", "") or ""),
    )


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
        "cwd": claim.cwd,
    }
    if claim.role:
        payload["role"] = claim.role
    if claim.agent_uuid:
        payload["agent_uuid"] = claim.agent_uuid
    if claim.capabilities:
        payload["capabilities"] = list(claim.capabilities)
    if claim.owner_session_id and claim.owner_token_sha256:
        payload["owner_session_id"] = claim.owner_session_id
        payload["owner_token_sha256"] = claim.owner_token_sha256
    elif claim.owner_identity:
        payload["owner_identity"] = claim.owner_identity
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
    }
    _write_json_file(path, payload, create_new=True)


def _write_json_file(
    path: Path,
    payload: dict[str, object],
    *,
    create_new: bool = False,
) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if create_new:
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(body)
        except FileExistsError as exc:
            raise WorkQueueError(
                f"could not create claim, likely already exists: {path}"
            ) from exc
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
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc)
    except OverflowError:   # year 1 at +14:00 (or 9999 at -14:00) leaves the datetime range: invalid, not a crash
        raise ValueError("timestamp is outside the representable UTC range") from None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
