"""Synthetic-only tests for the opt-in producer wrapper."""
import pytest

from tools import wd_q4_live_update_once as update


def test_off_does_not_inspect_configuration():
    assert update.run_once(config_path=object(), expected_config_sha256=object()) == {'status': 'OFF'}


def test_enabled_requires_exact_external_config_pin(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text('{}', encoding='ascii')
    with pytest.raises(update.UpdateRefused):
        update.run_once(enabled=True, config_path=str(path), expected_config_sha256='0' * 64)


def test_cli_default_off(capsys):
    assert update.main([]) == 0
    assert 'OFF' in capsys.readouterr().out


def test_cli_never_reads_config_without_once(capsys):
    assert update.main(['--config', 'nonexistent', '--sha256', '0' * 64]) == 0
    assert 'OFF' in capsys.readouterr().out


def test_write_roots_cannot_overlap_bundle_or_generation(tmp_path):
    bundle, generations, index, private = (tmp_path / name for name in ('bundle', 'gens', 'index', 'private'))
    for path in (bundle, generations, index, private):
        path.mkdir()
    descriptor = generations / ('a' * 40) / 'shared-session-config.v2.json'
    update._require_write_roots(str(descriptor), str(bundle), str(index), str(private))
    with pytest.raises(ValueError):
        update._require_write_roots(str(descriptor), str(bundle), str(index), str(bundle))
    with pytest.raises(ValueError):
        update._require_write_roots(str(descriptor), str(bundle), str(generations), str(private))


@pytest.mark.skipif(__import__('os').name != 'nt', reason='real guarded peer requires Windows')
@pytest.mark.parametrize('fault_stage', ['capture', 'materialize', 'publish', 'after_selector', 'interrupt'])
def test_two_cli_updates_are_consumed_by_same_guarded_caller(tmp_path, monkeypatch, capsys, fault_stage):
    """Actual producer, capture, binding, selector and caller; synthetic model/peer."""
    import importlib.util
    import io
    import json
    from pathlib import Path
    import sys
    import types

    pytest.importorskip('faiss')
    source = Path(__file__).with_name('test_wd_q4_live_snapshot_e2e.py')
    spec = importlib.util.spec_from_file_location('live_update_existing_fixture', source)
    e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e)
    real_helper = e2e.helper
    real_publish = update.live.publish
    state = {'runs': [], 'publications': []}

    def helper(name, path):
        module = real_helper(name, path)
        if path == 'test_wd_q4_status_prefix_runner.py':
            old = module.runner.__wrapped__
            def make_runner():
                original = old()
                raw = original.run_once
                state.update(runner=original, raw_run=raw)
                def record(**kwargs):
                    state['runs'].append(kwargs)
                    return raw(**kwargs)
                original.run_once = record
                return original
            module.runner = types.SimpleNamespace(__wrapped__=make_runner)
        return module

    def publish(**kwargs):
        state['publications'].append(kwargs)
        return real_publish(**kwargs)

    monkeypatch.setattr(e2e, 'helper', helper)
    monkeypatch.setattr(update.live, 'publish', publish)
    e2e.test_two_real_fenced_publications_are_selected_by_running_caller(tmp_path, monkeypatch, capsys, 'none')
    assert len(state['runs']) == len(state['publications']) == 2
    monkeypatch.setattr(state['runner'], 'run_once', state['raw_run'])
    monkeypatch.setattr(update, 'runner', state['runner'])
    first, publication = state['runs'][0], state['publications'][0]
    route = json.loads(first['route_bytes'])
    descriptor, _, _, _, _, inputs = publication['baseline']
    public, _, _ = update.live._plan(descriptor, inputs)
    config_dir = tmp_path / 'explicit-config'
    config_dir.mkdir()
    for name, raw in (('route', first['route_bytes']), ('plan', first['plan_bytes']), ('anchor', first['anchor_bytes'])):
        (config_dir / name).write_bytes(raw)
    config = {'schema': 'wd.q4-live-update-once.v1', 'route_path': str(config_dir / 'route'),
        'route_sha256': update._sha(first['route_bytes']), 'plan_path': str(config_dir / 'plan'),
        'anchor_path': str(config_dir / 'anchor'), 'policy_path': public['policy_path'],
        'public_policy_path': public['public_policy_path'], 'baseline_path': publication['baseline_path'],
        'baseline_sha256': publication['baseline_sha256'], 'manifest_sha256': publication['manifest_sha256']}
    raw = update._encode(config)
    config_path = config_dir / 'config.json'
    config_path.write_bytes(raw)
    bridge = Path(route['source'])
    event = json.loads(bridge.read_bytes().splitlines()[0])
    real_open = update.os.open
    def marker_race(path, flags, *args, **kwargs):
        if str(path).endswith('q4-live-update.pending.json'):
            raise FileExistsError('synthetic competing owner')
        return real_open(path, flags, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(update.os, 'open', marker_race)
        assert update.main(['--once', '--config', str(config_path), '--sha256', update._sha(raw)]) == 2
        assert json.loads(capsys.readouterr().err)['status'] == 'REFUSED'
    answer_pins = []
    for minute in (2, 3):
        event['ts_utc'] = f'2026-10-31T12:0{minute}:00Z'
        with bridge.open('ab') as stream:
            stream.write(update._encode(event) + b'\n')
        assert update.main(['--once', '--config', str(config_path), '--sha256', update._sha(raw)]) == 0
        receipt = json.loads(capsys.readouterr().out)
        assert receipt['status'] == 'PUBLISHED' and receipt['freshness'] == 'UNKNOWN'
        query = update._encode({'situation': 'synthetic recall', 'time': '2026-11-01T12:00:00Z'})
        monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(query), encoding='utf-8'))
        assert update.caller.main(['--enabled']) == 0
        answer = json.loads(capsys.readouterr().out)
        assert answer['status'] == 'VALIDATED' and answer['inner_status'] == 'ANSWER'
        assert answer['descriptor_sha256'] == receipt['descriptor_sha256']
        assert answer['answer']['binding']['pointer_sha256'] == receipt['pointer_sha256']
        answer_pins.append(receipt['pointer_sha256'])
    assert answer_pins[0] != answer_pins[1]
    private_root = Path(route['private_state_root'])
    assert not (private_root / 'q4-live-update.pending.json').exists()
    assert len(list(private_root.glob('q4-live-update-result-*.json'))) == 2
    event['ts_utc'] = '2026-10-31T12:04:00Z'
    with bridge.open('ab') as stream:
        stream.write(update._encode(event) + b'\n')
    calls = []
    def counted_run(**kwargs):
        calls.append(1)
        return state['raw_run'](**kwargs)
    monkeypatch.setattr(state['runner'], 'run_once', counted_run)
    def fail(*_args, **_kwargs):
        raise KeyboardInterrupt() if fault_stage == 'interrupt' else ValueError('synthetic failure')
    def fail_after_selector(**kwargs):
        real_publish(**kwargs)
        raise ValueError('synthetic post-selector failure')
    with monkeypatch.context() as scoped:
        if fault_stage in ('capture', 'interrupt'):
            scoped.setattr(update, '_snapshot', fail)
        elif fault_stage == 'materialize':
            scoped.setattr(update.materializer, 'materialize_descriptor', fail)
        else:
            scoped.setattr(update.live, 'publish', fail_after_selector if fault_stage == 'after_selector' else fail)
        assert update.main(['--once', '--config', str(config_path), '--sha256', update._sha(raw)]) == (130 if fault_stage == 'interrupt' else 3)
    assert calls == [1]
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)['status'] == 'UNKNOWN'
    assert (private_root / 'q4-live-update.pending.json').is_file()
    progress_files = list(private_root.glob('q4-live-update-progress-*.json'))
    assert len(progress_files) == 3
    attempt = json.loads((private_root / 'q4-live-update.pending.json').read_bytes())['attempt']
    progress = json.loads((private_root / ('q4-live-update-progress-' + attempt + '.json')).read_bytes())
    assert progress['schema'] == 'wd.q4-live-update-progress.v1'
    assert progress['config_sha256'] == update._sha(raw)
    assert progress['pointer_sha256'] != answer_pins[-1]
    assert update.main(['--once', '--config', str(config_path), '--sha256', update._sha(raw)]) == 2
    assert json.loads(capsys.readouterr().err)['status'] == 'REFUSED' and calls == [1]
    query = update._encode({'situation': 'synthetic recall', 'time': '2026-11-01T12:00:00Z'})
    monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(query), encoding='utf-8'))
    assert update.caller.main(['--enabled']) == (0 if fault_stage == 'after_selector' else 2)
    observed = capsys.readouterr().out
    assert bool(observed) == (fault_stage == 'after_selector')
    from tools import wd_q4_live_update_recovery as recovery
    progress_path = private_root / ('q4-live-update-progress-' + attempt + '.json')
    kwargs = {'enabled': True, 'config_path': str(config_path), 'expected_config_sha256': update._sha(raw),
              'expected_attempt': attempt, 'expected_progress_sha256': update._sha(progress_path.read_bytes())}
    partial_generation = Path(config['baseline_path']).parent.parent / progress['pointer_sha256']
    if fault_stage in ('publish', 'after_selector'):
        assert partial_generation.is_dir()
    with pytest.raises(recovery.RecoveryRefused):
        recovery.recover(**{**kwargs, 'expected_progress_sha256': '0' * 64})
    with pytest.raises(recovery.RecoveryRefused):
        recovery.recover(**{**kwargs, 'expected_attempt': '0' * 64})
    if fault_stage == 'capture':
        import subprocess
        lock_code = ('import os,msvcrt,sys; f=os.open(sys.argv[1],os.O_RDWR|os.O_BINARY); '
                     'msvcrt.locking(f,msvcrt.LK_NBLCK,1); print("locked",flush=True); '
                     'sys.stdin.readline(); msvcrt.locking(f,msvcrt.LK_UNLCK,1); os.close(f)')
        peer = subprocess.Popen([sys.executable, '-I', '-S', '-c', lock_code, str(private_root / 'q4-live-update.lock')],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert peer.stdout.readline().strip() == 'locked'
            with pytest.raises(recovery.RecoveryRefused):
                recovery.recover(**kwargs)
        finally:
            peer.communicate(input='done\n', timeout=10)
        assert peer.returncode == 0
    result = recovery.recover(**kwargs)
    assert result['status'] == 'PUBLISHED' and result['pointer_sha256'] == progress['pointer_sha256']
    assert (private_root / result['receipt_file']).is_file()
    assert not (private_root / 'q4-live-update.pending.json').exists()
    if fault_stage in ('publish', 'after_selector'):
        assert partial_generation.is_dir()
    monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(query), encoding='utf-8'))
    assert update.caller.main(['--enabled']) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered['answer']['binding']['pointer_sha256'] == result['pointer_sha256']
