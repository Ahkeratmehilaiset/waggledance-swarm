# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only digest of bridge events needing operator attention.

This tool is intentionally not a push-notification sender. It reads durable
bridge JSONL events, ranks operator-addressed open attention items, and emits a
path-free report that another operator-local process can decide how to surface.
It does not write bridge events, enqueue work, access the network, or grant
runtime/merge authority.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.bridge_next_action as bridge_next_action  # noqa: E402
from tools.bridge_next_action import (  # noqa: E402
    BridgeNextActionError,
    CLOSED_REQUEST_STATUSES,
    _event_agent,
    _event_recipients,
    _event_status,
    _event_ts,
    _event_type,
    _is_request_like,
    _latest_event_time,
    _parse_utc,
    _task_id,
    read_events,
)
from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402


SENSITIVE_MARKERS = tuple(getattr(bridge_next_action, "PRIVATE_" + "MARKERS"))
DEFAULT_TAIL = 50000
DEFAULT_MAX_ITEMS = 20
DEFAULT_MAX_AGE_HOURS = 12.0
DEFAULT_MIN_AGE_MINUTES = 0.0
OPERATOR_AGENT = "operator"
HIGH_SEVERITIES = {"critical", "high", "major"}
TERMINAL_TYPES = {"done", "release"}
ATTENTION_STATUS_TOKENS = {
    "action",
    "attention",
    "blocked",
    "blocking",
    "failed",
    "failure",
    "required",
    "requested",
    "signature",
    "stalled",
}
WINDOWS_PATH_RE = re.compile(r"(?i)\b[a-z]:\\[^\s\"'<>]+")
UNIX_PATH_RE = re.compile(r"(?<![\w:/])/(?:home|users|tmp|var|mnt|workspace|w)\S*")


class OperatorAttentionDigestError(ValueError):
    """Raised when the digest cannot be produced safely."""

    def __init__(self, report: dict[str, Any], exit_code: int = 2) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read bridge events and report unresolved operator-addressed "
            "attention items without sending notifications."
        ),
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Bridge events JSONL path. Defaults to <bridge-root>/shared/events.jsonl.",
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
    parser.add_argument(
        "--min-age-minutes",
        type=float,
        default=DEFAULT_MIN_AGE_MINUTES,
        help="Only include attention items at least this old.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help=(
            "Ignore attention items older than this many hours; use <=0 to "
            "include the full selected event tail."
        ),
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=DEFAULT_TAIL,
        help="Maximum event lines to read from the end of the JSONL file.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=DEFAULT_MAX_ITEMS,
        help="Maximum attention items to include.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Override current UTC time for age evaluation.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"
    try:
        report = build_operator_attention_digest(
            events=read_events(events_path, tail=args.tail),
            min_age_minutes=args.min_age_minutes,
            max_age_hours=args.max_age_hours,
            max_items=args.max_items,
            now_utc=_parse_now(args.now),
        )
    except BridgeNextActionError as exc:
        report = {
            "ok": False,
            "decision": "operator_attention_digest_error",
            "errors": exc.report.get("errors", [str(exc)]),
        }
        exit_code = 2
    except OperatorAttentionDigestError as exc:
        report = exc.report
        exit_code = exc.exit_code
    except OSError as exc:
        report = {
            "ok": False,
            "decision": "operator_attention_digest_error",
            "errors": [exc.__class__.__name__],
        }
        exit_code = 1
    else:
        exit_code = 0

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


def build_operator_attention_digest(
    *,
    events: Sequence[Mapping[str, Any]],
    min_age_minutes: float = DEFAULT_MIN_AGE_MINUTES,
    max_age_hours: float | None = DEFAULT_MAX_AGE_HOURS,
    max_items: int = DEFAULT_MAX_ITEMS,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return unresolved operator-addressed bridge attention items."""
    if not math.isfinite(min_age_minutes) or min_age_minutes < 0:
        raise _error("min_age_minutes must be finite and non-negative")
    if max_items <= 0:
        raise _error("max_items must be positive")
    if max_age_hours is not None and not math.isfinite(max_age_hours):
        raise _error("max_age_hours must be finite")
    if max_age_hours is not None and max_age_hours > 0:
        max_age_minutes = max_age_hours * 60.0
    else:
        max_age_hours = None
        max_age_minutes = None

    effective_now = (
        now_utc
        or _latest_event_time(events)
        or datetime.now(timezone.utc).astimezone(timezone.utc)
    )
    open_items = _open_operator_attention_items(events)

    items: list[dict[str, Any]] = []
    for state in open_items.values():
        event_ts = _parse_utc(str(state["ts_utc"]))
        if event_ts is None:
            continue
        age_minutes = (
            effective_now.astimezone(timezone.utc) - event_ts
        ).total_seconds() / 60.0
        if age_minutes < min_age_minutes:
            continue
        if max_age_minutes is not None and age_minutes > max_age_minutes:
            continue
        items.append(_attention_row(state, age_minutes=age_minutes))

    items.sort(
        key=lambda row: (
            -int(row["rank_score"]),
            -float(row["age_minutes"]),
            str(row["task_id"]),
        )
    )
    items = items[:max_items]

    by_priority: dict[str, int] = {}
    for row in items:
        priority = str(row["priority"])
        by_priority[priority] = by_priority.get(priority, 0) + 1

    report = {
        "ok": True,
        "decision": "operator_attention_digest",
        "events_checked": len(events),
        "events_path_recorded": False,
        "local_paths_recorded": False,
        "read_only": True,
        "push_delivery_attempted": False,
        "network_authority": False,
        "bridge_write_authority": False,
        "scheduler_authority": False,
        "runtime_authority": False,
        "min_age_minutes": min_age_minutes,
        "max_age_hours": max_age_hours,
        "max_items": max_items,
        "attention_count": len(items),
        "by_priority": dict(sorted(by_priority.items())),
        "items": items,
    }
    _assert_path_free(report)
    return report


def _open_operator_attention_items(
    events: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    open_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for index, event in enumerate(events):
        _close_satisfied_items(open_by_key, event)
        if not _is_operator_attention_event(event):
            continue
        key = (_task_key(event), _payload_scalar(event, "pr") or "")
        previous = open_by_key.get(key)
        event_count = int(previous.get("event_count", 0)) + 1 if previous else 1
        first_ts = str(previous.get("first_ts_utc")) if previous else _event_ts(event)
        open_by_key[key] = {
            "source_agent": _event_agent(event),
            "task_id": _task_id(event),
            "type": _event_type(event),
            "status": _event_status(event),
            "severity": _severity(event),
            "ts_utc": _event_ts(event),
            "first_ts_utc": first_ts,
            "event_index": index,
            "event_count": event_count,
            "message": _safe_text(event.get("message")),
            "payload_pr": _payload_scalar(event, "pr")
            or _payload_scalar(event, "pr_number"),
            "payload_head": _payload_scalar(event, "head"),
            "attention_reasons": _attention_reasons(event),
        }
    return open_by_key


def _close_satisfied_items(
    open_by_key: dict[tuple[str, str], dict[str, Any]],
    event: Mapping[str, Any],
) -> None:
    if not _is_closure_event(event):
        return
    event_ts = _event_ts(event)
    event_task_key = _task_key(event)
    event_pr = _payload_scalar(event, "pr") or _payload_scalar(event, "pr_number")
    for key, state in list(open_by_key.items()):
        state_task_key, state_pr = key
        if event_ts <= str(state["ts_utc"]):
            continue
        same_task = bool(event_task_key and event_task_key == state_task_key)
        same_pr = bool(event_pr and state_pr and event_pr == state_pr)
        if same_task or same_pr:
            del open_by_key[key]


def _is_operator_attention_event(event: Mapping[str, Any]) -> bool:
    if OPERATOR_AGENT not in _event_recipients(event):
        return False
    if _event_agent(event) in {OPERATOR_AGENT, "system"}:
        return False
    if _event_status(event) in CLOSED_REQUEST_STATUSES:
        return False
    event_type = _event_type(event)
    if event_type in TERMINAL_TYPES or event_type in {"claim", "test"}:
        return False
    if _is_request_like(event):
        return True
    reasons = set(_attention_reasons(event))
    if event_type == "finding" and any(
        reason.startswith("severity:") for reason in reasons
    ):
        return True
    return bool(
        reasons.intersection(
            {
                "operator_action_signal",
                "payload_no_more_nudges",
                "payload_operator_action_required",
                "wake_request_to_operator",
            }
        )
    )


def _is_closure_event(event: Mapping[str, Any]) -> bool:
    return (
        _event_type(event) in TERMINAL_TYPES
        or _event_status(event) in CLOSED_REQUEST_STATUSES
    )


def _attention_reasons(event: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    severity = _severity(event)
    if severity in HIGH_SEVERITIES:
        reasons.append(f"severity:{severity}")
    if _event_type(event) == "wake_request":
        reasons.append("wake_request_to_operator")
    tokens = _tokens(
        " ".join(
            [
                _event_status(event),
                _event_type(event),
                str(event.get("message") or ""),
                _payload_text(event),
            ]
        )
    )
    if tokens.intersection(ATTENTION_STATUS_TOKENS):
        reasons.append("operator_action_signal")
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        if payload.get("operator_action_required") is True:
            reasons.append("payload_operator_action_required")
        if payload.get("do_not_emit_additional_wake_requests") is True:
            reasons.append("payload_no_more_nudges")
    return sorted(set(reasons))


def _attention_row(state: Mapping[str, Any], *, age_minutes: float) -> dict[str, Any]:
    reasons = [str(item) for item in state.get("attention_reasons", [])]
    priority, score = _priority_and_score(reasons, state)
    row: dict[str, Any] = {
        "priority": priority,
        "rank_score": score,
        "source_agent": state["source_agent"],
        "task_id": state["task_id"],
        "type": state["type"],
        "status": state["status"],
        "severity": state["severity"],
        "ts_utc": state["ts_utc"],
        "first_ts_utc": state["first_ts_utc"],
        "age_minutes": round(age_minutes, 3),
        "event_count": state["event_count"],
        "operator_addressed": True,
        "bridge_visible": True,
        "push_delivery_attempted": False,
        "message": state["message"],
        "attention_reasons": reasons,
        "suggested_action": _suggested_action(reasons),
    }
    if state.get("payload_pr"):
        row["pr"] = state["payload_pr"]
    if state.get("payload_head"):
        row["head"] = state["payload_head"]
    return row


def _priority_and_score(
    reasons: Sequence[str],
    state: Mapping[str, Any],
) -> tuple[str, int]:
    score = 0
    if any(reason in reasons for reason in ("severity:critical", "severity:high")):
        score += 100
    if "severity:major" in reasons:
        score += 80
    if "payload_operator_action_required" in reasons:
        score += 60
    if "payload_no_more_nudges" in reasons:
        score += 40
    if "wake_request_to_operator" in reasons:
        score += 30
    if "operator_action_signal" in reasons:
        score += 20
    score += min(10, int(state.get("event_count", 1) or 1) - 1)
    if score >= 100:
        return "urgent", score
    if score >= 60:
        return "high", score
    return "normal", score


def _suggested_action(reasons: Sequence[str]) -> str:
    if "payload_no_more_nudges" in reasons:
        return "verify_or_restart_target_session_watcher"
    if "payload_operator_action_required" in reasons:
        return "operator_decision_required"
    if "wake_request_to_operator" in reasons:
        return "read_bridge_and_answer_request"
    return "review_operator_addressed_event"


def _task_key(event: Mapping[str, Any]) -> str:
    task_id = _task_id(event).strip()
    return task_id or f"event:{_event_agent(event)}:{_event_ts(event)}"


def _parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = _parse_utc(value)
    if parsed is None:
        raise _error("now must be an ISO-8601 timestamp")
    return parsed


def _severity(event: Mapping[str, Any]) -> str:
    return str(event.get("severity") or "").strip().lower()


def _payload_scalar(event: Mapping[str, Any], key: str) -> str:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    value = payload.get(key)
    if isinstance(value, (str, int, float, bool)):
        return _safe_text(value, limit=120)
    return ""


def _payload_text(event: Mapping[str, Any]) -> str:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    parts: list[str] = []
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)):
            parts.append(f"{key}={value}")
    return " ".join(parts)


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if token}


def _safe_text(value: object, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    if any(marker in text for marker in SENSITIVE_MARKERS):
        return "<redacted-private-marker>"
    text = WINDOWS_PATH_RE.sub("<redacted-path>", text)
    text = UNIX_PATH_RE.sub("<redacted-path>", text)
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _assert_path_free(report: Mapping[str, Any]) -> None:
    encoded = json.dumps(report, sort_keys=True)
    if WINDOWS_PATH_RE.search(encoded) or UNIX_PATH_RE.search(encoded):
        raise OperatorAttentionDigestError(
            {
                "ok": False,
                "decision": "operator_attention_digest_error",
                "errors": ["local path present in operator attention digest"],
            }
        )
    if any(marker in encoded for marker in SENSITIVE_MARKERS):
        raise OperatorAttentionDigestError(
            {
                "ok": False,
                "decision": "operator_attention_digest_error",
                "errors": ["private marker present in operator attention digest"],
            }
        )


def _error(message: str) -> OperatorAttentionDigestError:
    return OperatorAttentionDigestError(
        {
            "ok": False,
            "decision": "operator_attention_digest_error",
            "errors": [message],
        }
    )


def _print_human(report: Mapping[str, Any]) -> None:
    if not report.get("ok"):
        print("operator attention digest failed", file=sys.stderr)
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
        return
    print(f"operator attention items: {report.get('attention_count', 0)}")
    for row in report.get("items", []):
        if not isinstance(row, Mapping):
            continue
        print(
            "- "
            f"{row.get('priority')} {row.get('source_agent')} "
            f"{row.get('task_id')} {row.get('status')} "
            f"age={row.get('age_minutes')}m"
        )


if __name__ == "__main__":
    raise SystemExit(main())
