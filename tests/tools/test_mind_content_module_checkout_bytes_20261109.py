"""Pinned content modules must retain their blob bytes in a Windows checkout."""

import hashlib
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
MODULES = (
    "sandbox/mind/step2/content_memory.py",
    "sandbox/mind/step2/shared_content_projection.py",
    "sandbox/mind/step2/shared_doc_projection.py",
)


def test_fresh_checkout_preserves_module_blobs_with_autocrlf(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="Synthetic",
        GIT_AUTHOR_EMAIL="synthetic@example.invalid",
        GIT_COMMITTER_NAME="Synthetic",
        GIT_COMMITTER_EMAIL="synthetic@example.invalid",
    )

    def git(cwd, *args):
        return subprocess.run(
            ["git", "-c", "core.autocrlf=true", *args],
            cwd=cwd,
            env=env,
            check=True,
            capture_output=True,
        ).stdout

    expected = {path: git(ROOT, "show", "HEAD:" + path) for path in MODULES}
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    (source / ".gitattributes").write_bytes((ROOT / ".gitattributes").read_bytes())
    for path, data in expected.items():
        destination = source / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    git(source, "add", "--", ".gitattributes", *MODULES)
    git(source, "commit", "-q", "-m", "synthetic content module fixture")

    checkout = tmp_path / "fresh"
    git(tmp_path, "clone", "-q", "--no-local", str(source), str(checkout))
    for path, data in expected.items():
        actual = (checkout / path).read_bytes()
        assert hashlib.sha256(actual).digest() == hashlib.sha256(data).digest(), path
        assert actual == data, path
