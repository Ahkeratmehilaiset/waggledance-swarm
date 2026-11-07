"""Synthetic, hash-pinned staged-card CLI; no live installation or real model."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.tools.test_wd_q4_staged_card_composer import materialize, staged_producer  # noqa: F401
from tools import wd_q4_staged_card_composer_cli as cli


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def pinned_request(materialize, tmp_path):
    values = dict(materialize)
    values.pop('enabled')
    plan = values.pop('fixed_plan_bytes')
    plan_path = tmp_path / 'fixed-plan-v3.json'
    plan_path.write_bytes(plan)
    values.update(schema='wd.q4-staged-card-composer-request.v1', fixed_plan_path=str(plan_path))
    path = tmp_path / 'request.json'
    raw = json.dumps(values, sort_keys=True, separators=(',', ':')).encode()
    path.write_bytes(raw)
    return path, raw


def run(path, pin):
    return cli.main(['--enable', '--request', str(path), '--sha256', pin])


def test_default_off_does_not_read_or_compose(monkeypatch, capsys):
    monkeypatch.setattr(cli.input_files, '_read_pinned', lambda *a, **kw: pytest.fail('OFF read'))
    monkeypatch.setattr(cli.composer, 'compose_staged_card', lambda **kw: pytest.fail('OFF composed'))
    assert cli.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'OFF'}


def test_default_off_in_a_separate_process():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, '-B', '-m', 'tools.wd_q4_staged_card_composer_cli'],
                            cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0 and json.loads(result.stdout) == {'status': 'OFF'}


def test_pinned_request_materializes_real_synthetic_writer_and_descriptor(pinned_request, capsys):
    path, raw = pinned_request
    code = run(path, sha(raw))
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output['status'] == 'PREPARED'
    assert sha(Path(output['descriptor_path']).read_bytes()) == output['descriptor_sha256']
    assert sha(Path(output['pointer_path']).read_bytes()) == output['pointer_sha256']
    assert not (Path(output['pointer_path']).parent / 'WD_Q4_DESCRIPTOR_CURRENT.json').exists()


@pytest.mark.parametrize('fault', ['request_pin', 'missing_key', 'extra_key', 'bad_fixed_pin', 'bad_host_pin',
                                   'wrong_schema', 'duplicate_key', 'wrong_flags'])
def test_invalid_or_incomplete_request_refuses_before_writes(pinned_request, capsys, fault):
    path, raw = pinned_request
    case = json.loads(raw)
    stage = Path(case['stage_dir'])
    if fault == 'request_pin': pin = '0'*64
    elif fault == 'wrong_flags':
        assert cli.main(['--enable', '--request', str(path)]) == 2
        assert json.loads(capsys.readouterr().out) == {'status': 'REFUSED'}
        assert not list(stage.iterdir())
        return
    else:
        if fault == 'missing_key': del case['expected_host_sha256']
        if fault == 'extra_key': case['extra'] = 'unexpected'
        if fault == 'bad_fixed_pin': case['expected_fixed_plan_sha256'] = '0'*64
        if fault == 'bad_host_pin': case['expected_host_sha256'] = '0'*64
        if fault == 'wrong_schema': case['schema'] = 'wd.other.v1'
        raw = (b'{"schema":"duplicate","schema":"duplicate"}' if fault == 'duplicate_key'
               else json.dumps(case, sort_keys=True, separators=(',', ':')).encode())
        path.write_bytes(raw)
        pin = sha(raw)
    assert run(path, pin) == 2
    assert json.loads(capsys.readouterr().out) == {'status': 'REFUSED'}
    assert not list(stage.iterdir())


@pytest.mark.parametrize('outcome,code,status', [
    ('incomplete', 3, 'INCOMPLETE'), ('unknown', 3, 'UNKNOWN'), ('interrupt', 130, 'UNKNOWN'),
    ('system_exit', 3, 'UNKNOWN'), ('bad_result', 3, 'UNKNOWN')])
def test_composer_failure_or_unverifiable_result_is_never_a_success(pinned_request, monkeypatch, capsys, outcome, code, status):
    def failure(**kwargs):
        if outcome == 'incomplete': raise cli.composer.ComposeIncomplete('synthetic')
        if outcome == 'unknown': raise RuntimeError('synthetic-private')
        if outcome == 'interrupt': raise KeyboardInterrupt()
        if outcome == 'system_exit': raise SystemExit(7)
        return {'status': 'PREPARED'}
    monkeypatch.setattr(cli.composer, 'compose_staged_card', failure)
    path, raw = pinned_request
    assert run(path, sha(raw)) == code
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {'status': status}
    assert 'synthetic-private' not in captured.out + captured.err


def test_post_write_verification_failure_is_unknown_and_preserves_staged_files(pinned_request, monkeypatch, capsys):
    path, raw = pinned_request
    stage = Path(json.loads(raw)['stage_dir'])
    def refuse(*args):
        raise ValueError('synthetic-private')
    monkeypatch.setattr(cli, '_verify_result', refuse)
    assert run(path, sha(raw)) == 3
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {'status': 'UNKNOWN'}
    assert 'synthetic-private' not in captured.out + captured.err
    assert list(stage.iterdir())


@pytest.mark.parametrize('name', ['fixed-plan.v2.json', 'public-snapshot.v1.json'])
def test_changed_materialized_artifact_after_composition_is_unknown(pinned_request, monkeypatch, capsys, name):
    original = cli.composer.compose_staged_card
    def changed(**kwargs):
        result = original(**kwargs)
        path = Path(result['descriptor_path']).parent / name
        path.write_bytes(path.read_bytes() + b' ')
        return result
    monkeypatch.setattr(cli.composer, 'compose_staged_card', changed)
    path, raw = pinned_request
    assert run(path, sha(raw)) == 3
    assert json.loads(capsys.readouterr().out) == {'status': 'UNKNOWN'}
