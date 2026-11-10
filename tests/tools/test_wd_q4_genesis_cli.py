"""Synthetic, isolated genesis CLI checks: no live source or subprocesses."""
import importlib
import hashlib
import json
from pathlib import Path
import pytest


def test_default_off_does_not_import_or_read(monkeypatch, capsys):
    cli = importlib.import_module('tools.wd_q4_genesis_cli')
    monkeypatch.setattr(cli, '_read_config', lambda *_: pytest.fail('OFF read config'))
    assert cli.main([]) == 0
    assert 'OFF' in capsys.readouterr().out


def test_enabled_requires_exact_config_and_pin(capsys):
    cli = importlib.import_module('tools.wd_q4_genesis_cli')
    assert cli.main(['--enable']) == 2
    assert 'REFUSED' in capsys.readouterr().out


def test_output_never_contains_exception_or_config(monkeypatch, capsys):
    from tools import wd_q4_genesis_cli as cli
    def fail(*_):
        raise ValueError('synthetic-private-canary')
    monkeypatch.setattr(cli, '_prepare', fail)
    assert cli.main(['--enable', '--config', 'synthetic-private-canary', '--sha256', 'a' * 64]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out) == {'status': 'REFUSED'}
    assert 'synthetic-private-canary' not in output.out + output.err


@pytest.mark.parametrize('outcome,code,status', [
    ('ok', 0, 'PUBLISHED_GENESIS'), ('unknown', 3, 'UNKNOWN'),
    ('interrupt', 3, 'UNKNOWN'), ('source_changed', 3, 'UNKNOWN'), ('refused', 2, 'REFUSED')])
def test_one_call_and_post_admission_outcome(monkeypatch, capsys, outcome, code, status):
    from tools import wd_q4_genesis_cli as cli
    from tools import wd_q4_genesis_bootstrap as genesis
    config = {'source_path': 'private', 'source_sha256': 'a' * 64, 'source_bytes': 17,
              'source_file_identity': 'windows-v1:00000001:0000000000000001',
              'generation_path': 'private', 'generation': 'synthetic', 'checkpoint_root': 'private',
              'private_state_root': 'private', 'trusted_model_root': 'private',
              'policy_sha256': 'b' * 64, 'public_policy_sha256': 'c' * 64, 'fleet_sha256': 'd' * 64,
              'max_prefix_bytes': 1024, 'max_bytes': 32, 'max_rows': 1, 'plan_template': {}, 'renewable': True,
              'cutoff_utc': None, 'batch_rows': False, 'min_headroom_bytes': 128, 'allow_partial_genesis': False}
    monkeypatch.setattr(cli, '_prepare', lambda *_: (config, b'p', b'u', b'f', genesis))
    calls = []
    def invoke(**kwargs):
        calls.append(kwargs)
        if outcome == 'refused': raise genesis.GenesisRefused()
        if outcome == 'unknown': raise genesis.GenesisUnknown()
        if outcome == 'interrupt': raise KeyboardInterrupt()
        return {'status': 'PUBLISHED_GENESIS', 'genesis_snapshot_bytes': 17,
                'genesis_snapshot_sha256': ('f' if outcome == 'source_changed' else 'a') * 64}
    monkeypatch.setattr(genesis, 'bootstrap', invoke)
    def verify(config, genesis, result, policy, fleet):
        if result['genesis_snapshot_sha256'] != config['source_sha256']: raise ValueError()
        return True
    monkeypatch.setattr(cli, '_verify_result', verify)
    assert cli.main(['--enable', '--config', 'private', '--sha256', 'a' * 64]) == code
    assert json.loads(capsys.readouterr().out) == {'status': status}
    assert len(calls) == 1 and calls[0]['renewable'] is True


def test_duplicate_config_keys_are_refused():
    from tools import wd_q4_genesis_cli as cli
    with pytest.raises(ValueError):
        cli._doc(b'{"schema":"a","schema":"b"}')


def test_config_pins_cutoff_batch_headroom_and_partial_and_reports_partial(monkeypatch, capsys):
    from tools import wd_q4_genesis_cli as cli, wd_q4_genesis_bootstrap as genesis
    config = {'source_path': 'private', 'source_sha256': 'a' * 64, 'source_bytes': 17,
              'source_file_identity': 'windows-v1:00000001:0000000000000001',
              'generation_path': 'private', 'generation': 'synthetic', 'checkpoint_root': 'private',
              'private_state_root': 'private', 'trusted_model_root': 'private', 'policy_sha256': 'b' * 64,
              'public_policy_sha256': 'c' * 64, 'fleet_sha256': 'd' * 64, 'max_prefix_bytes': 1024,
              'max_bytes': 32, 'max_rows': 1, 'plan_template': {}, 'renewable': True,
              'cutoff_utc': '2026-01-01T00:00:00Z', 'batch_rows': True,
              'min_headroom_bytes': 128, 'allow_partial_genesis': True}
    monkeypatch.setattr(cli, '_prepare', lambda *_: (config, b'p', b'u', b'f', genesis))
    calls = []
    def bootstrap(**kwargs):
        calls.append(kwargs)
        return {'status': 'PUBLISHED_GENESIS', 'genesis_initial_prefix_fully_covered': False,
                'coverage': 'partial_ready_prefix'}
    monkeypatch.setattr(genesis, 'bootstrap', bootstrap)
    monkeypatch.setattr(cli, '_verify_result', lambda *args: False)
    assert cli.main(['--enable', '--config', 'private', '--sha256', 'a' * 64]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'PUBLISHED_GENESIS_PARTIAL'}
    assert len(calls) == 1
    assert {k: calls[0][k] for k in ('cutoff_utc', 'batch_rows', 'min_headroom_bytes', 'allow_partial_genesis')} == {
        k: config[k] for k in ('cutoff_utc', 'batch_rows', 'min_headroom_bytes', 'allow_partial_genesis')}
    assert calls[0]['expected_source_identity'] == config['source_file_identity']
    assert calls[0]['expected_source_prefix_bytes'] == config['source_bytes']
    assert calls[0]['expected_source_prefix_sha256'] == config['source_sha256']


def test_machine_receipt_has_only_valid_public_hashes(monkeypatch, capsys):
    from tools import wd_q4_genesis_cli as cli, wd_q4_genesis_bootstrap as genesis
    config = {'source_path': 'private', 'source_sha256': 'a' * 64, 'source_bytes': 17,
              'source_file_identity': 'windows-v1:00000001:0000000000000001', 'generation_path': 'private',
              'generation': 'synthetic', 'checkpoint_root': 'private', 'private_state_root': 'private',
              'trusted_model_root': 'private', 'policy_sha256': 'b' * 64, 'public_policy_sha256': 'c' * 64,
              'fleet_sha256': 'd' * 64, 'max_prefix_bytes': 1024, 'max_bytes': 32, 'max_rows': 1,
              'plan_template': {}, 'renewable': True, 'cutoff_utc': None, 'batch_rows': False,
              'min_headroom_bytes': 128, 'allow_partial_genesis': False}
    monkeypatch.setattr(cli, '_prepare', lambda *_: (config, b'p', b'u', b'f', genesis))
    monkeypatch.setattr(genesis, 'bootstrap', lambda **_: {'status': 'PUBLISHED_GENESIS',
        'pointer_sha256': 'e' * 64, 'runner_receipt_sha256': 'f' * 64, 'private': 'must-not-emit'})
    monkeypatch.setattr(cli, '_verify_result', lambda *args: True)
    assert cli.main(['--enable', '--config', 'private', '--sha256', 'a' * 64]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'PUBLISHED_GENESIS',
        'pointer_sha256': 'e' * 64, 'runner_receipt_sha256': 'f' * 64}
    monkeypatch.setattr(genesis, 'bootstrap', lambda **_: {'status': 'PUBLISHED_GENESIS',
        'pointer_sha256': 'e' * 64, 'runner_receipt_sha256': 'invalid'})
    assert cli.main(['--enable', '--config', 'private', '--sha256', 'a' * 64]) == 3
    assert json.loads(capsys.readouterr().out) == {'status': 'UNKNOWN'}


def test_cli_calls_actual_bootstrap_and_runner_with_synthetic_child(tmp_path, monkeypatch, capsys):
    """Actual CLI/bootstrap/runner; isolated files, synthetic child and checkpoint accessor."""
    from tools import wd_q4_genesis_cli as cli, wd_q4_genesis_bootstrap as genesis
    from waggledance.core.bridge_log_reader import _file_identity
    path = Path(__file__).with_name('test_wd_q4_genesis_bootstrap.py')
    spec = importlib.util.spec_from_file_location('q4_cli_genesis_fixture', path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    args, calls = helper.synthetic.__wrapped__(tmp_path, monkeypatch)
    source = Path(args['source_path'])
    with source.open('rb') as stream: source_id = _file_identity(stream)
    config = {'source_path': args['source_path'], 'source_sha256': _sha(source.read_bytes()),
        'source_bytes': source.stat().st_size, 'source_file_identity': source_id,
        'generation_path': args['generation_path'], 'generation': args['generation'],
        'checkpoint_root': args['checkpoint_root'], 'private_state_root': args['private_state_root'],
        'trusted_model_root': args['trusted_model_root'], 'policy_sha256': args['policy_sha256'],
        'public_policy_sha256': args['public_policy_sha256'], 'fleet_sha256': args['expected_fleet_sha256'],
        'max_prefix_bytes': args['max_prefix_bytes'], 'max_bytes': args['max_bytes'], 'max_rows': args['max_rows'],
        'plan_template': args['plan_template'], 'renewable': False, 'cutoff_utc': '2026-01-01T00:00:00Z',
        'batch_rows': True, 'min_headroom_bytes': 1, 'allow_partial_genesis': True}
    monkeypatch.setattr(cli, '_prepare', lambda *_: (config, args['policy_bytes'], args['public_policy_bytes'],
                                                     args['fleet_bytes'], genesis))
    assert cli.main(['--enable', '--config', 'private', '--sha256', 'a' * 64]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {'status': 'PUBLISHED_GENESIS', 'pointer_sha256': 'a' * 64}
    assert len(calls) == 1 and Path(args['generation_path']).exists()
    assert calls[0]['cutoff_utc'].isoformat() == '2026-01-01T00:00:00+00:00'


@pytest.mark.parametrize('path', ['C:\\a\\..\\b', 'F:\\thing', '\\\\server\\share', 'relative'])
def test_non_c_or_noncanonical_path_refused(path):
    from tools import wd_q4_genesis_cli as cli
    if '..' in path:
        from tools.wd_q4_guard_state_binding import _root
        with pytest.raises(Exception): _root(cli._c_path(path))
    else:
        with pytest.raises(ValueError): cli._c_path(path)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _fixture(tmp_path, monkeypatch):
    from tools import wd_q4_genesis_cli as cli, wd_q4_genesis_bootstrap as genesis
    from sandbox.mind.step2 import wd_q4_prefix_build_child as child
    repo = Path(__file__).resolve().parents[2]
    root = tmp_path
    dirs = {name: root / name for name in ('bundle', 'source', 'sidecar', 'checkpoint', 'private', 'model', 'inputs')}
    for path in dirs.values(): path.mkdir()
    code = dirs['bundle'] / 'tools-bootstrap' / 'tools'
    code.mkdir(parents=True)
    files = {}
    for module in (cli, genesis):
        dest = code / Path(module.__file__).name
        dest.write_bytes(Path(module.__file__).read_bytes())
        files['tools-bootstrap/tools/' + dest.name] = _sha(dest.read_bytes()).upper()
        monkeypatch.setattr(module, '__file__', str(dest))
    fleet = (repo / 'ops/windows/reboot/wd-fleet.json').read_bytes()
    (dirs['bundle'] / 'wd-fleet.json').write_bytes(fleet)
    files['wd-fleet.json'] = _sha(fleet).upper()
    manifest = json.dumps({'files': files}).encode()
    (dirs['bundle'] / 'deployment-manifest.json').write_bytes(manifest)
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', _sha(manifest).upper())
    policy = (repo / 'configs/mind/pilot_non_review_policy.json').read_bytes()
    public = (repo / 'configs/mind/pilot_public_status_policy.json').read_bytes()
    (dirs['inputs'] / 'policy.json').write_bytes(policy)
    (dirs['inputs'] / 'public.json').write_bytes(public)
    source = b'{"synthetic":true}\n'
    (dirs['source'] / 'events.jsonl').write_bytes(source)
    from waggledance.core.bridge_log_reader import _file_identity
    with (dirs['source'] / 'events.jsonl').open('rb') as stream: source_id = _file_identity(stream)
    weights = b'synthetic model bytes'
    (dirs['model'] / child.WEIGHTS_FILE).write_bytes(weights)
    config = {'schema': 'wd.q4-genesis-cli.v1', 'bundle_root': str(dirs['bundle']),
        'manifest_sha256': _sha(manifest), 'source_path': str(dirs['source'] / 'events.jsonl'),
        'source_sha256': _sha(source), 'source_bytes': len(source), 'source_file_identity': source_id,
        'generation_path': str(dirs['sidecar'] / 'generation.json'), 'generation': 'synthetic',
        'checkpoint_root': str(dirs['checkpoint']), 'private_state_root': str(dirs['private']),
        'trusted_model_root': str(dirs['model']), 'policy_path': str(dirs['inputs'] / 'policy.json'),
        'policy_sha256': _sha(policy), 'public_policy_path': str(dirs['inputs'] / 'public.json'),
        'public_policy_sha256': _sha(public), 'fleet_sha256': _sha(fleet), 'max_prefix_bytes': 1024,
        'max_bytes': 512, 'max_rows': 20, 'plan_template': {
            'schema': 'wd.q4-prefix-build-plan.v3', 'generation': 'synthetic', 'expected_pointer_sha256': None,
            'max_prefix_bytes': 1024, 'max_windows': 2, 'timeout': 1, 'fenced': True,
            'encoder': {'model': child.ENCODER_MODEL, 'revision': 'a' * 40, 'dim': child.ENCODER_DIM,
                        'model_dir': str(dirs['model']), 'weights_sha256': _sha(weights),
                        'assets': {child.WEIGHTS_FILE: _sha(weights)}}},
        'renewable': False, 'cutoff_utc': None, 'batch_rows': False, 'min_headroom_bytes': 1,
        'allow_partial_genesis': False}
    path = dirs['inputs'] / 'config.json'
    def write():
        raw = json.dumps(config).encode()
        path.write_bytes(raw)
        return _sha(raw)
    return cli, dirs, config, path, write


def test_real_preflight_verifies_config_package_and_source_without_writes(tmp_path, monkeypatch):
    cli, dirs, config, path, write = _fixture(tmp_path, monkeypatch)
    pin = write()
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*'))
    prepared = cli._prepare(str(path), pin)
    assert prepared[0] == config
    assert not list(dirs['checkpoint'].iterdir()) and not list(dirs['private'].iterdir())
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*')) == before


def test_real_pinned_source_accepts_append_and_detects_changed_prefix(tmp_path, monkeypatch):
    from tools import wd_q4_genesis_bootstrap as genesis
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
    from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin
    cli, dirs, config, path, write = _fixture(tmp_path, monkeypatch)
    pin = write()
    source = dirs['source'] / 'events.jsonl'
    with source.open('ab') as stream: stream.write(b'{"new":true}\n')
    assert cli._prepare(str(path), pin)[0] == config
    updated = source.read_bytes()
    private_tuple = b'{"schema":"wd.q4-genesis-private-tuple.v1"}'
    (dirs['private'] / 'q4-genesis-tuple-v1.json').write_bytes(private_tuple)
    receipt = {'status': 'PUBLISHED_GENESIS', 'pointer_sha256': 'a' * 64,
               'genesis_tuple_sha256': _sha(private_tuple), 'genesis_snapshot_bytes': len(updated),
               'genesis_initial_prefix_fully_covered': True}
    prefix = BridgePrefixPin(BridgeCursor(len(updated), config['source_file_identity'], 'synthetic'), _sha(updated))
    monkeypatch.setattr(checkpoint, 'read_verified_prefix_pointer', lambda *args, **kwargs: ({}, prefix))
    policy = (dirs['inputs'] / 'policy.json').read_bytes()
    fleet = (dirs['bundle'] / 'wd-fleet.json').read_bytes()
    assert cli._verify_result(config, genesis, receipt, policy, fleet) is True
    partial = BridgePrefixPin(BridgeCursor(config['source_bytes'], config['source_file_identity'], 'synthetic'),
                              config['source_sha256'])
    monkeypatch.setattr(checkpoint, 'read_verified_prefix_pointer', lambda *args, **kwargs: ({}, partial))
    receipt.update(genesis_initial_prefix_fully_covered=False, coverage='partial_ready_prefix')
    with pytest.raises(ValueError): cli._verify_result(config, genesis, receipt, policy, fleet)
    config['allow_partial_genesis'] = True
    assert cli._verify_result(config, genesis, receipt, policy, fleet) is False
    receipt['genesis_initial_prefix_fully_covered'] = True
    with pytest.raises(ValueError): cli._verify_result(config, genesis, receipt, policy, fleet)
    receipt['genesis_initial_prefix_fully_covered'] = False
    source.write_bytes(b'{"synthetic":false}\n' + b'{"new":true}\n')
    with pytest.raises(ValueError): cli._verify_result(config, genesis, receipt, policy, fleet)


@pytest.mark.parametrize('case', ['wrong_config_hash', 'source_changed', 'manifest_changed', 'fleet_changed',
                                  'policy_changed', 'code_changed', 'model_changed', 'overlap', 'unknown_key',
                                  'model_off_c', 'batch_without_cutoff', 'partial_without_batch', 'bad_cutoff',
                                  'bad_headroom', 'boolean_headroom'])
def test_real_preflight_refuses_changed_or_unbound_inputs(tmp_path, monkeypatch, case):
    cli, dirs, config, path, write = _fixture(tmp_path, monkeypatch)
    assert cli._prepare(str(path), write())[0] == config
    if case == 'overlap': config['private_state_root'] = config['checkpoint_root']
    if case == 'unknown_key': config['unexpected'] = True
    if case == 'model_off_c': config['plan_template']['encoder']['model_dir'] = 'F:\\unbound'
    if case == 'batch_without_cutoff': config['batch_rows'] = True
    if case == 'partial_without_batch': config['allow_partial_genesis'] = True
    if case == 'bad_cutoff': config['cutoff_utc'] = '2026-02-30T00:00:00Z'
    if case == 'bad_headroom': config['min_headroom_bytes'] = 0
    if case == 'boolean_headroom': config['min_headroom_bytes'] = True
    pin = write()
    if case == 'wrong_config_hash': pin = '0' * 64
    if case == 'source_changed': (dirs['source'] / 'events.jsonl').write_bytes(b'{"different":1}\n')
    if case == 'manifest_changed': (dirs['bundle'] / 'deployment-manifest.json').write_bytes(b'{}')
    if case == 'fleet_changed': (dirs['bundle'] / 'wd-fleet.json').write_bytes(b'{}')
    if case == 'policy_changed': (dirs['inputs'] / 'policy.json').write_bytes(b'{}')
    if case == 'code_changed': (dirs['bundle'] / 'tools-bootstrap/tools/wd_q4_genesis_cli.py').write_bytes(b'# changed')
    if case == 'model_changed': (dirs['model'] / 'model.safetensors').write_bytes(b'changed')
    with pytest.raises(Exception): cli._prepare(str(path), pin)
    assert not list(dirs['checkpoint'].iterdir()) and not list(dirs['private'].iterdir())


# Late public (lead-mind-genesis-skip-cli-20261106-v1): explicit opt-in resume_genesis_skip; progress is reported as
# GENESIS_SKIP_PROGRESS (exit 4) without claiming publication and the same pinned config resumes.
def _stub_config(**extra):
    config = {'source_path': 'private', 'source_sha256': 'a' * 64, 'source_bytes': 17,
              'source_file_identity': 'windows-v1:00000001:0000000000000001', 'generation_path': 'private',
              'generation': 'synthetic', 'checkpoint_root': 'private', 'private_state_root': 'private',
              'trusted_model_root': 'private', 'policy_sha256': 'b' * 64, 'public_policy_sha256': 'c' * 64,
              'fleet_sha256': 'd' * 64, 'max_prefix_bytes': 1024, 'max_bytes': 32, 'max_rows': 1,
              'plan_template': {}, 'renewable': True, 'cutoff_utc': '2026-01-01T00:00:00Z', 'batch_rows': True,
              'min_headroom_bytes': 128, 'allow_partial_genesis': True}
    config.update(extra)
    return config


def test_default_config_without_the_key_calls_bootstrap_exactly_as_before(monkeypatch, capsys):
    from tools import wd_q4_genesis_cli as cli, wd_q4_genesis_bootstrap as genesis
    config = _stub_config()
    monkeypatch.setattr(cli, '_prepare', lambda *_: (config, b'p', b'u', b'f', genesis))
    calls = []

    def bootstrap(**kwargs):
        calls.append(kwargs)
        raise genesis.GenesisRefused('genesis_skip_progress')

    monkeypatch.setattr(genesis, 'bootstrap', bootstrap)
    monkeypatch.setattr(cli, '_verify_skip_progress', lambda *_: pytest.fail('progress needs the opt-in'),
                        raising=False)
    assert cli.main(['--enable', '--config', 'private', '--sha256', 'a' * 64]) == 2
    assert json.loads(capsys.readouterr().out) == {'status': 'REFUSED'}
    assert len(calls) == 1 and 'resume_genesis_skip' not in calls[0]


@pytest.mark.parametrize('outcome,code,expected', [
    ('progress', 4, {'status': 'GENESIS_SKIP_PROGRESS', 'skip_receipt_sha256': 'e' * 64}),
    ('progress_unverified', 3, {'status': 'UNKNOWN'}),
    ('progress_interrupted', 3, {'status': 'UNKNOWN'}),
    ('other_refusal', 2, {'status': 'REFUSED'})])
def test_opt_in_reports_progress_only_after_verification(monkeypatch, capsys, outcome, code, expected):
    from tools import wd_q4_genesis_cli as cli, wd_q4_genesis_bootstrap as genesis
    config = _stub_config(resume_genesis_skip=True)
    monkeypatch.setattr(cli, '_prepare', lambda *_: (config, b'p', b'u', b'f', genesis))
    calls = []

    def bootstrap(**kwargs):
        calls.append(kwargs)
        raise genesis.GenesisRefused('genesis_no_public_row' if outcome == 'other_refusal' else 'genesis_skip_progress')

    def verify(*_):
        if outcome == 'progress_unverified':
            raise ValueError('synthetic-private-canary')
        if outcome == 'progress_interrupted':
            raise KeyboardInterrupt()
        return {'skip_receipt_sha256': 'e' * 64}

    monkeypatch.setattr(genesis, 'bootstrap', bootstrap)
    monkeypatch.setattr(cli, '_verify_skip_progress', verify, raising=False)
    assert cli.main(['--enable', '--config', 'private', '--sha256', 'a' * 64]) == code
    output = capsys.readouterr()
    assert json.loads(output.out) == expected and 'synthetic-private-canary' not in output.out + output.err
    assert len(calls) == 1 and calls[0]['resume_genesis_skip'] is True


@pytest.mark.parametrize('case', ['not_boolean', 'without_partial', 'runner_changed', 'runner_unlisted'])
def test_real_preflight_refuses_an_unbound_skip_opt_in(tmp_path, monkeypatch, case):
    cli, dirs, config, path, write = _fixture(tmp_path, monkeypatch)
    _pin_runner(dirs, monkeypatch, config)
    config.update(cutoff_utc='2026-01-01T00:00:00Z', batch_rows=True, allow_partial_genesis=True,
                  resume_genesis_skip=True)
    assert cli._prepare(str(path), write())[0] == config
    if case == 'not_boolean': config['resume_genesis_skip'] = 1
    if case == 'without_partial': config['allow_partial_genesis'] = False
    if case == 'runner_changed':
        (dirs['bundle'] / 'tools-bootstrap/tools/wd_q4_status_prefix_runner.py').write_bytes(b'# changed')
    if case == 'runner_unlisted':
        manifest = json.loads((dirs['bundle'] / 'deployment-manifest.json').read_bytes())
        del manifest['files']['tools-bootstrap/tools/wd_q4_status_prefix_runner.py']
        raw = json.dumps(manifest).encode()
        (dirs['bundle'] / 'deployment-manifest.json').write_bytes(raw)
        monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', _sha(raw).upper())
        config['manifest_sha256'] = _sha(raw)
    pin = write()
    with pytest.raises(Exception): cli._prepare(str(path), pin)
    assert not list(dirs['checkpoint'].iterdir()) and not list(dirs['private'].iterdir())


def test_real_preflight_accepts_the_bound_skip_opt_in_without_writes(tmp_path, monkeypatch):
    cli, dirs, config, path, write = _fixture(tmp_path, monkeypatch)
    _pin_runner(dirs, monkeypatch, config)
    config.update(cutoff_utc='2026-01-01T00:00:00Z', batch_rows=True, allow_partial_genesis=True,
                  resume_genesis_skip=True)
    pin = write()
    assert cli._prepare(str(path), pin)[0] == config
    assert not list(dirs['checkpoint'].iterdir()) and not list(dirs['private'].iterdir())


def _pin_runner(dirs, monkeypatch, config):
    """List the status runner in the fixture manifest, as a deployed bundle does."""
    from tools import wd_q4_status_prefix_runner as runner
    code = dirs['bundle'] / 'tools-bootstrap' / 'tools' / 'wd_q4_status_prefix_runner.py'
    code.write_bytes(Path(runner.__file__).read_bytes())
    monkeypatch.setattr(runner, '__file__', str(code))
    manifest_path = dirs['bundle'] / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['tools-bootstrap/tools/wd_q4_status_prefix_runner.py'] = _sha(code.read_bytes()).upper()
    raw = json.dumps(manifest).encode()
    manifest_path.write_bytes(raw)
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', _sha(raw).upper())
    config['manifest_sha256'] = _sha(raw)


def _late_public_cli(tmp_path, monkeypatch, kinds, max_windows=2):
    """Actual CLI, bootstrap, pre-scan, skip receipts and runner on a synthetic multi-window source; synthetic
    child and checkpoint accessor from the bootstrap tests."""
    from tools import wd_q4_genesis_cli as cli, wd_q4_genesis_bootstrap as genesis
    from waggledance.core.bridge_log_reader import _file_identity
    path = Path(__file__).with_name('test_wd_q4_genesis_bootstrap.py')
    spec = importlib.util.spec_from_file_location('q4_cli_genesis_fixture_late', path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    args, calls = helper.synthetic.__wrapped__(tmp_path, monkeypatch)
    monkeypatch.setattr(genesis, '_prescan_public_rows', helper.REAL_PRESCAN)
    rows = helper.f2_rows(args, kinds)
    run = helper.f2_args(args, rows, max_windows=max_windows)
    source = Path(run['source_path'])
    data = source.read_bytes()
    with source.open('rb') as stream: source_id = _file_identity(stream)
    config = {'source_path': run['source_path'], 'source_sha256': _sha(data), 'source_bytes': len(data),
        'source_file_identity': source_id, 'generation_path': run['generation_path'], 'generation': run['generation'],
        'checkpoint_root': run['checkpoint_root'], 'private_state_root': run['private_state_root'],
        'trusted_model_root': run['trusted_model_root'], 'policy_sha256': run['policy_sha256'],
        'public_policy_sha256': run['public_policy_sha256'], 'fleet_sha256': run['expected_fleet_sha256'],
        'max_prefix_bytes': run['max_prefix_bytes'], 'max_bytes': run['max_bytes'], 'max_rows': run['max_rows'],
        'plan_template': run['plan_template'], 'renewable': False, 'cutoff_utc': run['cutoff_utc'],
        'batch_rows': True, 'min_headroom_bytes': 1, 'allow_partial_genesis': True, 'resume_genesis_skip': True}
    monkeypatch.setattr(cli, '_prepare', lambda *_: (config, run['policy_bytes'], run['public_policy_bytes'],
                                                     run['fleet_bytes'], genesis))
    return cli, config, rows, calls


def _run(cli, capsys):
    code = cli.main(['--enable', '--config', 'private', '--sha256', 'a' * 64])
    return code, json.loads(capsys.readouterr().out)


def test_real_cli_skips_empty_windows_resumes_and_publishes_from_the_receipt(tmp_path, monkeypatch, capsys):
    cli, config, rows, calls = _late_public_cli(tmp_path, monkeypatch, ['other_task', 'private'] * 4 + ['public'])
    receipt = Path(config['private_state_root']) / 'q4-genesis-skip-v1.json'
    seen = []
    for expected_runs in (1, 2):
        code, output = _run(cli, capsys)
        assert code == 4 and set(output) == {'status', 'skip_receipt_sha256'}
        assert output['status'] == 'GENESIS_SKIP_PROGRESS' and output['skip_receipt_sha256'] == _sha(receipt.read_bytes())
        assert json.loads(receipt.read_bytes())['runs'] == expected_runs and calls == []
        assert not Path(config['generation_path']).exists()
        seen.append(output['skip_receipt_sha256'])
    assert seen[0] != seen[1] and json.loads(receipt.read_bytes())['offset'] == len(b''.join(rows[:8]))
    code, output = _run(cli, capsys)
    assert code == 0 and output['status'] in ('PUBLISHED_GENESIS', 'PUBLISHED_GENESIS_PARTIAL')
    assert set(output) <= {'status', 'pointer_sha256', 'runner_receipt_sha256'} and len(calls) == 1
    assert calls[0]['config'].source.cursor.offset == len(b''.join(rows[:8]))
    text = json.dumps(output)
    assert str(len(b''.join(rows[:8]))) not in text and json.loads(receipt.read_bytes())['prefix_sha256'] not in text


def test_real_cli_progress_never_moves_past_a_changed_or_replaced_source(tmp_path, monkeypatch, capsys):
    cli, config, rows, calls = _late_public_cli(tmp_path, monkeypatch, ['other_task', 'private'] * 4 + ['public'])
    receipt = Path(config['private_state_root']) / 'q4-genesis-skip-v1.json'
    assert _run(cli, capsys)[0] == 4
    before = receipt.read_bytes()
    source = Path(config['source_path'])
    copy = source.with_name('copy.jsonl')
    copy.write_bytes(source.read_bytes())
    copy.replace(source)
    assert _run(cli, capsys) == (2, {'status': 'REFUSED'})
    assert receipt.read_bytes() == before and calls == [] and not Path(config['generation_path']).exists()


def test_real_cli_unverifiable_progress_is_unknown(tmp_path, monkeypatch, capsys):
    from tools import wd_q4_status_prefix_runner as runner
    cli, config, rows, calls = _late_public_cli(tmp_path, monkeypatch, ['other_task', 'private'] * 4 + ['public'])
    original = runner._genesis_skip

    def tampered(raw):
        value = dict(original(raw))
        value['generation'] = 'another-generation'
        return value

    monkeypatch.setattr(runner, '_genesis_skip', tampered)
    assert _run(cli, capsys) == (3, {'status': 'UNKNOWN'})
    assert calls == [] and not Path(config['generation_path']).exists()


def test_real_cli_publication_must_bind_the_current_receipt(tmp_path, monkeypatch, capsys):
    from tools import wd_q4_genesis_bootstrap as genesis
    cli, config, rows, calls = _late_public_cli(tmp_path, monkeypatch, ['other_task', 'private'] * 2 + ['public'])
    assert _run(cli, capsys)[0] == 4
    receipt = Path(config['private_state_root']) / 'q4-genesis-skip-v1.json'
    real = genesis.bootstrap

    def swap_after(**kwargs):
        result = real(**kwargs)
        value = json.loads(receipt.read_bytes())
        value['runs'] += 7
        receipt.write_bytes(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())
        return result

    monkeypatch.setattr(genesis, 'bootstrap', swap_after)
    assert _run(cli, capsys) == (3, {'status': 'UNKNOWN'})
    assert len(calls) == 1


@pytest.mark.parametrize('fault', ['none', 'sidecar_exists', 'tuple_exists', 'receipt_policy', 'receipt_public_policy',
                                   'source_pin', 'receipt_prefix', 'receipt_missing'])
def test_skip_progress_verification_checks_state_receipt_and_source(tmp_path, monkeypatch, capsys, fault):
    from tools import wd_q4_genesis_bootstrap as genesis
    cli, config, rows, calls = _late_public_cli(tmp_path, monkeypatch, ['other_task', 'private'] * 4 + ['public'])
    assert _run(cli, capsys)[0] == 4
    receipt = Path(config['private_state_root']) / 'q4-genesis-skip-v1.json'
    value = json.loads(receipt.read_bytes())
    if fault == 'sidecar_exists': Path(config['generation_path']).write_bytes(b'{}')
    if fault == 'tuple_exists': (Path(config['private_state_root']) / 'q4-genesis-tuple-v1.json').write_bytes(b'{}')
    if fault in ('receipt_policy', 'receipt_public_policy', 'receipt_prefix'):
        key = {'receipt_policy': 'policy_sha256', 'receipt_public_policy': 'public_policy_sha256',
               'receipt_prefix': 'prefix_sha256'}[fault]
        value[key] = 'e' * 64
        receipt.write_bytes(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())
    if fault == 'source_pin': config['source_sha256'] = 'e' * 64
    if fault == 'receipt_missing': receipt.unlink()
    if fault == 'none':
        assert cli._verify_skip_progress(config, genesis) == {'skip_receipt_sha256': _sha(receipt.read_bytes())}
    else:
        with pytest.raises(Exception): cli._verify_skip_progress(config, genesis)
