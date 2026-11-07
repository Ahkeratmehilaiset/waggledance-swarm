"""Synthetic-only tests for the opt-in producer wrapper."""
import pytest

from tools import wd_q4_live_update_once as update


def test_off_does_not_inspect_configuration():
    assert update.run_once(config_path=object(), expected_config_sha256=object()) == {'status': 'OFF'}


def test_legacy_writer_and_v2_enrollment_cannot_overlap(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from tools import wd_q4_lifecycle_v2 as lifecycle
    root = tmp_path / 'generations'
    (root / ('b' * 40)).mkdir(parents=True)
    private = tmp_path / (root.name + '.q4-lifecycle-private-v2')
    private.mkdir()
    baseline = root / ('b' * 40) / 'shared-session-config.v2.json'
    raw = update._encode({'schema': 'wd.q4-lifecycle-binding.v2', 'generation_root': str(root),
                          'private_root': str(private)})
    def attempt():
        with pytest.raises(lifecycle.Refused):
            with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=update._sha(raw),
                                 attempt='a' * 64, previous_selector_sha256=None):
                pass
    with update._legacy_lifecycle_guard(str(baseline)):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(attempt).result()
        assert not (root / 'q4-lifecycle-v2.enrolled.json').exists()
    with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=update._sha(raw),
                         attempt='a' * 64, previous_selector_sha256=None):
        assert (root / 'q4-lifecycle-v2.enrolled.json').exists()


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
@pytest.mark.parametrize('fault_stage', ['capture', 'materialize', 'publish', 'after_selector', 'interrupt', 'empty_bad_fence', 'before_progress', 'before_progress_no_change', 'before_runner_receipt'])
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
    real_enc = e2e.enc
    synthetic_job = b'synthetic pinned Job helper, never executed'
    def encode_with_synthetic_job(value):
        if isinstance(value, dict) and value.get('schema_version') == 1 and 'files' in value:
            (tmp_path / 'installed-bundle' / 'WdQ4ProcessJob.ps1').write_bytes(synthetic_job)
            value = {**value, 'files': {**value['files'], 'WdQ4ProcessJob.ps1': update._sha(synthetic_job).upper()}}
        return real_enc(value)
    monkeypatch.setattr(e2e, 'enc', encode_with_synthetic_job)
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
    e2e.test_two_real_fenced_publications_are_selected_by_running_caller(
        tmp_path, monkeypatch, capsys, 'none', False, 'codex-lead-1')
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
    private_root = Path(route['private_state_root'])
    if fault_stage == 'capture':
        from tools import wd_q4_live_update_recovery as recovery
        enrolled = Path(config['baseline_path']).parent.parent / 'q4-lifecycle-v2.enrolled.json'
        enrolled.write_bytes(b'synthetic enrollment')
        try:
            assert update.main(['--once', '--config', str(config_path), '--sha256', update._sha(raw)]) == 2
            assert json.loads(capsys.readouterr().err)['status'] == 'REFUSED'
            assert not (private_root / 'q4-live-update.pending.json').exists()
            with pytest.raises(recovery.RecoveryRefused):
                recovery.recover(enabled=True, config_path=str(config_path), expected_config_sha256=update._sha(raw),
                                 expected_attempt='a' * 64, expected_progress_sha256='b' * 64)
            assert not (private_root / 'q4-live-update.pending.json').exists()
        finally:
            enrolled.unlink()
    job_marker = private_root / 'q4-adopt-recovery-job.pending.json'
    job_marker.write_bytes(b'synthetic recovery job pending')
    before = (Path(route['checkpoint_root']) / 'q4-latest.json').read_bytes()
    try:
        assert update.main(['--once', '--config', str(config_path), '--sha256', update._sha(raw)]) == 3
        assert json.loads(capsys.readouterr().err)['status'] == 'UNKNOWN'
        assert job_marker.read_bytes() == b'synthetic recovery job pending'
        assert not (private_root / 'q4-live-update.pending.json').exists()
        assert (Path(route['checkpoint_root']) / 'q4-latest.json').read_bytes() == before
    finally:
        job_marker.unlink()
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
    if fault_stage == 'capture':
        private_root = Path(route['private_state_root'])
        marker = private_root / 'q4-continuation-adopt.pending.json'
        marker.write_bytes(b'unknown adoption outcome\n')
        before = (Path(route['checkpoint_root']) / 'q4-latest.json').read_bytes()
        try:
            assert update.main(['--once', '--config', str(config_path), '--sha256', update._sha(raw)]) == 3
            assert json.loads(capsys.readouterr().err)['status'] == 'UNKNOWN'
            assert (Path(route['checkpoint_root']) / 'q4-latest.json').read_bytes() == before
            assert marker.read_bytes() == b'unknown adoption outcome\n'
        finally:
            marker.unlink()
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
    if fault_stage != 'before_progress_no_change':
        with bridge.open('ab') as stream:
            stream.write(update._encode(event) + b'\n')
    calls = []
    reports = []
    def counted_run(**kwargs):
        calls.append(1)
        result = state['raw_run'](**kwargs)
        reports.append(result)
        return result
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
        elif fault_stage in ('before_progress', 'before_progress_no_change'):
            create = state['runner']._create
            def fail_progress(path, data):
                if Path(path).name.startswith('q4-live-update-progress-'):
                    raise OSError('synthetic interruption before progress')
                return create(path, data)
            scoped.setattr(state['runner'], '_create', fail_progress)
        elif fault_stage == 'before_runner_receipt':
            from tools import wd_q4_status_prefix_recovery as prefix_recovery
            scoped.setattr(prefix_recovery, '_write_receipt', fail)
        else:
            scoped.setattr(update.live, 'publish', fail_after_selector if fault_stage == 'after_selector' else fail)
        assert update.main(['--once', '--config', str(config_path), '--sha256', update._sha(raw)]) == (130 if fault_stage == 'interrupt' else 3)
    assert calls == [1]
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)['status'] == 'UNKNOWN'
    assert (private_root / 'q4-live-update.pending.json').is_file()
    progress_files = list(private_root.glob('q4-live-update-progress-*.json'))
    if fault_stage == 'before_runner_receipt':
        from tools import wd_q4_live_update_recovery as recovery
        from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
        marker_path = private_root / 'q4-live-update.pending.json'
        marker_raw = marker_path.read_bytes()
        marker = json.loads(marker_raw)
        attempt = marker['attempt']
        reservation_path = private_root / ('q4-live-runner-reservation-' + attempt + '.json')
        reservation_raw = reservation_path.read_bytes()
        target = update._sha((Path(route['checkpoint_root']) / checkpoint.POINTER).read_bytes())
        assert target != marker['previous_pointer_sha256'] and not reports
        job_receipt = update._encode({'schema': 'wd.q4-live-job-result.v1', 'attempt': attempt,
            'config_sha256': update._sha(raw), 'intent_sha256': marker['intent_sha256'],
            'marker_sha256': update._sha(marker_raw), 'reservation_sha256': update._sha(reservation_raw),
            'job_helper_sha256': update._sha(synthetic_job), 'cleanup': 'confirmed', 'child_outcome': 'completed', 'exit_code': 3})
        job_path = private_root / ('q4-live-job-result-' + attempt + '.json')
        kwargs = {'enabled': True, 'config_path': str(config_path), 'expected_config_sha256': update._sha(raw),
                  'expected_attempt': attempt, 'expected_pointer_sha256': target,
                  'expected_job_receipt_sha256': update._sha(job_receipt)}
        with pytest.raises(recovery.RecoveryOutcomeUnknown):
            recovery.recover(**kwargs)  # Missing host receipt never authorizes cleanup.
        assert marker_path.read_bytes() == marker_raw
        job_path.write_bytes(job_receipt)  # Synthetic Job-host observation; no actual host or live bridge.
        with pytest.raises(recovery.RecoveryOutcomeUnknown):
            recovery.recover(**{**kwargs, 'expected_job_receipt_sha256': '0' * 64})
        runner_mark = Path(route['checkpoint_root']) / state['runner']._PENDING
        original_mark = runner_mark.read_bytes()
        runner_mark.unlink()  # Synthetic fault: no completed reconciliation receipt exists yet.
        with pytest.raises(recovery.RecoveryOutcomeUnknown):
            recovery.recover(**kwargs)
        assert marker_path.read_bytes() == marker_raw
        runner_mark.write_bytes(original_mark)
        runner_mark.write_bytes(b'synthetic-foreign-reservation')
        with pytest.raises(recovery.RecoveryOutcomeUnknown):
            recovery.recover(**kwargs)
        assert marker_path.read_bytes() == marker_raw
        runner_mark.write_bytes(original_mark)
        landed = checkpoint._fence_record(Path(route['checkpoint_root']), target, 'landed')
        original_landed = landed.read_bytes()
        landed.write_bytes(b'synthetic-bad-fence')
        with pytest.raises(recovery.RecoveryOutcomeUnknown):
            recovery.recover(**kwargs)
        assert marker_path.read_bytes() == marker_raw
        landed.write_bytes(original_landed)
        open_fence = Path(route['checkpoint_root']) / checkpoint.FENCE
        open_fence.write_bytes(checkpoint._fence_bytes(marker['previous_pointer_sha256'], target))
        with pytest.raises(recovery.RecoveryOutcomeUnknown):
            recovery.recover(**kwargs)
        assert marker_path.read_bytes() == marker_raw
        open_fence.unlink()  # Only the synthetic fault introduced by this test.
        publish = update._publish
        def publish_then_interrupt(*args, **kwargs):
            publish(*args, **kwargs)
            raise OSError('synthetic interruption after selector')
        with monkeypatch.context() as scoped:
            scoped.setattr(update, '_publish', publish_then_interrupt)
            with pytest.raises(recovery.RecoveryOutcomeUnknown):
                recovery.recover(**kwargs)
        assert marker_path.read_bytes() == marker_raw
        verify_own = state['runner']._verify_own
        def receipt_then_interrupt(path, data, info):
            verify_own(path, data, info)
            if Path(path).name.startswith('q4-live-job-reconciled-'):
                raise OSError('synthetic interruption after reconciliation receipt')
        with monkeypatch.context() as scoped:
            scoped.setattr(state['runner'], '_verify_own', receipt_then_interrupt)
            with pytest.raises(recovery.RecoveryOutcomeUnknown):
                recovery.recover(**kwargs)
        assert marker_path.read_bytes() == marker_raw
        unlink = recovery.os.unlink
        for removed in (runner_mark, private_root / state['runner']._PENDING):
            def unlink_then_interrupt(path, *, fault=removed):
                unlink(path)
                if Path(path) == fault:
                    raise OSError('synthetic interruption immediately after owned marker removal')
            with monkeypatch.context() as scoped:
                scoped.setattr(recovery.os, 'unlink', unlink_then_interrupt)
                scoped.setattr(update, '_publish', lambda *_args, **_kwargs: pytest.fail('retry republished'))
                with pytest.raises(recovery.RecoveryOutcomeUnknown):
                    recovery.recover(**kwargs)
            assert not removed.exists() and marker_path.read_bytes() == marker_raw
            if removed == runner_mark:
                reconciled = private_root / ('q4-live-job-reconciled-' + attempt + '.json')
                original_reconciled = reconciled.read_bytes()
                reconciled.write_bytes(b'synthetic-modified-reconciliation')
                with pytest.raises(recovery.RecoveryOutcomeUnknown):
                    recovery.recover(**kwargs)
                assert marker_path.read_bytes() == marker_raw
                reconciled.write_bytes(original_reconciled)
        with monkeypatch.context() as scoped:
            scoped.setattr(update, '_publish', lambda *_args, **_kwargs: pytest.fail('retry republished'))
            result = recovery.recover(**kwargs)
        assert result['status'] == 'PUBLISHED' and result['pointer_sha256'] == target
        assert not marker_path.exists()
        for root in (Path(route['checkpoint_root']), private_root):
            assert not (root / state['runner']._PENDING).exists()
        monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(query), encoding='utf-8'))
        assert update.caller.main(['--enabled']) == 0
        answer = json.loads(capsys.readouterr().out)
        assert answer['answer']['binding']['pointer_sha256'] == target
        return
    if fault_stage in ('before_progress', 'before_progress_no_change'):
        from tools import wd_q4_live_update_recovery as recovery
        expected_status = 'EMPTY_NO_CHANGE' if fault_stage == 'before_progress_no_change' else 'PUBLISHED_HISTORY'
        assert len(progress_files) == 2 and reports[0]['status'] == expected_status
        attempt = json.loads((private_root / 'q4-live-update.pending.json').read_bytes())['attempt']
        kwargs = {'enabled': True, 'config_path': str(config_path), 'expected_config_sha256': update._sha(raw),
                  'expected_attempt': attempt, 'expected_runner_receipt_sha256': reports[0]['runner_receipt_sha256'],
                  'expected_pointer_sha256': reports[0]['pointer_sha256']}
        if fault_stage == 'before_progress':
            foreign = private_root / 'q4-continuation-adopt.pending.json'
            foreign.write_bytes(b'unknown adoption outcome\n')
            own = private_root / 'q4-live-update.pending.json'
            own_before = own.read_bytes()
            try:
                with pytest.raises(recovery.RecoveryOutcomeUnknown):
                    recovery.recover(**kwargs)
                assert foreign.read_bytes() == b'unknown adoption outcome\n'
                assert own.read_bytes() == own_before
            finally:
                foreign.unlink()  # Remove only this test's own synthetic marker.
        with pytest.raises(recovery.RecoveryRefused):
            recovery.recover(**{**kwargs, 'expected_runner_receipt_sha256': '0' * 64})
        partial_progress = private_root / ('q4-live-update-progress-' + attempt + '.json')
        partial_progress.write_bytes(b'synthetic-partial-progress')
        with pytest.raises(recovery.RecoveryRefused):
            recovery.recover(**kwargs)
        assert partial_progress.read_bytes() == b'synthetic-partial-progress'
        partial_progress.unlink()  # This test removes only its own synthetic fault file.
        if fault_stage == 'before_progress':
            from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
            landed = checkpoint._fence_record(Path(route['checkpoint_root']), reports[0]['pointer_sha256'], 'landed')
            backup = landed.with_name(landed.name + '.synthetic-backup')
            try:
                landed.rename(backup)
                with pytest.raises(recovery.RecoveryRefused):
                    recovery.recover(**kwargs)
            finally:
                backup.rename(landed)
            selector = Path(config['baseline_path']).parent.parent / 'q4-live-current.json'
            original_selector = selector.read_bytes()
            try:
                selector.write_bytes(b'{}')
                with pytest.raises(recovery.RecoveryRefused):
                    recovery.recover(**kwargs)
            finally:
                selector.write_bytes(original_selector)
        result = recovery.recover(**kwargs)
        assert result['status'] == ('NO_CHANGE' if fault_stage == 'before_progress_no_change' else 'PUBLISHED')
        assert result['pointer_sha256'] == reports[0]['pointer_sha256']
        assert not (private_root / 'q4-live-update.pending.json').exists() and calls == [1]
        monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(query), encoding='utf-8'))
        assert update.caller.main(['--enabled']) == 0
        assert json.loads(capsys.readouterr().out)['answer']['binding']['pointer_sha256'] == result['pointer_sha256']
        return
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
        generations = Path(config['baseline_path']).parent.parent
        before = {p.name for p in generations.iterdir()}
        with monkeypatch.context() as scoped:
            scoped.setattr(update.live, 'publish', lambda **_kw: (_ for _ in ()).throw(ValueError('partial recovery')))
            with pytest.raises(recovery.RecoveryOutcomeUnknown):
                recovery.recover(**kwargs)
        after = {p.name for p in generations.iterdir()}
        partial_names = after - before
        assert len(partial_names) == 1 and (private_root / 'q4-live-update.pending.json').exists()
        def recovery_after_selector(**kw):
            real_publish(**kw)
            raise ValueError('synthetic recovery interruption after selector')
        with monkeypatch.context() as scoped:
            scoped.setattr(update.live, 'publish', recovery_after_selector)
            with pytest.raises(recovery.RecoveryOutcomeUnknown):
                recovery.recover(**kwargs)
        assert (private_root / 'q4-live-update.pending.json').exists()
    result = recovery.recover(**kwargs)
    assert result['status'] == 'PUBLISHED' and result['pointer_sha256'] == progress['pointer_sha256']
    assert (private_root / result['receipt_file']).is_file()
    assert not (private_root / 'q4-live-update.pending.json').exists()
    if fault_stage in ('publish', 'after_selector'):
        assert partial_generation.is_dir()
    if fault_stage == 'capture':
        assert all((generations / name).is_dir() for name in partial_names)
    monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(query), encoding='utf-8'))
    assert update.caller.main(['--enabled']) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered['answer']['binding']['pointer_sha256'] == result['pointer_sha256']


def _p1_test_config():
    return dict(schema='wd.q4-live-update-once.v2', route_path='route', route_sha256='a'*64,
        plan_path='plan', anchor_path='anchor', policy_path='policy', public_policy_path='public',
        baseline_path='baseline', baseline_sha256='b'*64, manifest_sha256='c'*64, p1_enabled=True)


@pytest.mark.parametrize('legacy', [False,True])
def test_p1_configuration_round_trip_keeps_explicit_policy(legacy):
    config=_p1_test_config()
    if legacy:
        config.pop('p1_enabled');config['schema']='wd.q4-live-update-once.v1'
    checked=update._configuration(update._encode(config))
    assert checked==config
    assert update._selector_options(checked)==({} if legacy else {'p1_enabled':True})


@pytest.mark.parametrize('change', [
    {'p1_enabled':False},{'p1_enabled':1},{'p1_enabled':'true'},{'p1_enabled':None},
    {'schema':'wd.q4-live-update-once.v1'},{'schema':'wd.q4-live-update-once.v3'},
    {'unexpected':'value'},{'baseline_sha256':'BAD'},
])
def test_p1_configuration_refuses_unrecognized_or_downgraded_policy(change):
    with pytest.raises(ValueError):
        update._configuration(update._encode({**_p1_test_config(),**change}))


@pytest.mark.parametrize('route_schema', ['wd.q4-status-prefix-route.v1', 'wd.q4-genesis-private-tuple.v1', 'unknown'])
def test_p1_status_route_refuses_before_legacy_baseline(tmp_path, monkeypatch, route_schema):
    route = tmp_path / 'route.json'; route.write_bytes(update._encode({'schema': route_schema}))
    config = _p1_test_config()
    config.update(route_path=str(route), route_sha256=update._sha(route.read_bytes()))
    path = tmp_path / 'config.json'; path.write_bytes(update._encode(config))
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', config['manifest_sha256'])
    seen = []
    def baseline(*_):
        seen.append('baseline')
        raise ValueError()
    monkeypatch.setattr(update.caller, '_configuration', baseline)
    monkeypatch.setattr(update.runner, 'run_once', lambda **_: pytest.fail('legacy writer reached'))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(update.UpdateRefused):
        update.run_once(enabled=True, config_path=str(path), expected_config_sha256=update._sha(path.read_bytes()))
    assert seen == []
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
