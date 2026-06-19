#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a route-stage handoff-bundle summary bridge-event template."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index import (  # noqa: E402
    BUNDLE_INDEX_VERSION,
    FINAL_VERIFICATION_ARTIFACT_ID,
    REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
)
from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary import (  # noqa: E402
    PROOF_ID as SOURCE_PROOF_ID,
    ROUTE_STAGE_BUNDLE_VERIFICATION_KEY,
    SUMMARY_VERSION as SOURCE_SUMMARY_VERSION,
)
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
)
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template import (  # noqa: E402
    SafeInputError,
    _as_nonnegative_int,
    _assert_no_forbidden_input,
    _assert_no_forbidden_output,
    _bridge_template_input_error,
    _mapping,
    _parse_utc,
    _safe_ref_or_invalid,
    _safe_token,
    _safe_token_list,
    _token_list_schema_blockers,
    _utc_iso,
    _validate_targets,
)
from tools.verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index import (  # noqa: E402
    VERIFICATION_VERSION,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402


TEMPLATE_VERSION = (
    "waggledance.route_stage_feed_health_drill_evidence_reviewer_"
    "handoff_bundle_verification_summary_bridge_event_template.v1"
)
PROOF_ID = (
    "route_stage_feed_health_drill_evidence_reviewer_"
    "handoff_bundle_verification_summary_bridge_event_template_v1"
)
EVENT_STATUS = (
    "route_stage_feed_health_drill_evidence_reviewer_"
    "handoff_bundle_verification_summary_ready"
)
_ARTIFACT_IDS = (
    FINAL_VERIFICATION_ARTIFACT_ID,
    REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
)
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "payload",
        "payloads",
        "raw_payload",
        "raw_payloads",
        "artifact_payload",
        "artifact_payloads",
        "payload_bytes",
        "payload_content",
        "raw_bytes",
        "raw_content",
    }
)
_FORBIDDEN_PATH_KEYS = frozenset(
    {
        "path",
        "paths",
        "local_path",
        "local_paths",
        "file_path",
        "file_paths",
        "filename",
        "filenames",
        "source_path",
        "source_paths",
        "target_path",
        "target_paths",
        "url",
        "urls",
        "uri",
        "uris",
        "endpoint",
        "endpoints",
    }
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-verification-summary-json",
        "--verification-summary-json",
        "--summary-json",
        dest="bundle_verification_summary_json",
        required=True,
        type=Path,
    )
    parser.add_argument("--agent", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--to",
        default="operator,claude-rco-1,codex-tools-1",
        help="Comma-separated bridge targets for the template.",
    )
    parser.add_argument(
        "--severity",
        default="medium",
        choices=("", "low", "medium", "high"),
    )
    parser.add_argument("--role", default="lead-impl")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-19T07:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = _load_json_report(args.bundle_verification_summary_json)
        report = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template(
            summary=summary,
            agent_id=args.agent,
            task_id=args.task_id,
            to=args.to,
            severity=args.severity,
            role=args.role,
            run_id=args.run_id,
            session_id=args.session_id,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except SafeInputError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report(
            "route_stage_feed_health_drill_evidence_reviewer_"
            "handoff_bundle_verification_summary_bridge_event_template_invalid"
        )

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json:
        print(encoded)
    elif report["ok"]:
        print(
            json.dumps(
                report["bridge_event_template"],
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        )
    else:
        print(
            "route-stage feed-health handoff-bundle verification summary "
            "bridge-event template FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template(
    *,
    summary: Mapping[str, Any],
    agent_id: str,
    task_id: str,
    to: str = "operator,claude-rco-1,codex-tools-1",
    severity: str = "medium",
    role: str = "lead-impl",
    run_id: str = "",
    session_id: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a valid bridge-event template without appending it."""

    if not isinstance(summary, Mapping):
        return _failure_report("bundle_verification_summary_not_mapping")
    try:
        _assert_no_forbidden_input("bundle_verification_summary", summary)
    except SafeInputError as exc:
        return _failure_report(exc.code)

    input_error = _bridge_template_input_error(
        agent_id=agent_id,
        task_id=task_id,
        to=to,
        severity=severity,
        role=role,
        run_id=run_id,
        session_id=session_id,
    )
    if input_error is not None:
        return _failure_report(input_error)
    targets, _ = _validate_targets(to)

    contract_blockers = _summary_contract_blockers(summary)
    if contract_blockers:
        return _failure_report(contract_blockers[0])

    verification = _mapping(summary.get(ROUTE_STAGE_BUNDLE_VERIFICATION_KEY))
    reviewer = _mapping(summary.get("reviewer_ownership"))
    payload = {
        "schema_version": TEMPLATE_VERSION,
        "summary_version": SOURCE_SUMMARY_VERSION,
        "summary_proof_id": _safe_ref_or_invalid(summary.get("proof_id")),
        "reviewer_ownership": {
            "reviewer_agent_id": _safe_ref_or_invalid(
                reviewer.get("reviewer_agent_id")
            ),
            "handoff_ref": _safe_ref_or_invalid(reviewer.get("handoff_ref")),
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
        },
        ROUTE_STAGE_BUNDLE_VERIFICATION_KEY: {
            "verification_ok": True,
            "verification_version": VERIFICATION_VERSION,
            "bundle_index_version": BUNDLE_INDEX_VERSION,
            "artifact_count_checked": len(_ARTIFACT_IDS),
            "digest_checks": _match_checks(verification.get("digest_checks")),
            "size_checks": _match_checks(verification.get("size_checks")),
            "schema_version_checks": _match_checks(
                verification.get("schema_version_checks")
            ),
            "source_contract_check": "match",
            "rebuilt_bundle_index_check": "match",
            "reviewer_handoff_summary_check": "match",
            "template_only": True,
            "blocker_count": 0,
            "warning_count": _as_nonnegative_int(verification.get("warning_count")),
            "warnings": _safe_token_list(verification.get("warnings")),
        },
        "operator_boundary": {
            "verification_report_boundary_ok": True,
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
            "review_route_stage_feed_health_handoff_bundle_verification_summary_bridge_event_template",
            "compare_bridge_event_template_to_handoff_bundle_summary_contract",
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
    }
    event = {
        "ts_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "agent": agent_id,
        "type": "handoff",
        "task_id": task_id,
        "status": EVENT_STATUS,
        "severity": severity,
        "to": targets,
        "message": (
            "Route-stage feed-health reviewer handoff-bundle verification "
            "summary bridge-event template ready; manual_review_required=true; "
            "approval_granted=false; release_decision_made=false; "
            "automatic_release_decision=false; template_only=true; no bridge "
            "write, transport, external fetch, payload inclusion, local path "
            "recording, runtime controls, runtime authority, merge, promotion, "
            "or release decision."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": run_id,
        "role": role,
        "session_id": session_id,
        "capabilities": [
            "wd_image1",
            "route_stage_feed",
            "bridge_event",
        ],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    try:
        _assert_no_forbidden_output(json.dumps(event, allow_nan=False, sort_keys=True))
        validate_event(event)
    except Exception:
        return _failure_report("bridge_event_schema_invalid")
    return {
        "proof_id": PROOF_ID,
        "ok": True,
        "template_version": TEMPLATE_VERSION,
        "bridge_event_template": event,
        "template_only": True,
        "manual_review_required": True,
        "direct_bridge_write_performed": False,
        "automatic_release_decision": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_controls_added": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "network_access_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [],
        "warnings": _safe_token_list(summary.get("warnings")),
    }


def _summary_contract_blockers(summary: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    blockers.extend(_recursive_contract_blockers(summary))
    blockers.extend(
        _token_list_schema_blockers(
            summary,
            "blockers",
            prefix="bundle_verification_summary",
        )
    )
    blockers.extend(
        _token_list_schema_blockers(
            summary,
            "warnings",
            prefix="bundle_verification_summary",
        )
    )
    if summary.get("ok") is not True:
        blockers.append("bundle_verification_summary_not_ok")
    if summary.get("summary_version") != SOURCE_SUMMARY_VERSION:
        blockers.append("bundle_verification_summary_version_mismatch")
    if summary.get("proof_id") != SOURCE_PROOF_ID:
        blockers.append("bundle_verification_summary_proof_id_mismatch")
    if summary.get("template_only") is not True:
        blockers.append("bundle_verification_summary_template_only_not_true")
    if summary.get("manual_review_required") is not True:
        blockers.append("bundle_verification_summary_manual_review_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if summary.get(field) is not False:
            blockers.append(f"bundle_verification_summary_{field}_not_false")
    if _safe_token_list(summary.get("blockers")):
        blockers.append("bundle_verification_summary_blockers_present")

    reviewer = _mapping(summary.get("reviewer_ownership"))
    if reviewer.get("manual_review_required") is not True:
        blockers.append("reviewer_ownership_manual_review_required_not_true")
    if _safe_ref_or_invalid(reviewer.get("reviewer_agent_id")) == "invalid_ref":
        blockers.append("reviewer_ownership_reviewer_agent_id_invalid")
    if _safe_ref_or_invalid(reviewer.get("handoff_ref")) == "invalid_ref":
        blockers.append("reviewer_ownership_handoff_ref_invalid")
    for field in (
        "approval_granted",
        "release_decision_made",
        "automatic_release_decision",
    ):
        if reviewer.get(field) is not False:
            blockers.append(f"reviewer_ownership_{field}_not_false")

    verification = _mapping(summary.get(ROUTE_STAGE_BUNDLE_VERIFICATION_KEY))
    if verification.get("verification_ok") is not True:
        blockers.append("bundle_verification_not_ok")
    if verification.get("verification_version") != VERIFICATION_VERSION:
        blockers.append("bundle_verification_version_mismatch")
    if verification.get("bundle_index_version") != BUNDLE_INDEX_VERSION:
        blockers.append("bundle_verification_bundle_index_version_mismatch")
    if verification.get("artifact_count_checked") != len(_ARTIFACT_IDS):
        blockers.append("bundle_verification_artifact_count_mismatch")
    if verification.get("source_contract_check") != "match":
        blockers.append("bundle_verification_source_contract_not_match")
    if verification.get("rebuilt_bundle_index_check") != "match":
        blockers.append("bundle_verification_rebuilt_bundle_index_not_match")
    if verification.get("reviewer_handoff_summary_check") != "match":
        blockers.append("bundle_verification_reviewer_handoff_summary_not_match")
    if verification.get("template_only") is not True:
        blockers.append("bundle_verification_template_only_not_true")
    if verification.get("blocker_count") != 0:
        blockers.append("bundle_verification_blocker_count_nonzero")
    if _safe_token_list(verification.get("blockers")):
        blockers.append("bundle_verification_blockers_present")
    blockers.extend(
        _token_list_schema_blockers(
            verification,
            "blockers",
            prefix="bundle_verification",
        )
    )
    blockers.extend(
        _token_list_schema_blockers(
            verification,
            "warnings",
            prefix="bundle_verification",
        )
    )
    for check_name in ("digest_checks", "size_checks", "schema_version_checks"):
        checks = _mapping(verification.get(check_name))
        for artifact_id in _ARTIFACT_IDS:
            if checks.get(artifact_id) != "match":
                blockers.append(
                    f"bundle_verification_{check_name}_{artifact_id}_not_match"
                )

    boundary = _mapping(summary.get("operator_boundary"))
    if boundary.get("verification_report_boundary_ok") is not True:
        blockers.append("operator_boundary_verification_report_not_ok")
    if _safe_token_list(boundary.get("boundary_blockers")):
        blockers.append("operator_boundary_blockers_present")
    blockers.extend(
        _token_list_schema_blockers(
            boundary,
            "boundary_blockers",
            prefix="operator_boundary",
        )
    )
    if boundary.get("manual_review_required") is not True:
        blockers.append("operator_boundary_manual_review_required_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")
    return sorted(set(blockers))


def _recursive_contract_blockers(value: Any) -> list[str]:
    blockers: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = raw_key if isinstance(raw_key, str) else "invalid_key"
            if key in _FORBIDDEN_PAYLOAD_KEYS or key.endswith("_payload"):
                blockers.append(f"bundle_verification_summary_payload_key:{key}")
            if (
                key in _FORBIDDEN_PATH_KEYS
                or key.endswith("_path")
                or key.endswith("_paths")
            ):
                blockers.append(f"bundle_verification_summary_path_key:{key}")
            if key in AUTHORITY_FALSE_FIELDS and child is not False:
                blockers.append(f"bundle_verification_summary_{key}_not_false")
            blockers.extend(_recursive_contract_blockers(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            blockers.extend(_recursive_contract_blockers(child))
    return sorted(set(blockers))


def _load_json_report(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SafeInputError("bundle_verification_summary_unreadable") from exc

    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite_json_constant")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise SafeInputError("bundle_verification_summary_decode_error") from exc
    except json.JSONDecodeError as exc:
        raise SafeInputError("bundle_verification_summary_json_error") from exc
    except ValueError as exc:
        raise SafeInputError("bundle_verification_summary_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise SafeInputError("bundle_verification_summary_not_mapping")
    return parsed


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "template_version": TEMPLATE_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "direct_bridge_write_performed": False,
        "automatic_release_decision": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_controls_added": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "network_access_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "route_stage_feed_health_drill_evidence_reviewer_"
            "handoff_bundle_verification_summary_bridge_event_template_failed:"
            f"{_safe_token(reason)}"
        ],
        "warnings": [],
    }


def _match_checks(value: Any) -> dict[str, str]:
    raw = _mapping(value)
    return {
        artifact_id: "match" if raw.get(artifact_id) == "match" else "unknown"
        for artifact_id in _ARTIFACT_IDS
    }


if __name__ == "__main__":
    raise SystemExit(main())
