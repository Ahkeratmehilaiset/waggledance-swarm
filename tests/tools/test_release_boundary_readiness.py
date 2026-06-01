# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tools.run_release_boundary_readiness import (
    FALSE_RELEASE_BOUNDARY,
    SCHEMA_VERSION,
    build_report,
    main,
    strict_exit_code,
)


FIXED_NOW = dt.datetime(2026, 6, 1, 3, 0, tzinfo=dt.UTC)


def _phase_synthesis_refresh(
    *,
    status: str = "ready_for_release_boundary_review",
) -> dict[str, object]:
    return {
        "schema_version": "waggledance.magma_100h_phase_synthesis_refresh.v0",
        "sprint_id": "magma-100h-sprint3-2026-05-26",
        "generated_at_utc": "2026-06-01T02:40:00Z",
        "ok": True,
        "blockers": [],
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "remaining_work_packages": [
            {
                "id": "operator_gated_authority_activation_decision",
                "status": "operator_approval_missing_decision_packet_recorded",
                "owner": "operator",
            },
            {
                "id": "release_soak_evidence_blocker_resolution",
                "status": status,
                "owner": "operator,codex",
                "acceptance": (
                    "release boundary remains false until release gate passes "
                    "and an explicit release-boundary authorization exists"
                ),
            },
        ],
    }


def _release_gate_recheck(
    *,
    decision: str = "pass",
    blockers: list[str] | None = None,
) -> dict[str, object]:
    blockers = list(blockers or [])
    return {
        "schema_version": "waggledance.release_gate_readonly_recheck.v0",
        "ok": True,
        "read_only": True,
        "release_gate_decision": decision,
        "blockers": blockers,
        "release_gate_effect": "none",
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "read_only_invariants": {
            "release_gate_effect": "observation_only",
            "no_tag_created": True,
            "no_docker_latest_moved": True,
            "no_stable_release_claim": True,
            "no_external_effect_authority_change": True,
        },
    }


def _write_torch_pack(path: Path, *, signed: bool = True) -> Path:
    signed_by = '"operator:jani:2026-05-22T18:14:34Z"' if signed else '""'
    chosen = "A2_cu126" if signed else ""
    path.write_text(
        f"""schema_version: waggledance.operator_decision_pack.v1
decision_id: torch-cuda-vs-cpu
category: dependency_security
created_utc: 2026-05-22T14:00:00Z
author_agent: claude
options:
  - id: A1_cpu_only
    summary: CPU only
  - id: A2_cu126
    summary: CUDA 12.6
operator_signoff:
  signed_by: {signed_by}
  chosen_option: "{chosen}"
structural_invariants:
  no_main_branch_auto_merge: true
  dependency_change_lands_via_pr: true
  agent_must_not_self_resolve: true
""",
        encoding="utf-8",
    )
    return path


def _write_docker_pack(
    path: Path,
    *,
    signed: bool = True,
    chosen: str = "ghcr_stable_only",
    moves_latest_for_stable_only: bool = False,
) -> Path:
    signed_by = '"operator:jani:2026-05-22T18:14:34Z"' if signed else '""'
    chosen_option = chosen if signed else ""
    moves_latest = "true" if moves_latest_for_stable_only else "false"
    path.write_text(
        f"""schema_version: waggledance.operator_decision_pack.v1
decision_id: docker-latest-promotion
category: docker_promotion
created_utc: 2026-05-22T14:00:00Z
author_agent: claude
options:
  - id: ghcr_stable_only
    summary: Stable and version tag only
    data:
      moves_latest: {moves_latest}
      registries: ["ghcr.io"]
  - id: ghcr_stable_and_latest
    summary: Stable, version tag, and latest
    data:
      moves_latest: true
      registries: ["ghcr.io"]
operator_signoff:
  signed_by: {signed_by}
  chosen_option: "{chosen_option}"
structural_invariants:
  no_main_branch_auto_merge: true
  latest_move_is_operator_only: true
  agent_must_not_self_resolve: true
""",
        encoding="utf-8",
    )
    return path


def test_report_records_ready_for_operator_finalization_without_release_action(
    tmp_path: Path,
) -> None:
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["checked_at_utc"] == "2026-06-01T03:00:00Z"
    assert report["ok"] is True
    assert report["release_boundary_status"] == "ready_for_operator_finalization"
    assert report["release_boundary_blockers"] == []
    assert report["operator_finalization_required"] is True
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY
    assert report["release_boundary_guardrails"] == {
        "release_boundary_effect": "none",
        "tag_creation_applied": False,
        "docker_latest_move_applied": False,
        "docker_stable_move_applied": False,
        "stable_release_claim_applied": False,
        "external_effect_authority_change_applied": False,
        "requires_operator_only_finalization": True,
    }
    assert report["read_only_invariants"] == {
        "no_tag_created": True,
        "no_docker_latest_moved": True,
        "no_docker_stable_moved": True,
        "no_stable_release_claim": True,
        "no_external_effect_authority_change": True,
        "release_boundary_effect": "none",
    }
    docker_pack = report["operator_decision_packs"]["docker_latest_promotion"]
    assert docker_pack["chosen_option"] == "ghcr_stable_only"
    assert docker_pack["chosen_option_data"]["moves_latest"] is False
    packet = report["release_decision_packet"]
    assert packet["decision_status"] == "operator_finalization_required"
    assert packet["release_boundary_effect_before_followup"] == "none"
    assert packet["operator_finalization_required"] is True
    assert all(
        option["tag_creation_allowed"] is False
        and option["docker_latest_move_allowed"] is False
        and option["docker_stable_move_allowed"] is False
        and option["stable_release_claim_allowed"] is False
        and option["external_effect_authority_change_allowed"] is False
        for option in packet["decision_options"]
    )
    assert strict_exit_code(report) == 0


def test_landed_release_soak_package_keeps_readiness_idempotent(
    tmp_path: Path,
) -> None:
    phase = _phase_synthesis_refresh()
    phase["remaining_work_packages"] = [
        package
        for package in phase["remaining_work_packages"]
        if package["id"] != "release_soak_evidence_blocker_resolution"
    ]
    phase["landed_work_packages"] = [
        {
            "id": "release_soak_evidence_blocker_resolution",
            "status": "complete_release_boundary_readiness_recorded",
            "owner": "operator,codex",
        }
    ]

    report = build_report(
        phase_synthesis_refresh=phase,
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["source_phase_synthesis_refresh"][
        "remaining_release_soak_package"
    ] == {
        "id": "release_soak_evidence_blocker_resolution",
        "status": None,
        "owner": None,
    }
    assert report["source_phase_synthesis_refresh"][
        "landed_release_soak_package"
    ] == {
        "id": "release_soak_evidence_blocker_resolution",
        "status": "complete_release_boundary_readiness_recorded",
        "owner": "operator,codex",
    }
    assert strict_exit_code(report) == 0


def test_unsigned_docker_decision_pack_blocks_readiness(tmp_path: Path) -> None:
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(
            tmp_path / "docker.yaml",
            signed=False,
        ),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert report["release_boundary_status"] == "hold_release_boundary_review_required"
    assert "docker_operator_decision_pack_unsigned" in report[
        "release_boundary_blockers"
    ]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY
    assert strict_exit_code(report) == 2


def test_docker_latest_move_in_signed_pack_blocks_readiness(tmp_path: Path) -> None:
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(
            tmp_path / "docker.yaml",
            moves_latest_for_stable_only=True,
        ),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "docker_docker_latest_move_not_forbidden_by_pack" in report[
        "release_boundary_blockers"
    ]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY


def test_release_gate_hold_blocks_readiness_without_release_mutation(
    tmp_path: Path,
) -> None:
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(
            decision="hold",
            blockers=["soak_evidence_duration_lt_336h"],
        ),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "release_gate_not_passed" in report["release_boundary_blockers"]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY


def test_phase_status_must_be_ready_for_release_boundary_review(
    tmp_path: Path,
) -> None:
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(
            status="blocked_until_release_gate_soak_evidence_passes"
        ),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "release_soak_package_not_ready_for_boundary_review" in report[
        "release_boundary_blockers"
    ]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY


def test_cli_writes_readiness_report_and_honors_strict(
    tmp_path: Path,
    capsys,
) -> None:
    phase_path = tmp_path / "phase.json"
    gate_path = tmp_path / "gate.json"
    output_path = tmp_path / "release_boundary_readiness.json"
    phase_path.write_text(json.dumps(_phase_synthesis_refresh()), encoding="utf-8")
    gate_path.write_text(json.dumps(_release_gate_recheck()), encoding="utf-8")

    rc = main(
        [
            "--phase-synthesis-refresh",
            str(phase_path),
            "--release-gate-recheck",
            str(gate_path),
            "--torch-decision-pack",
            str(_write_torch_pack(tmp_path / "torch.yaml")),
            "--docker-decision-pack",
            str(_write_docker_pack(tmp_path / "docker.yaml")),
            "--checked-at-utc",
            "2026-06-01T03:00:00Z",
            "--output",
            str(output_path),
            "--json",
            "--strict",
        ]
    )

    assert rc == 0
    stdout_report = json.loads(capsys.readouterr().out)
    disk_report = json.loads(output_path.read_text(encoding="utf-8"))
    assert stdout_report == disk_report
    assert disk_report["ok"] is True
    assert disk_report["release_boundary_status"] == (
        "ready_for_operator_finalization"
    )
