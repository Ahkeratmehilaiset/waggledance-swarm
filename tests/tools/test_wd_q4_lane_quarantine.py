"""Synthetic process-boundary tests for a fail-closed Q4 caller guard."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
IDENTITY = "a" * 64
DESCRIPTOR = "b" * 64
LANE_A = "codex-lead-1"
LANE_B = "claude-rco-1"


@pytest.fixture
def guard_module():
    from tools import wd_q4_lane_quarantine
    return wd_q4_lane_quarantine


def acquire(module, root, lane=LANE_A):
    return module.acquire(root, lane, identity_sha256=IDENTITY, descriptor_sha256=DESCRIPTOR)


def child(root, lane, mode, *, text=""):
    program = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
from tools import wd_q4_lane_quarantine as q
try:
    lease = q.acquire(sys.argv[2], sys.argv[3], identity_sha256="a"*64, descriptor_sha256="b"*64)
except q.CallerUnavailable:
    print("UNAVAILABLE", flush=True)
    sys.exit(3)
print("ACQUIRED", flush=True)
if sys.argv[4] == "crash":
    os._exit(17)
if sys.argv[4] == "wait":
    sys.stdin.readline()
if sys.argv[4] != "retain":
    lease.complete_confirmed({"status":"VALIDATED", "cleanup":"chain_confirmed"})
'''
    return subprocess.run([sys.executable, "-I", "-B", "-c", program, str(ROOT), str(root), lane, mode],
                          input=text, capture_output=True, text=True, timeout=30)


def test_confirmed_call_creates_minimal_pending_then_allows_next(guard_module, tmp_path):
    lease = acquire(guard_module, tmp_path)
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert set(doc) == {"schema", "lane", "call_id", "started_utc", "pid", "identity_sha256", "descriptor_sha256"}
    assert doc["lane"] == LANE_A and doc["identity_sha256"] == IDENTITY
    assert doc["descriptor_sha256"] == DESCRIPTOR and str(tmp_path) not in json.dumps(doc)
    lease.complete_confirmed({"status": "VALIDATED", "cleanup": "chain_confirmed"})
    assert list(tmp_path.iterdir()) == []
    acquire(guard_module, tmp_path).complete_confirmed({"status": "REFUSED", "cleanup": "chain_confirmed"})


def test_unknown_cannot_be_cleared_and_next_process_refuses_before_input(guard_module, tmp_path):
    lease = acquire(guard_module, tmp_path)
    with pytest.raises(guard_module.CallerUnavailable):
        lease.complete_confirmed({"status": "UNKNOWN", "cleanup": "unknown"})
    assert len(list(tmp_path.iterdir())) == 1
    denied = child(tmp_path, LANE_A, "wait", text="synthetic-never-read\n")
    assert denied.returncode == 3 and denied.stdout == "UNAVAILABLE\n"
    assert "synthetic-never-read" not in denied.stdout + denied.stderr


def test_crashed_process_leaves_guard_for_the_next_process(tmp_path):
    crashed = child(tmp_path, LANE_A, "crash")
    assert crashed.returncode == 17 and crashed.stdout == "ACQUIRED\n"
    denied = child(tmp_path, LANE_A, "normal")
    assert denied.returncode == 3 and denied.stdout == "UNAVAILABLE\n"


def test_other_lane_is_not_blocked(guard_module, tmp_path):
    acquire(guard_module, tmp_path)
    result = child(tmp_path, LANE_B, "normal")
    assert result.returncode == 0 and result.stdout == "ACQUIRED\n"
    assert child(tmp_path, LANE_A, "normal").returncode == 3


def test_two_concurrent_processes_do_not_both_enter(tmp_path):
    code = r'''import sys
sys.path.insert(0, sys.argv[1])
from tools import wd_q4_lane_quarantine as q
lease=q.acquire(sys.argv[2], "codex-lead-1", identity_sha256="a"*64, descriptor_sha256="b"*64)
print("ACQUIRED", flush=True)
sys.stdin.readline()
lease.complete_confirmed({"status":"VALIDATED", "cleanup":"chain_confirmed"})
'''
    first = subprocess.Popen([sys.executable, "-I", "-B", "-c", code, str(ROOT), str(tmp_path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert first.stdout.readline() == "ACQUIRED\n"
        denied = child(tmp_path, LANE_A, "normal")
        assert denied.returncode == 3 and denied.stdout == "UNAVAILABLE\n"
        _, err = first.communicate("finish\n", timeout=30)
        assert first.returncode == 0, err
        assert child(tmp_path, LANE_A, "normal").returncode == 0
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=30)


def test_invalid_lane_hash_or_untrusted_root_fails_without_record(guard_module, tmp_path):
    for lane, identity, root in (("../escape", IDENTITY, tmp_path), (LANE_A, "not-a-hash", tmp_path),
                                 (LANE_A, IDENTITY, Path("relative"))):
        with pytest.raises((ValueError, OSError)):
            guard_module.acquire(root, lane, identity_sha256=identity, descriptor_sha256=DESCRIPTOR)
    assert list(tmp_path.iterdir()) == []


def test_replaced_marker_is_not_removed(guard_module, tmp_path):
    lease = acquire(guard_module, tmp_path)
    marker = next(tmp_path.iterdir())
    marker.write_bytes(b"synthetic replacement")
    with pytest.raises(guard_module.CallerUnavailable):
        lease.complete_confirmed({"status": "VALIDATED", "cleanup": "chain_confirmed"})
    assert marker.read_bytes() == b"synthetic replacement"


def test_failed_pending_flush_stays_fail_closed(guard_module, tmp_path, monkeypatch):
    def fail_flush(_fd):
        raise OSError("synthetic flush failure")

    monkeypatch.setattr(guard_module.os, "fsync", fail_flush)
    with pytest.raises(OSError, match="synthetic flush failure"):
        acquire(guard_module, tmp_path)
    assert len(list(tmp_path.iterdir())) == 1
    with pytest.raises(guard_module.CallerUnavailable):
        acquire(guard_module, tmp_path)


def test_empty_old_pending_record_is_never_automatically_removed(guard_module, tmp_path):
    marker = tmp_path / (LANE_A + ".pending.json")
    marker.write_bytes(b"")
    with pytest.raises(guard_module.CallerUnavailable):
        acquire(guard_module, tmp_path)
    assert marker.read_bytes() == b""


def test_no_public_reconcile_by_arbitrary_digest(guard_module):
    assert not hasattr(guard_module, "reconcile")
