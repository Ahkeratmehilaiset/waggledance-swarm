"""Offline cases for the capture producer's bridge reader boundary and its count options.

The producer parses its arguments and runs its cycles at import, so these cases execute its own reader constants,
classes and functions, taken from the module file, with a fake process runner as ``subprocess``: nothing starts.
The count options are checked by executing the module's own statements up to its parse-time check with a fake
command line: none of the statements before the check writes or starts a process (asserted), and none after it runs.
The --seed-from read is checked the same way, up to and including that read, the code tree being this one: none
of the statements between the count check and the read writes or starts a process (asserted). So is the
latest.json read, right after the seed read in the same statement, and the live dir's lock, the statement right
after the live dir is made (two starts on one live dir run in this process: its lock is per descriptor).
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
    "non_ascii_digits": ("-ContinuityTail", "\u0663\u0660\u0660"),
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
    # a seed reads with --window + SEED_MARGIN lines, a delta with DETAIL_TAIL; --window is held to the rest
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


def parsed(monkeypatch, *options):
    """The producer's own statements up to and including its parse-time count check, executed with a fake command
    line: the parsed options, or SystemExit. Before parse_args there are only the docstring, imports, literal
    constants, definitions and the parser's own statements: nothing writes or starts a process."""
    body = ast.parse(PRODUCER.read_text(encoding="utf-8")).body
    [at] = [i for i, node in enumerate(body) if ast.unparse(node) == "args = parser.parse_args()"]
    assert ast.unparse(body[at + 1]) == "problem = count_problem(args)" and isinstance(body[at + 2], ast.If)
    for node in body[:at]:
        text = ast.unparse(node)
        assert (isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef))
                or (isinstance(node, (ast.Expr, ast.Assign)) and isinstance(node.value, ast.Constant))
                or text.startswith(("parser = argparse.ArgumentParser(", "parser.add_argument("))), text
    monkeypatch.setattr(sys, "argv", ["mind_capture_producer.py", "--src", "src", "--live-dir", "live", "--bin",
                                      "bin", *options])
    space = {}
    exec(compile(ast.Module(body=body[:at + 3], type_ignores=[]), str(PRODUCER), "exec"), space)
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


# The calls the statements between the count check and the --seed-from read may make: the paths, the code tree's
# sys.path entry, the reader regexes and the charter check. None of them writes or starts a process.
QUIET_CALLS = {"args.src.resolve", "Path", "os.path.abspath", "next", "sys.path.insert", "str", "re.compile",
               "CHARTER_FILE.resolve", "CHARTER_FILE.resolve().is_relative_to"}


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


def started(monkeypatch, tmp_path, run, *options, through="read_seed", lock=False):
    """The producer's own statements up to and including its start reads, executed with a fake command line, ``run``
    as the process runner, a copy of sys.path and this code tree as --src: the namespace, or SystemExit. The reads
    are one statement right before the live directory is made: the module's one read_seed call and (asserted for
    ``through="read_latest"``) its one read_latest call, of the latest.json path assigned in the statement before;
    between the count check and the reads there are only imports, definitions, assignments, the charter assert and
    the sys.path entry, and every call they make when executed is one of QUIET_CALLS (asserted); the statements
    before the count check are those ``parsed`` checks, and no statement after the reads runs. With ``lock`` the
    live dir's mkdir and its lock run too: the module's one lock_live_dir call, of <live-dir>/producer.lock, is the
    statement right after the mkdir and right before the cycles, files-cache and state dirs (asserted); the caller
    closes the namespace's lock."""
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    body = tree.body
    [check] = [i for i, node in enumerate(body) if ast.unparse(node) == "problem = count_problem(args)"]
    [at] = [i for i, node in enumerate(body) if isinstance(node, ast.Try) and "read_seed(" in ast.unparse(node)]
    assert ast.unparse(body[at + 1]) == "live.mkdir(parents=True, exist_ok=True)"
    for node in body[check + 2:at]:
        text = ast.unparse(node)
        assert isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.Assign,
                                 ast.Assert)) or text == "sys.path.insert(0, str(src))", text
        calls = {ast.unparse(call.func) for part in run_when_defined(node) for call in ast.walk(part)
                 if isinstance(call, ast.Call)}
        assert calls <= QUIET_CALLS, (text, calls - QUIET_CALLS)
    for name in {"read_seed", through}:
        reads = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == name]
        assert len(reads) == 1 and any(node is reads[0] for node in ast.walk(body[at])), (name, len(reads))
        if name == "read_latest":
            assert ast.unparse(reads[0]) == "read_latest(latest_file)"
            assert ast.unparse(body[at - 1]) == "latest_file = live / 'latest.json'"
    end = at + 1
    if lock:
        held = body[at + 2]
        assert isinstance(held, ast.Try) and not held.orelse and not held.finalbody and [
            ast.unparse(s) for s in held.body] == ["lock = lock_live_dir(live / 'producer.lock')"], ast.unparse(held)
        assert [(ast.unparse(h.type), [ast.unparse(s) for s in h.body]) for h in held.handlers] == [
            ("LiveDirRefused", ["parser.error(f'live dir refused: {exc}')"])]
        assert ast.unparse(body[at + 3]).startswith("for name in ('cycles', 'files-cache', 'state'):")
        locks = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and ast.unparse(node.func) == "lock_live_dir"]
        assert len(locks) == 1 and any(node is locks[0] for node in ast.walk(held))
        end = at + 3
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", ["mind_capture_producer.py", "--src", str(PRODUCER.parents[2]), "--live-dir",
                                      str(tmp_path / ".codex-audit" / "live"), "--bin", "bin", *options])
    space = {}
    exec(compile(ast.Module(body=body[:end], type_ignores=[]), str(PRODUCER), "exec"), space)
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


def test_every_cycle_hands_the_one_seed_read_to_its_window_read():
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    calls = [ast.unparse(node) for node in ast.walk(tree)
             if isinstance(node, ast.Call) and ast.unparse(node.func) == "advance"]
    assert calls == ["advance(live / 'state', seeded)"]


LAST_OK = {"cycle_dir": "20261016T000000Z", "capture_file": "cycles/20261016T000000Z/capture.json"}
LATEST_REFUSED_AT_START = {  # one latest.json per fixed reason -> the reason; None: a directory in its place
    "a_directory": (None, "latest_unreadable"),
    "not_json": (b"{", "latest_unparseable"),
    # live_mind's schema: its live dir is not this producer's to write or prune in
    "another_tools_latest_json": (json.dumps({"schema": "wd.bridge-mind-live-latest.v1", "cycle": 1}).encode(
        "ascii"), "latest_shape_unexpected"),
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
    put_latest(tmp_path, json.dumps({"schema": "wd.fable5.mind-shadow-live-latest.v1", "last_ok": LAST_OK}).encode(
        "ascii"))
    before, run = tree_of(tmp_path), FakeRun()
    space = started(monkeypatch, tmp_path, run, through="read_latest")
    assert space["last_ok"] == LAST_OK and run.calls == [] and tree_of(tmp_path) == before


def test_without_a_latest_json_a_first_start_holds_no_last_ok_and_writes_nothing(monkeypatch, tmp_path):
    run = FakeRun()
    space = started(monkeypatch, tmp_path, run, through="read_latest")
    assert space["last_ok"] is None and run.calls == [] and list(tmp_path.iterdir()) == []


def test_every_cycle_names_its_dir_and_writes_its_latest_json_through_the_functions_the_start_read_checks():
    # the state cases read the writer's own forms back (new_cycle_dir, latest_object); here the loop is held to them
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    loop = [node for statement in tree.body if not isinstance(statement, (ast.FunctionDef, ast.ClassDef))
            for node in ast.walk(statement)]  # the module's own statements, the cycle loop among them
    assigned = sorted(ast.unparse(node) for node in loop if isinstance(node, ast.Assign)
                      and ast.unparse(node.targets[0]) in ("latest", "cycle_dir"))
    assert assigned == ["cycle_dir = new_cycle_dir(live / 'cycles')", "latest = latest_object(cycle, None, None)",
                        "latest = latest_object(cycle, rec, last_ok)"]
    made = sorted(ast.unparse(node) for node in loop if isinstance(node, ast.Call)
                  and ast.unparse(node.func).endswith(".mkdir"))  # no cycle dir made there but by new_cycle_dir
    assert made == ["(live / name).mkdir(exist_ok=True)", "live.mkdir(parents=True, exist_ok=True)"], made
    uses = sorted(ast.unparse(node) for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and "latest_file" in ast.unparse(node))
    assert uses == ["atomic_write(latest_file, canonical(latest))", "read_latest(latest_file)"], uses


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
