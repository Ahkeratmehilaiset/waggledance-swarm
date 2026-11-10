"""Synthetic C2 binding preparation; no live state, model or activation."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.tools.test_wd_q4_staged_lane_query import staged_producer  # noqa: F401 - pytest fixture
from tools import wd_q4_staged_composer as composer


def args(case):
    return {key: case[key] for key in ('expected_commit', 'manifest_bytes', 'expected_manifest_sha256',
        'fleet_bytes', 'expected_fleet_sha256', 'wrapper_bytes', 'expected_wrapper_sha256',
        'public_plan_bytes', 'expected_public_plan_sha256')}


def test_staged_card_composer_defaults_off_without_touching_inputs():
    class Untouchable:
        def __getattribute__(self, name):
            raise AssertionError('OFF inspected input')
    assert composer.compose_staged_card(public_plan_path=Untouchable()) == {'status': 'OFF'}


@pytest.fixture
def materialize(staged_producer, tmp_path, monkeypatch):
    if os.name != 'nt':
        pytest.skip('staged card caller reads drive-absolute Windows paths')
    from tests.tools.test_wd_q4_shared_descriptor_producer import kwargs as descriptor_kwargs
    from tools import wd_q4_fixed_plan_producer as fixed_producer
    sha = lambda raw: hashlib.sha256(raw).hexdigest()
    store = tmp_path / 'store'
    store.mkdir()
    active = store / ('f' * 40)
    active.mkdir()
    bundle = store / ('a' * 40)
    bundle.mkdir()
    stage_root = tmp_path / 'generations'
    stage_root.mkdir()
    stage = stage_root / 'stage'
    stage.mkdir()
    state = tmp_path / 'current.json'
    state_bytes = json.dumps({'schema_version': 1, 'active_bundle': str(active),
        'source_commit': 'f'*40, 'final_commit': 'f'*40,
        'manifest_sha256': 'D'*64, 'final_manifest_sha256': 'D'*64}).encode('ascii')
    state.write_bytes(state_bytes)
    monkeypatch.setattr(composer, '_STATE_POINTER', state)
    wrapper = staged_producer['wrapper_bytes']
    fleet = staged_producer['fleet_bytes']
    (bundle / 'Invoke-WdQ4Query.ps1').write_bytes(wrapper)
    (bundle / 'wd-fleet.json').write_bytes(fleet)
    tools = bundle / 'tools-bootstrap' / 'tools'
    tools.mkdir(parents=True)
    peer, backend = b'synthetic-peer', b'synthetic-backend'
    (tools / 'wd_q4_secure_transport.py').write_bytes(peer)
    (tools / 'wd_q4_staged_fixed_backend.py').write_bytes(backend)
    manifest = json.dumps({'schema_version': 1, 'source_commit': 'a'*40, 'files': {
        'wd-fleet.json': sha(fleet), 'Invoke-WdQ4Query.ps1': sha(wrapper),
        'tools-bootstrap/tools/wd_q4_secure_transport.py': sha(peer),
        'tools-bootstrap/tools/wd_q4_staged_fixed_backend.py': sha(backend)}}).encode()
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    descriptor = descriptor_kwargs(tmp_path)
    options = {key: descriptor[key] for key in composer.cli._DESCRIPTOR_KEYS}
    plan = json.loads(staged_producer['public_plan_bytes'])
    options['outer']['runtime-revision'] = plan['config']['runtime_revision']
    runtime_tools = Path(options['outer']['runtime-root']) / 'assets' / 'code' / 'tools'
    runtime_tools.mkdir(parents=True)
    runtime_files = []
    for name, raw in (('wd_q4_secure_transport.py', peer), ('wd_q4_staged_fixed_backend.py', backend)):
        (runtime_tools / name).write_bytes(raw)
        runtime_files.append({'path': 'assets/code/tools/' + name, 'size': len(raw), 'sha256': sha(raw)})
    runtime_manifest = json.dumps({'schema': 'wd.q4-runtime-manifest.v1',
        'generation': options['outer']['runtime-generation'], 'revision': options['outer']['runtime-revision'],
        'files': runtime_files}, sort_keys=True).encode()
    Path(options['runtime_manifest_path']).write_bytes(runtime_manifest)
    options['runtime_manifest_sha256'] = sha(runtime_manifest)
    plan['config'].update(interpreter=options['interpreter'], interpreter_sha256=options['interpreter_sha256'],
        runtime_root=options['outer']['runtime-root'], runtime_manifest=options['runtime_manifest_path'],
        runtime_manifest_sha256=options['runtime_manifest_sha256'],
        runtime_generation=options['outer']['runtime-generation'], verifier_sha256=options['outer']['verifier-sha256'])
    plan_raw = json.dumps(plan, sort_keys=True).encode()
    plan_path = tmp_path / 'public-plan-v3.json'
    plan_path.write_bytes(plan_raw)
    prep_args = args(staged_producer)
    prep_args.update(manifest_bytes=manifest, expected_manifest_sha256=sha(manifest),
                     public_plan_bytes=plan_raw, expected_public_plan_sha256=sha(plan_raw))
    prep = composer._prepare_staged_card_binding(**prep_args)
    pins = json.loads(prep['binding_bytes'])['binding']
    options.update(bundle_root=str(bundle), public_plan=str(plan_path), expected_binding=pins)
    fixed = {'schema': 'wd.q4-fixed-plan.v3', 'backend': 'staged-card-wrapper-v1',
        'interpreter_sha256': options['interpreter_sha256'], 'peer_code_sha256': sha(peer),
        'runtime_manifest_sha256': options['runtime_manifest_sha256'], 'bundle_sha256': prep['bundle_sha256'],
        'bundle_bytes': len(prep['bundle_bytes']), 'verifier_sha256': options['outer']['verifier-sha256'],
        'runtime_root': options['outer']['runtime-root'], 'runtime_manifest_path': options['runtime_manifest_path'],
        'runtime_generation': options['outer']['runtime-generation'], 'runtime_revision': options['outer']['runtime-revision'],
        'approved_tasks': prep['approved_tasks'], 'expected_pointer_sha256': pins['pointer_sha256'],
        'policy_sha256': pins['policy_sha256'], 'roster_sha256': pins['roster_sha256'],
        'expected_public_policy_sha256': pins['public_policy_sha256'], 'expected_source_sha256': pins['source_anchor_sha256'],
        'expected_encoder_sha256': pins['encoder_sha256'], 'card_decision_sha256': pins['card_decision_sha256'],
        'card_policy_sha256': pins['card_policy_sha256'], 'wrapper_sha256': sha(wrapper),
        'deployment_manifest_sha256': sha(manifest), 'public_plan_sha256': sha(plan_raw),
        'host_sha256': 'f'*64, 'backend_sha256': sha(backend), 'bundle_root': str(bundle),
        'bundle_commit': 'a'*40, 'wrapper_path': str(bundle / 'Invoke-WdQ4Query.ps1'),
        'public_plan_path': str(plan_path)}
    fixed_options = {key: value for key, value in fixed.items() if key not in ('schema', 'backend')}
    fixed_raw = fixed_producer.build_staged_fixed_plan(enabled=True, **fixed_options)['plan_bytes']
    return {'enabled': True, 'stage_dir': str(stage), 'generation': 'b'*40, 'expected_bundle': str(bundle),
        'expected_commit': 'a'*40, 'expected_manifest_sha256': sha(manifest).upper(),
        'public_plan_path': str(plan_path), 'expected_public_plan_sha256': sha(plan_raw),
        'fixed_plan_bytes': fixed_raw, 'expected_fixed_plan_sha256': sha(fixed_raw),
        'expected_host_sha256': 'f'*64, 'descriptor_options': options, 'generation_root': str(stage_root),
        'bundle_store_root': str(store), 'active_bundle_root': str(active), 'expected_state_sha256': sha(state_bytes)}


def test_materializes_staged_card_descriptor_with_matching_v3_pins(materialize):
    result = composer.compose_staged_card(**materialize)
    assert result['status'] == 'PREPARED'
    descriptor = json.loads(Path(result['descriptor_path']).read_bytes())
    assert descriptor['expected_binding'] == materialize['descriptor_options']['expected_binding']
    assert json.loads(Path(result['pointer_path']).read_bytes())['descriptor_sha256'] == result['descriptor_sha256']
    assert not (Path(materialize['bundle_store_root']).parent / 'WD_Q4_DESCRIPTOR_CURRENT.json').exists()


def test_refuses_descriptor_interpreter_limit_exceeding_lane_caller(materialize):
    from tools.wd_q4_lane_caller_cli import MAX_INTERPRETER

    case = dict(materialize)
    case['descriptor_options'] = dict(case['descriptor_options'], interpreter_max_bytes=MAX_INTERPRETER + 1)
    with pytest.raises(composer.ComposeRefused):
        composer.compose_staged_card(**case)
    assert not any(Path(case['stage_dir']).iterdir())


def test_real_writer_to_staged_composer_materializes_matching_descriptor(materialize, tmp_path):
    from tools import wd_q4_public_card_plan_writer as writer
    from tools import wd_q4_lane_link as link
    from tools import wd_q4_fixed_plan_producer as fixed_producer

    case = dict(materialize)
    staged = json.loads(Path(case['public_plan_path']).read_bytes())
    v1 = {key: value for key, value in staged.items()
          if key in link._PUBLIC_PLAN_SCHEMAS['wd.q4-public-wrapper-plan.v1']}
    v1['schema'] = 'wd.q4-public-wrapper-plan.v1'
    v1_raw = json.dumps(v1, sort_keys=True, separators=(',', ':')).encode()
    v1_path = tmp_path / 'base-public-plan.json'
    v1_path.write_bytes(v1_raw)
    out = tmp_path / 'writer-out'
    out.mkdir()
    produced = writer.write_staged_card_plan(enabled=True, public_plan_path=str(v1_path),
        expected_public_plan_sha256=hashlib.sha256(v1_raw).hexdigest(), output_dir=str(out),
        **{key: staged[key] for key in ('pointer_path', 'card_decision_path', 'card_policy_path',
                                         'source_path', 'generation_path')})
    assert produced['status'] == 'WRITTEN'
    case['public_plan_path'], case['expected_public_plan_sha256'] = produced['public_plan_path'], produced['public_plan_sha256']
    case['descriptor_options'] = dict(case['descriptor_options'], public_plan=produced['public_plan_path'])
    fixed = json.loads(case['fixed_plan_bytes'])
    fixed.update(public_plan_path=produced['public_plan_path'], public_plan_sha256=produced['public_plan_sha256'])
    fixed_options = {key: value for key, value in fixed.items() if key not in ('schema', 'backend')}
    case['fixed_plan_bytes'] = fixed_producer.build_staged_fixed_plan(enabled=True, **fixed_options)['plan_bytes']
    case['expected_fixed_plan_sha256'] = hashlib.sha256(case['fixed_plan_bytes']).hexdigest()
    result = composer.compose_staged_card(**case)
    assert result['status'] == 'PREPARED'
    descriptor = json.loads(Path(result['descriptor_path']).read_bytes())
    assert descriptor['public_plan'] == produced['public_plan_path']
    assert descriptor['expected_binding'] == case['descriptor_options']['expected_binding']
    assert descriptor['expected_binding']['card_decision_sha256'] == produced['card_decision_sha256']
    assert descriptor['expected_binding']['card_policy_sha256'] == produced['card_policy_sha256']


@pytest.mark.parametrize('field', ['expected_pointer_sha256', 'policy_sha256', 'roster_sha256',
    'expected_public_policy_sha256', 'expected_source_sha256', 'expected_encoder_sha256',
    'card_decision_sha256', 'card_policy_sha256', 'bundle_sha256', 'host_sha256',
    'public_plan_sha256', 'runtime_revision', 'backend_sha256'])
def test_wrong_fixed_plan_pins_refuse_before_any_staging(materialize, field):
    case = dict(materialize)
    fixed = json.loads(case['fixed_plan_bytes'])
    fixed[field] = ('0' if fixed[field][0] != '0' else '1') + fixed[field][1:]
    case['fixed_plan_bytes'] = composer.transport._canonical(fixed)
    case['expected_fixed_plan_sha256'] = hashlib.sha256(case['fixed_plan_bytes']).hexdigest()
    with pytest.raises(composer.ComposeRefused, match='^input_refused$'):
        composer.compose_staged_card(**case)
    assert not list(Path(case['stage_dir']).iterdir())


def test_changed_plan_after_validation_refuses_before_writes(materialize, monkeypatch):
    path = Path(materialize['public_plan_path'])
    original = composer._prepare_staged_card_binding
    calls = []
    def changed(**kwargs):
        result = original(**kwargs)
        calls.append(True)
        if len(calls) == 2:
            path.write_bytes(path.read_bytes() + b' ')
        return result
    monkeypatch.setattr(composer, '_prepare_staged_card_binding', changed)
    with pytest.raises(composer.ComposeRefused, match='^input_refused$'):
        composer.compose_staged_card(**materialize)
    assert not list(Path(materialize['stage_dir']).iterdir())


def test_failure_after_first_attempt_is_incomplete_without_retry(materialize, monkeypatch):
    from tools import wd_q4_descriptor_materializer as writer
    original = writer.materialize_descriptor
    def interrupted(**kwargs):
        original(**kwargs)
        raise RuntimeError('synthetic')
    monkeypatch.setattr(writer, 'materialize_descriptor', interrupted)
    with pytest.raises(composer.ComposeIncomplete, match='^staging_incomplete$'):
        composer.compose_staged_card(**materialize)
    assert list(Path(materialize['stage_dir']).iterdir())
    with pytest.raises(composer.ComposeRefused, match='^input_refused$'):
        composer.compose_staged_card(**materialize)


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
