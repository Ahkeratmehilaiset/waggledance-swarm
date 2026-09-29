"""The stall detector posts only through a pinned bundle writer (2026-09-29).

Before this, C:\\Python\\wd_consensus_stall_detector.py posted through the July
runtime-root .agent-bridge\\bin\\Write-AgentEvent.ps1, which no deployment pins.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'tools' / 'wd_consensus_stall_detector.py'
WRITER_RELATIVE = 'tools-bootstrap/.agent-bridge/bin/Write-AgentEvent.ps1'


def load_detector():
    spec = importlib.util.spec_from_file_location('stall_detector_under_test', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pinned_bundle(root: Path, writer: bytes = b'# pinned writer') -> tuple[Path, str]:
    target = root / WRITER_RELATIVE
    target.parent.mkdir(parents=True)
    target.write_bytes(writer)
    manifest = {'files': {WRITER_RELATIVE.replace('/', '\\'): hashlib.sha256(writer).hexdigest().upper()}}
    raw = json.dumps(manifest).encode('utf-8')
    (root / 'deployment-manifest.json').write_bytes(raw)
    return root, hashlib.sha256(raw).hexdigest().upper()


def record_runs(module, monkeypatch, returncode=0):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=returncode, stdout='', stderr='')

    monkeypatch.setattr(module.subprocess, 'run', run)
    return calls


def test_a_dry_run_never_posts(monkeypatch):
    module = load_detector()
    calls = record_runs(module, monkeypatch)
    assert module.post_alert('branch', 'message', False) == 'DRY-ALERT'
    assert calls == []


def test_without_a_pin_the_alert_is_skipped_and_nothing_runs(monkeypatch):
    module = load_detector()
    calls = record_runs(module, monkeypatch)
    assert module.post_alert('branch', 'message', True) == 'ALERT-SKIPPED:unpinned-writer'
    assert calls == []


def test_a_pinned_alert_runs_the_verified_bundle_writer_with_a_clean_identity(monkeypatch, tmp_path):
    module = load_detector()
    bundle, sha = pinned_bundle(tmp_path / 'bundle')
    monkeypatch.setenv('AGENT_BRIDGE_AGENT_UUID', 'inherited-lane-uuid')
    calls = record_runs(module, monkeypatch)
    note = module.post_alert('fix/x', 'stalled', True, recipients=['claude-rco-1'],
                             payload_head='a' * 40, pin=(str(bundle), sha))
    assert note == 'ALERTED->claude-rco-1'
    [(command, kwargs)] = calls
    assert command[:5] == ['pwsh', '-NoProfile', '-NonInteractive', '-File',
                           str((bundle / WRITER_RELATIVE).resolve())]
    assert command[command.index('-Agent') + 1] == 'wd-stall-monitor'
    assert command[command.index('-Role') + 1] == 'monitor'
    assert command[command.index('-Type') + 1] == 'message'
    assert json.loads(command[command.index('-PayloadJson') + 1]) == {'head': 'a' * 40}
    env = kwargs['env']
    assert env['AGENT_BRIDGE_RUNTIME_ROOT'] == str(module.RUNTIME_ROOT)
    assert 'AGENT_BRIDGE_AGENT_UUID' not in env


@pytest.mark.parametrize('case', ['wrong_manifest_hash', 'changed_writer', 'writer_not_pinned', 'no_bundle'])
def test_an_unverified_pin_fails_closed_without_running_anything(monkeypatch, tmp_path, case):
    module = load_detector()
    bundle, sha = pinned_bundle(tmp_path / 'bundle')
    if case == 'wrong_manifest_hash':
        sha = '0' * 64
    elif case == 'changed_writer':
        (bundle / WRITER_RELATIVE).write_bytes(b'# tampered writer')
    elif case == 'writer_not_pinned':
        raw = json.dumps({'files': {}}).encode('utf-8')
        (bundle / 'deployment-manifest.json').write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
    elif case == 'no_bundle':
        bundle = tmp_path / 'missing'
    calls = record_runs(module, monkeypatch)
    note = module.post_alert('branch', 'message', True, pin=(str(bundle), sha))
    assert note.startswith('ALERT-FAILED:')
    assert calls == []


def test_a_failed_writer_is_not_reported_as_alerted(monkeypatch, tmp_path):
    module = load_detector()
    bundle, sha = pinned_bundle(tmp_path / 'bundle')
    record_runs(module, monkeypatch, returncode=1)
    assert module.post_alert('branch', 'message', True, pin=(str(bundle), sha)) == 'ALERT-FAILED:exit-1'


def test_the_runtime_root_writer_is_gone():
    text = SOURCE.read_text(encoding='utf-8')
    assert 'RUNTIME_ROOT / "bin"' not in text
    assert 'WRITER' not in text


@pytest.mark.parametrize('argv', [['--bridge-bundle', 'x'], ['--bridge-manifest-sha256', 'A' * 64]])
def test_half_a_pin_is_a_usage_error(argv):
    module = load_detector()
    with pytest.raises(SystemExit) as excinfo:
        module.main(['--alert', *argv])
    assert excinfo.value.code == 2
