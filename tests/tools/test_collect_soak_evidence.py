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
