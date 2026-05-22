from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from tools.run_v12_rival_local_check_matrix import (
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
    jamjet_manifest = evidence_dir / "jamjet.json"
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
    assert {row["local_status"] for row in payload["checks"]} == {"not_passed"}
    manifest = json.loads(jamjet_manifest.read_text(encoding="utf-8"))
    assert manifest["smoke_result"] == "not_run"
    assert manifest["local_artifact_sha256"] == "sha256:" + ("0" * 64)


def test_valid_local_evidence_manifest_marks_one_rival_passed(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "jamjet-smoke.json"
    artifact.parent.mkdir()
    _write_valid_artifact(
        artifact,
        rival="JamJet",
        pinned_revision="jamjet-test-rev",
        evidence_type="local_smoke",
    )
    (evidence_dir / "jamjet.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "JamJet",
                "pinned_revision": "jamjet-test-rev",
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

    assert report["passed_count"] == 1
    assert report["blocked_count"] == 3
    jamjet = next(row for row in report["checks"] if row["rival"] == "JamJet")
    assert jamjet["local_status"] == "passed"
    assert jamjet["consensus_grade_contribution"] is True
    assert jamjet["local_artifact_sha256"] == _sha256(artifact)
    assert (
        jamjet["evidence_artifact_contract_version"]
        == "wd.v12.rival_local_evidence_artifact.v1"
    )
    assert report["consensus_grade"] is False


def test_weak_local_evidence_artifact_does_not_pass(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "jamjet-smoke.json"
    artifact.parent.mkdir()
    artifact.write_text('{"ok": true, "offline": true}\n', encoding="utf-8")
    (evidence_dir / "jamjet.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "JamJet",
                "pinned_revision": "jamjet-test-rev",
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
    assert report["passed_count"] == 0
    assert jamjet["local_status"] == "invalid_artifact"
    assert "local artifact missing required fields" in jamjet["blocker"]


def test_local_evidence_artifact_must_match_manifest(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "jamjet-smoke.json"
    artifact.parent.mkdir()
    _write_valid_artifact(
        artifact,
        rival="JamJet",
        pinned_revision="different-rev",
        evidence_type="local_smoke",
    )
    (evidence_dir / "jamjet.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "JamJet",
                "pinned_revision": "jamjet-test-rev",
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
    assert report["passed_count"] == 0
    assert jamjet["local_status"] == "invalid_artifact"
    assert jamjet["blocker"] == "local artifact pinned_revision does not match manifest"


def test_malformed_manifest_field_fails_closed(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "jamjet-smoke.json"
    artifact.parent.mkdir()
    _write_valid_artifact(
        artifact,
        rival="JamJet",
        pinned_revision="jamjet-test-rev",
        evidence_type="local_smoke",
    )
    (evidence_dir / "jamjet.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": ["JamJet"],
                "pinned_revision": "jamjet-test-rev",
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
    assert report["passed_count"] == 0
    assert jamjet["local_status"] == "invalid_manifest"
    assert jamjet["blocker"] == "manifest rival does not match pilot row"


def test_malformed_artifact_field_fails_closed(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "jamjet-smoke.json"
    artifact.parent.mkdir()
    artifact.write_text(
        json.dumps(
            {
                "evidence_artifact_contract_version": (
                    "wd.v12.rival_local_evidence_artifact.v1"
                ),
                "rival": ["JamJet"],
                "pinned_revision": "jamjet-test-rev",
                "smoke_result": "passed",
                "offline": True,
                "ok": True,
                "evidence_type": "local_smoke",
            }
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
                "pinned_revision": "jamjet-test-rev",
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
    assert report["passed_count"] == 0
    assert jamjet["local_status"] == "invalid_artifact"
    assert jamjet["blocker"] == "local artifact rival does not match manifest"


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


def test_manifest_with_missing_local_artifact_does_not_pass(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "preloop.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "Preloop",
                "pinned_revision": "preloop-test-rev",
                "local_artifact_path": "artifacts/preloop-smoke.json",
                "local_artifact_sha256": "sha256:" + ("0" * 64),
                "smoke_command": "preloop smoke --offline",
                "smoke_result": "passed",
                "cloud_dependency": False,
                "evidence_type": "local_smoke",
            }
        ),
        encoding="utf-8",
    )

    report = build_rival_local_check_matrix(evidence_dir=evidence_dir)

    preloop = next(row for row in report["checks"] if row["rival"] == "Preloop")
    assert report["passed_count"] == 0
    assert preloop["local_status"] == "invalid_artifact"
    assert preloop["blocker"] == "local_artifact_path does not name an existing file"


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
    (evidence_dir / "jamjet.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": (
                    "wd.v12.rival_local_evidence_manifest.v1"
                ),
                "rival": "JamJet",
                "pinned_revision": "jamjet-test-rev",
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

    jamjet = next(row for row in report["checks"] if row["rival"] == "JamJet")
    assert report["passed_count"] == 0
    assert jamjet["local_status"] == "invalid_artifact"
    assert jamjet["blocker"] == "local_artifact_path escapes evidence_dir"


def test_manifest_contract_version_must_match_v1(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    artifact = evidence_dir / "artifacts" / "jamjet-smoke.json"
    artifact.parent.mkdir()
    artifact.write_text('{"ok": true, "offline": true}\n', encoding="utf-8")
    (evidence_dir / "jamjet.json").write_text(
        json.dumps(
            {
                "evidence_manifest_contract_version": "legacy.v0",
                "rival": "JamJet",
                "pinned_revision": "jamjet-test-rev",
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
    assert report["passed_count"] == 0
    assert jamjet["local_status"] == "invalid_manifest"
    assert jamjet["blocker"] == "evidence_manifest_contract_version does not match v1"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_valid_artifact(
    path: Path,
    *,
    rival: str,
    pinned_revision: str,
    evidence_type: str,
) -> None:
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
            }
        )
        + "\n",
        encoding="utf-8",
    )
