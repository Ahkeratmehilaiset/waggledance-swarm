"""The stall detector posts only through a pinned bundle writer (2026-09-29).

Before this, C:\\Python\\wd_consensus_stall_detector.py posted through the July
runtime-root .agent-bridge\\bin\\Write-AgentEvent.ps1, which no deployment pins.
"""
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'tools' / 'wd_consensus_stall_detector.py'
WRITER_RELATIVE = 'tools-bootstrap/.agent-bridge/bin/Write-AgentEvent.ps1'
REAL_RUN = subprocess.run


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


def receipt(module, **delivery) -> bytes:
    """The writer's -ReceiptJson stdout: the event with its _bridge_delivery receipt."""
    fields = {'schema': 'waggledance.bridge.delivery-receipt.v1', 'accepted': True,
              'delivery_status': 'canonical', 'canonical_durable': True, 'events_path': str(module.BRIDGE)}
    fields.update(delivery)
    return (json.dumps({'type': 'message', '_bridge_delivery': fields}) + '\r\n').encode('utf-8')


def record_runs(module, monkeypatch, returncode=0, stdout=None):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=returncode, stdout=receipt(module) if stdout is None else stdout,
                               stderr=b'')

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
    assert '-ReceiptJson' in command
    assert 'text' not in kwargs and 'encoding' not in kwargs
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


PR = {'number': 7, 'headRefName': 'fix/x', 'headRefOid': 'a' * 40, 'mergeStateStatus': 'CLEAN'}


def stalled(module, eligible: bool) -> tuple[dict, dict, object]:
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    d = dict(module.diagnose(PR, []), alert_eligible=eligible)
    state = {f"{d['pr']}@{d['head']}": {'first_seen': (now - timedelta(days=1)).isoformat()}}
    return d, state, now


def test_no_repository_code_is_imported_for_classification(monkeypatch):
    module = load_detector()
    imported = []
    real_import = __builtins__['__import__'] if isinstance(__builtins__, dict) else __builtins__.__import__

    def watch(name, *args, **kwargs):
        imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr('builtins.__import__', watch)
    assert module.is_operator_signature_class(PR) is None
    module.diagnose(PR, [])
    assert not [name for name in imported if 'check_standing_consensus_sign_class' in name]
    text = SOURCE.read_text(encoding='utf-8')
    for unpinned in ('sys.path', '_wd_tools_current', 'C:/Python/project2-master', 'import classify_ab'):
        assert unpinned not in text


def test_an_unclassified_stall_is_diagnosed_but_not_alert_eligible():
    module = load_detector()
    d = module.diagnose(PR, [])
    assert d['problems'] == ['lead_build', 'tools_build', 'rco1', 'rco2']
    assert d['alert_eligible'] is False


def test_an_operator_signature_hold_still_suppresses_the_stall():
    module = load_detector()
    hold = {'agent': 'codex-lead-1', 'task_id': 'fix/x', 'status': 'operator_signature_required'}
    assert module.diagnose(PR, [hold]) is None
    assert module.diagnose(PR, [dict(hold, agent='someone-else')]) is not None


def test_an_unclassified_stall_is_withheld_even_with_a_pin_and_posting(monkeypatch, tmp_path):
    module = load_detector()
    bundle, sha = pinned_bundle(tmp_path / 'bundle')
    calls = record_runs(module, monkeypatch)
    d, state, now = stalled(module, eligible=False)
    assert module.maybe_alert(d, state, now, True, pin=(str(bundle), sha)) == 'ALERT-WITHHELD:unclassified'
    assert calls == []
    assert 'last_alert' not in state[f"{d['pr']}@{d['head']}"]


def test_a_classified_stall_with_a_pin_is_alerted(monkeypatch, tmp_path):
    module = load_detector()
    bundle, sha = pinned_bundle(tmp_path / 'bundle')
    calls = record_runs(module, monkeypatch)
    d, state, now = stalled(module, eligible=True)
    note = module.maybe_alert(d, state, now, True, pin=(str(bundle), sha))
    assert note.startswith('ALERTED->')
    assert len(calls) == 1


@pytest.mark.parametrize('note', ['DRY-ALERT', 'ALERT-SKIPPED:unpinned-writer', 'ALERT-FAILED:exit-1',
                                  'ALERT-FAILED:TimeoutExpired', 'ALERT-UNCONFIRMED:queued',
                                  'ALERT-WITHHELD:unclassified'])
def test_only_a_posted_alert_starts_the_quiet_period(monkeypatch, note):
    module = load_detector()
    d, state, now = stalled(module, eligible=True)
    monkeypatch.setattr(module, 'post_alert', lambda *args, **kwargs: note)
    assert module.maybe_alert(d, state, now, True) == note
    assert 'last_alert' not in state[f"{d['pr']}@{d['head']}"]
    monkeypatch.setattr(module, 'post_alert', lambda *args, **kwargs: 'ALERTED->codex-lead-1')
    assert module.maybe_alert(d, state, now, True) == 'ALERTED->codex-lead-1'
    assert state[f"{d['pr']}@{d['head']}"]['last_alert'] == now.isoformat()
    assert module.maybe_alert(d, state, now, True) == 'recently-alerted'


def test_an_unpinned_run_does_not_hold_back_the_next_pinned_run(monkeypatch, tmp_path):
    module = load_detector()
    bundle, sha = pinned_bundle(tmp_path / 'bundle')
    calls = record_runs(module, monkeypatch)
    d, state, now = stalled(module, eligible=True)
    assert module.maybe_alert(d, state, now, True) == 'ALERT-SKIPPED:unpinned-writer'
    assert module.maybe_alert(d, state, now, True, pin=(str(bundle), sha)).startswith('ALERTED->')
    assert len(calls) == 1


def emitting(module, monkeypatch, payload: bytes, returncode: int = 0) -> list:
    """Runs the real subprocess.run with the given kwargs, on a child that writes payload."""
    import sys
    real_run = REAL_RUN   # not subprocess.run: a second call in a test would wrap the first wrapper
    script = ('import sys; sys.stdout.buffer.write(bytes(' + repr(list(payload)) + ')); '
              'sys.stderr.buffer.write(bytes(' + repr(list(payload)) + ')); sys.exit(' + str(returncode) + ')')
    results = []

    def run(command, **kwargs):
        results.append(real_run([sys.executable, '-c', script], **kwargs))
        return results[-1]

    monkeypatch.setattr(module.subprocess, 'run', run)
    return results


# 0x81 (in 'Á', UTF-8 C3 81) is undefined in cp1252, and 0x84 is a Finnish 'ä' in the OEM code page.
AWKWARD = bytes([0xC3, 0x81, 0x84, 0xFF])
# A text-mode capture decodes in subprocess's reader threads, whose exceptions pytest only warns about.
DECODE_ERRORS_FAIL = pytest.mark.filterwarnings('error::pytest.PytestUnhandledThreadExceptionWarning')


def oem_receipt(module) -> bytes:
    """A canonical receipt as pwsh prints it to a pipe: OEM bytes inside the event text."""
    good = receipt(module)
    return AWKWARD + b'\r\n' + good.replace(b'"type": "message"', b'"type": "message", "message": "' + AWKWARD + b'"')


@DECODE_ERRORS_FAIL
@pytest.mark.parametrize('returncode, output, expected', [
    (0, 'oem_receipt', 'ALERTED->codex-lead-1'),
    (1, 'oem_receipt', 'ALERT-FAILED:exit-1'),
    (0, 'awkward_only', 'ALERT-UNCONFIRMED:no-receipt'),
])
def test_the_writer_output_is_captured_as_bytes(monkeypatch, tmp_path, returncode, output, expected):
    module = load_detector()
    bundle, sha = pinned_bundle(tmp_path / 'bundle')
    payload = oem_receipt(module) if output == 'oem_receipt' else AWKWARD
    results = emitting(module, monkeypatch, payload, returncode)
    assert module.post_alert('branch', 'message', True, pin=(str(bundle), sha)) == expected
    [result] = results
    assert result.stdout == payload and result.stderr == payload


@pytest.mark.parametrize('case, expected', [
    ('queued', 'ALERT-UNCONFIRMED:queued'),
    ('suppressed', 'ALERT-UNCONFIRMED:suppressed'),
    ('canonical_not_durable', 'ALERT-UNCONFIRMED:not-canonical'),
    ('durable_not_canonical', 'ALERT-UNCONFIRMED:queued'),
    ('not_accepted', 'ALERT-UNCONFIRMED:not-canonical'),
    ('durable_as_text', 'ALERT-UNCONFIRMED:not-canonical'),
    ('other_log', 'ALERT-UNCONFIRMED:other-log'),
    ('no_log', 'ALERT-UNCONFIRMED:other-log'),
    ('empty', 'ALERT-UNCONFIRMED:no-receipt'),
    ('not_json', 'ALERT-UNCONFIRMED:no-receipt'),
    ('no_delivery', 'ALERT-UNCONFIRMED:no-receipt'),
    ('delivery_not_object', 'ALERT-UNCONFIRMED:no-receipt'),
    ('canonical', 'ALERTED->codex-lead-1'),
])
def test_only_a_canonical_durable_receipt_for_the_read_log_is_alerted(monkeypatch, tmp_path, case, expected):
    module = load_detector()
    bundle, sha = pinned_bundle(tmp_path / 'bundle')
    stdout = {
        'queued': receipt(module, delivery_status='queued', canonical_durable=False),
        'suppressed': receipt(module, delivery_status='suppressed', canonical_durable=False),
        'canonical_not_durable': receipt(module, canonical_durable=False),
        'durable_not_canonical': receipt(module, delivery_status='queued'),
        'not_accepted': receipt(module, accepted=False),
        'durable_as_text': receipt(module, canonical_durable='true'),
        'other_log': receipt(module, events_path=str(tmp_path / 'bundle' / 'shared' / 'events.jsonl')),
        'no_log': receipt(module, events_path=None),
        'empty': b'',
        'not_json': b'written\r\n',
        'no_delivery': b'{"type": "message"}\r\n',
        'delivery_not_object': b'{"type": "message", "_bridge_delivery": "canonical"}\r\n',
        'canonical': receipt(module),
    }[case]
    record_runs(module, monkeypatch, stdout=stdout)
    assert module.post_alert('branch', 'message', True, pin=(str(bundle), sha)) == expected


@DECODE_ERRORS_FAIL
def test_gh_output_is_decoded_as_utf8(monkeypatch):
    module = load_detector()
    title = 'Á ä'
    emitting(module, monkeypatch, ('[{"title": "' + title + '"}]').encode('utf-8'))
    assert module.gh_json(['pr', 'list']) == [{'title': title}]
    emitting(module, monkeypatch, 'x\tpass\tÁ'.encode('utf-8'))
    assert module.ci_all_green(1) is True
    emitting(module, monkeypatch, AWKWARD)
    assert module.gh_json(['pr', 'list']) is None
