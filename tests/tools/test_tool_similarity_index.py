import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.build_tool_similarity_index import (
    extract_skeleton,
    gather_files,
    require_local_ollama_url,
    require_repo_relative_file,
)


ROOT = Path(__file__).resolve().parents[2]
FIND_SCRIPT = ROOT / "tools" / "find_similar_tools.py"


def test_extract_skeleton_uses_ast_without_executing_module_code() -> None:
    source = """
raise RuntimeError("module body would execute")
import os
from pathlib import Path

def build(x, y=1):
    return x + y

class Runner:
    pass
"""

    skeleton, n_defs, imports = extract_skeleton(source)

    assert n_defs == 2
    assert imports == ["os", "pathlib.Path"]
    assert "def build(x, y)" in skeleton
    assert "class Runner" in skeleton


def test_require_local_ollama_url_accepts_loopback_only() -> None:
    assert require_local_ollama_url("http://localhost:11434") == (
        "http://localhost:11434"
    )
    assert require_local_ollama_url("http://127.0.0.1:11434") == (
        "http://127.0.0.1:11434"
    )
    assert require_local_ollama_url("http://[::1]:11434") == (
        "http://[::1]:11434"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost:11434",
        "http://example.com:11434",
        "http://192.168.1.10:11434",
        "http://localhost:11434/api/embed",
        "http://user@localhost:11434",
        "http://localhost:11434?token=secret",
        "file:///tmp/ollama.sock",
    ],
)
def test_require_local_ollama_url_rejects_non_local_or_credential_shapes(
    url: str,
) -> None:
    with pytest.raises(ValueError):
        require_local_ollama_url(url)


def test_require_repo_relative_file_rejects_absolute_path_outside_root(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('not repo source')\n", encoding="utf-8")

    with pytest.raises(ValueError, match="file_outside_repo_root"):
        require_repo_relative_file(repo, str(outside))


def test_gather_files_rejects_parent_glob_escape(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    inside = repo / "inside.py"
    inside.write_text("def inside(): pass\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("def outside(): pass\n", encoding="utf-8")

    assert gather_files(repo, ["*.py"]) == [inside.resolve()]
    assert gather_files(repo, ["../*.py"]) == []


def test_gather_files_rejects_symlink_escape(tmp_path: Path) -> None:
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


def test_find_similar_tools_rejects_outside_file_without_path_leak(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside_secret.py"
    outside.write_text("print('not repo source')\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(FIND_SCRIPT),
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
