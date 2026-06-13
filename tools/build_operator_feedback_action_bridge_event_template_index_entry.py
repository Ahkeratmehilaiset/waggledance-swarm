#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a local operator-feedback action bridge-template index entry.

The index entry binds a template-only ``feedback_action_taken`` bridge-event
report to stable digests and selected safety metadata. It never appends bridge
events, enqueues scheduler work, ticks the scheduler, grants runtime authority,
or skips gates.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_operator_feedback_action_bridge_event_template import (  # noqa: E402
    ACTION_FALSE_FIELDS,
    CANDIDATE_FALSE_FIELDS,
    EVENT_STATUS as SOURCE_EVENT_STATUS,
    FEEDBACK_ACTION_TAKEN_EVENT_TYPE,
    FORBIDDEN_OUTPUT_MARKERS,
    GAP_SIGNAL_FALSE_FIELDS,
    RATE_LIMIT_SOURCE,
    REPORT_FALSE_FIELDS,
    TEMPLATE_VERSION as SOURCE_TEMPLATE_VERSION,
    validate_operator_feedback_action_bridge_event_template_report,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


INDEX_ENTRY_VERSION = (
    "wd.operator_feedback_action_bridge_event_template_index_entry.v1"
)
PROOF_ID = "operator_feedback_action_bridge_event_template_index_entry_v1"
TEMPLATE_ARTIFACT_ID = "operator_feedback_action_bridge_event_template"
AUTHORITY_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
    "scheduler_enqueue_allowed",
    "scheduler_tick_allowed",
    "scheduler_tick_executed",
    "queue_writes_applied",
    "control_plane_writes_applied",
    "bridge_event_written",
    "canary_activation_applied",
    "gate_skip_allowed",
    "promotion_gate_skip_allowed",
    "adversarial_gate_skip_allowed",
    "canary_gate_skip_allowed",
    "raw_query_exported",
    "artifact_payloads_included",
    "local_paths_recorded",
)


class IndexEntryError(ValueError):
    """Raised when index-entry inputs violate the local contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bridge-event-template-json",
        "--template-json",
        dest="bridge_event_template_json",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-06T12:30:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        template_bytes, template_report = _load_json_artifact(
            args.bridge_event_template_json,
            TEMPLATE_ARTIFACT_ID,
        )
        report = build_operator_feedback_action_bridge_event_template_index_entry(
            bridge_event_template_report=template_report,
            bridge_event_template_bytes=template_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except IndexEntryError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report("operator_feedback_action_template_index_entry_invalid")

    indent = 2 if args.pretty else None
    encoded = json.dumps(report, indent=indent, sort_keys=True, allow_nan=False)
    if args.json or report["ok"]:
        print(encoded)
    else:
        print(
            "operator feedback action bridge-template index entry FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_operator_feedback_action_bridge_event_template_index_entry(
    *,
    bridge_event_template_report: Mapping[str, Any],
    bridge_event_template_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free local index entry for the bridge-event template."""

    try:
        _assert_mapping(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
        _assert_no_forbidden_input(
            TEMPLATE_ARTIFACT_ID,
            bridge_event_template_report,
        )
        _assert_template_contract(bridge_event_template_report)
    except IndexEntryError as exc:
        return _failure_report(exc.code)

    template_sha256 = _sha256_hex(bridge_event_template_bytes)
    template_digest = sha256_digest(_plain_json_object(bridge_event_template_report))
    event = _mapping(bridge_event_template_report.get("bridge_event_template"))
    payload = _mapping(event.get("payload"))
    action = _mapping(payload.get("feedback_action_taken"))
    candidate = _mapping(payload.get("scheduler_candidate_artifact"))
    gap_signal = _mapping(action.get("gap_signal"))
    template_index_entry = {
        "artifact_id": TEMPLATE_ARTIFACT_ID,
        "template_version": SOURCE_TEMPLATE_VERSION,
        "template_only": True,
        "bridge_event_schema_validated": True,
        "template_report_sha256": template_sha256,
        "template_report_digest": template_digest,
        "event_digest": sha256_digest(_plain_json_object(event)),
        "payload_digest": sha256_digest(_plain_json_object(payload)),
        "feedback_action_digest": sha256_digest(_plain_json_object(action)),
        "scheduler_candidate_digest": sha256_digest(_plain_json_object(candidate)),
        "event_type": event.get("type"),
        "event_status": event.get("status"),
        "feedback_event_type": payload.get("event_type"),
        "feedback_id": payload.get("feedback_id"),
        "action_id": payload.get("action_id"),
        "verified_operator_id": payload.get("verified_operator_id"),
        "rate_limit_source": payload.get("rate_limit_source"),
        "operator_fast_track_count": _as_nonnegative_int(
            payload.get("operator_fast_track_count")
        ),
        "global_fast_track_count": _as_nonnegative_int(
            payload.get("global_fast_track_count")
        ),
        "global_fast_track_per_hour_max": _as_positive_int(
            payload.get("global_fast_track_per_hour_max")
        ),
        "queue_priority": candidate.get("queue_priority"),
        "gap_signal_queue_priority": gap_signal.get("queue_priority"),
        "fast_track_queue_priority_requested": (
            payload.get("fast_track_queue_priority_requested") is True
        ),
        "fast_track_is_queue_priority_only": True,
        "fast_track_grants_runtime_authority": False,
        "manual_review_required": True,
    }
    for field in AUTHORITY_FALSE_FIELDS:
        template_index_entry[field] = False

    entry = {
        "proof_id": PROOF_ID,
        "ok": True,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "template_version": SOURCE_TEMPLATE_VERSION,
        "artifact_count": 1,
        "artifacts": [
            {
                "artifact_id": TEMPLATE_ARTIFACT_ID,
                "role": "verified_no_authority_operator_feedback_action_template",
                "sha256": template_sha256,
                "byte_count": len(bridge_event_template_bytes),
                "digest": template_digest,
                "template_only": True,
                "manual_review_required": True,
                "raw_artifact_payload_included": False,
                "local_path_recorded": False,
            },
        ],
        "template_index_entry": template_index_entry,
        "operator_feedback_action": {
            "feedback_id": payload.get("feedback_id"),
            "action_id": payload.get("action_id"),
            "verified_operator_id": payload.get("verified_operator_id"),
            "rate_limit_source": payload.get("rate_limit_source"),
            "source_bridge_event_digest": payload.get("source_bridge_event_digest"),
            "preflight_digest": payload.get("preflight_digest"),
            "action_plan_digest": payload.get("action_plan_digest"),
            "queue_priority": candidate.get("queue_priority"),
            "fast_track_queue_priority_requested": (
                payload.get("fast_track_queue_priority_requested") is True
            ),
            "fast_track_is_queue_priority_only": True,
            "scheduler_enqueue_allowed": False,
            "scheduler_tick_allowed": False,
            "bridge_event_written": False,
            "runtime_authority_granted": False,
            "gate_skip_allowed": False,
        },
        "consistency": {
            "required_artifacts_present": [TEMPLATE_ARTIFACT_ID],
            "all_artifact_digests_recorded": True,
            "bridge_event_schema_validated": True,
            "template_report_validator": "pass",
            "rate_limit_source_check": "durable_bridge_log",
            "verified_operator_identity_check": "bridge_operator_identity",
            "global_fast_track_cap_recorded": True,
            "fast_track_queue_priority_only": True,
            "template_only": True,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "operator_boundary": _operator_boundary(
            payload.get("fast_track_queue_priority_requested") is True
        ),
        "reviewer_next_actions": [
            "review_operator_feedback_action_bridge_event_template_index_entry",
            "append_bridge_event_separately_only_after_manual_review",
        ],
        "template_only": True,
        "manual_review_required": True,
        "fast_track_queue_priority_requested": (
            payload.get("fast_track_queue_priority_requested") is True
        ),
        "fast_track_is_queue_priority_only": True,
        "blockers": [],
        "warnings": _safe_string_list(bridge_event_template_report.get("warnings")),
    }
    for field in AUTHORITY_FALSE_FIELDS:
        entry[field] = False

    errors = validate_operator_feedback_action_bridge_event_template_index_entry(entry)
    if errors:
        return _failure_report(errors[0])
    _assert_no_forbidden_output(json.dumps(entry, allow_nan=False, sort_keys=True))
    return entry


def validate_operator_feedback_action_bridge_event_template_index_entry(
    entry: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    entry_dict = _plain_json_object_or_none(entry)
    if entry_dict is None:
        return ["index_entry_not_json_object"]
    if entry_dict.get("ok") is not True:
        errors.append("index_entry_ok_not_true")
    if entry_dict.get("index_entry_version") != INDEX_ENTRY_VERSION:
        errors.append("index_entry_version_mismatch")
    if entry_dict.get("template_only") is not True:
        errors.append("index_entry_template_only_not_true")
    if entry_dict.get("manual_review_required") is not True:
        errors.append("index_entry_manual_review_required_not_true")
    if entry_dict.get("fast_track_is_queue_priority_only") is not True:
        errors.append("index_entry_fast_track_queue_priority_only_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if entry_dict.get(field) is not False:
            errors.append(f"index_entry_{field}_not_exact_false")
    artifacts = entry_dict.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        errors.append("index_entry_artifacts_invalid")
    template_index_entry = _plain_json_object_or_none(
        entry_dict.get("template_index_entry")
    )
    if template_index_entry is None:
        errors.append("template_index_entry_not_object")
    else:
        if template_index_entry.get("artifact_id") != TEMPLATE_ARTIFACT_ID:
            errors.append("template_index_entry_artifact_id_mismatch")
        if template_index_entry.get("rate_limit_source") != RATE_LIMIT_SOURCE:
            errors.append("template_index_entry_rate_limit_source_mismatch")
        if template_index_entry.get("feedback_event_type") != (
            FEEDBACK_ACTION_TAKEN_EVENT_TYPE
        ):
            errors.append("template_index_entry_feedback_event_type_mismatch")
        if template_index_entry.get("event_status") != SOURCE_EVENT_STATUS:
            errors.append("template_index_entry_event_status_mismatch")
        if template_index_entry.get("fast_track_is_queue_priority_only") is not True:
            errors.append("template_index_entry_fast_track_queue_priority_only_not_true")
        if template_index_entry.get("fast_track_grants_runtime_authority") is not False:
            errors.append("template_index_entry_fast_track_authority_not_false")
        for field in AUTHORITY_FALSE_FIELDS:
            if template_index_entry.get(field) is not False:
                errors.append(f"template_index_entry_{field}_not_exact_false")
    consistency = _plain_json_object_or_none(entry_dict.get("consistency"))
    if consistency is None:
        errors.append("consistency_not_object")
    else:
        if consistency.get("template_report_validator") != "pass":
            errors.append("consistency_template_report_validator_not_pass")
        if consistency.get("rate_limit_source_check") != "durable_bridge_log":
            errors.append("consistency_rate_limit_source_check_mismatch")
        if consistency.get("fast_track_queue_priority_only") is not True:
            errors.append("consistency_fast_track_queue_priority_only_not_true")
    return errors


def _assert_template_contract(template_report: Mapping[str, Any]) -> None:
    errors = validate_operator_feedback_action_bridge_event_template_report(
        template_report
    )
    if errors:
        raise IndexEntryError("bridge_event_template_invalid:" + _safe_reason(errors[0]))
    if template_report.get("ok") is not True:
        raise IndexEntryError("bridge_event_template_not_ok")
    if template_report.get("template_version") != SOURCE_TEMPLATE_VERSION:
        raise IndexEntryError("bridge_event_template_version_mismatch")
    if template_report.get("template_only") is not True:
        raise IndexEntryError("bridge_event_template_template_only_not_true")
    _expect_empty_items(
        template_report.get("blockers"),
        "bridge_event_template_blockers_present",
    )
    for field in REPORT_FALSE_FIELDS:
        if template_report.get(field) is not False:
            raise IndexEntryError(f"bridge_event_template_{field}_not_false")

    event = _mapping(template_report.get("bridge_event_template"))
    try:
        validate_event(event)
    except Exception as exc:
        raise IndexEntryError("bridge_event_template_schema_invalid") from exc
    if event.get("type") != "handoff":
        raise IndexEntryError("bridge_event_template_type_mismatch")
    if event.get("status") != SOURCE_EVENT_STATUS:
        raise IndexEntryError("bridge_event_template_status_mismatch")
    if event.get("paths") != []:
        raise IndexEntryError("bridge_event_template_paths_not_empty")
    if event.get("write_scope") != []:
        raise IndexEntryError("bridge_event_template_write_scope_not_empty")
    if event.get("pid") != 0:
        raise IndexEntryError("bridge_event_template_pid_not_zero")
    if event.get("cwd") != "template_not_emitted":
        raise IndexEntryError("bridge_event_template_cwd_mismatch")

    payload = _mapping(event.get("payload"))
    if payload.get("schema_version") != SOURCE_TEMPLATE_VERSION:
        raise IndexEntryError("bridge_event_template_payload_schema_mismatch")
    if payload.get("event_type") != FEEDBACK_ACTION_TAKEN_EVENT_TYPE:
        raise IndexEntryError("bridge_event_template_payload_event_type_mismatch")
    if payload.get("rate_limit_source") != RATE_LIMIT_SOURCE:
        raise IndexEntryError("bridge_event_template_rate_limit_source_mismatch")
    if not _is_verified_bridge_operator(payload.get("verified_operator_id")):
        raise IndexEntryError("bridge_event_template_operator_identity_unverified")
    if _as_positive_int(payload.get("global_fast_track_per_hour_max")) < 1:
        raise IndexEntryError("bridge_event_template_global_fast_track_cap_missing")
    if payload.get("fast_track_is_queue_priority_only") is not True:
        raise IndexEntryError("bridge_event_template_fast_track_not_queue_only")
    for field in REPORT_FALSE_FIELDS:
        if payload.get(field) is not False:
            raise IndexEntryError(f"bridge_event_template_payload_{field}_not_false")

    action = _mapping(payload.get("feedback_action_taken"))
    for field in ACTION_FALSE_FIELDS:
        if action.get(field) is not False:
            raise IndexEntryError(f"bridge_event_template_action_{field}_not_false")
    gap_signal = _mapping(action.get("gap_signal"))
    if gap_signal.get("queue_priority_only") is not True:
        raise IndexEntryError("bridge_event_template_gap_signal_not_queue_only")
    for field in GAP_SIGNAL_FALSE_FIELDS:
        if gap_signal.get(field) is not False:
            raise IndexEntryError(
                f"bridge_event_template_gap_signal_{field}_not_false"
            )
    candidate = _mapping(payload.get("scheduler_candidate_artifact"))
    if candidate.get("queue_priority") not in {"fast_track", "normal"}:
        raise IndexEntryError("bridge_event_template_candidate_priority_invalid")
    for field in CANDIDATE_FALSE_FIELDS:
        if candidate.get(field) is not False:
            raise IndexEntryError(f"bridge_event_template_candidate_{field}_not_false")
    boundary = _mapping(payload.get("authority_boundary"))
    if boundary.get("fast_track_is_queue_priority_only") is not True:
        raise IndexEntryError("bridge_event_template_boundary_not_queue_only")
    for field in REPORT_FALSE_FIELDS:
        if boundary.get(field) is not False:
            raise IndexEntryError(f"bridge_event_template_boundary_{field}_not_false")


def _operator_boundary(fast_track_requested: bool) -> dict[str, Any]:
    boundary = {
        "manual_review_required": True,
        "fast_track_queue_priority_requested": fast_track_requested,
        "fast_track_is_queue_priority_only": True,
        "fast_track_grants_runtime_authority": False,
    }
    for field in AUTHORITY_FALSE_FIELDS:
        boundary[field] = False
    return boundary


def _failure_report(reason: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "proof_id": PROOF_ID,
        "ok": False,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "fast_track_queue_priority_requested": False,
        "fast_track_is_queue_priority_only": True,
        "blockers": [
            "operator_feedback_action_bridge_event_template_index_entry_failed:"
            + _safe_reason(reason)
        ],
        "warnings": [],
    }
    for field in AUTHORITY_FALSE_FIELDS:
        report[field] = False
    return report


def _load_json_artifact(path: Path, label: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IndexEntryError(f"{label}_unreadable") from exc

    def reject_constant(value: str) -> None:
        raise ValueError(f"non_finite_json_constant:{value}")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise IndexEntryError(f"{label}_decode_error") from exc
    except json.JSONDecodeError as exc:
        raise IndexEntryError(f"{label}_json_error") from exc
    except ValueError as exc:
        raise IndexEntryError(f"{label}_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise IndexEntryError(f"{label}_not_object")
    return raw, parsed


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _assert_mapping(label: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise IndexEntryError(f"{label}_not_object")


def _assert_no_forbidden_input(label: str, value: Any) -> None:
    if _contains_forbidden_output_marker(value):
        raise IndexEntryError(f"{label}_contains_forbidden_marker")


def _assert_no_forbidden_output(encoded: str) -> None:
    if any(marker in encoded for marker in FORBIDDEN_OUTPUT_MARKERS):
        raise IndexEntryError("index_entry_contains_forbidden_output_marker")


def _contains_forbidden_output_marker(value: Any) -> bool:
    if isinstance(value, str):
        return any(marker in value for marker in FORBIDDEN_OUTPUT_MARKERS)
    if isinstance(value, Mapping):
        return any(_contains_forbidden_output_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_output_marker(item) for item in value)
    return False


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IndexEntryError("expected_mapping_missing")
    return value


def _plain_json_object(value: Any) -> dict[str, Any]:
    plain = _plain_json_object_or_none(value)
    if plain is None:
        raise IndexEntryError("json_object_required")
    return plain


def _plain_json_object_or_none(value: Any) -> dict[str, Any] | None:
    try:
        plain = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        return None
    if not isinstance(plain, dict):
        return None
    return plain


def _expect_empty_items(value: Any, code: str) -> None:
    if value not in ([], (), None):
        raise IndexEntryError(code)


def _is_verified_bridge_operator(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    prefix = "bridge:operator:"
    if not value.startswith(prefix):
        return False
    uuid = value[len(prefix):]
    return len(uuid) == 36 and uuid.count("-") == 4


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and not _contains_forbidden_output_marker(item):
            result.append(_safe_reason(item))
    return result


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IndexEntryError("nonnegative_int_required")
    return value


def _as_positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise IndexEntryError("positive_int_required")
    return value


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("expected UTC timestamp ending with Z")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("expected UTC timestamp")
    return parsed


def _safe_reason(reason: str) -> str:
    clean = str(reason).replace("\\", "_").replace("/", "_")
    clean = "".join(ch if ch.isalnum() or ch in "._:-" else "_" for ch in clean)
    return clean[:160] or "invalid"


if __name__ == "__main__":
    raise SystemExit(main())
