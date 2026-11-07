from pathlib import Path
import pytest
from tests.tools import test_wd_q4_genesis_bootstrap as base
from tests.tools.test_wd_q4_genesis_bootstrap import synthetic
from tools import wd_q4_genesis_bootstrap as genesis
from tools.wd_q4_roster_from_fleet import build_roster_snapshot


def test_genesis_snapshot_uses_pinned_policy_and_roster(synthetic, monkeypatch):
    from tools import wd_q4_genesis_snapshot_prepare as prepare
    original = genesis.bootstrap
    observed = []
    def probe(**kw):
        result = original(**kw)
        roster, roster_sha = build_roster_snapshot(kw['fleet_bytes'], kw['expected_fleet_sha256'], enabled=True)
        inputs = dict(enabled=True, checkpoint_root=kw['checkpoint_root'], expected_pointer_sha256=result['pointer_sha256'],
            policy_bytes=kw['policy_bytes'], expected_policy_sha256=kw['policy_sha256'],
            roster_bytes=roster, expected_roster_sha256=roster_sha,
            public_policy_bytes=kw['public_policy_bytes'], expected_public_policy_sha256=kw['public_policy_sha256'])
        wrong = {**inputs, 'expected_policy_sha256': '0' * 64}
        with pytest.raises(prepare.PrepareRefused):
            prepare.prepare_snapshot(**wrong)
        with pytest.raises(prepare.PrepareRefused):
            prepare.prepare_snapshot(**{**inputs, 'expected_pointer_sha256': '0' * 64})
        assert not list(Path(kw['checkpoint_root']).glob('q4-snapshot-input-*'))
        prepared = prepare.prepare_snapshot(**inputs)
        assert prepared['status'] == 'PREPARED'
        assert prepared['pointer_sha256'] == result['pointer_sha256']
        assert prepare._sha(prepared['snapshot_bytes']) == prepared['snapshot_sha256']
        again = prepare.prepare_snapshot(**inputs)
        assert again['snapshot_sha256'] == prepared['snapshot_sha256']
        policy_copy = Path(prepared['policy_paths'][0])
        saved = policy_copy.read_bytes()
        policy_copy.write_bytes(b'synthetic tamper')
        with pytest.raises(prepare.PrepareRefused):
            prepare.prepare_snapshot(**inputs)
        assert policy_copy.read_bytes() == b'synthetic tamper'
        policy_copy.write_bytes(saved)
        observed.append(True)
        return result
    monkeypatch.setattr(genesis, 'bootstrap', probe)
    base.test_actual_producer_checkpoint_and_partial_continuation(synthetic, monkeypatch)
    assert observed == [True]


def test_default_off_does_not_read_inputs():
    from tools import wd_q4_genesis_snapshot_prepare as prepare
    assert prepare.prepare_snapshot() == {'status': 'OFF'}


def test_interrupted_capture_reports_unknown_after_attempted_write(synthetic, monkeypatch):
    from tools import wd_q4_genesis_snapshot_prepare as prepare
    original = genesis.bootstrap
    observed = []
    def probe(**kw):
        result = original(**kw)
        roster, roster_sha = build_roster_snapshot(kw['fleet_bytes'], kw['expected_fleet_sha256'], enabled=True)
        monkeypatch.setattr(prepare.update, '_snapshot', lambda *a, **k: (_ for _ in ()).throw(OSError()))
        with pytest.raises(prepare.PrepareUnknown):
            prepare.prepare_snapshot(enabled=True, checkpoint_root=kw['checkpoint_root'],
                expected_pointer_sha256=result['pointer_sha256'], policy_bytes=kw['policy_bytes'],
                expected_policy_sha256=kw['policy_sha256'], roster_bytes=roster, expected_roster_sha256=roster_sha,
                public_policy_bytes=kw['public_policy_bytes'], expected_public_policy_sha256=kw['public_policy_sha256'])
        assert list(Path(kw['checkpoint_root']).glob('q4-snapshot-input-*'))
        observed.append(True)
        return result
    monkeypatch.setattr(genesis, 'bootstrap', probe)
    base.test_actual_producer_checkpoint_and_partial_continuation(synthetic, monkeypatch)
    assert observed == [True]
