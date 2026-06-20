#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify a route-stage reviewer handoff verifier-summary template index entry."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
    EVENT_STATUS,
    FORBIDDEN_OUTPUT_MARKERS,
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
    TEMPLATE_VERSION,
    TemplateIndexEntryError,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)


VERIFICATION_VERSION = (
    "waggledance.route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
    "verification_summary_bridge_event_template_index_entry_verification_summary_"
    "bridge_event_template_index_entry_verification.v1"
)
INDEX_ENTRY_ARTIFACT_ID = (
    "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
    "verification_summary_bridge_event_template_index_entry_verification_summary_"
    "bridge_event_template_index_entry"
)
_REQUIRED_ARTIFACTS = (SUMMARY_ARTIFACT_ID, TEMPLATE_ARTIFACT_ID)


class TemplateIndexEntryVerificationError(ValueError):
    """Raised when verifier inputs cannot be safely read."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index-entry-json",
        "--template-index-entry-json",
        dest="index_entry_json",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--index-entry-verification-summary-json",
        "--verification-summary-json",
        "--summary-json",
        dest="index_entry_verification_summary_json",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--summary-bridge-event-template-json",
        "--bridge-event-template-json",
        "--template-json",
        dest="summary_bridge_event_template_json",
        required=True,
        type=Path,
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _, index_entry = _load_json_artifact(
            args.index_entry_json,
            INDEX_ENTRY_ARTIFACT_ID,
        )
        summary_bytes, summary = _load_json_artifact(
            args.index_entry_verification_summary_json,
            SUMMARY_ARTIFACT_ID,
        )
        template_bytes, template_report = _load_json_artifact(
            args.summary_bridge_event_template_json,
            TEMPLATE_ARTIFACT_ID,
        )
        report = verify_route_stage_handoff_verifier_summary_bridge_template_index_entry(
            index_entry=index_entry,
            index_entry_verification_summary=summary,
            summary_bridge_event_template_report=template_report,
            index_entry_verification_summary_bytes=summary_bytes,
            summary_bridge_event_template_bytes=template_bytes,
        )
    except TemplateIndexEntryVerificationError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report(
            "route_stage_handoff_verifier_summary_bridge_template_index_entry_"
            "verification_invalid"
        )

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json or report["ok"]:
        print(encoded)
    else:
        print(
            "route-stage reviewer handoff verifier-summary bridge-template "
            "index entry verification FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def verify_route_stage_handoff_verifier_summary_bridge_template_index_entry(
    *,
    index_entry: Mapping[str, Any],
    index_entry_verification_summary: Mapping[str, Any],
    summary_bridge_event_template_report: Mapping[str, Any],
    index_entry_verification_summary_bytes: bytes,
    summary_bridge_event_template_bytes: bytes,
) -> dict[str, Any]:
    """Recompute checks for the reviewer handoff verifier-summary index entry."""

    _assert_mapping(INDEX_ENTRY_ARTIFACT_ID, index_entry)
    _assert_mapping(SUMMARY_ARTIFACT_ID, index_entry_verification_summary)
    _assert_mapping(TEMPLATE_ARTIFACT_ID, summary_bridge_event_template_report)
    for artifact_id, artifact in (
        (INDEX_ENTRY_ARTIFACT_ID, index_entry),
        (SUMMARY_ARTIFACT_ID, index_entry_verification_summary),
        (TEMPLATE_ARTIFACT_ID, summary_bridge_event_template_report),
    ):
        _assert_no_forbidden_input(artifact_id, artifact)

    blockers: list[str] = []
    warnings: list[str] = []
    rebuilt_entry = _rebuilt_source_index_entry(
        index_entry_verification_summary=index_entry_verification_summary,
        summary_bridge_event_template_report=summary_bridge_event_template_report,
        index_entry_verification_summary_bytes=(
            index_entry_verification_summary_bytes
        ),
        summary_bridge_event_template_bytes=summary_bridge_event_template_bytes,
        blockers=blockers,
    )
    rebuilt_index_entry_check = "failed"

    if index_entry.get("index_entry_version") != INDEX_ENTRY_VERSION:
        blockers.append("index_entry_version_mismatch")
    if index_entry.get("ok") is not True:
        blockers.append("index_entry_not_ok")
    if (
        not isinstance(index_entry.get("artifact_count"), int)
        or index_entry.get("artifact_count") != len(_REQUIRED_ARTIFACTS)
    ):
        blockers.append("artifact_count_mismatch")

    _collect_boundary_blockers(index_entry, blockers)
    _collect_template_entry_blockers(
        index_entry,
        index_entry_verification_summary_bytes=(
            index_entry_verification_summary_bytes
        ),
        summary_bridge_event_template_bytes=summary_bridge_event_template_bytes,
        blockers=blockers,
    )
    _collect_consistency_blockers(index_entry, blockers)

    if rebuilt_entry is not None:
        rebuilt_index_entry_check = _compare_rebuilt_index_entry(
            observed=index_entry,
            rebuilt=rebuilt_entry,
            blockers=blockers,
        )

    artifacts = {
        SUMMARY_ARTIFACT_ID: (
            index_entry_verification_summary,
            index_entry_verification_summary_bytes,
        ),
        TEMPLATE_ARTIFACT_ID: (
            summary_bridge_event_template_report,
            summary_bridge_event_template_bytes,
        ),
    }
    records = _artifact_records(index_entry, blockers)
    digest_checks: dict[str, str] = {}
    size_checks: dict[str, str] = {}
    schema_version_checks: dict[str, str] = {}
    for artifact_id in _REQUIRED_ARTIFACTS:
        artifact, raw = artifacts[artifact_id]
        record = records.get(artifact_id)
        if record is None:
            digest_checks[artifact_id] = "missing_index_record"
            size_checks[artifact_id] = "missing_index_record"
            schema_version_checks[artifact_id] = "missing_index_record"
            continue
        digest_checks[artifact_id] = _check_equal(
            record.get("sha256"),
            _sha256_hex(raw),
            f"digest_mismatch:{artifact_id}",
            blockers,
        )
        size_checks[artifact_id] = _check_equal(
            record.get("size_bytes"),
            len(raw),
            f"size_mismatch:{artifact_id}",
            blockers,
        )
        schema_version_checks[artifact_id] = _check_equal(
            record.get("json_schema_version"),
            _schema_version(artifact),
            f"schema_version_mismatch:{artifact_id}",
            blockers,
        )
        if record.get("payload_included") is not False:
            blockers.append(f"payload_included_not_false:{artifact_id}")
        if record.get("local_path_recorded") is not False:
            blockers.append(f"local_path_recorded_not_false:{artifact_id}")

    template_entry = _mapping(index_entry.get("template_index_entry"))
    report = {
        "ok": not blockers,
        "verification_version": VERIFICATION_VERSION,
        "index_entry_version": index_entry.get("index_entry_version"),
        "artifact_count_checked": len(_REQUIRED_ARTIFACTS),
        "digest_checks": digest_checks,
        "size_checks": size_checks,
        "schema_version_checks": schema_version_checks,
        "source_contract_check": "match" if rebuilt_entry is not None else "failed",
        "rebuilt_index_entry_check": rebuilt_index_entry_check,
        "bridge_event_schema_check": (
            "match"
            if template_entry.get("bridge_event_schema_validated") is True
            and rebuilt_entry is not None
            else "failed"
        ),
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
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    _assert_no_forbidden_output(json.dumps(report, allow_nan=False, sort_keys=True))
    return report


def _rebuilt_source_index_entry(
    *,
    index_entry_verification_summary: Mapping[str, Any],
    summary_bridge_event_template_report: Mapping[str, Any],
    index_entry_verification_summary_bytes: bytes,
    summary_bridge_event_template_bytes: bytes,
    blockers: list[str],
) -> Mapping[str, Any] | None:
    try:
        return build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
            index_entry_verification_summary=index_entry_verification_summary,
            summary_bridge_event_template_report=summary_bridge_event_template_report,
            index_entry_verification_summary_bytes=(
                index_entry_verification_summary_bytes
            ),
            summary_bridge_event_template_bytes=(
                summary_bridge_event_template_bytes
            ),
        )
    except TemplateIndexEntryError as exc:
        blockers.append(f"source_contract_failed:{exc.code}")
    except ValueError:
        blockers.append("source_contract_failed:invalid_source_artifact")
    return None


def _compare_rebuilt_index_entry(
    *,
    observed: Mapping[str, Any],
    rebuilt: Mapping[str, Any],
    blockers: list[str],
) -> str:
    if _deterministic_index_entry(observed) == _deterministic_index_entry(rebuilt):
        return "match"
    blockers.append("rebuilt_index_entry_mismatch")
    return "mismatch"


def _deterministic_index_entry(index_entry: Mapping[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(index_entry, allow_nan=False, sort_keys=True))
    if isinstance(normalized, dict):
        normalized.pop("created_at_utc", None)
        return normalized
    return {}


def _collect_boundary_blockers(
    index_entry: Mapping[str, Any],
    blockers: list[str],
) -> None:
    operator_boundary = _mapping(index_entry.get("operator_boundary"))
    if index_entry.get("manual_review_required") is not True:
        blockers.append("manual_review_required_not_true")
    if operator_boundary.get("manual_review_required") is not True:
        blockers.append("operator_boundary_manual_review_required_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if index_entry.get(field) is not False:
            blockers.append(f"{field}_not_false")
        if operator_boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")


def _collect_template_entry_blockers(
    index_entry: Mapping[str, Any],
    *,
    index_entry_verification_summary_bytes: bytes,
    summary_bridge_event_template_bytes: bytes,
    blockers: list[str],
) -> None:
    template_entry = _mapping(index_entry.get("template_index_entry"))
    if template_entry.get("artifact_id") != TEMPLATE_ARTIFACT_ID:
        blockers.append("template_index_entry_artifact_id_mismatch")
    if template_entry.get("template_version") != TEMPLATE_VERSION:
        blockers.append("template_index_entry_template_version_mismatch")
    for field in (
        "template_only",
        "bridge_event_schema_validated",
        "manual_review_required",
    ):
        if template_entry.get(field) is not True:
            blockers.append(f"template_index_entry_{field}_not_true")
    if template_entry.get("source_summary_artifact_id") != SUMMARY_ARTIFACT_ID:
        blockers.append("template_index_entry_source_summary_artifact_id_mismatch")
    if template_entry.get("source_summary_sha256") != _sha256_hex(
        index_entry_verification_summary_bytes
    ):
        blockers.append("template_index_entry_source_summary_sha256_mismatch")
    if template_entry.get("template_sha256") != _sha256_hex(
        summary_bridge_event_template_bytes
    ):
        blockers.append("template_index_entry_template_sha256_mismatch")
    for field in ("source_contract_check", "rebuilt_template_check"):
        if template_entry.get(field) != "match":
            blockers.append(f"template_index_entry_{field}_not_match")
    if template_entry.get("event_type") != "handoff":
        blockers.append("template_index_entry_event_type_mismatch")
    if template_entry.get("event_status") != EVENT_STATUS:
        blockers.append("template_index_entry_event_status_mismatch")
    for field in AUTHORITY_FALSE_FIELDS:
        if template_entry.get(field) is not False:
            blockers.append(f"template_index_entry_{field}_not_false")


def _collect_consistency_blockers(
    index_entry: Mapping[str, Any],
    blockers: list[str],
) -> None:
    consistency = _mapping(index_entry.get("consistency"))
    if consistency.get("required_artifacts_present") != list(_REQUIRED_ARTIFACTS):
        blockers.append("consistency_required_artifacts_mismatch")
    for field in (
        "all_artifact_digests_recorded",
        "bridge_event_schema_validated",
    ):
        if consistency.get(field) is not True:
            blockers.append(f"consistency_{field}_not_true")
    for field in ("source_contract_check", "rebuilt_template_check"):
        if consistency.get(field) != "match":
            blockers.append(f"consistency_{field}_not_match")
    if consistency.get("template_only") is not True:
        blockers.append("consistency_template_only_not_true")
    for field in ("artifact_payloads_included", "local_paths_recorded"):
        if consistency.get(field) is not False:
            blockers.append(f"consistency_{field}_not_false")


def _artifact_records(
    index_entry: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, Mapping[str, Any]]:
    raw_records = index_entry.get("artifacts")
    if not isinstance(raw_records, list):
        blockers.append("artifact_records_missing")
        return {}
    if len(raw_records) != len(_REQUIRED_ARTIFACTS):
        blockers.append("artifact_record_count_mismatch")
    records: dict[str, Mapping[str, Any]] = {}
    for raw_record in raw_records:
        if not isinstance(raw_record, Mapping):
            blockers.append("artifact_record_not_mapping")
            continue
        artifact_id = raw_record.get("artifact_id")
        if not isinstance(artifact_id, str):
            blockers.append("artifact_record_id_missing")
            continue
        if artifact_id not in _REQUIRED_ARTIFACTS:
            blockers.append(f"artifact_record_unknown:{artifact_id}")
            continue
        if artifact_id in records:
            blockers.append(f"artifact_record_duplicate:{artifact_id}")
            continue
        records[artifact_id] = raw_record
    for artifact_id in _REQUIRED_ARTIFACTS:
        if artifact_id not in records:
            blockers.append(f"artifact_record_missing:{artifact_id}")
    return records


def _check_equal(
    observed: Any,
    expected: Any,
    blocker: str,
    blockers: list[str],
) -> str:
    if observed == expected:
        return "match"
    blockers.append(blocker)
    return "mismatch"


def _schema_version(artifact: Mapping[str, Any]) -> str:
    for field in (
        "schema_version",
        "summary_version",
        "template_version",
        "index_entry_version",
    ):
        value = artifact.get(field)
        if isinstance(value, str):
            return value
    return "invalid_schema"


def _load_json_artifact(path: Path, artifact_id: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TemplateIndexEntryVerificationError(
            f"{artifact_id}_unreadable"
        ) from exc

    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite_json_constant")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise TemplateIndexEntryVerificationError(
            f"{artifact_id}_decode_error"
        ) from exc
    except json.JSONDecodeError as exc:
        raise TemplateIndexEntryVerificationError(
            f"{artifact_id}_json_error"
        ) from exc
    except ValueError as exc:
        raise TemplateIndexEntryVerificationError(
            f"{artifact_id}_json_error"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise TemplateIndexEntryVerificationError(f"{artifact_id}_not_object")
    return raw, parsed


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "verification_version": VERIFICATION_VERSION,
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
            f"{INDEX_ENTRY_ARTIFACT_ID}_verification_failed:{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


def _assert_mapping(artifact_id: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise TemplateIndexEntryVerificationError(f"{artifact_id}_not_object")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _assert_no_forbidden_input(artifact_id: str, value: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise TemplateIndexEntryVerificationError(
            f"{artifact_id}_non_finite_json_value"
        ) from exc
    if _forbidden_output_markers(serialized):
        raise TemplateIndexEntryVerificationError(f"{artifact_id}_forbidden_marker")


def _safe_reason(value: Any) -> str:
    if isinstance(value, str) and not _forbidden_output_markers(value):
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
    if _forbidden_output_markers(text):
        raise ValueError(
            "route-stage reviewer handoff verifier-summary bridge-template "
            "index entry verification contains forbidden markers"
        )


if __name__ == "__main__":
    raise SystemExit(main())
