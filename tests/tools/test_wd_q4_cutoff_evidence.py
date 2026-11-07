"""Synthetic evidence binds a frozen reader bundle to a producer cutoff."""
import hashlib
import json
from pathlib import Path

from tests.tools.test_wd_q4_genesis_bootstrap import synthetic
from tests.tools import test_wd_q4_genesis_to_composer_cli as integrated


def test_default_off_is_inert():
    from tools import wd_q4_cutoff_evidence as evidence
    assert evidence.observe() == {'status': 'OFF'}
    assert evidence.observe(enabled='true') == {'status': 'UNKNOWN'}


def test_real_synthetic_producer_receipt_and_reader_bundle(synthetic, monkeypatch, tmp_path, capsys):
    from tools import wd_q4_cutoff_evidence as evidence
    from tools import wd_q4_genesis_to_composer_cli as composer
    original_preflight = composer._preflight
    original_snapshot = composer.snapshot_prep.prepare_snapshot
    inputs = {}
    observations = []

    def remember(path, pin):
        result = original_preflight(path, pin)
        inputs.update(path=path, pin=pin)
        return result

    def check_snapshot(**kwargs):
        snapshot = original_snapshot(**kwargs)
        raw = snapshot['snapshot_bytes']
        digest = hashlib.sha256(raw).hexdigest()
        args = dict(enabled=True, config_path=inputs['path'], expected_config_sha256=inputs['pin'],
                    bundle_bytes=raw, expected_bundle_sha256=digest)
        result = evidence.observe(**args)
        observations.append(result)
        assert evidence.observe(**{**args, 'expected_bundle_sha256': '0'*64}) == {'status': 'UNKNOWN'}
        assert evidence.observe(**{**args, 'expected_config_sha256': '0'*64}) == {'status': 'UNKNOWN'}
        assert evidence.observe(**{**args, 'bundle_bytes': raw+b' '}) == {'status': 'UNKNOWN'}
        from tools import wd_q4_secure_transport as transport
        parts = transport._fixed_bundle_decode(raw)
        parts['pointer_bytes'] += b' '
        wrong_pointer = transport.fixed_snapshot_bundle(**parts)
        assert evidence.observe(**{**args, 'bundle_bytes': wrong_pointer,
                    'expected_bundle_sha256': hashlib.sha256(wrong_pointer).hexdigest()}) == {'status': 'UNKNOWN'}
        parts = transport._fixed_bundle_decode(raw)
        parts['policy_bytes'] += b' '
        wrong_policy = transport.fixed_snapshot_bundle(**parts)
        assert evidence.observe(**{**args, 'bundle_bytes': wrong_policy,
                    'expected_bundle_sha256': hashlib.sha256(wrong_policy).hexdigest()}) == {'status': 'UNKNOWN'}
        parts = transport._fixed_bundle_decode(raw)
        parts['active_artifact_bytes']['build_receipt.json'] += b' '
        wrong_build = transport.fixed_snapshot_bundle(**parts)
        assert evidence.observe(**{**args, 'bundle_bytes': wrong_build,
                    'expected_bundle_sha256': hashlib.sha256(wrong_build).hexdigest()}) == {'status': 'UNKNOWN'}
        config = json.loads(Path(inputs['path']).read_bytes())
        receipt = Path(config['receipt_path'])
        old = receipt.read_bytes()
        receipt.write_bytes(old+b' ')
        try:
            assert evidence.observe(**args) == {'status': 'UNKNOWN'}
        finally:
            receipt.write_bytes(old)
        doc = json.loads(old)
        private = Path(json.loads(Path(config['genesis_config_path']).read_bytes())['private_state_root'])
        watermark = private / ('q4-cutoff-record-' + doc['watermark_record_sha256'] + '.json')
        original = watermark.read_bytes()
        watermark.write_bytes(original + b' ')
        try:
            assert evidence.observe(**args) == {'status': 'UNKNOWN'}
        finally:
            watermark.write_bytes(original)
        pending = private / 'q4-cutoff.pending.json'
        pending.write_bytes(b'{}')
        try:
            assert evidence.observe(**args) == {'status': 'UNKNOWN'}
        finally:
            pending.unlink()
        lock = private / 'prefix-private-state.lock'
        old_lock = lock.read_bytes()
        lock.unlink()
        try:
            assert evidence.observe(**args) == {'status': 'UNKNOWN'}
            assert not lock.exists()
        finally:
            lock.write_bytes(old_lock)
        return snapshot

    monkeypatch.setattr(composer, '_preflight', remember)
    monkeypatch.setattr(composer.snapshot_prep, 'prepare_snapshot', check_snapshot)
    integrated.test_actual_genesis_receipt_to_five_lane_descriptor(
        synthetic, monkeypatch, tmp_path, capsys, 'continuation_v3')
    assert observations == [{'status': 'CUTOFF_WITHIN_PINNED_PREFIX',
                             'cutoff_utc': '2026-10-31T13:10:00Z',
                             'freshness': 'UNKNOWN', 'source_authentication': 'UNKNOWN',
                             'history_coverage': 'UNKNOWN'}], repr(observations)
