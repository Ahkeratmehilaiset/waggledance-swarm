# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.build_tool_similarity_index import gather_files, require_repo_relative_file

from tests.contracts.review_tool_input_boundaries_registry import (
    REVIEW_TOOL_INPUT_BOUNDARY_CASES,
    ReviewToolInputBoundaryCase,
)

ROOT = Path(__file__).resolve().parents[2]


def _modules_importing_build_tool_similarity_index() -> set[str]:
    modules: set[str] = set()
    for path in (ROOT / "tools").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(
                    alias.name == "build_tool_similarity_index"
                    or alias.name.startswith("build_tool_similarity_index.")
                    for alias in node.names
                ):
                    imported = True
                    break
            if isinstance(node, ast.ImportFrom):
                if node.module == "build_tool_similarity_index" or (
                    node.module
                    and node.module.startswith("build_tool_similarity_index.")
                ):
                    imported = True
                    break
        if imported:
            modules.add(f"tools/{path.name}")
    return modules


@pytest.mark.parametrize(
    "case",
    REVIEW_TOOL_INPUT_BOUNDARY_CASES,
    ids=lambda case: case.tool_id,
)
def test_review_tool_input_boundaries_case(
    case: ReviewToolInputBoundaryCase, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    inside = repo / "inside.py"
    outside = tmp_path / "outside.py"
    outside.write_text("def outside(): pass\n", encoding="utf-8")
    inside.write_text("def inside(): pass\n", encoding="utf-8")

    assert gather_files(repo, ["*.py"]) == [inside.resolve()]
    assert gather_files(repo, ["../*.py"]) == []

    link = repo / "linked_outside.py"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    assert gather_files(repo, ["*.py"]) == []

    if case.cli_path is None:
        with pytest.raises(ValueError, match="file_outside_repo_root"):
            require_repo_relative_file(repo, str(outside))
        return

    outside_secret = tmp_path / "outside_secret.py"
    outside_secret.write_text("def outside(): pass\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(case.cli_path),
            "--root",
            str(repo),
            "--file",
            str(outside_secret),
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
    assert str(outside_secret) not in combined
    assert outside_secret.name not in combined


def test_review_tool_input_boundaries_registry_covers_importers() -> None:
    importer_modules = _modules_importing_build_tool_similarity_index()
    registered = {case.tool_id for case in REVIEW_TOOL_INPUT_BOUNDARY_CASES}
    missing = sorted(importer_modules - registered)
    assert not missing, f"Unregistered modules import confinement helper: {missing}"

