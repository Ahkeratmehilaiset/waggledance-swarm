# SPDX-License-Identifier: BUSL-1.1
"""Verify operator-feedback wiring guardrails from durable bridge events.

This verifier is intentionally outside the scheduler path. It gives RCO and
driver automation a small, reproducible check that the operator-feedback
fast-track lane remains identity-bound, log-derived, globally capped, and
limited to queue priority.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_event_schema import validate_event_line

DEFAULT_EVENTS_PATH = ROOT / ".agent-bridge" / "shared" / "events.jsonl"
DEFAULT_CONTRACT_PATH = (
    ROOT / "docs" / "eig2" / "contracts" / "operator_feedback_amplifier.json"
)
SCHEMA_VERSION = "operator_feedback_wiring_guard.v1"
OPS_FEEDBACK_EVENT_TYPE = "ops_feedback"
FEEDBACK_ACTION_TAKEN_EVENT_TYPE = "feedback_action_taken"
ACTION_SCHEMA_VERSION = "operator_feedback_action_plan.v1"
VERIFIED_OPERATOR_ID_PREFIX = "bridge"
NESTED_RELEVANT_PAYLOAD_KEYS = (
    "ops_feedback",
    "feedback_event",
    "feedback_action",
    "feedback_action_taken",
    "action_plan",
)
GATE_SKIP_TOKENS = (
    "gate_skip",
    "skip_gate",
    "skip_adversarial",
    "adversarial_skip",
    "skip_counterfactual",
    "counterfactual_skip",
    "skip_validation",
    "validation_skip",
    "bypass_gate",
    "gate_bypass",
    "bypass_adversarial",
    "bypass_counterfactual",
    "bypass_validation",
    "disable_gate",
    "gate_disabled",
)
AUTHORITY_TRUE_KEYS = frozenset({
    "runtime_authority_granted",
    "canary_activation_applied",
    "adversarial_gate_skipped",
    "counterfactual_gate_skipped",
    "validation_gate_skipped",
})


@dataclass(frozen=True)
class GuardIssue:
    code: str
    message: str
    line_no: int | None = None
    feedback_id: str = ""
    operator_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "line_no": self.line_no,
            "feedback_id": self.feedback_id,
            "operator_id": self.operator_id,
        }


@dataclass(frozen=True)
class BridgeEnvelope:
    line_no: int
    event: Mapping[str, Any]
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class FeedbackRecord:
    line_no: int
    feedback_id: str
    operator_id: str
    submitted_at_utc: datetime
    priority: str
    expected_fast_track: bool


@dataclass(frozen=True)
class ActionRecord:
    line_no: int
    feedback_id: str
    operator_id: str
    submitted_at_utc: datetime | None
    fast_track: bool
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class GuardReport:
    schema_version: str
    events_path: str
    durable_rate_limit_source: str
    checked_bridge_events: int
    ops_feedback_events: int
    feedback_action_events: int
    per_operator_fast_track_per_hour_max: int
    global_fast_track_per_hour_max: int
    operator_identity_ok: bool
    durable_rate_limit_ok: bool
    global_fast_track_cap_ok: bool
    fast_track_gate_skip_ok: bool
    ok: bool
    issues: tuple[GuardIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "events_path": self.events_path,
            "durable_rate_limit_source": self.durable_rate_limit_source,
            "checked_bridge_events": self.checked_bridge_events,
            "ops_feedback_events": self.ops_feedback_events,
            "feedback_action_events": self.feedback_action_events,
            "per_operator_fast_track_per_hour_max": (
                self.per_operator_fast_track_per_hour_max
            ),
            "global_fast_track_per_hour_max": self.global_fast_track_per_hour_max,
            "operator_identity_ok": self.operator_identity_ok,
            "durable_rate_limit_ok": self.durable_rate_limit_ok,
            "global_fast_track_cap_ok": self.global_fast_track_cap_ok,
            "fast_track_gate_skip_ok": self.fast_track_gate_skip_ok,
            "ok": self.ok,
            "issue_count": len(self.issues),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def verify_operator_feedback_wiring_guard(
    events_path: str | Path = DEFAULT_EVENTS_PATH,
    *,
    tail: int | None = None,
    per_operator_fast_track_per_hour_max: int | None = None,
    global_fast_track_per_hour_max: int | None = None,
) -> GuardReport:
    """Evaluate operator-feedback wiring invariants from bridge JSONL."""

    path = Path(events_path)
    per_operator_cap = (
        int(per_operator_fast_track_per_hour_max)
        if per_operator_fast_track_per_hour_max is not None
        else _load_per_operator_fast_track_cap()
    )
    global_cap = (
        int(global_fast_track_per_hour_max)
        if global_fast_track_per_hour_max is not None
        else _load_global_fast_track_cap()
    )
    issues: list[GuardIssue] = []
    envelopes = _read_bridge_envelopes(path, tail=tail, issues=issues)
    feedback_records = _collect_feedback_records(envelopes, issues)
    action_records = _collect_action_records(envelopes)
    issues.extend(_durable_rate_limit_issues(
        feedback_records,
        action_records,
        per_operator_cap=per_operator_cap,
    ))
    issues.extend(_global_fast_track_cap_issues(
        action_records,
        global_cap=global_cap,
    ))
    issues.extend(_fast_track_gate_skip_issues(action_records))

    operator_identity_ok = not any(
        issue.code.startswith("operator_id_") for issue in issues
    )
    durable_rate_limit_ok = not any(
        issue.code == "per_operator_fast_track_cap_exceeded" for issue in issues
    )
    global_fast_track_cap_ok = not any(
        issue.code == "global_fast_track_cap_exceeded" for issue in issues
    )
    fast_track_gate_skip_ok = not any(
        issue.code in {"fast_track_gate_skip", "fast_track_authority_grant"}
        for issue in issues
    )
    ok = (
        operator_identity_ok
        and durable_rate_limit_ok
        and global_fast_track_cap_ok
        and fast_track_gate_skip_ok
        and not any(issue.code == "invalid_bridge_event" for issue in issues)
    )
    return GuardReport(
        schema_version=SCHEMA_VERSION,
        events_path=str(path),
        durable_rate_limit_source="bridge_event_log",
        checked_bridge_events=len(envelopes),
        ops_feedback_events=len(feedback_records),
        feedback_action_events=len(action_records),
        per_operator_fast_track_per_hour_max=per_operator_cap,
        global_fast_track_per_hour_max=global_cap,
        operator_identity_ok=operator_identity_ok,
        durable_rate_limit_ok=durable_rate_limit_ok,
        global_fast_track_cap_ok=global_fast_track_cap_ok,
        fast_track_gate_skip_ok=fast_track_gate_skip_ok,
        ok=ok,
        issues=tuple(issues),
    )


def _read_bridge_envelopes(
    path: Path,
    *,
    tail: int | None,
    issues: list[GuardIssue],
) -> list[BridgeEnvelope]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    selected = _tail_lines(lines, tail=tail)
    envelopes: list[BridgeEnvelope] = []
    for line_no, line in selected:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(GuardIssue(
                code="invalid_bridge_event",
                message=f"line {line_no}: invalid JSON: {exc.msg}",
                line_no=line_no,
            ))
            continue
        if not isinstance(raw, Mapping):
            continue
        if not _raw_event_is_relevant(raw):
            continue
        try:
            event = validate_event_line(line, line_no=line_no).model_dump()
        except ValueError as exc:
            issues.append(GuardIssue(
                code="invalid_bridge_event",
                message=str(exc),
                line_no=line_no,
            ))
            continue
        payload = _relevant_payload_from(event.get("payload"))
        envelopes.append(BridgeEnvelope(
            line_no=line_no,
            event=event,
            payload=payload or {},
        ))
    return envelopes


def _raw_event_is_relevant(raw: Mapping[str, Any]) -> bool:
    if _relevant_payload_from(raw.get("payload")) is not None:
        return True
    return raw.get("type") in {
        OPS_FEEDBACK_EVENT_TYPE,
        FEEDBACK_ACTION_TAKEN_EVENT_TYPE,
    }


def _relevant_payload_from(payload: Any) -> Mapping[str, Any] | None:
    for candidate in _payload_candidates(payload):
        if (
            candidate.get("event_type")
            in {OPS_FEEDBACK_EVENT_TYPE, FEEDBACK_ACTION_TAKEN_EVENT_TYPE}
            or candidate.get("schema_version") == ACTION_SCHEMA_VERSION
        ):
            return candidate
    return None


def _payload_candidates(payload: Any) -> Iterable[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return
    yield payload
    for key in NESTED_RELEVANT_PAYLOAD_KEYS:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            yield nested


def _collect_feedback_records(
    envelopes: Sequence[BridgeEnvelope],
    issues: list[GuardIssue],
) -> list[FeedbackRecord]:
    records: list[FeedbackRecord] = []
    provisional: list[tuple[int, Mapping[str, Any], bool]] = []
    for envelope in envelopes:
        payload = envelope.payload
        if payload.get("event_type") != OPS_FEEDBACK_EVENT_TYPE:
            continue
        feedback_id = _string(payload.get("feedback_id"))
        operator_id = _string(payload.get("operator_id"))
        if not _has_verified_operator_identity(envelope.event):
            issues.append(GuardIssue(
                code="operator_id_unverified_bridge_event",
                message="bridge event lacks agent_uuid/session_id identity binding",
                line_no=envelope.line_no,
                feedback_id=feedback_id,
                operator_id=operator_id,
            ))
            continue
        expected_operator_id = _verified_operator_id(envelope.event)
        if operator_id != expected_operator_id:
            issues.append(GuardIssue(
                code="operator_id_not_verified_bridge_identity",
                message=(
                    "operator_id must match "
                    f"{VERIFIED_OPERATOR_ID_PREFIX}:<agent>:<agent_uuid>"
                ),
                line_no=envelope.line_no,
                feedback_id=feedback_id,
                operator_id=operator_id,
            ))
            continue
        submitted = _parse_utc(_string(payload.get("submitted_at_utc")))
        if submitted is None:
            issues.append(GuardIssue(
                code="operator_feedback_missing_submitted_at",
                message="ops_feedback submitted_at_utc must be UTC ISO-8601",
                line_no=envelope.line_no,
                feedback_id=feedback_id,
                operator_id=operator_id,
            ))
            continue
        provisional.append((
            envelope.line_no,
            payload,
            _string(payload.get("priority")) == "high",
        ))

    provisional.sort(key=lambda item: _parse_utc(
        _string(item[1].get("submitted_at_utc")),
    ) or datetime.min.replace(tzinfo=timezone.utc))
    per_operator_prior: dict[str, list[datetime]] = {}
    for line_no, payload, wants_fast_track in provisional:
        operator_id = _string(payload.get("operator_id"))
        submitted = _parse_utc(_string(payload.get("submitted_at_utc")))
        if submitted is None:
            continue
        prior = [
            item for item in per_operator_prior.get(operator_id, [])
            if submitted - timedelta(hours=1) <= item < submitted
        ]
        expected_fast_track = wants_fast_track
        records.append(FeedbackRecord(
            line_no=line_no,
            feedback_id=_string(payload.get("feedback_id")),
            operator_id=operator_id,
            submitted_at_utc=submitted,
            priority=_string(payload.get("priority")),
            expected_fast_track=expected_fast_track,
        ))
        if wants_fast_track:
            per_operator_prior[operator_id] = [*prior, submitted]
        else:
            per_operator_prior[operator_id] = prior
    return records


def _collect_action_records(envelopes: Sequence[BridgeEnvelope]) -> list[ActionRecord]:
    records: list[ActionRecord] = []
    for envelope in envelopes:
        payload = envelope.payload
        if not _is_action_payload(payload):
            continue
        records.append(ActionRecord(
            line_no=envelope.line_no,
            feedback_id=_string(payload.get("feedback_id")),
            operator_id=_string(payload.get("operator_id")),
            submitted_at_utc=_parse_utc(_string(
                payload.get("submitted_at_utc")
                or payload.get("scheduled_for_utc")
                or envelope.event.get("ts_utc")
            )),
            fast_track=_is_fast_track_action(payload),
            payload=payload,
        ))
    return records


def _durable_rate_limit_issues(
    feedback_records: Sequence[FeedbackRecord],
    action_records: Sequence[ActionRecord],
    *,
    per_operator_cap: int,
) -> list[GuardIssue]:
    issues: list[GuardIssue] = []
    actions_by_feedback = {record.feedback_id: record for record in action_records}
    prior_by_operator: dict[str, list[datetime]] = {}
    for record in sorted(feedback_records, key=lambda item: item.submitted_at_utc):
        prior = [
            item for item in prior_by_operator.get(record.operator_id, [])
            if record.submitted_at_utc - timedelta(hours=1) <= item
            < record.submitted_at_utc
        ]
        over_cap = record.priority == "high" and len(prior) >= per_operator_cap
        action = actions_by_feedback.get(record.feedback_id)
        if over_cap and action is not None and action.fast_track:
            issues.append(GuardIssue(
                code="per_operator_fast_track_cap_exceeded",
                message=(
                    "fast-track action exceeds durable per-operator "
                    "bridge-log hour cap"
                ),
                line_no=action.line_no,
                feedback_id=record.feedback_id,
                operator_id=record.operator_id,
            ))
        if record.priority == "high":
            prior_by_operator[record.operator_id] = [*prior, record.submitted_at_utc]
        else:
            prior_by_operator[record.operator_id] = prior
    return issues


def _global_fast_track_cap_issues(
    action_records: Sequence[ActionRecord],
    *,
    global_cap: int,
) -> list[GuardIssue]:
    issues: list[GuardIssue] = []
    fast_track_actions = sorted(
        (record for record in action_records if record.fast_track and record.submitted_at_utc),
        key=lambda item: item.submitted_at_utc or datetime.min.replace(tzinfo=timezone.utc),
    )
    prior: list[ActionRecord] = []
    for record in fast_track_actions:
        assert record.submitted_at_utc is not None
        prior = [
            item for item in prior
            if item.submitted_at_utc is not None
            and record.submitted_at_utc - timedelta(hours=1)
            <= item.submitted_at_utc
            < record.submitted_at_utc
        ]
        if len(prior) >= global_cap:
            issues.append(GuardIssue(
                code="global_fast_track_cap_exceeded",
                message="fast-track action exceeds global durable hour cap",
                line_no=record.line_no,
                feedback_id=record.feedback_id,
                operator_id=record.operator_id,
            ))
        prior.append(record)
    return issues


def _fast_track_gate_skip_issues(
    action_records: Sequence[ActionRecord],
) -> list[GuardIssue]:
    issues: list[GuardIssue] = []
    for record in action_records:
        if not record.fast_track:
            continue
        for path, value in _walk_mapping(record.payload):
            key = path[-1]
            normalized_key = key.lower().replace("-", "_")
            if normalized_key in AUTHORITY_TRUE_KEYS and value is True:
                issues.append(GuardIssue(
                    code="fast_track_authority_grant",
                    message="fast-track may not grant runtime authority",
                    line_no=record.line_no,
                    feedback_id=record.feedback_id,
                    operator_id=record.operator_id,
                ))
            if _looks_like_gate_skip(normalized_key, value):
                issues.append(GuardIssue(
                    code="fast_track_gate_skip",
                    message="fast-track may only affect queue priority, not gates",
                    line_no=record.line_no,
                    feedback_id=record.feedback_id,
                    operator_id=record.operator_id,
                ))
    return issues


def _is_action_payload(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("event_type") == FEEDBACK_ACTION_TAKEN_EVENT_TYPE
        or payload.get("schema_version") == ACTION_SCHEMA_VERSION
    )


def _is_fast_track_action(payload: Mapping[str, Any]) -> bool:
    lane = _string(payload.get("lane")).lower()
    queue_priority = _string(payload.get("queue_priority")).lower()
    gap_signal = payload.get("gap_signal")
    return (
        lane in {"fast_track_canary", "fast_track", "priority"}
        or queue_priority in {"fast_track", "high"}
        or (
            isinstance(gap_signal, Mapping)
            and gap_signal.get("fast_track_canary") is True
        )
    )


def _has_verified_operator_identity(event: Mapping[str, Any]) -> bool:
    return bool(_string(event.get("agent")) and _string(event.get("agent_uuid"))
                and _string(event.get("session_id")))


def _verified_operator_id(event: Mapping[str, Any]) -> str:
    return (
        f"{VERIFIED_OPERATOR_ID_PREFIX}:"
        f"{_string(event.get('agent'))}:"
        f"{_string(event.get('agent_uuid'))}"
    )


def _looks_like_gate_skip(normalized_key: str, value: Any) -> bool:
    if value is False or value is None:
        return False
    if any(token in normalized_key for token in GATE_SKIP_TOKENS):
        return value is True or _string(value).lower() in {
            "true",
            "skip",
            "skipped",
            "bypass",
            "bypassed",
            "disabled",
        }
    if normalized_key in {"require_adversarial_gate", "require_counterfactual_gate"}:
        return value is False
    if normalized_key in {"gate_decision", "adversarial_gate", "counterfactual_gate"}:
        return _string(value).lower() in {"skip", "skipped", "bypass", "disabled"}
    return False


def _walk_mapping(
    mapping: Mapping[str, Any],
    *,
    prefix: tuple[str, ...] = (),
) -> Iterable[tuple[tuple[str, ...], Any]]:
    for key, value in mapping.items():
        path = (*prefix, str(key))
        yield path, value
        if isinstance(value, Mapping):
            yield from _walk_mapping(value, prefix=path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                item_path = (*path, str(index))
                yield item_path, item
                if isinstance(item, Mapping):
                    yield from _walk_mapping(item, prefix=item_path)


def _tail_lines(lines: Sequence[str], *, tail: int | None) -> list[tuple[int, str]]:
    numbered = list(enumerate(lines, start=1))
    if tail is None:
        return numbered
    return numbered[-max(int(tail), 0):]


def _load_per_operator_fast_track_cap() -> int:
    try:
        raw = json.loads(DEFAULT_CONTRACT_PATH.read_text(encoding="utf-8"))
        defaults = raw.get("policy_defaults")
        if isinstance(defaults, Mapping):
            value = defaults.get("fast_track_per_hour_max")
            if isinstance(value, int) and value > 0:
                return value
    except Exception:  # noqa: BLE001 - verifier has a conservative fallback
        pass
    return 10


def _load_global_fast_track_cap() -> int:
    try:
        raw = json.loads(DEFAULT_CONTRACT_PATH.read_text(encoding="utf-8"))
        defaults = raw.get("policy_defaults")
        if isinstance(defaults, Mapping):
            value = defaults.get("fast_track_global_per_hour_max")
            if isinstance(value, int) and value > 0:
                return value
    except Exception:  # noqa: BLE001 - verifier has a conservative fallback
        pass
    return 30


def _parse_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify operator-feedback wiring guardrails from bridge events.",
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--tail", type=int, default=None)
    parser.add_argument("--per-operator-fast-track-per-hour-max", type=int)
    parser.add_argument("--global-fast-track-per-hour-max", type=int)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    report = verify_operator_feedback_wiring_guard(
        args.events,
        tail=args.tail,
        per_operator_fast_track_per_hour_max=(
            args.per_operator_fast_track_per_hour_max
        ),
        global_fast_track_per_hour_max=args.global_fast_track_per_hour_max,
    )
    print(json.dumps(
        report.to_dict(),
        indent=2 if args.pretty else None,
        sort_keys=True,
    ))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
