#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Render a path-free reviewer-handoff summary for the configured runtime
receipt sink proof (``runtime_receipt_settings_sink_smoke``).

Input is the path-free proof dict produced by
``build_runtime_receipt_settings_sink_smoke`` (the deterministic_solver_first
"configured runtime receipt sink" coverage proof). The output is a measurement-
only reviewer summary: it surfaces only derived booleans and validated counts,
never the source proof's free-text conclusion, config paths, payloads, or any
authority. It does not change default receipt emission, grant runtime authority,
or flip any capability ``claim_safe``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


SUMMARY_VERSION = (
    "waggledance.runtime_receipt_settings_sink_reviewer_handoff_summary.v1"
)
PROOF_ID = "runtime_receipt_settings_sink_reviewer_handoff_summary_v1"
SOURCE_PROOF_ID = "runtime_receipt_settings_sink_smoke_v1"

AGENT_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,191}$")
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
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
# Source-proof coverage booleans surfaced as-is (each strict-bool validated).
COVERAGE_BOOL_FIELDS = (
    "default_off_preserved",
    "configured_sink_callable",
    "configured_sink_verifier_ok",
    "configured_sink_result_path_free",
    "configured_sink_result_payload_free",
    "local_receipt_bundle_written",
    "emitted_receipt_payload_safe",
    "temp_artifacts_removed",
)
# Source-proof boundary flags that MUST be False (fail-closed if any is True).
BOUNDARY_FALSE_FIELDS = (
    "settings_default_enabled",
    "paths_returned",
    "payloads_returned",
    "default_runtime_receipt_emission_changed",
    "runtime_authority_changed",
    "external_writes_applied",
)
# Authority/decision fields the summary always pins False.
AUTHORITY_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "runtime_authority_granted",
    "default_runtime_receipt_emission_changed",
    "external_writes_applied",
    "artifact_payloads_included",
    "local_paths_recorded",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sink-proof-json", required=True, type=Path)
    parser.add_argument("--reviewer-agent", required=True)
    parser.add_argument("--handoff-ref", required=True)
    parser.add_argument("--now", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        proof = json.loads(args.sink_proof_json.read_text(encoding="utf-8"))
        report = build_runtime_receipt_settings_sink_reviewer_handoff_summary(
            sink_proof=proof,
            reviewer_agent_id=args.reviewer_agent,
            handoff_ref=args.handoff_ref,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, json.JSONDecodeError, ValueError):
        report = _failure("runtime_receipt_sink_reviewer_summary_invalid")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    elif report["ok"]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(", ".join(report["blockers"]), file=sys.stderr)
    return 0 if report["ok"] else 1


def build_runtime_receipt_settings_sink_reviewer_handoff_summary(
    *,
    sink_proof: Mapping[str, Any],
    reviewer_agent_id: str,
    handoff_ref: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    error = _input_error(reviewer_agent_id, handoff_ref)
    if error:
        return _failure(error)
    proof, error = _safe_proof(sink_proof)
    if error:
        return _failure(error)
    assert proof is not None

    coverage = {field: proof[field] is True for field in COVERAGE_BOOL_FIELDS}
    receipt_count = _int(proof.get("configured_sink_receipt_count"))
    source_ok = proof.get("ok") is True
    # Measurement-only OK: source proof ok, all coverage booleans true, at least
    # one configured-sink receipt observed, and no boundary flag tripped. This is
    # reviewer context; it does NOT flip any capability claim_safe.
    ok = (
        source_ok
        and all(coverage.values())
        and receipt_count >= 1
    )

    summary: dict[str, Any] = {
        "proof_id": PROOF_ID,
        "summary_version": SUMMARY_VERSION,
        "ok": ok,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "reviewer_agent_id": reviewer_agent_id,
        "handoff_ref": handoff_ref,
        "source_proof_id": SOURCE_PROOF_ID,
        "source_proof_ok": source_ok,
        "configured_sink": {
            "receipt_count": receipt_count,
            **coverage,
        },
        "manual_review_required": True,
        "reviewer_next_actions": [
            "review_configured_runtime_receipt_sink_coverage",
            "confirm_default_off_receipt_emission_unchanged",
        ],
        "template_only": True,
        "claim_safe_unchanged": True,
        "blockers": [],
        "warnings": [],
    }
    for field in AUTHORITY_FALSE_FIELDS:
        summary[field] = False

    try:
        _assert_no_forbidden(json.dumps(summary, sort_keys=True, allow_nan=False))
    except ValueError:
        return _failure("reviewer_summary_forbidden_output")
    return summary


def _safe_proof(value: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(value, Mapping):
        return None, "sink_proof_not_object"
    try:
        proof = json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError):
        return None, "sink_proof_not_json"
    try:
        _assert_no_forbidden(json.dumps(proof, sort_keys=True, allow_nan=False))
    except ValueError:
        return None, "path_or_private_marker_present"
    if proof.get("proof_id") != SOURCE_PROOF_ID:
        return None, "source_proof_id_mismatch"
    if not isinstance(proof.get("ok"), bool):
        return None, "ok_not_bool"
    # Every surfaced coverage boolean must be a strict bool (no truthy coercion).
    for field in COVERAGE_BOOL_FIELDS:
        if not isinstance(proof.get(field), bool):
            return None, f"{field}_not_bool"
    # The boundary flags must all be present and explicitly False (fail-closed):
    # a tripped/missing/non-bool boundary flag rejects the summary.
    for field in BOUNDARY_FALSE_FIELDS:
        if proof.get(field) is not False:
            return None, f"{field}_not_false"
    if not _is_uint(proof.get("configured_sink_receipt_count")):
        return None, "configured_sink_receipt_count_unsafe"
    return proof, ""


def _input_error(reviewer_agent_id: str, handoff_ref: str) -> str:
    if not isinstance(reviewer_agent_id, str) or not AGENT_RE.fullmatch(
        reviewer_agent_id
    ):
        return "reviewer_agent_unsafe"
    if not isinstance(handoff_ref, str) or not SAFE_REF_RE.fullmatch(handoff_ref):
        return "handoff_ref_unsafe"
    return ""


def _int(value: Any) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _is_uint(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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


def _assert_no_forbidden(text: str) -> None:
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in FORBIDDEN_MARKERS):
        raise ValueError("forbidden output marker")


def _failure(reason: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "proof_id": PROOF_ID,
        "summary_version": SUMMARY_VERSION,
        "ok": False,
        "template_only": True,
        "claim_safe_unchanged": True,
        "manual_review_required": True,
        "blockers": [f"runtime_receipt_sink_reviewer_summary_failed:{reason}"],
        "warnings": [],
    }
    for field in AUTHORITY_FALSE_FIELDS:
        summary[field] = False
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
