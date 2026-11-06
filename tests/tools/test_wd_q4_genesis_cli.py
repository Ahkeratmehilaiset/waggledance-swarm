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
