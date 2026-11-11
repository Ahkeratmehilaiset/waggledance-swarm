"""Process identity probes use synthetic ports or this test's own child only."""
import os
import subprocess
import sys

import pytest


IMAGE = r'C:\synthetic\worker.exe'
PID = 12345
CREATED = 133000000000000001


class FakeApi:
    def __init__(self):
        self.identity_values = [(PID, CREATED, IMAGE), (PID, CREATED, IMAGE)]
        self.alive_values = [True, True]
        self.closed = 0
        self.opened = []

    def open(self, pid):
        self.opened.append(pid)
        return object()

    def identity(self, _handle):
        return self.identity_values.pop(0)

    def alive(self, _handle):
        return self.alive_values.pop(0)

    def close(self, _handle):
        self.closed += 1


def subject():
    from tools import wd_startup_process_identity as module
    return module


def request(**kwargs):
    args = dict(enabled=True, expected_pid=PID, expected_creation_filetime=CREATED, expected_image_path=IMAGE)
    args.update(kwargs)
    return subject().inspect_process_instance(**args)


def setup_fake(monkeypatch, api):
    module = subject()
    monkeypatch.setattr(module, '_windows', lambda: True)
    monkeypatch.setattr(module, '_new_api', lambda: api)


def test_default_off_never_initializes_windows_api(monkeypatch):
    module = subject()
    def forbidden(): raise AssertionError('OFF called API')
    monkeypatch.setattr(module, '_new_api', forbidden)
    result = module.inspect_process_instance()
    assert result['status'] == 'OFF' and result['authority_effect'] == 'none'


def test_matching_tuple_is_only_instantaneous_process_observation(monkeypatch):
    api = FakeApi(); setup_fake(monkeypatch, api)
    result = request()
    assert result['status'] == 'PROCESS_INSTANCE_OBSERVED'
    assert result['instance_tuple_matches'] and result['live_at_last_probe']
    for key in ('anchor_authenticated', 'image_bytes_verified', 'full_startup_verified',
                'future_liveness_verified', 'rollback_performed'):
        assert result[key] is False
    assert result['authority_effect'] == 'none' and api.opened == [PID] and api.closed == 1


@pytest.mark.parametrize('fault', ['pid', 'creation', 'image', 'exit_before', 'exit_after',
                                  'creation_changes', 'pid_changes', 'image_changes', 'api_failure', 'close_failure'])
def test_mismatch_exit_failure_or_reused_pid_holds(monkeypatch, fault):
    api = FakeApi()
    if fault == 'pid': api.identity_values[0] = (PID + 1, CREATED, IMAGE)
    elif fault == 'creation': api.identity_values[0] = (PID, CREATED + 1, IMAGE)
    elif fault == 'image': api.identity_values[0] = (PID, CREATED, r'C:\synthetic\other.exe')
    elif fault == 'exit_before': api.alive_values[0] = False
    elif fault == 'exit_after': api.alive_values[1] = False
    elif fault == 'creation_changes': api.identity_values[1] = (PID, CREATED + 1, IMAGE)
    elif fault == 'pid_changes': api.identity_values[1] = (PID + 1, CREATED, IMAGE)
    elif fault == 'image_changes': api.identity_values[1] = (PID, CREATED, r'C:\synthetic\other.exe')
    elif fault == 'api_failure':
        def denied(_pid): raise OSError('synthetic access denied')
        api.open = denied
    elif fault == 'close_failure':
        def failed_close(_handle): raise OSError('synthetic close failure')
        api.close = failed_close
    setup_fake(monkeypatch, api)
    result = request()
    assert result['status'] == 'HOLD' and not result['instance_tuple_matches']
    assert not result['live_at_last_probe'] and result['authority_effect'] == 'none'
    if fault not in ('api_failure', 'close_failure'): assert api.closed == 1


@pytest.mark.parametrize('field,value', [
    ('enabled', 1), ('enabled', None), ('expected_pid', True), ('expected_pid', 0),
    ('expected_pid', 2**32), ('expected_creation_filetime', True), ('expected_creation_filetime', 0),
    ('expected_creation_filetime', 2**64), ('expected_image_path', ''),
    ('expected_image_path', r'C:\synthetic\..\worker.exe'), ('expected_image_path', r'\\server\worker.exe'),
    ('expected_image_path', r'C:\synthetic\workér.exe'),
])
def test_invalid_expected_tuple_holds_without_opening(monkeypatch, field, value):
    api = FakeApi(); setup_fake(monkeypatch, api)
    result = request(**{field:value})
    assert result['status'] == 'HOLD' and not api.opened and result['authority_effect'] == 'none'


def test_only_ascii_case_changes_in_image_path_are_accepted(monkeypatch):
    api = FakeApi(); api.identity_values = [(PID, CREATED, IMAGE.upper())] * 2
    setup_fake(monkeypatch, api)
    assert request()['status'] == 'PROCESS_INSTANCE_OBSERVED'


@pytest.mark.skipif(os.name != 'nt', reason='Windows kernel process-handle integration')
def test_actual_kernel_handle_on_owned_synthetic_child():
    import ctypes
    from ctypes import wintypes
    class FileTime(ctypes.Structure):
        _fields_ = [('low', wintypes.DWORD), ('high', wintypes.DWORD)]
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(FileTime)] * 4
    k.GetProcessTimes.restype = wintypes.BOOL
    process = subprocess.Popen([sys.executable, '-I', '-c', 'import sys;sys.stdin.buffer.read(1)'],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        created, exited, kernel, user = (FileTime() for _ in range(4))
        assert k.GetProcessTimes(int(process._handle), ctypes.byref(created), ctypes.byref(exited),
                                 ctypes.byref(kernel), ctypes.byref(user))
        filetime = (created.high << 32) | created.low
        expected = dict(enabled=True, expected_pid=process.pid, expected_creation_filetime=filetime,
                        expected_image_path=os.path.realpath(sys.executable))
        assert subject().inspect_process_instance(**expected)['status'] == 'PROCESS_INSTANCE_OBSERVED'
        expected['expected_creation_filetime'] += 1
        assert subject().inspect_process_instance(**expected)['status'] == 'HOLD'
        expected['expected_creation_filetime'] = filetime
        process.stdin.close()
        assert process.wait(timeout=10) == 0
        assert subject().inspect_process_instance(**expected)['status'] == 'HOLD'
    finally:
        if not process.stdin.closed: process.stdin.close()
        process.wait(timeout=10)
