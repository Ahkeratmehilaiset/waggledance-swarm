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
import hashlib
import threading
import time

MAX_PACKET = 65540
MAX_STDOUT_BUFFER = 65540
MAX_STDOUT_BYTES = 1024 * 1024
BULK_CONTRACT = b'{"frame_bytes":65536,"inflight_bytes":65540,"nonce_limit":8192,"schema":"wd.mind-e5-bulk-contract.v1","stdout_bytes":67108864}'
BULK_CONTRACT_SHA256 = '61998bfeea1342988301f30f21d9781017249ef66761ec81874e5cc34633bc08'
BULK_STDOUT_BYTES = 64 * 1024 * 1024
MAX_STDERR_BYTES = 16384
READ_CHUNK = 4096
_slot_lock = threading.Lock()
_active = None

# A child exit code is an unauthenticated hint, not proof of the failure cause.
# In particular 2 remains the bootstrap/interpreter's existing refusal code.
_WORKER_STAGE_HINTS = {
    20: 'arguments', 21: 'model_pin', 22: 'offline_environment',
    23: 'reader_mutex', 24: 'library_import', 25: 'model_load', 29: 'serving',
}


def _worker_exit_diagnostic(code, verified):
    if verified is not True or type(code) is not int or not 0 <= code <= 0xffffffff:
        return ('UNKNOWN', None)
    hint = _WORKER_STAGE_HINTS.get(code, 'UNKNOWN')
    return (hint, code if hint == 'UNKNOWN' else None)


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


def reserve(*, enabled=False, bulk_contract_sha256=None, require_spawn_attempt=False):
    """Reserve before creating a child. HOLD also occupies this single slot."""
    global _active
    if (enabled is not True or type(require_spawn_attempt) is not bool or (bulk_contract_sha256 is not None and
            (type(bulk_contract_sha256) is not str or bulk_contract_sha256 != BULK_CONTRACT_SHA256
             or hashlib.sha256(BULK_CONTRACT).hexdigest() != BULK_CONTRACT_SHA256))):
        raise LifecycleRefused("mind_e5_lifecycle_refused")
    with _slot_lock:
        if _active is not None:
            raise LifecycleRefused("mind_e5_lifecycle_refused")
        owner = _Owner(bulk_contract_sha256, require_spawn_attempt=require_spawn_attempt)
        _active = owner
        try:
            # Start the lifetime keeper BEFORE any native process can exist.
            owner._keeper.start()
        except Exception:
            _active = None
            raise LifecycleRefused("mind_e5_lifecycle_refused") from None
        return owner


class _Owner:
    def __init__(self, bulk_contract_sha256=None, *, require_spawn_attempt=False):
        self._bulk_contract_sha256 = bulk_contract_sha256
        self._condition = threading.Condition()
        self.resources = _Resources()
        self._state, self._reason, self._code = "STARTING", None, None
        self._verified_exit_code = None
        # Legacy callers may still bind without registering an attempt.  Until
        # they are migrated this state does not prove that no spawn occurred.
        self._spawn_state = "NOT_ENTERED"
        self._job_state = "NOT_ENTERED"
        self._require_spawn_attempt = require_spawn_attempt
        self._owned, self._closing, self._eof = True, False, False
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
            if (not self._owned or (self._closing and self._job_state != "ATTEMPTED")
                    or (self._job is not None and self._job is not job)
                    or self._process is not None or job.kill_on_close is not False):
                raise LifecycleRefused("mind_e5_lifecycle_refused")
            self.resources.job = job
            self._job_state = "BOUND"
            self._condition.notify_all()

    def mark_job_attempt(self):
        """Retain ownership before publishing an owner or allocating a Job."""
        with self._condition:
            if (not self._owned or self._closing or self._job is not None
                    or self._process is not None or self._job_state != "NOT_ENTERED"):
                raise LifecycleRefused("mind_e5_lifecycle_refused")
            self._job_state = "ATTEMPTED"
            self._condition.notify_all()

    def mark_spawn_attempt(self):
        """Record uncertainty before entering native process creation."""
        with self._condition:
            if (not self._owned or self._closing or self._job is None or self._job_state != "BOUND"
                    or self._process is not None or self._spawn_state != "NOT_ENTERED"):
                raise LifecycleRefused("mind_e5_lifecycle_refused")
            self._spawn_state = "ATTEMPTED"
            self._condition.notify_all()

    def bind(self, process):
        """Retain the actual process immediately after Popen, before attachment."""
        with self._condition:
            if (process is None or not self._owned
                    or (self._closing and self._spawn_state != "ATTEMPTED")
                    or (self._require_spawn_attempt and self._spawn_state != "ATTEMPTED")
                    or (self._process is not None and self._process is not process)
                    or self._job is None):
                raise LifecycleRefused("mind_e5_lifecycle_refused")
            self.resources.process = process
            self._spawn_state = "BOUND"
            self._condition.notify_all()

    def mark_ready(self):
        with self._condition:
            if not self._owned or self._closing or self._reason is not None:
                raise LifecycleRefused("mind_e5_lifecycle_refused")
            self._state = "RUNNING"

    def status(self):
        with self._condition:
            return {"state": self._state, "owned": self._owned,
                    "pid": None if self._process is None else self._process.pid,
                    "exit_code": self._code, "eof_sent": self._eof, "reason": self._reason}

    def worker_exit_diagnostic(self):
        """Observe only an already-recorded code; never wait for owner I/O."""
        if not self._condition.acquire(blocking=False):
            return ('UNKNOWN', None)
        try:
            return _worker_exit_diagnostic(self._verified_exit_code, self._verified_exit_code is not None)
        finally:
            self._condition.release()

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
                    limit = (BULK_STDOUT_BYTES if self._bulk_contract_sha256 is not None else MAX_STDOUT_BYTES) if name == "stdout" else MAX_STDERR_BYTES
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
            try:
                stream.close()  # Only this pump closes its own possibly locked pipe.
            except Exception:
                self._fault(name + "_close")
            with self._condition:
                self._done.add(name)
                self._condition.notify_all()

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
            try:
                self._process.stdin.close()
                with self._condition:
                    self._eof = True
            except Exception:
                self._fault("stdin_close")
            with self._condition:
                self._done.add("stdin")
                self._condition.notify_all()

    def _keep(self):
        with self._condition:
            # A native spawn may have succeeded without returning a process
            # object. Neither shutdown nor an unbound process is proof that
            # the attempt was harmless; keep ownership and the slot on HOLD.
            while self._owned and (self._job_state == "ATTEMPTED" or self._spawn_state == "ATTEMPTED"
                                   or (self._process is None and not self._closing)):
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
                        ready = self._done == {"stdin", "stdout", "stderr"} and all(not p.is_alive() for p in self._pumps)
                if ready:
                    if self._process.wait(timeout=0) != code:
                        raise ValueError("exit")
                    with self._condition:
                        # This verifies the exit code only. Pump completion is
                        # not proof of EOF when a pump terminated on a fault.
                        self._verified_exit_code = code
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
