"""D22 for step-2 calibrate (Root's 08:33Z finding plus RCO1's R5 class, write-new): a case with no record available
before it is an explicit UNKNOWN row (every statistic None, ``unknown`` says why, the case kept and counted, no query
encoded, no score made up), and ``--out`` is written new: an existing output is refused before any input is read, and
the result is published whole as LF bytes, or nothing at that path. D23 (Lead's 09:03Z slice): a case whose before
time is missing, malformed, timezone-less or out of range is an UNKNOWN row too (never a made-up time), and the encoder
is built only at the first case that has an applicable record. Synthetic notes, captures and a fake encoder only (no
e5, no F: data, no network).

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
     sandbox/mind/step2/test_calibrate_unknown.py
"""

import errno
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import calibrate as calibrate_mod  # noqa: E402
import corpus as corpus_mod  # noqa: E402
import encode as encode_mod  # noqa: E402

CUTOFF = "2026-10-14T00:00:00Z"
NOTE_TIME = 1760263200  # 2025-10-12T10:00:00Z, before the cutoff (D21's test comment says 2026: its value is 2025)
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
EARLY = "2025-10-01T00:00:00Z"  # before every record (the notes' mtime too): none is applicable
LATE = "2026-10-13T12:00:00Z"  # after every record: all four are applicable
STATISTICS = ["gap10", "gap2", "gap5", "hub", "lex", "s1", "z"]
UNWRITTEN, EXISTING = "output_unwritten", "output_exists"
SITES = ("parent", "open", "write", "flush", "fsync", "close", "readback", "link")


def unit(text):
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "little")
    row = np.random.default_rng(seed).standard_normal(encode_mod.DIM)
    return row / np.linalg.norm(row)


class FakeE5:
    """Deterministic unit vectors per text; every encoded query is recorded in ``queries``, every construction counted
    in ``made``."""
    policy = {"prefix_passage": "passage: ", "normalize": True, "dtype": "float32", "dim": encode_mod.DIM,
              "device": "fake"}
    queries = []
    made = 0

    def __init__(self):
        FakeE5.made += 1

    def passages(self, texts):
        return np.ascontiguousarray(np.vstack([unit("passage: " + text) for text in texts]), dtype="<f4")

    def query(self, text):
        FakeE5.queries.append(text)
        return np.ascontiguousarray(unit("query: " + text), dtype="<f4")


@pytest.fixture()
def built(tmp_path, monkeypatch):
    """A synthetic corpus of two notes and two events and its vectors, built with the fake encoder."""
    monkeypatch.setattr(encode_mod, "E5", FakeE5)
    monkeypatch.setattr(encode_mod, "model_identity", lambda: {"model": "fake", "revision": "none"})
    monkeypatch.setattr(FakeE5, "queries", [])
    lessons = tmp_path / "in" / "lessons"
    lessons.mkdir(parents=True)
    for name, body in NOTES.items():
        path = lessons / name
        path.write_bytes(f"---\nname: {name[:-3]}\ndescription: {body}\n---\n\n{body}.\n".encode("utf-8"))
        os.utime(path, (NOTE_TIME, NOTE_TIME))
    capture = tmp_path / "in" / "capture.json"
    capture.write_bytes(json.dumps({"observed_utc": "2026-10-13T10:00:00Z", "events": EVENTS}).encode("utf-8"))
    corpus_dir, vectors_dir = tmp_path / "in" / "corpus", tmp_path / "in" / "vectors"
    corpus_mod.build(lessons, [capture], corpus_mod.parse_utc(CUTOFF), corpus_dir)
    encode_mod.build(corpus_dir, vectors_dir)
    monkeypatch.setattr(FakeE5, "made", 0)  # the builds above made their own encoder
    return corpus_dir, vectors_dir


def case_set(path, cases=(), negative=()):
    path.write_bytes(json.dumps({"cases": list(cases), "negative": list(negative)}).encode("utf-8"))
    return path


def positive(case_id, before):
    return {"id": case_id, "situation": f"{case_id}: why did the watcher narrow its retry reasons",
            "before": before, "expect_lessons": [{"file": "alpha_note_20261010.md"}]}


def argv(built, sets, out):
    corpus_dir, vectors_dir = built
    args = ["--corpus", str(corpus_dir), "--vectors", str(vectors_dir), "--out", str(out)]
    for path in sets:
        args += ["--set", str(path)]
    return args


def listing(directory):
    """The names in ``directory``, sorted; none when it does not exist."""
    return sorted(os.listdir(directory)) if directory.is_dir() else []


def staging_pattern(out):
    return re.compile(re.escape(f".{out.name}.staging-") + r"[0-9a-f]{16}")


def check_result(out):
    """The published result: LF bytes exactly as ``json.dumps(result, indent=1)`` writes them on every platform."""
    data = out.read_bytes()
    result = json.loads(data)
    assert b"\r" not in data and data == json.dumps(result, indent=1).encode("utf-8")
    return result


# UNKNOWN rows ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["positive", "negative"])
def test_d22_a_case_with_no_applicable_record_is_an_unknown_row_and_no_query_is_encoded(tmp_path, built, capsys,
                                                                                         kind):
    early = positive("early", EARLY) if kind == "positive" else {"id": "early", "situation": "a quiet start",
                                                                 "before": EARLY}
    sets = [case_set(tmp_path / "set.json", [positive("late", LATE)] + ([early] if kind == "positive" else []),
                     [] if kind == "positive" else [early])]
    out = tmp_path / "calibration.json"
    assert calibrate_mod.main(argv(built, sets, out)) == 0
    result = check_result(out)
    rows = {row["id"]: row for row in result["rows"]}
    assert sorted(rows) == ["early", "late"] and result["unknown_rows"] == 1
    unknown = rows["early"]
    assert unknown["kind"] == kind and unknown["applicable"] == 0 and unknown["unknown"] == "no_applicable_records"
    assert {key: unknown[key] for key in STATISTICS} == dict.fromkeys(STATISTICS) and unknown["top3"] == []
    if kind == "positive":
        assert (unknown["expected_in_corpus"], unknown["missing_expected"]) == (0, 1)
        assert unknown["best_rank"] is None and unknown["best_expected_score"] is None
    known = rows["late"]
    assert known["applicable"] == 4 and "unknown" not in known and len(known["top3"]) == 3
    assert all(isinstance(known[key], float) and np.isfinite(known[key]) for key in STATISTICS)
    assert known["expected_in_corpus"] == 1 and known["best_rank"] in (1, 2, 3, 4)
    assert FakeE5.queries == [positive("late", LATE)["situation"]]  # the UNKNOWN case encodes no query
    lines = {line.split()[1]: line for line in capsys.readouterr().out.splitlines()}
    assert lines["early"].split()[2:] == ["UNKNOWN", "no_applicable_records"] and "s1=" in lines["late"]


def test_d22_every_case_unknown_still_publishes_every_row(tmp_path, built):
    sets = [case_set(tmp_path / "set.json", [positive("first", EARLY), positive("second", EARLY)])]
    out = tmp_path / "calibration.json"
    assert calibrate_mod.main(argv(built, sets, out)) == 0
    result = check_result(out)
    assert [row["id"] for row in result["rows"]] == ["first", "second"] and result["unknown_rows"] == 2
    assert FakeE5.queries == []


def test_d22_statistics_of_no_applicable_record_are_all_unknown_never_an_index_error():
    stats = calibrate_mod.statistics(np.array([], dtype="<f4"), np.array([], dtype=np.int64), np.array([]),
                                     {"watcher"}, [], {"watcher": 5.0}, 6.0, 4.0)
    assert stats == dict.fromkeys(STATISTICS)


def test_d22_statistics_of_one_applicable_record_keep_their_values():
    stats = calibrate_mod.statistics(np.array([0.5]), np.array([0]), np.array([0.25]), {"watcher"}, [{"watcher"}],
                                     {"watcher": 5.0}, 6.0, 4.0)
    assert stats == {"s1": 0.5, "gap2": 0.0, "gap5": 0.0, "gap10": 0.0, "z": 0.0, "hub": 0.25, "lex": 1.0}


# --out write-new ------------------------------------------------------------------------------------------------------

def test_d22_the_result_is_published_new_as_lf_bytes_and_no_staging_stays(tmp_path, built):
    sets = [case_set(tmp_path / "set.json", [positive("late", LATE)])]
    out = tmp_path / "deeper" / "calibration.json"
    assert calibrate_mod.main(argv(built, sets, out)) == 0
    result = check_result(out)
    assert result["schema"] == "wd.bridge-mind-hex-step2-calibration.v1" and result["unknown_rows"] == 0
    assert listing(out.parent) == ["calibration.json"]


@pytest.mark.parametrize("existing", ["previous_result", "empty_directory", "file"])
def test_d22_an_existing_output_is_refused_before_any_input_is_read_and_kept(tmp_path, built, monkeypatch, existing):
    sets = [case_set(tmp_path / "set.json", [positive("late", LATE)])]
    out = tmp_path / "calibration.json"
    if existing == "previous_result":
        calibrate_mod.main(argv(built, sets, out))
    elif existing == "empty_directory":
        out.mkdir()
    else:
        out.write_bytes(b"not a result")
    before = out.read_bytes() if out.is_file() else listing(out)
    names = listing(tmp_path)

    def no_read(*args, **kwargs):
        raise AssertionError("an input was read for an output that already exists")

    for module, name in ((corpus_mod, "read_corpus"), (encode_mod, "read_vectors"), (calibrate_mod, "load_cases"),
                         (encode_mod, "E5")):
        monkeypatch.setattr(module, name, no_read)
    with pytest.raises(calibrate_mod.CalibrateError, match=r"\Aoutput_exists\Z"):
        calibrate_mod.main(argv(built, sets, out))
    assert (out.read_bytes() if out.is_file() else listing(out)) == before and listing(tmp_path) == names


class FailingFile:
    """A write handle whose ``site`` step fails with ``error``: a write stores half of its bytes first (a cut write),
    a close fails after the real close, and ``readback`` silently stores one byte less than it reports (a lying
    device). D21's injection, for one file."""

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
    """``site`` fails ONCE with ``error``: creating the output's parent, a write open of the output or of its staging
    file (the handle's write, flush, close or byte count for those sites), os.fsync, or the link onto the output.
    Returns the sites reached, in order."""
    reached = []
    real_mkdir, real_open, real_fsync, real_link = Path.mkdir, Path.open, os.fsync, os.link
    staged = staging_pattern(out)

    def once(name):
        if site == name and name not in reached:
            reached.append(name)
            return True
        return False

    def mkdir(self, *args, **kwargs):
        if Path(self) == out.parent and once("parent"):
            raise error
        return real_mkdir(self, *args, **kwargs)

    def open_(self, mode="r", *args, **kwargs):
        path = Path(self)
        inside = path == out or (path.parent == out.parent and staged.fullmatch(path.name))
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

    def link(source, target, *args, **kwargs):
        if Path(target) == out and once("link"):
            raise error
        return real_link(source, target, *args, **kwargs)

    patch.setattr(Path, "mkdir", mkdir)
    patch.setattr(Path, "open", open_)
    patch.setattr(os, "fsync", fsync)
    patch.setattr(os, "link", link)
    return reached


@pytest.mark.parametrize("site", SITES)
def test_d22_an_error_at_any_write_step_publishes_nothing_and_leaves_no_staging(tmp_path, built, monkeypatch, site):
    sets = [case_set(tmp_path / "set.json", [positive("late", LATE)])]
    out = tmp_path / "deeper" / "calibration.json"
    with monkeypatch.context() as patch:
        reached = inject(patch, out, site, OSError(errno.EIO, f"injected {site} failure"))
        with pytest.raises(calibrate_mod.CalibrateError, match=r"\Aoutput_unwritten\Z"):
            calibrate_mod.main(argv(built, sets, out))
    assert reached == [site]
    assert not os.path.lexists(out) and listing(out.parent) == []  # no output and no staging file
    assert calibrate_mod.main(argv(built, sets, out)) == 0  # control: the same run publishes
    check_result(out)


@pytest.mark.parametrize("failure", ["native", "posix_eperm"])
def test_d22_an_output_another_writer_makes_before_the_link_is_refused_and_kept(tmp_path, built, monkeypatch,
                                                                                 failure):
    """The name is taken between the check and the link: the link never replaces it, and whatever errno the platform
    gives (natively FileExistsError) the refusal is output_exists (D21.1's rule)."""
    sets = [case_set(tmp_path / "set.json", [positive("late", LATE)])]
    out = tmp_path / "calibration.json"
    real_link = os.link

    def link(source, target, *args, **kwargs):
        Path(target).write_bytes(b"another writer")
        if failure == "native":
            return real_link(source, target, *args, **kwargs)
        raise OSError(errno.EPERM, "simulated")

    monkeypatch.setattr(os, "link", link)
    with pytest.raises(calibrate_mod.CalibrateError, match=r"\Aoutput_exists\Z"):
        calibrate_mod.main(argv(built, sets, out))
    assert out.read_bytes() == b"another writer" and listing(tmp_path) == sorted(["calibration.json", "in",
                                                                                 "set.json"])


def test_d22_a_file_already_at_the_staging_name_is_never_used_or_removed(tmp_path, built, monkeypatch):
    sets = [case_set(tmp_path / "set.json", [positive("late", LATE)])]
    out = tmp_path / "calibration.json"
    monkeypatch.setattr(calibrate_mod.secrets, "token_hex", lambda size: "0" * (2 * size))
    staging = tmp_path / ".calibration.json.staging-0000000000000000"
    staging.write_bytes(b"someone else")
    with pytest.raises(calibrate_mod.CalibrateError, match=r"\Aoutput_unwritten\Z"):
        calibrate_mod.main(argv(built, sets, out))
    assert not os.path.lexists(out) and staging.read_bytes() == b"someone else"


@pytest.mark.parametrize("outcome", ["published", "refused"])
def test_d22_a_cleanup_that_fails_never_changes_the_outcome(tmp_path, built, monkeypatch, outcome):
    """Removing the staging name fails: a published result stays published and whole, a refusal stays the same fixed
    refusal; only the staging leftover remains."""
    sets = [case_set(tmp_path / "set.json", [positive("late", LATE)])]
    out = tmp_path / "calibration.json"
    real_unlink, staged = Path.unlink, staging_pattern(out)

    def unlink(self, *args, **kwargs):
        if staged.fullmatch(Path(self).name):
            raise OSError(errno.EACCES, "injected cleanup failure")
        return real_unlink(self, *args, **kwargs)

    def link(source, target, *args, **kwargs):
        raise OSError(errno.EIO, "injected link failure")

    monkeypatch.setattr(Path, "unlink", unlink)
    if outcome == "refused":
        monkeypatch.setattr(os, "link", link)
        with pytest.raises(calibrate_mod.CalibrateError, match=r"\Aoutput_unwritten\Z"):
            calibrate_mod.main(argv(built, sets, out))
        assert not os.path.lexists(out)
    else:
        assert calibrate_mod.main(argv(built, sets, out)) == 0
        check_result(out)
    assert len([name for name in listing(tmp_path) if staged.fullmatch(name)]) == 1


def test_d22_each_publish_stages_under_a_fresh_random_name(tmp_path, built, monkeypatch):
    sets = [case_set(tmp_path / "set.json", [positive("late", LATE)])]
    names, real_open = [], Path.open

    def open_(self, mode="r", *args, **kwargs):
        if "x" in mode:
            names.append(Path(self).name)
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_)
    for folder in ("one", "two"):
        assert calibrate_mod.main(argv(built, sets, tmp_path / folder / "calibration.json")) == 0
    assert len(names) == 2 and names[0] != names[1]
    assert all(staging_pattern(Path("calibration.json")).fullmatch(name) for name in names)


CRASH = """
import os
import sys
from pathlib import Path
here, out, when = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(here))
import calibrate
real_fsync, real_link = os.fsync, os.link

def fsync(fd):
    real_fsync(fd)
    if when == "after_sync":
        os._exit(17)  # a hard crash: no except, no finally, no cleanup

def link(source, target, *args, **kwargs):
    real_link(source, target, *args, **kwargs)
    os._exit(17)

os.fsync, os.link = fsync, link
calibrate.write_new(out, bytes(range(256)) * 512)
"""


@pytest.mark.parametrize("when", ["after_sync", "after_link"])
def test_d22_a_hard_crash_never_leaves_a_cut_result_under_the_output_name(tmp_path, when):
    """The process dies after the staged file's sync or just after the link: the output is absent or whole, never
    cut; a staging leftover is never touched and never blocks a later publish."""
    data = bytes(range(256)) * 512
    out = tmp_path / "deeper" / "calibration.json"
    run = subprocess.run([sys.executable, "-B", "-c", CRASH, str(HERE), str(out), when], capture_output=True,
                         text=True)
    assert run.returncode == 17, run.stderr[-2000:]
    [staged] = [name for name in listing(out.parent) if name != out.name]
    assert staging_pattern(out).fullmatch(staged) and (out.parent / staged).read_bytes() == data
    if when == "after_link":
        assert out.read_bytes() == data
        with pytest.raises(calibrate_mod.CalibrateError, match=r"\Aoutput_exists\Z"):
            calibrate_mod.write_new(out, b"later")
        return
    assert not os.path.lexists(out)
    calibrate_mod.write_new(out, b"later")
    assert out.read_bytes() == b"later" and listing(out.parent) == sorted(["calibration.json", staged])


# D23: an unusable before time -----------------------------------------------------------------------------------------

BAD_BEFORE = {"missing": None, "malformed": "not a time", "timezone_less": "2026-10-13T12:00:00",
              "date_only": "2026-10-13", "number": 20261013, "out_of_range": "0001-01-01T00:00:00+01:00"}
POSITIVE_FIELDS = ("expected_in_corpus", "missing_expected", "best_rank", "best_expected_score")


@pytest.mark.parametrize("label", list(BAD_BEFORE))
def test_d23_a_positive_case_with_an_unusable_before_time_is_an_unknown_row(tmp_path, built, capsys, label):
    bad = positive("bad", BAD_BEFORE[label])
    if label == "missing":
        del bad["before"]
    sets = [case_set(tmp_path / "set.json", [positive("late", LATE), bad])]
    out = tmp_path / "calibration.json"
    assert calibrate_mod.main(argv(built, sets, out)) == 0
    result = check_result(out)
    rows = {row["id"]: row for row in result["rows"]}
    assert sorted(rows) == ["bad", "late"] and result["unknown_rows"] == 1
    row = rows["bad"]
    assert row["applicable"] is None and row["unknown"] == "before_time_invalid" and row["top3"] == []
    assert {key: row[key] for key in STATISTICS} == dict.fromkeys(STATISTICS)
    assert [row[key] for key in POSITIVE_FIELDS] == [None] * 4  # never a made-up time, rank or count
    assert rows["late"]["applicable"] == 4 and FakeE5.queries == [positive("late", LATE)["situation"]]
    lines = {line.split()[1]: line for line in capsys.readouterr().out.splitlines()}
    assert lines["bad"].split()[2:] == ["UNKNOWN", "before_time_invalid"]


def test_d23_a_negative_case_and_a_background_event_with_an_unusable_time_are_unknown_rows(tmp_path, built):
    negative = {"id": "neg", "situation": "a quiet start", "before": "2026-10-13T12:00:00"}
    sets = [case_set(tmp_path / "set.json", [positive("late", LATE)], [negative])]
    background = tmp_path / "background.json"
    background.write_bytes(json.dumps({"events": [
        {"ts_utc": "late-but-not-a-time", "type": "message", "message": "a background situation"},
        {"ts_utc": "2026-10-14T06:00:00Z", "type": "message", "message": "a later background situation"}]}).encode())
    out = tmp_path / "calibration.json"
    assert calibrate_mod.main(argv(built, sets, out) + ["--background", str(background)]) == 0
    result = check_result(out)
    rows = {row["id"]: row for row in result["rows"]}
    assert result["unknown_rows"] == 2 and rows["neg"]["applicable"] is None
    assert rows["neg"]["unknown"] == rows["bg:late-but-not-a-time"]["unknown"] == "before_time_invalid"
    assert rows["bg:2026-10-14T06:00:00Z"]["applicable"] == 4 and "unknown" not in rows["bg:2026-10-14T06:00:00Z"]


def test_d23_no_encoder_is_built_when_no_case_has_an_applicable_record(tmp_path, built):
    sets = [case_set(tmp_path / "set.json", [positive("early", EARLY), positive("earlier", "2025-09-01T00:00:00Z")])]
    out = tmp_path / "calibration.json"
    assert calibrate_mod.main(argv(built, sets, out)) == 0
    assert check_result(out)["unknown_rows"] == 2 and FakeE5.made == 0 and FakeE5.queries == []


def test_d23_the_encoder_is_built_once_at_the_first_applicable_case(tmp_path, built):
    sets = [case_set(tmp_path / "set.json", [positive("early", EARLY), positive("late", LATE),
                                             positive("later", "2026-10-13T13:00:00Z")])]
    out = tmp_path / "calibration.json"
    assert calibrate_mod.main(argv(built, sets, out)) == 0
    assert check_result(out)["unknown_rows"] == 1 and FakeE5.made == 1 and len(FakeE5.queries) == 2
