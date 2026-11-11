"""Only the controller's own synthetic child or in-memory fakes are used."""
import subprocess
import sys
from types import SimpleNamespace

import pytest


PID = 12345
CREATED = 133000000000000001
IMAGE = r'C:\synthetic\python.exe'


class Handle:
    def __init__(self): self.closed = False
    def __int__(self): return 2468
    def Close(self): self.closed = True


class Stdin:
    def __init__(self): self.closed = False
    def close(self): self.closed = True


class Process:
    def __init__(self):
        self.pid = PID; self._handle = Handle(); self.stdin = Stdin(); self.waits = []
    def wait(self, timeout):
        self.waits.append(timeout)
        assert self.stdin.closed
        return 0
    def kill(self): raise AssertionError('must not terminate process')
    terminate = kill


class Api:
    def __init__(self):
        self.ids = [(PID, CREATED, IMAGE)] * 2
        self.alives = [True, True]; self.handles = []
    def identity(self, handle): self.handles.append(handle); return self.ids.pop(0)
    def alive(self, handle): self.handles.append(handle); return self.alives.pop(0)


def subject():
    from tools import wd_synthetic_launch_controller as module
    return module


def setup_fake(monkeypatch):
    m = subject(); process = Process(); api = Api(); calls = []
    monkeypatch.setenv('SystemRoot', r'C:\Windows')
    monkeypatch.setattr(m, '_windows', lambda: True)
    monkeypatch.setattr(m, '_controller_executable', lambda: IMAGE)
    monkeypatch.setattr(m, '_new_api', lambda: api)
    def spawn(*args, **kwargs): calls.append((args, kwargs)); return process
    monkeypatch.setattr(m.subprocess, 'Popen', spawn)
    return m, process, api, calls


def test_default_off_does_not_spawn(monkeypatch):
    m = subject()
    def forbidden(*_a, **_k): raise AssertionError('must not spawn')
    monkeypatch.setattr(m.subprocess, 'Popen', forbidden)
    result = m.run_synthetic_controller()
    assert result['status'] == 'OFF' and result['authority_effect'] == 'none'


def test_owned_handle_is_used_and_child_exits_without_termination(monkeypatch):
    m, process, api, calls = setup_fake(monkeypatch)
    result = m.run_synthetic_controller(enabled=True, deadline_seconds=5)
    assert result['status'] == 'SYNTHETIC_CHILD_OBSERVED_AND_EXITED'
    assert result['pid'] == PID and result['creation_filetime'] == CREATED and result['image_path'] == IMAGE
    assert api.handles == [2468, 2468, 2468, 2468]
    assert process.stdin.closed and process._handle.closed and len(process.waits) == 1
    assert calls[0][0][0] == [IMAGE, '-I', '-S', '-B', '-c', m._SYNTHETIC_CHILD_CODE]
    assert calls[0][1]['stdin'] == subprocess.PIPE and calls[0][1]['shell'] is False
    assert calls[0][1]['close_fds'] is True
    assert calls[0][1]['cwd'] == calls[0][1]['env']['SystemRoot']
    assert set(calls[0][1]['env']) == {'SystemRoot', 'WINDIR'}
    assert result['authority_effect'] == 'none' and result['child_cleanup_confirmed']
    assert not any(result[k] for k in ('anchor_authenticated', 'image_bytes_verified', 'peer_channel_verified',
                                         'uac_verified', 'lane_startup_verified', 'rollback_performed',
                                         'hard_wall_clock_deadline_enforced'))


@pytest.mark.parametrize('fault', ['bad_pid', 'different_creation', 'different_image', 'exited_before',
                                  'exited_after', 'api_failure', 'close_failure', 'child_timeout', 'bad_exit',
                                  'boolean_exit', 'nonbool_alive', 'boolean_second_creation', 'pid_overflow'])
def test_mismatch_or_cleanup_failure_holds_without_killing(monkeypatch, fault):
    m, process, api, _calls = setup_fake(monkeypatch)
    timeout_calls = []
    if fault == 'bad_pid': api.ids[0] = (PID+1, CREATED, IMAGE)
    if fault == 'different_creation': api.ids[1] = (PID, CREATED+1, IMAGE)
    if fault == 'different_image': api.ids[1] = (PID, CREATED, IMAGE.upper())
    if fault == 'exited_before': api.alives[0] = False
    if fault == 'exited_after': api.alives[1] = False
    if fault == 'api_failure':
        def fail(_handle): raise OSError('synthetic denied')
        api.identity = fail
    if fault == 'close_failure':
        def fail_close(): raise OSError('synthetic handle close failure')
        process._handle.Close = fail_close
    if fault == 'child_timeout':
        def timeout(timeout):
            timeout_calls.append(timeout)
            raise subprocess.TimeoutExpired('synthetic', timeout)
        process.wait = timeout
    if fault == 'bad_exit': process.wait = lambda timeout: 4
    if fault == 'boolean_exit': process.wait = lambda timeout: False
    if fault == 'nonbool_alive': api.alives[0] = 1
    if fault == 'boolean_second_creation': api.ids = [(PID, 1, IMAGE), (PID, True, IMAGE)]
    if fault == 'pid_overflow':
        process.pid = 2**32; api.ids = [(2**32, CREATED, IMAGE)] * 2
    result = m.run_synthetic_controller(enabled=True, deadline_seconds=5)
    assert result['status'] == 'HOLD' and result['authority_effect'] == 'none'
    assert process.stdin.closed and not result['uac_verified'] and not result['rollback_performed']
    if fault == 'child_timeout':
        assert len(timeout_calls) == 1 and timeout_calls[0] >= 0
        assert not result['child_cleanup_confirmed']


@pytest.mark.parametrize('enabled,seconds', [(1, 1), (None, 1), (True, 0), (True, 31), (True, True),
                                            (True, float('nan')), (True, float('inf')),
                                            (True, 10**400), (True, -(10**400))])
def test_invalid_controls_do_not_spawn(monkeypatch, enabled, seconds):
    m, _process, _api, calls = setup_fake(monkeypatch)
    assert m.run_synthetic_controller(enabled=enabled, deadline_seconds=seconds)['status'] == 'HOLD'
    assert not calls


def test_expired_deadline_after_spawn_holds_and_still_closes_own_input(monkeypatch):
    m, process, _api, _calls = setup_fake(monkeypatch)
    moments = iter([100., 106., 106.])
    monkeypatch.setattr(m.time, 'monotonic', lambda: next(moments, 106.))
    result = m.run_synthetic_controller(enabled=True, deadline_seconds=5)
    assert result['status'] == 'HOLD' and process.stdin.closed
    assert not result['hard_wall_clock_deadline_enforced']


def test_final_clock_error_holds_after_child_cleanup(monkeypatch):
    m, process, api, _calls = setup_fake(monkeypatch)
    calls = []
    def clock():
        calls.append(1)
        if len(calls) == 5:
            raise OSError('synthetic final clock failure')
        return 100.0
    monkeypatch.setattr(m, 'time', SimpleNamespace(monotonic=clock))
    result = m.run_synthetic_controller(enabled=True, deadline_seconds=5)
    assert len(calls) == 5 and len(api.handles) == 4
    assert process.stdin.closed and process._handle.closed and len(process.waits) == 1
    assert result['status'] == 'HOLD' and result['pid'] is None
    assert result['child_cleanup_confirmed'] and result['handle_release_confirmed']


@pytest.mark.skipif(sys.platform != 'win32', reason='owned Windows process handle')
def test_real_owned_synthetic_child_exits_naturally():
    result = subject().run_synthetic_controller(enabled=True, deadline_seconds=10)
    assert result['status'] == 'SYNTHETIC_CHILD_OBSERVED_AND_EXITED'
    assert result['pid'] > 0 and result['creation_filetime'] > 0
    assert result['image_path'] == sys.executable
    assert result['child_cleanup_confirmed'] and result['authority_effect'] == 'none'
    assert not result['uac_verified'] and not result['hard_wall_clock_deadline_enforced']
