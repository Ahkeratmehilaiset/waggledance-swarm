#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Verify a local hex-upgrade cross-consistency template index entry.

The verifier is local and inert. It recomputes the index entry from the
source bridge-event template, checks recorded digest/size/schema metadata, and
confirms the no-authority boundary. It does not append bridge events, transport
artifacts, include payloads, upgrade claims, mutate runtime controls, or grant
runtime subdivision authority.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
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
)
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
    INDEX_ENTRY_VERSION,
    TEMPLATE_ARTIFACT_ID,
    IndexEntryError,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry,
    validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry,
)
from tools.hex_shadow_subdivision_replay import _contains_path_marker  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


VERIFICATION_VERSION = (
    "wd.hex_upgrade_cross_consistency_digest_bridge_event_template_"
    "index_entry_verification.v1"
)
INDEX_ENTRY_ARTIFACT_ID = (
    "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry"
)
_REQUIRED_ARTIFACTS = (TEMPLATE_ARTIFACT_ID,)
_SHA256_REF_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


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
        "--bridge-event-template-json",
        "--template-json",
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
            INDEX_ENTRY_ARTIFACT_ID,
        )
        template_bytes, template_report = _load_json_artifact(
            args.bridge_event_template_json,
            TEMPLATE_ARTIFACT_ID,
        )
        report = verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            index_entry=index_entry,
            bridge_event_template_report=template_report,
            bridge_event_template_bytes=template_bytes,
        )
    except TemplateIndexEntryVerificationError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report(
            "hex_upgrade_cross_consistency_digest_bridge_event_template_"
            "index_entry_verification_invalid"
        )

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
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
    bridge_event_template_bytes: bytes,
) -> dict[str, Any]:
    """Recompute artifact checks for the hex cross-consistency template index."""

    _assert_mapping(INDEX_ENTRY_ARTIFACT_ID, index_entry)
    _assert_mapping(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
    for artifact_id, artifact in (
        (INDEX_ENTRY_ARTIFACT_ID, index_entry),
        (TEMPLATE_ARTIFACT_ID, bridge_event_template_report),
    ):
        _assert_no_forbidden_input(artifact_id, artifact)

    blockers: list[str] = []
    rebuilt_entry = _rebuilt_source_index_entry(
        bridge_event_template_report=bridge_event_template_report,
        bridge_event_template_bytes=bridge_event_template_bytes,
        blockers=blockers,
    )
    rebuilt_index_entry_check = "failed"

    if index_entry.get("index_entry_version") != INDEX_ENTRY_VERSION:
        blockers.append("index_entry_version_mismatch")
    if index_entry.get("ok") is not True:
        blockers.append("index_entry_not_ok")
    if index_entry.get("template_only") is not True:
        blockers.append("index_entry_template_only_not_true")
    if index_entry.get("manual_review_required") is not True:
        blockers.append("manual_review_required_not_true")
    if index_entry.get("path_free_verified") is not True:
        blockers.append("index_entry_path_free_verified_not_true")
    if (
        not isinstance(index_entry.get("artifact_count"), int)
        or index_entry.get("artifact_count") != len(_REQUIRED_ARTIFACTS)
    ):
        blockers.append("artifact_count_mismatch")
    for error in validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry
    ):
        blockers.append(f"index_entry_contract:{error}")

    _collect_boundary_blockers(index_entry, blockers)
    _collect_template_entry_blockers(
        index_entry,
        bridge_event_template_report=bridge_event_template_report,
        bridge_event_template_bytes=bridge_event_template_bytes,
        blockers=blockers,
    )
    _collect_consistency_blockers(index_entry, blockers)

    if rebuilt_entry is not None:
        rebuilt_index_entry_check = _compare_rebuilt_index_entry(
            observed=index_entry,
            rebuilt=rebuilt_entry,
            blockers=blockers,
        )

    records = _artifact_records(index_entry, blockers)
    digest_checks: dict[str, str] = {}
    size_checks: dict[str, str] = {}
    schema_version_checks: dict[str, str] = {}
    artifact = bridge_event_template_report
    raw = bridge_event_template_bytes
    record = records.get(TEMPLATE_ARTIFACT_ID)
    if record is None:
        digest_checks[TEMPLATE_ARTIFACT_ID] = "missing_index_record"
        size_checks[TEMPLATE_ARTIFACT_ID] = "missing_index_record"
        schema_version_checks[TEMPLATE_ARTIFACT_ID] = "missing_index_record"
    else:
        digest_checks[TEMPLATE_ARTIFACT_ID] = _check_equal(
            record.get("sha256"),
            _sha256_hex(raw),
            f"digest_mismatch:{TEMPLATE_ARTIFACT_ID}",
            blockers,
        )
        size_checks[TEMPLATE_ARTIFACT_ID] = _check_equal(
            record.get("size_bytes"),
            len(raw),
            f"size_mismatch:{TEMPLATE_ARTIFACT_ID}",
            blockers,
        )
        schema_version_checks[TEMPLATE_ARTIFACT_ID] = _check_equal(
            record.get("json_schema_version"),
            _schema_version(artifact),
            f"schema_version_mismatch:{TEMPLATE_ARTIFACT_ID}",
            blockers,
        )
        if record.get("digest") != _canonical_digest(artifact):
            blockers.append(f"canonical_digest_mismatch:{TEMPLATE_ARTIFACT_ID}")
        if record.get("payload_included") is not False:
            blockers.append(f"payload_included_not_false:{TEMPLATE_ARTIFACT_ID}")
        if record.get("local_path_recorded") is not False:
            blockers.append(f"local_path_recorded_not_false:{TEMPLATE_ARTIFACT_ID}")

    template_entry = _mapping(index_entry.get("template_index_entry"))
    report: dict[str, Any] = {
        "ok": not blockers,
        "verification_version": VERIFICATION_VERSION,
        "index_entry_version": index_entry.get("index_entry_version"),
        "source_template_version": bridge_event_template_report.get(
            "template_version"
        ),
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
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": sorted(set(blockers)),
        "warnings": [],
    }
    for field in AUTHORITY_FALSE_FIELDS:
        report[field] = False
    report["path_free_verified"] = not _contains_path_marker(report)
    _assert_no_forbidden_output(json.dumps(report, allow_nan=False, sort_keys=True))
    return report


def _rebuilt_source_index_entry(
    *,
    bridge_event_template_report: Mapping[str, Any],
    bridge_event_template_bytes: bytes,
    blockers: list[str],
) -> Mapping[str, Any] | None:
    try:
        return build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            bridge_event_template_report=bridge_event_template_report,
            bridge_event_template_bytes=bridge_event_template_bytes,
        )
    except IndexEntryError as exc:
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
    for field in AUTHORITY_FALSE_FIELDS:
        if index_entry.get(field) is not False:
            blockers.append(f"{field}_not_false")


def _collect_template_entry_blockers(
    index_entry: Mapping[str, Any],
    *,
    bridge_event_template_report: Mapping[str, Any],
    bridge_event_template_bytes: bytes,
    blockers: list[str],
) -> None:
    template_entry = _mapping(index_entry.get("template_index_entry"))
    event = _mapping(bridge_event_template_report.get("bridge_event_template"))
    payload = _mapping(event.get("payload"))
    cross = _mapping(payload.get("cross_consistency"))
    if template_entry.get("artifact_id") != TEMPLATE_ARTIFACT_ID:
        blockers.append("template_index_entry_artifact_id_mismatch")
    if template_entry.get("template_proof_id") != SOURCE_TEMPLATE_PROOF_ID:
        blockers.append("template_index_entry_template_proof_id_mismatch")
    if template_entry.get("template_version") != SOURCE_TEMPLATE_VERSION:
        blockers.append("template_index_entry_template_version_mismatch")
    if template_entry.get("event_status") != SOURCE_EVENT_STATUS:
        blockers.append("template_index_entry_event_status_mismatch")
    if template_entry.get("bridge_event_schema_validated") is not True:
        blockers.append("template_index_entry_schema_validated_not_true")
    if template_entry.get("source_digest_schema_version") != DIGEST_REPORT_VERSION:
        blockers.append("template_index_entry_digest_schema_version_mismatch")
    if not _sha256_ref(template_entry.get("source_digest_ref")):
        blockers.append("template_index_entry_source_digest_ref_invalid")
    if template_entry.get("source_digest_ref") != cross.get("digest_ref"):
        blockers.append("template_index_entry_source_digest_ref_mismatch")
    if template_entry.get("template_report_sha256") != _sha256_hex(
        bridge_event_template_bytes
    ):
        blockers.append("template_index_entry_template_report_sha256_mismatch")
    for field in _DIGEST_VERDICT_FIELDS:
        if not isinstance(template_entry.get(field), bool):
            blockers.append(f"template_index_entry_{field}_not_bool")
        expected = cross.get(field) is True
        if template_entry.get(field) != expected:
            blockers.append(f"template_index_entry_{field}_mismatch")
    for check in (
        "source_contract_check",
        "template_contract_check",
        "authority_boundary_check",
        "cross_consistency_safe_keys_check",
    ):
        if template_entry.get(check) != "match":
            blockers.append(f"template_index_entry_{check}_not_match")
    for field in AUTHORITY_FALSE_FIELDS:
        if template_entry.get(field) is not False:
            blockers.append(f"template_index_entry_{field}_not_false")
    if template_entry.get("raw_digest_payload_included") is not False:
        blockers.append("template_index_entry_raw_digest_payload_included_not_false")


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
    for check in (
        "source_contract_check",
        "template_contract_check",
        "authority_boundary_check",
        "cross_consistency_safe_keys_check",
    ):
        if consistency.get(check) != "match":
            blockers.append(f"consistency_{check}_not_match")
    if consistency.get("template_only") is not True:
        blockers.append("consistency_template_only_not_true")
    if consistency.get("artifact_payloads_included") is not False:
        blockers.append("consistency_payloads_included_not_false")
    if consistency.get("local_paths_recorded") is not False:
        blockers.append("consistency_paths_recorded_not_false")


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
    for field in ("template_version", "index_entry_version"):
        value = artifact.get(field)
        if isinstance(value, str):
            return value
    return "invalid_schema"


def _canonical_digest(artifact: Mapping[str, Any]) -> str:
    plain = json.loads(json.dumps(artifact, allow_nan=False))
    if not isinstance(plain, dict):
        return "invalid_digest"
    return sha256_digest(plain)


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
    report: dict[str, Any] = {
        "ok": False,
        "verification_version": VERIFICATION_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "path_free_verified": True,
        "blockers": [
            "hex_upgrade_cross_consistency_digest_bridge_event_template_"
            "index_entry_verification_failed:"
            f"{_safe_reason(reason)}"
        ],
        "warnings": [],
    }
    for field in AUTHORITY_FALSE_FIELDS:
        report[field] = False
    return report


def _assert_mapping(artifact_id: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise TemplateIndexEntryVerificationError(f"{artifact_id}_not_object")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_ref(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_REF_PATTERN.fullmatch(value) is not None


def _assert_no_forbidden_input(artifact_id: str, value: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise TemplateIndexEntryVerificationError(
            f"{artifact_id}_non_finite_json_value"
        ) from exc
    if _contains_path_marker(value) or _forbidden_output_markers(serialized):
        raise TemplateIndexEntryVerificationError(f"{artifact_id}_forbidden_marker")


def _safe_reason(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9:._-]+", "_", str(value)).strip("_")
    if not text:
        return "invalid_reason"
    if not text[0].isalnum():
        text = "reason_" + text
    if _forbidden_output_markers(text) or _contains_path_marker(text):
        return "unsafe_marker_redacted"
    return text[:192]


def _forbidden_output_markers(text: str) -> list[str]:
    lower_text = text.lower()
    return sorted(
        marker
        for marker in FORBIDDEN_OUTPUT_MARKERS
        if marker.lower() in lower_text
    )


def _assert_no_forbidden_output(text: str) -> None:
    if _contains_path_marker(text) or _forbidden_output_markers(text):
        raise ValueError(
            "hex-upgrade cross-consistency bridge-event template index entry "
            "verification contains forbidden markers"
        )


if __name__ == "__main__":
    raise SystemExit(main())
