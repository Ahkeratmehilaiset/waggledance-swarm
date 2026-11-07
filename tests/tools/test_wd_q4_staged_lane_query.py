"""Synthetic staged card lane query, with substituted v3 transport replies."""

import hashlib
import json
from pathlib import Path

import pytest

from tests.tools.test_lane_card_decision_forward import lane
from tools import wd_q4_lane_binding_producer as producer
from tools import wd_q4_lane_query as query


@pytest.fixture
def staged_lane(lane):
    call, plan = lane['call'], lane['document']
    old_transport = call['transport']
    input_bytes = json.dumps({'situation': call['situation'],
                              'time': call['time'].strftime('%Y-%m-%dT%H:%M:%SZ')}).encode()
    raw, code = old_transport(wrapper='synthetic', arguments=('-PublicPlanPath', str(call['public_plan_path'])),
                              stdin_bytes=input_bytes)
    assert code == 0
    reply = json.loads(raw)
    reply['schema'] = 'wd.q4-card-snapshot-query.v1'
    reply['completeness'] = 'UNKNOWN'
    reply['binding'].pop('public_policy_sha256')
    reply['binding'].pop('receipt_sha256')
    reply['binding'].update(source_mode='card-snapshot-v1', card_policy_sha256='f'*64)
    plan.update(schema='wd.q4-public-wrapper-plan.v3', card_policy_path='synthetic-policy',
                card_policy_sha256='f'*64, pointer_path='synthetic-pointer', source_path='synthetic-source',
                generation_path='synthetic-generation', end_cursor={'offset': 3,
                'file_identity': 'synthetic', 'generation': 'g1'})
    raw_plan = json.dumps(plan, sort_keys=True).encode()
    Path(call['public_plan_path']).write_bytes(raw_plan)
    call['expected_public_plan_sha256'] = hashlib.sha256(raw_plan).hexdigest()
    call['expected_binding']['card_policy_sha256'] = 'f'*64
    call['transport'] = lambda **_: (json.dumps(reply).encode(), 0)
    return call, reply


def test_staged_result_resolves_through_lane_without_invented_receipt(staged_lane):
    call, _ = staged_lane
    answer = query.query_public(**call)
    assert answer['status'] == 'ANSWER' and answer['results']
    assert answer['binding'] == call['expected_binding']
    assert 'receipt_sha256' not in answer['binding']
    assert answer['trust']['freshness'] == 'UNKNOWN'


@pytest.mark.parametrize('fault', ['different_card_policy', 'different_projection', 'missing_completeness'])
def test_staged_result_with_bad_binding_refuses(staged_lane, fault):
    call, reply = staged_lane
    if fault == 'different_card_policy': reply['binding']['card_policy_sha256'] = 'e'*64
    if fault == 'different_projection': reply['binding']['projection_sha256'] = 'e'*64
    if fault == 'missing_completeness': del reply['completeness']
    with pytest.raises(query.LaneQueryRefused):
        query.query_public(**call)


@pytest.fixture
def staged_producer(tmp_path):
    pytest.importorskip('faiss')
    from tests.tools.test_wd_q4_card_generation import prepared
    from tools import wd_q4_card_generation, wd_q4_roster_from_fleet
    from sandbox.mind.step2.shared_index_preflight import _encoder_digest

    args, root = prepared(tmp_path)
    fleet = args['roster_bytes']
    roster, roster_sha = wd_q4_roster_from_fleet.build_roster_snapshot(fleet, args['roster_sha256'], enabled=True)
    decision = json.loads(args['decision_bytes'])
    decision['roster_sha256'] = roster_sha
    decision_raw = json.dumps(decision, sort_keys=True, separators=(',', ':')).encode('ascii')
    args.update(roster_bytes=roster, roster_sha256=roster_sha, decision_bytes=decision_raw,
                decision_sha256=hashlib.sha256(decision_raw).hexdigest())
    result = wd_q4_card_generation.prepare_card_generation(**args)
    assert result['status'] == 'STAGED'
    directory = root / args['generation_name']
    sha = lambda raw: hashlib.sha256(raw).hexdigest()
    artifacts = {str(path.relative_to(directory)).replace('\\', '/'): sha(path.read_bytes())
                 for path in directory.rglob('*') if path.is_file()}
    pointer = {'schema': 'wd.q4-card-snapshot.v1', 'generation': args['generation_name'],
               'artifacts': artifacts, 'decision_sha256': args['decision_sha256'],
               'policy_sha256': args['policy_sha256'], 'roster_sha256': roster_sha,
               'source_prefix': decision['source_prefix']}
    pointer_raw = json.dumps(pointer, sort_keys=True, separators=(',', ':')).encode('ascii')
    public = json.dumps({'schema': 'wd.mind-public-status-policy.v1',
                         'public_tasks': ['mind-public-knowledge-v1'], 'public_types': ['status']}).encode()
    blobs = {'pointer': pointer_raw, 'policy': args['policy_bytes'], 'roster': roster, 'public_policy': public,
             'card_decision': decision_raw, 'card_policy': args['card_policy_bytes']}
    paths = {}
    for name, raw in blobs.items():
        path = tmp_path / (name + '.json')
        path.write_bytes(raw)
        paths[name] = str(path)
    descriptor = json.loads((directory / 'descriptor.json').read_bytes())
    cursor = args['end_cursor']
    plan = {'schema': 'wd.q4-public-wrapper-plan.v3', 'config': {'descriptor': str(directory / 'descriptor.json'),
            'descriptor_sha256': artifacts['descriptor.json'], 'memory': str(directory),
            'runtime_revision': descriptor['encoder']['revision']}, 'root': str(root),
            **{name + '_path': paths[name] for name in blobs},
            **{name + '_sha256': sha(raw) for name, raw in blobs.items() if name != 'card_policy'},
            'card_policy_sha256': sha(blobs['card_policy']), 'source_path': args['source_path'],
            'generation_path': args['generation_path'], 'source_anchor_sha256': decision['source_prefix']['sha256'],
            'encoder_sha256': _encoder_digest(descriptor['encoder']),
            'end_cursor': {'file_identity': cursor.file_identity, 'generation': cursor.generation, 'offset': cursor.offset}}
    wrapper = b'synthetic-wrapper'
    manifest = json.dumps({'schema_version': 1, 'source_commit': 'a'*40,
                           'files': {'wd-fleet.json': sha(fleet), 'Invoke-WdQ4Query.ps1': sha(wrapper)}}).encode()
    plan_raw = json.dumps(plan, sort_keys=True).encode()
    return dict(enabled=True, expected_commit='a'*40, manifest_bytes=manifest,
                expected_manifest_sha256=sha(manifest), fleet_bytes=fleet, expected_fleet_sha256=sha(fleet),
                wrapper_bytes=wrapper, expected_wrapper_sha256=sha(wrapper), public_plan_bytes=plan_raw,
                expected_public_plan_sha256=sha(plan_raw), pointer_bytes=pointer_raw,
                policy_bytes=args['policy_bytes'], roster_bytes=roster, public_policy_bytes=public,
                card_decision_bytes=decision_raw, card_policy_bytes=args['card_policy_bytes'])


def test_real_synthetic_staged_snapshot_produces_ten_pin_binding(staged_producer):
    result = producer.build_staged_lane_binding(**staged_producer)
    doc = json.loads(result['binding_bytes'])
    assert result['binding_sha256'] == hashlib.sha256(result['binding_bytes']).hexdigest()
    assert set(doc['binding']) == query.STAGED_BINDING_KEYS
    assert len(doc['lanes']) == 5
    assert 'receipt_sha256' not in doc['binding']


def test_staged_producer_is_off_without_reading_inputs():
    class Untouchable:
        def __getattribute__(self, name):
            raise AssertionError('OFF accessed input')
    assert producer.build_staged_lane_binding(manifest_bytes=Untouchable()) == {'status': 'OFF'}


def test_staged_producer_accepts_append_after_frozen_prefix_without_claiming_freshness(staged_producer):
    plan = json.loads(staged_producer['public_plan_bytes'])
    source = Path(plan['source_path'])
    source.write_bytes(source.read_bytes() + b'{"synthetic":"later"}\n')
    result = producer.build_staged_lane_binding(**staged_producer)
    assert json.loads(result['binding_bytes'])['binding']['source_anchor_sha256'] == plan['source_anchor_sha256']


@pytest.mark.parametrize('fault', ['source', 'generation', 'projection', 'card_policy', 'pointer',
                                   'wrong_cursor', 'wrong_encoder', 'wrong_descriptor', 'wrong_roster',
                                   'wrong_memory', 'wrong_revision'])
def test_staged_producer_refuses_changed_or_mismatched_evidence(staged_producer, fault):
    case = dict(staged_producer)
    plan = json.loads(case['public_plan_bytes'])
    if fault in ('source', 'generation', 'projection', 'card_policy', 'pointer'):
        if fault == 'projection':
            path = Path(plan['config']['descriptor']).parent / 'projection.json'
        else:
            path = Path(plan[{'source': 'source_path', 'generation': 'generation_path',
                              'card_policy': 'card_policy_path', 'pointer': 'pointer_path'}[fault]])
        if fault == 'source':
            data = path.read_bytes()
            path.write_bytes(b'!' + data[1:])
        elif fault == 'generation':
            path.write_bytes(b'{"generation":"other"}')
        else:
            path.write_bytes(path.read_bytes() + b' ')
    elif fault == 'wrong_roster':
        case['roster_bytes'] += b' '
    else:
        if fault == 'wrong_cursor': plan['end_cursor']['offset'] += 1
        if fault == 'wrong_encoder': plan['encoder_sha256'] = 'f'*64
        if fault == 'wrong_descriptor': plan['config']['descriptor_sha256'] = 'f'*64
        if fault == 'wrong_memory': plan['config']['memory'] = plan['root']
        if fault == 'wrong_revision': plan['config']['runtime_revision'] = 'f'*40
        case['public_plan_bytes'] = json.dumps(plan, sort_keys=True).encode()
        case['expected_public_plan_sha256'] = hashlib.sha256(case['public_plan_bytes']).hexdigest()
    with pytest.raises(producer.BindingProducerRefused, match='^lane_binding_producer_refused$'):
        producer.build_staged_lane_binding(**case)
