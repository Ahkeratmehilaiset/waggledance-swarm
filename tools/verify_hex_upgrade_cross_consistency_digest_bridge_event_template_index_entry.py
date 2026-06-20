#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Verify a local hex cross-consistency bridge-template index entry.

The verifier recomputes the source digest/template index entry from explicit
local JSON artifacts and checks only deterministic, path-free facts: digest,
size, schema/version, source-contract, rebuilt-entry, bridge-event schema, and
authority-boundary booleans. It does not append bridge events, transport
payloads, write runtime state, upgrade claims, or grant subdivision authority.
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
    EVENT_STATUS as SOURCE_EVENT_STATUS,
    TEMPLATE_VERSION as SOURCE_TEMPLATE_VERSION,
    _DIGEST_VERDICT_FIELDS,
)
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
    DIGEST_ARTIFACT_ID,
    FORBIDDEN_OUTPUT_MARKERS,
    INDEX_ENTRY_VERSION,
    PROOF_ID as SOURCE_PROOF_ID,
    TEMPLATE_ARTIFACT_ID,
    IndexEntryError,
    _contains_path_marker_local,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry,
)
from tools.hex_shadow_subdivision_replay import _contains_path_marker  # noqa: E402
from tools.run_hex_upgrade_cross_consistency_digest import (  # noqa: E402
    REPORT_VERSION as DIGEST_REPORT_VERSION,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


VERIFICATION_VERSION = (
    "wd.hex_upgrade_cross_consistency_digest_bridge_event_template_"
    "index_entry_verification.v1"
)
VERIFICATION_PROOF_ID = (
    "hex_upgrade_cross_consistency_digest_bridge_event_template_"
    "index_entry_verification_v1"
)
INDEX_ENTRY_ARTIFACT_ID = (
    "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry"
)
_REQUIRED_ARTIFACTS = (DIGEST_ARTIFACT_ID, TEMPLATE_ARTIFACT_ID)


class HexCrossConsistencyIndexEntryVerificationError(ValueError):
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
    parser.add_argument("--digest-json", required=True, type=Path)
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
        digest_bytes, digest = _load_json_artifact(
            args.digest_json,
            DIGEST_ARTIFACT_ID,
        )
        template_bytes, template_report = _load_json_artifact(
            args.bridge_event_template_json,
            TEMPLATE_ARTIFACT_ID,
        )
        report = verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            index_entry=index_entry,
            digest=digest,
            bridge_event_template_report=template_report,
            digest_bytes=digest_bytes,
            bridge_event_template_bytes=template_bytes,
        )
    except HexCrossConsistencyIndexEntryVerificationError as exc:
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
            "hex cross-consistency bridge-template index entry verification "
            "FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
    *,
    index_entry: Mapping[str, Any],
    digest: Mapping[str, Any],
    bridge_event_template_report: Mapping[str, Any],
    digest_bytes: bytes,
    bridge_event_template_bytes: bytes,
) -> dict[str, Any]:
    """Recompute artifact checks for the hex cross-consistency index entry."""

    _assert_mapping(INDEX_ENTRY_ARTIFACT_ID, index_entry)
    _assert_mapping(DIGEST_ARTIFACT_ID, digest)
    _assert_mapping(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
    for artifact_id, value in (
        (INDEX_ENTRY_ARTIFACT_ID, index_entry),
        (DIGEST_ARTIFACT_ID, digest),
        (TEMPLATE_ARTIFACT_ID, bridge_event_template_report),
    ):
        _assert_no_forbidden_input(artifact_id, value)

    blockers: list[str] = []
    rebuilt_entry = _rebuilt_source_index_entry(
        digest=digest,
        bridge_event_template_report=bridge_event_template_report,
        digest_bytes=digest_bytes,
        bridge_event_template_bytes=bridge_event_template_bytes,
        blockers=blockers,
    )
    rebuilt_index_entry_check = "failed"

    if index_entry.get("proof_id") != SOURCE_PROOF_ID:
        blockers.append("proof_id_mismatch")
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
    _collect_template_entry_blockers(
        index_entry,
        digest=digest,
        digest_bytes=digest_bytes,
        bridge_event_template_bytes=bridge_event_template_bytes,
        blockers=blockers,
    )
    _collect_cross_consistency_blockers(index_entry, digest, blockers)
    _collect_consistency_blockers(index_entry, blockers)

    if rebuilt_entry is not None:
        rebuilt_index_entry_check = _compare_rebuilt_index_entry(
            observed=index_entry,
            rebuilt=rebuilt_entry,
            blockers=blockers,
        )

    artifacts = {
        DIGEST_ARTIFACT_ID: (digest, digest_bytes),
        TEMPLATE_ARTIFACT_ID: (
            bridge_event_template_report,
            bridge_event_template_bytes,
        ),
    }
    records = _artifact_records(index_entry, blockers)
    digest_checks: dict[str, str] = {}
    size_checks: dict[str, str] = {}
    schema_version_checks: dict[str, str] = {}
    for artifact_id in _REQUIRED_ARTIFACTS:
        artifact_value, raw = artifacts[artifact_id]
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
            _schema_version(artifact_value),
            f"schema_version_mismatch:{artifact_id}",
            blockers,
        )
        if record.get("payload_included") is not False:
            blockers.append(f"payload_included_not_false:{artifact_id}")
        if record.get("local_path_recorded") is not False:
            blockers.append(f"local_path_recorded_not_false:{artifact_id}")

    template_entry = _mapping(index_entry.get("template_index_entry"))
    report = {
        "proof_id": VERIFICATION_PROOF_ID,
        "ok": not blockers,
        "verification_version": VERIFICATION_VERSION,
        "verified_proof_id": index_entry.get("proof_id"),
        "index_entry_version": index_entry.get("index_entry_version"),
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
        "digest_ref_check": (
            "match" if _digest_ref_check(index_entry, digest) else "failed"
        ),
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "merge_decision_made": False,
        "promotion_granted": False,
        "automatic_release_decision": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "fast_track_priority": False,
        "digest_payloads_included": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": sorted(set(blockers)),
        "warnings": [],
    }
    _assert_no_forbidden_output(json.dumps(report, allow_nan=False, sort_keys=True))
    return report


def _rebuilt_source_index_entry(
    *,
    digest: Mapping[str, Any],
    bridge_event_template_report: Mapping[str, Any],
    digest_bytes: bytes,
    bridge_event_template_bytes: bytes,
    blockers: list[str],
) -> Mapping[str, Any] | None:
    try:
        return build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            digest=digest,
            bridge_event_template_report=bridge_event_template_report,
            digest_bytes=digest_bytes,
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
    operator_boundary = _mapping(index_entry.get("operator_boundary"))
    if index_entry.get("manual_review_required") is not True:
        blockers.append("manual_review_required_not_true")
    if operator_boundary.get("manual_review_required") is not True:
        blockers.append("operator_boundary_manual_review_required_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if index_entry.get(field) is not False:
            blockers.append(f"{field}_not_false")
        if operator_boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")


def _collect_template_entry_blockers(
    index_entry: Mapping[str, Any],
    *,
    digest: Mapping[str, Any],
    digest_bytes: bytes,
    bridge_event_template_bytes: bytes,
    blockers: list[str],
) -> None:
    template_entry = _mapping(index_entry.get("template_index_entry"))
    if template_entry.get("artifact_id") != TEMPLATE_ARTIFACT_ID:
        blockers.append("template_index_entry_artifact_id_mismatch")
    if template_entry.get("source_artifact_id") != DIGEST_ARTIFACT_ID:
        blockers.append("template_index_entry_source_artifact_id_mismatch")
    if template_entry.get("template_version") != SOURCE_TEMPLATE_VERSION:
        blockers.append("template_index_entry_template_version_mismatch")
    for field in (
        "template_only",
        "bridge_event_schema_validated",
        "manual_review_required",
    ):
        if template_entry.get(field) is not True:
            blockers.append(f"template_index_entry_{field}_not_true")
    if template_entry.get("source_digest_sha256") != _sha256_hex(digest_bytes):
        blockers.append("template_index_entry_source_digest_sha256_mismatch")
    if template_entry.get("source_digest_ref") != sha256_digest(_plain_json_object(digest)):
        blockers.append("template_index_entry_source_digest_ref_mismatch")
    if template_entry.get("template_sha256") != _sha256_hex(
        bridge_event_template_bytes
    ):
        blockers.append("template_index_entry_template_sha256_mismatch")
    for field in ("source_contract_check", "digest_ref_check", "rebuilt_template_check"):
        if template_entry.get(field) != "match":
            blockers.append(f"template_index_entry_{field}_not_match")
    if template_entry.get("event_type") != "handoff":
        blockers.append("template_index_entry_event_type_mismatch")
    if template_entry.get("event_status") != SOURCE_EVENT_STATUS:
        blockers.append("template_index_entry_event_status_mismatch")
    for field in _DIGEST_VERDICT_FIELDS:
        if template_entry.get(field) is not digest.get(field):
            blockers.append(f"template_index_entry_{field}_mismatch")
    for field in AUTHORITY_FALSE_FIELDS:
        if field in template_entry and template_entry.get(field) is not False:
            blockers.append(f"template_index_entry_{field}_not_false")


def _collect_cross_consistency_blockers(
    index_entry: Mapping[str, Any],
    digest: Mapping[str, Any],
    blockers: list[str],
) -> None:
    cross = _mapping(index_entry.get("cross_consistency"))
    if cross.get("digest_schema_version") != DIGEST_REPORT_VERSION:
        blockers.append("cross_consistency_digest_schema_version_mismatch")
    if cross.get("digest_ref") != sha256_digest(_plain_json_object(digest)):
        blockers.append("cross_consistency_digest_ref_mismatch")
    for field in _DIGEST_VERDICT_FIELDS:
        if cross.get(field) is not digest.get(field):
            blockers.append(f"cross_consistency_{field}_mismatch")
    if cross.get("raw_digest_payload_included") is not False:
        blockers.append("cross_consistency_raw_digest_payload_included_not_false")
    if cross.get("template_cross_consistency_ref_matches") is not True:
        blockers.append("cross_consistency_template_ref_match_not_true")


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
        "template_only",
    ):
        if consistency.get(field) is not True:
            blockers.append(f"consistency_{field}_not_true")
    for field in ("source_contract_check", "digest_ref_check", "rebuilt_template_check"):
        if consistency.get(field) != "match":
            blockers.append(f"consistency_{field}_not_match")
    for field in (
        "digest_payloads_included",
        "artifact_payloads_included",
        "local_paths_recorded",
    ):
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


def _digest_ref_check(index_entry: Mapping[str, Any], digest: Mapping[str, Any]) -> bool:
    digest_ref = sha256_digest(_plain_json_object(digest))
    return (
        _mapping(index_entry.get("template_index_entry")).get("source_digest_ref")
        == digest_ref
        and _mapping(index_entry.get("cross_consistency")).get("digest_ref")
        == digest_ref
    )


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
    for field in ("report_version", "template_version", "index_entry_version"):
        value = artifact.get(field)
        if isinstance(value, str):
            return value
    return "invalid_schema"


def _plain_json_object(value: Any) -> dict[str, Any]:
    try:
        plain = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise HexCrossConsistencyIndexEntryVerificationError(
            "json_object_invalid"
        ) from exc
    if not isinstance(plain, dict):
        raise HexCrossConsistencyIndexEntryVerificationError(
            "json_object_not_object"
        )
    return plain


def _load_json_artifact(path: Path, artifact_id: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HexCrossConsistencyIndexEntryVerificationError(
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
        raise HexCrossConsistencyIndexEntryVerificationError(
            f"{artifact_id}_decode_error"
        ) from exc
    except json.JSONDecodeError as exc:
        raise HexCrossConsistencyIndexEntryVerificationError(
            f"{artifact_id}_json_error"
        ) from exc
    except ValueError as exc:
        raise HexCrossConsistencyIndexEntryVerificationError(
            f"{artifact_id}_json_error"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise HexCrossConsistencyIndexEntryVerificationError(
            f"{artifact_id}_not_object"
        )
    return raw, parsed


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "proof_id": VERIFICATION_PROOF_ID,
        "ok": False,
        "verification_version": VERIFICATION_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "merge_decision_made": False,
        "promotion_granted": False,
        "automatic_release_decision": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "fast_track_priority": False,
        "digest_payloads_included": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "hex_upgrade_cross_consistency_digest_bridge_event_template_"
            f"index_entry_verification_failed:{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


def _assert_mapping(artifact_id: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise HexCrossConsistencyIndexEntryVerificationError(
            f"{artifact_id}_not_object"
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _assert_no_forbidden_input(artifact_id: str, value: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise HexCrossConsistencyIndexEntryVerificationError(
            f"{artifact_id}_non_finite_json_value"
        ) from exc
    if (
        _contains_path_marker(value)
        or _contains_path_marker_local(value)
        or _forbidden_output_markers(serialized)
    ):
        raise HexCrossConsistencyIndexEntryVerificationError(
            f"{artifact_id}_forbidden_marker"
        )


def _safe_reason(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9:._-]+", "_", str(value)).strip("_")
    if not text or _forbidden_output_markers(text):
        return "unsafe_marker_redacted"
    if not text[0].isalnum():
        text = "reason_" + text
    return text[:192]


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
            "hex cross-consistency bridge-event template index entry "
            "verification contains forbidden markers"
        )


if __name__ == "__main__":
    raise SystemExit(main())
