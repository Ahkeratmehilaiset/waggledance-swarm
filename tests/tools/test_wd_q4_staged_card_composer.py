"""Synthetic C2 binding preparation; no live state, model or activation."""

import hashlib
import json
from pathlib import Path

import pytest

from tests.tools.test_wd_q4_staged_lane_query import staged_producer  # noqa: F401 - pytest fixture
from tools import wd_q4_staged_composer as composer


def args(case):
    return {key: case[key] for key in ('expected_commit', 'manifest_bytes', 'expected_manifest_sha256',
        'fleet_bytes', 'expected_fleet_sha256', 'wrapper_bytes', 'expected_wrapper_sha256',
        'public_plan_bytes', 'expected_public_plan_sha256')}


def test_actual_c2_plan_builds_ten_pin_binding_without_legacy_snapshot(staged_producer, monkeypatch):
    def no_legacy(*unused, **ignored):
        pytest.fail('v3 must not decode a v6 fixed snapshot')
    monkeypatch.setattr(composer.transport, '_fixed_bundle_decode', no_legacy)
    result = composer._prepare_staged_card_binding(**args(staged_producer))
    document = json.loads(result['binding_bytes'])
    assert hashlib.sha256(result['binding_bytes']).hexdigest() == result['binding_sha256']
    assert len(document['binding']) == 10 and 'receipt_sha256' not in document['binding']
    assert 'mind-public-knowledge-v1' in result['approved_tasks']
    assert hashlib.sha256(result['bundle_bytes']).hexdigest() == result['bundle_sha256']
    bundle = composer.transport._fixed_card_bundle_decode(result['bundle_bytes'])
    assert bundle['pointer'] == staged_producer['pointer_bytes']
    assert bundle['card_policy'] == staged_producer['card_policy_bytes']
    assert hashlib.sha256(bundle['projection']).hexdigest() == document['binding']['projection_sha256']


def test_changed_memory_between_binding_and_bundle_refuses(staged_producer, monkeypatch):
    original = composer.binding_producer.build_staged_lane_binding
    def changed_after_binding(**kwargs):
        result = original(**kwargs)
        plan = json.loads(kwargs['public_plan_bytes'])
        path = Path(plan['config']['memory']) / 'memory.json'
        path.write_bytes(path.read_bytes() + b' ')
        return result
    monkeypatch.setattr(composer.binding_producer, 'build_staged_lane_binding', changed_after_binding)
    with pytest.raises(composer.ComposeRefused, match='^input_refused$'):
        composer._prepare_staged_card_binding(**args(staged_producer))


@pytest.mark.parametrize('fault', ['plan_pin', 'pointer', 'card_policy', 'source', 'cursor', 'generation'])
def test_changed_or_mismatched_c2_evidence_refuses(staged_producer, fault):
    case = dict(staged_producer)
    plan = json.loads(case['public_plan_bytes'])
    if fault == 'plan_pin':
        case['expected_public_plan_sha256'] = '0' * 64
    elif fault in ('pointer', 'card_policy', 'source', 'generation'):
        path = Path(plan[fault + '_path'])
        old = path.read_bytes()
        if fault == 'source': path.write_bytes(b'!' + old[1:])
        elif fault == 'generation': path.write_bytes(b'{"generation":"different"}')
        else: path.write_bytes(old + b' ')
    else:
        plan['end_cursor']['offset'] += 1
        case['public_plan_bytes'] = json.dumps(plan, sort_keys=True).encode()
        case['expected_public_plan_sha256'] = hashlib.sha256(case['public_plan_bytes']).hexdigest()
    with pytest.raises((ValueError, composer.ComposeRefused)):
        composer._prepare_staged_card_binding(**args(case))
