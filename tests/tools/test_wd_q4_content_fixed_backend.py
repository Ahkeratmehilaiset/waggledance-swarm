"""Synthetic contract checks for the content fixed-wrapper candidate.

These unit tests do not exercise the real v5 source rederivation or a running
wrapper; the combined 30fc-plus-integration acceptance test remains required.
"""
import hashlib
import json

import pytest

from tools import wd_q4_content_fixed_backend as backend
from tools import wd_q4_secure_transport as fixed
from tools import wd_q4_lane_link as link


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def test_content_bundle_is_closed_and_roundtrips():
    data = {k: ('synthetic-' + k).encode() for k in fixed._CONTENT_BUNDLE_FIELDS}
    assert fixed.fixed_content_bundle(**data) == {'status': 'OFF'}
    raw = fixed.fixed_content_bundle(enabled=True, **data)
    assert fixed._fixed_content_bundle_decode(raw) == data
    with pytest.raises(fixed._Refused):
        fixed._fixed_content_bundle_decode(raw + b' ')
    with pytest.raises(fixed._Refused):
        fixed.fixed_content_bundle(enabled=True, **{**data, 'policy': b''})


def test_content_input_is_off_and_invalid_input_fails_closed():
    assert backend.validate_content_input({}, b'', b'') == {'status': 'OFF'}
    with pytest.raises(ValueError, match='^content_input_refused$'):
        backend.validate_content_input({}, b'', b'', enabled=True)


def test_v4_public_plan_is_rejected_by_v5_backend():
    public = json.dumps({'schema': 'wd.q4-public-wrapper-plan.v4', 'config': {'timeout': 20}}).encode()
    plan = {'bundle_root': r'C:\synthetic\bundle', 'wrapper_path': r'C:\synthetic\bundle\Invoke-WdQ4Query.ps1',
            'public_plan_path': r'C:\synthetic\public-plan.json', 'bundle_commit': 'a' * 40,
            'deployment_manifest_sha256': 'b' * 64, 'public_plan_sha256': sha(public)}
    with pytest.raises(ValueError):
        backend._wrapper_command(plan, r'C:\Windows', public)


@pytest.mark.parametrize('changed', sorted(fixed._CONTENT_PINS))
def test_any_public_pin_difference_refuses_before_source_access(changed, monkeypatch):
    data = {k: ('synthetic-' + k).encode() for k in fixed._CONTENT_BUNDLE_FIELDS}
    raw = fixed.fixed_content_bundle(enabled=True, **data)
    public = json.dumps({'schema': 'wd.q4-public-wrapper-plan.v5'}).encode()
    plan = {k: 'a' * 64 for k in fixed._CONTENT_PINS}
    plan.update(bundle_bytes=len(raw), bundle_sha256=sha(raw), public_plan_sha256=sha(public),
                public_plan_path=r'C:\synthetic\public-plan.json')
    pins = {k: plan[k] for k in fixed._CONTENT_PINS}
    pins[changed] = 'b' * 64
    monkeypatch.setattr(link, '_public_plan', lambda *_: ('fixture', public, pins))
    monkeypatch.setattr(link, '_json', lambda *_: pytest.fail('must refuse before loading source'))
    with pytest.raises(ValueError, match='^content_input_refused$'):
        backend.validate_content_input(plan, raw, public, enabled=True)


def test_parent_refuses_to_start_wrapper_if_pinned_input_check_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(backend, 'validate_content_input', lambda *a, **k: (_ for _ in ()).throw(ValueError()))
    monkeypatch.setattr(backend.subprocess, 'Popen', lambda *a, **k: calls.append('started'))
    instance = backend._PreparedBackend({}, b'plan', [], {}, None, None, None, None, None)
    instance._accepted_bundle = sha(b'bundle')
    instance._check_imports = lambda: None
    with pytest.raises(ValueError):
        instance(instance.plan, b'plan', b'bundle', b'query')
    assert calls == []

from pathlib import Path
from tests.tools.test_wd_q4_content_query import content  # synthetic message/doc source fixture


@pytest.fixture
def content_v5(content, tmp_path):
    from sandbox.mind.step2 import content_memory as cm
    from tools.wd_q4_public_query import _encoder_digest
    if not hasattr(cm, 'snapshot_sources'):
        pytest.skip('requires the separately integrated 30fc query-time source route')
    args, source = content['args'], content['route']
    def put(name, data):
        path = tmp_path / name
        path.write_bytes(data)
        return str(path)
    policy_path = put('nonreview.json', args['policy_bytes'])
    roster_path = put('roster.json', args['roster_bytes'])
    decision_path = put('decision.json', args['content_decision_bytes'])
    message_policy_path = put('message.json', source['messages']['policy_bytes'])
    doc_policy_path = put('docs.json', source['documents']['policy_bytes'])
    memory_dir = content['dir']
    descriptor = (memory_dir / 'descriptor.json').read_bytes()
    memory = (memory_dir / 'memory.json').read_bytes()
    projection = (memory_dir / 'projection.json').read_bytes()
    d = json.loads(descriptor)
    public = {'schema': 'wd.q4-public-wrapper-plan.v5',
        'config': {'descriptor_sha256': sha(descriptor), 'descriptor': str(memory_dir / 'descriptor.json'),
                   'memory': str(memory_dir), 'runtime_revision': d['encoder']['revision']},
        'policy_path': policy_path, 'roster_path': roster_path, 'content_decision_path': decision_path,
        'policy_sha256': args['policy_sha256'], 'roster_sha256': args['roster_sha256'],
        'content_decision_sha256': args['content_decision_sha256'], 'encoder_sha256': _encoder_digest(d['encoder']),
        'memory_sha256': sha(memory), 'projection_sha256': sha(projection),
        'message_source_path': source['messages']['source_path'],
        'message_generation_path': source['messages']['generation_path'], 'message_policy_path': message_policy_path,
        'doc_repo_root': source['documents']['repo_root'], 'doc_policy_path': doc_policy_path,
        'doc_git_executable': source['documents']['git_executable'], 'doc_git_sha256': source['documents']['git_sha256']}
    public_bytes = json.dumps(public, sort_keys=True).encode()
    public_path = put('content-plan.v5.json', public_bytes)
    bundle = fixed.fixed_content_bundle(enabled=True, policy=args['policy_bytes'], roster=args['roster_bytes'],
        content_decision=args['content_decision_bytes'], memory=memory, projection=projection, descriptor=descriptor)
    plan = {key: public['config']['descriptor_sha256'] if key == 'descriptor_sha256' else public[key]
            for key in fixed._CONTENT_PINS}
    plan.update(bundle_bytes=len(bundle), bundle_sha256=sha(bundle), public_plan_sha256=sha(public_bytes),
                public_plan_path=public_path, approved_tasks=json.loads(args['policy_bytes'])['approved_tasks'])
    return plan, bundle, public_bytes, public


def test_content_v5_accepts_consistent_pinned_projection(content_v5):
    plan, bundle, raw, _ = content_v5
    assert backend.validate_content_input(plan, bundle, raw, enabled=True) == {
        'status': 'VALIDATED_CONTENT', 'completeness': 'UNKNOWN'}


@pytest.mark.parametrize('kind', ['generation', 'prefix', 'document_policy', 'git_pin'])
def test_content_v5_wrapper_refuses_changed_pinned_source_before_query(content_v5, content, kind):
    from tests.tools.test_wd_q4_content_snapshot_query import run, content_sources
    plan, bundle, raw, public = content_v5
    sources = content_sources(content['route'])
    if kind in ('generation', 'prefix', 'document_policy'):
        key = {'generation': 'message_generation_path', 'prefix': 'message_source_path',
               'document_policy': 'doc_policy_path'}[kind]
        path = Path(public[key])
        path.write_bytes(b'changed-synthetic-source')
        if kind == 'document_policy':
            sources['doc_policy_bytes'] = path.read_bytes()
    else:
        sources['doc_git_sha256'] = '0' * 64
    code, result = run(content, content_sources=sources)
    assert code == 2 and result['status'] == 'REFUSED'
    assert content['calls'] == []  # the 30fc wrapper refuses before the model/reader call


def test_content_v5_reresolves_real_synthetic_child_result(content_v5, content):
    from tests.tools.test_wd_q4_content_snapshot_query import run, cq, tcm
    plan, bundle, raw, _ = content_v5
    code, result = run(content)
    assert code == 0 and result['status'] == 'ANSWER'
    query = json.dumps({'situation': cq.SITUATION, 'time': tcm.ASK_TIME}).encode('utf-8')
    encoded = json.dumps(result).encode('utf-8')
    checked = backend.validate_content_result(plan, bundle, raw, query, encoded, enabled=True)
    assert json.loads(checked) == result
    result['binding']['memory_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='^content_result_refused$'):
        backend.validate_content_result(plan, bundle, raw, query, json.dumps(result).encode(), enabled=True)


from tests.tools.test_wd_q4_bound_lane_transport import case


@pytest.mark.parametrize('changed', [None, *sorted(fixed._CONTENT_PINS)])
def test_guarded_content_factory_requires_all_seven_matching_pins(content_v5, case, tmp_path, monkeypatch, changed):
    from tests.tools.test_wd_q4_owner_plumbing import _synthetic_guard
    from tools import wd_q4_bound_lane_transport as bound
    from tools import wd_q4_fixed_plan_producer as producer
    input_plan, bundle, public_bytes, _ = content_v5
    path = Path(case['public_plan_path'])
    path.write_bytes(public_bytes)
    case['expected_public_plan_sha256'] = sha(public_bytes)
    base = fixed._fixed_plan(case['fixed_plan_bytes'])
    pins = {key: input_plan[key] for key in fixed._CONTENT_PINS}
    if changed is not None:
        pins[changed] = '0' * 64
    fixed_plan = producer.build_content_fixed_plan(enabled=True, **pins,
        interpreter_sha256=base['interpreter_sha256'], peer_code_sha256=base['peer_code_sha256'],
        verifier_sha256=base['verifier_sha256'], runtime_manifest_sha256=base['runtime_manifest_sha256'],
        bundle_sha256=sha(bundle), wrapper_sha256=case['expected_wrapper_sha256'],
        deployment_manifest_sha256=case['expected_manifest_sha256'], public_plan_sha256=case['expected_public_plan_sha256'],
        host_sha256='1' * 64, backend_sha256='2' * 64, bundle_bytes=len(bundle), runtime_root=base['runtime_root'],
        runtime_manifest_path=base['runtime_manifest_path'], runtime_generation=base['runtime_generation'],
        runtime_revision=base['runtime_revision'], bundle_root=case['bundle_root'], bundle_commit=case['expected_commit'],
        wrapper_path=str(Path(case['bundle_root']) / 'Invoke-WdQ4Query.ps1'), public_plan_path=str(path),
        approved_tasks=sorted(input_plan['approved_tasks']))
    case.update(fixed_plan_bytes=fixed_plan['plan_bytes'], expected_fixed_plan_sha256=fixed_plan['plan_sha256'],
                fixed_bundle_bytes=bundle)
    guard, pending = _synthetic_guard(tmp_path / 'content-guard', 'codex-lead-1')
    context = guard._owner_context()
    before = pending.read_bytes()
    monkeypatch.setattr(fixed, 'run_fixed_peer', lambda **_: pytest.fail('factory must never launch a child'))
    values = {key: value for key, value in case.items() if not key.startswith('_')}
    if changed is not None:
        with pytest.raises(bound.BoundLaneTransportRefused):
            bound.make_guarded_content_transport(**values, _owner_guard=guard, _owner_context=context)
    else:
        with pytest.raises(bound.BoundLaneTransportRefused):
            bound.make_guarded_content_transport(**values)
        assert callable(bound.make_guarded_content_transport(**values, _owner_guard=guard, _owner_context=context))
        # Exercise only factory selection and decoded content bytes; the
        # synthetic lane binding below is not a real roster admission.
        from tools import wd_q4_lane_session_client as session
        parts = fixed._fixed_content_bundle_decode(bundle)
        roster_sha = sha(parts['roster'])
        resolved = {'lane': 'codex-lead-1', 'expected_commit': case['expected_commit'],
            'expected_manifest_sha256': case['expected_manifest_sha256'],
            'expected_wrapper_sha256': case['expected_wrapper_sha256'],
            'expected_public_plan_sha256': case['expected_public_plan_sha256'],
            'expected_binding': {'roster_sha256': roster_sha}}
        monkeypatch.setattr(session.lane_binding, 'resolve_lane_binding', lambda *a, **k: resolved)
        monkeypatch.setattr(session.roster, 'build_roster_snapshot', lambda *a, **k: (parts['roster'], roster_sha))
        seen = []
        monkeypatch.setattr(session.lane_query, 'query_public', lambda **kw: seen.append(kw) or {'status': 'SELECTION_ONLY'})
        result = session.query_session(lane='codex-lead-1', situation='synthetic', time=None,
            fleet_bytes=b'synthetic', expected_fleet_sha256=sha(b'synthetic'), binding_bytes=b'{}',
            expected_binding_sha256='a' * 64,
            **{key: case[key] for key in ('bundle_root', 'public_plan_path', 'executable',
                'expected_interpreter_sha256', 'interpreter_max_bytes', 'fixed_plan_bytes',
                'expected_fixed_plan_sha256', 'fixed_bundle_bytes', 'runtime_manifest_bytes', 'cwd',
                'timeout_seconds', 'max_output_bytes')}, enabled=True, _owner_guard=guard, _owner_context=context)
        assert result == {'status': 'SELECTION_ONLY'} and len(seen) == 1
        assert seen[0]['projection_bytes'] == parts['projection'] and seen[0]['policy_bytes'] == parts['policy']
        assert seen[0]['roster_bytes'] == parts['roster']
    assert pending.read_bytes() == before


@pytest.mark.parametrize('valid', [True, False])
def test_content_fixed_peer_validates_bundle_before_ack_or_receiving_query(monkeypatch, valid):
    plan = {'schema': 'wd.q4-fixed-plan.v4', 'backend': 'content-wrapper-v1',
        **{key: 'a' * 64 for key in fixed._CONTENT_FIXED_KEYS if key.endswith('_sha256')},
        'bundle_bytes': 1, 'runtime_root': r'C:\synthetic\runtime',
        'runtime_manifest_path': r'C:\synthetic\runtime-manifest.json', 'runtime_generation': 'a' * 40,
        'runtime_revision': 'b' * 40, 'approved_tasks': ['synthetic-task'], 'bundle_root': r'C:\synthetic\bundle',
        'bundle_commit': 'c' * 40, 'wrapper_path': r'C:\synthetic\bundle\Invoke-WdQ4Query.ps1',
        'public_plan_path': r'C:\synthetic\public-plan.json'}
    bundle = b'synthetic-content'; plan.update(bundle_bytes=len(bundle), bundle_sha256=sha(bundle))
    nonce = 'e' * 32
    frames = [fixed._canonical(plan), fixed._canonical({'schema':'wd.q4-fixed-challenge.v1','nonce':nonce}),
              bundle, b'synthetic-query']
    state = {'validated': False, 'ack': False, 'query_read': False}
    class Stub:
        def validate_bundle(self, raw):
            assert raw == bundle and not state['ack']
            if not valid:
                raise ValueError('pinned_input_mismatch')
            state['validated'] = True
        def __call__(self, _plan, _raw, _bundle, query):
            assert state['validated'] and state['ack'] and query == b'synthetic-query'
            return b'{}'
    monkeypatch.setattr(fixed, '_fixed_child_backend', lambda *_: Stub())
    def read(_limit):
        value = frames.pop(0)
        if value == b'synthetic-query':
            state['query_read'] = True
            assert state['validated'] and state['ack']
        return value
    def write(raw):
        if json.loads(raw)['status'] == 'BUNDLE_VERIFIED':
            assert state['validated']
            state['ack'] = True
    if valid:
        result = fixed._fixed_child_protocol(read, write, plan['peer_code_sha256'])
        assert fixed._fixed_unwrap(nonce, b'synthetic-query', result, 1024) == b'{}'
        assert state == {'validated': True, 'ack': True, 'query_read': True}
    else:
        with pytest.raises(ValueError):
            fixed._fixed_child_protocol(read, write, plan['peer_code_sha256'])
        assert state == {'validated': False, 'ack': False, 'query_read': False}


def test_only_wrapper_performs_full_source_rederivation(content_v5, content, monkeypatch):
    """The content helper validates pins around the wrapper without re-reading full sources itself."""
    from sandbox.mind.step2 import content_memory as cm
    from tests.tools.test_wd_q4_content_snapshot_query import run, cq, tcm
    plan, bundle, raw, _ = content_v5
    original = cm.validate_content_snapshot
    calls = []
    def record(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(cm, 'validate_content_snapshot', record)
    assert backend.validate_content_input(plan, bundle, raw, enabled=True)['status'] == 'VALIDATED_CONTENT'
    assert calls == []
    code, result = run(content)
    assert code == 0 and calls == [True]
    query = json.dumps({'situation': cq.SITUATION, 'time': tcm.ASK_TIME}).encode()
    backend.validate_content_result(plan, bundle, raw, query, json.dumps(result).encode(), enabled=True)
    assert calls == [True]
