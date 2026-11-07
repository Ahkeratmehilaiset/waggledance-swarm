"""Staged card pins must survive every lane caller boundary."""
import json
from pathlib import Path

import pytest

import test_wd_q4_lane_binding as lb
import test_wd_q4_lane_caller_adapter as ad
from test_wd_q4_lane_caller_adapter import chain, data  # noqa: F401
from tools import wd_q4_lane_binding as binding
from tools import wd_q4_lane_caller_cli as cli
from tools import wd_q4_lane_link as link


def pins():
    return {**lb.pins(), 'card_decision_sha256': '9a' * 32, 'card_policy_sha256': '8b' * 32}


def test_staged_binding_document_and_cli_accept_exact_ten_pins():
    document = lb.binding_document(binding=pins())
    assert binding._shape(document, frozenset(document['lanes']))
    assert cli._binding(pins()) == pins()


def test_staged_binding_checks_query_contract_and_resolves_exact_pins(monkeypatch):
    monkeypatch.setattr(binding.lane_query, 'STAGED_BINDING_KEYS', frozenset(pins()), raising=False)
    document = lb.binding_document(binding=pins())
    assert lb.resolve(document=document)['expected_binding'] == pins()
    monkeypatch.setattr(binding.lane_query, 'STAGED_BINDING_KEYS', frozenset())
    lb.refused('contract_refused', document=document)


def test_staged_binding_rejects_orphan_policy_and_wrong_pin():
    bad = pins()
    del bad['card_decision_sha256']
    document = lb.binding_document(binding=bad)
    assert not binding._shape(document, frozenset(document['lanes']))
    bad = pins()
    bad['card_policy_sha256'] = 'BAD'
    with pytest.raises(Exception):
        cli._binding(bad)


def test_staged_current_snapshot_reads_the_plan_pointer_not_legacy_latest(monkeypatch, tmp_path):
    expected = pins()
    pointer = tmp_path / 'snapshot' / 'staged-pointer.json'
    root = tmp_path / 'public'
    plan = {'schema': 'wd.q4-public-wrapper-plan.v3', 'root': str(root), 'pointer_path': str(pointer)}
    plan_bytes = json.dumps(plan).encode()
    binding_doc = {'schema': 'wd.q4-lane-binding.v1', 'binding': expected, 'public_plan_sha256': 'a' * 64}
    monkeypatch.setattr(link, '_public_plan', lambda *_: ('plan', plan_bytes, {
        key: value for key, value in expected.items() if key != 'projection_sha256'}))
    monkeypatch.setattr(cli, '_path', lambda value: Path(value))
    calls = []
    monkeypatch.setattr(cli, '_read', lambda path, limit, pin: calls.append((path, limit, pin)))
    cli._require_current_snapshot({'public_plan': 'plan'}, expected,
                                  {'binding_bytes': json.dumps(binding_doc).encode()})
    assert calls == [(str(pointer), 1048576, expected['pointer_sha256'])]


def test_adapter_accepts_exact_staged_descriptor_and_answer(data, chain):
    calls, state = chain
    values = {key: '2' * 64 for key in ad.BINDING_KEYS}
    values.update(card_decision_sha256='9a' * 32, card_policy_sha256='8b' * 32)
    descriptor = ad.descriptor_for(data['inputs'], expected_binding=values)
    identity = ad.identity_for(descriptor)
    data['kw'].update(identity_bytes=identity, expected_identity_sha256=ad.sha(identity),
                      descriptor_bytes=descriptor, expected_descriptor_sha256=ad.sha(descriptor))
    data['descriptor'] = descriptor
    state['outcome'] = ad.answer_for(ad.LANE, descriptor)
    result = ad.call(data)
    assert result['status'] == 'VALIDATED' and result['answer']['binding'] == values
    assert len(calls) == 1
