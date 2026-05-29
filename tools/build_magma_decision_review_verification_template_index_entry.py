# SPDX-License-Identifier: BUSL-1.1
"""Build a local MAGMA verification bridge-template index entry."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template import (  # noqa: E402
    TEMPLATE_VERSION as BRIDGE_TEMPLATE_VERSION,
    ContractError,
    SafeInputError,
    build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary import (  # noqa: E402
    SUMMARY_VERSION,
)
from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402


INDEX_ENTRY_VERSION = "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry.v1"

_ARTIFACT_ORDER = (
    "operator_decision_reference_review_bundle_verification_summary",
    "operator_decision_reference_review_bundle_verification_bridge_event_template",
)
_AUTHORITY_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "artifact_payloads_included",
    "local_paths_recorded",
)
_REFERENCE_FALSE_FIELDS = (
    "decision_reference_is_approval",
    "decision_reference_is_release_decision",
)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")


class BridgeTemplateIndexEntryError(ValueError):
    """Raised when bridge-template index-entry inputs are unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review-bundle-verification-summary-json",
        "--verification-summary-json",
        dest="review_bundle_verification_summary_json",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--bridge-event-template-json",
        "--verification-bridge-template-json",
        dest="bridge_event_template_json",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-29T08:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary_bytes, summary = _load_json_artifact(
            args.review_bundle_verification_summary_json,
            "operator_decision_reference_review_bundle_verification_summary",
        )
        template_bytes, template_report = _load_json_artifact(
            args.bridge_event_template_json,
            "operator_decision_reference_review_bundle_verification_bridge_event_template",
        )
        report = build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry(
            verification_summary=summary,
            bridge_event_template_report=template_report,
            verification_summary_bytes=summary_bytes,
            bridge_event_template_bytes=template_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except BridgeTemplateIndexEntryError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report(
            "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_invalid"
        )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "MAGMA operator decision-reference review bundle verification "
            "bridge-event template index entry FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry(
    *,
    verification_summary: Mapping[str, Any],
    bridge_event_template_report: Mapping[str, Any],
    verification_summary_bytes: bytes,
    bridge_event_template_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free local index entry for a bridge-event template."""

    _assert_mapping(
        "operator_decision_reference_review_bundle_verification_summary",
        verification_summary,
    )
    _assert_mapping(
        "operator_decision_reference_review_bundle_verification_bridge_event_template",
        bridge_event_template_report,
    )
    _assert_no_forbidden_input(
        "operator_decision_reference_review_bundle_verification_summary",
        verification_summary,
    )
    _assert_no_forbidden_input(
        "operator_decision_reference_review_bundle_verification_bridge_event_template",
        bridge_event_template_report,
    )
    _assert_bytes_match_artifact(
        "operator_decision_reference_review_bundle_verification_summary",
        verification_summary,
        verification_summary_bytes,
    )
    _assert_bytes_match_artifact(
        "operator_decision_reference_review_bundle_verification_bridge_event_template",
        bridge_event_template_report,
        bridge_event_template_bytes,
    )

    rebuilt_template = _rebuilt_bridge_template(
        verification_summary,
        bridge_event_template_report,
    )
    if _deterministic_artifact(rebuilt_template) != _deterministic_artifact(
        bridge_event_template_report
    ):
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_rebuilt_mismatch"
        )

    event = _mapping(bridge_event_template_report.get("bridge_event_template"))
    payload = _mapping(event.get("payload"))
    identity = _identity(verification_summary)
    reference = _assert_reference_contract(
        _mapping(verification_summary.get("operator_decision_reference_review"))
    )
    _assert_template_report_contract(
        bridge_event_template_report,
        verification_summary=verification_summary,
        identity=identity,
        reference=reference,
    )

    summary_digest = _sha256_hex(verification_summary_bytes)
    template_digest = _sha256_hex(bridge_event_template_bytes)
    artifacts = [
        _artifact_record(
            artifact_id=_ARTIFACT_ORDER[0],
            role="verified_operator_decision_reference_review_bundle_context",
            artifact=verification_summary,
            raw=verification_summary_bytes,
        ),
        _artifact_record(
            artifact_id=_ARTIFACT_ORDER[1],
            role="template_only_bridge_handoff_context",
            artifact=bridge_event_template_report,
            raw=bridge_event_template_bytes,
        ),
    ]
    entry = {
        "ok": True,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "release_ref": identity["release_ref"],
        "commit_sha": identity["commit_sha"],
        "ci_run_ref": identity["ci_run_ref"],
        "operator_decision_reference_review": {
            "decision_reference": reference["decision_reference"],
            "expected_decision_reference": reference[
                "expected_decision_reference"
            ],
            "decision_reference_verified": True,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
            "review_context_only": True,
            "manual_review_required": True,
        },
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "template_index_entry": {
            "artifact_id": _ARTIFACT_ORDER[1],
            "template_version": BRIDGE_TEMPLATE_VERSION,
            "template_only": True,
            "bridge_event_schema_validated": True,
            "source_summary_artifact_id": _ARTIFACT_ORDER[0],
            "source_summary_sha256": summary_digest,
            "template_sha256": template_digest,
            "source_contract_check": "match",
            "rebuilt_template_check": "match",
            "event_type": "handoff",
            "event_status": "decision_reference_review_bundle_verification_ready",
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "direct_bridge_write_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "runtime_controls_added": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "consistency": {
            "release_ref_match": True,
            "commit_sha_match": True,
            "ci_run_ref_match": True,
            "decision_reference_match": True,
            "required_artifacts_present": list(_ARTIFACT_ORDER),
            "all_artifact_digests_recorded": True,
            "bridge_event_schema_validated": True,
            "source_contract_check": "match",
            "rebuilt_template_check": "match",
            "template_only": True,
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
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "reviewer_next_actions": [
            "review_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry",
            "record_operator_decision_separately",
        ],
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [],
        "warnings": _safe_token_list(verification_summary.get("warnings")),
    }
    _assert_no_forbidden_output(json.dumps(entry, allow_nan=False, sort_keys=True))
    return entry


def _rebuilt_bridge_template(
    summary: Mapping[str, Any],
    template_report: Mapping[str, Any],
) -> Mapping[str, Any]:
    event = _mapping(template_report.get("bridge_event_template"))
    if not event:
        raise BridgeTemplateIndexEntryError("bridge_event_template_missing")
    try:
        validate_event(event)
    except ValueError as exc:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_schema_invalid"
        ) from exc

    try:
        return build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template(
            summary=summary,
            agent_id=_required_safe_ref(
                event.get("agent"),
                "bridge_event_template_agent_invalid",
            ),
            task_id=_required_safe_ref(
                event.get("task_id"),
                "bridge_event_template_task_id_invalid",
            ),
            to=_required_targets(event.get("to")),
            severity=_required_severity(event.get("severity")),
            role=_required_safe_ref(
                event.get("role"),
                "bridge_event_template_role_invalid",
            ),
            run_id=_optional_safe_ref(event.get("run_id")),
            session_id=_optional_safe_ref(event.get("session_id")),
            now_utc=_parse_utc(
                _required_safe_ref(
                    event.get("ts_utc"),
                    "bridge_event_template_ts_utc_invalid",
                )
            ),
        )
    except (ContractError, SafeInputError) as exc:
        raise BridgeTemplateIndexEntryError(
            f"bridge_event_template_source_contract_failed:{exc.code}"
        ) from exc


def _assert_template_report_contract(
    template_report: Mapping[str, Any],
    *,
    verification_summary: Mapping[str, Any],
    identity: Mapping[str, str],
    reference: Mapping[str, str],
) -> None:
    if template_report.get("ok") is not True:
        raise BridgeTemplateIndexEntryError("bridge_event_template_not_ok")
    if template_report.get("template_version") != BRIDGE_TEMPLATE_VERSION:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_version_mismatch"
        )
    if template_report.get("template_only") is not True:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_template_only_not_true"
        )
    _expect_empty_items(
        template_report.get("blockers"),
        "bridge_event_template_blockers_present",
    )
    _expect_authority_false(template_report, "bridge_event_template")
    event = _mapping(template_report.get("bridge_event_template"))
    if event.get("type") != "handoff":
        raise BridgeTemplateIndexEntryError("bridge_event_template_type_mismatch")
    if event.get("status") != "decision_reference_review_bundle_verification_ready":
        raise BridgeTemplateIndexEntryError("bridge_event_template_status_mismatch")
    if event.get("paths") != []:
        raise BridgeTemplateIndexEntryError("bridge_event_template_paths_not_empty")
    if event.get("write_scope") != []:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_write_scope_not_empty"
        )
    if event.get("pid") != 0:
        raise BridgeTemplateIndexEntryError("bridge_event_template_pid_not_zero")
    if event.get("cwd") != "template_not_emitted":
        raise BridgeTemplateIndexEntryError("bridge_event_template_cwd_mismatch")

    payload = _mapping(event.get("payload"))
    if payload.get("schema_version") != BRIDGE_TEMPLATE_VERSION:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_payload_schema_mismatch"
        )
    if payload.get("summary_version") != SUMMARY_VERSION:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_payload_summary_version_mismatch"
        )
    if payload.get("template_only") is not True:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_payload_template_only_not_true"
        )
    _expect_authority_false(payload, "bridge_event_template_payload")
    if payload.get("release_ref") != identity["release_ref"]:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_release_ref_mismatch"
        )
    if payload.get("commit_sha") != identity["commit_sha"]:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_commit_sha_mismatch"
        )
    if payload.get("ci_run_ref") != identity["ci_run_ref"]:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_ci_run_ref_mismatch"
        )

    payload_reference = _mapping(payload.get("operator_decision_reference_review"))
    if payload_reference.get("decision_reference") != reference["decision_reference"]:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_decision_reference_mismatch"
        )
    if (
        payload_reference.get("expected_decision_reference")
        != reference["expected_decision_reference"]
    ):
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_expected_decision_reference_mismatch"
        )
    _assert_reference_contract(payload_reference)

    payload_verification = _mapping(
        payload.get("operator_decision_reference_review_bundle_verification")
    )
    summary_verification = _mapping(
        verification_summary.get(
            "operator_decision_reference_review_bundle_verification"
        )
    )
    for field in (
        "source_contract_check",
        "rebuilt_index_check",
        "artifact_count_checked",
    ):
        if payload_verification.get(field) != summary_verification.get(field):
            raise BridgeTemplateIndexEntryError(
                f"bridge_event_template_verification_{field}_mismatch"
            )
    if payload_verification.get("verification_ok") is not True:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_verification_not_ok"
        )

    boundary = _mapping(payload.get("operator_boundary"))
    if boundary.get("manual_review_required") is not True:
        raise BridgeTemplateIndexEntryError(
            "bridge_event_template_boundary_manual_review_required_not_true"
        )
    _expect_authority_false(boundary, "bridge_event_template_boundary")


def _artifact_record(
    *,
    artifact_id: str,
    role: str,
    artifact: Mapping[str, Any],
    raw: bytes,
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "role": role,
        "sha256": _sha256_hex(raw),
        "size_bytes": len(raw),
        "json_schema_version": _schema_version(artifact),
        "payload_included": False,
        "local_path_recorded": False,
    }


def _schema_version(artifact: Mapping[str, Any]) -> str:
    for field in ("summary_version", "template_version", "index_entry_version"):
        value = artifact.get(field)
        if isinstance(value, str):
            return value
    return "invalid_schema"


def _load_json_artifact(path: Path, artifact_id: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BridgeTemplateIndexEntryError(f"{artifact_id}_unreadable") from exc
    parsed = _parse_json_bytes(raw, artifact_id)
    if not isinstance(parsed, Mapping):
        raise BridgeTemplateIndexEntryError(f"{artifact_id}_not_mapping")
    return raw, parsed


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "operator_decision_reference_review_bundle_verification_bridge_event_template_"
            f"index_entry_failed:{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


def _identity(artifact: Mapping[str, Any]) -> dict[str, str]:
    return {
        "release_ref": _required_safe_ref(
            artifact.get("release_ref"),
            "verification_summary_release_ref_invalid",
        ),
        "commit_sha": _required_commit(
            artifact.get("commit_sha"),
            "verification_summary_commit_sha_invalid",
        ),
        "ci_run_ref": _required_safe_ref(
            artifact.get("ci_run_ref"),
            "verification_summary_ci_run_ref_invalid",
        ),
    }


def _assert_reference_contract(reference: Mapping[str, Any]) -> dict[str, str]:
    decision_reference = _required_safe_ref(
        reference.get("decision_reference"),
        "operator_decision_reference_invalid",
    )
    expected_decision_reference = _required_safe_ref(
        reference.get("expected_decision_reference"),
        "operator_decision_reference_expected_invalid",
    )
    if decision_reference != expected_decision_reference:
        raise BridgeTemplateIndexEntryError("operator_decision_reference_mismatch")
    if reference.get("decision_reference_verified") is not True:
        raise BridgeTemplateIndexEntryError(
            "operator_decision_reference_not_verified"
        )
    for field in _REFERENCE_FALSE_FIELDS:
        if reference.get(field) is not False:
            raise BridgeTemplateIndexEntryError(
                f"operator_decision_reference_{field}_not_false"
            )
    if reference.get("decision_must_be_recorded_separately") is not True:
        raise BridgeTemplateIndexEntryError(
            "operator_decision_reference_decision_must_be_recorded_separately_not_true"
        )
    if reference.get("review_context_only") is not True:
        raise BridgeTemplateIndexEntryError(
            "operator_decision_reference_review_context_only_not_true"
        )
    if reference.get("manual_review_required") is not True:
        raise BridgeTemplateIndexEntryError(
            "operator_decision_reference_manual_review_required_not_true"
        )
    _expect_authority_absent_or_false(reference, "operator_decision_reference")
    return {
        "decision_reference": decision_reference,
        "expected_decision_reference": expected_decision_reference,
    }


def _assert_mapping(artifact_id: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise BridgeTemplateIndexEntryError(f"{artifact_id}_not_mapping")


def _assert_no_forbidden_input(artifact_id: str, value: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise BridgeTemplateIndexEntryError(
            f"{artifact_id}_non_finite_json_value"
        ) from exc
    if _forbidden_output_markers(serialized):
        raise BridgeTemplateIndexEntryError(f"{artifact_id}_forbidden_marker")


def _assert_bytes_match_artifact(
    artifact_id: str,
    artifact: Mapping[str, Any],
    raw: bytes,
) -> None:
    parsed = _parse_json_bytes(raw, artifact_id)
    if parsed != artifact:
        raise BridgeTemplateIndexEntryError(f"{artifact_id}_bytes_mismatch")


def _parse_json_bytes(raw: bytes, artifact_id: str) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite_json_constant")

    try:
        return json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
    except UnicodeDecodeError as exc:
        raise BridgeTemplateIndexEntryError(f"{artifact_id}_decode_error") from exc
    except json.JSONDecodeError as exc:
        raise BridgeTemplateIndexEntryError(f"{artifact_id}_json_error") from exc
    except ValueError as exc:
        raise BridgeTemplateIndexEntryError(f"{artifact_id}_json_error") from exc


def _expect_authority_false(artifact: Mapping[str, Any], label: str) -> None:
    for field in _AUTHORITY_FALSE_FIELDS:
        if artifact.get(field) is not False:
            raise BridgeTemplateIndexEntryError(f"{label}_{field}_not_false")


def _expect_authority_absent_or_false(artifact: Mapping[str, Any], label: str) -> None:
    for field in _AUTHORITY_FALSE_FIELDS:
        if field in artifact and artifact.get(field) is not False:
            raise BridgeTemplateIndexEntryError(f"{label}_{field}_not_false")


def _expect_empty_items(value: Any, reason: str) -> None:
    if value is None:
        return
    if isinstance(value, (list, tuple, set)):
        if value:
            raise BridgeTemplateIndexEntryError(reason)
        return
    raise BridgeTemplateIndexEntryError(reason)


def _required_safe_ref(value: Any, reason: str) -> str:
    if (
        isinstance(value, str)
        and _SAFE_REF_RE.match(value)
        and not _forbidden_output_markers(value)
    ):
        return value
    raise BridgeTemplateIndexEntryError(reason)


def _optional_safe_ref(value: Any) -> str:
    if value in (None, ""):
        return ""
    return _required_safe_ref(value, "bridge_event_template_optional_ref_invalid")


def _required_commit(value: Any, reason: str) -> str:
    if isinstance(value, str) and _COMMIT_RE.match(value):
        return value
    raise BridgeTemplateIndexEntryError(reason)


def _required_severity(value: Any) -> str:
    if value in {"", "low", "medium", "high"}:
        return value
    raise BridgeTemplateIndexEntryError("bridge_event_template_severity_invalid")


def _required_targets(value: Any) -> str:
    targets = [item.strip() for item in value.split(",")] if isinstance(value, str) else []
    if (
        isinstance(value, str)
        and value
        and not _forbidden_output_markers(value)
        and targets
        and all(target and _SAFE_REF_RE.match(target) for target in targets)
    ):
        return ",".join(targets)
    raise BridgeTemplateIndexEntryError("bridge_event_template_to_invalid")


def _parse_utc(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise BridgeTemplateIndexEntryError("now_utc_unsafe")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise BridgeTemplateIndexEntryError("now_utc_unsafe") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise BridgeTemplateIndexEntryError("now_utc_unsafe")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _deterministic_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(artifact, allow_nan=False, sort_keys=True))
    return normalized if isinstance(normalized, dict) else {}


def _safe_token_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, (set, tuple)):
        raw_values = list(value)
    else:
        raw_values = [value]
    return sorted({_safe_reason(item) for item in raw_values})


def _safe_reason(value: Any) -> str:
    if (
        isinstance(value, str)
        and _SAFE_REF_RE.match(value)
        and not _forbidden_output_markers(value)
    ):
        return value
    return "unsafe_marker_redacted"


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
        raise ValueError(
            "sanitized operator decision-reference review bundle verification "
            "bridge-event template index entry contains forbidden markers: "
            + ", ".join(found)
        )


if __name__ == "__main__":
    raise SystemExit(main())
