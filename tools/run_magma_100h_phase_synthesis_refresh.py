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
DEFAULT_OPERATOR_AUTHORITY_READINESS = SPRINT_DIR / "operator_authority_readiness.json"
DEFAULT_RELEASE_BOUNDARY_READINESS = SPRINT_DIR / "release_boundary_readiness.json"
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


def _decision_options_non_mutating(packet: dict[str, Any]) -> bool:
    options = packet.get("decision_options")
    if not isinstance(options, list) or not options:
        return False
    return all(
        isinstance(option, dict)
        and option.get("runtime_authority_granted") is False
        and option.get("runtime_traffic_mutation_allowed") is False
        and option.get("candidate_state_mutation_allowed") is False
        and option.get("release_boundary_mutation_allowed") is False
        for option in options
    )


def _release_decision_options_non_mutating(packet: dict[str, Any]) -> bool:
    options = packet.get("decision_options")
    if not isinstance(options, list) or not options:
        return False
    return all(
        isinstance(option, dict)
        and option.get("tag_creation_allowed") is False
        and option.get("docker_latest_move_allowed") is False
        and option.get("docker_stable_move_allowed") is False
        and option.get("stable_release_claim_allowed") is False
        and option.get("external_effect_authority_change_allowed") is False
        for option in options
    )


def _operator_authority_guardrails(report: dict[str, Any]) -> dict[str, Any]:
    invariants = report.get("read_only_invariants") or {}
    authority_guardrails = report.get("authority_guardrails") or {}
    packet = report.get("operator_decision_packet") or {}
    options = packet.get("decision_options")
    option_count = len(options) if isinstance(options, list) else 0

    return {
        "report_ok": report.get("ok") is True,
        "authority_activation_status": report.get("authority_activation_status"),
        "activation_blockers": list(report.get("activation_blockers") or []),
        "explicit_operator_approval_found": (
            report.get("explicit_operator_approval_found") is True
        ),
        "release_boundary_all_false": _boundary_is_false(report),
        "authority_guardrails": {
            "operator_gate_required": authority_guardrails.get(
                "operator_gate_required"
            ),
            "requires_separate_receipt_bound_activation": authority_guardrails.get(
                "requires_separate_receipt_bound_activation"
            ),
            "activation_effect": authority_guardrails.get("activation_effect"),
            "runtime_authority_granted": authority_guardrails.get(
                "runtime_authority_granted"
            ),
            "runtime_traffic_mutation_applied": authority_guardrails.get(
                "runtime_traffic_mutation_applied"
            ),
            "candidate_state_mutation_applied": authority_guardrails.get(
                "candidate_state_mutation_applied"
            ),
        },
        "read_only_invariants": dict(invariants),
        "operator_decision_packet": {
            "schema_version": packet.get("schema_version"),
            "decision_status": packet.get("decision_status"),
            "default_recommendation": packet.get("default_recommendation"),
            "approval_event_required_for_activation": packet.get(
                "approval_event_required_for_activation"
            ),
            "activation_effect_before_followup": packet.get(
                "activation_effect_before_followup"
            ),
            "option_count": option_count,
            "all_options_non_mutating": _decision_options_non_mutating(packet),
        },
    }


def _release_boundary_guardrails(report: dict[str, Any]) -> dict[str, Any]:
    invariants = report.get("read_only_invariants") or {}
    guardrails = report.get("release_boundary_guardrails") or {}
    packet = report.get("release_decision_packet") or {}
    source_phase = report.get("source_phase_synthesis_refresh") or {}
    source_gate = report.get("source_release_gate_readonly_recheck") or {}
    options = packet.get("decision_options")
    option_count = len(options) if isinstance(options, list) else 0

    return {
        "report_ok": report.get("ok") is True,
        "release_boundary_status": report.get("release_boundary_status"),
        "release_boundary_blockers": list(
            report.get("release_boundary_blockers") or []
        ),
        "operator_finalization_required": (
            report.get("operator_finalization_required") is True
        ),
        "release_boundary_all_false": _boundary_is_false(report),
        "release_boundary_guardrails": {
            "release_boundary_effect": guardrails.get("release_boundary_effect"),
            "tag_creation_applied": guardrails.get("tag_creation_applied"),
            "docker_latest_move_applied": guardrails.get(
                "docker_latest_move_applied"
            ),
            "docker_stable_move_applied": guardrails.get(
                "docker_stable_move_applied"
            ),
            "stable_release_claim_applied": guardrails.get(
                "stable_release_claim_applied"
            ),
            "external_effect_authority_change_applied": guardrails.get(
                "external_effect_authority_change_applied"
            ),
            "requires_operator_only_finalization": guardrails.get(
                "requires_operator_only_finalization"
            ),
        },
        "read_only_invariants": dict(invariants),
        "release_decision_packet": {
            "schema_version": packet.get("schema_version"),
            "decision_status": packet.get("decision_status"),
            "default_recommendation": packet.get("default_recommendation"),
            "operator_finalization_required": packet.get(
                "operator_finalization_required"
            ),
            "release_boundary_effect_before_followup": packet.get(
                "release_boundary_effect_before_followup"
            ),
            "option_count": option_count,
            "all_options_non_mutating": _release_decision_options_non_mutating(
                packet
            ),
        },
        "source_phase_synthesis_refresh": {
            "schema_version": source_phase.get("schema_version"),
            "sprint_id": source_phase.get("sprint_id"),
            "generated_at_utc": source_phase.get("generated_at_utc"),
            "ok": source_phase.get("ok") is True,
            "release_boundary_all_false": (
                source_phase.get("release_boundary_all_false") is True
            ),
            "remaining_release_soak_status": (
                (source_phase.get("remaining_release_soak_package") or {}).get(
                    "status"
                )
            ),
            "landed_release_soak_status": (
                (source_phase.get("landed_release_soak_package") or {}).get(
                    "status"
                )
            ),
        },
        "source_release_gate_readonly_recheck": {
            "schema_version": source_gate.get("schema_version"),
            "ok": source_gate.get("ok") is True,
            "read_only": source_gate.get("read_only") is True,
            "release_gate_decision": source_gate.get("release_gate_decision"),
            "release_gate_effect": source_gate.get("release_gate_effect"),
            "release_boundary_all_false": (
                source_gate.get("release_boundary_all_false") is True
            ),
            "blockers": list(source_gate.get("blockers") or []),
        },
    }


def _operator_authority_remaining_status(report: dict[str, Any] | None) -> str:
    if report is None:
        return "operator_decision_required"
    if report.get("explicit_operator_approval_found") is True:
        return "operator_approval_recorded_activation_still_requires_followup"
    packet = report.get("operator_decision_packet")
    if (
        isinstance(packet, dict)
        and packet.get("decision_status") == "operator_approval_missing"
    ):
        return "operator_approval_missing_decision_packet_recorded"
    return "operator_decision_required"


def _release_boundary_ready(report: dict[str, Any] | None) -> bool:
    if report is None:
        return False
    guardrails = _release_boundary_guardrails(report)
    return (
        guardrails["report_ok"] is True
        and guardrails["release_boundary_status"] == "ready_for_operator_finalization"
        and guardrails["release_boundary_blockers"] == []
        and guardrails["release_boundary_all_false"] is True
    )


def _landed_work_packages(
    *,
    rival_report: dict[str, Any],
    release_gate_report: dict[str, Any],
    operator_authority_report: dict[str, Any] | None,
    release_boundary_readiness_report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    packages = [
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
    ]
    if _release_boundary_ready(release_boundary_readiness_report):
        packages.append(
            {
                "id": "release_soak_evidence_blocker_resolution",
                "status": "complete_release_boundary_readiness_recorded",
                "evidence": _repo_path(DEFAULT_RELEASE_BOUNDARY_READINESS),
                "summary": _release_boundary_guardrails(
                    release_boundary_readiness_report or {}
                ),
            }
        )
    if operator_authority_report is not None:
        packages.append(
            {
                "id": "operator_authority_decision_packet",
                "status": "complete_non_authority_decision_surface_recorded",
                "evidence": _repo_path(DEFAULT_OPERATOR_AUTHORITY_READINESS),
                "summary": _operator_authority_guardrails(
                    operator_authority_report
                ),
            }
        )
    packages.append(
        {
            "id": "phase_synthesis_and_baseline_refresh",
            "status": "complete_if_report_ok",
            "evidence": _repo_path(DEFAULT_OUTPUT),
        }
    )
    return packages


def _remaining_work_packages(
    *,
    release_gate_report: dict[str, Any],
    operator_authority_report: dict[str, Any] | None,
    release_boundary_readiness_report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    packages = [
        {
            "id": "operator_gated_authority_activation_decision",
            "status": _operator_authority_remaining_status(
                operator_authority_report
            ),
            "owner": "operator",
            "peer": "codex,claude",
            "target": (
                "record explicit operator approval or keep runtime authority "
                "held"
            ),
            "acceptance": (
                "requires an explicit operator approval event; no runtime "
                "traffic or candidate-state mutation before approval"
            ),
        },
    ]
    if _release_boundary_ready(release_boundary_readiness_report):
        packages.append(
            {
                "id": "operator_release_finalization_decision",
                "status": "operator_release_finalization_required",
                "owner": "operator",
                "peer": "codex,claude",
                "target": (
                    "operator performs any tag, Docker, or stable release "
                    "finalization separately"
                ),
                "acceptance": (
                    "release boundary remains false until operator-only "
                    "finalization creates signed release receipts; agents do "
                    "not tag releases or move Docker aliases"
                ),
            }
        )
    else:
        status = (
            "blocked_until_release_gate_soak_evidence_passes"
            if release_gate_report.get("release_gate_decision") == "hold"
            else "ready_for_release_boundary_review"
        )
        if release_boundary_readiness_report is not None:
            status = "release_boundary_readiness_blocked"
        packages.append(
            {
                "id": "release_soak_evidence_blocker_resolution",
                "status": status,
                "owner": "operator,codex",
                "peer": "claude",
                "target": (
                    "resolve release soak evidence blockers before any "
                    "release boundary review"
                ),
                "acceptance": (
                    "release boundary remains false until release gate passes "
                    "and an explicit release-boundary authorization exists"
                ),
            },
        )
    return packages


def _work_package_ids(packages: list[dict[str, Any]] | Any) -> list[str]:
    if not isinstance(packages, list):
        return []
    ids: list[str] = []
    for package in packages:
        if isinstance(package, dict) and isinstance(package.get("id"), str):
            ids.append(package["id"])
    return ids


def _phase_next_work_package(package: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "status", "owner", "peer", "target", "acceptance")
    return {key: package[key] for key in keys if package.get(key) is not None}


def _bind_effective_next_work_packages(
    phase_synthesis: dict[str, Any],
    *,
    landed_work_packages: list[dict[str, Any]],
    remaining_work_packages: list[dict[str, Any]],
) -> None:
    baseline_next_work = [
        dict(package)
        for package in (phase_synthesis.get("next_work_packages") or [])
        if isinstance(package, dict)
    ]
    phase_synthesis["baseline_next_work_packages"] = baseline_next_work
    phase_synthesis["landed_work_package_ids"] = sorted(
        set(_work_package_ids(landed_work_packages))
    )
    phase_synthesis["next_work_packages"] = [
        _phase_next_work_package(package) for package in remaining_work_packages
    ]


def _collect_blockers(
    *,
    baseline: dict[str, Any],
    phase_synthesis: dict[str, Any],
    rival_report: dict[str, Any],
    release_gate_report: dict[str, Any],
    operator_authority_report: dict[str, Any] | None,
    release_boundary_readiness_report: dict[str, Any] | None,
    landed_work_packages: list[dict[str, Any]],
    remaining_work_packages: list[dict[str, Any]],
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
    phase_next_ids = set(
        _work_package_ids(phase_synthesis.get("next_work_packages") or [])
    )
    landed_ids = set(_work_package_ids(landed_work_packages))
    remaining_ids = set(_work_package_ids(remaining_work_packages))
    if phase_next_ids & landed_ids:
        blockers.append("phase_synthesis_next_work_includes_landed_package")
    if phase_next_ids != remaining_ids:
        blockers.append("phase_synthesis_next_work_not_bound_to_remaining_packages")

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

    if release_boundary_readiness_report is not None:
        release_boundary = _release_boundary_guardrails(
            release_boundary_readiness_report
        )
        boundary_guardrails = release_boundary["release_boundary_guardrails"]
        decision_packet = release_boundary["release_decision_packet"]
        source_phase = release_boundary["source_phase_synthesis_refresh"]
        source_gate = release_boundary["source_release_gate_readonly_recheck"]
        read_only_invariants = release_boundary["read_only_invariants"]

        if release_boundary["report_ok"] is not True:
            blockers.append("release_boundary_readiness_report_not_ok")
        if release_boundary["release_boundary_status"] != (
            "ready_for_operator_finalization"
        ):
            blockers.append("release_boundary_not_ready_for_operator_finalization")
        if release_boundary["release_boundary_blockers"]:
            blockers.append("release_boundary_readiness_blockers_present")
        if release_boundary["operator_finalization_required"] is not True:
            blockers.append("release_boundary_operator_finalization_not_required")
        if release_boundary["release_boundary_all_false"] is not True:
            blockers.append("release_boundary_readiness_boundary_mutated")
        if release_guardrails["decision"] != "pass":
            blockers.append("release_boundary_readiness_without_release_gate_pass")
        if boundary_guardrails["release_boundary_effect"] != "none":
            blockers.append("release_boundary_effect_not_none")
        if boundary_guardrails["tag_creation_applied"] is not False:
            blockers.append("release_boundary_tag_created")
        if boundary_guardrails["docker_latest_move_applied"] is not False:
            blockers.append("release_boundary_docker_latest_moved")
        if boundary_guardrails["docker_stable_move_applied"] is not False:
            blockers.append("release_boundary_docker_stable_moved")
        if boundary_guardrails["stable_release_claim_applied"] is not False:
            blockers.append("release_boundary_stable_release_claimed")
        if (
            boundary_guardrails["external_effect_authority_change_applied"]
            is not False
        ):
            blockers.append("release_boundary_external_effect_authority_changed")
        if boundary_guardrails["requires_operator_only_finalization"] is not True:
            blockers.append("release_boundary_operator_only_finalization_missing")
        if read_only_invariants.get("no_tag_created") is not True:
            blockers.append("release_boundary_no_tag_invariant_missing")
        if read_only_invariants.get("no_docker_latest_moved") is not True:
            blockers.append("release_boundary_no_latest_invariant_missing")
        if read_only_invariants.get("no_docker_stable_moved") is not True:
            blockers.append("release_boundary_no_stable_invariant_missing")
        if read_only_invariants.get("no_stable_release_claim") is not True:
            blockers.append("release_boundary_no_claim_invariant_missing")
        if (
            read_only_invariants.get("no_external_effect_authority_change")
            is not True
        ):
            blockers.append("release_boundary_no_external_effect_invariant_missing")
        if read_only_invariants.get("release_boundary_effect") != "none":
            blockers.append("release_boundary_read_only_effect_not_none")
        if decision_packet["operator_finalization_required"] is not True:
            blockers.append("release_boundary_packet_finalization_not_required")
        if decision_packet["release_boundary_effect_before_followup"] != "none":
            blockers.append("release_boundary_packet_effect_not_none")
        if decision_packet["all_options_non_mutating"] is not True:
            blockers.append("release_boundary_decision_option_mutates")
        if source_phase["sprint_id"] != baseline.get("sprint_id"):
            blockers.append("release_boundary_source_sprint_mismatch")
        if source_phase["ok"] is not True:
            blockers.append("release_boundary_source_phase_not_ok")
        if source_phase["release_boundary_all_false"] is not True:
            blockers.append("release_boundary_source_phase_boundary_mutated")
        if (
            source_phase["remaining_release_soak_status"]
            != "ready_for_release_boundary_review"
            and source_phase["landed_release_soak_status"]
            != "complete_release_boundary_readiness_recorded"
        ):
            blockers.append("release_boundary_source_soak_status_not_ready")
        if source_gate["ok"] is not True:
            blockers.append("release_boundary_source_gate_not_ok")
        if source_gate["read_only"] is not True:
            blockers.append("release_boundary_source_gate_not_read_only")
        if source_gate["release_gate_decision"] != "pass":
            blockers.append("release_boundary_source_gate_not_pass")
        if source_gate["release_gate_effect"] != "none":
            blockers.append("release_boundary_source_gate_effect_not_none")
        if source_gate["release_boundary_all_false"] is not True:
            blockers.append("release_boundary_source_gate_boundary_mutated")
        if source_gate["blockers"]:
            blockers.append("release_boundary_source_gate_blockers_present")

    if operator_authority_report is not None:
        operator_guardrails = _operator_authority_guardrails(
            operator_authority_report
        )
        authority_guardrails = operator_guardrails["authority_guardrails"]
        decision_packet = operator_guardrails["operator_decision_packet"]
        read_only_invariants = operator_guardrails["read_only_invariants"]

        if operator_guardrails["report_ok"] is not True:
            blockers.append("operator_authority_readiness_report_not_ok")
        if operator_guardrails["release_boundary_all_false"] is not True:
            blockers.append("operator_authority_release_boundary_mutated")
        if authority_guardrails["operator_gate_required"] is not True:
            blockers.append("operator_authority_gate_not_required")
        if (
            authority_guardrails["requires_separate_receipt_bound_activation"]
            is not True
        ):
            blockers.append("operator_authority_separate_activation_not_required")
        if authority_guardrails["activation_effect"] != "none":
            blockers.append("operator_authority_activation_effect_not_none")
        if authority_guardrails["runtime_authority_granted"] is not False:
            blockers.append("operator_authority_runtime_authority_granted")
        if authority_guardrails["runtime_traffic_mutation_applied"] is not False:
            blockers.append("operator_authority_runtime_traffic_mutated")
        if authority_guardrails["candidate_state_mutation_applied"] is not False:
            blockers.append("operator_authority_candidate_state_mutated")
        if read_only_invariants.get("no_runtime_authority_granted") is not True:
            blockers.append("operator_authority_runtime_invariant_missing")
        if read_only_invariants.get("no_runtime_traffic_mutated") is not True:
            blockers.append("operator_authority_traffic_invariant_missing")
        if read_only_invariants.get("no_candidate_state_mutated") is not True:
            blockers.append("operator_authority_candidate_invariant_missing")
        if read_only_invariants.get("no_release_boundary_mutated") is not True:
            blockers.append("operator_authority_release_invariant_missing")
        if decision_packet["approval_event_required_for_activation"] is not True:
            blockers.append("operator_authority_approval_event_not_required")
        if decision_packet["activation_effect_before_followup"] != "none":
            blockers.append("operator_authority_packet_activation_effect_not_none")
        if decision_packet["all_options_non_mutating"] is not True:
            blockers.append("operator_authority_decision_option_mutates")

    return blockers


def build_report(
    *,
    baseline: dict[str, Any],
    rival_report: dict[str, Any],
    release_gate_report: dict[str, Any],
    operator_authority_report: dict[str, Any] | None = None,
    release_boundary_readiness_report: dict[str, Any] | None = None,
    generated_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    generated_at_utc = generated_at_utc or _utc_now()
    phase_synthesis = build_synthesis(
        baseline,
        generated_at_utc=generated_at_utc,
    )
    landed_work_packages = _landed_work_packages(
        rival_report=rival_report,
        release_gate_report=release_gate_report,
        operator_authority_report=operator_authority_report,
        release_boundary_readiness_report=release_boundary_readiness_report,
    )
    remaining_work_packages = _remaining_work_packages(
        release_gate_report=release_gate_report,
        operator_authority_report=operator_authority_report,
        release_boundary_readiness_report=release_boundary_readiness_report,
    )
    _bind_effective_next_work_packages(
        phase_synthesis,
        landed_work_packages=landed_work_packages,
        remaining_work_packages=remaining_work_packages,
    )
    blockers = _collect_blockers(
        baseline=baseline,
        phase_synthesis=phase_synthesis,
        rival_report=rival_report,
        release_gate_report=release_gate_report,
        operator_authority_report=operator_authority_report,
        release_boundary_readiness_report=release_boundary_readiness_report,
        landed_work_packages=landed_work_packages,
        remaining_work_packages=remaining_work_packages,
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
        "landed_work_packages": landed_work_packages,
        "remaining_work_packages": remaining_work_packages,
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
    }


def build_report_from_paths(
    *,
    baseline_path: Path,
    rival_report_path: Path,
    release_gate_report_path: Path,
    operator_authority_path: Path | None = None,
    release_boundary_readiness_path: Path | None = None,
    generated_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    return build_report(
        baseline=_read_json(baseline_path),
        rival_report=_read_json(rival_report_path),
        release_gate_report=_read_json(release_gate_report_path),
        operator_authority_report=(
            _read_json(operator_authority_path)
            if operator_authority_path is not None
            else None
        ),
        release_boundary_readiness_report=(
            _read_json(release_boundary_readiness_path)
            if (
                release_boundary_readiness_path is not None
                and release_boundary_readiness_path.exists()
            )
            else None
        ),
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
        "--operator-authority-readiness",
        type=Path,
        default=DEFAULT_OPERATOR_AUTHORITY_READINESS,
    )
    parser.add_argument(
        "--release-boundary-readiness",
        type=Path,
        default=DEFAULT_RELEASE_BOUNDARY_READINESS,
    )
    parser.add_argument(
        "--skip-release-boundary-readiness",
        action="store_true",
        help="Do not consume the optional release-boundary readiness report.",
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
        operator_authority_path=args.operator_authority_readiness,
        release_boundary_readiness_path=(
            None
            if args.skip_release_boundary_readiness
            else args.release_boundary_readiness
        ),
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
