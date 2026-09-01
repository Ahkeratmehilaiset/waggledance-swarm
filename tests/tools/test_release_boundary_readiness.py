# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import tools.run_release_boundary_readiness as boundary
from tools.run_release_boundary_readiness import (
    CANONICAL_SOAK_EVIDENCE,
    DEFAULT_PHASE_SYNTHESIS_REFRESH,
    DEFAULT_RELEASE_GATE_RECHECK,
    FALSE_RELEASE_BOUNDARY,
    ROOT,
    SCHEMA_VERSION,
    build_report,
    build_report_from_paths,
    main,
    strict_exit_code,
)


FIXED_NOW = dt.datetime(2026, 6, 1, 3, 0, tzinfo=dt.UTC)
CANONICAL_SOAK_COMMIT = json.loads(
    CANONICAL_SOAK_EVIDENCE.read_text(encoding="utf-8")
)["commit"]


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
        "checked_at_utc": "2026-06-01T02:55:00Z",
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


def _pass_live_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolated pass fixture: mock the live builder and the git HEAD.

    Only tests may take this path; the CLI and default runs always evaluate
    the real canonical gate.
    """
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda checked_at_utc: _release_gate_recheck(),
    )
    monkeypatch.setattr(boundary, "_git_head", lambda: CANONICAL_SOAK_COMMIT)


def _write_torch_pack(
    path: Path,
    *,
    signed: bool = True,
    scope_updates_yaml: str = "",
) -> Path:
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
{scope_updates_yaml}structural_invariants:
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


def _build_pass_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    torch_scope_yaml: str = "",
) -> dict[str, object]:
    _pass_live_gate(monkeypatch)
    return build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(
            tmp_path / "torch.yaml",
            scope_updates_yaml=torch_scope_yaml,
        ),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )


def test_report_records_ready_for_operator_finalization_without_release_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(tmp_path, monkeypatch)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["checked_at_utc"] == "2026-06-01T03:00:00Z"
    assert report["ok"] is True
    assert report["release_boundary_status"] == "ready_for_operator_finalization"
    assert report["release_boundary_blockers"] == []
    assert report["operator_finalization_required"] is True
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY
    assert report["source_live_release_gate"]["release_gate_decision"] == "pass"
    assert report["head_soak_binding"]["git_head"] == CANONICAL_SOAK_COMMIT
    assert report["head_soak_binding"]["soak_commit"] == CANONICAL_SOAK_COMMIT
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_live_gate(monkeypatch)
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


def test_unsigned_docker_decision_pack_blocks_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_live_gate(monkeypatch)
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


def test_docker_latest_move_in_signed_pack_blocks_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_live_gate(monkeypatch)
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


def test_stale_snapshot_hold_still_blocks_as_continuity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_live_gate(monkeypatch)
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_live_gate(monkeypatch)
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_live_gate(monkeypatch)
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


def test_cli_hold_returns_nonzero_without_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_path = tmp_path / "phase.json"
    gate_path = tmp_path / "gate.json"
    output_path = tmp_path / "release_boundary_readiness.json"
    phase_path.write_text(json.dumps(_phase_synthesis_refresh()), encoding="utf-8")
    gate_path.write_text(json.dumps(_release_gate_recheck()), encoding="utf-8")
    live_hold = _release_gate_recheck(
        decision="hold",
        blockers=["soak_evidence_not_reproducible"],
    )
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda checked_at_utc: live_hold,
    )
    monkeypatch.setattr(boundary, "_git_head", lambda: CANONICAL_SOAK_COMMIT)

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
        ]
    )

    assert rc == 2
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY
    assert "live_release_gate_not_passed" in report["release_boundary_blockers"]


# --- Live-gate authority: the stale-snapshot false green cannot recur ---


def test_repository_defaults_hold_for_live_gate_reason() -> None:
    """Unmocked repository defaults must HOLD via the live gate, never ready."""
    report = build_report_from_paths(
        phase_synthesis_refresh_path=ROOT / DEFAULT_PHASE_SYNTHESIS_REFRESH,
        release_gate_recheck_path=ROOT / DEFAULT_RELEASE_GATE_RECHECK,
        torch_decision_pack=ROOT / "docs/operator_inbox/torch-cuda-vs-cpu.yaml",
        docker_decision_pack=(
            ROOT / "docs/operator_inbox/docker-latest-promotion.yaml"
        ),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "live_release_gate_not_passed" in report["release_boundary_blockers"]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY
    assert strict_exit_code(report) == 2


def test_stale_passing_snapshot_cannot_grant_readiness(tmp_path: Path) -> None:
    """The original defect: a checked-in passing snapshot granted readiness."""
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(decision="pass"),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "live_release_gate_not_passed" in report["release_boundary_blockers"]
    assert strict_exit_code(report) == 2


def test_live_evaluator_exception_is_a_named_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(checked_at_utc):
        raise RuntimeError("evaluator exploded")

    monkeypatch.setattr(boundary, "_run_live_release_gate", _raise)
    monkeypatch.setattr(boundary, "_git_head", lambda: CANONICAL_SOAK_COMMIT)
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "live_release_gate_evaluator_error" in report[
        "release_boundary_blockers"
    ]
    assert report["source_live_release_gate"]["ok"] is False
    assert strict_exit_code(report) == 2


@pytest.mark.parametrize("malformed", [None, [], {"blockers": "none"}])
def test_malformed_live_report_is_a_named_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformed: object,
) -> None:
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda checked_at_utc: malformed,
    )
    monkeypatch.setattr(boundary, "_git_head", lambda: CANONICAL_SOAK_COMMIT)

    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "live_release_gate_report_malformed" in report[
        "release_boundary_blockers"
    ]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY


@pytest.mark.parametrize(
    ("source", "value", "expected"),
    [
        (
            "phase",
            None,
            "phase_synthesis_generated_at_utc_missing",
        ),
        (
            "phase",
            "not-a-time",
            "phase_synthesis_generated_at_utc_malformed",
        ),
        (
            "phase",
            "2026-06-01T02:40:00",
            "phase_synthesis_generated_at_utc_naive",
        ),
        (
            "phase",
            "2999-01-01T00:00:00Z",
            "phase_synthesis_generated_at_utc_in_future",
        ),
        (
            "gate",
            None,
            "release_gate_recheck_checked_at_utc_missing",
        ),
        (
            "gate",
            "not-a-time",
            "release_gate_recheck_checked_at_utc_malformed",
        ),
        (
            "gate",
            "2026-06-01T02:55:00",
            "release_gate_recheck_checked_at_utc_naive",
        ),
        (
            "gate",
            "2999-01-01T00:00:00Z",
            "release_gate_recheck_checked_at_utc_in_future",
        ),
    ],
)
def test_lineage_timestamps_fail_closed_without_an_age_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    value: str | None,
    expected: str,
) -> None:
    _pass_live_gate(monkeypatch)
    phase = _phase_synthesis_refresh()
    gate = _release_gate_recheck()
    if source == "phase":
        phase["generated_at_utc"] = value
    else:
        gate["checked_at_utc"] = value

    report = build_report(
        phase_synthesis_refresh=phase,
        release_gate_recheck=gate,
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert expected in report["release_boundary_blockers"]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "live_release_gate_checked_at_utc_missing"),
        ("not-a-time", "live_release_gate_checked_at_utc_malformed"),
        ("2026-06-01T02:55:00", "live_release_gate_checked_at_utc_naive"),
        ("2999-01-01T00:00:00Z", "live_release_gate_checked_at_utc_in_future"),
    ],
)
def test_invalid_live_timestamp_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    expected: str,
) -> None:
    live_report = _release_gate_recheck()
    if value is None:
        live_report.pop("checked_at_utc")
    else:
        live_report["checked_at_utc"] = value
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda checked_at_utc: live_report,
    )
    monkeypatch.setattr(boundary, "_git_head", lambda: CANONICAL_SOAK_COMMIT)

    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert expected in report["release_boundary_blockers"]


def test_naive_or_future_checked_at_blocks_and_uses_safe_live_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dt.datetime] = []
    monkeypatch.setattr(boundary, "_utc_now", lambda: FIXED_NOW)
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda checked_at_utc: (
            seen.append(checked_at_utc) or _release_gate_recheck()
        ),
    )
    monkeypatch.setattr(boundary, "_git_head", lambda: CANONICAL_SOAK_COMMIT)
    common = {
        "phase_synthesis_refresh": _phase_synthesis_refresh(),
        "release_gate_recheck": _release_gate_recheck(),
        "torch_decision_pack": _write_torch_pack(tmp_path / "torch.yaml"),
        "docker_decision_pack": _write_docker_pack(tmp_path / "docker.yaml"),
    }

    naive = build_report(
        **common,
        checked_at_utc=dt.datetime(2026, 6, 1, 3, 0),
    )
    future = build_report(
        **common,
        checked_at_utc=FIXED_NOW + dt.timedelta(seconds=1),
    )

    assert "checked_at_utc_naive" in naive["release_boundary_blockers"]
    assert "checked_at_utc_in_future" in future["release_boundary_blockers"]
    assert seen == [FIXED_NOW, FIXED_NOW]


# --- Head/soak binding ---


def test_git_head_unavailable_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda checked_at_utc: _release_gate_recheck(),
    )

    def _raise() -> str:
        raise RuntimeError("git missing")

    monkeypatch.setattr(boundary, "_git_head", _raise)
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "git_head_unavailable" in report["release_boundary_blockers"]


def test_git_head_not_full_hex_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda checked_at_utc: _release_gate_recheck(),
    )
    monkeypatch.setattr(boundary, "_git_head", lambda: "abc123")
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "git_head_not_full_hex" in report["release_boundary_blockers"]


def test_git_head_soak_commit_mismatch_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        boundary,
        "_run_live_release_gate",
        lambda checked_at_utc: _release_gate_recheck(),
    )
    monkeypatch.setattr(boundary, "_git_head", lambda: "f" * 40)
    report = build_report(
        phase_synthesis_refresh=_phase_synthesis_refresh(),
        release_gate_recheck=_release_gate_recheck(),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=_write_docker_pack(tmp_path / "docker.yaml"),
        checked_at_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "git_head_does_not_match_soak_commit" in report[
        "release_boundary_blockers"
    ]


# --- Torch operator_signoff.scope_updates fail-closed inspection ---


_SCOPE_SIGNED = """  scope_updates:
    - recorded_utc: 2026-08-26T07:00:00Z
      recorded_by: "codex-lead-1"
      signed_by: "operator:jani:2026-08-26T07:00:00Z"
      lock_evidence_contract:
        operator_signature_required: true
"""

_SCOPE_MISSING_SIGNER = """  scope_updates:
    - recorded_utc: 2026-08-26T07:00:00Z
      recorded_by: "codex-lead-1"
      lock_evidence_contract:
        operator_signature_required: true
"""

_SCOPE_WRONG_SIGNER = """  scope_updates:
    - recorded_utc: 2026-08-26T07:00:00Z
      signed_by: "agent:codex-lead-1:2026-08-26T07:00:00Z"
      lock_evidence_contract:
        operator_signature_required: true
"""

_SCOPE_TRUTHY_FLAG = """  scope_updates:
    - recorded_utc: 2026-08-26T07:00:00Z
      signed_by: "operator:jani:2026-08-26T07:00:00Z"
      lock_evidence_contract:
        operator_signature_required: "yes"
"""

_SCOPE_FLAG_MISSING = """  scope_updates:
    - recorded_utc: 2026-08-26T07:00:00Z
      lock_evidence_contract:
        note: "contract present but required flag absent"
"""

_SCOPE_HISTORICAL = """  scope_updates:
    - recorded_utc: 2026-05-27T08:14:26Z
      recorded_by: "codex-lead-1"
      summary: "historical dependency-floor authorization, no contract"
"""

_SCOPE_NOT_A_LIST = """  scope_updates: "not-a-list"
"""


def test_exact_signed_required_scope_update_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_SIGNED,
    )
    assert report["ok"] is True
    assert report["release_boundary_blockers"] == []


def test_required_scope_update_without_direct_signer_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_MISSING_SIGNER,
    )
    assert report["ok"] is False
    assert "torch_scope_update_0_missing_direct_signed_by" in report[
        "release_boundary_blockers"
    ]


def test_required_scope_update_with_wrong_identity_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_WRONG_SIGNER,
    )
    assert report["ok"] is False
    assert "torch_scope_update_0_signer_identity_mismatch" in report[
        "release_boundary_blockers"
    ]


def test_truthy_non_bool_required_flag_blocks_as_malformed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_TRUTHY_FLAG,
    )
    assert report["ok"] is False
    assert "torch_scope_update_0_required_flag_malformed" in report[
        "release_boundary_blockers"
    ]


def test_contract_without_required_flag_blocks_as_ambiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_FLAG_MISSING,
    )
    assert report["ok"] is False
    assert "torch_scope_update_0_required_flag_missing" in report[
        "release_boundary_blockers"
    ]


def test_historical_update_without_contract_does_not_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_HISTORICAL,
    )
    assert report["ok"] is True
    assert report["release_boundary_blockers"] == []


def test_scope_updates_not_a_list_blocks_as_malformed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _build_pass_report(
        tmp_path,
        monkeypatch,
        torch_scope_yaml=_SCOPE_NOT_A_LIST,
    )
    assert report["ok"] is False
    assert "torch_scope_updates_malformed" in report["release_boundary_blockers"]
