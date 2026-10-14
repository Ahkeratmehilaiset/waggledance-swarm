"""Tests of the JOIN turn v0: one command from a frozen cycle to one lane's card. Synthetic cycles are built from the
step-1 fixtures (the real core writes the snapshot rows); the frozen P1 cycles are used when the sandbox data disk is
present, and there the turn's view must equal the view acceptance 1a wrote for the same cycle.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_join_turn.py -q -p no:cacheprovider
"""

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
GUARDED = "\\u0050" + "RIVATE" + "_MARKER"  # escaped; the decoded guard reveals it


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


def test_a_file_that_appears_after_the_check_is_not_overwritten(sandbox_root, monkeypatch):
    out = sandbox_root / "race"
    out.mkdir()
    (out / "delivery.json").write_bytes(b"earlier")
    monkeypatch.setattr(jt, "OUTPUTS", ())  # the pre-check sees nothing, as if the file appeared after it
    with pytest.raises(jt.JoinTurnError, match="already holds a turn"):
        jt.write_turn(out, jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
    assert (out / "delivery.json").read_bytes() == b"earlier"


def test_any_one_earlier_output_refuses_before_the_first_byte(sandbox_root):
    for name in FOUR:
        out = sandbox_root / f"holds-{name}"
        out.mkdir()
        (out / name).write_bytes(b"earlier")
        with pytest.raises(jt.JoinTurnError, match="already holds a turn"):
            jt.write_turn(out, jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN))
        assert [path.name for path in out.iterdir()] == [name] and (out / name).read_bytes() == b"earlier"


def test_written_bytes_are_read_back_against_their_digest(sandbox_root, monkeypatch):
    result = jt.turn_case(TOOLS, synthetic_case(), learn_ref=LEARN)
    monkeypatch.setattr(jt.lv, "sha256_text", lambda text: "0" * 64)  # no written file can hold these digests
    with pytest.raises(jt.JoinTurnError, match="does not hold the bytes written"):
        jt.write_turn(sandbox_root / "readback", result)


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
    assert not (sandbox_root / "guarded").exists()


def test_cli_prints_the_card_and_refuses_with_fixed_reasons(monkeypatch, sandbox_root, capsys):
    files = {"publication.json": "a" * 64, "context.json": "b" * 64}
    frozen = {"cycles": [dict(synthetic_case(), files=files, nulls=["capture.json"])], "manifest_sha256": "c" * 64}
    monkeypatch.setattr(jt.fz, "load_frozen", lambda root: frozen)
    args = ["--lane", TOOLS, "--cycle", "synthetic", "--learn-ref", LEARN]
    strict = sandbox_root / "duplicate-key.json"
    strict.write_bytes(b'{"schema": 1, "schema": 2}')  # strict JSON refuses a duplicate key
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


@pytest.mark.skipif(not (fz.FROZEN_ROOT.is_dir() and tjd.FROZEN_VIEWS.is_dir()),
                    reason="frozen P1 cycles and acceptance-1a views are on the sandbox data disk")
def test_frozen_p1_cycles_give_the_acceptance_1a_view_and_bounded_cards_for_every_lane():
    frozen = fz.load_frozen(fz.FROZEN_ROOT)
    assert frozen["cycles"]
    for case in frozen["cycles"]:
        stored = json.loads((tjd.FROZEN_VIEWS / f"{case['cycle']}.lane-view.json").read_text(encoding="utf-8"))
        for lane in lv.LANES:
            result = jt.turn_case(lane, case, learn_ref=LEARN)
            assert result["view"] == stored
            text = result["card"]
            assert len(text.encode()) <= jc.CARD_BYTES and not jc.ws._has_marker(text)
            assert not any(FORBIDDEN.search(row) for row in text.splitlines())
            if stored["coverage"] != "complete":
                assert "last_open_slot=yes" not in text
