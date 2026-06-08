# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_v12_memory_palace_shortcut_runtime_promotion_design_verification_summary import (
    SUMMARY_VERSION,
    build_memory_palace_shortcut_runtime_promotion_design_verification_summary,
    render_markdown,
)
from tools.run_v12_memory_palace_shortcut_runtime_promotion_design import (
    build_memory_palace_shortcut_runtime_promotion_design,
)
from tools.verify_v12_memory_palace_shortcut_runtime_promotion_design import (
    verify_memory_palace_shortcut_runtime_promotion_design,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_v12_memory_palace_shortcut_runtime_promotion_design_verification_summary.py"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_summary_accepts_valid_verification_without_authority() -> None:
    summary = build_memory_palace_shortcut_runtime_promotion_design_verification_summary(
        _valid_verification(),
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["source_verification_ok"] is True
    assert summary["runtime_promotion_design_count_checked"] == 2
    assert set(summary["checks"].values()) == {"match"}
    assert set(summary["required_true_flags"].values()) == {True}
    assert set(summary["authority_boundary"].values()) == {False}
    assert summary["promotion_action_allowed"] is False
    assert summary["promotion_performed"] is False
    assert summary["runtime_route_changed"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["blockers"] == []


def test_summary_rejects_source_failure_and_redacts_unsafe_blockers() -> None:
    verification = _valid_verification()
    verification["ok"] = False
    verification["promotion_action_allowed"] = True
    verification["blockers"] = [
        "authority_boundary_check_failed",
        r"C:\operator\private\palace.json",
    ]

    summary = build_memory_palace_shortcut_runtime_promotion_design_verification_summary(
        verification,
    )
    encoded = json.dumps(summary, sort_keys=True)

    assert summary["ok"] is False
    assert "source_verification_not_ok" in summary["blockers"]
    assert "promotion_action_allowed_not_false" in summary["blockers"]
    assert "source_verification_blockers_present" in summary["blockers"]
    assert "authority_boundary_check_failed" in summary["blockers"]
    assert "unsafe_blocker_redacted" in summary["blockers"]
    assert summary["promotion_action_allowed"] is False
    assert r"C:\operator\private\palace.json" not in encoded
    assert "palace.json" not in encoded


def test_summary_fails_closed_on_missing_version() -> None:
    summary = build_memory_palace_shortcut_runtime_promotion_design_verification_summary(
        {},
    )

    assert summary["ok"] is False
    assert summary["source_verification_version"] == ""
    assert "verification_version_mismatch" in summary["blockers"]
    assert "source_verification_not_ok" in summary["blockers"]
    assert summary["runtime_authority_granted"] is False


def test_render_markdown_reports_summary_without_release_decision() -> None:
    summary = build_memory_palace_shortcut_runtime_promotion_design_verification_summary(
        _valid_verification(),
    )

    markdown = render_markdown(summary)

    assert "V12 Memory Palace Runtime-Promotion Design Verification Summary" in markdown
    assert "ok: `true`" in markdown
    assert "runtime_promotion_design_count_checked: `2`" in markdown
    assert "This does not promote a route" in markdown


def test_cli_json_summarizes_verification_path_free(tmp_path: Path) -> None:
    verification_path = tmp_path / "verification.json"
    _write_json(verification_path, _valid_verification())

    result = _run("--verification-json", str(verification_path), "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["runtime_promotion_design_count_checked"] == 2
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert verification_path.name not in combined


def test_cli_rejects_duplicate_json_keys_path_free(tmp_path: Path) -> None:
    verification_path = tmp_path / "unsafe_verification.json"
    verification_path.write_text(
        '{"verification_version":"x","verification_version":"y"}',
        encoding="utf-8",
    )

    result = _run("--verification-json", str(verification_path), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == [
        "memory_palace_shortcut_runtime_promotion_design_verification_"
        "summary_failed:"
        "memory_palace_shortcut_runtime_promotion_design_verification_json_error",
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert verification_path.name not in combined


def test_cli_rejects_non_finite_json_path_free(tmp_path: Path) -> None:
    verification_path = tmp_path / "nan_verification.json"
    verification_path.write_text(
        '{"verification_version":"x","ok":NaN}',
        encoding="utf-8",
    )

    result = _run("--verification-json", str(verification_path), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == [
        "memory_palace_shortcut_runtime_promotion_design_verification_"
        "summary_failed:"
        "memory_palace_shortcut_runtime_promotion_design_verification_json_error",
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert verification_path.name not in combined


def test_cli_missing_input_is_path_free() -> None:
    missing = Path("C:/operator/private/missing_verification.json")

    result = _run("--verification-json", str(missing), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "memory_palace_shortcut_runtime_promotion_design_verification_"
        "summary_failed:"
        "memory_palace_shortcut_runtime_promotion_design_verification_unreadable",
    ]
    combined = result.stdout + result.stderr
    assert str(missing) not in combined
    assert "missing_verification.json" not in combined


def _valid_verification() -> dict[str, object]:
    report = build_memory_palace_shortcut_runtime_promotion_design(
        now_utc=datetime(2026, 6, 8, 7, 30, tzinfo=timezone.utc),
    )
    return verify_memory_palace_shortcut_runtime_promotion_design(report)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
