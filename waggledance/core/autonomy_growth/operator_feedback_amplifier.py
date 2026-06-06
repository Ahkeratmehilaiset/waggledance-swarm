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
import re
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
SCHEDULER_PREFLIGHT_SCHEMA_VERSION = "operator_feedback_scheduler_preflight.v1"
SCHEDULER_CANDIDATE_SCHEMA_VERSION = "operator_feedback_scheduler_candidate.v1"
RATE_LIMIT_SOURCE_DURABLE_BRIDGE_LOG = "durable_bridge_log"


class OperatorFeedbackValidationError(ValueError):
    """Raised when an ``ops_feedback`` event cannot be amplified."""


@dataclass(frozen=True)
class OperatorFeedbackPolicy:
    feedback_kinds: tuple[str, ...]
    priority_enum: tuple[str, ...]
    required_fields: tuple[str, ...]
    fast_track_canary_minutes: int
    fast_track_per_hour_max: int
    fast_track_global_per_hour_max: int


@dataclass(frozen=True)
class OperatorFeedbackActionPlan:
    """Auditable action plan derived from one operator feedback event."""

    schema_version: str
    event_type: str
    action_id: str
    feedback_id: str
    feedback_kind: str
    query_class_hash: str
    route_context_hash: str | None
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
            "route_context_hash": self.route_context_hash,
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


@dataclass(frozen=True)
class OperatorFeedbackSchedulerPreflight:
    """Scheduler-adjacent artifact for an operator feedback action plan.

    This is deliberately preflight-only: it proves identity/rate-limit state and
    renders a priority candidate, but it does not persist runtime gap signals,
    enqueue growth intents, or run the scheduler.
    """

    schema_version: str
    source_bridge_event_digest: str
    verified_operator_id: str
    rate_limit_source: str
    operator_fast_track_count: int
    global_fast_track_count: int
    global_fast_track_per_hour_max: int
    action_plan: OperatorFeedbackActionPlan
    scheduler_candidate_artifact: Mapping[str, Any]
    scheduler_enqueue_allowed: bool
    scheduler_tick_allowed: bool
    gate_skip_allowed: bool
    bridge_event_written: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_bridge_event_digest": self.source_bridge_event_digest,
            "verified_operator_id": self.verified_operator_id,
            "rate_limit_source": self.rate_limit_source,
            "operator_fast_track_count": self.operator_fast_track_count,
            "global_fast_track_count": self.global_fast_track_count,
            "global_fast_track_per_hour_max": (
                self.global_fast_track_per_hour_max
            ),
            "action_plan": self.action_plan.to_dict(),
            "scheduler_candidate_artifact": dict(
                self.scheduler_candidate_artifact
            ),
            "scheduler_enqueue_allowed": self.scheduler_enqueue_allowed,
            "scheduler_tick_allowed": self.scheduler_tick_allowed,
            "gate_skip_allowed": self.gate_skip_allowed,
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
        fast_track_global_per_hour_max=_require_positive_int(
            "policy_defaults.fast_track_global_per_hour_max",
            defaults.get("fast_track_global_per_hour_max"),
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

    feedback_id = _validate_ref("feedback_id", _clean_string(event["feedback_id"]))
    query_class_hash = _validate_sha256_ref(
        "query_class_hash",
        _clean_string(event["query_class_hash"]),
    )
    route_context_hash = _normalize_route_context_hash(event, feedback_kind)
    submitted = _parse_utc(_clean_string(event["submitted_at_utc"]))
    return {
        "event_type": event_type,
        "feedback_id": feedback_id,
        "feedback_kind": feedback_kind,
        "query_class_hash": query_class_hash,
        **(
            {"route_context_hash": route_context_hash}
            if route_context_hash is not None
            else {}
        ),
        "operator_id": _validate_ref(
            "operator_id",
            _clean_string(event["operator_id"]),
        ),
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
    rate_counts = _count_prior_fast_track_feedback(
        prior_events=prior_events,
        operator_id=normalized["operator_id"],
        window_start=window_start,
        submitted=submitted,
        policy=policy,
    )
    wants_fast_track = normalized["priority"] == "high"
    rate_limited = (
        wants_fast_track
        and (
            rate_counts["operator"] >= policy.fast_track_per_hour_max
            or rate_counts["global"] >= policy.fast_track_global_per_hour_max
        )
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
        route_context_hash=normalized.get("route_context_hash"),
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
) -> dict[str, int]:
    operator_count = 0
    global_count = 0
    for prior in prior_events:
        try:
            normalized = validate_operator_feedback_event(prior, policy=policy)
        except OperatorFeedbackValidationError:
            continue
        if normalized["priority"] != "high":
            continue
        prior_submitted = _parse_utc(normalized["submitted_at_utc"])
        if window_start <= prior_submitted < submitted:
            global_count += 1
            if normalized["operator_id"] == operator_id:
                operator_count += 1
    return {"operator": operator_count, "global": global_count}


def build_operator_feedback_scheduler_preflight(
    event: Mapping[str, Any],
    *,
    source_bridge_event: Mapping[str, Any],
    durable_bridge_events: Sequence[Mapping[str, Any]],
    policy: OperatorFeedbackPolicy | None = None,
) -> OperatorFeedbackSchedulerPreflight:
    """Build a no-authority scheduler preflight from durable bridge evidence.

    The current operator identity is derived from the bridge event envelope and
    the source event must already be present in the passed durable bridge-log
    window. This prevents callers from trusting a free-string ``operator_id`` or
    an in-memory rate-limit list when preparing scheduler-adjacent work.
    """

    policy = policy or load_operator_feedback_policy()
    source_digest, verified_operator_id = _verified_operator_from_bridge_log(
        source_bridge_event=source_bridge_event,
        durable_bridge_events=durable_bridge_events,
    )

    source_event_with_identity = _source_ops_feedback_event_from_bridge_payload(
        source_bridge_event=source_bridge_event,
        verified_operator_id=verified_operator_id,
    )
    source_normalized = validate_operator_feedback_event(
        source_event_with_identity,
        policy=policy,
    )

    supplied_event_with_identity = dict(event)
    provided_operator_id = _clean_string(
        supplied_event_with_identity.get("operator_id")
    )
    if provided_operator_id != verified_operator_id:
        raise OperatorFeedbackValidationError(
            "operator_id must match verified bridge identity"
        )
    supplied_event_with_identity["operator_id"] = verified_operator_id
    supplied_normalized = validate_operator_feedback_event(
        supplied_event_with_identity,
        policy=policy,
    )
    if supplied_normalized != source_normalized:
        mismatched = sorted(
            field
            for field in sorted(source_normalized.keys() | supplied_normalized.keys())
            if source_normalized.get(field) != supplied_normalized.get(field)
        )
        raise OperatorFeedbackValidationError(
            "ops_feedback event must match durable source payload: "
            + ", ".join(mismatched)
        )

    durable_prior_events = _extract_durable_ops_feedback_events(
        durable_bridge_events=durable_bridge_events,
        exclude_source_digest=source_digest,
    )
    normalized = source_normalized
    submitted = _parse_utc(normalized["submitted_at_utc"])
    window_start = submitted - timedelta(hours=1)
    rate_counts = _count_prior_fast_track_feedback(
        prior_events=durable_prior_events,
        operator_id=verified_operator_id,
        window_start=window_start,
        submitted=submitted,
        policy=policy,
    )
    action_plan = amplify_operator_feedback(
        source_event_with_identity,
        prior_events=durable_prior_events,
        policy=policy,
    )
    candidate = _scheduler_candidate_artifact_for(
        action_plan=action_plan,
        source_bridge_event_digest=source_digest,
        verified_operator_id=verified_operator_id,
    )
    return OperatorFeedbackSchedulerPreflight(
        schema_version=SCHEDULER_PREFLIGHT_SCHEMA_VERSION,
        source_bridge_event_digest=source_digest,
        verified_operator_id=verified_operator_id,
        rate_limit_source=RATE_LIMIT_SOURCE_DURABLE_BRIDGE_LOG,
        operator_fast_track_count=rate_counts["operator"],
        global_fast_track_count=rate_counts["global"],
        global_fast_track_per_hour_max=policy.fast_track_global_per_hour_max,
        action_plan=action_plan,
        scheduler_candidate_artifact=candidate,
        scheduler_enqueue_allowed=False,
        scheduler_tick_allowed=False,
        gate_skip_allowed=False,
        bridge_event_written=False,
    )


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
        "queue_priority": "fast_track" if fast_track else "normal",
        "queue_priority_only": True,
        "gate_skip_allowed": False,
        "promotion_gate_skip_allowed": False,
        "adversarial_gate_skip_allowed": False,
        "canary_gate_skip_allowed": False,
        "scheduled_for_utc": scheduled_for_utc,
        "feedback_digest": feedback_digest,
        "raw_query_exported": False,
        "runtime_authority_granted": False,
    }


def _scheduler_candidate_artifact_for(
    *,
    action_plan: OperatorFeedbackActionPlan,
    source_bridge_event_digest: str,
    verified_operator_id: str,
) -> dict[str, Any]:
    fast_track_priority = (
        action_plan.priority == "high"
        and action_plan.rate_limited is False
        and action_plan.gap_signal is not None
    )
    return {
        "schema_version": SCHEDULER_CANDIDATE_SCHEMA_VERSION,
        "candidate_kind": "operator_feedback_gap_signal",
        "action_id": action_plan.action_id,
        "feedback_id": action_plan.feedback_id,
        "feedback_kind": action_plan.feedback_kind,
        "query_class_hash": action_plan.query_class_hash,
        "route_context_hash": action_plan.route_context_hash,
        "verified_operator_id": verified_operator_id,
        "source_bridge_event_digest": source_bridge_event_digest,
        "queue_priority": "fast_track" if fast_track_priority else "normal",
        "priority_weight": 100 if fast_track_priority else 0,
        "fast_track_priority": fast_track_priority,
        "rate_limited": action_plan.rate_limited,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "bridge_event_written": False,
        "runtime_authority_granted": False,
        "gate_skip_allowed": False,
        "promotion_gate_skip_allowed": False,
        "adversarial_gate_skip_allowed": False,
        "canary_gate_skip_allowed": False,
        "raw_query_exported": False,
    }


def _verified_operator_from_bridge_log(
    *,
    source_bridge_event: Mapping[str, Any],
    durable_bridge_events: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    source_digest = sha256_digest(source_bridge_event)
    durable_digests = {
        sha256_digest(event)
        for event in durable_bridge_events
        if isinstance(event, Mapping)
    }
    if source_digest not in durable_digests:
        raise OperatorFeedbackValidationError(
            "source bridge event must be present in durable bridge log"
        )

    agent = _clean_string(source_bridge_event.get("agent"))
    if agent != "operator":
        raise OperatorFeedbackValidationError(
            "operator feedback scheduler preflight requires operator bridge agent"
        )
    _parse_utc(_clean_string(source_bridge_event.get("ts_utc")))
    agent_uuid = source_bridge_event.get("agent_uuid")
    session_id = source_bridge_event.get("session_id")
    if _non_empty_string(agent_uuid):
        value = _clean_string(agent_uuid).lower()
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}",
            value,
        ):
            raise OperatorFeedbackValidationError(
                "operator bridge agent_uuid is malformed"
            )
        return source_digest, _validate_ref("operator_id", f"bridge:{agent}:{value}")
    if _non_empty_string(session_id):
        safe_session = _safe_ref(_clean_string(session_id))
        return (
            source_digest,
            _validate_ref("operator_id", f"bridge:{agent}:{safe_session}"),
        )
    return source_digest, _validate_ref("operator_id", f"bridge:{agent}")


def _extract_durable_ops_feedback_events(
    *,
    durable_bridge_events: Sequence[Mapping[str, Any]],
    exclude_source_digest: str,
) -> list[Mapping[str, Any]]:
    events: list[Mapping[str, Any]] = []
    for bridge_event in durable_bridge_events:
        if not isinstance(bridge_event, Mapping):
            continue
        if sha256_digest(bridge_event) == exclude_source_digest:
            continue
        payload = bridge_event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if payload.get("event_type") == OPS_FEEDBACK_EVENT_TYPE:
            events.append(payload)
            continue
        for key in ("ops_feedback", "feedback_event"):
            nested = payload.get(key)
            if (
                isinstance(nested, Mapping)
                and nested.get("event_type") == OPS_FEEDBACK_EVENT_TYPE
            ):
                events.append(nested)
                break
    return events


def _source_ops_feedback_event_from_bridge_payload(
    *,
    source_bridge_event: Mapping[str, Any],
    verified_operator_id: str,
) -> Mapping[str, Any]:
    payload = source_bridge_event.get("payload")
    if not isinstance(payload, Mapping):
        raise OperatorFeedbackValidationError(
            "source bridge event payload must be a mapping"
        )

    source_event: Mapping[str, Any] | None = None
    if payload.get("event_type") == OPS_FEEDBACK_EVENT_TYPE:
        source_event = payload
    else:
        for key in ("ops_feedback", "feedback_event"):
            nested = payload.get(key)
            if (
                isinstance(nested, Mapping)
                and nested.get("event_type") == OPS_FEEDBACK_EVENT_TYPE
            ):
                source_event = nested
                break
    if source_event is None:
        raise OperatorFeedbackValidationError(
            "source bridge event payload must contain ops_feedback"
        )

    event_with_identity = dict(source_event)
    provided_operator_id = _clean_string(event_with_identity.get("operator_id"))
    if provided_operator_id != verified_operator_id:
        raise OperatorFeedbackValidationError(
            "source ops_feedback operator_id must match verified bridge identity"
        )
    event_with_identity["operator_id"] = verified_operator_id
    return event_with_identity


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


def _normalize_route_context_hash(
    event: Mapping[str, Any],
    feedback_kind: str,
) -> str | None:
    if feedback_kind != "broken_route":
        return None
    if not _non_empty_string(event.get("route_context_hash")):
        raise OperatorFeedbackValidationError(
            "route_context_required: broken_route feedback requires "
            "route_context_hash"
        )
    return _validate_sha256_ref(
        "route_context_hash",
        _clean_string(event["route_context_hash"]),
    )


def _validate_sha256_ref(field_name: str, value: str) -> str:
    if not value.startswith("sha256:"):
        raise OperatorFeedbackValidationError(
            f"{field_name} must be a sha256: hex digest"
        )
    digest = value.removeprefix("sha256:")
    if len(digest) != 64 or any(
        char not in "0123456789abcdefABCDEF" for char in digest
    ):
        raise OperatorFeedbackValidationError(
            f"{field_name} must be a sha256: hex digest"
        )
    return "sha256:" + digest.lower()


def _validate_ref(field_name: str, value: str) -> str:
    if len(value) > 96:
        raise OperatorFeedbackValidationError(f"{field_name} is too long")
    if not value[0].isalnum():
        raise OperatorFeedbackValidationError(
            f"{field_name} must start with an alphanumeric character"
        )
    if any(not (char.isalnum() or char in ":._-") for char in value):
        raise OperatorFeedbackValidationError(
            f"{field_name} contains unsupported characters"
        )
    return value


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
    "OperatorFeedbackSchedulerPreflight",
    "OperatorFeedbackValidationError",
    "PROBE_INTENT_SCHEMA_VERSION",
    "RATE_LIMIT_SOURCE_DURABLE_BRIDGE_LOG",
    "SCHEDULER_CANDIDATE_SCHEMA_VERSION",
    "SCHEDULER_PREFLIGHT_SCHEMA_VERSION",
    "amplify_operator_feedback",
    "build_operator_feedback_scheduler_preflight",
    "load_operator_feedback_policy",
    "validate_operator_feedback_event",
]
