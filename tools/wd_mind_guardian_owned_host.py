"""Default-off owned host for the guarded local Mind owner.

This file has no installed entrypoint. The production bootstrap gate below is
deliberately closed: it must not be opened before pinned launch, legacy-owner
exclusion and first-CLEAR migration have independently been established. This
module neither provisions markers nor starts processes on import.

The host retains one owner thread and its control/status objects through HOLD.
It exposes no caller transport or peer authentication and stores no source text.
An OS process crash, legacy unguarded owner and same-user mutation remain UNKNOWN.
"""

import json
from pathlib import Path
import re
import threading

_BOOTSTRAP_PROVEN = False
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_MAX_STATUS = 4096


class HostRefused(ValueError):
    """Fixed non-sensitive refusal."""


def _unique(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError('duplicate')
        output[key] = value
    return output


class _StopControl:
    def __init__(self, stop):
        self._stop, self._sent = stop, False

    def read(self, size):
        if type(size) is not int or size != 1:
            raise ValueError('control')
        self._stop.wait()
        if self._sent:
            return b''
        self._sent = True
        return b'S'


class _StatusOutput:
    def __init__(self, owner):
        self._owner, self._buffer = owner, bytearray()

    def write(self, data):
        try:
            raw = bytes(data)
            if not raw or len(self._buffer) + len(raw) > _MAX_STATUS:
                raise ValueError('status_size')
            self._buffer.extend(raw)
            while b'\n' in self._buffer:
                position = self._buffer.index(b'\n')
                line = bytes(self._buffer[:position])
                del self._buffer[:position + 1]
                value = json.loads(line.decode('utf-8'), object_pairs_hook=_unique,
                                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError('constant')))
                keys = {'schema', 'state', 'manifest_sha256', 'authority', 'caller_authentication',
                        'source_authentication', 'index_refresh'}
                if (type(value) is not dict or set(value) != keys
                        or value['schema'] != 'wd.mind-local-owner-status.v1'
                        or value['state'] not in ('STARTING', 'READY', 'HOLD', 'STOPPED')
                        or value['manifest_sha256'] != self._owner._manifest
                        or value['authority'] != 'none'
                        or value['caller_authentication'] != 'UNKNOWN'
                        or value['source_authentication'] != 'UNKNOWN'
                        or value['index_refresh'] != 'NOT_SCHEDULED'):
                    raise ValueError('status_binding')
                with self._owner._lock:
                    previous = self._owner._reported
                    if (previous == 'STOPPED' or (previous == 'HOLD' and value['state'] in ('STARTING', 'READY'))
                            or (previous == 'READY' and value['state'] == 'STARTING')):
                        raise ValueError('status_regression')
                    self._owner._reported = value['state']
            return len(raw)
        except BaseException:
            self._buffer.clear()
            raise HostRefused('guardian_host_refused') from None

    def flush(self):
        if self._buffer:
            raise HostRefused('guardian_host_refused')


class _OwnedHost:
    def __init__(self, enabled, bundle, manifest, policy_path, policy_sha, approval_sha):
        self._enabled, self._bundle, self._manifest = enabled, bundle, manifest
        self._policy_path, self._policy_sha, self._approval_sha = policy_path, policy_sha, approval_sha
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._launch_gate = threading.Lock()
        self._owner_start_inflight = threading.Event()
        self._thread = None
        self._state, self._reported = ('NEW' if enabled else 'OFF'), None
        self._control, self._output = _StopControl(self._stop), _StatusOutput(self)

    def start(self):
        with self._lock:
            if not self._enabled:
                return self._status_locked()
            if not _BOOTSTRAP_PROVEN:
                self._state = 'HOLD'
                return self._status_locked()
            if self._state != 'NEW' or self._stop.is_set():
                return {'state':'HOLD', 'authority':'none', 'crash_ownership':'UNKNOWN'}
            self._state = 'STARTING'
            self._thread = threading.Thread(target=self._run, name='mind-owned-host', daemon=False)
            try:
                self._thread.start()
            except BaseException:
                self._state = 'HOLD'  # Retain the possibly started thread.
            return self._status_locked()

    def _run(self):
        result = None
        try:
            # The closed bootstrap gate is checked before this dependency can
            # be imported. The future pinned launcher must verify both files.
            if not _BOOTSTRAP_PROVEN:
                raise HostRefused('guardian_host_refused')
            # This is the commitment to enter the guarded child. A stop that
            # wins this gate prevents entry; after commitment, stop remains
            # cooperative and child startup/cleanup may still be in progress.
            with self._launch_gate:
                stopped_before_entry = self._stop.is_set()
            if stopped_before_entry:
                with self._lock:
                    self._state = 'STOPPED'
                return
            from tools import wd_mind_runtime_child as child
            result = child._guarded_local_entry(child._local_candidate_owner_entry,
                control_stream=self._control, output_stream=self._output, bundle_root=self._bundle,
                expected_manifest_sha256=self._manifest, policy_path=self._policy_path,
                policy_sha256=self._policy_sha, approval_reference_sha256=self._approval_sha,
                _host_start_gate=self._launch_gate, _host_stop=self._stop,
                _host_owner_start_inflight=self._owner_start_inflight)
        except BaseException:
            pass
        with self._lock:
            self._state = ('STOPPED' if type(result) is int and result == 0
                           and self._reported == 'STOPPED' and self._stop.is_set() else 'HOLD')

    def request_stop(self):
        if not self._launch_gate.acquire(blocking=False):
            return {'state':'BUSY', 'authority':'none', 'crash_ownership':'UNKNOWN'}
        try:
            if self._owner_start_inflight.is_set():
                return {'state':'BUSY', 'authority':'none', 'crash_ownership':'UNKNOWN'}
            self._stop.set()
        finally:
            self._launch_gate.release()
        return self.status()

    def _status_locked(self):
        state = self._state
        if state == 'STARTING' and self._reported in ('READY', 'HOLD'):
            state = self._reported
        if self._stop.is_set() and state not in ('OFF', 'STOPPED'):
            state = 'HOLD'
        return {'state':state, 'authority':'none', 'crash_ownership':'UNKNOWN'}

    def status(self):
        if not self._lock.acquire(blocking=False):
            return {'state':'HOLD', 'authority':'none', 'crash_ownership':'UNKNOWN'}
        try:
            return self._status_locked()
        finally:
            self._lock.release()


def create_host(*, enabled=False, bundle_root=None, manifest_sha256=None, policy_path=None,
                policy_sha256=None, approval_reference_sha256=None):
    if type(enabled) is not bool:
        raise HostRefused('guardian_host_refused')
    if enabled and not _BOOTSTRAP_PROVEN:
        raise HostRefused('guardian_host_refused')
    if (type(bundle_root) is not str or not Path(bundle_root).is_absolute()
            or type(policy_path) is not str or not Path(policy_path).is_absolute()
            or any(type(value) is not str or not _HEX.fullmatch(value)
                   for value in (manifest_sha256, policy_sha256, approval_reference_sha256))):
        raise HostRefused('guardian_host_refused')
    return _OwnedHost(enabled, bundle_root, manifest_sha256, policy_path, policy_sha256,
                      approval_reference_sha256)
