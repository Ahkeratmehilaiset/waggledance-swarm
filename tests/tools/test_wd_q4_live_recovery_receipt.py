import importlib.util
import json
from pathlib import Path
import pytest
from tools import wd_q4_live_update_recovery as recovery
from tools import wd_q4_live_update_once as update


@pytest.mark.parametrize('receipt_case', ['exact', 'different', 'partial', 'fence_race', 'selector_race'])
def test_recovery_receipt_written_before_interruption_can_be_checked(tmp_path, monkeypatch, capsys, receipt_case):
    """Synthetic Windows E2E: reuse only an exact receipt after fresh terminal checks."""
    source = Path.cwd() / 'tests/tools/test_wd_q4_live_update_once.py'
    spec = importlib.util.spec_from_file_location('recovery_original_test', source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    real_recover = recovery.recover
    state = {}
    def record_recover(**kwargs):
        if (kwargs.get('expected_progress_sha256') not in (None, '0' * 64)
                and kwargs.get('expected_attempt') != '0' * 64):
            state['kwargs'] = kwargs
            state['real_create'] = update.runner._create
            def fault_create(path, data):
                info = state['real_create'](path, data)
                if Path(path).name.startswith('q4-live-update-recovery-result-'):
                    state['receipt'] = Path(path)
                    state['receipt_bytes'] = data
                    raise OSError('synthetic interruption after receipt')
                return info
            monkeypatch.setattr(update.runner, '_create', fault_create)
        return real_recover(**kwargs)
    monkeypatch.setattr(recovery, 'recover', record_recover)
    with pytest.raises(recovery.RecoveryOutcomeUnknown):
        fixture.test_two_cli_updates_are_consumed_by_same_guarded_caller(tmp_path, monkeypatch, capsys, 'publish')
    assert state['receipt'].read_bytes() == state['receipt_bytes']
    assert (state['receipt'].parent / 'q4-live-update.pending.json').exists()
    monkeypatch.setattr(update.runner, '_create', state['real_create'])
    # Desired recovery behavior: accept and validate the exact existing receipt,
    # then finish without publishing again. Current code raises UNKNOWN.
    if receipt_case in ('different', 'partial'):
        state['receipt'].write_bytes(b'{}' if receipt_case == 'different' else state['receipt_bytes'][:10])
        with pytest.raises(recovery.RecoveryOutcomeUnknown):
            real_recover(**state['kwargs'])
        assert (state['receipt'].parent / 'q4-live-update.pending.json').exists()
    elif receipt_case in ('fence_race', 'selector_race'):
        from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
        config = json.loads(Path(state['kwargs']['config_path']).read_bytes())
        route = json.loads(Path(config['route_path']).read_bytes())
        progress_path = state['receipt'].parent / ('q4-live-update-progress-' + state['kwargs']['expected_attempt'] + '.json')
        progress = json.loads(progress_path.read_bytes())
        fence = checkpoint._fence_record(Path(route['checkpoint_root']), progress['pointer_sha256'], 'landed')
        selector = Path(config['baseline_path']).parent.parent / 'q4-live-current.json'
        original_verify = update.runner._verify_own
        altered = []
        def mutate_after_first_receipt_check(path, raw, info):
            original_verify(path, raw, info)
            if Path(path) == state['receipt'] and not altered:
                (fence if receipt_case == 'fence_race' else selector).write_bytes(b'{}')
                altered.append(True)
        monkeypatch.setattr(update.runner, '_verify_own', mutate_after_first_receipt_check)
        with pytest.raises(recovery.RecoveryOutcomeUnknown):
            real_recover(**state['kwargs'])
        assert altered and (state['receipt'].parent / 'q4-live-update.pending.json').exists()
    else:
        result = real_recover(**state['kwargs'])
        assert result['status'] == 'PUBLISHED'
        assert state['receipt'].read_bytes() == state['receipt_bytes']
        assert not (state['receipt'].parent / 'q4-live-update.pending.json').exists()
