"""Explicit staged lane-binding producer; synthetic bytes only, no live paths."""
import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from tools import wd_q4_lane_binding as consumer, wd_q4_secure_transport as transport
from tests.tools.test_wd_q4_fixed_result_adapter import make_case
from tests.tools.test_wd_q4_public_content_plan_writer import setup, lane, content


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def module():
    return importlib.import_module('tools.wd_q4_lane_binding_producer')


@pytest.fixture
def canonical_content_roster(monkeypatch):
    from sandbox.mind.step2 import test_content_route as route
    from tools import wd_q4_roster_from_fleet as producer
    original = route.strict_pins

    def canonical(*args, **kwargs):
        pins = original(*args, **kwargs)
        pins['roster_bytes'], pins['roster_sha256'] = producer.build_roster_snapshot(
            pins['roster_bytes'], pins['roster_sha256'], enabled=True)
        return pins

    monkeypatch.setattr(route, 'strict_pins', canonical)


@pytest.fixture
def content_case(canonical_content_roster, setup):
    from tools import wd_q4_public_content_plan_writer as writer
    written = writer.write_content_plan(enabled=True, **setup['arguments'])
    plan = json.loads(Path(written['public_plan_path']).read_bytes())
    plan['schema'] = 'wd.q4-public-wrapper-plan.v5'
    plan['config']['memory'] = setup['arguments']['memory_dir']
    plan['config']['descriptor'] = str(Path(setup['arguments']['memory_dir']) / 'descriptor.json')
    plan['config']['timeout'] = 480
    for key in ('message_source_path', 'message_generation_path', 'message_policy_path', 'doc_repo_root',
                'doc_policy_path', 'doc_git_executable', 'doc_git_sha256'):
        plan[key] = setup['arguments'][key]
    fleet_doc = json.loads(setup['lane']['content']['args']['roster_bytes'])
    fleet_doc.update(setup['arguments']['trusted_git'])
    fleet = raw(fleet_doc)
    wrapper = b'synthetic-wrapper'
    manifest = raw({'schema_version': 1, 'source_commit': 'a' * 40,
                    'files': {'wd-fleet.json': sha(fleet), 'Invoke-WdQ4Query.ps1': sha(wrapper)}})
    plan_raw = raw(plan)
    return dict(enabled=True, expected_commit='a' * 40, manifest_bytes=manifest,
                expected_manifest_sha256=sha(manifest), fleet_bytes=fleet, expected_fleet_sha256=sha(fleet),
                wrapper_bytes=wrapper, expected_wrapper_sha256=sha(wrapper), public_plan_bytes=plan_raw,
                expected_public_plan_sha256=sha(plan_raw), expected_binding=setup['lane']['call']['expected_binding'],
                memory_dir=setup['arguments']['memory_dir'])


def update_fleet(case, fleet):
    case['fleet_bytes'] = raw(fleet)
    case['expected_fleet_sha256'] = sha(case['fleet_bytes'])
    manifest = json.loads(case['manifest_bytes'])
    manifest['files']['wd-fleet.json'] = case['expected_fleet_sha256']
    case['manifest_bytes'] = raw(manifest)
    case['expected_manifest_sha256'] = sha(case['manifest_bytes'])


def update_plan(case, plan):
    case['public_plan_bytes'] = raw(plan)
    case['expected_public_plan_sha256'] = sha(case['public_plan_bytes'])


@pytest.mark.parametrize('field', ['git_executable', 'git_sha256'])
def test_documents_missing_independent_fleet_git_pin_refuse_before_subprocess(content_case, monkeypatch, field):
    fleet = json.loads(content_case['fleet_bytes'])
    del fleet[field]
    update_fleet(content_case, fleet)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected subprocess'))
    with pytest.raises(module().BindingProducerRefused):
        module().build_content_lane_binding(**content_case)


@pytest.mark.parametrize('field', ['doc_git_executable', 'doc_git_sha256'])
def test_attacker_selected_document_git_refuses_before_subprocess(content_case, monkeypatch, field):
    plan = json.loads(content_case['public_plan_bytes'])
    plan[field] = sys.executable if field == 'doc_git_executable' else 'f' * 64
    update_plan(content_case, plan)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected subprocess'))
    with pytest.raises(module().BindingProducerRefused):
        module().build_content_lane_binding(**content_case)


def test_attacker_selected_path_and_matching_hash_refuse_before_subprocess(content_case, monkeypatch):
    plan = json.loads(content_case['public_plan_bytes'])
    plan['doc_git_executable'] = sys.executable
    plan['doc_git_sha256'] = sha(Path(sys.executable).read_bytes())
    update_plan(content_case, plan)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected subprocess'))
    with pytest.raises(module().BindingProducerRefused):
        module().build_content_lane_binding(**content_case)


@pytest.mark.parametrize('field,value', [('git_executable', 'relative-git'), ('git_sha256', 'invalid')])
def test_malformed_fleet_git_refuses_before_subprocess(content_case, monkeypatch, field, value):
    fleet = json.loads(content_case['fleet_bytes'])
    fleet[field] = value
    update_fleet(content_case, fleet)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected subprocess'))
    with pytest.raises(module().BindingProducerRefused):
        module().build_content_lane_binding(**content_case)


@pytest.mark.parametrize('field,change', [('memory', 'missing'), ('memory', 'different'),
                                          ('descriptor', 'missing'), ('descriptor', 'different')])
def test_content_binding_requires_exact_runtime_paths(content_case, monkeypatch, field, change):
    from tools import wd_q4_public_content_plan_writer as writer
    monkeypatch.setattr(writer, '_content_plan', lambda plan, *a, **kw: dict(plan))
    plan = json.loads(content_case['public_plan_bytes'])
    if change == 'missing':
        del plan['config'][field]
    else:
        plan['config'][field] = str(Path(content_case['memory_dir']) / 'wrong')
    update_plan(content_case, plan)
    with pytest.raises(module().BindingProducerRefused):
        module().build_content_lane_binding(**content_case)


def test_content_binding_requires_derived_v5_schema(content_case, monkeypatch):
    from tools import wd_q4_public_content_plan_writer as writer
    monkeypatch.setattr(writer, '_content_plan', lambda plan, *a, **kw:
                        dict(plan, schema='wd.q4-public-wrapper-plan.v4'))
    with pytest.raises(module().BindingProducerRefused):
        module().build_content_lane_binding(**content_case)


def test_projection_refuses_after_pinned_source_mutation(content_case):
    plan = json.loads(content_case['public_plan_bytes'])
    path = Path(plan['message_source_path'])
    raw_data = bytearray(path.read_bytes())
    raw_data[0] = ord('!')
    path.write_bytes(raw_data)
    with pytest.raises(module().BindingProducerRefused):
        module().build_content_lane_binding(**content_case)


def test_message_only_binding_succeeds_without_fleet_git(content_case, setup, tmp_path, monkeypatch):
    from sandbox.mind.step2 import test_content_route as route_fixture
    from tools import wd_q4_public_content_plan_writer as writer
    route = setup['route']
    original_decision = json.loads(route['decision'])
    decision, decision_sha = route_fixture.decision_of(
        prefix=original_decision['source_prefix'], message_pin=route['messages']['policy_sha256'], doc_pin=None)
    built, memory = route_fixture.build_route(tmp_path, route, name='message-only', documents=None,
                                              decision_bytes=decision, decision_sha256=decision_sha,
                                              trusted_git=None)
    decision_path = tmp_path / 'message-only-decision.json'
    decision_path.write_bytes(decision)
    v1 = dict(setup['v1'])
    v1['config'] = dict(v1['config'], descriptor_sha256=built['descriptor_sha256'], memory=str(memory),
                        descriptor=str(memory / 'descriptor.json'), timeout=480)
    v1_raw = raw(v1)
    v1_path = tmp_path / 'message-only-v1.json'
    v1_path.write_bytes(v1_raw)
    out = tmp_path / 'message-only-plan'
    out.mkdir()
    opts = dict(setup['arguments'], public_plan_path=str(v1_path), expected_public_plan_sha256=sha(v1_raw),
                output_dir=str(out), memory_dir=str(memory), content_decision_path=str(decision_path),
                content_decision_sha256=decision_sha, doc_repo_root=None, doc_policy_path=None,
                doc_git_executable=None, doc_git_sha256=None, trusted_git=None)
    written = writer.write_content_plan(enabled=True, **opts)
    plan_raw = Path(written['public_plan_path']).read_bytes()
    plan = json.loads(plan_raw)
    fleet = json.loads(content_case['fleet_bytes'])
    del fleet['git_executable'], fleet['git_sha256']
    update_fleet(content_case, fleet)
    content_case.update(public_plan_bytes=plan_raw, expected_public_plan_sha256=sha(plan_raw), memory_dir=str(memory),
                        expected_binding={key: (plan['config']['descriptor_sha256'] if key == 'descriptor_sha256'
                                                 else plan[key]) for key in module()._CONTENT_PINS})
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected subprocess'))
    result = module().build_content_lane_binding(**content_case)
    assert json.loads(result['binding_bytes'])['binding'] == content_case['expected_binding']


def test_content_binding_uses_seven_independent_pins_and_all_five_lanes(content_case):
    result = module().build_content_lane_binding(**content_case)
    document = json.loads(result['binding_bytes'])
    assert result['binding_sha256'] == sha(result['binding_bytes'])
    assert document['binding'] == content_case['expected_binding']
    assert set(document['lanes']) == {'codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5'}
    assert document['public_plan_sha256'] == content_case['expected_public_plan_sha256']


def test_content_fixture_preserves_a_bounded_timeout_ladder(content_case, tmp_path):
    from tests.tools.test_wd_q4_shared_descriptor_producer import kwargs, build
    kw = kwargs(tmp_path)
    kw['expected_binding'] = dict(content_case['expected_binding'])
    kw['timeout_seconds'] = 540
    descriptor = json.loads(build(kw)['descriptor_bytes'])
    inner = json.loads(content_case['public_plan_bytes'])['config']['timeout']
    assert type(inner) is int and type(descriptor['timeout_seconds']) is int
    assert 200.49 < inner < descriptor['timeout_seconds'] <= 1800


def test_each_content_pin_is_required_and_bound_to_the_plan(content_case):
    for key in content_case['expected_binding']:
        for change in ('missing', 'wrong', 'bad_value'):
            altered = dict(content_case['expected_binding'])
            if change == 'missing':
                del altered[key]
            else:
                altered[key] = 'f' * 64 if change == 'wrong' else 'bad'
            with pytest.raises(module().BindingProducerRefused):
                module().build_content_lane_binding(**dict(content_case, expected_binding=altered))


def test_content_binding_off_is_default_and_never_inspects_inputs():
    class NoTouch:
        def __getattribute__(self, name):
            raise AssertionError('disabled path inspected input')
    assert module().build_content_lane_binding(manifest_bytes=NoTouch()) == {'status': 'OFF'}


def test_content_binding_does_not_return_source_or_projection_contents(content_case):
    result = module().build_content_lane_binding(**content_case)
    doc = json.loads(result['binding_bytes'])
    assert set(doc) == {'schema', 'bundle_commit', 'manifest_sha256', 'wrapper_sha256',
                        'public_plan_sha256', 'binding', 'lanes'}
    assert b'content_decision_path' not in result['binding_bytes']
    assert b'message_source_path' not in result['binding_bytes']


@pytest.mark.parametrize('change', ['missing', 'extra', 'wrong', 'bad_value'])
def test_content_binding_refuses_changed_pins(content_case, change):
    pins = dict(content_case['expected_binding'])
    if change == 'missing': del pins['memory_sha256']
    elif change == 'extra': pins['unexpected_sha256'] = 'a' * 64
    elif change == 'wrong': pins['memory_sha256'] = 'a' * 64
    else: pins['memory_sha256'] = 'invalid'
    content_case['expected_binding'] = pins
    with pytest.raises(module().BindingProducerRefused):
        module().build_content_lane_binding(**content_case)


@pytest.mark.parametrize('field', ['message_policy_path', 'doc_git_sha256', 'memory_sha256', 'projection_sha256'])
def test_content_binding_refuses_rehashed_changed_source_or_artifact_pin(content_case, field):
    plan = json.loads(content_case['public_plan_bytes'])
    plan[field] = 'a' * 64 if field.endswith('sha256') else str(Path(plan[field]).with_name('missing-policy'))
    content_case['public_plan_bytes'] = raw(plan)
    content_case['expected_public_plan_sha256'] = sha(content_case['public_plan_bytes'])
    with pytest.raises(module().BindingProducerRefused):
        module().build_content_lane_binding(**content_case)


@pytest.mark.parametrize('change', ['missing_source', 'extra_source', 'old_version'])
def test_content_binding_requires_the_exact_v5_plan(content_case, change):
    plan = json.loads(content_case['public_plan_bytes'])
    if change == 'missing_source': del plan['message_generation_path']
    elif change == 'extra_source': plan['extra_source'] = 'untrusted'
    else: plan['schema'] = 'wd.q4-public-wrapper-plan.v4'
    content_case['public_plan_bytes'] = raw(plan)
    content_case['expected_public_plan_sha256'] = sha(content_case['public_plan_bytes'])
    with pytest.raises(module().BindingProducerRefused):
        module().build_content_lane_binding(**content_case)


@pytest.fixture
def case():
    synth = make_case(canonical_roster=True)
    roster = json.loads(synth['roster_bytes'])
    fleet = raw({'lanes': roster['lanes'], 'tools_supervisor': roster['tools_supervisor']})
    wrapper = b'# synthetic-only wrapper; never executed'
    manifest = raw({'schema_version': 1, 'source_commit': 'a' * 40,
                    'files': {'wd-fleet.json': sha(fleet), 'Invoke-WdQ4Query.ps1': sha(wrapper)}})
    active = synth['active_artifact_bytes']
    plan = {'schema': 'wd.q4-public-wrapper-plan.v1',
            'config': {'descriptor_sha256': sha(active['descriptor.json'])},
            'root': 'synthetic-root', 'policy_path': 'synthetic-policy',
            'roster_path': 'synthetic-roster', 'public_policy_path': 'synthetic-public-policy',
            'pointer_sha256': synth['expected_pointer_sha256'],
            'source_anchor_sha256': synth['expected_source_sha256'],
            'encoder_sha256': synth['expected_encoder_sha256'],
            'policy_sha256': synth['policy_sha256'], 'roster_sha256': synth['roster_sha256'],
            'public_policy_sha256': synth['expected_public_policy_sha256']}
    snapshot = transport.fixed_snapshot_bundle(**{k: synth[k] for k in (
        'pointer_bytes', 'policy_bytes', 'roster_bytes', 'public_policy_bytes', 'active_artifact_bytes',
        'history_bytes', 'cell_snapshots')})
    plan_raw = raw(plan)
    return dict(enabled=True, expected_commit='a' * 40, manifest_bytes=manifest,
                expected_manifest_sha256=sha(manifest), fleet_bytes=fleet, expected_fleet_sha256=sha(fleet),
                wrapper_bytes=wrapper, expected_wrapper_sha256=sha(wrapper), public_plan_bytes=plan_raw,
                expected_public_plan_sha256=sha(plan_raw), snapshot_bytes=snapshot,
                expected_snapshot_sha256=sha(snapshot))


def test_valid_pinned_snapshot_produces_binding_for_each_real_consumer(case):
    result = module().build_lane_binding(**case)
    assert set(result) == {'binding_bytes', 'binding_sha256'}
    assert result['binding_sha256'] == sha(result['binding_bytes'])
    document = json.loads(result['binding_bytes'])
    assert document['bundle_commit'] == case['expected_commit']
    assert document['manifest_sha256'] == case['expected_manifest_sha256']
    assert document['wrapper_sha256'] == case['expected_wrapper_sha256']
    assert document['public_plan_sha256'] == case['expected_public_plan_sha256']
    assert len(document['binding']) == 8 and len(document['lanes']) == 5
    for lane in document['lanes']:
        pins = consumer.resolve_lane_binding(case['fleet_bytes'], case['expected_fleet_sha256'],
            result['binding_bytes'], result['binding_sha256'], lane=lane, enabled=True)
        assert pins['expected_binding'] == document['binding']


def test_default_off_never_inspects_inputs():
    class NoTouch:
        def __getattribute__(self, name):
            raise AssertionError('disabled path inspected input')
    assert module().build_lane_binding(manifest_bytes=NoTouch()) == {'status': 'OFF'}


@pytest.mark.parametrize('field', ['expected_commit', 'expected_manifest_sha256', 'expected_fleet_sha256',
    'expected_wrapper_sha256', 'expected_public_plan_sha256', 'expected_snapshot_sha256'])
def test_mismatched_explicit_pins_are_refused(case, field):
    case[field] = ('f' * 40) if field == 'expected_commit' else ('f' * 64)
    with pytest.raises(module().BindingProducerRefused):
        module().build_lane_binding(**case)


@pytest.mark.parametrize('field', ['pointer_sha256', 'source_anchor_sha256', 'encoder_sha256',
    'policy_sha256', 'roster_sha256', 'public_policy_sha256', 'descriptor_sha256'])
def test_plan_changed_and_rehashed_still_refuses_snapshot_mismatch(case, field):
    plan = json.loads(case['public_plan_bytes'])
    if field == 'descriptor_sha256': plan['config'][field] = 'f' * 64
    else: plan[field] = 'f' * 64
    case['public_plan_bytes'] = raw(plan)
    case['expected_public_plan_sha256'] = sha(case['public_plan_bytes'])
    with pytest.raises(module().BindingProducerRefused):
        module().build_lane_binding(**case)


def test_binding_output_is_deterministic_and_contains_no_snapshot_content(case):
    first = module().build_lane_binding(**case)
    second = module().build_lane_binding(**case)
    assert first == second
    assert b'public-task' not in first['binding_bytes']
    assert b'fake-index' not in first['binding_bytes']


@pytest.mark.parametrize('flag', [None, 0, 1, 'true', [], {}])
def test_only_bool_true_enables(flag, case):
    case['enabled'] = flag
    with pytest.raises(module().BindingProducerRefused):
        module().build_lane_binding(**case)


@pytest.mark.parametrize('mutation', ['schema', 'commit', 'fleet_entry', 'wrapper_entry', 'duplicate'])
def test_rehashed_manifest_must_still_bind_exact_candidate(case, mutation):
    doc = json.loads(case['manifest_bytes'])
    if mutation == 'schema': doc['schema_version'] = True
    elif mutation == 'commit': doc['source_commit'] = 'f' * 40
    elif mutation == 'fleet_entry': doc['files']['wd-fleet.json'] = 'f' * 64
    elif mutation == 'wrapper_entry': doc['files']['Invoke-WdQ4Query.ps1'] = 'f' * 64
    if mutation == 'duplicate':
        case['manifest_bytes'] = b'{"schema_version":1,"schema_version":1}'
    else:
        case['manifest_bytes'] = raw(doc)
    case['expected_manifest_sha256'] = sha(case['manifest_bytes'])
    with pytest.raises(module().BindingProducerRefused):
        module().build_lane_binding(**case)


@pytest.mark.parametrize('target', ['descriptor.json', 'projection.json', 'memory.json', 'build_receipt.json'])
def test_rehashed_snapshot_rejects_changed_active_artifact(case, target):
    parts = transport._fixed_bundle_decode(case['snapshot_bytes'])
    parts['active_artifact_bytes'][target] += b' '
    case['snapshot_bytes'] = transport.fixed_snapshot_bundle(**parts)
    case['expected_snapshot_sha256'] = sha(case['snapshot_bytes'])
    with pytest.raises(module().BindingProducerRefused):
        module().build_lane_binding(**case)


def test_rehashed_different_fleet_cannot_reuse_old_snapshot_roster(case):
    fleet = json.loads(case['fleet_bytes'])
    fleet['lanes'][0]['agent_uuid'] = '99999999-9999-4999-8999-999999999999'
    case['fleet_bytes'] = raw(fleet)
    case['expected_fleet_sha256'] = sha(case['fleet_bytes'])
    manifest = json.loads(case['manifest_bytes'])
    manifest['files']['wd-fleet.json'] = case['expected_fleet_sha256']
    case['manifest_bytes'] = raw(manifest)
    case['expected_manifest_sha256'] = sha(case['manifest_bytes'])
    with pytest.raises(module().BindingProducerRefused):
        module().build_lane_binding(**case)


def test_refusal_has_no_exception_chain_or_snapshot_in_outer_frame(case):
    secret = b'synthetic-private-binding-input-sentinel'
    case['snapshot_bytes'] += secret
    case['expected_snapshot_sha256'] = sha(case['snapshot_bytes'])
    with pytest.raises(module().BindingProducerRefused) as caught:
        module().build_lane_binding(**case)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    tb = caught.value.__traceback__
    own = []
    while tb:
        if tb.tb_frame.f_code.co_name == 'build_lane_binding':
            own.append(repr(tb.tb_frame.f_locals))
        tb = tb.tb_next
    assert own and all(secret.decode() not in frame for frame in own)
