"""Synthetic subprocess tests: never run pip, a real wheel or a real installer."""

import base64
import csv
import hashlib
import io
import subprocess
from types import SimpleNamespace
from pathlib import Path
import zipfile

import pytest


def digest(data):
    return hashlib.sha256(data).hexdigest()


def synthetic_wheel(name, *, broken_record=False):
    info = f'{name}-1.0.dist-info'
    files = {
        f'{name}/__init__.py': b'# synthetic; never execute\n',
        f'{info}/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n',
        f'{info}/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n\n'.encode(),
    }
    rows = []
    for path, data in files.items():
        encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        rows.append([path, 'sha256=' + encoded, str(len(data))])
    if broken_record:
        rows[0][1] = 'sha256=' + 'A' * 43
    rows.append([f'{info}/RECORD', '', ''])
    record = io.StringIO(newline='')
    csv.writer(record, lineterminator='\n').writerows(rows)
    files[f'{info}/RECORD'] = record.getvalue().encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return buffer.getvalue()


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    import shutil

    audit = tmp_path / '.codex-audit'
    audit.mkdir()
    roots = [tmp_path / 'wheels1', tmp_path / 'wheels2']
    for root in roots:
        root.mkdir()
    wheels = []
    for root, name in zip(roots, ['alpha', 'beta']):
        data = synthetic_wheel(name)
        path = root / f'{name}-1.0-py3-none-any.whl'
        path.write_bytes(data)
        wheels.append({'path': path, 'size': len(data), 'sha256': digest(data)})
    executable = tmp_path / 'python.exe'
    executable.write_bytes(b'not-an-executable')
    lock = ''.join(f'{name}==1.0 --hash=sha256:{item["sha256"]}\n'
                   for name, item in zip(('alpha', 'beta'), wheels)).encode()
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=30 * 1024**3))
    monkeypatch.setenv('SECRET_THAT_MUST_NOT_LEAK', 'synthetic-secret')
    return dict(audit_root=audit, candidate_name='candidate', python_executable=executable,
                python_sha256=digest(b'not-an-executable'), lock_bytes=lock,
                lock_sha256=digest(lock), wheel_roots=roots, wheels=wheels,
                max_wheel_bytes=100_000, peak_reservation_bytes=1024**3,
                system_root=tmp_path, deadline_seconds=0.1)


def run(**kwargs):
    from tools.q4_offline_materializer import materialize
    return materialize(**kwargs)


class FakeProcess:
    def __init__(self, *, output=b'ok', error=b'', code=0, hung=False, kill_fails=False):
        self.stdout, self.stderr = io.BytesIO(output), io.BytesIO(error)
        self.returncode, self.hung, self.kill_fails = code, hung, kill_fails
        self.killed = False

    def poll(self):
        return None if self.hung else self.returncode

    def kill(self):
        self.killed = True
        if self.kill_fails:
            raise OSError('synthetic kill failure')
        self.hung = False
        self.returncode = 9

    def wait(self, timeout=None):
        if self.hung:
            raise subprocess.TimeoutExpired('synthetic', timeout)
        return self.returncode


def test_disabled_never_starts_or_writes(candidate, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('launched'))
    result = run(**candidate)
    assert result['state'] == 'DISABLED'
    assert not (candidate['audit_root'] / 'candidate').exists()


def test_valid_synthetic_launch_uses_only_pinned_offline_inputs(candidate, monkeypatch):
    calls = []
    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeProcess(output=b'hello')
    monkeypatch.setattr(subprocess, 'Popen', fake_popen)
    result = run(enabled=True, **candidate)
    assert result['state'] == 'EXIT_ZERO_UNVERIFIED'
    assert result['production'] == 'NOT_INSTALLED'
    assert result['dependency_closure'] == 'NOT_VERIFIED'
    assert result['process_tree'] == 'NOT_VERIFIED'
    argv, params = calls[0]
    assert Path(argv[0]) == candidate['python_executable']
    assert '--no-index' in argv and '--require-hashes' in argv and '--no-deps' in argv
    assert '-I' in argv and '--target' in argv
    assert Path(argv[argv.index('--target') + 1]).parent == candidate['audit_root'] / 'candidate'
    assert params['env'].get('SECRET_THAT_MUST_NOT_LEAK') is None
    assert result['stdout'] == 'hello'


def test_bad_hash_and_existing_target_never_launch(candidate, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('launched'))
    with pytest.raises(ValueError):
        run(enabled=True, **{**candidate, 'python_sha256': '0' * 64})
    (candidate['audit_root'] / 'candidate').mkdir()
    with pytest.raises(ValueError):
        run(enabled=True, **candidate)


def test_invalid_record_is_refused_even_if_entire_archive_hash_is_pinned(candidate, monkeypatch):
    data = synthetic_wheel('alpha', broken_record=True)
    wheel = candidate['wheels'][0]
    wheel['path'].write_bytes(data)
    wheel['size'], wheel['sha256'] = len(data), digest(data)
    lock = (f'alpha==1.0 --hash=sha256:{wheel["sha256"]}\n'
            f'beta==1.0 --hash=sha256:{candidate["wheels"][1]["sha256"]}\n').encode()
    candidate['lock_bytes'], candidate['lock_sha256'] = lock, digest(lock)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('invalid wheel launched'))
    with pytest.raises(ValueError, match='wheel_inspection_refused'):
        run(enabled=True, **candidate)
    assert not (candidate['audit_root'] / 'candidate').exists()


def test_reservation_must_cover_inspected_uncompressed_bytes(candidate, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('underbudget launch'))
    totals = []
    for wheel in candidate['wheels']:
        with zipfile.ZipFile(wheel['path']) as archive:
            totals.append(sum(item.file_size for item in archive.infolist()))
    reserve = sum(totals) - 1
    assert reserve >= max(totals)
    with pytest.raises(ValueError, match='uncompressed_budget_exceeded'):
        run(enabled=True, **{**candidate, 'peak_reservation_bytes': reserve})
    assert not (candidate['audit_root'] / 'candidate').exists()


def test_fast_exit_does_not_hide_disk_floor_crossing(candidate, monkeypatch):
    import shutil
    samples = iter([30 * 1024**3, 30 * 1024**3, 30 * 1024**3, 3 * 1024**3])
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=next(samples)))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: FakeProcess())
    result = run(enabled=True, **candidate)
    assert result['state'] == 'LOW_DISK_AFTER_EXIT'
    assert result['disk_floor_guarantee'] == 'NOT_GUARANTEED'


def test_low_space_never_starts_or_writes(candidate, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=3 * 1024**3))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('launched'))
    result = run(enabled=True, **candidate)
    assert result['state'] == 'LOW_DISK_PREFLIGHT'
    assert not (candidate['audit_root'] / 'candidate').exists()


def test_final_prelaunch_sample_must_cover_peak_not_just_wheels(candidate, monkeypatch):
    import shutil
    floor = 3 * 1024**3
    samples = iter([30 * 1024**3, 30 * 1024**3, floor + candidate['max_wheel_bytes'] + 1])
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=next(samples)))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('underbudget launch'))
    result = run(enabled=True, **candidate)
    assert result['state'] == 'LOW_DISK_BEFORE_LAUNCH'
    assert (candidate['audit_root'] / 'candidate').exists()


def test_timeout_preserves_candidate_and_does_not_claim_tree_stopped(candidate, monkeypatch):
    process = FakeProcess(hung=True)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: process)
    result = run(enabled=True, **candidate)
    assert process.killed
    assert result['state'] == 'TIMEOUT_DIRECT_PROCESS_STOPPED_TREE_UNKNOWN'
    assert result['process_tree'] == 'NOT_VERIFIED'
    assert (candidate['audit_root'] / 'candidate').exists()


def test_failed_kill_is_incomplete_and_retains_artifacts(candidate, monkeypatch):
    process = FakeProcess(hung=True, kill_fails=True)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: process)
    result = run(enabled=True, **candidate)
    assert result['state'] == 'INCOMPLETE_KILL_OR_WAIT_FAILED'
    assert (candidate['audit_root'] / 'candidate').exists()


def test_output_capture_is_bounded(candidate, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: FakeProcess(output=b'x' * 100000))
    result = run(enabled=True, **candidate)
    assert len(result['stdout'].encode()) <= 16384
    assert result['stdout_truncated'] is True


def test_disk_falls_after_launch_only_own_direct_process_killed(candidate, monkeypatch):
    import shutil
    samples = iter([30 * 1024**3, 30 * 1024**3, 30 * 1024**3, 3 * 1024**3])
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=next(samples)))
    process = FakeProcess(hung=True)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: process)
    result = run(enabled=True, **candidate)
    assert process.killed
    assert result['state'] == 'LOW_DISK_DIRECT_PROCESS_STOPPED_TREE_UNKNOWN'
    assert result['disk_floor_guarantee'] == 'NOT_GUARANTEED'


def test_launch_failure_is_incomplete_and_preserves_partial(candidate, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('do not leak this path into result')
    monkeypatch.setattr(subprocess, 'Popen', fail)
    result = run(enabled=True, **candidate)
    assert result['state'] == 'LAUNCH_FAILED'
    assert (candidate['audit_root'] / 'candidate' / 'requirements.lock').read_bytes() == candidate['lock_bytes']


def test_wait_failure_is_never_success(candidate, monkeypatch):
    class WaitFails(FakeProcess):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired('synthetic', timeout)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: WaitFails())
    assert run(enabled=True, **candidate)['state'] == 'INCOMPLETE_WAIT_OR_CAPTURE'


@pytest.mark.parametrize('name', ['../escape', 'a/b', '', 'a\\b', '..'])
def test_candidate_name_cannot_escape_audit(candidate, monkeypatch, name):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('launched'))
    with pytest.raises(ValueError):
        run(enabled=True, **{**candidate, 'candidate_name': name})
