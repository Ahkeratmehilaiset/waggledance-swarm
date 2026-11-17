"""Test-only, retained subprocess with framed code and cooperative STOP.

This is not a production supervisor. A caller must retain the object and its
fixture directory while state is HOLD. There is no crash recovery, and a STOP
cannot interrupt a blocked native call. Never put secrets in the test program.
"""
from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import threading
import time


_MAX_PROGRAM = 2 * 1024 * 1024
_MAX_CAPTURE = 64 * 1024
_LOADER = r'''
import os, struct, sys, threading
descriptor = sys.stdin.fileno()
def exact(n):
    data = bytearray()
    while len(data) < n:
        part = os.read(descriptor, n - len(data))
        if not part:
            raise ValueError('incomplete synthetic frame')
        data.extend(part)
    return bytes(data)
length = struct.unpack('>I', exact(4))[0]
if not 0 < length <= 2 * 1024 * 1024:
    raise ValueError('invalid synthetic frame')
code = exact(length)
COOP_STOP = threading.Event()
def read_stop():
    try:
        if exact(4) == b'STOP':
            COOP_STOP.set()
    except Exception:
        pass
threading.Thread(target=read_stop, name='synthetic-stop-listener', daemon=True).start()
namespace = {'__name__': '__main__', 'COOP_STOP': COOP_STOP}
exec(compile(code, '<synthetic-test-program>', 'exec'), namespace)
'''


class CooperativeChild:
    """Keep the child and every pump reachable until verified natural exit."""

    def __init__(self, *, program: bytes, args=(), cwd=None):
        if type(program) is not bytes or not 0 < len(program) <= _MAX_PROGRAM:
            raise ValueError('invalid synthetic program')
        if type(args) not in (tuple, list) or any(type(arg) is not str for arg in args):
            raise ValueError('invalid synthetic arguments')
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._output = {'stdout': bytearray(), 'stderr': bytearray()}
        self._overflow = False
        self._pump_error = False
        self._stop_sent = False
        self._process = subprocess.Popen(
            [sys.executable, '-I', '-S', '-B', '-c', _LOADER, *args], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self._threads = [
            threading.Thread(target=self._feed, args=(program,), name='synthetic-child-feed'),
            threading.Thread(target=self._drain, args=('stdout',), name='synthetic-child-stdout'),
            threading.Thread(target=self._drain, args=('stderr',), name='synthetic-child-stderr'),
        ]
        for thread in self._threads:
            try:
                thread.start()
            except BaseException:
                # Retain the process even when a pump start is uncertain.
                self._pump_error = True

    def __repr__(self):
        return '<CooperativeSyntheticChild>'

    def _feed(self, program):
        try:
            frame = struct.pack('>I', len(program)) + program
            offset = 0
            while offset < len(frame):
                written = self._process.stdin.write(frame[offset:])
                if type(written) is not int or written <= 0:
                    raise ValueError('frame_write')
                offset += written
            self._process.stdin.flush()
            while not self._stop.wait(0.02):
                if self._process.poll() is not None:
                    return
            if self._process.poll() is None:
                self._process.stdin.write(b'STOP')
                self._process.stdin.flush()
                with self._lock:
                    self._stop_sent = True
        except BaseException:
            # A failed write is not evidence the child exited.
            if self._process.poll() is None:
                with self._lock:
                    self._pump_error = True
        finally:
            try:
                self._process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def _drain(self, channel):
        stream = getattr(self._process, channel)
        try:
            while True:
                block = stream.read(4096)
                if not block:
                    return
                with self._lock:
                    out = self._output[channel]
                    keep = max(0, _MAX_CAPTURE - len(out))
                    out.extend(block[:keep])
                    if len(block) > keep:
                        self._overflow = True
        except BaseException:
            with self._lock:
                self._pump_error = True
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def request_stop(self):
        """Request cooperation only; this never terminates the child."""
        self._stop.set()

    def status(self):
        code = self._process.poll()
        alive = any(thread.is_alive() for thread in self._threads)
        with self._lock:
            failed = self._pump_error or self._overflow
            sent = self._stop_sent
        if code is not None and not alive:
            return {'state': 'STOPPED', 'returncode': code, 'output_valid': not failed,
                    'stop_sent': sent}
        return {'state': 'HOLD', 'returncode': code, 'output_valid': False,
                'stop_sent': sent}

    def wait(self, *, timeout):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('invalid timeout')
        deadline = time.monotonic() + timeout
        while True:
            outcome = self.status()
            if outcome['state'] == 'STOPPED' or time.monotonic() >= deadline:
                return outcome
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

    def output(self):
        if self.status()['state'] != 'STOPPED':
            raise ValueError('child still retained')
        with self._lock:
            if self._overflow or self._pump_error:
                raise ValueError('synthetic output unavailable')
            return bytes(self._output['stdout']), bytes(self._output['stderr'])
