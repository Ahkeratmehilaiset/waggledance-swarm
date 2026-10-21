"""Offline cases for the overlap lens: a throwaway git repo, a synthetic context and files cache, no network."""

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


ABSENT = "OVERLAP: no active open PR of {} shares a path with another active open PR"
UNREAD = ("OVERLAP: UNKNOWN for {}: the file list of {} is UNKNOWN or may be cut off, so an active overlap "
          "may be missing")


def only(context, numbers):
    data = json.loads(context.read_text(encoding="utf-8"))
    data["prs"] = {key: row for key, row in data["prs"].items() if int(key) in numbers}
    context.write_text(json.dumps(data), encoding="utf-8")


def last_commit(cache, context, number, stamp="2026-10-13T12:00:00Z"):
    head = json.loads(context.read_text(encoding="utf-8"))["prs"][str(number)]["head"]
    path = cache / f"{number}-{head}.v2.json"
    view = json.loads(path.read_text(encoding="utf-8"))
    view["commits"] = [{"committedDate": stamp}]
    path.write_text(json.dumps(view), encoding="utf-8")


def lane_of(tmp_path, repo, cache, context, lane, name):
    out = tmp_path / name
    assert lens.main(["--context", str(context), "--files-cache", str(cache), "--git-dir", str(repo),
                      "--out-dir", str(out), "--lane", lane]) == 0
    view = json.loads((out / "overlap-lens.json").read_text(encoding="utf-8"))
    return view, view["lanes"][lane]


def test_no_overlap_is_stated_only_when_no_file_list_that_could_hide_one_is_unknown_or_cut_off(tmp_path, monkeypatch):
    repo, cache, context = fixture(tmp_path)
    only(context, {1700, 1901})  # fable-5's docs/b.md shares no path with codex-lead-1's two files
    monkeypatch.setattr(lens, "FILES_LIMIT", 2)  # so PR1901's two files may be a cut-off list
    # PR1700 has no commit in 48 h, so it can be in no active pair: the absence holds.
    assert lane_of(tmp_path, repo, cache, context, "fable-5", "old")[1][-1] == ABSENT.format("fable-5")
    last_commit(cache, context, 1700)
    view, lines = lane_of(tmp_path, repo, cache, context, "fable-5", "cut")
    assert view["files_maybe_truncated"] == [1901] and lines[-1] == UNREAD.format("fable-5", "PR1901")
    # A cut-off list of a PR with no commit in 48 h can hide no active pair: the absence holds again.
    last_commit(cache, context, 1901, "2026-10-10T00:00:00Z")
    assert lane_of(tmp_path, repo, cache, context, "fable-5", "cut_old")[1][-1] == ABSENT.format("fable-5")
    last_commit(cache, context, 1901)
    monkeypatch.setattr(lens, "FILES_LIMIT", 100)
    assert lane_of(tmp_path, repo, cache, context, "fable-5", "full")[1][-1] == ABSENT.format("fable-5")
    # The lane's own active PR without a cached file list: UNKNOWN too, never an absence.
    (tmp_path / "second").mkdir()
    repo, cache, context = fixture(tmp_path / "second")
    only(context, {1500, 1898})
    view, lines = lane_of(tmp_path, repo, cache, context, "fable-5", "own")
    assert view["files_unknown"] == [1500] and lines[-1] == UNREAD.format("fable-5", "PR1500")


def test_a_pair_is_same_only_when_neither_file_list_may_be_cut_off(tmp_path, monkeypatch):
    repo, cache, context = fixture(tmp_path)
    monkeypatch.setattr(lens, "FILES_LIMIT", 2)
    view, lines = lane_of(tmp_path, repo, cache, context, "claude-rco-2", "out")
    assert pair(view, 1898, 1901) == {"a": 1898, "b": 1901, "relation": "unknown", "same": ["tools/view.py"],
                                      "diverged": [], "unknown": [], "cut_off": [1901]}
    assert pair(view, 1878, 1898)["relation"] == "diverged" and pair(view, 1878, 1898)["cut_off"] == [1878]
    assert view["counts"] == {"diverged": 2, "unknown": 2, "same": 0}
    assert "OVERLAP your PR1898 with PR1901 (codex-lead-1): same file on tools/view.py so far, but the file list of " \
           "PR1901 may be cut off: other shared paths UNKNOWN" in lines
    tools = lane_of(tmp_path, repo, cache, context, "codex-tools-1", "tools")[1]
    assert "OVERLAP your PR1600 with PR1901 (codex-lead-1): UNKNOWN on tools/base.py (head not in the local store); " \
           "the file list of PR1901 may be cut off" in tools


def test_the_header_names_the_active_rule_with_its_hours(tmp_path):
    # Lead 10-16: "active" is the declared, readable commit date rule, not proof of work or of inactivity.
    repo, cache, context = fixture(tmp_path)
    rule = ("; active = a declared commit date at most {} h before the context observation or after it, not proof "
            "of work")
    for extra, hours in (([], lens.ACTIVE_HOURS), (["--active-hours", "200"], 200)):
        out = tmp_path / f"out{hours}"
        assert lens.main(["--context", str(context), "--files-cache", str(cache), "--git-dir", str(repo),
                          "--out-dir", str(out), "--lane", "fable-5", *extra]) == 0
        view = json.loads((out / "overlap-lens.json").read_text(encoding="utf-8"))
        assert view["active_hours"] == hours and view["lanes"]["fable-5"][0].endswith(rule.format(hours))


@pytest.mark.parametrize("stamp, listed", [("2026-10-11T12:59:59Z", False), ("2026-10-11T13:00:00Z", True),
                                           ("2026-10-20T00:00:00Z", True)], ids=["48h_1s_before", "48h_before",
                                                                                "155h_after"])
def test_the_active_rule_is_one_sided_a_date_after_the_observation_is_active(tmp_path, stamp, listed):
    # Lead 10-16: a declared date after the observation meets the one-sided rule; the docs and the header say so.
    repo, cache, context = fixture(tmp_path)
    head = json.loads(context.read_text(encoding="utf-8"))["prs"]["1700"]["head"]
    path = cache / f"1700-{head}.v2.json"
    view = json.loads(path.read_text(encoding="utf-8"))
    view["files"].append({"path": "tools/view.py"})
    view["commits"] = [{"committedDate": stamp}]
    path.write_text(json.dumps(view), encoding="utf-8")
    lines = lane_of(tmp_path, repo, cache, context, "fable-5", "out")[1]
    assert any("your PR1700" in line for line in lines) is listed


UNREADABLE = ("not_json", "not_utf8", "nested_too_deep")
SHAPES = ("a_list", "schema_another", "without_prs", "prs_a_list", "key_not_a_number", "key_ten_digits",
          "row_not_an_object", "state_not_a_string", "state_unknown", "open_head_a_number", "open_head_not_40_hex",
          "authors_one_string", "authors_empty_string", "authors_an_object", "authors_with_a_number",
          "authors_not_lane_shaped", "observed_utc_absent",
          "observed_utc_a_number", "observed_utc_naive", "observed_utc_out_of_range")


def refused_context(context, case):
    """The fixture context with one change, as bytes: the lens must refuse each before any write."""
    data = json.loads(context.read_text(encoding="utf-8"))
    rows = data["prs"]
    if case == "not_json":
        return b"{"
    if case == "not_utf8":
        return b"\xff" + context.read_bytes()
    if case == "nested_too_deep":
        return b"[" * 100000 + b"]" * 100000
    if case == "a_list":
        return b"[]"
    if case == "schema_another":
        data["schema"] = "wd.bridge-mind-pr-context.v3"
    elif case == "without_prs":
        del data["prs"]
    elif case == "prs_a_list":
        data["prs"] = []
    elif case == "key_not_a_number":
        rows["x"] = rows.pop("1898")
    elif case == "key_ten_digits":
        rows["1" * 10] = rows.pop("1898")
    elif case == "row_not_an_object":
        rows["1898"] = "OPEN"
    elif case == "state_not_a_string":
        rows["1400"]["state"] = 5
    elif case == "state_unknown":
        rows["1400"]["state"] = "DRAFT"
    elif case == "open_head_a_number":
        rows["1898"]["head"] = 5
    elif case == "open_head_not_40_hex":  # else the OPEN PR was left out silently
        rows["1898"]["head"] = rows["1898"]["head"][:39]
    elif case == "authors_one_string":  # else read as its characters
        rows["1898"]["commit_authors"] = "claude-rco-2"
    elif case == "authors_empty_string":  # else read as an empty list: known, no lane-shaped author
        rows["1898"]["commit_authors"] = ""
    elif case == "authors_an_object":  # else read as its keys
        rows["1898"]["commit_authors"] = {"claude-rco-2": True}
    elif case == "authors_with_a_number":
        rows["1898"]["commit_authors"] = ["claude-rco-2", 5]
    elif case == "authors_not_lane_shaped":
        rows["1898"]["commit_authors"] = ["claude-rco-2", "Claude RCO 2"]
    elif case == "observed_utc_absent":  # else no PR is active and every absence line is claimed
        del data["observed_utc"]
    elif case == "observed_utc_a_number":
        data["observed_utc"] = 20261013
    elif case == "observed_utc_naive":
        data["observed_utc"] = "2026-10-13T13:00:00"
    elif case == "observed_utc_out_of_range":
        data["observed_utc"] = "0001-01-01T00:00:00+01:00"
    return json.dumps(data).encode("utf-8")


@pytest.mark.parametrize("case", UNREADABLE + SHAPES)
def test_a_context_the_lens_cannot_read_whole_is_refused_before_any_write(tmp_path, case):
    repo, cache, context = fixture(tmp_path)
    context.write_bytes(refused_context(context, case))
    out, pub = tmp_path / "out", tmp_path / "pub"
    with pytest.raises(SystemExit) as refused:
        lens.main(["--context", str(context), "--files-cache", str(cache), "--git-dir", str(repo),
                   "--out-dir", str(out), "--publish-dir", str(pub), "--lane", "fable-5"])
    reason = "context_unreadable" if case in UNREADABLE else "context_shape_unexpected"
    assert str(refused.value) == f"refused: {reason}" and not out.exists() and not pub.exists()


def test_a_context_path_that_cannot_be_read_is_refused_before_any_write(tmp_path):
    repo, cache, context = fixture(tmp_path)
    context.unlink()
    context.mkdir()
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as refused:
        lens.main(["--context", str(context), "--files-cache", str(cache), "--git-dir", str(repo),
                   "--out-dir", str(out), "--lane", "fable-5"])
    assert str(refused.value) == "refused: context_unreadable" and not out.exists()


def test_fields_the_lens_does_not_read_are_not_checked(tmp_path):
    # The head and commit authors of a row that is not OPEN are never read, so they refuse nothing.
    repo, cache, context = fixture(tmp_path)
    before = lane_of(tmp_path, repo, cache, context, "fable-5", "before")[1]
    data = json.loads(context.read_text(encoding="utf-8"))
    data["prs"]["1400"].update(head=None, commit_authors="fable-5")
    context.write_text(json.dumps(data), encoding="utf-8")
    assert lane_of(tmp_path, repo, cache, context, "fable-5", "after")[1] == before


@pytest.mark.parametrize("case", ["negative", "beyond_a_timedelta"])
def test_active_hours_out_of_range_are_refused_before_any_write(tmp_path, case):
    repo, cache, context = fixture(tmp_path)
    hours = "-1" if case == "negative" else str(lens.MAX_ACTIVE_HOURS + 1)
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as refused:
        lens.main(["--context", str(context), "--files-cache", str(cache), "--git-dir", str(repo),
                   "--out-dir", str(out), "--lane", "fable-5", "--active-hours", hours])
    assert str(refused.value) == "refused: active hours invalid" and not out.exists()
    assert lens.main(["--context", str(context), "--files-cache", str(cache), "--git-dir", str(repo),
                      "--out-dir", str(out), "--lane", "fable-5", "--active-hours", str(lens.MAX_ACTIVE_HOURS)]) == 0
    # The bound is the widest window a timedelta holds, in whole hours: one hour more does not fit.
    assert dt.timedelta(hours=lens.MAX_ACTIVE_HOURS) <= dt.timedelta.max
    with pytest.raises(OverflowError):
        dt.timedelta(hours=lens.MAX_ACTIVE_HOURS + 1)


CACHE_SHAPES = ("a_directory", "not_json", "nested_too_deep", "a_list", "another_head", "files_not_a_list",
                "files_an_empty_object", "file_item_not_an_object", "file_path_a_number", "file_path_empty",
                "file_path_with_a_nul", "commits_absent", "commits_a_string", "commit_item_not_an_object")


def broken_cache(view, case):
    """A cached files view with one fault, as bytes."""
    if case == "not_json":
        return b"{"
    if case == "nested_too_deep":
        return b"[" * 100000 + b"]" * 100000
    if case == "a_list":
        return b"[]"
    if case == "another_head":
        view["headRefOid"] = "0" * 40
    elif case == "files_not_a_list":
        view["files"] = {"path": "tools/view.py"}
    elif case == "files_an_empty_object":  # else read as a known list without paths
        view["files"] = {}
    elif case == "file_item_not_an_object":
        view["files"] = ["tools/view.py"]
    elif case == "file_path_a_number":
        view["files"] = [{"path": 5}]
    elif case == "file_path_empty":  # else the good path is kept and the bad one skipped
        view["files"] = [{"path": "tools/view.py"}, {"path": ""}]
    elif case == "file_path_with_a_nul":  # no git path holds one, and no git call can take it
        view["files"] = [{"path": "tools/view.py"}, {"path": "tools/view.py\0x"}]
    elif case == "commits_absent":
        del view["commits"]
    elif case == "commits_a_string":  # else read as no commit dates
        view["commits"] = "2026-10-13T12:00:00Z"
    elif case == "commit_item_not_an_object":
        view["commits"] = ["2026-10-13T12:00:00Z"]
    return json.dumps(view).encode("utf-8")


@pytest.mark.parametrize("case", CACHE_SHAPES)
def test_a_cached_files_view_not_of_its_shape_gives_unknown_files_never_a_guess(tmp_path, case):
    repo, cache, context = fixture(tmp_path)
    head = json.loads(context.read_text(encoding="utf-8"))["prs"]["1898"]["head"]
    path = cache / f"1898-{head}.v2.json"
    if case == "a_directory":  # a read that fails
        path.unlink()
        path.mkdir()
    else:
        path.write_bytes(broken_cache(json.loads(path.read_text(encoding="utf-8")), case))
    view, lines = lane_of(tmp_path, repo, cache, context, "claude-rco-2", "out")
    assert view["files_unknown"] == [1500, 1898] and all(1898 not in (p["a"], p["b"]) for p in view["pairs"])
    assert lines[-1] == UNREAD.format("claude-rco-2", "PR1500, PR1898")


HIDDEN = ("OVERLAP: UNKNOWN for {}: the commit authors of {} are UNKNOWN and each is in an active pair, so an "
          "active overlap may be missing")


def set_authors(context, number, authors):
    data = json.loads(context.read_text(encoding="utf-8"))
    if authors == "absent":
        del data["prs"][str(number)]["commit_authors"]
    else:
        data["prs"][str(number)]["commit_authors"] = authors
    context.write_text(json.dumps(data), encoding="utf-8")


def test_an_active_pair_of_a_pr_whose_commit_authors_are_unknown_keeps_every_lane_absence_unproven(tmp_path):
    # null authors (the producer's commit list may be incomplete) or none at all (an older context): the PR may be
    # any lane's; an empty list is known (no lane-shaped author) and the absence holds
    repo, cache, context = fixture(tmp_path)
    only(context, {1898, 1901})
    set_authors(context, 1901, None)
    view, lines = lane_of(tmp_path, repo, cache, context, "claude-rco-1", "null")
    assert lines[-1] == HIDDEN.format("claude-rco-1", "PR1901")
    assert view["authors_unknown"] == [1901] and "; commit authors UNKNOWN for 1; " in lines[0]
    rco2 = lane_of(tmp_path, repo, cache, context, "claude-rco-2", "rco2")[1]
    assert "OVERLAP your PR1898 with PR1901 (UNKNOWN): same file on tools/view.py" in rco2
    set_authors(context, 1901, [])
    view, lines = lane_of(tmp_path, repo, cache, context, "claude-rco-1", "empty")
    assert lines[-1] == ABSENT.format("claude-rco-1") and view["authors_unknown"] == []
    rco2 = lane_of(tmp_path, repo, cache, context, "claude-rco-2", "rco2_empty")[1]
    assert "OVERLAP your PR1898 with PR1901 (no lane-shaped author): same file on tools/view.py" in rco2
    set_authors(context, 1901, "absent")
    assert lane_of(tmp_path, repo, cache, context, "claude-rco-1", "absent")[1][-1] == HIDDEN.format(
        "claude-rco-1", "PR1901")
    # Only an active pair can hide an active overlap: with no commit of PR1901 in 48 h the absence holds.
    last_commit(cache, context, 1901, "2026-10-10T00:00:00Z")
    assert lane_of(tmp_path, repo, cache, context, "claude-rco-1", "older")[1][-1] == ABSENT.format("claude-rco-1")


def test_an_active_pr_with_unknown_authors_and_an_unread_list_elsewhere_keeps_the_absence_unproven(tmp_path):
    repo, cache, context = fixture(tmp_path)
    only(context, {1500, 1901})  # PR1500 has no cached file list, so it may share a path with PR1901
    set_authors(context, 1901, None)
    assert lane_of(tmp_path, repo, cache, context, "claude-rco-1", "null")[1][-1] == UNREAD.format(
        "claude-rco-1", "PR1500")
    set_authors(context, 1901, ["codex-lead-1"])
    assert lane_of(tmp_path, repo, cache, context, "claude-rco-1", "known")[1][-1] == ABSENT.format("claude-rco-1")
    # Both reasons at once: both lines, the file list first.
    (tmp_path / "second").mkdir()
    repo, cache, context = fixture(tmp_path / "second")
    only(context, {1500, 1898, 1901})
    set_authors(context, 1901, None)
    lines = lane_of(tmp_path, repo, cache, context, "claude-rco-1", "both")[1]
    assert lines[-2:] == [UNREAD.format("claude-rco-1", "PR1500"), HIDDEN.format("claude-rco-1", "PR1901")]


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired(["git"], 60), FileNotFoundError("git")],
                         ids=["timeout", "not_started"])
def test_a_git_read_that_fails_gives_unknown_heads_not_a_crash(tmp_path, monkeypatch, error):
    repo, cache, context = fixture(tmp_path)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(lens.subprocess, "run", fail)
    view, _ = lane_of(tmp_path, repo, cache, context, "fable-5", "out")
    assert view["heads_not_in_local_store"] == [1600, 1878, 1898, 1901]
    assert view["pairs"] and all(p["relation"] == "unknown" for p in view["pairs"])


def test_a_commit_date_out_of_range_is_no_date_not_a_crash(tmp_path):
    repo, cache, context = fixture(tmp_path)
    head = json.loads(context.read_text(encoding="utf-8"))["prs"]["1898"]["head"]
    path = cache / f"1898-{head}.v2.json"
    view = json.loads(path.read_text(encoding="utf-8"))
    view["commits"] = [{"committedDate": "0001-01-01T00:00:00+01:00"}, {"committedDate": "2026-10-13T12:00:00Z"}]
    path.write_text(json.dumps(view), encoding="utf-8")
    lens_view, lines = lane_of(tmp_path, repo, cache, context, "claude-rco-2", "out")
    assert pair(lens_view, 1898, 1901)["relation"] == "same"
    assert any(line.startswith("OVERLAP your PR1898 with PR1901 (codex-lead-1): same file") for line in lines)
