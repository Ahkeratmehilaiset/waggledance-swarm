from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from tools.build_tool_similarity_index import gather_files


ROOT = Path(__file__).resolve().parents[2]
FIND_SIMILAR_TOOLS = ROOT / "tools" / "find_similar_tools.py"


@dataclass(frozen=True)
class ReviewToolInputBoundaryCase:
    tool_id: str
    validate: Callable[[Path], None]


def _check_build_tool_similarity_index_boundaries(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    inside = repo / "inside.py"
    outside = tmp_path / "outside.py"
    inside.write_text("def inside(): pass\n", encoding="utf-8")
    outside.write_text("def outside(): pass\n", encoding="utf-8")

    assert gather_files(repo, ["*.py"]) == [inside.resolve()]
    assert gather_files(repo, ["../*.py"]) == []

    outside_py = tmp_path / "outside.py"
    outside_py.write_text("def outside(): pass\n", encoding="utf-8")
    link = repo / "linked_outside.py"
    try:
        link.symlink_to(outside_py)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    # The escaping symlink is dropped, but the legitimate in-repo file is
    # retained: confinement must exclude the symlink without over-pruning.
    assert gather_files(repo, ["*.py"]) == [inside.resolve()]


def _check_find_similar_tools_file_leak_boundaries(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside_secret.py"
    outside.write_text("print('not repo source')\n", encoding="utf-8")

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


def _module_imports_confinement_helper(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"build_tool_similarity_index", "tools.build_tool_similarity_index"}:
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module
            if module in {"build_tool_similarity_index", "tools.build_tool_similarity_index"}:
                return True
            if module and module.endswith(".build_tool_similarity_index"):
                return True
    return False


def discover_tools_importing_confinement_helpers() -> tuple[str, ...]:
    tools_root = ROOT / "tools"
    modules: list[str] = []
    for path in sorted(tools_root.glob("*.py")):
        if path.name in {"__pycache__", "__init__.py"}:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        if _module_imports_confinement_helper(tree):
            rel = path.relative_to(ROOT).as_posix()
            if rel not in modules:
                modules.append(rel)
    return tuple(modules)


REVIEW_TOOL_INPUT_BOUNDARY_CASES: tuple[ReviewToolInputBoundaryCase, ...] = (
    ReviewToolInputBoundaryCase(
        tool_id="tools/build_tool_similarity_index.py",
        validate=_check_build_tool_similarity_index_boundaries,
    ),
    ReviewToolInputBoundaryCase(
        tool_id="tools/find_similar_tools.py",
        validate=_check_find_similar_tools_file_leak_boundaries,
    ),
)
