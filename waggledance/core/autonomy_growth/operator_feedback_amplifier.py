# SPDX-License-Identifier: BUSL-1.1
"""Operator-feedback amplifier planner for the autogrowth lane.

ADR-053 defines ``ops_feedback`` as the operator's fast path from "this
query needs help" into the low-risk autogrowth loop. This module implements
the pure planning half: validate the feedback event, apply the bounded
fast-track policy from the machine-readable contract, and produce a sanitized
``feedback_action_taken`` plan. It does not write bridge events, mutate solver
state, or grant runtime authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONTRACT_PATH = (
    ROOT / "docs" / "eig2" / "contracts" / "operator_feedback_amplifier.json"
)

OPS_FEEDBACK_EVENT_TYPE = "ops_feedback"
FEEDBACK_ACTION_TAKEN_EVENT_TYPE = "feedback_action_taken"
ACTION_SCHEMA_VERSION = "operator_feedback_action_plan.v1"
GAP_SIGNAL_SCHEMA_VERSION = "operator_feedback_gap_signal.v1"
PROBE_INTENT_SCHEMA_VERSION = "operator_feedback_adversarial_probe_intent.v1"


class OperatorFeedbackValidationError(ValueError):
    """Raised when an ``ops_feedback`` event cannot be amplified."""


@dataclass(frozen=True)
class OperatorFeedbackPolicy:
    feedback_kinds: tuple[str, ...]
    priority_enum: tuple[str, ...]
    required_fields: tuple[str, ...]
    fast_track_canary_minutes: int
    fast_track_per_hour_max: int


@dataclass(frozen=True)
class OperatorFeedbackActionPlan:
    """Auditable action plan derived from one operator feedback event."""

    schema_version: str
    event_type: str
    action_id: str
    feedback_id: str
    feedback_kind: str
    query_class_hash: str
    operator_id: str
    priority: str
    lane: str
    scheduled_for_utc: str | None
    rate_limited: bool
    rate_limit_window_start_utc: str
    fast_track_canary_minutes: int
    feedback_digest: str
    action_kind: str
    gap_signal: Mapping[str, Any] | None
    adversarial_probe_intent: Mapping[str, Any] | None
    runtime_authority_granted: bool
    canary_activation_applied: bool
    bridge_event_written: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "action_id": self.action_id,
            "feedback_id": self.feedback_id,
            "feedback_kind": self.feedback_kind,
            "query_class_hash": self.query_class_hash,
            "operator_id": self.operator_id,
            "priority": self.priority,
            "lane": self.lane,
            "scheduled_for_utc": self.scheduled_for_utc,
            "rate_limited": self.rate_limited,
            "rate_limit_window_start_utc": self.rate_limit_window_start_utc,
            "fast_track_canary_minutes": self.fast_track_canary_minutes,
            "feedback_digest": self.feedback_digest,
            "action_kind": self.action_kind,
            "gap_signal": (
                dict(self.gap_signal) if self.gap_signal is not None else None
            ),
            "adversarial_probe_intent": (
                dict(self.adversarial_probe_intent)
                if self.adversarial_probe_intent is not None
                else None
            ),
            "runtime_authority_granted": self.runtime_authority_granted,
            "canary_activation_applied": self.canary_activation_applied,
            "bridge_event_written": self.bridge_event_written,
        }


def load_operator_feedback_policy(
    contract_path: Path | str = DEFAULT_CONTRACT_PATH,
) -> OperatorFeedbackPolicy:
    """Load the ADR-053 event policy from the checked-in JSON contract."""

    path = Path(contract_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise OperatorFeedbackValidationError(
            f"operator feedback contract load failed: {exc!r}"
        ) from exc

    defaults = _require_mapping("policy_defaults", raw.get("policy_defaults"))
    return OperatorFeedbackPolicy(
        feedback_kinds=tuple(_require_string_list(
            "feedback_kinds",
            raw.get("feedback_kinds"),
        )),
        priority_enum=tuple(_require_string_list(
            "priority_enum",
            raw.get("priority_enum"),
        )),
        required_fields=tuple(_require_string_list(
            "required_fields",
            raw.get("required_fields"),
        )),
        fast_track_canary_minutes=_require_positive_int(
            "policy_defaults.fast_track_canary_minutes",
            defaults.get("fast_track_canary_minutes"),
        ),
        fast_track_per_hour_max=_require_positive_int(
            "policy_defaults.fast_track_per_hour_max",
            defaults.get("fast_track_per_hour_max"),
        ),
    )


def validate_operator_feedback_event(
    event: Mapping[str, Any],
    *,
    policy: OperatorFeedbackPolicy | None = None,
) -> dict[str, str]:
    """Validate and normalize one ``ops_feedback`` event.

    Extra fields are intentionally ignored. The returned mapping contains only
    contract fields that downstream action planning is allowed to echo.
    """

    policy = policy or load_operator_feedback_policy()
    if not isinstance(event, Mapping):
        raise OperatorFeedbackValidationError("ops_feedback event must be a mapping")

    missing = [
        field for field in policy.required_fields
        if field not in event or not _non_empty_string(event.get(field))
    ]
    if missing:
        raise OperatorFeedbackValidationError(
            "ops_feedback missing required field(s): " + ", ".join(sorted(missing))
        )

    event_type = _clean_string(event["event_type"])
    if event_type != OPS_FEEDBACK_EVENT_TYPE:
        raise OperatorFeedbackValidationError(
            f"event_type must be {OPS_FEEDBACK_EVENT_TYPE!r}"
        )

    feedback_kind = _clean_string(event["feedback_kind"])
    if feedback_kind not in policy.feedback_kinds:
        raise OperatorFeedbackValidationError(
            f"feedback_kind must be one of {sorted(policy.feedback_kinds)!r}"
        )

    priority = _clean_string(event["priority"])
    if priority not in policy.priority_enum:
        raise OperatorFeedbackValidationError(
            f"priority must be one of {sorted(policy.priority_enum)!r}"
        )

    submitted = _parse_utc(_clean_string(event["submitted_at_utc"]))
    return {
        "event_type": event_type,
        "feedback_id": _clean_string(event["feedback_id"]),
        "feedback_kind": feedback_kind,
        "query_class_hash": _clean_string(event["query_class_hash"]),
        "operator_id": _clean_string(event["operator_id"]),
        "priority": priority,
        "submitted_at_utc": _utc_iso(submitted),
    }


def amplify_operator_feedback(
    event: Mapping[str, Any],
    *,
    prior_events: Sequence[Mapping[str, Any]] = (),
    policy: OperatorFeedbackPolicy | None = None,
) -> OperatorFeedbackActionPlan:
    """Build a bounded action plan for one validated operator feedback event."""

    policy = policy or load_operator_feedback_policy()
    normalized = validate_operator_feedback_event(event, policy=policy)
    submitted = _parse_utc(normalized["submitted_at_utc"])
    window_start = submitted - timedelta(hours=1)
    prior_fast_track_count = _count_prior_fast_track_feedback(
        prior_events=prior_events,
        operator_id=normalized["operator_id"],
        window_start=window_start,
        submitted=submitted,
        policy=policy,
    )
    wants_fast_track = normalized["priority"] == "high"
    rate_limited = (
        wants_fast_track
        and prior_fast_track_count >= policy.fast_track_per_hour_max
    )
    fast_track = wants_fast_track and not rate_limited

    scheduled = (
        submitted + timedelta(minutes=policy.fast_track_canary_minutes)
        if fast_track
        else None
    )
    action_kind = _action_kind_for(normalized["feedback_kind"])
    feedback_digest = sha256_digest(normalized)
    action_id = _action_id(normalized["feedback_id"], normalized["feedback_kind"])
    gap_signal = _gap_signal_for(
        normalized=normalized,
        action_id=action_id,
        feedback_digest=feedback_digest,
        fast_track=fast_track,
        scheduled_for_utc=_utc_iso(scheduled) if scheduled else None,
    )
    probe_intent = _probe_intent_for(
        normalized=normalized,
        action_id=action_id,
        feedback_digest=feedback_digest,
    )

    return OperatorFeedbackActionPlan(
        schema_version=ACTION_SCHEMA_VERSION,
        event_type=FEEDBACK_ACTION_TAKEN_EVENT_TYPE,
        action_id=action_id,
        feedback_id=normalized["feedback_id"],
        feedback_kind=normalized["feedback_kind"],
        query_class_hash=normalized["query_class_hash"],
        operator_id=normalized["operator_id"],
        priority=normalized["priority"],
        lane="fast_track_canary" if fast_track else "normal_gap_queue",
        scheduled_for_utc=_utc_iso(scheduled) if scheduled else None,
        rate_limited=rate_limited,
        rate_limit_window_start_utc=_utc_iso(window_start),
        fast_track_canary_minutes=policy.fast_track_canary_minutes,
        feedback_digest=feedback_digest,
        action_kind=action_kind,
        gap_signal=gap_signal,
        adversarial_probe_intent=probe_intent,
        runtime_authority_granted=False,
        canary_activation_applied=False,
        bridge_event_written=False,
    )


def _count_prior_fast_track_feedback(
    *,
    prior_events: Sequence[Mapping[str, Any]],
    operator_id: str,
    window_start: datetime,
    submitted: datetime,
    policy: OperatorFeedbackPolicy,
) -> int:
    count = 0
    for prior in prior_events:
        try:
            normalized = validate_operator_feedback_event(prior, policy=policy)
        except OperatorFeedbackValidationError:
            continue
        if normalized["operator_id"] != operator_id:
            continue
        if normalized["priority"] != "high":
            continue
        prior_submitted = _parse_utc(normalized["submitted_at_utc"])
        if window_start <= prior_submitted < submitted:
            count += 1
    return count


def _gap_signal_for(
    *,
    normalized: Mapping[str, str],
    action_id: str,
    feedback_digest: str,
    fast_track: bool,
    scheduled_for_utc: str | None,
) -> dict[str, Any] | None:
    kind = normalized["feedback_kind"]
    if kind == "broken_route":
        return None

    gap_kind = (
        "needs_solver"
        if kind == "needs_solver"
        else "wrong_output_confirmed_gap"
    )
    return {
        "schema_version": GAP_SIGNAL_SCHEMA_VERSION,
        "signal_kind": "operator_feedback_gap_signal",
        "gap_kind": gap_kind,
        "feedback_id": normalized["feedback_id"],
        "action_id": action_id,
        "query_class_hash": normalized["query_class_hash"],
        "priority": normalized["priority"],
        "fast_track_canary": fast_track,
        "scheduled_for_utc": scheduled_for_utc,
        "feedback_digest": feedback_digest,
        "raw_query_exported": False,
        "runtime_authority_granted": False,
    }


def _probe_intent_for(
    *,
    normalized: Mapping[str, str],
    action_id: str,
    feedback_digest: str,
) -> dict[str, Any] | None:
    if normalized["feedback_kind"] != "wrong_output":
        return None
    return {
        "schema_version": PROBE_INTENT_SCHEMA_VERSION,
        "probe_source": "operator_feedback_wrong_output",
        "feedback_id": normalized["feedback_id"],
        "action_id": action_id,
        "query_class_hash": normalized["query_class_hash"],
        "added_by": normalized["operator_id"],
        "feedback_digest": feedback_digest,
        "raw_query_exported": False,
        "yaml_write_applied": False,
    }


def _action_kind_for(feedback_kind: str) -> str:
    if feedback_kind == "needs_solver":
        return "spawn_gap_signal"
    if feedback_kind == "broken_route":
        return "schedule_negative_tunnel_mining"
    if feedback_kind == "wrong_output":
        return "spawn_gap_signal_and_probe_intent"
    raise OperatorFeedbackValidationError(
        f"unsupported feedback_kind: {feedback_kind!r}"
    )


def _action_id(feedback_id: str, feedback_kind: str) -> str:
    safe_feedback = _safe_ref(feedback_id)
    safe_kind = _safe_ref(feedback_kind)
    return f"feedback_action:{safe_kind}:{safe_feedback}"


def _require_mapping(field_name: str, value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OperatorFeedbackValidationError(f"{field_name} must be a mapping")
    return value


def _require_string_list(field_name: str, value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise OperatorFeedbackValidationError(
            f"{field_name} must be a non-empty list"
        )
    out: list[str] = []
    for item in value:
        if not _non_empty_string(item):
            raise OperatorFeedbackValidationError(
                f"{field_name} entries must be non-empty strings"
            )
        out.append(str(item).strip())
    return out


def _require_positive_int(field_name: str, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OperatorFeedbackValidationError(
            f"{field_name} must be a positive int"
        )
    return value


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _clean_string(value: object) -> str:
    if not _non_empty_string(value):
        raise OperatorFeedbackValidationError("value must be a non-empty string")
    return str(value).strip()


def _parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise OperatorFeedbackValidationError(
            f"submitted_at_utc is not ISO-8601 UTC: {value!r}"
        ) from exc
    if dt.tzinfo is None:
        raise OperatorFeedbackValidationError(
            "submitted_at_utc must include timezone"
        )
    return dt.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_ref(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in ":._-" else "_"
        for char in value
    ).strip("_")
    if not safe or not safe[0].isalpha():
        safe = f"ref:{safe or 'unknown'}"
    return safe[:96]


__all__ = [
    "ACTION_SCHEMA_VERSION",
    "DEFAULT_CONTRACT_PATH",
    "FEEDBACK_ACTION_TAKEN_EVENT_TYPE",
    "GAP_SIGNAL_SCHEMA_VERSION",
    "OPS_FEEDBACK_EVENT_TYPE",
    "OperatorFeedbackActionPlan",
    "OperatorFeedbackPolicy",
    "OperatorFeedbackValidationError",
    "PROBE_INTENT_SCHEMA_VERSION",
    "amplify_operator_feedback",
    "load_operator_feedback_policy",
    "validate_operator_feedback_event",
]
