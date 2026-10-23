"""Offline cases for the capture producer's B5 artifact budget (Lead Source-GO 2026-10-23, 12 h run): every byte the
producer writes under the live dir in one run is charged against --max-output-bytes BEFORE the write (``write_new``,
``atomic_write`` with its temporary copy, ``journal`` with the line end a cut last line may need), the files the
shadow run writes are counted right after it (``tree_bytes``), and an exhausted budget is reported by one refusal
record drawn from a small reserve, after which the script program ends with STOP_EXIT.

As in test_mind_capture_producer_state.py, the producer's functions, classes and constants are taken from the module
file and executed in a namespace that holds the globals its script program sets; the shadow run and the cycle's
reads are stand-ins, nothing starts and nothing outside tmp_path is written.
"""

import ast
import datetime
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

from tools import bridge_mind_workspace as ws
from tools import wd_bridge_mind as core
from tools import wd_bridge_mind_shadow as shadow

PRODUCER = Path(__file__).resolve().parent / "mind_capture_producer.py"
CONSTANTS = ("READER", "READER_OPTION", "DETAIL_TAIL", "TAIL_CAP", "SEED_MARGIN", "COUNT", "EVENT_REF", "CURSOR",
             "REPORT_TASK", "LANE", "LATEST_SCHEMA", "STATE_SCHEMA", "STATE_KEYS", "ROW_KEYS", "VIEW_SCHEMA",
             "VIEW_KEYS", "LIST_FIELDS", "REFUSAL_SCHEMA", "VIEW_CAP", "GH_TIMEOUT", "GH_BUDGET", "PRUNED",
             "STOP_EXIT", "CYCLE_DIR", "CYCLE_TIME", "LOCK_BYTE", "LIST_LIMIT")
REASON = "output_budget_exhausted"
TREE = ast.parse(PRODUCER.read_text(encoding="utf-8"))


def load(live, room=None):
    """The producer's own definitions over ``live``; ``room`` sets the allowance to the reserve plus that many bytes
    (the bytes a normal write may use)."""
    nodes = [node for node in TREE.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             or (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) in CONSTANTS)]
    space = {"re": re, "json": json, "os": os, "hashlib": hashlib, "time": time, "dt": datetime, "Path": Path,
             "stat": stat, "tempfile": tempfile, "sys": sys, "live": live, "audit_root": live.parent, "core": core,
             "shadow": shadow, "ws": ws, "args": types.SimpleNamespace(keep=2)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(PRODUCER), "exec"), space)
    budget = space["OutputBudget"]
    if room is not None:
        budget.allowance = budget.reserve + room
    return space, budget


def exhausted(space):
    """pytest.raises for the budget refusal: exactly its fixed reason, and a cycle refusal like every other."""
    return pytest.raises(space["BudgetExhausted"], match=f"^{REASON}$")


def main_block():
    return next(node for node in TREE.body if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and getattr(node.test.left, "id", None) == "__name__")


def test_the_default_allowance_is_finite_and_positive_and_is_the_parser_default(tmp_path):
    space, budget = load(tmp_path / "live")
    assert budget.allowance == 268435456 and 0 < budget.reserve < budget.allowance
    assert budget.spent == 0 and budget.exhausted is False
    calls = [node for node in ast.walk(TREE) if isinstance(node, ast.Call)
             and getattr(node.func, "attr", None) == "add_argument" and node.args
             and getattr(node.args[0], "value", None) == "--max-output-bytes"]
    assert len(calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    assert getattr(keywords["type"], "id", None) == "int"
    assert getattr(keywords["default"], "value", None) == budget.allowance


def test_the_script_sets_the_checked_allowance_before_its_first_write():
    body = main_block().body
    checks = [node.lineno for node in ast.walk(main_block()) if isinstance(node, ast.Call)
              and getattr(node.func, "id", None) == "count_problem"]
    sets = [node.lineno for node in body if isinstance(node, ast.Assign)
            and ast.unparse(node.targets[0]) == "OutputBudget.allowance"
            and ast.unparse(node.value) == "args.max_output_bytes"]
    first_write = next(node.lineno for node in body if "live.mkdir(" in ast.unparse(node))
    assert len(checks) == 1 and len(sets) == 1 and checks[0] < sets[0] < first_write


def test_an_allowance_with_no_room_past_the_reserve_is_refused_at_parse_time(tmp_path):
    space, budget = load(tmp_path / "live")

    def problem(value):
        return space["count_problem"](types.SimpleNamespace(window=3000, keep=12, max_output_bytes=value))

    for value in (-1, 0, budget.reserve, 2 ** 40 + 1):
        assert problem(value) == f"--max-output-bytes must be {budget.reserve + 1}..{2 ** 40}", value
    for value in (budget.reserve + 1, budget.allowance, 2 ** 40):
        assert problem(value) is None, value


def test_write_new_refuses_before_creating_a_file_past_the_allowance(tmp_path):
    space, budget = load(tmp_path, room=10)
    space["write_new"](tmp_path / "a", b"x" * 10)  # exactly the room passes
    with exhausted(space):
        space["write_new"](tmp_path / "b", b"y")
    assert not (tmp_path / "b").exists() and budget.spent == 10 and budget.exhausted is True
    assert issubclass(space["BudgetExhausted"], space["CycleRefused"])


def test_atomic_write_charges_its_temporary_copy_and_refuses_before_making_it(tmp_path):
    (tmp_path / "latest.json").write_bytes(b"old")
    space, budget = load(tmp_path, room=5)
    with exhausted(space):
        space["atomic_write"](tmp_path / "latest.json", b"123456")
    assert sorted(os.listdir(tmp_path)) == ["latest.json"] and (tmp_path / "latest.json").read_bytes() == b"old"
    space, budget = load(tmp_path, room=6)
    space["atomic_write"](tmp_path / "latest.json", b"123456")  # the replaced bytes are never credited back
    assert (tmp_path / "latest.json").read_bytes() == b"123456" and budget.spent == 6


def test_every_log_record_is_charged_with_the_line_end_a_cut_last_line_may_need(tmp_path):
    space, budget = load(tmp_path, room=4096)
    record = {"cycle": 1, "status": "prepared"}
    space["journal"](record)
    line = (json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")
    assert (tmp_path / "live-log.jsonl").read_bytes() == line and budget.spent == len(line) + 1


def test_tree_bytes_counts_every_regular_file_below_the_run_dir(tmp_path):
    space, _ = load(tmp_path)
    run = tmp_path / "run"
    assert space["tree_bytes"](run) == 0  # not made: nothing written
    (run / "nested").mkdir(parents=True)
    (run / "board.txt").write_bytes(b"b" * 3)
    (run / "nested" / "receipt.json").write_bytes(b"r" * 4)
    assert space["tree_bytes"](run) == 7


def candidate_space(tmp_path, room, run_files):
    """A namespace whose candidate_cycle reads stand-in rows and runs a stand-in shadow run that writes
    ``run_files`` (name -> bytes) into the cycle's run dir."""
    live = tmp_path / "live"
    (live / "cycles").mkdir(parents=True)
    space, budget = load(live, room=room)
    payloads = {"capture.json": b"c" * 10, "detail-manifest.txt": b"m" * 5, "state.json": b"s" * 20}
    space.update(advance=lambda state, seeded: ([], "cursor", {}, {"new_rows": 0}),
                 state_envelope=lambda generation, cursor, source, rows: {"generation": generation},
                 capture_object=lambda rows: ({"observed_utc": "2026-10-23T00:00:00Z"}, b"m" * 5),
                 snapshot_prs=lambda capture: set(), pr_context=lambda cache_dir, needed=None: (None, {"ms": 0}),
                 prewrite=lambda capture, manifest, context, state: dict(payloads),
                 previous_publication=lambda last_ok: (None, {"status": "none"}))

    def run(capture_path, audit_root, run_dir, context_path=None, previous_path=None):
        run_dir.mkdir()
        for name, data in run_files.items():
            (run_dir / name).write_bytes(data)
        return {"capture": {"sha256": hashlib.sha256(capture_path.read_bytes()).hexdigest(),
                            "observed_utc": "2026-10-23T00:00:00Z"}, "context": None,
                "publication": {"snapshot_sha256": "0" * 64}, "board_text_sha256": "1" * 64, "counts": {},
                "global_question": None}

    space["shadow"] = types.SimpleNamespace(run=run)
    return space, budget, sum(len(data) for data in payloads.values())


RUN_FILES = {"board.txt": b"b" * 30, "reflected.publication.json": b"p" * 40, "receipt.json": b"r" * 50}


def test_a_candidate_is_charged_its_payloads_and_every_file_its_shadow_run_wrote(tmp_path):
    space, budget, payload_bytes = candidate_space(tmp_path, 4096, RUN_FILES)
    committed = space["candidate_cycle"]({}, None, {})
    assert committed["generation"] == 1 and budget.exhausted is False
    assert budget.spent == payload_bytes + sum(len(data) for data in RUN_FILES.values())


def test_a_shadow_run_whose_files_pass_the_allowance_refuses_the_candidate(tmp_path):
    run_bytes = sum(len(data) for data in RUN_FILES.values())
    space, budget, payload_bytes = candidate_space(tmp_path, 35 + run_bytes - 1, RUN_FILES)
    assert payload_bytes == 35
    with exhausted(space):
        space["candidate_cycle"]({}, None, {})
    assert budget.exhausted is True


def test_a_cycle_refused_by_the_budget_keeps_the_pointer_and_last_ok_and_writes_one_record_from_the_reserve(
        tmp_path, capsys):
    live = tmp_path / "live"
    live.mkdir()
    (live / "latest.json").write_bytes(b"prior")
    space, budget = load(live, room=0)  # no room for a normal write: only the reserve is left
    last_ok = {"cycle_dir": "20261023T000000Z"}

    def candidate(rec, given, seeded):
        rec["cycle_dir"] = "20261023T000100Z"
        space["write_new"](live / "capture.json", b"c")
        raise AssertionError("the budget did not refuse the write")

    space["candidate_cycle"] = candidate
    assert space["one_cycle"](1, last_ok, {}) is last_ok
    lines = (live / "live-log.jsonl").read_bytes().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["status"] == "refused" and json.loads(lines[0])["reason"] == REASON
    assert (live / "latest.json").read_bytes() == b"prior" and not (live / "capture.json").exists()
    assert budget.exhausted is True and budget.spent <= budget.allowance
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["reason"] == REASON


def test_a_log_record_past_the_room_refuses_the_commit_with_the_budget_reason(tmp_path, capsys):
    live = tmp_path / "live"
    (live / "cycles").mkdir(parents=True)
    space, budget = load(live, room=5)  # the candidate commits nothing, its prepared record does not fit
    space.update(candidate_cycle=lambda rec, given, seeded: {"cycle_dir": "20261023T000100Z"},
                 prune=lambda cycles_dir, keep, protected: (0, 0),
                 latest_object=lambda committed: {"schema": "stand-in"})
    assert space["one_cycle"](1, None, {}) is None
    lines = (live / "live-log.jsonl").read_bytes().splitlines()
    assert [json.loads(line)["reason"] for line in lines] == [REASON]
    assert not (live / "latest.json").exists() and budget.exhausted is True


def test_after_exhaustion_a_record_past_the_reserve_falls_back_then_ends_the_producer(tmp_path, capsys):
    live = tmp_path / "live"
    live.mkdir()
    space, budget = load(live)
    budget.exhausted, budget.spent = True, budget.allowance - 10  # 10 bytes of the reserve left
    rec = {"cycle": 2, "started_utc": "2026-10-23T00:05:00Z", "reader": space["READER"]}
    with pytest.raises(SystemExit) as ended:
        space["refused"](rec, REASON, time.perf_counter(), None)
    assert ended.value.code == space["STOP_EXIT"]
    assert sorted(os.listdir(live)) == [] and budget.spent == budget.allowance - 10
    assert capsys.readouterr().err == "mind_capture_producer STOP: refusal_unrecordable\n"


def test_the_script_ends_after_the_cycle_that_exhausted_the_budget():
    loop = next(node for node in main_block().body if isinstance(node, ast.For))
    after = [ast.unparse(node) for node in loop.body]
    at = after.index("last_ok = one_cycle(cycle, last_ok, seeded)")
    check = loop.body[at + 1]
    assert isinstance(check, ast.If) and ast.unparse(check.test) == "OutputBudget.exhausted"
    assert ast.unparse(check.body[-1]) == "raise SystemExit(STOP_EXIT)"
