"""Tests-only guard contract candidate, not a runtime or cleanup witness.

Dependencies: PR1985 CLI/test support at 4ce829cb and PR1989 guard at 77d4223b.
Lead composes those exact bytes and runs RED before any Source change.

There is no approved stable-root selector or cleanup-proof verifier yet. The CLI
test therefore requires refusal BEFORE input/adapter use when neither is bound;
it does not invent a production root, verifier, receipt schema or successful
guarded-call fixture. The process tests below exercise the actual primitive in
pytest-owned temporary directories; they do NOT prove CLI integration, restart
reconciliation, power-loss durability, native transport cleanup or isolation.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
IDENTITY = "a" * 64
DESCRIPTOR = "b" * 64
QUERY = "synthetic-guard-query-not-for-storage"
TOKEN = "synthetic-guard-token-not-for-storage"
RECORD_KEYS = {"schema", "lane", "call_id", "started_utc", "pid",
               "identity_sha256", "descriptor_sha256"}


def load_actual(name, path):
    # Missing dependencies fail collection/execution; never importorskip/fallback.
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Reuse the exact existing synthetic CLI fixture, without copying configuration
# logic or inventing a guard-root environment variable. This imported fixture is
# registered in this test module just like a normal imported pytest fixture.
support = load_actual("synthetic_guard_cli_test_support",
                      ROOT / "tests/tools/test_wd_q4_lane_caller_cli.py")
case = support.case


@pytest.fixture
def guard():
    return load_actual("synthetic_guard_under_test", ROOT / "tools/wd_q4_lane_quarantine.py")


def acquire(guard, root, lane="codex-tools-1"):
    return guard.acquire(root, lane, identity_sha256=IDENTITY, descriptor_sha256=DESCRIPTOR)


def marker(root, lane="codex-tools-1"):
    return root / (lane + ".pending.json")


class ObservedStdin:
    def __init__(self, raw):
        self.raw = raw
        self.reads = 0

    @property
    def buffer(self):
        return self

    def read(self, size=-1):
        self.reads += 1
        return self.raw if size < 0 else self.raw[:size]


@pytest.mark.parametrize("claim", ["VALIDATED", "REFUSED", "TIMEOUT"],
                         ids=["answer-claim", "refusal-claim", "timeout-claim"])
def test_unbound_guard_cannot_read_query_or_call_adapter(case, monkeypatch, capsys, claim):
    """A valid old CLI configuration is not an approved guard/proof binding."""
    probe = ObservedStdin(case["query"])
    monkeypatch.setattr(sys, "stdin", probe)
    # None of these fake adapter strings supplies guard authority. In fact the
    # adapter must not be reached at all without the missing approved binding.
    case["outcome"]["validated"] = claim == "VALIDATED"
    if claim == "TIMEOUT":
        case["outcome"]["result"] = {"status": "TIMEOUT", "cleanup": "chain_confirmed"}
    code = case["cli"].main(["--enabled"])
    observed = capsys.readouterr()
    assert code in (2, 3)
    assert probe.reads == 0
    assert case["calls"] == []
    assert observed.out == "" and observed.err == support.REFUSAL


@pytest.mark.parametrize("result", [
    None, {}, {"status": "UNKNOWN", "cleanup": "unknown"},
    {"status": "TIMEOUT", "cleanup": "unknown"},
    {"status": "VALIDATED", "cleanup": "unrecognized"},
    {"status": "VALIDATED"}, RuntimeError(QUERY + TOKEN),
], ids=["none", "empty", "unknown", "timeout-without-cleanup", "unrecognized",
        "missing-cleanup", "exception-object"])
def test_unverified_completion_keeps_exact_marker_and_no_secrets(guard, tmp_path, result):
    lease = acquire(guard, tmp_path)
    path = marker(tmp_path)
    before = path.read_bytes()
    with pytest.raises(guard.CallerUnavailable) as error:
        lease.complete_confirmed(result)
    assert str(error.value) == "caller_unavailable"
    assert path.read_bytes() == before
    assert QUERY.encode() not in before and TOKEN.encode() not in before
    assert set(json.loads(before)) == RECORD_KEYS
    with pytest.raises(guard.CallerUnavailable):
        acquire(guard, tmp_path)


@pytest.mark.parametrize("lane", LANES, ids=LANES)
def test_pending_lane_does_not_block_a_different_lane(guard, tmp_path, lane):
    other = next(candidate for candidate in LANES if candidate != lane)
    acquire(guard, tmp_path, lane)
    before = marker(tmp_path, lane).read_bytes()
    acquire(guard, tmp_path, other)
    assert marker(tmp_path, lane).read_bytes() == before
    assert marker(tmp_path, other).is_file()
    with pytest.raises(guard.CallerUnavailable):
        acquire(guard, tmp_path, lane)
    # Both remain pending; no fake cleanup string is used to clear either.
    assert {path.name for path in tmp_path.iterdir()} == {
        lane + ".pending.json", other + ".pending.json"}


PROCESS = r'''
import os, sys
sys.path.insert(0, sys.argv[1])  # Explicit test source root, never a runtime fallback.
from tools import wd_q4_lane_quarantine as guard
try:
    lease = guard.acquire(sys.argv[2], sys.argv[3],
                          identity_sha256="a"*64, descriptor_sha256="b"*64)
except guard.CallerUnavailable:
    print("BLOCKED", flush=True)
    raise SystemExit(3)
print("ACQUIRED", flush=True)
phase = sys.argv[4]
if phase == "crash":
    os._exit(17)
if phase == "exception":
    raise RuntimeError("synthetic after-acquisition exception")
if phase == "unknown":
    try:
        lease.complete_confirmed({"status":"UNKNOWN", "cleanup":"unknown"})
    except guard.CallerUnavailable:
        print("HELD", flush=True)
    else:
        raise AssertionError("unverified completion accepted")
elif phase == "probe":
    print("STDIN_READ", flush=True)
    sys.stdin.buffer.read(1)
'''


def process(root, lane, phase):
    # Query/token canaries go only to stdin. The child never echoes input; argv
    # carries test-control paths/lane/phase only. Inherited pins are not changed.
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c", PROCESS, str(ROOT), str(root), lane, phase],
        input=(QUERY + "\n" + TOKEN).encode("ascii"), capture_output=True,
        timeout=20, check=False)


@pytest.mark.parametrize("phase,expected", [("unknown", 0), ("exception", 1), ("crash", 17)],
                         ids=["cleanup-unknown", "exception", "abrupt-exit"])
def test_next_primitive_process_refuses_before_input_after_uncertain_exit(tmp_path, phase, expected):
    """Actual subprocess primitive coverage; explicitly NOT a guarded CLI test."""
    first = process(tmp_path, "codex-tools-1", phase)
    assert first.returncode == expected
    assert first.stdout.splitlines()[0] == b"ACQUIRED"
    path = marker(tmp_path)
    before = path.read_bytes()
    doc = json.loads(before)
    assert set(doc) == RECORD_KEYS and doc["lane"] == "codex-tools-1"
    second = process(tmp_path, "codex-tools-1", "probe")
    assert second.returncode == 3 and second.stdout.splitlines() == [b"BLOCKED"]
    assert b"STDIN_READ" not in second.stdout + second.stderr
    assert path.read_bytes() == before
    for raw in (before, first.stdout, first.stderr, second.stdout, second.stderr):
        assert QUERY.encode() not in raw and TOKEN.encode() not in raw


def test_marker_records_control_metadata_without_query_token_or_path(guard, tmp_path):
    acquire(guard, tmp_path)
    raw = marker(tmp_path).read_bytes()
    doc = json.loads(raw)
    assert set(doc) == RECORD_KEYS
    assert doc["schema"] == "wd.q4-caller-pending.v1"
    assert doc["identity_sha256"] == IDENTITY and doc["descriptor_sha256"] == DESCRIPTOR
    for value in (QUERY, TOKEN, str(tmp_path)):
        assert value.encode("utf-8") not in raw
        assert json.dumps(value, ensure_ascii=True).encode("ascii")[1:-1] not in raw
