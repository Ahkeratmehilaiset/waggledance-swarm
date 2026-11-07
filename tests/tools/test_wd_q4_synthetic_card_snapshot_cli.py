"""Only synthetic, pinned local fixture inputs; no installed runtime or bridge."""
import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.tools import test_wd_q4_card_generation as fixture


def sha(value):
    return hashlib.sha256(value).hexdigest()


def setup_request(tmp_path):
    args, out = fixture.prepared(tmp_path)
    paths = {}
    for name in ('decision', 'vocabulary', 'path_allowlist', 'policy', 'roster', 'card_policy'):
        raw = args[name + '_bytes']
        path = tmp_path / (name + '.json')
        path.write_bytes(raw)
        paths[name] = {'path': path.name, 'sha256': sha(raw)}
    for name in ('source', 'generation'):
        path = Path(args[name + '_path'])
        paths[name] = {'path': path.relative_to(tmp_path).as_posix(), 'sha256': sha(path.read_bytes())}
    cursor = args['end_cursor']
    request = {'schema': 'wd.q4-synthetic-card-snapshot-request.v1', 'inputs': paths,
               'cursor': {'offset': cursor.offset, 'file_identity': cursor.file_identity,
                          'generation': cursor.generation}, 'now_utc': args['now_utc'],
               'output_dir': out.name, 'generation_name': args['generation_name']}
    raw = json.dumps(request, sort_keys=True, separators=(',', ':')).encode()
    (tmp_path / 'request.json').write_bytes(raw)
    return request, raw, out


def test_default_off_does_not_touch_inputs():
    mod = importlib.import_module('tools.wd_q4_synthetic_card_snapshot_cli')
    class Untouchable:
        def __getattribute__(self, name): raise AssertionError('off accessed input')
    assert mod.build(enabled=False, root=Untouchable()) == {'status': 'OFF'}


def test_audit_root_with_parent_traversal_refused_before_input_read(tmp_path, monkeypatch):
    mod = importlib.import_module('tools.wd_q4_synthetic_card_snapshot_cli')
    calls = []
    def unexpected(*args, **kwargs):
        calls.append(True)
        raise ValueError()
    monkeypatch.setattr(mod, '_inside', unexpected)
    with pytest.raises(mod.Refused):
        mod.build(enabled=True, root=tmp_path / '..', request_path='x', request_sha256='0' * 64)
    assert calls == []


def test_synthetic_generation_and_pointer_pass_real_c2(tmp_path):
    pytest.importorskip('faiss')
    mod = importlib.import_module('tools.wd_q4_synthetic_card_snapshot_cli')
    request, raw, out = setup_request(tmp_path)
    result = mod.build(enabled=True, root=tmp_path, request_path='request.json', request_sha256=sha(raw))
    assert result['status'] == 'VALIDATED_STAGED' and result['completeness'] == 'UNKNOWN'
    pointer = (out / 'card-snapshot.pointer.json').read_bytes()
    assert sha(pointer) == result['pointer_sha256']
    assert json.loads(pointer)['generation'] == request['generation_name']
    assert (out / 'candidate' / 'memory.json').is_file()


@pytest.mark.parametrize('fault', ['request_pin', 'input_pin', 'real_decision', 'source_outside', 'preexisting_pointer'])
def test_refusal_before_output(tmp_path, fault):
    mod = importlib.import_module('tools.wd_q4_synthetic_card_snapshot_cli')
    request, raw, out = setup_request(tmp_path)
    if fault == 'input_pin': request['inputs']['policy']['sha256'] = '0' * 64
    if fault == 'source_outside': request['inputs']['source']['path'] = '../outside'
    if fault == 'real_decision':
        path = tmp_path / request['inputs']['decision']['path']
        decision = json.loads(path.read_bytes()); decision['synthetic'] = False
        path.write_text(json.dumps(decision)); request['inputs']['decision']['sha256'] = sha(path.read_bytes())
    if fault == 'preexisting_pointer': (out / 'card-snapshot.pointer.json').write_bytes(b'unchanged')
    raw = json.dumps(request, sort_keys=True, separators=(',', ':')).encode()
    (tmp_path / 'request.json').write_bytes(raw)
    with pytest.raises(mod.Refused):
        mod.build(enabled=True, root=tmp_path, request_path='request.json',
                  request_sha256=('0' * 64 if fault == 'request_pin' else sha(raw)))
    assert not (out / 'candidate').exists()
    if fault == 'preexisting_pointer': assert (out / 'card-snapshot.pointer.json').read_bytes() == b'unchanged'


def test_after_attempt_failure_is_unknown_and_no_pointer(tmp_path, monkeypatch):
    mod = importlib.import_module('tools.wd_q4_synthetic_card_snapshot_cli')
    _, raw, out = setup_request(tmp_path)
    from tools import wd_q4_card_generation as producer
    def partial(**_kwargs):
        (out / 'candidate').mkdir()
        raise RuntimeError('synthetic')
    monkeypatch.setattr(producer, 'prepare_card_generation', partial)
    with pytest.raises(mod.Unknown):
        mod.build(enabled=True, root=tmp_path, request_path='request.json', request_sha256=sha(raw))
    assert (out / 'candidate').is_dir()
    assert not (out / 'card-snapshot.pointer.json').exists()


def test_failed_c2_recheck_after_pointer_write_is_unknown(tmp_path, monkeypatch):
    pytest.importorskip('faiss')
    mod = importlib.import_module('tools.wd_q4_synthetic_card_snapshot_cli')
    from sandbox.mind.step2 import shared_card_checkpoint as checkpoint
    original = checkpoint.validate_card_snapshot
    count = []
    def second_fails(**kwargs):
        count.append(True)
        return original(**kwargs) if len(count) == 1 else {'status': 'INCOMPLETE'}
    monkeypatch.setattr(checkpoint, 'validate_card_snapshot', second_fails)
    _, raw, out = setup_request(tmp_path)
    with pytest.raises(mod.Unknown):
        mod.build(enabled=True, root=tmp_path, request_path='request.json', request_sha256=sha(raw))
    assert len(count) == 2 and (out / 'card-snapshot.pointer.json').is_file()


def test_cli_runs_actual_synthetic_producer(tmp_path):
    pytest.importorskip('faiss')
    _, raw, out = setup_request(tmp_path)
    result = subprocess.run([sys.executable, '-B', '-m', 'tools.wd_q4_synthetic_card_snapshot_cli',
        '--enabled', '--root', str(tmp_path), '--request', 'request.json', '--request-sha256', sha(raw)],
        cwd=str(Path(__file__).resolve().parents[2]), capture_output=True, timeout=60)
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout)['status'] == 'VALIDATED_STAGED'
    assert (out / 'card-snapshot.pointer.json').is_file()
