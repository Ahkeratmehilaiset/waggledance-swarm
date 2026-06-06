#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render a path-free reviewer summary for counterfactual guard reports."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_counterfactual_observability_guard import (  # noqa: E402
    GUARD_SCHEMA_VERSION,
)
from waggledance.core.autonomy_growth.counterfactual_replay import (  # noqa: E402
    A3_LABEL_RUNTIME_MEASURED,
    COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA,
    DEFAULT_A3_MIN_SAMPLES,
    summarize_counterfactual_observability,
)


SUMMARY_VERSION = "wd.counterfactual_observability_guard_reviewer_summary.v1"
PROOF_ID = "counterfactual_observability_guard_reviewer_summary_v1"
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,511}$")
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
_FORBIDDEN_RAW_KEYS = frozenset(
    {
        "candidate_hash",
        "incumbent_hash",
        "per_arm",
        "divergences",
        "candidate_output",
        "incumbent_output",
        "inputs",
        "output",
    }
)
_FORBIDDEN_AUTHORITY_CONTAINER_KEYS = frozenset(
    {
        "operator_boundary",
        "reviewer_ownership",
        "release_authority",
        "approval_authority",
        "runtime_authority",
    }
)
_AUTHORITY_FALSE_FIELDS = (
    "literal_future_claim_safe",
    "source_path_recorded",
    "raw_fields_exported",
    "runtime_authority_granted",
    "external_writes_applied",
    "bridge_event_written",
    "direct_bridge_write_performed",
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "controls_present",
    "network_access_performed",
    "artifact_payloads_included",
    "payload_fields_exported",
    "local_paths_recorded",
)
_FORBIDDEN_OUTPUT_MARKERS = (
    "://",
    "\\",
    "C:/",
    "C:\\",
)


class SafeInputError(ValueError):
    """Raised when local guard inputs are unsafe to summarize."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--guard-report-json",
        "--report-json",
        dest="guard_report_json",
        required=True,
        type=Path,
    )
    parser.add_argument("--reviewer-agent", required=True)
    parser.add_argument("--handoff-ref", required=True)
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-06T00:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now_utc = _parse_utc(args.now) if args.now else None
    try:
        guard_report = _load_json_report(args.guard_report_json)
        summary = build_counterfactual_observability_guard_reviewer_summary(
            guard_report=guard_report,
            reviewer_agent_id=args.reviewer_agent,
            handoff_ref=args.handoff_ref,
            now_utc=now_utc,
        )
    except SafeInputError as exc:
        summary = _failure_summary(
            exc.code,
            reviewer_agent_id=args.reviewer_agent,
            handoff_ref=args.handoff_ref,
            now_utc=now_utc,
        )
    except ValueError:
        summary = _failure_summary(
            "counterfactual_observability_guard_reviewer_summary_invalid",
            reviewer_agent_id=args.reviewer_agent,
            handoff_ref=args.handoff_ref,
            now_utc=now_utc,
        )

    encoded = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
    if args.json:
        print(encoded)
    elif summary["ok"]:
        print(render_counterfactual_observability_guard_reviewer_summary_markdown(summary))
    else:
        print(
            "counterfactual observability guard reviewer summary FAILED: "
            + ", ".join(summary["blockers"]),
            file=sys.stderr,
        )
    return 0 if summary["ok"] else 1


def build_counterfactual_observability_guard_reviewer_summary(
    *,
    guard_report: Mapping[str, Any],
    reviewer_agent_id: str,
    handoff_ref: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return path-free reviewer context for a local guard report."""

    if not isinstance(guard_report, Mapping):
        raise ValueError("guard_report_not_mapping")
    _assert_no_forbidden_input("guard_report", guard_report)
    _validate_safe_ref("reviewer_agent_id", reviewer_agent_id)
    _validate_safe_ref("handoff_ref", handoff_ref)

    report_blockers = _safe_token_list(guard_report.get("blockers"))
    report_warnings = _safe_token_list(guard_report.get("warnings"))
    boundary_blockers = _boundary_blockers(guard_report)
    boundary_blockers.extend(_recursive_boundary_blockers(guard_report))
    contract_blockers = _guard_report_contract_blockers(guard_report)
    contract_blockers.extend(
        _token_list_schema_blockers(guard_report, "blockers", required=True)
    )
    contract_blockers.extend(
        _token_list_schema_blockers(guard_report, "warnings", required=False)
    )
    blockers = sorted(set(report_blockers + boundary_blockers + contract_blockers))
    guard_ok = (
        guard_report.get("ok") is True
        and guard_report.get("schema_version") == GUARD_SCHEMA_VERSION
    )
    observability = _mapping(guard_report.get("observability_summary"))

    summary = {
        "proof_id": PROOF_ID,
        "ok": guard_ok and not blockers,
        "summary_version": SUMMARY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "reviewer_ownership": {
            "reviewer_agent_id": reviewer_agent_id,
            "handoff_ref": handoff_ref,
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
        },
        "counterfactual_observability_guard": {
            "guard_ok": guard_ok,
            "guard_schema_version": _safe_ref_or_invalid(
                guard_report.get("schema_version")
            ),
            "guard_kind": _safe_ref_or_invalid(guard_report.get("guard_kind")),
            "runtime_measured_claim_safe": (
                guard_report.get("runtime_measured_claim_safe") is True
            ),
            "literal_future_claim_safe": False,
            "source_path_recorded": False,
            "raw_fields_exported": False,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
            "bridge_event_written": False,
            "blocker_count": len(report_blockers),
            "blockers": report_blockers,
            "warning_count": len(report_warnings),
            "warnings": report_warnings,
        },
        "observability_summary": _sanitized_observability_summary(observability),
        "operator_boundary": {
            "guard_report_boundary_ok": not (boundary_blockers or contract_blockers),
            "boundary_blockers": sorted(set(boundary_blockers + contract_blockers)),
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
            "review_counterfactual_observability_guard_summary",
            "compare_guard_summary_to_local_counterfactual_artifact",
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
        "blockers": blockers,
        "warnings": report_warnings,
    }
    _assert_no_forbidden_output(json.dumps(summary, allow_nan=False, sort_keys=True))
    return summary


def render_counterfactual_observability_guard_reviewer_summary_markdown(
    summary: Mapping[str, Any],
) -> str:
    guard = _mapping(summary.get("counterfactual_observability_guard"))
    observability = _mapping(summary.get("observability_summary"))
    boundary = _mapping(summary.get("operator_boundary"))
    lines = [
        "# Counterfactual Observability Guard Reviewer Summary",
        "",
        f"- Summary version: `{summary.get('summary_version')}`",
        f"- Created at UTC: `{summary.get('created_at_utc')}`",
        "- Manual review required: `true`",
        "- Automatic release decision: `false`",
        "- Approval granted: `false`",
        "- Release decision made: `false`",
        "- Direct bridge write performed: `false`",
        "- Transport added: `false`",
        "- External fetch performed: `false`",
        "- Runtime controls added: `false`",
        "- Artifact payloads included: `false`",
        "- Local paths recorded: `false`",
        "",
        "## Guard",
        "",
        f"- Guard OK: `{guard.get('guard_ok')}`",
        f"- Runtime measured claim safe: `{guard.get('runtime_measured_claim_safe')}`",
        f"- Guard blockers: `{guard.get('blocker_count')}`",
        "",
        "## Observability",
        "",
        f"- Status: `{observability.get('status')}`",
        f"- A3 label: `{observability.get('a3_label')}`",
        f"- Sample count: `{observability.get('sample_count')}`",
        f"- Divergence count: `{observability.get('divergence_count')}`",
        f"- Same sample set: `{observability.get('same_sample_set')}`",
        f"- Deterministic: `{observability.get('deterministic')}`",
        f"- Delta digest present: `{observability.get('delta_digest_present')}`",
        "",
        "## Boundary",
        "",
        f"- Guard report boundary OK: `{boundary.get('guard_report_boundary_ok')}`",
        f"- Boundary blockers: `{len(boundary.get('boundary_blockers') or [])}`",
    ]
    if summary.get("blockers"):
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- `{blocker}`" for blocker in summary["blockers"])
    return "\n".join(lines) + "\n"


def _load_json_report(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SafeInputError("guard_report_json_unreadable") from exc
    try:
        return json.loads(text, parse_constant=_reject_json_constant)
    except ValueError as exc:
        raise SafeInputError("guard_report_json_invalid_or_non_finite") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _guard_report_contract_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if report.get("schema_version") != GUARD_SCHEMA_VERSION:
        blockers.append("guard_report_schema_version_mismatch")
    if report.get("guard_kind") != "counterfactual_observability_guard":
        blockers.append("guard_report_guard_kind_mismatch")
    if not isinstance(report.get("ok"), bool):
        blockers.append("guard_report_ok_not_bool")
    observability = report.get("observability_summary")
    if not isinstance(observability, Mapping):
        blockers.append("guard_report_observability_summary_not_mapping")
        observability = {}
    elif (
        observability.get("schema_version")
        != COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA
    ):
        blockers.append("guard_report_observability_schema_mismatch")
    runtime_measured_claim_safe = report.get("runtime_measured_claim_safe")
    if not isinstance(runtime_measured_claim_safe, bool):
        blockers.append("guard_report_runtime_measured_claim_safe_not_bool")
    if runtime_measured_claim_safe is True:
        blockers.extend(_runtime_measured_claim_blockers(_mapping(observability)))
    return blockers


def _runtime_measured_claim_blockers(
    observability: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if observability.get("status") != "runtime_measured":
        blockers.append("runtime_measured_claim_status_not_runtime_measured")
    if observability.get("a3_label") != A3_LABEL_RUNTIME_MEASURED:
        blockers.append("runtime_measured_claim_a3_label_not_runtime_measured")
    if _as_nonnegative_int(observability.get("sample_count")) < DEFAULT_A3_MIN_SAMPLES:
        blockers.append("runtime_measured_claim_sample_floor_not_met")
    if observability.get("same_sample_set") is not True:
        blockers.append("runtime_measured_claim_same_sample_set_not_proven")
    if observability.get("deterministic") is not True:
        blockers.append("runtime_measured_claim_determinism_not_proven")
    if observability.get("delta_digest_present") is not True:
        blockers.append("runtime_measured_claim_delta_digest_missing")
    return blockers


def _boundary_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    for field in (
        "literal_future_claim_safe",
        "source_path_recorded",
        "raw_fields_exported",
        "runtime_authority_granted",
        "external_writes_applied",
        "bridge_event_written",
    ):
        if report.get(field) is not False:
            blockers.append(f"guard_report_{field}_not_false")
    return blockers


def _recursive_boundary_blockers(value: Any, prefix: str = "guard_report") -> list[str]:
    blockers: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.lower()
            if normalized in _FORBIDDEN_AUTHORITY_CONTAINER_KEYS:
                blockers.append(f"{prefix}_forbidden_authority_container:{key_text}")
            if normalized in _AUTHORITY_FALSE_FIELDS and item is not False:
                blockers.append(f"{prefix}_nested_authority_field_not_false:{key_text}")
            blockers.extend(_recursive_boundary_blockers(item, prefix=prefix))
    elif isinstance(value, list):
        for item in value:
            blockers.extend(_recursive_boundary_blockers(item, prefix=prefix))
    return blockers


def _token_list_schema_blockers(
    report: Mapping[str, Any],
    field: str,
    *,
    required: bool,
) -> list[str]:
    value = report.get(field)
    if value is None and not required:
        return []
    if not isinstance(value, list):
        return [f"guard_report_{field}_not_list"]
    blockers: list[str] = []
    for item in value:
        if not isinstance(item, str):
            blockers.append(f"guard_report_{field}_item_not_string")
        elif not _SAFE_TOKEN_RE.fullmatch(item):
            blockers.append(f"guard_report_{field}_item_not_safe_token")
    return sorted(set(blockers))


def _sanitized_observability_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": _safe_ref_or_invalid(summary.get("schema_version")),
        "source_available": summary.get("source_available") is True,
        "compute_status": _safe_ref_or_invalid(summary.get("compute_status")),
        "status": _safe_ref_or_invalid(summary.get("status")),
        "a3_label": _safe_ref_or_invalid(summary.get("a3_label")),
        "sample_count": _as_nonnegative_int(summary.get("sample_count")),
        "divergence_count": _as_nonnegative_int(summary.get("divergence_count")),
        "same_sample_set": summary.get("same_sample_set") is True,
        "deterministic": summary.get("deterministic") is True,
        "no_delta": summary.get("no_delta") is True,
        "delta_digest_present": summary.get("delta_digest_present") is True,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "payload_fields_exported": False,
    }


def _assert_no_forbidden_input(label: str, value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.lower()
            if normalized in _FORBIDDEN_PATH_KEYS:
                raise SafeInputError(f"{label}_forbidden_path_key:{key_text}")
            if normalized in _FORBIDDEN_PAYLOAD_KEYS:
                raise SafeInputError(f"{label}_forbidden_payload_key:{key_text}")
            if normalized in _FORBIDDEN_RAW_KEYS:
                raise SafeInputError(f"{label}_forbidden_raw_key:{key_text}")
            _assert_no_forbidden_input(label, item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden_input(label, item)


def _assert_no_forbidden_output(encoded: str) -> None:
    for marker in _FORBIDDEN_OUTPUT_MARKERS:
        if marker in encoded:
            raise SafeInputError("reviewer_summary_forbidden_output_marker")


def _safe_token_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and _SAFE_TOKEN_RE.fullmatch(item)]


def _validate_safe_ref(label: str, value: str) -> None:
    if not isinstance(value, str) or not _SAFE_REF_RE.fullmatch(value):
        raise ValueError(f"{label}_invalid")


def _safe_ref_or_invalid(value: Any) -> str:
    if isinstance(value, str) and _SAFE_REF_RE.fullmatch(value):
        return value
    return "invalid_ref"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _failure_summary(
    blocker: str,
    *,
    reviewer_agent_id: str,
    handoff_ref: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    reviewer = (
        reviewer_agent_id
        if isinstance(reviewer_agent_id, str)
        and _SAFE_REF_RE.fullmatch(reviewer_agent_id)
        else "invalid_ref"
    )
    handoff = (
        handoff_ref
        if isinstance(handoff_ref, str) and _SAFE_REF_RE.fullmatch(handoff_ref)
        else "invalid_ref"
    )
    summary = summarize_counterfactual_observability(None)
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "summary_version": SUMMARY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "reviewer_ownership": {
            "reviewer_agent_id": reviewer,
            "handoff_ref": handoff,
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
        },
        "counterfactual_observability_guard": {
            "guard_ok": False,
            "guard_schema_version": "invalid_ref",
            "guard_kind": "invalid_ref",
            "runtime_measured_claim_safe": False,
            "literal_future_claim_safe": False,
            "source_path_recorded": False,
            "raw_fields_exported": False,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
            "bridge_event_written": False,
            "blocker_count": 0,
            "blockers": [],
            "warning_count": 0,
            "warnings": [],
        },
        "observability_summary": _sanitized_observability_summary(summary),
        "operator_boundary": {
            "guard_report_boundary_ok": False,
            "boundary_blockers": [blocker],
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
            "review_counterfactual_observability_guard_summary",
            "compare_guard_summary_to_local_counterfactual_artifact",
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
        "blockers": [blocker],
        "warnings": [],
    }


def _parse_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


if __name__ == "__main__":
    raise SystemExit(main())
