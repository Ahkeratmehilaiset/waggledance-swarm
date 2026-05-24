from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.run_v12_rival_local_check_matrix import (
    PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL,
    build_rival_local_check_matrix,
    render_markdown,
    write_evidence_manifest_templates,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_v12_rival_local_check_matrix.py"


def _run_matrix(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_default_matrix_reports_required_rivals_as_not_configured() -> None:
    report = build_rival_local_check_matrix()

    assert report["report_version"] == "wd.v12.rival_local_check_matrix.v0"
    assert (
        report["evidence_manifest_contract_version"]
        == "wd.v12.rival_local_evidence_manifest.v1"
    )
    assert report["ok"] is True
    assert report["required_count"] == 4
    assert report["passed_count"] == 0
    assert report["blocked_count"] == 4
    assert report["consensus_grade"] is False
    assert (
        report["evidence_artifact_contract_version"]
        == "wd.v12.rival_local_evidence_artifact.v1"
    )
    assert "offline" in report["required_evidence_artifact_fields"]
    assert {row["rival"] for row in report["checks"]} == {
        "JamJet",
        "Asqav",
        "Microsoft AGT",
        "Preloop",
    }
    assert {row["local_status"] for row in report["checks"]} == {"not_configured"}


def test_markdown_output_preserves_no_benchmark_guardrail() -> None:
    report = build_rival_local_check_matrix()
    markdown = render_markdown(report)

    assert "V12 Rival Local Check Matrix" in markdown
    assert "evidence_manifest_contract_version" in markdown
    assert "rival local checks passed: `0/4`" in markdown
    assert "This is not a competitor benchmark" in markdown
    assert "consensus_grade: `false`" in markdown
    assert "Required Evidence Artifact Fields" in markdown
    assert "Required Rival Observations" in markdown


def test_cli_json_reports_non_consensus_grade() -> None:
    result = _run_matrix("--json", "--now", "2026-05-20T19:30:00Z")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["generated_at_utc"] == "2026-05-20T19:30:00Z"
    assert payload["required_count"] == 4
    assert payload["passed_count"] == 0
    assert payload["consensus_grade"] is False


def test_cli_writes_markdown_report(tmp_path: Path) -> None:
    out = tmp_path / "rival_matrix.md"

    result = _run_matrix("--markdown-out", str(out))

    assert result.returncode == 0, result.stderr
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "V12 Rival Local Check Matrix" in text
    assert "not a competitor benchmark" in text


def test_init_evidence_templates_are_non_passing(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"

    init = write_evidence_manifest_templates(evidence_dir=evidence_dir)
    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    assert init["created_count"] == 4
    assert init["overwrote_existing"] is False
    assert init["overwritten_count"] == 0
    assert init["safe_defaults"]["smoke_result"] == "not_run"
    assert init["safe_defaults"]["consensus_grade_contribution"] is False
    assert report["passed_count"] == 0
    assert report["blocked_count"] == 4
    assert report["consensus_grade"] is False
    # Per 2026-05-23 upstream verification (C2): JamJet + Preloop are
    # both open_source_installable (Apache-2.0, github.com/jamjet-labs/jamjet
    # python-sdk-v0.8.6 + github.com/preloop/preloop v0.9.3), so their
    # template manifests with smoke_result=not_run surface as not_passed
    # (same as AGT/Asqav templates). The {"not_configured"} entry only
    # appeared while the registry hard-blocked JamJet/Preloop.
    assert {row["local_status"] for row in report["checks"]} == {"not_passed"}
    for manifest_path in init["manifest_paths"]:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        assert manifest["evidence_manifest_contract_version"] == (
            "wd.v12.rival_local_evidence_manifest.v1"
        )
        assert manifest["smoke_result"] == "not_run"
        assert manifest["cloud_dependency"] is False
        assert manifest["local_artifact_sha256"] == "sha256:" + ("0" * 64)
        assert manifest["expected_artifact"][
            "evidence_artifact_contract_version"
        ] == "wd.v12.rival_local_evidence_artifact.v1"
        assert "observations" in manifest["expected_artifact"]


def test_cli_init_evidence_templates_reports_non_consensus(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"

    result = _run_matrix("--json", "--init-evidence-dir", str(evidence_dir))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["template_init"]["created_count"] == 4
    assert payload["template_init"]["overwrote_existing"] is False
    assert payload["passed_count"] == 0
    assert payload["blocked_count"] == 4
    assert payload["consensus_grade"] is False
    # Post-2026-05-23 upstream verification: JamJet + Preloop are
    # open_source_installable; all four template manifests surface as
    # not_passed (smoke_result=not_run), none hard-blocked.
    assert {row["local_status"] for row in payload["checks"]} == {"not_passed"}


def test_init_evidence_templates_refuse_overwrite_without_flag(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    write_evidence_manifest_templates(evidence_dir=evidence_dir)

    result = _run_matrix("--json", "--init-evidence-dir", str(evidence_dir))

    assert result.returncode == 1
    assert "template manifest already exists" in result.stderr
    assert "--overwrite-templates" in result.stderr


def test_init_evidence_templates_overwrite_still_non_passing(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    write_evidence_manifest_templates(evidence_dir=evidence_dir)
    jamjet_manifest = evidence_dir / "microsoft-agt.json"
    jamjet_manifest.write_text('{"smoke_result": "passed"}\n', encoding="utf-8")

    result = _run_matrix(
        "--json",
        "--init-evidence-dir",
        str(evidence_dir),
        "--overwrite-templates",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["template_init"]["overwrote_existing"] is True
    assert payload["template_init"]["overwritten_count"] == 4
    assert payload["passed_count"] == 0
    assert payload["consensus_grade"] is False
    # Post-2026-05-23 upstream verification: JamJet + Preloop are
    # open_source_installable; all four template manifests surface as
    # not_passed (smoke_result=not_run), none hard-blocked.
    assert {row["local_status"] for row in payload["checks"]} == {"not_passed"}
    manifest = json.loads(jamjet_manifest.read_text(encoding="utf-8"))
    assert manifest["smoke_result"] == "not_run"
    assert manifest["local_artifact_sha256"] == "sha256:" + ("0" * 64)


def test_valid_local_evidence_manifest_marks_one_rival_passed(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "microsoft-agt-smoke.json"
    artifact.parent.mkdir()
    _write_valid_artifact(
        artifact,
        rival="Microsoft AGT",
        pinned_revision="microsoft-agt-test-rev",
        evidence_type="local_smoke",
    )
    (evidence_dir / "microsoft-agt.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Microsoft AGT",
                "pinned_revision": "microsoft-agt-test-rev",
                "local_artifact_path": "artifacts/microsoft-agt-smoke.json",
                "local_artifact_sha256": _sha256(artifact),
                "smoke_command": "jamjet smoke --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    assert report["passed_count"] == 1
    assert report["blocked_count"] == 3
    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert agt["local_status"] == "passed"
    assert agt["consensus_grade_contribution"] is True
    assert agt["local_artifact_sha256"] == _sha256(artifact)
    assert (
        agt["evidence_artifact_contract_version"]
        == "wd.v12.rival_local_evidence_artifact.v1"
    )
    assert report["consensus_grade"] is False


def test_manifest_required_field_types_fail_closed_before_pass(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "microsoft-agt-smoke.json"
    artifact.parent.mkdir()
    _write_valid_artifact(
        artifact,
        rival="Microsoft AGT",
        pinned_revision="microsoft-agt-test-rev",
        evidence_type="local_smoke",
    )
    (evidence_dir / "microsoft-agt.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Microsoft AGT",
                "pinned_revision": "microsoft-agt-test-rev",
                "local_artifact_path": "artifacts/microsoft-agt-smoke.json",
                "local_artifact_sha256": _sha256(artifact),
                "smoke_command": ["microsoft-agt", "smoke", "--offline"],
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert report["passed_count"] == 0
    assert report["consensus_grade"] is False
    assert agt["local_status"] == "invalid_manifest"
    assert agt["blocker"] == "manifest field smoke_command must be str; got list"
    assert agt["consensus_grade_contribution"] is False


def test_repository_evidence_dir_keeps_microsoft_agt_passed() -> None:
    report = build_rival_local_check_matrix(
        evidence_dir=ROOT / "docs" / "benchmarks" / "rival_local_checks",
    )

    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert agt["local_status"] == "passed"
    assert agt["blocker"] is None
    assert agt["consensus_grade_contribution"] is True
    assert report["passed_count"] >= 1


def test_local_artifact_digest_is_line_ending_stable(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "microsoft-agt-smoke.json"
    artifact.parent.mkdir()
    _write_valid_artifact(
        artifact,
        rival="Microsoft AGT",
        pinned_revision="microsoft-agt-test-rev",
        evidence_type="local_smoke",
    )
    lf_digest = _sha256(artifact)
    artifact.write_bytes(
        artifact.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"),
    )
    (evidence_dir / "microsoft-agt.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Microsoft AGT",
                "pinned_revision": "microsoft-agt-test-rev",
                "local_artifact_path": "artifacts/microsoft-agt-smoke.json",
                "local_artifact_sha256": lf_digest,
                "smoke_command": "jamjet smoke --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert agt["local_status"] == "passed"
    assert agt["local_artifact_sha256"] == lf_digest


def test_weak_local_evidence_artifact_does_not_pass(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "microsoft-agt-smoke.json"
    artifact.parent.mkdir()
    artifact.write_text('{"ok": true, "offline": true}\n', encoding="utf-8")
    (evidence_dir / "microsoft-agt.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Microsoft AGT",
                "pinned_revision": "microsoft-agt-test-rev",
                "local_artifact_path": "artifacts/microsoft-agt-smoke.json",
                "local_artifact_sha256": _sha256(artifact),
                "smoke_command": "jamjet smoke --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert report["passed_count"] == 0
    assert agt["local_status"] == "invalid_artifact"
    assert "local artifact missing required fields" in agt["blocker"]


def test_local_evidence_artifact_must_match_manifest(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "microsoft-agt-smoke.json"
    artifact.parent.mkdir()
    _write_valid_artifact(
        artifact,
        rival="Microsoft AGT",
        pinned_revision="different-rev",
        evidence_type="local_smoke",
    )
    (evidence_dir / "microsoft-agt.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Microsoft AGT",
                "pinned_revision": "microsoft-agt-test-rev",
                "local_artifact_path": "artifacts/microsoft-agt-smoke.json",
                "local_artifact_sha256": _sha256(artifact),
                "smoke_command": "jamjet smoke --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert report["passed_count"] == 0
    assert agt["local_status"] == "invalid_artifact"
    assert agt["blocker"] == "local artifact pinned_revision does not match manifest"


def test_malformed_manifest_field_fails_closed(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "microsoft-agt-smoke.json"
    artifact.parent.mkdir()
    _write_valid_artifact(
        artifact,
        rival="Microsoft AGT",
        pinned_revision="microsoft-agt-test-rev",
        evidence_type="local_smoke",
    )
    (evidence_dir / "microsoft-agt.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": ["JamJet"],
                "pinned_revision": "microsoft-agt-test-rev",
                "local_artifact_path": "artifacts/microsoft-agt-smoke.json",
                "local_artifact_sha256": _sha256(artifact),
                "smoke_command": "jamjet smoke --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert report["passed_count"] == 0
    assert agt["local_status"] == "invalid_manifest"
    assert agt["blocker"] == "manifest field rival must be str; got list"


def test_malformed_artifact_field_fails_closed(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "microsoft-agt-smoke.json"
    artifact.parent.mkdir()
    artifact.write_text(
        json.dumps(
            {
                "evidence_artifact_contract_version": (
                    "wd.v12.rival_local_evidence_artifact.v1"
                ),
                "rival": ["JamJet"],
                "pinned_revision": "microsoft-agt-test-rev",
                "smoke_result": "passed",
                "offline": True,
                "ok": True,
                "evidence_type": "local_smoke",
                "observations": {
                    "policy_audit_or_replay_smoke": {
                        "ok": True,
                        "offline": True,
                        "summary": "policy/audit/replay smoke passed",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (evidence_dir / "microsoft-agt.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Microsoft AGT",
                "pinned_revision": "microsoft-agt-test-rev",
                "local_artifact_path": "artifacts/microsoft-agt-smoke.json",
                "local_artifact_sha256": _sha256(artifact),
                "smoke_command": "jamjet smoke --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert report["passed_count"] == 0
    assert agt["local_status"] == "invalid_artifact"
    assert agt["blocker"] == "local artifact field rival must be str; got list"


def test_cloud_dependent_manifest_does_not_pass(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "asqav-smoke.json"
    artifact.parent.mkdir()
    artifact.write_text('{"ok": true, "offline": false}\n', encoding="utf-8")
    (evidence_dir / "asqav.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Asqav",
                "pinned_revision": "asqav-test-rev",
                "local_artifact_path": "artifacts/asqav-smoke.json",
                "local_artifact_sha256": _sha256(artifact),
                "smoke_command": "asqav verify --online",
                "smoke_result": "passed",
                "cloud_dependency": True,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    assert report["passed_count"] == 0
    asqav = next(row for row in report["checks"] if row["rival"] == "Asqav")
    assert asqav["local_status"] == "cloud_dependent"
    assert asqav["blocker"] == "cloud_dependency is not false"


def test_cloud_dependent_manifest_surfaces_verified_artifact_proof(
    tmp_path: Path,
) -> None:
    """A cloud-dependent rival with a valid digest-matching artifact must
    remain non-passing (does NOT contribute to consensus_grade) but should
    surface artifact_digest_verified=true so the operator sees the
    local-proof receipt without mistaking it for consensus-grade."""
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "asqav-smoke.json"
    artifact.parent.mkdir()
    artifact.write_text(
        '{"ok": true, "offline": true, "queued": true}\n', encoding="utf-8"
    )
    (evidence_dir / "asqav.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Asqav",
                "pinned_revision": "asqav-test-rev",
                "local_artifact_path": "artifacts/asqav-smoke.json",
                "local_artifact_sha256": _sha256(artifact),
                "smoke_command": "asqav verify --online",
                "smoke_result": "passed",
                "cloud_dependency": True,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)
    asqav = next(row for row in report["checks"] if row["rival"] == "Asqav")

    # Cloud-dependent stays non-passing.
    assert asqav["local_status"] == "cloud_dependent"
    assert asqav["blocker"] == "cloud_dependency is not false"
    assert asqav.get("consensus_grade_contribution") is False
    assert asqav.get("blocked_artifact_reason") == "cloud_dependency"
    assert report["passed_count"] == 0
    # Aggregate consensus_grade stays False because cloud-dependent never
    # contributes -- this is the key honesty invariant.
    assert report["consensus_grade"] is False

    # ...but the artifact proof IS surfaced.
    proof = asqav["artifact_proof"]
    assert proof["artifact_digest_verified"] is True
    assert proof["artifact_digest_reason"] is None


def test_cloud_dependent_manifest_with_digest_mismatch_surfaces_unverified(
    tmp_path: Path,
) -> None:
    """A cloud-dependent rival whose manifest declares a digest that does
    NOT match the on-disk artifact surfaces artifact_digest_verified=false
    with a reason; this distinguishes 'cloud-dependent with audit-traceable
    proof' from 'cloud-dependent with broken proof'."""
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "asqav-smoke.json"
    artifact.parent.mkdir()
    artifact.write_text('{"ok": true, "offline": true}\n', encoding="utf-8")
    (evidence_dir / "asqav.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Asqav",
                "pinned_revision": "asqav-test-rev",
                "local_artifact_path": "artifacts/asqav-smoke.json",
                # Deliberately wrong digest:
                "local_artifact_sha256": "sha256:" + "0" * 64,
                "smoke_command": "asqav verify --online",
                "smoke_result": "passed",
                "cloud_dependency": True,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)
    asqav = next(row for row in report["checks"] if row["rival"] == "Asqav")

    assert asqav["local_status"] == "cloud_dependent"
    assert asqav.get("consensus_grade_contribution") is False
    proof = asqav["artifact_proof"]
    assert proof["artifact_digest_verified"] is False
    assert "does not match" in (proof["artifact_digest_reason"] or "")


def test_repository_evidence_dir_asqav_cloud_dependent_with_verified_artifact() -> None:
    """Regression: the committed Asqav manifest in
    docs/benchmarks/rival_local_checks/ has cloud_dependency=true so it
    must NOT contribute to consensus_grade, but its artifact MUST surface
    artifact_digest_verified=true under the lightweight proof so the
    operator sees the audit-traceable local receipt. The aggregate
    consensus_grade must stay False because cloud-dependent never
    contributes regardless of how many local proofs verify."""
    report = build_rival_local_check_matrix(
        evidence_dir=ROOT / "docs" / "benchmarks" / "rival_local_checks",
    )

    asqav = next(row for row in report["checks"] if row["rival"] == "Asqav")
    assert asqav["local_status"] == "cloud_dependent"
    assert asqav["consensus_grade_contribution"] is False
    assert asqav["blocked_artifact_reason"] == "cloud_dependency"
    proof = asqav["artifact_proof"]
    assert proof["artifact_digest_verified"] is True
    assert proof["artifact_digest_reason"] is None
    assert report["consensus_grade"] is False


def test_cloud_dependent_manifest_with_missing_artifact_file_surfaces_reason(
    tmp_path: Path,
) -> None:
    """A cloud-dependent manifest pointing at a missing artifact file
    must yield a structured artifact_proof block (digest_verified=false
    with a reason that mentions the file does not exist), distinguishing
    it from the digest-mismatch case."""
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "asqav.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Asqav",
                "pinned_revision": "asqav-test-rev",
                "local_artifact_path": "artifacts/asqav-missing.json",
                "local_artifact_sha256": "sha256:" + "0" * 64,
                "smoke_command": "asqav verify --online",
                "smoke_result": "passed",
                "cloud_dependency": True,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)
    asqav = next(row for row in report["checks"] if row["rival"] == "Asqav")

    assert asqav["local_status"] == "cloud_dependent"
    proof = asqav["artifact_proof"]
    assert proof["artifact_digest_verified"] is False
    assert "does not name an existing file" in (
        proof["artifact_digest_reason"] or ""
    )


def test_missing_required_observation_does_not_pass(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "microsoft-agt-smoke.json"
    artifact.parent.mkdir()
    _write_valid_artifact(
        artifact,
        rival="Microsoft AGT",
        pinned_revision="agt-test-rev",
        evidence_type="local_smoke",
        observations={
            "policy_deny_smoke": {
                "ok": True,
                "offline": True,
                "summary": "deny rule blocked a dangerous tool",
            }
        },
    )
    (evidence_dir / "microsoft-agt.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Microsoft AGT",
                "pinned_revision": "agt-test-rev",
                "local_artifact_path": "artifacts/microsoft-agt-smoke.json",
                "local_artifact_sha256": _sha256(artifact),
                "smoke_command": "agt policy smoke --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert report["passed_count"] == 0
    assert agt["local_status"] == "invalid_artifact"
    assert agt["blocker"] == (
        "local artifact missing required observations: fail_closed_error_path_smoke"
    )


def test_manifest_with_missing_local_artifact_does_not_pass(tmp_path: Path) -> None:
    """Use an installable-surface rival (Microsoft AGT) so the matrix
    exercises its invalid_artifact branch. Per Sprint 2 rival-axis
    hardening, JamJet/Preloop short-circuit to not_configured regardless
    of manifest contents, so cannot be used to test downstream branches."""
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "microsoft-agt.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Microsoft AGT",
                "pinned_revision": "microsoft-agt-test-rev",
                "local_artifact_path": "artifacts/microsoft-agt-smoke.json",
                "local_artifact_sha256": "sha256:" + ("0" * 64),
                "smoke_command": "microsoft-agt smoke --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert report["passed_count"] == 0
    assert agt["local_status"] == "invalid_artifact"
    assert agt["blocker"] == "local_artifact_path does not name an existing file"


def test_manifest_with_digest_mismatch_does_not_pass(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "microsoft-agt-smoke.json"
    artifact.parent.mkdir()
    artifact.write_text('{"ok": true, "offline": true}\n', encoding="utf-8")
    (evidence_dir / "microsoft-agt.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Microsoft AGT",
                "pinned_revision": "agt-test-rev",
                "local_artifact_path": "artifacts/microsoft-agt-smoke.json",
                "local_artifact_sha256": "sha256:" + ("0" * 64),
                "smoke_command": "agt policy-deny --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert report["passed_count"] == 0
    assert agt["local_status"] == "invalid_artifact"
    assert agt["blocker"] == "local_artifact_sha256 does not match artifact"


def test_manifest_path_must_stay_under_evidence_dir(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "microsoft-agt.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Microsoft AGT",
                "pinned_revision": "microsoft-agt-test-rev",
                "local_artifact_path": "../outside.json",
                "local_artifact_sha256": "sha256:" + ("0" * 64),
                "smoke_command": "jamjet smoke --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert report["passed_count"] == 0
    assert agt["local_status"] == "invalid_artifact"
    assert agt["blocker"] == "local_artifact_path escapes evidence_dir"


def test_manifest_contract_version_must_match_v1(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "microsoft-agt-smoke.json"
    artifact.parent.mkdir()
    artifact.write_text('{"ok": true, "offline": true}\n', encoding="utf-8")
    (evidence_dir / "microsoft-agt.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": "legacy.v0",
                "rival": "Microsoft AGT",
                "pinned_revision": "microsoft-agt-test-rev",
                "local_artifact_path": "artifacts/microsoft-agt-smoke.json",
                "local_artifact_sha256": _sha256(artifact),
                "smoke_command": "jamjet smoke --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    agt = next(row for row in report["checks"] if row["rival"] == "Microsoft AGT")
    assert report["passed_count"] == 0
    assert agt["local_status"] == "invalid_manifest"
    assert agt["blocker"] == "evidence_manifest_contract_version does not match v1"


def _sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    canonical_text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def _write_valid_artifact(
    path: Path,
    *,
    rival: str,
    pinned_revision: str,
    evidence_type: str,
    observations: dict[str, object] | None = None,
) -> None:
    default_observations = {
        "JamJet": {
            "policy_audit_or_replay_smoke": {
                "ok": True,
                "offline": True,
                "summary": "policy/audit/replay smoke passed",
            },
        },
        "Asqav": {
            "local_sign_or_hash_chain_smoke": {
                "ok": True,
                "offline": True,
                "summary": "local sign or hash-chain smoke passed",
            },
        },
        "Microsoft AGT": {
            "policy_deny_smoke": {
                "ok": True,
                "offline": True,
                "summary": "deny policy smoke passed",
            },
            "fail_closed_error_path_smoke": {
                "ok": True,
                "offline": True,
                "summary": "forced evaluator error denied",
            },
        },
        "Preloop": {
            "mcp_allow_deny_approval_smoke": {
                "ok": True,
                "offline": True,
                "summary": "MCP allow/deny/approval smoke passed",
            },
        },
    }
    path.write_text(
        json.dumps(
            {
                "evidence_artifact_contract_version": (
                    "wd.v12.rival_local_evidence_artifact.v1"
                ),
                "rival": rival,
                "pinned_revision": pinned_revision,
                "smoke_result": "passed",
                "offline": True,
                "ok": True,
                "evidence_type": evidence_type,
                "observations": observations or default_observations[rival],
            }
        )
        + "\n",
        encoding="utf-8",
    )


# --- public-doc-claim surface assessment (Sprint 2 rival-axis hardening) ---


def test_public_doc_claim_surface_registry_lists_every_required_rival() -> None:
    """Guard: every rival in REQUIRED_OBSERVATIONS_BY_RIVAL must have a
    corresponding entry in PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL. A new rival
    added without a surface assessment is a finding -- the registry is
    the source of truth for the anti-overclaim blocker."""
    from tools.run_v12_rival_local_check_matrix import (
        REQUIRED_OBSERVATIONS_BY_RIVAL,
    )

    for rival in REQUIRED_OBSERVATIONS_BY_RIVAL:
        assert rival in PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL, rival


def test_jamjet_and_preloop_surface_marked_as_open_source_installable() -> None:
    """Per the 2026-05-23 upstream verification pass (C2 scout artifact
    iterations/codex_scout_tasks/jamjet_preloop_oss_surface_scout_2026_05_23.md),
    JamJet (github.com/jamjet-labs/jamjet, Apache-2.0, python-sdk-v0.8.6,
    PyPI 'jamjet') and Preloop (github.com/preloop/preloop, Apache-2.0,
    v0.9.3, PyPI 'preloop') are both fully OSS-installable with hosted
    control planes explicitly optional. Registry must reflect this."""
    assert (
        PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL["JamJet"]
        == "open_source_installable"
    )
    assert (
        PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL["Preloop"]
        == "open_source_installable"
    )


def test_agt_and_asqav_surface_marked_as_installable() -> None:
    """AGT and Asqav have installable surfaces (open-source and pypi
    respectively); their surface assessment must be specific so the
    matrix does not lump them with the no_local_installable rivals."""
    assert (
        PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL["Microsoft AGT"]
        == "open_source_installable"
    )
    assert (
        PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL["Asqav"]
        == "pypi_installable_cloud_dependent_headline"
    )


def test_jamjet_default_call_reports_generic_blocker_after_upstream_verification() -> None:
    """Post-2026-05-23 upstream verification: JamJet is open_source_installable
    so without --evidence-dir the matrix surfaces the generic
    'no evidence_dir provided' blocker. The anti-overclaim teeth move to
    test_synthetic_hard_blocked_rival_manifest_cannot_bypass_block which
    monkeypatches a synthetic no_local_installable_surface_yet rival."""
    report = build_rival_local_check_matrix()
    jamjet = next(r for r in report["checks"] if r["rival"] == "JamJet")
    assert jamjet["local_status"] == "not_configured"
    assert jamjet["blocker"] == "no evidence_dir provided"
    assert jamjet["consensus_grade_contribution"] is False


def test_preloop_default_call_reports_generic_blocker_after_upstream_verification() -> None:
    """Post-2026-05-23 upstream verification: Preloop is open_source_installable
    so without --evidence-dir the matrix surfaces the generic
    'no evidence_dir provided' blocker (same as AGT)."""
    report = build_rival_local_check_matrix()
    preloop = next(r for r in report["checks"] if r["rival"] == "Preloop")
    assert preloop["local_status"] == "not_configured"
    assert preloop["blocker"] == "no evidence_dir provided"


def test_agt_default_call_keeps_generic_blocker() -> None:
    """AGT is open-source-installable per the registry, so when no
    evidence_dir is given the matrix should still report the generic
    'no evidence_dir provided' blocker -- AGT is NOT marked as
    structurally unable to be tested locally."""
    report = build_rival_local_check_matrix()
    agt = next(r for r in report["checks"] if r["rival"] == "Microsoft AGT")
    assert agt["local_status"] == "not_configured"
    assert agt["blocker"] == "no evidence_dir provided"


def test_evidence_dir_without_jamjet_manifest_reports_generic_missing_blocker(
    tmp_path: Path,
) -> None:
    """Post-2026-05-23 upstream verification: with no JamJet manifest in
    evidence_dir, JamJet (now open_source_installable) reports the
    generic 'evidence manifest missing' blocker, not silently passes.
    consensus_grade stays False."""
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    # Provide an empty evidence_dir -- no JamJet manifest.

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)
    jamjet = next(r for r in report["checks"] if r["rival"] == "JamJet")
    assert jamjet["local_status"] == "not_configured"
    assert jamjet["blocker"] == "evidence manifest missing"
    assert report["consensus_grade"] is False


def test_repository_evidence_dir_jamjet_static_artifact_stays_not_passing() -> None:
    """Production smoke: against the real docs/benchmarks/rival_local_checks
    evidence dir, JamJet has a digest-verified static-inspection artifact but
    remains not_passed because no policy/audit/replay smoke was run. Preloop
    has a digest-verified static metadata artifact but remains not_passed
    because no MCP allow/deny/approval smoke was run. AGT stays passed, Asqav
    stays cloud_dependent. Aggregate consensus_grade stays False because only
    1/4 rivals has actually passed a local check (AGT)."""
    report = build_rival_local_check_matrix(
        evidence_dir=ROOT / "docs" / "benchmarks" / "rival_local_checks",
    )

    jamjet = next(r for r in report["checks"] if r["rival"] == "JamJet")
    preloop = next(r for r in report["checks"] if r["rival"] == "Preloop")
    agt = next(r for r in report["checks"] if r["rival"] == "Microsoft AGT")
    asqav = next(r for r in report["checks"] if r["rival"] == "Asqav")

    assert jamjet["local_status"] == "not_passed"
    assert jamjet["blocker"] == "smoke_result is not passed"
    assert jamjet["blocked_artifact_reason"] == "smoke_result"
    assert jamjet["artifact_proof"]["artifact_digest_verified"] is True
    assert (
        jamjet["artifact_proof"]["local_artifact_sha256"]
        == "sha256:9004288030d50c41da509d0e189a1f79aba5333e73c476f5ea3e817f64ba28bc"
    )
    assert jamjet["consensus_grade_contribution"] is False
    assert preloop["local_status"] == "not_passed"
    assert preloop["blocker"] == "smoke_result is not passed"
    assert preloop["blocked_artifact_reason"] == "smoke_result"
    assert preloop["artifact_proof"]["artifact_digest_verified"] is True
    assert (
        preloop["artifact_proof"]["local_artifact_sha256"]
        == "sha256:3dbf07f70b700a917be0afdcc893b6dfb22c59403763b369470f35d1d41e53ae"
    )
    assert preloop["consensus_grade_contribution"] is False
    assert agt["local_status"] == "passed"
    assert asqav["local_status"] == "cloud_dependent"
    assert report["passed_count"] == 1
    assert report["consensus_grade"] is False


def test_synthetic_manifest_cannot_bypass_no_local_installable_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ANTI-OVERCLAIM TEETH (originally per Codex RCO on #607; re-targeted
    2026-05-23 post-upstream-verification of JamJet+Preloop). Even a
    fully-formed synthetic manifest claiming cloud_dependency=false and
    smoke_result=passed MUST NOT promote a rival to passing while the
    registry says no_local_installable_surface_yet. The block is
    structural, not cosmetic. Updating PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL
    is the only legitimate channel out of this state.

    Because the live registry no longer hard-blocks any of the four
    real rivals (post-2026-05-23 verification), this test monkeypatches
    JamJet's value back to no_local_installable_surface_yet to exercise
    the hard-block logic on a hypothetical synthetic future rival. The
    test is renamed to reflect the registry-driven invariant rather than
    a specific rival."""
    monkeypatch.setitem(
        PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL,
        "JamJet",
        "no_local_installable_surface_yet",
    )
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "jamjet-smoke.json"
    artifact.parent.mkdir()
    _write_valid_artifact(
        artifact,
        rival="JamJet",
        pinned_revision="jamjet-fake-rev",
        evidence_type="local_smoke",
    )
    (evidence_dir / "jamjet.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "JamJet",
                "pinned_revision": "jamjet-fake-rev",
                "local_artifact_path": "artifacts/jamjet-smoke.json",
                "local_artifact_sha256": _sha256(artifact),
                "smoke_command": "jamjet smoke --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)
    jamjet = next(row for row in report["checks"] if row["rival"] == "JamJet")

    # Hard block: even with a perfectly-formed manifest the registry's
    # monkeypatched no_local_installable_surface_yet surface assessment
    # trumps it.
    assert jamjet["local_status"] == "not_configured"
    assert jamjet["blocker"] == "no_local_installable_surface_yet"
    assert jamjet["consensus_grade_contribution"] is False
    assert report["passed_count"] == 0
    assert report["consensus_grade"] is False


def test_open_source_installable_rival_yields_not_passed_with_template_manifest(
    tmp_path: Path,
) -> None:
    """Post-2026-05-23 verification: with JamJet/Preloop now
    open_source_installable, an init template manifest (smoke_result=
    not_run, cloud_dependency=False) surfaces as local_status='not_passed'
    -- never silently 'passed'. This is the consensus_grade guard for the
    new (more permissive) registry value."""
    evidence_dir = tmp_path / "evidence"
    write_evidence_manifest_templates(evidence_dir=evidence_dir)

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)
    jamjet = next(r for r in report["checks"] if r["rival"] == "JamJet")
    preloop = next(r for r in report["checks"] if r["rival"] == "Preloop")

    assert jamjet["local_status"] == "not_passed"
    assert jamjet["blocker"] == "smoke_result is not passed"
    assert jamjet["consensus_grade_contribution"] is False
    assert preloop["local_status"] == "not_passed"
    assert preloop["blocker"] == "smoke_result is not passed"
    assert preloop["consensus_grade_contribution"] is False
    assert report["passed_count"] == 0
    assert report["consensus_grade"] is False


def test_not_passed_manifest_can_surface_lightweight_artifact_proof(
    tmp_path: Path,
) -> None:
    evidence_dir = tmp_path / "evidence"
    artifact = evidence_dir / "artifacts" / "jamjet-static-inspection.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "rival": "JamJet",
                "smoke_result": "not_run",
                "ok": False,
                "offline": True,
                "notes": "static inspection only",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (evidence_dir / "jamjet.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "JamJet",
                "pinned_revision": "pypi:jamjet==0.8.6",
                "local_artifact_path": "artifacts/jamjet-static-inspection.json",
                "local_artifact_sha256": _sha256(artifact),
                "smoke_command": "static inspection only",
                "smoke_result": "not_run",
                "cloud_dependency": False,
                "evidence_type": "local_inspection",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)
    jamjet = next(row for row in report["checks"] if row["rival"] == "JamJet")

    assert jamjet["local_status"] == "not_passed"
    assert jamjet["blocker"] == "smoke_result is not passed"
    assert jamjet["blocked_artifact_reason"] == "smoke_result"
    assert jamjet["artifact_proof"]["artifact_digest_verified"] is True
    assert jamjet["artifact_proof"]["local_artifact_sha256"] == _sha256(artifact)
    assert jamjet["consensus_grade_contribution"] is False
    assert report["passed_count"] == 0
    assert report["consensus_grade"] is False
