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


# --- D1: v2 request selects the shared immutable generations root (composer shared_stage=True) ------------------
# The materialize fixture predates the af098f1d peer fix, so its success path is refused at _read_staged_backend on
# this head. These tests replace only that attestation (bundle peer path, staged backend plan); every other composer
# and CLI check runs for real.

_V2 = 'wd.q4-staged-card-composer-request.v2'
_GEN_B, _GEN_C = 'b' * 40, 'c' * 40


@pytest.fixture
def attested(materialize, monkeypatch):
    peer = Path(materialize['expected_bundle']) / 'tools-bootstrap' / 'tools' / 'wd_q4_secure_transport.py'
    backend = Path(materialize['expected_bundle']) / 'tools-bootstrap' / 'tools' / 'wd_q4_staged_fixed_backend.py'

    def attest(path, plan):
        assert Path(path) == peer and plan['backend'] == 'staged-card-wrapper-v1'
        return backend.read_bytes()
    monkeypatch.setattr(cli.composer.transport, '_read_staged_backend', attest)
    return materialize


def _write_request(case, tmp_path, name, **changes):
    values = dict(case)
    values.pop('enabled')
    plan = values.pop('fixed_plan_bytes')
    plan_path = tmp_path / (name + '-fixed-plan-v3.json')
    if not plan_path.exists():
        plan_path.write_bytes(plan)
    values.update(schema='wd.q4-staged-card-composer-request.v1', fixed_plan_path=str(plan_path))
    values.update(changes)
    if values['schema'] == _V2:  # shared mode binds fixed plan and descriptor to GEN/public-plan.json
        values = _bound_to_generation(values, plan_path)
    path = tmp_path / (name + '-request.json')
    raw = json.dumps(values, sort_keys=True, separators=(',', ':')).encode()
    path.write_bytes(raw)
    return path, raw


def _bound_to_generation(values, plan_path):
    target = str(Path(values['stage_dir']) / values['generation'] / 'public-plan.json')
    if 'fixed_plan_bytes' in values:
        plan = json.loads(values['fixed_plan_bytes'])
    else:
        plan = json.loads(Path(values['fixed_plan_path']).read_bytes())
    plan['public_plan_path'] = target
    raw = cli.composer.transport._canonical(plan)
    values = dict(values, expected_fixed_plan_sha256=sha(raw),
                  descriptor_options=dict(values['descriptor_options'], public_plan=target))
    if 'fixed_plan_bytes' in values:
        return dict(values, fixed_plan_bytes=raw)
    bound_path = Path(str(plan_path) + '.' + values['generation'][:8] + '.json')
    bound_path.write_bytes(raw)
    return dict(values, fixed_plan_path=str(bound_path))


def _snapshot(stage):
    return {str(p.relative_to(stage)): (p.read_bytes() if p.is_file() else None) for p in sorted(Path(stage).rglob('*'))}


def test_v2_request_composes_into_shared_root_with_generation_pointer(attested, tmp_path, capsys):
    stage = Path(attested['stage_dir'])
    path, raw = _write_request(attested, tmp_path, 'b', schema=_V2, shared_stage=True, generation=_GEN_B)
    assert run(path, sha(raw)) == 0
    first = json.loads(capsys.readouterr().out)
    assert Path(first['pointer_path']) == stage / ('candidate-pointer-' + _GEN_B + '.json')
    before = _snapshot(stage)
    path, raw = _write_request(attested, tmp_path, 'c', schema=_V2, shared_stage=True, generation=_GEN_C)
    assert run(path, sha(raw)) == 0
    second = json.loads(capsys.readouterr().out)
    assert second['status'] == 'PREPARED'
    copy = stage / _GEN_C / 'public-plan.json'
    assert copy.read_bytes() == Path(attested['public_plan_path']).read_bytes()
    assert Path(second['pointer_path']) == stage / ('candidate-pointer-' + _GEN_C + '.json')
    after = _snapshot(stage)
    assert {key: after[key] for key in before} == before


def test_v1_request_keeps_legacy_empty_stage_and_fixed_pointer(attested, tmp_path, capsys):
    path, raw = _write_request(attested, tmp_path, 'legacy')
    assert run(path, sha(raw)) == 0
    output = json.loads(capsys.readouterr().out)
    assert Path(output['pointer_path']) == Path(attested['stage_dir']) / 'candidate-pointer.json'


@pytest.mark.parametrize('variant', ['v2-false', 'v2-missing', 'v2-int', 'v2-string', 'v1-with-flag'])
def test_shared_flag_must_match_the_schema_before_any_write(attested, tmp_path, capsys, variant):
    changes = {'v2-false': dict(schema=_V2, shared_stage=False), 'v2-missing': dict(schema=_V2),
               'v2-int': dict(schema=_V2, shared_stage=1), 'v2-string': dict(schema=_V2, shared_stage='true'),
               'v1-with-flag': dict(shared_stage=True)}[variant]
    path, raw = _write_request(attested, tmp_path, variant, **changes)
    assert run(path, sha(raw)) == 2
    assert json.loads(capsys.readouterr().out) == {'status': 'REFUSED'}
    assert not any(Path(attested['stage_dir']).iterdir())


def test_v2_result_naming_the_legacy_pointer_is_rejected(attested):
    values = _bound_to_generation(dict(attested, shared_stage=True), None)
    values.pop('enabled')
    result = cli.composer.compose_staged_card(enabled=True, **values)
    cli._verify_result(result, values)
    forged = dict(result, pointer_path=str(Path(values['stage_dir']) / 'candidate-pointer.json'))
    with pytest.raises(ValueError):
        cli._verify_result(forged, values)
    with pytest.raises(ValueError):
        cli._verify_result(result, dict(values, shared_stage=False))


@pytest.mark.parametrize('damage', ['missing', 'changed'])
def test_v2_result_without_the_pinned_generation_public_plan_is_rejected(attested, damage):
    values = _bound_to_generation(dict(attested, shared_stage=True), None)
    values.pop('enabled')
    result = cli.composer.compose_staged_card(enabled=True, **values)
    copy = Path(result['descriptor_path']).parent / 'public-plan.json'
    if damage == 'missing':
        copy.unlink()
    else:
        copy.write_bytes(copy.read_bytes() + b' ')
    with pytest.raises(cli.input_files._Refused):
        cli._verify_result(result, values)
