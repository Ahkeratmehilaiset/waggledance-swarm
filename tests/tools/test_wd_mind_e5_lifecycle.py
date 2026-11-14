"""Cooperative ownership tests: every process and pipe below is synthetic."""
import threading
import time

import pytest

from tools import wd_mind_e5_lifecycle as lifecycle


class Pipe:
    def __init__(self, data=b""):
        self.data = bytearray(data)
        self.condition = threading.Condition()
        self.closed = False

    def read1(self, size):
        with self.condition:
            while not self.data and not self.closed:
                self.condition.wait()
            result = bytes(self.data[:size])
            del self.data[:size]
            return result

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()


class Process:
    pid = 12345

    def __init__(self, output=b"", error=b"", *, on_eof=True, exit_code=0):
        self.stdout, self.stderr = Pipe(output), Pipe(error)
        self.code, self.eof_code = None, exit_code
        self.written = bytearray()
        process = self

        class Input:
            closed = False

            def write(self, data):
                process.written.extend(data)
                return len(data)

            def flush(self):
                pass

            def close(self):
                self.closed = True
                if on_eof:
                    process.finish(process.eof_code)

        self.stdin = Input()

    def finish(self, code=0):
        self.code = code
        self.stdout.close()
        self.stderr.close()

    def poll(self):
        return self.code

    def wait(self, timeout):
        assert self.code is not None
        return self.code

    def kill(self):
        pytest.fail("kill forbidden")

    def terminate(self):
        pytest.fail("terminate forbidden")


class Job:
    kill_on_close = False

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def terminate(self):
        pytest.fail("job termination forbidden")


def bind(process):
    owner = lifecycle.reserve(enabled=True)
    job = Job()
    owner.set_job(job)
    owner.bind(process)
    return owner, job


def finish(owner, process):
    process.finish()
    result = owner.shutdown(timeout=2)
    assert result["owned"] is False


def test_default_closed_and_only_one_reserved_owner():
    with pytest.raises(lifecycle.LifecycleRefused):
        lifecycle.reserve()
    owner = lifecycle.reserve(enabled=True)
    try:
        assert owner.status()["state"] == "STARTING"
        with pytest.raises(lifecycle.LifecycleRefused):
            lifecycle.reserve(enabled=True)
    finally:
        owner.cancel_unstarted()
    assert owner.status()["owned"] is False


@pytest.mark.parametrize("code,state", [(0, "EXITED"), (7, "FAILED")])
def test_eof_then_verified_exit_releases_job(code, state):
    process = Process(exit_code=code)
    owner, job = bind(process)
    try:
        result = owner.shutdown(timeout=2)
        assert result["state"] == state and result["exit_code"] == code
        assert result["eof_sent"] and not result["owned"] and job.closed
    finally:
        finish(owner, process)


def test_deadline_keeps_same_process_job_and_excludes_restart():
    process = Process(on_eof=False)
    owner, job = bind(process)
    try:
        result = owner.shutdown(timeout=0.02)
        assert result["state"] == "HOLD" and result["owned"]
        assert result["pid"] == 12345 and not job.closed
        assert owner._process is process and owner._job is job
        assert owner._keeper.is_alive() and not owner._keeper.daemon
        with pytest.raises(lifecycle.LifecycleRefused):
            lifecycle.reserve(enabled=True)
    finally:
        finish(owner, process)
    replacement = lifecycle.reserve(enabled=True)
    replacement.cancel_unstarted()


def test_reads_writes_use_fixed_pumps_and_do_not_echo():
    process = Process(output=b"abcd")
    owner, _ = bind(process)
    try:
        deadline = time.monotonic() + 2
        assert owner.read(4, deadline) == b"abcd"
        for _ in range(20):
            assert owner.write(b"x", deadline) == 1
        assert process.written == b"x" * 20
        assert len(owner._pumps) == 3
    finally:
        finish(owner, process)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_output_flood_is_bounded_and_requests_eof(stream):
    limit = lifecycle.MAX_STDOUT_BUFFER if stream == "stdout" else lifecycle.MAX_STDERR_BYTES
    process = Process(output=b"x" * (limit + 8192) if stream == "stdout" else b"",
                      error=b"private" * (limit + 8192) if stream == "stderr" else b"",
                      on_eof=False)
    owner, job = bind(process)
    try:
        end = time.monotonic() + 2
        while owner.status()["reason"] is None and time.monotonic() < end:
            time.sleep(0.005)
        status = owner.status()
        assert status["reason"] == stream + "_budget"
        assert status["owned"] and not job.closed
        assert len(owner._output) <= lifecycle.MAX_STDOUT_BUFFER
        assert "private" not in repr(status)
        assert owner._counts[stream] <= limit + lifecycle.READ_CHUNK
    finally:
        finish(owner, process)


def test_blocked_write_reports_eof_pending_without_blocking_caller():
    process = Process(on_eof=False)
    entered, unblock = threading.Event(), threading.Event()
    original = process.stdin.write

    def blocked(data):
        entered.set()
        unblock.wait()
        return original(data)

    process.stdin.write = blocked
    owner, job = bind(process)
    try:
        with pytest.raises(lifecycle.LifecycleRefused):
            owner.write(b"synthetic", time.monotonic() + 0.03)
        assert entered.is_set()
        status = owner.shutdown(timeout=0.02)
        assert status["state"] == "HOLD" and not status["eof_sent"]
        assert status["owned"] and not job.closed
    finally:
        unblock.set()
        finish(owner, process)


def test_exit_alone_does_not_release_when_pipe_remains_held():
    process = Process(on_eof=False)
    owner, job = bind(process)
    try:
        process.code = 0
        status = owner.shutdown(timeout=0.02)
        assert status["state"] == "HOLD" and status["owned"] and not job.closed
    finally:
        finish(owner, process)


def test_killing_job_is_refused_before_process_bind():
    owner = lifecycle.reserve(enabled=True)
    job = Job()
    job.kill_on_close = True
    try:
        with pytest.raises(lifecycle.LifecycleRefused):
            owner.set_job(job)
    finally:
        owner.cancel_unstarted()


def test_failed_handle_close_holds_slot_even_after_exit():
    process = Process()
    owner, job = bind(process)
    allow_close = threading.Event()
    original = job.close

    def close():
        if not allow_close.is_set():
            raise OSError("synthetic close failure")
        original()

    job.close = close
    try:
        status = owner.shutdown(timeout=0.03)
        assert status["owned"] and status["exit_code"] == 0 and not job.closed
        assert status["reason"] == "exit_verification"
    finally:
        allow_close.set()
        finish(owner, process)


@pytest.mark.parametrize("bad", [True, -1, float("nan"), float("inf"), 1801])
def test_invalid_shutdown_timeout_cannot_drop_ownership(bad):
    process = Process(on_eof=False)
    owner, job = bind(process)
    try:
        with pytest.raises(lifecycle.LifecycleRefused):
            owner.shutdown(timeout=bad)
        assert owner.status()["owned"] and not job.closed
    finally:
        finish(owner, process)


def test_consumed_stdout_still_counts_against_session_budget(monkeypatch):
    monkeypatch.setattr(lifecycle, "MAX_STDOUT_BYTES", 16)
    process = Process(on_eof=False)
    owner, _ = bind(process)
    try:
        for _ in range(4):
            with process.stdout.condition:
                process.stdout.data.extend(b"abcd")
                process.stdout.condition.notify_all()
            assert owner.read(4, time.monotonic() + 2) == b"abcd"
        with process.stdout.condition:
            process.stdout.data.extend(b"x")
            process.stdout.condition.notify_all()
        with pytest.raises(lifecycle.LifecycleRefused):
            owner.read(1, time.monotonic() + 2)
        assert owner.status()["reason"] == "stdout_budget"
        assert len(owner._output) == 0
    finally:
        finish(owner, process)
