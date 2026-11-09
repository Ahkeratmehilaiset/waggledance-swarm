"""Opt-in, offline *candidate* site runner; never an installer or activation.

Inputs and hashes are caller supplied, not trust anchors. Checking them does
not verify pip, stdlib, DLLs, wheel safety, dependency closure or interpreter
ABI. Same-user path races remain. The child is not OS-network-sandboxed; pip
receives no-index and an isolated environment. Disk sampling cannot guarantee
a floor against a concurrent writer. Only the direct subprocess is killed;
its descendants and any effects outside the target are not verified. Failed
candidate directories are intentionally preserved. Do not use a result as a
production, installed, or measured receipt.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from tools import q4_offline_site_candidate as preflight
from tools.q4_wheel_archive_check import inspect_wheel
from sandbox.mind.step2 import runtime_manifest


_FLOOR = 3 * 1024**3
_CAPTURE_LIMIT = 16384
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')


class _Capture:
    """Continuously drain a child pipe, retaining at most a fixed byte count."""

    def __init__(self, pipe):
        self.pipe = pipe
        self.data = bytearray()
        self.truncated = False
        self.error = False
        self.thread = threading.Thread(target=self._read, daemon=True)

    def _read(self):
        try:
            while True:
                part = self.pipe.read(8192)
                if not part:
                    return
                remaining = _CAPTURE_LIMIT - len(self.data)
                self.data.extend(part[:max(0, remaining)])
                if len(part) > remaining:
                    self.truncated = True
        except (OSError, ValueError):
            self.error = True

    def start(self):
        self.thread.start()

    def finish(self):
        self.thread.join(timeout=2)
        return not self.thread.is_alive() and not self.error


def _base(state):
    return {'state': state, 'production': 'NOT_INSTALLED',
            'dependency_closure': 'NOT_VERIFIED', 'pip_stdlib_dlls': 'NOT_VERIFIED',
            'network_isolation': 'NOT_VERIFIED', 'process_tree': 'NOT_VERIFIED',
            'disk_floor_guarantee': 'NOT_GUARANTEED', 'partial_artifacts': 'PRESERVED'}


def materialize(*, audit_root, candidate_name, python_executable, python_sha256,
                lock_bytes, lock_sha256, wheel_roots, wheels, max_wheel_bytes,
                peak_reservation_bytes, system_root, deadline_seconds=180,
                enabled=False):
    """Create one new audit-only site candidate; never delete partial results.

    Set enabled=True explicitly. The caller must select a conservative peak
    reservation. Polling and that estimate are not hard allocation limits.
    The returned stdout/stderr are bounded diagnostics and can include paths;
    do not copy them into public logs without a separate redaction decision.
    """
    if enabled is not True:
        return _base('DISABLED')
    if (type(candidate_name) is not str or not _NAME.fullmatch(candidate_name)
            or type(peak_reservation_bytes) is not int or peak_reservation_bytes <= 0
            or type(deadline_seconds) not in (int, float)
            or not 0.01 <= deadline_seconds <= 1800):
        raise ValueError('parameters_invalid')
    root = preflight._directory(audit_root)
    if '.codex-audit' not in [part.lower() for part in root.parts]:
        raise ValueError('audit_root_invalid')
    candidate = root / candidate_name
    free = shutil.disk_usage(root).free
    if free < _FLOOR + peak_reservation_bytes:
        return _base('LOW_DISK_PREFLIGHT')
    plan = dict(wheel_roots=wheel_roots, wheels=wheels, target=candidate,
                python_executable=python_executable, python_sha256=python_sha256,
                lock_bytes=lock_bytes, lock_sha256=lock_sha256,
                target_tag='cp313-cp313-win_amd64', max_bytes=max_wheel_bytes,
                free_bytes=free)
    preflight.validate_plan(**plan)
    # Check each archive on its pinned byte stream before creating output. The
    # cumulative uncompressed size is only a lower bound on extraction peak.
    uncompressed_bytes = 0
    for wheel in wheels:
        checked = inspect_wheel(wheel['path'], expected_sha256=wheel['sha256'],
                                target_tag='cp313-cp313-win_amd64',
                                max_uncompressed_bytes=min(peak_reservation_bytes,
                                                           runtime_manifest.MAX_TOTAL_BYTES))
        uncompressed_bytes += checked['uncompressed_bytes']
        if uncompressed_bytes > peak_reservation_bytes:
            raise ValueError('uncompressed_budget_exceeded')
    # Exclusive creation: never reuse a previous or partially written candidate.
    candidate.mkdir()
    temp = candidate / 'temp'
    temp.mkdir()
    lock = candidate / 'requirements.lock'
    with lock.open('xb') as stream:
        stream.write(lock_bytes)
    site = candidate / 'site'
    env = preflight.build_isolated_env(system_root=system_root, temp_dir=temp)
    if shutil.disk_usage(root).free < _FLOOR + peak_reservation_bytes:
        return _base('LOW_DISK_AFTER_CREATION')
    # Re-read caller pins immediately before launch. This does not remove TOCTOU.
    plan['target'] = site
    plan['free_bytes'] = shutil.disk_usage(root).free
    if plan['free_bytes'] < _FLOOR + peak_reservation_bytes:
        return _base('LOW_DISK_BEFORE_LAUNCH')
    preflight.validate_plan(**plan)
    argv = [str(python_executable), '-I', '-B', '-m', 'pip', 'install',
            '--no-index', '--no-deps', '--require-hashes', '--only-binary=:all:',
            '--no-cache-dir', '--no-compile', '--no-input', '--disable-pip-version-check']
    for wheel_root in wheel_roots:
        argv.extend(['--find-links', str(wheel_root)])
    argv.extend(['--target', str(site), '-r', str(lock)])
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=str(candidate), env=env,
                                shell=False)
    except OSError:
        return _base('LAUNCH_FAILED')
    stdout, stderr = _Capture(proc.stdout), _Capture(proc.stderr)
    stdout.start()
    stderr.start()
    deadline = time.monotonic() + deadline_seconds
    cause = None
    while proc.poll() is None:
        if time.monotonic() >= deadline:
            cause = 'TIMEOUT'
            break
        try:
            if shutil.disk_usage(root).free < _FLOOR + 64 * 1024**2:
                cause = 'LOW_DISK'
                break
        except OSError:
            cause = 'DISK_UNKNOWN'
            break
        time.sleep(0.05)
    killed = True
    if cause:
        try:
            proc.kill()
        except OSError:
            killed = False
    try:
        code = proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        code = None
    stdout_ok, stderr_ok = stdout.finish(), stderr.finish()
    capture_ok = stdout_ok and stderr_ok
    if cause and (not killed or code is None):
        state = 'INCOMPLETE_KILL_OR_WAIT_FAILED'
    elif code is None or not capture_ok:
        state = 'INCOMPLETE_WAIT_OR_CAPTURE'
    elif cause:
        state = cause + '_DIRECT_PROCESS_STOPPED_TREE_UNKNOWN'
    elif code == 0:
        try:
            state = ('LOW_DISK_AFTER_EXIT' if shutil.disk_usage(root).free < _FLOOR + 64 * 1024**2
                     else 'EXIT_ZERO_UNVERIFIED')
        except OSError:
            state = 'DISK_UNKNOWN_AFTER_EXIT'
    else:
        state = 'EXIT_NONZERO'
    result = _base(state)
    result.update({'exit_code': code, 'candidate_root': str(candidate),
                   'inspected_uncompressed_bytes': uncompressed_bytes,
                   'stdout': bytes(stdout.data).decode('utf-8', errors='replace'),
                   'stderr': bytes(stderr.data).decode('utf-8', errors='replace'),
                   'stdout_truncated': stdout.truncated, 'stderr_truncated': stderr.truncated,
                   'capture_complete': capture_ok})
    return result
