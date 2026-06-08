# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_v12_memory_palace_shortcut_promotion_candidates import (
    build_memory_palace_shortcut_promotion_candidate_report,
)
from tools.verify_v12_memory_palace_shortcut_promotion_candidates import (
    VERIFICATION_VERSION,
    verify_memory_palace_shortcut_promotion_candidate_report,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "verify_v12_memory_palace_shortcut_promotion_candidates.py"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_verifies_valid_promotion_candidate_report() -> None:
    report = _valid_report()

    verification = verify_memory_palace_shortcut_promotion_candidate_report(
        report,
    )

    assert verification["ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["source_report_version_check"] == "match"
    assert verification["source_claim_label_check"] == "match"
    assert verification["candidate_count_checked"] == 2
    assert verification["promotion_observable_count_checked"] == 2
    assert verification["authority_boundary_check"] == "match"
    assert verification["guardrail_check"] == "match"
    assert verification["candidate_action_boundary_check"] == "match"
    assert verification["promotion_action_allowed"] is False
    assert verification["promotion_performed"] is False
    assert verification["runtime_authority_granted"] is False
    assert verification["blockers"] == []


def test_rejects_authority_boundary_promotion_action() -> None:
    report = _valid_report()
    report["authority_boundary"]["promotion_action_allowed"] = True

    verification = verify_memory_palace_shortcut_promotion_candidate_report(
        report,
    )

    assert verification["ok"] is False
    assert verification["authority_boundary_check"] == "mismatch"
    assert (
        "authority_boundary_promotion_action_allowed_not_false"
        in verification["blockers"]
    )
    assert verification["promotion_action_allowed"] is False


def test_rejects_candidate_side_effects_without_echoing_candidate_values() -> None:
    report = _valid_report()
    report["promotion_candidates"][0]["promotion_performed"] = True
    report["promotion_candidates"][0]["target_node_id"] = (
        r"C:\operator\private\palace.json"
    )

    verification = verify_memory_palace_shortcut_promotion_candidate_report(
        report,
    )
    encoded = json.dumps(verification, sort_keys=True)

    assert verification["ok"] is False
    assert verification["candidate_action_boundary_check"] == "mismatch"
    assert "candidate_0_promotion_performed_not_false" in (
        verification["blockers"]
    )
    assert r"C:\operator\private\palace.json" not in encoded
    assert "palace.json" not in encoded


def test_rejects_candidate_summary_drift() -> None:
    report = _valid_report()
    report["candidate_summary"]["promotion_observable_count"] = 99

    verification = verify_memory_palace_shortcut_promotion_candidate_report(
        report,
    )

    assert verification["ok"] is False
    assert "promotion_observable_count_mismatch" in (
        verification["blockers"]
    )


def test_rejects_missing_operator_gate_guardrail() -> None:
    report = _valid_report()
    report["no_overclaim_guardrails"][
        "operator_gate_required_for_runtime_promotion"
    ] = False

    verification = verify_memory_palace_shortcut_promotion_candidate_report(
        report,
    )

    assert verification["ok"] is False
    assert verification["guardrail_check"] == "mismatch"
    assert (
        "guardrail_operator_gate_required_for_runtime_promotion_not_true"
        in verification["blockers"]
    )


def test_cli_json_verifies_report_path_free(tmp_path: Path) -> None:
    report_path = tmp_path / "promotion_candidates_report.json"
    _write_json(report_path, _valid_report())

    result = _run("--report-json", str(report_path), "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["candidate_count_checked"] == 2
    assert str(tmp_path) not in result.stdout
    assert report_path.name not in result.stdout
    assert str(tmp_path) not in result.stderr
    assert report_path.name not in result.stderr


def test_cli_rejects_duplicate_json_keys_path_free(tmp_path: Path) -> None:
    report_path = tmp_path / "unsafe_report.json"
    report_path.write_text(
        '{"report_version":"x","report_version":"y"}',
        encoding="utf-8",
    )

    result = _run("--report-json", str(report_path), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == [
        "memory_palace_shortcut_promotion_candidates_verification_failed:"
        "memory_palace_shortcut_promotion_candidates_report_json_error"
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert report_path.name not in combined


def test_cli_missing_input_is_path_free() -> None:
    missing = Path("C:/operator/private/missing_report.json")

    result = _run("--report-json", str(missing), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "memory_palace_shortcut_promotion_candidates_verification_failed:"
        "memory_palace_shortcut_promotion_candidates_report_unreadable"
    ]
    combined = result.stdout + result.stderr
    assert str(missing) not in combined
    assert "missing_report.json" not in combined


def _valid_report() -> dict[str, object]:
    return build_memory_palace_shortcut_promotion_candidate_report(
        now_utc=datetime(2026, 6, 7, 16, 0, tzinfo=timezone.utc),
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
