# SPDX-License-Identifier: BUSL-1.1
"""Build a path-free summary for Memory Palace runtime-design verification.

The summary consumes an already-built verification report and emits only
review-safe status, check, count, warning, and blocker fields. It does not
append bridge events, include artifact payloads, record local paths, dispatch
solvers, enqueue schedulers, change routes, or grant promotion authority.
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

from tools.verify_v12_memory_palace_shortcut_runtime_promotion_design import (  # noqa: E402
    VERIFICATION_VERSION as SOURCE_VERIFICATION_VERSION,
)


SUMMARY_VERSION = (
    "wd.v12.memory_palace_shortcut_runtime_promotion_design."
    "verification_summary.v0"
)
VERIFICATION_ARTIFACT_ID = (
    "memory_palace_shortcut_runtime_promotion_design_verification"
)

_CHECK_FIELDS = (
    "source_report_version_check",
    "source_claim_label_check",
    "authority_boundary_check",
    "guardrail_check",
    "design_row_action_boundary_check",
)
_REQUIRED_TRUE_FIELDS = (
    "template_only",
    "design_only",
    "read_side_report_only",
    "manual_review_required",
    "operator_gate_required_for_runtime_promotion",
)
_REQUIRED_FALSE_FIELDS = (
    "promotion_action_allowed",
    "promotion_performed",
    "runtime_route_changed",
    "storage_write_performed",
    "bridge_append_performed",
    "solver_call_performed",
    "scheduler_enqueue_performed",
    "gate_skip_performed",
    "network_access_performed",
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "external_writes_applied",
    "controls_present",
    "runtime_authority_granted",
    "artifact_payloads_included",
    "local_paths_recorded",
)
_SAFE_CODE_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,160}$")


class VerificationSummaryError(ValueError):
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
        summary = build_memory_palace_shortcut_runtime_promotion_design_verification_summary(
            verification,
        )
    except VerificationSummaryError as exc:
        summary = _failure_summary(exc.code)

    encoded = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
    if args.json or summary["ok"]:
        print(encoded)
    else:
        print(
            "Memory Palace shortcut runtime-promotion design verification "
            "summary FAILED: " + ", ".join(summary["blockers"]),
            file=sys.stderr,
        )
    return 0 if summary["ok"] else 1


def build_memory_palace_shortcut_runtime_promotion_design_verification_summary(
    verification: Any,
) -> dict[str, Any]:
    """Return a path-free, authority-free summary for a verification report."""

    if not isinstance(verification, Mapping):
        return _failure_summary(f"{VERIFICATION_ARTIFACT_ID}_not_object")

    blockers: list[str] = []
    if _contains_non_finite(verification):
        blockers.append("verification_contains_non_finite")

    source_version_ok = (
        verification.get("verification_version") == SOURCE_VERIFICATION_VERSION
    )
    if not source_version_ok:
        blockers.append("verification_version_mismatch")
    if verification.get("ok") is not True:
        blockers.append("source_verification_not_ok")

    checks = _safe_checks(verification, blockers)
    true_flags = _safe_true_flags(verification, blockers)
    false_flags = _safe_false_flags(verification, blockers)
    design_count = _safe_design_count(verification, blockers)

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
        "runtime_promotion_design_count_checked": design_count,
        "checks": checks,
        "required_true_flags": true_flags,
        "authority_boundary": false_flags,
        "template_only": True,
        "design_only": True,
        "read_side_report_only": True,
        "manual_review_required": True,
        "operator_gate_required_for_runtime_promotion": True,
        "promotion_action_allowed": False,
        "promotion_performed": False,
        "runtime_route_changed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "solver_call_performed": False,
        "scheduler_enqueue_performed": False,
        "gate_skip_performed": False,
        "network_access_performed": False,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "external_writes_applied": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": summary_blockers,
        "warnings": source_warnings,
        "operator_interpretation": (
            "This is a path-free summary of a local verification report for "
            "operator-gated Memory Palace shortcut runtime-promotion design. "
            "It is not a release decision and grants no runtime authority."
        ),
    }
    json.dumps(summary, sort_keys=True, allow_nan=False)
    return summary


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# V12 Memory Palace Runtime-Promotion Design Verification Summary",
        "",
        f"- summary_version: `{report['summary_version']}`",
        f"- ok: `{str(report['ok']).lower()}`",
        f"- source_verification_ok: `{str(report['source_verification_ok']).lower()}`",
        (
            "- runtime_promotion_design_count_checked: `"
            + str(report["runtime_promotion_design_count_checked"])
            + "`"
        ),
        "",
        "## Checks",
        "",
    ]
    for key, value in sorted(report["checks"].items()):
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
                "This does not promote a route, dispatch a solver, enqueue "
                "scheduler work, append bridge events, write storage, skip a "
                "gate, grant approval, make a release decision, or grant "
                "runtime authority."
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


def _safe_true_flags(
    verification: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for field in _REQUIRED_TRUE_FIELDS:
        ok = verification.get(field) is True
        flags[field] = ok
        if not ok:
            blockers.append(f"{field}_not_true")
    return flags


def _safe_false_flags(
    verification: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for field in _REQUIRED_FALSE_FIELDS:
        safe = verification.get(field) is False
        flags[field] = False
        if not safe:
            blockers.append(f"{field}_not_false")
    return flags


def _safe_design_count(
    verification: Mapping[str, Any],
    blockers: list[str],
) -> int:
    count = verification.get("runtime_promotion_design_count_checked")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        blockers.append("runtime_promotion_design_count_checked_invalid")
        return 0
    return count


def _safe_code_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        return []
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


def _contains_non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(_contains_non_finite(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite(item) for item in value)
    return False


def _load_json_report(path: Path) -> Mapping[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VerificationSummaryError(
            f"{VERIFICATION_ARTIFACT_ID}_unreadable",
        ) from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VerificationSummaryError(
            f"{VERIFICATION_ARTIFACT_ID}_json_error",
        ) from exc
    if not isinstance(payload, Mapping):
        raise VerificationSummaryError(f"{VERIFICATION_ARTIFACT_ID}_not_object")
    if _contains_non_finite(payload):
        raise VerificationSummaryError(
            f"{VERIFICATION_ARTIFACT_ID}_non_finite",
        )
    return payload


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> None:
    raise ValueError(f"non_finite_json_constant:{value}")


def _failure_summary(code: str) -> dict[str, Any]:
    return {
        "summary_version": SUMMARY_VERSION,
        "ok": False,
        "source_verification_version": "",
        "source_verification_ok": False,
        "runtime_promotion_design_count_checked": 0,
        "checks": {field: "mismatch" for field in _CHECK_FIELDS},
        "required_true_flags": {field: False for field in _REQUIRED_TRUE_FIELDS},
        "authority_boundary": {field: False for field in _REQUIRED_FALSE_FIELDS},
        "template_only": True,
        "design_only": True,
        "read_side_report_only": True,
        "manual_review_required": True,
        "operator_gate_required_for_runtime_promotion": True,
        "promotion_action_allowed": False,
        "promotion_performed": False,
        "runtime_route_changed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "solver_call_performed": False,
        "scheduler_enqueue_performed": False,
        "gate_skip_performed": False,
        "network_access_performed": False,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "external_writes_applied": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [f"{VERIFICATION_ARTIFACT_ID}_summary_failed:{code}"],
        "warnings": [],
        "operator_interpretation": (
            "The verification summary input could not be safely loaded. "
            "No runtime authority is granted."
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
