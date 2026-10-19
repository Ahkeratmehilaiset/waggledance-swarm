"""D21 (RCO1's R5 class, write-new) for the step-2 builds: corpus.build and encode.build publish their two files as a
new output directory, whole, or nothing at that path; an existing output is refused before any input is read and
is never replaced or written into. Synthetic notes, captures and a fake encoder only (no e5, no F: data, no network).

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
     sandbox/mind/step2/test_step2_write_new.py
"""

import errno
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import corpus as corpus_mod  # noqa: E402
import encode as encode_mod  # noqa: E402

CUTOFF = "2026-10-14T00:00:00Z"
NOTE_TIME = 1760263200  # 2026-10-12T10:00:00Z, before the cutoff
NOTES = {
    "alpha_note_20261010.md": "watcher retry reasons narrowed after a fatal refusal at startup",
    "beta_note_20261011.md": "a reply nonce is echoed verbatim so the requester can look it up",
}
EVENTS = [
    {"ts_utc": "2026-10-13T08:00:00Z", "agent": "claude-rco-1", "type": "message", "task_id": "lane/task-1",
     "message": "the decoded guard reads the escaped json string before the raw text"},
    {"ts_utc": "2026-10-13T09:00:00Z", "agent": "codex-tools-1", "type": "message", "task_id": "lane/task-2",
     "message": "the digest is recomputed after the powershell edition changes the escaping"},
]
UNWRITTEN, EXISTING = "output_unwritten", "output_exists"
SITES = ("parent", "mkdir", "open", "write", "flush", "fsync", "close", "readback", "rename")


def inputs(root, events=EVENTS):
    """A lessons directory with two notes older than the cutoff and one capture file; the corpus CLI arguments."""
    lessons = root / "lessons"
    lessons.mkdir(parents=True)
    for name, body in NOTES.items():
        path = lessons / name
        path.write_bytes(f"---\nname: {name[:-3]}\ndescription: {body}\n---\n\n{body}.\n".encode("utf-8"))
        os.utime(path, (NOTE_TIME, NOTE_TIME))
    capture = root / "capture.json"
    capture.write_bytes(json.dumps({"observed_utc": "2026-10-13T10:00:00Z", "events": events}).encode("utf-8"))
    return lessons, capture


def corpus_argv(lessons, capture, out_dir):
    return ["--lessons", str(lessons), "--capture", str(capture), "--cutoff", CUTOFF, "--out", str(out_dir)]


def build_corpus(lessons, capture, out_dir):
    return corpus_mod.build(lessons, [capture], corpus_mod.parse_utc(CUTOFF), out_dir)


def listing(directory):
    """The names in ``directory``, sorted; none when it does not exist."""
    return sorted(os.listdir(directory)) if directory.is_dir() else []


def tree(directory):
    """{relative path: bytes} of every file under ``directory``."""
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def staging_pattern(out_dir):
    return re.compile(re.escape(f".{out_dir.name}.staging-") + r"[0-9a-f]{16}")


def manifest_bytes(manifest):
    return json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8")


class FailingFile:
    """A write handle whose ``site`` step fails with ``error``: a write stores half of its bytes first (a cut write),
    a close fails after the real close, and ``readback`` silently stores one byte less than it reports (a lying
    device). D17's injection, for a directory."""

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


def inject(patch, out_dir, site, error):
    """``site`` fails ONCE with ``error``: creating the output's parent, creating the staging directory, a write open
    of a file in the output or in its staging directory (the handle's write, flush, close or byte count for those
    sites), os.fsync, or the rename onto the output. Returns the sites reached, in order."""
    reached = []
    real_mkdir, real_open, real_fsync, real_rename = Path.mkdir, Path.open, os.fsync, os.rename
    staged = staging_pattern(out_dir)

    def once(name):
        if site == name and name not in reached:
            reached.append(name)
            return True
        return False

    def mkdir(self, *args, **kwargs):
        path = Path(self)
        if path == out_dir.parent and once("parent"):
            raise error
        if path.parent == out_dir.parent and staged.fullmatch(path.name) and once("mkdir"):
            raise error
        return real_mkdir(self, *args, **kwargs)

    def open_(self, mode="r", *args, **kwargs):
        folder = Path(self).parent
        inside = folder == out_dir or (folder.parent == out_dir.parent and staged.fullmatch(folder.name))
        if not inside or not set(mode) & set("wax+"):
            return real_open(self, mode, *args, **kwargs)
        if once("open"):
            raise error
        handle = real_open(self, mode, *args, **kwargs)
        if site in ("write", "flush", "close", "readback") and once(site):
            return FailingFile(handle, site, error)
        return handle

    def fsync(fd):
        if once("fsync"):
            raise error
        return real_fsync(fd)

    def rename(source, target, *args, **kwargs):
        if Path(target) == out_dir and once("rename"):
            raise error
        return real_rename(source, target, *args, **kwargs)

    patch.setattr(Path, "mkdir", mkdir)
    patch.setattr(Path, "open", open_)
    patch.setattr(os, "fsync", fsync)
    patch.setattr(os, "rename", rename)
    return reached


def check_corpus(out_dir, manifest):
    """The published corpus: exactly its two files, the manifest as LF bytes, and the reader accepts it."""
    assert listing(out_dir) == ["corpus-manifest.json", "corpus.jsonl"]
    assert (out_dir / "corpus-manifest.json").read_bytes() == manifest_bytes(manifest)  # LF on every platform
    read_manifest, records = corpus_mod.read_corpus(out_dir)
    assert read_manifest == manifest and len(records) == manifest["records"] == 4


# corpus.build ---------------------------------------------------------------------------------------------------------

def test_d21_corpus_build_publishes_exactly_its_two_files_and_the_reader_accepts_them(tmp_path):
    lessons, capture = inputs(tmp_path)
    out_dir = tmp_path / "deeper" / "corpus"
    manifest = build_corpus(lessons, capture, out_dir)
    check_corpus(out_dir, manifest)
    assert listing(out_dir.parent) == ["corpus"]  # no staging directory stays


@pytest.mark.parametrize("existing", ["previous_build", "empty_directory", "file"])
def test_d21_corpus_build_refuses_an_existing_output_before_any_input_is_read_and_keeps_it(tmp_path, monkeypatch,
                                                                                         existing):
    lessons, capture = inputs(tmp_path)
    out_dir = tmp_path / "corpus"
    if existing == "previous_build":
        build_corpus(lessons, capture, out_dir)
    elif existing == "empty_directory":
        out_dir.mkdir()
    else:
        out_dir.write_bytes(b"not a corpus")
    before = tree(out_dir) if out_dir.is_dir() else out_dir.read_bytes()

    def no_read(*args, **kwargs):
        raise AssertionError("an input was read for an output that already exists")

    monkeypatch.setattr(corpus_mod, "load_captures", no_read)
    monkeypatch.setattr(corpus_mod, "lesson_records", no_read)
    with pytest.raises(corpus_mod.CorpusError, match=r"\Aoutput_exists\Z"):
        build_corpus(lessons, capture, out_dir)
    assert (tree(out_dir) if out_dir.is_dir() else out_dir.read_bytes()) == before
    assert listing(tmp_path) == sorted(["capture.json", "corpus", "lessons"])  # no staging directory either


@pytest.mark.parametrize("site", SITES)
def test_d21_corpus_an_error_at_any_write_step_publishes_nothing_and_leaves_no_staging(tmp_path, monkeypatch, site):
    lessons, capture = inputs(tmp_path)
    out_dir = tmp_path / "deeper" / "corpus"
    with monkeypatch.context() as patch:
        reached = inject(patch, out_dir, site, OSError(errno.EIO, f"injected {site} failure"))
        with pytest.raises(corpus_mod.CorpusError, match=r"\Aoutput_unwritten\Z"):
            build_corpus(lessons, capture, out_dir)
    assert reached == [site]
    assert not os.path.lexists(out_dir) and listing(out_dir.parent) == []  # no output and no staging directory
    assert out_dir.parent.is_dir() == (site != "parent")  # a parent made before the failure stays, empty
    check_corpus(out_dir, build_corpus(lessons, capture, out_dir))  # control: the same build publishes


CRASH = """
import os
import sys
from pathlib import Path
here, when = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(here))
import corpus
real_fsync, real_rename = os.fsync, os.rename

def fsync(fd):
    real_fsync(fd)
    if when == "first_sync":
        os._exit(17)  # a hard crash: no except, no finally, no cleanup

def rename(source, target, *args, **kwargs):
    if when == "after_rename":
        real_rename(source, target, *args, **kwargs)
    os._exit(17)

os.fsync, os.rename = fsync, rename
corpus.main(sys.argv[3:])
"""


@pytest.mark.parametrize("when", ["first_sync", "before_rename", "after_rename"])
def test_d21_corpus_a_hard_crash_never_leaves_a_cut_corpus_under_the_output_name(tmp_path, when):
    """The process dies after the first file's sync, just before the rename or just after it: the output is absent
    or whole, never cut; a staging leftover is never touched and never blocks a later build."""
    lessons, capture = inputs(tmp_path)
    out_dir = tmp_path / "deeper" / "corpus"
    run = subprocess.run([sys.executable, "-B", "-c", CRASH, str(HERE), when] + corpus_argv(lessons, capture, out_dir),
                         capture_output=True, text=True)
    assert run.returncode == 17, run.stderr[-2000:]
    if when == "after_rename":
        assert listing(out_dir.parent) == ["corpus"]
        manifest, records = corpus_mod.read_corpus(out_dir)
        assert len(records) == 4 and listing(out_dir) == ["corpus-manifest.json", "corpus.jsonl"]
        with pytest.raises(corpus_mod.CorpusError, match=r"\Aoutput_exists\Z"):
            build_corpus(lessons, capture, out_dir)
        return
    assert not os.path.lexists(out_dir)
    [staged] = listing(out_dir.parent)
    assert staging_pattern(out_dir).fullmatch(staged)
    leftover = tree(out_dir.parent / staged)
    assert list(leftover) == (["corpus.jsonl"] if when == "first_sync" else ["corpus-manifest.json", "corpus.jsonl"])
    check_corpus(out_dir, build_corpus(lessons, capture, out_dir))
    assert listing(out_dir.parent) == sorted(["corpus", staged])
    assert tree(out_dir.parent / staged) == leftover  # a leftover is never touched
    assert leftover["corpus.jsonl"] == (out_dir / "corpus.jsonl").read_bytes()  # the process died, the disk did not


def test_d21_corpus_files_are_staged_beside_the_output_and_synced_before_the_output_exists(tmp_path, monkeypatch):
    lessons, capture = inputs(tmp_path)
    out_dir = tmp_path / "deeper" / "corpus"
    real_fsync, seen = os.fsync, []

    def fsync_and_look(fd):
        real_fsync(fd)
        names = listing(out_dir.parent)
        seen.append((os.path.lexists(out_dir), names, [listing(out_dir.parent / name) for name in names]))

    monkeypatch.setattr(os, "fsync", fsync_and_look)
    manifest = build_corpus(lessons, capture, out_dir)
    assert len(seen) == 2 and not any(published for published, _, _ in seen)
    [(_, [first], [first_files]), (_, [second], [second_files])] = seen
    assert first == second and staging_pattern(out_dir).fullmatch(first)
    assert first_files == ["corpus.jsonl"] and second_files == ["corpus-manifest.json", "corpus.jsonl"]
    check_corpus(out_dir, manifest)


def test_d21_corpus_an_output_another_writer_makes_before_the_rename_is_kept_and_the_build_refused(tmp_path,
                                                                                                    monkeypatch):
    lessons, capture = inputs(tmp_path)
    out_dir = tmp_path / "deeper" / "corpus"
    real_fsync, synced = os.fsync, []

    def fsync_then_race(fd):
        real_fsync(fd)
        synced.append(fd)
        if len(synced) == 2:
            out_dir.mkdir()
            (out_dir / "theirs.txt").write_bytes(b"another writer's build")

    monkeypatch.setattr(os, "fsync", fsync_then_race)
    with pytest.raises(corpus_mod.CorpusError, match=r"\Aoutput_exists\Z"):
        build_corpus(lessons, capture, out_dir)
    assert tree(out_dir) == {"theirs.txt": b"another writer's build"} and listing(out_dir.parent) == ["corpus"]


def test_d21_corpus_a_staging_name_collision_is_never_used_or_removed(tmp_path, monkeypatch):
    lessons, capture = inputs(tmp_path)
    out_dir = tmp_path / "deeper" / "corpus"
    taken = out_dir.parent / (".corpus.staging-" + "0" * 16)
    taken.mkdir(parents=True)
    (taken / "corpus.jsonl").write_bytes(b"another build's staged bytes")
    monkeypatch.setattr(secrets, "token_hex", lambda size: "0" * 2 * size)
    with pytest.raises(corpus_mod.CorpusError, match=r"\Aoutput_unwritten\Z"):
        build_corpus(lessons, capture, out_dir)
    assert listing(out_dir.parent) == [taken.name] and tree(taken) == {"corpus.jsonl": b"another build's staged bytes"}


@pytest.mark.parametrize("existing", ["empty_directory", "file"])
def test_d21_publish_new_refuses_an_existing_output_before_it_stages_anything(tmp_path, monkeypatch, existing):
    """publish_new's own check, which the builds' early checks hide: no token, no staging and no sync for an output
    that exists (on POSIX the rename alone would replace an empty directory)."""
    out_dir = tmp_path / "corpus"
    if existing == "empty_directory":
        out_dir.mkdir()
    else:
        out_dir.write_bytes(b"not a corpus")

    def no_staging(*args, **kwargs):
        raise AssertionError("staged for an output that already exists")

    monkeypatch.setattr(secrets, "token_hex", no_staging)
    monkeypatch.setattr(os, "fsync", no_staging)
    with pytest.raises(corpus_mod.CorpusError, match=r"\Aoutput_exists\Z"):
        corpus_mod.publish_new(out_dir, {"corpus.jsonl": b"{}\n"}, corpus_mod.CorpusError)
    assert listing(tmp_path) == ["corpus"] and (listing(out_dir) == [] if out_dir.is_dir() else True)


def test_d21_corpus_a_cleanup_that_fails_never_changes_the_refusal_or_blocks_a_later_build(tmp_path, monkeypatch):
    """The rename fails and so does every removal of the staged files: the refusal stays output_unwritten, the
    staging directory stays behind unpublished, and the next build publishes beside it."""
    lessons, capture = inputs(tmp_path)
    out_dir = tmp_path / "deeper" / "corpus"
    real_unlink, staged_name = Path.unlink, staging_pattern(out_dir)
    with monkeypatch.context() as patch:
        reached = inject(patch, out_dir, "rename", OSError(errno.EIO, "injected rename failure"))

        def unlink(self, *args, **kwargs):
            if staged_name.fullmatch(Path(self).parent.name):
                raise PermissionError(errno.EACCES, "injected cleanup failure")
            return real_unlink(self, *args, **kwargs)

        patch.setattr(Path, "unlink", unlink)
        with pytest.raises(corpus_mod.CorpusError, match=r"\Aoutput_unwritten\Z"):
            build_corpus(lessons, capture, out_dir)
    assert reached == ["rename"] and not os.path.lexists(out_dir)
    [staged] = listing(out_dir.parent)
    assert staged_name.fullmatch(staged) and listing(out_dir.parent / staged) == ["corpus-manifest.json", "corpus.jsonl"]
    check_corpus(out_dir, build_corpus(lessons, capture, out_dir))
    assert listing(out_dir.parent) == sorted(["corpus", staged])


def test_d21_corpus_each_build_stages_under_a_fresh_random_token_so_a_leftover_never_blocks_it(tmp_path, monkeypatch):
    """Two builds beside an all-zero leftover, each staged under its own fresh 16-hex token (the crash test's child
    imports the module from disk, so an in-memory mutant with a fixed token never reaches it)."""
    lessons, capture = inputs(tmp_path)
    parent = tmp_path / "deeper"
    leftover = parent / (".corpus.staging-" + "0" * 16)
    leftover.mkdir(parents=True)
    (leftover / "corpus.jsonl").write_bytes(b"an earlier build's staged bytes")
    real_fsync, tokens = os.fsync, set()

    def fsync_and_look(fd):
        real_fsync(fd)
        tokens.update(name.rsplit("-", 1)[1] for name in listing(parent)
                      if ".staging-" in name and name != leftover.name)

    monkeypatch.setattr(os, "fsync", fsync_and_look)
    check_corpus(parent / "corpus", build_corpus(lessons, capture, parent / "corpus"))
    check_corpus(parent / "second", build_corpus(lessons, capture, parent / "second"))
    assert len(tokens) == 2 and all(re.fullmatch(r"[0-9a-f]{16}", token) for token in tokens)
    assert listing(parent) == sorted(["corpus", "second", leftover.name])
    assert tree(leftover) == {"corpus.jsonl": b"an earlier build's staged bytes"}


# encode.build (a fake encoder: the build path, not e5) --------------------------------------------------------------

class FakeE5:
    policy = {"prefix_passage": "passage: ", "normalize": True, "dtype": "float32", "dim": encode_mod.DIM,
              "device": "fake"}

    def passages(self, texts):
        rows = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "little")
            row = np.random.default_rng(seed).standard_normal(encode_mod.DIM)
            rows.append(row / np.linalg.norm(row))
        return np.ascontiguousarray(np.vstack(rows), dtype="<f4")


@pytest.fixture()
def fake_encoder(monkeypatch):
    monkeypatch.setattr(encode_mod, "E5", FakeE5)
    monkeypatch.setattr(encode_mod, "model_identity", lambda: {"model": "fake", "revision": "none"})


@pytest.fixture()
def corpus_dir(tmp_path):
    lessons, capture = inputs(tmp_path / "in")
    out_dir = tmp_path / "corpus"
    build_corpus(lessons, capture, out_dir)
    return out_dir


def test_d21_encode_build_publishes_exactly_its_two_files_and_the_reader_accepts_them(tmp_path, fake_encoder,
                                                                                    corpus_dir):
    out_dir = tmp_path / "deeper" / "vectors"
    manifest = encode_mod.build(corpus_dir, out_dir)
    assert listing(out_dir) == ["vectors-manifest.json", "vectors.f32"] and listing(out_dir.parent) == ["vectors"]
    assert (out_dir / "vectors-manifest.json").read_bytes() == manifest_bytes(manifest)  # LF on every platform
    read_manifest, views = encode_mod.read_vectors(out_dir, corpus_mod.read_corpus(corpus_dir)[0])
    assert read_manifest == manifest and {view: rows.shape for view, rows in views.items()} == {
        "w": (4, encode_mod.DIM), "d": (4, encode_mod.DIM)}


@pytest.mark.parametrize("existing", ["previous_build", "empty_directory"])
def test_d21_encode_build_refuses_an_existing_output_before_the_encoder_loads_and_keeps_it(tmp_path, monkeypatch,
                                                                                         fake_encoder, corpus_dir,
                                                                                         existing):
    out_dir = tmp_path / "vectors"
    if existing == "previous_build":
        encode_mod.build(corpus_dir, out_dir)
    else:
        out_dir.mkdir()
    before = tree(out_dir)

    def no_encoder():
        raise AssertionError("the encoder was loaded for an output that already exists")

    monkeypatch.setattr(encode_mod, "E5", no_encoder)
    with pytest.raises(encode_mod.EncodeError, match=r"\Aoutput_exists\Z"):
        encode_mod.build(corpus_dir, out_dir)
    assert tree(out_dir) == before


@pytest.mark.parametrize("site", ["open", "fsync", "rename"])
def test_d21_encode_an_error_at_a_write_step_publishes_nothing(tmp_path, monkeypatch, fake_encoder, corpus_dir, site):
    out_dir = tmp_path / "deeper" / "vectors"
    with monkeypatch.context() as patch:
        reached = inject(patch, out_dir, site, OSError(errno.EIO, f"injected {site} failure"))
        with pytest.raises(encode_mod.EncodeError, match=r"\Aoutput_unwritten\Z"):
            encode_mod.build(corpus_dir, out_dir)
    assert reached == [site] and not os.path.lexists(out_dir) and listing(out_dir.parent) == []
    encode_mod.build(corpus_dir, out_dir)  # control: the same build publishes
    assert listing(out_dir) == ["vectors-manifest.json", "vectors.f32"]
