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
@pytest.mark.parametrize('fault_stage', ['capture', 'materialize', 'publish', 'after_selector', 'interrupt', 'empty_bad_fence'])
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
    if fault_stage in ('capture', 'empty_bad_fence'):
        from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
        checkpoint_root = Path(route['checkpoint_root'])
        orphan = checkpoint_root / ('q4-delta-' + 'f' * 64 + '.json')
        orphan.write_bytes(b'x' * (8 * 1024 * 1024))
        original_history_check = checkpoint._validate_pointer_history
        history_checks = []
        def observed_history(*args, **kwargs):
            history_checks.append(kwargs.get('snapshot_bytes') is not None)
            return original_history_check(*args, **kwargs)
        with monkeypatch.context() as scoped:
            scoped.setattr(checkpoint, '_validate_pointer_history', observed_history)
            pointer, frozen_snapshot = update._snapshot(checkpoint_root, answer_pins[-1], public['policy_path'],
                                                        public['roster_path'], public['public_policy_path'])
        assert True in history_checks
        parts = update.transport._fixed_bundle_decode(frozen_snapshot)
        tasks, roster = update.capture_adapter.strict_pins(parts['policy_bytes'], route['policy_sha256'],
                                                          parts['roster_bytes'], route['roster_sha256'])
        checkpoint._validate_pointer_history(None, parts['pointer_bytes'], pointer, tasks, roster,
            route['policy_sha256'], route['roster_sha256'], policy_bytes=parts['policy_bytes'],
            roster_bytes=parts['roster_bytes'], expected_source_mode=checkpoint._requested_source_mode(
                'public-v1', route['public_policy_sha256']), prefix_required=True, snapshot_bytes=parts['history_bytes'])
        assert orphan.name not in parts['history_bytes']
        delta = checkpoint_root / ('q4-delta-' + pointer['delta_projection_sha256'] + '.json')
        original_delta = delta.read_bytes()
        try:
            delta.write_bytes(b'synthetic-tamper')
            with pytest.raises(Exception):
                update._snapshot(checkpoint_root, answer_pins[-1], public['policy_path'],
                                 public['roster_path'], public['public_policy_path'])
        finally:
            delta.write_bytes(original_delta)
        backup = delta.with_name(delta.name + '.synthetic-backup')
        try:
            delta.rename(backup)
            with pytest.raises(Exception):
                update._snapshot(checkpoint_root, answer_pins[-1], public['policy_path'],
                                 public['roster_path'], public['public_policy_path'])
        finally:
            backup.rename(delta)
        with pytest.raises(ValueError):
            update._history_specs(checkpoint_root, {**pointer, 'previous_pointer_sha256': answer_pins[-1]}, answer_pins[-1])
        from tools import wd_q4_status_prefix_recovery as prefix_recovery
        from sandbox.mind.step2 import wd_q4_prefix_build_child as child_module
        from sandbox.mind.step2 import shared_bridge_snapshot as bridge_snapshot
        from sandbox.mind.step2 import shared_prefix_public_producer as prefix_producer
        prior_complete = prefix_recovery.complete_owned_publication
        prior_child = child_module.build_prefix_generation
        prior_catchup = bridge_snapshot.read_public_catchup
        prior_prepared = prefix_producer._publish_prepared
        errors = []
        def record_complete(**kwargs):
            try:
                return prior_complete(**kwargs)
            except Exception as exc:
                errors.append((type(exc).__name__, str(exc)))
                raise
        def record_child(**kwargs):
            try:
                return prior_child(**kwargs)
            except Exception as exc:
                errors.append((type(exc).__name__, str(exc)))
                raise
        def record_catchup(*args, **kwargs):
            try:
                value = prior_catchup(*args, **kwargs)
                errors.append(('catchup', value['end_reason'], len(value['windows']),
                               [(len(w['projection']['records']), w['projection']['refused']) for w in value['windows']]))
                return value
            except Exception as exc:
                errors.append((type(exc).__name__, str(exc)))
                raise
        def record_prepared(*args, **kwargs):
            try:
                return prior_prepared(*args, **kwargs)
            except Exception as exc:
                errors.append(('prepared', type(exc).__name__, str(exc)))
                raise
        with monkeypatch.context() as scoped:
            scoped.setattr(prefix_recovery, 'complete_owned_publication', record_complete)
            scoped.setattr(child_module, 'build_prefix_generation', record_child)
            scoped.setattr(bridge_snapshot, 'read_public_catchup', record_catchup)
            scoped.setattr(prefix_producer, '_publish_prepared', record_prepared)
            exit_code = update.main(['--once', '--config', str(config_path), '--sha256', update._sha(raw)])
        assert exit_code == 0, errors
        empty_no_change = json.loads(capsys.readouterr().out)
        assert empty_no_change['status'] == 'NO_CHANGE' and empty_no_change['pointer_sha256'] == answer_pins[-1]
        assert not (private_root / 'q4-live-update.pending.json').exists()
        event['ts_utc'] = '2026-10-31T12:03:30Z'
        event['message'] = 'synthetic-private-body'
        with bridge.open('ab') as stream:
            stream.write(update._encode(event) + b'\n')
        if fault_stage == 'empty_bad_fence':
            def corrupt_landed(**kwargs):
                report, root = kwargs['report'], kwargs['checkpoint']
                assert report['status'] == 'EMPTY_ADVANCED'
                from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
                checkpoint._fence_record(root, report['pointer_sha256'], 'landed').write_bytes(b'synthetic-bad-fence')
                return prior_complete(**kwargs)
            with monkeypatch.context() as scoped:
                scoped.setattr(prefix_recovery, 'complete_owned_publication', corrupt_landed)
                assert update.main(['--once', '--config', str(config_path), '--sha256', update._sha(raw)]) == 3
            assert json.loads(capsys.readouterr().err)['status'] == 'UNKNOWN'
            assert (private_root / 'q4-live-update.pending.json').is_file()
            return
        assert update.main(['--once', '--config', str(config_path), '--sha256', update._sha(raw)]) == 0
        empty_advanced = json.loads(capsys.readouterr().out)
        assert empty_advanced['status'] == 'PUBLISHED' and empty_advanced['pointer_sha256'] != answer_pins[-1]
        assert not (private_root / 'q4-live-update.pending.json').exists()
        monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(query), encoding='utf-8'))
        assert update.caller.main(['--enabled']) == 0
        assert json.loads(capsys.readouterr().out)['answer']['binding']['pointer_sha256'] == empty_advanced['pointer_sha256']
        answer_pins.append(empty_advanced['pointer_sha256'])
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
    assert len(progress_files) == (5 if fault_stage == 'capture' else 3)
    attempt = json.loads((private_root / 'q4-live-update.pending.json').read_bytes())['attempt']
    progress = json.loads((private_root / ('q4-live-update-progress-' + attempt + '.json')).read_bytes())
    assert progress['schema'] == 'wd.q4-live-update-progress.v1'
    assert progress['config_sha256'] == update._sha(raw)
    assert progress['pointer_sha256'] != answer_pins[-1]
    assert progress['status'] == ('EMPTY_ADVANCED' if fault_stage == 'capture' else 'PUBLISHED_HISTORY')
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
