# SPDX-License-Identifier: BUSL-1.1
"""Build a local MAGMA reviewer handoff bundle index."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_magma_alert_feed_reviewer_bridge_event_template import (  # noqa: E402
    TEMPLATE_VERSION,
)
from tools.build_magma_alert_feed_reviewer_handoff_summary import (  # noqa: E402
    SUMMARY_VERSION,
)
from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
    PACKAGE_VERSION,
)


BUNDLE_INDEX_VERSION = "magma_alert_feed_reviewer_handoff_bundle_index.v1"

_ARTIFACT_ORDER = (
    "release_evidence_package",
    "validator_report",
    "reviewer_handoff_summary",
    "bridge_event_template",
)


class BundleIndexError(ValueError):
    """Raised when a bundle index input is unsafe or inconsistent."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-json", required=True, type=Path)
    parser.add_argument("--validation-json", required=True, type=Path)
    parser.add_argument("--summary-json", required=True, type=Path)
    parser.add_argument("--bridge-template-json", required=True, type=Path)
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-28T08:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
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
        report = build_magma_alert_feed_reviewer_handoff_bundle_index(
            package=package,
            validation_report=validation_report,
            reviewer_summary=reviewer_summary,
            bridge_template_report=bridge_template_report,
            package_bytes=package_bytes,
            validation_bytes=validation_bytes,
            summary_bytes=summary_bytes,
            bridge_template_bytes=bridge_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except BundleIndexError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report("handoff_bundle_index_invalid")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "MAGMA reviewer handoff bundle index FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_magma_alert_feed_reviewer_handoff_bundle_index(
    *,
    package: Mapping[str, Any],
    validation_report: Mapping[str, Any],
    reviewer_summary: Mapping[str, Any],
    bridge_template_report: Mapping[str, Any],
    package_bytes: bytes,
    validation_bytes: bytes,
    summary_bytes: bytes,
    bridge_template_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free local bundle index for reviewer handoff artifacts."""

    _assert_mapping("release_evidence_package", package)
    _assert_mapping("validator_report", validation_report)
    _assert_mapping("reviewer_handoff_summary", reviewer_summary)
    _assert_mapping("bridge_event_template", bridge_template_report)
    _assert_no_forbidden_input("release_evidence_package", package)
    _assert_no_forbidden_input("validator_report", validation_report)
    _assert_no_forbidden_input("reviewer_handoff_summary", reviewer_summary)
    _assert_no_forbidden_input("bridge_event_template", bridge_template_report)

    _assert_package_contract(package)
    _assert_validation_contract(validation_report)
    _assert_summary_contract(reviewer_summary)
    bridge_event_payload = _assert_bridge_template_contract(bridge_template_report)

    identity = _matching_identity(
        package,
        validation_report,
        reviewer_summary,
        bridge_event_payload,
    )
    artifacts = [
        _artifact_record(
            "release_evidence_package",
            "operator_provided_release_evidence_package",
            package,
            package_bytes,
        ),
        _artifact_record(
            "validator_report",
            "reviewer_validation_report",
            validation_report,
            validation_bytes,
        ),
        _artifact_record(
            "reviewer_handoff_summary",
            "operator_owned_reviewer_handoff_summary",
            reviewer_summary,
            summary_bytes,
        ),
        _artifact_record(
            "bridge_event_template",
            "template_only_bridge_handoff_event",
            bridge_template_report,
            bridge_template_bytes,
        ),
    ]
    index = {
        "ok": True,
        "bundle_index_version": BUNDLE_INDEX_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "release_ref": identity["release_ref"],
        "commit_sha": identity["commit_sha"],
        "ci_run_ref": identity["ci_run_ref"],
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "consistency": {
            "release_ref_match": True,
            "commit_sha_match": True,
            "ci_run_ref_match": True,
            "required_artifacts_present": list(_ARTIFACT_ORDER),
            "all_artifact_digests_recorded": True,
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
        "blockers": [],
        "warnings": [],
    }
    _assert_no_forbidden_output(json.dumps(index, allow_nan=False, sort_keys=True))
    return index


def _load_json_artifact(path: Path, artifact_id: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BundleIndexError(f"{artifact_id}_unreadable") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise BundleIndexError(f"{artifact_id}_decode_error") from exc
    except json.JSONDecodeError as exc:
        raise BundleIndexError(f"{artifact_id}_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise BundleIndexError(f"{artifact_id}_not_mapping")
    return raw, parsed


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "bundle_index_version": BUNDLE_INDEX_VERSION,
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
        "blockers": [f"handoff_bundle_index_failed:{reason}"],
        "warnings": [],
    }


def _assert_mapping(artifact_id: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise BundleIndexError(f"{artifact_id}_not_mapping")


def _assert_no_forbidden_input(artifact_id: str, value: Mapping[str, Any]) -> None:
    if _forbidden_output_markers(json.dumps(value, sort_keys=True)):
        raise BundleIndexError(f"{artifact_id}_forbidden_marker")


def _assert_package_contract(package: Mapping[str, Any]) -> None:
    if package.get("package_version") != PACKAGE_VERSION:
        raise BundleIndexError("release_evidence_package_version_mismatch")
    if package.get("ok") is not True:
        raise BundleIndexError("release_evidence_package_not_ok")
    ownership = _mapping(package.get("operator_ownership"))
    if ownership.get("manual_review_required") is not True:
        raise BundleIndexError("release_evidence_package_manual_gate_missing")
    if ownership.get("automatic_release_decision") is not False:
        raise BundleIndexError("release_evidence_package_auto_decision_not_false")


def _assert_validation_contract(validation_report: Mapping[str, Any]) -> None:
    if validation_report.get("package_version") != PACKAGE_VERSION:
        raise BundleIndexError("validator_report_package_version_mismatch")
    if validation_report.get("ok") is not True:
        raise BundleIndexError("validator_report_not_ok")
    _expect_false(validation_report, "automatic_release_decision", "validator_report")
    _expect_false(validation_report, "release_decision_made", "validator_report")
    _expect_false(validation_report, "runtime_controls_added", "validator_report")
    _expect_false(validation_report, "transport_added", "validator_report")
    _expect_false(validation_report, "external_fetch_performed", "validator_report")


def _assert_summary_contract(reviewer_summary: Mapping[str, Any]) -> None:
    if reviewer_summary.get("summary_version") != SUMMARY_VERSION:
        raise BundleIndexError("reviewer_handoff_summary_version_mismatch")
    if reviewer_summary.get("ok") is not True:
        raise BundleIndexError("reviewer_handoff_summary_not_ok")
    _expect_false(
        reviewer_summary,
        "automatic_release_decision",
        "reviewer_handoff_summary",
    )
    _expect_false(reviewer_summary, "approval_granted", "reviewer_handoff_summary")
    _expect_false(reviewer_summary, "release_decision_made", "reviewer_handoff_summary")
    _expect_false(reviewer_summary, "runtime_controls_added", "reviewer_handoff_summary")
    _expect_false(reviewer_summary, "transport_added", "reviewer_handoff_summary")
    _expect_false(reviewer_summary, "external_fetch_performed", "reviewer_handoff_summary")


def _assert_bridge_template_contract(
    bridge_template_report: Mapping[str, Any],
) -> Mapping[str, Any]:
    if bridge_template_report.get("template_version") != TEMPLATE_VERSION:
        raise BundleIndexError("bridge_event_template_version_mismatch")
    if bridge_template_report.get("ok") is not True:
        raise BundleIndexError("bridge_event_template_not_ok")
    _expect_false(
        bridge_template_report,
        "direct_bridge_write_performed",
        "bridge_event_template",
    )
    _expect_false(bridge_template_report, "automatic_release_decision", "bridge_event_template")
    _expect_false(bridge_template_report, "approval_granted", "bridge_event_template")
    _expect_false(bridge_template_report, "release_decision_made", "bridge_event_template")
    _expect_false(bridge_template_report, "runtime_controls_added", "bridge_event_template")
    _expect_false(bridge_template_report, "transport_added", "bridge_event_template")
    _expect_false(bridge_template_report, "external_fetch_performed", "bridge_event_template")
    event = _mapping(bridge_template_report.get("bridge_event_template"))
    payload = _mapping(event.get("payload"))
    if payload.get("schema_version") != TEMPLATE_VERSION:
        raise BundleIndexError("bridge_event_template_payload_version_mismatch")
    if payload.get("template_only") is not True:
        raise BundleIndexError("bridge_event_template_not_template_only")
    for field in (
        "direct_bridge_write_performed",
        "transport_added",
        "external_fetch_performed",
        "runtime_controls_added",
    ):
        if payload.get(field) is not False:
            raise BundleIndexError(f"bridge_event_template_{field}_not_false")
    operator_decision = _mapping(payload.get("operator_decision"))
    for field in (
        "approval_granted",
        "release_decision_made",
        "automatic_release_decision",
        "decision_reference_is_approval",
        "decision_reference_is_release_decision",
    ):
        if operator_decision.get(field) is not False:
            raise BundleIndexError(f"bridge_event_template_{field}_not_false")
    return payload


def _matching_identity(
    package: Mapping[str, Any],
    validation_report: Mapping[str, Any],
    reviewer_summary: Mapping[str, Any],
    bridge_event_payload: Mapping[str, Any],
) -> dict[str, str]:
    release_refs = {
        _safe_text(package.get("release_ref")),
        _safe_text(validation_report.get("release_ref")),
        _safe_text(reviewer_summary.get("release_ref")),
        _safe_text(bridge_event_payload.get("release_ref")),
    }
    commit_shas = {
        _safe_text(package.get("commit_sha")),
        _safe_text(validation_report.get("commit_sha")),
        _safe_text(reviewer_summary.get("commit_sha")),
        _safe_text(bridge_event_payload.get("commit_sha")),
    }
    ci_run_refs = {
        _safe_text(package.get("ci_run_ref")),
        _safe_text(validation_report.get("ci_run_ref")),
        _safe_text(reviewer_summary.get("ci_run_ref")),
        _safe_text(bridge_event_payload.get("ci_run_ref")),
    }
    if len(release_refs) != 1 or len(commit_shas) != 1 or len(ci_run_refs) != 1:
        raise BundleIndexError("artifact_identity_mismatch")
    return {
        "release_ref": next(iter(release_refs)),
        "commit_sha": next(iter(commit_shas)),
        "ci_run_ref": next(iter(ci_run_refs)),
    }


def _artifact_record(
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
    for field in ("package_version", "summary_version", "template_version"):
        value = artifact.get(field)
        if isinstance(value, str):
            return value
    return "validator_report.v1"


def _expect_false(artifact: Mapping[str, Any], field: str, label: str) -> None:
    if artifact.get(field) is not False:
        raise BundleIndexError(f"{label}_{field}_not_false")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) and value else "invalid_ref"


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise BundleIndexError("now_utc_unsafe")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise BundleIndexError("now_utc_unsafe") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise BundleIndexError("now_utc_unsafe")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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
            "sanitized reviewer handoff bundle index contains forbidden markers"
        )


if __name__ == "__main__":
    raise SystemExit(main())
