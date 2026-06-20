#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a local hex-upgrade cross-consistency bridge-template index entry.

The index entry binds the template-only hex-upgrade cross-consistency digest
bridge-event report to stable digests and selected safety metadata. It records
no payloads or local paths, never appends bridge events, transports artifacts,
upgrades claims, or grants runtime subdivision authority.
"""
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

from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template import (  # noqa: E402
    DIGEST_REPORT_VERSION,
    EVENT_STATUS as SOURCE_EVENT_STATUS,
    FORBIDDEN_OUTPUT_MARKERS,
    PROOF_ID as SOURCE_TEMPLATE_PROOF_ID,
    TEMPLATE_VERSION as SOURCE_TEMPLATE_VERSION,
    _DIGEST_VERDICT_FIELDS,
    _TEMPLATE_SAFE_KEYS,
)
from tools.hex_shadow_subdivision_replay import _contains_path_marker  # noqa: E402
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


INDEX_ENTRY_VERSION = (
    "wd.hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry.v1"
)
PROOF_ID = "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_v1"
TEMPLATE_ARTIFACT_ID = "hex_upgrade_cross_consistency_digest_bridge_event_template"
AUTHORITY_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "merge_decision_made",
    "promotion_granted",
    "claim_safe",
    "literal_future_claim_safe",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "runtime_authority_granted",
    "runtime_subdivision_authority_granted",
    "scheduler_enqueue_allowed",
    "bridge_event_written",
    "gate_skip_allowed",
    "fast_track_priority",
    "digest_payloads_included",
    "artifact_payloads_included",
    "local_paths_recorded",
)
SHA256_REF_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"(?:^|[^A-Za-z0-9])(?:[A-Za-z]:[\\/])")


class IndexEntryError(ValueError):
    """Raised when index-entry inputs violate the local contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
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
        help="Optional UTC timestamp override such as 2026-06-20T08:30:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        template_bytes, template_report = _load_json_artifact(
            args.bridge_event_template_json,
            TEMPLATE_ARTIFACT_ID,
        )
        report = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            bridge_event_template_report=template_report,
            bridge_event_template_bytes=template_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except IndexEntryError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report("hex_upgrade_cross_consistency_index_entry_invalid")

    indent = 2 if args.pretty else None
    encoded = json.dumps(report, indent=indent, sort_keys=True, allow_nan=False)
    if args.json or report["ok"]:
        print(encoded)
    else:
        print(
            "hex-upgrade cross-consistency bridge-template index entry FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
    *,
    bridge_event_template_report: Mapping[str, Any],
    bridge_event_template_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free local index entry for the bridge-event template."""

    try:
        _assert_mapping(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
        _assert_bytes_match_artifact(
            TEMPLATE_ARTIFACT_ID,
            bridge_event_template_report,
            bridge_event_template_bytes,
        )
        _assert_no_forbidden_input(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
        _assert_template_contract(bridge_event_template_report)
    except IndexEntryError as exc:
        return _failure_report(exc.code)

    template_sha256 = _sha256_hex(bridge_event_template_bytes)
    template_digest = sha256_digest(_plain_json_object(bridge_event_template_report))
    event = _mapping(bridge_event_template_report.get("bridge_event_template"))
    payload = _mapping(event.get("payload"))
    cross = _mapping(payload.get("cross_consistency"))

    template_index_entry = {
        "artifact_id": TEMPLATE_ARTIFACT_ID,
        "template_proof_id": SOURCE_TEMPLATE_PROOF_ID,
        "template_version": SOURCE_TEMPLATE_VERSION,
        "template_only": True,
        "bridge_event_schema_validated": True,
        "template_report_sha256": template_sha256,
        "template_report_digest": template_digest,
        "event_digest": sha256_digest(_plain_json_object(event)),
        "payload_digest": sha256_digest(_plain_json_object(payload)),
        "source_digest_schema_version": cross.get("digest_schema_version"),
        "source_digest_ref": cross.get("digest_ref"),
        "event_type": event.get("type"),
        "event_status": event.get("status"),
        "source_contract_check": "match",
        "template_contract_check": "match",
        "authority_boundary_check": "match",
        "cross_consistency_safe_keys_check": "match",
        "manual_review_required": True,
        "raw_digest_payload_included": False,
    }
    for field in _DIGEST_VERDICT_FIELDS:
        template_index_entry[field] = cross.get(field) is True
    for field in AUTHORITY_FALSE_FIELDS:
        template_index_entry[field] = False

    cross_summary = {
        "digest_schema_version": cross.get("digest_schema_version"),
        "digest_ref": cross.get("digest_ref"),
        "raw_digest_payload_included": False,
        "claim_safe": False,
    }
    for field in _DIGEST_VERDICT_FIELDS:
        cross_summary[field] = cross.get(field) is True

    entry = {
        "proof_id": PROOF_ID,
        "ok": True,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "template_version": SOURCE_TEMPLATE_VERSION,
        "artifact_count": 1,
        "artifacts": [
            {
                "artifact_id": TEMPLATE_ARTIFACT_ID,
                "role": "verified_no_authority_hex_cross_consistency_template",
                "sha256": template_sha256,
                "size_bytes": len(bridge_event_template_bytes),
                "digest": template_digest,
                "json_schema_version": SOURCE_TEMPLATE_VERSION,
                "template_only": True,
                "manual_review_required": True,
                "payload_included": False,
                "local_path_recorded": False,
            },
        ],
        "template_index_entry": template_index_entry,
        "cross_consistency_digest": cross_summary,
        "consistency": {
            "required_artifacts_present": [TEMPLATE_ARTIFACT_ID],
            "all_artifact_digests_recorded": True,
            "bridge_event_schema_validated": True,
            "source_contract_check": "match",
            "template_contract_check": "match",
            "authority_boundary_check": "match",
            "cross_consistency_safe_keys_check": "match",
            "template_only": True,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "reviewer_next_actions": [
            "review_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry",
            "append_bridge_event_separately_only_after_manual_review",
        ],
        "template_only": True,
        "manual_review_required": True,
        "blockers": [],
        "warnings": _safe_string_list(bridge_event_template_report.get("warnings")),
    }
    for field in AUTHORITY_FALSE_FIELDS:
        entry[field] = False
    entry["path_free_verified"] = not _contains_path_marker(entry)

    errors = validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        entry
    )
    if errors:
        return _failure_report(errors[0])
    _assert_no_forbidden_output(json.dumps(entry, allow_nan=False, sort_keys=True))
    return entry


def validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
    entry: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    entry_dict = _plain_json_object_or_none(entry)
    if entry_dict is None:
        return ["index_entry_not_json_object"]
    if entry_dict.get("ok") is not True:
        errors.append("index_entry_ok_not_true")
    if entry_dict.get("index_entry_version") != INDEX_ENTRY_VERSION:
        errors.append("index_entry_version_mismatch")
    if entry_dict.get("template_version") != SOURCE_TEMPLATE_VERSION:
        errors.append("index_entry_template_version_mismatch")
    if entry_dict.get("template_only") is not True:
        errors.append("index_entry_template_only_not_true")
    if entry_dict.get("manual_review_required") is not True:
        errors.append("index_entry_manual_review_required_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if entry_dict.get(field) is not False:
            errors.append(f"index_entry_{field}_not_exact_false")
    if entry_dict.get("path_free_verified") is not True:
        errors.append("index_entry_path_free_verified_not_true")

    artifacts = entry_dict.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        errors.append("index_entry_artifacts_invalid")
    elif not isinstance(artifacts[0], Mapping):
        errors.append("index_entry_artifact_not_object")
    else:
        artifact = artifacts[0]
        if artifact.get("artifact_id") != TEMPLATE_ARTIFACT_ID:
            errors.append("index_entry_artifact_id_mismatch")
        if artifact.get("template_only") is not True:
            errors.append("index_entry_artifact_template_only_not_true")
        if artifact.get("payload_included") is not False:
            errors.append("index_entry_artifact_payload_included_not_false")
        if artifact.get("local_path_recorded") is not False:
            errors.append("index_entry_artifact_local_path_recorded_not_false")
        if not _hex_sha256(artifact.get("sha256")):
            errors.append("index_entry_artifact_sha256_invalid")
        if not _sha256_ref(artifact.get("digest")):
            errors.append("index_entry_artifact_digest_invalid")

    template_index_entry = _plain_json_object_or_none(
        entry_dict.get("template_index_entry")
    )
    if template_index_entry is None:
        errors.append("template_index_entry_not_object")
    else:
        if template_index_entry.get("artifact_id") != TEMPLATE_ARTIFACT_ID:
            errors.append("template_index_entry_artifact_id_mismatch")
        if template_index_entry.get("template_proof_id") != SOURCE_TEMPLATE_PROOF_ID:
            errors.append("template_index_entry_template_proof_id_mismatch")
        if template_index_entry.get("template_version") != SOURCE_TEMPLATE_VERSION:
            errors.append("template_index_entry_template_version_mismatch")
        if template_index_entry.get("event_status") != SOURCE_EVENT_STATUS:
            errors.append("template_index_entry_event_status_mismatch")
        if template_index_entry.get("bridge_event_schema_validated") is not True:
            errors.append("template_index_entry_schema_validated_not_true")
        if template_index_entry.get("source_digest_schema_version") != (
            DIGEST_REPORT_VERSION
        ):
            errors.append("template_index_entry_digest_schema_version_mismatch")
        if not _sha256_ref(template_index_entry.get("source_digest_ref")):
            errors.append("template_index_entry_source_digest_ref_invalid")
        for field in _DIGEST_VERDICT_FIELDS:
            if not isinstance(template_index_entry.get(field), bool):
                errors.append(f"template_index_entry_{field}_not_bool")
        for check in (
            "source_contract_check",
            "template_contract_check",
            "authority_boundary_check",
            "cross_consistency_safe_keys_check",
        ):
            if template_index_entry.get(check) != "match":
                errors.append(f"template_index_entry_{check}_not_match")
        for field in AUTHORITY_FALSE_FIELDS:
            if template_index_entry.get(field) is not False:
                errors.append(f"template_index_entry_{field}_not_exact_false")
        if template_index_entry.get("raw_digest_payload_included") is not False:
            errors.append("template_index_entry_raw_digest_payload_included_not_false")

    cross_summary = _plain_json_object_or_none(entry_dict.get("cross_consistency_digest"))
    if cross_summary is None:
        errors.append("cross_consistency_digest_not_object")
    else:
        if cross_summary.get("digest_schema_version") != DIGEST_REPORT_VERSION:
            errors.append("cross_consistency_digest_schema_version_mismatch")
        if not _sha256_ref(cross_summary.get("digest_ref")):
            errors.append("cross_consistency_digest_ref_invalid")
        if cross_summary.get("claim_safe") is not False:
            errors.append("cross_consistency_digest_claim_safe_not_false")
        if cross_summary.get("raw_digest_payload_included") is not False:
            errors.append("cross_consistency_digest_raw_payload_included_not_false")
        for field in _DIGEST_VERDICT_FIELDS:
            if not isinstance(cross_summary.get(field), bool):
                errors.append(f"cross_consistency_digest_{field}_not_bool")

    consistency = _plain_json_object_or_none(entry_dict.get("consistency"))
    if consistency is None:
        errors.append("index_entry_consistency_not_object")
    else:
        for check in (
            "source_contract_check",
            "template_contract_check",
            "authority_boundary_check",
            "cross_consistency_safe_keys_check",
        ):
            if consistency.get(check) != "match":
                errors.append(f"index_entry_consistency_{check}_not_match")
        if consistency.get("artifact_payloads_included") is not False:
            errors.append("index_entry_consistency_payloads_included_not_false")
        if consistency.get("local_paths_recorded") is not False:
            errors.append("index_entry_consistency_paths_recorded_not_false")
    return errors


def _assert_template_contract(template_report: Mapping[str, Any]) -> None:
    if template_report.get("ok") is not True:
        raise IndexEntryError("bridge_event_template_not_ok")
    if template_report.get("proof_id") != SOURCE_TEMPLATE_PROOF_ID:
        raise IndexEntryError("bridge_event_template_proof_id_mismatch")
    if template_report.get("template_version") != SOURCE_TEMPLATE_VERSION:
        raise IndexEntryError("bridge_event_template_version_mismatch")
    if template_report.get("template_only") is not True:
        raise IndexEntryError("bridge_event_template_template_only_not_true")
    if template_report.get("manual_review_required") is not True:
        raise IndexEntryError("bridge_event_template_manual_review_required_not_true")
    if template_report.get("path_free_verified") is not True:
        raise IndexEntryError("bridge_event_template_path_free_not_true")
    _expect_empty_items(template_report.get("blockers"), "bridge_event_template_blockers_present")
    _expect_authority_false(template_report, "bridge_event_template")

    event = _mapping(template_report.get("bridge_event_template"))
    if not event:
        raise IndexEntryError("bridge_event_template_event_missing")
    try:
        validate_event(event)
    except Exception as exc:  # pragma: no cover - exact validator type may vary.
        raise IndexEntryError("bridge_event_template_schema_invalid") from exc
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
    if payload.get("template_only") is not True:
        raise IndexEntryError("bridge_event_template_payload_template_only_not_true")
    if payload.get("digest_payloads_included") is not False:
        raise IndexEntryError("bridge_event_template_payload_digest_payloads_included")
    if payload.get("local_paths_recorded") is not False:
        raise IndexEntryError("bridge_event_template_payload_local_paths_recorded")
    _expect_authority_false(payload, "bridge_event_template_payload")

    boundary = _mapping(payload.get("authority_boundary"))
    if boundary.get("manual_review_required") is not True:
        raise IndexEntryError("bridge_event_template_boundary_manual_review_not_true")
    _expect_authority_false(boundary, "bridge_event_template_boundary")

    cross = _mapping(payload.get("cross_consistency"))
    if set(cross) != set(_TEMPLATE_SAFE_KEYS):
        raise IndexEntryError("bridge_event_template_cross_consistency_keys_mismatch")
    if cross.get("digest_schema_version") != DIGEST_REPORT_VERSION:
        raise IndexEntryError("bridge_event_template_digest_schema_version_mismatch")
    if not _sha256_ref(cross.get("digest_ref")):
        raise IndexEntryError("bridge_event_template_digest_ref_invalid")
    if cross.get("raw_digest_payload_included") is not False:
        raise IndexEntryError("bridge_event_template_raw_digest_payload_included")
    for field in _DIGEST_VERDICT_FIELDS:
        if not isinstance(cross.get(field), bool):
            raise IndexEntryError("bridge_event_template_digest_verdict_not_bool")


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
    except (json.JSONDecodeError, ValueError) as exc:
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


def _plain_json_object(value: Any) -> dict[str, Any]:
    try:
        plain = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise IndexEntryError("json_object_invalid") from exc
    if not isinstance(plain, dict):
        raise IndexEntryError("json_object_not_object")
    return plain


def _plain_json_object_or_none(value: Any) -> dict[str, Any] | None:
    try:
        plain = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        return None
    return plain if isinstance(plain, dict) else None


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = _safe_reason(item)
            if text:
                result.append(text)
    return result


def _sha256_ref(value: Any) -> bool:
    return isinstance(value, str) and SHA256_REF_PATTERN.fullmatch(value) is not None


def _hex_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _contains_local_path_marker(value: Any) -> bool:
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
            _contains_local_path_marker(key) or _contains_local_path_marker(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_local_path_marker(item) for item in value)
    return False


def _forbidden_output_markers(text: str) -> list[str]:
    lower_text = text.lower()
    return sorted(
        marker for marker in FORBIDDEN_OUTPUT_MARKERS if marker.lower() in lower_text
    )


def _assert_no_forbidden_output(text: str) -> None:
    found = _forbidden_output_markers(text)
    if found or _contains_local_path_marker(text):
        markers = found or ["path_marker"]
        raise IndexEntryError(
            "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_contains_forbidden_markers:"
            + "_".join(_safe_reason(marker) for marker in markers)
        )


def _safe_reason(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9:._-]+", "_", str(value)).strip("_")
    if not text:
        return "invalid_reason"
    if not text[0].isalnum():
        text = "reason_" + text
    return text[:192]


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "template_version": SOURCE_TEMPLATE_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "merge_decision_made": False,
        "promotion_granted": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "scheduler_enqueue_allowed": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "fast_track_priority": False,
        "digest_payloads_included": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "path_free_verified": True,
        "blockers": [
            "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_failed:"
            f"{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _deterministic_artifact(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _parse_utc(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
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
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
