#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a local route-stage feed-health bridge-template index entry."""

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

from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template import (  # noqa: E402
    EVENT_STATUS as SOURCE_EVENT_STATUS,
    TEMPLATE_VERSION as SOURCE_TEMPLATE_VERSION,
    build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template,
)
from tools.verify_route_stage_feed_health_drill_evidence import (  # noqa: E402
    PACKAGE_SCHEMA_VERSION,
    VERIFICATION_SCHEMA_VERSION,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402


INDEX_ENTRY_VERSION = (
    "waggledance.route_stage_feed_health_drill_evidence_verification_summary_"
    "bridge_event_template_index_entry.v1"
)
PROOF_ID = (
    "route_stage_feed_health_drill_evidence_verification_summary_"
    "bridge_event_template_index_entry_v1"
)
SUMMARY_ARTIFACT_ID = (
    "route_stage_feed_health_drill_evidence_verification_summary"
)
TEMPLATE_ARTIFACT_ID = (
    "route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
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
    "automatic_release_decision",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "network_access_performed",
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
        "--verification-summary-json",
        "--summary-json",
        dest="verification_summary_json",
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
        help="Optional UTC timestamp override such as 2026-06-05T00:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary_bytes, summary = _load_json_artifact(
            args.verification_summary_json,
            SUMMARY_ARTIFACT_ID,
        )
        template_bytes, template_report = _load_json_artifact(
            args.bridge_event_template_json,
            TEMPLATE_ARTIFACT_ID,
        )
        report = build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry(
            verification_summary=summary,
            bridge_event_template_report=template_report,
            verification_summary_bytes=summary_bytes,
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
            "route-stage feed-health bridge-template index entry FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry(
    *,
    verification_summary: Mapping[str, Any],
    bridge_event_template_report: Mapping[str, Any],
    verification_summary_bytes: bytes,
    bridge_event_template_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free local index entry for the bridge-event template."""

    _assert_mapping(SUMMARY_ARTIFACT_ID, verification_summary)
    _assert_mapping(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
    _assert_no_forbidden_input(SUMMARY_ARTIFACT_ID, verification_summary)
    _assert_no_forbidden_input(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
    _assert_bytes_match_artifact(
        SUMMARY_ARTIFACT_ID,
        verification_summary,
        verification_summary_bytes,
    )
    _assert_bytes_match_artifact(
        TEMPLATE_ARTIFACT_ID,
        bridge_event_template_report,
        bridge_event_template_bytes,
    )
    _assert_summary_contract(verification_summary)

    rebuilt = _rebuilt_template(verification_summary, bridge_event_template_report)
    if _deterministic_artifact(rebuilt) != _deterministic_artifact(
        bridge_event_template_report
    ):
        raise IndexEntryError("bridge_event_template_rebuilt_mismatch")

    _assert_template_contract(bridge_event_template_report, verification_summary)

    summary_digest = _sha256_hex(verification_summary_bytes)
    template_digest = _sha256_hex(bridge_event_template_bytes)
    event = _mapping(bridge_event_template_report.get("bridge_event_template"))
    payload = _mapping(event.get("payload"))
    verification = _mapping(
        payload.get("route_stage_feed_health_drill_evidence_verification")
    )
    artifacts = (
        _artifact_record(
            artifact_id=SUMMARY_ARTIFACT_ID,
            role="verified_route_stage_feed_health_drill_context",
            artifact=verification_summary,
            raw=verification_summary_bytes,
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
        "verification_schema_version": VERIFICATION_SCHEMA_VERSION,
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "template_version": SOURCE_TEMPLATE_VERSION,
        "artifact_count": len(artifacts),
        "artifacts": list(artifacts),
        "template_index_entry": {
            "artifact_id": TEMPLATE_ARTIFACT_ID,
            "template_version": SOURCE_TEMPLATE_VERSION,
            "template_only": True,
            "bridge_event_schema_validated": True,
            "source_summary_artifact_id": SUMMARY_ARTIFACT_ID,
            "source_summary_sha256": summary_digest,
            "template_sha256": template_digest,
            "source_contract_check": "match",
            "rebuilt_template_check": "match",
            "event_type": "handoff",
            "event_status": SOURCE_EVENT_STATUS,
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "direct_bridge_write_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "runtime_controls_added": False,
            "controls_present": False,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
            "network_access_performed": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "verification_summary": {
            "verified": verification_summary.get("verified") is True,
            "evidence_sha256": verification_summary.get("evidence_sha256"),
            "evidence_size_bytes": verification_summary.get("evidence_size_bytes"),
            "blocker_count": 0,
            "warning_count": len(_safe_token_list(verification_summary.get("warnings"))),
        },
        "consistency": {
            "required_artifacts_present": [SUMMARY_ARTIFACT_ID, TEMPLATE_ARTIFACT_ID],
            "all_artifact_digests_recorded": True,
            "bridge_event_schema_validated": True,
            "source_contract_check": "match",
            "rebuilt_template_check": "match",
            "template_only": True,
            "verification_check_count": verification.get("check_count"),
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "operator_boundary": {
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "direct_bridge_write_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "runtime_controls_added": False,
            "controls_present": False,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
            "network_access_performed": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "reviewer_next_actions": [
            "review_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry",
            "append_bridge_event_separately_only_after_manual_review",
        ],
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "network_access_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [],
        "warnings": _safe_token_list(bridge_event_template_report.get("warnings")),
    }
    _assert_no_forbidden_output(json.dumps(entry, allow_nan=False, sort_keys=True))
    return entry


def _rebuilt_template(
    summary: Mapping[str, Any],
    template_report: Mapping[str, Any],
) -> Mapping[str, Any]:
    event = _mapping(template_report.get("bridge_event_template"))
    if not event:
        raise IndexEntryError("bridge_event_template_missing")
    try:
        validate_event(event)
    except Exception as exc:
        raise IndexEntryError("bridge_event_template_schema_invalid") from exc
    return build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template(
        summary=summary,
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
    summary: Mapping[str, Any],
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
    if payload.get("verification_schema_version") != VERIFICATION_SCHEMA_VERSION:
        raise IndexEntryError("bridge_event_template_payload_verification_schema_mismatch")
    if payload.get("package_schema_version") != PACKAGE_SCHEMA_VERSION:
        raise IndexEntryError("bridge_event_template_payload_package_schema_mismatch")
    if payload.get("evidence_sha256") != summary.get("evidence_sha256"):
        raise IndexEntryError("bridge_event_template_evidence_sha256_mismatch")
    if payload.get("evidence_size_bytes") != summary.get("evidence_size_bytes"):
        raise IndexEntryError("bridge_event_template_evidence_size_mismatch")
    if payload.get("template_only") is not True:
        raise IndexEntryError("bridge_event_template_payload_template_only_not_true")
    _expect_authority_false(payload, "bridge_event_template_payload")
    _expect_authority_false(
        _mapping(payload.get("operator_boundary")),
        "bridge_event_template_boundary",
    )
    verification = _mapping(
        payload.get("route_stage_feed_health_drill_evidence_verification")
    )
    if verification.get("verification_ok") is not True:
        raise IndexEntryError("bridge_event_template_verification_not_ok")
    if verification.get("verified") is not True:
        raise IndexEntryError("bridge_event_template_verification_not_verified")
    if verification.get("payload_included") is not False:
        raise IndexEntryError("bridge_event_template_verification_payload_included")
    if verification.get("blocker_count") != 0:
        raise IndexEntryError("bridge_event_template_verification_blockers_present")


def _assert_summary_contract(summary: Mapping[str, Any]) -> None:
    blockers: list[str] = []
    if summary.get("schema_version") != VERIFICATION_SCHEMA_VERSION:
        blockers.append("verification_summary_schema_version_mismatch")
    if summary.get("package_schema_version") != PACKAGE_SCHEMA_VERSION:
        blockers.append("verification_summary_package_schema_version_mismatch")
    if summary.get("ok") is not True:
        blockers.append("verification_summary_not_ok")
    if summary.get("verified") is not True:
        blockers.append("verification_summary_not_verified")
    if summary.get("evidence_package") != "<redacted>":
        blockers.append("verification_summary_evidence_package_not_redacted")
    if not _is_sha256(summary.get("evidence_sha256")):
        blockers.append("verification_summary_evidence_sha256_invalid")
    if _as_nonnegative_int(summary.get("evidence_size_bytes")) <= 0:
        blockers.append("verification_summary_evidence_size_invalid")
    _expect_empty_items(summary.get("blockers"), "verification_summary_blockers_present")
    for field in (
        "controls_present",
        "runtime_authority_granted",
        "external_writes_applied",
        "network_access_performed",
    ):
        if summary.get(field) is not False:
            blockers.append(f"verification_summary_{field}_not_false")
    checks = _mapping(summary.get("checks"))
    if not checks or not all(value is True for value in checks.values()):
        blockers.append("verification_summary_checks_not_all_true")
    if blockers:
        raise IndexEntryError(blockers[0])


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


def _safe_token(value: Any, fallback: str = "invalid_token") -> str:
    if isinstance(value, str) and SAFE_TOKEN_PATTERN.fullmatch(value):
        return value
    return fallback


def _safe_token_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_safe_token(item) for item in value if isinstance(item, str)]


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


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


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
        raise IndexEntryError("bridge_event_template_index_entry_not_path_free")


def _deterministic_artifact(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
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
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "network_access_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "route_stage_feed_health_drill_evidence_verification_summary_"
            f"bridge_event_template_index_entry_failed:{_safe_token(reason)}"
        ],
        "warnings": [],
    }


if __name__ == "__main__":
    raise SystemExit(main())
