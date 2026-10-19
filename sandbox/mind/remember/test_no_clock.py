"""D12: no REMEMBER answer or shortlist holds a clock reading, so one question on one memory gives the same bytes in
every run. Until D12 each shortlist and answer held ``telemetry.ms``, a wall-clock time (``PRIMARY_ONLY.md``, finding
while testing): two identical runs differed in bytes, and so did the digest a JOIN card cites for an answer.

Every engine (v2, v3 with its hop and BRD arms, v2.1, v2.2, v2.3) answers the same questions three times: with every
clock of the ``time`` module replaced by one that moves a second per read, by one that moves an hour per read, and
with the real clocks. The three must be equal byte for byte. A clock bound at import (``from time import ...``) or read
through ``datetime`` would escape that patch, so no module of this directory except a test imports ``time`` or reads
the current time. And two more processes, with other string hash seeds, give the same bytes again: a process id, a
random draw or the order of a set of strings would show there."""

from __future__ import annotations

import ast
import itertools
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import remember_v23 as r23  # noqa: E402  (it loads v2.2, v2.1, v3, v2 and the step-2 modules once)
import test_remember_v22 as t22  # noqa: E402  (its fixtures: topic, build, q4 and the step-2 records)

r22, r21, rv = r23.v22, r23.v21, r23.v2
r3 = r21.v3
WHEN, SITUATION = t22.WHEN, t22.SITUATION
CLOCKS = ("perf_counter", "monotonic", "time", "process_time", "thread_time")
READS_NOW = {"now", "utcnow", "today", "perf_counter", "monotonic", "process_time", "thread_time"} | {
    clock + "_ns" for clock in CLOCKS}  # attribute reads; a plain ``.time`` is left to the import check
PICKS = ({"picks": [1, 2], "contested": [[1, 2]], "nothing_applies": False}, {"picks": [], "nothing_applies": True},
         {"picks": [99], "nothing_applies": False})  # ANSWER, ABSTAIN, REFUSED: each path through _answer
SEEDED = """import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import test_no_clock as t
print(json.dumps({name: t.answered(pathlib.Path(sys.argv[2]), name)[0] for name in t.ENGINES}))
"""


def v3_arm(q4, arm):
    engine = r3.RememberV3(q4)
    engine.set_arm(arm)
    return engine


ENGINES = {"v2": lambda q4: rv.RememberV2(q4), "v3t": lambda q4: v3_arm(q4, "v3t"), "v3h": lambda q4: v3_arm(q4, "v3h"),
           "v3brd": lambda q4: v3_arm(q4, "v3brd"), "v21a": lambda q4: r21.RememberV21(q4, "v21a"),
           "v22a": lambda q4: r22.RememberV22(q4, "v22a"), "v23a": lambda q4: r23.RememberV23(q4, "v23a")}


def ticking(patch, seconds):
    """Every clock of the time module moves ``seconds`` per read (its _ns form in nanoseconds)."""
    ticks = itertools.count(1)
    for name in CLOCKS:
        patch.setattr(time, name, lambda: next(ticks) * seconds)
        patch.setattr(time, name + "_ns", lambda: next(ticks) * int(seconds * 10**9))


def answered(memory, name):
    """(the canonical JSON of one engine's shortlist and of its answers to ``PICKS``, plus v2's gate, and the answer
    statuses), each engine on a fresh load of the memory."""
    engine = ENGINES[name](t22.q4(memory))
    listed = getattr(engine, "shortlist_arm", engine.shortlist)(SITUATION, WHEN, [SITUATION])
    out = {"listed": listed, "answers": [engine.apply_picks(listed, picks) for picks in PICKS]}
    if name == "v2":
        out["gate"] = [engine.gate(SITUATION, WHEN, SITUATION), engine.gate(SITUATION, WHEN, " ")]
    text = json.dumps(out, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return text, [answer["status"] for answer in out["answers"] + out.get("gate", [])]


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    return t22.build(tmp_path_factory.mktemp("clock"), t22.t2.records() + t22.topic(opposite_at=12))


@pytest.mark.parametrize("name", ENGINES)
def test_one_question_gives_the_same_bytes_whatever_the_clocks_read(memory, name, monkeypatch):
    real, statuses = answered(memory, name)
    with monkeypatch.context() as patch:
        ticking(patch, 1.0)
        fast, _ = answered(memory, name)
    with monkeypatch.context() as patch:
        ticking(patch, 3600.0)
        slow, _ = answered(memory, name)
    assert statuses[:3] == ["ANSWER", "ABSTAIN", "REFUSED"]
    assert statuses[3:] == ([] if name != "v2" else ["ANSWER", "REFUSED"])
    assert fast == real and slow == real


def test_two_processes_with_other_hash_seeds_give_the_same_bytes(memory):
    here = {name: answered(memory, name)[0] for name in ENGINES}
    seeded = [subprocess.run([sys.executable, "-B", "-c", SEEDED, str(HERE), str(memory)], capture_output=True,
                             text=True, encoding="utf-8", env=dict(os.environ, PYTHONHASHSEED=seed), timeout=600)
              for seed in ("1", "2")]
    assert [done.returncode for done in seeded] == [0, 0]
    assert [json.loads(done.stdout) for done in seeded] == [here, here]


@pytest.mark.parametrize("module", sorted(path.name for path in HERE.glob("*.py") if not path.name.startswith("test_")))
def test_no_module_imports_a_clock_or_reads_the_current_time(module):
    tree = ast.parse((HERE / module).read_text(encoding="utf-8"))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    read = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} & READS_NOW
    assert "time" not in imported and not read
