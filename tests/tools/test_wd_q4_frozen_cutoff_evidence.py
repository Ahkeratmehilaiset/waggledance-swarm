"""Frozen, independently pinned synthetic continuation and reader evidence."""
import hashlib
import json
from pathlib import Path

from tests.tools.test_wd_q4_genesis_bootstrap import synthetic
from tests.tools import test_wd_q4_genesis_to_composer_cli as integrated


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def test_off_does_not_touch_inputs():
    from tools import wd_q4_frozen_cutoff_evidence as evidence
    assert evidence.observe() == {'status': 'OFF'}
    assert evidence.observe(enabled='true') == {'status': 'UNKNOWN'}


def test_frozen_real_synthetic_chain(synthetic, monkeypatch, tmp_path, capsys):
    from tools import wd_q4_frozen_cutoff_evidence as evidence
    from tools import wd_q4_genesis_to_composer_cli as composer
    from tools import wd_q4_live_update_recovery as recovery
    from tools import wd_q4_secure_transport as transport
    original_preflight = composer._preflight
    original_snapshot = composer.snapshot_prep.prepare_snapshot
    inputs, observations = {}, []

    def remember(path, pin):
        result = original_preflight(path, pin)
        inputs.update(path=path, pin=pin, prepared=result)
        return result

    def check_snapshot(**kwargs):
        snapshot = original_snapshot(**kwargs)
        cfg, gc, pointer_pin, policy, roster, roster_pin, public, v3 = inputs['prepared']
        receipt, _, private, _, _, _ = v3
        receipt_raw = Path(cfg['receipt_path']).read_bytes()
        watermark_raw = (private / ('q4-cutoff-record-' + receipt['watermark_record_sha256'] + '.json')).read_bytes()
        route_raw = (private / ('q4-continuation-route-' + receipt['route_sha256'] + '.json')).read_bytes()
        name, _ = recovery._find_runner_receipt(private, receipt['runner_receipt_sha256'])
        runner_raw = (private / name).read_bytes()
        bundle = snapshot['snapshot_bytes']
        args = dict(enabled=True, bundle_bytes=bundle, expected_bundle_sha256=sha(bundle),
            expected_pointer_sha256=pointer_pin, policy_sha256=gc['policy_sha256'],
            roster_sha256=roster_pin, public_policy_sha256=gc['public_policy_sha256'],
            receipt_bytes=receipt_raw, expected_receipt_sha256=sha(receipt_raw),
            watermark_bytes=watermark_raw, expected_watermark_sha256=sha(watermark_raw),
            runner_receipt_bytes=runner_raw, route_bytes=route_raw)
        # After capture the verifier must use only these immutable bytes.
        with monkeypatch.context() as no_io:
            import builtins
            import os
            def forbidden(*_a, **_k):
                raise AssertionError('filesystem access in frozen verifier')
            no_io.setattr(builtins, 'open', forbidden)
            no_io.setattr(os, 'open', forbidden)
            result = evidence.observe(**args)
        observations.append(result)
        if result.get('status') != 'CUTOFF_WITHIN_PINNED_PREFIX':
            return snapshot
        assert result == {'status':'CUTOFF_WITHIN_PINNED_PREFIX', 'cutoff_utc':'2026-10-31T13:10:00Z',
                          'freshness':'UNKNOWN', 'source_authentication':'UNKNOWN', 'history_coverage':'UNKNOWN'}
        for name in ('expected_bundle_sha256', 'expected_pointer_sha256', 'policy_sha256',
                     'roster_sha256', 'public_policy_sha256', 'expected_receipt_sha256',
                     'expected_watermark_sha256'):
            assert evidence.observe(**{**args, name:'0'*64}) == {'status':'UNKNOWN'}
        for name in ('bundle_bytes','receipt_bytes','watermark_bytes','runner_receipt_bytes','route_bytes'):
            assert evidence.observe(**{**args, name:args[name]+b' '}) == {'status':'UNKNOWN'}
        changed = json.loads(watermark_raw)
        changed['pointer_sha256'] = '0'*64
        altered = json.dumps(changed, sort_keys=True, separators=(',',':')).encode()
        assert evidence.observe(**{**args,'watermark_bytes':altered,'expected_watermark_sha256':sha(altered)}) == {'status':'UNKNOWN'}
        changed_receipt = json.loads(receipt_raw)
        changed_receipt['status'] = 'partial_ready_prefix'
        partial = json.dumps(changed_receipt, sort_keys=True, separators=(',',':')).encode()
        assert evidence.observe(**{**args,'receipt_bytes':partial,'expected_receipt_sha256':sha(partial)}) == {'status':'UNKNOWN'}
        changed_receipt = json.loads(receipt_raw)
        changed_mark = json.loads(watermark_raw)
        changed_receipt['cutoff_utc'] = changed_mark['cutoff_utc'] = '2026-10-31T13:11:00Z'
        altered_mark = json.dumps(changed_mark, sort_keys=True, separators=(',',':')).encode()
        changed_receipt['watermark_record_sha256'] = sha(altered_mark)
        altered_receipt = json.dumps(changed_receipt, sort_keys=True, separators=(',',':')).encode()
        assert evidence.observe(**{**args,'receipt_bytes':altered_receipt,'expected_receipt_sha256':sha(altered_receipt),
                    'watermark_bytes':altered_mark,'expected_watermark_sha256':sha(altered_mark)}) == {'status':'UNKNOWN'}
        parts = transport._fixed_bundle_decode(bundle)
        parts['pointer_bytes'] += b' '
        bad_bundle = transport.fixed_snapshot_bundle(**parts)
        assert evidence.observe(**{**args,'bundle_bytes':bad_bundle,'expected_bundle_sha256':sha(bad_bundle)}) == {'status':'UNKNOWN'}
        return snapshot

    monkeypatch.setattr(composer, '_preflight', remember)
    monkeypatch.setattr(composer.snapshot_prep, 'prepare_snapshot', check_snapshot)
    integrated.test_actual_genesis_receipt_to_five_lane_descriptor(
        synthetic, monkeypatch, tmp_path, capsys, 'continuation_v3')
    assert observations == [{'status':'CUTOFF_WITHIN_PINNED_PREFIX', 'cutoff_utc':'2026-10-31T13:10:00Z',
                             'freshness':'UNKNOWN', 'source_authentication':'UNKNOWN', 'history_coverage':'UNKNOWN'}], repr(observations)


def test_real_empty_advance_reports_only_previous_build_cutoff(synthetic, monkeypatch):
    from tests.tools import test_wd_q4_production_continuation as integration
    from tools import wd_q4_frozen_cutoff_evidence as evidence
    from tools import wd_q4_genesis_to_composer_cli as composer
    from tools import wd_q4_genesis_snapshot_prepare as snapshot_prep
    from tools import wd_q4_live_update_recovery as recovery
    from tools import wd_q4_production_continuation as producer
    from tools import wd_q4_roster_from_fleet as roster_producer
    original = producer.continue_once
    observed = []

    def collect(**kwargs):
        result = original(**kwargs)
        args, _ = synthetic
        private = Path(args['private_state_root'])
        roster, roster_sha = roster_producer.build_roster_snapshot(args['fleet_bytes'],args['expected_fleet_sha256'],enabled=True)
        snapshot = snapshot_prep.prepare_snapshot(enabled=True,checkpoint_root=args['checkpoint_root'],
            expected_pointer_sha256=result['pointer_sha256'],policy_bytes=args['policy_bytes'],
            expected_policy_sha256=args['policy_sha256'],roster_bytes=roster,expected_roster_sha256=roster_sha,
            public_policy_bytes=args['public_policy_bytes'],expected_public_policy_sha256=args['public_policy_sha256'])
        receipt = {key:result[key] for key in composer._RECEIPT_V3}
        receipt_raw = json.dumps(receipt,sort_keys=True,separators=(',',':')).encode()
        mark = (private / ('q4-cutoff-record-' + result['watermark_record_sha256'] + '.json')).read_bytes()
        route = (private / ('q4-continuation-route-' + result['route_sha256'] + '.json')).read_bytes()
        runner_name, _ = recovery._find_runner_receipt(private,result['runner_receipt_sha256'])
        rr = (private / runner_name).read_bytes()
        bundle = snapshot['snapshot_bytes']
        observed.append(evidence.observe(enabled=True,bundle_bytes=bundle,expected_bundle_sha256=sha(bundle),
            expected_pointer_sha256=result['pointer_sha256'],policy_sha256=args['policy_sha256'],
            roster_sha256=roster_sha,public_policy_sha256=args['public_policy_sha256'],
            receipt_bytes=receipt_raw,expected_receipt_sha256=sha(receipt_raw),watermark_bytes=mark,
            expected_watermark_sha256=sha(mark),runner_receipt_bytes=rr,route_bytes=route))
        # A forged caught-up claim cannot turn an EMPTY_ADVANCED partial
        # runner receipt into a newer cutoff proof.
        claimed = json.dumps({**receipt,'status':'caught_up_to_cutoff'},sort_keys=True,separators=(',',':')).encode()
        assert evidence.observe(enabled=True,bundle_bytes=bundle,expected_bundle_sha256=sha(bundle),
            expected_pointer_sha256=result['pointer_sha256'],policy_sha256=args['policy_sha256'],
            roster_sha256=roster_sha,public_policy_sha256=args['public_policy_sha256'],
            receipt_bytes=claimed,expected_receipt_sha256=sha(claimed),watermark_bytes=mark,
            expected_watermark_sha256=sha(mark),runner_receipt_bytes=rr,route_bytes=route) == {'status':'UNKNOWN'}
        return result

    monkeypatch.setattr(producer, 'continue_once', collect)
    integration.test_real_empty_advance_binds_its_own_anchor(synthetic,monkeypatch,'none')
    assert observed == [{'status':'ADVANCED_WITHOUT_CUTOFF','build_cutoff_utc':'2026-10-31T13:00:00Z',
                         'freshness':'UNKNOWN','source_authentication':'UNKNOWN','history_coverage':'UNKNOWN'}], repr(observed)
