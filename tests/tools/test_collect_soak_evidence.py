# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import tools.collect_soak_evidence as collect_soak
import tools.run_release_docker_policy_evidence as docker_policy
from tools.check_release_gate import evaluate_release_gate
from tools.collect_soak_evidence import (
    build_soak_evidence,
    local_artifact_statuses,
    main,
)
from tools.run_release_docker_policy_evidence import (
    AUTH_SCHEMA_VERSION as DOCKER_AUTH_SCHEMA_VERSION,
    build_report as build_docker_policy_report,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_COMMIT = subprocess.run(
    ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
CLAIMED_SOAK_START_UTC = dt.datetime(2026, 5, 10, tzinfo=dt.UTC)
CLAIMED_SOAK_END_UTC = dt.datetime(2026, 5, 24, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def _stub_operator_authorization_source_binding(monkeypatch) -> None:
    monkeypatch.setattr(
        docker_policy,
        "inspect_operator_authorization_source",
        lambda authorization, **kwargs: {
            "verified": True,
            "reason": "verified",
            "decision_pack_path": (
                authorization.get("decision_pack_path", "")
                if isinstance(authorization, dict)
                else ""
            ),
            "decision_pack_sha256": (
                authorization.get("decision_pack_sha256", "")
                if isinstance(authorization, dict)
                else ""
            ),
        },
    )


def _write_bandit_report(root, *, high: object = 0, medium: object = 0) -> None:
    (root / "v3.12.0_bandit_report_after_static_hardening_zero_medium.json").write_text(
        json.dumps({
            "metrics": {
                "_totals": {
                    "SEVERITY.HIGH": high,
                    "SEVERITY.MEDIUM": medium,
                },
            },
            "results": [],
        }),
        encoding="utf-8",
    )


def _write_pip_audit_report(
    root,
    *,
    vuln_count: int = 0,
    name: str = "v3.12.0_pip_audit_report_after_direct_ci_deps.json",
) -> None:
    vulns = [{"id": f"PYSEC-TEST-{idx}"} for idx in range(vuln_count)]
    (root / name).write_text(
        json.dumps({"dependencies": [{"name": "pkg", "version": "1", "vulns": vulns}]}),
        encoding="utf-8",
    )


def _write_pip_audit_skip_report(root) -> None:
    (root / "v3.12.0_pip_audit_report_after_direct_ci_deps.json").write_text(
        json.dumps({
            "dependencies": [
                {"name": "torch", "skip_reason": "Dependency could not be audited"}
            ]
        }),
        encoding="utf-8",
    )


def _write_privacy_precheck(root, *, ok: bool = True) -> None:
    text = "74 passed\nSMOKE_OK\n" if ok else "73 passed\nSMOKE_FAILED\n"
    (root / "v3.12.0_security_privacy_precheck.md").write_text(
        text,
        encoding="utf-8",
    )


def _write_release_notes(path, *, forbidden: bool = False) -> None:
    text = (
        "## Truth statements\n"
        "* Does **not** claim AGI, consciousness, model superiority, or any "
        "threshold-of-intelligence benchmark.\n"
        "* States Docker `:latest` will remain `v3.8.0` until stable promotion.\n"
    )
    if forbidden:
        text += "\nThis release beats all competitors.\n"
    path.write_text(text, encoding="utf-8")


def _write_axis_a_scale_proof(
    root,
    *,
    warm_p99: object = 0.05,
    cold_p99: object = 20.0,
    misses: int = 0,
) -> None:
    path = root / "v3.12.0_axis_a_solver_scale" / "solver_scale_proof.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "synthetic_solver_descriptors_total": 10000,
            "lookup_pass_count": 1000,
            "lookup_capability_hits_total": 1000,
            "lookup_fifo_fallback_total": 0,
            "lookup_miss_total": misses,
            "lookup_p99_ms": warm_p99,
            "lookup_cold_after_attach": {"lookup_p99_ms": cold_p99},
            "production_hot_path_cache_attached": True,
            "lookup_benchmark_shape": "hot_path_cache_attached_warm_pass",
            "no_provider_credentials_required": True,
            "no_runtime_network_required": True,
            "provider_jobs_delta": 0,
            "builder_jobs_delta": 0,
            "hot_path_cache_stats": {
                "warm_hits": 1000,
                "cold_hits_warmed": 1000,
            },
        }),
        encoding="utf-8",
    )


def _write_axis_b_hex_eval(root, *, quality: float = 0.7476) -> None:
    cells = [
        "bee_ops",
        "environment",
        "home_comfort",
        "hub",
        "logistics",
        "production",
        "safety_security",
    ]
    positive_counts = [6, 8, 6, 15, 7, 4, 6]
    per_file = [
        {
            "cell": cell,
            "file_score": round(((pos_correct / 15) + 1.0) / 2, 4),
            "pos_score": round(pos_correct / 15, 4),
            "neg_score": 1.0,
            "pos_correct": pos_correct,
            "pos_total": 15,
            "neg_correct": 5,
            "neg_total": 5,
        }
        for cell, pos_correct in zip(cells, positive_counts, strict=True)
    ]
    (root / "v3.12.0_axis_b_hex_aligned_eval.json").write_text(
        json.dumps({
            "schema_version": "waggledance.axis_b_hex_eval.v1",
            "target_version": "v3.12.0",
            "result": "pass" if quality >= 0.74 else "blocked",
            "corpus": {
                "cells": cells,
                "files": 7,
                "total_positive": 105,
                "total_negative": 35,
            },
            "thresholds": {
                "quality_floor": 0.74,
                "mismatched_baseline_quality": 0.5,
                "minimum_baseline_delta": 0.2,
                "per_cell_quality_floor": 0.6,
            },
            "quality": quality,
            "micro_pos": sum(positive_counts),
            "micro_pos_total": 105,
            "micro_neg": 35,
            "micro_neg_total": 35,
            "per_file": per_file,
            "blockers": [] if quality >= 0.74 else ["quality_below_floor"],
        }),
        encoding="utf-8",
    )


def _write_soak_log_audit(
    root,
    *,
    source_files: list[str] | None = None,
    started_at_utc: str = "2026-05-10T00:00:00Z",
    ended_at_utc: str = "2026-05-24T00:00:00Z",
    silent_failure_count: object = 0,
    error_count: object = 0,
    undated_record_count: object = 0,
    blockers: list[str] | None = None,
) -> None:
    source_files = (
        ["docs/runs/release_soak_evidence/test_soak.log"]
        if source_files is None
        else source_files
    )
    blockers = [] if blockers is None else blockers
    source_hashes = {}
    for source_file in source_files:
        source_path = Path(source_file)
        if source_path.exists() and source_path.is_file():
            normalized = source_path.read_text(encoding="utf-8").replace("\r\n", "\n")
            source_hashes[source_file] = (
                "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            )
    (root / "v3.12.0_soak_log_audit.json").write_text(
        json.dumps({
            "schema_version": "waggledance.release_soak_log_audit.v1",
            "target_version": "v3.12.0",
            "audit_result": "pass" if not blockers else "blocked",
            "source_files": source_files,
            "source_hashes": source_hashes,
            "started_at_utc": started_at_utc,
            "ended_at_utc": ended_at_utc,
            "silent_failure_count": silent_failure_count,
            "error_count": error_count,
            "undated_record_count": undated_record_count,
            "error_log_clean": not blockers and error_count == 0,
            "blockers": blockers,
        }),
        encoding="utf-8",
    )


def _ci_run(
    workflow: str,
    jobs: list[str],
    *,
    commit: str,
    status: str = "completed",
    conclusion: str = "success",
) -> dict:
    return {
        "workflow_name": workflow,
        "run_id": 1000 + len(jobs),
        "head_sha": commit,
        "event": "push",
        "status": status,
        "conclusion": conclusion,
        "created_at_utc": "2026-05-22T13:21:32Z",
        "updated_at_utc": "2026-05-22T13:30:00Z",
        "url": f"https://github.example/runs/{workflow}",
        "jobs": [
            {
                "name": job,
                "status": status,
                "conclusion": conclusion,
                "started_at_utc": "2026-05-22T13:21:34Z",
                "completed_at_utc": "2026-05-22T13:30:00Z",
                "url": f"https://github.example/jobs/{job}",
            }
            for job in jobs
        ],
    }


def _write_ci_status(root, *, commit: str, report_commit: str | None = None) -> None:
    report_commit = commit if report_commit is None else report_commit
    (root / "v3.12.0_ci_status.json").write_text(
        json.dumps({
            "schema_version": "waggledance.release_ci_status.v1",
            "target_version": "v3.12.0",
            "commit": report_commit,
            "source": {
                "type": "github_actions",
                "repo": "Ahkeratmehilaiset/waggledance-swarm",
                "collector": "gh run list + gh run view",
            },
            "generated_at_utc": "2026-05-22T13:30:00Z",
            "required_jobs": [
                {"workflow": "WaggleDance CI", "job": "test (3.11)"},
                {"workflow": "WaggleDance CI", "job": "test (3.12)"},
                {"workflow": "WaggleDance CI", "job": "test (3.13)"},
                {"workflow": "WaggleDance CI", "job": "security-scan"},
                {"workflow": "Tests", "job": "unified"},
            ],
            "runs": [
                _ci_run(
                    "WaggleDance CI",
                    ["test (3.11)", "test (3.12)", "test (3.13)", "security-scan"],
                    commit=report_commit,
                ),
                _ci_run("Tests", ["unified"], commit=report_commit),
            ],
            "blockers": [],
            "ci_status": "pass",
        }),
        encoding="utf-8",
    )


def _write_docker_policy(
    root,
    *,
    commit: str,
    operator_authorized: bool = True,
    report_commit: str | None = None,
) -> None:
    report_commit = commit if report_commit is None else report_commit
    authorization = None
    if operator_authorized:
        authorization = {
            "schema_version": DOCKER_AUTH_SCHEMA_VERSION,
            "target_version": "v3.12.0",
            "commit": report_commit,
            "commit_scope": "exact",
            "decision_pack_target_version": "v3.12.0",
            "decision_pack_commit": report_commit,
            "stable_promotion_authorized": True,
            "docker_promotion_deferred": False,
            "move_latest": "no",
            "authorization_id": (
                "decision-pack:docker-v3-12-0-stable-promotion:"
                "ghcr_stable_only:janik"
            ),
            "authorized_at_utc": "2026-05-24T00:00:00Z",
            "decision_pack_created_at_utc": "2026-05-22T14:00:00Z",
            "decision_pack_path": (
                "docs/operator_inbox/"
                "docker-v3-12-0-stable-promotion.yaml"
            ),
            "decision_pack_sha256": "sha256:" + ("1" * 64),
            "source": "operator_decision_pack",
            "decision_id": "docker-v3-12-0-stable-promotion",
            "chosen_option": "ghcr_stable_only",
            "operator_id": "janik",
        }
    report = build_docker_policy_report(
        source_root=REPO_ROOT,
        commit=report_commit,
        operator_authorization=authorization,
        generated_at_utc=dt.datetime(2026, 5, 25, 0, 0, tzinfo=dt.UTC),
    )
    (root / "v3.12.0_docker_policy.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )


def test_collector_draft_is_fail_closed_until_evidence_is_explicit() -> None:
    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=dt.datetime(2026, 5, 16, 12, 0, tzinfo=dt.UTC),
    )

    assert evidence["schema_version"] == "waggledance.release_soak.v1"
    assert evidence["target_version"] == "v3.12.0"
    assert evidence["commit"] == "dc76e81cd8c804608bfaedf951220e46ff1baffa"
    assert evidence["started_at_utc"] == "2026-05-10T00:00:00Z"
    assert evidence["ended_at_utc"] == "2026-05-16T12:00:00Z"
    assert evidence["duration_hours"] == 156
    assert evidence["result"] == "hold"
    assert evidence["ci_status"] == "unknown"
    assert evidence["profile_s_smoke"] == "unknown"
    assert evidence["silent_failures"] is None
    assert evidence["error_log_clean"] is False
    assert evidence["docker_stable_policy"] == "draft"


def test_collector_output_still_uses_release_gate_as_source_of_truth(tmp_path) -> None:
    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=dt.datetime(2026, 5, 16, 12, 0, tzinfo=dt.UTC),
    )
    evidence_path = tmp_path / "v3.12.0.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        "docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 16),
    )

    assert result["decision"] == "hold"
    assert "soak_window_incomplete" in result["blockers"]
    assert "soak_evidence_result_not_pass" in result["blockers"]
    assert "soak_evidence_ci_status_not_pass" in result["blockers"]
    assert "soak_evidence_docker_policy_not_finalized" in result["blockers"]


def test_collector_manual_explicit_evidence_remains_hold(tmp_path) -> None:
    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=dt.datetime(2026, 5, 24, 0, 0, tzinfo=dt.UTC),
        status_overrides={
            "ci_status": "pass",
            "profile_s_smoke": "pass",
            "security_privacy_gate": "pass",
            "axis_a_regression": "pass",
            "axis_b_gate": "pass",
            "release_notes_anti_claims": "pass",
        },
        silent_failures=0,
        error_log_clean=True,
        docker_stable_policy="finalized",
    )
    evidence_path = tmp_path / "v3.12.0.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        "docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        checked_at_utc=dt.datetime(2026, 5, 24, tzinfo=dt.UTC),
    )

    assert evidence["collection_mode"] == "manual"
    assert evidence["result"] == "hold"
    assert result["decision"] == "hold"
    assert "soak_evidence_collection_mode_invalid" in result["blockers"]
    assert "soak_evidence_result_not_pass" in result["blockers"]


def test_collector_local_artifact_mode_can_emit_pass(monkeypatch) -> None:
    def local_fields(**kwargs):
        assert kwargs["soak_started_at_utc"] == dt.datetime(
            2026,
            5,
            10,
            tzinfo=dt.UTC,
        )
        assert kwargs["soak_ended_at_utc"] == dt.datetime(
            2026,
            5,
            24,
            tzinfo=dt.UTC,
        )
        return {
            **collect_soak.STATUS_PASS_FIELDS,
            "silent_failures": 0,
            "error_log_clean": True,
            "docker_stable_policy": "finalized",
        }

    monkeypatch.setattr(
        collect_soak,
        "local_artifact_evidence_fields",
        local_fields,
    )

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=dt.datetime(2026, 5, 24, tzinfo=dt.UTC),
        use_local_artifacts=True,
    )

    assert evidence["collection_mode"] == "local_artifacts"
    assert evidence["result"] == "pass"


@pytest.mark.parametrize("silent_failures", [False, 0.0, -0.0])
def test_collector_result_requires_integer_zero_silent_failures(
    silent_failures,
) -> None:
    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=dt.datetime(2026, 5, 24, 0, 0, tzinfo=dt.UTC),
        status_overrides={
            "ci_status": "pass",
            "profile_s_smoke": "pass",
            "security_privacy_gate": "pass",
            "axis_a_regression": "pass",
            "axis_b_gate": "pass",
            "release_notes_anti_claims": "pass",
        },
        silent_failures=silent_failures,
        error_log_clean=True,
        docker_stable_policy="finalized",
    )

    assert evidence["result"] == "hold"


def test_local_artifacts_block_security_when_dependency_audit_has_vulns(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_bandit_report(evidence_root)
    _write_pip_audit_report(evidence_root, vuln_count=2)
    _write_privacy_precheck(evidence_root)
    _write_release_notes(release_notes)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["profile_s_smoke"] == "pass"
    assert statuses["security_privacy_gate"] == "blocked"
    assert statuses["release_notes_anti_claims"] == "pass"


@pytest.mark.parametrize(
    ("high", "medium"),
    [
        (0.9, 0),
        (0, False),
        ("0", 0),
    ],
)
def test_local_artifacts_reject_noninteger_bandit_counts(
    tmp_path,
    high,
    medium,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_bandit_report(evidence_root, high=high, medium=medium)
    _write_pip_audit_report(evidence_root, vuln_count=0)
    _write_privacy_precheck(evidence_root)
    _write_release_notes(release_notes)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["security_privacy_gate"] == "blocked"


def test_local_artifacts_reject_malformed_bandit_metrics_without_crashing(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_bandit_report(evidence_root)
    bandit_path = (
        evidence_root
        / "v3.12.0_bandit_report_after_static_hardening_zero_medium.json"
    )
    report = json.loads(bandit_path.read_text(encoding="utf-8"))
    report["metrics"] = []
    bandit_path.write_text(json.dumps(report), encoding="utf-8")
    _write_pip_audit_report(evidence_root, vuln_count=0)
    _write_privacy_precheck(evidence_root)
    _write_release_notes(release_notes)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["security_privacy_gate"] == "blocked"


def test_local_artifacts_block_security_when_dependency_audit_missing(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_bandit_report(evidence_root)
    _write_privacy_precheck(evidence_root)
    _write_release_notes(release_notes)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["profile_s_smoke"] == "pass"
    assert statuses["security_privacy_gate"] == "blocked"
    assert statuses["release_notes_anti_claims"] == "pass"


def test_local_artifacts_can_pass_security_only_when_all_checks_are_clean(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_bandit_report(evidence_root)
    _write_pip_audit_report(evidence_root, vuln_count=0)
    _write_privacy_precheck(evidence_root)
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=dt.datetime(2026, 5, 16, 12, 0, tzinfo=dt.UTC),
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["profile_s_smoke"] == "pass"
    assert evidence["security_privacy_gate"] == "pass"
    assert evidence["release_notes_anti_claims"] == "pass"
    assert evidence["result"] == "hold"


def test_local_artifacts_can_pass_axis_gates_from_metric_artifacts(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_axis_a_scale_proof(evidence_root)
    _write_axis_b_hex_eval(evidence_root)
    _write_release_notes(release_notes)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["axis_a_regression"] == "pass"
    assert statuses["axis_b_gate"] == "pass"


def test_local_artifacts_can_pass_ci_from_github_actions_artifact(
    tmp_path,
) -> None:
    commit = "dc76e81cd8c804608bfaedf951220e46ff1baffa"
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_ci_status(evidence_root, commit=commit)
    _write_release_notes(release_notes)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
        commit=commit,
    )

    assert statuses["ci_status"] == "pass"


def test_local_artifacts_reject_ci_artifact_commit_mismatch(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_ci_status(
        evidence_root,
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        report_commit="1748c3104a61e2e14f65c38fa7c95c42237e04f9",
    )
    _write_release_notes(release_notes)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
    )

    assert statuses["ci_status"] == "blocked"


def test_local_artifacts_can_finalize_docker_policy_from_artifact(
    tmp_path,
    monkeypatch,
) -> None:
    commit = REPO_COMMIT
    source_snapshot = {
        str(path): (REPO_ROOT / path).read_bytes()
        for path in docker_policy.REQUIRED_SOURCE_FILES
    }
    monkeypatch.setattr(
        docker_policy,
        "inspect_source_commit_binding",
        lambda source_root, expected_commit: {
            "commit": expected_commit,
            "verified": True,
            "reason": "verified",
            "source_blob_oids": {},
        },
    )
    monkeypatch.setattr(
        docker_policy,
        "_load_committed_source_snapshot",
        lambda source_root, expected_commit: source_snapshot,
    )
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_docker_policy(evidence_root, commit=commit)
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit=commit,
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["docker_stable_policy"] == "finalized"


def test_local_artifacts_reject_unscoped_signed_operator_pack() -> None:
    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit=REPO_COMMIT,
        use_local_artifacts=True,
    )

    assert evidence["docker_stable_policy"] == "draft"


def test_local_artifacts_override_manual_docker_policy_stub_when_artifact_missing(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        docker_stable_policy="finalized",
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["docker_stable_policy"] == "draft"


def test_local_artifacts_reject_huge_docker_policy_integer(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_release_notes(release_notes)
    (evidence_root / "v3.12.0_docker_policy.json").write_text(
        '{"oversized_integer":' + ("9" * 5000) + "}",
        encoding="utf-8",
    )

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit=REPO_COMMIT,
        docker_stable_policy="finalized",
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["docker_stable_policy"] == "draft"


def test_local_artifact_reader_rejects_duplicate_json_key(tmp_path) -> None:
    artifact = tmp_path / "duplicate.json"
    artifact.write_text(
        '{"duration_hours":1,"duration_hours":336}',
        encoding="utf-8",
    )

    assert collect_soak._read_json(artifact) is None


def test_local_artifacts_keep_docker_policy_draft_without_operator_authorization(
    tmp_path,
) -> None:
    commit = "dc76e81cd8c804608bfaedf951220e46ff1baffa"
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_docker_policy(evidence_root, commit=commit, operator_authorized=False)
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit=commit,
        docker_stable_policy="finalized",
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["docker_stable_policy"] == "draft"


def test_local_artifacts_block_axis_a_when_hot_path_metrics_regress(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_axis_a_scale_proof(evidence_root, warm_p99=5.0)
    _write_axis_b_hex_eval(evidence_root)
    _write_release_notes(release_notes)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["axis_a_regression"] == "blocked"
    assert statuses["axis_b_gate"] == "pass"


@pytest.mark.parametrize(
    ("warm_p99", "cold_p99"),
    [
        (float("nan"), 20.0),
        (0.05, float("nan")),
        (-1.0, 20.0),
        (0.05, -1.0),
        (False, 20.0),
    ],
)
def test_local_artifacts_reject_invalid_axis_a_latency_domain(
    tmp_path,
    warm_p99,
    cold_p99,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_axis_a_scale_proof(
        evidence_root,
        warm_p99=warm_p99,
        cold_p99=cold_p99,
    )
    _write_axis_b_hex_eval(evidence_root)
    _write_release_notes(release_notes)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["axis_a_regression"] != "pass"


def test_local_artifacts_block_axis_b_when_quality_below_floor(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_axis_a_scale_proof(evidence_root)
    _write_axis_b_hex_eval(evidence_root, quality=0.7)
    _write_release_notes(release_notes)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["axis_a_regression"] == "pass"
    assert statuses["axis_b_gate"] == "blocked"


@pytest.mark.parametrize("quality", [float("nan"), 2.0, -0.1])
def test_local_artifacts_reject_invalid_axis_b_quality_domain(
    tmp_path,
    quality,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_axis_a_scale_proof(evidence_root)
    _write_axis_b_hex_eval(evidence_root)
    _write_release_notes(release_notes)
    report_path = evidence_root / "v3.12.0_axis_b_hex_aligned_eval.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["quality"] = quality
    for row in report["per_file"]:
        row["file_score"] = quality
    report_path.write_text(json.dumps(report), encoding="utf-8")

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["axis_b_gate"] != "pass"


def test_local_artifacts_reject_axis_b_correct_counts_above_totals(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_axis_a_scale_proof(evidence_root)
    _write_axis_b_hex_eval(evidence_root)
    _write_release_notes(release_notes)
    report_path = evidence_root / "v3.12.0_axis_b_hex_aligned_eval.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["micro_pos"] = 700
    for row in report["per_file"]:
        row["pos_correct"] = 100
    report_path.write_text(json.dumps(report), encoding="utf-8")

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["axis_b_gate"] != "pass"


def test_local_artifacts_reject_axis_b_scores_not_derived_from_counts(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_axis_a_scale_proof(evidence_root)
    _write_axis_b_hex_eval(evidence_root)
    _write_release_notes(release_notes)
    report_path = evidence_root / "v3.12.0_axis_b_hex_aligned_eval.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["micro_pos"] = 0
    for row in report["per_file"]:
        row["pos_correct"] = 0
    report_path.write_text(json.dumps(report), encoding="utf-8")

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["axis_b_gate"] != "pass"


def test_local_artifacts_reject_axis_b_redistributed_cell_denominators(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_axis_a_scale_proof(evidence_root)
    _write_axis_b_hex_eval(evidence_root)
    _write_release_notes(release_notes)
    report_path = evidence_root / "v3.12.0_axis_b_hex_aligned_eval.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    positive_totals = [1, 1, 1, 1, 1, 1, 99]
    positive_correct = [1, 1, 1, 1, 1, 1, 20]
    for row, correct, total in zip(
        report["per_file"],
        positive_correct,
        positive_totals,
        strict=True,
    ):
        row["pos_correct"] = correct
        row["pos_total"] = total
        row["pos_score"] = round(correct / total, 4)
        row["file_score"] = round(((correct / total) + 1.0) / 2, 4)
    report["micro_pos"] = sum(positive_correct)
    report["quality"] = round(
        sum(row["file_score"] for row in report["per_file"])
        / len(report["per_file"]),
        4,
    )
    report_path.write_text(json.dumps(report), encoding="utf-8")

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert report["quality"] >= 0.94
    assert statuses["axis_b_gate"] != "pass"


def test_local_artifacts_reject_axis_b_wrong_target_version(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_axis_a_scale_proof(evidence_root)
    _write_axis_b_hex_eval(evidence_root)
    _write_release_notes(release_notes)
    report_path = evidence_root / "v3.12.0_axis_b_hex_aligned_eval.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["target_version"] = "v999"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["axis_b_gate"] != "pass"


def test_local_artifacts_block_axis_b_when_pass_artifact_has_blockers(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_axis_a_scale_proof(evidence_root)
    _write_axis_b_hex_eval(evidence_root)
    _write_release_notes(release_notes)
    report_path = evidence_root / "v3.12.0_axis_b_hex_aligned_eval.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["blockers"] = ["manual_pass_conflicts_with_blocker"]
    report["result"] = "pass"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["axis_a_regression"] == "pass"
    assert statuses["axis_b_gate"] == "blocked"


def test_local_artifacts_block_axis_b_when_pass_artifact_is_incomplete(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_axis_a_scale_proof(evidence_root)
    _write_axis_b_hex_eval(evidence_root)
    _write_release_notes(release_notes)
    report_path = evidence_root / "v3.12.0_axis_b_hex_aligned_eval.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["per_file"] = report["per_file"][:1]
    report["result"] = "pass"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["axis_a_regression"] == "pass"
    assert statuses["axis_b_gate"] == "blocked"


def test_local_artifacts_block_axis_b_when_pass_artifact_lowers_thresholds(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_axis_a_scale_proof(evidence_root)
    _write_axis_b_hex_eval(evidence_root)
    _write_release_notes(release_notes)
    report_path = evidence_root / "v3.12.0_axis_b_hex_aligned_eval.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["thresholds"]["quality_floor"] = 0.1
    report["thresholds"]["per_cell_quality_floor"] = 0.1
    report["result"] = "pass"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["axis_a_regression"] == "pass"
    assert statuses["axis_b_gate"] == "blocked"


def test_local_artifacts_can_derive_clean_soak_log_fields(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    source = tmp_path / "test_soak.log"
    source.write_text("0 errors\nno silent failures\n", encoding="utf-8")
    _write_soak_log_audit(evidence_root, source_files=[str(source)])
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=CLAIMED_SOAK_END_UTC,
        silent_failures=9,
        error_log_clean=False,
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["silent_failures"] == 0
    assert evidence["error_log_clean"] is True


@pytest.mark.parametrize(
    ("audit_started_at_utc", "audit_ended_at_utc"),
    [
        (
            "2026-05-10T00:00:01Z",
            "2026-05-24T00:00:00Z",
        ),
        (
            "2026-05-10T00:00:00Z",
            "2026-05-23T23:59:59Z",
        ),
    ],
)
def test_local_artifacts_reject_soak_log_audit_not_covering_claimed_interval(
    tmp_path,
    audit_started_at_utc,
    audit_ended_at_utc,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    source = tmp_path / "test_soak.log"
    source.write_text("0 errors\nno silent failures\n", encoding="utf-8")
    _write_soak_log_audit(
        evidence_root,
        source_files=[str(source)],
        started_at_utc=audit_started_at_utc,
        ended_at_utc=audit_ended_at_utc,
    )
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        started_at_utc=CLAIMED_SOAK_START_UTC,
        ended_at_utc=CLAIMED_SOAK_END_UTC,
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["collection_mode"] == "local_artifacts"
    assert evidence["silent_failures"] is None
    assert evidence["error_log_clean"] is False
    assert evidence["result"] == "hold"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("silent_failure_count", False),
        ("silent_failure_count", 0.0),
        ("silent_failure_count", "0"),
        ("error_count", False),
        ("error_count", 0.9),
        ("undated_record_count", -0.0),
    ],
)
def test_local_artifacts_reject_noninteger_soak_log_counts(
    tmp_path,
    field,
    value,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    source = tmp_path / "test_soak.log"
    source.write_text("0 errors\nno silent failures\n", encoding="utf-8")
    kwargs = {field: value}
    _write_soak_log_audit(
        evidence_root,
        source_files=[str(source)],
        **kwargs,
    )
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=CLAIMED_SOAK_END_UTC,
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["silent_failures"] is None
    assert evidence["error_log_clean"] is False


def test_local_artifacts_block_manual_soak_log_stub_when_artifact_missing(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=CLAIMED_SOAK_END_UTC,
        silent_failures=0,
        error_log_clean=True,
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["silent_failures"] is None
    assert evidence["error_log_clean"] is False


def test_local_artifacts_keep_soak_log_blocked_when_errors_detected(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    source = tmp_path / "test_soak.log"
    source.write_text("ERROR: backend unhealthy\n", encoding="utf-8")
    _write_soak_log_audit(
        evidence_root,
        source_files=[str(source)],
        error_count=2,
        blockers=["errors_detected"],
    )
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=CLAIMED_SOAK_END_UTC,
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["silent_failures"] == 0
    assert evidence["error_log_clean"] is False


def test_local_artifacts_reject_soak_log_audit_without_sources(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_soak_log_audit(evidence_root, source_files=[])
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=CLAIMED_SOAK_END_UTC,
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["silent_failures"] is None
    assert evidence["error_log_clean"] is False


def test_local_artifacts_reject_soak_log_audit_with_fake_source(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_soak_log_audit(evidence_root, source_files=[str(tmp_path / "fake.log")])
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=CLAIMED_SOAK_END_UTC,
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["silent_failures"] is None
    assert evidence["error_log_clean"] is False


def test_local_artifacts_reject_soak_log_audit_when_source_hash_changes(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    source = tmp_path / "test_soak.log"
    source.write_text("2026-05-22T12:00:00Z INFO clean\n", encoding="utf-8")
    _write_soak_log_audit(evidence_root, source_files=[str(source)])
    source.write_text("2026-05-22T12:00:00Z ERROR changed\n", encoding="utf-8")
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=CLAIMED_SOAK_END_UTC,
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["silent_failures"] is None
    assert evidence["error_log_clean"] is False


def test_local_artifacts_reject_soak_log_audit_without_source_hashes(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    source = tmp_path / "test_soak.log"
    source.write_text("2026-05-22T12:00:00Z INFO clean\n", encoding="utf-8")
    _write_soak_log_audit(evidence_root, source_files=[str(source)])
    report_path = evidence_root / "v3.12.0_soak_log_audit.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    del report["source_hashes"]
    report_path.write_text(json.dumps(report), encoding="utf-8")
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=CLAIMED_SOAK_END_UTC,
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["silent_failures"] is None
    assert evidence["error_log_clean"] is False


def test_local_artifacts_reject_soak_log_audit_with_source_blocker(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_soak_log_audit(
        evidence_root,
        source_files=["missing.log"],
        blockers=["source_missing:missing.log"],
    )
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        ended_at_utc=CLAIMED_SOAK_END_UTC,
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["silent_failures"] is None
    assert evidence["error_log_clean"] is False


def test_local_artifacts_override_manual_axis_gate_stubs(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        status_overrides={
            "axis_a_regression": "pass",
            "axis_b_gate": "pass",
        },
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["axis_a_regression"] == "unknown"
    assert evidence["axis_b_gate"] == "unknown"


def test_local_artifacts_override_manual_ci_stub_when_artifact_missing(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        status_overrides={"ci_status": "pass"},
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["ci_status"] == "unknown"


def test_local_artifacts_block_security_when_pip_audit_skips_dependency(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_bandit_report(evidence_root)
    _write_pip_audit_skip_report(evidence_root)
    _write_privacy_precheck(evidence_root)
    _write_release_notes(release_notes)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["security_privacy_gate"] == "blocked"


def test_local_artifacts_prefer_newer_lock_audit_over_older_clean_reports(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_bandit_report(evidence_root)
    _write_pip_audit_report(evidence_root, vuln_count=0)
    _write_pip_audit_report(
        evidence_root,
        vuln_count=1,
        name="v3.12.0_pip_audit_report_lock_after_prune_osv.json",
    )
    _write_privacy_precheck(evidence_root)
    _write_release_notes(release_notes)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["security_privacy_gate"] == "blocked"


def test_local_artifacts_override_manual_security_stub(tmp_path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_bandit_report(evidence_root)
    _write_pip_audit_report(evidence_root, vuln_count=1)
    _write_privacy_precheck(evidence_root)
    _write_release_notes(release_notes)

    evidence = build_soak_evidence(
        "docs/release/RELEASE_READINESS.md",
        commit="dc76e81cd8c804608bfaedf951220e46ff1baffa",
        status_overrides={"security_privacy_gate": "pass"},
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["security_privacy_gate"] == "blocked"


def test_release_notes_anti_claims_block_forbidden_positive_claim(
    tmp_path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    release_notes = tmp_path / "v3.12.0.md"
    _write_bandit_report(evidence_root)
    _write_pip_audit_report(evidence_root, vuln_count=0)
    _write_privacy_precheck(evidence_root)
    _write_release_notes(release_notes, forbidden=True)

    statuses = local_artifact_statuses(
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert statuses["release_notes_anti_claims"] == "blocked"


def test_collector_cli_writes_output_and_history(tmp_path) -> None:
    output = tmp_path / "evidence" / "v3.12.0.json"
    history = tmp_path / "history.jsonl"

    rc = main([
        "--release-readiness",
        "docs/release/RELEASE_READINESS.md",
        "--output",
        str(output),
        "--history",
        str(history),
        "--commit",
        "dc76e81cd8c804608bfaedf951220e46ff1baffa",
        "--ended-at-utc",
        "2026-05-16T12:00:00Z",
    ])

    assert rc == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    history_lines = history.read_text(encoding="utf-8").splitlines()
    assert evidence["result"] == "hold"
    assert len(history_lines) == 1
    assert json.loads(history_lines[0])["target_version"] == "v3.12.0"
