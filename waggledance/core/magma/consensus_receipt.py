# SPDX-License-Identifier: BUSL-1.1
"""Consensus-receipt construction and verification helpers for bridge-gated merges."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from waggledance.core.magma.canonical import sha256_digest


SCHEMA_VERSION = "magma.bridge_consensus_receipt.v1"
DECISION_EVENT_TYPES = frozenset({"decision", "rco_review", "finding"})
BUILD_CONSENSUS_STATUSES = frozenset(
    {
        "approved",
        "build_consensus",
        "build_consensus_pass",
        "concur",
        "concurred",
        "agree",
        "agreed",
    }
)
RCO_PASS_STATUSES = frozenset(
    {"rco_pass", "rco_pass_operator_merge_required", "rco_pass_pending_ci"}
)
CONSENSUS_BLOCKING_STATUSES = frozenset(
    {"changes_requested", "rco_block", "blocked", "rco_blocked", "block_requested"}
)
CONSENSUS_BLOCKING_NEGATION_TOKENS = frozenset({"no", "not", "non", "none", "without"})
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
EVENT_IDENTITY_KEYS = (
    "build_lead",
    "build_tools",
    "rco",
)
LEAD_AGENT = "codex-lead-1"
TOOLS_AGENT = "codex-tools-1"
RCO_AGENT = "claude-rco-1"


def build_bridge_consensus_receipt(
    *,
    schema_version: str,
    pr_number: int,
    head_sha: str,
    merge_commit_sha: str,
    build_lead: Mapping[str, str],
    build_tools: Mapping[str, str],
    rco: Mapping[str, str],
    bridge_consensus_verdict_snapshot: Mapping[str, Any],
    charter_path: str | Path,
    charter_digest: str,
    ci_status_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a canonicalized consensus receipt payload."""
    if not SHA_RE.fullmatch(head_sha):
        raise ValueError("head_sha must be a 40-char lowercase SHA")
    if merge_commit_sha and not SHA_RE.fullmatch(merge_commit_sha):
        raise ValueError("merge_commit_sha must be empty or a 40-char lowercase SHA")
    if not isinstance(pr_number, int) or pr_number <= 0:
        raise ValueError("pr_number must be a positive integer")
    if not isinstance(bridge_consensus_verdict_snapshot, Mapping):
        raise ValueError("bridge_consensus_verdict_snapshot must be an object")
    if not isinstance(ci_status_snapshot, Mapping):
        raise ValueError("ci_status_snapshot must be an object")
    if not DIGEST_RE.fullmatch(charter_digest):
        raise ValueError("charter_digest must be sha256:<hex>")

    identities = {
        "build_lead": _coerce_identity(build_lead),
        "build_tools": _coerce_identity(build_tools),
        "rco": _coerce_identity(rco),
    }
    _validate_distinct_agents(identities)

    receipt: dict[str, Any] = {
        "schema_version": str(schema_version).strip(),
        "pr_number": pr_number,
        "head_sha": head_sha,
        "merge_commit_sha": merge_commit_sha,
        "build_lead": identities["build_lead"],
        "build_tools": identities["build_tools"],
        "rco": identities["rco"],
        "bridge_consensus_verdict_snapshot": dict(bridge_consensus_verdict_snapshot),
        "charter_path": str(charter_path),
        "charter_digest": charter_digest,
        "ci_status_snapshot": dict(ci_status_snapshot),
    }
    if not receipt["schema_version"]:
        raise ValueError("schema_version must be non-empty")
    receipt["canonical_digest"] = _canonical_digest(receipt)
    return receipt


def verify_bridge_consensus_receipt(
    receipt: Mapping[str, Any],
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    pr_number: int | None = None,
    charter_path: str | Path,
    require_merge_commit_sha: bool = False,
) -> dict[str, Any]:
    """Re-derive bridge consensus and verify the stored receipt snapshot."""
    errors: list[str] = []
    if not isinstance(receipt, Mapping):
        return _verify_result(False, ["receipt must be an object"], {}, None)

    try:
        _validate_charter_path(Path(charter_path))
        expected_charter_digest = _compute_charter_digest(charter_path)
    except (OSError, ValueError) as exc:
        return _verify_result(
            False,
            [f"charter digest validation failed: {exc}"],
            {},
            None,
        )

    payload = _coerce_nonempty_string(receipt.get("schema_version"), "schema_version")
    if payload is None:
        return _verify_result(False, ["schema_version must be a non-empty string"], {}, None)
    if payload != SCHEMA_VERSION:
        errors.append(f"schema_version mismatch: {payload}")

    pr_number_value = receipt.get("pr_number")
    if pr_number_value != pr_number:
        return _verify_result(
            False,
            ["receipt pr_number does not match provided pr_number"],
            {},
            None,
        )
    head_sha = _coerce_sha(receipt.get("head_sha"), "head_sha")
    if head_sha is None:
        errors.append("head_sha must be a 40-char lowercase sha")

    merge_commit_sha = str(receipt.get("merge_commit_sha", ""))
    if require_merge_commit_sha and merge_commit_sha and not SHA_RE.fullmatch(merge_commit_sha):
        errors.append("merge_commit_sha must be a 40-char lowercase sha")

    consensus_snapshot = receipt.get("bridge_consensus_verdict_snapshot")
    if not isinstance(consensus_snapshot, Mapping):
        return _verify_result(False, ["bridge_consensus_verdict_snapshot must be an object"])
    canonical_digest = str(receipt.get("canonical_digest", ""))
    if not DIGEST_RE.fullmatch(canonical_digest):
        return _verify_result(False, ["canonical_digest must be sha256:<hex>"])
    snapshot = {
        k: receipt[k]
        for k in (
            "schema_version",
            "pr_number",
            "head_sha",
            "merge_commit_sha",
            "build_lead",
            "build_tools",
            "rco",
            "bridge_consensus_verdict_snapshot",
            "charter_path",
            "charter_digest",
            "ci_status_snapshot",
        )
    }
    if _canonical_digest(snapshot) != canonical_digest:
        return _verify_result(False, ["canonical_digest mismatch"])

    if receipt.get("charter_digest") != expected_charter_digest:
        return _verify_result(False, ["charter_digest does not match live charter digest"])

    try:
        live_snapshot = evaluate_bridge_consensus(
            events=events,
            task_id=task_id,
            head_sha=head_sha,
            pr_number=pr_number,
        )
    except ValueError as exc:
        return _verify_result(False, [f"event scope/head invalid: {exc}"])

    if json.dumps(live_snapshot, sort_keys=True) != json.dumps(
        consensus_snapshot,
        sort_keys=True,
    ):
        errors.append("bridge_consensus_verdict_snapshot does not match live verdict")

    if not live_snapshot.get("ok"):
        errors.append("live bridge consensus is not verified")

    identities = {}
    for role in EVENT_IDENTITY_KEYS:
        identity = _coerce_identity(receipt.get(role, {}))
        if identity is None:
            errors.append(f"{role} identity must include agent, event_ts, event_digest")
            identities[role] = None
            continue
        identities[role] = identity

    _validate_distinct_agents(identities)
    event_map = _consensus_identity_events(
        events=events,
        task_id=task_id,
        head_sha=head_sha,
        pr_number=pr_number,
    )
    for role in EVENT_IDENTITY_KEYS:
        identity = identities[role]
        if identity is None:
            continue
        event = event_map.get(role)
        if event is None:
            errors.append(f"{role} has no live approved identity event at head")
            continue
        expected_agent = {
            "build_lead": LEAD_AGENT,
            "build_tools": TOOLS_AGENT,
            "rco": RCO_AGENT,
        }[role]
        if identity["agent"] != expected_agent:
            errors.append(
                f"{role} must be {expected_agent}, got {identity['agent']}"
            )
        if identity["event_ts"] != str(event.get("ts_utc", "")):
            errors.append(f"{role} event_ts does not match source bridge event")
        if identity["event_digest"] != sha256_digest(dict(event)):
            errors.append(f"{role} event_digest does not match live bridge event")

    rco_pass_ref = live_snapshot.get("rco_pass_ref")
    if rco_pass_ref is not None:
        if live_snapshot["rco"]["approved"] and identity_has_digest(
            identities.get("rco"),
            _latest_live_identity_event(
                events=events,
                task_id=task_id,
                head_sha=head_sha,
                pr_number=pr_number,
            ),
        ):
            pass

    if errors:
        return _verify_result(False, errors, identities, consensus_snapshot)

    return _verify_result(True, [], identities, live_snapshot)


def evaluate_bridge_consensus(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    head_sha: str,
    pr_number: int | None = None,
    lead_agent: str = LEAD_AGENT,
    tools_agent: str = TOOLS_AGENT,
    rco_agent: str = RCO_AGENT,
) -> dict[str, Any]:
    """Fail-closed consensus re-derivation used by receipt verification."""
    expected = (lead_agent, tools_agent, rco_agent)
    base: dict[str, Any] = {
        "ok": False,
        "decision": "bridge_consensus_incomplete",
        "reasons": [],
        "head_sha": head_sha,
        "identities": {},
        "rco_pass_ref": None,
    }
    if len({a for a in expected if a and a.strip()}) != 3:
        return {
            **base,
            "decision": "invalid_consensus_config",
            "reasons": ["bridge consensus requires three distinct agent identities"],
        }
    if not SHA_RE.fullmatch(head_sha):
        return {
            **base,
            "decision": "invalid_consensus_head",
            "reasons": ["head_sha must be a 40-char lowercase sha for consensus binding"],
        }

    latest_approval: dict[str, tuple[int, Mapping[str, Any]]] = {}
    latest_block: dict[str, int] = {}
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        agent = str(event.get("agent", ""))
        if agent not in expected:
            continue
        if not _consensus_scope_match(event, task_id=task_id, pr_number=pr_number):
            continue
        status = str(event.get("status", "")).lower()
        if _is_consensus_block(status):
            latest_block[agent] = index
            continue
        if str(event.get("type", "")).lower() not in DECISION_EVENT_TYPES:
            continue
        if not _event_binds_head(event, head_sha):
            continue
        if agent == rco_agent:
            if status in RCO_PASS_STATUSES:
                latest_approval[agent] = (index, event)
        elif status in BUILD_CONSENSUS_STATUSES:
            latest_approval[agent] = (index, event)

    reasons: list[str] = []
    identities: dict[str, Any] = {}
    for agent, role in (
        (lead_agent, "build_lead"),
        (tools_agent, "build_tools"),
        (rco_agent, "rco"),
    ):
        approval = latest_approval.get(agent)
        block_index = latest_block.get(agent)
        approved = approval is not None and (
            block_index is None or approval[0] > block_index
        )
        identities[role] = {
            "agent": agent,
            "approved": approved,
            "approval_index": approval[0] if approval is not None else None,
            "block_index": block_index,
        }
        if not approved:
            if approval is None:
                reasons.append(
                    f"{role} ({agent}): no head-bound approval at {head_sha}"
                )
            else:
                reasons.append(
                    f"{role} ({agent}): a later block invalidates the approval"
                )

    rco_pass_ref: dict[str, Any] | None = None
    rco_approval = latest_approval.get(rco_agent)
    if rco_approval is not None and identities["rco"]["approved"]:
        rco_event = rco_approval[1]
        rco_pass_ref = {
            "agent": rco_agent,
            "ts_utc": str(rco_event.get("ts_utc", "")),
            "status": str(rco_event.get("status", "")),
            "task_id": str(rco_event.get("task_id", "")),
        }

    ok = not reasons
    return {
        "ok": ok,
        "decision": "bridge_consensus_verified" if ok else "bridge_consensus_incomplete",
        "reasons": reasons,
        "head_sha": head_sha,
        "identities": identities,
        "rco_pass_ref": rco_pass_ref,
    }


def _consensus_identity_events(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    head_sha: str,
    pr_number: int | None,
) -> dict[str, Mapping[str, Any]]:
    """Return the latest live approved events per consensus identity role."""
    event_map: dict[str, Mapping[str, Any]] = {}
    latest: dict[str, tuple[int, Mapping[str, Any]]] = {}
    blocks: dict[str, int] = {}
    role_by_agent = {
        LEAD_AGENT: "build_lead",
        TOOLS_AGENT: "build_tools",
        RCO_AGENT: "rco",
    }
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        agent = str(event.get("agent", ""))
        role = role_by_agent.get(agent)
        if role is None:
            continue
        if not _consensus_scope_match(event, task_id=task_id, pr_number=pr_number):
            continue
        status = str(event.get("status", "")).lower()
        if _is_consensus_block(status):
            blocks[agent] = index
            continue
        if str(event.get("type", "")).lower() not in DECISION_EVENT_TYPES:
            continue
        if not _event_binds_head(event, head_sha):
            continue
        if agent == RCO_AGENT:
            if status in RCO_PASS_STATUSES:
                latest[agent] = (index, event)
        elif status in BUILD_CONSENSUS_STATUSES:
            latest[agent] = (index, event)

    for agent, (index, event) in latest.items():
        if blocks.get(agent) is None or index > blocks[agent]:
            role = role_by_agent[agent]
            event_map[role] = event
    return event_map


def _latest_live_identity_event(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    head_sha: str,
    pr_number: int | None,
) -> Mapping[str, Any] | None:
    # convenience for rco identity binding; keep behavior deterministic
    return _consensus_identity_events(
        events=events,
        task_id=task_id,
        head_sha=head_sha,
        pr_number=pr_number,
    ).get("rco")


def _coerce_identity(value: object) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    agent = str(value.get("agent", "")).strip()
    event_ts = str(value.get("event_ts", "")).strip()
    event_digest = str(value.get("event_digest", "")).strip()
    if not agent or not event_ts or not DIGEST_RE.fullmatch(event_digest):
        return None
    return {
        "agent": agent,
        "event_ts": event_ts,
        "event_digest": event_digest,
    }


def identity_has_digest(
    identity: Mapping[str, str] | None,
    event: Mapping[str, Any] | None,
) -> bool:
    if identity is None or event is None:
        return False
    return identity["event_digest"] == sha256_digest(dict(event))


def compute_charter_digest(path: str | Path) -> str:
    """Compute digest used by bridge consensus receipts for charter binding."""
    return _compute_charter_digest(path)


def _compute_charter_digest(path: str | Path) -> str:
    charter_path = _validate_charter_path(Path(path))
    return sha256_digest(
        {
            "charter_path": str(charter_path),
            "charter_text": charter_path.read_text(encoding="utf-8"),
        }
    )


def _validate_distinct_agents(identities: Mapping[str, Mapping[str, str] | None]) -> None:
    agents = [
        identity["agent"]
        for identity in identities.values()
        if identity is not None
    ]
    if len(agents) != 3:
        raise ValueError("all three identities must be present")
    if len(set(agents)) != 3:
        raise ValueError("bridge identities must be three distinct agents")


def _validate_charter_path(path: Path) -> Path:
    if not path.exists():
        raise OSError(f"charter file not found: {path}")
    return path


def _coerce_sha(value: object, field: str) -> str | None:
    text = str(value or "")
    if not SHA_RE.fullmatch(text):
        return None
    return text


def _coerce_nonempty_string(value: object, field: str) -> str | None:
    text = str(value or "")
    return text if text else None


def _canonical_digest(value: Mapping[str, Any]) -> str:
    digest_input = {k: value[k] for k in sorted(value)}
    return sha256_digest(digest_input)


def _verify_result(
    ok: bool,
    reasons: list[str],
    identities: Mapping[str, Mapping[str, str] | None] | None = None,
    fresh_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": bool(ok),
        "decision": "bridge_consensus_receipt_verified" if ok else "bridge_consensus_receipt_invalid",
        "reasons": reasons,
        "live_bridge_consensus_snapshot": dict(fresh_snapshot or {}),
        "identities": {
            role: (dict(identity) if identity is not None else None)
            for role, identity in (identities or {}).items()
        },
    }


def _event_binds_head(event: Mapping[str, Any], head_sha: str) -> bool:
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        for key in ("head", "head_sha", "expected_head", "head_oid", "head_commit"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip().lower() == head_sha:
                return True
    message = event.get("message")
    if isinstance(message, str) and head_sha in message.lower():
        return True
    return False


def _consensus_scope_match(
    event: Mapping[str, Any],
    *,
    task_id: str,
    pr_number: int | None,
) -> bool:
    if str(event.get("task_id", "")) == task_id:
        return True
    if pr_number is None:
        return False
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        for key in ("pr", "pr_number", "pull_request", "pull_request_number"):
            value = payload.get(key)
            if value == pr_number or (
                isinstance(value, str) and value.strip() == str(pr_number)
            ):
                return True
    pattern = re.compile(rf"(?i)(?:\bpr\s*#?\s*|#){pr_number}\b")
    return pattern.search(str(event.get("task_id", ""))) is not None


def _is_consensus_block(status: str) -> bool:
    if status in CONSENSUS_BLOCKING_STATUSES:
        return True
    tokens = {token for token in re.split(r"[^a-z0-9]+", status.lower()) if token}
    has_blocking_shape = {"changes", "requested"}.issubset(tokens) or any(
        token.startswith("block") for token in tokens
    )
    if not has_blocking_shape:
        return False
    return not tokens.intersection(CONSENSUS_BLOCKING_NEGATION_TOKENS)

