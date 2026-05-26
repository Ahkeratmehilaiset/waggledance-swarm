#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record the MAGMA 100h phase synthesis and baseline refresh.

The report is intentionally an aggregator. It does not create release tags,
move Docker aliases, activate runtime authority, or promote rival benchmark
claims. It binds the refreshed sprint baseline to the landed read-only
release-gate and rival-local accepted-blocker evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.magma_phase_synthesis import build_synthesis


SCHEMA_VERSION = "waggledance.magma_100h_phase_synthesis_refresh.v0"
SPRINT_DIR = Path("docs/runs/magma_100h_sprint_2026_05_26")
DEFAULT_BASELINE = SPRINT_DIR / "baseline.json"
DEFAULT_RIVAL_ACCEPTED_BLOCKERS = SPRINT_DIR / "rival_local_accepted_blockers.json"
DEFAULT_RELEASE_GATE_RECHECK = SPRINT_DIR / "release_gate_readonly_recheck.json"
DEFAULT_OUTPUT = SPRINT_DIR / "phase_synthesis_refresh.json"

EXPECTED_FORBIDDEN_CLAIMS = {
    "beats all competitors",
    "world best AI",
    "AGI",
    "consciousness",
    "production-ready fleet learning",
    "public cryptographic verification parity",
    "rival benchmark consensus-grade",
}

FALSE_RELEASE_BOUNDARY = {
    "stable_release_claim": False,
    "tag_creation": False,
    "docker_latest_move": False,
    "external_effect_authority_change": False,
}

SOAK_DIAGNOSTIC_SUMMARY_KEYS = (
    "target_version",
    "result",
    "duration_hours",
    "required_duration_hours",
    "ended_at_date",
    "required_soak_end",
    "silent_failures",
    "expected_silent_failures",
    "error_log_clean",
    "expected_error_log_clean",
    "docker_stable_policy",
    "expected_docker_stable_policy",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def _format_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _repo_path(path: Path) -> str:
    return path.as_posix()


def _boundary_is_false(report: dict[str, Any]) -> bool:
    return report.get("release_boundary") == FALSE_RELEASE_BOUNDARY


def _all_rival_blockers_are_non_contributing(report: dict[str, Any]) -> bool:
    blockers = report.get("accepted_blockers")
    if not isinstance(blockers, list):
        return False
    return all(
        blocker.get("accepted_blocker") is True
        and blocker.get("artifact_digest_verified") is True
        and blocker.get("consensus_grade_contribution") is False
        for blocker in blockers
        if isinstance(blocker, dict)
    ) and len(blockers) == 3


def _baseline_guardrails(baseline: dict[str, Any]) -> dict[str, Any]:
    forbidden = set(baseline.get("forbidden_claims") or [])
    return {
        "baseline_ok": baseline.get("ok") is True,
        "release_boundary_all_false": _boundary_is_false(baseline),
        "forbidden_claims_preserved": EXPECTED_FORBIDDEN_CLAIMS <= forbidden,
        "forbidden_claims_count": len(forbidden),
        "competitor_consensus_grade": (
            (baseline.get("current_state") or {})
            .get("competitor_pilot", {})
            .get("consensus_grade")
        ),
        "rival_local_check_consensus_grade": (
            (baseline.get("current_state") or {})
            .get("competitor_pilot", {})
            .get("rival_local_check_consensus_grade")
        ),
    }


def _rival_guardrails(report: dict[str, Any]) -> dict[str, Any]:
    guardrails = report.get("no_overclaim_guardrails") or {}
    return {
        "report_ok": report.get("ok") is True,
        "accepted_blocker_count": report.get("accepted_blocker_count"),
        "passed_count": report.get("passed_count"),
        "blocked_count": report.get("blocked_count"),
        "consensus_grade": report.get("consensus_grade"),
        "accepted_blockers_non_contributing": (
            _all_rival_blockers_are_non_contributing(report)
        ),
        "no_overclaim_guardrails": dict(guardrails),
    }


def _release_soak_diagnostics_summary(report: dict[str, Any]) -> dict[str, Any]:
    gate = report.get("gate")
    if not isinstance(gate, dict):
        return {}
    diagnostics = gate.get("soak_evidence_diagnostics")
    if not isinstance(diagnostics, dict):
        return {}

    summary = {
        key: diagnostics[key]
        for key in SOAK_DIAGNOSTIC_SUMMARY_KEYS
        if key in diagnostics
    }
    status_fields = diagnostics.get("status_fields")
    if isinstance(status_fields, dict):
        safe_status_fields = {}
        for name, value in sorted(status_fields.items()):
            if isinstance(name, str) and isinstance(value, dict):
                safe_status_fields[name] = {
                    "actual": value.get("actual"),
                    "expected": value.get("expected"),
                }
        if safe_status_fields:
            summary["status_fields"] = safe_status_fields
    return summary


def _release_gate_guardrails(report: dict[str, Any]) -> dict[str, Any]:
    invariants = report.get("read_only_invariants") or {}
    summary = {
        "report_ok": report.get("ok") is True,
        "read_only": report.get("read_only") is True,
        "decision": report.get("release_gate_decision"),
        "blockers": list(report.get("blockers") or []),
        "release_gate_effect": report.get("release_gate_effect"),
        "release_boundary_all_false": _boundary_is_false(report),
        "read_only_invariants": dict(invariants),
    }
    soak_diagnostics = _release_soak_diagnostics_summary(report)
    if soak_diagnostics:
        summary["soak_evidence_diagnostics"] = soak_diagnostics
    return summary


def _collect_blockers(
    *,
    baseline: dict[str, Any],
    phase_synthesis: dict[str, Any],
    rival_report: dict[str, Any],
    release_gate_report: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []

    baseline_guardrails = _baseline_guardrails(baseline)
    if baseline_guardrails["baseline_ok"] is not True:
        blockers.append("baseline_not_ok")
    if baseline_guardrails["release_boundary_all_false"] is not True:
        blockers.append("baseline_release_boundary_mutated")
    if baseline_guardrails["forbidden_claims_preserved"] is not True:
        blockers.append("baseline_forbidden_claims_not_preserved")
    if baseline_guardrails["competitor_consensus_grade"] is not False:
        blockers.append("baseline_competitor_consensus_grade_overclaim")
    if baseline_guardrails["rival_local_check_consensus_grade"] is not False:
        blockers.append("baseline_rival_local_consensus_grade_overclaim")

    if phase_synthesis.get("ok") is not True:
        blockers.append("phase_synthesis_not_ok")
    if phase_synthesis.get("baseline_generated_at_utc") != baseline.get(
        "generated_at_utc"
    ):
        blockers.append("phase_synthesis_not_bound_to_refreshed_baseline")
    if phase_synthesis.get("release_boundary") != baseline.get("release_boundary"):
        blockers.append("phase_synthesis_release_boundary_mismatch")

    rival_guardrails = _rival_guardrails(rival_report)
    if rival_guardrails["report_ok"] is not True:
        blockers.append("rival_accepted_blockers_report_not_ok")
    if rival_guardrails["accepted_blocker_count"] != 3:
        blockers.append("rival_accepted_blocker_count_not_3")
    if rival_guardrails["passed_count"] != 1:
        blockers.append("rival_passed_count_not_1")
    if rival_guardrails["blocked_count"] != 3:
        blockers.append("rival_blocked_count_not_3")
    if rival_guardrails["consensus_grade"] is not False:
        blockers.append("rival_consensus_grade_overclaim")
    if rival_guardrails["accepted_blockers_non_contributing"] is not True:
        blockers.append("rival_blockers_not_verified_non_contributing")

    release_guardrails = _release_gate_guardrails(release_gate_report)
    if release_guardrails["report_ok"] is not True:
        blockers.append("release_gate_recheck_report_not_ok")
    if release_guardrails["read_only"] is not True:
        blockers.append("release_gate_recheck_not_read_only")
    if release_guardrails["release_gate_effect"] != "none":
        blockers.append("release_gate_effect_not_none")
    if release_guardrails["release_boundary_all_false"] is not True:
        blockers.append("release_gate_release_boundary_mutated")
    if release_guardrails["decision"] not in {"hold", "pass"}:
        blockers.append("release_gate_decision_unknown")

    return blockers


def build_report(
    *,
    baseline: dict[str, Any],
    rival_report: dict[str, Any],
    release_gate_report: dict[str, Any],
    generated_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    generated_at_utc = generated_at_utc or _utc_now()
    phase_synthesis = build_synthesis(
        baseline,
        generated_at_utc=generated_at_utc,
    )
    blockers = _collect_blockers(
        baseline=baseline,
        phase_synthesis=phase_synthesis,
        rival_report=rival_report,
        release_gate_report=release_gate_report,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _format_utc(generated_at_utc),
        "ok": not blockers,
        "blockers": blockers,
        "sprint_id": baseline.get("sprint_id"),
        "baseline_refresh": {
            "baseline_generated_at_utc": baseline.get("generated_at_utc"),
            "baseline_guardrails": _baseline_guardrails(baseline),
            "release_boundary": dict(baseline.get("release_boundary") or {}),
            "forbidden_claims": list(baseline.get("forbidden_claims") or []),
        },
        "phase_synthesis": phase_synthesis,
        "landed_work_packages": [
            {
                "id": "rival_local_evidence_execution_or_accepted_blockers",
                "status": "complete_accepted_blockers_recorded",
                "evidence": _repo_path(DEFAULT_RIVAL_ACCEPTED_BLOCKERS),
                "summary": _rival_guardrails(rival_report),
            },
            {
                "id": "release_gate_readonly_recheck",
                "status": "complete_observation_only",
                "evidence": _repo_path(DEFAULT_RELEASE_GATE_RECHECK),
                "summary": _release_gate_guardrails(release_gate_report),
            },
            {
                "id": "phase_synthesis_and_baseline_refresh",
                "status": "complete_if_report_ok",
                "evidence": _repo_path(DEFAULT_OUTPUT),
            },
        ],
        "remaining_work_packages": [
            {
                "id": "operator_gated_authority_activation_decision",
                "status": "operator_decision_required",
                "owner": "operator",
                "acceptance": (
                    "requires an explicit operator approval event; no runtime "
                    "traffic or candidate-state mutation before approval"
                ),
            },
            {
                "id": "release_soak_evidence_blocker_resolution",
                "status": (
                    "blocked_until_release_gate_soak_evidence_passes"
                    if release_gate_report.get("release_gate_decision") == "hold"
                    else "ready_for_release_boundary_review"
                ),
                "owner": "operator,codex",
                "acceptance": (
                    "release boundary remains false until release gate passes "
                    "and an explicit release-boundary authorization exists"
                ),
            },
        ],
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
    }


def build_report_from_paths(
    *,
    baseline_path: Path,
    rival_report_path: Path,
    release_gate_report_path: Path,
    generated_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    return build_report(
        baseline=_read_json(baseline_path),
        rival_report=_read_json(rival_report_path),
        release_gate_report=_read_json(release_gate_report_path),
        generated_at_utc=generated_at_utc,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--rival-accepted-blockers",
        type=Path,
        default=DEFAULT_RIVAL_ACCEPTED_BLOCKERS,
    )
    parser.add_argument(
        "--release-gate-recheck",
        type=Path,
        default=DEFAULT_RELEASE_GATE_RECHECK,
    )
    parser.add_argument(
        "--generated-at-utc",
        type=_parse_timestamp,
        help="Override report timestamp, ISO-8601 UTC.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = build_report_from_paths(
        baseline_path=args.baseline,
        rival_report_path=args.rival_accepted_blockers,
        release_gate_report_path=args.release_gate_recheck,
        generated_at_utc=args.generated_at_utc,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    if args.json:
        print(encoded, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
