# SPDX-License-Identifier: BUSL-1.1
"""Verify a V12 Memory Palace shortcut promotion-candidate report.

The verifier is intentionally read-only. It checks that a generated promotion
candidate report remains an observation-only artifact and does not grant route
promotion, bridge, scheduler, solver, storage, gate-skip, or runtime authority.
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

from tools.run_v12_memory_palace_shortcut_promotion_candidates import (  # noqa: E402
    CLAIM_LABEL as SOURCE_CLAIM_LABEL,
    REPORT_VERSION as SOURCE_REPORT_VERSION,
)


VERIFICATION_VERSION = (
    "wd.v12.memory_palace_shortcut_promotion_candidates.verification.v0"
)
REPORT_ARTIFACT_ID = "memory_palace_shortcut_promotion_candidates_report"

_BOUNDARY_TRUE_FIELDS = (
    "source_proof_ok",
    "source_authority_boundary_ok",
    "read_side_report_only",
    "all_candidates_authority_flags_false",
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
)
_GUARDRAIL_TRUE_FIELDS = (
    "not_router_dispatch",
    "not_solver_call",
    "not_storage_write",
    "not_bridge_append",
    "not_scheduler_enqueue",
    "not_gate_skip",
    "not_promotion_action",
    "operator_gate_required_for_runtime_promotion",
    "deterministic_local_fixture",
)
_CANDIDATE_FALSE_FIELDS = (
    "promotion_action_allowed",
    "runtime_route_changed",
    "storage_write_performed",
    "bridge_append_performed",
    "solver_call_performed",
    "scheduler_enqueue_performed",
    "gate_skip_performed",
    "promotion_performed",
)


class PromotionCandidateVerificationError(ValueError):
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
        help="Path to a JSON report emitted by the promotion-candidate tool.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = _load_json_report(args.report_json)
        verification = verify_memory_palace_shortcut_promotion_candidate_report(
            report,
        )
    except PromotionCandidateVerificationError as exc:
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
            "Memory Palace shortcut promotion-candidate verification FAILED: "
            + ", ".join(verification["blockers"]),
            file=sys.stderr,
        )
    return 0 if verification["ok"] else 1


def verify_memory_palace_shortcut_promotion_candidate_report(
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an action-free verification summary for a report mapping."""

    if not isinstance(report, Mapping):
        raise PromotionCandidateVerificationError(
            f"{REPORT_ARTIFACT_ID}_not_object",
        )

    blockers: list[str] = []
    _collect_header_blockers(report, blockers)
    _collect_threshold_blockers(report, blockers)
    _collect_summary_blockers(report, blockers)
    boundary_check = _collect_authority_boundary_blockers(report, blockers)
    guardrail_check = _collect_guardrail_blockers(report, blockers)
    candidate_check = _collect_candidate_blockers(report, blockers)

    candidates = report.get("promotion_candidates")
    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    observable_count = _observable_count(candidates)
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
        "candidate_count_checked": candidate_count,
        "promotion_observable_count_checked": observable_count,
        "authority_boundary_check": boundary_check,
        "guardrail_check": guardrail_check,
        "candidate_action_boundary_check": candidate_check,
        "template_only": True,
        "read_side_report_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
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
    if report.get("source_of_truth") != "projection_only":
        blockers.append("source_of_truth_not_projection_only")


def _collect_threshold_blockers(
    report: Mapping[str, Any],
    blockers: list[str],
) -> None:
    thresholds = _mapping(report.get("thresholds"))
    min_rank_score = thresholds.get("min_rank_score")
    min_hops = thresholds.get("min_intermediate_hops_skipped")
    if (
        not isinstance(min_rank_score, (int, float))
        or isinstance(min_rank_score, bool)
        or not (0.0 <= float(min_rank_score) <= 1.0)
    ):
        blockers.append("min_rank_score_invalid")
    if (
        not isinstance(min_hops, int)
        or isinstance(min_hops, bool)
        or min_hops < 0
    ):
        blockers.append("min_intermediate_hops_skipped_invalid")


def _collect_summary_blockers(
    report: Mapping[str, Any],
    blockers: list[str],
) -> None:
    candidates = report.get("promotion_candidates")
    summary = _mapping(report.get("candidate_summary"))
    if not isinstance(candidates, list):
        blockers.append("promotion_candidates_not_list")
        return
    observable_count = _observable_count(candidates)
    source_count = len(candidates)
    blocked_count = source_count - observable_count
    if summary.get("source_candidate_count") != source_count:
        blockers.append("source_candidate_count_mismatch")
    if summary.get("promotion_observable_count") != observable_count:
        blockers.append("promotion_observable_count_mismatch")
    if summary.get("blocked_count") != blocked_count:
        blockers.append("blocked_count_mismatch")
    expected_top = _expected_top_candidate_target(candidates)
    if summary.get("top_candidate_target") != expected_top:
        blockers.append("top_candidate_target_mismatch")


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


def _collect_candidate_blockers(
    report: Mapping[str, Any],
    blockers: list[str],
) -> str:
    candidates = report.get("promotion_candidates")
    before = len(blockers)
    if not isinstance(candidates, list):
        return "mismatch"
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            blockers.append(f"candidate_{index}_not_object")
            continue
        if candidate.get("authority_flags_false") is not True:
            blockers.append(f"candidate_{index}_authority_flags_false_not_true")
        if candidate.get("promotion_observable") is True:
            if (
                candidate.get("candidate_status")
                != "observable_pending_operator_design"
            ):
                blockers.append(f"candidate_{index}_status_mismatch")
        for field in _CANDIDATE_FALSE_FIELDS:
            if candidate.get(field) is not False:
                blockers.append(f"candidate_{index}_{field}_not_false")
    return "match" if len(blockers) == before else "mismatch"


def _load_json_report(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PromotionCandidateVerificationError(
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
        raise PromotionCandidateVerificationError(
            f"{REPORT_ARTIFACT_ID}_decode_error",
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise PromotionCandidateVerificationError(
            f"{REPORT_ARTIFACT_ID}_json_error",
        ) from exc
    if not isinstance(parsed, Mapping):
        raise PromotionCandidateVerificationError(
            f"{REPORT_ARTIFACT_ID}_not_object",
        )
    return parsed


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "verification_version": VERIFICATION_VERSION,
        "template_only": True,
        "read_side_report_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
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
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "external_writes_applied": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "memory_palace_shortcut_promotion_candidates_verification_failed:"
            f"{reason}"
        ],
        "warnings": [],
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _observable_count(candidates: Any) -> int:
    if not isinstance(candidates, list):
        return 0
    return sum(
        1
        for candidate in candidates
        if isinstance(candidate, Mapping)
        and candidate.get("promotion_observable") is True
    )


def _expected_top_candidate_target(candidates: Sequence[Any]) -> str:
    for candidate in candidates:
        if (
            isinstance(candidate, Mapping)
            and candidate.get("promotion_observable") is True
        ):
            target = candidate.get("target_node_id")
            return target if isinstance(target, str) else "none"
    return "none"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
