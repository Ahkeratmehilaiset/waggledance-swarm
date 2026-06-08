# SPDX-License-Identifier: BUSL-1.1
"""Verify a V12 Memory Palace shortcut runtime-promotion design report.

The verifier is read-only and path-free. It checks that design rows remain
operator-gated preflight observations and do not grant route, solver,
scheduler, bridge, storage, release, gate-skip, promotion, or runtime
authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_v12_memory_palace_shortcut_runtime_promotion_design import (  # noqa: E402
    CLAIM_LABEL as SOURCE_CLAIM_LABEL,
    REPORT_VERSION as SOURCE_REPORT_VERSION,
)


VERIFICATION_VERSION = (
    "wd.v12.memory_palace_shortcut_runtime_promotion_design.verification.v0"
)
REPORT_ARTIFACT_ID = "memory_palace_shortcut_runtime_promotion_design_report"

_BOUNDARY_TRUE_FIELDS = (
    "source_candidate_report_ok",
    "source_candidate_verification_ok",
    "design_only",
    "manual_review_required",
    "operator_gate_required_for_runtime_promotion",
    "all_design_rows_action_free",
)
_BOUNDARY_FALSE_FIELDS = (
    "runtime_route_changed",
    "storage_write_performed",
    "bridge_append_performed",
    "solver_call_performed",
    "scheduler_enqueue_performed",
    "promotion_performed",
    "promotion_action_allowed",
    "gate_skip_performed",
    "network_access_performed",
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
)
_GUARDRAIL_TRUE_FIELDS = (
    "design_only",
    "manual_review_required",
    "operator_gate_required_for_runtime_promotion",
    "not_router_dispatch",
    "not_solver_call",
    "not_storage_write",
    "not_bridge_append",
    "not_scheduler_enqueue",
    "not_gate_skip",
    "not_promotion_action",
    "not_networked_retrieval",
    "source_verification_required",
    "deterministic_local_fixture",
)
_DESIGN_TRUE_FIELDS = (
    "operator_gate_required",
    "manual_review_required",
)


class RuntimePromotionDesignVerificationError(ValueError):
    """Raised when verifier inputs cannot be safely loaded."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-json",
        required=True,
        type=Path,
        help="Path to a JSON report emitted by the runtime-design tool.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = _load_json_report(args.report_json)
        verification = verify_memory_palace_shortcut_runtime_promotion_design(
            report,
        )
    except RuntimePromotionDesignVerificationError as exc:
        verification = _failure_report(exc.code)

    encoded = json.dumps(
        verification,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    if args.json or verification["ok"]:
        print(encoded)
    else:
        print(
            "Memory Palace shortcut runtime-promotion design verification "
            "FAILED: " + ", ".join(verification["blockers"]),
            file=sys.stderr,
        )
    return 0 if verification["ok"] else 1


def verify_memory_palace_shortcut_runtime_promotion_design(
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an action-free verification summary for a design report."""

    if not isinstance(report, Mapping):
        raise RuntimePromotionDesignVerificationError(
            f"{REPORT_ARTIFACT_ID}_not_object",
        )

    blockers: list[str] = []
    _collect_header_blockers(report, blockers)
    _collect_summary_blockers(report, blockers)
    boundary_check = _collect_authority_boundary_blockers(report, blockers)
    guardrail_check = _collect_guardrail_blockers(report, blockers)
    design_check = _collect_design_row_blockers(report, blockers)

    designs = report.get("runtime_promotion_designs")
    design_count = len(designs) if isinstance(designs, list) else 0
    verification = {
        "ok": not blockers,
        "verification_version": VERIFICATION_VERSION,
        "source_report_version_check": (
            "match"
            if report.get("report_version") == SOURCE_REPORT_VERSION
            else "mismatch"
        ),
        "source_claim_label_check": (
            "match"
            if report.get("claim_label") == SOURCE_CLAIM_LABEL
            else "mismatch"
        ),
        "runtime_promotion_design_count_checked": design_count,
        "authority_boundary_check": boundary_check,
        "guardrail_check": guardrail_check,
        "design_row_action_boundary_check": design_check,
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
        "blockers": sorted(set(blockers)),
        "warnings": [],
    }
    json.dumps(verification, allow_nan=False, sort_keys=True)
    return verification


def _collect_header_blockers(
    report: Mapping[str, Any],
    blockers: list[str],
) -> None:
    if report.get("report_version") != SOURCE_REPORT_VERSION:
        blockers.append("report_version_mismatch")
    if report.get("claim_label") != SOURCE_CLAIM_LABEL:
        blockers.append("claim_label_mismatch")
    if report.get("ok") is not True:
        blockers.append("source_report_not_ok")
    if report.get("source_verification_ok") is not True:
        blockers.append("source_verification_not_ok")
    if report.get("source_of_truth") != "projection_only":
        blockers.append("source_of_truth_not_projection_only")


def _collect_summary_blockers(
    report: Mapping[str, Any],
    blockers: list[str],
) -> None:
    designs = report.get("runtime_promotion_designs")
    summary = _mapping(report.get("design_summary"))
    if not isinstance(designs, list):
        blockers.append("runtime_promotion_designs_not_list")
        return
    design_count = len(designs)
    if summary.get("runtime_promotion_design_count") != design_count:
        blockers.append("runtime_promotion_design_count_mismatch")
    expected_top = _expected_top_design_target(designs)
    if summary.get("top_design_target") != expected_top:
        blockers.append("top_design_target_mismatch")
    designable_count = summary.get("designable_source_candidate_count")
    if not isinstance(designable_count, int) or designable_count < design_count:
        blockers.append("designable_source_candidate_count_invalid")


def _collect_authority_boundary_blockers(
    report: Mapping[str, Any],
    blockers: list[str],
) -> str:
    boundary = _mapping(report.get("authority_boundary"))
    before = len(blockers)
    for field in _BOUNDARY_TRUE_FIELDS:
        if boundary.get(field) is not True:
            blockers.append(f"authority_boundary_{field}_not_true")
    for field in _BOUNDARY_FALSE_FIELDS:
        if boundary.get(field) is not False:
            blockers.append(f"authority_boundary_{field}_not_false")
    return "match" if len(blockers) == before else "mismatch"


def _collect_guardrail_blockers(
    report: Mapping[str, Any],
    blockers: list[str],
) -> str:
    guardrails = _mapping(report.get("no_overclaim_guardrails"))
    before = len(blockers)
    for field in _GUARDRAIL_TRUE_FIELDS:
        if guardrails.get(field) is not True:
            blockers.append(f"guardrail_{field}_not_true")
    return "match" if len(blockers) == before else "mismatch"


def _collect_design_row_blockers(
    report: Mapping[str, Any],
    blockers: list[str],
) -> str:
    designs = report.get("runtime_promotion_designs")
    before = len(blockers)
    if not isinstance(designs, list):
        return "mismatch"
    for index, design in enumerate(designs):
        if not isinstance(design, Mapping):
            blockers.append(f"runtime_design_{index}_not_object")
            continue
        if design.get("design_status") != (
            "operator_gate_required_before_runtime_promotion"
        ):
            blockers.append(f"runtime_design_{index}_status_mismatch")
        controls = design.get("required_operator_controls")
        if not isinstance(controls, list) or "operator_authorization" not in controls:
            blockers.append(f"runtime_design_{index}_operator_controls_missing")
        for field in _DESIGN_TRUE_FIELDS:
            if design.get(field) is not True:
                blockers.append(f"runtime_design_{index}_{field}_not_true")
        for field in _BOUNDARY_FALSE_FIELDS:
            if design.get(field) is not False:
                blockers.append(f"runtime_design_{index}_{field}_not_false")
    return "match" if len(blockers) == before else "mismatch"


def _load_json_report(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimePromotionDesignVerificationError(
            f"{REPORT_ARTIFACT_ID}_unreadable",
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
        raise RuntimePromotionDesignVerificationError(
            f"{REPORT_ARTIFACT_ID}_decode_error",
        ) from exc
    except ValueError as exc:
        raise RuntimePromotionDesignVerificationError(
            f"{REPORT_ARTIFACT_ID}_json_error",
        ) from exc
    if not isinstance(parsed, Mapping):
        raise RuntimePromotionDesignVerificationError(
            f"{REPORT_ARTIFACT_ID}_not_object",
        )
    return parsed


def _reject_duplicate_object(
    pairs: Sequence[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _failure_report(code: str) -> dict[str, Any]:
    return {
        "ok": False,
        "verification_version": VERIFICATION_VERSION,
        "source_report_version_check": "unavailable",
        "source_claim_label_check": "unavailable",
        "runtime_promotion_design_count_checked": 0,
        "authority_boundary_check": "unavailable",
        "guardrail_check": "unavailable",
        "design_row_action_boundary_check": "unavailable",
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
        "blockers": [f"{REPORT_ARTIFACT_ID}_verification_failed:{code}"],
        "warnings": [],
    }


def _expected_top_design_target(designs: Sequence[Any]) -> str:
    for design in designs:
        if isinstance(design, Mapping):
            target = design.get("target_node_id")
            if isinstance(target, str) and target:
                return target
    return "none"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
