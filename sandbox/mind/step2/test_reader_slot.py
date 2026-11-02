"""Synthetic process-level tests for the Q4 reader resource slot."""

from pathlib import Path
import subprocess
import sys
import time

import pytest

from sandbox.mind.step2.reader_slot import ReaderBusy, reader_slot


_HOLDER = """
import sys, time
from sandbox.mind.step2.reader_slot import reader_slot
with reader_slot(sys.argv[1], timeout_seconds=3):
    print('LOCKED', flush=True)
    time.sleep(float(sys.argv[2]))
"""


def _start_holder(lock_path, duration=30):
    proc = subprocess.Popen(
        [sys.executable, "-B", "-u", "-c", _HOLDER, str(lock_path), str(duration)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).resolve().parents[3],
    )
    assert proc.stdout.readline().strip() == "LOCKED"
    return proc


def _kill_holder(proc):
    if proc.poll() is None:
        proc.kill()
    proc.communicate(timeout=10)


def test_other_process_gets_fixed_busy_and_bounded_wait(tmp_path):
    lock_path = tmp_path / "reader.lock"
    proc = _start_holder(lock_path)
    try:
        start = time.monotonic()
        with pytest.raises(ReaderBusy, match="^reader_busy$"):
            with reader_slot(lock_path, timeout_seconds=0.2):
                pytest.fail("the second reader entered while held")
        assert 0.15 <= time.monotonic() - start < 3
    finally:
        _kill_holder(proc)


def test_abrupt_process_exit_releases_slot(tmp_path):
    lock_path = tmp_path / "reader.lock"
    proc = _start_holder(lock_path)
    _kill_holder(proc)
    with reader_slot(lock_path, timeout_seconds=2):
        assert lock_path.exists()


def test_waiter_acquires_after_other_process_exits(tmp_path):
    lock_path = tmp_path / "reader.lock"
    proc = _start_holder(lock_path, duration=0.4)
    try:
        start = time.monotonic()
        with reader_slot(lock_path, timeout_seconds=3):
            assert time.monotonic() - start >= 0.2
        assert proc.wait(timeout=5) == 0
    finally:
        _kill_holder(proc)


def test_second_process_gets_busy_without_path_disclosure(tmp_path):
    lock_path = tmp_path / "reader.lock"
    contender = """
import sys
from sandbox.mind.step2.reader_slot import ReaderBusy, reader_slot
try:
    with reader_slot(sys.argv[1], timeout_seconds=0):
        print('UNEXPECTED_ACQUIRE')
except ReaderBusy as exc:
    print(str(exc))
"""
    proc = _start_holder(lock_path)
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-c", contender, str(lock_path)],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert result.stdout.strip() == "reader_busy"
        assert result.stderr == ""
    finally:
        _kill_holder(proc)


def test_context_release_allows_next_reader(tmp_path):
    lock_path = tmp_path / "reader.lock"
    with reader_slot(lock_path, timeout_seconds=1):
        with pytest.raises(ReaderBusy, match="^reader_busy$"):
            with reader_slot(lock_path, timeout_seconds=0):
                pytest.fail("nested slot unexpectedly acquired")
    with reader_slot(lock_path, timeout_seconds=0):
        pass


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf"), 31])
def test_invalid_timeout_is_rejected(tmp_path, timeout):
    with pytest.raises(ValueError):
        with reader_slot(tmp_path / "reader.lock", timeout_seconds=timeout):
            pass
