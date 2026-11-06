"""Synthetic generation selection; not a production or identity proof."""
import hashlib
import json
from pathlib import Path

import pytest

from tools import wd_q4_lane_binding_producer as binding_producer
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_secure_transport as transport
from tests.tools.test_wd_q4_fixed_result_adapter import make_case


def enc(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def case(tmp_path):
    synth = make_case(canonical_roster=True)
    roster = json.loads(synth['roster_bytes'])
    fleet = enc({'lanes': roster['lanes'], 'tools_supervisor': roster['tools_supervisor']})
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    wrapper = b'synthetic-wrapper'
    (bundle / 'wd-fleet.json').write_bytes(fleet)
    (bundle / 'Invoke-WdQ4Query.ps1').write_bytes(wrapper)
    manifest = enc({'schema_version': 1, 'source_commit': 'a' * 40,
                    'files': {'wd-fleet.json': sha(fleet), 'Invoke-WdQ4Query.ps1': sha(wrapper)}})
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    index = tmp_path / 'index'
    index.mkdir()
    (index / 'q4-latest.json').write_bytes(synth['pointer_bytes'])
    snapshot = transport.fixed_snapshot_bundle(**{key: synth[key] for key in (
        'pointer_bytes', 'policy_bytes', 'roster_bytes', 'public_policy_bytes',
        'active_artifact_bytes', 'history_bytes', 'cell_snapshots')})
    pointer = json.loads(synth['pointer_bytes'])
    pins = {'pointer_sha256': synth['expected_pointer_sha256'], 'source_anchor_sha256': synth['expected_source_sha256'],
            'encoder_sha256': synth['expected_encoder_sha256'], 'policy_sha256': synth['policy_sha256'],
            'roster_sha256': synth['roster_sha256'], 'public_policy_sha256': synth['expected_public_policy_sha256'],
            'descriptor_sha256': pointer['artifacts']['descriptor_sha256']}
    public = enc({'schema': 'wd.q4-public-wrapper-plan.v1', 'root': str(index),
                  'policy_path': str(index / 'policy'), 'roster_path': str(index / 'roster'),
                  'public_policy_path': str(index / 'public'), 'config': {'descriptor_sha256': pins['descriptor_sha256']},
                  **{k: v for k, v in pins.items() if k != 'descriptor_sha256'}})
    made = binding_producer.build_lane_binding(enabled=True, expected_commit='a' * 40,
        manifest_bytes=manifest, expected_manifest_sha256=sha(manifest), fleet_bytes=fleet,
        expected_fleet_sha256=sha(fleet), wrapper_bytes=wrapper, expected_wrapper_sha256=sha(wrapper),
        public_plan_bytes=public, expected_public_plan_sha256=sha(public), snapshot_bytes=snapshot,
        expected_snapshot_sha256=sha(snapshot))
    runtime_bytes = b'synthetic-runtime-manifest'
    (tmp_path / 'runtime-manifest').write_bytes(runtime_bytes)
    fixed = enc({'schema': 'wd.q4-fixed-plan.v2', 'backend': 'public-snapshot-v1',
        'interpreter_sha256': '1' * 64, 'peer_code_sha256': '2' * 64, 'runtime_manifest_sha256': sha(runtime_bytes),
        'bundle_sha256': sha(snapshot), 'bundle_bytes': len(snapshot), 'verifier_sha256': '4' * 64,
        'runtime_root': str(tmp_path / 'runtime'), 'runtime_manifest_path': str(tmp_path / 'runtime-manifest'),
        'runtime_generation': '5' * 40, 'runtime_revision': '6' * 40, 'approved_tasks': sorted(synth['approved_tasks']),
        'expected_pointer_sha256': pins['pointer_sha256'], 'expected_source_sha256': pins['source_anchor_sha256'],
        'expected_encoder_sha256': pins['encoder_sha256'], 'policy_sha256': pins['policy_sha256'],
        'roster_sha256': pins['roster_sha256'], 'expected_public_policy_sha256': pins['public_policy_sha256']})
    config = {}
    root = tmp_path / 'generations'
    for gen in ('b' * 40, 'c' * 40):
        directory = root / gen
        directory.mkdir(parents=True)
        for name, blob in (('lane-binding.v1.json', made['binding_bytes']), ('fixed-plan.v2.json', fixed),
                           ('public-snapshot.v1.json', snapshot), ('public-plan.json', public)):
            (directory / name).write_bytes(blob)
        descriptor = {'schema': 'synthetic-descriptor', 'bundle_root': str(bundle), 'fleet_sha256': sha(fleet),
            'public_plan': str(directory / 'public-plan.json'), 'binding_path': str(directory / 'lane-binding.v1.json'),
            'binding_sha256': made['binding_sha256'], 'fixed_plan_path': str(directory / 'fixed-plan.v2.json'),
            'fixed_plan_sha256': sha(fixed), 'snapshot_path': str(directory / 'public-snapshot.v1.json'),
            'snapshot_sha256': sha(snapshot), 'runtime_manifest_path': str(tmp_path / 'runtime-manifest'),
            'runtime_manifest_sha256': sha(runtime_bytes), 'expected_binding': json.loads(made['binding_bytes'])['binding'],
            'interpreter': 'synthetic-stable-interpreter'}
        descriptor_raw = enc(descriptor)
        path = directory / 'shared-session-config.v2.json'
        path.write_bytes(descriptor_raw)
        inputs = {'binding_bytes': made['binding_bytes'], 'fixed_plan_bytes': fixed, 'fixed_bundle_bytes': snapshot,
                  'runtime_manifest_bytes': runtime_bytes}
        config[gen] = (descriptor, descriptor_raw, sha(descriptor_raw), fleet, descriptor['expected_binding'], inputs)
    a, b = ('b' * 40, 'c' * 40)
    return {'root': root, 'base_path': str(root / a / 'shared-session-config.v2.json'), 'base': config[a],
            'candidate_path': str(root / b / 'shared-session-config.v2.json'), 'candidate': config[b],
            'manifest_sha': sha(manifest), 'index': index, 'config': config}


def do_publish(c):
    return live.publish(enabled=True, baseline_path=c['base_path'], baseline_sha256=c['base'][2], baseline=c['base'],
        manifest_sha256=c['manifest_sha'], candidate_path=c['candidate_path'], candidate_sha256=c['candidate'][2],
        candidate=c['candidate'])


def do_select(c):
    def loader(path, digest):
        item = c['config'][Path(path).parent.name]
        assert sha(Path(path).read_bytes()) == digest == item[2]
        return item
    return live.select(enabled=True, baseline_path=c['base_path'], baseline_sha256=c['base'][2], baseline=c['base'],
                       manifest_sha256=c['manifest_sha'], load_configuration=loader)


def test_published_candidate_selects_same_real_binding_and_fixed_plan(case):
    result = do_publish(case)
    chosen = do_select(case)
    assert result['status'] == 'PUBLISHED' and chosen['status'] == 'SELECTED'
    assert chosen['configuration'][2] == case['candidate'][2]
    assert chosen['publication_sha256'] == result['publication_sha256']
    live.recheck(chosen['publication_path'], chosen['publication_sha256'])


def _external_baseline_plan(case, tmp_path):
    """Model the existing staged composer's externally pinned baseline plan."""
    original = case['base']
    descriptor = dict(original[0])
    external = tmp_path / 'anchored-baseline-public-plan.json'
    external.write_bytes(Path(descriptor['public_plan']).read_bytes())
    descriptor['public_plan'] = str(external)
    descriptor_raw = enc(descriptor)
    Path(case['base_path']).write_bytes(descriptor_raw)
    case['base'] = (descriptor, descriptor_raw, sha(descriptor_raw), *original[3:])
    case['config'][Path(case['base_path']).parent.name] = case['base']
    return external


def test_external_baseline_plan_can_publish_and_select_pinned_candidate(case, tmp_path):
    _external_baseline_plan(case, tmp_path)
    assert do_publish(case)['status'] == 'PUBLISHED'
    assert do_select(case)['configuration'][2] == case['candidate'][2]


def test_external_baseline_plan_tamper_is_not_accepted(case, tmp_path):
    external = _external_baseline_plan(case, tmp_path)
    assert do_publish(case)['status'] == 'PUBLISHED'
    external.write_bytes(b'{"modified":true}')
    with pytest.raises(live.LiveSnapshotRefused): do_select(case)


def test_candidate_plan_must_remain_in_candidate_generation(case, tmp_path):
    original = case['candidate']
    descriptor = dict(original[0])
    external = tmp_path / 'unanchored-candidate-public-plan.json'
    external.write_bytes(Path(descriptor['public_plan']).read_bytes())
    descriptor['public_plan'] = str(external)
    descriptor_raw = enc(descriptor)
    Path(case['candidate_path']).write_bytes(descriptor_raw)
    case['candidate'] = (descriptor, descriptor_raw, sha(descriptor_raw), *original[3:])
    case['config'][Path(case['candidate_path']).parent.name] = case['candidate']
    with pytest.raises(live.LiveSnapshotRefused): do_publish(case)
    assert not (case['root'] / 'q4-live-current.json').exists()


def test_real_staged_composer_baseline_reaches_live_pointer_lookup(tmp_path, monkeypatch):
    from tests.tools.test_wd_q4_staged_composer import setup_case
    from tools import wd_q4_staged_composer as composer
    kw, _, _ = setup_case(tmp_path)
    built = composer.compose(**kw)
    path = Path(built['descriptor_path'])
    descriptor = json.loads(path.read_bytes())
    attempted = []
    def read_spy(path, limit, pin=None):
        attempted.append(Path(path).name)
        raise ValueError('synthetic stop')
    monkeypatch.setattr(live, '_read', read_spy)
    with pytest.raises(live.LiveSnapshotRefused):
        live.select(enabled=True, baseline_path=str(path), baseline_sha256=built['descriptor_sha256'],
                    baseline=(descriptor,), manifest_sha256=kw['expected_manifest_sha256'],
                    load_configuration=lambda *_: None)
    assert attempted == ['q4-live-current.json']


def test_stale_index_cannot_be_published(case):
    (case['index'] / 'q4-latest.json').write_bytes(b'different')
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(case)
    assert not (case['root'] / 'q4-live-current.json').exists()


def test_missing_or_changed_pointer_refuses(case):
    with pytest.raises(live.LiveSnapshotRefused):
        do_select(case)
    do_publish(case)
    chosen = do_select(case)
    (case['root'] / 'q4-live-current.json').write_bytes(b'changed')
    with pytest.raises(live.LiveSnapshotRefused):
        live.recheck(chosen['publication_path'], chosen['publication_sha256'])


def test_select_refuses_when_index_has_advanced(case):
    do_publish(case)
    (case['index'] / 'q4-latest.json').write_bytes(b'synthetic-index-advanced')
    with pytest.raises(live.LiveSnapshotRefused):
        do_select(case)


def test_policy_change_refuses_even_if_candidate_is_rehashed(case):
    candidate = list(case['candidate'])
    candidate[0] = {**candidate[0], 'interpreter': 'unapproved-new-interpreter'}
    candidate[1] = enc(candidate[0])
    candidate[2] = sha(candidate[1])
    Path(case['candidate_path']).write_bytes(candidate[1])
    case['candidate'] = tuple(candidate)
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(case)


def test_rehashed_fixed_plan_with_wrong_source_anchor_is_refused(case):
    candidate = list(case['candidate'])
    descriptor = dict(candidate[0])
    inputs = dict(candidate[5])
    fixed = json.loads(inputs['fixed_plan_bytes'])
    fixed['expected_source_sha256'] = '9' * 64
    inputs['fixed_plan_bytes'] = enc(fixed)
    Path(descriptor['fixed_plan_path']).write_bytes(inputs['fixed_plan_bytes'])
    descriptor['fixed_plan_sha256'] = sha(inputs['fixed_plan_bytes'])
    candidate[0], candidate[5] = descriptor, inputs
    candidate[1] = enc(descriptor)
    candidate[2] = sha(candidate[1])
    Path(case['candidate_path']).write_bytes(candidate[1])
    case['candidate'] = tuple(candidate)
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(case)


@pytest.mark.parametrize('part', ['binding', 'fixed-plan', 'snapshot', 'runtime', 'fleet', 'public-plan'])
def test_changed_generation_or_anchor_bytes_refuse(case, part):
    paths = {'binding': Path(case['candidate_path']).parent / 'lane-binding.v1.json',
             'fixed-plan': Path(case['candidate_path']).parent / 'fixed-plan.v2.json',
             'snapshot': Path(case['candidate_path']).parent / 'public-snapshot.v1.json',
             'runtime': Path(case['candidate'][0]['runtime_manifest_path']),
             'fleet': Path(case['candidate'][0]['bundle_root']) / 'wd-fleet.json',
             'public-plan': Path(case['candidate'][0]['public_plan'])}
    with paths[part].open('ab') as stream:
        stream.write(b'changed')
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(case)
    assert not (case['root'] / 'q4-live-current.json').exists()


def test_new_generation_must_be_in_same_root(case):
    case['candidate_path'] = str(case['root'].parent / ('c' * 40) / 'shared-session-config.v2.json')
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(case)


@pytest.mark.parametrize('overlap', ['bundle', 'index'])
def test_generation_root_cannot_overlap_bundle_or_index(case, overlap):
    bundle = Path(case['base'][0]['bundle_root'])
    root = bundle if overlap == 'bundle' else case['index']
    with pytest.raises(ValueError):
        live._validate_roots(root, bundle, case['index'])


def test_interrupted_atomic_replace_stays_unknown(case, monkeypatch):
    monkeypatch.setattr(live.pointer_io, '_atomic_replace', lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    caught_by_exception = False
    with pytest.raises(live.LiveSnapshotInterrupted):
        try:
            do_publish(case)
        except Exception:
            caught_by_exception = True
    assert not caught_by_exception


def test_default_off_does_not_read_inputs():
    assert live.publish() == {'status': 'OFF'}
    assert live.select() == {'status': 'OFF'}


def test_refusal_has_no_active_exception_context(case):
    (case['index'] / 'q4-latest.json').write_bytes(b'stale')
    with pytest.raises(live.LiveSnapshotRefused) as caught:
        do_publish(case)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_uncertain_write_has_no_active_exception_context(case, monkeypatch):
    monkeypatch.setattr(live.pointer_io, '_atomic_replace', lambda *_: (_ for _ in ()).throw(OSError('private sentinel')))
    with pytest.raises(live.LiveSnapshotOutcomeUnknown) as caught:
        do_publish(case)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_failed_atomic_replace_is_unknown(case, monkeypatch):
    monkeypatch.setattr(live.pointer_io, '_atomic_replace', lambda *_: (_ for _ in ()).throw(OSError()))
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        do_publish(case)
    assert not (case['root'] / 'q4-live-current.json').exists()


def test_failure_after_replace_never_claims_not_published(case, monkeypatch):
    replace = live.pointer_io._atomic_replace
    def commit_then_fail(*values):
        replace(*values)
        raise OSError('synthetic failure after replacement')
    monkeypatch.setattr(live.pointer_io, '_atomic_replace', commit_then_fail)
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        do_publish(case)
    assert (case['root'] / 'q4-live-current.json').is_file()
