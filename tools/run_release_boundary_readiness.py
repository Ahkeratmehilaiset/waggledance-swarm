#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record release-boundary readiness without performing release actions.

This tool is deliberately read-only with respect to the release boundary. It
can observe that the release gate and operator decision packs are in place, but
it never creates a tag, moves a Docker alias, claims stable release, or changes
external authority. Finalization remains operator-only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.operator_decision_pack import DecisionPackError, is_signed, load_pack


SCHEMA_VERSION = "waggledance.release_boundary_readiness.v0"
DECISION_PACKET_SCHEMA_VERSION = "waggledance.release_boundary_decision_packet.v0"
SPRINT_DIR = Path("docs/runs/magma_100h_sprint_2026_05_26")
DEFAULT_PHASE_SYNTHESIS_REFRESH = SPRINT_DIR / "phase_synthesis_refresh.json"
DEFAULT_RELEASE_GATE_RECHECK = SPRINT_DIR / "release_gate_readonly_recheck.json"
DEFAULT_TORCH_DECISION_PACK = Path("docs/operator_inbox/torch-cuda-vs-cpu.yaml")
DEFAULT_DOCKER_DECISION_PACK = Path(
    "docs/operator_inbox/docker-latest-promotion.yaml"
)
DEFAULT_OUTPUT = SPRINT_DIR / "release_boundary_readiness.json"

RELEASE_SOAK_TASK_ID = "release_soak_evidence_blocker_resolution"
FINALIZATION_TASK_ID = "operator_release_finalization_decision"
STRICT_BLOCKED_EXIT_CODE = 2

FALSE_RELEASE_BOUNDARY = {
    "stable_release_claim": False,
    "tag_creation": False,
    "docker_latest_move": False,
    "external_effect_authority_change": False,
}


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def _format_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _parse_timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _remaining_package(
    phase_synthesis_refresh: dict[str, Any],
    package_id: str,
) -> dict[str, Any]:
    for package in phase_synthesis_refresh.get("remaining_work_packages") or []:
        if isinstance(package, dict) and package.get("id") == package_id:
            return dict(package)
    return {}


def _landed_package(
    phase_synthesis_refresh: dict[str, Any],
    package_id: str,
) -> dict[str, Any]:
    for package in phase_synthesis_refresh.get("landed_work_packages") or []:
        if isinstance(package, dict) and package.get("id") == package_id:
            return dict(package)
    return {}


def _source_release_soak_package(
    phase_synthesis_refresh: dict[str, Any],
) -> dict[str, Any]:
    return _remaining_package(
        phase_synthesis_refresh,
        RELEASE_SOAK_TASK_ID,
    ) or _landed_package(
        phase_synthesis_refresh,
        RELEASE_SOAK_TASK_ID,
    )


def _chosen_option(pack: Mapping[str, Any]) -> dict[str, Any]:
    signoff = pack.get("operator_signoff")
    chosen = ""
    if isinstance(signoff, Mapping):
        chosen = str(signoff.get("chosen_option") or "")
    for option in pack.get("options") or []:
        if isinstance(option, Mapping) and option.get("id") == chosen:
            return dict(option)
    return {}


def _decision_pack_summary(
    path: Path,
    *,
    expected_decision_id: str,
    expected_category: str,
) -> dict[str, Any]:
    blockers: list[str] = []
    summary: dict[str, Any] = {
        "path": str(path),
        "expected_decision_id": expected_decision_id,
        "expected_category": expected_category,
        "signed": False,
        "blockers": blockers,
    }
    try:
        pack = load_pack(path)
    except (OSError, DecisionPackError) as exc:
        blockers.append("operator_decision_pack_missing_or_invalid")
        summary["error"] = str(exc)
        return summary

    signoff = pack.get("operator_signoff")
    chosen_option = ""
    signed_by = ""
    if isinstance(signoff, Mapping):
        chosen_option = str(signoff.get("chosen_option") or "")
        signed_by = str(signoff.get("signed_by") or "")

    summary.update({
        "decision_id": pack.get("decision_id"),
        "category": pack.get("category"),
        "chosen_option": chosen_option,
        "signed_by": signed_by,
        "signed": is_signed(pack),
        "structural_invariants": dict(pack.get("structural_invariants") or {}),
    })
    if pack.get("decision_id") != expected_decision_id:
        blockers.append("operator_decision_pack_id_mismatch")
    if pack.get("category") != expected_category:
        blockers.append("operator_decision_pack_category_mismatch")
    if not is_signed(pack):
        blockers.append("operator_decision_pack_unsigned")

    option = _chosen_option(pack)
    data = option.get("data") if isinstance(option, Mapping) else None
    if isinstance(data, Mapping):
        summary["chosen_option_data"] = dict(data)
    return summary


def _docker_pack_summary(path: Path) -> dict[str, Any]:
    summary = _decision_pack_summary(
        path,
        expected_decision_id="docker-latest-promotion",
        expected_category="docker_promotion",
    )
    blockers = summary["blockers"]
    data = summary.get("chosen_option_data") or {}
    invariants = summary.get("structural_invariants") or {}
    if summary.get("signed") is True:
        if summary.get("chosen_option") != "ghcr_stable_only":
            blockers.append("docker_promotion_choice_not_ghcr_stable_only")
        if data.get("moves_latest") is not False:
            blockers.append("docker_latest_move_not_forbidden_by_pack")
        if invariants.get("latest_move_is_operator_only") is not True:
            blockers.append("docker_latest_operator_only_invariant_missing")
        if invariants.get("agent_must_not_self_resolve") is not True:
            blockers.append("docker_agent_must_not_self_resolve_missing")
    return summary


def _torch_pack_summary(path: Path) -> dict[str, Any]:
    return _decision_pack_summary(
        path,
        expected_decision_id="torch-cuda-vs-cpu",
        expected_category="dependency_security",
    )


def _release_gate_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": report.get("schema_version"),
        "ok": report.get("ok") is True,
        "read_only": report.get("read_only") is True,
        "release_gate_decision": report.get("release_gate_decision"),
        "blockers": list(report.get("blockers") or []),
        "release_gate_effect": report.get("release_gate_effect"),
        "release_boundary_all_false": (
            report.get("release_boundary") == FALSE_RELEASE_BOUNDARY
        ),
    }


def _source_phase_synthesis_summary(
    phase_synthesis_refresh: dict[str, Any],
) -> dict[str, Any]:
    remaining_package = _remaining_package(
        phase_synthesis_refresh,
        RELEASE_SOAK_TASK_ID,
    )
    landed_package = _landed_package(phase_synthesis_refresh, RELEASE_SOAK_TASK_ID)
    return {
        "schema_version": phase_synthesis_refresh.get("schema_version"),
        "sprint_id": phase_synthesis_refresh.get("sprint_id"),
        "generated_at_utc": phase_synthesis_refresh.get("generated_at_utc"),
        "ok": phase_synthesis_refresh.get("ok") is True,
        "release_boundary_all_false": (
            phase_synthesis_refresh.get("release_boundary")
            == FALSE_RELEASE_BOUNDARY
        ),
        "remaining_release_soak_package": {
            "id": RELEASE_SOAK_TASK_ID,
            "status": remaining_package.get("status"),
            "owner": remaining_package.get("owner"),
        },
        "landed_release_soak_package": {
            "id": RELEASE_SOAK_TASK_ID,
            "status": landed_package.get("status"),
            "owner": landed_package.get("owner"),
        },
    }


def _collect_blockers(
    *,
    phase_synthesis_refresh: dict[str, Any],
    release_gate_recheck: dict[str, Any],
    torch_pack: dict[str, Any],
    docker_pack: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if phase_synthesis_refresh.get("ok") is not True:
        blockers.append("phase_synthesis_refresh_not_ok")
    if phase_synthesis_refresh.get("release_boundary") != FALSE_RELEASE_BOUNDARY:
        blockers.append("phase_synthesis_release_boundary_mutated")

    remaining_package = _remaining_package(
        phase_synthesis_refresh,
        RELEASE_SOAK_TASK_ID,
    )
    landed_package = _landed_package(phase_synthesis_refresh, RELEASE_SOAK_TASK_ID)
    release_soak_ready = (
        remaining_package.get("status") == "ready_for_release_boundary_review"
        or landed_package.get("status")
        == "complete_release_boundary_readiness_recorded"
    )
    if release_soak_ready is not True:
        blockers.append("release_soak_package_not_ready_for_boundary_review")

    gate = _release_gate_summary(release_gate_recheck)
    if gate["ok"] is not True:
        blockers.append("release_gate_recheck_report_not_ok")
    if gate["read_only"] is not True:
        blockers.append("release_gate_recheck_not_read_only")
    if gate["release_gate_effect"] != "none":
        blockers.append("release_gate_effect_not_none")
    if gate["release_boundary_all_false"] is not True:
        blockers.append("release_gate_release_boundary_mutated")
    if gate["release_gate_decision"] != "pass" or gate["blockers"]:
        blockers.append("release_gate_not_passed")

    for blocker in torch_pack.get("blockers") or []:
        blockers.append(f"torch_{blocker}")
    for blocker in docker_pack.get("blockers") or []:
        blockers.append(f"docker_{blocker}")
    return blockers


def _release_decision_packet(
    *,
    phase_synthesis_refresh: dict[str, Any],
    release_gate_recheck: dict[str, Any],
    ready: bool,
) -> dict[str, Any]:
    package = _source_release_soak_package(phase_synthesis_refresh)
    return {
        "schema_version": DECISION_PACKET_SCHEMA_VERSION,
        "id": FINALIZATION_TASK_ID,
        "decision_status": (
            "operator_finalization_required"
            if ready
            else "release_boundary_readiness_blocked"
        ),
        "default_recommendation": "hold_no_release_boundary_change",
        "source_status": package.get("status"),
        "source_acceptance": package.get("acceptance"),
        "release_gate_decision": release_gate_recheck.get("release_gate_decision"),
        "release_boundary_effect_before_followup": "none",
        "operator_input_required": True,
        "operator_finalization_required": True,
        "decision_options": [
            {
                "id": "hold_no_release_boundary_change",
                "summary": "Keep all release-boundary guardrails closed.",
                "operator_action_required": False,
                "tag_creation_allowed": False,
                "docker_latest_move_allowed": False,
                "docker_stable_move_allowed": False,
                "stable_release_claim_allowed": False,
                "external_effect_authority_change_allowed": False,
                "next_status": "hold_operator_finalization_required",
            },
            {
                "id": "operator_finalizes_release_boundary_separately",
                "summary": (
                    "Operator may perform a separate release finalization; "
                    "this report still performs no release action."
                ),
                "operator_action_required": True,
                "tag_creation_allowed": False,
                "docker_latest_move_allowed": False,
                "docker_stable_move_allowed": False,
                "stable_release_claim_allowed": False,
                "external_effect_authority_change_allowed": False,
                "next_status": "operator_release_finalization_required",
                "followup_requirements": [
                    "operator_only_release_finalization",
                    "fresh_exact_head_ci",
                    "rco_and_bridge_preflight",
                    "signed_tag_or_release_receipt_after_operator_action",
                ],
            },
        ],
    }


def build_report(
    *,
    phase_synthesis_refresh: dict[str, Any],
    release_gate_recheck: dict[str, Any],
    torch_decision_pack: Path,
    docker_decision_pack: Path,
    checked_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    checked_at_utc = checked_at_utc or _utc_now()
    torch_pack = _torch_pack_summary(torch_decision_pack)
    docker_pack = _docker_pack_summary(docker_decision_pack)
    blockers = _collect_blockers(
        phase_synthesis_refresh=phase_synthesis_refresh,
        release_gate_recheck=release_gate_recheck,
        torch_pack=torch_pack,
        docker_pack=docker_pack,
    )
    ready = not blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at_utc": _format_utc(checked_at_utc),
        "ok": ready,
        "release_boundary_status": (
            "ready_for_operator_finalization"
            if ready
            else "hold_release_boundary_review_required"
        ),
        "release_boundary_blockers": blockers,
        "operator_finalization_required": True,
        "source_phase_synthesis_refresh": _source_phase_synthesis_summary(
            phase_synthesis_refresh
        ),
        "source_release_gate_readonly_recheck": _release_gate_summary(
            release_gate_recheck
        ),
        "operator_decision_packs": {
            "torch_cuda_vs_cpu": torch_pack,
            "docker_latest_promotion": docker_pack,
        },
        "release_decision_packet": _release_decision_packet(
            phase_synthesis_refresh=phase_synthesis_refresh,
            release_gate_recheck=release_gate_recheck,
            ready=ready,
        ),
        "release_boundary_guardrails": {
            "release_boundary_effect": "none",
            "tag_creation_applied": False,
            "docker_latest_move_applied": False,
            "docker_stable_move_applied": False,
            "stable_release_claim_applied": False,
            "external_effect_authority_change_applied": False,
            "requires_operator_only_finalization": True,
        },
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "read_only_invariants": {
            "no_tag_created": True,
            "no_docker_latest_moved": True,
            "no_docker_stable_moved": True,
            "no_stable_release_claim": True,
            "no_external_effect_authority_change": True,
            "release_boundary_effect": "none",
        },
    }


def build_report_from_paths(
    *,
    phase_synthesis_refresh_path: Path,
    release_gate_recheck_path: Path,
    torch_decision_pack: Path,
    docker_decision_pack: Path,
    checked_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    return build_report(
        phase_synthesis_refresh=_read_json(phase_synthesis_refresh_path),
        release_gate_recheck=_read_json(release_gate_recheck_path),
        torch_decision_pack=torch_decision_pack,
        docker_decision_pack=docker_decision_pack,
        checked_at_utc=checked_at_utc,
    )


def strict_exit_code(report: dict[str, Any]) -> int:
    return 0 if report.get("ok") is True else STRICT_BLOCKED_EXIT_CODE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase-synthesis-refresh",
        type=Path,
        default=DEFAULT_PHASE_SYNTHESIS_REFRESH,
    )
    parser.add_argument(
        "--release-gate-recheck",
        type=Path,
        default=DEFAULT_RELEASE_GATE_RECHECK,
    )
    parser.add_argument(
        "--torch-decision-pack",
        type=Path,
        default=DEFAULT_TORCH_DECISION_PACK,
    )
    parser.add_argument(
        "--docker-decision-pack",
        type=Path,
        default=DEFAULT_DOCKER_DECISION_PACK,
    )
    parser.add_argument(
        "--checked-at-utc",
        type=_parse_timestamp,
        help="Override report timestamp, ISO-8601 UTC.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a blocked exit code when readiness blockers are present.",
    )
    args = parser.parse_args(argv)

    report = build_report_from_paths(
        phase_synthesis_refresh_path=args.phase_synthesis_refresh,
        release_gate_recheck_path=args.release_gate_recheck,
        torch_decision_pack=args.torch_decision_pack,
        docker_decision_pack=args.docker_decision_pack,
        checked_at_utc=args.checked_at_utc,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    if args.json:
        print(encoded, end="")
    return strict_exit_code(report) if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
