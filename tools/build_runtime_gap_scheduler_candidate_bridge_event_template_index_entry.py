#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a local runtime gap scheduler-candidate bridge-template index entry.

The index entry binds a no-authority scheduler-candidate preview artifact to
its template-only bridge-event report. It records only digests, sizes, schema
references, and authority-boundary booleans; it never appends bridge events,
transports artifact payloads, enqueues scheduler work, or grants runtime
authority.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_runtime_gap_scheduler_candidate_bridge_event_template import (  # noqa: E402
    ARTIFACT_MEASUREMENT_SCOPE,
    ARTIFACT_SCHEMA_VERSION,
    ARTIFACT_VERSION,
    EVENT_STATUS as SOURCE_EVENT_STATUS,
    SOURCE_REPORT_MEASUREMENT_SCOPE,
    SOURCE_REPORT_SCHEMA_VERSION,
    SOURCE_REPORT_VERSION,
    TEMPLATE_VERSION as SOURCE_TEMPLATE_VERSION,
    build_runtime_gap_scheduler_candidate_bridge_event_template,
    validate_runtime_gap_scheduler_candidate_artifact,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


INDEX_ENTRY_VERSION = (
    "wd.runtime_gap_scheduler_candidate_bridge_event_template_index_entry.v1"
)
PROOF_ID = "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_v1"
ARTIFACT_ID = "runtime_gap_scheduler_candidate_artifact"
TEMPLATE_ARTIFACT_ID = "runtime_gap_scheduler_candidate_bridge_event_template"

SAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"(?:^|[^A-Za-z0-9])(?:[A-Za-z]:[\\/])")
FORBIDDEN_OUTPUT_MARKERS = (
    "http://",
    "https://",
    "C:/",
    "C:\\",
    "\\\\",
    "/home/",
    "/Users/",
    "PRIVATE_",
    "Authorization",
    "Bearer ",
    "secret",
    "password",
)
AUTHORITY_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
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
    "gate_skip_allowed",
    "promotion_gate_skip_allowed",
    "adversarial_gate_skip_allowed",
    "canary_gate_skip_allowed",
    "fast_track_priority",
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
        "--artifact-json",
        "--candidate-artifact-json",
        dest="artifact_json",
        required=True,
        type=Path,
    )
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
        help="Optional UTC timestamp override such as 2026-06-07T08:45:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifact_bytes, artifact = _load_json_artifact(
            args.artifact_json,
            ARTIFACT_ID,
        )
        template_bytes, template_report = _load_json_artifact(
            args.bridge_event_template_json,
            TEMPLATE_ARTIFACT_ID,
        )
        report = build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
            artifact=artifact,
            bridge_event_template_report=template_report,
            artifact_bytes=artifact_bytes,
            bridge_event_template_bytes=template_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except IndexEntryError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report("bridge_event_template_index_entry_invalid")

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json or report["ok"]:
        print(encoded)
    else:
        print(
            "runtime gap scheduler-candidate bridge-template index entry FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
    *,
    artifact: Mapping[str, Any],
    bridge_event_template_report: Mapping[str, Any],
    artifact_bytes: bytes,
    bridge_event_template_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free local index entry for the bridge-event template."""

    _assert_mapping(ARTIFACT_ID, artifact)
    _assert_mapping(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
    _assert_no_forbidden_input(ARTIFACT_ID, artifact)
    _assert_no_forbidden_input(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
    _assert_bytes_match_artifact(ARTIFACT_ID, artifact, artifact_bytes)
    _assert_bytes_match_artifact(
        TEMPLATE_ARTIFACT_ID,
        bridge_event_template_report,
        bridge_event_template_bytes,
    )
    _assert_artifact_contract(artifact)

    rebuilt = _rebuilt_template(artifact, bridge_event_template_report)
    if _deterministic_artifact(rebuilt) != _deterministic_artifact(
        bridge_event_template_report
    ):
        raise IndexEntryError("bridge_event_template_rebuilt_mismatch")

    _assert_template_contract(bridge_event_template_report, artifact)

    artifact_sha256 = _sha256_hex(artifact_bytes)
    template_sha256 = _sha256_hex(bridge_event_template_bytes)
    artifact_digest = sha256_digest(_plain_json_object(artifact))
    event = _mapping(bridge_event_template_report.get("bridge_event_template"))
    payload = _mapping(event.get("payload"))
    preview = _mapping(payload.get("scheduler_candidate_preview"))
    artifacts = (
        _artifact_record(
            artifact_id=ARTIFACT_ID,
            role="verified_no_authority_scheduler_candidate_preview",
            artifact=artifact,
            raw=artifact_bytes,
        ),
        _artifact_record(
            artifact_id=TEMPLATE_ARTIFACT_ID,
            role="template_only_bridge_handoff_context",
            artifact=bridge_event_template_report,
            raw=bridge_event_template_bytes,
        ),
    )
    entry = {
        "proof_id": PROOF_ID,
        "ok": True,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "artifact_version": ARTIFACT_VERSION,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_measurement_scope": ARTIFACT_MEASUREMENT_SCOPE,
        "source_report_version": SOURCE_REPORT_VERSION,
        "source_report_schema_version": SOURCE_REPORT_SCHEMA_VERSION,
        "source_report_measurement_scope": SOURCE_REPORT_MEASUREMENT_SCOPE,
        "template_version": SOURCE_TEMPLATE_VERSION,
        "artifact_count": len(artifacts),
        "artifacts": list(artifacts),
        "template_index_entry": {
            "artifact_id": TEMPLATE_ARTIFACT_ID,
            "template_version": SOURCE_TEMPLATE_VERSION,
            "template_only": True,
            "bridge_event_schema_validated": True,
            "source_artifact_id": ARTIFACT_ID,
            "source_artifact_sha256": artifact_sha256,
            "source_artifact_digest": artifact_digest,
            "template_sha256": template_sha256,
            "source_contract_check": "match",
            "artifact_digest_check": "match",
            "rebuilt_template_check": "match",
            "event_type": "handoff",
            "event_status": SOURCE_EVENT_STATUS,
            "scheduler_candidate_count": _as_nonnegative_int(
                preview.get("scheduler_candidate_count")
            ),
            "blocked_candidate_count": _as_nonnegative_int(
                preview.get("blocked_candidate_count")
            ),
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "direct_bridge_write_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "runtime_controls_added": False,
            "controls_present": False,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
            "required_runtime_evidence_present": False,
            "scheduler_enqueue_allowed": False,
            "scheduler_tick_allowed": False,
            "scheduler_tick_executed": False,
            "queue_writes_applied": False,
            "control_plane_writes_applied": False,
            "bridge_event_written": False,
            "gate_skip_allowed": False,
            "promotion_gate_skip_allowed": False,
            "adversarial_gate_skip_allowed": False,
            "canary_gate_skip_allowed": False,
            "fast_track_priority": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "scheduler_candidate_preview": {
            "artifact_verified": True,
            "artifact_digest": artifact_digest,
            "artifact_path_free": True,
            "source_report_digest": artifact.get("source_report_digest"),
            "scheduler_candidate_count": _as_nonnegative_int(
                artifact.get("scheduler_candidate_count")
            ),
            "blocked_candidate_count": _as_nonnegative_int(
                artifact.get("blocked_candidate_count")
            ),
            "candidate_digests": _safe_token_list(preview.get("candidate_digests")),
            "queue_priority_counts": _safe_mapping_ints(
                preview.get("queue_priority_counts")
            ),
            "raw_artifact_payload_included": False,
            "raw_signal_payload_included": False,
            "raw_query_exported": False,
        },
        "consistency": {
            "required_artifacts_present": [ARTIFACT_ID, TEMPLATE_ARTIFACT_ID],
            "all_artifact_digests_recorded": True,
            "bridge_event_schema_validated": True,
            "source_contract_check": "match",
            "artifact_digest_check": "match",
            "rebuilt_template_check": "match",
            "template_only": True,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "operator_boundary": {
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "direct_bridge_write_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "runtime_controls_added": False,
            "controls_present": False,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
            "required_runtime_evidence_present": False,
            "scheduler_enqueue_allowed": False,
            "scheduler_tick_allowed": False,
            "scheduler_tick_executed": False,
            "queue_writes_applied": False,
            "control_plane_writes_applied": False,
            "bridge_event_written": False,
            "gate_skip_allowed": False,
            "promotion_gate_skip_allowed": False,
            "adversarial_gate_skip_allowed": False,
            "canary_gate_skip_allowed": False,
            "fast_track_priority": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "reviewer_next_actions": [
            "review_runtime_gap_scheduler_candidate_bridge_event_template_index_entry",
            "append_bridge_event_separately_only_after_manual_review",
        ],
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "required_runtime_evidence_present": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "scheduler_tick_executed": False,
        "queue_writes_applied": False,
        "control_plane_writes_applied": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "promotion_gate_skip_allowed": False,
        "adversarial_gate_skip_allowed": False,
        "canary_gate_skip_allowed": False,
        "fast_track_priority": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [],
        "warnings": _safe_token_list(bridge_event_template_report.get("warnings")),
    }
    _assert_no_forbidden_output(json.dumps(entry, allow_nan=False, sort_keys=True))
    return entry


def _rebuilt_template(
    artifact: Mapping[str, Any],
    template_report: Mapping[str, Any],
) -> Mapping[str, Any]:
    event = _mapping(template_report.get("bridge_event_template"))
    if not event:
        raise IndexEntryError("bridge_event_template_missing")
    try:
        validate_event(event)
    except Exception as exc:
        raise IndexEntryError("bridge_event_template_schema_invalid") from exc
    return build_runtime_gap_scheduler_candidate_bridge_event_template(
        artifact=artifact,
        agent_id=_required_string(event.get("agent"), "bridge_event_template_agent_invalid"),
        task_id=_required_string(
            event.get("task_id"),
            "bridge_event_template_task_id_invalid",
        ),
        to=_required_string(event.get("to"), "bridge_event_template_to_invalid"),
        severity=_required_severity(event.get("severity")),
        role=_required_string(event.get("role"), "bridge_event_template_role_invalid"),
        run_id=_optional_string(event.get("run_id")),
        session_id=_optional_string(event.get("session_id")),
        now_utc=_parse_utc(
            _required_string(event.get("ts_utc"), "bridge_event_template_ts_invalid")
        ),
    )


def _assert_template_contract(
    template_report: Mapping[str, Any],
    artifact: Mapping[str, Any],
) -> None:
    if template_report.get("ok") is not True:
        raise IndexEntryError("bridge_event_template_not_ok")
    if template_report.get("template_version") != SOURCE_TEMPLATE_VERSION:
        raise IndexEntryError("bridge_event_template_version_mismatch")
    if template_report.get("template_only") is not True:
        raise IndexEntryError("bridge_event_template_template_only_not_true")
    _expect_empty_items(template_report.get("blockers"), "bridge_event_template_blockers_present")
    _expect_authority_false(template_report, "bridge_event_template")

    event = _mapping(template_report.get("bridge_event_template"))
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
    if payload.get("artifact_version") != ARTIFACT_VERSION:
        raise IndexEntryError("bridge_event_template_artifact_version_mismatch")
    if payload.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise IndexEntryError("bridge_event_template_artifact_schema_mismatch")
    if payload.get("artifact_measurement_scope") != ARTIFACT_MEASUREMENT_SCOPE:
        raise IndexEntryError("bridge_event_template_artifact_scope_mismatch")
    if payload.get("artifact_digest") != sha256_digest(_plain_json_object(artifact)):
        raise IndexEntryError("bridge_event_template_artifact_digest_mismatch")
    for field in (
        "source_report_digest",
        "source_report_version",
        "source_report_schema_version",
        "source_report_measurement_scope",
        "input_source_kind",
    ):
        if payload.get(field) != artifact.get(field):
            raise IndexEntryError(f"bridge_event_template_{field}_mismatch")
    if payload.get("template_only") is not True:
        raise IndexEntryError("bridge_event_template_payload_template_only_not_true")
    _expect_authority_false(payload, "bridge_event_template_payload")
    _expect_authority_false(
        _mapping(payload.get("authority_boundary")),
        "bridge_event_template_boundary",
    )

    preview = _mapping(payload.get("scheduler_candidate_preview"))
    if preview.get("artifact_verified") is not True:
        raise IndexEntryError("bridge_event_template_preview_artifact_not_verified")
    if preview.get("artifact_path_free") is not True:
        raise IndexEntryError("bridge_event_template_preview_not_path_free")
    if preview.get("scheduler_candidate_count") != artifact.get("scheduler_candidate_count"):
        raise IndexEntryError("bridge_event_template_preview_candidate_count_mismatch")
    if preview.get("blocked_candidate_count") != artifact.get("blocked_candidate_count"):
        raise IndexEntryError("bridge_event_template_preview_blocked_count_mismatch")
    for field in (
        "raw_artifact_payload_included",
        "raw_signal_payload_included",
        "raw_query_exported",
    ):
        if preview.get(field) is not False:
            raise IndexEntryError(f"bridge_event_template_preview_{field}_not_false")


def _assert_artifact_contract(artifact: Mapping[str, Any]) -> None:
    errors = validate_runtime_gap_scheduler_candidate_artifact(artifact)
    if errors:
        raise IndexEntryError("scheduler_candidate_artifact_invalid:" + _safe_reason(errors[0]))


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


def _artifact_record(
    *,
    artifact_id: str,
    role: str,
    artifact: Mapping[str, Any],
    raw: bytes,
) -> dict[str, Any]:
    schema_version = (
        artifact.get("schema_version")
        or artifact.get("template_version")
        or artifact.get("index_entry_version")
    )
    return {
        "artifact_id": artifact_id,
        "role": role,
        "sha256": _sha256_hex(raw),
        "size_bytes": len(raw),
        "json_schema_version": schema_version,
        "payload_included": False,
        "local_path_recorded": False,
    }


def _assert_mapping(label: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise IndexEntryError(f"{label}_not_object")


def _assert_bytes_match_artifact(
    label: str,
    artifact: Mapping[str, Any],
    raw: bytes,
) -> None:
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise IndexEntryError(f"{label}_bytes_invalid") from exc
    if _deterministic_artifact(parsed) != _deterministic_artifact(artifact):
        raise IndexEntryError(f"{label}_bytes_mismatch")


def _assert_no_forbidden_input(label: str, artifact: Mapping[str, Any]) -> None:
    text = json.dumps(artifact, sort_keys=True, default=str)
    if _contains_path_marker(artifact) or _forbidden_output_markers(text):
        raise IndexEntryError(f"{label}_not_path_free")


def _expect_empty_items(value: Any, code: str) -> None:
    if value != []:
        raise IndexEntryError(code)


def _expect_authority_false(payload: Mapping[str, Any], prefix: str) -> None:
    for field in AUTHORITY_FALSE_FIELDS:
        if field in payload and payload.get(field) is not False:
            raise IndexEntryError(f"{prefix}_{field}_not_false")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _plain_json_object(value: Any) -> dict[str, Any]:
    try:
        plain = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise IndexEntryError("json_object_invalid") from exc
    if not isinstance(plain, dict):
        raise IndexEntryError("json_object_not_object")
    return plain


def _safe_token(value: Any, fallback: str = "invalid_token") -> str:
    if isinstance(value, str) and SAFE_TOKEN_PATTERN.fullmatch(value):
        return value
    return fallback


def _safe_token_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_safe_token(item) for item in value if isinstance(item, str)]


def _safe_mapping_ints(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key, item in value.items():
        safe_key = _safe_token(key)
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            result[safe_key] = item
    return dict(sorted(result.items()))


def _required_string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise IndexEntryError(code)
    return value


def _optional_string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _required_severity(value: Any) -> str:
    if value not in {"", "low", "medium", "high"}:
        raise IndexEntryError("bridge_event_template_severity_invalid")
    return str(value)


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value >= 0 else 0


def _contains_path_marker(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.replace("\\", "/").lower()
        return (
            WINDOWS_DRIVE_PATH_PATTERN.search(value) is not None
            or normalized.startswith("//")
            or "/home/" in normalized
            or "/users/" in normalized
            or "/tmp/" in normalized
            or "waggledance-agent-worktrees" in normalized
        )
    if isinstance(value, Mapping):
        return any(
            _contains_path_marker(key) or _contains_path_marker(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_path_marker(item) for item in value)
    return False


def _forbidden_output_markers(text: str) -> list[str]:
    lower_text = text.lower()
    return sorted(
        marker
        for marker in FORBIDDEN_OUTPUT_MARKERS
        if marker.lower() in lower_text
    )


def _assert_no_forbidden_output(text: str) -> None:
    found = _forbidden_output_markers(text)
    if found:
        raise IndexEntryError(
            "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_contains_forbidden_markers:"
            + "_".join(_safe_reason(marker) for marker in found)
        )


def _safe_reason(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9:._-]+", "_", str(value)).strip("_")
    if not text:
        return "invalid_reason"
    if not text[0].isalnum():
        text = "reason_" + text
    return text[:192]


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "template_version": SOURCE_TEMPLATE_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "required_runtime_evidence_present": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "scheduler_tick_executed": False,
        "queue_writes_applied": False,
        "control_plane_writes_applied": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "promotion_gate_skip_allowed": False,
        "adversarial_gate_skip_allowed": False,
        "canary_gate_skip_allowed": False,
        "fast_track_priority": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_failed:"
            f"{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _deterministic_artifact(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _parse_utc(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise IndexEntryError("now_utc_unsafe")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise IndexEntryError("now_utc_unsafe") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise IndexEntryError("now_utc_unsafe")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
