"""Synthetic command-line invocation, no live bundle or bridge use."""
import json
import subprocess
import sys
from pathlib import Path
import pytest
from tests.tools.test_wd_q4_staged_composer import setup_case


def argv(kw):
    return ['--request', kw['request_path'], '--stage', kw['stage_dir'], '--bundle', kw['expected_bundle'],
            '--commit', kw['expected_commit'], '--manifest-sha256', kw['expected_manifest_sha256'],
            '--public-plan-sha256', kw['expected_public_plan_sha256'], '--enable']


def test_default_off_and_no_compose(capsys, monkeypatch):
    from tools import wd_q4_staged_composer_cli as cli
    def forbidden(**_): raise AssertionError('no calls')
    monkeypatch.setattr(cli.composer, 'compose', forbidden)
    assert cli.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'OFF'}


def test_valid_no_enable_does_not_open_input(capsys, monkeypatch, tmp_path):
    from tools import wd_q4_staged_composer_cli as cli
    kw, _, _ = setup_case(tmp_path)
    def forbidden(**_): raise AssertionError('no calls')
    monkeypatch.setattr(cli.composer, 'compose', forbidden)
    assert cli.main(argv(kw)[:-1]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'OFF'}


def test_real_synthetic_chain_outputs_closed_descriptor(tmp_path, capsys):
    from tools import wd_q4_staged_composer_cli as cli
    kw, _, stage = setup_case(tmp_path)
    assert cli.main(argv(kw)) == 0
    out = capsys.readouterr()
    result = json.loads(out.out)
    assert result['status'] == 'PREPARED'
    assert set(result) == {'status', 'bundle_commit', 'manifest_sha256', 'binding_sha256',
                           'descriptor_path', 'descriptor_sha256', 'pointer_path', 'pointer_sha256'}
    assert Path(result['pointer_path']).is_file()
    assert stage in Path(result['pointer_path']).parents
    assert out.err == ''
    assert not list(tmp_path.rglob('WD_Q4_DESCRIPTOR_CURRENT.json'))


@pytest.mark.parametrize('change', [lambda a:a[:-2], lambda a:a+['--stage','x'],
                                    lambda a:a+['--wrong'], lambda a:['--enable']])
def test_invalid_argument_refuses_without_composer(tmp_path, capsys, monkeypatch, change):
    from tools import wd_q4_staged_composer_cli as cli
    kw, _, _ = setup_case(tmp_path)
    monkeypatch.setattr(cli.composer, 'compose', lambda **_: pytest.fail('called'))
    assert cli.main(change(argv(kw))) != 0
    assert json.loads(capsys.readouterr().out) == {'status': 'REFUSED'}


def test_refusal_has_no_input_echo(tmp_path, capsys):
    from tools import wd_q4_staged_composer_cli as cli
    kw, _, stage = setup_case(tmp_path)
    kw['expected_manifest_sha256'] = 'F'*64
    assert cli.main(argv(kw)) != 0
    out = capsys.readouterr()
    assert json.loads(out.out) == {'status': 'REFUSED'}
    assert out.err == '' and not list(stage.iterdir())


def test_partial_failure_is_not_success_or_silent_retry(tmp_path, capsys, monkeypatch):
    from tools import wd_q4_staged_composer_cli as cli
    kw, _, stage = setup_case(tmp_path)
    def fail(_): raise ValueError('private-sentinel')
    monkeypatch.setattr(cli.composer.cli, '_compose', fail)
    assert cli.main(argv(kw)) != 0
    out = capsys.readouterr()
    assert json.loads(out.out) == {'status': 'INCOMPLETE'}
    assert 'private-sentinel' not in out.out + out.err
    assert (stage / 'binding-source.json').exists()


def test_unknown_exception_after_dispatch_reports_unknown(tmp_path, capsys, monkeypatch):
    from tools import wd_q4_staged_composer_cli as cli
    kw, _, _ = setup_case(tmp_path)
    def fail(**_): raise RuntimeError('private-sentinel')
    monkeypatch.setattr(cli.composer, 'compose', fail)
    assert cli.main(argv(kw)) != 0
    out = capsys.readouterr()
    assert json.loads(out.out) == {'status': 'UNKNOWN'}
    assert 'private-sentinel' not in out.out + out.err


def test_interrupt_is_nonzero_and_does_not_claim_success(tmp_path, capsys, monkeypatch):
    from tools import wd_q4_staged_composer_cli as cli
    kw, _, _ = setup_case(tmp_path)
    def fail(**_): raise KeyboardInterrupt()
    monkeypatch.setattr(cli.composer, 'compose', fail)
    assert cli.main(argv(kw)) == 130
    assert 'PREPARED' not in capsys.readouterr().out


def test_actual_module_process_runs_same_synthetic_chain(tmp_path):
    kw, _, stage = setup_case(tmp_path)
    run = subprocess.run([sys.executable, '-B', '-m', 'tools.wd_q4_staged_composer_cli', *argv(kw)],
                         cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and run.stderr == ''
    result = json.loads(run.stdout)
    assert result['status'] == 'PREPARED' and Path(result['pointer_path']).parent == stage
    assert not list(tmp_path.rglob('WD_Q4_DESCRIPTOR_CURRENT.json'))


def test_unexpected_result_cannot_claim_success(tmp_path, capsys, monkeypatch):
    from tools import wd_q4_staged_composer_cli as cli
    kw, _, _ = setup_case(tmp_path)
    monkeypatch.setattr(cli.composer, 'compose', lambda **_: {'status': 'PREPARED'})
    assert cli.main(argv(kw)) != 0
    assert json.loads(capsys.readouterr().out) == {'status': 'UNKNOWN'}


def test_success_output_failure_never_returns_zero(tmp_path, monkeypatch):
    from tools import wd_q4_staged_composer_cli as cli
    kw, _, _ = setup_case(tmp_path)
    monkeypatch.setattr(cli, '_emit', lambda _: False)
    assert cli.main(argv(kw)) != 0
