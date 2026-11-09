"""Staged child with actual C2/query over fabricated cards; no live runtime."""
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path

import pytest

from sandbox.mind.step2 import runtime_launcher, wd_q4_private_read as reader
from tools import wd_q4_public_child as child
from tools import wd_q4_public_query as query

_spec = importlib.util.spec_from_file_location('synthetic_staged_snapshot',
    Path(__file__).with_name('test_wd_q4_real_card_snapshot_e2e.py'))
fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_module)
real_fixture = fixture_module.real_fixture
real_snapshot = fixture_module.real_snapshot
rt = fixture_module.rt


@pytest.fixture
def route(real_snapshot, monkeypatch):
    directory, built, _, snapshot, _, _ = real_snapshot
    descriptor = json.loads((directory / 'descriptor.json').read_bytes())
    config = rt.config(directory, built['descriptor_sha256'])
    pointer = json.loads(snapshot['pointer_bytes'])
    args = dict(root=directory.parent, expected_source_sha256=pointer['source_prefix']['sha256'],
                expected_encoder_sha256=query._encoder_digest(descriptor['encoder']),
                pointer_bytes=snapshot['pointer_bytes'], expected_pointer_sha256=snapshot['expected_pointer_sha256'],
                policy_bytes=snapshot['policy_bytes'], policy_sha256=snapshot['policy_sha256'],
                roster_bytes=snapshot['roster_bytes'], roster_sha256=snapshot['roster_sha256'],
                card_decision_bytes=snapshot['decision_bytes'], card_decision_sha256=snapshot['decision_sha256'],
                card_policy_bytes=snapshot['card_policy_bytes'], source_path=snapshot['source_path'],
                generation_path=snapshot['generation_path'], end_cursor=snapshot['end_cursor'], enabled=True)
    monkeypatch.setattr(reader, 'ENCODER_FACTORY', lambda **kwargs: rt.fx.Encoder())
    monkeypatch.setattr(reader, 'bind_runtime_encoder', lambda *args: None)
    monkeypatch.setattr(query.q4_query, '_utc_now', lambda: dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc))
    calls = []
    def launcher(*, query, **kwargs):
        calls.append(True)
        situation = json.loads(query)
        out = reader._private_read_locked(directory, directory / 'descriptor.json', config.descriptor_sha256,
                situation['situation'], situation['time'], r'C:\synthetic\model', config.runtime_revision)
        return runtime_launcher._validate_output(json.dumps(out).encode(), 0, config.descriptor_sha256)
    monkeypatch.setattr(query.q4_query, '_invoke_launcher', launcher)
    return config, args, calls


class Unreadable:
    def read(self, *args):
        pytest.fail('unverified C2 consumed input')


def run(route, stdin=None):
    config, args, calls = route
    if stdin is None:
        stdin = io.BytesIO(json.dumps({'situation': 'merged green pr 2046 genesis',
                                       'time': '2026-11-02T00:00:00Z'}).encode())
    output = io.StringIO()
    code = child.run_staged_card(config, stdin, output, **args)
    return code, json.loads(output.getvalue()), calls


def test_default_off_never_reads_inputs():
    output = io.StringIO()
    assert child.run_staged_card(object(), Unreadable(), output, root=object()) == 2
    assert json.loads(output.getvalue()) == {'schema': 'wd.q4-card-snapshot-query.v1', 'status': 'REFUSED',
                                             'reason': 'staged_query_refused'}


def test_real_staged_query_c2_to_child_answer(route):
    code, output, calls = run(route)
    assert code == 0 and output['schema'] == 'wd.q4-card-snapshot-query.v1' and output['status'] == 'ANSWER'
    assert calls == [True] and output['binding']['source_mode'] == 'card-snapshot-v1'
    for field in ('publisher_authentication', 'source_filter_attestation', 'freshness', 'same_user_aba', 'completeness'):
        assert output[field] == 'UNKNOWN'
    assert 'receipt_sha256' not in output['binding']


@pytest.mark.parametrize('field', ['expected_pointer_sha256', 'expected_source_sha256', 'expected_encoder_sha256',
                                   'policy_sha256', 'roster_sha256', 'card_decision_sha256'])
def test_wrong_pins_refuse_before_stdin(route, field):
    route[1][field] = '0' * 64
    code, output, calls = run(route, Unreadable())
    assert code == 2 and output['status'] == 'REFUSED' and calls == []


def test_output_claiming_authentication_is_refused(route, monkeypatch):
    original = query.query_staged_card_snapshot
    def altered(*args, **kwargs):
        value = original(*args, **kwargs)
        value['publisher_authentication'] = 'VERIFIED'
        return value
    monkeypatch.setattr(query, 'query_staged_card_snapshot', altered)
    code, output, _ = run(route)
    assert code == 2 and output['status'] == 'REFUSED'


@pytest.mark.parametrize('field', ['card_policy_bytes', 'source_path', 'generation_path', 'end_cursor'])
def test_missing_c2_evidence_refuses_before_input(route, field):
    route[1][field] = None
    code, output, calls = run(route, Unreadable())
    assert code == 2 and output['status'] == 'REFUSED' and calls == []


@pytest.mark.parametrize('fault', ['extra_receipt', 'wrong_memory', 'wrong_source_mode', 'bad_result'])
def test_malformed_staged_output_is_not_emitted(route, monkeypatch, fault):
    original = query.query_staged_card_snapshot
    def altered(*args, **kwargs):
        value = original(*args, **kwargs)
        if fault == 'extra_receipt': value['binding']['receipt_sha256'] = '0' * 64
        if fault == 'wrong_memory': value['binding']['memory_sha256'] = '0' * 64
        if fault == 'wrong_source_mode': value['binding']['source_mode'] = 'public-v1'
        if fault == 'bad_result': value['results'][0]['canonical_event'] = True
        return value
    monkeypatch.setattr(query, 'query_staged_card_snapshot', altered)
    code, output, _ = run(route)
    assert code == 2 and output['status'] == 'REFUSED'


def test_short_write_is_an_error(route):
    class ShortWriter:
        def write(self, value): return len(value) - 1
        def flush(self): pass
    config, args, _ = route
    request = io.BytesIO(json.dumps({'situation': 'merged green pr 2046 genesis',
                                      'time': '2026-11-02T00:00:00Z'}).encode())
    assert child.run_staged_card(config, request, ShortWriter(), **args) == 2
