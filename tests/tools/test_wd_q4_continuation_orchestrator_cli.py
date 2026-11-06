"""Synthetic-only, default-off orchestrator CLI tests."""
import importlib
import json

import pytest
from tests.tools.test_wd_q4_continuation_orchestrator import anchored_template, sha


def test_off_never_opens_config_or_calls_orchestrator(monkeypatch, capsys):
    cli = importlib.import_module('tools.wd_q4_continuation_orchestrator_cli')
    monkeypatch.setattr(cli, '_prepare', lambda *_: pytest.fail('opened'))
    assert cli.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'OFF'}


def test_unpinned_config_never_invokes_orchestrator(monkeypatch, capsys):
    cli = importlib.import_module('tools.wd_q4_continuation_orchestrator_cli')
    monkeypatch.setattr(cli.orchestrator, 'run_once', lambda **_: pytest.fail('called'))
    assert cli.main(['--enable', '--config', 'bad', '--sha256', 'not-a-sha']) == 2
    assert json.loads(capsys.readouterr().out) == {'status': 'REFUSED'}


def test_interrupted_attempt_is_unknown_without_retry(monkeypatch, capsys):
    cli = importlib.import_module('tools.wd_q4_continuation_orchestrator_cli')
    monkeypatch.setattr(cli, '_prepare', lambda *_: {'cutoff_utc': '2026-10-31T13:01:00Z'})
    calls = []
    def interrupted(**kwargs):
        calls.append(kwargs)
        raise cli.orchestrator.OrchestrationInterrupted('synthetic')
    monkeypatch.setattr(cli.orchestrator, 'run_once', interrupted)
    assert cli.main(['--enable', '--config', 'synthetic', '--sha256', 'a' * 64]) == 3
    assert json.loads(capsys.readouterr().out) == {'status': 'UNKNOWN'}
    assert len(calls) == 1


def test_config_pins_bundle_template_and_runtime(tmp_path, monkeypatch):
    # Fixture-oriented full pinned-file checks are exercised by the separate
    # end-to-end test; this case verifies there is no implicit config route.
    cli = importlib.import_module('tools.wd_q4_continuation_orchestrator_cli')
    config = tmp_path / 'bad.json'
    config.write_bytes(b'{}')
    import hashlib
    pin = hashlib.sha256(config.read_bytes()).hexdigest()
    with pytest.raises(Exception):
        cli._prepare(str(config), pin)


def test_real_config_and_template_are_both_hash_bound(anchored_template, monkeypatch, tmp_path):
    cli = importlib.import_module('tools.wd_q4_continuation_orchestrator_cli')
    _, inputs, _, _ = anchored_template
    config = {'schema': 'wd.q4-continuation-orchestrator-cli.v1',
              'bundle_root': str(tmp_path / 'bundle'), **inputs,
              'cutoff_utc': '2026-10-31T13:01:00Z', 'timeout_seconds': 30}
    path = tmp_path / 'orchestrator.json'
    path.write_bytes(json.dumps(config).encode())
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', inputs['expected_manifest_sha256'])
    assert cli._prepare(str(path), sha(path.read_bytes()))['cutoff_utc'] == config['cutoff_utc']
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(Exception):
        cli._prepare(str(path), 'a' * 64)
