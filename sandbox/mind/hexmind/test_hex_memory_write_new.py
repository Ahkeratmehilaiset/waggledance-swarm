"""R5 write-new (RCO1's R5 class) for ``hex_memory.build`` (fable-5, 2026-10-21): the index directory ``--out`` is
published as a NEW directory, whole, or nothing at that path (the ``step2/corpus.publish_new`` shape). An existing
``--out`` is refused before any input is read and is never replaced or written into; a fault at any write step, a
refused receipt or a hard crash leaves nothing at ``--out``; a published index keeps the files and bytes of before.
Synthetic captures and lessons in tmp_path and the hash embedder only (no e5, no data disk, no network).

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
     sandbox/mind/hexmind/test_hex_memory_write_new.py
"""

import errno
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("faiss")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hex_memory as hm  # noqa: E402
import core.faiss_store as faiss_store  # noqa: E402  (hex_memory puts the repository root on sys.path)

EVENTS = [
    {"agent": "claude-rco-2", "ts_utc": "2026-10-12T07:36:00Z", "type": "message", "status": "info",
     "task_id": "claude-rco-2/t", "message": "status system health uptime of the monitor"},
    {"agent": "codex-lead-1", "ts_utc": "2026-10-12T08:00:00Z", "type": "message", "status": "info",
     "task_id": "codex-lead-1/t", "message": "composition of the nine members for the merge"},
    {"agent": "claude-rco-1", "ts_utc": "2026-10-12T09:00:00Z", "type": "finding", "status": "finding",
     "task_id": "claude-rco-1/t", "message": "veto: changes requested on the gate"},
]
LESSON = ("---\nname: feedback-lowercase-before-a-case-sensitive-check\n---\n"
          "Lowercasing the text before a case-sensitive leak check lets a mixed-case variant through.\n")
QUESTION = "seen before? lowercasing before a case-sensitive leak check"
INDEX = ["embedder.json", "faiss", "hex-memory-receipt.json"]
UNWRITTEN, EXISTING = "HexMemoryError:output_unwritten", "HexMemoryError:output_exists"
SITES = ("parent", "staging", "open", "write", "flush", "fsync", "close", "readback", "faiss_save", "faiss_sync",
         "receipt_write", "rename")


def inputs(root, extra=None):
    """A synthetic capture and lessons directory under ``root``; ``extra`` adds one event (another build's input)."""
    lessons = root / "lessons"
    lessons.mkdir(parents=True)
    (lessons / "feedback_lowercase_case_check_20261009.md").write_bytes(LESSON.encode("utf-8"))
    events = EVENTS + ([{"agent": "codex-tools-1", "ts_utc": "2026-10-12T10:00:00Z", "type": "message",
                         "status": "info", "task_id": "codex-tools-1/t", "message": extra}] if extra else [])
    capture = root / "capture.json"
    capture.write_bytes(json.dumps({"events": events}).encode("utf-8"))
    return capture, lessons


def argv(capture, lessons, out):
    return ["build", "--capture", str(capture), "--lessons", str(lessons), "--out", str(out), "--embedder", "hash"]


def outcome(call):
    """What a build did: "published" when it returned 0, else the error's type name and text."""
    try:
        code = call()
    except (OSError, ValueError, RuntimeError) as error:
        return f"{type(error).__name__}:{error}"
    return "published" if code == 0 else f"exit {code}"


def listing(directory):
    """The names in ``directory``, sorted; none when it is not a directory."""
    return sorted(os.listdir(directory)) if directory.is_dir() else []


def tree(directory):
    """{relative path: bytes} of every file under ``directory``."""
    if not directory.is_dir():
        return {}
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def left(out):
    """What a build left at the output name (file sizes), for a failure message."""
    if out.is_dir():
        return {name: len(data) for name, data in tree(out).items()}
    return out.read_bytes()[:40] if os.path.lexists(out) else None


def staging_pattern(out):
    """The staging name ``.staging-<16 hex>``, the same whatever ``out`` is called (Root GPT review F1 below)."""
    return re.compile(r"\.staging-[0-9a-f]{16}")


def check_index(out):
    """A published index: exactly its files, two FAISS files per cell, and the reader recalls the lesson from it."""
    assert listing(out) == INDEX
    for cell in listing(out / "faiss"):
        assert listing(out / "faiss" / cell) == ["index.faiss", "meta.json"]
    hits = hm.Memory(out).recall(QUESTION, k=2)["hits"]
    assert any(hit["doc_id"].startswith("lesson:") for hit in hits)


class FailingFile:
    """A write handle whose ``site`` step fails with ``error``: a write stores half of its bytes first (a cut write),
    a close fails after the real close, and ``readback`` silently stores one byte less than it reports (a lying
    device). The D17/D21 injection."""

    def __init__(self, handle, site, error):
        self.handle, self.site, self.error = handle, site, error

    def write(self, data):
        if self.site == "write":
            self.handle.write(data[:len(data) // 2])
            self.handle.flush()
            raise self.error
        if self.site == "readback":
            self.handle.write(data[:-1])
            return len(data)
        return self.handle.write(data)

    def flush(self):
        if self.site == "flush":
            raise self.error
        return self.handle.flush()

    def close(self):
        self.handle.close()
        if self.site == "close":
            raise self.error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __getattr__(self, name):
        return getattr(self.handle, name)


def inject(patch, out, site, error):
    """``site`` fails ONCE with ``error``: making the output's parent, making the staging directory, the write open of
    embedder.json in the output or in a staging directory (or that handle's write, flush, close or byte count), the
    first os.fsync, a FAISS cell save that leaves its meta.json cut, the second os.fsync (the first FAISS file), a
    write of the receipt that stores half of its bytes, or the rename onto the output. Returns the sites reached."""
    reached, syncs = [], []
    real_mkdir, real_open, real_fsync, real_rename = Path.mkdir, Path.open, os.fsync, os.rename
    real_save = faiss_store.FaissCollection.save
    staged = staging_pattern(out)

    def once(name):
        if site == name and name not in reached:
            reached.append(name)
            return True
        return False

    def mkdir(self, *args, **kwargs):
        path = Path(self)
        if path == out.parent and once("parent"):
            raise error
        if path.parent == out.parent and staged.fullmatch(path.name) and once("staging"):
            raise error
        return real_mkdir(self, *args, **kwargs)

    def open_(self, mode="r", *args, **kwargs):
        path = Path(self)
        inside = path.parent == out or (path.parent.parent == out.parent and staged.fullmatch(path.parent.name))
        if not inside or not set(mode) & set("wax+"):
            return real_open(self, mode, *args, **kwargs)
        if path.name == "embedder.json" and once("open"):
            raise error
        handle = real_open(self, mode, *args, **kwargs)
        if path.name == "embedder.json" and site in ("write", "flush", "close", "readback") and once(site):
            return FailingFile(handle, site, error)
        if path.name == "hex-memory-receipt.json" and once("receipt_write"):
            return FailingFile(handle, "write", error)
        return handle

    def fsync(fd):
        syncs.append(fd)
        if (len(syncs) == 1 and once("fsync")) or (len(syncs) == 2 and once("faiss_sync")):
            raise error
        return real_fsync(fd)

    def save(self):
        real_save(self)
        if once("faiss_save"):
            meta = Path(self._dir) / "meta.json"
            data = meta.read_bytes()
            meta.write_bytes(data[:len(data) // 2])  # the cut file a crash in the middle of this write leaves
            raise error

    def rename(source, target, *args, **kwargs):
        if Path(target) == out and once("rename"):
            raise error
        return real_rename(source, target, *args, **kwargs)

    patch.setattr(Path, "mkdir", mkdir)
    patch.setattr(Path, "open", open_)
    patch.setattr(os, "fsync", fsync)
    patch.setattr(faiss_store.FaissCollection, "save", save)
    patch.setattr(os, "rename", rename)
    return reached


def test_r5_a_build_publishes_exactly_its_index_in_the_format_of_before(tmp_path, capsys):
    capture, lessons = inputs(tmp_path / "in")
    out = tmp_path / "deeper" / "index"
    assert hm.main(argv(capture, lessons, out)) == 0
    printed = json.loads(capsys.readouterr().out.splitlines()[-1])
    check_index(out)
    assert listing(out.parent) == ["index"]  # no staging directory stays
    assert printed["records"] == sum(printed["by_cell"].values()) == 4
    assert listing(out / "faiss") == sorted(f"cell_{cell}" for cell in printed["by_cell"])
    embedder = (out / "embedder.json").read_bytes()
    assert embedder == json.dumps(json.loads(embedder), ensure_ascii=True, sort_keys=True,
                                  separators=(",", ":")).encode("ascii")
    # the text-mode write of before R5: the indent=1 lines end in os.linesep (CRLF on Windows)
    receipt = (out / "hex-memory-receipt.json").read_bytes()
    assert receipt == json.dumps(printed, indent=1, sort_keys=True).replace("\n", os.linesep).encode("ascii")


@pytest.mark.parametrize("existing", ["previous_index", "empty_directory", "file"])
def test_r5_an_existing_output_is_refused_and_kept_byte_for_byte(tmp_path, existing):
    capture, lessons = inputs(tmp_path / "in")
    out = tmp_path / "index"
    if existing == "previous_index":
        assert hm.main(argv(capture, lessons, out)) == 0
        capture, lessons = inputs(tmp_path / "other", extra="a later event that only the second build would add")
    elif existing == "empty_directory":
        out.mkdir()
    else:
        out.write_bytes(b"not an index")
    before = tree(out) if out.is_dir() else out.read_bytes()
    result = outcome(lambda: hm.main(argv(capture, lessons, out)))
    after = tree(out) if out.is_dir() else out.read_bytes()
    assert (result, after == before) == (EXISTING, True), f"now at --out: {left(out)}"
    assert not [name for name in os.listdir(tmp_path) if ".staging-" in name]


def test_r5_an_existing_output_is_refused_before_any_input_is_read(tmp_path, monkeypatch):
    capture, lessons = inputs(tmp_path / "in")
    out = tmp_path / "index"
    assert hm.main(argv(capture, lessons, out)) == 0
    before = tree(out)
    real_read_bytes = Path.read_bytes

    def read_bytes(self):
        if Path(self) == capture:
            raise AssertionError("the capture was read for an output that already exists")
        return real_read_bytes(self)

    def no_read(*args, **kwargs):
        raise AssertionError("an input was read for an output that already exists")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", read_bytes)
        patch.setattr(hm, "event_records", no_read)
        patch.setattr(hm, "lesson_records", no_read)
        result = outcome(lambda: hm.main(argv(capture, lessons, out)))
    assert result == EXISTING and tree(out) == before


@pytest.mark.parametrize("site", SITES)
def test_r5_a_fault_at_any_write_step_publishes_nothing_and_leaves_no_staging(tmp_path, monkeypatch, site):
    capture, lessons = inputs(tmp_path / "in")
    out = tmp_path / "deeper" / "index"
    with monkeypatch.context() as patch:
        reached = inject(patch, out, site, OSError(errno.EIO, f"injected {site} failure"))
        result = outcome(lambda: hm.main(argv(capture, lessons, out)))
    assert (result, os.path.lexists(out), listing(out.parent)) == (UNWRITTEN, False, []), f"at --out: {left(out)}"
    assert reached == [site]
    assert out.parent.is_dir() == (site != "parent")  # a parent made before the fault stays, empty
    assert hm.main(argv(capture, lessons, out)) == 0  # control: the same build publishes
    check_index(out)


def test_r5_a_receipt_the_privacy_guard_refuses_publishes_no_partial_index(tmp_path, monkeypatch):
    capture, lessons = inputs(tmp_path / "in")
    out = tmp_path / "deeper" / "index"
    real_guard = hm.ws.guard_object

    def guard(value):
        if isinstance(value, dict) and value.get("schema") == hm.SCHEMA:
            raise hm.ws.WorkspaceError("refused by the test")
        return real_guard(value)

    with monkeypatch.context() as patch:
        patch.setattr(hm.ws, "guard_object", guard)
        result = outcome(lambda: hm.main(argv(capture, lessons, out)))
    state = (result, os.path.lexists(out), listing(out.parent))
    assert state == ("WorkspaceError:refused by the test", False, []), f"at --out: {left(out)}"
    assert hm.main(argv(capture, lessons, out)) == 0
    check_index(out)


def test_r5_every_file_is_synced_in_one_staging_directory_before_the_output_name_exists(tmp_path, monkeypatch,
                                                                                         capsys):
    capture, lessons = inputs(tmp_path / "in")
    out = tmp_path / "deeper" / "index"
    real_fsync, seen = os.fsync, []

    def fsync_and_look(fd):
        real_fsync(fd)
        seen.append((os.path.lexists(out), tuple(listing(out.parent))))

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fsync_and_look)
        assert hm.main(argv(capture, lessons, out)) == 0
    printed = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert len(seen) == 2 + 2 * len(printed["by_cell"])  # embedder.json, the receipt and each cell's two FAISS files
    assert not any(published for published, _ in seen)
    [(staged,)] = {names for _, names in seen}  # one staging directory, alone beside the output name
    assert staging_pattern(out).fullmatch(staged)
    check_index(out)


def test_r5_an_output_another_writer_makes_before_the_rename_is_kept_and_the_build_refused(tmp_path, monkeypatch):
    capture, lessons = inputs(tmp_path / "in")
    out = tmp_path / "deeper" / "index"
    real_rename = os.rename

    def race(source, target, *args, **kwargs):
        if Path(target) == out:
            out.mkdir()
            (out / "theirs.txt").write_bytes(b"another writer's index")
        return real_rename(source, target, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "rename", race)
        result = outcome(lambda: hm.main(argv(capture, lessons, out)))
    assert (result, tree(out), listing(out.parent)) == (EXISTING, {"theirs.txt": b"another writer's index"}, ["index"])


def test_r5_a_staging_name_collision_is_never_used_or_removed(tmp_path, monkeypatch):
    capture, lessons = inputs(tmp_path / "in")
    out = tmp_path / "deeper" / "index"
    taken = out.parent / (".staging-" + "0" * 16)
    taken.mkdir(parents=True)
    (taken / "embedder.json").write_bytes(b"another build's staged bytes")
    with monkeypatch.context() as patch:
        patch.setattr(secrets, "token_hex", lambda size: "0" * 2 * size)
        result = outcome(lambda: hm.main(argv(capture, lessons, out)))
    assert (result, listing(out.parent), tree(taken)) == (UNWRITTEN, [taken.name],
                                                          {"embedder.json": b"another build's staged bytes"})


def test_r5_a_cleanup_that_fails_never_changes_the_refusal_or_blocks_a_later_build(tmp_path, monkeypatch):
    capture, lessons = inputs(tmp_path / "in")
    out = tmp_path / "deeper" / "index"
    staged = staging_pattern(out)

    def refuse(real):
        def call(path, *args, **kwargs):
            if any(staged.fullmatch(part) for part in Path(path).parts):
                raise PermissionError(errno.EACCES, "injected cleanup failure")
            return real(path, *args, **kwargs)
        return call

    with monkeypatch.context() as patch:
        reached = inject(patch, out, "rename", OSError(errno.EIO, "injected rename failure"))
        for name in ("unlink", "remove", "rmdir"):
            patch.setattr(os, name, refuse(getattr(os, name)))
        result = outcome(lambda: hm.main(argv(capture, lessons, out)))
    assert (result, reached, os.path.lexists(out)) == (UNWRITTEN, ["rename"], False)
    [leftover] = listing(out.parent)
    assert staged.fullmatch(leftover) and listing(out.parent / leftover) == INDEX
    assert hm.main(argv(capture, lessons, out)) == 0  # the next build publishes beside the leftover
    check_index(out)
    assert listing(out.parent) == sorted(["index", leftover])


CRASH = r'''
import os
import sys
from pathlib import Path

here, when = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(here))
import hex_memory as hm

real_open, real_guard, real_rename = Path.open, hm.ws.guard_object, os.rename


class Cut:
    """A write handle that dies halfway through its write: no except, no finally, no cleanup."""

    def __init__(self, handle):
        self.handle = handle

    def write(self, data):
        self.handle.write(data[:len(data) // 2])
        self.handle.flush()
        os._exit(17)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.handle.close()
        return False

    def __getattr__(self, name):
        return getattr(self.handle, name)


def open_(self, mode="r", *args, **kwargs):
    handle = real_open(self, mode, *args, **kwargs)
    return Cut(handle) if set(mode) & set("wx") and Path(self).name == when else handle


def guard(value):
    if when == "before_receipt" and isinstance(value, dict) and value.get("schema") == hm.SCHEMA:
        os._exit(17)  # every FAISS cell is saved, the receipt is not written yet
    return real_guard(value)


def rename(source, target, *args, **kwargs):
    real_rename(source, target, *args, **kwargs)
    if when == "after_publish":
        os._exit(17)


Path.open, hm.ws.guard_object, os.rename = open_, guard, rename
sys.exit(hm.main(sys.argv[3:]))
'''


@pytest.mark.parametrize("when", ["embedder.json", "hex-memory-receipt.json", "before_receipt", "after_publish"])
def test_r5_a_hard_crash_never_leaves_a_cut_or_partial_index_under_the_output_name(tmp_path, when):
    """The process dies halfway through embedder.json, halfway through the receipt, after the FAISS cells but before
    the receipt, or right after the publishing rename: the output is absent or whole, never cut or partial; a staging
    leftover is never touched and never blocks a later build."""
    capture, lessons = inputs(tmp_path / "in")
    out = tmp_path / "deeper" / "index"
    run = subprocess.run([sys.executable, "-B", "-I", "-c", CRASH, str(HERE), when] + argv(capture, lessons, out),
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 17, run.stderr[-2000:]
    if when == "after_publish":
        check_index(out)
        assert listing(out.parent) == ["index"]
        assert outcome(lambda: hm.main(argv(capture, lessons, out))) == EXISTING
        return
    assert not os.path.lexists(out), f"left at --out by the crash: {left(out)}"
    [staged] = listing(out.parent)
    assert staging_pattern(out).fullmatch(staged)
    leftover = tree(out.parent / staged)
    assert hm.main(argv(capture, lessons, out)) == 0  # a leftover never blocks a later build
    check_index(out)
    assert listing(out.parent) == sorted(["index", staged]) and tree(out.parent / staged) == leftover


# --- Root GPT static review of 7d094fb4 (Lead 2026-10-21T14:24:18Z), RED first on that module ---------------------


def test_r5_review_a_long_valid_output_name_publishes(tmp_path):
    """F1: the staging name ``.<name>.staging-<16 hex>`` is 26 characters longer than the output name, so a valid
    240-character output name made a 266-character staging component, over the usual 255 limit. The control comes
    first: when this filesystem refuses a 240-character name, the case is UNKNOWN here and skipped with that reason."""
    capture, lessons = inputs(tmp_path / "in")
    control = tmp_path / ("c" * 240)
    try:
        control.mkdir()
    except OSError as error:
        pytest.skip(f"UNKNOWN here: this filesystem refuses a 240-character name ({error})")
    out = tmp_path / ("i" * 240)
    result = outcome(lambda: hm.main(argv(capture, lessons, out)))
    assert result == "published", f"at --out: {left(out)}"
    check_index(out)
    assert listing(tmp_path) == sorted(["c" * 240, "i" * 240, "in"])


def cut_half(path):
    data = path.read_bytes()
    path.write_bytes(data[:len(data) // 2])


def drop_last_doc(path):
    """A meta.json that still parses but no longer lists the cell's last record."""
    meta = json.loads(path.read_bytes())
    for key in ("doc_ids", "texts", "metadata"):
        meta[key] = meta[key][:-1]
    path.write_bytes(json.dumps(meta).encode("utf-8"))


def empty_index(path):
    """An index.faiss that still loads, with the same dim and no vector."""
    faiss = faiss_store.faiss
    faiss.write_index(faiss.IndexFlatIP(faiss.read_index(str(path)).d), str(path))


SILENT = {"meta_cut": ("meta.json", cut_half), "index_cut": ("index.faiss", cut_half),
          "meta_altered": ("meta.json", drop_last_doc), "index_emptied": ("index.faiss", empty_index)}


@pytest.mark.parametrize("case", sorted(SILENT))
def test_r5_review_a_faiss_save_that_silently_cuts_or_alters_a_file_publishes_nothing(tmp_path, monkeypatch, case):
    """F3: a FAISS cell save that leaves a file cut or altered WITHOUT raising (a lying device, the class the JSON
    read-back already covers; the faiss_save site above cuts AND raises, so it proves only the cleanup): half a file,
    a meta.json that parses but lacks the cell's last record, or an index that loads with no vector. Nothing is
    published, no staging directory stays, and the same build then publishes."""
    capture, lessons = inputs(tmp_path / "in")
    out = tmp_path / "deeper" / "index"
    name, change = SILENT[case]
    real_save, changed = faiss_store.FaissCollection.save, []

    def save_then_change(self):
        real_save(self)
        if not changed:
            change(Path(self._dir) / name)
            changed.append(self.name)

    with monkeypatch.context() as patch:
        patch.setattr(faiss_store.FaissCollection, "save", save_then_change)
        result = outcome(lambda: hm.main(argv(capture, lessons, out)))
    assert len(changed) == 1  # the witness changed exactly one file
    assert (result, os.path.lexists(out), listing(out.parent)) == (UNWRITTEN, False, []), f"at --out: {left(out)}"
    assert hm.main(argv(capture, lessons, out)) == 0  # control: the same build publishes
    check_index(out)
