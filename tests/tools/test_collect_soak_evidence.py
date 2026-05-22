# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json

from tools.check_release_gate import evaluate_release_gate
from tools.collect_soak_evidence import (
    build_soak_evidence,
    local_artifact_statuses,
    main,
)


def _write_bandit_report(root, *, high: int = 0, medium: int = 0) -> None:
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
    warm_p99: float = 0.05,
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
            "lookup_cold_after_attach": {"lookup_p99_ms": 20.0},
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
    per_file = [
        {
            "cell": cell,
            "file_score": 0.7,
            "pos_correct": 8,
            "pos_total": 15,
            "neg_correct": 5,
            "neg_total": 5,
        }
        for cell in cells
    ]
    (root / "v3.12.0_axis_b_hex_aligned_eval.json").write_text(
        json.dumps({
            "schema_version": "waggledance.axis_b_hex_eval.v1",
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
            "micro_pos": 56,
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
    silent_failure_count: int = 0,
    error_count: int = 0,
    blockers: list[str] | None = None,
) -> None:
    source_files = (
        ["docs/runs/release_soak_evidence/test_soak.log"]
        if source_files is None
        else source_files
    )
    blockers = [] if blockers is None else blockers
    (root / "v3.12.0_soak_log_audit.json").write_text(
        json.dumps({
            "schema_version": "waggledance.release_soak_log_audit.v1",
            "target_version": "v3.12.0",
            "audit_result": "pass" if not blockers else "blocked",
            "source_files": source_files,
            "started_at_utc": "2026-05-10T00:00:00Z",
            "ended_at_utc": "2026-05-22T12:49:00Z",
            "silent_failure_count": silent_failure_count,
            "error_count": error_count,
            "error_log_clean": not blockers and error_count == 0,
            "blockers": blockers,
        }),
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


def test_collector_can_emit_valid_pass_when_all_evidence_is_explicit(tmp_path) -> None:
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
        today=dt.date(2026, 5, 24),
    )

    assert evidence["result"] == "pass"
    assert result["decision"] == "pass"
    assert result["blockers"] == []


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
        silent_failures=9,
        error_log_clean=False,
        use_local_artifacts=True,
        evidence_root=evidence_root,
        release_notes=release_notes,
    )

    assert evidence["silent_failures"] == 0
    assert evidence["error_log_clean"] is True


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
