"""R5 write-new tests for HEX MEMORY STEP 2 ``cell.py`` (fable-5, 2026-10-21), on synthetic rows only (pytest tmp_path).

The integrity ratchet counted four 'write' sites in cell.py at bdc39301: line 88 (``write_atomic``) and lines 184, 187
and 199 (``write_generation``: vectors.f32, provenance.jsonl, receipt.json). Verdicts, each pinned below:

* 184, 187, 199, the cut-file half, is NOT a defect: they write into the generation's staging directory, which
  ``publish`` makes fresh, removes on any exception and verifies from disk before the rename; a hard crash leaves only
  that hidden directory, never adopted (two guards).
* 184, 187, 199, two real defects (red on the bdc39301 module first): ``Path.write_bytes`` truncates, and the
  ``staging_not_empty`` guard saw only a non-empty FAISS index, so a file already in a non-fresh directory, a published
  EMPTY generation, or a file that appears while the generation is written was silently replaced; and no generation
  file was flushed and fsynced before the rename that gives it its canonical name, while the log line and the pointer
  are (that an OS crash could then leave the pointer naming unsynced bytes is from code, not observed).
* 88 is NOT a defect: both callers (the pointer swap, memory.json) replace by contract, and it is already a sibling
  staging file, written, flushed, fsynced and ``os.replace``d, so the old bytes stay on every failure (a guard).
"""

import errno
import os
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cell as cell_mod  # noqa: E402

CUTOFF = "2026-10-14T00:00:00.000000Z"
DIM = 8
SENTINEL = b"bytes another writer left here"
GENERATION_FILES = ("index", "meta", "vectors", "provenance", "receipt")


def rows(count, start=0):
    return [{"record_id": f"lesson:r5_{i:03d}#w", "kind": "lesson", "time_utc": "2026-10-12T00:00:00.000000Z",
             "stored_text": f"synthetic note number {i}", "meta": {"kind": "lesson", "file": f"r5_{i:03d}.md"},
             "provenance": {"source_kind": "memory_note", "file": f"r5_{i:03d}.md"}}
            for i in range(start, start + count)]


def vectors(count, start=0):
    out = np.full((count, DIM), 0.25, dtype="<f4")
    for row, i in enumerate(range(start, start + count)):
        out[row, i % DIM] += 1.0
    return out


def publish(cell_dir, count=2, start=0):
    return cell_mod.publish(cell_dir, "learning", rows(count, start), vectors(count, start), dim=DIM,
                            cutoff_utc=CUTOFF, encoder={}, corpus_sha256="0" * 64)


def write(gen_dir, count=2, start=0):
    return cell_mod.write_generation(gen_dir, "learning", rows(count, start), vectors(count, start), dim=DIM,
                                     cutoff_utc=CUTOFF, encoder={}, corpus_sha256="0" * 64, source="r5-test")


def tree(path):
    """Every entry under ``path``: a file with its bytes, a directory as None, so an untouched tree compares equal."""
    return {item.relative_to(path).as_posix(): item.read_bytes() if item.is_file() else None
            for item in sorted(path.rglob("*"))}


def generation_files(gen_dir):
    return dict(cell_mod.paths(gen_dir, "learning"), receipt=gen_dir / "receipt.json")


class HalfThenFull:
    """A write handle that stores half of its first write and then fails the way a full disk does."""

    def __init__(self, handle):
        self.handle = handle

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.handle.close()
        return False

    def write(self, data):
        self.handle.write(bytes(data)[:len(data) // 2])
        self.handle.flush()
        raise OSError(errno.ENOSPC, "injected: no space left halfway through the write")

    def flush(self):
        self.handle.flush()

    def fileno(self):
        return self.handle.fileno()

    def close(self):
        self.handle.close()


# --- RED on the bdc39301 module: write_generation replaces what is already there --------------------------------


@pytest.mark.parametrize("name", ["vectors.f32", "provenance.jsonl", "receipt.json"])
def test_r5_write_generation_refuses_a_directory_that_already_holds_a_file(tmp_path, name):
    """The docstring's 'fresh staging directory' is enforced before any byte is written: nothing replaced, nothing
    added."""
    gen_dir = tmp_path / "gen"
    gen_dir.mkdir()
    (gen_dir / name).write_bytes(SENTINEL)
    before = tree(gen_dir)
    with pytest.raises(cell_mod.CellError) as error:
        write(gen_dir)
    assert error.value.code == "staging_not_empty"
    assert tree(gen_dir) == before


def test_r5_write_generation_never_rewrites_a_published_empty_generation(tmp_path):
    """An empty FAISS index passed the old count-only guard, so a published generation of no records was rewritten
    in place under its canonical name."""
    cell_dir = tmp_path / "c"
    pointer = cell_mod.publish(cell_dir, "learning", [], np.zeros((0, DIM), dtype="<f4"), dim=DIM, cutoff_utc=CUTOFF,
                               encoder={}, corpus_sha256="0" * 64)
    before = tree(cell_dir)
    with pytest.raises(cell_mod.CellError) as error:
        write(cell_dir / f"gen-{pointer['generation']:06d}")
    assert error.value.code == "staging_not_empty"
    assert tree(cell_dir) == before
    view = cell_mod.load(cell_dir, "learning")
    assert view.healthy and view.pointer == pointer and view.collection.count == 0


@pytest.mark.parametrize("name", ["vectors.f32", "provenance.jsonl", "receipt.json"])
def test_r5_a_file_that_appears_while_the_generation_is_written_is_never_replaced(tmp_path, monkeypatch, name):
    """Another writer drops a file after the emptiness check (right after the FAISS files are saved): each file is
    created exclusively, so the call fails and that file keeps its bytes."""
    gen_dir = tmp_path / "gen"
    gen_dir.mkdir()
    real_save = cell_mod.FaissCollection.save

    def save_then_another_writer(self):
        real_save(self)
        (gen_dir / name).write_bytes(SENTINEL)

    monkeypatch.setattr(cell_mod.FaissCollection, "save", save_then_another_writer)
    with pytest.raises(FileExistsError):
        write(gen_dir)
    assert (gen_dir / name).read_bytes() == SENTINEL


# --- RED on the bdc39301 module: the generation is renamed into place before its bytes are synced ----------------


def test_r5_every_generation_file_is_synced_before_the_rename_that_publishes_it(tmp_path, monkeypatch):
    """Each file of the new generation, by (st_dev, st_ino), must be fsynced before ``os.rename`` gives the generation
    its gen-NNNNNN name. LOG.jsonl, synced after the rename, is the probe's own control."""
    synced, at_rename = set(), []
    real_fsync, real_rename = os.fsync, os.rename

    def fsync(fd):
        info = os.fstat(fd)
        real_fsync(fd)
        synced.add((info.st_dev, info.st_ino))

    def rename(source, target):
        if Path(target).name.startswith("gen-"):
            at_rename.append(set(synced))
        return real_rename(source, target)

    monkeypatch.setattr(cell_mod.os, "fsync", fsync)
    monkeypatch.setattr(cell_mod.os, "rename", rename)
    cell_dir = tmp_path / "c"
    pointer = publish(cell_dir)
    monkeypatch.undo()
    keys = {name: (os.stat(path).st_dev, os.stat(path).st_ino)
            for name, path in generation_files(cell_dir / f"gen-{pointer['generation']:06d}").items()}
    log = os.stat(cell_dir / "LOG.jsonl")
    assert len(at_rename) == 1 and len(set(keys.values())) == len(GENERATION_FILES)
    assert (log.st_dev, log.st_ino) in synced and (log.st_dev, log.st_ino) not in at_rename[0]  # the control
    assert sorted(name for name, key in keys.items() if key not in at_rename[0]) == []


# --- guards: they pass on the bdc39301 module and after the fix ---------------------------------------------------


def test_r5_guard_a_write_that_fails_halfway_in_staging_publishes_nothing(tmp_path, monkeypatch):
    """provenance.jsonl gets half its bytes and then a full disk: the publish fails, its staging directory is removed,
    and the cell keeps its published generation, log and pointer byte for byte."""
    cell_dir = tmp_path / "c"
    first = publish(cell_dir)
    before = tree(cell_dir)
    real_open, real_write_bytes = open, Path.write_bytes

    def open_(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if Path(file).name == "provenance.jsonl" and ("w" in mode or "x" in mode):
            return HalfThenFull(handle)
        return handle

    def write_bytes(self, data):  # the bdc39301 module writes this file with Path.write_bytes
        if self.name == "provenance.jsonl":
            with open_(self, "wb") as handle:
                return handle.write(data)
        return real_write_bytes(self, data)

    monkeypatch.setattr(cell_mod, "open", open_, raising=False)
    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    with pytest.raises(OSError) as error:
        publish(cell_dir, 3, start=2)
    monkeypatch.undo()
    assert error.value.errno == errno.ENOSPC
    assert tree(cell_dir) == before
    view = cell_mod.load(cell_dir, "learning")
    assert view.healthy and view.pointer == first


def test_r5_guard_a_staging_directory_left_by_a_crash_is_never_adopted(tmp_path):
    """A hard crash mid-write leaves its hidden staging directory with a cut file: the next publish neither adopts
    nor touches it."""
    cell_dir = tmp_path / "c"
    first = publish(cell_dir)
    leftover = cell_dir / f".staging-{first['generation'] + 1:06d}-{'0' * 32}"
    leftover.mkdir()
    (leftover / "vectors.f32").write_bytes(b"\x00\x00\x80")
    untouched = tree(leftover)
    second = publish(cell_dir, 3, start=2)
    assert second["generation"] == first["generation"] + 1
    view = cell_mod.load(cell_dir, "learning")
    assert view.healthy and view.pointer == second
    assert [row["record_id"] for row in view.rows] == [row["record_id"] for row in rows(3, start=2)]
    assert tree(leftover) == untouched


@pytest.mark.parametrize("step", ["write", "fsync", "replace"])
def test_r5_guard_write_atomic_keeps_the_old_bytes_when_a_step_fails(tmp_path, monkeypatch, step):
    """Line 88 is a replace by contract (pointer, memory.json): a failure at its write, fsync or os.replace leaves
    the old file byte for byte; the success path then writes exactly the new bytes."""
    target = tmp_path / "current.json"
    target.write_bytes(b'{"generation":1}')
    real_open = open

    def open_(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        return HalfThenFull(handle) if step == "write" and Path(file).name.endswith(".tmp") else handle

    def fail(*args, **kwargs):
        raise OSError(errno.EIO, f"injected: {step} failed")

    monkeypatch.setattr(cell_mod, "open", open_, raising=False)
    if step != "write":
        monkeypatch.setattr(cell_mod.os, step, fail)
    with pytest.raises(OSError):
        cell_mod.write_atomic(target, b'{"generation":2}')
    monkeypatch.undo()
    assert target.read_bytes() == b'{"generation":1}'
    cell_mod.write_atomic(target, b'{"generation":2}')
    assert target.read_bytes() == b'{"generation":2}'


def test_r5_guard_success_path_bytes_are_the_documented_formulas(tmp_path):
    gen_dir = tmp_path / "gen"
    gen_dir.mkdir()
    receipt = write(gen_dir, 3)
    files = generation_files(gen_dir)
    assert files["vectors"].read_bytes() == np.ascontiguousarray(vectors(3), dtype="<f4").tobytes()
    expected = [{"record_id": r["record_id"], "kind": r["kind"], "time_utc": r["time_utc"], "cell_id": "learning",
                 "provenance": r["provenance"]} for r in rows(3)]
    assert files["provenance"].read_bytes() == "".join(cell_mod.canonical(r) + "\n" for r in expected).encode("utf-8")
    assert files["receipt"].read_bytes() == cell_mod.canonical(receipt).encode("utf-8")
    checked = cell_mod.verify_generation(gen_dir, "learning", cell_mod.file_sha256(files["receipt"]))
    assert checked[0] == receipt and checked[3] is True
