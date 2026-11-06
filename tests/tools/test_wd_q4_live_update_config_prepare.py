"""Synthetic tests for preparing one pinned updater configuration; never install or schedule."""
import importlib
import importlib.util
import json
from pathlib import Path
import base64
import types
import sys
import inspect
import io

import pytest

_source = Path(__file__).with_name('test_wd_q4_production_continuation.py')
_spec = importlib.util.spec_from_file_location('config_prepare_synthetic_continuation', _source)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
synthetic = _fixture.synthetic


def test_disabled_does_not_inspect_inputs():
    module = importlib.import_module('tools.wd_q4_live_update_config_prepare')
    assert module.prepare_config(output_path=object(), baseline_path=object()) == {'status': 'OFF'}


def test_unpinned_configuration_refuses_before_creating_file(tmp_path):
    module = importlib.import_module('tools.wd_q4_live_update_config_prepare')
    output = tmp_path / 'update.json'
    with pytest.raises(module.ConfigPrepareRefused):
        module.prepare_config(enabled=True, output_path=str(output), baseline_path='missing',
                              expected_baseline_sha256='a' * 64, expected_manifest_sha256='b' * 64)
    assert not output.exists()


def test_cli_disabled_does_not_read_paths(capsys):
    module = importlib.import_module('tools.wd_q4_live_update_config_prepare')
    assert module.main(['--tuple-path', 'missing']) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'OFF'}


def test_pinned_private_tuple_is_materialized_without_changing_its_cutoff(tmp_path, monkeypatch, synthetic):
    """Real tuple and policies; descriptor/current-pointer reader is a synthetic seam."""
    module = importlib.import_module('tools.wd_q4_live_update_config_prepare')
    from tools import wd_q4_live_update_once as update
    continuation, args, state, document, route, plan, _ = synthetic
    bundle = tmp_path / 'bundle'
    (bundle / 'tools-bootstrap' / 'tools').mkdir(parents=True)
    files = {}
    for name in ('WdQ4ProcessJob.ps1', 'tools-bootstrap/tools/wd_q4_live_update_bootstrap.py',
                 'tools-bootstrap/tools/wd_q4_live_update_once.py'):
        path = bundle / name
        path.write_bytes(('synthetic:' + name).encode())
        files[name] = update._sha(path.read_bytes()).upper()
    manifest = update._encode({'schema_version': 1, 'files': files})
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    manifest_pin = update._sha(manifest)
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', manifest_pin)
    policies = tmp_path / 'policies'
    policies.mkdir()
    policy, public_policy = policies / 'policy.json', policies / 'public.json'
    policy.write_bytes(args['policy_bytes'])
    public_policy.write_bytes(args['public_policy_bytes'])
    tuple_path = tmp_path / 'genesis-tuple.json'
    tuple_path.write_bytes(args['tuple_bytes'])
    base_dir = tmp_path / 'generations' / 'synthetic'
    base_dir.mkdir(parents=True)
    baseline_path = base_dir / 'descriptor.json'
    baseline_path.write_bytes(b'synthetic-descriptor')
    binding = {key: route[key] for key in ('policy_sha256', 'roster_sha256', 'public_policy_sha256')}
    binding['pointer_sha256'] = state['pointer']
    descriptor = {'bundle_root': str(bundle), 'fleet_sha256': args['expected_fleet_sha256']}
    baseline = (descriptor, b'synthetic-descriptor', update._sha(b'synthetic-descriptor'),
                args['fleet_bytes'], binding, {})
    monkeypatch.setattr(module.caller, '_configuration', lambda *_: baseline)
    monkeypatch.setattr(module.caller, '_require_current_snapshot', lambda *_: None)
    public = {'root': route['checkpoint_root'], 'policy_path': str(policy), 'public_policy_path': str(public_policy)}
    monkeypatch.setattr(module.live, '_plan', lambda *_: (public, {}, b''))
    out_dir = tmp_path / 'output'
    out_dir.mkdir()
    output = out_dir / 'updater-config.json'
    kwargs = dict(enabled=True, output_path=str(output), baseline_path=str(baseline_path),
                  expected_baseline_sha256=baseline[2], expected_manifest_sha256=manifest_pin,
                  tuple_path=str(tuple_path), expected_tuple_sha256=args['expected_tuple_sha256'])
    result = module.prepare_config(**kwargs)
    assert result['status'] == 'PREPARED' and result['freshness'] == 'UNKNOWN'
    assert result['cutoff_utc'] == route['cutoff_utc']
    raw = output.read_bytes()
    assert result['config_sha256'] == update._sha(raw)
    config = json.loads(raw)
    assert set(config) == update._CONFIG_KEYS
    assert json.loads(Path(config['route_path']).read_bytes()) == route
    assert json.loads(Path(config['plan_path']).read_bytes()) == plan
    assert update._sha(Path(config['route_path']).read_bytes()) == config['route_sha256']
    with pytest.raises(module.ConfigPrepareRefused):
        module.prepare_config(**kwargs)
    assert output.read_bytes() == raw
    other = tmp_path / 'other'
    other.mkdir()
    with pytest.raises(module.ConfigPrepareRefused):
        module.prepare_config(**{**kwargs, 'output_path': str(other / 'updater-config.json'),
                                 'expected_tuple_sha256': '0' * 64})
    assert not list(other.iterdir())
    job = bundle / 'WdQ4ProcessJob.ps1'
    job_raw = job.read_bytes()
    job.write_bytes(b'synthetic tamper')
    with pytest.raises(module.ConfigPrepareRefused):
        module.prepare_config(**{**kwargs, 'output_path': str(other / 'updater-config.json')})
    assert not list(other.iterdir())
    job.write_bytes(job_raw)
    create = module.runner._create
    def fail_second(path, content):
        if path.name == 'q4-updater-plan.json':
            raise OSError('synthetic interruption')
        return create(path, content)
    with monkeypatch.context() as scoped:
        scoped.setattr(module.runner, '_create', fail_second)
        with pytest.raises(module.ConfigPrepareUnknown):
            module.prepare_config(**{**kwargs, 'output_path': str(other / 'updater-config.json')})
    assert (other / 'q4-updater-route.json').is_file()
    assert not (other / 'updater-config.json').exists()
    interrupted = tmp_path / 'interrupted'
    interrupted.mkdir()
    def interrupt_second(path, content):
        if path.name == 'q4-updater-plan.json':
            raise KeyboardInterrupt()
        return create(path, content)
    with monkeypatch.context() as scoped:
        scoped.setattr(module.runner, '_create', interrupt_second)
        with pytest.raises(module.ConfigPrepareInterrupted):
            module.prepare_config(**{**kwargs, 'output_path': str(interrupted / 'updater-config.json')})
    assert (interrupted / 'q4-updater-route.json').is_file()
    assert not (interrupted / 'updater-config.json').exists()


@pytest.mark.skipif(__import__('os').name != 'nt', reason='real guarded synthetic caller needs Windows')
@pytest.mark.parametrize('batch', [True, False])
def test_materializes_from_real_synthetic_descriptor_and_fenced_checkpoint(tmp_path, monkeypatch, capsys, batch):
    pytest.importorskip('faiss')
    module = importlib.import_module('tools.wd_q4_live_update_config_prepare')
    from tools import wd_q4_live_update_once as update
    path = Path(__file__).with_name('test_wd_q4_live_snapshot_e2e.py')
    spec = importlib.util.spec_from_file_location('config_prepare_real_e2e', path)
    e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e)
    original_helper, original_enc = e2e.helper, e2e.enc
    state = {'runs': [], 'publications': []}
    original_publish = update.live.publish
    def observe_publication(**kwargs):
        state['publications'].append(kwargs)
        return original_publish(**kwargs)
    monkeypatch.setattr(update.live, 'publish', observe_publication)
    def helper(name, source):
        loaded = original_helper(name, source)
        if source == 'test_wd_q4_status_prefix_runner.py':
            original = loaded.runner.__wrapped__
            def make_runner():
                runner = original()
                run = runner.run_once
                state['runner'] = runner
                state['raw_run'] = run
                def observe(**kwargs):
                    state['runs'].append(kwargs)
                    return run(**kwargs)
                runner.run_once = observe
                return runner
            loaded.runner = types.SimpleNamespace(__wrapped__=make_runner)
        return loaded
    def with_job_assets(value):
        if isinstance(value, dict) and value.get('schema_version') == 1 and 'files' in value:
            bundle = tmp_path / 'installed-bundle'
            entries = dict(value['files'])
            for name in ('WdQ4ProcessJob.ps1', 'tools-bootstrap/tools/wd_q4_live_update_bootstrap.py',
                         'tools-bootstrap/tools/wd_q4_live_update_once.py'):
                target = bundle / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(('synthetic:' + name).encode())
                entries[name] = update._sha(target.read_bytes()).upper()
            value = {**value, 'files': entries}
        return original_enc(value)
    monkeypatch.setattr(e2e, 'helper', helper)
    monkeypatch.setattr(e2e, 'enc', with_job_assets)
    e2e.test_two_real_fenced_publications_are_selected_by_running_caller(tmp_path, monkeypatch, capsys, 'none')
    monkeypatch.setattr(state['runner'], 'run_once', state['raw_run'])
    first = state['runs'][0]
    route, plan = json.loads(first['route_bytes']), json.loads(first['plan_bytes'])
    route['cutoff_utc'] = '2026-11-01T00:00:00Z'
    if batch:
        route['batch_rows'] = True
    source = Path(route['source'])
    source_bytes = source.read_bytes()
    tuple_doc = {'schema': 'wd.q4-genesis-private-tuple.v1', 'publication': 'UNKNOWN',
        'allow_partial_genesis': True,
        'independent_source_pin': {'identity': route['cursor']['file_identity'], 'prefix_bytes': len(source_bytes),
                                   'prefix_sha256': update._sha(source_bytes)},
        **{name: {'bytes_b64': base64.b64encode(raw).decode('ascii'), 'sha256': update._sha(raw)}
           for name, raw in (('route', update._encode(route)), ('plan', first['plan_bytes']), ('anchor', first['anchor_bytes']))}}
    tuple_path = tmp_path / 'genesis-tuple.json'
    tuple_path.write_bytes(update._encode(tuple_doc))
    publication = state['publications'][0]
    baseline_path = Path(publication['baseline_path'])
    baseline_pin = publication['baseline_sha256']
    manifest_pin = update._sha((tmp_path / 'installed-bundle' / 'deployment-manifest.json').read_bytes())
    for name in ('_FLEET_SHA', '_POLICY_SHA', '_PUBLIC_SHA'):
        monkeypatch.setattr(module.continuation.runner, name, getattr(state['runner'], name))
    output_dir = tmp_path / 'prepared-config'
    output_dir.mkdir()
    result = module.prepare_config(enabled=True, output_path=str(output_dir / 'updater-config.json'),
        baseline_path=str(baseline_path), expected_baseline_sha256=baseline_pin,
        expected_manifest_sha256=manifest_pin, tuple_path=str(tuple_path), expected_tuple_sha256=update._sha(tuple_path.read_bytes()))
    assert result['status'] == 'PREPARED' and result['cutoff_utc'] == route['cutoff_utc']
    assert update._sha((output_dir / 'updater-config.json').read_bytes()) == result['config_sha256']
    second = tmp_path / 'prepared-config-cli'
    second.mkdir()
    capsys.readouterr()
    assert module.main(['--prepare', '--output-path', str(second / 'updater-config.json'),
        '--baseline-path', str(baseline_path), '--baseline-sha256', baseline_pin,
        '--manifest-sha256', manifest_pin, '--tuple-path', str(tuple_path),
        '--tuple-sha256', update._sha(tuple_path.read_bytes())]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'PREPARED' and report['cutoff_utc'] == route['cutoff_utc']
    assert update._sha((second / 'updater-config.json').read_bytes()) == report['config_sha256']
    assert 'config_path' not in report
    monkeypatch.setattr(update, 'runner', state['runner'])
    original_event = json.loads(source_bytes.splitlines()[0])
    original_event['ts_utc'] = '2026-10-31T12:04:00Z'
    with source.open('ab') as stream:
        stream.write(update._encode(original_event) + b'\n')
    from sandbox.mind.step2 import wd_q4_prefix_build_child as child
    prior_child = inspect.getclosurevars(child.build_prefix_generation).nonlocals['original_child']
    monkeypatch.setattr(child, 'build_prefix_generation', prior_child)
    actual = update.run_once(enabled=True, config_path=str(output_dir / 'updater-config.json'),
                             expected_config_sha256=result['config_sha256'])
    assert actual['status'] == 'PUBLISHED' and actual['freshness'] == 'UNKNOWN'
    query = update._encode({'situation': 'synthetic recall', 'time': '2026-11-01T12:00:00Z'})
    monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(query), encoding='utf-8'))
    capsys.readouterr()
    assert update.caller.main(['--enabled']) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer['answer']['binding']['pointer_sha256'] == actual['pointer_sha256']
    # The pinned genesis cutoff deliberately prevents claiming rolling freshness.
    # The next event is after that cutoff, so it remains deferred. The report
    # must not claim that the event stream was caught up.
    original_event['ts_utc'] = '2026-11-01T00:01:00Z'
    with source.open('ab') as stream:
        stream.write(update._encode(original_event) + b'\n')
    reports = []
    real_run = state['runner'].run_once
    def observe_deferred(**kwargs):
        report = real_run(**kwargs)
        reports.append(report)
        return report
    monkeypatch.setattr(state['runner'], 'run_once', observe_deferred)
    deferred = update.run_once(enabled=True, config_path=str(output_dir / 'updater-config.json'),
                               expected_config_sha256=result['config_sha256'])
    assert deferred['status'] == 'NO_CHANGE' and deferred['pointer_sha256'] == actual['pointer_sha256']
    assert reports[0]['status'] == 'EMPTY_NO_CHANGE' and reports[0]['coverage'] == 'partial_ready_prefix'
    assert not (Path(route['private_state_root']) / 'q4-live-update.pending.json').exists()
