"""C1 card decision read-path forwarding (lead-mind-card-decision-forward-fable-20261107-v1). Synthetic fixtures only.

The public pointer resolver, the public query and the public child forward an already pinned card route decision
(bytes and sha256) into the existing card-aware resolution (``wd_q4_agent_query.query_and_resolve`` and the shared
result resolver). Default OFF: the two parameters default to ``None`` and without them every call, binding and output
is exactly the v6 one. The resolver verifies the decision against the policy and roster pins it runs with and refuses
a decision for a generation whose projection is not the decision's card projection; the query and the child bind the
decision pin into the output binding and refuse an output that does not carry exactly that pin.

Not provided or proven here (do not read this slice as more): the checkpoint still validates only v6 public histories
(C2), so the positive end-to-end test substitutes ``read_verified_pointer`` for a synthetic card generation and says
so; there is no card producer (C3); no session config or lane carries a decision (C4); who issued the decision is not
authenticated; freshness, completeness and heads stay UNKNOWN; nothing is enabled or installed.
"""
import copy
import hashlib
import importlib.util
import inspect
import io
import json
import os
import re
from pathlib import Path

import pytest

from tools import wd_q4_agent_query as q4
from tools import wd_q4_public_child as child
from tools import wd_q4_public_pointer_resolver as pointer_resolver
from tools import wd_q4_public_query as public_query

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('card_route_fixture_c1', ROOT / 'tests/tools/test_shared_card_route.py')
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)
fx = rt.fx
HEX40 = re.compile(r'head:[0-9a-f]{40}')
CARD_KEYS = {'card_decision_bytes', 'card_decision_sha256'}
V6_QUERY_KWARGS = {'policy_bytes', 'policy_sha256', 'roster_bytes', 'roster_sha256', 'expected_memory_sha256'}
PUBLIC_POLICY = json.dumps({'schema': 'wd.mind-public-status-policy.v1', 'public_tasks': [rt.TASK],
                            'public_types': ['status']}, sort_keys=True, separators=(',', ':')).encode()
POINTER = b'{"synthetic":"card-pointer"}'
SITUATION = 'merged green pr genesis head test result needs review launcher cache'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_the_decision_parameters_are_keyword_only_and_default_off_everywhere():
    for function in (pointer_resolver.resolve_public_pointer, public_query.query_public, child.run_public):
        parameters = inspect.signature(function).parameters
        for name in CARD_KEYS:
            assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert parameters[name].default is None
        assert parameters['enabled'].default is False
    from sandbox.mind.step2 import shared_card_projection as scp
    assert child.MAX_DECISION == scp.MAX_DECISION


def card_public(tmp_path, monkeypatch, *, heads=None):
    """A synthetic card generation under a public root. The checkpoint view is SUBSTITUTED (C2 is missing: the real
    read_verified_pointer validates v6 public histories only); everything after it is the real chain in process."""
    route = rt.make_route(tmp_path, monkeypatch, heads=heads)
    if os.name != 'nt':
        # The real checkpoint path below (_secure, also used by the resolver) supports fixed Windows volumes only and
        # refuses every path elsewhere; only tests that take this real path are skipped there. The guard is unchanged
        # and the resolver tests on the simulated seam stay portable.
        pytest.skip('shared_index_checkpoint._secure supports fixed Windows volumes only')
    memory_dir = pointer_resolver.checkpoint._secure(route['dir'])
    root = memory_dir.parent
    (root / pointer_resolver.checkpoint.POINTER).write_bytes(POINTER)
    descriptor_bytes = (memory_dir / 'descriptor.json').read_bytes()
    data = route['data']
    assert sha((memory_dir / 'memory.json').read_bytes()) == data['memory_sha256']
    view = {'pointer_sha256': sha(POINTER), 'generation': memory_dir.name,
            'artifacts': {'descriptor_sha256': sha(descriptor_bytes), 'memory_sha256': data['memory_sha256'],
                          'projection_sha256': data['projection_sha256'], 'receipt_sha256': '7' * 64},
            'anchor_sha256': '8' * 64, 'cursor_end_sha256': '9' * 64, 'watermark_sha256': 'a' * 64}
    seen = {'views': [], 'resolve': [], 'query': []}

    def substitute_view(root_arg, tasks, **kwargs):
        seen['views'].append(kwargs)
        return copy.deepcopy(view)

    real_resolve, real_query = pointer_resolver.resolve_public_pointer, q4.query_and_resolve

    def resolve_spy(*args, **kwargs):
        seen['resolve'].append(dict(kwargs))
        return real_resolve(*args, **kwargs)

    def query_spy(*args, **kwargs):
        seen['query'].append(dict(kwargs))
        return real_query(*args, **kwargs)

    monkeypatch.setattr(pointer_resolver.checkpoint, 'read_verified_pointer', substitute_view)
    monkeypatch.setattr(pointer_resolver, 'resolve_public_pointer', resolve_spy)
    monkeypatch.setattr(q4, 'query_and_resolve', query_spy)
    from sandbox.mind.step2.shared_index_preflight import _encoder_digest
    args = {'root': str(root), 'expected_pointer_sha256': sha(POINTER), 'expected_source_sha256': '8' * 64,
            'expected_encoder_sha256': _encoder_digest(json.loads(descriptor_bytes)['encoder']),
            'policy_bytes': data['policy_bytes'], 'policy_sha256': data['policy_sha256'],
            'roster_bytes': data['roster_bytes'], 'roster_sha256': data['roster_sha256'],
            'public_policy_bytes': PUBLIC_POLICY, 'public_policy_sha256': sha(PUBLIC_POLICY)}
    decision = {'card_decision_bytes': data['card_decision_bytes'],
                'card_decision_sha256': data['card_decision_sha256']}
    return {'route': route, 'config': rt.config(memory_dir, view['artifacts']['descriptor_sha256']), 'args': args,
            'decision': decision, 'seen': seen, 'root': root}


def run(state, **extra):
    output = io.StringIO()
    query = json.dumps({'situation': SITUATION, 'time': '2026-11-02T00:00:00Z'}).encode()
    code = child.run_public(state['config'], io.BytesIO(query), output, enabled=True, **state['args'], **extra)
    return code, json.loads(output.getvalue())


@pytest.fixture
def cards(tmp_path, monkeypatch):
    return card_public(tmp_path, monkeypatch)


def test_the_public_child_answers_a_synthetic_card_generation_through_the_forwarded_decision(cards):
    code, out = run(cards, **cards['decision'])
    assert code == 0 and out['schema'] == 'wd.q4-public-query.v2' and out['status'] == 'ANSWER'
    assert cards['route']['calls'] == [True]
    assert out['binding']['card_decision_sha256'] == cards['decision']['card_decision_sha256']
    assert out['binding']['memory_sha256'] == cards['route']['data']['memory_sha256']
    assert {out[key] for key in ('publisher_authentication', 'source_filter_attestation', 'freshness',
                                 'same_user_aba')} == {'UNKNOWN'}
    lines = {record['stored_text'] for record in cards['route']['projection']['records']}
    texts = [item['stored_text'] for item in out['results']]
    rule = fx.transport_regex()
    assert texts and all(rule.fullmatch(text) for text in texts)
    assert any(' head:unknown ' in text for text in texts) and not any(HEX40.search(text) for text in texts)
    assert all(text in lines or ' head:unknown ' in text for text in texts)
    assert [view['expected_mode'] for view in cards['seen']['views']] == ['public-v1', 'public-v1']


def test_the_decision_reaches_each_hop_exactly(cards):
    run(cards, **cards['decision'])
    resolve_kwargs, = cards['seen']['resolve']
    query_kwargs, = cards['seen']['query']
    for kwargs in (resolve_kwargs, query_kwargs):
        assert {key: kwargs[key] for key in CARD_KEYS} == cards['decision']
    assert set(query_kwargs) == V6_QUERY_KWARGS | CARD_KEYS


def test_without_a_decision_every_hop_is_the_v6_call_and_the_card_generation_is_refused(cards):
    code, out = run(cards)
    assert code == 2 and out == {'schema': 'wd.q4-public-query.v2', 'status': 'REFUSED',
                                 'reason': 'public_query_refused'}
    assert cards['route']['calls'] == []
    resolve_kwargs, = cards['seen']['resolve']
    assert not CARD_KEYS & set(resolve_kwargs)
    assert [set(kwargs) for kwargs in cards['seen']['query']] == [V6_QUERY_KWARGS]


def tampered(state):
    raw = state['decision']['card_decision_bytes']
    value = json.loads(raw)
    value['roster_sha256'] = '0' * 64
    other = rt.canonical_decision(value)
    return {'card_decision_bytes': other, 'card_decision_sha256': sha(other)}


@pytest.mark.parametrize('change', ['bytes only', 'pin only', 'other pin', 'text bytes', 'upper pin',
                                    'other roster', 'oversized'])
def test_a_missing_mismatched_or_unverifiable_decision_is_refused_before_the_backend(cards, change):
    raw, pin = cards['decision']['card_decision_bytes'], cards['decision']['card_decision_sha256']
    extra = {
        'bytes only': {'card_decision_bytes': raw},
        'pin only': {'card_decision_sha256': pin},
        'other pin': {'card_decision_bytes': raw, 'card_decision_sha256': sha(raw + b' ')},
        'text bytes': {'card_decision_bytes': raw.decode('ascii'), 'card_decision_sha256': pin},
        'upper pin': {'card_decision_bytes': raw, 'card_decision_sha256': pin.upper()},
        'other roster': tampered(cards),
        'oversized': {'card_decision_bytes': raw + b' ' * (256 * 1024),
                      'card_decision_sha256': sha(raw + b' ' * (256 * 1024))},
    }[change]
    code, out = run(cards, **extra)
    assert code == 2 and out['status'] == 'REFUSED'
    assert cards['route']['calls'] == [] and cards['seen']['query'] == []


def test_the_query_refuses_a_resolver_binding_without_the_forwarded_pin(cards, monkeypatch):
    spy = pointer_resolver.resolve_public_pointer

    def drop_pin(*args, **kwargs):
        binding = spy(*args, **kwargs)
        binding.pop('card_decision_sha256', None)
        return binding

    monkeypatch.setattr(pointer_resolver, 'resolve_public_pointer', drop_pin)
    code, _ = run(cards, **cards['decision'])
    assert code == 2 and cards['route']['calls'] == []


def test_the_query_refuses_a_resolver_binding_with_a_pin_it_did_not_send(cards, monkeypatch):
    spy = pointer_resolver.resolve_public_pointer

    def add_pin(*args, **kwargs):
        binding = spy(*args, **kwargs)
        binding['card_decision_sha256'] = '6' * 64
        return binding

    monkeypatch.setattr(pointer_resolver, 'resolve_public_pointer', add_pin)
    for extra in ({}, cards['decision']):
        code, _ = run(cards, **extra)
        assert code == 2
    assert cards['route']['calls'] == []


@pytest.mark.parametrize('change', ['dropped', 'other', 'added without decision'])
def test_the_child_refuses_an_output_whose_binding_does_not_carry_exactly_the_sent_pin(cards, monkeypatch, change):
    code, good = run(cards, **cards['decision'])
    assert code == 0
    bad = copy.deepcopy(good)
    extra = cards['decision']
    if change == 'dropped':
        del bad['binding']['card_decision_sha256']
    elif change == 'other':
        bad['binding']['card_decision_sha256'] = '6' * 64
    else:
        extra = {}
    monkeypatch.setattr(public_query, 'query_public', lambda *a, **k: copy.deepcopy(bad))
    code, out = run(cards, **extra)
    assert code == 2 and out['status'] == 'REFUSED'


# --- the resolver alone ----------------------------------------------------------------------------------------------

@pytest.fixture
def simulated(tmp_path, monkeypatch):
    """The v6-shaped resolver seam of test_wd_q4_public_pointer_resolver.py with the card fixture pins."""
    strict = rt.pins()
    projection = json.dumps({'records': [{'meta': {'agent': 'codex-lead-1', 'task_id': rt.TASK,
                                                   'type': 'status'}}]}).encode()
    values = {'q4-latest.json': POINTER, 'descriptor.json': b'synthetic-descriptor', 'projection.json': projection}
    view = {'pointer_sha256': sha(POINTER), 'generation': 'generation-1',
            'artifacts': {'descriptor_sha256': sha(values['descriptor.json']), 'memory_sha256': 'a' * 64,
                          'projection_sha256': sha(projection), 'receipt_sha256': 'b' * 64},
            'anchor_sha256': 'c' * 64, 'cursor_end_sha256': 'd' * 64, 'watermark_sha256': 'e' * 64}
    monkeypatch.setattr(pointer_resolver.adapter, 'strict_pins', lambda *a: (frozenset({rt.TASK}), {}))
    monkeypatch.setattr(pointer_resolver.adapter, 'public_status_policy',
                        lambda *a: (frozenset({rt.TASK}), frozenset({'status'})))
    monkeypatch.setattr(pointer_resolver.checkpoint, '_secure', lambda path: path)
    monkeypatch.setattr(pointer_resolver.checkpoint, '_read', lambda path, *, max_bytes: values[path.name])
    monkeypatch.setattr(pointer_resolver.checkpoint, 'read_verified_pointer', lambda root, tasks, **kw: view)
    raw, pin, _ = rt.anchor(tmp_path / 'prefix', fx.corpus())
    args = {'root': tmp_path, 'expected_pointer_sha256': sha(POINTER), 'policy_bytes': strict['policy_bytes'],
            'policy_sha256': strict['policy_sha256'], 'roster_bytes': strict['roster_bytes'],
            'roster_sha256': strict['roster_sha256'], 'public_policy_bytes': b'public',
            'public_policy_sha256': '3' * 64, 'enabled': True}
    return {'args': args, 'raw': raw, 'pin': pin, 'values': values}


def test_the_resolver_binding_is_unchanged_without_a_decision(simulated):
    binding = pointer_resolver.resolve_public_pointer(**simulated['args'])
    assert 'card_decision_sha256' not in binding and binding['source_mode'] == 'public-v1'


def test_the_resolver_refuses_a_decision_for_a_generation_that_is_not_the_card_projection(simulated):
    with pytest.raises(pointer_resolver.PublicPointerRefused, match='^resolution_refused$'):
        pointer_resolver.resolve_public_pointer(**simulated['args'], card_decision_bytes=simulated['raw'],
                                                card_decision_sha256=simulated['pin'])


def test_the_resolver_binds_a_verified_decision_to_a_card_projection(simulated):
    from sandbox.mind.step2 import shared_card_projection as scp
    value = json.loads(simulated['values']['projection.json'])
    simulated['values']['projection.json'] = json.dumps(dict(value, schema=scp.SCHEMA)).encode()
    # the substituted view must pin the changed projection bytes
    view = pointer_resolver.checkpoint.read_verified_pointer(None, None)
    view['artifacts']['projection_sha256'] = sha(simulated['values']['projection.json'])
    binding = pointer_resolver.resolve_public_pointer(**simulated['args'], card_decision_bytes=simulated['raw'],
                                                      card_decision_sha256=simulated['pin'])
    assert binding['card_decision_sha256'] == simulated['pin']
    with pytest.raises(pointer_resolver.PublicPointerRefused):
        pointer_resolver.resolve_public_pointer(**dict(simulated['args'], roster_sha256='0' * 64),
                                                card_decision_bytes=simulated['raw'],
                                                card_decision_sha256=simulated['pin'])


# --- each hop checks the pair itself (no reliance on the next hop) ---------------------------------------------------

def half_pairs(state):
    raw, pin = state['decision']['card_decision_bytes'], state['decision']['card_decision_sha256']
    return [{'card_decision_bytes': raw}, {'card_decision_sha256': pin},
            {'card_decision_bytes': raw.decode('ascii'), 'card_decision_sha256': pin},
            {'card_decision_bytes': raw, 'card_decision_sha256': pin.upper()}]


def test_the_child_refuses_a_half_or_malformed_pair_before_the_public_query(cards, monkeypatch):
    called = []
    monkeypatch.setattr(public_query, 'query_public', lambda *a, **k: called.append(True))
    for extra in half_pairs(cards):
        code, out = run(cards, **extra)
        assert code == 2 and out['status'] == 'REFUSED'
    assert called == []


def test_the_query_refuses_a_half_or_malformed_pair_before_the_resolver(cards):
    for extra in half_pairs(cards):
        with pytest.raises(public_query.PublicQueryRefused, match='^public_query_refused$'):
            public_query.query_public(cards['config'], io.BytesIO(b'{}'), enabled=True, **cards['args'], **extra)
    assert cards['seen']['resolve'] == [] and cards['seen']['query'] == []


@pytest.mark.parametrize('half', ['bytes', 'pin'])
def test_the_resolver_refuses_half_a_decision(simulated, half):
    extra = ({'card_decision_bytes': simulated['raw']} if half == 'bytes'
             else {'card_decision_sha256': simulated['pin']})
    with pytest.raises(pointer_resolver.PublicPointerRefused, match='^resolution_refused$'):
        pointer_resolver.resolve_public_pointer(**simulated['args'], **extra)
