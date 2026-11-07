"""Synthetic card wiring through the existing shared reader (lead-mind-card-route-wire-fable-20261107-v1).

Synthetic data only: cards carry the ``synthetic`` tag, the policy and roster are synthetic pinned bytes, and nothing
reads an installed bridge, policy, model or runtime. The chain is the real one where it can run in process: the shared
private reader (``wd_q4_private_read._private_read_locked`` with a synthetic encoder), the launcher output check
(``runtime_launcher._validate_output``), the resolved child (``wd_q4_resolved_child.run_resolved``) and the resolver;
only the launcher process call is replaced by the private reader's own output. v6 behaviour is unchanged and the card
route is OFF unless ``shared_result_resolver.CARD_ROUTE`` holds the card module itself.
"""
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

from sandbox.mind.step2 import shared_card_projection as scp
from sandbox.mind.step2 import shared_result_resolver as resolver
from tools import wd_q4_agent_query as q4
from tools import wd_q4_resolved_child as child
from tools.bridge_compact_view import event_id

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('card_projection_fixture', ROOT / 'tests/tools/test_shared_card_projection.py')
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)

ROSTER = {lane: fx.UUIDS[lane] for lane in fx.LANES}
TASK = 'mind-public-knowledge-v1'


def policy_bytes(tasks):
    return fx.raw({'schema': 'wd.q4-non-review-policy.v1', 'approved_tasks': list(tasks)})


FLEET = fx.raw({'lanes': [{'agent': n, 'agent_uuid': u} for n, u in ROSTER.items() if n != fx.TOOLS],
                'tools_supervisor': {'agent': fx.TOOLS, 'agent_uuid': ROSTER[fx.TOOLS]}})


def pins(tasks=(TASK,)):
    policy = policy_bytes(tasks)
    return {'policy_bytes': policy, 'policy_sha256': fx.sha(policy), 'roster_bytes': FLEET,
            'roster_sha256': fx.sha(FLEET)}


def config(memory_dir, descriptor_sha256):
    return q4.VerifiedQ4Config(interpreter=r'C:\fake\python.exe', interpreter_sha256='1' * 64,
        bootstrap=r'C:\fake\runtime_bootstrap.py', bootstrap_sha256='2' * 64, verifier_sha256='3' * 64,
        runtime_root=r'C:\fake\runtime', runtime_manifest=r'C:\fake\runtime.json', runtime_manifest_sha256='4' * 64,
        runtime_generation='5' * 40, runtime_revision=fx.IDENTITY['revision'], memory=str(memory_dir),
        descriptor=str(memory_dir / 'descriptor.json'), descriptor_sha256=descriptor_sha256,
        lock_file=r'C:\fake\lock', lock_timeout='2', timeout=30, scratch_root=r'C:\fake-scratch')


@pytest.fixture
def route(tmp_path, monkeypatch):
    pytest.importorskip('faiss')
    from sandbox.mind.step2 import runtime_launcher
    from sandbox.mind.step2 import wd_q4_private_read as reader
    events = fx.corpus()
    projection = fx.project(events)
    root = tmp_path / 'out'
    root.mkdir()
    built = scp.build_shared_route_card_memory(projection, fx.encode, fx.IDENTITY, fx.DIM, root, 'cards',
                                               enabled=True, **pins())
    memory_dir = root / 'cards'
    descriptor_sha256 = built['descriptor_sha256']
    monkeypatch.setattr(reader, 'ENCODER_FACTORY', lambda **kw: fx.Encoder())
    monkeypatch.setattr(reader, 'bind_runtime_encoder', lambda descriptor, model_dir: None)
    calls = []

    def launcher(*, query, **kwargs):
        """The private reader's real output through the real launcher output check (no process)."""
        situation = json.loads(query)
        calls.append(True)
        out = reader._private_read_locked(memory_dir, memory_dir / 'descriptor.json', descriptor_sha256,
                                          situation['situation'], situation['time'], r'C:\fake\model',
                                          fx.IDENTITY['revision'])
        raw = json.dumps(out).encode('utf-8')
        return runtime_launcher._validate_output(raw, 0, descriptor_sha256)

    monkeypatch.setattr(q4, '_invoke_launcher', launcher)
    monkeypatch.setattr(q4, '_utc_now', lambda: dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc))
    data = dict(pins(), memory_bytes=(memory_dir / 'memory.json').read_bytes(),
                memory_sha256=built['memory_sha256'], projection_bytes=(memory_dir / 'projection.json').read_bytes(),
                projection_sha256=fx.sha((memory_dir / 'projection.json').read_bytes()))
    return {'config': config(memory_dir, descriptor_sha256), 'data': data, 'events': events, 'calls': calls,
            'projection': projection, 'built': built, 'dir': memory_dir}


def run(route, situation='blocked red mind genesis needs operator disk', when='2026-11-02T00:00:00Z', data=None):
    output = io.StringIO()
    query = json.dumps({'situation': situation, 'time': when}).encode()
    code = child.run_resolved(route['config'], io.BytesIO(query), output, **(data or route['data']))
    return code, json.loads(output.getvalue())


def test_card_route_is_off_by_default_and_the_chain_refuses(route):
    assert resolver.CARD_ROUTE is None and resolver.card_route() is None
    code, out = run(route)
    assert code == 2 and out == {'schema': 'wd.q4-resolved-child.v1', 'status': 'REFUSED',
                                 'reason': 'resolved_refused'}
    assert route['calls'] == []


def test_shared_reader_answers_synthetic_cards_end_to_end(route, monkeypatch):
    monkeypatch.setattr(resolver, 'CARD_ROUTE', scp)
    code, out = run(route)
    assert code == 0 and out['schema'] == 'wd.q4-resolved-child.v1' and out['status'] == 'ANSWER'
    assert route['calls'] == [True]
    lines = {r['stored_text'] for r in route['projection']['records']}
    rule = fx.transport_regex()
    for item in out['results']:
        assert set(item) == {'canonical_event', 'doc_id_sha256', 'ref_kind', 'ref_sha256', 'stored_text'}
        assert item['stored_text'] in lines and rule.fullmatch(item['stored_text'])
        assert scp.card_status(item['stored_text'], '2026-11-02T00:00:00Z') == 'CURRENT'
    assert any(i['stored_text'].startswith('blocked red codex-tools-1 component:mind.genesis') for i in out['results'])
    assert 'ZQ-PRIVATE-CANARY' not in json.dumps(out)


def test_status_after_until_is_stale_and_before_any_card_abstains(route, monkeypatch):
    monkeypatch.setattr(resolver, 'CARD_ROUTE', scp)
    monkeypatch.setattr(q4, '_utc_now', lambda: dt.datetime(2026, 11, 6, tzinfo=dt.timezone.utc))
    code, out = run(route, when='2026-11-05T00:00:00Z')
    assert code == 0 and out['status'] == 'ANSWER'
    assert {scp.card_status(i['stored_text'], '2026-11-05T00:00:00Z') for i in out['results']} == {'STALE'}
    code, out = run(route, situation='mind genesis', when='2026-10-01T00:00:00Z')
    assert code == 0 and out == {'schema': 'wd.q4-resolved-child.v1', 'status': 'ABSTAIN', 'results': []}


@pytest.mark.parametrize('change', ['policy_without_card_task', 'other_policy_pin', 'tampered_projection',
                                    'tampered_manifest_policy'])
def test_the_card_route_fails_closed_on_any_pin_or_binding_change(route, monkeypatch, change):
    monkeypatch.setattr(resolver, 'CARD_ROUTE', scp)
    data = dict(route['data'])
    if change == 'policy_without_card_task':
        data.update(pins(('synthetic-task',)))
    elif change == 'other_policy_pin':
        data.update(pins((TASK, 'synthetic-task')))
    elif change == 'tampered_projection':
        raw = data['projection_bytes'].replace(b'"green"', b'"red"', 1)
        data.update(projection_bytes=raw, projection_sha256=fx.sha(raw))
    else:
        manifest = json.loads(data['memory_bytes'])
        manifest['policy']['policy_sha256'] = '0' * 64
        raw = json.dumps(manifest).encode()
        data.update(memory_bytes=raw, memory_sha256=fx.sha(raw))
    code, out = run(route, data=data)
    assert code == 2 and out['status'] == 'REFUSED'
    assert route['calls'] == []


def test_the_build_needs_a_pinned_policy_that_approves_the_card_task(tmp_path):
    pytest.importorskip('faiss')
    projection = fx.project(fx.corpus())
    root = tmp_path / 'out'
    root.mkdir()
    with pytest.raises(scp.SharedCardRefused, match='policy_not_card'):
        scp.build_shared_route_card_memory(projection, fx.encode, fx.IDENTITY, fx.DIM, root, 'cards', enabled=True,
                                           **pins(('synthetic-task',)))
    with pytest.raises(scp.SharedCardRefused, match='policy_invalid'):
        scp.build_shared_route_card_memory(projection, fx.encode, fx.IDENTITY, fx.DIM, root, 'cards', enabled=True,
                                           **dict(pins(), policy_sha256='0' * 64))
    assert list(root.iterdir()) == []
    assert scp.build_shared_route_card_memory(projection, None, None, None, root, 'x') == {'status': 'OFF'}


def test_shared_route_memory_binds_the_shared_descriptor_and_the_v7_projection(route):
    from sandbox.mind.step2 import shared_index_builder as sib
    manifest = json.loads(route['data']['memory_bytes'])
    descriptor = json.loads((route['dir'] / 'descriptor.json').read_bytes())
    assert manifest['scope'] == descriptor['scope'] == sib.SCOPE and descriptor['schema'] == sib.DESCRIPTOR_SCHEMA
    assert manifest['projection_schema'] == scp.SCHEMA != resolver.PROJECTION_SCHEMA
    assert manifest['policy'] == {'authorship': 'declared_roster_consistent', 'review_signal_excluded': True,
                                  'policy_sha256': route['data']['policy_sha256'],
                                  'roster_sha256': route['data']['roster_sha256']}


def test_a_v6_manifest_label_on_a_card_projection_is_refused(route, monkeypatch):
    monkeypatch.setattr(resolver, 'CARD_ROUTE', scp)
    data = dict(route['data'])
    manifest = json.loads(data['memory_bytes'])
    manifest['projection_schema'] = resolver.PROJECTION_SCHEMA
    raw = json.dumps(manifest).encode()
    data.update(memory_bytes=raw, memory_sha256=fx.sha(raw))
    code, out = run(route, data=data)
    assert code == 2 and out['status'] == 'REFUSED'


def test_the_card_resolver_path_needs_strict_pins_and_visibility(route, monkeypatch):
    monkeypatch.setattr(resolver, 'CARD_ROUTE', scp)
    record = route['projection']['records'][0]
    item = {'ref_sha256': fx.sha(record['provenance']['event_ref'].encode()),
            'doc_id_sha256': fx.sha(record['record_id'].encode()), 'ref_kind': 'sha256_of_projected_metadata',
            'canonical_event': False}
    data = route['data']
    common = dict(memory_manifest=data['memory_bytes'], expected_memory_sha256=data['memory_sha256'],
                  projection_bytes=data['projection_bytes'], results=[item])
    strict = dict(policy_bytes=data['policy_bytes'], policy_sha256=data['policy_sha256'],
                  roster_bytes=data['roster_bytes'], roster_sha256=data['roster_sha256'])
    out = resolver.resolve_results(query_utc='2026-11-02T00:00:00Z', **common, **strict)
    assert out == [dict(item, stored_text=record['stored_text'])]
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(query_utc='2026-11-02T00:00:00Z', approved_tasks=frozenset({TASK}), **common)
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(query_utc='2026-11-01T10:00:30Z', **common, **strict)


def test_the_resolved_child_refuses_a_card_by_a_review_lane(route, monkeypatch):
    monkeypatch.setattr(resolver, 'CARD_ROUTE', scp)
    projection = json.loads(route['data']['projection_bytes'])
    monkeypatch.setattr(scp, 'validate_shared_card_projection', lambda value: value)  # isolate the child check
    projection['records'][0]['meta']['agent'] = 'claude-rco-1'
    raw = json.dumps(projection, sort_keys=True, indent=1).encode('utf-8')
    with pytest.raises(ValueError):
        child._validate_projection(raw, frozenset({TASK}), ROSTER)


def test_v6_records_still_go_through_the_unchanged_v6_path(monkeypatch):
    spec = importlib.util.spec_from_file_location('resolved_child_fixture', ROOT / 'tests/tools/test_wd_q4_resolved_child.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data, result, record = module.fixture()
    for flag in (None, scp):
        monkeypatch.setattr(resolver, 'CARD_ROUTE', flag)
        monkeypatch.setattr(q4, '_utc_now', lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
        monkeypatch.setattr(q4, '_invoke_launcher', lambda **_: result)
        code, output = module.run(data)
        decoded = json.loads(output)
        assert code == 0 and decoded['status'] == 'ANSWER'
        assert decoded['results'][0]['stored_text'] == record['stored_text']


def _item(record):
    return {'ref_sha256': fx.sha(record['provenance']['event_ref'].encode()),
            'doc_id_sha256': fx.sha(record['record_id'].encode()), 'ref_kind': 'sha256_of_projected_metadata',
            'canonical_event': False}


def direct(route, edit_manifest=None, projection_raw=None, policy=None):
    """The resolver alone (no child pre-checks); the manifest is re-pinned to the given projection and policy."""
    data = route['data']
    raw_projection = data['projection_bytes'] if projection_raw is None else projection_raw
    strict = pins() if policy is None else policy
    manifest = json.loads(data['memory_bytes'])
    manifest['projection_sha256'] = fx.sha(raw_projection)
    manifest['policy']['policy_sha256'] = strict['policy_sha256']
    if edit_manifest:
        edit_manifest(manifest)
    raw = json.dumps(manifest).encode()
    return resolver.resolve_results(memory_manifest=raw, expected_memory_sha256=fx.sha(raw),
                                    projection_bytes=raw_projection, results=[_item(route['projection']['records'][0])],
                                    query_utc='2026-11-02T00:00:00Z', **strict)


def canonical(value):
    return json.dumps(value, sort_keys=True, indent=1).encode('utf-8')


def test_the_resolver_alone_answers_the_re_pinned_baseline(route, monkeypatch):
    monkeypatch.setattr(resolver, 'CARD_ROUTE', scp)
    record = route['projection']['records'][0]
    assert direct(route) == [dict(_item(record), stored_text=record['stored_text'])]


def test_the_resolver_alone_refuses_a_card_memory_while_the_route_is_off(route):
    assert resolver.CARD_ROUTE is None and resolver.card_route() is None
    with pytest.raises(resolver.ResolveRefused):
        direct(route)


@pytest.mark.parametrize('change', ['scope', 'projection_pin', 'policy_without_card_task', 'non_canonical_bytes',
                                    'tampered_stored_text', 'review_lane_author'])
def test_the_resolver_alone_refuses_each_card_route_change(route, monkeypatch, change):
    monkeypatch.setattr(resolver, 'CARD_ROUTE', scp)
    projection = json.loads(route['data']['projection_bytes'])
    kwargs = {}
    if change == 'scope':
        kwargs['edit_manifest'] = lambda m: m.update(scope='wd.q4-shared-card-events.v1')
    elif change == 'projection_pin':
        kwargs['edit_manifest'] = lambda m: m.update(projection_sha256='0' * 64)
    elif change == 'policy_without_card_task':
        kwargs['policy'] = pins(('synthetic-task',))
    elif change == 'non_canonical_bytes':
        kwargs['projection_raw'] = json.dumps(projection, sort_keys=True, indent=2).encode('utf-8')
    elif change == 'tampered_stored_text':
        projection['records'][0]['stored_text'] = projection['records'][0]['stored_text'].replace('green', 'red', 1)
        projection['records'][0]['stored_text'] += ' tag:x'
        kwargs['projection_raw'] = canonical(projection)
    else:
        monkeypatch.setattr(scp, 'validate_shared_card_projection', lambda value: value)  # isolate the lane check
        projection['records'][0]['meta']['agent'] = 'claude-rco-1'
        kwargs['projection_raw'] = canonical(projection)
    with pytest.raises(resolver.ResolveRefused):
        direct(route, **kwargs)


def test_the_resolved_child_uses_the_card_validator_only_while_the_route_is_on(route, monkeypatch):
    raw = route['data']['projection_bytes']
    with pytest.raises(ValueError):
        child._validate_projection(raw, frozenset({TASK}), ROSTER)
    monkeypatch.setattr(resolver, 'CARD_ROUTE', scp)
    child._validate_projection(raw, frozenset({TASK}), ROSTER)
    projection = json.loads(raw)
    projection['records'][0]['stored_text'] += ' tag:x'
    with pytest.raises(ValueError):
        child._validate_projection(canonical(projection), frozenset({TASK}), ROSTER)


def test_the_build_refuses_a_review_lane_author_even_past_the_card_validator(tmp_path, monkeypatch):
    pytest.importorskip('faiss')
    projection = fx.project(fx.corpus())
    projection['records'][0]['meta']['agent'] = 'claude-rco-2'
    monkeypatch.setattr(scp, 'validate_shared_card_projection', lambda value: value)  # isolate the lane check
    root = tmp_path / 'out'
    root.mkdir()
    with pytest.raises(scp.SharedCardRefused, match='roster_mismatch'):
        scp.build_shared_route_card_memory(projection, fx.encode, fx.IDENTITY, fx.DIM, root, 'cards', enabled=True,
                                           **pins())
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('switch', [True, 'sandbox.mind.step2.shared_card_projection', json])
def test_only_the_card_module_itself_turns_the_route_on(route, monkeypatch, switch):
    monkeypatch.setattr(resolver, 'CARD_ROUTE', switch)
    assert resolver.card_route() is None
    with pytest.raises(resolver.ResolveRefused):
        direct(route)
    code, out = run(route)
    assert code == 2 and out['status'] == 'REFUSED' and route['calls'] == []


def test_the_resolver_and_the_child_never_import_the_card_module():
    for rel in ('sandbox/mind/step2/shared_result_resolver.py', 'tools/wd_q4_resolved_child.py'):
        text = (ROOT / rel).read_text(encoding='utf-8')
        assert 'import shared_card_projection' not in text and 'shared_card_projection import' not in text
