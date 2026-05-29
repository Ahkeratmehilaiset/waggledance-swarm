# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.build_tool_similarity_index import gather_files


ROOT = Path(__file__).resolve().parents[2]
FIND_SIMILAR_TOOLS = ROOT / "tools" / "find_similar_tools.py"


def test_review_index_globs_are_confined_to_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    inside = repo / "inside.py"
    outside = tmp_path / "outside.py"
    inside.write_text("def inside(): pass\n", encoding="utf-8")
    outside.write_text("def outside(): pass\n", encoding="utf-8")

    assert gather_files(repo, ["*.py"]) == [inside.resolve()]
    assert gather_files(repo, ["../*.py"]) == []


def test_review_index_symlinks_are_confined_to_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def outside(): pass\n", encoding="utf-8")
    link = repo / "linked_outside.py"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    assert gather_files(repo, ["*.py"]) == []


def test_review_tool_cli_rejects_outside_file_without_path_leak(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside_secret.py"
    outside.write_text("def outside(): pass\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(FIND_SIMILAR_TOOLS),
            "--root",
            str(repo),
            "--file",
            str(outside),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout) == {"error": "file_outside_repo_root"}
    combined = result.stdout + result.stderr
    assert str(outside) not in combined
    assert outside.name not in combined
