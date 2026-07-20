# SPDX-License-Identifier: BUSL-1.1
"""Opt-in bridge idle detector for idle-protocol v1.

This is a read-only primitive: it reports idle/active/unknown and does not
emit bridge events, query GitHub, or start the protocol by itself.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_event_schema import AGENT_ID_PATTERN
from waggledance.core.work_queue import TASK_ID_PATTERN, resolve_bridge_root
from tools.bridge_next_action import (
    _event_agent,
    _event_recipients,
    _event_status,
    _event_type,
    _is_answer_like,
    _is_request_like,
    _task_id,
)


DEFAULT_EVENTS_PATH = Path(".agent-bridge") / "shared" / "events.jsonl"
DEFAULT_CLAIMS_DIR = Path(".agent-bridge") / "work_queue" / "claims"
DEFAULT_WAIVERS_PATH = ROOT / "configs" / "bridge_event_validation_waivers.json"
CANONICAL_RCO_SCHEMA = "wd.exact_head_consensus_request.v1"
REQUESTER_TERMINAL_STATUS_STEMS = (
    "done",
    "closed",
    "superseded",
    "merged",
    "abandoned",
    "completed",
    "approved",
    "cancelled",
    "canceled",
)
REQUESTER_MESSAGE_TERMINAL_STATUS_STEMS = (
    "closed",
    "superseded",
    "cancelled",
    "canceled",
)
NONTERMINAL_STATUS_TOKENS = frozenset(
    {
        "cannot",
        "failed",
        "failure",
        "incomplete",
        "never",
        "not",
        "notyet",
        "open",
        "pending",
        "progress",
        "unresolved",
        "working",
    }
)
UNRESOLVED_TARGET = "__unresolved_deliberation_target__"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check whether the bridge is idle enough for idle protocol v1.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Bridge events JSONL path. Defaults to <bridge-root>/shared/events.jsonl.",
    )
    parser.add_argument(
        "--claims-dir",
        type=Path,
        default=None,
        help="Bridge claims directory. Defaults to <bridge-root>/work_queue/claims.",
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Path to .agent-bridge directory (default: "
            "AGENT_BRIDGE_RUNTIME_ROOT/AGENT_BRIDGE_ROOT or repo-local)."
        ),
    )
    parser.add_argument("--pending-ci-count", type=int, default=0)
    parser.add_argument("--idle-minutes", type=int, default=60)
    parser.add_argument("--now", default=None)
    parser.add_argument("--operator-last-activity-utc", default=None)
    parser.add_argument("--open-request-max-age-hours", type=float, default=12.0)
    parser.add_argument(
        "--waivers",
        type=Path,
        default=DEFAULT_WAIVERS_PATH,
        help="Known-invalid historical event waivers JSON.",
    )
    parser.add_argument(
        "--no-waivers",
        action="store_true",
        help="Disable known-invalid historical event waivers.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"
    claims_dir = args.claims_dir or bridge_root / "work_queue" / "claims"
    try:
        waiver_hashes = {} if args.no_waivers else _load_waivers(args.waivers)
        report = evaluate_idle_state(
            events_path=events_path,
            claims_dir=claims_dir,
            now_utc=_parse_utc(args.now) if args.now else datetime.now(timezone.utc),
            idle_minutes=args.idle_minutes,
            pending_ci_count=args.pending_ci_count,
            open_request_max_age_hours=args.open_request_max_age_hours,
            waived_line_sha256=waiver_hashes,
            operator_last_activity_utc=(
                _parse_utc(args.operator_last_activity_utc)
                if args.operator_last_activity_utc
                else None
            ),
        )
    except ValueError as exc:
        print(f"idle check FAILED: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        if report["blockers"]:
            print("blockers: " + ", ".join(report["blockers"]))
    return 0


def evaluate_idle_state(
    *,
    events_path: Path,
    claims_dir: Path,
    now_utc: datetime,
    idle_minutes: int,
    pending_ci_count: int,
    open_request_max_age_hours: float,
    waived_line_sha256: Mapping[int, str] | None = None,
    operator_last_activity_utc: datetime | None = None,
) -> dict[str, Any]:
    if idle_minutes <= 0:
        raise ValueError("--idle-minutes must be positive")
    if pending_ci_count < 0:
        raise ValueError("--pending-ci-count cannot be negative")
    if open_request_max_age_hours <= 0:
        raise ValueError("--open-request-max-age-hours must be positive")
    if not events_path.exists():
        raise ValueError(f"missing bridge events file: {events_path}")
    if waived_line_sha256 is None:
        waived_line_sha256 = _load_waivers(DEFAULT_WAIVERS_PATH)

    events, invalid_lines = _read_events(
        events_path,
        waived_line_sha256=waived_line_sha256,
    )
    if not events:
        raise ValueError(f"empty bridge events file: {events_path}")

    now_utc = now_utc.astimezone(timezone.utc)
    cutoff = now_utc - timedelta(minutes=idle_minutes)
    request_max_age = timedelta(hours=open_request_max_age_hours)
    event_requests, stale_requests = _fresh_and_stale_requests(
        _open_event_requests(events),
        now_utc,
        request_max_age,
    )
    claim_task_ids, claim_requests = _claim_state(claims_dir)
    requests = event_requests + claim_requests

    latest_operator = _latest(events, _is_operator_activity)
    if operator_last_activity_utc is not None:
        latest_operator = _max_dt(latest_operator, operator_last_activity_utc)

    criteria = {
        "pending_ci": {
            "ok": pending_ci_count == 0,
            "pending_ci_count": pending_ci_count,
        },
        "open_work_claims": {"ok": not claim_task_ids, "task_ids": claim_task_ids},
        "open_scout_requests": _request_criterion(requests, "scout"),
        "open_rco_requests": _request_criterion(requests, "rco"),
        "recent_merge": _quiet(_latest(events, _is_merge_event), cutoff),
        "recent_agent_message": _quiet(
            _latest(events, _is_substantive_agent_message),
            cutoff,
        ),
        "recent_operator_activity": _quiet(latest_operator, cutoff),
        "invalid_events": {"ok": invalid_lines == 0, "invalid_lines": invalid_lines},
        "stale_open_requests_ignored": {
            "ok": True,
            "task_ids": [request["task_id"] for request in stale_requests],
            "max_age_hours": open_request_max_age_hours,
        },
    }
    blockers = [name for name, value in criteria.items() if not value["ok"]]
    return {
        "decision": "idle" if not blockers else "active",
        "idle": not blockers,
        "checked_at_utc": _iso(now_utc),
        "idle_minutes": idle_minutes,
        "cutoff_utc": _iso(cutoff),
        "events_path": str(events_path),
        "claims_dir": str(claims_dir),
        "blockers": blockers,
        "criteria": criteria,
    }


def _read_events(
    path: Path,
    *,
    waived_line_sha256: Mapping[int, str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    invalid = 0
    waivers = dict(waived_line_sha256 or {})
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            event["_line_no"] = line_no
            event["_ts"] = _parse_utc(str(event["ts_utc"]))
            events.append(event)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            if waivers.get(line_no) == _line_sha256(line):
                continue
            invalid += 1
    events.sort(key=lambda event: (event["_ts"], event["_line_no"]))
    return events, invalid


def _load_waivers(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc.msg}") from exc
    entries = payload.get("waivers") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{path}: expected object with waivers list")

    waiver_hashes: dict[int, str] = {}
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: waiver {index} must be an object")
        line_no = entry.get("line_no")
        raw_sha256 = entry.get("raw_line_sha256")
        if not isinstance(line_no, int) or line_no <= 0:
            raise ValueError(f"{path}: waiver {index} line_no must be a positive integer")
        if not isinstance(raw_sha256, str) or not _is_sha256_digest(raw_sha256):
            raise ValueError(
                f"{path}: waiver {index} raw_line_sha256 must be sha256:<64 hex>"
            )
        waiver_hashes[line_no] = raw_sha256
    return waiver_hashes


def _is_sha256_digest(value: str) -> bool:
    if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
        return False
    return all(char in "0123456789abcdef" for char in value.removeprefix("sha256:"))


def _line_sha256(line: str) -> str:
    return "sha256:" + hashlib.sha256(line.encode("utf-8")).hexdigest()


def _open_event_requests(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return open scout/RCO requests using bridge-next-action semantics.

    The idle detector is a global HOLD gate, so a multi-recipient RCO request
    remains open until every addressed RCO identity emits its own later,
    answer-like event (or the requester explicitly closes/withdraws it).
    Exact-head requests additionally require the response payload to bind the
    same head. This mirrors agent-facing request routing instead of treating
    any same-task event as a response.
    """

    open_by_key: dict[tuple[str, str, str], dict[str, str]] = {}
    for event in events:
        canonical_schema = _event_schema(event) == CANONICAL_RCO_SCHEMA
        strict_contract = canonical_schema or _is_near_canonical_rco_request(event)
        raw_task_id = _literal_task_id(event) if strict_contract else _task_id(event)
        task_id = (
            raw_task_id
            if raw_task_id.strip()
            else _invalid_canonical_task_id(event)
        )
        canonical_contract_valid = _valid_canonical_rco_contract(event)
        required_signal = (
            _required_rco_signal(event) if canonical_contract_valid else {}
        )
        required_response_payload = required_signal.get("payload")
        if not isinstance(required_response_payload, Mapping):
            required_response_payload = {}
        for kind, target_agent in _request_targets(event):
            if not task_id:
                continue
            open_by_key[(kind, task_id, target_agent)] = {
                "kind": kind,
                "task_id": task_id,
                "target_agent": target_agent,
                "request_agent": (
                    _literal_event_agent(event)
                    if strict_contract
                    else _event_agent(event)
                ),
                "request_head": _event_head(event),
                "canonical_schema": "true" if strict_contract else "false",
                "canonical_contract": (
                    "valid" if canonical_contract_valid else "absent_or_invalid"
                ),
                "required_response_type": str(
                    required_signal.get("type") or ""
                ).lower(),
                "required_response_status": str(
                    required_signal.get("status") or ""
                ).lower(),
                "required_message_token": str(
                    required_signal.get("message_must_contain_head") or ""
                ).lower(),
                "required_response_payload_json": json.dumps(
                    dict(required_response_payload),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "persistent_hold": (
                    "true"
                    if kind == "rco"
                    and (
                        target_agent == UNRESOLVED_TARGET
                        or _event_head(event)
                        or canonical_schema
                    )
                    else "false"
                ),
                "opened_at_utc": _iso(event["_ts"]),
            }
        for key, request in list(open_by_key.items()):
            if _closes_request(request, event):
                del open_by_key[key]
    return list(open_by_key.values())


def _invalid_canonical_task_id(event: Mapping[str, Any]) -> str:
    if (
        _event_schema(event) != CANONICAL_RCO_SCHEMA
        and not _is_near_canonical_rco_request(event)
    ):
        return ""
    return f"invalid-canonical-request-line-{event.get('_line_no', 'unknown')}"


def _fresh_and_stale_requests(
    requests: list[dict[str, str]],
    now_utc: datetime,
    max_age: timedelta,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    fresh: list[dict[str, str]] = []
    stale: list[dict[str, str]] = []
    for request in requests:
        if request.get("persistent_hold") == "true":
            fresh.append(request)
            continue
        opened_at = _parse_utc(request["opened_at_utc"])
        (stale if now_utc - opened_at > max_age else fresh).append(request)
    return fresh, stale


def _claim_state(claims_dir: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not claims_dir.exists():
        return [], []
    task_ids: list[str] = []
    requests: list[dict[str, str]] = []
    for path in claims_dir.glob("*.json"):
        try:
            claim = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            claim = {"task_id": path.stem}
        task_id = str(claim.get("task_id", path.stem))
        task_ids.append(task_id)
        kind = _claim_kind(claim)
        if kind:
            requests.append(
                {
                    "kind": kind,
                    "task_id": task_id,
                    "opened_at_utc": str(claim.get("claimed_at_utc", "")),
                }
            )
    return sorted(task_ids), requests


def _claim_kind(claim: dict[str, Any]) -> str | None:
    """Classify a claim as a deliberation request ('scout'/'rco') or None.

    Honors the 2026-05-18 bridge-consensus substrate-invariant #1 (phase A):
    deliberation locks and active-work locks must be distinguishable. A claim
    may set an explicit ``claim_kind`` field:

      - ``claim_kind == "active_work"``  -> never a deliberation request
        (returns None) even if its free text contains "scout"/"rco".
      - ``claim_kind == "deliberation"`` -> ALWAYS a deliberation request
        (an explicit declaration that this claim is a deliberation lock).
        The subtype is resolved in this order: (1) ``deliberation_kind``
        if it is "scout"/"rco"; (2) free-text hint ("scout" if the text
        contains scout, else "rco" if it contains rco); (3) **fail closed
        to "rco"** when neither is available. Failing closed to a
        deliberation lock is intentional: a claim that declared itself a
        deliberation must keep the bridge active until it is answered,
        rather than silently dropping the lock.

    When ``claim_kind`` is absent (legacy claims, and any writer that has not
    yet adopted the field), the original free-text substring heuristic is used
    (scout-if-text, rco-if-text, else None) so behavior is unchanged. This
    makes the field opt-in on the write side.
    """
    explicit = str(claim.get("claim_kind", "")).lower()
    if explicit == "active_work":
        return None
    text = " ".join(
        str(claim.get(field, ""))
        for field in ("task_id", "summary", "release_status", "release_message")
    ).lower()
    if explicit == "deliberation":
        subtype = str(claim.get("deliberation_kind", "")).lower()
        if subtype in {"scout", "rco"}:
            return subtype
        if "scout" in text:
            return "scout"
        # Fail closed to a deliberation lock: an explicitly-declared
        # deliberation claim must keep the bridge active even when its
        # subtype cannot be inferred from deliberation_kind or free text.
        return "rco"
    return "scout" if "scout" in text else "rco" if "rco" in text else None


def _request_targets(event: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Classify deliberation targets without broad ``*claude*`` matching."""

    canonical_schema = _event_schema(event) == CANONICAL_RCO_SCHEMA
    requester = (
        _literal_event_agent(event) if canonical_schema else _event_agent(event)
    )
    raw_recipients = (
        _literal_recipients(event)
        if canonical_schema
        else _event_recipients(event)
    )
    recipients = tuple(
        dict.fromkeys(
            recipient
            for recipient in raw_recipients
            if recipient not in {requester, "operator", "system"}
        )
    )
    if canonical_schema:
        if not _valid_canonical_rco_contract(event):
            return [("rco", UNRESOLVED_TARGET)]
        eligible_rco_agents = _eligible_rco_agents(event)
        targets = [
            ("rco", recipient)
            for recipient in recipients
            if recipient in eligible_rco_agents
        ]
        return targets or [("rco", UNRESOLVED_TARGET)]
    if _is_near_canonical_rco_request(event):
        return [("rco", UNRESOLVED_TARGET)]

    if not _is_request_like(event):
        return []
    status = _event_status(event)
    if status in {"request_scout", "scout_requested"}:
        return _deliberation_targets("scout", recipients)
    if status == "rco_requested":
        return _deliberation_targets("rco", recipients)

    if _event_type(event) not in {"wake_request", "peer_review_request"}:
        return []
    targets = [
        ("rco", recipient)
        for recipient in recipients
        if "rco" in _identity_tokens(recipient)
    ]
    if targets:
        return targets
    return []


def _is_near_canonical_rco_request(event: Mapping[str, Any]) -> bool:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return False
    if _is_answer_like(event):
        return False
    request_intent = _is_request_like(event) or payload.get("request_only") is True
    if not request_intent:
        return False
    required_signals = payload.get("required_signals")
    if (
        "required_signals" in payload
        and _has_rco_required_signal_marker(required_signals)
    ):
        return True
    schema_text = str(payload.get("schema")).casefold()
    return "wd.exact_head_consensus" in schema_text


def _has_rco_required_signal_marker(
    value: object,
    *,
    scalar_allowed: bool = True,
) -> bool:
    if isinstance(value, str):
        return scalar_allowed and value.strip().casefold() == "rco"
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str) and key.strip().casefold() == "rco":
                return True
            if isinstance(nested, Mapping) and _has_rco_required_signal_marker(
                nested,
                scalar_allowed=False,
            ):
                return True
            if (
                isinstance(nested, Sequence)
                and not isinstance(nested, (str, bytes))
                and _has_rco_required_signal_marker(
                    nested,
                    scalar_allowed=False,
                )
            ):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(
            _has_rco_required_signal_marker(
                item,
                scalar_allowed=(
                    scalar_allowed
                    and not isinstance(item, Mapping)
                ),
            )
            for item in value
        )
    return False


def _deliberation_targets(
    kind: str,
    recipients: Sequence[str],
) -> list[tuple[str, str]]:
    if recipients:
        return [(kind, recipient) for recipient in recipients]
    return [(kind, UNRESOLVED_TARGET)]


def _eligible_rco_agents(event: Mapping[str, Any]) -> set[str]:
    rco = _required_rco_signal(event)
    eligible = rco.get("eligible_agents")
    if not isinstance(eligible, Sequence) or isinstance(eligible, (str, bytes)):
        return set()
    return {
        str(agent).strip().lower()
        for agent in eligible
        if str(agent).strip()
    }


def _required_rco_signal(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return {}
    required_signals = payload.get("required_signals")
    if not isinstance(required_signals, Mapping):
        return {}
    rco = required_signals.get("rco")
    if not isinstance(rco, Mapping):
        return {}
    return rco


def _valid_canonical_rco_contract(event: Mapping[str, Any]) -> bool:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return False
    if payload.get("schema") != CANONICAL_RCO_SCHEMA:
        return False
    if event.get("type") != "wake_request" or event.get("status") != "review_requested":
        return False
    task_id = _literal_task_id(event)
    request_agent = _literal_event_agent(event)
    head = _literal_canonical_head(event)
    if (
        not task_id
        or task_id != task_id.strip()
        or not _is_safe_task_id(task_id)
        or not request_agent
        or not head
    ):
        return False
    message = event.get("message")
    if not isinstance(message, str) or head not in message:
        return False
    if payload.get("canonical_task_id") != task_id:
        return False
    if payload.get("request_only") is not True:
        return False
    if payload.get("approval_asserted") is not False:
        return False

    rco = _required_rco_signal(event)
    eligible = rco.get("eligible_agents")
    if not isinstance(eligible, Sequence) or isinstance(eligible, (str, bytes)):
        return False
    eligible_agents = list(eligible)
    if not eligible_agents or any(
        not isinstance(agent, str)
        or agent != agent.strip().lower()
        or not re.fullmatch(AGENT_ID_PATTERN, agent)
        or "rco" not in _identity_tokens(agent)
        for agent in eligible_agents
    ):
        return False
    if len(set(eligible_agents)) != len(eligible_agents):
        return False
    literal_recipients = _literal_recipients(event)
    if not literal_recipients:
        return False
    addressed_rco_agents = {
        recipient
        for recipient in literal_recipients
        if recipient not in {request_agent, "operator", "system"}
        and "rco" in _identity_tokens(recipient)
    }
    if addressed_rco_agents != set(eligible_agents):
        return False
    if rco.get("type") != "decision":
        return False
    if rco.get("status") != "rco_pass":
        return False
    if rco.get("task_id") != task_id:
        return False
    if rco.get("message_must_contain_head") != head:
        return False
    response_payload = rco.get("payload")
    return (
        _is_safe_response_payload_template(response_payload)
        and response_payload.get("head") == head
        and response_payload.get("canonical_task_id") == task_id
        and response_payload.get("operator_gated") is True
    )


def _is_safe_response_payload_template(value: object) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    return all(
        isinstance(key, str)
        and bool(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key))
        and type(item) in {str, bool, int}
        for key, item in value.items()
    )


def _identity_tokens(agent: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", agent.lower()) if token}


def _event_head(event: Mapping[str, Any]) -> str:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    return str(payload.get("head") or "").strip().lower()


def _literal_canonical_head(event: Mapping[str, Any]) -> str:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    head = payload.get("head")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        return ""
    return head


def _literal_task_id(event: Mapping[str, Any]) -> str:
    task_id = event.get("task_id")
    return task_id if isinstance(task_id, str) else ""


def _is_safe_task_id(task_id: str) -> bool:
    return bool(TASK_ID_PATTERN.fullmatch(task_id)) and all(
        segment not in {"", ".", ".."} for segment in task_id.split("/")
    )


def _literal_event_agent(event: Mapping[str, Any]) -> str:
    agent = event.get("agent")
    if (
        not isinstance(agent, str)
        or agent != agent.strip().lower()
        or not re.fullmatch(AGENT_ID_PATTERN, agent)
    ):
        return ""
    return agent


def _literal_recipients(event: Mapping[str, Any]) -> tuple[str, ...]:
    raw_to = event.get("to")
    if not isinstance(raw_to, str) or not raw_to:
        return ()
    recipients = tuple(raw_to.split(","))
    if len(set(recipients)) != len(recipients) or any(
        recipient != recipient.lower()
        or not re.fullmatch(AGENT_ID_PATTERN, recipient)
        for recipient in recipients
    ):
        return ()
    return recipients


def _event_schema(event: Mapping[str, Any]) -> str:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    return str(payload.get("schema") or "").strip().lower()


def _closes_request(request: Mapping[str, str], event: Mapping[str, Any]) -> bool:
    canonical_schema = request.get("canonical_schema") == "true"
    event_task_id = (
        _literal_task_id(event) if canonical_schema else _task_id(event)
    )
    if event_task_id != request["task_id"]:
        return False
    if event["_ts"] <= _parse_utc(request["opened_at_utc"]):
        return False
    target_event_agent = (
        _literal_event_agent(event) if canonical_schema else _event_agent(event)
    )
    target_answer = (
        request["target_agent"] != UNRESOLVED_TARGET
        and target_event_agent == request["target_agent"]
    )
    requester_closure = (
        _literal_event_agent(event) == request["request_agent"]
        and _is_requester_closure_event(event)
    )
    if requester_closure:
        return True
    if not target_answer:
        return False
    answer_like = (
        _is_answer_like(event)
        if canonical_schema
        else _is_legacy_target_answer(request, event)
    )
    if not answer_like:
        return False
    request_head = request.get("request_head", "")
    response_head = (
        _literal_canonical_head(event) if canonical_schema else _event_head(event)
    )
    if request_head and response_head != request_head:
        return False
    if target_answer and request_head and request["kind"] == "rco":
        return _is_valid_exact_head_rco_response(request, event)
    return True


def _is_requester_closure_event(event: Mapping[str, Any]) -> bool:
    event_type = event.get("type")
    status = event.get("status")
    if not isinstance(event_type, str) or not isinstance(status, str):
        return False
    if event_type != event_type.lower() or status != status.lower():
        return False
    stems = (
        REQUESTER_MESSAGE_TERMINAL_STATUS_STEMS
        if event_type == "message"
        else REQUESTER_TERMINAL_STATUS_STEMS
    )
    if event_type not in {"message", "done", "release", "decision"}:
        return False
    normalized_status = status
    status_tokens = _identity_tokens(normalized_status)
    if status_tokens & NONTERMINAL_STATUS_TOKENS:
        return False
    # Preserve the tracked close helper's explicit requester receipt contract.
    if event_type == "decision" and normalized_status == "rco_closed_postmerge":
        return True
    return any(
        normalized_status == stem or normalized_status.startswith(f"{stem}_")
        for stem in stems
    )


def _is_legacy_target_answer(
    request: Mapping[str, str],
    event: Mapping[str, Any],
) -> bool:
    event_type = _event_type(event)
    status = _event_status(event)
    if event_type == "message" and (
        _identity_tokens(status) & NONTERMINAL_STATUS_TOKENS
    ):
        return False
    if _request_targets(event):
        return False
    if _is_answer_like(event):
        return True
    if event_type in {"decision", "done", "blocked", "release"}:
        return True
    markers = {
        "scout": ("scout_", "answered", "recommend", "blocked", "done"),
        "rco": ("rco_", "source_review", "pass", "blocked", "done"),
    }
    return any(marker in status for marker in markers[request["kind"]])


def _is_valid_exact_head_rco_response(
    request: Mapping[str, str],
    event: Mapping[str, Any],
) -> bool:
    if request.get("canonical_contract") == "valid":
        payload = event.get("payload")
        if (
            not isinstance(payload, Mapping)
            or payload.get("canonical_task_id") != request["task_id"]
            or event.get("to") != request["request_agent"]
        ):
            return False
        try:
            required_payload = json.loads(
                request.get("required_response_payload_json") or "{}"
            )
        except (TypeError, ValueError):
            return False
        if not isinstance(required_payload, dict) or any(
            key not in payload
            or type(payload[key]) is not type(expected)
            or payload[key] != expected
            for key, expected in required_payload.items()
        ):
            return False

    strict_canonical = request.get("canonical_contract") == "valid"
    event_type = (
        str(event.get("type") or "") if strict_canonical else _event_type(event)
    )
    status = (
        str(event.get("status") or "")
        if strict_canonical
        else _event_status(event)
    )
    required_message_token = (
        request.get("required_message_token") or request["request_head"]
    )
    raw_message = event.get("message")
    if strict_canonical and not isinstance(raw_message, str):
        return False
    message = str(raw_message or "")
    if not strict_canonical:
        message = message.lower()
    if required_message_token not in message:
        return False
    if event_type == "finding" and status == "changes_requested":
        return True

    required_type = request.get("required_response_type") or "decision"
    required_status = request.get("required_response_status") or "rco_pass"
    if event_type != required_type or status != required_status:
        return False
    return True


def _request_criterion(
    requests: list[dict[str, str]],
    kind: str,
) -> dict[str, Any]:
    task_ids = list(
        dict.fromkeys(
            request["task_id"]
            for request in requests
            if request["kind"] == kind
        )
    )
    return {"ok": not task_ids, "task_ids": task_ids}


def _latest(
    events: list[dict[str, Any]],
    predicate: Any,
) -> datetime | None:
    latest: datetime | None = None
    for event in events:
        if predicate(event):
            latest = _max_dt(latest, event["_ts"])
    return latest


def _is_merge_event(event: dict[str, Any]) -> bool:
    if _is_retroactive_rco_closure(event):
        return False
    status = str(event.get("status", "")).lower()
    if "merged" in status or "merge_commit" in status or "mergecommit" in status:
        return True
    if str(event.get("type", "")).lower() != "done":
        return False
    message = str(event.get("message", "")).lower()
    return " merged " in f" {message} " or "merge commit" in message


def _is_retroactive_rco_closure(event: dict[str, Any]) -> bool:
    if str(event.get("type", "")).lower() != "done":
        return False
    text = (
        f"{event.get('status', '')} "
        f"{event.get('task_id', '')} "
        f"{event.get('message', '')}"
    ).lower()
    return "stale rco" in text and (
        "retroactive close" in text or "bookkeeping closure" in text
    )


def _is_substantive_agent_message(event: dict[str, Any]) -> bool:
    agent = str(event.get("agent", "")).lower()
    if agent in {"operator", "system"} or not re.fullmatch(AGENT_ID_PATTERN, agent):
        return False
    if str(event.get("type", "")).lower() != "message":
        return False
    message = str(event.get("message", "")).strip()
    if len(message) < 20:
        return False
    text = f"{event.get('status', '')} {event.get('task_id', '')} {message}".lower()
    return not (
        len(message) < 120
        and any(marker in text for marker in ("cron", "poll", "heartbeat", "liveness"))
    )


def _is_operator_activity(event: dict[str, Any]) -> bool:
    return (
        str(event.get("agent", "")).lower() == "operator"
        or str(event.get("to", "")).lower() == "operator"
    )


def _quiet(latest: datetime | None, cutoff: datetime) -> dict[str, Any]:
    return {
        "ok": latest is None or latest < cutoff,
        "latest_utc": _iso(latest) if latest is not None else None,
    }


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _max_dt(left: datetime | None, right: datetime) -> datetime:
    return right if left is None or right > left else left


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
