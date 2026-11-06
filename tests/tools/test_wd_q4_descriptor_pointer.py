import hashlib
import json

import pytest

from tools import wd_q4_descriptor_pointer as subject
from tools.wd_q4_lane_binding import BINDING_KEYS, _roster


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def setup(tmp_path):
    machine = tmp_path / 'machine'
    bundle = tmp_path / 'bundles' / ('a' * 40)
    genroot = tmp_path / 'generations'
    gen = genroot / ('b' * 40)
    for directory in (machine, bundle, gen):
        directory.mkdir(parents=True)
    fleet = {'lanes': [{'agent': x, 'agent_uuid': f'00000000-0000-0000-0000-{i:012d}'}
                       for i, x in enumerate(('codex-lead-1', 'claude-rco-1', 'claude-rco-2', 'fable-5'), 1)],
             'tools_supervisor': {'agent': 'codex-tools-1', 'agent_uuid': '00000000-0000-0000-0000-000000000005'}}
    fleet_bytes = raw(fleet)
    (bundle / 'wd-fleet.json').write_bytes(fleet_bytes)
    (bundle / 'Invoke-WdQ4Query.ps1').write_bytes(b'wrapper')
    manifest = raw({'schema_version': 1, 'source_commit': 'a' * 40, 'files': {
        'wd-fleet.json': sha(fleet_bytes), 'Invoke-WdQ4Query.ps1': sha(b'wrapper')}})
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    lanes, roster_sha = _roster(fleet_bytes, sha(fleet_bytes))
    pins = {key: 'c' * 64 for key in BINDING_KEYS}
    pins['roster_sha256'] = roster_sha
    binding = {'schema': 'wd.q4-lane-binding.v1', 'bundle_commit': 'a' * 40,
               'manifest_sha256': sha(manifest), 'wrapper_sha256': sha(b'wrapper'),
               'public_plan_sha256': 'd' * 64, 'binding': pins, 'lanes': sorted(lanes)}
    binding_bytes = raw(binding)
    (gen / 'lane-binding.v1.json').write_bytes(binding_bytes)
    descriptor = {'schema': 'wd.q4-shared-session-config.v2', 'fleet_sha256': sha(fleet_bytes),
                  'lanes': sorted(lanes), 'bundle_root': str(bundle), 'public_plan': str(gen / 'plan'),
                  'interpreter': str(gen / 'interpreter'), 'cwd': str(gen),
                  'binding_path': str(gen / 'lane-binding.v1.json'), 'fixed_plan_path': str(gen / 'fixed'),
                  'snapshot_path': str(gen / 'snapshot'), 'runtime_manifest_path': str(gen / 'runtime'),
                  'interpreter_sha256': '1' * 64, 'binding_sha256': sha(binding_bytes),
                  'fixed_plan_sha256': '2' * 64, 'snapshot_sha256': '3' * 64,
                  'runtime_manifest_sha256': '4' * 64, 'interpreter_max_bytes': 1, 'timeout_seconds': 1,
                  'max_output_bytes': 1, 'outer': {}, 'expected_binding': pins}
    descriptor_path = gen / 'shared-session-config.v2.json'
    descriptor_path.write_bytes(raw(descriptor))
    state = {'schema_version': 1, 'source_commit': 'a' * 40, 'final_commit': 'a' * 40,
             'active_bundle': str(bundle), 'fleet_manifest': str(bundle / 'wd-fleet.json'),
             'manifest_sha256': sha(manifest).upper(), 'final_manifest_sha256': sha(manifest).upper()}
    state_path = machine / 'WD_REBOOT_STATE_CURRENT.json'
    state_path.write_bytes(raw(state))
    pointer = machine / 'WD_Q4_DESCRIPTOR_CURRENT.json'
    pointer.write_bytes(b'previous')
    return locals()


def call(s):
    return subject.write_descriptor_pointer(state_path=str(s['state_path']), expected_bundle=str(s['bundle']),
        expected_commit='a' * 40, expected_manifest_sha256=sha(s['manifest']).upper(),
        generations_root=str(s['genroot']), generation='b' * 40,
        descriptor_sha256=sha(s['descriptor_path'].read_bytes()), enabled=True)


def test_off_without_access():
    assert subject.write_descriptor_pointer() == {'status': 'OFF'}


def test_success_and_closed_shape(setup):
    originals = {p: p.read_bytes() for root in (setup['bundle'], setup['gen']) for p in root.iterdir() if p.is_file()}
    original_state = setup['state_path'].read_bytes()
    result = call(setup)
    data = setup['pointer'].read_bytes()
    doc = json.loads(data)
    assert set(doc) == {'schema', 'active_bundle', 'manifest_sha256', 'bundle_commit', 'descriptor_path',
                        'descriptor_sha256', 'generation'}
    assert doc['schema'] == 'wd.q4-descriptor-pointer.v1'
    assert result['pointer_sha256'] == sha(data)
    assert original_state == setup['state_path'].read_bytes()
    assert all(p.read_bytes() == content for p, content in originals.items())
    assert sorted(p.name for p in setup['machine'].iterdir()) == ['WD_Q4_DESCRIPTOR_CURRENT.json',
                                                                  'WD_REBOOT_STATE_CURRENT.json']


@pytest.mark.parametrize('field,value', [('active_bundle', 'C:\\foreign'), ('final_commit', 'e' * 40),
                                           ('final_manifest_sha256', 'F' * 64)])
def test_stale_state_refused(setup, field, value):
    setup['state'][field] = value
    setup['state_path'].write_bytes(raw(setup['state']))
    with pytest.raises(subject.PointerRefused):
        call(setup)
    assert setup['pointer'].read_bytes() == b'previous'


def test_binding_manifest_mismatch_refused(setup):
    setup['binding']['manifest_sha256'] = 'e' * 64
    blob = raw(setup['binding'])
    (setup['gen'] / 'lane-binding.v1.json').write_bytes(blob)
    setup['descriptor']['binding_sha256'] = sha(blob)
    setup['descriptor_path'].write_bytes(raw(setup['descriptor']))
    with pytest.raises(subject.PointerRefused):
        call(setup)
    assert setup['pointer'].read_bytes() == b'previous'


def test_reparse_point_refused(setup, monkeypatch):
    orig = subject._is_reparse
    monkeypatch.setattr(subject, '_is_reparse', lambda path, info: path == setup['gen'] or orig(path, info))
    with pytest.raises(subject.PointerRefused):
        call(setup)
    assert setup['pointer'].read_bytes() == b'previous'


def test_failed_atomic_replace_preserves_old_pointer(setup, monkeypatch):
    def fail(*args):
        raise OSError('synthetic crash before replacement')
    monkeypatch.setattr(subject, '_atomic_replace', fail)
    with pytest.raises(subject.PointerOutcomeUnknown):
        call(setup)
    assert setup['pointer'].read_bytes() == b'previous'


def test_failure_after_replace_is_unknown_not_old_claim(setup, monkeypatch):
    replace = subject._atomic_replace
    def commit_then_fail(*args):
        replace(*args)
        raise OSError('synthetic interruption after commit')
    monkeypatch.setattr(subject, '_atomic_replace', commit_then_fail)
    with pytest.raises(subject.PointerOutcomeUnknown):
        call(setup)
    assert json.loads(setup['pointer'].read_bytes())['schema'] == subject.SCHEMA


def test_interrupt_is_not_swallowed_by_exception_handler(setup, monkeypatch):
    monkeypatch.setattr(subject, '_atomic_replace', lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(subject.PointerInterrupted):
        try:
            call(setup)
        except Exception:
            pytest.fail('interrupt was swallowed')
    assert setup['pointer'].read_bytes() == b'previous'


@pytest.mark.parametrize('field,value', [('bundle_root', 'C:\\foreign'), ('fleet_sha256', 'f' * 64),
                                           ('binding_sha256', 'f' * 64)])
def test_foreign_descriptor_refused(setup, field, value):
    setup['descriptor'][field] = value
    setup['descriptor_path'].write_bytes(raw(setup['descriptor']))
    with pytest.raises(subject.PointerRefused):
        call(setup)
    assert setup['pointer'].read_bytes() == b'previous'


@pytest.mark.parametrize('filename', ['wd-fleet.json', 'Invoke-WdQ4Query.ps1', 'deployment-manifest.json'])
def test_modified_installed_file_refused(setup, filename):
    (setup['bundle'] / filename).write_bytes(b'modified')
    with pytest.raises(subject.PointerRefused):
        call(setup)
    assert setup['pointer'].read_bytes() == b'previous'


def test_binding_outside_generation_refused(setup):
    setup['descriptor']['binding_path'] = str(setup['machine'] / 'lane-binding.v1.json')
    setup['descriptor_path'].write_bytes(raw(setup['descriptor']))
    with pytest.raises(subject.PointerRefused):
        call(setup)
    assert setup['pointer'].read_bytes() == b'previous'


def test_state_changed_after_temp_write_is_unknown(setup, monkeypatch):
    real_verify = subject._verify
    count = 0
    def modified(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            setup['state']['final_commit'] = 'e' * 40
            setup['state_path'].write_bytes(raw(setup['state']))
        return real_verify(**kwargs)
    monkeypatch.setattr(subject, '_verify', modified)
    with pytest.raises(subject.PointerOutcomeUnknown):
        call(setup)
    assert setup['pointer'].read_bytes() == b'previous'
