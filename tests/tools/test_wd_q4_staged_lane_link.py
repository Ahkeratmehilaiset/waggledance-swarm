"""Synthetic staged-card lane transport; no installed bridge or model."""

import hashlib
import json

import pytest

from tests.tools.test_wd_q4_lane_link import plan, public_plan
from tools import wd_q4_lane_link as link


@pytest.fixture
def staged(public_plan):
    kwargs, document = public_plan
    document.update(schema='wd.q4-public-wrapper-plan.v3', card_decision_path='C:\\decision',
                    card_decision_sha256='a'*64, card_policy_path='C:\\card-policy',
                    card_policy_sha256='b'*64, pointer_path='C:\\pointer',
                    source_path='C:\\source', generation_path='C:\\generation',
                    end_cursor={'offset': 19, 'file_identity': 'synthetic', 'generation': 'g1'})
    raw = json.dumps(document, sort_keys=True).encode()
    kwargs['public_plan_path'].write_bytes(raw)
    kwargs['expected_public_plan_sha256'] = hashlib.sha256(raw).hexdigest()
    binding = {name: document[name] for name in ('pointer_sha256', 'source_anchor_sha256', 'encoder_sha256',
                                                  'policy_sha256', 'roster_sha256', 'card_decision_sha256',
                                                  'card_policy_sha256')}
    binding.update(descriptor_sha256=document['config']['descriptor_sha256'],
                   memory_sha256='7'*64, projection_sha256='8'*64, source_mode='card-snapshot-v1')
    value = dict(schema='wd.q4-card-snapshot-query.v1', status='ANSWER', binding=binding,
                 freshness='UNKNOWN', publisher_authentication='UNKNOWN', source_filter_attestation='UNKNOWN',
                 same_user_aba='UNKNOWN', completeness='UNKNOWN', results=[dict(
                     stored_text='synthetic', ref_sha256='a'*64, doc_id_sha256='b'*64,
                     ref_kind='sha256_of_projected_metadata', canonical_event=False)])
    return kwargs, document, value


def invoke(kwargs, result, code=0):
    return link.invoke_public(enabled=True, lane='codex-lead-1', query_bytes=b'synthetic', **kwargs,
                              transport=lambda **_: (json.dumps(result).encode(), code))


def test_staged_reply_round_trips_without_fabricating_receipt(staged):
    kwargs, _, value = staged
    result = invoke(kwargs, value)
    assert result == value
    assert 'receipt_sha256' not in result['binding']
    assert 'public_policy_sha256' not in result['binding']


def test_staged_abstention_keeps_unknown_and_empty_results(staged):
    kwargs, _, value = staged
    value.update(status='ABSTAIN', results=[])
    assert invoke(kwargs, value) == value


def test_staged_refusal_is_sanitized(staged):
    kwargs, _, _ = staged
    value = {'schema': 'wd.q4-public-query.v2', 'status': 'REFUSED', 'reason': 'synthetic-private'}
    assert invoke(kwargs, value, 2) == {'schema': 'wd.q4-public-query.v2', 'status': 'REFUSED',
                                      'reason': 'runtime_refused'}


@pytest.mark.parametrize('fault', ['missing_cursor', 'extra_cursor', 'boolean_offset', 'negative_offset',
                                   'empty_identity', 'missing_source', 'bad_card_pin'])
def test_bad_staged_plan_never_dispatches(staged, fault):
    kwargs, document, value = staged
    if fault == 'missing_cursor': del document['end_cursor']['generation']
    if fault == 'extra_cursor': document['end_cursor']['extra'] = 'x'
    if fault == 'boolean_offset': document['end_cursor']['offset'] = True
    if fault == 'negative_offset': document['end_cursor']['offset'] = -1
    if fault == 'empty_identity': document['end_cursor']['file_identity'] = ''
    if fault == 'missing_source': document['source_path'] = ''
    if fault == 'bad_card_pin': document['card_policy_sha256'] = 'invalid'
    raw = json.dumps(document, sort_keys=True).encode()
    kwargs['public_plan_path'].write_bytes(raw)
    kwargs['expected_public_plan_sha256'] = hashlib.sha256(raw).hexdigest()
    calls = []
    with pytest.raises(link.LaneLinkRefused, match='binding_refused'):
        link.invoke_public(enabled=True, lane='codex-lead-1', query_bytes=b'synthetic', **kwargs,
                           transport=lambda **kw: calls.append(kw))
    assert calls == []


@pytest.mark.parametrize('field', ['pointer_sha256', 'source_anchor_sha256', 'encoder_sha256', 'policy_sha256',
                                   'roster_sha256', 'card_decision_sha256', 'card_policy_sha256', 'descriptor_sha256'])
def test_staged_reply_rejects_a_different_bound_pin(staged, field):
    kwargs, _, value = staged
    value['binding'][field] = 'f'*64
    with pytest.raises(link.LaneLinkRefused):
        invoke(kwargs, value)


@pytest.mark.parametrize('fault', ['wrong_schema', 'wrong_mode', 'extra_receipt', 'wrong_unknown', 'bad_result',
                                   'answer_empty', 'nonzero_exit'])
def test_staged_reply_rejects_malformed_or_untrusted_fields(staged, fault):
    kwargs, _, value = staged
    if fault == 'wrong_schema': value['schema'] = 'wd.q4-public-query.v2'
    if fault == 'wrong_mode': value['binding']['source_mode'] = 'history'
    if fault == 'extra_receipt': value['binding']['receipt_sha256'] = 'f'*64
    if fault == 'wrong_unknown': value['completeness'] = 'COMPLETE'
    if fault == 'bad_result': value['results'][0]['canonical_event'] = True
    if fault == 'answer_empty': value['results'] = []
    with pytest.raises(link.LaneLinkRefused):
        invoke(kwargs, value, 2 if fault == 'nonzero_exit' else 0)
