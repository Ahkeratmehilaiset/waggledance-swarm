# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "clean_pytest_temp.ps1"
DOC = ROOT / "docs" / "operations" / "PYTEST_TEMP_ACL_CLEANUP.md"


def test_pytest_temp_directories_are_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".codex-audit/" in gitignore
    assert ".pytest_tmp*/" in gitignore


def test_policy_points_new_pytest_runs_to_audit_basetemp() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "--basetemp=.codex-audit/pytest_tmp/<task-id>" in text
    assert ".pytest_tmp*" in text
    assert "Orphan worktree deletion is a separate operator task" in text


def test_cleanup_script_has_path_guard_and_literal_remove() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "Test-IsAllowedPytestTempTarget" in text
    assert "Test-IsSubPath" in text
    assert "Remove-Item -LiteralPath $targetPath -Recurse -Force" in text
    assert "SupportsShouldProcess = $true" in text
    assert "cmd /c" not in text.lower()


def test_cleanup_script_repairs_acl_only_after_allowlist_check() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    allowlist_pos = text.index("Test-IsAllowedPytestTempTarget -Target $targetPath")
    takeown_pos = text.index("takeown /F $targetPath")
    icacls_pos = text.index("icacls $targetPath")

    assert allowlist_pos < takeown_pos < icacls_pos
    assert "-RepairAcl" in DOC.read_text(encoding="utf-8")
