# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest

import tools.run_release_boundary_readiness as readiness
from tools.run_release_boundary_readiness import (
    DEFAULT_DOCKER_DECISION_PACK,
    FALSE_RELEASE_BOUNDARY,
    ROOT,
    SCHEMA_VERSION,
    build_report,
    main,
    strict_exit_code,
)
from tools.run_release_docker_policy_evidence import (
    AUTH_SCHEMA_VERSION,
    SCHEMA_VERSION as DOCKER_POLICY_SCHEMA_VERSION,
)


FIXED_NOW = dt.datetime(2026, 6, 1, 3, 0, tzinfo=dt.UTC)
SUBJECT_COMMIT = "dc76e81cd8c804608bfaedf951220e46ff1baffa"
REAL_RETAINED_FILE_SOURCE_BINDING = readiness._retained_file_source_binding
REAL_RETAINED_JSON_SOURCE_BINDING = readiness._retained_json_source_binding
REAL_LOCAL_ARTIFACT_REVALIDATION = (
    readiness._revalidate_local_artifact_evidence
)


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
        "gate": {
            "decision": decision,
            "blockers": blockers,
        },
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


def _write_torch_pack(
    path: Path,
    *,
    signed: bool = True,
    signed_by: str | None = None,
) -> Path:
    signoff_value = (
        signed_by
        if signed_by is not None
        else "operator:jani:2026-05-22T18:14:34Z"
    )
    signed_by_yaml = json.dumps(signoff_value if signed else "")
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
  signed_by: {signed_by_yaml}
  chosen_option: "{chosen}"
structural_invariants:
  no_main_branch_auto_merge: true
  dependency_change_lands_via_pr: true
  agent_must_not_self_resolve: true
""",
        encoding="utf-8",
    )
    return path


def _docker_policy_evidence(
    *,
    signed: bool = True,
    chosen: str = "ghcr_stable_only",
    moves_latest_for_stable_only: bool = False,
    decision_id: str = "docker-v3-12-0-stable-promotion",
    target_version: str | None = "v3.12.0",
    commit: str | None = SUBJECT_COMMIT,
    authorization_commit: str | None = None,
) -> dict[str, object]:
    authorization = None
    source_binding: dict[str, object] | None = None
    if signed:
        authorization_commit = (
            commit if authorization_commit is None else authorization_commit
        )
        authorization = {
            "schema_version": AUTH_SCHEMA_VERSION,
            "target_version": target_version,
            "commit": authorization_commit,
            "commit_scope": "exact",
            "decision_pack_target_version": target_version,
            "decision_pack_commit": authorization_commit,
            "stable_promotion_authorized": True,
            "docker_promotion_deferred": False,
            "move_latest": (
                "yes" if moves_latest_for_stable_only else "no"
            ),
            "authorization_id": (
                f"decision-pack:{decision_id}:{chosen}:jani"
            ),
            "authorized_at_utc": "2026-05-22T18:14:34Z",
            "decision_pack_created_at_utc": "2026-05-22T14:00:00Z",
            "decision_pack_path": (
                "docs/operator_inbox/"
                "docker-v3-12-0-stable-promotion.yaml"
            ),
            "decision_pack_sha256": "sha256:" + ("1" * 64),
            "source": "operator_decision_pack",
            "decision_id": decision_id,
            "chosen_option": chosen,
            "operator_id": "jani",
        }
        source_binding = {
            "verified": True,
            "reason": "verified",
            "decision_pack_path": authorization["decision_pack_path"],
            "decision_pack_sha256": authorization["decision_pack_sha256"],
        }
    return {
        "schema_version": DOCKER_POLICY_SCHEMA_VERSION,
        "target_version": target_version,
        "commit": commit,
        "generated_at_utc": "2026-05-22T18:15:00Z",
        "operator_authorization": authorization,
        "operator_authorization_source_binding": source_binding,
        "post_tag_runtime_verification_required": True,
        "latest_move_requires_operator_opt_in": True,
        "blockers": [],
        "docker_stable_policy": "finalized",
    }


def _soak_evidence(*, commit: str = SUBJECT_COMMIT) -> dict[str, object]:
    return {
        "axis_a_regression": "pass",
        "axis_b_gate": "pass",
        "ci_status": "pass",
        "docker_stable_policy": "finalized",
        "duration_hours": 336,
        "ended_at_utc": "2026-05-24T00:00:00Z",
        "error_log_clean": True,
        "profile_s_smoke": "pass",
        "release_notes_anti_claims": "pass",
        "schema_version": "waggledance.release_soak.v1",
        "collection_mode": "local_artifacts",
        "security_privacy_gate": "pass",
        "silent_failures": 0,
        "started_at_utc": "2026-05-10T00:00:00Z",
        "target_version": "v3.12.0",
        "commit": commit,
        "result": "pass",
    }


@pytest.fixture(autouse=True)
def _stub_docker_policy_evaluator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        readiness,
        "evaluate_docker_policy_report",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        readiness,
        "_retained_file_source_binding",
        lambda *args, **kwargs: {
            "verified": True,
            "reason": "verified",
        },
    )
    monkeypatch.setattr(
        readiness,
        "_retained_json_source_binding",
        lambda *args, **kwargs: {
            "verified": True,
            "reason": "verified",
        },
    )
    monkeypatch.setattr(
        readiness,
        "_revalidate_local_artifact_evidence",
        lambda *args, **kwargs: {
            "verified": True,
            "reason": "verified",
            "mismatches": [],
        },
    )


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def test_retained_source_binding_uses_real_git_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    relative_path = readiness.DEFAULT_PHASE_SYNTHESIS_REFRESH
    canonical_path = source_root / relative_path
    canonical_path.parent.mkdir(parents=True)
    report = {
        "schema_version": readiness.PHASE_SYNTHESIS_SCHEMA_VERSION,
        "ok": True,
        "blockers": [],
    }
    encoded = json.dumps(report, sort_keys=True) + "\n"
    canonical_path.write_text(encoded, encoding="utf-8")
    _git(source_root, "init")
    _git(source_root, "config", "user.name", "WaggleDance Test")
    _git(
        source_root,
        "config",
        "user.email",
        "test@waggledance.invalid",
    )
    _git(source_root, "config", "core.autocrlf", "false")
    _git(source_root, "add", relative_path.as_posix())
    _git(source_root, "commit", "-m", "fixture")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "attacker.git"))

    clean = REAL_RETAINED_FILE_SOURCE_BINDING(
        relative_path,
        expected_relative_path=relative_path,
        source_root=source_root,
    )
    clean_json = REAL_RETAINED_JSON_SOURCE_BINDING(
        report,
        relative_path,
        expected_relative_path=relative_path,
        source_root=source_root,
    )
    monkeypatch.delenv("GIT_DIR")
    wrong_path = REAL_RETAINED_FILE_SOURCE_BINDING(
        source_root / "scratch.json",
        expected_relative_path=relative_path,
        source_root=source_root,
    )
    canonical_path.write_text(
        json.dumps({**report, "ok": False}),
        encoding="utf-8",
    )
    worktree_mismatch = REAL_RETAINED_FILE_SOURCE_BINDING(
        relative_path,
        expected_relative_path=relative_path,
        source_root=source_root,
    )
    _git(source_root, "add", relative_path.as_posix())
    index_mismatch = REAL_RETAINED_FILE_SOURCE_BINDING(
        relative_path,
        expected_relative_path=relative_path,
        source_root=source_root,
    )

    assert clean["verified"] is True
    assert clean_json["verified"] is True
    assert wrong_path["reason"] == "path_mismatch"
    assert worktree_mismatch["reason"] == "worktree_mismatch"
    assert index_mismatch["reason"] == "index_mismatch"


def _build_report(
    *,
    tmp_path: Path,
    phase_synthesis_refresh: dict[str, object] | None = None,
    release_gate_recheck: dict[str, object] | None = None,
    docker_policy_evidence: dict[str, object] | None = None,
    soak_evidence: dict[str, object] | None = None,
    soak_evidence_path: Path | None = readiness.DEFAULT_SOAK_EVIDENCE,
    docker_decision_pack: Path = DEFAULT_DOCKER_DECISION_PACK,
    checked_at_utc: dt.datetime = FIXED_NOW,
) -> dict[str, object]:
    soak = (
        soak_evidence
        if soak_evidence is not None
        else _soak_evidence()
    )
    if soak_evidence_path is not None:
        retained_soak_path = (
            soak_evidence_path
            if soak_evidence_path.is_absolute()
            else tmp_path / soak_evidence_path
        )
        retained_soak_path.parent.mkdir(parents=True, exist_ok=True)
        retained_soak_path.write_text(json.dumps(soak), encoding="utf-8")
    return build_report(
        phase_synthesis_refresh=(
            phase_synthesis_refresh or _phase_synthesis_refresh()
        ),
        release_gate_recheck=(
            release_gate_recheck or _release_gate_recheck()
        ),
        torch_decision_pack=_write_torch_pack(tmp_path / "torch.yaml"),
        docker_decision_pack=docker_decision_pack,
        docker_policy_evidence=(
            docker_policy_evidence or _docker_policy_evidence()
        ),
        soak_evidence=soak,
        soak_evidence_path=soak_evidence_path,
        source_root=tmp_path,
        checked_at_utc=checked_at_utc,
    )


def test_report_records_ready_for_operator_finalization_without_release_action(
    tmp_path: Path,
) -> None:
    report = _build_report(tmp_path=tmp_path)

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


def test_future_checked_at_override_holds_boundary(tmp_path: Path) -> None:
    report = _build_report(
        tmp_path=tmp_path,
        checked_at_utc=dt.datetime(2999, 1, 1, tzinfo=dt.UTC),
    )

    assert report["ok"] is False
    assert "checked_at_utc_in_future" in report["release_boundary_blockers"]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY


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

    report = _build_report(
        tmp_path=tmp_path,
        phase_synthesis_refresh=phase,
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
    report = _build_report(
        tmp_path=tmp_path,
        docker_policy_evidence=_docker_policy_evidence(signed=False),
    )

    assert report["ok"] is False
    assert report["release_boundary_status"] == "hold_release_boundary_review_required"
    assert "docker_operator_decision_pack_unsigned" in report[
        "release_boundary_blockers"
    ]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY
    assert strict_exit_code(report) == 2


def test_historical_or_unscoped_docker_pack_blocks_readiness(
    tmp_path: Path,
) -> None:
    historical = _build_report(
        tmp_path=tmp_path,
        docker_policy_evidence=_docker_policy_evidence(
            decision_id="docker-latest-promotion",
        ),
    )
    assert historical["ok"] is False
    assert "docker_operator_decision_pack_id_mismatch" in historical[
        "release_boundary_blockers"
    ]

    unscoped = _build_report(
        tmp_path=tmp_path,
        docker_policy_evidence=_docker_policy_evidence(
            target_version=None,
            commit=None,
        ),
    )
    assert unscoped["ok"] is False
    assert "docker_docker_target_version_not_exact" in unscoped[
        "release_boundary_blockers"
    ]
    assert "docker_docker_commit_scope_not_exact" in unscoped[
        "release_boundary_blockers"
    ]


def test_docker_latest_move_in_signed_pack_blocks_readiness(tmp_path: Path) -> None:
    report = _build_report(
        tmp_path=tmp_path,
        docker_policy_evidence=_docker_policy_evidence(
            moves_latest_for_stable_only=True,
        ),
    )

    assert report["ok"] is False
    assert "docker_docker_latest_move_not_forbidden_by_pack" in report[
        "release_boundary_blockers"
    ]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY


def test_docker_policy_evaluator_blockers_hold_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        readiness,
        "evaluate_docker_policy_report",
        lambda *args, **kwargs: [
            "operator_authorization_source_not_verified:"
            "decision_pack_not_tracked"
        ],
    )

    report = _build_report(tmp_path=tmp_path)

    assert report["ok"] is False
    assert "docker_docker_policy_evidence_invalid" in report[
        "release_boundary_blockers"
    ]
    docker = report["operator_decision_packs"]["docker_latest_promotion"]
    assert docker["policy_evaluation_blockers"] == [
        "operator_authorization_source_not_verified:"
        "decision_pack_not_tracked"
    ]


def test_docker_policy_commit_must_match_bound_authorization(
    tmp_path: Path,
) -> None:
    report = _build_report(
        tmp_path=tmp_path,
        docker_policy_evidence=_docker_policy_evidence(
            authorization_commit="a" * 40,
        ),
    )

    assert report["ok"] is False
    assert "docker_docker_authorization_commit_mismatch" in report[
        "release_boundary_blockers"
    ]


def test_docker_policy_commit_must_match_release_soak_subject(
    tmp_path: Path,
) -> None:
    report = _build_report(
        tmp_path=tmp_path,
        soak_evidence=_soak_evidence(commit="b" * 40),
    )

    assert report["ok"] is False
    assert "docker_docker_policy_soak_commit_mismatch" in report[
        "release_boundary_blockers"
    ]


@pytest.mark.parametrize(
    ("field", "value", "expected_blocker"),
    [
        (
            "schema_version",
            "garbage",
            "release_soak_schema_version_invalid",
        ),
        (
            "target_version",
            "v999",
            "release_soak_target_version_mismatch",
        ),
        (
            "collection_mode",
            "manual",
            "release_soak_collection_mode_invalid",
        ),
        (
            "result",
            "hold",
            "release_soak_result_not_pass",
        ),
        (
            "docker_stable_policy",
            "draft",
            "release_soak_docker_policy_not_finalized",
        ),
        (
            "started_at_utc",
            None,
            "release_soak_started_at_invalid",
        ),
        (
            "ended_at_utc",
            "not-a-timestamp",
            "release_soak_ended_at_invalid",
        ),
        (
            "duration_hours",
            float("inf"),
            "release_soak_duration_insufficient",
        ),
        (
            "started_at_utc",
            "2026-05-23T23:59:59Z",
            "release_soak_elapsed_duration_insufficient",
        ),
        (
            "silent_failures",
            False,
            "release_soak_silent_failures_nonzero",
        ),
        (
            "silent_failures",
            0.0,
            "release_soak_silent_failures_nonzero",
        ),
        (
            "started_at_utc",
            "0001-01-01T00:00:00+14:00",
            "release_soak_started_at_invalid",
        ),
        (
            "ended_at_utc",
            "9999-12-31T23:59:59-14:00",
            "release_soak_ended_at_invalid",
        ),
    ],
)
def test_invalid_release_soak_content_blocks_boundary(
    tmp_path: Path,
    field: str,
    value: object,
    expected_blocker: str,
) -> None:
    soak = _soak_evidence()
    soak[field] = value

    report = _build_report(tmp_path=tmp_path, soak_evidence=soak)

    assert report["ok"] is False
    assert expected_blocker in report["release_boundary_blockers"]


@pytest.mark.parametrize(
    ("started_at", "ended_at", "expected_blocker"),
    [
        (
            "2099-01-01T00:00:00Z",
            "2099-01-15T00:00:00Z",
            "release_soak_ended_at_in_future",
        ),
        (
            "2020-01-01T00:00:00Z",
            "2020-01-15T00:00:00Z",
            "release_soak_ended_before_required_window_end",
        ),
    ],
)
def test_release_soak_window_must_cover_required_dates(
    tmp_path: Path,
    started_at: str,
    ended_at: str,
    expected_blocker: str,
) -> None:
    soak = _soak_evidence()
    soak["started_at_utc"] = started_at
    soak["ended_at_utc"] = ended_at

    report = _build_report(tmp_path=tmp_path, soak_evidence=soak)

    assert report["ok"] is False
    assert expected_blocker in report["release_boundary_blockers"]


def test_release_soak_huge_json_integer_fails_closed(tmp_path: Path) -> None:
    soak_path = tmp_path / readiness.DEFAULT_SOAK_EVIDENCE
    soak_path.parent.mkdir(parents=True, exist_ok=True)
    soak_path.write_text(
        '{"duration_hours":' + ("9" * 5000) + "}",
        encoding="utf-8",
    )

    loaded = readiness._read_json(soak_path)
    summary = readiness._release_soak_summary(
        loaded,
        path=readiness.DEFAULT_SOAK_EVIDENCE,
        source_root=tmp_path,
        checked_at_utc=FIXED_NOW,
    )

    assert loaded == {}
    assert "canonical_file_unreadable" in summary["blockers"]
    assert "duration_insufficient" in summary["blockers"]


def test_release_soak_duplicate_json_key_fails_closed(tmp_path: Path) -> None:
    soak_path = tmp_path / readiness.DEFAULT_SOAK_EVIDENCE
    soak_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(_soak_evidence()).replace(
        '"duration_hours": 336',
        '"duration_hours": 1, "duration_hours": 336',
    )
    soak_path.write_text(encoded, encoding="utf-8")

    loaded = readiness._read_json(soak_path)
    summary = readiness._release_soak_summary(
        loaded,
        path=readiness.DEFAULT_SOAK_EVIDENCE,
        source_root=tmp_path,
        checked_at_utc=FIXED_NOW,
    )

    assert loaded == {}
    assert "canonical_file_unreadable" in summary["blockers"]


def test_release_soak_canonical_file_must_be_regular(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soak_path = tmp_path / readiness.DEFAULT_SOAK_EVIDENCE
    soak_path.parent.mkdir(parents=True, exist_ok=True)
    retained = _soak_evidence()
    soak_path.write_text(json.dumps(retained), encoding="utf-8")
    monkeypatch.setattr(readiness, "_is_regular_file", lambda _path: False)

    summary = readiness._release_soak_summary(
        retained,
        path=readiness.DEFAULT_SOAK_EVIDENCE,
        source_root=tmp_path,
        checked_at_utc=FIXED_NOW,
    )

    assert "canonical_file_not_regular" in summary["blockers"]


def test_release_soak_canonical_path_rejects_ancestor_indirection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soak_path = tmp_path / readiness.DEFAULT_SOAK_EVIDENCE
    soak_path.parent.mkdir(parents=True, exist_ok=True)
    retained = _soak_evidence()
    soak_path.write_text(json.dumps(retained), encoding="utf-8")
    monkeypatch.setattr(
        readiness,
        "_path_has_indirection",
        lambda _path, *, root: True,
    )

    summary = readiness._release_soak_summary(
        retained,
        path=readiness.DEFAULT_SOAK_EVIDENCE,
        source_root=tmp_path,
        checked_at_utc=FIXED_NOW,
    )

    assert "canonical_path_indirection" in summary["blockers"]


@pytest.mark.parametrize(
    ("file_attributes", "reparse_tag"),
    [
        (readiness.WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT, 0),
        (0, readiness.WINDOWS_IO_REPARSE_TAG_MOUNT_POINT),
    ],
)
def test_release_soak_detects_windows_reparse_metadata_without_is_junction(
    file_attributes: int,
    reparse_tag: int,
) -> None:
    metadata = type(
        "ReparseMetadata",
        (),
        {
            "st_file_attributes": file_attributes,
            "st_reparse_tag": reparse_tag,
        },
    )()

    assert readiness._stat_is_reparse_point(metadata)


def test_release_soak_content_binding_preserves_scalar_types(
    tmp_path: Path,
) -> None:
    soak_path = tmp_path / readiness.DEFAULT_SOAK_EVIDENCE
    soak_path.parent.mkdir(parents=True, exist_ok=True)
    retained = _soak_evidence()
    retained["silent_failures"] = False
    retained["error_log_clean"] = 1
    soak_path.write_text(json.dumps(retained), encoding="utf-8")

    summary = readiness._release_soak_summary(
        _soak_evidence(),
        path=readiness.DEFAULT_SOAK_EVIDENCE,
        source_root=tmp_path,
        checked_at_utc=FIXED_NOW,
    )

    assert "canonical_content_mismatch" in summary["blockers"]


def test_self_declared_local_mode_requires_artifact_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forged = _soak_evidence()
    soak_path = tmp_path / readiness.DEFAULT_SOAK_EVIDENCE
    soak_path.parent.mkdir(parents=True, exist_ok=True)
    soak_path.write_text(json.dumps(forged), encoding="utf-8")
    monkeypatch.setattr(
        readiness,
        "_revalidate_local_artifact_evidence",
        REAL_LOCAL_ARTIFACT_REVALIDATION,
    )

    summary = readiness._release_soak_summary(
        forged,
        path=readiness.DEFAULT_SOAK_EVIDENCE,
        source_root=tmp_path,
        checked_at_utc=FIXED_NOW,
    )

    assert "local_artifacts_not_verified" in summary["blockers"]
    revalidation = summary["local_artifact_revalidation"]
    assert revalidation["verified"] is False
    assert revalidation["mismatches"]


def test_noncanonical_or_missing_soak_path_blocks_boundary(
    tmp_path: Path,
) -> None:
    scratch = _build_report(
        tmp_path=tmp_path,
        soak_evidence_path=tmp_path / "scratch-soak.json",
    )
    missing = _build_report(
        tmp_path=tmp_path,
        soak_evidence_path=None,
    )

    assert scratch["ok"] is False
    assert missing["ok"] is False
    blocker = "release_soak_canonical_path_mismatch"
    assert blocker in scratch["release_boundary_blockers"]
    assert blocker in missing["release_boundary_blockers"]


def test_untracked_scratch_docker_pack_cannot_make_boundary_ready(
    tmp_path: Path,
) -> None:
    report = _build_report(
        tmp_path=tmp_path,
        docker_decision_pack=tmp_path / "scratch-pack.yaml",
    )

    assert report["ok"] is False
    assert "docker_operator_decision_pack_path_mismatch" in report[
        "release_boundary_blockers"
    ]


def test_release_gate_hold_blocks_readiness_without_release_mutation(
    tmp_path: Path,
) -> None:
    report = _build_report(
        tmp_path=tmp_path,
        release_gate_recheck=_release_gate_recheck(
            decision="hold",
            blockers=["soak_evidence_duration_lt_336h"],
        ),
    )

    assert report["ok"] is False
    assert "release_gate_not_passed" in report["release_boundary_blockers"]
    assert report["release_boundary"] == FALSE_RELEASE_BOUNDARY


@pytest.mark.parametrize("schema_version", [None, "untrusted.phase.v999"])
def test_phase_synthesis_requires_exact_schema(
    tmp_path: Path,
    schema_version: object,
) -> None:
    phase = _phase_synthesis_refresh()
    phase["schema_version"] = schema_version

    report = _build_report(
        tmp_path=tmp_path,
        phase_synthesis_refresh=phase,
    )

    assert report["ok"] is False
    assert "phase_synthesis_schema_invalid" in report[
        "release_boundary_blockers"
    ]


def test_phase_synthesis_blockers_require_string_list(tmp_path: Path) -> None:
    phase = _phase_synthesis_refresh()
    phase["blockers"] = 0

    report = _build_report(
        tmp_path=tmp_path,
        phase_synthesis_refresh=phase,
    )

    assert report["ok"] is False
    assert "phase_synthesis_blockers_invalid" in report[
        "release_boundary_blockers"
    ]


@pytest.mark.parametrize("schema_version", [None, "untrusted.gate.v999"])
def test_release_gate_recheck_requires_exact_schema(
    tmp_path: Path,
    schema_version: object,
) -> None:
    gate = _release_gate_recheck()
    gate["schema_version"] = schema_version

    report = _build_report(
        tmp_path=tmp_path,
        release_gate_recheck=gate,
    )

    assert report["ok"] is False
    assert "release_gate_recheck_schema_invalid" in report[
        "release_boundary_blockers"
    ]


def test_release_gate_recheck_blockers_require_string_list(
    tmp_path: Path,
) -> None:
    gate = _release_gate_recheck()
    gate["blockers"] = 0

    report = _build_report(
        tmp_path=tmp_path,
        release_gate_recheck=gate,
    )

    assert report["ok"] is False
    assert "release_gate_recheck_blockers_invalid" in report[
        "release_boundary_blockers"
    ]


def test_release_gate_recheck_nested_gate_must_match_top_level(
    tmp_path: Path,
) -> None:
    gate = _release_gate_recheck()
    gate["gate"] = {"decision": "hold", "blockers": ["forged"]}

    report = _build_report(
        tmp_path=tmp_path,
        release_gate_recheck=gate,
    )

    assert report["ok"] is False
    assert "release_gate_recheck_nested_gate_mismatch" in report[
        "release_boundary_blockers"
    ]


@pytest.mark.parametrize(
    ("unbound_path", "expected_blocker"),
    [
        (
            readiness.DEFAULT_PHASE_SYNTHESIS_REFRESH,
            "phase_synthesis_source_not_verified:path_mismatch",
        ),
        (
            readiness.DEFAULT_RELEASE_GATE_RECHECK,
            "release_gate_recheck_source_not_verified:path_mismatch",
        ),
    ],
)
def test_phase_and_gate_sources_must_be_canonically_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unbound_path: Path,
    expected_blocker: str,
) -> None:
    def binding(
        *args: object,
        expected_relative_path: Path,
        **kwargs: object,
    ) -> dict[str, object]:
        verified = expected_relative_path != unbound_path
        return {
            "verified": verified,
            "reason": "verified" if verified else "path_mismatch",
        }

    monkeypatch.setattr(readiness, "_retained_json_source_binding", binding)

    report = _build_report(tmp_path=tmp_path)

    assert report["ok"] is False
    assert expected_blocker in report["release_boundary_blockers"]


def test_torch_pack_must_be_canonically_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        readiness,
        "_retained_file_source_binding",
        lambda *args, **kwargs: {
            "verified": False,
            "reason": "path_mismatch",
        },
    )

    report = _build_report(tmp_path=tmp_path)

    assert report["ok"] is False
    assert (
        "torch_operator_decision_pack_source_not_verified:path_mismatch"
        in report["release_boundary_blockers"]
    )


def test_torch_pack_requires_scoped_operator_signoff(tmp_path: Path) -> None:
    summary = readiness._decision_pack_summary(
        _write_torch_pack(
            tmp_path / "torch.yaml",
            signed_by="not-an-operator",
        ),
        expected_decision_id="torch-cuda-vs-cpu",
        expected_category="dependency_security",
        checked_at_utc=FIXED_NOW,
    )

    assert summary["signed"] is False
    assert "operator_decision_pack_unsigned" in summary["blockers"]


@pytest.mark.parametrize(
    ("signed_by", "expected_blocker"),
    [
        (
            "operator:jani:1900-01-01T00:00:00Z",
            "operator_decision_pack_signoff_predates_creation",
        ),
        (
            "operator:jani:2999-01-01T00:00:00Z",
            "operator_decision_pack_signoff_in_future",
        ),
    ],
)
def test_torch_pack_signoff_time_is_bounded(
    tmp_path: Path,
    signed_by: str,
    expected_blocker: str,
) -> None:
    summary = readiness._decision_pack_summary(
        _write_torch_pack(
            tmp_path / "torch.yaml",
            signed_by=signed_by,
        ),
        expected_decision_id="torch-cuda-vs-cpu",
        expected_category="dependency_security",
        checked_at_utc=FIXED_NOW,
    )

    assert summary["signed"] is False
    assert expected_blocker in summary["blockers"]


def test_input_release_boundaries_require_exact_false_booleans(
    tmp_path: Path,
) -> None:
    phase = _phase_synthesis_refresh()
    phase["release_boundary"] = {
        field: 0 for field in FALSE_RELEASE_BOUNDARY
    }
    gate = _release_gate_recheck()
    gate["release_boundary"] = {
        field: 0 for field in FALSE_RELEASE_BOUNDARY
    }

    report = _build_report(
        tmp_path=tmp_path,
        phase_synthesis_refresh=phase,
        release_gate_recheck=gate,
    )

    assert report["ok"] is False
    assert "phase_synthesis_release_boundary_mutated" in report[
        "release_boundary_blockers"
    ]
    assert "release_gate_release_boundary_mutated" in report[
        "release_boundary_blockers"
    ]


def test_phase_status_must_be_ready_for_release_boundary_review(
    tmp_path: Path,
) -> None:
    report = _build_report(
        tmp_path=tmp_path,
        phase_synthesis_refresh=_phase_synthesis_refresh(
            status="blocked_until_release_gate_soak_evidence_passes"
        ),
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
    docker_policy_path = tmp_path / "docker-policy.json"
    soak_path = tmp_path / readiness.DEFAULT_SOAK_EVIDENCE
    output_path = tmp_path / "release_boundary_readiness.json"
    phase_path.write_text(json.dumps(_phase_synthesis_refresh()), encoding="utf-8")
    gate_path.write_text(json.dumps(_release_gate_recheck()), encoding="utf-8")
    docker_policy_path.write_text(
        json.dumps(_docker_policy_evidence()),
        encoding="utf-8",
    )
    soak_path.parent.mkdir(parents=True, exist_ok=True)
    soak_path.write_text(json.dumps(_soak_evidence()), encoding="utf-8")

    rc = main(
        [
            "--phase-synthesis-refresh",
            str(phase_path),
            "--release-gate-recheck",
            str(gate_path),
            "--torch-decision-pack",
            str(_write_torch_pack(tmp_path / "torch.yaml")),
            "--docker-decision-pack",
            str(DEFAULT_DOCKER_DECISION_PACK),
            "--docker-policy-evidence",
            str(docker_policy_path),
            "--soak-evidence",
            str(soak_path),
            "--source-root",
            str(tmp_path),
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
