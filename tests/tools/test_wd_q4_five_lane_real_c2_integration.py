"""Synthetic C2 -> actual updater/CAS/selector -> five declared library readers.

Uses fabricated Git/cards, a bag-of-words encoder and an in-process launcher.
Identity, source checks, FAISS read and result resolution are real. No native
Job, installed route, caller guard cleanup, E5 or authenticated lane is proved.
The source-ref oracle is rebuilt from original source bytes, never query output
or projection records. These tests do not authorize a production source mode.
"""
import copy
import datetime as dt
import io
import json
from pathlib import Path
import types

import pytest

from tests.tools.test_wd_q4_card_audit_orchestrator import attested, materialize, sha
from tests.tools.test_wd_q4_staged_lane_query import staged_producer
from tests.tools.test_wd_q4_card_refresh_coordinator import _build_case, configured, invoke
from tests.tools import test_shared_card_route as rt


LANES = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')


def canonical(value, *, ascii=True):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=ascii, allow_nan=False).encode('utf-8')


def source_oracle(document):
    """Independent oracle for original cards and the primary superseding card."""
    source = document['source']
    prefix = Path(source['path']).read_bytes()[:source['bytes']]
    assert sha(prefix) == source['sha256']
    events = [json.loads(line) for line in prefix.splitlines()]
    assert 5 <= len(events) <= 6
    original_ids = [sha(canonical(event, ascii=False)) for event in events]
    if len(events) == 6:
        assert events[-1]['payload']['public_knowledge']['supersedes'] == original_ids[0]
    superseded = {event['payload']['public_knowledge'].get('supersedes') for event in events}
    expected, primary = {}, None
    for event, source_id in zip(events, original_ids):
        if source_id in superseded:
            continue
        data = event['payload']['public_knowledge']
        assert data['kind'] == 'merged' and 2046 <= data['subject']['pr'] <= 2050
        assert data['outcome'] == 'green' and data['next'] == 'none' and data['tags'] == ['genesis']
        text = ('merged green ' + event['agent'] + ' pr:' + str(data['subject']['pr']) + ' head:' + data['head']
                + ' next:none tag:genesis until:' + data['valid_until_utc'][:16] + 'Z')
        ref = sha(canonical({'text': text, 'source': source_id}))
        item = {'ref_sha256': sha(ref.encode()), 'doc_id_sha256': sha(('card:' + ref).encode()),
                'stored_text': text, 'source_ref': source_id}
        expected[item['ref_sha256']] = item
        if data['subject']['pr'] == 2046:
            primary = item['ref_sha256']
    assert len(expected) == 5 and primary
    return {'allowed': expected, 'primary': primary}


def sufficient_source(original, root):
    """Five cards meet the unchanged Q4 minimum; never weaken its agreement rule."""
    args, out, event, observation = original(root)
    events = [event]
    for minute in range(1, 5):
        extra = rt.fx.card(rt.fx.LEAD, minute, 'merged', {'pr': 2046 + minute},
                           'green', 'none', ['genesis'], head=event['payload']['public_knowledge']['head'])
        extra['payload']['public_knowledge']['tags'].remove('synthetic')
        events.append(extra)
    decision, _, source = rt.anchor(root / 'enough-prefix', events,
        decision='enable_real_card_route', synthetic=False, observed_heads=observation['heads'],
        vocabulary_sha256=args['vocabulary_sha256'], head_observation_sha256=observation['sha256'])
    args.update(decision_bytes=decision, decision_sha256=sha(decision), **source)
    return args, out, event, observation


@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    root = tmp_path_factory.mktemp('f') / '.codex-audit'; root.mkdir()
    patch = pytest.MonkeyPatch()
    try:
        staged = staged_producer.__wrapped__(root)
        fleet = json.loads(staged['fleet_bytes'])
        runtime = root / 'runtime'
        members = fleet['lanes'] + [fleet['tools_supervisor']]
        assert {entry['agent'] for entry in members} == set(LANES)
        for entry in members:
            worktree = root / 'lanes' / entry['agent']; worktree.mkdir(parents=True)
            entry['worktree'] = str(worktree)
            (runtime / 'shared' / '.codex-audit' / entry['agent'] / 'q4-caller-state').mkdir(parents=True)
        fleet['runtime_root'] = str(runtime)
        staged['fleet_bytes'] = canonical(fleet)
        staged['expected_fleet_sha256'] = sha(staged['fleet_bytes'])
        settings = materialize.__wrapped__(staged, root, patch)
        from tests.tools import test_wd_q4_card_audit_orchestrator as orchestrator_fixture
        original = orchestrator_fixture.real_fixture.__wrapped__
        patch.setattr(orchestrator_fixture, 'real_fixture',
                      types.SimpleNamespace(__wrapped__=lambda path: sufficient_source(original, path)))
        value = _build_case(attested.__wrapped__(settings, patch), root, patch)
        value['members'] = {entry['agent']: entry for entry in members}
        value['counter'] = 0
        value['originals'] = {path: path.read_bytes() for path in root.rglob('*.json')}
        yield value
    finally:
        patch.undo()


@pytest.fixture
def route(prepared, monkeypatch):
    prepared['counter'] += 1
    out = prepared['tmp'] / ('r' + str(prepared['counter'])); out.mkdir()
    private = out / 'private'; private.mkdir()
    def tuple_for(char='c', previous=None):
        result = copy.deepcopy(prepared['tuple_for'](char, previous))
        result['private_root'] = str(private)
        return result
    value = dict(prepared, tmp=out, private=private, tuple_for=tuple_for)
    try:
        yield value
    finally:
        if value['selector'].exists():
            (out / 'selector-after.json').write_bytes(value['selector'].read_bytes())
            value['selector'].unlink()  # fabricated selector owned by this fixture
        for path, raw in prepared['originals'].items():
            if path.read_bytes() != raw:
                path.write_bytes(raw)


def publish(route, char):
    previous = sha(route['selector'].read_bytes()) if route['selector'].exists() else None
    document = route['tuple_for'](char, previous)
    oracle = source_oracle(document)
    result = invoke(configured(route, document, char), char * 64)
    assert result['status'] == 'PUBLISHED'
    return oracle


def selected(route):
    baseline = route['caller']._configuration(route['baseline']['path'], route['baseline']['sha256'])
    result = route['live'].select(enabled=True, p1_enabled=True,
        baseline_path=route['baseline']['path'], baseline_sha256=route['baseline']['sha256'],
        baseline=baseline, manifest_sha256=route['manifest'], load_configuration=route['caller']._configuration)
    return result['configuration']


def read_as(route, lane, monkeypatch, *, when='2026-11-03T00:00:00Z', unknown=False):
    from tools import wd_q4_public_query as query
    from sandbox.mind.step2 import runtime_launcher, wd_q4_private_read as reader
    from waggledance.core.bridge_log_reader import BridgeCursor
    descriptor, _, pin, fleet, binding, inputs = selected(route)
    member = route['members'][lane]
    monkeypatch.chdir(member['worktree'])
    for key, value in {'AGENT_BRIDGE_AGENT': lane, 'AGENT_BRIDGE_AGENT_UUID': member['agent_uuid'],
            'AGENT_BRIDGE_RUN_ID': 'synthetic-' + lane, 'AGENT_BRIDGE_OWNER_TOKEN': 'synthetic-token-' + lane}.items():
        monkeypatch.setenv(key, value)
    actual_lane, identity, _ = route['caller']._identity(fleet, descriptor, pin)
    assert actual_lane == lane
    identity = json.loads(identity)
    assert identity['agent_uuid'] == member['agent_uuid'] and identity['run_id'] == 'synthetic-' + lane
    plan = json.loads(Path(descriptor['public_plan']).read_bytes())
    config = rt.config(Path(plan['config']['memory']), plan['config']['descriptor_sha256'])
    calls = []
    monkeypatch.setattr(reader, 'ENCODER_FACTORY', lambda **_: rt.fx.Encoder())
    monkeypatch.setattr(reader, 'bind_runtime_encoder', lambda *a: None)
    clock = dt.datetime.fromisoformat(when.replace('Z', '+00:00'))
    monkeypatch.setattr(query.q4_query, '_utc_now', lambda: clock)
    def launcher(*, query, **_):
        calls.append(True)
        request = json.loads(query)
        if unknown:
            return {'status': 'UNKNOWN', 'results': []}
        result = reader._private_read_locked(Path(config.memory), Path(config.descriptor),
            config.descriptor_sha256, request['situation'], request['time'],
            r'C:\synthetic\model', config.runtime_revision)
        return runtime_launcher._validate_output(canonical(result), 0, config.descriptor_sha256)
    monkeypatch.setattr(query.q4_query, '_invoke_launcher', launcher)
    situation = ('Please retrieve prior merged green genesis work regarding pr 2046 '
                 'with evidence before this unrelated planning request')
    args = dict(root=plan['root'], pointer_bytes=Path(plan['pointer_path']).read_bytes(),
        expected_pointer_sha256=plan['pointer_sha256'], expected_source_sha256=plan['source_anchor_sha256'],
        expected_encoder_sha256=plan['encoder_sha256'], policy_bytes=Path(plan['policy_path']).read_bytes(),
        policy_sha256=plan['policy_sha256'], roster_bytes=Path(plan['roster_path']).read_bytes(),
        roster_sha256=plan['roster_sha256'], card_decision_bytes=Path(plan['card_decision_path']).read_bytes(),
        card_decision_sha256=plan['card_decision_sha256'], card_policy_bytes=Path(plan['card_policy_path']).read_bytes(),
        source_path=plan['source_path'], generation_path=plan['generation_path'],
        end_cursor=BridgeCursor(**plan['end_cursor']), enabled=True)
    answer = query.query_staged_card_snapshot(config,
        io.BytesIO(canonical({'situation': situation, 'time': when})), **args)
    assert calls == [True]
    assert answer['binding']['pointer_sha256'] == binding['pointer_sha256']
    assert answer['freshness'] == answer['completeness'] == answer['publisher_authentication'] == 'UNKNOWN'
    return answer, identity


@pytest.mark.parametrize('lane', LANES)
def test_real_c2_update_c_then_d_read_with_exact_identity_and_source_refs(route, monkeypatch, lane):
    identities = []
    for char in 'cd':
        expected = publish(route, char)
        answer, identity = read_as(route, lane, monkeypatch)
        assert answer['status'] == 'ANSWER' and answer['results']
        assert expected['primary'] in {item['ref_sha256'] for item in answer['results']}
        for item in answer['results']:
            expected_item = expected['allowed'][item['ref_sha256']]
            assert {key: item[key] for key in ('ref_sha256', 'doc_id_sha256', 'stored_text')} == {
                key: expected_item[key] for key in ('ref_sha256', 'doc_id_sha256', 'stored_text')}
            assert item['canonical_event'] is False
        identities.append(identity)
    assert identities[0]['descriptor_sha256'] != identities[1]['descriptor_sha256']


@pytest.mark.parametrize('lane', LANES)
@pytest.mark.parametrize('fault', ('stale', 'missing', 'unknown'))
def test_each_reader_refuses_stale_missing_or_unknown_instead_of_success(route, monkeypatch, lane, fault):
    from tools import wd_q4_public_query as query
    publish(route, 'c')
    if fault == 'stale':
        answer, _ = read_as(route, lane, monkeypatch, when='2026-11-05T00:00:00Z')
        assert answer['status'] == 'ABSTAIN' and answer['results'] == []
    elif fault == 'unknown':
        with pytest.raises(query.PublicQueryRefused):
            read_as(route, lane, monkeypatch, unknown=True)
    else:
        descriptor = selected(route)[0]
        plan = json.loads(Path(descriptor['public_plan']).read_bytes())
        pointer = Path(plan['pointer_path']); raw = pointer.read_bytes()
        try:
            pointer.unlink()
            with pytest.raises(ValueError):
                read_as(route, lane, monkeypatch)
        finally:
            pointer.write_bytes(raw)


@pytest.mark.parametrize('lane', LANES)
def test_each_identity_rejects_other_lanes_uuid(route, monkeypatch, lane):
    publish(route, 'c')
    descriptor, _, pin, fleet, _, _ = selected(route)
    member = route['members'][lane]
    monkeypatch.chdir(member['worktree'])
    other = next(name for name in LANES if name != lane)
    for key, value in {'AGENT_BRIDGE_AGENT': lane,
            'AGENT_BRIDGE_AGENT_UUID': route['members'][other]['agent_uuid'],
            'AGENT_BRIDGE_RUN_ID': 'synthetic-cross-lane', 'AGENT_BRIDGE_OWNER_TOKEN': 'synthetic-token'}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        route['caller']._identity(fleet, descriptor, pin)
