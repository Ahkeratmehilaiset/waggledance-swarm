#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a local index entry for the hex cross-consistency bridge template.

This binds the path-free hex-upgrade cross-consistency digest to the
template-only bridge-event report that presents it for reviewer handoff. The
index records only digests, sizes, schema/version refs, and authority-boundary
booleans. It never appends to the bridge, transports artifacts, includes
payloads, records local paths, grants approval, or grants runtime subdivision
authority.
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
    EVENT_STATUS as SOURCE_EVENT_STATUS,
    TEMPLATE_VERSION as SOURCE_TEMPLATE_VERSION,
    _DIGEST_VERDICT_FIELDS,
    _TEMPLATE_SAFE_KEYS,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template,
)
from tools.hex_shadow_subdivision_replay import _contains_path_marker  # noqa: E402
from tools.run_hex_upgrade_cross_consistency_digest import (  # noqa: E402
    REPORT_VERSION as DIGEST_REPORT_VERSION,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


INDEX_ENTRY_VERSION = (
    "wd.hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry.v1"
)
PROOF_ID = "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_v1"
DIGEST_ARTIFACT_ID = "hex_upgrade_cross_consistency_digest"
TEMPLATE_ARTIFACT_ID = "hex_upgrade_cross_consistency_digest_bridge_event_template"

SAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"(?:^|[^A-Za-z0-9])(?:[A-Za-z]:[\\/])")
FORBIDDEN_OUTPUT_MARKERS = (
    "http://",
    "https://",
    "C:/",
    "C:\\",
    "\\\\",
    "/home/",
    "/Users/",
    "/tmp/",
    "PRIVATE_",
    "Authorization",
    "Bearer ",
    "secret",
    "password",
)
AUTHORITY_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "merge_decision_made",
    "promotion_granted",
    "claim_safe",
    "literal_future_claim_safe",
    "runtime_authority_granted",
    "runtime_subdivision_authority_granted",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "bridge_event_written",
    "gate_skip_allowed",
    "fast_track_priority",
    "digest_payloads_included",
    "artifact_payloads_included",
    "local_paths_recorded",
)


class IndexEntryError(ValueError):
    """Raised when index-entry inputs violate the local contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digest-json", required=True, type=Path)
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
        help="Optional UTC timestamp override such as 2026-06-20T08:20:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        digest_bytes, digest = _load_json_artifact(
            args.digest_json,
            DIGEST_ARTIFACT_ID,
        )
        template_bytes, template_report = _load_json_artifact(
            args.bridge_event_template_json,
            TEMPLATE_ARTIFACT_ID,
        )
        report = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            digest=digest,
            bridge_event_template_report=template_report,
            digest_bytes=digest_bytes,
            bridge_event_template_bytes=template_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except IndexEntryError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report("hex_upgrade_cross_consistency_index_entry_invalid")

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json or report["ok"]:
        print(encoded)
    else:
        print(
            "hex cross-consistency bridge-template index entry FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
    *,
    digest: Mapping[str, Any],
    bridge_event_template_report: Mapping[str, Any],
    digest_bytes: bytes,
    bridge_event_template_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free index entry for the bridge-event template."""

    _assert_mapping(DIGEST_ARTIFACT_ID, digest)
    _assert_mapping(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
    _assert_no_forbidden_input(DIGEST_ARTIFACT_ID, digest)
    _assert_no_forbidden_input(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
    _assert_bytes_match_artifact(DIGEST_ARTIFACT_ID, digest, digest_bytes)
    _assert_bytes_match_artifact(
        TEMPLATE_ARTIFACT_ID,
        bridge_event_template_report,
        bridge_event_template_bytes,
    )
    _assert_digest_contract(digest)

    rebuilt = _rebuilt_template(digest, bridge_event_template_report)
    if _deterministic_artifact(rebuilt) != _deterministic_artifact(
        bridge_event_template_report
    ):
        raise IndexEntryError("bridge_event_template_rebuilt_mismatch")

    _assert_template_contract(bridge_event_template_report, digest)

    digest_sha256 = _sha256_hex(digest_bytes)
    template_sha256 = _sha256_hex(bridge_event_template_bytes)
    digest_ref = sha256_digest(_plain_json_object(digest))
    event = _mapping(bridge_event_template_report.get("bridge_event_template"))
    payload = _mapping(event.get("payload"))
    cross = _mapping(payload.get("cross_consistency"))
    artifacts = (
        _artifact_record(
            artifact_id=DIGEST_ARTIFACT_ID,
            role="measurement_only_hex_cross_consistency_digest",
            artifact=digest,
            raw=digest_bytes,
        ),
        _artifact_record(
            artifact_id=TEMPLATE_ARTIFACT_ID,
            role="template_only_bridge_handoff_context",
            artifact=bridge_event_template_report,
            raw=bridge_event_template_bytes,
        ),
    )
    entry = {
        "proof_id": PROOF_ID,
        "ok": True,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "digest_report_version": DIGEST_REPORT_VERSION,
        "template_version": SOURCE_TEMPLATE_VERSION,
        "artifact_count": len(artifacts),
        "artifacts": list(artifacts),
        "template_index_entry": {
            "artifact_id": TEMPLATE_ARTIFACT_ID,
            "source_artifact_id": DIGEST_ARTIFACT_ID,
            "template_version": SOURCE_TEMPLATE_VERSION,
            "template_only": True,
            "bridge_event_schema_validated": True,
            "source_digest_sha256": digest_sha256,
            "source_digest_ref": digest_ref,
            "template_sha256": template_sha256,
            "source_contract_check": "match",
            "digest_ref_check": "match",
            "rebuilt_template_check": "match",
            "event_type": "handoff",
            "event_status": SOURCE_EVENT_STATUS,
            **{field: cross.get(field) is True for field in _DIGEST_VERDICT_FIELDS},
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "merge_decision_made": False,
            "promotion_granted": False,
            "claim_safe": False,
            "literal_future_claim_safe": False,
            "runtime_authority_granted": False,
            "runtime_subdivision_authority_granted": False,
            "direct_bridge_write_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "runtime_controls_added": False,
            "bridge_event_written": False,
            "gate_skip_allowed": False,
            "fast_track_priority": False,
            "digest_payloads_included": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "cross_consistency": {
            "digest_schema_version": DIGEST_REPORT_VERSION,
            "digest_ref": digest_ref,
            **{field: digest.get(field) is True for field in _DIGEST_VERDICT_FIELDS},
            "raw_digest_payload_included": False,
            "template_cross_consistency_ref_matches": True,
        },
        "consistency": {
            "required_artifacts_present": [DIGEST_ARTIFACT_ID, TEMPLATE_ARTIFACT_ID],
            "all_artifact_digests_recorded": True,
            "bridge_event_schema_validated": True,
            "source_contract_check": "match",
            "digest_ref_check": "match",
            "rebuilt_template_check": "match",
            "template_only": True,
            "digest_payloads_included": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "operator_boundary": _operator_boundary(),
        "reviewer_next_actions": [
            "review_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry",
            "append_bridge_event_separately_only_after_manual_review",
        ],
        "template_only": True,
        "manual_review_required": True,
        **_false_authority_axes(),
        "blockers": [],
        "warnings": _safe_token_list(bridge_event_template_report.get("warnings")),
    }
    _assert_no_forbidden_output(json.dumps(entry, allow_nan=False, sort_keys=True))
    return entry


def _rebuilt_template(
    digest: Mapping[str, Any],
    template_report: Mapping[str, Any],
) -> Mapping[str, Any]:
    event = _mapping(template_report.get("bridge_event_template"))
    if not event:
        raise IndexEntryError("bridge_event_template_missing")
    try:
        validate_event(event)
    except Exception as exc:
        raise IndexEntryError("bridge_event_template_schema_invalid") from exc
    return build_hex_upgrade_cross_consistency_digest_bridge_event_template(
        digest=digest,
        agent_id=_required_string(event.get("agent"), "bridge_event_template_agent_invalid"),
        task_id=_required_string(
            event.get("task_id"),
            "bridge_event_template_task_id_invalid",
        ),
        to=_required_string(event.get("to"), "bridge_event_template_to_invalid"),
        severity=_required_severity(event.get("severity")),
        role=_optional_string(event.get("role")),
        run_id=_optional_string(event.get("run_id")),
        session_id=_optional_string(event.get("session_id")),
        now_utc=_parse_utc(
            _required_string(event.get("ts_utc"), "bridge_event_template_ts_invalid")
        ),
    )


def _assert_digest_contract(digest: Mapping[str, Any]) -> None:
    if digest.get("report_version") != DIGEST_REPORT_VERSION:
        raise IndexEntryError("digest_report_version_mismatch")
    if digest.get("claim_safe") is not False:
        raise IndexEntryError("digest_claim_safe_not_false")
    for field in _DIGEST_VERDICT_FIELDS:
        if not isinstance(digest.get(field), bool):
            raise IndexEntryError(f"digest_{field}_not_bool")
    if digest.get("path_free_verified") is not True:
        raise IndexEntryError("digest_path_free_not_true")


def _assert_template_contract(
    template_report: Mapping[str, Any],
    digest: Mapping[str, Any],
) -> None:
    if template_report.get("ok") is not True:
        raise IndexEntryError("bridge_event_template_not_ok")
    if template_report.get("template_version") != SOURCE_TEMPLATE_VERSION:
        raise IndexEntryError("bridge_event_template_version_mismatch")
    if template_report.get("template_only") is not True:
        raise IndexEntryError("bridge_event_template_template_only_not_true")
    if template_report.get("path_free_verified") is not True:
        raise IndexEntryError("bridge_event_template_path_free_not_true")
    _expect_empty_items(template_report.get("blockers"), "bridge_event_template_blockers_present")
    _expect_authority_false(template_report, "bridge_event_template")

    event = _mapping(template_report.get("bridge_event_template"))
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
    _expect_authority_false(
        _mapping(payload.get("authority_boundary")),
        "bridge_event_template_boundary",
    )

    cross = _mapping(payload.get("cross_consistency"))
    if not cross:
        raise IndexEntryError("bridge_event_template_cross_consistency_missing")
    if set(cross) - _TEMPLATE_SAFE_KEYS:
        raise IndexEntryError("bridge_event_template_cross_consistency_key_mismatch")
    if cross.get("digest_schema_version") != DIGEST_REPORT_VERSION:
        raise IndexEntryError("bridge_event_template_digest_schema_mismatch")
    if cross.get("digest_ref") != sha256_digest(_plain_json_object(digest)):
        raise IndexEntryError("bridge_event_template_digest_ref_mismatch")
    for field in _DIGEST_VERDICT_FIELDS:
        if cross.get(field) is not digest.get(field):
            raise IndexEntryError(f"bridge_event_template_{field}_mismatch")
    if cross.get("raw_digest_payload_included") is not False:
        raise IndexEntryError("bridge_event_template_raw_digest_payload_included")


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
    except json.JSONDecodeError as exc:
        raise IndexEntryError(f"{label}_json_error") from exc
    except ValueError as exc:
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


def _artifact_record(
    *,
    artifact_id: str,
    role: str,
    artifact: Mapping[str, Any],
    raw: bytes,
) -> dict[str, Any]:
    schema_version = (
        artifact.get("schema_version")
        or artifact.get("template_version")
        or artifact.get("report_version")
    )
    return {
        "artifact_id": artifact_id,
        "role": role,
        "sha256": _sha256_hex(raw),
        "size_bytes": len(raw),
        "json_schema_version": schema_version,
        "payload_included": False,
        "local_path_recorded": False,
    }


def _operator_boundary() -> dict[str, bool]:
    return {
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "merge_decision_made": False,
        "promotion_granted": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "fast_track_priority": False,
        "digest_payloads_included": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
    }


def _false_authority_axes() -> dict[str, bool]:
    return {
        "approval_granted": False,
        "release_decision_made": False,
        "merge_decision_made": False,
        "promotion_granted": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "fast_track_priority": False,
        "digest_payloads_included": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
    }


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
    try:
        text = json.dumps(artifact, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise IndexEntryError(f"{label}_non_finite_json_value") from exc
    if _contains_path_marker(artifact) or _contains_path_marker_local(artifact):
        raise IndexEntryError(f"{label}_not_path_free")
    if _forbidden_output_markers(text):
        raise IndexEntryError(f"{label}_forbidden_marker")


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


def _deterministic_artifact(value: Any) -> Any:
    plain = json.loads(json.dumps(value, allow_nan=False, sort_keys=True))
    if isinstance(plain, dict):
        normalized = dict(plain)
        normalized.pop("created_at_utc", None)
        return normalized
    return plain


def _safe_token(value: Any, fallback: str = "invalid_token") -> str:
    if isinstance(value, str) and SAFE_TOKEN_PATTERN.fullmatch(value):
        return value
    return fallback


def _safe_token_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_safe_token(item) for item in value if isinstance(item, str)]


def _required_string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise IndexEntryError(code)
    return value


def _optional_string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _required_severity(value: Any) -> str:
    if value not in {"", "low", "medium", "high"}:
        raise IndexEntryError("bridge_event_template_severity_invalid")
    return str(value)


def _contains_path_marker_local(value: Any) -> bool:
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
            _contains_path_marker_local(key) or _contains_path_marker_local(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_path_marker_local(item) for item in value)
    return False


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
        raise IndexEntryError(
            "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_contains_forbidden_markers:"
            + "_".join(_safe_reason(marker) for marker in found)
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
        **_false_authority_axes(),
        "blockers": [
            "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_failed:"
            f"{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise IndexEntryError("now_utc_unsafe")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise IndexEntryError("now_utc_unsafe") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise IndexEntryError("now_utc_unsafe")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
