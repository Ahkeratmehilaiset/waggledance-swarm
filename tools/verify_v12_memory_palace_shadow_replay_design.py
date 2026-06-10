# SPDX-License-Identifier: BUSL-1.1
"""Verify a V12 Memory Palace shortcut shadow-replay design report.

Companion fail-closed verifier for run_v12_memory_palace_shadow_replay_design.py
(#1047). Every other Memory Palace design has a paired verifier; this is the
shadow-replay design's. It is read-only and path-free: it re-derives the
report's canonical_digest, confirms the design stayed design-only (every
authority field literal false on the report and on every replay row), checks
the guardrail block, and validates each replay row's route/criteria shape, so
a consumer can re-derive the verdict instead of trusting the report's ``ok``.

A forged report (tampered counts/fields with the digest left stale, a flipped
authority field, or a non-design-only claim) fails closed. Output carries all
authority fields false and is itself action-free.

Exit codes: 0 verified, 1 verification failed (blockers listed).
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

from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from tools.run_v12_memory_palace_shadow_replay_design import (  # noqa: E402
    CLAIM_LABEL as SOURCE_CLAIM_LABEL,
    REPORT_VERSION as SOURCE_REPORT_VERSION,
    _AUTHORITY_FALSE_FIELDS,
)

VERIFICATION_VERSION = "wd.v12.memory_palace_shadow_replay_design.verification.v0"
REPORT_ARTIFACT_ID = "memory_palace_shadow_replay_design_report"

_GUARDRAIL_TRUE_FIELDS = (
    "design_only",
    "shadow_replay_not_executed",
    "operator_gate_required_before_runtime_route",
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
_REPLAY_ROW_TRUE_FIELDS = ("operator_gate_required", "manual_review_required")


class ShadowReplayDesignVerificationError(ValueError):
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
        help="Path to a JSON report emitted by the shadow-replay design tool.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = _load_json_report(args.report_json)
        verification = verify_memory_palace_shadow_replay_design(report)
    except ShadowReplayDesignVerificationError as exc:
        verification = _failure_report(exc.code)

    encoded = json.dumps(verification, indent=2, sort_keys=True, allow_nan=False)
    if args.json or verification["ok"]:
        print(encoded)
    else:
        print(
            "Memory Palace shadow-replay design verification FAILED: "
            + ", ".join(verification["blockers"]),
            file=sys.stderr,
        )
    return 0 if verification["ok"] else 1


def verify_memory_palace_shadow_replay_design(
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an action-free, re-derived verification summary for the design."""
    if not isinstance(report, Mapping):
        raise ShadowReplayDesignVerificationError(f"{REPORT_ARTIFACT_ID}_not_object")

    blockers: list[str] = []

    if report.get("report_version") != SOURCE_REPORT_VERSION:
        blockers.append("source_report_version_mismatch")
    if report.get("claim_label") != SOURCE_CLAIM_LABEL:
        blockers.append("source_claim_label_mismatch")
    if report.get("ok") is not True:
        blockers.append("source_report_not_ok")

    digest_check = _digest_rederives(report)
    if not digest_check:
        blockers.append("canonical_digest_mismatch")

    # Report-level authority boundary: every field must be literal False.
    for field in _AUTHORITY_FALSE_FIELDS:
        if report.get(field) is not False:
            blockers.append(f"report_authority_not_false:{field}")

    guardrails = report.get("no_overclaim_guardrails")
    if not isinstance(guardrails, Mapping):
        blockers.append("guardrails_missing")
    else:
        for field in _GUARDRAIL_TRUE_FIELDS:
            if guardrails.get(field) is not True:
                blockers.append(f"guardrail_not_true:{field}")

    rows = report.get("shadow_replay_designs")
    row_count = len(rows) if isinstance(rows, list) else 0
    if not isinstance(rows, list) or not rows:
        blockers.append("no_shadow_replay_rows")
    else:
        for index, row in enumerate(rows):
            blockers.extend(_row_blockers(index, row))

    verification = {
        "ok": not blockers,
        "verification_version": VERIFICATION_VERSION,
        "source_report_version_check": (
            "match" if report.get("report_version") == SOURCE_REPORT_VERSION
            else "mismatch"
        ),
        "source_claim_label_check": (
            "match" if report.get("claim_label") == SOURCE_CLAIM_LABEL
            else "mismatch"
        ),
        "canonical_digest_rederived": bool(digest_check),
        "shadow_replay_design_count_checked": row_count,
        "design_only": True,
        "read_side_report_only": True,
        "manual_review_required": True,
        "operator_gate_required_before_runtime_route": True,
        "shadow_replay_executed": False,
        "runtime_route_changed": False,
        "solver_call_performed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "scheduler_enqueue_performed": False,
        "gate_skip_performed": False,
        "promotion_performed": False,
        "promotion_action_allowed": False,
        "network_access_performed": False,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "external_writes_applied": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "blockers": sorted(set(blockers)),
        "warnings": [],
    }
    json.dumps(verification, allow_nan=False, sort_keys=True)
    return verification


def _row_blockers(index: int, row: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(row, Mapping):
        return [f"replay_row_not_object:{index}"]
    for field in _AUTHORITY_FALSE_FIELDS:
        if row.get(field) is not False:
            out.append(f"replay_row_authority_not_false:{index}:{field}")
    for field in _REPLAY_ROW_TRUE_FIELDS:
        if row.get(field) is not True:
            out.append(f"replay_row_flag_not_true:{index}:{field}")
    if row.get("replay_status") != "design_only_not_executed":
        out.append(f"replay_row_status_not_design_only:{index}")
    incumbent = row.get("incumbent_route")
    candidate = row.get("candidate_route")
    if not isinstance(incumbent, Mapping) or not isinstance(candidate, Mapping):
        out.append(f"replay_row_route_missing:{index}")
        return out
    inc_hops = incumbent.get("hop_count")
    cand_hops = candidate.get("hop_count")
    if not _is_int(inc_hops) or not _is_int(cand_hops):
        out.append(f"replay_row_hop_count_invalid:{index}")
    else:
        expected_reduction = max(0, inc_hops - cand_hops)
        if row.get("hop_reduction") != expected_reduction:
            out.append(f"replay_row_hop_reduction_mismatch:{index}")
    if row.get("agreement_criterion") != "both_routes_resolve_to_same_target_node":
        out.append(f"replay_row_agreement_criterion_unexpected:{index}")
    return out


def _digest_rederives(report: Mapping[str, Any]) -> bool:
    digest = report.get("canonical_digest")
    if not isinstance(digest, str) or not digest:
        return False
    core = {k: v for k, v in report.items() if k != "canonical_digest"}
    try:
        return digest == sha256_digest(core)
    except (TypeError, ValueError):
        return False


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _load_json_report(path: Path) -> Mapping[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ShadowReplayDesignVerificationError("report_unreadable") from exc
    try:
        report = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ShadowReplayDesignVerificationError("report_invalid_json") from exc
    if not isinstance(report, Mapping):
        raise ShadowReplayDesignVerificationError(f"{REPORT_ARTIFACT_ID}_not_object")
    return report


def _failure_report(code: str) -> dict[str, Any]:
    return {
        "ok": False,
        "verification_version": VERIFICATION_VERSION,
        "source_report_version_check": "mismatch",
        "source_claim_label_check": "mismatch",
        "canonical_digest_rederived": False,
        "shadow_replay_design_count_checked": 0,
        "design_only": True,
        "read_side_report_only": True,
        "manual_review_required": True,
        "operator_gate_required_before_runtime_route": True,
        "shadow_replay_executed": False,
        "runtime_route_changed": False,
        "solver_call_performed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "scheduler_enqueue_performed": False,
        "gate_skip_performed": False,
        "promotion_performed": False,
        "promotion_action_allowed": False,
        "network_access_performed": False,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "external_writes_applied": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "blockers": [code],
        "warnings": [],
    }


if __name__ == "__main__":
    raise SystemExit(main())
