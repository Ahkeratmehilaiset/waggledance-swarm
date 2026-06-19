#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a path-free route-stage feed-health reviewer handoff bundle index."""

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

from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary import (  # noqa: E402
    PROOF_ID as HANDOFF_SUMMARY_PROOF_ID,
    SUMMARY_VERSION as HANDOFF_SUMMARY_VERSION,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary,
)
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
    FORBIDDEN_OUTPUT_MARKERS,
)
from tools.verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    VERIFICATION_VERSION as FINAL_VERIFICATION_VERSION,
)


BUNDLE_INDEX_VERSION = "waggledance.route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index.v1"
PROOF_ID = "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index_v1"
FINAL_VERIFICATION_ARTIFACT_ID = (
    "route_stage_feed_health_drill_evidence_final_verification"
)
REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID = (
    "route_stage_feed_health_drill_evidence_reviewer_handoff_summary"
)
_ARTIFACT_ORDER = (
    FINAL_VERIFICATION_ARTIFACT_ID,
    REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
)
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_WINDOWS_DRIVE_PATH_RE = re.compile(r"(?:^|[^A-Za-z0-9])(?:[A-Za-z]:[\\/])")


class HandoffBundleIndexError(ValueError):
    """Raised when local handoff bundle inputs are unsafe or inconsistent."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
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
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-19T06:30:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        verification_bytes, verification_report = _load_json_artifact(
            args.verification_json,
            FINAL_VERIFICATION_ARTIFACT_ID,
        )
        summary_bytes, reviewer_handoff_summary = _load_json_artifact(
            args.summary_json,
            REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
        )
        report = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index(
            verification_report=verification_report,
            reviewer_handoff_summary=reviewer_handoff_summary,
            verification_report_bytes=verification_bytes,
            reviewer_handoff_summary_bytes=summary_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except HandoffBundleIndexError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report(
            "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index_invalid"
        )

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json or report["ok"]:
        print(encoded)
    else:
        print(
            "route-stage feed-health reviewer handoff bundle index FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index(
    *,
    verification_report: Mapping[str, Any],
    reviewer_handoff_summary: Mapping[str, Any],
    verification_report_bytes: bytes,
    reviewer_handoff_summary_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a local bundle index for reviewer handoff artifacts without payloads."""

    _assert_mapping(FINAL_VERIFICATION_ARTIFACT_ID, verification_report)
    _assert_mapping(REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID, reviewer_handoff_summary)
    _assert_no_forbidden_input(FINAL_VERIFICATION_ARTIFACT_ID, verification_report)
    _assert_no_forbidden_input(
        REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
        reviewer_handoff_summary,
    )
    _assert_bytes_match_artifact(
        FINAL_VERIFICATION_ARTIFACT_ID,
        verification_report,
        verification_report_bytes,
    )
    _assert_bytes_match_artifact(
        REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
        reviewer_handoff_summary,
        reviewer_handoff_summary_bytes,
    )
    _assert_verification_contract(verification_report)
    _assert_summary_contract(
        reviewer_handoff_summary,
        verification_report=verification_report,
    )

    rebuilt_summary = _rebuilt_reviewer_handoff_summary(
        verification_report=verification_report,
        reviewer_handoff_summary=reviewer_handoff_summary,
    )
    if _deterministic_artifact(rebuilt_summary) != _deterministic_artifact(
        reviewer_handoff_summary
    ):
        raise HandoffBundleIndexError("reviewer_handoff_summary_rebuilt_mismatch")

    artifacts = [
        _artifact_record(
            artifact_id=FINAL_VERIFICATION_ARTIFACT_ID,
            role="verified_route_stage_final_verification_report",
            artifact=verification_report,
            raw=verification_report_bytes,
        ),
        _artifact_record(
            artifact_id=REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
            role="operator_owned_reviewer_handoff_summary",
            artifact=reviewer_handoff_summary,
            raw=reviewer_handoff_summary_bytes,
        ),
    ]
    reviewer = _mapping(reviewer_handoff_summary.get("reviewer_ownership"))
    verifier = _mapping(reviewer_handoff_summary.get("validated_verifier_chain"))
    index = {
        "proof_id": PROOF_ID,
        "ok": True,
        "bundle_index_version": BUNDLE_INDEX_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "handoff_bundle": {
            "summary_artifact_id": REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
            "summary_proof_id": HANDOFF_SUMMARY_PROOF_ID,
            "summary_version": HANDOFF_SUMMARY_VERSION,
            "summary_sha256": _sha256_hex(reviewer_handoff_summary_bytes),
            "source_verification_artifact_id": FINAL_VERIFICATION_ARTIFACT_ID,
            "source_verification_version": FINAL_VERIFICATION_VERSION,
            "source_verification_sha256": _sha256_hex(verification_report_bytes),
            "source_contract_check": "match",
            "rebuilt_summary_check": "match",
            "reviewer_agent_id": _safe_ref_or_invalid(
                reviewer.get("reviewer_agent_id")
            ),
            "handoff_ref": _safe_ref_or_invalid(reviewer.get("handoff_ref")),
            "verification_version": _safe_ref_or_invalid(
                verifier.get("verification_version")
            ),
            "index_entry_version": _safe_ref_or_invalid(
                verifier.get("index_entry_version")
            ),
            "artifact_count_checked": _as_nonnegative_int(
                verifier.get("artifact_count_checked")
            ),
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
        "consistency": {
            "required_artifacts_present": list(_ARTIFACT_ORDER),
            "all_artifact_digests_recorded": True,
            "source_contract_check": "match",
            "rebuilt_summary_check": "match",
            "verification_report_ok": True,
            "reviewer_handoff_summary_ok": True,
            "template_only": True,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "operator_boundary": _authority_boundary(),
        "reviewer_next_actions": [
            "review_route_stage_feed_health_reviewer_handoff_bundle_index",
            "compare_bundle_index_to_local_final_verification_and_summary",
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
        "warnings": _safe_token_list(reviewer_handoff_summary.get("warnings")),
    }
    _assert_no_forbidden_output(json.dumps(index, allow_nan=False, sort_keys=True))
    return index


def _rebuilt_reviewer_handoff_summary(
    *,
    verification_report: Mapping[str, Any],
    reviewer_handoff_summary: Mapping[str, Any],
) -> Mapping[str, Any]:
    reviewer = _mapping(reviewer_handoff_summary.get("reviewer_ownership"))
    rebuilt = build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary(
        verification_report=verification_report,
        reviewer_agent_id=_required_safe_ref(
            reviewer.get("reviewer_agent_id"),
            "reviewer_handoff_summary_reviewer_agent_id_invalid",
        ),
        handoff_ref=_required_safe_ref(
            reviewer.get("handoff_ref"),
            "reviewer_handoff_summary_handoff_ref_invalid",
        ),
        now_utc=_parse_utc(
            _required_safe_ref(
                reviewer_handoff_summary.get("created_at_utc"),
                "reviewer_handoff_summary_created_at_invalid",
            )
        ),
    )
    if rebuilt.get("ok") is not True:
        blockers = _safe_token_list(rebuilt.get("blockers"))
        reason = blockers[0] if blockers else "reviewer_handoff_summary_not_ok"
        raise HandoffBundleIndexError(
            f"reviewer_handoff_summary_source_contract_failed:{reason}"
        )
    return rebuilt


def _assert_verification_contract(report: Mapping[str, Any]) -> None:
    blockers: list[str] = []
    if report.get("ok") is not True:
        blockers.append("final_verification_report_not_ok")
    if report.get("verification_version") != FINAL_VERIFICATION_VERSION:
        blockers.append("final_verification_version_mismatch")
    if report.get("template_only") is not True:
        blockers.append("final_verification_template_only_not_true")
    if report.get("manual_review_required") is not True:
        blockers.append("final_verification_manual_review_required_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if report.get(field) is not False:
            blockers.append(f"final_verification_{field}_not_false")
    _expect_empty_items_or_collect(
        report.get("blockers"),
        "final_verification_blockers_present",
        blockers,
    )
    if report.get("artifact_count_checked") != 2:
        blockers.append("final_verification_artifact_count_checked_mismatch")
    for field in (
        "source_contract_check",
        "rebuilt_index_entry_check",
        "bridge_event_schema_check",
    ):
        if report.get(field) != "match":
            blockers.append(f"final_verification_{field}_not_match")
    for check_name in ("digest_checks", "size_checks", "schema_version_checks"):
        checks = _mapping(report.get(check_name))
        if not checks:
            blockers.append(f"final_verification_{check_name}_missing")
        for artifact_id, status in checks.items():
            if status != "match":
                blockers.append(
                    f"final_verification_{check_name}_{_safe_reason(artifact_id)}_not_match"
                )
    if blockers:
        raise HandoffBundleIndexError(
            f"final_verification_source_contract_failed:{sorted(set(blockers))[0]}"
        )


def _assert_summary_contract(
    summary: Mapping[str, Any],
    *,
    verification_report: Mapping[str, Any],
) -> None:
    blockers: list[str] = []
    blockers.extend(_recursive_contract_blockers(summary))
    if summary.get("ok") is not True:
        blockers.append("reviewer_handoff_summary_not_ok")
    if summary.get("proof_id") != HANDOFF_SUMMARY_PROOF_ID:
        blockers.append("reviewer_handoff_summary_proof_mismatch")
    if summary.get("summary_version") != HANDOFF_SUMMARY_VERSION:
        blockers.append("reviewer_handoff_summary_version_mismatch")
    if summary.get("template_only") is not True:
        blockers.append("reviewer_handoff_summary_template_only_not_true")
    if summary.get("manual_review_required") is not True:
        blockers.append("reviewer_handoff_summary_manual_review_required_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if summary.get(field) is not False:
            blockers.append(f"reviewer_handoff_summary_{field}_not_false")
    _expect_empty_items_or_collect(
        summary.get("blockers"),
        "reviewer_handoff_summary_blockers_present",
        blockers,
    )

    reviewer = _mapping(summary.get("reviewer_ownership"))
    if reviewer.get("manual_review_required") is not True:
        blockers.append("reviewer_ownership_manual_review_required_not_true")
    if _safe_ref_or_invalid(reviewer.get("reviewer_agent_id")) == "invalid_ref":
        blockers.append("reviewer_ownership_reviewer_agent_id_invalid")
    if _safe_ref_or_invalid(reviewer.get("handoff_ref")) == "invalid_ref":
        blockers.append("reviewer_ownership_handoff_ref_invalid")
    for field in ("approval_granted", "release_decision_made", "automatic_release_decision"):
        if reviewer.get(field) is not False:
            blockers.append(f"reviewer_ownership_{field}_not_false")

    verifier = _mapping(summary.get("validated_verifier_chain"))
    if verifier.get("verification_ok") is not True:
        blockers.append("validated_verifier_chain_not_ok")
    _compare_summary_verifier_field(
        verifier,
        verification_report,
        "verification_version",
        blockers,
    )
    _compare_summary_verifier_field(
        verifier,
        verification_report,
        "index_entry_version",
        blockers,
    )
    _compare_summary_verifier_field(
        verifier,
        verification_report,
        "artifact_count_checked",
        blockers,
    )
    for field in (
        "digest_checks",
        "size_checks",
        "schema_version_checks",
        "source_contract_check",
        "rebuilt_index_entry_check",
        "bridge_event_schema_check",
        "template_only",
    ):
        _compare_summary_verifier_field(verifier, verification_report, field, blockers)
    if verifier.get("blocker_count") != 0:
        blockers.append("validated_verifier_chain_blocker_count_nonzero")
    _expect_empty_items_or_collect(
        verifier.get("blockers"),
        "validated_verifier_chain_blockers_present",
        blockers,
    )

    boundary = _mapping(summary.get("operator_boundary"))
    if boundary.get("verification_report_boundary_ok") is not True:
        blockers.append("operator_boundary_verification_report_not_ok")
    _expect_empty_items_or_collect(
        boundary.get("boundary_blockers"),
        "operator_boundary_blockers_present",
        blockers,
    )
    if boundary.get("manual_review_required") is not True:
        blockers.append("operator_boundary_manual_review_required_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")

    if blockers:
        raise HandoffBundleIndexError(
            f"reviewer_handoff_summary_source_contract_failed:{sorted(set(blockers))[0]}"
        )


def _compare_summary_verifier_field(
    verifier: Mapping[str, Any],
    verification_report: Mapping[str, Any],
    field: str,
    blockers: list[str],
) -> None:
    if verifier.get(field) != verification_report.get(field):
        blockers.append(f"validated_verifier_chain_{field}_mismatch")


def _load_json_artifact(path: Path, artifact_id: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HandoffBundleIndexError(f"{artifact_id}_unreadable") from exc
    parsed = _parse_json_bytes(raw, artifact_id)
    if not isinstance(parsed, Mapping):
        raise HandoffBundleIndexError(f"{artifact_id}_not_mapping")
    return raw, parsed


def _parse_json_bytes(raw: bytes, artifact_id: str) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite_json_constant")

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise HandoffBundleIndexError(f"{artifact_id}_decode_error") from exc
    except json.JSONDecodeError as exc:
        raise HandoffBundleIndexError(f"{artifact_id}_json_error") from exc
    except ValueError as exc:
        raise HandoffBundleIndexError(f"{artifact_id}_json_error") from exc


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
    for field in ("verification_version", "summary_version", "bundle_index_version"):
        value = artifact.get(field)
        if isinstance(value, str):
            return value
    return "invalid_schema"


def _authority_boundary() -> dict[str, bool]:
    return {
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


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "bundle_index_version": BUNDLE_INDEX_VERSION,
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
            f"failed:{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


def _assert_mapping(artifact_id: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise HandoffBundleIndexError(f"{artifact_id}_not_mapping")


def _assert_bytes_match_artifact(
    artifact_id: str,
    artifact: Mapping[str, Any],
    raw: bytes,
) -> None:
    parsed = _parse_json_bytes(raw, artifact_id)
    if _deterministic_artifact(parsed) != _deterministic_artifact(artifact):
        raise HandoffBundleIndexError(f"{artifact_id}_bytes_mismatch")


def _assert_no_forbidden_input(artifact_id: str, artifact: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(artifact, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise HandoffBundleIndexError(f"{artifact_id}_non_finite_json_value") from exc
    if _contains_path_marker(artifact) or _forbidden_output_markers(serialized):
        raise HandoffBundleIndexError(f"{artifact_id}_not_path_free")


def _recursive_contract_blockers(value: Any) -> list[str]:
    blockers: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = raw_key if isinstance(raw_key, str) else "invalid_key"
            if key in {"payload", "payloads", "raw_payload", "raw_payloads"}:
                blockers.append(f"input_payload_key:{key}")
            if key in {"path", "paths", "local_path", "local_paths"}:
                blockers.append(f"input_path_key:{key}")
            if key in AUTHORITY_FALSE_FIELDS and child is not False:
                blockers.append(f"input_{key}_not_false")
            blockers.extend(_recursive_contract_blockers(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            blockers.extend(_recursive_contract_blockers(child))
    return sorted(set(blockers))


def _expect_empty_items_or_collect(value: Any, reason: str, blockers: list[str]) -> None:
    if value == []:
        return
    blockers.append(reason)


def _required_safe_ref(value: Any, reason: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_REF_RE.fullmatch(value)
        or _forbidden_output_markers(value)
    ):
        raise HandoffBundleIndexError(reason)
    return value


def _safe_ref_or_invalid(value: Any) -> str:
    return (
        value
        if isinstance(value, str)
        and _SAFE_REF_RE.fullmatch(value)
        and not _forbidden_output_markers(value)
        else "invalid_ref"
    )


def _safe_token(value: Any, fallback: str = "invalid_token") -> str:
    return (
        value
        if isinstance(value, str)
        and _SAFE_REF_RE.fullmatch(value)
        and not _forbidden_output_markers(value)
        else fallback
    )


def _safe_reason(value: Any) -> str:
    return _safe_token(value, "unsafe_reason_redacted")


def _safe_token_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_safe_token(item) for item in value if isinstance(item, str)]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value >= 0 else 0


def _contains_path_marker(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.replace("\\", "/").lower()
        return (
            _WINDOWS_DRIVE_PATH_RE.search(value) is not None
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


def _assert_no_forbidden_output(serialized: str) -> None:
    if _forbidden_output_markers(serialized):
        raise HandoffBundleIndexError("forbidden_output_marker")


def _deterministic_artifact(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HandoffBundleIndexError("artifact_non_finite_json_value") from exc


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_utc(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise HandoffBundleIndexError("timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise HandoffBundleIndexError("timestamp_invalid") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise HandoffBundleIndexError("timestamp_invalid")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


if __name__ == "__main__":
    raise SystemExit(main())
