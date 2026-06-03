#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Render a path-free summary for a route-depth capture-window verifier run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.future_scale_contract_safety import validate_scalar_safety  # noqa: E402
from tools.run_future_scale_route_depth_benchmark import (  # noqa: E402
    ALLOWED_CAPTURE_SOURCE_KINDS,
    JSON_ARTIFACT_NAME,
    PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_NAME,
    PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_SCHEMA_VERSION,
    PRODUCTION_CAPTURE_WINDOW_SCHEMA_VERSION,
    SAFE_FALSE_FIELDS,
    validate_benchmark_report,
)


SUMMARY_SCHEMA_VERSION = (
    "future_scale_route_depth_capture_window_verification_summary.v1"
)
SUMMARY_STATUS_READY = "operator_capture_window_verification_summary_ready"
SUMMARY_STATUS_BLOCKED = "operator_capture_window_verification_summary_blocked"
SUMMARY_FALSE_FIELDS = SAFE_FALSE_FIELDS + (
    "artifact_payloads_included",
    "local_paths_recorded",
    "raw_payload_included",
    "query_text_included",
    "transport_added",
    "external_fetch_performed",
    "bridge_write_performed",
    "live_production_export_claimed_by_tool",
)


class CaptureWindowSummaryError(ValueError):
    """Raised when summary inputs cannot be read safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-json", required=True, type=Path)
    parser.add_argument("--capture-attachment-json", required=True, type=Path)
    parser.add_argument("--min-capture-windows", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.min_capture_windows < 0:
            raise CaptureWindowSummaryError("min_capture_windows_negative")
        benchmark_bytes, benchmark_report = _load_json_artifact(
            args.benchmark_json,
            "benchmark_json",
        )
        attachment_bytes, capture_attachment = _load_json_artifact(
            args.capture_attachment_json,
            "capture_attachment_json",
        )
        summary = build_capture_window_verification_summary(
            benchmark_report=benchmark_report,
            capture_attachment=capture_attachment,
            benchmark_bytes=benchmark_bytes,
            capture_attachment_bytes=attachment_bytes,
            min_capture_windows=args.min_capture_windows,
        )
    except CaptureWindowSummaryError as exc:
        summary = _summary_from_blockers([exc.code])
    except ValueError:
        summary = _summary_from_blockers(["capture_window_summary_invalid_input"])

    output = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
    if args.json or summary["ok"]:
        print(output)
    else:
        print(
            "Route-depth capture-window verification summary FAILED: "
            + ", ".join(summary["blockers"]),
            file=sys.stderr,
        )
    return 0 if summary["ok"] else 1


def build_capture_window_verification_summary(
    *,
    benchmark_report: Mapping[str, Any],
    capture_attachment: Mapping[str, Any],
    benchmark_bytes: bytes | None = None,
    capture_attachment_bytes: bytes | None = None,
    min_capture_windows: int = 1,
) -> dict[str, Any]:
    """Summarize a verified operator-owned capture-window attachment.

    The function does not trust the input path or payload identity. It only
    reports whether the provided artifacts satisfy the already-versioned
    route-depth verifier contract and keeps all claim gates false.
    """

    if min_capture_windows < 0:
        raise ValueError("min_capture_windows_negative")

    blockers: list[str] = []
    benchmark_errors = validate_benchmark_report(dict(benchmark_report))
    if benchmark_errors:
        blockers.append("benchmark_report_contract_invalid")
    if benchmark_report.get("ok") is not True:
        blockers.append("benchmark_report_not_ok")

    embedded_attachment = benchmark_report.get(
        "production_route_depth_capture_window_attachment"
    )
    if not isinstance(embedded_attachment, Mapping):
        blockers.append("benchmark_report_capture_attachment_missing")
        embedded_attachment = {}
    if dict(embedded_attachment) != dict(capture_attachment):
        blockers.append("capture_attachment_mismatch")

    _collect_capture_attachment_blockers(
        capture_attachment,
        min_capture_windows=min_capture_windows,
        blockers=blockers,
    )

    capture_windows = _list_of_mappings(capture_attachment.get("capture_windows"))
    capture_window_ids = _safe_window_ids(capture_windows)
    capture_window_digests = _safe_window_digests(capture_windows)
    source_kinds = sorted(
        {
            str(window.get("source_kind"))
            for window in capture_windows
            if window.get("source_kind") in ALLOWED_CAPTURE_SOURCE_KINDS
        }
    )
    summary = _base_summary()
    summary.update(
        {
            "ok": not blockers,
            "status": SUMMARY_STATUS_READY if not blockers else SUMMARY_STATUS_BLOCKED,
            "blockers": blockers,
            "benchmark_report_digest_sha256": _artifact_digest(
                benchmark_bytes,
                benchmark_report,
            ),
            "capture_attachment_file_digest_sha256": _artifact_digest(
                capture_attachment_bytes,
                capture_attachment,
            ),
            "capture_attachment_schema_version": capture_attachment.get(
                "schema_version"
            ),
            "capture_window_schema_version": capture_attachment.get(
                "capture_window_schema_version"
            ),
            "capture_attachment_status": capture_attachment.get(
                "attachment_status"
            ),
            "capture_attachment_digest_sha256": capture_attachment.get(
                "attachment_digest_sha256"
            ),
            "capture_window_count": capture_attachment.get("capture_window_count"),
            "capture_window_ids": capture_window_ids,
            "capture_window_digest_sha256s": capture_window_digests,
            "source_kinds": source_kinds,
            "min_capture_windows": min_capture_windows,
            "production_runtime_data_attached": capture_attachment.get(
                "production_runtime_data_attached"
            )
            is True,
            "operator_owned_capture_window_contract_verified": not blockers,
            "operator_owned_input_attested_by_payload": bool(capture_windows),
            "benchmark_validation_error_count": len(benchmark_errors),
            "required_artifact_names": [
                JSON_ARTIFACT_NAME,
                PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_NAME,
            ],
            "safe_conclusion": (
                "A local route-depth capture-window attachment satisfied the "
                "verifier contract."
                if not blockers
                else "A local route-depth capture-window attachment did not "
                "satisfy the verifier contract."
            )
            + " This summary records no input paths or raw payloads, performs "
            "no endpoint fetches, grants no runtime authority, and does not "
            "upgrade future-scale claim gates.",
        }
    )
    scalar_errors = validate_scalar_safety(summary)
    if scalar_errors:
        summary["ok"] = False
        summary["status"] = SUMMARY_STATUS_BLOCKED
        summary["blockers"] = list(summary["blockers"]) + [
            "summary_scalar_safety_failed"
        ]
        summary["operator_owned_capture_window_contract_verified"] = False
    return summary


def _collect_capture_attachment_blockers(
    capture_attachment: Mapping[str, Any],
    *,
    min_capture_windows: int,
    blockers: list[str],
) -> None:
    if (
        capture_attachment.get("schema_version")
        != PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_SCHEMA_VERSION
    ):
        blockers.append("capture_attachment_schema_version_mismatch")
    if (
        capture_attachment.get("capture_window_schema_version")
        != PRODUCTION_CAPTURE_WINDOW_SCHEMA_VERSION
    ):
        blockers.append("capture_window_schema_version_mismatch")
    if capture_attachment.get("attachment_status") != "operator_capture_window_attached":
        blockers.append("capture_attachment_not_operator_attached")
    if capture_attachment.get("production_runtime_data_attached") is not True:
        blockers.append("production_runtime_data_not_attached")
    if capture_attachment.get("network_access") != "not_used":
        blockers.append("capture_attachment_network_access_not_allowed")
    if capture_attachment.get("cloud_api_calls") != 0:
        blockers.append("capture_attachment_cloud_api_calls_not_zero")
    for field in SAFE_FALSE_FIELDS:
        if capture_attachment.get(field) is not False:
            blockers.append(f"capture_attachment_{field}_not_false")

    capture_windows = _list_of_mappings(capture_attachment.get("capture_windows"))
    count = capture_attachment.get("capture_window_count")
    if not isinstance(count, int) or isinstance(count, bool):
        blockers.append("capture_window_count_not_int")
    elif count != len(capture_windows):
        blockers.append("capture_window_count_mismatch")
    elif count < min_capture_windows:
        blockers.append("capture_window_count_insufficient")

    for index, window in enumerate(capture_windows):
        prefix = f"capture_window_{index}"
        if window.get("source_kind") not in ALLOWED_CAPTURE_SOURCE_KINDS:
            blockers.append(f"{prefix}_source_kind_mismatch")
        if window.get("operator_owned_export") is not True:
            blockers.append(f"{prefix}_operator_owned_export_not_true")
        for field in (
            "raw_payload_included",
            "query_text_included",
            "local_paths_recorded",
        ):
            if window.get(field) is not False:
                blockers.append(f"{prefix}_{field}_not_false")
        if window.get("network_access") != "not_used":
            blockers.append(f"{prefix}_network_access_not_allowed")
        if window.get("cloud_api_calls") != 0:
            blockers.append(f"{prefix}_cloud_api_calls_not_zero")


def _base_summary() -> dict[str, Any]:
    summary = {
        "summary_schema_version": SUMMARY_SCHEMA_VERSION,
        "summary_kind": "route_depth_capture_window_verification_summary",
        "measurement_scope": (
            "path-free reviewer summary over local route-depth capture-window "
            "verifier artifacts"
        ),
        "ok": False,
        "status": SUMMARY_STATUS_BLOCKED,
        "blockers": [],
        "claim_gate_satisfied": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "required_runtime_evidence_present": False,
        "runtime_authority_changed": False,
        "runtime_authority_granted": False,
        "controls_present": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "raw_payload_included": False,
        "query_text_included": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "bridge_write_performed": False,
        "live_production_export_claimed_by_tool": False,
        "network_access": "not_used",
        "cloud_api_calls": 0,
    }
    for field in SUMMARY_FALSE_FIELDS:
        summary[field] = False
    return summary


def _summary_from_blockers(blockers: Sequence[str]) -> dict[str, Any]:
    summary = _base_summary()
    summary["blockers"] = list(blockers)
    summary["safe_conclusion"] = (
        "The route-depth capture-window verification summary failed closed "
        "without recording input paths, raw payloads, or runtime authority."
    )
    return summary


def _load_json_artifact(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CaptureWindowSummaryError(f"{label}_unreadable") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise CaptureWindowSummaryError(f"{label}_not_utf8") from exc
    except json.JSONDecodeError as exc:
        raise CaptureWindowSummaryError(f"{label}_decode_error") from exc
    if not isinstance(parsed, dict):
        raise CaptureWindowSummaryError(f"{label}_not_object")
    return raw, parsed


def _artifact_digest(raw: bytes | None, value: Mapping[str, Any]) -> str:
    if raw is not None:
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _safe_window_ids(windows: Sequence[Mapping[str, Any]]) -> list[str]:
    ids: list[str] = []
    for window in windows:
        window_id = window.get("capture_window_id")
        if isinstance(window_id, str):
            ids.append(window_id)
    return ids


def _safe_window_digests(windows: Sequence[Mapping[str, Any]]) -> list[str]:
    digests: list[str] = []
    for window in windows:
        digest = window.get("window_digest_sha256")
        if isinstance(digest, str):
            digests.append(digest)
    return digests


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
