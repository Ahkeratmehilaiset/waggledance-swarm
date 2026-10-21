"""Offline cases for the capture producer's bridge reader boundary, its count options, its start and its imports.

The producer's functions read the module globals its script program sets, so these cases execute its own reader
constants, classes and functions, taken from the module file, with a fake process runner as ``subprocess``: nothing
starts. The program runs only as a script, under the module's one ``if __name__ == "__main__":`` block (``program``).
The count options are checked by executing the program's own statements up to its parse-time check with a fake
command line: none of the statements before the check writes or starts a process (asserted), and none after it runs.
The --seed-from read is checked the same way, up to and including that read, the code tree being this one: none
of the statements between the count check and the read writes or starts a process (asserted). So are the
latest.json read and the fresh-state check, in the same statement as the seed read, and the live dir's lock, the
statement right after the live dir is made (two starts on one live dir run in this process: its lock is per
descriptor). Two cases run a fresh interpreter: an import of the module, which must run, import, write and start
nothing, and a run of the program past the code tree's imports under an import finder that refuses every gate,
classifier, charter and taxonomy module (Lead's delivery rule: no such import in the production shadow path).
Each refusal is compared by its exception's arguments: exactly the fixed reason, no added text (``refused``).
"""

import ast
import contextlib
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

import tools.bridge_compact_view as compact_view

PRODUCER = Path(__file__).resolve().parent / "mind_capture_producer.py"
ROOT = PRODUCER.parents[2]
# the producer's reader constants; READER_OPTION is the per-token check the slot grammar replaced, listed so that
# these cases also run over a producer from before that change
CONSTANTS = ("READER_OPTION", "DETAIL_TAIL", "TAIL_CAP", "SEED_MARGIN", "COUNT", "EVENT_REF", "CURSOR")
REF = "0123456789abcdef" * 4
CURSOR = "bc1.eyJmaWxlX2lkZW50aXR5IjoiYTpiIiwib2Zmc2V0IjoxMn0"
HOSTILE = ["x; Write-Output p", "$(Write-Output p)", "'", "a b", REF.upper(), REF + "0", "-Raw", "-Compact", "",
           "1" * 10, "-5", "bc1.", "bc1.a b", "bc1.a$b", "bc2.abc", 600, None]
MARGIN = 500  # a seed reads each listed row from the last --window + 500 lines (the state cases pin it)


def tool_tail_bound():
    """The compact tool's own --tail bound, read from its argument check in the code tree, never typed."""
    tree = ast.parse(Path(compact_view.__file__).read_text(encoding="utf-8"))
    [check] = [node for node in ast.walk(tree) if isinstance(node, ast.Compare) and len(node.ops) == 2
               and ast.unparse(node.comparators[0]) == "args.tail"]
    assert [type(op) for op in check.ops] == [ast.LtE, ast.LtE]
    return ast.literal_eval(check.left), ast.literal_eval(check.comparators[1])


TOOL_FLOOR, TOOL_TAIL = tool_tail_bound()  # the tool's 0 is its bounded full snapshot, not a count of lines


class FakeRun:
    """Records each would-be process start and starts none."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return types.SimpleNamespace(returncode=0, stdout=b"{}")


class RaisingRun(FakeRun):
    """A start that raises, as a pwsh that times out or cannot be started does."""

    def __init__(self, error):
        super().__init__()
        self.error = error

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        raise self.error


class FakedStr:
    """isinstance() reads __class__, so it takes this for a str; it is none."""

    __class__ = str


def producer_reader(run=None):
    """The producer's own reader constants, classes and functions over a fake process runner: (namespace, run)."""
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             or (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) in CONSTANTS)]
    run = FakeRun() if run is None else run
    space = {"re": re, "json": json, "bin_dir": Path("bin"),
             "subprocess": types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(PRODUCER), "exec"), space)
    return space, run


@contextlib.contextmanager
def refused(error, reason):
    """pytest.raises(error) whose exception's only argument is exactly the fixed reason: a match= search, ^ and $
    included, would also pass the reason with a trailing newline."""
    with pytest.raises(error) as raised:
        yield raised
    assert raised.value.args == (reason,), raised.value.args


@pytest.mark.parametrize("option", HOSTILE, ids=repr)
def test_an_option_outside_the_reader_shapes_starts_no_process(option):
    space, run = producer_reader()
    with refused(space["CaptureRefused"], "reader_option_invalid"):
        space["reader"]("-ContinuityTail", "600", "-EventId", option)
    with refused(space["CaptureRefused"], "reader_option_invalid"):
        space["reader"]("-AfterEvent", option)
    assert run.calls == []


def test_a_cursor_is_held_to_the_compact_tool_bound_of_2048_characters():
    # RCO1 10-16: the installed compact tool refuses a cursor over 2048 characters in all (position_cursor_invalid),
    # after the reader process started; the producer refuses it before any process starts.
    space, run = producer_reader()
    longest = "bc1." + "A" * 2044
    assert space["reader"]("-AfterEvent", longest) == b"{}"
    with refused(space["CaptureRefused"], "reader_option_invalid"):
        space["reader"]("-AfterEvent", longest + "A")
    assert len(run.calls) == 1


def test_the_compact_shapes_reach_the_command_line_unchanged():
    space, run = producer_reader()
    reader = space["reader"]
    assert reader("-AfterEvent", CURSOR) == b"{}"
    assert reader("-ContinuityTail", "3000") == b"{}"
    assert reader("-ContinuityTail", "600", "-EventId", REF) == b"{}"
    assert [argv[:3] for argv in run.calls] == [["pwsh", "-NoProfile", "-Command"]] * 3
    assert [argv[3].split("Read-AgentBridge.ps1' ", 1)[1] for argv in run.calls] == [
        "-Compact -AfterEvent " + CURSOR, "-Compact -ContinuityTail 3000",
        "-Compact -ContinuityTail 600 -EventId " + REF]


SLOT_REFUSED = {  # options outside the three calls the producer makes, slot by slot -> no process starts
    "a_cursor_slot_with_a_count": ("-AfterEvent", "600"),
    "a_cursor_slot_with_a_ref": ("-AfterEvent", REF),
    "a_cursor_slot_with_a_flag": ("-AfterEvent", "-EventId"),
    "a_cursor_then_a_ref": ("-AfterEvent", CURSOR, "-EventId", REF),
    "a_count_slot_with_a_ref": ("-ContinuityTail", REF),
    "a_count_slot_with_a_cursor": ("-ContinuityTail", CURSOR),
    "a_count_slot_with_a_flag": ("-ContinuityTail", "-EventId"),
    "a_zero_count_the_tools_full_snapshot": ("-ContinuityTail", "0"),
    "a_count_above_the_tool_bound": ("-ContinuityTail", str(TOOL_TAIL + 1)),
    "the_widest_count_of_the_token_check": ("-ContinuityTail", "9" * 9),
    "a_leading_zero": ("-ContinuityTail", "0600"),
    "non_ascii_digits": ("-ContinuityTail", "٣٠٠"),
    "a_count_beyond_the_default_int_digit_limit": ("-ContinuityTail", "1" * 4301),
    "a_ref_slot_with_a_count": ("-ContinuityTail", "600", "-EventId", "600"),
    "a_ref_slot_with_a_flag": ("-ContinuityTail", "600", "-EventId", "-AfterEvent"),
    "a_cursor_after_a_count": ("-ContinuityTail", "600", "-AfterEvent", CURSOR),
    "a_wrong_flag_before_a_ref": ("-ContinuityTail", "600", "-AfterEvent", REF),
    "a_raw_flag_before_a_count": ("-Raw", "600"),
    "the_ref_flag_alone": ("-EventId", REF),
    "a_count_first": ("600", "-ContinuityTail"),
    "three_options": ("-ContinuityTail", "600", "-EventId"),
    "five_options": ("-ContinuityTail", "600", "-EventId", REF, REF),
    "one_option": ("-AfterEvent",),
    "no_option": (),
    "a_faked_str_cursor": ("-AfterEvent", FakedStr()),
    "a_faked_str_flag": (FakedStr(), CURSOR),
}


@pytest.mark.parametrize("case", SLOT_REFUSED)
def test_options_outside_the_three_reader_calls_start_no_process(case):
    space, run = producer_reader()
    with refused(space["CaptureRefused"], "reader_option_invalid") as raised:
        space["reader"](*SLOT_REFUSED[case])
    assert type(raised.value) is space["CaptureRefused"] and run.calls == []


SLOT_ACCEPTED = {  # the count slots at their bounds -> one process start, the options unchanged
    "a_listing_of_one_line": ("-ContinuityTail", "1"),
    "a_listing_at_the_tool_bound": ("-ContinuityTail", str(TOOL_TAIL)),
    "a_detail_read_of_one_line": ("-ContinuityTail", "1", "-EventId", REF),
    "a_detail_read_at_the_tool_bound": ("-ContinuityTail", str(TOOL_TAIL), "-EventId", REF),
}


@pytest.mark.parametrize("case", SLOT_ACCEPTED)
def test_a_count_at_its_bound_reaches_the_command_line_unchanged(case):
    space, run = producer_reader()
    assert space["reader"](*SLOT_ACCEPTED[case]) == b"{}"
    assert [argv[3].split("Read-AgentBridge.ps1' ", 1)[1] for argv in run.calls] == [
        "-Compact " + " ".join(SLOT_ACCEPTED[case])]


def test_the_reader_count_bound_is_the_compact_tool_bound():
    space, _ = producer_reader()
    assert (TOOL_FLOOR, space.get("TAIL_CAP")) == (0, TOOL_TAIL)


def test_every_count_the_producer_passes_is_within_that_bound():
    # a seed reads with --window + SEED_MARGIN lines, a delta with DETAIL_TAIL plus the rows listed after its cursor,
    # held to TAIL_CAP; --window is held to the rest
    space, _ = producer_reader()
    assert space.get("SEED_MARGIN") == MARGIN and 1 <= space["DETAIL_TAIL"] <= TOOL_TAIL


STARTS_RAISING = {
    "timed_out": (subprocess.TimeoutExpired(["pwsh"], 300), "compact_reader_timed_out"),
    "not_found": (FileNotFoundError(2, "not found"), "compact_reader_not_started"),
    "not_permitted": (PermissionError(13, "denied"), "compact_reader_not_started"),
}


@pytest.mark.parametrize("case", STARTS_RAISING)
def test_a_reader_that_times_out_or_cannot_start_is_a_fixed_refusal_of_the_cycle(case):
    error, reason = STARTS_RAISING[case]
    space, run = producer_reader(RaisingRun(error))
    with refused(space["CaptureRefused"], reason) as raised:
        space["reader"]("-ContinuityTail", "600")
    assert type(raised.value) is space.get("CycleRefused") and len(run.calls) == 1


def program(tree):
    """The producer's statements in the order a script run executes them: the module's own, then the body of its one
    ``if __name__ == "__main__":`` block, which is its last statement (asserted)."""
    body = tree.body
    guards = [i for i, node in enumerate(body)
              if isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'"]
    assert guards == [len(body) - 1], guards
    return body[:-1] + body[-1].body


def run_when_defined(node):
    """The parts of a module statement that run when it is executed: a whole statement, but of a definition only
    its decorators and defaults, and of a class its bases and keywords and its body (its methods so far as their own
    decorators and defaults)."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [*node.decorator_list, *node.args.defaults, *(value for value in node.args.kw_defaults if value)]
    if isinstance(node, ast.ClassDef):
        return [*node.decorator_list, *node.bases, *node.keywords,
                *(part for statement in node.body for part in run_when_defined(statement))]
    return [node]


def calls_made(node):
    return {ast.unparse(call.func) for part in run_when_defined(node) for call in ast.walk(part)
            if isinstance(call, ast.Call)}


# The calls the statements before the parse may make: the parser's construction, the constants' regexes and key
# sets. None of them writes or starts a process.
PARSE_QUIET_CALLS = {"argparse.ArgumentParser", "__doc__.splitlines", "parser.add_argument", "re.compile",
                     "frozenset"}


def parsed(monkeypatch, *options, invoked="mind_capture_producer.py"):
    """The program's own statements up to and including its parse-time count check, executed with a fake command
    line: the parsed options, or SystemExit. Before parse_args there are only the docstring, imports, definitions,
    constants and the parser's own statements, and every call they make when executed is one of PARSE_QUIET_CALLS
    (asserted): nothing writes or starts a process."""
    statements = program(ast.parse(PRODUCER.read_text(encoding="utf-8")))
    [at] = [i for i, node in enumerate(statements) if ast.unparse(node) == "args = parser.parse_args()"]
    assert ast.unparse(statements[at + 1]) == "problem = count_problem(args)" and isinstance(statements[at + 2], ast.If)
    for node in statements[:at]:
        text = ast.unparse(node)
        assert isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.Assign, ast.Expr)), text
        assert calls_made(node) <= PARSE_QUIET_CALLS, (text, calls_made(node) - PARSE_QUIET_CALLS)
    monkeypatch.setattr(sys, "argv", [invoked, "--src", "src", "--live-dir", "live", "--bin", "bin", *options])
    space = {}
    exec(compile(ast.Module(body=statements[:at + 3], type_ignores=[]), str(PRODUCER), "exec"), space)
    return space["args"]


COUNTS_REFUSED = {  # a count option outside its bound -> refused at parse time (exit 2), the reason on stderr
    "window_zero": (("--window", "0"), f"--window must be 1..{TOOL_TAIL - MARGIN} lines"),
    "window_negative": (("--window", "-1"), f"--window must be 1..{TOOL_TAIL - MARGIN} lines"),
    "window_one_above_the_bound": (("--window", str(TOOL_TAIL - MARGIN + 1)),
                                   f"--window must be 1..{TOOL_TAIL - MARGIN} lines"),
    "window_at_the_tool_bound": (("--window", str(TOOL_TAIL)), f"--window must be 1..{TOOL_TAIL - MARGIN} lines"),
    "keep_zero": (("--keep", "0"), "--keep must be at least 1"),
    "keep_negative": (("--keep", "-1"), "--keep must be at least 1"),
}


@pytest.mark.parametrize("case", COUNTS_REFUSED)
def test_a_count_option_outside_its_bound_is_refused_at_parse_time(monkeypatch, capsys, case):
    options, reason = COUNTS_REFUSED[case]
    with pytest.raises(SystemExit) as raised:
        parsed(monkeypatch, *options)
    err = capsys.readouterr().err  # argparse: the usage, then "<prog>: error: <message>" as the last line
    assert raised.value.code == 2 and err.endswith(f"\nmind_capture_producer.py: error: {reason}\n"), err[-160:]


@pytest.mark.parametrize("invoked", ["mind_capture_producer.py", "__main__.py", "pytest"])
def test_the_error_line_names_the_program_however_the_interpreter_was_invoked(monkeypatch, capsys, invoked):
    # Bot finding 4302420552 (PR1910 at 7623, Lead 10:58Z): from Python 3.14 the default argparse prog follows how the
    # interpreter ran __main__ (under pytest it is not this file name), so the error lines asserted with the file
    # name were not portable. Python 3.14 is not installed here (native run NOT_RUN); this synthetic control varies
    # sys.argv[0], the input of the default up to 3.13, so the lines keep the file name only with an explicit prog.
    with pytest.raises(SystemExit) as raised:
        parsed(monkeypatch, "--keep", "0", invoked=invoked)
    err = capsys.readouterr().err
    assert (raised.value.code, err.split(" ", 2)[:2], err.splitlines()[-1]) == (
        2, ["usage:", "mind_capture_producer.py"], "mind_capture_producer.py: error: --keep must be at least 1")


COUNTS_ACCEPTED = {  # -> (window, keep) as parsed
    "the_defaults": ((), (3000, 12)),
    "a_window_of_one_line": (("--window", "1"), (1, 12)),
    "a_window_at_the_bound": (("--window", str(TOOL_TAIL - MARGIN)), (TOOL_TAIL - MARGIN, 12)),
    "one_kept_cycle": (("--keep", "1"), (3000, 1)),
}


@pytest.mark.parametrize("case", COUNTS_ACCEPTED)
def test_a_count_option_within_its_bound_parses(monkeypatch, case):
    options, expected = COUNTS_ACCEPTED[case]
    args = parsed(monkeypatch, *options)
    assert (args.window, args.keep) == expected


# The calls the statements between the count check and the start reads may make: the paths and the code tree's
# sys.path entry. None of them writes or starts a process.
QUIET_CALLS = {"args.src.resolve", "Path", "os.path.abspath", "next", "sys.path.insert", "str"}
START_READS = ["seeded = {} if args.seed_from is None else read_seed(args.seed_from)",
               "last_ok = read_latest(latest_file)", "refuse_legacy(live)"]
START_REFUSALS = [("SeedRefused", ["parser.error(f'--seed-from refused: {exc}')"]),
                  ("LatestRefused", ["parser.error(f'latest.json refused: {exc}')"]),
                  ("LiveDirRefused", ["parser.error(f'live dir refused: {exc}')"])]


def started(monkeypatch, tmp_path, run, *options, through="read_seed", lock=False):
    """The program's own statements up to and including its start reads, executed with a fake command line, ``run``
    as the process runner, a copy of sys.path and this code tree as --src: the namespace, or SystemExit. The reads
    are one statement right before the live directory is made: the seed read, the read of the latest.json path
    assigned in the statement before and the fresh-state check, each refusal a parser error (asserted); between the
    count check and the reads there are only the code tree's imports, assignments and the sys.path entry, and every
    call they make when executed is one of QUIET_CALLS (asserted); the statements before the count check are those
    ``parsed`` checks, and no statement after the reads runs. With ``lock`` the live dir's mkdir and its lock run
    too: the program's one lock_live_dir call, of <live-dir>/producer.lock, is the statement right after the mkdir
    and right before the cycles and files-cache dirs (asserted); the caller closes the namespace's lock."""
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    statements = program(tree)
    [check] = [i for i, node in enumerate(statements) if ast.unparse(node) == "problem = count_problem(args)"]
    [at] = [i for i, node in enumerate(statements) if isinstance(node, ast.Try) and "read_seed(" in ast.unparse(node)]
    reads = statements[at]
    assert [ast.unparse(s) for s in reads.body] == START_READS and not reads.orelse and not reads.finalbody
    assert [(ast.unparse(h.type), [ast.unparse(s) for s in h.body]) for h in reads.handlers] == START_REFUSALS
    assert ast.unparse(statements[at + 1]) == "live.mkdir(parents=True, exist_ok=True)"
    for node in statements[check + 2:at]:
        text = ast.unparse(node)
        assert isinstance(node, (ast.ImportFrom, ast.Assign)) or text == "sys.path.insert(0, str(src))", text
        assert calls_made(node) <= QUIET_CALLS, (text, calls_made(node) - QUIET_CALLS)
    for name in {"read_seed", through}:
        found = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == name]
        assert len(found) == 1 and any(node is found[0] for node in ast.walk(reads)), (name, len(found))
        if name == "read_latest":
            assert ast.unparse(statements[at - 1]) == "latest_file = live / 'latest.json'"
    end = at + 1
    if lock:
        held = statements[at + 2]
        assert isinstance(held, ast.Try) and not held.orelse and not held.finalbody and [
            ast.unparse(s) for s in held.body] == ["lock = lock_live_dir(live / 'producer.lock')"], ast.unparse(held)
        assert [(ast.unparse(h.type), [ast.unparse(s) for s in h.body]) for h in held.handlers] == [
            ("LiveDirRefused", ["parser.error(f'live dir refused: {exc}')"])]
        assert ast.unparse(statements[at + 3]).startswith("try:\n    for name in ('cycles', 'files-cache'):")
        locks = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and ast.unparse(node.func) == "lock_live_dir"]
        assert len(locks) == 1 and any(node is locks[0] for node in ast.walk(held))
        end = at + 3
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", ["mind_capture_producer.py", "--src", str(ROOT), "--live-dir",
                                      str(tmp_path / ".codex-audit" / "live"), "--bin", "bin", *options])
    space = {}
    exec(compile(ast.Module(body=statements[:end], type_ignores=[]), str(PRODUCER), "exec"), space)
    return space


EVENT = {"agent": "lane-a", "type": "message", "task_id": "lane-a/seed-case", "ts_utc": "2026-10-16T00:00:00Z",
         "message": "seeded"}
SEED_REFUSED_AT_START = {  # one --seed-from file per fixed reason -> the reason; None: no file
    "missing": (None, "seed_unreadable"),
    "not_json": (b"{", "seed_unparseable"),
    "events_null": (b'{"events": null}', "seed_shape_unexpected"),
    "an_event_with_nan": (b'{"events": [{"n": NaN}]}', "seed_ref_unverifiable"),
}


@pytest.mark.parametrize("case", SEED_REFUSED_AT_START)
def test_a_seed_the_producer_cannot_read_whole_exits_2_before_any_write_or_start(monkeypatch, capsys, tmp_path,
                                                                                 case):
    data, reason = SEED_REFUSED_AT_START[case]
    seed = tmp_path / "seed.json"
    if data is not None:
        seed.write_bytes(data)
    run = FakeRun()
    with pytest.raises(SystemExit) as raised:
        started(monkeypatch, tmp_path, run, "--seed-from", str(seed))
    err = capsys.readouterr().err  # argparse: the usage, then "<prog>: error: <message>" as the last line
    assert raised.value.code == 2 and err.endswith(
        f"\nmind_capture_producer.py: error: --seed-from refused: {reason}\n"), err[-160:]
    written = [] if data is None else ["seed.json"]  # the case's own file only
    assert run.calls == [] and sorted(path.name for path in tmp_path.iterdir()) == written


def test_a_seed_read_whole_is_held_before_any_write_or_start(monkeypatch, tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_bytes(json.dumps({"events": [EVENT, EVENT]}).encode("ascii"))
    run = FakeRun()
    space = started(monkeypatch, tmp_path, run, "--seed-from", str(seed))
    assert space["seeded"] == {compact_view.event_id(EVENT): EVENT}
    assert run.calls == [] and [path.name for path in tmp_path.iterdir()] == ["seed.json"]


def test_without_a_seed_no_row_is_held_and_nothing_is_read(monkeypatch, tmp_path):
    run = FakeRun()
    space = started(monkeypatch, tmp_path, run)
    assert space["seeded"] == {} and run.calls == [] and list(tmp_path.iterdir()) == []


def test_every_cycle_hands_the_one_seed_read_to_its_window_read_from_the_committed_state():
    # the seed rows reach the window read through one_cycle and candidate_cycle, and the window read continues from
    # the state latest.json's last_ok names and hashes (read_state), never from a mutable state file
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    calls = {name: sorted(ast.unparse(node) for node in ast.walk(tree)
                          if isinstance(node, ast.Call) and ast.unparse(node.func) == name)
             for name in ("advance", "candidate_cycle", "one_cycle", "read_state")}
    assert calls == {"advance": ["advance(state, seeded)"],
                     "candidate_cycle": ["candidate_cycle(rec, last_ok, seeded)"],
                     "one_cycle": ["one_cycle(cycle, last_ok, seeded)"],
                     "read_state": ["read_state(live / last_ok['state_file'], last_ok['state_sha256'], "
                                    "last_ok['generation'])"]}


CYCLE = "20261016T000000Z"
LAST_OK = {"cycle_dir": CYCLE, "generation": 1, "capture_file": f"cycles/{CYCLE}/capture.json",
           "capture_sha256": "a" * 64, "state_file": f"cycles/{CYCLE}/state.json", "state_sha256": "e" * 64,
           "publication_file": f"cycles/{CYCLE}/run/reflected.publication.json",
           "board_file": f"cycles/{CYCLE}/run/board.txt", "receipt_file": f"cycles/{CYCLE}/run/receipt.json",
           "snapshot_sha256": "c" * 64}
LATEST_REFUSED_AT_START = {  # one latest.json per fixed reason -> the reason; None: a directory in its place
    "a_directory": (None, "latest_unreadable"),
    "not_json": (b"{", "latest_unparseable"),
    # live_mind's schema: its live dir is not this producer's to write or prune in
    "another_tools_latest_json": (json.dumps({"schema": "wd.bridge-mind-live-latest.v1", "cycle": 1}).encode(
        "ascii"), "latest_shape_unexpected"),
    # the earlier window/cursor producer's: fresh production state only, nothing migrated
    "the_earlier_producers_latest_json": (json.dumps({"schema": "wd.fable5.mind-shadow-live-latest.v1",
                                                      "last_ok": LAST_OK}).encode("ascii"), "latest_shape_unexpected"),
}


def tree_of(root):
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def put_latest(tmp_path, data):
    live = tmp_path / ".codex-audit" / "live"
    live.mkdir(parents=True)
    if data is None:
        (live / "latest.json").mkdir()
    else:
        (live / "latest.json").write_bytes(data)


@pytest.mark.parametrize("case", LATEST_REFUSED_AT_START)
def test_a_latest_json_the_producer_cannot_read_whole_exits_2_before_any_write_or_start(monkeypatch, capsys,
                                                                                        tmp_path, case):
    data, reason = LATEST_REFUSED_AT_START[case]
    put_latest(tmp_path, data)
    before, run = tree_of(tmp_path), FakeRun()
    with pytest.raises(SystemExit) as raised:
        started(monkeypatch, tmp_path, run, through="read_latest")
    err = capsys.readouterr().err  # argparse: the usage, then "<prog>: error: <message>" as the last line
    assert raised.value.code == 2 and err.endswith(
        f"\nmind_capture_producer.py: error: latest.json refused: {reason}\n"), err[-160:]
    assert run.calls == [] and tree_of(tmp_path) == before


def test_a_latest_json_read_whole_holds_its_last_ok_before_any_write_or_start(monkeypatch, tmp_path):
    put_latest(tmp_path, json.dumps({"schema": "wd.bridge-mind-shadow-latest.v2", "last_ok": LAST_OK}).encode(
        "ascii"))
    before, run = tree_of(tmp_path), FakeRun()
    space = started(monkeypatch, tmp_path, run, through="read_latest")
    assert space["last_ok"] == LAST_OK and run.calls == [] and tree_of(tmp_path) == before


def test_without_a_latest_json_a_first_start_holds_no_last_ok_and_writes_nothing(monkeypatch, tmp_path):
    run = FakeRun()
    space = started(monkeypatch, tmp_path, run, through="read_latest")
    assert space["last_ok"] is None and run.calls == [] and list(tmp_path.iterdir()) == []


def test_a_live_dir_holding_the_earlier_producers_state_exits_2_before_any_write_or_start(monkeypatch, capsys,
                                                                                         tmp_path):
    state = tmp_path / ".codex-audit" / "live" / "state"
    state.mkdir(parents=True)
    (state / "cursor.txt").write_bytes(b"not read")
    before, run = tree_of(tmp_path), FakeRun()
    with pytest.raises(SystemExit) as raised:
        started(monkeypatch, tmp_path, run, through="read_latest")
    err = capsys.readouterr().err
    assert raised.value.code == 2 and err.endswith(
        "\nmind_capture_producer.py: error: live dir refused: live_dir_legacy_state\n"), err[-160:]
    assert run.calls == [] and tree_of(tmp_path) == before


def test_every_cycle_names_its_dir_and_commits_through_the_one_latest_json_write_the_start_read_checks():
    # the state cases read the writer's own forms back (new_cycle_dir, latest_object, read_latest); here the program
    # is held to them: one cycle dir maker, and latest.json replaced only by one_cycle with latest_object's object
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    every = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    made = sorted(ast.unparse(node) for node in every if ast.unparse(node.func).endswith(".mkdir"))
    assert made == ["(live / name).mkdir(exist_ok=True)", "live.mkdir(parents=True, exist_ok=True)",
                    "path.mkdir()"], made
    assert [ast.unparse(node) for node in every if ast.unparse(node.func) == "new_cycle_dir"] == [
        "new_cycle_dir(live / 'cycles')"]
    replaced = sorted(ast.unparse(node) for node in every if ast.unparse(node.func) == "atomic_write")
    assert replaced == ["atomic_write(cache_dir / f'{number}-{head}.view.v1.json', compact(view))",
                        "atomic_write(live / 'last-refusal.json', canonical(fallback))",
                        "atomic_write(live / 'latest.json', canonical(latest))"], replaced
    [one_cycle] = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "one_cycle"]
    assigned = [ast.unparse(node) for node in ast.walk(one_cycle) if isinstance(node, ast.Assign)
                and ast.unparse(node.targets[0]) == "latest"]
    assert assigned == ["latest = latest_object(committed)"]
    assert [ast.unparse(node) for node in ast.walk(one_cycle) if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "atomic_write"] == ["atomic_write(live / 'latest.json', canonical(latest))"]
    uses = sorted(ast.unparse(node) for node in every if "latest_file" in ast.unparse(node))
    assert uses == ["read_latest(latest_file)"], uses


LOCKED = [".codex-audit", ".codex-audit/live", ".codex-audit/live/producer.lock"]  # a start through its lock


def test_a_first_start_locks_the_live_dir_right_after_making_it_and_holds_the_lock(monkeypatch, tmp_path):
    run = FakeRun()
    space = started(monkeypatch, tmp_path, run, lock=True)
    try:
        assert tree_of(tmp_path) == LOCKED and type(space["lock"]) is int and run.calls == []
    finally:
        os.close(space["lock"])
    os.close(started(monkeypatch, tmp_path, FakeRun(), lock=True)["lock"])  # a restart after it ended locks again


def test_a_live_dir_another_start_holds_exits_2_before_any_other_write_or_start(monkeypatch, capsys, tmp_path):
    first = started(monkeypatch, tmp_path, FakeRun(), lock=True)
    try:
        before, run = tree_of(tmp_path), FakeRun()
        with pytest.raises(SystemExit) as raised:
            started(monkeypatch, tmp_path, run, lock=True)
        err = capsys.readouterr().err  # argparse: the usage, then "<prog>: error: <message>" as the last line
        assert raised.value.code == 2 and err.endswith(
            "\nmind_capture_producer.py: error: live dir refused: live_dir_in_use\n"), err[-160:]
        assert run.calls == [] and tree_of(tmp_path) == before == LOCKED
    finally:
        os.close(first["lock"])


def test_a_lock_file_the_producer_cannot_open_exits_2_before_any_other_write_or_start(monkeypatch, capsys,
                                                                                    tmp_path):
    (tmp_path / ".codex-audit" / "live" / "producer.lock").mkdir(parents=True)  # a directory in its place
    before, run = tree_of(tmp_path), FakeRun()
    with pytest.raises(SystemExit) as raised:
        started(monkeypatch, tmp_path, run, lock=True)
    err = capsys.readouterr().err
    assert raised.value.code == 2 and err.endswith(
        "\nmind_capture_producer.py: error: live dir refused: live_dir_unlockable\n"), err[-160:]
    assert run.calls == [] and tree_of(tmp_path) == before


def fresh(script, *arguments, cwd):
    """``script`` in a fresh isolated interpreter (-I -B) with ``arguments``: the completed process."""
    return subprocess.run([sys.executable, "-I", "-B", "-c", script, *map(str, arguments)], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", timeout=180)


IMPORT_CHILD = """\
import importlib, json, sys
sys.path.insert(0, sys.argv[1])
sys.argv = ["-c"]
before = set(sys.modules)
module = importlib.import_module("sandbox.mind.mind_capture_producer")
print(json.dumps({"file": module.__file__,
                  "loaded": sorted(name for name in set(sys.modules) - before
                                   if name.split(".")[0] in ("tools", "waggledance")),
                  "program_names": sorted(name for name in ("args", "live", "core", "shadow", "ws", "lock", "last_ok")
                                          if hasattr(module, name))}))
"""


def test_importing_the_producer_defines_it_and_runs_imports_writes_and_starts_nothing(tmp_path):
    # the package's import smoke imports it: with the child's own argv (no --src) a parse at import would exit 2,
    # and with a valid one it would run cycles
    done = fresh(IMPORT_CHILD, ROOT, cwd=tmp_path)
    assert done.returncode == 0, done.stderr[-2000:]
    observed = json.loads(done.stdout)
    assert Path(observed["file"]).resolve() == PRODUCER
    assert observed["loaded"] == [] and observed["program_names"] == [] and list(tmp_path.iterdir()) == []


# Lead's delivery rule: no gate, classifier, charter or taxonomy module in the production shadow path. Each name is
# a module of this code tree (asserted, so a misspelt name cannot leave the guard empty); the finder also refuses
# any tools. or waggledance. module whose last name holds one of GATE_WORDS. The core's no-gate closure test names
# the same modules (aligned 07:53Z: the consensus receipt is named here too, not left to the word match).
GATE_MODULES = ("tools.check_bridge_changes_requested", "tools.check_rco_pass_present",
                "tools.idle_consensus_auto_merge", "tools.merge_with_bridge_receipt",
                "tools.write_bridge_consensus_merge_receipt", "tools.bridge_rule12_review_eligibility",
                "tools.bridge_v2_identity_registry", "tools.rule12_grok_ledger_adapter",
                "tools.check_proven_safe_autosign_class", "tools.check_standing_consensus_sign_class",
                "tools.classify_bridge_ci_scope", "tools.bridge_event_taxonomy",
                "waggledance.core.idle_consensus_charter", "waggledance.core.magma.consensus_receipt")
GATE_WORDS = ("consensus", "rco_pass", "changes_requested", "rule12", "merge", "receipt", "autosign", "classif",
              "charter", "standing", "taxonomy")
GUARD_CHILD = """\
import importlib, importlib.abc, json, runpy, sys
root, producer, live, seed = sys.argv[1:5]
denied, words = set(json.loads(sys.argv[5])), tuple(json.loads(sys.argv[6]))


def gate(name):
    return any(name == item or name.startswith(item + ".") for item in denied) or (
        name.split(".")[0] in ("tools", "waggledance") and any(word in name.rsplit(".", 1)[-1] for word in words))


class NoGate(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if gate(fullname):
            raise RuntimeError("producer_gate_import_refused:" + fullname)


sys.meta_path.insert(0, NoGate())
sys.path.insert(0, root)
try:  # the control: the finder refuses a gate module that exists in this code tree
    importlib.import_module("tools.check_rco_pass_present")
except RuntimeError as error:
    assert str(error) == "producer_gate_import_refused:tools.check_rco_pass_present", error
else:
    raise AssertionError("producer_gate_guard_control_failed")
sys.argv = [producer, "--src", root, "--live-dir", live, "--bin", "bin", "--seed-from", seed]
try:
    runpy.run_path(producer, run_name="__main__")
    code = None
except SystemExit as stop:
    code = stop.code
loaded = sorted(name for name in sys.modules if name.split(".")[0] in ("tools", "waggledance"))
print(json.dumps({"code": code, "loaded": loaded, "gate_loaded": [name for name in loaded if gate(name)]}))
"""


def run_guarded(producer, tmp_path):
    """The program of ``producer`` run as a script under the gate-refusing finder, this code tree as --src and a
    --seed-from that does not exist: it parses, imports the code tree's modules, then exits 2 at the seed read,
    before any write or process start."""
    return fresh(GUARD_CHILD, ROOT, producer, tmp_path / ".codex-audit" / "live", tmp_path / "missing-seed.json",
                 json.dumps(GATE_MODULES), json.dumps(GATE_WORDS), cwd=tmp_path)


def test_the_gate_modules_named_are_modules_of_this_code_tree():
    assert [name for name in GATE_MODULES if not (ROOT / (name.replace(".", "/") + ".py")).is_file()] == []


def test_the_producer_run_as_a_script_imports_no_gate_classifier_charter_or_taxonomy_module(tmp_path):
    done = run_guarded(PRODUCER, tmp_path)
    assert done.returncode == 0, done.stderr[-2000:]
    observed = json.loads(done.stdout.splitlines()[-1])
    assert observed["code"] == 2 and done.stderr.endswith(
        "\nmind_capture_producer.py: error: --seed-from refused: seed_unreadable\n"), done.stderr[-300:]
    assert {"tools.wd_bridge_mind", "tools.wd_bridge_mind_shadow", "tools.bridge_mind_workspace",
            "tools.bridge_compact_view", "waggledance.core.bridge_log_reader"} <= set(observed["loaded"])
    assert observed["gate_loaded"] == [] and list(tmp_path.iterdir()) == []


def test_a_producer_that_imports_the_classifier_again_is_refused_by_that_guard(tmp_path):
    # a named mutant: the cp5 producer's classifier import, put back after the code tree's imports
    source = PRODUCER.read_text(encoding="utf-8")
    anchor = "    from waggledance.core.bridge_log_reader import parse_bridge_json_object  # noqa: E402\n"
    assert source.count(anchor) == 1
    mutant = tmp_path / "mutant" / "mind_capture_producer.py"
    mutant.parent.mkdir()
    mutant.write_text(source.replace(anchor, anchor + "    from tools.check_standing_consensus_sign_class import "
                                                      "classify_ab  # noqa: E402\n"), encoding="utf-8")
    done = run_guarded(mutant, tmp_path)
    assert done.returncode != 0
    assert "RuntimeError: producer_gate_import_refused:tools.check_standing_consensus_sign_class" in done.stderr
