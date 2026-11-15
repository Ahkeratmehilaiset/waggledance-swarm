# SPDX-License-Identifier: BUSL-1.1
"""Opt-in cooperative E5 ownership, with no process creation or termination.

One slot per loaded module, three bounded pipe pumps and a non-daemon keeper.
The keeper deliberately keeps this Python owner alive during HOLD. EOF cannot
interrupt native model loading/encoding or a blocked pipe write. A timeout only
bounds the caller's wait; it does not promise child termination. Owner crashes,
interpreter forced exits, native descendants and cross-process coordination are
outside this contract. This module must be independently pinned before launch.
"""
import math
import threading
import time

MAX_PACKET = 65540
MAX_STDOUT_BUFFER = 65540
MAX_STDOUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 16384
READ_CHUNK = 4096
_slot_lock = threading.Lock()
_active = None


class LifecycleRefused(ValueError):
    """Fixed refusal, with no pipe contents or native exception text."""


class _Resources:
    """Supervisor-owned references, stored before fallible registration hooks.

    The trusted launcher writes each returned native resource here immediately.
    Registration validates it; registration is not the act of taking ownership.
    """
    __slots__ = ("job", "process")

    def __init__(self):
        self.job = self.process = None


def _duration(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1800:
        raise LifecycleRefused("mind_e5_lifecycle_refused")
    return float(value)


def reserve(*, enabled=False, launch_pending=False):
    """Reserve one slot; declare launch_pending before calling native creators.

    A launcher must finish_launch only after retaining the returned child or
    proving no child was created, with no attachment still pending. A bare
    constructor exception is not that proof. Cancellation never resolves it.
    The default supports adoption of resources whose creation already finished.
    """
    global _active
    if enabled is not True or type(launch_pending) is not bool:
        raise LifecycleRefused("mind_e5_lifecycle_refused")
    with _slot_lock:
        if _active is not None:
            raise LifecycleRefused("mind_e5_lifecycle_refused")
        owner = _Owner(launch_pending=launch_pending)
        _active = owner
        try:
            # Start the lifetime keeper BEFORE any native process can exist.
            owner._keeper.start()
        except Exception:
            _active = None
            raise LifecycleRefused("mind_e5_lifecycle_refused") from None
        return owner


class _Owner:
    def __init__(self, *, launch_pending=False):
        self._condition = threading.Condition()
        self.resources = _Resources()
        self._state, self._reason, self._code = "STARTING", None, None
        self._owned, self._closing, self._eof = True, False, False
        self._launch_pending = launch_pending
        self._output = bytearray()
        self._counts = {"stdout": 0, "stderr": 0}
        self._done = set()
        self._pending = None
        self._writing = False
        self._pumps = []
        self._keeper = threading.Thread(target=self._keep, name="mind-e5-owner", daemon=False)

    @property
    def _process(self):
        return self.resources.process

    @property
    def _job(self):
        return self.resources.job

    def set_job(self, job):
        with self._condition:
            if (not self._owned or self._closing or (self._job is not None and self._job is not job)
                    or self._process is not None or job.kill_on_close is not False):
                raise LifecycleRefused("mind_e5_lifecycle_refused")
            self.resources.job = job

    def bind(self, process):
        """Retain the actual process immediately after Popen, before attachment."""
        with self._condition:
            if (not self._owned or self._closing or (self._process is not None and self._process is not process)
                    or self._job is None):
                raise LifecycleRefused("mind_e5_lifecycle_refused")
            self.resources.process = process
            self._condition.notify_all()

    def mark_ready(self):
        with self._condition:
            if not self._owned or self._closing or self._reason is not None:
                raise LifecycleRefused("mind_e5_lifecycle_refused")
            self._state = "RUNNING"
            self._launch_pending = False
            self._condition.notify_all()

    def finish_launch(self):
        """Trusted creator resolved child/no-child and all pending native calls."""
        with self._condition:
            self._launch_pending = False
            self._condition.notify_all()

    def status(self):
        with self._condition:
            return {"state": self._state, "owned": self._owned,
                    "pid": None if self._process is None else self._process.pid,
                    "exit_code": self._code, "eof_sent": self._eof, "reason": self._reason,
                    "launch_pending": self._launch_pending}

    def _release(self, state):
        global _active
        # Caller holds the condition; no reservation path acquires it in reverse.
        with _slot_lock:
            if _active is self:
                _active = None
        self._owned, self._state = False, state
        self._condition.notify_all()

    def cancel_unstarted(self, *, timeout=2):
        """Bounded cancellation; even a Job-only HOLD retains its owner channel."""
        with self._condition:
            if self._process is not None:
                raise LifecycleRefused("mind_e5_lifecycle_refused")
        return self.shutdown(timeout=timeout)

    def _fault(self, reason):
        with self._condition:
            if self._owned:
                if self._reason is None:
                    self._reason = reason
                self._closing, self._state = True, "HOLD"
                self._condition.notify_all()

    def shutdown(self, *, timeout=0):
        """Request EOF and wait at most timeout; HOLD retains every handle."""
        deadline = time.monotonic() + _duration(timeout)
        with self._condition:
            self._closing = True
            if self._owned and self._state != "HOLD":
                self._state = "DRAINING"
            self._condition.notify_all()
            while self._owned and time.monotonic() < deadline:
                self._condition.wait(max(0, deadline - time.monotonic()))
            if self._owned:
                self._fault("shutdown_deadline")
            return self.status()

    def read(self, size, deadline):
        if type(size) is not int or not 0 < size <= MAX_PACKET:
            raise LifecycleRefused("mind_e5_lifecycle_refused")
        with self._condition:
            while not self._output and "stdout" not in self._done and self._reason is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._fault("read_deadline")
                    break
                self._condition.wait(remaining)
            if self._reason is not None or time.monotonic() >= deadline:
                raise LifecycleRefused("mind_e5_lifecycle_refused")
            result = bytes(self._output[:size])
            del self._output[:size]
            return result

    def write(self, packet, deadline):
        """Refuse late completion; bytes already written cannot be withdrawn."""
        if type(packet) is not bytes or not 0 < len(packet) <= MAX_PACKET:
            raise LifecycleRefused("mind_e5_lifecycle_refused")
        request = [packet, False, False, deadline]
        with self._condition:
            if not self._owned or self._closing or self._pending is not None or self._writing or time.monotonic() >= deadline:
                raise LifecycleRefused("mind_e5_lifecycle_refused")
            self._pending = request
            self._condition.notify_all()
            while not request[1] and self._reason is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._fault("write_deadline")
                    break
                self._condition.wait(remaining)
            if time.monotonic() >= deadline:
                self._fault("write_deadline")
            if not request[1] or not request[2] or self._reason is not None:
                raise LifecycleRefused("mind_e5_lifecycle_refused")
            return len(packet)

    def _reader(self, name):
        stream = getattr(self._process, name)
        try:
            while True:
                # BufferedReader.read(n) can wait for all n bytes of a tiny frame.
                read1 = getattr(stream, "read1", None)
                part = read1(READ_CHUNK) if read1 is not None else stream.read(1)
                if type(part) is not bytes or len(part) > READ_CHUNK:
                    raise ValueError("pipe")
                if not part:
                    break
                with self._condition:
                    limit = MAX_STDOUT_BYTES if name == "stdout" else MAX_STDERR_BYTES
                    self._counts[name] += len(part)
                    if self._counts[name] > limit or (name == "stdout" and len(self._output) + len(part) > MAX_STDOUT_BUFFER):
                        self._fault(name + "_budget")
                        break
                    if name == "stdout":
                        self._output.extend(part)
                    self._condition.notify_all()
        except Exception:
            self._fault(name + "_pipe")
        finally:
            self._close_pipe(stream, name)

    def _close_pipe(self, stream, name):
        # Only the existing pump touches its own possibly locked pipe. A failed
        # close is not completion, even after process exit. Retain this pump and
        # its owner until a later successful close also confirms closed=True.
        while True:
            try:
                stream.close()
                if stream.closed is not True:
                    raise ValueError("pipe_not_closed")
            except Exception:
                self._fault(name + "_close")
                time.sleep(0.01)
                continue
            with self._condition:
                if name == "stdin":
                    self._eof = True
                self._done.add(name)
                self._condition.notify_all()
            return

    def _writer(self):
        try:
            while True:
                with self._condition:
                    while self._pending is None and not self._closing:
                        self._condition.wait()
                    if self._closing:
                        self._pending = None
                        break
                    request, self._pending = self._pending, None
                    self._writing = True
                if time.monotonic() >= request[3]:
                    self._fault("write_deadline")
                    break
                success = self._process.stdin.write(request[0]) == len(request[0])
                self._process.stdin.flush()
                with self._condition:
                    self._writing = False
                    if time.monotonic() >= request[3]:
                        success = False
                        self._fault("write_deadline")
                    request[1], request[2] = True, success
                    self._condition.notify_all()
                if not success:
                    self._fault("stdin_pipe")
        except Exception:
            self._fault("stdin_pipe")
        finally:
            self._close_pipe(self._process.stdin, "stdin")

    def _keep(self):
        with self._condition:
            while self._owned and self._process is None and (not self._closing or self._launch_pending):
                self._condition.wait()
            if not self._owned:
                return
        if self._process is None:
            # No native cleanup runs with the condition held, or on the caller.
            # A failing/blocked close leaves the same owner and slot in place.
            while True:
                try:
                    if self._job is not None:
                        self._job.close()
                    with self._condition:
                        self._release("CANCELLED")
                    return
                except Exception:
                    self._fault("cleanup_failed")
                    time.sleep(0.01)
        try:
            for name in ("stdin", "stdout", "stderr"):
                target = self._writer if name == "stdin" else lambda name=name: self._reader(name)
                pump = threading.Thread(target=target, name="mind-e5-" + name, daemon=True)
                self._pumps.append(pump)
                pump.start()
        except Exception:
            # Keep ownership even if the interpreter cannot create a pipe pump.
            self._fault("pump_start")
        while True:
            try:
                code = self._process.poll()
                ready = False
                with self._condition:
                    if type(code) is int:
                        self._code, self._closing = code, True
                        self._condition.notify_all()
                        ready = (not self._launch_pending and self._done == {"stdin", "stdout", "stderr"}
                                 and all(not p.is_alive() for p in self._pumps))
                if ready:
                    if self._process.wait(timeout=0) != code:
                        raise ValueError("exit")
                    self._job.close()
                    handle = getattr(self._process, "_handle", None)
                    if handle is not None:
                        handle.Close()
                    with self._condition:
                        self._output.clear()
                        self._release("EXITED" if code == 0 and self._reason is None else "FAILED")
                    return
                with self._condition:
                    self._condition.wait(0.01)
            except Exception:
                self._fault("exit_verification")
                time.sleep(0.01)
