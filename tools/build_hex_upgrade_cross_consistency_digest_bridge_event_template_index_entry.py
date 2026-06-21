#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a local index entry for the hex-upgrade cross-consistency template.

Advances the WD Image #1 *hexagonal upgrades* pillar. The source
``build_hex_upgrade_cross_consistency_digest_bridge_event_template`` tool emits
schema-valid bridge-event TEMPLATE JSON for the measurement-only hex-upgrade
cross-consistency digest. This tool records that template in a path-free LOCAL
INDEX ENTRY bound to stable digests and selected safe-scalar verdicts.

It is read-only and grants no authority. It never appends bridge events,
transports artifacts, writes files, calls providers, mutates runtime, activates
subdivision authority, or upgrades any claim. Content-safe by construction: it
emits only derived booleans, strict ints, fixed version/schema strings, and
sha256 digest references. It never emits the raw event message, timestamp, or
payload. ``claim_safe`` is hardcoded False.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template import (  # noqa: E402
    EVENT_STATUS as SOURCE_EVENT_STATUS,
    FORBIDDEN_OUTPUT_MARKERS,
    TEMPLATE_VERSION as SOURCE_TEMPLATE_VERSION,
    _DIGEST_VERDICT_FIELDS,
)
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
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "runtime_authority_granted",
    "runtime_subdivision_authority_granted",
    "bridge_event_written",
    "gate_skip_allowed",
    "fast_track_priority",
    "digest_payloads_included",
    "local_paths_recorded",
    "claim_safe",
)
_TEMPLATE_REPORT_FALSE_FIELDS = (
    "direct_bridge_write_performed",
    "approval_granted",
    "release_decision_made",
    "runtime_authority_granted",
    "runtime_subdivision_authority_granted",
    "bridge_event_written",
    "fast_track_priority",
    "gate_skip_allowed",
    "claim_safe",
    "digest_payloads_included",
    "local_paths_recorded",
)
_BOUNDARY_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "merge_decision_made",
    "promotion_granted",
    "claim_safe",
    "literal_future_claim_safe",
    "runtime_authority_granted",
    "runtime_subdivision_authority_granted",
    "bridge_event_written",
    "gate_skip_allowed",
    "fast_track_priority",
)
_PAYLOAD_FALSE_FIELDS = (
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "digest_payloads_included",
    "local_paths_recorded",
)


class IndexEntryError(ValueError):
    """Raised when index-entry inputs violate the local contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
    *,
    bridge_event_template_report: Mapping[str, Any],
    bridge_event_template_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free local index entry for the hex bridge-event template."""

    try:
        _assert_mapping(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
        _assert_no_forbidden_input(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
        cross = _assert_template_contract(bridge_event_template_report)

        template_sha256 = hashlib.sha256(bridge_event_template_bytes).hexdigest()
        template_digest = sha256_digest(_plain_json_object(bridge_event_template_report))
        event = _mapping(bridge_event_template_report.get("bridge_event_template"))
        payload = _mapping(event.get("payload"))

        template_index_entry: dict[str, Any] = {
            "artifact_id": TEMPLATE_ARTIFACT_ID,
            "template_version": SOURCE_TEMPLATE_VERSION,
            "template_only": True,
            "bridge_event_schema_validated": True,
            "template_report_sha256": template_sha256,
            "template_report_digest": template_digest,
            "event_digest": sha256_digest(_plain_json_object(event)),
            "payload_digest": sha256_digest(_plain_json_object(payload)),
            "cross_consistency_digest_ref": _safe_sha256_ref(
                cross.get("digest_ref")
            ),
            "digest_schema_version": cross.get("digest_schema_version"),
            "event_type": event.get("type"),
            "event_status": event.get("status"),
            "manual_review_required": True,
        }
        for field in _DIGEST_VERDICT_FIELDS:
            template_index_entry[field] = cross.get(field) is True
        for field in AUTHORITY_FALSE_FIELDS:
            template_index_entry[field] = False

        entry: dict[str, Any] = {
            "proof_id": PROOF_ID,
            "ok": True,
            "index_entry_version": INDEX_ENTRY_VERSION,
            "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
            "template_version": SOURCE_TEMPLATE_VERSION,
            "artifact_count": 1,
            "artifacts": [
                {
                    "artifact_id": TEMPLATE_ARTIFACT_ID,
                    "role": (
                        "verified_no_authority_hex_cross_consistency_template"
                    ),
                    "sha256": template_sha256,
                    "byte_count": len(bridge_event_template_bytes),
                    "digest": template_digest,
                    "template_only": True,
                    "manual_review_required": True,
                    "raw_artifact_payload_included": False,
                    "local_path_recorded": False,
                },
            ],
            "template_index_entry": template_index_entry,
            "consistency": {
                "required_artifacts_present": [TEMPLATE_ARTIFACT_ID],
                "all_artifact_digests_recorded": True,
                "bridge_event_schema_validated": True,
                "template_report_validator": "pass",
                "cross_consistency_digest_ref_recorded": True,
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

        errors = (
            validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
                entry
            )
        )
        if errors:
            return _failure_report(errors[0])
        _assert_no_forbidden_output(json.dumps(entry, allow_nan=False, sort_keys=True))
        return entry
    except IndexEntryError as exc:
        return _failure_report(exc.code)


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
    if entry_dict.get("template_only") is not True:
        errors.append("index_entry_template_only_not_true")
    if entry_dict.get("manual_review_required") is not True:
        errors.append("index_entry_manual_review_required_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if entry_dict.get(field) is not False:
            errors.append(f"index_entry_{field}_not_exact_false")

    artifacts = entry_dict.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        errors.append("index_entry_artifacts_invalid")
    else:
        artifact = _plain_json_object_or_none(artifacts[0])
        if artifact is None:
            errors.append("index_entry_artifact_not_object")
        else:
            if artifact.get("artifact_id") != TEMPLATE_ARTIFACT_ID:
                errors.append("index_entry_artifact_id_mismatch")
            if artifact.get("raw_artifact_payload_included") is not False:
                errors.append("index_entry_artifact_payload_included_not_false")
            if artifact.get("local_path_recorded") is not False:
                errors.append("index_entry_artifact_local_path_recorded_not_false")

    tie = _plain_json_object_or_none(entry_dict.get("template_index_entry"))
    if tie is None:
        errors.append("template_index_entry_not_object")
    else:
        if tie.get("artifact_id") != TEMPLATE_ARTIFACT_ID:
            errors.append("template_index_entry_artifact_id_mismatch")
        if tie.get("template_version") != SOURCE_TEMPLATE_VERSION:
            errors.append("template_index_entry_template_version_mismatch")
        if tie.get("event_status") != SOURCE_EVENT_STATUS:
            errors.append("template_index_entry_event_status_mismatch")
        if tie.get("bridge_event_schema_validated") is not True:
            errors.append("template_index_entry_schema_validated_not_true")
        if not _is_sha256_ref(tie.get("cross_consistency_digest_ref")):
            errors.append("template_index_entry_digest_ref_invalid")
        for field in _DIGEST_VERDICT_FIELDS:
            if not isinstance(tie.get(field), bool):
                errors.append(f"template_index_entry_{field}_not_bool")
        for field in AUTHORITY_FALSE_FIELDS:
            if tie.get(field) is not False:
                errors.append(f"template_index_entry_{field}_not_exact_false")

    consistency = _plain_json_object_or_none(entry_dict.get("consistency"))
    if consistency is None:
        errors.append("consistency_not_object")
    elif consistency.get("template_report_validator") != "pass":
        errors.append("consistency_template_report_validator_not_pass")
    return errors


def _assert_template_contract(template_report: Mapping[str, Any]) -> Mapping[str, Any]:
    if template_report.get("ok") is not True:
        raise IndexEntryError("bridge_event_template_not_ok")
    if template_report.get("template_version") != SOURCE_TEMPLATE_VERSION:
        raise IndexEntryError("bridge_event_template_version_mismatch")
    if template_report.get("template_only") is not True:
        raise IndexEntryError("bridge_event_template_template_only_not_true")
    if template_report.get("path_free_verified") is not True:
        raise IndexEntryError("bridge_event_template_path_free_not_true")
    _expect_empty_items(
        template_report.get("blockers"), "bridge_event_template_blockers_present"
    )
    for field in _TEMPLATE_REPORT_FALSE_FIELDS:
        if template_report.get(field) is not False:
            raise IndexEntryError(f"bridge_event_template_{field}_not_false")

    event = _mapping(template_report.get("bridge_event_template"))
    try:
        validate_event(event)
    except Exception as exc:  # noqa: BLE001
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
    for field in _PAYLOAD_FALSE_FIELDS:
        if payload.get(field) is not False:
            raise IndexEntryError(f"bridge_event_template_payload_{field}_not_false")

    boundary = _mapping(payload.get("authority_boundary"))
    if boundary.get("manual_review_required") is not True:
        raise IndexEntryError("bridge_event_template_boundary_manual_review_not_true")
    for field in _BOUNDARY_FALSE_FIELDS:
        if boundary.get(field) is not False:
            raise IndexEntryError(f"bridge_event_template_boundary_{field}_not_false")

    cross = _mapping(payload.get("cross_consistency"))
    if cross.get("digest_schema_version") is None:
        raise IndexEntryError("bridge_event_template_cross_consistency_missing")
    if not _is_sha256_ref(cross.get("digest_ref")):
        raise IndexEntryError("bridge_event_template_digest_ref_invalid")
    for field in _DIGEST_VERDICT_FIELDS:
        if not isinstance(cross.get(field), bool):
            raise IndexEntryError(f"bridge_event_template_cross_{field}_not_bool")
    return cross


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bridge-event-template-json",
        "--template-json",
        dest="bridge_event_template_json",
        required=True,
        type=Path,
    )
    parser.add_argument("--now", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        template_bytes, template_report = _load_json_artifact(
            args.bridge_event_template_json, TEMPLATE_ARTIFACT_ID
        )
        report = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            bridge_event_template_report=template_report,
            bridge_event_template_bytes=template_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except IndexEntryError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report("index_entry_invalid")

    indent = 2 if args.pretty else None
    encoded = json.dumps(report, indent=indent, sort_keys=True, allow_nan=False)
    if args.json or report["ok"]:
        print(encoded)
    else:
        print(
            "hex-upgrade cross-consistency digest bridge-template index entry "
            "FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def _failure_report(reason: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "proof_id": PROOF_ID,
        "ok": False,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "blockers": [
            "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_failed:"
            + _safe_reason(reason)
        ],
        "warnings": [],
    }
    for field in AUTHORITY_FALSE_FIELDS:
        report[field] = False
    return report


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


def _assert_no_forbidden_input(label: str, value: Any) -> None:
    if _contains_forbidden_marker(value):
        raise IndexEntryError(f"{label}_contains_forbidden_marker")


def _assert_no_forbidden_output(encoded: str) -> None:
    if _has_forbidden_marker(encoded):
        raise IndexEntryError("index_entry_contains_forbidden_output_marker")


def _contains_forbidden_marker(value: Any) -> bool:
    if isinstance(value, str):
        return _has_forbidden_marker(value)
    if isinstance(value, Mapping):
        return any(_contains_forbidden_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_marker(item) for item in value)
    return False


def _has_forbidden_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in FORBIDDEN_OUTPUT_MARKERS)


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IndexEntryError("expected_mapping_missing")
    return value


def _plain_json_object(value: Any) -> dict[str, Any]:
    plain = _plain_json_object_or_none(value)
    if plain is None:
        raise IndexEntryError("json_object_required")
    return plain


def _plain_json_object_or_none(value: Any) -> dict[str, Any] | None:
    try:
        plain = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        return None
    return plain if isinstance(plain, dict) else None


def _expect_empty_items(value: Any, code: str) -> None:
    if value not in ([], (), None):
        raise IndexEntryError(code)


def _is_sha256_ref(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == len("sha256:") + 64
        and value.startswith("sha256:")
        and all(ch in "0123456789abcdef" for ch in value[len("sha256:") :])
    )


def _safe_sha256_ref(value: Any) -> str:
    if _is_sha256_ref(value):
        return str(value)
    return "sha256:" + ("0" * 64)


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [
        _safe_reason(item)
        for item in value
        if isinstance(item, str) and not _contains_forbidden_marker(item)
    ]


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("expected UTC timestamp ending with Z")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("expected UTC timestamp")
    return parsed


def _safe_reason(reason: str) -> str:
    clean = "".join(
        ch if ch.isalnum() or ch in "._:-" else "_" for ch in str(reason)
    )
    return clean[:160] or "invalid"


if __name__ == "__main__":
    raise SystemExit(main())
