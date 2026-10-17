"""Tests of the JOIN turn v0.2: one command from a frozen cycle to one lane's card. Synthetic cycles are built from the
step-1 fixtures (the real core writes the snapshot rows); the frozen P1 cycles are used when the sandbox data disk is
present, and there the turn's view must equal the view acceptance 1a wrote for the same cycle. The ordinary error paths
that the Tools static audit of v0.1 (2026-10-16) found untested (G1-G7) are injected at the end, followed by the v0.2
repairs after RCO1's fault probe v2 (P14, P15).

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_join_turn.py -q -p no:cacheprovider
"""

import errno
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _path in (str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import join_card as jc  # noqa: E402
import join_card_delivery as jcd  # noqa: E402
import join_turn as jt  # noqa: E402
import lane_view as lv  # noqa: E402
import lane_view_frozen as fz  # noqa: E402
import test_join_card_delivery as tjd  # noqa: E402  (real core + real lane view fixtures)
import test_lane_view as tlv  # noqa: E402

AS_OF = "2026-10-13T19:00:00Z"
LEARN = "rco2-learn:f80a92c2"
TOOLS = "codex-tools-1"
FOUR = ("view.json", "delivery.json", "card.txt", "turn.json")  # literal: a test never trusts the module's own list
SITUATION = hashlib.sha256(b"a synthetic turn situation").hexdigest()
FORBIDDEN = re.compile(r"(?i)approv|merg")
GATE = re.compile(r"(?i)consensus|changes_requested|rco_pass|auto_merge|rule12|autosign|receipt|merg")
CLOSURE = """
import sys
from pathlib import Path
repo = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(repo), str(repo / "sandbox" / "mind"), str(repo / "sandbox" / "mind" / "step1")]
import join_turn
for module in list(sys.modules.values()):
    path = Path(getattr(module, "__file__", None) or "/").resolve()
    if path.is_relative_to(repo):
        print(path.relative_to(repo).as_posix())
"""
DETERMINISM = """
import sys
from pathlib import Path
here, root = Path(sys.argv[1]), Path(sys.argv[2])
sys.path[:0] = [str(here), str(here / "step1")]
import join_turn as jt
import test_join_turn as t
jt.SANDBOX_ROOT = root.parent
jt.write_turn(root / "turn", jt.turn_case(t.TOOLS, t.synthetic_case(), learn_ref=t.LEARN, envelope=t.envelope()))
"""
CRASH = """
import os
import sys
from pathlib import Path
here, root, flushed = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
sys.path[:0] = [str(here), str(here / "step1")]
import join_turn as jt
import test_join_turn as t
jt.SANDBOX_ROOT = root
real_fsync, calls = os.fsync, []

def fsync_then_crash(fd):
    real_fsync(fd)
    calls.append(fd)
    if len(calls) == flushed:
        os._exit(17)  # a hard crash: no except, no finally, no cleanup

os.fsync = fsync_then_crash
jt.write_turn(root / "turn", jt.turn_case(t.TOOLS, t.synthetic_case(), learn_ref=t.LEARN, envelope=t.envelope()))
"""
STAGING = re.compile(r"\.turn\.staging-[0-9a-f]{16}")
GUARDED ="\\u0050" + "RIVATE" + "_MARKER"  # escaped; the decoded guard reveals it


def synthetic_case(as_of=AS_OF, events=None):
    events = tlv.base_events() if events is None else events
    return {"cycle": "synthetic", "snapshot": tlv.snapshot_of(events, as_of, log_identity=tjd.COMPLETE),
            "github": tlv.github_of(observed=tjd.shift(as_of, minutes=-5)),
            "bodies": {tjd.event_id(row): row for row in events}, "as_of_utc": as_of}


def envelope(as_of=AS_OF, lane=TOOLS):
    answer = {"status": "ANSWER", "abstain": False, "reason": None, "degraded": False, "degraded_cells": [],
              "results": [{"ref": "lesson_a.md", "kind": "lesson", "time_utc": "2026-10-12T00:00:00.000000Z",
                           "summary": "watcher retry reasons narrowed", "contested_with": [], "pair_with": [],
                           "superseded_by": None}], "version": "remember-v2.0", "telemetry": {}}
    return jc.remember_envelope(lane, as_of, SITUATION, answer)


@pytest.fixture
def sandbox_root(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(jt, "SANDBOX_ROOT", root)
    return root


def test_the_turn_is_exactly_the_manual_chain():
    case = synthetic_case()
    result = jt.turn_case(TOOLS, case, learn_ref=LEARN)
    view = lv.lane_view(case["snapshot"], case["github"], case["bodies"], as_of_utc=AS_OF)
    delivery = jcd.deliver(view, jt.NO_RECEIPTS, now_utc=AS_OF)
    assert result["view"] == view and result["delivery"] == delivery
    assert result["card"] == jc.card(TOOLS, delivery, view, None, learn_ref=LEARN)
    assert result["now_utc"] == AS_OF and result["as_of_utc"] == AS_OF and result["receipts_given"] is False


def test_the_turn_reports_the_whole_second_as_of_its_card_is_bound_to():
    result = jt.turn_case(TOOLS, dict(synthetic_case(), as_of_utc="2026-10-13T19:00:00.900000Z"), learn_ref=LEARN)
    assert result["as_of_utc"] == result["view"]["as_of_utc"] == result["now_utc"] == AS_OF
    assert result["card"].startswith(f"JOIN {TOOLS} as_of={AS_OF} now={AS_OF} ")


def test_without_receipts_every_receipt_is_unknown_never_none():
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    [row] = [row for row in result["delivery"]["ledger"] if row["lane"] == TOOLS]
    assert all(row["receipts"][kind] == {"state": "UNKNOWN", "reason": "no_source"} for kind in jcd.KINDS)
    assert "requested=UNKNOWN delivered=UNKNOWN read=UNKNOWN answered=UNKNOWN" in result["card"]


def test_given_receipts_are_used():
    receipts = tjd.receipts([tjd.rrow("requested", "2026-10-13T18:00:00Z")], requested=AS_OF)
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN, receipts=receipts)
    assert result["receipts_given"] is True and "requested=UNKNOWN" not in result["card"]


def test_notified_identities_are_passed_to_hear():
    first = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    [shown] = [row["identity"] for row in first["delivery"]["ledger"] if row["lane"] == TOOLS and row["state"] == "alert"]
    assert "new 1, unchanged 0" in first["card"]
    again = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN, notified=[shown])
    assert "new 0, unchanged 1" in again["card"] and again["delivery"]["notified"] == [shown]


def test_a_later_now_makes_the_card_stale_and_derives_no_step():
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN, now_utc="2026-10-13T19:15:01Z")
    assert result["now_utc"] == "2026-10-13T19:15:01Z" and "last_open_slot=UNKNOWN reason=card_stale" in result["card"]


def test_a_bound_envelope_renders_and_a_stale_one_is_refused():
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN, envelope=envelope())
    assert "REMEMBER [1] lesson_a.md" in result["card"] and "REMEMBER picks [remember-v2.0 answer=" in result["card"]
    with pytest.raises(jc.JoinCardError):
        jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN, envelope=envelope(as_of="2026-10-13T18:00:00Z"))


def test_a_case_that_is_not_a_loaded_cycle_is_refused():
    for bad in ({}, {"snapshot": {}}, None, []):
        with pytest.raises(jt.JoinTurnError):
            jt.turn_case(TOOLS, bad, learn_ref=LEARN)


def test_out_writes_four_guarded_files_with_their_hashes(sandbox_root):
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN, envelope=envelope())
    manifest = jt.write_turn(sandbox_root / "fable-5" / "turn-1", result)
    out = sandbox_root / "fable-5" / "turn-1"
    assert sorted(path.name for path in out.iterdir()) == sorted(FOUR)
    for name in ("view.json", "delivery.json", "card.txt"):
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == manifest["outputs"][name]
    assert (out / "card.txt").read_text(encoding="utf-8") == result["card"]
    assert json.loads((out / "turn.json").read_text(encoding="utf-8")) == manifest
    assert manifest["remember_envelope_sha256"] == lv.sha256_text(lv.canonical(envelope()))
    assert set(manifest["code_sha256"]) == set(jt.CODE) and manifest["authority"] == "none"


def test_two_processes_with_different_hash_seeds_write_the_same_bytes(sandbox_root):
    jt.write_turn(sandbox_root / "in-process" / "turn",
                  jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN, envelope=envelope()))
    for seed in ("1", "2"):
        run = subprocess.run([sys.executable, "-B", "-c", DETERMINISM, str(HERE), str(sandbox_root / seed)],
                             capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed})
        assert run.returncode == 0, run.stderr[-2000:]
    for name in FOUR:
        written = {(sandbox_root / where / "turn" / name).read_bytes() for where in ("in-process", "1", "2")}
        assert len(written) == 1, name


def test_the_code_hashes_cover_exactly_the_import_closure_and_no_gate_code():
    run = subprocess.run([sys.executable, "-B", "-I", "-c", CLOSURE, str(jt.REPO)], capture_output=True, text=True,
                         check=True)
    assert set(run.stdout.split()) == set(jt.CODE)
    assert not [name for name in jt.CODE if GATE.search(name)]
    assert jt.code_sha256() == {name: hashlib.sha256((jt.REPO / name).read_bytes()).hexdigest() for name in jt.CODE}


def test_an_output_directory_that_appears_after_the_check_is_never_replaced(sandbox_root, monkeypatch):
    """v0.1: a competing writer creates the output directory after every check, just before the rename."""
    out, move = sandbox_root / "race", jt._move_into_place

    def compete_then_move(staging, target):
        target.mkdir()
        (target / "delivery.json").write_bytes(b"earlier")
        move(staging, target)

    monkeypatch.setattr(jt, "_move_into_place", compete_then_move)
    with pytest.raises(jt.JoinTurnError, match="already exists"):
        jt.write_turn(out, jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
    assert [path.name for path in out.iterdir()] == ["delivery.json"]
    assert (out / "delivery.json").read_bytes() == b"earlier"
    assert [path.name for path in sandbox_root.iterdir()] == ["race"]  # the staging directory is removed


def test_an_existing_output_directory_or_file_is_refused_before_the_first_byte(sandbox_root, monkeypatch):
    """v0.1: a turn is only written into a new directory, so even an empty one that exists is refused."""
    flushed = []
    monkeypatch.setattr(jt.os, "fsync", flushed.append)
    (sandbox_root / "empty").mkdir()
    (sandbox_root / "a-file").write_bytes(b"earlier")
    for name in ("empty", "a-file"):
        with pytest.raises(jt.JoinTurnError, match="already exists"):
            jt.write_turn(sandbox_root / name, jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
    assert not flushed  # no file was written, not even into a staging directory
    assert sorted(path.name for path in sandbox_root.iterdir()) == ["a-file", "empty"]
    assert not list((sandbox_root / "empty").iterdir()) and (sandbox_root / "a-file").read_bytes() == b"earlier"


def test_any_one_earlier_output_refuses_before_the_first_byte(sandbox_root):
    for name in FOUR:
        out = sandbox_root / f"holds-{name}"
        out.mkdir()
        (out / name).write_bytes(b"earlier")
        with pytest.raises(jt.JoinTurnError, match="already holds a turn"):
            jt.write_turn(out, jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
        assert [path.name for path in out.iterdir()] == [name] and (out / name).read_bytes() == b"earlier"
    assert sorted(path.name for path in sandbox_root.iterdir()) == sorted(f"holds-{name}" for name in FOUR)


def test_written_bytes_are_read_back_against_their_digest(sandbox_root, monkeypatch):
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    monkeypatch.setattr(jt.lv, "sha256_text", lambda text: "0" * 64)  # no written file can hold these digests
    with pytest.raises(jt.JoinTurnError, match="does not hold the bytes written"):
        jt.write_turn(sandbox_root / "readback", result)
    assert not list(sandbox_root.iterdir())  # no output directory, and the staging directory is removed


@pytest.mark.parametrize("flushed", [1, 2, 3, 4])
def test_a_hard_crash_leaves_a_staging_directory_never_a_partial_turn(sandbox_root, flushed):
    """v0.1: the process dies after ``flushed`` of the four files are on disk, before the rename."""
    run = subprocess.run([sys.executable, "-B", "-c", CRASH, str(HERE), str(sandbox_root), str(flushed)],
                         capture_output=True, text=True)
    assert run.returncode == 17, run.stderr[-2000:]
    [staging] = list(sandbox_root.iterdir())
    assert STAGING.fullmatch(staging.name) and not (sandbox_root / "turn").exists()
    assert sorted(path.name for path in staging.iterdir()) == sorted(FOUR[:flushed])
    jt.write_turn(sandbox_root / "turn", jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN, envelope=envelope()))
    assert sorted(path.name for path in (sandbox_root / "turn").iterdir()) == sorted(FOUR)
    assert sorted(path.name for path in staging.iterdir()) == sorted(FOUR[:flushed])  # a leftover is never touched
    for name in FOUR[:flushed]:  # each flushed file holds all its bytes, the same as the complete turn's
        assert (staging / name).read_bytes() == (sandbox_root / "turn" / name).read_bytes(), name


@pytest.mark.parametrize("foreign", [False, True])
def test_an_error_while_writing_removes_only_what_the_turn_wrote(sandbox_root, monkeypatch, foreign):
    """The second file's flush fails (as on a full disk): the turn's own files and its staging directory go; a file
    that another writer put into the staging directory stays, and so does the directory then."""
    fsync, calls = jt.os.fsync, []

    def fsync_then_fail(fd):
        fsync(fd)
        calls.append(fd)
        if len(calls) == 2:
            if foreign:
                [staging] = list(sandbox_root.iterdir())
                (staging / "foreign.txt").write_bytes(b"not the turn's")
            raise OSError(28, "no space left on the device")

    monkeypatch.setattr(jt.os, "fsync", fsync_then_fail)
    with pytest.raises(OSError, match="no space left"):
        jt.write_turn(sandbox_root / "turn", jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
    left = list(sandbox_root.iterdir())
    if not foreign:
        assert not left
    else:
        [staging] = left
        assert STAGING.fullmatch(staging.name) and [path.name for path in staging.iterdir()] == ["foreign.txt"]
        assert (staging / "foreign.txt").read_bytes() == b"not the turn's"  # G7: its bytes stay too, not only its name


@pytest.mark.parametrize("error, reason", [
    (FileNotFoundError(errno.ENOENT, "the parent is gone"), "could not be moved into its output directory"),
    (OSError(errno.ENOTEMPTY, "a POSIX target holds files"), "already exists"),
    (NotADirectoryError(errno.ENOTDIR, "a POSIX target is a file"), "already exists")])
def test_a_rename_error_that_is_not_a_block_is_never_retried(sandbox_root, monkeypatch, error, reason):
    sleeps = []

    def failing_rename(source, target):
        raise error

    monkeypatch.setattr(jt.os, "rename", failing_rename)
    monkeypatch.setattr(jt.time, "sleep", sleeps.append)
    with pytest.raises(jt.JoinTurnError, match=reason):
        jt.write_turn(sandbox_root / "turn", jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
    assert sleeps == [] and not list(sandbox_root.iterdir())


def test_a_blocked_rename_is_retried_briefly_then_refused_without_a_trace(sandbox_root, monkeypatch):
    rename, sleeps, blocked = jt.os.rename, [], [2]

    def blocked_rename(source, target):
        if blocked[0]:
            blocked[0] -= 1
            raise PermissionError(13, "an open file blocks the rename")
        rename(source, target)

    monkeypatch.setattr(jt.os, "rename", blocked_rename)
    monkeypatch.setattr(jt.time, "sleep", sleeps.append)
    jt.write_turn(sandbox_root / "retried", jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
    assert sorted(path.name for path in (sandbox_root / "retried").iterdir()) == sorted(FOUR)
    assert sleeps == [0.05, 0.1]
    blocked[0], sleeps[:] = 10 ** 6, []
    with pytest.raises(jt.JoinTurnError, match="could not be moved into its output directory"):
        jt.write_turn(sandbox_root / "blocked", jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
    assert sleeps == [0.05, 0.1, 0.2, 0.4, 0.8] and len(sleeps) == jt.RENAME_TRIES - 1
    assert [path.name for path in sandbox_root.iterdir()] == ["retried"]  # the staging directory is removed


@pytest.mark.skipif(os.name != "nt", reason="an open file blocks a directory rename on Windows")
def test_a_real_open_file_in_the_staging_directory_delays_the_rename_until_it_is_closed(sandbox_root, monkeypatch):
    """No mocked rename: a reader holds the first staged file open, as a scanner would, until the first wait."""
    fsync, held, sleeps = jt.os.fsync, [], []

    def fsync_and_hold(fd):
        fsync(fd)
        if not held and not sleeps:
            [staging] = list(sandbox_root.iterdir())
            held.append((staging / "view.json").open("rb"))

    def wait(seconds):
        sleeps.append(seconds)
        if held:
            held.pop().close()

    monkeypatch.setattr(jt.os, "fsync", fsync_and_hold)
    monkeypatch.setattr(jt.time, "sleep", wait)
    jt.write_turn(sandbox_root / "held", jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
    assert sleeps == [0.05] and not held
    assert [path.name for path in sandbox_root.iterdir()] == ["held"]
    assert sorted(path.name for path in (sandbox_root / "held").iterdir()) == sorted(FOUR)


def test_out_outside_the_sandbox_root_writes_nothing(sandbox_root, tmp_path):
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    for out in (tmp_path / "elsewhere", sandbox_root, sandbox_root / ".." / "escape"):
        with pytest.raises(jt.JoinTurnError):
            jt.write_turn(out, result)
    assert not (tmp_path / "elsewhere").exists() and not (tmp_path / "escape").exists()


def test_an_earlier_turn_is_never_overwritten(sandbox_root):
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    jt.write_turn(sandbox_root / "turn", result)
    before = {path.name: path.read_bytes() for path in (sandbox_root / "turn").iterdir()}
    with pytest.raises(jt.JoinTurnError):
        jt.write_turn(sandbox_root / "turn", jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN, envelope=envelope()))
    assert {path.name: path.read_bytes() for path in (sandbox_root / "turn").iterdir()} == before


def test_a_guarded_output_writes_nothing(sandbox_root):
    result = dict(jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN), card=f"JOIN {GUARDED}\n")
    with pytest.raises(jt.JoinTurnError):
        jt.write_turn(sandbox_root / "guarded", result)
    assert not list(sandbox_root.iterdir())  # not even a staging directory


def test_cli_prints_the_card_and_refuses_with_fixed_reasons(monkeypatch, sandbox_root, capsys):
    files = {"publication.json": "a" * 64, "context.json": "b" * 64}
    frozen = {"cycles": [dict(synthetic_case(), files=files, nulls=["capture.json"])], "manifest_sha256": "c" * 64}
    monkeypatch.setattr(jt.fz, "load_frozen", lambda root: frozen)
    args = ["--lane", TOOLS, "--cycle", "synthetic", "--learn-ref", LEARN]
    strict = sandbox_root / "duplicate-key.json"
    strict.write_bytes(b'{"schema": 1, "schema": 2}')  # strict JSON refuses a duplicate key
    (sandbox_root / "empty").mkdir()
    assert jt.main(args + ["--out", str(sandbox_root / "cli")]) == 0
    printed = capsys.readouterr().out
    assert printed == jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)["card"]
    assert (sandbox_root / "cli" / "card.txt").read_text(encoding="utf-8") == printed
    inputs = json.loads((sandbox_root / "cli" / "turn.json").read_text(encoding="utf-8"))["inputs"]
    assert inputs == {"frozen_root": str(fz.FROZEN_ROOT), "manifest_sha256": "c" * 64, "cycle_files": files,
                      "cycle_nulls": ["capture.json"]}
    for bad, reason in ((["--lane", TOOLS, "--cycle", "other", "--learn-ref", LEARN], "the cycle is not exactly once"),
                        (["--lane", "nobody", "--cycle", "synthetic", "--learn-ref", LEARN], "the lane is not a fleet"),
                        (args + ["--now", "yesterday"], "an explicit now_utc is required"),
                        (args + ["--out", str(sandbox_root / "cli")], "the output directory already holds a turn"),
                        (args + ["--out", str(sandbox_root / "empty")], "the output directory already exists"),
                        (args + ["--out", str(sandbox_root.parent / "outside")], "the output directory is not under"),
                        (args + ["--receipts", str(sandbox_root / "missing.json")], "a file could not be read"),
                        (args + ["--remember", str(strict)], "a file could not be read")):
        assert jt.main(bad) == 2
        assert capsys.readouterr().out.startswith(f"refused: {reason}")


def test_a_cycle_named_twice_in_the_manifest_is_refused(monkeypatch, capsys):
    twice = dict(synthetic_case(), files={}, nulls=[])
    monkeypatch.setattr(jt.fz, "load_frozen", lambda root: {"cycles": [twice, twice], "manifest_sha256": "c" * 64})
    assert jt.main(["--lane", TOOLS, "--cycle", "synthetic", "--learn-ref", LEARN]) == 2
    assert capsys.readouterr().out.splitlines() == ["refused: the cycle is not exactly once in the frozen manifest"]


def _frozen_cli(monkeypatch):
    frozen = {"cycles": [dict(synthetic_case(), files={}, nulls=[])], "manifest_sha256": "c" * 64}
    monkeypatch.setattr(jt.fz, "load_frozen", lambda root: frozen)
    return ["--lane", TOOLS, "--cycle", "synthetic", "--learn-ref", LEARN]


def _deep_envelope_text(depth):
    """A canonical turn-bound envelope whose answer telemetry holds ``depth`` nested lists, its digest consistent."""
    answer = dict(envelope()["answer"], telemetry={"deep": "@@DEEP@@"})
    answer_text = lv.canonical(answer).replace('"@@DEEP@@"', "[" * depth + "]" * depth)
    outer = dict(envelope(), answer="@@ANSWER@@", answer_sha256=lv.sha256_text(answer_text))
    return lv.canonical(outer).replace('"@@ANSWER@@"', answer_text)


@pytest.mark.parametrize("option", ["--receipts", "--notified", "--remember"])
def test_a_cli_file_nested_too_deeply_is_a_fixed_refusal_never_a_raw_error(monkeypatch, sandbox_root, capsys, option):
    """Residual of v1.2: a file too deep for the strict reader raised a raw RecursionError out of main."""
    deep = sandbox_root / "deep.json"
    deep.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    out = sandbox_root / "deep-turn"
    assert jt.main(_frozen_cli(monkeypatch) + [option, str(deep), "--out", str(out)]) == 2
    assert capsys.readouterr().out.splitlines() == ["refused: a file could not be read or written"]
    assert not out.exists()


def test_an_envelope_nested_at_the_reader_limit_gives_a_whole_turn_or_a_fixed_refusal(monkeypatch, sandbox_root,
                                                                                       capsys):
    """Residual of v1.2: around this interpreter's JSON nesting limit (found here, never assumed) a turn either writes
    all four files, bound to the envelope's digest, or refuses with a fixed reason and writes nothing."""
    args = _frozen_cli(monkeypatch)

    def loads(depth):
        try:
            json.loads(_deep_envelope_text(depth))
        except RecursionError:
            return False
        return True

    low, high = 1, 100000
    while low < high:  # the deepest telemetry the reader still loads
        middle = (low + high + 1) // 2
        low, high = (middle, high) if loads(middle) else (low, middle - 1)
    outcomes = set()
    for depth in range(low - 2, low + 3):
        text = _deep_envelope_text(depth)
        path = sandbox_root / f"envelope-{depth}.json"
        path.write_text(text, encoding="utf-8")
        out = sandbox_root / f"turn-{depth}"
        code = jt.main(args + ["--remember", str(path), "--out", str(out)])
        printed = capsys.readouterr().out
        outcomes.add(code)
        if code == 0:
            assert sorted(item.name for item in out.iterdir()) == sorted(FOUR)
            manifest = json.loads((out / "turn.json").read_text(encoding="utf-8"))
            assert manifest["remember_envelope_sha256"] == lv.sha256_text(text)
            assert (out / "card.txt").read_text(encoding="utf-8") == printed
        else:
            assert code == 2 and printed.splitlines() == ["refused: a file could not be read or written"]
            assert not out.exists()
    assert outcomes == {0, 2}  # the window spans the limit: both sides ran


def test_synthetic_frozen_cycles_give_the_acceptance_1a_view_and_bounded_cards_for_every_lane(tmp_path):
    """The reference test over the real P1 copy and its stored views, on a synthetic frozen root through the real
    loader instead: each cycle's turn view must equal the acceptance-1a view of that cycle. No data disk is read."""
    import run_acceptance_1a as acc
    frozen = fz.load_frozen(tlv.write_frozen_root(tmp_path / "frozen", tlv.synthetic_cycles()))
    assert frozen["cycles"]
    for case in frozen["cycles"]:
        stored = acc.run_view(case)
        for lane in lv.LANES:
            result = jt.turn_case(lane, case, learn_ref=LEARN)
            assert result["view"] == stored
            text = result["card"]
            assert len(text.encode()) <= jc.CARD_BYTES and not jc.ws._has_marker(text)
            assert not any(FORBIDDEN.search(row) for row in text.splitlines())
            if stored["coverage"] != "complete":
                assert "last_open_slot=yes" not in text


# --- the ordinary error paths of the Tools static audit of v0.1 (2026-10-16): G1-G7 ----------------------------------
# Each failure is injected only into the turn's own staging directory; the real file system does the rest. Each test
# asserts the error the caller gets, what is left on disk and which files the turn tried to remove.

SITES = ("open", "write", "flush", "fsync", "close")


def _staged(path):
    """Whether ``path`` names an entry of a turn's staging directory."""
    return bool(STAGING.fullmatch(Path(path).parent.name))


class _FailingFile:
    """A real staged file whose write (after half its bytes), flush or close (after the real close) fails, as on a
    full or failing disk."""

    def __init__(self, handle, site, error):
        self._handle, self._site, self._error = handle, site, error

    def __enter__(self):
        return self

    def __exit__(self, kind, value, trace):
        self._handle.close()
        if self._site == "close" and kind is None:
            raise self._error
        return False

    def write(self, data):
        if self._site == "write":
            self._handle.write(data[:len(data) // 2])
            raise self._error
        return self._handle.write(data)

    def flush(self):
        if self._site == "flush":
            raise self._error
        self._handle.flush()

    def fileno(self):
        return self._handle.fileno()


def _inject(monkeypatch, site, position, error):
    """The ``position``-th file the turn creates (1 to 4, in its own order) fails at ``site`` with ``error``. Returns
    the names the turn created and the staged names it then tried to remove, both in order."""
    created, removed, synced = [], [], []
    real_open, real_unlink, real_fsync = Path.open, Path.unlink, jt.os.fsync

    def open_(self, mode="r", *args, **kwargs):
        if mode != "xb" or not _staged(self):
            return real_open(self, mode, *args, **kwargs)
        if site == "open" and len(created) + 1 == position:
            raise error
        handle = real_open(self, mode, *args, **kwargs)
        created.append(self.name)
        return _FailingFile(handle, site if len(created) == position else None, error)

    def unlink_(self, *args, **kwargs):
        if _staged(self):
            removed.append(self.name)
        return real_unlink(self, *args, **kwargs)

    def fsync_(fd):  # fails instead of the real fsync: that file is never fsynced (not a disk measurement)
        synced.append(fd)
        if site == "fsync" and len(synced) == position:
            raise error
        real_fsync(fd)

    monkeypatch.setattr(Path, "open", open_)
    monkeypatch.setattr(Path, "unlink", unlink_)
    monkeypatch.setattr(jt.os, "fsync", fsync_)
    return created, removed


@pytest.mark.parametrize("position", [1, 2, 3, 4])
@pytest.mark.parametrize("site", SITES)
def test_g2_g3_an_error_at_any_step_of_any_file_reaches_the_caller_and_leaves_nothing(sandbox_root, monkeypatch,
                                                                                    site, position):
    """G2 and G3: the exclusive open, a partial write, the flush, an fsync before success or the close fails, at each
    of the four files. The caller gets that same error, every file the turn created is removed and only those, and
    neither the output nor the staging directory is left."""
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    error = OSError(errno.EIO, f"injected {site} failure")
    created, removed = _inject(monkeypatch, site, position, error)
    with pytest.raises(OSError) as caught:
        jt.write_turn(sandbox_root / "turn", result)
    assert caught.value is error
    assert created == list(FOUR[:position - 1] if site == "open" else FOUR[:position]) and removed == created
    assert not list(sandbox_root.iterdir())


@pytest.mark.parametrize("site", SITES)
def test_g2_the_cli_refuses_every_write_step_error_with_its_fixed_reason(monkeypatch, sandbox_root, capsys, site):
    args = _frozen_cli(monkeypatch) + ["--out", str(sandbox_root / "turn")]
    _inject(monkeypatch, site, 2, OSError(errno.EIO, f"injected {site} failure"))
    assert jt.main(args) == 2
    assert capsys.readouterr().out.splitlines() == ["refused: a file could not be read or written"]
    assert not list(sandbox_root.iterdir())


def test_g2_g7_a_foreign_file_at_a_staged_name_is_never_removed_or_overwritten(sandbox_root, monkeypatch):
    """G2 and G7: another writer puts delivery.json into the staging directory while the turn writes view.json. The
    turn's exclusive create of delivery.json fails, the turn removes only its own view.json, and the foreign file
    keeps its bytes (so the staging directory stays)."""
    fsync, foreign = jt.os.fsync, b"another writer's delivery"

    def fsync_then_compete(fd):
        fsync(fd)
        [staging] = list(sandbox_root.iterdir())
        if not (staging / "delivery.json").exists():
            (staging / "delivery.json").write_bytes(foreign)

    monkeypatch.setattr(jt.os, "fsync", fsync_then_compete)
    with pytest.raises(FileExistsError):
        jt.write_turn(sandbox_root / "turn", jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
    [staging] = list(sandbox_root.iterdir())
    assert STAGING.fullmatch(staging.name)
    assert {path.name: path.read_bytes() for path in staging.iterdir()} == {"delivery.json": foreign}


@pytest.mark.parametrize("held", [False, True])
def test_g1_a_staging_name_that_exists_is_never_used_or_removed(monkeypatch, sandbox_root, capsys, held):
    """G1: a staging-name collision (forced here; 16 random hex digits in practice) fails at the exclusive mkdir,
    before any file is written. The existing directory, empty or not, is left exactly as it was, and the CLI refuses
    with its fixed reason."""
    monkeypatch.setattr(jt.secrets, "token_hex", lambda size: "0" * 2 * size)
    taken = sandbox_root / (".turn.staging-" + "0" * 16)
    taken.mkdir()
    before = {"view.json": b"another writer's view"} if held else {}
    for name, data in before.items():
        (taken / name).write_bytes(data)
    with pytest.raises(FileExistsError):
        jt.write_turn(sandbox_root / "turn", jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
    assert jt.main(_frozen_cli(monkeypatch) + ["--out", str(sandbox_root / "turn")]) == 2
    assert capsys.readouterr().out.splitlines() == ["refused: a file could not be read or written"]
    assert [path.name for path in sandbox_root.iterdir()] == [taken.name]
    assert {path.name: path.read_bytes() for path in taken.iterdir()} == before


def test_g1_an_output_parent_that_cannot_be_created_fails_before_any_staging(sandbox_root):
    """G1: the output's parent is a file, so creating the parent fails before any staging directory or file."""
    (sandbox_root / "a-file").write_bytes(b"earlier")
    with pytest.raises(OSError):
        jt.write_turn(sandbox_root / "a-file" / "turn", jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
    assert [path.name for path in sandbox_root.iterdir()] == ["a-file"]
    assert (sandbox_root / "a-file").read_bytes() == b"earlier"


def test_g1_parents_that_a_refused_turn_created_stay_and_hold_nothing(sandbox_root, monkeypatch):
    """G1: missing parents of the output are created first and stay, empty, when the turn is then refused."""
    def failing_rename(source, target):
        raise FileNotFoundError(errno.ENOENT, "the parent is gone")

    monkeypatch.setattr(jt.os, "rename", failing_rename)
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    with pytest.raises(jt.JoinTurnError, match="could not be moved into its output directory"):
        jt.write_turn(sandbox_root / "new-a" / "new-b" / "turn", result)
    assert [path.name for path in sandbox_root.iterdir()] == ["new-a"]
    assert [path.name for path in (sandbox_root / "new-a").iterdir()] == ["new-b"]
    assert not list((sandbox_root / "new-a" / "new-b").iterdir())


@pytest.mark.parametrize("position", [1, 4])
@pytest.mark.parametrize("fault", ["error", "other bytes"])
def test_g4_a_readback_fault_of_any_file_reaches_the_caller_and_leaves_nothing(sandbox_root, monkeypatch, fault,
                                                                               position):
    """G4: reading back the first or the last file fails, or returns other bytes, injected after the real read. A
    read error reaches the caller as it is, other bytes are the fixed refusal, and nothing is left."""
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    error, read, real = OSError(errno.EIO, "injected read failure"), [], Path.read_bytes

    def read_bytes(self):
        data = real(self)
        if not _staged(self):
            return data
        read.append(self.name)
        if len(read) != position:
            return data
        if fault == "error":
            raise error
        return data + b" "

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    if fault == "error":
        with pytest.raises(OSError) as caught:
            jt.write_turn(sandbox_root / "turn", result)
        assert caught.value is error
    else:
        with pytest.raises(jt.JoinTurnError, match="does not hold the bytes written"):
            jt.write_turn(sandbox_root / "turn", result)
    assert read == list(FOUR[:position]) and not list(sandbox_root.iterdir())


BLOCK = PermissionError(errno.EACCES, "an open file blocks the rename")


@pytest.mark.parametrize("errors, calls, refusal", [
    ((), 1, None),
    ((BLOCK,) * 2, 3, None),
    ((BLOCK,) * 6, 6, "could not be moved into its output directory"),
    ((FileExistsError(errno.EEXIST, "the target exists"),), 1, "already exists"),
    ((FileNotFoundError(errno.ENOENT, "the parent is gone"),), 1, "could not be moved into its output directory"),
    ((OSError(errno.ENOTEMPTY, "a POSIX target holds files"),), 1, "already exists"),
    ((NotADirectoryError(errno.ENOTDIR, "a POSIX target is a file"),), 1, "already exists")])
def test_g5_the_rename_is_called_exactly_as_often_as_its_rule_says(sandbox_root, monkeypatch, errors, calls, refusal):
    """G5: one call when the rename succeeds or fails with anything but a block, one more per block, and six calls
    at most (the literal six, never the module's own constant)."""
    rename, made = jt.os.rename, []

    def counted(source, target):
        made.append((Path(source).name, Path(target).name))
        if len(made) <= len(errors):
            raise errors[len(made) - 1]
        rename(source, target)

    monkeypatch.setattr(jt.os, "rename", counted)
    monkeypatch.setattr(jt.time, "sleep", lambda seconds: None)
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    if refusal is None:
        jt.write_turn(sandbox_root / "turn", result)
        assert sorted(path.name for path in (sandbox_root / "turn").iterdir()) == sorted(FOUR)
    else:
        with pytest.raises(jt.JoinTurnError, match=refusal):
            jt.write_turn(sandbox_root / "turn", result)
        assert not list(sandbox_root.iterdir())
    assert len(made) == calls and all(STAGING.fullmatch(source) and target == "turn" for source, target in made)


@pytest.mark.parametrize("failing", ["unlink", "rmdir"])
@pytest.mark.parametrize("origin", ["fsync", "rename"])
def test_g6_a_cleanup_error_never_stops_the_cleanup_or_hides_the_original_error(sandbox_root, monkeypatch, origin,
                                                                                failing):
    """G6: after the third file's fsync fails (or the rename is refused), removing view.json (or the directory) fails
    too. Every other removal is still tried, the cleanup error is swallowed, and the caller gets the original error
    or refusal; the staging directory stays with only what could not be removed (best effort)."""
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    error, synced, real_fsync = OSError(errno.EIO, "injected fsync failure"), [], jt.os.fsync

    def fsync_(fd):
        synced.append(fd)
        if origin == "fsync" and len(synced) == 3:
            raise error
        real_fsync(fd)

    monkeypatch.setattr(jt.os, "fsync", fsync_)
    monkeypatch.setattr(jt.os, "rename", _gone)
    tried = _failing_cleanup(monkeypatch, failing, PermissionError(errno.EACCES, f"injected {failing} failure"))
    with pytest.raises((OSError, jt.JoinTurnError)) as caught:
        jt.write_turn(sandbox_root / "turn", result)
    if origin == "fsync":
        assert caught.value is error
    else:
        assert type(caught.value) is jt.JoinTurnError and "could not be moved" in str(caught.value)
    assert tried == list(FOUR[:3] if origin == "fsync" else FOUR) + ["rmdir"]
    _assert_left(sandbox_root, failing, result)


def _gone(source, target):
    raise FileNotFoundError(errno.ENOENT, "the parent is gone")


def _failing_cleanup(monkeypatch, failing, error):
    """Removing the staged view.json (``failing="unlink"``) or the staging directory (``"rmdir"``) raises ``error``;
    returns the staged removals tried, in order."""
    tried, real_unlink, real_rmdir = [], Path.unlink, Path.rmdir

    def unlink_(self, *args, **kwargs):
        if _staged(self):
            tried.append(self.name)
            if failing == "unlink" and self.name == "view.json":
                raise error
        return real_unlink(self, *args, **kwargs)

    def rmdir_(self):
        if STAGING.fullmatch(self.name):
            tried.append("rmdir")
            if failing == "rmdir":
                raise error
        return real_rmdir(self)

    monkeypatch.setattr(Path, "unlink", unlink_)
    monkeypatch.setattr(Path, "rmdir", rmdir_)
    return tried


def _assert_left(sandbox_root, failing, result):
    """Only the staging directory stays, holding view.json whole if its removal failed, else nothing."""
    [staging] = list(sandbox_root.iterdir())
    left = {path.name: path.read_bytes() for path in staging.iterdir()}
    assert STAGING.fullmatch(staging.name)
    if failing == "unlink":
        assert list(left) == ["view.json"] and json.loads(left["view.json"].decode("utf-8")) == result["view"]
    else:
        assert left == {}


# --- v0.2 after RCO1's fault probe v2 of v0.1 (2026-10-16): P14 and P15 -----------------------------------------------
# Two design observations, not defects of the stated design: a cleanup step that raised an exception other than an
# OSError replaced the caller's refusal and stopped the cleanup midway (P14), and a file that another writer put into
# the staging directory was carried into the completed turn, unlisted in turn.json (P15). My own tests; RCO1's harness
# and case table were not read. Each repair test was red on the prior branch head first.

@pytest.mark.parametrize("failing", ["unlink", "rmdir"])
def test_v02_a_cleanup_step_that_raises_any_ordinary_exception_never_replaces_the_refusal(sandbox_root, monkeypatch,
                                                                                         failing):
    """P14: after a refused rename, removing view.json (or the directory) raises a RuntimeError. Every other removal
    is still tried and the caller gets the refusal; the staging directory stays with what was not removed."""
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    monkeypatch.setattr(jt.os, "rename", _gone)
    tried = _failing_cleanup(monkeypatch, failing, RuntimeError(f"injected {failing} failure"))
    with pytest.raises(jt.JoinTurnError, match="could not be moved into its output directory"):
        jt.write_turn(sandbox_root / "turn", result)
    assert tried == list(FOUR) + ["rmdir"]
    _assert_left(sandbox_root, failing, result)


@pytest.mark.parametrize("route", ["api", "cli"])
def test_v02_a_file_another_writer_puts_into_the_staging_directory_is_refused_never_carried(monkeypatch, sandbox_root,
                                                                                         capsys, route):
    """P15: another writer drops a file into the staging directory once the turn's last file is on disk. The turn is
    refused before the rename, removes only its own four files, and the other file keeps its bytes."""
    args = _frozen_cli(monkeypatch) + ["--out", str(sandbox_root / "turn")]
    fsync, foreign, synced = jt.os.fsync, b"another writer's file", []

    def fsync_then_drop(fd):
        fsync(fd)
        synced.append(fd)
        if len(synced) == 4:
            [staging] = list(sandbox_root.iterdir())
            (staging / "extra.json").write_bytes(foreign)

    monkeypatch.setattr(jt.os, "fsync", fsync_then_drop)
    refusal = "the staging directory holds a file the turn did not write; nothing is written there"
    if route == "api":
        with pytest.raises(jt.JoinTurnError) as caught:
            jt.write_turn(sandbox_root / "turn", jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
        assert str(caught.value) == refusal
    else:
        assert jt.main(args) == 2
        assert capsys.readouterr().out.splitlines() == [f"refused: {refusal}"]
    [staging] = list(sandbox_root.iterdir())
    assert STAGING.fullmatch(staging.name)
    assert {path.name: path.read_bytes() for path in staging.iterdir()} == {"extra.json": foreign}


# --- interrupts (fable-5's own review of v0.2, 2026-10-16) ---------------------------------------------------------
# The cleanup swallows only ordinary exceptions (P14). An interrupt, KeyboardInterrupt or SystemExit, is never
# swallowed: while writing, the cleanup still runs and the interrupt reaches the caller; during the cleanup, it stops
# the cleanup at once. Before these tests, a mutant that narrowed or widened either handler survived every test.

@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_v02_an_interrupt_while_writing_still_cleans_up_and_reaches_the_caller(sandbox_root, monkeypatch, interrupt):
    """An interrupt at the second file's fsync: the caller gets that same interrupt, and the cleanup has still removed
    the turn's own files and its staging directory."""
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    signal = interrupt()
    created, removed = _inject(monkeypatch, "fsync", 2, signal)
    with pytest.raises(interrupt) as caught:
        jt.write_turn(sandbox_root / "turn", result)
    assert caught.value is signal and created == list(FOUR[:2]) and removed == created
    assert not list(sandbox_root.iterdir())


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("failing", ["unlink", "rmdir"])
def test_v02_an_interrupt_during_the_cleanup_is_never_swallowed(sandbox_root, monkeypatch, failing, interrupt):
    """After a refused rename, removing view.json (or the directory) is interrupted. The interrupt reaches the caller
    at once with the refusal as its context, and what was not yet removed stays."""
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    monkeypatch.setattr(jt.os, "rename", _gone)
    signal = interrupt()
    tried = _failing_cleanup(monkeypatch, failing, signal)
    with pytest.raises(interrupt) as caught:
        jt.write_turn(sandbox_root / "turn", result)
    assert caught.value is signal and type(caught.value.__context__) is jt.JoinTurnError
    assert tried == (["view.json"] if failing == "unlink" else list(FOUR) + ["rmdir"])
    [staging] = list(sandbox_root.iterdir())
    assert STAGING.fullmatch(staging.name)
    assert sorted(path.name for path in staging.iterdir()) == (sorted(FOUR) if failing == "unlink" else [])
