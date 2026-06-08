# SPDX-License-Identifier: BUSL-1.1
"""Build a path-free summary for Memory Palace visualization-export verification.

The summary consumes an already-built verification report from
``verify_memory_palace_visualization_export`` and emits only review-safe status,
check, count, warning, and blocker fields. It does not append bridge events,
include source payloads, record local paths, dispatch solvers, enqueue
schedulers, change routes, or grant runtime, promotion, storage, bridge, or
gate-skip authority.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_memory_palace_visualization_export import (  # noqa: E402
    VERIFICATION_VERSION as SOURCE_VERIFICATION_VERSION,
)


SUMMARY_VERSION = "wd.v12.memory_palace_visualization_export.verification_summary.v0"
VERIFICATION_ARTIFACT_ID = "memory_palace_visualization_export_verification"

_CHECK_FIELDS = (
    "source_export_version_check",
    "source_claim_label_check",
    "source_of_truth_check",
    "source_projection_schema_version_check",
    "layout_check",
    "graph_check",
    "aggregate_check",
    "authority_boundary_check",
    "guardrail_check",
    "path_free_check",
)
_COUNT_FIELDS = (
    "node_count_checked",
    "edge_count_checked",
    "shortcut_edge_count_checked",
)
_REQUIRED_TRUE_FIELDS = (
    "read_side_report_only",
    "manual_review_required",
)
_REQUIRED_FALSE_FIELDS = (
    "runtime_route_changed",
    "storage_write_performed",
    "bridge_append_performed",
    "solver_call_performed",
    "scheduler_enqueue_performed",
    "promotion_performed",
    "gate_skip_performed",
    "network_access_performed",
    "runtime_authority_granted",
    "artifact_payloads_included",
    "local_paths_recorded",
)
_ALLOWED_VERIFICATION_FIELDS = frozenset(
    {
        "ok",
        "verification_version",
        "source_export_ok",
        "blockers",
        "warnings",
        *_CHECK_FIELDS,
        *_COUNT_FIELDS,
        *_REQUIRED_TRUE_FIELDS,
        *_REQUIRED_FALSE_FIELDS,
    }
)
_PATH_MARKER_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|file://|"
    r"(?<![:/])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]*)"
)
_SAFE_CODE_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,180}$")


class VisualizationExportVerificationSummaryError(ValueError):
    """Raised when a verification summary input cannot be safely loaded."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verification-json",
        required=True,
        type=Path,
        help="Path to a JSON verification report.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        verification = _load_json_report(args.verification_json)
        summary = build_memory_palace_visualization_export_verification_summary(
            verification,
        )
    except VisualizationExportVerificationSummaryError as exc:
        summary = _failure_summary(exc.code)

    encoded = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
    if args.json or summary["ok"]:
        print(encoded)
    else:
        print(
            "Memory Palace visualization export verification summary FAILED: "
            + ", ".join(summary["blockers"]),
            file=sys.stderr,
        )
    return 0 if summary["ok"] else 1


def build_memory_palace_visualization_export_verification_summary(
    verification: Any,
) -> dict[str, Any]:
    """Return a path-free, authority-free summary for a verification report."""

    if not isinstance(verification, Mapping):
        return _failure_summary(f"{VERIFICATION_ARTIFACT_ID}_not_object")

    blockers: list[str] = []
    if _contains_non_finite(verification):
        blockers.append("verification_contains_non_finite")
    if _contains_path_marker(verification):
        blockers.append("verification_contains_path_marker")
    if set(verification) - _ALLOWED_VERIFICATION_FIELDS:
        blockers.append("verification_unexpected_field_present")

    source_version_ok = (
        verification.get("verification_version") == SOURCE_VERIFICATION_VERSION
    )
    if not source_version_ok:
        blockers.append("verification_version_mismatch")
    if verification.get("ok") is not True:
        blockers.append("source_verification_not_ok")

    checks = _safe_checks(verification, blockers)
    counts = _safe_counts(verification, blockers)
    true_flags = _safe_true_flags(verification, blockers)
    false_flags = _safe_false_flags(verification, blockers)

    source_blockers = _safe_code_list(verification.get("blockers"), "blocker")
    if source_blockers:
        blockers.append("source_verification_blockers_present")
    source_warnings = _safe_code_list(verification.get("warnings"), "warning")

    summary_blockers = sorted(set(blockers + source_blockers))
    summary = {
        "summary_version": SUMMARY_VERSION,
        "ok": not summary_blockers,
        "source_verification_version": (
            SOURCE_VERIFICATION_VERSION if source_version_ok else ""
        ),
        "source_verification_ok": verification.get("ok") is True,
        "checks": checks,
        "counts": counts,
        "required_true_flags": true_flags,
        "authority_boundary": false_flags,
        "blocker_count": len(source_blockers),
        "warning_count": len(source_warnings),
        "read_side_report_only": True,
        "manual_review_required": True,
        "runtime_route_changed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "solver_call_performed": False,
        "scheduler_enqueue_performed": False,
        "promotion_performed": False,
        "gate_skip_performed": False,
        "network_access_performed": False,
        "runtime_authority_granted": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": summary_blockers,
        "warnings": source_warnings,
        "operator_interpretation": (
            "This is a path-free summary of a local verification report for "
            "Memory Palace visualization export. It is not a release decision "
            "and grants no runtime authority."
        ),
    }
    json.dumps(summary, sort_keys=True, allow_nan=False)
    return summary


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Memory Palace Visualization Export Verification Summary",
        "",
        f"- summary_version: `{report['summary_version']}`",
        f"- ok: `{str(report['ok']).lower()}`",
        f"- source_verification_ok: `{str(report['source_verification_ok']).lower()}`",
        f"- blocker_count: `{report['blocker_count']}`",
        f"- warning_count: `{report['warning_count']}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in sorted(report["checks"].items()):
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Counts", ""])
    for key, value in sorted(report["counts"].items()):
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Authority Boundary", ""])
    for key, value in sorted(report["authority_boundary"].items()):
        lines.append(f"- {key}: `{str(value).lower()}`")
    lines.extend(["", "## Blockers", ""])
    if report["blockers"]:
        for blocker in report["blockers"]:
            lines.append(f"- `{blocker}`")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## What This Does Not Do",
            "",
            (
                "This does not dispatch a solver, enqueue scheduler work, "
                "append bridge events, write storage, skip a gate, promote a "
                "route, make a release decision, or grant runtime authority."
            ),
            "",
        ],
    )
    return "\n".join(lines)


def _safe_checks(
    verification: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, str]:
    checks: dict[str, str] = {}
    for field in _CHECK_FIELDS:
        value = verification.get(field)
        if value == "match":
            checks[field] = "match"
        else:
            checks[field] = "mismatch"
            blockers.append(f"{field}_not_match")
    return checks


def _safe_counts(
    verification: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for field in _COUNT_FIELDS:
        value = verification.get(field)
        if _nonnegative_int(value):
            counts[field] = int(value)
        else:
            counts[field] = 0
            blockers.append(f"{field}_not_nonnegative_int")
    return counts


def _safe_true_flags(
    verification: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for field in _REQUIRED_TRUE_FIELDS:
        value = verification.get(field)
        flags[field] = value is True
        if value is not True:
            blockers.append(f"{field}_not_true")
    return flags


def _safe_false_flags(
    verification: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for field in _REQUIRED_FALSE_FIELDS:
        value = verification.get(field)
        flags[field] = False
        if value is not False:
            blockers.append(f"{field}_not_false")
    return flags


def _load_json_report(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except OSError as exc:
        raise VisualizationExportVerificationSummaryError(
            f"{VERIFICATION_ARTIFACT_ID}_unreadable",
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise VisualizationExportVerificationSummaryError(
            f"{VERIFICATION_ARTIFACT_ID}_json_error",
        ) from exc


def _reject_duplicate_json_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid_json_constant:{value}")


def _failure_summary(code: str) -> dict[str, Any]:
    safe_code = _safe_code(code)
    return {
        "summary_version": SUMMARY_VERSION,
        "ok": False,
        "source_verification_version": "",
        "source_verification_ok": False,
        "checks": {field: "not_checked" for field in _CHECK_FIELDS},
        "counts": {field: 0 for field in _COUNT_FIELDS},
        "required_true_flags": {field: True for field in _REQUIRED_TRUE_FIELDS},
        "authority_boundary": {field: False for field in _REQUIRED_FALSE_FIELDS},
        "blocker_count": 1,
        "warning_count": 0,
        "read_side_report_only": True,
        "manual_review_required": True,
        "runtime_route_changed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "solver_call_performed": False,
        "scheduler_enqueue_performed": False,
        "promotion_performed": False,
        "gate_skip_performed": False,
        "network_access_performed": False,
        "runtime_authority_granted": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [f"{VERIFICATION_ARTIFACT_ID}_summary_failed:{safe_code}"],
        "warnings": [],
        "operator_interpretation": (
            "The Memory Palace visualization export verification summary "
            "failed closed and grants no runtime authority."
        ),
    }


def _contains_non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(_contains_non_finite(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite(child) for child in value)
    return False


def _contains_path_marker(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_PATH_MARKER_RE.search(value))
    if isinstance(value, Mapping):
        return any(
            _contains_path_marker(str(key)) or _contains_path_marker(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_path_marker(child) for child in value)
    return False


def _safe_code_list(value: Any, label: str) -> list[str]:
    if value in (None, []):
        return []
    if not isinstance(value, list):
        return [f"source_{label}s_not_list"]
    safe: list[str] = []
    unsafe_seen = False
    for item in value:
        if isinstance(item, str) and _SAFE_CODE_RE.fullmatch(item):
            safe.append(item)
        else:
            unsafe_seen = True
    if unsafe_seen:
        safe.append(f"unsafe_{label}_redacted")
    return sorted(set(safe))


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _safe_code(value: str) -> str:
    cleaned = []
    for char in str(value).strip()[:180]:
        if char.isalnum() or char in "_.:-[]":
            cleaned.append(char)
        elif char.isspace():
            cleaned.append("_")
        else:
            cleaned.append("_")
    return "".join(cleaned).strip("_") or "invalid"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
