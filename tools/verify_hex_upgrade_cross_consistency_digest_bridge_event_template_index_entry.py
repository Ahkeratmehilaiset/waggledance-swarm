#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Verify a local hex-upgrade cross-consistency bridge-template index entry.

This is an offline verifier for the WD Image #1 hexagonal-upgrades chain:
measurement digest -> bridge-event template -> local index entry -> verifier.
It recomputes the index entry from the source template bytes, checks stable
digests and byte counts, and preserves the no-payload/no-path/no-authority
boundary. It never appends bridge events, transports artifacts, performs
network I/O, writes files, or grants runtime subdivision authority.
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

from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
    FORBIDDEN_OUTPUT_MARKERS,
    INDEX_ENTRY_VERSION,
    TEMPLATE_ARTIFACT_ID,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry,
    validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


VERIFICATION_VERSION = (
    "wd.hex_upgrade_cross_consistency_digest_bridge_event_template_"
    "index_entry_verification.v1"
)
INDEX_ENTRY_ARTIFACT_ID = (
    "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry"
)


class HexIndexEntryVerificationError(ValueError):
    """Raised when verifier inputs cannot be read safely."""

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
        "--bridge-event-template-json",
        "--template-json",
        dest="bridge_event_template_json",
        required=True,
        type=Path,
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        index_entry_bytes, index_entry = _load_json_artifact(
            args.index_entry_json,
            INDEX_ENTRY_ARTIFACT_ID,
        )
        template_bytes, template_report = _load_json_artifact(
            args.bridge_event_template_json,
            TEMPLATE_ARTIFACT_ID,
        )
        report = verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            index_entry=index_entry,
            bridge_event_template_report=template_report,
            index_entry_bytes=index_entry_bytes,
            bridge_event_template_bytes=template_bytes,
        )
    except HexIndexEntryVerificationError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report(
            "hex_upgrade_cross_consistency_digest_bridge_event_template_"
            "index_entry_verification_invalid"
        )

    encoded = json.dumps(
        report,
        indent=2 if args.pretty else None,
        sort_keys=True,
        allow_nan=False,
    )
    if args.json or report["ok"]:
        print(encoded)
    else:
        print(
            "hex-upgrade cross-consistency bridge-template index entry "
            "verification FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
    *,
    index_entry: Mapping[str, Any],
    bridge_event_template_report: Mapping[str, Any],
    index_entry_bytes: bytes,
    bridge_event_template_bytes: bytes,
) -> dict[str, Any]:
    """Recompute and verify the local hex bridge-template index entry."""

    _assert_mapping(INDEX_ENTRY_ARTIFACT_ID, index_entry)
    _assert_mapping(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
    _assert_no_forbidden_input(INDEX_ENTRY_ARTIFACT_ID, index_entry)
    _assert_no_forbidden_input(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)

    blockers: list[str] = []
    contract_errors = (
        validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            index_entry
        )
    )
    blockers.extend(f"index_entry_contract:{error}" for error in contract_errors)

    created_at = _parse_created_at(index_entry, blockers)
    rebuilt_entry: dict[str, Any] | None = None
    source_contract_check = "failed"
    rebuilt_index_entry_check = "failed"
    if created_at is not None:
        rebuilt_entry = (
            build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
                bridge_event_template_report=bridge_event_template_report,
                bridge_event_template_bytes=bridge_event_template_bytes,
                now_utc=created_at,
            )
        )
        if rebuilt_entry.get("ok") is True:
            source_contract_check = "match"
            rebuilt_index_entry_check = _compare_rebuilt_index_entry(
                observed=index_entry,
                rebuilt=rebuilt_entry,
                blockers=blockers,
            )
        else:
            blockers.append("source_contract_rebuild_failed")

    digest_checks = _digest_checks(
        index_entry=index_entry,
        bridge_event_template_report=bridge_event_template_report,
        bridge_event_template_bytes=bridge_event_template_bytes,
        blockers=blockers,
    )
    size_checks = _size_checks(
        index_entry=index_entry,
        index_entry_bytes=index_entry_bytes,
        bridge_event_template_bytes=bridge_event_template_bytes,
        blockers=blockers,
    )
    _collect_authority_blockers(index_entry, blockers)

    template_entry = _mapping(index_entry.get("template_index_entry"), blockers)
    report: dict[str, Any] = {
        "ok": not blockers,
        "verification_version": VERIFICATION_VERSION,
        "index_entry_version": index_entry.get("index_entry_version"),
        "source_contract_check": source_contract_check,
        "rebuilt_index_entry_check": rebuilt_index_entry_check,
        "bridge_event_schema_check": (
            "match"
            if template_entry.get("bridge_event_schema_validated") is True
            and source_contract_check == "match"
            else "failed"
        ),
        "digest_checks": digest_checks,
        "size_checks": size_checks,
        "template_only": True,
        "manual_review_required": True,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "network_access_performed": False,
        "runtime_controls_added": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "approval_granted": False,
        "release_decision_made": False,
        "merge_decision_made": False,
        "promotion_granted": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "fast_track_priority": False,
        "claim_safe": False,
        "blockers": sorted(set(blockers)),
        "warnings": [],
    }
    _assert_no_forbidden_output(json.dumps(report, allow_nan=False, sort_keys=True))
    return report


def _digest_checks(
    *,
    index_entry: Mapping[str, Any],
    bridge_event_template_report: Mapping[str, Any],
    bridge_event_template_bytes: bytes,
    blockers: list[str],
) -> dict[str, str]:
    template_sha256 = _sha256_hex(bridge_event_template_bytes)
    template_digest = sha256_digest(_plain_json_object(bridge_event_template_report))
    event = _mapping(
        bridge_event_template_report.get("bridge_event_template"),
        blockers,
    )
    payload = _mapping(event.get("payload"), blockers)
    template_entry = _mapping(index_entry.get("template_index_entry"), blockers)
    artifacts = _artifact_records(index_entry, blockers)
    artifact = artifacts.get(TEMPLATE_ARTIFACT_ID, {})
    return {
        "template_report_sha256": _check_equal(
            template_entry.get("template_report_sha256"),
            template_sha256,
            "template_report_sha256_mismatch",
            blockers,
        ),
        "artifact_sha256": _check_equal(
            artifact.get("sha256"),
            template_sha256,
            "artifact_sha256_mismatch",
            blockers,
        ),
        "template_report_digest": _check_equal(
            template_entry.get("template_report_digest"),
            template_digest,
            "template_report_digest_mismatch",
            blockers,
        ),
        "artifact_digest": _check_equal(
            artifact.get("digest"),
            template_digest,
            "artifact_digest_mismatch",
            blockers,
        ),
        "event_digest": _check_equal(
            template_entry.get("event_digest"),
            sha256_digest(_plain_json_object(event)),
            "event_digest_mismatch",
            blockers,
        ),
        "payload_digest": _check_equal(
            template_entry.get("payload_digest"),
            sha256_digest(_plain_json_object(payload)),
            "payload_digest_mismatch",
            blockers,
        ),
    }


def _size_checks(
    *,
    index_entry: Mapping[str, Any],
    index_entry_bytes: bytes,
    bridge_event_template_bytes: bytes,
    blockers: list[str],
) -> dict[str, str]:
    artifacts = _artifact_records(index_entry, blockers)
    artifact = artifacts.get(TEMPLATE_ARTIFACT_ID, {})
    return {
        "index_entry_bytes_present": "match" if len(index_entry_bytes) > 0 else "failed",
        "artifact_byte_count": _check_equal(
            artifact.get("byte_count"),
            len(bridge_event_template_bytes),
            "artifact_byte_count_mismatch",
            blockers,
        ),
    }


def _compare_rebuilt_index_entry(
    *,
    observed: Mapping[str, Any],
    rebuilt: Mapping[str, Any],
    blockers: list[str],
) -> str:
    if _plain_json_object(observed) == _plain_json_object(rebuilt):
        return "match"
    blockers.append("rebuilt_index_entry_mismatch")
    return "mismatch"


def _collect_authority_blockers(
    index_entry: Mapping[str, Any],
    blockers: list[str],
) -> None:
    template_entry = _mapping(index_entry.get("template_index_entry"), blockers)
    for field in AUTHORITY_FALSE_FIELDS:
        if index_entry.get(field) is not False:
            blockers.append(f"index_entry_{field}_not_false")
        if template_entry.get(field) is not False:
            blockers.append(f"template_index_entry_{field}_not_false")

    consistency = _mapping(index_entry.get("consistency"), blockers)
    if consistency.get("artifact_payloads_included") is not False:
        blockers.append("consistency_artifact_payloads_included_not_false")
    if consistency.get("local_paths_recorded") is not False:
        blockers.append("consistency_local_paths_recorded_not_false")
    for artifact in _artifact_records(index_entry, blockers).values():
        if artifact.get("raw_artifact_payload_included") is not False:
            blockers.append("artifact_raw_payload_included_not_false")
        if artifact.get("local_path_recorded") is not False:
            blockers.append("artifact_local_path_recorded_not_false")


def _artifact_records(
    index_entry: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, Mapping[str, Any]]:
    artifacts = index_entry.get("artifacts")
    if not isinstance(artifacts, list):
        blockers.append("artifacts_not_list")
        return {}
    records: dict[str, Mapping[str, Any]] = {}
    for item in artifacts:
        if not isinstance(item, Mapping):
            blockers.append("artifact_not_object")
            continue
        artifact_id = item.get("artifact_id")
        if isinstance(artifact_id, str):
            records[artifact_id] = item
    return records


def _parse_created_at(
    index_entry: Mapping[str, Any],
    blockers: list[str],
) -> datetime | None:
    value = index_entry.get("created_at_utc")
    if not isinstance(value, str):
        blockers.append("created_at_utc_missing")
        return None
    try:
        return _parse_utc(value)
    except ValueError:
        blockers.append("created_at_utc_invalid")
        return None


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


def _failure_report(reason: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "verification_version": VERIFICATION_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "network_access_performed": False,
        "runtime_controls_added": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "approval_granted": False,
        "release_decision_made": False,
        "merge_decision_made": False,
        "promotion_granted": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "fast_track_priority": False,
        "claim_safe": False,
        "blockers": [
            "hex_upgrade_cross_consistency_digest_bridge_event_template_"
            "index_entry_verification_failed:"
            + _safe_reason(reason)
        ],
        "warnings": [],
    }
    return report


def _load_json_artifact(path: Path, label: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HexIndexEntryVerificationError(f"{label}_unreadable") from exc
    return raw, _decode_json_artifact_bytes(raw, label)


def _decode_json_artifact_bytes(raw: bytes, label: str) -> Mapping[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non_finite_json_constant:{value}")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise HexIndexEntryVerificationError(f"{label}_decode_error") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise HexIndexEntryVerificationError(f"{label}_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise HexIndexEntryVerificationError(f"{label}_not_object")
    return parsed


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _assert_mapping(label: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise HexIndexEntryVerificationError(f"{label}_not_object")


def _mapping(value: Any, blockers: list[str]) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    blockers.append("expected_mapping_missing")
    return {}


def _assert_no_forbidden_input(label: str, value: Any) -> None:
    if _contains_forbidden_marker(value):
        raise HexIndexEntryVerificationError(f"{label}_contains_forbidden_marker")


def _assert_no_forbidden_output(encoded: str) -> None:
    if _has_forbidden_marker(encoded):
        raise HexIndexEntryVerificationError(
            "verification_contains_forbidden_output_marker"
        )


def _contains_forbidden_marker(value: Any) -> bool:
    if isinstance(value, str):
        return _has_forbidden_marker(value)
    if isinstance(value, Mapping):
        return any(_contains_forbidden_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_marker(item) for item in value)
    return False


def _has_forbidden_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in FORBIDDEN_OUTPUT_MARKERS)


def _plain_json_object(value: Any) -> dict[str, Any]:
    try:
        plain = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise HexIndexEntryVerificationError("json_object_required") from exc
    if not isinstance(plain, dict):
        raise HexIndexEntryVerificationError("json_object_required")
    return plain


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


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
