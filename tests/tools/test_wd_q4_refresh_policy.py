"""No live inputs: caller pins synthetic policy, baseline and bundle bytes."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest

from tests.tools.test_wd_q4_descriptor_pointer import setup  # noqa: F401


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def api():
    return importlib.import_module('tools.wd_q4_refresh_policy')


@pytest.fixture
def item(setup, tmp_path):
    runtime = tmp_path/'runtime'; runtime.mkdir()
    snapshots = tmp_path/'approved-snapshots'; snapshots.mkdir()
    anchors = tmp_path/'approved-anchors'; anchors.mkdir()
    source = tmp_path/'source'/'events.jsonl'; source.parent.mkdir(); source.write_bytes(b'{"synthetic":1}\n')
    descriptor = setup['descriptor']
    descriptor['outer'] = {'runtime-root':str(runtime)}
    baseline = encode(descriptor); setup['descriptor_path'].write_bytes(baseline)
    manifest = setup['manifest']; baseline_sha = sha(baseline); manifest_sha = sha(manifest)
    policy = {'schema':'wd.q4-refresh-policy.v1', 'baseline_descriptor_sha256':baseline_sha,
        'deployment_manifest_sha256':manifest_sha, 'snapshot_root':str(snapshots),
        'anchor_root':str(anchors), 'source_path':str(source)}
    return {'setup':setup, 'runtime':runtime, 'snapshot':snapshots, 'anchor':anchors,
            'source':source, 'baseline_sha':baseline_sha, 'manifest_sha':manifest_sha, 'policy':policy}


def inspect(item, policy=None, **overrides):
    raw = encode(item['policy'] if policy is None else policy)
    args = {'enabled':True, 'policy_bytes':raw, 'expected_policy_sha256':sha(raw),
            'baseline_path':str(item['setup']['descriptor_path']), 'expected_baseline_sha256':item['baseline_sha'],
            'expected_manifest_sha256':item['manifest_sha']}
    args.update(overrides)
    return api().validate(**args)


def test_default_off_never_inspects_caller_inputs():
    class NoAccess:
        def __getattribute__(self, _): raise AssertionError('unexpected access')
    assert api().validate(policy_bytes=NoAccess()) == {'status':'OFF'}


def test_valid_policy_checks_bytes_and_disjoint_paths_without_writing(item):
    root = item['source'].parent.parent
    before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
    result = inspect(item)
    assert result['status'] == 'VALIDATED' and result['source_authentication'] == 'UNKNOWN'
    assert result['snapshot_root'] == str(item['snapshot']) and result['anchor_root'] == str(item['anchor'])
    assert result['source_path'] == str(item['source'])
    assert sorted(str(p.relative_to(root)) for p in root.rglob('*')) == before


@pytest.mark.parametrize('case', ['policy_pin', 'extra', 'wrong_schema', 'baseline_pin', 'manifest_pin',
                                   'baseline_changed', 'manifest_changed', 'missing_root', 'overlap_roots',
                                   'bundle_overlap', 'stage_overlap', 'runtime_overlap', 'source_overlap',
                                   'binding_changed', 'fleet_changed', 'wrapper_changed',
                                   'source_in_bundle', 'source_in_stage', 'source_in_runtime'])
def test_policy_refuses_mismatch_and_unsafe_roots(item, case):
    policy = dict(item['policy']); overrides = {}
    if case == 'policy_pin': overrides['expected_policy_sha256'] = '0'*64
    if case == 'extra': policy['unapproved'] = 'x'
    if case == 'wrong_schema': policy['schema'] = 'wd.q4-refresh-policy.v2'
    if case == 'baseline_pin': policy['baseline_descriptor_sha256'] = '0'*64
    if case == 'manifest_pin': policy['deployment_manifest_sha256'] = '0'*64
    if case == 'baseline_changed': item['setup']['descriptor_path'].write_bytes(b'{}')
    if case == 'manifest_changed': (item['setup']['bundle']/'deployment-manifest.json').write_bytes(b'{}')
    if case == 'binding_changed': (item['setup']['gen']/'lane-binding.v1.json').write_bytes(b'{}')
    if case == 'fleet_changed': (item['setup']['bundle']/'wd-fleet.json').write_bytes(b'{}')
    if case == 'wrapper_changed': (item['setup']['bundle']/'Invoke-WdQ4Query.ps1').write_bytes(b'changed')
    if case == 'missing_root': policy['snapshot_root'] = str(item['snapshot']/'missing')
    if case == 'overlap_roots': policy['anchor_root'] = str(item['snapshot'])
    if case == 'bundle_overlap': policy['snapshot_root'] = str(item['setup']['bundle'])
    if case == 'stage_overlap': policy['snapshot_root'] = str(item['setup']['genroot'])
    if case == 'runtime_overlap': policy['snapshot_root'] = str(item['runtime'])
    if case == 'source_overlap': policy['snapshot_root'] = str(item['source'].parent)
    if case == 'source_in_bundle': policy['source_path'] = str(item['setup']['bundle']/'wd-fleet.json')
    if case == 'source_in_stage': policy['source_path'] = str(item['setup']['descriptor_path'])
    if case == 'source_in_runtime':
        path = item['runtime']/'synthetic'; path.write_bytes(b'x'); policy['source_path'] = str(path)
    with pytest.raises(ValueError): inspect(item, policy, **overrides)


@pytest.mark.parametrize('field', ['snapshot_root','anchor_root','source_path'])
def test_reparse_components_are_refused(item, field, monkeypatch):
    from tools import wd_q4_descriptor_pointer as pointer
    bad = Path(item['policy'][field]); original = pointer._is_reparse
    monkeypatch.setattr(pointer, '_is_reparse', lambda path, info: path == bad or original(path, info))
    with pytest.raises(ValueError): inspect(item)


def test_duplicate_keys_and_non_boolean_enabled_are_refused(item):
    raw = encode(item['policy'])
    duplicate = raw[:-1] + b',"schema":"wd.q4-refresh-policy.v1"}'
    with pytest.raises(ValueError): inspect(item, policy_bytes=duplicate, expected_policy_sha256=sha(duplicate))
    with pytest.raises(ValueError): inspect(item, enabled=1)


def test_binding_changed_between_two_checks_is_refused(item, monkeypatch):
    from tools import wd_q4_descriptor_pointer as pointer
    original = pointer._verify; calls = []
    def mutate(**kwargs):
        result = original(**kwargs); calls.append(1)
        if len(calls) == 1:
            (item['setup']['gen']/'lane-binding.v1.json').write_bytes(b'changed')
        return result
    monkeypatch.setattr(pointer, '_verify', mutate)
    with pytest.raises(ValueError): inspect(item)
    assert len(calls) == 1
