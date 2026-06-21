#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a path-free bundle index for runtime receipt sink reviewer handoff.

The index binds the configured runtime receipt sink proof to its reviewer
handoff summary by digest. It records only source/summary hashes, sizes, and
derived safety booleans. It does not include payloads or local paths, append a
bridge event, change default receipt emission, upgrade claim safety, or grant
runtime authority.
"""
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

from tools.build_runtime_receipt_settings_sink_reviewer_handoff_summary import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
    BOUNDARY_FALSE_FIELDS,
    COVERAGE_BOOL_FIELDS,
    PROOF_ID as SUMMARY_PROOF_ID,
    SOURCE_PROOF_ID,
    SUMMARY_VERSION,
)


BUNDLE_INDEX_VERSION = (
    "waggledance.runtime_receipt_settings_sink_reviewer_handoff_bundle_index.v1"
)
PROOF_ID = "runtime_receipt_settings_sink_reviewer_handoff_bundle_index_v1"

FORBIDDEN_MARKERS = (
    "PRIVATE" + "_MARKER",
    "_DO" + "_NOT" + "_LEAK",
    "C:" + "\\",
    "C:/",
    "\\\\",
    "/home/",
    "/Users/",
    "/tmp/",
    "file" + "://",
    "http" + "://",
    "https" + "://",
    "waggledance-agent-worktrees",
    "Bearer ",
    "Author" + "ization",
)


class BundleIndexError(ValueError):
    """Raised when a bundle index input is unsafe or inconsistent."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sink-proof-json", required=True, type=Path)
    parser.add_argument("--summary-json", required=True, type=Path)
    parser.add_argument("--now", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sink_proof_bytes, sink_proof = _load_json_artifact(
            args.sink_proof_json,
            "source_sink_proof",
        )
        summary_bytes, reviewer_summary = _load_json_artifact(
            args.summary_json,
            "reviewer_handoff_summary",
        )
        report = build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index(
            sink_proof=sink_proof,
            reviewer_summary=reviewer_summary,
            sink_proof_bytes=sink_proof_bytes,
            summary_bytes=summary_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except BundleIndexError as exc:
        report = _failure(exc.code)
    except ValueError:
        report = _failure("runtime_receipt_sink_reviewer_bundle_index_invalid")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    elif report["ok"]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(", ".join(report["blockers"]), file=sys.stderr)
    return 0 if report["ok"] else 1


def build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index(
    *,
    sink_proof: Mapping[str, Any],
    reviewer_summary: Mapping[str, Any],
    sink_proof_bytes: bytes,
    summary_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free digest index for the sink proof and reviewer summary."""

    try:
        source = _safe_mapping("source_sink_proof", sink_proof)
        summary = _safe_mapping("reviewer_handoff_summary", reviewer_summary)
        _assert_source_proof_contract(source)
        _assert_summary_contract(source, summary)

        artifacts = [
            _artifact_record(
                artifact_id="source_sink_proof",
                artifact_kind="configured_runtime_receipt_sink_proof",
                raw_bytes=sink_proof_bytes,
            ),
            _artifact_record(
                artifact_id="reviewer_handoff_summary",
                artifact_kind="path_free_runtime_receipt_sink_reviewer_summary",
                raw_bytes=summary_bytes,
            ),
        ]
        index: dict[str, Any] = {
            "proof_id": PROOF_ID,
            "bundle_index_version": BUNDLE_INDEX_VERSION,
            "ok": True,
            "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
            "source_proof_id": SOURCE_PROOF_ID,
            "reviewer_summary_proof_id": SUMMARY_PROOF_ID,
            "reviewer_summary_version": SUMMARY_VERSION,
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
            "consistency": {
                "source_summary_binding": True,
                "source_proof_ok": source.get("ok") is True,
                "reviewer_summary_ok": summary.get("ok") is True,
                "source_summary_proof_id_match": True,
                "source_summary_coverage_match": True,
                "all_artifact_digests_recorded": True,
                "artifact_payloads_included": False,
                "local_paths_recorded": False,
            },
            "operator_boundary": {
                "manual_review_required": True,
                "claim_safe_unchanged": True,
                "approval_granted": False,
                "release_decision_made": False,
                "direct_bridge_write_performed": False,
                "transport_added": False,
                "external_fetch_performed": False,
                "runtime_controls_added": False,
                "runtime_authority_granted": False,
                "default_runtime_receipt_emission_changed": False,
                "external_writes_applied": False,
                "artifact_payloads_included": False,
                "local_paths_recorded": False,
            },
            "blockers": [],
            "warnings": [],
        }
        _assert_no_forbidden_output(json.dumps(index, sort_keys=True, allow_nan=False))
        return index
    except BundleIndexError as exc:
        return _failure(exc.code)


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
        raise BundleIndexError(f"{artifact_id}_not_object")
    return raw, parsed


def _safe_mapping(artifact_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BundleIndexError(f"{artifact_id}_not_object")
    try:
        safe = json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise BundleIndexError(f"{artifact_id}_not_json") from exc
    _assert_no_forbidden_input(json.dumps(safe, sort_keys=True, allow_nan=False))
    return safe


def _assert_source_proof_contract(source: Mapping[str, Any]) -> None:
    if source.get("proof_id") != SOURCE_PROOF_ID:
        raise BundleIndexError("source_proof_id_mismatch")
    if source.get("ok") is not True:
        raise BundleIndexError("source_proof_not_ok")
    for field in COVERAGE_BOOL_FIELDS:
        if source.get(field) is not True:
            raise BundleIndexError(f"source_{field}_not_true")
    for field in BOUNDARY_FALSE_FIELDS:
        if source.get(field) is not False:
            raise BundleIndexError(f"source_{field}_not_false")
    if not _is_uint(source.get("configured_sink_receipt_count")):
        raise BundleIndexError("source_configured_sink_receipt_count_unsafe")
    if source.get("configured_sink_receipt_count") < 1:
        raise BundleIndexError("source_configured_sink_receipt_count_missing")


def _assert_summary_contract(
    source: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> None:
    if summary.get("proof_id") != SUMMARY_PROOF_ID:
        raise BundleIndexError("summary_proof_id_mismatch")
    if summary.get("summary_version") != SUMMARY_VERSION:
        raise BundleIndexError("summary_version_mismatch")
    if summary.get("source_proof_id") != SOURCE_PROOF_ID:
        raise BundleIndexError("summary_source_proof_id_mismatch")
    if summary.get("ok") is not True:
        raise BundleIndexError("summary_not_ok")
    if summary.get("source_proof_ok") is not True:
        raise BundleIndexError("summary_source_proof_not_ok")
    if summary.get("manual_review_required") is not True:
        raise BundleIndexError("summary_manual_review_required_missing")
    if summary.get("claim_safe_unchanged") is not True:
        raise BundleIndexError("summary_claim_safe_changed")
    for field in AUTHORITY_FALSE_FIELDS:
        if summary.get(field) is not False:
            raise BundleIndexError(f"summary_{field}_not_false")

    configured_sink = summary.get("configured_sink")
    if not isinstance(configured_sink, Mapping):
        raise BundleIndexError("summary_configured_sink_not_object")
    if configured_sink.get("receipt_count") != source.get("configured_sink_receipt_count"):
        raise BundleIndexError("summary_receipt_count_mismatch")
    for field in COVERAGE_BOOL_FIELDS:
        if configured_sink.get(field) is not source.get(field):
            raise BundleIndexError(f"summary_{field}_mismatch")


def _artifact_record(
    *,
    artifact_id: str,
    artifact_kind: str,
    raw_bytes: bytes,
) -> dict[str, Any]:
    if not isinstance(raw_bytes, bytes) or len(raw_bytes) == 0:
        raise BundleIndexError(f"{artifact_id}_bytes_missing")
    return {
        "artifact_id": artifact_id,
        "artifact_kind": artifact_kind,
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "byte_count": len(raw_bytes),
        "payload_included": False,
        "local_path_recorded": False,
    }


def _parse_utc(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ValueError("unsafe now")
    parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("unsafe now")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _is_uint(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _assert_no_forbidden_input(text: str) -> None:
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in FORBIDDEN_MARKERS):
        raise BundleIndexError("path_or_private_marker_present")


def _assert_no_forbidden_output(text: str) -> None:
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in FORBIDDEN_MARKERS):
        raise BundleIndexError("bundle_index_forbidden_output")


def _failure(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "bundle_index_version": BUNDLE_INDEX_VERSION,
        "ok": False,
        "manual_review_required": True,
        "claim_safe_unchanged": True,
        "approval_granted": False,
        "release_decision_made": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "runtime_authority_granted": False,
        "default_runtime_receipt_emission_changed": False,
        "external_writes_applied": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            f"runtime_receipt_sink_reviewer_bundle_index_failed:{reason}"
        ],
        "warnings": [],
    }


if __name__ == "__main__":
    raise SystemExit(main())
