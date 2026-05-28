# SPDX-License-Identifier: BUSL-1.1
"""Verify a local MAGMA reviewer handoff bundle index."""
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

from tools.build_magma_alert_feed_reviewer_handoff_bundle_index import (  # noqa: E402
    BUNDLE_INDEX_VERSION,
)
from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
)


VERIFICATION_VERSION = "magma_alert_feed_reviewer_handoff_bundle_verification.v1"

_REQUIRED_ARTIFACTS = (
    "release_evidence_package",
    "validator_report",
    "reviewer_handoff_summary",
    "bridge_event_template",
)


class BundleVerificationError(ValueError):
    """Raised when verifier inputs cannot be safely read."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-index-json", required=True, type=Path)
    parser.add_argument("--package-json", required=True, type=Path)
    parser.add_argument("--validation-json", required=True, type=Path)
    parser.add_argument("--summary-json", required=True, type=Path)
    parser.add_argument("--bridge-template-json", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _, bundle_index = _load_json_artifact(
            args.bundle_index_json,
            "bundle_index",
        )
        package_bytes, package = _load_json_artifact(
            args.package_json,
            "release_evidence_package",
        )
        validation_bytes, validation_report = _load_json_artifact(
            args.validation_json,
            "validator_report",
        )
        summary_bytes, reviewer_summary = _load_json_artifact(
            args.summary_json,
            "reviewer_handoff_summary",
        )
        bridge_bytes, bridge_template_report = _load_json_artifact(
            args.bridge_template_json,
            "bridge_event_template",
        )
        report = verify_magma_alert_feed_reviewer_handoff_bundle_index(
            bundle_index=bundle_index,
            package=package,
            validation_report=validation_report,
            reviewer_summary=reviewer_summary,
            bridge_template_report=bridge_template_report,
            package_bytes=package_bytes,
            validation_bytes=validation_bytes,
            summary_bytes=summary_bytes,
            bridge_template_bytes=bridge_bytes,
        )
    except BundleVerificationError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report("handoff_bundle_verification_invalid")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "MAGMA reviewer handoff bundle verification FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def verify_magma_alert_feed_reviewer_handoff_bundle_index(
    *,
    bundle_index: Mapping[str, Any],
    package: Mapping[str, Any],
    validation_report: Mapping[str, Any],
    reviewer_summary: Mapping[str, Any],
    bridge_template_report: Mapping[str, Any],
    package_bytes: bytes,
    validation_bytes: bytes,
    summary_bytes: bytes,
    bridge_template_bytes: bytes,
) -> dict[str, Any]:
    """Recompute local artifact digests and compare them with a bundle index."""

    _assert_mapping("bundle_index", bundle_index)
    _assert_no_forbidden_input("bundle_index", bundle_index)
    artifact_inputs = {
        "release_evidence_package": (package, package_bytes),
        "validator_report": (validation_report, validation_bytes),
        "reviewer_handoff_summary": (reviewer_summary, summary_bytes),
        "bridge_event_template": (bridge_template_report, bridge_template_bytes),
    }
    for artifact_id, (artifact, _) in artifact_inputs.items():
        _assert_mapping(artifact_id, artifact)
        _assert_no_forbidden_input(artifact_id, artifact)

    blockers: list[str] = []
    warnings: list[str] = []
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
    identity = _verified_identity(
        (
            bundle_index,
            package,
            validation_report,
            reviewer_summary,
            bridge_template_report,
        ),
        blockers,
    )

    records = _artifact_records(bundle_index, blockers)
    digest_checks: dict[str, str] = {}
    size_checks: dict[str, str] = {}
    schema_version_checks: dict[str, str] = {}
    for artifact_id in _REQUIRED_ARTIFACTS:
        artifact, raw = artifact_inputs[artifact_id]
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
        "release_ref": identity["release_ref"],
        "commit_sha": identity["commit_sha"],
        "ci_run_ref": identity["ci_run_ref"],
        "artifact_count_checked": len(_REQUIRED_ARTIFACTS),
        "digest_checks": digest_checks,
        "size_checks": size_checks,
        "schema_version_checks": schema_version_checks,
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


def _load_json_artifact(path: Path, artifact_id: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BundleVerificationError(f"{artifact_id}_unreadable") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise BundleVerificationError(f"{artifact_id}_decode_error") from exc
    except json.JSONDecodeError as exc:
        raise BundleVerificationError(f"{artifact_id}_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise BundleVerificationError(f"{artifact_id}_not_mapping")
    return raw, parsed


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
        "blockers": [f"handoff_bundle_verification_failed:{reason}"],
        "warnings": [],
    }


def _artifact_records(
    bundle_index: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, Mapping[str, Any]]:
    raw_records = bundle_index.get("artifacts")
    if not isinstance(raw_records, list):
        blockers.append("artifact_records_missing")
        return {}
    records: dict[str, Mapping[str, Any]] = {}
    for raw_record in raw_records:
        if not isinstance(raw_record, Mapping):
            blockers.append("artifact_record_not_mapping")
            continue
        artifact_id = raw_record.get("artifact_id")
        if not isinstance(artifact_id, str):
            blockers.append("artifact_record_id_missing")
            continue
        if artifact_id in records:
            blockers.append(f"artifact_record_duplicate:{artifact_id}")
            continue
        records[artifact_id] = raw_record
    for artifact_id in _REQUIRED_ARTIFACTS:
        if artifact_id not in records:
            blockers.append(f"artifact_record_missing:{artifact_id}")
    return records


def _collect_boundary_blockers(
    bundle_index: Mapping[str, Any],
    blockers: list[str],
) -> None:
    consistency = _mapping(bundle_index.get("consistency"))
    operator_boundary = _mapping(bundle_index.get("operator_boundary"))
    for field in (
        "artifact_payloads_included",
        "local_paths_recorded",
    ):
        if consistency.get(field) is not False:
            blockers.append(f"consistency_{field}_not_false")
    for field in (
        "approval_granted",
        "release_decision_made",
        "automatic_release_decision",
        "direct_bridge_write_performed",
        "transport_added",
        "external_fetch_performed",
        "runtime_controls_added",
        "artifact_payloads_included",
        "local_paths_recorded",
    ):
        if operator_boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")


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
    for field in ("package_version", "summary_version", "template_version"):
        value = artifact.get(field)
        if isinstance(value, str):
            return value
    return "validator_report.v1"


def _assert_mapping(artifact_id: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise BundleVerificationError(f"{artifact_id}_not_mapping")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _assert_no_forbidden_input(artifact_id: str, value: Mapping[str, Any]) -> None:
    if _forbidden_output_markers(json.dumps(value, sort_keys=True)):
        raise BundleVerificationError(f"{artifact_id}_forbidden_marker")


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
            "sanitized reviewer handoff bundle verification contains forbidden markers"
        )


if __name__ == "__main__":
    raise SystemExit(main())
