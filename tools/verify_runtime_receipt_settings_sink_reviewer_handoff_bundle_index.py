#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Verify a local runtime-receipt-sink reviewer-handoff bundle index.

Offline verifier for the deterministic_solver_first measurement chain:
configured runtime receipt sink proof -> reviewer-handoff summary -> bundle
index -> this verifier. It RE-DERIVES the bundle index from the source sink
proof + reviewer summary (and their bytes) using the canonical builder, asserts
the recorded bundle index matches the re-derivation (so a tampered digest /
version / boundary flag fails closed), and independently re-checks the
measurement-only invariants. The CLI reads only the explicit local JSON input
files passed to it (read-only); beyond that the verification performs no network
or transport, no bridge writes, no output file writes, grants no runtime
authority, changes no default receipt emission, and upgrades no capability
claim_safe.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
    BUNDLE_INDEX_VERSION,
    PROOF_ID as BUNDLE_INDEX_PROOF_ID,
    SOURCE_PROOF_ID,
    SUMMARY_VERSION,
    build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index,
)


VERIFICATION_VERSION = (
    "wd.runtime_receipt_settings_sink_reviewer_handoff_bundle_index_"
    "verification.v1"
)
PROOF_ID = "runtime_receipt_settings_sink_reviewer_handoff_bundle_index_verification_v1"
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
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
# A fixed re-derivation timestamp; created_at_utc is excluded from the structural
# comparison and validated separately, so the rebuild clock is irrelevant.
_FIXED_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class BundleIndexVerificationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-index-json", required=True, type=Path)
    parser.add_argument("--sink-proof-json", required=True, type=Path)
    parser.add_argument("--reviewer-summary-json", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        bundle_bytes, bundle_index = _load_json_artifact(args.bundle_index_json)
        sink_bytes, sink_proof = _load_json_artifact(args.sink_proof_json)
        summary_bytes, summary = _load_json_artifact(args.reviewer_summary_json)
        report = verify_runtime_receipt_settings_sink_reviewer_handoff_bundle_index(
            bundle_index=bundle_index,
            sink_proof=sink_proof,
            reviewer_summary=summary,
            sink_proof_bytes=sink_bytes,
            summary_bytes=summary_bytes,
        )
    except (OSError, json.JSONDecodeError, ValueError):
        report = _failure("bundle_index_verification_invalid")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0 if report["ok"] else 1


def verify_runtime_receipt_settings_sink_reviewer_handoff_bundle_index(
    *,
    bundle_index: Mapping[str, Any],
    sink_proof: Mapping[str, Any],
    reviewer_summary: Mapping[str, Any],
    sink_proof_bytes: bytes,
    summary_bytes: bytes,
) -> dict[str, Any]:
    recorded, error = _safe_mapping(bundle_index)
    if error:
        return _failure(error)
    assert recorded is not None

    blockers: list[str] = []
    # (1) Re-derive the bundle index from the source artifacts via the canonical
    # builder. A tampered recorded entry will diverge from this re-derivation.
    try:
        rederived = build_runtime_receipt_settings_sink_reviewer_handoff_bundle_index(
            sink_proof=sink_proof,
            reviewer_summary=reviewer_summary,
            sink_proof_bytes=sink_proof_bytes,
            summary_bytes=summary_bytes,
            now_utc=_FIXED_NOW,
        )
    except Exception:
        return _failure("rederivation_failed")
    if rederived.get("ok") is not True:
        return _failure("rederived_bundle_index_not_ok")

    # (2) Structural match (created_at_utc excluded -- validated separately).
    recorded_cmp = {k: v for k, v in recorded.items() if k != "created_at_utc"}
    rederived_cmp = {k: v for k, v in rederived.items() if k != "created_at_utc"}
    structural_match = recorded_cmp == rederived_cmp
    if not structural_match:
        blockers.append("bundle_index_does_not_match_rederivation")

    # (3) Independent measurement-only invariants on the recorded entry.
    if recorded.get("ok") is not True:
        blockers.append("recorded_bundle_index_ok_not_true")
    if recorded.get("proof_id") != BUNDLE_INDEX_PROOF_ID:
        blockers.append("bundle_index_proof_id_mismatch")
    if recorded.get("bundle_index_version") != BUNDLE_INDEX_VERSION:
        blockers.append("bundle_index_version_mismatch")
    if recorded.get("source_proof_id") != SOURCE_PROOF_ID:
        blockers.append("source_proof_id_mismatch")
    if recorded.get("reviewer_summary_version") != SUMMARY_VERSION:
        blockers.append("reviewer_summary_version_mismatch")
    if not _is_utc(recorded.get("created_at_utc")):
        blockers.append("created_at_utc_unsafe")

    boundary = recorded.get("operator_boundary")
    if not isinstance(boundary, Mapping):
        blockers.append("operator_boundary_missing")
    else:
        if boundary.get("manual_review_required") is not True:
            blockers.append("manual_review_required_not_true")
        if boundary.get("claim_safe_unchanged") is not True:
            blockers.append("claim_safe_unchanged_not_true")
        for field in AUTHORITY_FALSE_FIELDS:
            if boundary.get(field) is not False:
                blockers.append(f"boundary_{field}_not_false")

    ok = not blockers
    report = {
        "proof_id": PROOF_ID,
        "verification_version": VERIFICATION_VERSION,
        "ok": ok,
        "bundle_index_proof_id": BUNDLE_INDEX_PROOF_ID,
        "rederivation_match": structural_match,
        "checks": {
            "rederivation_match": structural_match,
            "recorded_ok": recorded.get("ok") is True,
            "version_match": recorded.get("bundle_index_version")
            == BUNDLE_INDEX_VERSION,
            "boundary_claim_safe_unchanged": isinstance(boundary, Mapping)
            and boundary.get("claim_safe_unchanged") is True,
        },
        "template_only": True,
        "claim_safe_unchanged": True,
        "manual_review_required": True,
        "blockers": sorted(set(blockers)),
        "warnings": [],
    }
    for field in AUTHORITY_FALSE_FIELDS:
        report[field] = False
    try:
        _assert_no_forbidden(json.dumps(report, sort_keys=True, allow_nan=False))
    except ValueError:
        return _failure("verification_forbidden_output")
    return report


def _safe_mapping(value: Any) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(value, Mapping):
        return None, "bundle_index_not_object"
    try:
        normalized = json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError):
        return None, "bundle_index_not_json"
    try:
        _assert_no_forbidden(json.dumps(normalized, sort_keys=True, allow_nan=False))
    except ValueError:
        return None, "path_or_private_marker_present"
    return normalized, ""


def _load_json_artifact(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, Mapping):
        raise BundleIndexVerificationError("artifact_not_object")
    return raw, dict(parsed)


def _is_utc(value: Any) -> bool:
    return isinstance(value, str) and bool(_UTC_RE.fullmatch(value))


def _assert_no_forbidden(text: str) -> None:
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in FORBIDDEN_MARKERS):
        raise ValueError("forbidden output marker")


def _failure(reason: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "proof_id": PROOF_ID,
        "verification_version": VERIFICATION_VERSION,
        "ok": False,
        "template_only": True,
        "claim_safe_unchanged": True,
        "manual_review_required": True,
        "rederivation_match": False,
        "blockers": [f"bundle_index_verification_failed:{reason}"],
        "warnings": [],
    }
    for field in AUTHORITY_FALSE_FIELDS:
        report[field] = False
    return report


if __name__ == "__main__":
    raise SystemExit(main())
