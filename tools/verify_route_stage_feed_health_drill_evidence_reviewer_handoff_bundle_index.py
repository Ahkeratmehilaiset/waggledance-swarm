#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify a route-stage feed-health reviewer handoff bundle index."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
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
    HandoffBundleIndexError,
    REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
    _assert_no_forbidden_input,
    _deterministic_artifact,
    _load_json_artifact,
    _mapping,
    _parse_utc,
    _safe_reason,
    _schema_version,
    _sha256_hex,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index,
)
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
    FORBIDDEN_OUTPUT_MARKERS,
)


VERIFICATION_VERSION = (
    "waggledance.route_stage_feed_health_drill_evidence_reviewer_"
    "handoff_bundle_index_verification.v1"
)
BUNDLE_INDEX_ARTIFACT_ID = (
    "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index"
)
_REQUIRED_ARTIFACTS = (
    FINAL_VERIFICATION_ARTIFACT_ID,
    REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
)


class HandoffBundleVerificationError(ValueError):
    """Raised when verifier inputs cannot be safely read."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-index-json",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--verification-json",
        "--final-verification-json",
        dest="verification_json",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--summary-json",
        "--reviewer-handoff-summary-json",
        dest="summary_json",
        required=True,
        type=Path,
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _, bundle_index = _load_json_artifact(
            args.bundle_index_json,
            BUNDLE_INDEX_ARTIFACT_ID,
        )
        verification_bytes, verification_report = _load_json_artifact(
            args.verification_json,
            FINAL_VERIFICATION_ARTIFACT_ID,
        )
        summary_bytes, reviewer_handoff_summary = _load_json_artifact(
            args.summary_json,
            REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
        )
        report = verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index(
            bundle_index=bundle_index,
            verification_report=verification_report,
            reviewer_handoff_summary=reviewer_handoff_summary,
            verification_report_bytes=verification_bytes,
            reviewer_handoff_summary_bytes=summary_bytes,
        )
    except (HandoffBundleIndexError, HandoffBundleVerificationError) as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report(
            "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index_"
            "verification_invalid"
        )

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json or report["ok"]:
        print(encoded)
    else:
        print(
            "route-stage feed-health reviewer handoff bundle index verification "
            "FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index(
    *,
    bundle_index: Mapping[str, Any],
    verification_report: Mapping[str, Any],
    reviewer_handoff_summary: Mapping[str, Any],
    verification_report_bytes: bytes,
    reviewer_handoff_summary_bytes: bytes,
) -> dict[str, Any]:
    """Recompute bundle index checks from local artifacts without side effects."""

    for artifact_id, artifact in (
        (BUNDLE_INDEX_ARTIFACT_ID, bundle_index),
        (FINAL_VERIFICATION_ARTIFACT_ID, verification_report),
        (REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID, reviewer_handoff_summary),
    ):
        if not isinstance(artifact, Mapping):
            raise HandoffBundleVerificationError(f"{artifact_id}_not_mapping")
        _assert_no_forbidden_input(artifact_id, artifact)

    blockers: list[str] = []
    warnings: list[str] = []
    rebuilt_index = _rebuilt_bundle_index(
        bundle_index=bundle_index,
        verification_report=verification_report,
        reviewer_handoff_summary=reviewer_handoff_summary,
        verification_report_bytes=verification_report_bytes,
        reviewer_handoff_summary_bytes=reviewer_handoff_summary_bytes,
        blockers=blockers,
    )
    rebuilt_bundle_index_check = "failed"

    if bundle_index.get("bundle_index_version") != BUNDLE_INDEX_VERSION:
        blockers.append("bundle_index_version_mismatch")
    if bundle_index.get("ok") is not True:
        blockers.append("bundle_index_not_ok")
    if (
        not isinstance(bundle_index.get("artifact_count"), int)
        or bundle_index.get("artifact_count") != len(_REQUIRED_ARTIFACTS)
    ):
        blockers.append("artifact_count_mismatch")

    _collect_boundary_blockers(bundle_index, blockers)
    _collect_handoff_bundle_blockers(
        bundle_index,
        verification_report_bytes=verification_report_bytes,
        reviewer_handoff_summary_bytes=reviewer_handoff_summary_bytes,
        blockers=blockers,
    )
    _collect_consistency_blockers(bundle_index, blockers)

    if rebuilt_index is not None:
        rebuilt_bundle_index_check = _compare_rebuilt_bundle_index(
            observed=bundle_index,
            rebuilt=rebuilt_index,
            blockers=blockers,
        )

    artifacts = {
        FINAL_VERIFICATION_ARTIFACT_ID: (
            verification_report,
            verification_report_bytes,
        ),
        REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID: (
            reviewer_handoff_summary,
            reviewer_handoff_summary_bytes,
        ),
    }
    records = _artifact_records(bundle_index, blockers)
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

    report = {
        "ok": not blockers,
        "verification_version": VERIFICATION_VERSION,
        "bundle_index_version": bundle_index.get("bundle_index_version"),
        "artifact_count_checked": len(_REQUIRED_ARTIFACTS),
        "digest_checks": digest_checks,
        "size_checks": size_checks,
        "schema_version_checks": schema_version_checks,
        "source_contract_check": "match" if rebuilt_index is not None else "failed",
        "rebuilt_bundle_index_check": rebuilt_bundle_index_check,
        "reviewer_handoff_summary_check": (
            "match" if rebuilt_index is not None else "failed"
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


def _rebuilt_bundle_index(
    *,
    bundle_index: Mapping[str, Any],
    verification_report: Mapping[str, Any],
    reviewer_handoff_summary: Mapping[str, Any],
    verification_report_bytes: bytes,
    reviewer_handoff_summary_bytes: bytes,
    blockers: list[str],
) -> Mapping[str, Any] | None:
    try:
        return build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index(
            verification_report=verification_report,
            reviewer_handoff_summary=reviewer_handoff_summary,
            verification_report_bytes=verification_report_bytes,
            reviewer_handoff_summary_bytes=reviewer_handoff_summary_bytes,
            now_utc=_parse_utc(
                _required_string(
                    bundle_index.get("created_at_utc"),
                    "bundle_index_created_at_invalid",
                )
            ),
        )
    except HandoffBundleIndexError as exc:
        blockers.append(f"source_contract_failed:{exc.code}")
    except ValueError:
        blockers.append("source_contract_failed:invalid_source_artifact")
    return None


def _compare_rebuilt_bundle_index(
    *,
    observed: Mapping[str, Any],
    rebuilt: Mapping[str, Any],
    blockers: list[str],
) -> str:
    if _deterministic_artifact(observed) == _deterministic_artifact(rebuilt):
        return "match"
    blockers.append("rebuilt_bundle_index_mismatch")
    return "mismatch"


def _collect_boundary_blockers(
    bundle_index: Mapping[str, Any],
    blockers: list[str],
) -> None:
    operator_boundary = _mapping(bundle_index.get("operator_boundary"))
    if bundle_index.get("manual_review_required") is not True:
        blockers.append("manual_review_required_not_true")
    if operator_boundary.get("manual_review_required") is not True:
        blockers.append("operator_boundary_manual_review_required_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if bundle_index.get(field) is not False:
            blockers.append(f"{field}_not_false")
        if operator_boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")


def _collect_handoff_bundle_blockers(
    bundle_index: Mapping[str, Any],
    *,
    verification_report_bytes: bytes,
    reviewer_handoff_summary_bytes: bytes,
    blockers: list[str],
) -> None:
    handoff = _mapping(bundle_index.get("handoff_bundle"))
    if handoff.get("summary_artifact_id") != REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID:
        blockers.append("handoff_bundle_summary_artifact_id_mismatch")
    if handoff.get("source_verification_artifact_id") != FINAL_VERIFICATION_ARTIFACT_ID:
        blockers.append("handoff_bundle_source_verification_artifact_id_mismatch")
    if handoff.get("summary_sha256") != _sha256_hex(reviewer_handoff_summary_bytes):
        blockers.append("handoff_bundle_summary_sha256_mismatch")
    if handoff.get("source_verification_sha256") != _sha256_hex(
        verification_report_bytes
    ):
        blockers.append("handoff_bundle_source_verification_sha256_mismatch")
    for field in ("source_contract_check", "rebuilt_summary_check"):
        if handoff.get(field) != "match":
            blockers.append(f"handoff_bundle_{field}_not_match")
    if handoff.get("manual_review_required") is not True:
        blockers.append("handoff_bundle_manual_review_required_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if handoff.get(field) is not False:
            blockers.append(f"handoff_bundle_{field}_not_false")


def _collect_consistency_blockers(
    bundle_index: Mapping[str, Any],
    blockers: list[str],
) -> None:
    consistency = _mapping(bundle_index.get("consistency"))
    if consistency.get("required_artifacts_present") != list(_REQUIRED_ARTIFACTS):
        blockers.append("consistency_required_artifacts_mismatch")
    for field in (
        "all_artifact_digests_recorded",
        "verification_report_ok",
        "reviewer_handoff_summary_ok",
        "template_only",
    ):
        if consistency.get(field) is not True:
            blockers.append(f"consistency_{field}_not_true")
    for field in ("source_contract_check", "rebuilt_summary_check"):
        if consistency.get(field) != "match":
            blockers.append(f"consistency_{field}_not_match")
    for field in ("artifact_payloads_included", "local_paths_recorded"):
        if consistency.get(field) is not False:
            blockers.append(f"consistency_{field}_not_false")


def _artifact_records(
    bundle_index: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, Mapping[str, Any]]:
    raw_records = bundle_index.get("artifacts")
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
            blockers.append(f"artifact_record_unknown:{_safe_reason(artifact_id)}")
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


def _required_string(value: Any, reason: str) -> str:
    if not isinstance(value, str):
        raise HandoffBundleVerificationError(reason)
    return value


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
            "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index_"
            f"verification_failed:{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


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
            "route-stage reviewer handoff bundle index verification contains "
            "forbidden markers"
        )


if __name__ == "__main__":
    raise SystemExit(main())
