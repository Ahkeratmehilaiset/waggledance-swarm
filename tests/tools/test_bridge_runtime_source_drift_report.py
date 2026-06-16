# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "bridge_runtime_source_drift_report.py"
sys.path.insert(0, str(ROOT))

from tools.bridge_runtime_source_drift_report import (  # noqa: E402
    report_runtime_source_drift,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, filename: str, content: str, message: str) -> str:
    path = repo / filename
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Tests")
    return repo


def test_reports_clean_current_reference(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = _commit(repo, "a.txt", "a\n", "initial")

    report = report_runtime_source_drift(repo=repo, reference="HEAD")

    assert report["decision"] == "runtime_source_clean_current"
    assert report["drift"] is False
    assert report["dirty"] is False
    assert report["relationship"] == "at_reference"
    assert report["head"] == head
    assert report["reference_oid"] == head
    assert report["authority_boundary"]["git_fetch_allowed"] is False


def test_reports_dirty_runtime_even_when_reference_is_present(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "a.txt", "a\n", "initial")
    (repo / "scratch.txt").write_text("scratch\n", encoding="utf-8")

    report = report_runtime_source_drift(repo=repo, reference="HEAD")

    assert report["decision"] == "runtime_source_dirty"
    assert report["drift"] is True
    assert report["source_drift"] is False
    assert report["dirty"] is True
    assert report["dirty_count"] == 1
    assert report["untracked_count"] == 1
    assert report["safe_next_action"] == (
        "checkpoint_or_isolate_runtime_worktree_before_updating_source"
    )


def test_reports_behind_reference(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _commit(repo, "a.txt", "a\n", "initial")
    _commit(repo, "b.txt", "b\n", "second")
    _git(repo, "checkout", "--detach", base)

    report = report_runtime_source_drift(repo=repo, reference="master")

    assert report["decision"] == "runtime_source_behind_reference"
    assert report["drift"] is True
    assert report["source_drift"] is True
    assert report["relationship"] == "behind_reference"
    assert report["head_is_ancestor_of_reference"] is True
    assert report["contains_reference"] is False


def test_reports_diverged_from_reference(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _commit(repo, "a.txt", "a\n", "initial")
    _git(repo, "checkout", "-b", "runtime")
    _commit(repo, "runtime.txt", "runtime\n", "runtime")
    _git(repo, "checkout", "master")
    _commit(repo, "main.txt", "main\n", "main")
    _git(repo, "checkout", "runtime")

    report = report_runtime_source_drift(repo=repo, reference="master")

    assert report["decision"] == "runtime_source_diverged_from_reference"
    assert report["drift"] is True
    assert report["relationship"] == "diverged_from_reference"
    assert report["head"] != base
    assert report["head_is_ancestor_of_reference"] is False
    assert report["contains_reference"] is False


def test_cli_fail_on_drift_returns_three(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "a.txt", "a\n", "initial")
    (repo / "scratch.txt").write_text("scratch\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo),
            "--reference",
            "HEAD",
            "--fail-on-drift",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["decision"] == "runtime_source_dirty"
