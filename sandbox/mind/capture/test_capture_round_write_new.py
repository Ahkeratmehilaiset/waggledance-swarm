"""R5 write-new cases for capture/capture_round.py (fable-5 R5 child, 2026-10-21), written before the fix.

R5 (RCO1's class, the integrity ratchet's "write"): every output of a capture is written whole or not at all, and a
new output never replaces a file. Every case is synthetic: explicit tmp_path roots only (no default path is used), the
compact reader, gh and the charter state are monkeypatched, and no process is started. A full disk is simulated by
wrapping pathlib.Path.open so that the nth write-mode open in one directory returns a file whose write puts the first
half of its data on disk, flushes it and then raises OSError(ENOSPC): the unchanged module opens the final name there,
the fixed one its staging name. Each defect case asserts first that no cut file is left under the final name (the
defect), then the fixed refusal. The success cases pin the old bytes: each output equals what the replaced expression
wrote (capture-record.json through Path.write_text, so os.linesep line ends), and no staging file is left.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
     sandbox/mind/capture/test_capture_round_write_new.py
"""

import errno
import hashlib
import json
import pathlib
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import capture_round as cr  # noqa: E402

REAL_OPEN = pathlib.Path.open
PR, PR_HEAD, BRANCH = 4242, "a1" * 20, "fable-5/synthetic-r5"
CACHE_NAME = f"{PR}-{PR_HEAD}.v2.json"
EVENTS = [{"ts_utc": f"2026-10-21T10:00:0{i}Z", "agent": "fable-5", "type": "message", "task_id": "synthetic-r5",
           "message": f"synthetic row {i}" + (": \u00e4 \u00f6" if i == 2 else "")} for i in range(3)]
LIST_BYTES = json.dumps([{"number": PR, "state": "OPEN", "headRefOid": PR_HEAD, "headRefName": BRANCH},
                         {"number": PR - 1, "state": "MERGED", "headRefOid": "b2" * 20,
                          "headRefName": "codex-lead-1/synthetic-merged"}]).encode("ascii")
VIEW_BYTES = json.dumps({"files": [{"path": "sandbox/mind/synthetic.py"}], "headRefOid": PR_HEAD,
                         "commits": [{"authors": [{"name": "fable-5"}]}]}).encode("ascii")
REQUIRED_BYTES = b'["unit-tests", "lint"]'
OPEN_BYTES = json.dumps([{"number": PR, "headRefOid": PR_HEAD, "mergeStateStatus": "BLOCKED",
                          "statusCheckRollup": [{"name": "unit-tests", "conclusion": "SUCCESS"},
                                                {"name": "lint", "conclusion": "FAILURE"}]}]).encode("ascii")
MENTIONS_BYTES = json.dumps([{"number": PR, "title": "synthetic R5 case", "body": "follows #4241"}]).encode("ascii")
REUSED_CONTEXT = (json.dumps({"schema": "wd.bridge-mind-pr-context.v2", "authority": "none", "prs": {},
                              "observed_utc": "2026-10-21T09:59:00Z"}, indent=2, sort_keys=True) + "\n").encode("ascii")
ROUND_FILES = ("capture.json", "detail-manifest.txt", "gh-pr-list.json", "context.json", "gh-open-status.json",
               "observed-status.json")


def fake_reader(events=EVENTS):
    rows = {cr.event_id(event): event for event in events}

    def reader(bin_dir, *options):
        if "-EventId" in options:
            ref = options[options.index("-EventId") + 1]
            report = {"schema": "wd.bridge.event-detail.v1", "ref": ref, "event": rows[ref]}
        else:
            report = {"schema": "wd.bridge.compact-view.v1", "events": [{"ref": ref} for ref in rows]}
        return json.dumps(report).encode("utf-8")
    return reader


def fake_gh(view=VIEW_BYTES, before_view=None, calls=None):
    def gh(*options):
        if calls is not None:
            calls.append(options[:2])
        if options[0] == "api" and options[1].startswith("repos/"):
            return REQUIRED_BYTES
        if options[:2] == ("pr", "view"):
            if before_view is not None:
                before_view()
            return view
        if options[:2] == ("pr", "list"):
            return {"number,state,headRefOid,headRefName": LIST_BYTES,
                    "number,headRefOid,mergeStateStatus,statusCheckRollup": OPEN_BYTES,
                    "number,title,body": MENTIONS_BYTES}[options[options.index("--json") + 1]]
        raise AssertionError("an unexpected gh call in a synthetic case")
    return gh


class FullDisk:
    """A writable file whose write puts the first half of its data on disk, flushes it and fails as a full disk does;
    ``silent``: it reports the whole length as written instead (a write that lies, the read-back case)."""

    def __init__(self, real, silent=False):
        self._real, self._silent = real, silent

    def write(self, data):
        self._real.write(data[: len(data) // 2])
        self._real.flush()
        if self._silent:
            return len(data)
        raise OSError(errno.ENOSPC, "synthetic full disk")

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self._real.close()
        return False


def fail_nth_write_open(monkeypatch, directory, nth, silent=False):
    """The nth write-mode Path.open in ``directory`` gets a FullDisk; returns the names opened for writing there."""
    opened = []

    def open_(self, mode="r", *args, **kwargs):
        handle = REAL_OPEN(self, mode, *args, **kwargs)
        if Path(self).parent == directory and set(mode) & set("wxa+"):
            opened.append(Path(self).name)
            if len(opened) == nth:
                return FullDisk(handle, silent)
        return handle

    monkeypatch.setattr(pathlib.Path, "open", open_)
    return opened


def outcome(call):
    try:
        return "returned", call()
    except (OSError, cr.CaptureRefused) as exc:
        return "raised", exc


def refused(kind, value, reason):
    return kind == "raised" and type(value) is cr.CaptureRefused and str(value) == reason


def names(directory):
    return sorted(path.name for path in directory.iterdir())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def new_dirs(tmp_path, *names_):
    made = [tmp_path / name for name in names_]
    for path in made:
        path.mkdir()
    return made


def bridge_args(tmp_path, out):
    return SimpleNamespace(bin=tmp_path / "no-bin", out=out, seed_from=None, window=len(EVENTS), workers=1,
                           detail_tail=10)


def offline(monkeypatch, calls=None, **gh_options):
    monkeypatch.setattr(cr, "reader", fake_reader())
    monkeypatch.setattr(cr, "gh", fake_gh(calls=calls, **gh_options))
    monkeypatch.setattr(cr, "charter_state", lambda: (None, False))


def run_main(monkeypatch, tmp_path, out, cache, *extra):
    monkeypatch.setattr(sys, "argv", ["capture_round.py", "--bin", str(tmp_path / "no-bin"), "--out", str(out),
                                      "--files-cache", str(cache), "--window", str(len(EVENTS)), "--workers", "1",
                                      *extra])
    return cr.main()


def reuse_source(tmp_path, with_status=False):
    source = tmp_path / "earlier-cycle"
    source.mkdir()
    files = {"gh-pr-list.json": LIST_BYTES, "context.json": REUSED_CONTEXT}
    if with_status:
        files.update({"gh-open-status.json": OPEN_BYTES, "observed-status.json": b'{"synthetic": true}\n'})
    for name, data in files.items():
        (source / name).write_bytes(data)
    return source, files


# ---- success path: the old bytes, whole, and nothing else in the directory (pass before and after the fix) ----

def test_bridge_capture_writes_the_old_bytes_and_nothing_else(tmp_path, monkeypatch):
    out, ref = new_dirs(tmp_path, "cycle", "reference")
    offline(monkeypatch)
    result = cr.bridge_capture(bridge_args(tmp_path, out))
    assert (result["events"], result["detail_calls"], result["seed_rows"]) == (3, 3, 0)
    assert names(out) == ["capture.json", "detail-manifest.txt"]
    written = (out / "capture.json").read_bytes()
    capture = json.loads(written)
    (ref / "capture.json").write_text(json.dumps(capture, ensure_ascii=False), encoding="utf-8")  # the old expression
    assert written == (ref / "capture.json").read_bytes() and "\u00e4".encode("utf-8") in written
    manifest = "\n".join(f"{cr.event_id(e)} {cr.row_digest(e)} detail" for e in EVENTS).encode("ascii")
    assert (out / "detail-manifest.txt").read_bytes() == manifest
    assert capture["events"] == EVENTS and capture["reader_stdout_sha256"] == hashlib.sha256(manifest).hexdigest()


def test_github_context_writes_the_old_bytes_and_a_cached_view_is_read_not_fetched(tmp_path, monkeypatch):
    out, cache, out2 = new_dirs(tmp_path, "cycle", "files-cache", "cycle2")
    offline(monkeypatch)
    result = cr.github_context(SimpleNamespace(out=out, files_cache=cache))
    assert (result["prs"], result["views_fetched"], result["charter_loaded"]) == (2, 1, False)
    assert names(out) == ["context.json", "gh-pr-list.json"] and names(cache) == [CACHE_NAME]
    assert (out / "gh-pr-list.json").read_bytes() == LIST_BYTES and (cache / CACHE_NAME).read_bytes() == VIEW_BYTES
    written = (out / "context.json").read_bytes()
    context = json.loads(written)
    assert written == (json.dumps(context, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")
    assert context["prs"][str(PR)]["commit_authors"] == ["fable-5"] and context["capture_sha256"] == \
        hashlib.sha256(LIST_BYTES).hexdigest()
    again = cr.github_context(SimpleNamespace(out=out2, files_cache=cache))
    assert again["views_fetched"] == 0 and names(cache) == [CACHE_NAME]
    assert (cache / CACHE_NAME).read_bytes() == VIEW_BYTES


def test_github_status_writes_the_old_bytes_and_nothing_else(tmp_path, monkeypatch):
    (out,) = new_dirs(tmp_path, "cycle")
    offline(monkeypatch)
    assert cr.github_status(SimpleNamespace(out=out, workers=1))["open_prs"] == 1
    assert names(out) == ["gh-open-status.json", "observed-status.json"]
    assert (out / "gh-open-status.json").read_bytes() == OPEN_BYTES
    written = (out / "observed-status.json").read_bytes()
    status = json.loads(written)
    assert written == (json.dumps(status, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")
    row = status["prs"][str(PR)]
    assert row["all_required_success"] is False and row["mentions"] == [PR - 1] and status["mentions_captured"] is True


def test_main_with_status_writes_the_old_bytes_and_a_record_of_every_file(tmp_path, monkeypatch):
    out, cache, (ref,) = tmp_path / "cycle", tmp_path / "files-cache", new_dirs(tmp_path, "reference")
    offline(monkeypatch)
    assert run_main(monkeypatch, tmp_path, out, cache, "--with-status") == 0
    assert names(out) == sorted([*ROUND_FILES, "capture-record.json"]) and names(cache) == [CACHE_NAME]
    written = (out / "capture-record.json").read_bytes()
    record = json.loads(written)
    (ref / "capture-record.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="ascii")
    assert written == (ref / "capture-record.json").read_bytes()  # the old write_text bytes, os.linesep line ends
    assert record["files"] == {name: digest(out / name) for name in ROUND_FILES}
    assert "refused" not in record["github"]["status"]


def test_main_with_a_reused_context_copies_the_old_bytes(tmp_path, monkeypatch):
    out, cache, calls = tmp_path / "cycle", tmp_path / "files-cache", []
    source, copied = reuse_source(tmp_path, with_status=True)
    offline(monkeypatch, calls=calls)
    assert run_main(monkeypatch, tmp_path, out, cache, "--reuse-context", str(source)) == 0
    assert calls == [] and names(out) == sorted(["capture.json", "detail-manifest.txt", "capture-record.json", *copied])
    assert all((out / name).read_bytes() == data for name, data in copied.items())
    record = json.loads((out / "capture-record.json").read_bytes())
    assert record["github"]["reused_from"] == "earlier-cycle" and record["github"]["observed_utc"] == \
        "2026-10-21T09:59:00Z"
    assert record["files"] == {name: digest(out / name) for name in ROUND_FILES}


# ---- R5 defects: a write cut by a full disk leaves no file under its final name (fail on the unchanged module) ----

def test_capture_json_cut_by_a_full_disk_leaves_no_file_under_its_name(tmp_path, monkeypatch):
    (out,) = new_dirs(tmp_path, "cycle")
    offline(monkeypatch)
    opened = fail_nth_write_open(monkeypatch, out, 1)
    kind, value = outcome(lambda: cr.bridge_capture(bridge_args(tmp_path, out)))
    assert "capture.json" in opened[0]
    assert names(out) == [], "a cut capture.json is left under its real name (line 108)"
    assert refused(kind, value, "output_unwritten")


def test_detail_manifest_cut_by_a_full_disk_leaves_no_file_under_its_name(tmp_path, monkeypatch):
    (out,) = new_dirs(tmp_path, "cycle")
    offline(monkeypatch)
    opened = fail_nth_write_open(monkeypatch, out, 2)
    kind, value = outcome(lambda: cr.bridge_capture(bridge_args(tmp_path, out)))
    assert "detail-manifest.txt" in opened[1]
    assert names(out) == ["capture.json"], "a cut detail-manifest.txt is left under its real name (line 109)"
    assert json.loads((out / "capture.json").read_bytes())["events"] == EVENTS
    assert refused(kind, value, "output_unwritten")


def test_gh_pr_list_cut_by_a_full_disk_leaves_no_file_under_its_name(tmp_path, monkeypatch):
    out, cache = new_dirs(tmp_path, "cycle", "files-cache")
    offline(monkeypatch)
    opened = fail_nth_write_open(monkeypatch, out, 1)
    kind, value = outcome(lambda: cr.github_context(SimpleNamespace(out=out, files_cache=cache)))
    assert "gh-pr-list.json" in opened[0]
    assert names(out) == [], "a cut gh-pr-list.json is left under its real name (line 157)"
    assert names(cache) == [] and refused(kind, value, "output_unwritten")


def test_a_cached_view_cut_by_a_full_disk_is_not_left_for_later_rounds(tmp_path, monkeypatch):
    out, cache, out2 = new_dirs(tmp_path, "cycle", "files-cache", "cycle2")
    offline(monkeypatch)
    opened = fail_nth_write_open(monkeypatch, cache, 1)
    kind, value = outcome(lambda: cr.github_context(SimpleNamespace(out=out, files_cache=cache)))
    assert CACHE_NAME in opened[0]
    assert names(cache) == [], "a cut PR view is left in the shared files cache (line 167)"
    assert refused(kind, value, "output_unwritten") and names(out) == ["gh-pr-list.json"]
    monkeypatch.setattr(pathlib.Path, "open", REAL_OPEN)  # the disk has room again: the next round fetches anew
    again = cr.github_context(SimpleNamespace(out=out2, files_cache=cache))
    assert again["views_fetched"] == 1 and (cache / CACHE_NAME).read_bytes() == VIEW_BYTES
    assert json.loads((out2 / "context.json").read_bytes())["prs"][str(PR)]["commit_authors"] == ["fable-5"]


def test_a_cached_view_another_capture_published_first_is_kept_and_read(tmp_path, monkeypatch):
    out, cache = new_dirs(tmp_path, "cycle", "files-cache")
    theirs = json.dumps({"files": [{"path": "sandbox/mind/synthetic.py"}], "headRefOid": PR_HEAD,
                         "commits": [{"authors": [{"name": "codex-lead-1"}]}]}).encode("ascii")

    def another_capture_publishes():  # the same PR head, after this capture's exists() check and before its write
        (cache / CACHE_NAME).write_bytes(theirs)

    offline(monkeypatch, before_view=another_capture_publishes)
    result = cr.github_context(SimpleNamespace(out=out, files_cache=cache))
    assert (cache / CACHE_NAME).read_bytes() == theirs, "a view another capture published was replaced (line 167)"
    assert names(cache) == [CACHE_NAME] and result["views_fetched"] == 1
    assert json.loads((out / "context.json").read_bytes())["prs"][str(PR)]["commit_authors"] == ["codex-lead-1"]


def test_context_json_cut_by_a_full_disk_leaves_no_file_under_its_name(tmp_path, monkeypatch):
    out, cache = new_dirs(tmp_path, "cycle", "files-cache")
    offline(monkeypatch)
    opened = fail_nth_write_open(monkeypatch, out, 2)
    kind, value = outcome(lambda: cr.github_context(SimpleNamespace(out=out, files_cache=cache)))
    assert "context.json" in opened[1]
    assert names(out) == ["gh-pr-list.json"], "a cut context.json is left under its real name (line 180)"
    assert (out / "gh-pr-list.json").read_bytes() == LIST_BYTES and refused(kind, value, "output_unwritten")


def test_gh_open_status_cut_by_a_full_disk_leaves_no_file_under_its_name(tmp_path, monkeypatch):
    (out,) = new_dirs(tmp_path, "cycle")
    offline(monkeypatch)
    opened = fail_nth_write_open(monkeypatch, out, 1)
    kind, value = outcome(lambda: cr.github_status(SimpleNamespace(out=out, workers=1)))
    assert "gh-open-status.json" in opened[0]
    assert names(out) == [], "a cut gh-open-status.json is left under its real name (line 286)"
    assert refused(kind, value, "output_unwritten")


def test_observed_status_cut_by_a_full_disk_leaves_no_file_under_its_name(tmp_path, monkeypatch):
    (out,) = new_dirs(tmp_path, "cycle")
    offline(monkeypatch)
    opened = fail_nth_write_open(monkeypatch, out, 2)
    kind, value = outcome(lambda: cr.github_status(SimpleNamespace(out=out, workers=1)))
    assert "observed-status.json" in opened[1]
    assert names(out) == ["gh-open-status.json"], "a cut observed-status.json is left under its real name (line 287)"
    assert (out / "gh-open-status.json").read_bytes() == OPEN_BYTES and refused(kind, value, "output_unwritten")


def test_a_status_write_failure_leaves_the_status_unknown_and_every_listed_file_whole(tmp_path, monkeypatch):
    """main() documents that a status failure leaves the status UNKNOWN (no observed-status.json in this capture)
    and never refuses the capture itself; the unchanged module ends the capture on the OSError, the cut file left."""
    out, cache = tmp_path / "cycle", tmp_path / "files-cache"
    offline(monkeypatch)
    opened = fail_nth_write_open(monkeypatch, out, 6)
    kind, value = outcome(lambda: run_main(monkeypatch, tmp_path, out, cache, "--with-status"))
    assert "observed-status.json" in opened[5]
    assert not (out / "observed-status.json").exists(), "a cut observed-status.json is left (line 287, main)"
    assert (kind, value) == ("returned", 0)
    record = json.loads((out / "capture-record.json").read_bytes())
    assert (record["github"]["status"]["refused"], record["github"]["status"]["reason"]) == \
        ("status_capture_failed", "output_unwritten")
    listed = [name for name in names(out) if name != "capture-record.json"]
    assert "observed-status.json" not in listed and record["files"] == {name: digest(out / name) for name in listed}


def test_a_reused_context_copy_cut_by_a_full_disk_leaves_no_file_under_its_name(tmp_path, monkeypatch):
    out, cache, calls = tmp_path / "cycle", tmp_path / "files-cache", []
    source, _ = reuse_source(tmp_path)
    offline(monkeypatch, calls=calls)
    opened = fail_nth_write_open(monkeypatch, out, 3)
    kind, value = outcome(lambda: run_main(monkeypatch, tmp_path, out, cache, "--reuse-context", str(source)))
    assert "gh-pr-list.json" in opened[2]
    assert names(out) == ["capture.json", "detail-manifest.txt"], "a cut copy is left under its real name (line 317)"
    assert calls == [] and refused(kind, value, "output_unwritten")


def test_capture_record_cut_by_a_full_disk_leaves_no_file_under_its_name(tmp_path, monkeypatch):
    out, cache = tmp_path / "cycle", tmp_path / "files-cache"
    source, copied = reuse_source(tmp_path)
    offline(monkeypatch)
    opened = fail_nth_write_open(monkeypatch, out, 5)
    kind, value = outcome(lambda: run_main(monkeypatch, tmp_path, out, cache, "--reuse-context", str(source)))
    assert "capture-record.json" in opened[4]
    assert names(out) == sorted(["capture.json", "detail-manifest.txt", *copied]), \
        "a cut capture-record.json is left under its real name (line 338)"
    assert refused(kind, value, "output_unwritten")


# ---- contract pins (fail on the unchanged module; not reproductions of a fault reachable through main()) ----

def test_an_existing_output_is_refused_and_kept_on_a_direct_call(tmp_path, monkeypatch):
    """main() makes --out exclusively, so this is reachable only by a direct call: write-new never replaces."""
    (out,) = new_dirs(tmp_path, "cycle")
    (out / "capture.json").write_bytes(b"an earlier capture, kept")
    offline(monkeypatch)
    kind, value = outcome(lambda: cr.bridge_capture(bridge_args(tmp_path, out)))
    assert (out / "capture.json").read_bytes() == b"an earlier capture, kept", "an existing output was replaced"
    assert names(out) == ["capture.json"] and refused(kind, value, "output_exists")


def test_a_write_that_reports_success_after_half_the_bytes_is_never_published(tmp_path, monkeypatch):
    """The in-repo write_new read-back: a short write that does not raise is never published."""
    (out,) = new_dirs(tmp_path, "cycle")
    offline(monkeypatch)
    opened = fail_nth_write_open(monkeypatch, out, 1, silent=True)
    kind, value = outcome(lambda: cr.bridge_capture(bridge_args(tmp_path, out)))
    assert "capture.json" in opened[0]
    assert names(out) == [], "a short capture.json is published under its real name"
    assert refused(kind, value, "output_unwritten")
