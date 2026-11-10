"""Synthetic generation selection; not a production or identity proof."""
import hashlib
import json
import os
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


@pytest.fixture
def cas_case(case, monkeypatch):
    # Exercise selector publication/locking independently of C2 construction;
    # separate B/C/D tests retain the real staged producer and prefix checks.
    monkeypatch.setattr(live, '_checked_pair', lambda *a, **k:
                        (str(case['index']), (case['index'] / 'q4-latest.json', 16384)))
    return case


def _cas_publish(c, **kwargs):
    return live.publish(enabled=True, baseline_path=c['base_path'], baseline_sha256=c['base'][2],
        baseline=c['base'], manifest_sha256=c['manifest_sha'], candidate_path=c['candidate_path'],
        candidate_sha256=c['candidate'][2], candidate=c['candidate'], p1_enabled=True, **kwargs)


def test_enrolled_generation_rejects_unadmitted_publication(cas_case):
    root = cas_case['root']
    (root / 'q4-lifecycle-v2.enrolled.json').write_bytes(
        b'{"binding_sha256":"' + b'a' * 64 + b'","schema":"wd.q4-lifecycle-enrollment.v2"}')
    with pytest.raises(live.LiveSnapshotRefused):
        _cas_publish(cas_case, expected_previous_selector_sha256=None)
    assert not (root / 'q4-live-current.json').exists()
    assert not (root / 'q4-live-publication.pending.json').exists()


def _lifecycle_binding(root):
    private = root.parent / (root.name + '.q4-lifecycle-private-v2')
    private.mkdir()
    raw = enc({'schema': 'wd.q4-lifecycle-binding.v2', 'generation_root': str(root),
               'private_root': str(private)})
    return raw, sha(raw)


def test_lifecycle_admission_allows_owned_publication(cas_case):
    from tools import wd_q4_lifecycle_v2 as lifecycle
    raw, digest = _lifecycle_binding(cas_case['root'])
    with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=digest,
                         attempt='a' * 64, previous_selector_sha256=None):
        result = _cas_publish(cas_case, expected_previous_selector_sha256=None)
    assert result['status'] == 'PUBLISHED'
    with pytest.raises(live.LiveSnapshotRefused):
        _cas_publish(cas_case, expected_previous_selector_sha256=result['publication_sha256'])


def test_copied_admission_is_revoked_after_exit(cas_case):
    from contextvars import copy_context
    from tools import wd_q4_lifecycle_v2 as lifecycle
    raw, digest = _lifecycle_binding(cas_case['root'])
    with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=digest,
                         attempt='a' * 64, previous_selector_sha256=None):
        copied = copy_context()
    with pytest.raises(live.LiveSnapshotRefused):
        copied.run(_cas_publish, cas_case, expected_previous_selector_sha256=None)
    assert not (cas_case['root'] / 'q4-live-current.json').exists()


@pytest.mark.parametrize('tamper', ['enrollment', 'pending', 'different_previous', 'foreign_thread'])
def test_lifecycle_admission_refuses_foreign_or_changed_state(cas_case, tamper):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context
    from tools import wd_q4_lifecycle_v2 as lifecycle
    root = cas_case['root']
    raw, digest = _lifecycle_binding(root)
    with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=digest,
                         attempt='a' * 64, previous_selector_sha256=None):
        if tamper in ('enrollment', 'pending'):
            name = 'q4-lifecycle-v2.enrolled.json' if tamper == 'enrollment' else 'q4-lifecycle-v2.pending.json'
            (root / name).write_bytes(b'{}')
        expected = 'b' * 64 if tamper == 'different_previous' else None
        def publish():
            with pytest.raises(live.LiveSnapshotRefused):
                _cas_publish(cas_case, expected_previous_selector_sha256=expected)
        if tamper == 'foreign_thread':
            context = copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(context.run, publish).result()
        else:
            publish()
    assert not (root / 'q4-live-current.json').exists()


def test_p1_requires_an_explicit_previous_selector_pin(cas_case):
    with pytest.raises(live.LiveSnapshotRefused):
        _cas_publish(cas_case)
    assert not (cas_case['root'] / 'q4-live-current.json').exists()


def test_p1_compare_and_swap_rejects_absent_and_stale_expectations(cas_case):
    result = _cas_publish(cas_case, expected_previous_selector_sha256=None)
    path = cas_case['root'] / 'q4-live-current.json'
    prior = path.read_bytes()
    for expected in (None, '0' * 64):
        with pytest.raises(live.LiveSnapshotRefused):
            _cas_publish(cas_case, expected_previous_selector_sha256=expected)
        assert path.read_bytes() == prior
    result2 = _cas_publish(cas_case, expected_previous_selector_sha256=result['publication_sha256'])
    assert result2['status'] == 'PUBLISHED'


def test_p1_uncertain_publish_blocks_writers_and_readers(cas_case, monkeypatch):
    def fail_replace(*args):
        raise OSError('synthetic failure after reservation')
    monkeypatch.setattr(live.pointer_io, '_atomic_replace', fail_replace)
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        _cas_publish(cas_case, expected_previous_selector_sha256=None)
    pending = cas_case['root'] / 'q4-live-publication.pending.json'
    assert pending.is_file()
    before = pending.read_bytes()
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        _cas_publish(cas_case, expected_previous_selector_sha256=None)
    assert pending.read_bytes() == before
    with pytest.raises(live.LiveSnapshotRefused):
        do_select(cas_case)


def test_p1_simultaneous_first_publication_has_only_one_winner(cas_case, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    original = live._checked_pair
    def synchronized(*args, **kwargs):
        result = original(*args, **kwargs)
        barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(live, '_checked_pair', synchronized)
    def attempt():
        try:
            return _cas_publish(cas_case, expected_previous_selector_sha256=None)['status']
        except live.LiveSnapshotRefused:
            return 'REFUSED'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(outcomes) == ['PUBLISHED', 'REFUSED']


def test_p1_replace_failure_keeps_pending_and_blocks_selection(cas_case, monkeypatch):
    actual = live.pointer_io._atomic_replace
    def fail_after_replace(*args):
        actual(*args)
        raise OSError('synthetic uncertainty after replace')
    monkeypatch.setattr(live.pointer_io, '_atomic_replace', fail_after_replace)
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        _cas_publish(cas_case, expected_previous_selector_sha256=None)
    assert (cas_case['root'] / 'q4-live-current.json').is_file()
    assert (cas_case['root'] / 'q4-live-publication.pending.json').is_file()
    with pytest.raises(live.LiveSnapshotRefused):
        do_select(cas_case)
    with pytest.raises(live.LiveSnapshotRefused):
        live.recheck(cas_case['root'] / 'q4-live-current.json',
                     sha((cas_case['root'] / 'q4-live-current.json').read_bytes()))


def _hold_publication_lock(lock_path, ready, release):
    from sandbox.mind.step2.reader_slot import reader_slot
    with reader_slot(lock_path, timeout_seconds=5):
        ready.set()
        release.wait(20)


def test_lifecycle_enrollment_waits_for_legacy_publisher(cas_case):
    import multiprocessing
    from tools import wd_q4_lifecycle_v2 as lifecycle
    root = cas_case['root']
    raw, digest = _lifecycle_binding(root)
    ctx = multiprocessing.get_context('spawn')
    ready, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_hold_publication_lock,
                        args=(str(root / 'q4-live-publication.lock'), ready, release))
    child.start()
    try:
        assert ready.wait(10)
        with pytest.raises(lifecycle.Refused):
            with lifecycle.admit(enabled=True, binding_bytes=raw, binding_sha256=digest,
                                 attempt='a' * 64, previous_selector_sha256=None):
                pass
        assert not (root / 'q4-lifecycle-v2.enrolled.json').exists()
        assert not (root / 'q4-lifecycle-v2.pending.json').exists()
    finally:
        release.set()
        child.join(10)
    assert child.exitcode == 0


@pytest.mark.parametrize('p1', [False, True])
def test_all_writers_respect_cross_process_publication_lock(cas_case, p1):
    import multiprocessing
    ctx = multiprocessing.get_context('spawn')
    ready, release = ctx.Event(), ctx.Event()
    lock = cas_case['root'] / 'q4-live-publication.lock'
    child = ctx.Process(target=_hold_publication_lock, args=(str(lock), ready, release))
    child.start()
    try:
        assert ready.wait(10)
        with pytest.raises(live.LiveSnapshotRefused):
            if p1:
                _cas_publish(cas_case, expected_previous_selector_sha256=None)
            else:
                do_publish(cas_case)
        assert not (cas_case['root'] / 'q4-live-current.json').exists()
        assert not (cas_case['root'] / 'q4-live-publication.pending.json').exists()
    finally:
        release.set()
        child.join(10)
    assert child.exitcode == 0


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
    if os.name != 'nt':
        pytest.skip('real staged producer requires Windows fixed-volume checkpoint reader')
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


@pytest.fixture
def staged_transition(tmp_path, monkeypatch):
    """Real synthetic source identity and two externally labelled anchors."""
    from tools import wd_q4_external_generation_anchor as anchor
    from waggledance.core import bridge_log_reader as bridge
    parent = tmp_path / '.codex-audit' / 'approved'
    stage = parent / 'shared'; (stage / ('b'*40)).mkdir(parents=True)
    monkeypatch.setattr(live, '_P1_ROOT', parent)  # test-only, never read from an environment or CLI
    bundle = tmp_path / 'bundle'; bundle.mkdir()
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    old = parent / 'old' / 'snapshot'; old.mkdir(parents=True)
    new = parent / 'new' / 'snapshot'; new.mkdir(parents=True)
    source = tmp_path / 'source' / 'events.jsonl'; source.parent.mkdir()
    source.write_bytes(b'{"synthetic":1}\n')
    with bridge._open_log(source) as stream: identity = bridge._file_identity(stream)
    anchors = []
    for name in ('old-anchor', 'new-anchor'):
        anchor_dir = parent / '.codex-audit' / 'anchors' / name; anchor_dir.mkdir(parents=True)
        data = source.read_bytes()
        cursor = bridge.BridgeCursor(len(data), identity, None)
        prepared = anchor.prepare(enabled=True, source_path=str(source), original_cursor=cursor,
            expected_prefix_sha256=sha(data), output_root=str(anchor_dir))
        sidecar = anchor_dir / 'generation.json'
        generation = json.loads(sidecar.read_bytes())['generation']
        anchors.append((str(sidecar), {'offset':len(data), 'file_identity':identity, 'generation':generation}, sha(data)))
        if name == 'old-anchor': source.write_bytes(data + b'{"synthetic":2}\n')
    (old/'pointer.json').write_bytes(b'old')
    (new/'pointer.json').write_bytes(b'new')
    base = {'root':str(old), 'pointer_path':str(old/'pointer.json'), 'pointer_sha256':sha(b'old'),
            'source_path':str(source), 'generation_path':anchors[0][0], 'end_cursor':anchors[0][1],
            'source_anchor_sha256':anchors[0][2]}
    candidate = {'root':str(new), 'pointer_path':str(new/'pointer.json'), 'pointer_sha256':sha(b'new'),
                 'source_path':str(source), 'generation_path':anchors[1][0], 'end_cursor':anchors[1][1],
                 'source_anchor_sha256':anchors[1][2]}
    desc = {'binding_path':str(stage / ('b'*40) / 'lane-binding.v1.json'), 'bundle_root':str(bundle)}
    return parent, stage, bundle, runtime, source, base, candidate, desc


def test_staged_transition_accepts_contained_snapshot_and_same_source_prefix(staged_transition):
    _, _, _, runtime, _, base, candidate, desc = staged_transition
    live._validate_staged_transition(base, candidate, desc, {'runtime_root':str(runtime)})


def test_staged_transition_rejects_dot_prefixed_run(staged_transition):
    _, _, _, runtime, _, base, candidate, desc = staged_transition
    old_root = Path(candidate['root'])
    dot_root = old_root.parent.parent / '.hidden-run' / 'snapshot'
    old_root.parent.rename(dot_root.parent)
    candidate['root'] = str(dot_root)
    candidate['pointer_path'] = str(dot_root / 'pointer.json')
    with pytest.raises(ValueError):
        live._validate_staged_transition(base, candidate, desc, {'runtime_root':str(runtime)})


@pytest.mark.parametrize('mutation', ['root_escape','root_bundle','root_stage','root_runtime','root_source','pointer_escape',
                                      'generation_escape','generation_stage','anchor_binding',
                                      'source_rewrite','wrong_identity','offset_regression'])
def test_staged_transition_rejects_escapes_and_source_tampering(staged_transition, tmp_path, mutation):
    parent, stage, bundle, runtime, source, base, candidate, desc = staged_transition
    if mutation == 'root_escape': candidate['root'] = str(tmp_path)
    if mutation == 'root_bundle': candidate['root'] = str(bundle)
    if mutation == 'root_stage': candidate['root'] = str(stage)
    if mutation == 'root_runtime': candidate['root'] = str(runtime)
    if mutation == 'root_source': candidate['root'] = str(source.parent)
    if mutation == 'pointer_escape': candidate['pointer_path'] = str(Path(base['pointer_path']))
    if mutation == 'generation_escape':
        path = tmp_path/'outside-generation.json'; path.write_bytes(Path(candidate['generation_path']).read_bytes())
        candidate['generation_path'] = str(path)
    if mutation == 'generation_stage':
        path = stage/'generation.json'; path.write_bytes(Path(candidate['generation_path']).read_bytes())
        candidate['generation_path'] = str(path)
    if mutation == 'anchor_binding': Path(candidate['generation_path']).with_name('binding.json').write_bytes(b'{}')
    if mutation == 'source_rewrite': source.write_bytes(b'X' + source.read_bytes()[1:])
    if mutation == 'wrong_identity': candidate['end_cursor']['file_identity'] = 'foreign'
    if mutation == 'offset_regression': candidate['end_cursor']['offset'] = 1
    with pytest.raises(ValueError):
        live._validate_staged_transition(base, candidate, desc, {'runtime_root':str(runtime)})


@pytest.mark.parametrize('field', ['root','generation_path'])
def test_staged_transition_rejects_reparse_component(staged_transition, monkeypatch, field):
    from tools import wd_q4_descriptor_pointer as pointer_io
    _, _, _, runtime, _, base, candidate, desc = staged_transition
    bad = Path(candidate[field])
    original = pointer_io._is_reparse
    monkeypatch.setattr(pointer_io, '_is_reparse', lambda path, info: path == bad or original(path, info))
    with pytest.raises((ValueError, pointer_io.PointerRefused)):
        live._validate_staged_transition(base, candidate, desc, {'runtime_root':str(runtime)})


@pytest.mark.parametrize('field', ['vocabulary_sha256','path_allowlist_sha256','nonreview_policy_sha256',
                                   'roster_sha256','card_policy_sha256'])
def test_p1_requires_stable_decision_policy_pins(field):
    baseline = {key:'a'*64 for key in live._P1_DECISION_STABLE} | {'synthetic':False}
    live._validate_p1_decisions(enc(baseline), enc(baseline))
    changed = dict(baseline); changed[field] = 'b'*64
    with pytest.raises(ValueError): live._validate_p1_decisions(enc(baseline), enc(changed))
    changed = dict(baseline); del changed[field]
    with pytest.raises(ValueError): live._validate_p1_decisions(enc(baseline), enc(changed))


@pytest.mark.parametrize('which', ['baseline', 'candidate'])
def test_p1_rejects_synthetic_decision_route(which):
    baseline = {key:'a'*64 for key in live._P1_DECISION_STABLE} | {'synthetic':False}
    candidate = dict(baseline)
    (baseline if which == 'baseline' else candidate)['synthetic'] = True
    with pytest.raises(ValueError): live._validate_p1_decisions(enc(baseline), enc(candidate))


@pytest.mark.parametrize('flag', [None, 0, 1, 'true'])
def test_p1_flag_requires_exact_bool(staged, flag):
    with pytest.raises(live.LiveSnapshotRefused):
        live.publish(enabled=True, baseline_path=staged['base_path'], baseline_sha256=staged['base'][2],
            baseline=staged['base'], manifest_sha256=staged['manifest_sha'], candidate_path=staged['candidate_path'],
            candidate_sha256=staged['candidate'][2], candidate=staged['candidate'], p1_enabled=flag)


@pytest.mark.parametrize('field', ['interpreter','bootstrap','runtime_root','timeout'])
def test_staged_rehashed_config_drift_is_refused(staged, field):
    directory = staged['root'] / ('c'*40)
    base_directory = staged['root'] / ('b'*40)
    # Re-pin both generations after changing only the candidate config.
    for gen_dir, val in ((base_directory, 'baseline'), (directory, 'foreign')):
        generation = gen_dir.name
        item = list(staged['config'][generation]); descriptor = dict(item[0]); inputs = dict(item[5])
        plan = json.loads((gen_dir/'public-plan.json').read_bytes())
        plan['config'][field] = val
        raw_plan = enc(plan); (gen_dir/'public-plan.json').write_bytes(raw_plan)
        binding = json.loads(inputs['binding_bytes']); binding['public_plan_sha256'] = sha(raw_plan)
        inputs['binding_bytes'] = enc(binding); (gen_dir/'lane-binding.v1.json').write_bytes(inputs['binding_bytes'])
        fixed = json.loads(inputs['fixed_plan_bytes']); fixed['public_plan_sha256'] = sha(raw_plan)
        inputs['fixed_plan_bytes'] = enc(fixed); (gen_dir/'fixed-plan.v2.json').write_bytes(inputs['fixed_plan_bytes'])
        descriptor.update(binding_sha256=sha(inputs['binding_bytes']), fixed_plan_sha256=sha(inputs['fixed_plan_bytes']))
        raw = enc(descriptor); (gen_dir/'shared-session-config.v2.json').write_bytes(raw)
        staged['config'][generation] = (descriptor, raw, sha(raw), item[3], item[4], inputs)
        staged['producer_out'][sha(raw_plan)] = inputs['binding_bytes']
    staged['base'] = staged['config']['b'*40]; staged['candidate'] = staged['config']['c'*40]
    with pytest.raises(live.LiveSnapshotRefused):
        live.publish(enabled=True, baseline_path=staged['base_path'], baseline_sha256=staged['base'][2],
            baseline=staged['base'], manifest_sha256=staged['manifest_sha'], candidate_path=staged['candidate_path'],
            candidate_sha256=staged['candidate'][2], candidate=staged['candidate'], p1_enabled=True)


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


def _fresh_real_generation(c, evidence, pointer_location='index'):
    """Build a second immutable memory with the real producer, not a binding spy."""
    from tests.tools import test_shared_card_route as rt
    from tools import wd_q4_card_generation
    from waggledance.core.bridge_log_reader import BridgeCursor

    old = c['real_plan']
    cursor = old['end_cursor']
    args = dict(enabled=True, decision_bytes=evidence['card_decision_bytes'],
        decision_sha256=sha(evidence['card_decision_bytes']),
        vocabulary_bytes=rt.fx.VOCABULARY, vocabulary_sha256=rt.fx.PINS['vocabulary_sha256'],
        path_allowlist_bytes=rt.fx.PATHS, path_allowlist_sha256=rt.fx.PINS['path_allowlist_sha256'],
        now_utc=rt.fx.NOW, output_root=old['root'], generation_name='fresh-run',
        encode=rt.fx.encode, identity=rt.fx.IDENTITY, dimension=rt.fx.DIM,
        policy_bytes=evidence['policy_bytes'], policy_sha256=sha(evidence['policy_bytes']),
        roster_bytes=evidence['roster_bytes'], roster_sha256=sha(evidence['roster_bytes']),
        card_policy_bytes=evidence['card_policy_bytes'], source_path=old['source_path'],
        generation_path=old['generation_path'], end_cursor=BridgeCursor(**cursor))
    made = wd_q4_card_generation.prepare_card_generation(**args)
    assert made['status'] == 'STAGED'
    memory = Path(old['root']) / 'fresh-run'
    artifacts = {p.relative_to(memory).as_posix(): sha(p.read_bytes())
                 for p in memory.rglob('*') if p.is_file()}
    decision = json.loads(evidence['card_decision_bytes'])
    pointer = enc({'schema': 'wd.q4-card-snapshot.v1', 'generation': 'fresh-run',
        'artifacts': artifacts, 'decision_sha256': args['decision_sha256'],
        'policy_sha256': args['policy_sha256'], 'roster_sha256': args['roster_sha256'],
        'source_prefix': decision['source_prefix']})
    scope = Path(old['root']) if pointer_location == 'index' else Path(c['candidate_path']).parent
    pointer_path = scope / ('q4-pointer-' + sha(pointer) + '.json')
    pointer_path.write_bytes(pointer)
    plan = dict(old, pointer_path=str(pointer_path), pointer_sha256=sha(pointer),
        config=dict(old['config'], memory=str(memory), descriptor=str(memory/'descriptor.json'),
                    descriptor_sha256=artifacts['descriptor.json']))
    plan_raw = enc(plan)
    evidence = dict(evidence, pointer_bytes=pointer, public_plan_bytes=plan_raw,
                    expected_public_plan_sha256=sha(plan_raw))
    binding_result = _REAL_STAGED_BINDING_PRODUCER(**evidence)
    binding = json.loads(binding_result['binding_bytes'])['binding']
    snapshot = transport.fixed_card_bundle(enabled=True,
        **{key:evidence[key+'_bytes'] for key in
           ('pointer','policy','roster','public_policy','card_decision','card_policy')},
        memory=(memory/'memory.json').read_bytes(), projection=(memory/'projection.json').read_bytes(),
        descriptor=(memory/'descriptor.json').read_bytes())
    item = c['candidate']
    descriptor, inputs = dict(item[0]), dict(item[5])
    fixed = json.loads(inputs['fixed_plan_bytes'])
    fixed.update(bundle_sha256=sha(snapshot), bundle_bytes=len(snapshot),
        expected_pointer_sha256=binding['pointer_sha256'], public_plan_sha256=sha(plan_raw))
    fixed_raw = enc(fixed)
    descriptor.update(binding_sha256=binding_result['binding_sha256'], fixed_plan_sha256=sha(fixed_raw),
        snapshot_sha256=sha(snapshot), expected_binding=binding)
    raw = enc(descriptor)
    for key, blob in (('public_plan',plan_raw), ('binding_path',binding_result['binding_bytes']),
                      ('fixed_plan_path',fixed_raw), ('snapshot_path',snapshot)):
        Path(descriptor[key]).write_bytes(blob)
    Path(c['candidate_path']).write_bytes(raw)
    inputs.update(binding_bytes=binding_result['binding_bytes'], fixed_plan_bytes=fixed_raw,
                  fixed_bundle_bytes=snapshot)
    c['candidate'] = (descriptor,raw,sha(raw),item[3],binding,inputs)
    c['config'][Path(c['candidate_path']).parent.name] = c['candidate']
    c['fresh_plan'] = plan
    return pointer_path


@pytest.mark.parametrize('pointer_location', ['index', 'descriptor_generation'])
def test_fresh_real_generation_has_separate_pointer_and_preserves_old_reader(
        real_staged, staged_producer, pointer_location):
    from tools import wd_q4_lane_caller_cli as caller
    c = real_staged
    old_paths = [Path(c['real_plan']['pointer_path'])] + list(Path(c['real_plan']['config']['memory']).rglob('*'))
    old_bytes = {p:p.read_bytes() for p in old_paths if p.is_file()}
    new_pointer = _fresh_real_generation(c, staged_producer, pointer_location)
    assert new_pointer != Path(c['real_plan']['pointer_path'])
    published = do_publish(c)
    chosen = do_select(c)
    assert published['status'] == 'PUBLISHED' and chosen['status'] == 'SELECTED'
    assert chosen['configuration'][2] == c['candidate'][2]
    assert all(p.read_bytes() == raw for p,raw in old_bytes.items())
    descriptor, _, _, _, binding, inputs = c['base']
    caller._require_current_snapshot(descriptor,binding,inputs)
    caller._require_current_snapshot(chosen['configuration'][0],chosen['configuration'][4],chosen['configuration'][5])


@pytest.mark.parametrize('moment', ['before_publish','during_producer','before_select'])
def test_separate_pointer_refuses_mutation_of_the_baseline_pointer(
        real_staged, staged_producer, monkeypatch, moment):
    c = real_staged
    _fresh_real_generation(c, staged_producer)
    old = Path(c['real_plan']['pointer_path'])
    def mutate():
        old.write_bytes(old.read_bytes()+b' ')
    if moment == 'during_producer':
        def produce(**kwargs):
            value = _REAL_STAGED_BINDING_PRODUCER(**kwargs)
            mutate()
            return value
        monkeypatch.setattr(live.binding_producer,'build_staged_lane_binding',produce)
    elif moment == 'before_select':
        do_publish(c)
        mutate()
        with pytest.raises(live.LiveSnapshotRefused): do_select(c)
        return
    else:
        mutate()
    with pytest.raises(live.LiveSnapshotRefused): do_publish(c)
    assert not (c['root']/'q4-live-current.json').exists()
