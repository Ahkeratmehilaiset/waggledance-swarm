"""Synthetic generation selection; not a production or identity proof."""
import hashlib
import json
from pathlib import Path

import pytest

from tools import wd_q4_lane_binding_producer as binding_producer
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_secure_transport as transport
from tests.tools.test_wd_q4_fixed_result_adapter import make_case
from tests.tools.test_wd_q4_staged_lane_query import staged_producer  # noqa: F401 - pytest fixture

_REAL_STAGED_BINDING_PRODUCER = binding_producer.build_staged_lane_binding

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


# --- Staged card route (v3 public plan, ten-pin binding with card_policy_sha256) -------------------------------
# Synthetic only: the real staged producer is replaced by a spy that returns the generation's pinned binding
# bytes only when it receives exactly the bytes on disk under the v3 plan pins. This proves the selector's
# wiring and refusals, not the producer itself (covered by the producer and composer tests).

_STAGED_FILES = (('pointer', 'card-pointer.json'), ('policy', 'policy'), ('roster', 'roster'),
                 ('public_policy', 'public'), ('card_decision', 'card-decision.json'),
                 ('card_policy', 'card-policy.json'))


def _staged_generation(c, gen, files, card_policy=None):
    """Rewrite one generation of the legacy case as a staged-card generation; returns the 6-tuple."""
    index = c['index']
    directory = c['root'] / gen
    blobs = dict(files)
    if card_policy is not None:
        blobs['card_policy'] = card_policy
    pins = {name + '_sha256': sha(blobs[name]) for name, _ in _STAGED_FILES}
    plan = {'schema': 'wd.q4-public-wrapper-plan.v3', 'root': str(index),
            'config': {'descriptor_sha256': sha(b'descriptor-' + blobs['pointer'])},
            'encoder_sha256': c['base'][4]['encoder_sha256'], 'source_anchor_sha256': sha(b'anchor-' + blobs['pointer']),
            'end_cursor': {'file_identity': 'synthetic-file', 'generation': None, 'offset': 0},
            'source_path': str(index / 'source.jsonl'), 'generation_path': str(index / 'generation.json'),
            **{name + '_path': str(index / filename) for name, filename in _STAGED_FILES}, **pins}
    plan_raw = enc(plan)
    binding = {'pointer_sha256': plan['pointer_sha256'], 'source_anchor_sha256': plan['source_anchor_sha256'],
               'encoder_sha256': plan['encoder_sha256'], 'policy_sha256': plan['policy_sha256'],
               'roster_sha256': plan['roster_sha256'], 'public_policy_sha256': plan['public_policy_sha256'],
               'descriptor_sha256': plan['config']['descriptor_sha256'], 'projection_sha256': sha(b'projection'),
               'card_decision_sha256': plan['card_decision_sha256'], 'card_policy_sha256': plan['card_policy_sha256']}
    document = json.loads(c['base'][5]['binding_bytes'])
    document['binding'] = binding
    document['public_plan_sha256'] = sha(plan_raw)
    binding_raw = enc(document)
    bundle = Path(c['base'][0]['bundle_root'])
    fixed = json.loads(c['base'][5]['fixed_plan_bytes'])
    fixed.update(schema='wd.q4-fixed-plan.v3', backend='staged-card-wrapper-v1',
        expected_pointer_sha256=binding['pointer_sha256'], expected_source_sha256=binding['source_anchor_sha256'],
        expected_encoder_sha256=binding['encoder_sha256'], policy_sha256=binding['policy_sha256'],
        roster_sha256=binding['roster_sha256'], expected_public_policy_sha256=binding['public_policy_sha256'],
        card_decision_sha256=binding['card_decision_sha256'], card_policy_sha256=binding['card_policy_sha256'],
        wrapper_sha256=sha((bundle / 'Invoke-WdQ4Query.ps1').read_bytes()),
        deployment_manifest_sha256=c['manifest_sha'], public_plan_sha256=sha(plan_raw), host_sha256='8' * 64,
        backend_sha256='9' * 64, bundle_root=str(bundle), bundle_commit='a' * 40,
        wrapper_path=str(bundle / 'Invoke-WdQ4Query.ps1'), public_plan_path=str(directory / 'public-plan.json'))
    fixed_raw = enc(fixed)
    (directory / 'public-plan.json').write_bytes(plan_raw)
    (directory / 'lane-binding.v1.json').write_bytes(binding_raw)
    (directory / 'fixed-plan.v2.json').write_bytes(fixed_raw)
    descriptor = dict(c['config'][gen][0])
    descriptor.update(binding_sha256=sha(binding_raw), fixed_plan_sha256=sha(fixed_raw), expected_binding=binding)
    descriptor_raw = enc(descriptor)
    (directory / 'shared-session-config.v2.json').write_bytes(descriptor_raw)
    inputs = dict(c['config'][gen][5], binding_bytes=binding_raw, fixed_plan_bytes=fixed_raw)
    item = (descriptor, descriptor_raw, sha(descriptor_raw), c['config'][gen][3], binding, inputs)
    c['config'][gen] = item
    c['producer_out'][sha(plan_raw)] = binding_raw
    return item


@pytest.fixture
def staged(case, monkeypatch):
    files = {'pointer': b'{"synthetic":"card-pointer-1"}', 'policy': b'synthetic-policy', 'roster': b'synthetic-roster',
             'public_policy': b'synthetic-public-policy', 'card_decision': b'synthetic-card-decision-1',
             'card_policy': b'synthetic-card-policy'}
    for name, filename in _STAGED_FILES:
        (case['index'] / filename).write_bytes(files[name])
    case.update(producer_out={}, staged_calls=[], legacy_calls=[], files=files, after_producer=None)
    case['base'] = _staged_generation(case, 'b' * 40, files)
    case['candidate'] = _staged_generation(case, 'c' * 40, files)

    def staged_spy(**kw):
        plan = json.loads(kw['public_plan_bytes'])
        for name, _ in _STAGED_FILES:
            assert kw[name + '_bytes'] == Path(plan[name + '_path']).read_bytes()
            assert sha(kw[name + '_bytes']) == plan[name + '_sha256']
        assert kw['enabled'] is True and kw['expected_commit'] == 'a' * 40
        case['staged_calls'].append(sha(kw['public_plan_bytes']))
        out = case['producer_out'][sha(kw['public_plan_bytes'])]
        if case['after_producer']:
            case['after_producer']()
        return {'binding_bytes': out, 'binding_sha256': sha(out)}

    def legacy_spy(**kw):
        case['legacy_calls'].append(1)
        raise AssertionError('legacy producer used for a staged descriptor')

    monkeypatch.setattr(live.binding_producer, 'build_staged_lane_binding', staged_spy)
    monkeypatch.setattr(live.binding_producer, 'build_lane_binding', legacy_spy)
    return case


def test_staged_publish_and_select_use_staged_producer_and_card_pointer(staged):
    result = do_publish(staged)
    chosen = do_select(staged)
    assert result['status'] == 'PUBLISHED' and chosen['status'] == 'SELECTED'
    assert chosen['configuration'][2] == staged['candidate'][2]
    assert staged['staged_calls'] and not staged['legacy_calls']
    publication = json.loads((staged['root'] / 'q4-live-current.json').read_bytes())
    assert publication['index_pointer_sha256'] == sha(staged['files']['pointer'])
    assert not (staged['index'] / 'q4-latest.json').read_bytes() == staged['files']['pointer']


@pytest.fixture
def real_staged(case, staged_producer):
    """Build C2 with the real synthetic producer and retain the real binding producer."""
    c, evidence = case, staged_producer
    plan_raw = evidence['public_plan_bytes']; plan = json.loads(plan_raw)
    made = _REAL_STAGED_BINDING_PRODUCER(**evidence)
    binding = json.loads(made['binding_bytes'])['binding']
    assert len(binding) == 10 and 'receipt_sha256' not in binding
    directory = Path(plan['config']['memory'])
    snapshot = transport.fixed_card_bundle(enabled=True, **{key:evidence[key+'_bytes'] for key in
        ('pointer','policy','roster','public_policy','card_decision','card_policy')},
        memory=(directory/'memory.json').read_bytes(), projection=(directory/'projection.json').read_bytes(),
        descriptor=(directory/'descriptor.json').read_bytes())
    bundle = Path(c['base'][0]['bundle_root'])
    (bundle/'wd-fleet.json').write_bytes(evidence['fleet_bytes'])
    (bundle/'deployment-manifest.json').write_bytes(evidence['manifest_bytes'])
    (bundle/'Invoke-WdQ4Query.ps1').write_bytes(evidence['wrapper_bytes'])
    c['manifest_sha'] = evidence['expected_manifest_sha256']
    c['index'] = Path(plan['root'])
    for gen in ('b'*40, 'c'*40):
        generation = c['root']/gen
        public_path = generation/'public-plan.json'
        fixed = json.loads(c['config'][gen][5]['fixed_plan_bytes'])
        fixed.update(schema='wd.q4-fixed-plan.v3',backend='staged-card-wrapper-v1',
            bundle_sha256=sha(snapshot),bundle_bytes=len(snapshot),
            expected_pointer_sha256=binding['pointer_sha256'],expected_source_sha256=binding['source_anchor_sha256'],
            expected_encoder_sha256=binding['encoder_sha256'],policy_sha256=binding['policy_sha256'],
            roster_sha256=binding['roster_sha256'],expected_public_policy_sha256=binding['public_policy_sha256'],
            card_decision_sha256=binding['card_decision_sha256'],card_policy_sha256=binding['card_policy_sha256'],
            wrapper_sha256=evidence['expected_wrapper_sha256'],deployment_manifest_sha256=c['manifest_sha'],
            public_plan_sha256=sha(plan_raw),host_sha256='8'*64,backend_sha256='9'*64,
            bundle_root=str(bundle),bundle_commit='a'*40,wrapper_path=str(bundle/'Invoke-WdQ4Query.ps1'),
            public_plan_path=str(public_path))
        fixed_raw=enc(fixed)
        for name, data in (('public-plan.json',plan_raw),('lane-binding.v1.json',made['binding_bytes']),
                           ('fixed-plan.v2.json',fixed_raw),('public-snapshot.v1.json',snapshot)):
            (generation/name).write_bytes(data)
        descriptor=dict(c['config'][gen][0])
        descriptor.update(fleet_sha256=evidence['expected_fleet_sha256'],binding_sha256=made['binding_sha256'],
            fixed_plan_sha256=sha(fixed_raw),snapshot_sha256=sha(snapshot),expected_binding=binding)
        descriptor_raw=enc(descriptor);(generation/'shared-session-config.v2.json').write_bytes(descriptor_raw)
        inputs=dict(c['config'][gen][5],binding_bytes=made['binding_bytes'],fixed_plan_bytes=fixed_raw,
                    fixed_bundle_bytes=snapshot)
        c['config'][gen]=(descriptor,descriptor_raw,sha(descriptor_raw),evidence['fleet_bytes'],binding,inputs)
    c['base']=c['config']['b'*40];c['candidate']=c['config']['c'*40]
    c['real_plan']=plan
    return c


def test_real_staged_binding_producer_publishes_and_selects(real_staged):
    assert live.binding_producer.build_staged_lane_binding is _REAL_STAGED_BINDING_PRODUCER
    published = do_publish(real_staged)
    selected = do_select(real_staged)
    assert published['status'] == 'PUBLISHED' and selected['status'] == 'SELECTED'
    assert selected['configuration'][2] == real_staged['candidate'][2]
    assert selected['publication_sha256'] == published['publication_sha256']


@pytest.mark.parametrize('part', ['pointer','policy','card_policy','card_decision','projection','source','generation'])
def test_real_staged_binding_rejects_changed_c2_inputs_before_publish(real_staged, part):
    plan = real_staged['real_plan']
    if part == 'projection':
        path = Path(plan['config']['memory'])/'projection.json'
    else:
        path = Path(plan[part+'_path'])
    if part == 'source':
        data = path.read_bytes()
        path.write_bytes(b'!' + data[1:])  # Rewrite inside the pinned prefix, not an allowed append.
    elif part == 'generation':
        path.write_bytes(enc({'generation':'synthetic-changed'}))
    else:
        path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(real_staged)
    assert not (real_staged['root']/'q4-live-current.json').exists()


def test_real_staged_source_append_is_allowed_without_claiming_freshness(real_staged):
    path = Path(real_staged['real_plan']['source_path'])
    path.write_bytes(path.read_bytes()+b'{"synthetic":"later"}\n')
    assert do_publish(real_staged)['status'] == 'PUBLISHED'
    assert do_select(real_staged)['status'] == 'SELECTED'


def test_real_staged_pointer_change_after_producer_before_publish_refuses(real_staged, monkeypatch):
    pointer = Path(real_staged['real_plan']['pointer_path'])
    calls = []
    def run_and_change(**kwargs):
        value = _REAL_STAGED_BINDING_PRODUCER(**kwargs)
        calls.append(True)
        pointer.write_bytes(pointer.read_bytes()+b' ')
        return value
    monkeypatch.setattr(live.binding_producer,'build_staged_lane_binding',run_and_change)
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(real_staged)
    assert calls == [True]
    assert not (real_staged['root']/'q4-live-current.json').exists()


@pytest.mark.parametrize('part', ['pointer','publication'])
def test_real_staged_changed_evidence_before_query_output_refuses(real_staged, part):
    from tools import wd_q4_lane_caller_cli as caller
    published = do_publish(real_staged)
    selected = do_select(real_staged)
    descriptor, _, _, _, binding, inputs = selected['configuration']
    caller._require_current_snapshot(descriptor, binding, inputs)
    live.recheck(selected['publication_path'], published['publication_sha256'])
    if part == 'pointer':
        path = Path(real_staged['real_plan']['pointer_path'])
        path.write_bytes(path.read_bytes()+b' ')
        with pytest.raises(ValueError):
            caller._require_current_snapshot(descriptor, binding, inputs)
    else:
        path = Path(selected['publication_path'])
        path.write_bytes(path.read_bytes()+b' ')
        with pytest.raises(live.LiveSnapshotRefused):
            live.recheck(selected['publication_path'], published['publication_sha256'])


def test_staged_route_ignores_legacy_index_pointer(staged):
    (staged['index'] / 'q4-latest.json').write_bytes(b'unrelated legacy pointer')
    assert do_publish(staged)['status'] == 'PUBLISHED'
    assert do_select(staged)['status'] == 'SELECTED'


def test_staged_card_pointer_advanced_after_publish_refuses_select(staged):
    do_publish(staged)
    (staged['index'] / 'card-pointer.json').write_bytes(b'{"synthetic":"card-pointer-2"}')
    with pytest.raises(live.LiveSnapshotRefused):
        do_select(staged)


def test_staged_card_pointer_changed_mid_publish_refuses(staged):
    staged['after_producer'] = lambda: (staged['index'] / 'card-pointer.json').write_bytes(b'{"mid":"query"}')
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(staged)
    assert not (staged['root'] / 'q4-live-current.json').exists()


@pytest.mark.parametrize('name', [name for name, _ in _STAGED_FILES])
def test_staged_changed_plan_input_refuses(staged, name):
    path = staged['index'] / dict(_STAGED_FILES)[name]
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(staged)
    assert not (staged['root'] / 'q4-live-current.json').exists()


def test_staged_candidate_with_new_card_policy_refuses(staged):
    (staged['index'] / 'card-policy-2.json').write_bytes(b'other card policy')
    staged['candidate'] = _staged_generation(staged, 'c' * 40, staged['files'], card_policy=b'other card policy')
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(staged)


def test_staged_and_legacy_generations_do_not_mix(staged):
    legacy = staged['candidate']
    descriptor = dict(legacy[0])
    descriptor['expected_binding'] = {k: v for k, v in descriptor['expected_binding'].items()
                                      if k != 'card_policy_sha256'}
    raw = enc(descriptor)
    Path(staged['candidate_path']).write_bytes(raw)
    staged['candidate'] = (descriptor, raw, sha(raw), *legacy[3:])
    staged['config']['c' * 40] = staged['candidate']
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(staged)


@pytest.mark.parametrize('field,value', [('card_policy_sha256', '0' * 64), ('card_decision_sha256', '0' * 64),
                                         ('backend', 'public-snapshot-v1'), ('public_plan_sha256', '0' * 64),
                                         ('wrapper_sha256', '0' * 64), ('deployment_manifest_sha256', '0' * 64),
                                         ('bundle_commit', 'e' * 40)])
def test_staged_rehashed_fixed_plan_mismatch_refuses(staged, field, value):
    candidate = list(staged['candidate'])
    descriptor, inputs = dict(candidate[0]), dict(candidate[5])
    fixed = json.loads(inputs['fixed_plan_bytes'])
    fixed[field] = value
    if field == 'backend':
        fixed['schema'] = 'wd.q4-fixed-plan.v3'
    inputs['fixed_plan_bytes'] = enc(fixed)
    Path(descriptor['fixed_plan_path']).write_bytes(inputs['fixed_plan_bytes'])
    descriptor['fixed_plan_sha256'] = sha(inputs['fixed_plan_bytes'])
    raw = enc(descriptor)
    Path(staged['candidate_path']).write_bytes(raw)
    staged['candidate'] = (descriptor, raw, sha(raw), candidate[3], candidate[4], inputs)
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(staged)


@pytest.mark.parametrize('field', ['pointer_path', 'source_path', 'generation_path', 'card_policy_path', 'root'])
def test_staged_rehashed_plan_moving_stable_input_refuses(staged, field, tmp_path):
    gen = 'c' * 40
    directory = staged['root'] / gen
    plan = json.loads((directory / 'public-plan.json').read_bytes())
    moved = tmp_path / ('moved-' + field)
    moved.mkdir()
    if field == 'root':
        plan['root'] = str(moved)
    else:
        source = Path(plan[field])
        target = moved / source.name
        if source.exists():
            target.write_bytes(source.read_bytes())
        plan[field] = str(target)
    plan_raw = enc(plan)
    staged['producer_out'][sha(plan_raw)] = None
    candidate = list(staged['candidate'])
    descriptor, inputs = dict(candidate[0]), dict(candidate[5])
    document = json.loads(inputs['binding_bytes'])
    document['public_plan_sha256'] = sha(plan_raw)
    inputs['binding_bytes'] = enc(document)
    fixed = json.loads(inputs['fixed_plan_bytes'])
    fixed['public_plan_sha256'] = sha(plan_raw)
    inputs['fixed_plan_bytes'] = enc(fixed)
    staged['producer_out'][sha(plan_raw)] = inputs['binding_bytes']
    (directory / 'public-plan.json').write_bytes(plan_raw)
    (directory / 'lane-binding.v1.json').write_bytes(inputs['binding_bytes'])
    (directory / 'fixed-plan.v2.json').write_bytes(inputs['fixed_plan_bytes'])
    descriptor.update(binding_sha256=sha(inputs['binding_bytes']), fixed_plan_sha256=sha(inputs['fixed_plan_bytes']))
    raw = enc(descriptor)
    Path(staged['candidate_path']).write_bytes(raw)
    staged['candidate'] = (descriptor, raw, sha(raw), candidate[3], candidate[4], inputs)
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(staged)


def test_staged_plan_control_with_same_stable_inputs_publishes(staged):
    """Control for the moved-input test: rewriting the candidate plan with the same values still publishes."""
    gen = 'c' * 40
    staged['candidate'] = _staged_generation(staged, gen, staged['files'])
    assert do_publish(staged)['status'] == 'PUBLISHED'


def _rehash_fixed(c, gen, field, value):
    item = c['config'][gen]
    descriptor, inputs = dict(item[0]), dict(item[5])
    fixed = json.loads(inputs['fixed_plan_bytes'])
    fixed[field] = value
    inputs['fixed_plan_bytes'] = enc(fixed)
    Path(descriptor['fixed_plan_path']).write_bytes(inputs['fixed_plan_bytes'])
    descriptor['fixed_plan_sha256'] = sha(inputs['fixed_plan_bytes'])
    raw = enc(descriptor)
    (c['root'] / gen / 'shared-session-config.v2.json').write_bytes(raw)
    c['config'][gen] = (descriptor, raw, sha(raw), item[3], item[4], inputs)
    return c['config'][gen]


@pytest.mark.parametrize('field,value', [('wrapper_sha256', '0' * 64), ('deployment_manifest_sha256', '0' * 64),
                                         ('bundle_commit', 'e' * 40)])
def test_staged_both_generations_with_wrong_bundle_pins_refuse(staged, field, value):
    staged['base'] = _rehash_fixed(staged, 'b' * 40, field, value)
    staged['candidate'] = _rehash_fixed(staged, 'c' * 40, field, value)
    with pytest.raises(live.LiveSnapshotRefused):
        do_publish(staged)


def test_staged_both_generations_rehashed_control_publishes(staged):
    staged['base'] = _rehash_fixed(staged, 'b' * 40, 'host_sha256', '7' * 64)
    staged['candidate'] = _rehash_fixed(staged, 'c' * 40, 'host_sha256', '7' * 64)
    assert do_publish(staged)['status'] == 'PUBLISHED'
