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


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRIDGE_ROOT = ROOT / ".agent-bridge"
DEFAULT_CLAIMS_DIR = DEFAULT_BRIDGE_ROOT / "work_queue" / "claims"
DEFAULT_DONE_DIR = DEFAULT_BRIDGE_ROOT / "work_queue" / "done"
DEFAULT_LEASE_SECONDS = 900
DEFAULT_STALE_MAX_SECONDS = 12 * 60 * 60  # 12h matches bridge-event waiver window
BRIDGE_ROOT_ENV_NAMES = ("AGENT_BRIDGE_RUNTIME_ROOT", "AGENT_BRIDGE_ROOT")

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
    if mode == "write" and not write_scope:
        raise WorkQueueError("write claims require at least one write_scope path")
    if lease_seconds <= 0:
        raise WorkQueueError("lease_seconds must be positive")

    bridge = resolve_bridge_root(bridge_root)
    claims_dir = bridge / "work_queue" / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
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
                write_scope=write_scope,
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
        write_scope=tuple(write_scope),
        run_id=run_id,
        claimed_at_utc=timestamp,
        last_heartbeat_utc=timestamp,
        lease_seconds=int(lease_seconds),
        claim_lease_expires_utc=lease_expires,
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
    _validate_task_id(task_id)
    if not release_status or not release_status.strip():
        raise WorkQueueError("release_status required")

    bridge = resolve_bridge_root(bridge_root)
    claims_dir = bridge / "work_queue" / "claims"
    done_dir = bridge / "work_queue" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
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
    _write_release_file(done_path, record)
    claim_path.unlink()
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
    _validate_task_id(task_id)
    bridge = resolve_bridge_root(bridge_root)
    claim_path = _claim_path_for_task(bridge / "work_queue" / "claims", task_id)
    if not claim_path.exists():
        raise WorkQueueError(f"no active claim for task {task_id}")

    existing = _read_claim_file(claim_path)
    if existing.agent != agent:
        raise WorkQueueError(
            f"heartbeat rejected: claim held by {existing.agent}, not {agent}"
        )

    timestamp = _iso(now_utc or datetime.now(timezone.utc))
    refreshed_lease_seconds = (
        int(lease_seconds) if lease_seconds else existing.lease_seconds
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
    archived: list[ArchivedClaim] = []
    for claim in list_claims(bridge_root=bridge):
        if claim.agent in PRIVILEGED_AGENTS:
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
            _write_json_file(archive_path, payload, create_new=True)
            claim_file = bridge / "work_queue" / "claims" / f"{safe_task}.json"
            try:
                claim_file.unlink()
            except FileNotFoundError:
                pass
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
    if not write_scope:
        return []
    normalized_request = {_normalize_scope_entry(s) for s in write_scope if s}
    if not normalized_request:
        return []
    overlapping: list[Claim] = []
    for claim in list_claims(bridge_root=bridge_root):
        if claim.mode != "write":
            continue
        existing_scope = {_normalize_scope_entry(s) for s in claim.write_scope if s}
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
    if preferred.exists():
        return preferred
    if not claims_dir.exists():
        return preferred
    for path in sorted(claims_dir.glob("*.json")):
        try:
            claim = _read_claim_file(path)
        except WorkQueueError:
            continue
        if claim.task_id == task_id:
            return path
    return preferred


def _normalize_scope_entry(scope: str) -> str:
    return scope.replace("\\", "/").strip("/").lower()


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
    return Claim(
        agent=str(data.get("agent", "")),
        task_id=str(data.get("task_id", "")),
        summary=str(data.get("summary", "")),
        mode=str(data.get("mode", "read-only")),
        write_scope=tuple(str(s) for s in data.get("write_scope", []) if s),
        run_id=str(data.get("run_id", "")),
        claimed_at_utc=str(data.get("claimed_at_utc", "")),
        last_heartbeat_utc=str(data.get("last_heartbeat_utc", "")),
        lease_seconds=int(data.get("lease_seconds", DEFAULT_LEASE_SECONDS)),
        claim_lease_expires_utc=str(data.get("claim_lease_expires_utc", "")),
        role=str(data.get("role", "")),
        agent_uuid=str(data.get("agent_uuid", "")),
        capabilities=tuple(str(s) for s in data.get("capabilities", []) if s),
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
    }
    if claim.role:
        payload["role"] = claim.role
    if claim.agent_uuid:
        payload["agent_uuid"] = claim.agent_uuid
    if claim.capabilities:
        payload["capabilities"] = list(claim.capabilities)
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
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
