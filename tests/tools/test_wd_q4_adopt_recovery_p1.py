"""Synthetic P1 reconciliation never changes an unbound pending marker."""
import json

import pytest

from tests.tools.test_wd_q4_continuation_adopt import actual_continuation, synthetic
from tests.tools.test_wd_q4_continuation_adopt_recovery import paths


def test_v2_marker_requires_same_explicit_p1_on_recovery(actual_continuation, monkeypatch):
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    args = actual_continuation
    private, _ = paths(args)
    marker = private / 'q4-continuation-adopt.pending.json'
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update, '_publish', lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(adopt.AdoptInterrupted):
            adopt.run_once(**args, p1_enabled=True)
    before = marker.read_bytes()
    assert json.loads(before)['p1_enabled'] is True
    with pytest.raises(recovery.RecoveryUnknown):
        recovery.recover_once(**args)
    assert marker.read_bytes() == before
    assert recovery.recover_once(**args, p1_enabled=True) == {
        'status': 'NOT_ADOPTED', 'schema': 'wd.q4-continuation-adopt-recovery-result.v2',
        'p1_enabled': True, 'receipt_sha256': args['receipt_sha256']}
    assert not marker.exists()


def test_v1_marker_cannot_be_recovered_as_p1(actual_continuation, monkeypatch):
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    args = actual_continuation
    private, _ = paths(args)
    marker = private / 'q4-continuation-adopt.pending.json'
    monkeypatch.setattr(adopt.update, '_publish', lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(adopt.AdoptInterrupted):
        adopt.run_once(**args)
    before = marker.read_bytes()
    assert json.loads(before)['schema'] == 'wd.q4-continuation-adopt-pending.v1'
    with pytest.raises(recovery.RecoveryUnknown):
        recovery.recover_once(**args, p1_enabled=True)
    assert marker.read_bytes() == before
    assert recovery.recover_once(**args) == {'status': 'NOT_ADOPTED'}


@pytest.mark.parametrize('flag', [False, 1, 'true', None])
def test_invalid_or_incompatible_flag_never_removes_v2_marker(actual_continuation, monkeypatch, flag):
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    args = actual_continuation
    private, _ = paths(args)
    marker = private / 'q4-continuation-adopt.pending.json'
    monkeypatch.setattr(adopt.update, '_publish', lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(adopt.AdoptInterrupted):
        adopt.run_once(**args, p1_enabled=True)
    before = marker.read_bytes()
    with pytest.raises(recovery.RecoveryUnknown):
        recovery.recover_once(**args, p1_enabled=flag)
    assert marker.read_bytes() == before


def test_changed_v2_marker_is_unknown_and_preserved(actual_continuation, monkeypatch):
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    from tools import wd_q4_live_update_once as update
    args = actual_continuation
    private, _ = paths(args)
    marker = private / 'q4-continuation-adopt.pending.json'
    monkeypatch.setattr(adopt.update, '_publish', lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(adopt.AdoptInterrupted):
        adopt.run_once(**args, p1_enabled=True)
    changed = json.loads(marker.read_bytes())
    changed['p1_enabled'] = False
    marker.write_bytes(update._encode(changed))
    before = marker.read_bytes()
    with pytest.raises(recovery.RecoveryUnknown):
        recovery.recover_once(**args, p1_enabled=True)
    assert marker.read_bytes() == before


def test_p1_recovery_rechecks_selector_on_both_lock_passes(actual_continuation, monkeypatch):
    # Use the existing synthetic status-v1 implementation as a strict spy only;
    # the actual P1 producer/selector integration is tested separately.
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    args = actual_continuation
    private, _ = paths(args)
    marker = private / 'q4-continuation-adopt.pending.json'
    original_publish, original_select = adopt.update._publish, adopt.live.select
    flags = []

    def publish(*pos, **kw):
        assert kw.pop('p1_enabled') is True
        original_publish(*pos, **kw)
        raise KeyboardInterrupt()

    def select(*pos, **kw):
        flags.append(kw.pop('p1_enabled', None))
        return original_select(*pos, **kw)

    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update, '_publish', publish)
        with pytest.raises(adopt.AdoptInterrupted):
            adopt.run_once(**args, p1_enabled=True)
    monkeypatch.setattr(adopt.live, 'select', select)
    result = recovery.recover_once(**args, p1_enabled=True)
    assert flags == [True, True]
    assert result == {'status': 'ADOPTED', 'schema': 'wd.q4-continuation-adopt-recovery-result.v2',
                      'p1_enabled': True, 'receipt_sha256': args['receipt_sha256']}
    assert not marker.exists()
