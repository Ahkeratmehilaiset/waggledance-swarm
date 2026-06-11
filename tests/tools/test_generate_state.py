# SPDX-License-Identifier: BUSL-1.1
"""scan_licenses counts tracked files only.

The license tally in CURRENT_STATE.md must reflect the repository, not the
working copy: untracked third-party trees (e.g. a local ``.venv``) inflated
the 2026-05-11 refresh to 1741/1594 vs the tracked truth of ~660/280. These
tests lock the tracked-files contract and the no-git fallback.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools import generate_state

BUSL_HEADER = "# SPDX-License-Identifier: BUSL-1.1\n"
APACHE_HEADER = "# SPDX-License-Identifier: Apache-2.0\n"


@pytest.fixture()
def fixture_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tiny git repo: 2 tracked BUSL + 1 tracked Apache + untracked noise."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True
    )
    (repo / "a_busl.py").write_text(BUSL_HEADER, encoding="utf-8")
    (repo / "b_busl.py").write_text(BUSL_HEADER, encoding="utf-8")
    (repo / "c_apache.py").write_text(APACHE_HEADER, encoding="utf-8")
    subprocess.run(
        ["git", "add", "a_busl.py", "b_busl.py", "c_apache.py"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    # Untracked working-copy noise that must not affect the tally.
    vendored = repo / ".venv" / "site-packages"
    vendored.mkdir(parents=True)
    (vendored / "vendored_apache.py").write_text(APACHE_HEADER, encoding="utf-8")
    (repo / "untracked_busl.py").write_text(BUSL_HEADER, encoding="utf-8")
    monkeypatch.setattr(generate_state, "ROOT", repo)
    return repo


def test_scan_licenses_counts_tracked_files_only(fixture_repo: Path) -> None:
    assert generate_state.scan_licenses() == (2, 1)


def test_scan_licenses_falls_back_to_walk_without_git(
    fixture_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generate_state, "_tracked_python_files", lambda: None)
    # The fallback walks the working copy, so untracked files count again.
    assert generate_state.scan_licenses() == (3, 2)


def test_tracked_python_files_lists_only_tracked(fixture_repo: Path) -> None:
    files = generate_state._tracked_python_files()
    assert files is not None
    names = sorted(path.name for path in files)
    assert names == ["a_busl.py", "b_busl.py", "c_apache.py"]
    assert all(path.is_absolute() for path in files)


def test_tracked_python_files_none_when_git_unavailable(
    fixture_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*args: object, **kwargs: object) -> str:
        raise FileNotFoundError("git not on PATH")

    monkeypatch.setattr(generate_state.subprocess, "check_output", _raise)
    assert generate_state._tracked_python_files() is None


def _module_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> str:
    base = tmp_path / "pkg"
    base.mkdir(exist_ok=True)
    (base / "mod.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(generate_state, "ROOT", tmp_path)
    modules = generate_state.scan_modules(base)
    assert len(modules) == 1
    return modules[0]["status"]


def test_scan_modules_protocol_port_is_interface_not_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        '"""Port."""\n'
        "from typing import Protocol\n\n\n"
        "class SensorPort(Protocol):\n"
        "    def read(self) -> float: ...\n"
    )
    assert _module_status(tmp_path, monkeypatch, source) == "Interface (Protocol)"


def test_scan_modules_small_dataclass_model_is_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        '"""Domain model."""\n'
        "from dataclasses import dataclass\n\n\n"
        "@dataclass\n"
        "class Record:\n"
        "    id: str\n"
        "    content: str\n"
    )
    assert _module_status(tmp_path, monkeypatch, source) == "Complete"


def test_scan_modules_tiny_plain_file_is_still_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _module_status(tmp_path, monkeypatch, "X = 1\n") == "Stub"


def test_scan_modules_not_implemented_is_still_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = '"""WIP."""\n\n\nclass Thing:\n' + (
        "    def f(self):\n        raise NotImplementedError\n" * 4
    )
    assert _module_status(tmp_path, monkeypatch, source) == "Stub"
