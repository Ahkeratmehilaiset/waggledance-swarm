from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from tools.run_v12_supervisor_demo_pack import build_demo_pack


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_v12_supervisor_demo_pack.py"
CORPUS = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0.json"


def _run_demo(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _expected_case_count() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    return len(corpus["cases"])


def test_build_demo_pack_writes_verified_evidence(tmp_path: Path) -> None:
    expected_case_count = _expected_case_count()
    out_dir = tmp_path / "demo-pack"

    result = build_demo_pack(out_dir=out_dir, now_utc=_fixed_now())

    assert result["adversarial_case_count"] == expected_case_count
    assert result["adversarial_pass_count"] == expected_case_count
    assert result["writes_applied"] is False
    assert result["receipt_verifier_ok"] is True
    assert result["receipt_count"] == 1
    assert result["high_criticality_gap_count"] == 0
    assert result["status_counts"] == {
        "receipt_bound": 3,
        "receipt_capable_opt_in": 4,
    }
    assert result["a3_counterfactual_delta_proven"] is True
    assert result["a3_receipt_chain_verified"] is True
    assert result["a4_solver_growth_proven"] is True
    assert result["a4_receipt_chain_verified"] is True
    assert result["a4_dispatch_success_count"] == 27
    assert result["a4_dispatch_case_count"] == 27
    assert result["a4_registered_solver_count"] == 6
    assert result["rival_local_check_pass_count"] == 0
    assert result["rival_local_check_required_count"] == 4
    assert result["competitor_consensus_grade"] is False
    assert result["rival_evidence_template_count"] == 4
    assert Path(result["rival_evidence_template_dir"]) == out_dir / "rival_evidence_templates"
    assert Path(result["artifact_manifest"]) == out_dir / "demo_pack_artifact_manifest.json"
    assert result["artifact_manifest_verifier_command"] == (
        "python tools/verify_v12_demo_pack_artifact_manifest.py --manifest "
        f"{out_dir / 'demo_pack_artifact_manifest.json'}"
    )
    assert (out_dir / "summary.md").exists()
    assert (out_dir / "adversarial_eval_report.json").exists()
    assert (out_dir / "adversarial_receipts" / "manifest.json").exists()
    assert (out_dir / "receipt_verifier_report.json").exists()
    assert (out_dir / "receipt_adoption_report.md").exists()
    assert (out_dir / "a3_counterfactual_axis_proof.json").exists()
    assert (out_dir / "a3_counterfactual_axis_proof.md").exists()
    assert (out_dir / "a3_counterfactual_receipts" / "manifest.json").exists()
    assert (out_dir / "a4_solver_growth_axis_proof.json").exists()
    assert (out_dir / "a4_solver_growth_axis_proof.md").exists()
    assert (
        out_dir / "a4_solver_growth_axis" / "a4_solver_growth_receipts" / "manifest.json"
    ).exists()
    assert (out_dir / "rival_evidence_template_init.json").exists()
    assert (out_dir / "rival_evidence_templates" / "jamjet.json").exists()
    assert (out_dir / "rival_evidence_templates" / "asqav.json").exists()
    assert (out_dir / "rival_evidence_templates" / "microsoft-agt.json").exists()
    assert (out_dir / "rival_evidence_templates" / "preloop.json").exists()
    assert (out_dir / "rival_local_check_matrix.json").exists()
    assert (out_dir / "rival_local_check_matrix.md").exists()
    assert (out_dir / "demo_pack_artifact_manifest.json").exists()

    report = json.loads((out_dir / "adversarial_eval_report.json").read_text(encoding="utf-8"))
    assert report["receipt_bundle"]["verifier_report"]["ok"] is True
    assert report["writes_applied"] is False
    assert report["full_match_count"] == expected_case_count
    rival_matrix = json.loads((out_dir / "rival_local_check_matrix.json").read_text(encoding="utf-8"))
    assert rival_matrix["consensus_grade"] is False
    assert rival_matrix["rival_local_checks_status"] == "0/4 rival local checks passed"
    # Per 2026-05-23 upstream verification (C2): JamJet + Preloop are
    # open_source_installable (Apache-2.0 OSS with public GitHub +
    # PyPI surfaces; see iterations/codex_scout_tasks/
    # jamjet_preloop_oss_surface_scout_2026_05_23.md). All four init
    # templates with smoke_result=not_run now surface as not_passed.
    assert {row["local_status"] for row in rival_matrix["checks"]} == {"not_passed"}
    assert rival_matrix["template_init"]["created_count"] == 4
    assert rival_matrix["template_init"]["safe_defaults"]["smoke_result"] == "not_run"
    template_init = json.loads((out_dir / "rival_evidence_template_init.json").read_text(encoding="utf-8"))
    assert template_init["created_count"] == 4
    assert template_init["safe_defaults"]["consensus_grade_contribution"] is False
    jamjet_template = json.loads(
        (out_dir / "rival_evidence_templates" / "jamjet.json").read_text(
            encoding="utf-8"
        )
    )
    assert jamjet_template["smoke_result"] == "not_run"
    assert jamjet_template["local_artifact_sha256"] == "sha256:" + ("0" * 64)
    a3_proof = json.loads((out_dir / "a3_counterfactual_axis_proof.json").read_text(encoding="utf-8"))
    assert a3_proof["counterfactual_delta_proven"] is True
    assert a3_proof["receipt_chain_verified"] is True
    a4_proof = json.loads((out_dir / "a4_solver_growth_axis_proof.json").read_text(encoding="utf-8"))
    assert a4_proof["solver_growth_proven"] is True
    assert a4_proof["receipt_chain_verified"] is True

    summary = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert "WD V12 Supervisor Demo Pack" in summary
    assert f"{expected_case_count}/{expected_case_count}" in summary
    assert "writes_applied: `false`" in summary
    assert "A3 counterfactual delta proven: `true`" in summary
    assert "A3 receipt chain verified: `true`" in summary
    assert "A4 solver growth proven: `true`" in summary
    assert "A4 dispatch success: `27/27`" in summary
    assert "A4 receipt chain verified: `true`" in summary
    assert "rival local checks passed: `0/4`" in summary
    assert "competitor consensus grade: `false`" in summary
    assert "rival evidence templates: `4` safe non-passing manifests" in summary
    assert "artifact manifest: `demo_pack_artifact_manifest.json`" in summary
    assert (
        "artifact manifest verifier: `python tools/verify_v12_demo_pack_artifact_manifest.py "
        "--manifest <demo-pack-dir>/demo_pack_artifact_manifest.json`"
    ) in summary

    artifact_manifest = json.loads(
        (out_dir / "demo_pack_artifact_manifest.json").read_text(encoding="utf-8")
    )
    assert (
        artifact_manifest["manifest_version"]
        == "wd.v12.supervisor_demo_pack.artifact_manifest.v0"
    )
    assert artifact_manifest["demo_version"] == "wd.v12.supervisor_demo_pack.v0"
    assert artifact_manifest["generated_at_utc"] == "2026-05-20T18:50:00Z"
    assert artifact_manifest["file_count"] == result["artifact_manifest_file_count"]
    manifest_paths = {entry["path"] for entry in artifact_manifest["files"]}
    assert "demo_pack_artifact_manifest.json" not in manifest_paths
    assert "summary.md" in manifest_paths
    assert "rival_evidence_templates/jamjet.json" in manifest_paths
    assert "a3_counterfactual_axis_proof.json" in manifest_paths
    summary_entry = next(
        entry for entry in artifact_manifest["files"] if entry["path"] == "summary.md"
    )
    summary_digest = hashlib.sha256(
        (out_dir / "summary.md").read_bytes()
    ).hexdigest()
    assert summary_entry["sha256"] == "sha256:" + summary_digest
    assert f"artifact manifest file count: `{artifact_manifest['file_count']}`" in summary


def test_demo_pack_does_not_leak_hidden_expectations_or_canaries(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "demo-pack"

    build_demo_pack(out_dir=out_dir, now_utc=_fixed_now())

    combined = ""
    for path in out_dir.rglob("*.json"):
        combined += path.read_text(encoding="utf-8")
    for path in out_dir.rglob("*.md"):
        combined += path.read_text(encoding="utf-8")

    assert "_DO_NOT_LEAK" not in combined
    assert "peer_review_trap_marker" not in combined
    assert "should_claude_catch" not in combined
    assert "should_codex_catch" not in combined
    assert "expected_verdict" not in combined


def test_cli_json_reports_demo_pack(tmp_path: Path) -> None:
    expected_case_count = _expected_case_count()
    out_dir = tmp_path / "demo-pack"

    result = _run_demo(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-05-20T18:50:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["demo_version"] == "wd.v12.supervisor_demo_pack.v0"
    assert payload["adversarial_case_count"] == expected_case_count
    assert payload["receipt_verifier_ok"] is True
    assert payload["a3_counterfactual_delta_proven"] is True
    assert payload["a3_receipt_chain_verified"] is True
    assert payload["a4_solver_growth_proven"] is True
    assert payload["a4_receipt_chain_verified"] is True
    assert payload["a4_dispatch_success_count"] == 27
    assert payload["rival_local_check_pass_count"] == 0
    assert payload["competitor_consensus_grade"] is False
    assert payload["rival_evidence_template_count"] == 4
    assert payload["artifact_manifest_file_count"] > 0
    assert payload["artifact_manifest_verifier_command"].startswith(
        "python tools/verify_v12_demo_pack_artifact_manifest.py --manifest "
    )
    assert (out_dir / "summary.md").exists()
    assert Path(payload["artifact_manifest"]) == out_dir / "demo_pack_artifact_manifest.json"
    assert str(out_dir / "demo_pack_artifact_manifest.json") in payload[
        "artifact_manifest_verifier_command"
    ]
    assert (out_dir / "demo_pack_artifact_manifest.json").exists()


def test_cli_refuses_existing_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "demo-pack"
    out_dir.mkdir()

    result = _run_demo("--out-dir", str(out_dir))

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr


def test_cli_rejects_non_utc_timestamp(tmp_path: Path) -> None:
    result = _run_demo(
        "--out-dir",
        str(tmp_path / "demo-pack"),
        "--now",
        "2026-05-20T21:50:00+03:00",
    )

    assert result.returncode == 1
    assert "--now requires a UTC timestamp" in result.stderr


def _fixed_now() -> datetime:
    return datetime(2026, 5, 20, 18, 50, tzinfo=timezone.utc)
