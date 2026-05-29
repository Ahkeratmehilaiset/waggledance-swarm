# SPDX-License-Identifier: BUSL-1.1
"""Verify a local MAGMA verification bridge-template index entry."""
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

from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template import (  # noqa: E402
    TEMPLATE_VERSION as BRIDGE_TEMPLATE_VERSION,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary import (  # noqa: E402
    SUMMARY_VERSION,
)
from tools.build_magma_decision_review_verification_template_index_entry import (  # noqa: E402
    INDEX_ENTRY_VERSION,
    BridgeTemplateIndexEntryError,
    build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry,
)
from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
)


VERIFICATION_VERSION = "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification.v1"

_REQUIRED_ARTIFACTS = (
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


class BridgeTemplateIndexEntryVerificationError(ValueError):
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
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _, index_entry = _load_json_artifact(
            args.index_entry_json,
            "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry",
        )
        summary_bytes, summary = _load_json_artifact(
            args.review_bundle_verification_summary_json,
            "operator_decision_reference_review_bundle_verification_summary",
        )
        template_bytes, template_report = _load_json_artifact(
            args.bridge_event_template_json,
            "operator_decision_reference_review_bundle_verification_bridge_event_template",
        )
        report = verify_magma_decision_review_verification_template_index_entry(
            index_entry=index_entry,
            verification_summary=summary,
            bridge_event_template_report=template_report,
            verification_summary_bytes=summary_bytes,
            bridge_event_template_bytes=template_bytes,
        )
    except BridgeTemplateIndexEntryVerificationError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report(
            "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_invalid"
        )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "MAGMA operator decision-reference review bundle verification "
            "bridge-event template index entry verification FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def verify_magma_decision_review_verification_template_index_entry(
    *,
    index_entry: Mapping[str, Any],
    verification_summary: Mapping[str, Any],
    bridge_event_template_report: Mapping[str, Any],
    verification_summary_bytes: bytes,
    bridge_event_template_bytes: bytes,
) -> dict[str, Any]:
    """Recompute artifact checks for a bridge-template index entry."""

    _assert_mapping(
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry",
        index_entry,
    )
    _assert_mapping(
        "operator_decision_reference_review_bundle_verification_summary",
        verification_summary,
    )
    _assert_mapping(
        "operator_decision_reference_review_bundle_verification_bridge_event_template",
        bridge_event_template_report,
    )
    for artifact_id, artifact in (
        (
            "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry",
            index_entry,
        ),
        (
            "operator_decision_reference_review_bundle_verification_summary",
            verification_summary,
        ),
        (
            "operator_decision_reference_review_bundle_verification_bridge_event_template",
            bridge_event_template_report,
        ),
    ):
        _assert_no_forbidden_input(artifact_id, artifact)

    blockers: list[str] = []
    warnings: list[str] = []
    rebuilt_entry = _rebuilt_source_index_entry(
        verification_summary=verification_summary,
        bridge_event_template_report=bridge_event_template_report,
        verification_summary_bytes=verification_summary_bytes,
        bridge_event_template_bytes=bridge_event_template_bytes,
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
    _collect_template_entry_blockers(index_entry, blockers)
    _collect_consistency_blockers(index_entry, blockers)
    source_reference = _source_operator_decision_reference(
        verification_summary,
        blockers,
    )
    index_reference = _index_operator_decision_reference(index_entry, blockers)
    if source_reference != index_reference:
        blockers.append("operator_decision_reference_mismatch")

    identity = _verified_identity(
        (index_entry, verification_summary, _template_payload_identity_source(bridge_event_template_report)),
        blockers,
    )
    if rebuilt_entry is not None:
        rebuilt_index_entry_check = _compare_rebuilt_index_entry(
            observed=index_entry,
            rebuilt=rebuilt_entry,
            blockers=blockers,
        )
        for field in ("release_ref", "commit_sha", "ci_run_ref"):
            if index_entry.get(field) != rebuilt_entry.get(field):
                blockers.append(f"rebuilt_identity_{field}_mismatch")

    artifacts = {
        "operator_decision_reference_review_bundle_verification_summary": (
            verification_summary,
            verification_summary_bytes,
        ),
        "operator_decision_reference_review_bundle_verification_bridge_event_template": (
            bridge_event_template_report,
            bridge_event_template_bytes,
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
        "release_ref": identity["release_ref"],
        "commit_sha": identity["commit_sha"],
        "ci_run_ref": identity["ci_run_ref"],
        "operator_decision_reference_review": {
            "decision_reference": index_reference["decision_reference"],
            "expected_decision_reference": index_reference[
                "expected_decision_reference"
            ],
            "decision_reference_verified": not blockers,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
            "review_context_only": True,
            "manual_review_required": True,
        },
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
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    _assert_no_forbidden_output(json.dumps(report, allow_nan=False, sort_keys=True))
    return report


def _rebuilt_source_index_entry(
    *,
    verification_summary: Mapping[str, Any],
    bridge_event_template_report: Mapping[str, Any],
    verification_summary_bytes: bytes,
    bridge_event_template_bytes: bytes,
    blockers: list[str],
) -> Mapping[str, Any] | None:
    try:
        return build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry(
            verification_summary=verification_summary,
            bridge_event_template_report=bridge_event_template_report,
            verification_summary_bytes=verification_summary_bytes,
            bridge_event_template_bytes=bridge_event_template_bytes,
        )
    except BridgeTemplateIndexEntryError as exc:
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


def _template_payload_identity_source(
    template_report: Mapping[str, Any],
) -> Mapping[str, Any]:
    event = _mapping(template_report.get("bridge_event_template"))
    return _mapping(event.get("payload"))


def _verified_identity(
    artifacts: Sequence[Mapping[str, Any]],
    blockers: list[str],
) -> dict[str, str]:
    return {
        field: _required_matching_identity(artifacts, field, blockers)
        for field in ("release_ref", "commit_sha", "ci_run_ref")
    }


def _required_matching_identity(
    artifacts: Sequence[Mapping[str, Any]],
    field: str,
    blockers: list[str],
) -> str:
    values: set[str] = set()
    missing = False
    for artifact in artifacts:
        value = artifact.get(field)
        if not isinstance(value, str) or not value:
            missing = True
            continue
        values.add(value)
    if missing:
        blockers.append(f"artifact_identity_{field}_missing")
    if len(values) != 1:
        if values:
            blockers.append(f"artifact_identity_{field}_mismatch")
        return "invalid_ref"
    return next(iter(values)) if not missing else "invalid_ref"


def _source_operator_decision_reference(
    verification_summary: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, str]:
    reference = _mapping(
        verification_summary.get("operator_decision_reference_review")
    )
    decision_reference = reference.get("decision_reference")
    expected_decision_reference = reference.get("expected_decision_reference")
    if not isinstance(decision_reference, str) or not decision_reference:
        blockers.append("source_operator_decision_reference_missing")
        decision_reference = "invalid_ref"
    if (
        not isinstance(expected_decision_reference, str)
        or not expected_decision_reference
    ):
        blockers.append("source_operator_decision_reference_expected_missing")
        expected_decision_reference = "invalid_ref"
    return {
        "decision_reference": decision_reference,
        "expected_decision_reference": expected_decision_reference,
    }


def _index_operator_decision_reference(
    index_entry: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, str]:
    reference = _mapping(index_entry.get("operator_decision_reference_review"))
    decision_reference = reference.get("decision_reference")
    expected_decision_reference = reference.get("expected_decision_reference")
    if not isinstance(decision_reference, str) or not decision_reference:
        blockers.append("operator_decision_reference_missing")
        decision_reference = "invalid_ref"
    if (
        not isinstance(expected_decision_reference, str)
        or not expected_decision_reference
    ):
        blockers.append("operator_decision_reference_expected_missing")
        expected_decision_reference = "invalid_ref"
    if reference.get("decision_reference_verified") is not True:
        blockers.append("operator_decision_reference_not_verified")
    if reference.get("decision_must_be_recorded_separately") is not True:
        blockers.append("operator_decision_reference_record_separately_not_true")
    if reference.get("review_context_only") is not True:
        blockers.append("operator_decision_reference_review_context_only_not_true")
    if reference.get("manual_review_required") is not True:
        blockers.append("operator_decision_reference_manual_review_required_not_true")
    for field in _REFERENCE_FALSE_FIELDS:
        if reference.get(field) is not False:
            blockers.append(f"operator_decision_reference_{field}_not_false")
    for field in _AUTHORITY_FALSE_FIELDS:
        if field in reference and reference.get(field) is not False:
            blockers.append(f"operator_decision_reference_{field}_not_false")
    return {
        "decision_reference": decision_reference,
        "expected_decision_reference": expected_decision_reference,
    }


def _collect_boundary_blockers(
    index_entry: Mapping[str, Any],
    blockers: list[str],
) -> None:
    operator_boundary = _mapping(index_entry.get("operator_boundary"))
    if index_entry.get("manual_review_required") is not True:
        blockers.append("manual_review_required_not_true")
    if operator_boundary.get("manual_review_required") is not True:
        blockers.append("operator_boundary_manual_review_required_not_true")
    for field in _AUTHORITY_FALSE_FIELDS:
        if index_entry.get(field) is not False:
            blockers.append(f"{field}_not_false")
        if operator_boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")


def _collect_template_entry_blockers(
    index_entry: Mapping[str, Any],
    blockers: list[str],
) -> None:
    template_entry = _mapping(index_entry.get("template_index_entry"))
    if template_entry.get("artifact_id") != _REQUIRED_ARTIFACTS[1]:
        blockers.append("template_index_entry_artifact_id_mismatch")
    if template_entry.get("template_version") != BRIDGE_TEMPLATE_VERSION:
        blockers.append("template_index_entry_template_version_mismatch")
    for field in (
        "template_only",
        "bridge_event_schema_validated",
        "manual_review_required",
    ):
        if template_entry.get(field) is not True:
            blockers.append(f"template_index_entry_{field}_not_true")
    for field in ("source_contract_check", "rebuilt_template_check"):
        if template_entry.get(field) != "match":
            blockers.append(f"template_index_entry_{field}_not_match")
    if template_entry.get("event_type") != "handoff":
        blockers.append("template_index_entry_event_type_mismatch")
    if (
        template_entry.get("event_status")
        != "decision_reference_review_bundle_verification_ready"
    ):
        blockers.append("template_index_entry_event_status_mismatch")
    for field in _AUTHORITY_FALSE_FIELDS:
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
        "release_ref_match",
        "commit_sha_match",
        "ci_run_ref_match",
        "decision_reference_match",
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
    for field in ("summary_version", "template_version", "index_entry_version"):
        value = artifact.get(field)
        if isinstance(value, str):
            return value
    return "invalid_schema"


def _load_json_artifact(path: Path, artifact_id: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BridgeTemplateIndexEntryVerificationError(
            f"{artifact_id}_unreadable"
        ) from exc
    parsed = _parse_json_bytes(raw, artifact_id)
    if not isinstance(parsed, Mapping):
        raise BridgeTemplateIndexEntryVerificationError(
            f"{artifact_id}_not_mapping"
        )
    return raw, parsed


def _parse_json_bytes(raw: bytes, artifact_id: str) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite_json_constant")

    try:
        return json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
    except UnicodeDecodeError as exc:
        raise BridgeTemplateIndexEntryVerificationError(
            f"{artifact_id}_decode_error"
        ) from exc
    except json.JSONDecodeError as exc:
        raise BridgeTemplateIndexEntryVerificationError(
            f"{artifact_id}_json_error"
        ) from exc
    except ValueError as exc:
        raise BridgeTemplateIndexEntryVerificationError(
            f"{artifact_id}_json_error"
        ) from exc


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "verification_version": VERIFICATION_VERSION,
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
            f"index_entry_verification_failed:{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


def _assert_mapping(artifact_id: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise BridgeTemplateIndexEntryVerificationError(
            f"{artifact_id}_not_mapping"
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _assert_no_forbidden_input(artifact_id: str, value: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise BridgeTemplateIndexEntryVerificationError(
            f"{artifact_id}_non_finite_json_value"
        ) from exc
    if _forbidden_output_markers(serialized):
        raise BridgeTemplateIndexEntryVerificationError(
            f"{artifact_id}_forbidden_marker"
        )


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
            "sanitized operator decision-reference review bundle verification "
            "bridge-event template index entry verification contains forbidden "
            "markers"
        )


if __name__ == "__main__":
    raise SystemExit(main())
