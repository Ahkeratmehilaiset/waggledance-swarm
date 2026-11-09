"""Pinned Mind policy bytes survive an autocrlf-enabled fresh checkout."""
import hashlib
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
POLICIES = (
    "configs/mind/content_message_policy_v1.json",
    "configs/mind/pilot_non_review_policy.json",
    "configs/mind/content_message_policy_v2.json",
    "configs/mind/content_non_review_policy_v2.json",
)


def test_fresh_checkout_preserves_policy_blobs_with_autocrlf(tmp_path):
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="Synthetic", GIT_AUTHOR_EMAIL="synthetic@example.invalid",
               GIT_COMMITTER_NAME="Synthetic", GIT_COMMITTER_EMAIL="synthetic@example.invalid")
    for key in tuple(env):
        if key.startswith("GIT_") and key not in {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
                "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}:
            del env[key]

    def git(cwd, *args):
        return subprocess.run(["git", "-c", "core.autocrlf=true", *args], cwd=cwd, env=env,
                              check=True, capture_output=True).stdout

    # Git objects are read only; all writes and the fresh clone are synthetic.
    expected = {p: git(ROOT, "show", "HEAD:" + p) for p in POLICIES}
    fixture = tmp_path / "source"
    fixture.mkdir()
    git(fixture, "init", "-q")
    (fixture / ".gitattributes").write_bytes((ROOT / ".gitattributes").read_bytes())
    for path, raw in expected.items():
        target = fixture / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    git(fixture, "add", "--", ".gitattributes", *POLICIES)
    git(fixture, "commit", "-q", "-m", "synthetic policy fixture")
    checkout = tmp_path / "fresh"
    git(tmp_path, "clone", "-q", "--no-local", str(fixture), str(checkout))
    for path, raw in expected.items():
        actual = (checkout / path).read_bytes()
        assert hashlib.sha256(actual).hexdigest() == hashlib.sha256(raw).hexdigest(), path
        assert actual == raw, path
