"""Offline cases for the overlap lens: a throwaway git repo, a synthetic context and files cache, no network."""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import overlap_lens as lens  # noqa: E402

MISSING_HEAD = "f" * 40


def run(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    done = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.longpaths=true", "-C", str(repo), *args], capture_output=True, env=env)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-300:]
    return done.stdout.decode().strip()


def commit(repo, files, base=None):
    if base is not None:
        run(repo, "checkout", "-q", "--detach", base)
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            path.unlink()
        else:
            path.write_text(text, encoding="utf-8")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "c")
    return run(repo, "rev-parse", "HEAD")


def fixture(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-q")
    base = commit(repo, {"tools/view.py": "v0\n", "tools/base.py": "b0\n", "docs/a.md": "a\n"})
    component = commit(repo, {"tools/view.py": "fix by rco2\n"}, base)
    train_same = commit(repo, {"tools/view.py": "fix by rco2\n", "tools/base.py": "b1\n"}, base)
    train_diverged = commit(repo, {"tools/view.py": "fix by fable\n", "docs/a.md": None}, base)
    lonely = commit(repo, {"docs/b.md": "b\n"}, base)
    heads = {1898: (component, ["claude-rco-2"], ["tools/view.py"]),
             1878: (train_diverged, ["fable-5"], ["tools/view.py", "docs/a.md"]),
             1901: (train_same, ["codex-lead-1"], ["tools/view.py", "tools/base.py"]),
             1700: (lonely, ["fable-5"], ["docs/b.md"]),
             1600: (MISSING_HEAD, ["codex-tools-1"], ["tools/base.py"]),
             1500: (component, ["fable-5"], None)}
    cache = tmp_path / "cache"
    cache.mkdir()
    prs = {}
    for number, (head, authors, files) in heads.items():
        prs[str(number)] = {"state": "OPEN", "head": head, "head_ref_name": f"x/pr-{number}", "path_class": None,
                            "commit_authors": authors}
        if files is not None:
            stamp = "2026-10-10T00:00:00Z" if number == 1700 else "2026-10-13T12:00:00Z"
            (cache / f"{number}-{head}.v2.json").write_text(json.dumps(
                {"headRefOid": head, "files": [{"path": p} for p in files],
                 "commits": [{"committedDate": stamp}, {"committedDate": "not a date"}]}), encoding="utf-8")
    prs["1400"] = {"state": "MERGED", "head": component, "head_ref_name": "x/old", "path_class": None,
                   "commit_authors": ["fable-5"]}
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"schema": "wd.bridge-mind-pr-context.v2", "observed_utc": "2026-10-13T13:00:00Z",
                                   "prs": prs}), encoding="utf-8")
    return repo, cache, context


def pair(view, a, b):
    return next(p for p in view["pairs"] if (p["a"], p["b"]) == (min(a, b), max(a, b)))


def test_relations_follow_blob_identity_at_each_head_and_unknown_is_never_guessed(tmp_path):
    repo, cache, context = fixture(tmp_path)
    out = tmp_path / "out"
    assert lens.main(["--context", str(context), "--files-cache", str(cache), "--git-dir", str(repo),
                      "--out-dir", str(out), "--publish-dir", str(tmp_path / "pub"), "--lane", "fable-5"]) == 0
    view = json.loads((out / "overlap-lens.json").read_text(encoding="utf-8"))
    assert pair(view, 1898, 1901)["relation"] == "same"
    assert pair(view, 1878, 1898) == {"a": 1878, "b": 1898, "relation": "diverged", "same": [],
                                      "diverged": ["tools/view.py"], "unknown": []}
    assert pair(view, 1878, 1901)["relation"] == "diverged"
    assert pair(view, 1600, 1901) == {"a": 1600, "b": 1901, "relation": "unknown", "same": [], "diverged": [],
                                      "unknown": ["tools/base.py"]}
    assert view["heads_not_in_local_store"] == [1600]
    assert view["files_unknown"] == [1500] and view["open_prs"] == 6
    assert all(1400 not in (p["a"], p["b"]) and 1700 not in (p["a"], p["b"]) for p in view["pairs"])
    lines = (tmp_path / "pub" / "lens-overlap.fable-5.txt").read_text(encoding="ascii").splitlines()
    assert lines[1].startswith("OVERLAP your PR1878 with PR1898 (claude-rco-2): diverged on tools/view.py")
    assert any("PR1878 with PR1901 (codex-lead-1): diverged" in line for line in lines)


def test_an_absent_path_reads_none_and_a_head_missing_from_the_store_is_not_present(tmp_path):
    repo, _, _ = fixture(tmp_path)
    head = run(repo, "rev-parse", "HEAD")
    assert lens.blobs_at(repo, head, {"docs/a.md", "docs/none.md"})["docs/none.md"] is None
    assert lens.present_commits(repo, [head, MISSING_HEAD]) == {head}


def test_only_pairs_with_a_recent_commit_on_both_sides_are_listed_and_older_ones_are_counted(tmp_path):
    repo, cache, context = fixture(tmp_path)
    old = json.loads((cache / f"1700-{json.loads(context.read_text())['prs']['1700']['head']}.v2.json").read_text())
    old["files"].append({"path": "tools/view.py"})
    head = json.loads(context.read_text())["prs"]["1700"]["head"]
    (cache / f"1700-{head}.v2.json").write_text(json.dumps(old), encoding="utf-8")
    out = tmp_path / "out"
    assert lens.main(["--context", str(context), "--files-cache", str(cache), "--git-dir", str(repo),
                      "--out-dir", str(out), "--lane", "fable-5"]) == 0
    lines = json.loads((out / "overlap-lens.json").read_text(encoding="utf-8"))["lanes"]["fable-5"]
    assert not any("PR1700" in line for line in lines)
    assert lines[-1].startswith("OVERLAP: 3 older pairs for fable-5 not shown")
    assert lens.main(["--context", str(context), "--files-cache", str(cache), "--git-dir", str(repo),
                      "--out-dir", str(tmp_path / "wide"), "--lane", "fable-5", "--active-hours", "200"]) == 0
    wide = json.loads((tmp_path / "wide" / "overlap-lens.json").read_text(encoding="utf-8"))["lanes"]["fable-5"]
    assert any("your PR1700" in line for line in wide)


def test_a_lane_without_overlaps_says_so_and_a_full_file_list_is_flagged(tmp_path, monkeypatch):
    repo, cache, context = fixture(tmp_path)
    monkeypatch.setattr(lens, "FILES_LIMIT", 2)
    out = tmp_path / "out"
    assert lens.main(["--context", str(context), "--files-cache", str(cache), "--git-dir", str(repo),
                      "--out-dir", str(out), "--lane", "codex-tools-1", "--lane", "claude-rco-1"]) == 0
    view = json.loads((out / "overlap-lens.json").read_text(encoding="utf-8"))
    assert view["files_maybe_truncated"] == [1878, 1901]
    assert view["lanes"]["claude-rco-1"][-1] == ("OVERLAP: no active open PR of claude-rco-1 shares a path with "
                                                 "another active open PR")
    assert "UNKNOWN on tools/base.py" in view["lanes"]["codex-tools-1"][1]
