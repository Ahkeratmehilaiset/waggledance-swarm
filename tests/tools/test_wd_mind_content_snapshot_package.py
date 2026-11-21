"""Synthetic-only producer contract; no native index, model or live source."""
import importlib.util
import builtins
import hashlib
import json
import struct
import sys
import threading
from pathlib import Path

import pytest

from tools import wd_mind_content_query_readonly as reader


ROOT = Path(__file__).resolve().parents[2]
CAPACITY_PROFILE = "bounded_v3_capacity_v1"
FULL_PREFIX_PROFILE = "full_prefix_stream_v1"


def _flat_owner_fixture(package, fixtures, tmp_path, monkeypatch, fault=None):
    from datetime import datetime, timezone
    case, codec, output, snapshot, bundle, deploy, narrow, run = _narrow_fixture(package, fixtures, tmp_path)
    controls = (snapshot['messages']['source_path'], snapshot['messages']['generation_path'],
                'a' * 64, snapshot['roster_bytes'], snapshot['messages']['policy_bytes'])
    monkeypatch.setattr(package, '_consumer_bundle_controls', lambda root, pin: controls)
    class Clock(datetime):
        value = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz=None): return cls.value
    monkeypatch.setattr(package, 'datetime', Clock)
    projection = json.loads(snapshot['projection_bytes'])
    ids = tuple(sorted(row['record_id'] for row in projection['messages']['records']))
    vector = struct.pack('<384f', 1.0, *([0.0] * 383))
    vectors = vector * len(ids)
    pairs = hashlib.sha256()
    for record_id in ids:
        pairs.update(record_id.encode() + b'\0' + vector)
    encoder = output['encoder_binding_bytes']
    monkeypatch.setattr(package, '_consumer_flat_encoder', lambda root, pin: encoder)
    manifest = {'schema':'wd.mind-flat-f32-memory.v1', 'authority':'none',
                'projection_sha256': snapshot['projection_sha256'], 'records': len(ids),
                'encoder_binding_sha256': reader._sha(encoder), 'ids_sha256': reader._sha(reader._canonical(list(ids))),
                'vectors_sha256': reader._sha(vectors), 'pairs_sha256': pairs.hexdigest()}
    if fault == 'schema': manifest['schema'] = 'wd.bridge-mind-hex-memory-step2.v1'
    if fault == 'projection': manifest['projection_sha256'] = '0' * 64
    if fault == 'vector': vectors = b'\0' * len(vectors)
    if fault == 'vector_norm':
        vector = b'\0' * 1536
        vectors = vector * len(ids)
        pairs = hashlib.sha256()
        for record_id in ids:
            pairs.update(record_id.encode() + b'\0' + vector)
        manifest['vectors_sha256'] = reader._sha(vectors)
        manifest['pairs_sha256'] = pairs.hexdigest()
    if fault == 'pairs': manifest['pairs_sha256'] = '0' * 64
    if fault == 'ids': ids = ids[::-1]
    manifest_bytes = reader._canonical(manifest)
    owner = package.create_consumer_flat_source_owner(enabled=True, bundle_root=str(bundle),
        expected_manifest_sha256='b' * 64, snapshot=snapshot, flat_manifest_bytes=manifest_bytes,
        flat_manifest_sha256=reader._sha(manifest_bytes), ids=ids, vectors=vectors,
        encoder_binding_bytes=encoder)
    hits = run()['results']
    proposals = [{'ref_sha256': hit['ref_sha256'], 'doc_id_sha256': hit['doc_id_sha256'],
                  'source_event_sha256': hit['source_reference']['sha256']} for hit in hits]
    return owner, proposals, snapshot, Clock


def test_flat_consumer_owner_resolves_actual_source(package, fixtures, tmp_path, monkeypatch):
    owner, proposals, _, _ = _flat_owner_fixture(package, fixtures, tmp_path, monkeypatch)
    result = owner.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')
    assert result['authority'] == 'none' and result['index_match'] == 'UNKNOWN' and result['results']


def test_flat_consumer_owner_allows_verified_historical_source(package, fixtures, tmp_path, monkeypatch):
    from datetime import timedelta
    owner, proposals, _, clock = _flat_owner_fixture(package, fixtures, tmp_path, monkeypatch)
    clock.value += timedelta(days=2)
    result = owner.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')
    assert result['results']
    assert all(hit['event_age_seconds_upper_bound'] >= 900 and hit['event_time_utc_floor']
               for hit in result['results'])


@pytest.mark.parametrize('fault', ['schema','projection','vector','vector_norm','pairs','ids'])
def test_flat_consumer_owner_rejects_bad_format_or_memory(package, fixtures, tmp_path, monkeypatch, fault):
    with pytest.raises(package.SnapshotPackageRefused):
        _flat_owner_fixture(package, fixtures, tmp_path, monkeypatch, fault)


@pytest.mark.parametrize('fault', ['reference','future','row','scope','controls','encoder_controls'])
def test_flat_consumer_owner_rejects_untrusted_proposal(package, fixtures, tmp_path, monkeypatch, fault):
    from datetime import timedelta
    owner, proposals, snapshot, clock = _flat_owner_fixture(package, fixtures, tmp_path, monkeypatch)
    if fault == 'reference': proposals[0]['source_event_sha256'] = '0' * 64
    elif fault == 'future': clock.value -= timedelta(minutes=20)
    elif fault == 'row':
        path = Path(snapshot['messages']['source_path'])
        lines = path.read_bytes().splitlines(keepends=True)
        index = next(i for i, line in enumerate(lines)
                     if reader._sha(reader._canonical(json.loads(line))) == proposals[0]['source_event_sha256'])
        old = lines[index]; lines[index] = old.replace(b'"message":', b'"messagE":', 1)
        assert lines[index] != old and len(lines[index]) == len(old)
        path.write_bytes(b''.join(lines))
    elif fault == 'controls': monkeypatch.setattr(package, '_consumer_bundle_controls', lambda root, pin: None)
    elif fault == 'encoder_controls': monkeypatch.setattr(package, '_consumer_flat_encoder', lambda root, pin: b'changed')
    with pytest.raises(package.SnapshotPackageRefused):
        owner.resolve(proposals=proposals, lane='claude-rco-1' if fault == 'scope' else 'codex-lead-1',
                      scope='own' if fault == 'scope' else 'shared')


@pytest.mark.parametrize('micros', [899000000, 899000001, 900000000])
def test_flat_consumer_owner_reports_historical_delivery_age(package, fixtures, tmp_path, monkeypatch, micros):
    from datetime import datetime, timedelta, timezone
    owner, proposals, _, clock = _flat_owner_fixture(package, fixtures, tmp_path, monkeypatch)
    clock.value = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=micros)
    result = owner.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')
    assert all(hit['event_age_seconds_upper_bound'] == (micros + 999999) // 1000000
               for hit in result['results'])


def test_flat_consumer_rechecks_encoder_after_source_resolve(package, fixtures, tmp_path, monkeypatch):
    owner, proposals, _, _ = _flat_owner_fixture(package, fixtures, tmp_path, monkeypatch)
    fence = reader._narrow_fence
    def change_after_source_fence(*args, **kwargs):
        result = fence(*args, **kwargs)
        monkeypatch.setattr(package, '_consumer_flat_encoder', lambda root, pin: b'changed-binding')
        return result
    monkeypatch.setattr(reader, '_narrow_fence', change_after_source_fence)
    with pytest.raises(package.SnapshotPackageRefused):
        owner.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')


@pytest.mark.parametrize('optimized', [False, True])
def test_provider_cannot_forge_watcher_hold_or_leak_message(package, fixtures, tmp_path, optimized):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    args = _arguments(case, Codec(fixtures), FULL_PREFIX_PROFILE)
    def forged(_):
        raise package.SnapshotPackageHold('synthetic-private-hold-detail')
    args['passage_encoder'] = forged
    options = {'source_verification': 'verified_immutable_snapshot_v1'} if optimized else {}
    with pytest.raises(package.SnapshotPackageRefused) as caught:
        package.package_content_snapshot(**args, **options)
    assert type(caught.value) is package.SnapshotPackageRefused
    assert str(caught.value) == 'content_snapshot_package_refused'
    assert caught.value.__context__ is None
    assert package.snapshot_build_watcher_status() == {'state': 'idle'}


@pytest.mark.parametrize('signal', ['keyboard', 'cancelled'])
def test_immutable_watcher_hold_does_not_mask_cancellation(package, fixtures, tmp_path, monkeypatch, signal):
    import asyncio
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    args = _arguments(case, Codec(fixtures), FULL_PREFIX_PROFILE)
    entered, release = threading.Event(), threading.Event()
    verify = reader._verified_complete
    def blocked(snapshot):
        if threading.current_thread().name == 'mind-source-verifier':
            entered.set()
            assert release.wait(15)
        return verify(snapshot)
    def cancel(batch):
        assert entered.wait(5)
        if signal == 'keyboard':
            raise KeyboardInterrupt('synthetic-private-keyboard-detail')
        raise asyncio.CancelledError('synthetic-private-cancellation-detail')
    monkeypatch.setattr(reader, '_verified_complete', blocked)
    monkeypatch.setattr(package, '_WATCH_INTERVAL_SECONDS', 0.01)
    monkeypatch.setattr(package, '_WATCH_JOIN_TIMEOUT_SECONDS', 0.05)
    args['passage_encoder'] = cancel
    kind = KeyboardInterrupt if signal == 'keyboard' else asyncio.CancelledError
    try:
        with pytest.raises(kind) as caught:
            package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
        assert caught.value.args == () and caught.value.__context__ is None
        assert package.snapshot_build_watcher_status() == {'state': 'watcher_stopping'}
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name == 'mind-source-verifier':
                thread.join(10)
    assert package.snapshot_build_watcher_status() == {'state': 'idle'}


def test_narrow_source_manifest_requires_complete_verified_build_and_external_anchor(package, fixtures, tmp_path):
    """A hash next to untrusted bytes is not a deployment approval."""
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, FULL_PREFIX_PROFILE))
    snapshot = {key: case[key] for key in reader._Q4_SOURCE_KEYS}
    narrow = package.build_narrow_source_manifest(enabled=True, snapshot=snapshot,
        descriptor_bytes=output['descriptor_bytes'], descriptor_sha256=output['descriptor_sha256'],
        memory_bytes=output['memory_bytes'], memory_sha256=output['memory_sha256'])
    bundle = tmp_path / 'synthetic-bundle'
    bundle.mkdir()
    (bundle / 'wd-mind-narrow-source-manifest.json').write_bytes(narrow['manifest_bytes'])
    deploy = fixtures._raw({'schema_version': 1, 'files': {
        'wd-mind-narrow-source-manifest.json': narrow['manifest_sha256']}})
    (bundle / 'deployment-manifest.json').write_bytes(deploy)
    encoder, index = reader.adapt_complete_readonly_providers(
        encoder=case['encoder_provider'], index_decoder=codec.decode,
        **{key: output[key] for key in ('encoder_binding_bytes', 'encoder_binding_sha256',
                                       'index_binding_bytes', 'index_binding_sha256')})
    def run(pin):
        stream = fixtures._ObservedStream(fixtures._raw(
            {'situation': 'retry network outage', 'time': fixtures.QUERY_TIME}))
        return reader.query_narrow_content_snapshot(
            bundle_root=str(bundle), expected_deployment_manifest_sha256=pin,
            **snapshot, **output, encoder_provider=encoder, index_provider=index, stream=stream)
    with pytest.raises(reader.ContentQueryRefused):
        run('0' * 64)
    answer = run(fixtures._sha(deploy))
    assert answer['status'] == 'ANSWER'
    assert answer['authority'] == 'none'
    assert answer['results'] and all(hit['source_reference']['verified'] == 'MATCH_SELECTED_ROW'
                                     for hit in answer['results'])


def _narrow_fixture(package, fixtures, tmp_path, *, generationless=False, rows=None):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    if rows is not None:
        source = fixtures._all_tasks_case(reader, tmp_path, rows)
        case.update(fixtures._snapshot(source, reader.rederive_complete_message_projection(**source)))
    if generationless:
        from waggledance.core.bridge_log_reader import BridgeCursor
        cursor = case['messages']['end_cursor']
        case['messages']['end_cursor'] = BridgeCursor(cursor.offset, cursor.file_identity, None)
        Path(case['messages']['generation_path']).unlink()
        case['messages']['generation_path'] = str(Path(case['messages']['source_path']).with_name('events.generation.json'))
        decision = json.loads(case['decision_bytes'])
        decision['source_prefix']['generation'] = None
        case['decision_bytes'] = fixtures._raw(decision)
        case['decision_sha256'] = reader._sha(case['decision_bytes'])
        source = {key: case[key] for key in reader._INPUT_KEYS}
        case.update(fixtures._snapshot(source, reader.rederive_complete_message_projection(**source)))
    codec = Codec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, FULL_PREFIX_PROFILE))
    snapshot = {key: case[key] for key in reader._Q4_SOURCE_KEYS}
    narrow = package.build_narrow_source_manifest(enabled=True, snapshot=snapshot,
        descriptor_bytes=output['descriptor_bytes'], descriptor_sha256=output['descriptor_sha256'],
        memory_bytes=output['memory_bytes'], memory_sha256=output['memory_sha256'])
    bundle = tmp_path / 'synthetic-bundle'
    bundle.mkdir()
    (bundle / 'wd-mind-narrow-source-manifest.json').write_bytes(narrow['manifest_bytes'])
    deploy = fixtures._raw({'schema_version': 1, 'files': {
        'wd-mind-narrow-source-manifest.json': narrow['manifest_sha256']}})
    (bundle / 'deployment-manifest.json').write_bytes(deploy)
    encoder, index = reader.adapt_complete_readonly_providers(
        encoder=case['encoder_provider'], index_decoder=codec.decode,
        **{key: output[key] for key in ('encoder_binding_bytes', 'encoder_binding_sha256',
                                       'index_binding_bytes', 'index_binding_sha256')})
    def run(pin=None):
        stream = fixtures._ObservedStream(fixtures._raw(
            {'situation': 'retry network outage', 'time': fixtures.QUERY_TIME}))
        return reader.query_narrow_content_snapshot(
            bundle_root=str(bundle), expected_deployment_manifest_sha256=pin or fixtures._sha(deploy),
            **snapshot, **output, encoder_provider=encoder, index_provider=index, stream=stream)
    return case, codec, output, snapshot, bundle, deploy, narrow, run


def _consumer_owner_fixture(package, fixtures, tmp_path, monkeypatch):
    """Exercise the real row resolver with synthetic deployment controls."""
    from datetime import datetime, timezone
    case, codec, output, snapshot, bundle, deploy, narrow, run = _narrow_fixture(package, fixtures, tmp_path)
    controls = (snapshot['messages']['source_path'], snapshot['messages']['generation_path'],
                'a' * 64, snapshot['roster_bytes'], snapshot['messages']['policy_bytes'])
    monkeypatch.setattr(package, '_consumer_bundle_controls', lambda root, pin: controls)
    class Clock(datetime):
        value = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.value
    monkeypatch.setattr(package, 'datetime', Clock)
    owner = package.create_consumer_source_owner(enabled=True, bundle_root=str(bundle),
        expected_manifest_sha256='b' * 64, snapshot=snapshot,
        **{key: output[key] for key in ('descriptor_bytes','descriptor_sha256','memory_bytes','memory_sha256')})
    hits = run()['results']
    proposals = [{'ref_sha256': hit['ref_sha256'], 'doc_id_sha256': hit['doc_id_sha256'],
                  'source_event_sha256': hit['source_reference']['sha256']} for hit in hits]
    return owner, proposals, snapshot, Clock


def test_consumer_owner_independently_resolves_source_rows(package, fixtures, tmp_path, monkeypatch):
    owner, proposals, _, _ = _consumer_owner_fixture(package, fixtures, tmp_path, monkeypatch)
    answer = owner.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')
    assert answer['authority'] == 'none' and answer['sender_authentication'] == 'UNKNOWN'
    assert answer['index_match'] == 'UNKNOWN' and answer['same_user_aba'] == 'UNKNOWN'
    assert [hit['source_event_sha256'] for hit in answer['results']] == [row['source_event_sha256'] for row in proposals]


@pytest.mark.parametrize('fault', ['reference', 'doc', 'source', 'future', 'row', 'scope', 'controls'])
def test_consumer_owner_refuses_untrusted_or_stale_proposals(package, fixtures, tmp_path, monkeypatch, fault):
    from datetime import timedelta
    owner, proposals, snapshot, clock = _consumer_owner_fixture(package, fixtures, tmp_path, monkeypatch)
    if fault == 'reference': proposals[0]['ref_sha256'] = '0' * 64
    elif fault == 'doc': proposals[0]['doc_id_sha256'] = '0' * 64
    elif fault == 'source': proposals[0]['source_event_sha256'] = '0' * 64
    elif fault == 'future': clock.value -= timedelta(minutes=20)
    elif fault == 'row':
        path = Path(snapshot['messages']['source_path'])
        lines = path.read_bytes().splitlines(keepends=True)
        selected = next(i for i, line in enumerate(lines)
                        if reader._sha(reader._canonical(json.loads(line))) == proposals[0]['source_event_sha256'])
        original = lines[selected]
        lines[selected] = original.replace(b'"message":', b'"messagE":', 1)
        assert lines[selected] != original and len(lines[selected]) == len(original)
        path.write_bytes(b''.join(lines))
    elif fault == 'controls': monkeypatch.setattr(package, '_consumer_bundle_controls', lambda root, pin: None)
    lane = 'claude-rco-1' if fault == 'scope' else 'codex-lead-1'
    scope = 'own' if fault == 'scope' else 'shared'
    with pytest.raises(package.SnapshotPackageRefused, match='^content_snapshot_package_refused$'):
        owner.resolve(proposals=proposals, lane=lane, scope=scope)


def test_consumer_owner_is_default_off(package):
    with pytest.raises(package.SnapshotPackageRefused):
        package.create_consumer_source_owner()


@pytest.mark.parametrize('fault', ['none', 'manifest', 'fleet', 'policy', 'code', 'wrong_origin'])
def test_consumer_bundle_controls_pin_own_code_fleet_and_policy(package, fixtures, tmp_path, monkeypatch, fault):
    from tools import wd_mind_absent_generation_prefix_readonly as absent
    from waggledance.core import bridge_log_reader as bridge
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    bundle = tmp_path / 'verified-bundle'
    bundle.mkdir()
    runtime = tmp_path / 'runtime'
    shared = runtime / 'shared'
    shared.mkdir(parents=True)
    (shared / 'events.jsonl').write_bytes(b'{}\n')
    roster = json.loads(case['roster_bytes'])
    fleet = reader._canonical({'schema_version': 2, 'runtime_root': str(runtime),
                               'lanes': roster['lanes'], 'tools_supervisor': roster['tools_supervisor']})
    policy = case['messages']['policy_bytes']
    files = {}
    for name, data in [('wd-fleet.json', fleet), ('wd-mind-message-policy.json', policy)]:
        (bundle / name).write_bytes(data)
        files[name] = reader._sha(data)
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setattr(reader, '_narrow_dependencies', lambda: (bridge, absent))
    modules = ((package, 'tools-bootstrap/tools/wd_mind_content_snapshot_package.py'),
               (reader, 'tools-bootstrap/tools/wd_mind_content_query_readonly.py'),
               (absent, 'tools-bootstrap/tools/wd_mind_absent_generation_prefix_readonly.py'),
               (bridge, 'tools-bootstrap/waggledance/core/bridge_log_reader.py'))
    for module, relative in modules:
        data = Path(module.__file__).read_bytes()
        dest = bundle.joinpath(*relative.split('/'))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        files[relative] = reader._sha(data)
        monkeypatch.setattr(module, '__file__', str(dest))
    raw = reader._canonical({'schema_version': 1, 'files': files})
    (bundle / 'deployment-manifest.json').write_bytes(raw)
    pin = reader._sha(raw)
    if fault == 'manifest': pin = '0' * 64
    elif fault == 'fleet': (bundle / 'wd-fleet.json').write_bytes(b'{}')
    elif fault == 'policy': (bundle / 'wd-mind-message-policy.json').write_bytes(b'{}')
    elif fault == 'code': (bundle / modules[0][1]).write_bytes(b'# altered')
    elif fault == 'wrong_origin': monkeypatch.setattr(package, '__file__', str(bundle / 'elsewhere.py'))
    if fault == 'none':
        controls = package._consumer_bundle_controls(str(bundle), pin)
        assert controls == (str(shared / 'events.jsonl'), str(shared / 'events.generation.json'),
                            reader._sha(fleet), case['roster_bytes'], policy)
    else:
        with pytest.raises((ValueError, OSError)):
            package._consumer_bundle_controls(str(bundle), pin)


def test_consumer_owner_real_synthetic_bundle_chain(tmp_path):
    """Import pinned files from a synthetic bundle and check the whole chain.

    The subprocess is isolated from the already-imported repository modules.
    Every source path and byte belongs to this fixture, never the live bundle.
    """
    import subprocess
    script = r'''
import hashlib, json, shutil, struct, sys
from pathlib import Path
from datetime import datetime, timezone

repo, test_root = Path(sys.argv[1]), Path(sys.argv[2])
bundle, runtime = test_root / 'bundle', test_root / 'runtime'
bootstrap, shared = bundle / 'tools-bootstrap', runtime / 'shared'
bootstrap.mkdir(parents=True); shared.mkdir(parents=True)
paths = ['tools/__init__.py', 'tools/wd_bridge_mind.py', 'waggledance/core/bridge_log_reader.py',
         'tools/wd_mind_content_prefix_readonly.py', 'tools/wd_mind_absent_generation_prefix_readonly.py',
         'tools/bridge_compact_view.py', 'waggledance/__init__.py', 'waggledance/core/__init__.py',
         'tools/wd_mind_content_query_readonly.py', 'tools/wd_mind_content_snapshot_package.py']
for path in paths:
    dest = bootstrap / path; dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(repo / path, dest)
sys.path.insert(0, str(bootstrap)); sys.path.insert(1, str(repo / 'tests' / 'tools'))
from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_content_snapshot_package as package
from waggledance.core.bridge_log_reader import BridgeCursor
import test_wd_mind_content_query_readonly as fixtures
import test_wd_mind_content_snapshot_package as helpers

case = fixtures._semantic_case(reader, shared, all_tasks=True)
source = shared / 'events.jsonl'
Path(case['messages']['source_path']).rename(source)
Path(case['messages']['generation_path']).unlink()
cursor = case['messages']['end_cursor']
case['messages'].update(source_path=str(source), generation_path=str(shared / 'events.generation.json'),
                        end_cursor=BridgeCursor(cursor.offset, cursor.file_identity, None))
decision = json.loads(case['decision_bytes']); decision['source_prefix']['generation'] = None
case['decision_bytes'] = fixtures._raw(decision); case['decision_sha256'] = reader._sha(case['decision_bytes'])
source_input = {key: case[key] for key in reader._INPUT_KEYS}
case.update(fixtures._snapshot(source_input, reader.rederive_complete_message_projection(**source_input)))
codec = helpers.Codec(fixtures)
output = package.package_content_snapshot(**helpers._arguments(case, codec, helpers.FULL_PREFIX_PROFILE))
snapshot = {key: case[key] for key in reader._Q4_SOURCE_KEYS}
roster = json.loads(snapshot['roster_bytes'])
fleet = reader._canonical({'schema_version': 2, 'runtime_root': str(runtime),
                           'lanes': roster['lanes'], 'tools_supervisor': roster['tools_supervisor']})
policy = snapshot['messages']['policy_bytes']
(bundle / 'wd-fleet.json').write_bytes(fleet); (bundle / 'wd-mind-message-policy.json').write_bytes(policy)
files = {'wd-fleet.json': reader._sha(fleet), 'wd-mind-message-policy.json': reader._sha(policy)}
for path in paths: files['tools-bootstrap/' + path] = reader._sha((bootstrap / path).read_bytes())
worker_relative = 'tools-bootstrap/tools/wd_mind_e5_worker.py'
worker = bundle.joinpath(*worker_relative.split('/'))
worker.write_bytes(b'# synthetic pinned worker; not executed\n')
files[worker_relative] = reader._sha(worker.read_bytes())
flat_encoder = json.loads(output['encoder_binding_bytes'])
flat_encoder['code_sha256'] = files[worker_relative]
flat_encoder_raw = reader._canonical(flat_encoder)
runtime_binding = reader._canonical({'schema':'wd.mind-memory-encoder-binding.v1',
    'encoder':flat_encoder['encoder'], 'code_sha256':files[worker_relative],
    'query_policy':flat_encoder['query_policy'],
    'passage_policy':dict(flat_encoder['query_policy'], prefix='passage: ')})
(bundle / 'wd-mind-e5-encoder-binding.json').write_bytes(runtime_binding)
files['wd-mind-e5-encoder-binding.json'] = reader._sha(runtime_binding)
manifest = reader._canonical({'schema_version': 1, 'files': files})
(bundle / 'deployment-manifest.json').write_bytes(manifest)
class Clock(datetime):
    value = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)
    @classmethod
    def now(cls, tz=None): return cls.value
package.datetime = Clock
inputs = {key: output[key] for key in ('descriptor_bytes', 'descriptor_sha256', 'memory_bytes', 'memory_sha256')}
owner = package.create_consumer_source_owner(enabled=True, bundle_root=str(bundle),
    expected_manifest_sha256=reader._sha(manifest), snapshot=snapshot, **inputs)
encoder, index = reader.adapt_complete_readonly_providers(encoder=case['encoder_provider'], index_decoder=codec.decode,
    **{key: output[key] for key in ('encoder_binding_bytes','encoder_binding_sha256','index_binding_bytes','index_binding_sha256')})
stream = fixtures._ObservedStream(fixtures._raw({'situation':'retry network outage','time':fixtures.QUERY_TIME}))
answer = reader.query_complete_content_snapshot(**snapshot, **output, encoder_provider=encoder, index_provider=index, stream=stream)
proposals = [{'ref_sha256': hit['ref_sha256'], 'doc_id_sha256': hit['doc_id_sha256'],
              'source_event_sha256':hit['source_reference']['sha256']} for hit in answer['results']]
result = owner.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')
assert result['authority'] == 'none' and result['index_match'] == 'UNKNOWN' and result['results']
ids = tuple(sorted(row['record_id'] for row in json.loads(snapshot['projection_bytes'])['messages']['records']))
vector = struct.pack('<384f', 1.0, *([0.0] * 383)); vectors = vector * len(ids)
pairs = hashlib.sha256()
for record_id in ids: pairs.update(record_id.encode() + b'\0' + vector)
flat_raw = reader._canonical({'schema':'wd.mind-flat-f32-memory.v1', 'authority':'none',
    'projection_sha256':snapshot['projection_sha256'], 'records':len(ids),
    'encoder_binding_sha256':reader._sha(flat_encoder_raw), 'ids_sha256':reader._sha(reader._canonical(list(ids))),
    'vectors_sha256':reader._sha(vectors), 'pairs_sha256':pairs.hexdigest()})
flat = package.create_consumer_flat_source_owner(enabled=True, bundle_root=str(bundle),
    expected_manifest_sha256=reader._sha(manifest), snapshot=snapshot,
    flat_manifest_bytes=flat_raw, flat_manifest_sha256=reader._sha(flat_raw),
    ids=ids, vectors=vectors, encoder_binding_bytes=flat_encoder_raw)
flat_result = flat.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')
assert flat_result['results'] and flat_result['index_match'] == 'UNKNOWN'
worker_raw = worker.read_bytes(); worker.write_bytes(b'# modified worker\n')
try:
    flat.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')
    raise AssertionError('changed pinned E5 worker accepted')
except package.SnapshotPackageRefused:
    pass
worker.write_bytes(worker_raw)
original_fence = reader._narrow_fence
def change_worker_during_source_check(*args, **kwargs):
    value = original_fence(*args, **kwargs)
    worker.write_bytes(b'# worker changed after first E5-pin check\n')
    return value
reader._narrow_fence = change_worker_during_source_check
try:
    flat.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')
    raise AssertionError('mid-query E5 worker change accepted')
except package.SnapshotPackageRefused:
    pass
finally:
    reader._narrow_fence = original_fence
    worker.write_bytes(worker_raw)
before = source.read_bytes(); lines = before.splitlines(keepends=True)
selected = next(i for i, line in enumerate(lines) if reader._sha(reader._canonical(json.loads(line))) == proposals[0]['source_event_sha256'])
lines[selected] = lines[selected].replace(b'"message":', b'"messagE":', 1)
assert b''.join(lines) != before and len(b''.join(lines)) == len(before)
source.write_bytes(b''.join(lines))
try:
    owner.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')
    raise AssertionError('changed selected source row accepted')
except package.SnapshotPackageRefused:
    pass
try:
    flat.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')
    raise AssertionError('changed flat selected row accepted')
except package.SnapshotPackageRefused:
    pass
print('synthetic bundle control and selected-row verification passed')
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(ROOT), str(tmp_path)],
                            text=True, capture_output=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'synthetic bundle control and selected-row verification passed'


@pytest.mark.parametrize('selected', [False, True])
def test_narrow_read_checks_selected_rows_but_allows_unselected_corruption(package, fixtures, tmp_path, selected):
    case, codec, output, _, _, _, _, run = _narrow_fixture(package, fixtures, tmp_path)
    original = run()
    selected_refs = {hit['source_reference']['sha256'] for hit in original['results']}
    path = Path(case['messages']['source_path'])
    lines = path.read_bytes().splitlines(keepends=True)
    refs = [fixtures._sha(fixtures._raw(json.loads(line))) for line in lines]
    chosen = next(index for index, ref in enumerate(refs) if (ref in selected_refs) is selected)
    assert lines[chosen].endswith(b'}\n')
    lines[chosen] = lines[chosen][:-2] + b'!\n'
    path.write_bytes(b''.join(lines))
    with pytest.raises(reader.ContentQueryRefused):
        _complete_query(case, output, codec)
    if selected:
        with pytest.raises(reader.ContentQueryRefused):
            run()
    else:
        assert run()['results'] == original['results']


@pytest.mark.parametrize('fault', ['append', 'truncate', 'generation', 'replace'])
def test_narrow_read_fences_source_identity_generation_and_length(package, fixtures, tmp_path, fault):
    case, _, _, _, _, _, _, run = _narrow_fixture(package, fixtures, tmp_path)
    assert run()['results']
    path = Path(case['messages']['source_path'])
    original = path.read_bytes()
    if fault == 'append':
        with path.open('ab') as stream:
            stream.write(fixtures._raw(fixtures._row('synthetic later event')) + b'\n')
    elif fault == 'truncate':
        path.write_bytes(original[:-1])
    elif fault == 'generation':
        Path(case['messages']['generation_path']).write_bytes(fixtures._raw({'generation': 'synthetic-g2'}))
    else:
        replacement = path.with_name('synthetic-replacement.jsonl')
        replacement.write_bytes(original)
        replacement.replace(path)
    if fault == 'append':
        assert run()['results']
    else:
        with pytest.raises(reader.ContentQueryRefused):
            run()


def test_narrow_read_refuses_generation_sidecar_retarget(package, fixtures, tmp_path):
    case, _, _, snapshot, _, _, _, run = _narrow_fixture(package, fixtures, tmp_path)
    assert run()['results']
    original = Path(case['messages']['generation_path'])
    stale = tmp_path / 'synthetic-stale-generation.json'
    stale.write_bytes(original.read_bytes())
    original.write_bytes(fixtures._raw({'generation': 'synthetic-g2'}))
    snapshot['messages']['generation_path'] = str(stale)
    with pytest.raises(reader.ContentQueryRefused):
        run()


def test_narrow_deployment_pin_refuses_reanchored_manifest_tampering(package, fixtures, tmp_path):
    _, _, _, _, bundle, _, narrow, run = _narrow_fixture(package, fixtures, tmp_path)
    assert run()['results']
    manifest = json.loads(narrow['manifest_bytes'])
    entry = next(iter(manifest['entries'].values()))
    entry['offset'] += 1
    changed = reader._canonical(manifest)
    (bundle / 'wd-mind-narrow-source-manifest.json').write_bytes(changed)
    forged_deploy = fixtures._raw({'schema_version': 1, 'files': {
        'wd-mind-narrow-source-manifest.json': fixtures._sha(changed)}})
    (bundle / 'deployment-manifest.json').write_bytes(forged_deploy)
    with pytest.raises(reader.ContentQueryRefused):
        run()


@pytest.mark.parametrize('fault', ['middle', 'wrong_row', 'raw_hash', 'chunk'])
def test_narrow_read_refuses_forged_but_pinned_offsets_and_chunks(package, fixtures, tmp_path, fault):
    case, _, _, _, bundle, _, narrow, run = _narrow_fixture(package, fixtures, tmp_path)
    result = run()
    selected = result['results'][0]['source_reference']['sha256']
    manifest = json.loads(narrow['manifest_bytes'])
    entry = manifest['entries'][selected]
    if fault == 'middle':
        entry['offset'] += 1
    elif fault == 'wrong_row':
        other = next(ref for ref in manifest['entries'] if ref != selected)
        manifest['entries'][selected] = dict(manifest['entries'][other])
    elif fault == 'raw_hash':
        entry['raw_sha256'] = '0' * 64
    else:
        source = Path(case['messages']['source_path'])
        raw = source.read_bytes()
        at = entry['offset']
        end = at + entry['length']
        line = raw[at:end]
        changed = line.replace(b'network retry', b'changed retry', 1)
        assert changed != line and len(changed) == len(line)
        source.write_bytes(raw[:at] + changed + raw[end:])
        entry['raw_sha256'] = fixtures._sha(changed)
    narrow_bytes = reader._canonical(manifest)
    (bundle / 'wd-mind-narrow-source-manifest.json').write_bytes(narrow_bytes)
    deploy = fixtures._raw({'schema_version': 1, 'files': {
        'wd-mind-narrow-source-manifest.json': fixtures._sha(narrow_bytes)}})
    (bundle / 'deployment-manifest.json').write_bytes(deploy)
    # Even with a separately pinned changed deployment manifest, source event
    # identity and the exact projected chunk must still be re-derived.
    with pytest.raises(reader.ContentQueryRefused):
        run(fixtures._sha(deploy))


def test_narrow_builder_refuses_changed_source_even_when_projection_was_valid(package, fixtures, tmp_path):
    case, _, output, snapshot, _, _, _, _ = _narrow_fixture(package, fixtures, tmp_path)
    source = Path(case['messages']['source_path'])
    source.write_bytes(source.read_bytes().replace(b'network retry', b'changed retry', 1))
    with pytest.raises(package.SnapshotPackageRefused):
        package.build_narrow_source_manifest(enabled=True, snapshot=snapshot,
            descriptor_bytes=output['descriptor_bytes'], descriptor_sha256=output['descriptor_sha256'],
            memory_bytes=output['memory_bytes'], memory_sha256=output['memory_sha256'])


def test_narrow_selected_row_is_rechecked_after_provider_callback(package, fixtures, tmp_path):
    case, _, _, _, _, _, _, run = _narrow_fixture(package, fixtures, tmp_path)
    source = Path(case['messages']['source_path'])
    initial = run()
    selected = initial['results'][0]['source_reference']['sha256']
    lines = source.read_bytes().splitlines(keepends=True)
    index = next(i for i, line in enumerate(lines) if fixtures._sha(fixtures._raw(json.loads(line))) == selected)
    def mutate():
        lines[index] = lines[index][:-2] + b'!\n'
        source.write_bytes(b''.join(lines))
    case['encoder_provider'].on_query = mutate
    with pytest.raises(reader.ContentQueryRefused):
        run()


def test_narrow_read_full_snapshot_validation_is_not_per_reconstruct(package, fixtures, tmp_path, monkeypatch):
    _, _, _, snapshot, _, deploy, narrow, run = _narrow_fixture(package, fixtures, tmp_path)
    calls, parsed = [], []
    original = reader._NarrowReadGate.verify_snapshot
    original_json = reader.json.loads
    observed = {snapshot['projection_bytes'].decode('utf-8'), narrow['manifest_bytes'].decode('utf-8'),
                deploy.decode('utf-8-sig')}

    def counted(self, snapshot, **kwargs):
        calls.append(None)
        return original(self, snapshot, **kwargs)

    def counted_json(raw, *args, **kwargs):
        if type(raw) is str and raw in observed:
            parsed.append(raw)
        return original_json(raw, *args, **kwargs)

    monkeypatch.setattr(reader._NarrowReadGate, 'verify_snapshot', counted)
    monkeypatch.setattr(reader.json, 'loads', counted_json)
    assert run()['results']
    assert len(calls) > 4  # every provider callback still rechecks all pins
    assert all(parsed.count(raw) == 1 for raw in observed)


def test_narrow_gate_synthetic_2000_record_revalidation(package, fixtures, tmp_path):
    import inspect
    import time
    _, _, output, snapshot, bundle, _, narrow, _ = _narrow_fixture(package, fixtures, tmp_path)
    projection = json.loads(snapshot['projection_bytes'])
    template = projection['messages']['records'][0]
    records = []
    for number in range(2000):
        record = json.loads(json.dumps(template))
        record['record_id'] = f'synthetic-record-{number}'
        record['provenance']['source_event_ref'] = f'{number:064x}'
        records.append(record)
    projection['messages']['records'] = records
    snapshot['projection_bytes'] = reader._serialized(projection)
    snapshot['projection_sha256'] = reader._sha(snapshot['projection_bytes'])
    manifest = json.loads(narrow['manifest_bytes'])
    entry = next(iter(manifest['entries'].values()))
    manifest['entries'] = {record['provenance']['source_event_ref']: entry for record in records}
    manifest['projection_sha256'] = snapshot['projection_sha256']
    manifest_raw = reader._canonical(manifest)
    (bundle / 'wd-mind-narrow-source-manifest.json').write_bytes(manifest_raw)
    deployment = fixtures._raw({'schema_version': 1, 'files': {
        'wd-mind-narrow-source-manifest.json': reader._sha(manifest_raw)}})
    (bundle / 'deployment-manifest.json').write_bytes(deployment)
    values = {'descriptor_sha256': output['descriptor_sha256'], 'memory_sha256': output['memory_sha256']}
    gate = reader._NarrowReadGate(str(bundle), reader._sha(deployment), values)
    gate.verify_snapshot(snapshot)
    kwargs = ({'_return_projection': False} if '_return_projection' in
              inspect.signature(gate.verify_snapshot).parameters else {})
    start = time.perf_counter()
    reports = [gate.verify_snapshot(snapshot, **kwargs)[2] for _ in range(8)]
    cached = time.perf_counter() - start
    start = time.perf_counter()
    fresh = [reader._NarrowReadGate(str(bundle), reader._sha(deployment), values).verify_snapshot(
        snapshot, **kwargs)[2] for _ in range(8)]
    reparsed = time.perf_counter() - start
    assert reports == fresh
    print(f'synthetic_gate_records=2000 checks=8 cached_s={cached:.3f} fresh_s={reparsed:.3f}')


@pytest.mark.parametrize('damage', ['deployment', 'narrow_manifest', 'generation'])
def test_narrow_read_revalidates_controls_changed_by_provider(package, fixtures, tmp_path, damage):
    case, _, _, _, bundle, _, _, run = _narrow_fixture(package, fixtures, tmp_path)

    def mutate():
        if damage == 'generation':
            Path(case['messages']['generation_path']).write_bytes(b'{"generation":"changed"}')
        else:
            name = 'deployment-manifest.json' if damage == 'deployment' else 'wd-mind-narrow-source-manifest.json'
            path = bundle / name
            path.write_bytes(path.read_bytes() + b' ')

    case['encoder_provider'].on_query = mutate
    with pytest.raises(reader.ContentQueryRefused):
        run()


def test_narrow_read_rejects_projection_control_replacement_during_provider(package, fixtures, tmp_path):
    case, codec, output, snapshot, bundle, deploy, _, _ = _narrow_fixture(package, fixtures, tmp_path)
    encoder, index = reader.adapt_complete_readonly_providers(
        encoder=case['encoder_provider'], index_decoder=codec.decode,
        **{key: output[key] for key in ('encoder_binding_bytes', 'encoder_binding_sha256',
                                       'index_binding_bytes', 'index_binding_sha256')})
    stream = fixtures._ObservedStream(fixtures._raw(
        {'situation': 'retry network outage', 'time': fixtures.QUERY_TIME}))
    values = {**snapshot, **output, 'encoder_provider': encoder, 'index_provider': index, 'stream': stream}
    gate = reader._NarrowReadGate(str(bundle), fixtures._sha(deploy), values)
    def mutate():
        values['projection_bytes'] += b' '
    case['encoder_provider'].on_query = mutate
    with pytest.raises(reader.ContentQueryRefused):
        reader._provider_result(lambda: reader._query_complete_content_snapshot(values, _narrow=gate))


@pytest.mark.parametrize('damage', ['deployment', 'narrow_manifest', 'projection', 'policy_pin'])
def test_narrow_refuses_callback_tampering_before_later_restore(package, fixtures, tmp_path, damage):
    case, codec, output, snapshot, bundle, deploy, _, _ = _narrow_fixture(package, fixtures, tmp_path)
    restored = []
    values = None

    def decode(raw):
        index = codec.decode(raw)
        if damage in ('deployment', 'narrow_manifest'):
            name = 'deployment-manifest.json' if damage == 'deployment' else 'wd-mind-narrow-source-manifest.json'
            path = bundle / name
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            def restore(): path.write_bytes(original)
        elif damage == 'projection':
            original = values['projection_bytes']
            values['projection_bytes'] += b' '
            def restore(): values['projection_bytes'] = original
        else:
            original = values['messages']['policy_sha256']
            values['messages']['policy_sha256'] = '0' * 64
            def restore(): values['messages']['policy_sha256'] = original
        reconstruct = index.reconstruct_n
        def later_restore(start, count):
            restored.append(True)
            restore()
            return reconstruct(start, count)
        index.reconstruct_n = later_restore
        return index

    encoder, index = reader.adapt_complete_readonly_providers(
        encoder=case['encoder_provider'], index_decoder=decode,
        **{key: output[key] for key in ('encoder_binding_bytes', 'encoder_binding_sha256',
                                       'index_binding_bytes', 'index_binding_sha256')})
    stream = fixtures._ObservedStream(fixtures._raw(
        {'situation': 'retry network outage', 'time': fixtures.QUERY_TIME}))
    values = {**snapshot, **output, 'encoder_provider': encoder, 'index_provider': index, 'stream': stream}
    gate = reader._NarrowReadGate(str(bundle), fixtures._sha(deploy), values)
    with pytest.raises(reader.ContentQueryRefused):
        reader._provider_result(lambda: reader._query_complete_content_snapshot(values, _narrow=gate))
    assert not restored


@pytest.mark.parametrize('fault', ['append', 'sidecar', 'selected'])
def test_narrow_generationless_checks_sidecar_and_selected_row(package, fixtures, tmp_path, fault):
    case, _, _, _, _, _, _, run = _narrow_fixture(package, fixtures, tmp_path, generationless=True)
    initial = run()
    assert initial['results']
    source = Path(case['messages']['source_path'])
    if fault == 'sidecar':
        Path(case['messages']['generation_path']).write_bytes(fixtures._raw({'generation': 'synthetic-g1'}))
    elif fault == 'append':
        with source.open('ab') as stream:
            stream.write(fixtures._raw(fixtures._row('synthetic appended row')) + b'\n')
    else:
        ref = initial['results'][0]['source_reference']['sha256']
        lines = source.read_bytes().splitlines(keepends=True)
        index = next(i for i, line in enumerate(lines) if fixtures._sha(fixtures._raw(json.loads(line))) == ref)
        lines[index] = lines[index][:-2] + b'!\n'
        source.write_bytes(b''.join(lines))
    if fault == 'append':
        assert run()['results']
    else:
        with pytest.raises(reader.ContentQueryRefused):
            run()


def test_narrow_builder_deduplicates_identical_canonical_events_without_leaking_excluded_rows(package, fixtures, tmp_path):
    build = fixtures._row(fixtures.SEMANTIC_TEXTS[0], type='message', task_id='synthetic-build-1')
    other = fixtures._row(fixtures.SEMANTIC_TEXTS[1], type='done', task_id='synthetic-build-2')
    third = fixtures._row(fixtures.SEMANTIC_TEXTS[2], type='message', task_id='synthetic-build-4')
    fourth = fixtures._row(fixtures.SEMANTIC_TEXTS[3], type='message', task_id='synthetic-build-5')
    secret = fixtures._row('password=syntheticfixturevalue', task_id='synthetic-build-3')
    rco = fixtures._row('synthetic reviewer private', agent='claude-rco-1',
                        agent_uuid=fixtures.ROSTER['claude-rco-1'], task_id='synthetic-review-1')
    _, _, _, _, _, _, narrow, run = _narrow_fixture(package, fixtures, tmp_path,
                                                     rows=[build, build, secret, rco, other, third, fourth])
    manifest = json.loads(narrow['manifest_bytes'])
    build_ref = fixtures._sha(fixtures._raw(build))
    secret_ref = fixtures._sha(fixtures._raw(secret))
    rco_ref = fixtures._sha(fixtures._raw(rco))
    assert manifest['entries'][build_ref]['offset'] == 0
    assert secret_ref not in manifest['entries'] and rco_ref not in manifest['entries']
    assert b'synthetic reviewer private' not in narrow['manifest_bytes']
    assert b'syntheticfixturevalue' not in narrow['manifest_bytes']
    assert run()['results']
def test_immutable_build_profile_is_explicit_and_verifies_initial_and_final_source(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    calls = []
    original = reader._verified_complete

    def observed(snapshot):
        calls.append(True)
        return original(snapshot)

    monkeypatch.setattr(reader, '_verified_complete', observed)
    output = package.package_content_snapshot(**_arguments(case, codec, FULL_PREFIX_PROFILE),
        source_verification='verified_immutable_snapshot_v1')
    assert json.loads(output['memory_bytes'])['records'] > 0
    assert len(calls) == 2


@pytest.mark.parametrize('succeeds', [True, False])
def test_immutable_build_waits_for_running_long_verification(package, fixtures, tmp_path, monkeypatch, succeeds):
    import time
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    entered, finished = threading.Event(), threading.Event()
    verify = reader._verified_complete

    def slow_verify(snapshot):
        if threading.current_thread().name == 'mind-source-verifier':
            entered.set()
            time.sleep(2.2)
            if not succeeds:
                finished.set()
                raise RuntimeError('synthetic verifier failure')
            result = verify(snapshot)
            finished.set()
            return result
        return verify(snapshot)

    def encode(batch):
        assert entered.wait(5)
        return codec.passage(batch)

    monkeypatch.setattr(package, '_WATCH_INTERVAL_SECONDS', 0.001)
    monkeypatch.setattr(reader, '_verified_complete', slow_verify)
    args['passage_encoder'] = encode
    try:
        if not succeeds:
            with pytest.raises(package.SnapshotPackageRefused):
                package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
            assert finished.is_set()
            assert package.snapshot_build_watcher_status() == {'state': 'idle'}
            return
        output = package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
        assert finished.is_set()
        assert json.loads(output['memory_bytes'])['records'] > 0
        assert package.snapshot_build_watcher_status() == {'state': 'idle'}
    finally:
        for thread in threading.enumerate():
            if thread.name == 'mind-source-verifier':
                thread.join(10)


@pytest.mark.parametrize('timeout', [True, 0, -1, 601, float('nan'), float('inf'), '5'])
def test_watcher_close_wait_rejects_invalid_timeouts(package, timeout):
    with pytest.raises(package.SnapshotPackageRefused):
        package.package_content_snapshot(enabled=True, capacity_profile=FULL_PREFIX_PROFILE,
            source_verification='verified_immutable_snapshot_v1', watcher_join_timeout_seconds=timeout)


@pytest.mark.parametrize('source_valid', [True, False])
def test_long_watcher_join_precedes_final_source_check_and_publication(
        package, fixtures, tmp_path, monkeypatch, source_valid):
    import time
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    entered, finished = threading.Event(), threading.Event()
    sequence = []
    verify, final_check = reader._verified_complete, package._unchanged
    source = Path(case['messages']['source_path'])

    def slow_verify(snapshot):
        if threading.current_thread().name == 'mind-source-verifier':
            entered.set()
            time.sleep(2.2)
            result = verify(snapshot)
            sequence.append('watcher_completed')
            finished.set()
            return result
        return verify(snapshot)

    def checked_final(original, frozen, report, **kwargs):
        assert finished.is_set()
        assert not any(thread.name == 'mind-source-verifier' for thread in threading.enumerate())
        sequence.append('final_check_started')
        if not source_valid:
            old = source.read_bytes()
            source.write_bytes(old[:-2] + b'X\n')
        result = final_check(original, frozen, report, **kwargs)
        sequence.append('final_check_passed')
        return result

    def encode(batch):
        assert entered.wait(5)
        return codec.passage(batch)

    monkeypatch.setattr(package, '_WATCH_INTERVAL_SECONDS', 0.001)
    monkeypatch.setattr(reader, '_verified_complete', slow_verify)
    monkeypatch.setattr(package, '_unchanged', checked_final)
    args['passage_encoder'] = encode
    try:
        if source_valid:
            output = package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
            sequence.append('published')
            assert json.loads(output['memory_bytes'])['records'] > 0
            assert sequence == ['watcher_completed', 'final_check_started', 'final_check_passed', 'published']
        else:
            with pytest.raises(package.SnapshotPackageRefused):
                package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
            assert sequence == ['watcher_completed', 'final_check_started']
        assert package.snapshot_build_watcher_status() == {'state': 'idle'}
    finally:
        for thread in threading.enumerate():
            if thread.name == 'mind-source-verifier':
                thread.join(10)


def test_immutable_build_refuses_permanent_source_change_after_callback(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    original = codec.passage
    source = Path(case['messages']['source_path'])

    def mutate(batch):
        value = original(batch)
        raw = source.read_bytes()
        source.write_bytes(raw[:-2] + b'X\n')
        return value

    args['passage_encoder'] = mutate
    with pytest.raises(package.SnapshotPackageRefused):
        package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')


@pytest.mark.parametrize('damage', ['truncate', 'replace', 'generation', 'manifest', 'enabled'])
def test_immutable_build_refuses_callback_tampering(package, fixtures, tmp_path, damage):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    source = Path(case['messages']['source_path'])
    original = codec.passage

    def mutate(batch):
        value = original(batch)
        if damage == 'truncate':
            source.write_bytes(source.read_bytes()[:-1])
        elif damage == 'replace':
            replacement = tmp_path / 'replacement'
            replacement.write_bytes(source.read_bytes())
            replacement.replace(source)
        elif damage == 'generation':
            Path(case['messages']['generation_path']).write_bytes(b'{"generation":"changed"}')
        elif damage == 'manifest':
            args['snapshot']['projection_sha256'] = '0' * 64
        else:
            args['snapshot']['enabled'] = 1  # 1 == True; exact-type check must still reject.
        return value

    args['passage_encoder'] = mutate
    with pytest.raises(package.SnapshotPackageRefused):
        package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')


def test_immutable_build_watcher_failure_during_callback_refuses_and_joins(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    observed = threading.Event()
    verifier = reader._verified_complete

    def verify(snapshot):
        if threading.current_thread().name == 'mind-source-verifier':
            observed.set()
            raise RuntimeError('synthetic watcher failure')
        return verifier(snapshot)

    def block_until_watched(batch):
        assert observed.wait(10)
        return codec.passage(batch)

    monkeypatch.setattr(package, '_WATCH_INTERVAL_SECONDS', 0.01)
    monkeypatch.setattr(reader, '_verified_complete', verify)
    args['passage_encoder'] = block_until_watched
    with pytest.raises(package.SnapshotPackageRefused):
        package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
    assert not any(t.name == 'mind-source-verifier' for t in threading.enumerate())


def test_immutable_build_watcher_detects_source_mutation_during_callback(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    source = Path(case['messages']['source_path'])
    failed = threading.Event()
    verify = reader._verified_complete

    def observed(snapshot):
        try:
            return verify(snapshot)
        except Exception:
            if threading.current_thread().name == 'mind-source-verifier':
                failed.set()
            raise

    def mutate_and_wait(batch):
        source.write_bytes(source.read_bytes()[:-1])
        assert failed.wait(10)
        return codec.passage(batch)

    monkeypatch.setattr(package, '_WATCH_INTERVAL_SECONDS', 0.01)
    monkeypatch.setattr(reader, '_verified_complete', observed)
    args['passage_encoder'] = mutate_and_wait
    with pytest.raises(package.SnapshotPackageRefused):
        package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
    assert not any(t.name == 'mind-source-verifier' for t in threading.enumerate())


def test_immutable_build_callback_error_joins_watcher(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    args = _arguments(case, Codec(fixtures), FULL_PREFIX_PROFILE)
    def broken(_):
        raise RuntimeError('synthetic callback error')
    args['passage_encoder'] = broken
    with pytest.raises(package.SnapshotPackageRefused):
        package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
    assert not any(t.name == 'mind-source-verifier' for t in threading.enumerate())


def test_immutable_build_stuck_watcher_returns_hold_and_keeps_ownership(package, fixtures, tmp_path, monkeypatch):
    assert hasattr(package, 'SnapshotPackageHold')
    assert package.snapshot_build_watcher_status() == {'state': 'idle'}
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    entered, release = threading.Event(), threading.Event()
    verify = reader._verified_complete
    original_join, joins = threading.Thread.join, []

    def watched_verify(snapshot):
        if threading.current_thread().name == 'mind-source-verifier':
            entered.set()
            if not release.wait(15):
                raise RuntimeError('fixture watchdog timeout')
        return verify(snapshot)

    def join_once(thread, timeout=None):
        if thread.name == 'mind-source-verifier':
            joins.append(timeout)
        return original_join(thread, timeout)

    def encode(batch):
        assert entered.wait(5)
        return codec.passage(batch)

    monkeypatch.setattr(package, '_WATCH_INTERVAL_SECONDS', 0.01)
    monkeypatch.setattr(reader, '_verified_complete', watched_verify)
    monkeypatch.setattr(threading.Thread, 'join', join_once)
    args['passage_encoder'] = encode
    try:
        with pytest.raises(package.SnapshotPackageHold, match='content_snapshot_watcher_hold'):
            package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1',
                                             watcher_join_timeout_seconds=0.05)
        assert joins == [0.05]  # finally must not start a second timeout.
        assert package.snapshot_build_watcher_status() == {'state': 'watcher_stopping'}
        with pytest.raises(package.SnapshotPackageHold, match='content_snapshot_watcher_hold'):
            package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
        # A fresh module import in the same interpreter shares ownership.
        spec = importlib.util.spec_from_file_location('snapshot_package_another_import',
            ROOT / 'tools/wd_mind_content_snapshot_package.py')
        another = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(another)
        assert another.snapshot_build_watcher_status() == {'state': 'watcher_stopping'}
        with pytest.raises(another.SnapshotPackageHold, match='content_snapshot_watcher_hold'):
            another.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
        assert package.snapshot_build_watcher_status() == {'state': 'watcher_stopping'}
        # Strict verification remains available while the opt-in watcher is held.
        args['passage_encoder'] = codec.passage
        assert json.loads(package.package_content_snapshot(**args)['memory_bytes'])['records'] > 0
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name == 'mind-source-verifier':
                original_join(thread, 10)
    assert package.snapshot_build_watcher_status() == {'state': 'idle'}
    assert json.loads(package.package_content_snapshot(**args,
        source_verification='verified_immutable_snapshot_v1')['memory_bytes'])['records'] > 0


def test_immutable_build_simultaneous_opt_in_reserves_only_one_owner(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    entered, release, outcome = threading.Event(), threading.Event(), []
    def wait_callback(batch):
        entered.set()
        if not release.wait(10):
            raise RuntimeError('fixture watchdog timeout')
        return codec.passage(batch)
    args['passage_encoder'] = wait_callback
    def first_build():
        try:
            outcome.append(package.package_content_snapshot(**args,
                source_verification='verified_immutable_snapshot_v1'))
        except Exception as error:
            outcome.append(error)
    monkeypatch.setattr(package, '_WATCH_INTERVAL_SECONDS', 60)
    builder = threading.Thread(target=first_build)
    builder.start()
    try:
        assert entered.wait(5)
        assert package.snapshot_build_watcher_status() == {'state': 'building'}
        with pytest.raises(package.SnapshotPackageHold, match='content_snapshot_watcher_hold'):
            package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
    finally:
        release.set()
        builder.join(10)
    assert not builder.is_alive()
    assert len(outcome) == 1 and type(outcome[0]) is dict
    assert package.snapshot_build_watcher_status() == {'state': 'idle'}


def test_immutable_build_accepted_transient_aba_is_not_claimed_detected(package, fixtures, tmp_path, monkeypatch):
    # Operator accepted a transient in-place mutation that is restored between
    # the full scans may go unnoticed. This test exhibits that exact limit.
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    source = Path(case['messages']['source_path'])
    def transient(batch):
        raw = source.read_bytes()
        source.write_bytes(raw[:-2] + b'X\n')
        source.write_bytes(raw)
        return codec.passage(batch)
    monkeypatch.setattr(package, '_WATCH_INTERVAL_SECONDS', 60)
    args['passage_encoder'] = transient
    result = package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
    assert json.loads(result['memory_bytes'])['records'] > 0
    assert not any(t.name == 'mind-source-verifier' for t in threading.enumerate())


@pytest.mark.parametrize('mode', ['initial', 'final'])
def test_immutable_build_endpoint_verification_fails_closed(package, fixtures, tmp_path, monkeypatch, mode):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    verifier, count = reader._verified_complete, [0]
    def verify(snapshot):
        count[0] += 1
        if count[0] == (1 if mode == 'initial' else 2):
            raise ValueError('synthetic endpoint failure')
        return verifier(snapshot)
    monkeypatch.setattr(package, '_WATCH_INTERVAL_SECONDS', 60)
    monkeypatch.setattr(reader, '_verified_complete', verify)
    with pytest.raises(package.SnapshotPackageRefused):
        package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
    assert not any(t.name == 'mind-source-verifier' for t in threading.enumerate())


def test_immutable_build_generationless_sidecar_and_presence(package, fixtures, tmp_path):
    from waggledance.core.bridge_log_reader import BridgeCursor
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    cursor = case['messages']['end_cursor']
    case['messages']['end_cursor'] = BridgeCursor(cursor.offset, cursor.file_identity, None)
    Path(case['messages']['generation_path']).unlink()
    sidecar = Path(case['messages']['source_path']).with_name('events.generation.json')
    case['messages']['generation_path'] = str(sidecar)
    decision = json.loads(case['decision_bytes'])
    decision['source_prefix']['generation'] = None
    case['decision_bytes'] = fixtures._raw(decision)
    case['decision_sha256'] = reader._sha(case['decision_bytes'])
    source = {key: case[key] for key in reader._INPUT_KEYS}
    case.update(fixtures._snapshot(source, reader.rederive_complete_message_projection(**source)))
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    assert json.loads(package.package_content_snapshot(**args,
        source_verification='verified_immutable_snapshot_v1')['memory_bytes'])['records'] > 0
    def sidecar_appears(batch):
        sidecar.write_bytes(b'{"generation":"synthetic"}')
        return codec.passage(batch)
    args['passage_encoder'] = sidecar_appears
    with pytest.raises(package.SnapshotPackageRefused):
        package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')


def test_immutable_build_explicit_opt_in_preserves_strict_default_and_output(package, fixtures, tmp_path, monkeypatch):
    case = _chunk_case(fixtures, tmp_path, 33)
    codec = ChunkCodec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    verify, calls = reader._verified_complete, []
    def observed(snapshot):
        calls.append(True)
        return verify(snapshot)
    monkeypatch.setattr(package, '_WATCH_INTERVAL_SECONDS', 60)
    monkeypatch.setattr(reader, '_verified_complete', observed)
    strict = package.package_content_snapshot(**args)
    strict_scans = len(calls)
    assert strict_scans > 2
    assert [len(batch) for batch in codec.passages] == [16, 16, 1]
    calls.clear()
    optimized = package.package_content_snapshot(**args, source_verification='verified_immutable_snapshot_v1')
    optimized_scans = len(calls)
    assert optimized_scans == 2
    assert [len(batch) for batch in codec.passages] == [16, 16, 1, 16, 16, 1]
    print(f'synthetic 33 records: strict full verifications={strict_scans}, '
          f'immutable full verifications={optimized_scans}, passage batches=3 per build')
    assert strict == optimized
    for invalid in ('unknown', False, True, 1):
        with pytest.raises(package.SnapshotPackageRefused):
            package.package_content_snapshot(**args, source_verification=invalid)


def test_full_decoder_retains_canonical_float32_and_legacy_preserves_float64():
    """Equal verified f32 bytes imply equal full-profile ranking inputs."""
    vector = (1.00000001, -0.0) + (0.0,) * 382
    raw = struct.pack("<384f", *vector)
    assert raw == struct.pack("<384f", 1.0, -0.0, *([0.0] * 382))
    doc_ids = ["r#w", "r#d"]
    digest = hashlib.sha256(b"".join(doc.encode() + b"\x00" + raw for doc in doc_ids)).hexdigest()
    captured = {"general": ({"index": b"synthetic", "vectors": raw * 2},
                            {"count": 2, "pairs_sha256": digest},
                            {"doc_ids": doc_ids}, [("r", "w"), ("r", "d")])}

    class FakeIndex:
        d = 384
        ntotal = 2
        metric_type = 0
        def reconstruct_n(self, start, count):
            return [vector] * count

    class Provider:
        def load(self, raw_index):
            return FakeIndex()

    full = reader._q4_decode_cells_complete_v1(captured, Provider(), {"r": {}}, retain=True)
    legacy = reader._q4_decode_cells_bounded_v3(captured, Provider(), {"r": {}})
    for view in ("w", "d"):
        canonical = struct.unpack("<384d", full["r"][view])
        assert canonical[0] == 1.0
        assert struct.pack("<d", canonical[1]) == struct.pack("<d", -0.0)
        assert struct.unpack("<384d", legacy["r"][view])[0] == 1.00000001


def test_full_prefix_profile_is_explicit_and_retains_other_profiles(package):
    assert package._capacity(None) is None
    assert package._capacity(CAPACITY_PROFILE)["records"] == 32768
    assert package._capacity(FULL_PREFIX_PROFILE) == {"streamed": True}


def test_full_prefix_refuses_before_callbacks_when_reader_contract_missing(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    arguments = _arguments(case, codec, FULL_PREFIX_PROFILE)
    # Establish that the same inputs really exercise a successful full-prefix
    # build before removing the reader entry point used by the producer.
    assert package.package_content_snapshot(**arguments)
    monkeypatch.delattr(reader, "_verified_complete")
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("provider must not run")
    arguments.update(passage_encoder=forbidden, index_serializer=forbidden, index_decoder=forbidden)
    with pytest.raises(package.SnapshotPackageRefused):
        package.package_content_snapshot(**arguments)
    assert calls == []


def test_full_prefix_does_not_accept_invalid_reader_result(package, fixtures, tmp_path, monkeypatch):
    calls = []
    def fake_reader(*args, **kwargs):
        calls.append(True)
        raise ValueError('synthetic verification failure')
    monkeypatch.setattr(reader, "_verified_complete", fake_reader, raising=False)
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    with pytest.raises(package.SnapshotPackageRefused):
        package.package_content_snapshot(**_arguments(case, codec, FULL_PREFIX_PROFILE))
    assert calls == [True] and codec.passages == []


def test_full_prefix_packages_257_verified_records_without_partial_output(package, fixtures, tmp_path):
    case = _chunk_case(fixtures, tmp_path, 257)
    codec = ChunkCodec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, FULL_PREFIX_PROFILE))
    assert json.loads(output['memory_bytes'])['records'] == 257
    assert list(map(len, codec.passages)) == [16] * 16 + [1]


def _complete_query(case, output, codec, *, decoder=None, stream=None):
    encoder, index = reader.adapt_complete_readonly_providers(
        encoder=case['encoder_provider'], index_decoder=decoder or codec.decode,
        **{key: output[key] for key in ('encoder_binding_bytes', 'encoder_binding_sha256',
                                       'index_binding_bytes', 'index_binding_sha256')})
    if stream is None:
        stream = codec.fixtures._ObservedStream(codec.fixtures._raw(
            {'situation': 'retry network outage', 'time': codec.fixtures.QUERY_TIME}))
    return reader.query_complete_content_snapshot(
        **{key: case[key] for key in reader._Q4_SOURCE_KEYS}, **output,
        encoder_provider=encoder, index_provider=index, stream=stream)


@pytest.mark.parametrize('related', [True, False])
def test_full_prefix_cross_language_vector_candidate_or_abstain(package, fixtures, tmp_path, related):
    """Synthetic vectors model a related or orthogonal query, not a real embedding."""
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, FULL_PREFIX_PROFILE))
    if not related:
        case['encoder_provider'].query = lambda _: (0.0, 0.0, 1.0) + (0.0,) * 381
    stream = fixtures._ObservedStream(fixtures._raw(
        {'situation': 'yhteyskatko uudelleenyritys', 'time': fixtures.QUERY_TIME}))
    result = _complete_query(case, output, codec, stream=stream)
    if related:
        assert result['status'] == 'SEMANTIC_CANDIDATE'
        assert result['rule'] == 'q4-complete-semantic-candidate-v1'
        assert result['relevance'] == 'UNVERIFIED' and result['authority'] == 'none'
        assert all(hit['source_reference']['verified'] == 'MATCH_WITHIN_PREFIX' for hit in result['results'])
        assert len(case['encoder_provider'].calls) == 1
    else:
        assert result['status'] == 'ABSTAIN' and result['results'] == []


@pytest.mark.parametrize('excluded', ['expired', 'future', 'self_match', 'below_threshold', 'negative'])
def test_complete_semantic_candidate_keeps_temporal_self_and_cosine_gates(excluded):
    import math
    when = reader._utc('2026-01-01T00:02:00Z')
    records = {f'r{i}': {'stored_text': reader.encode_text(f'archive item {i}'),
        'provenance': {'available_utc': '2026-01-01T00:00:00Z'}} for i in range(5)}
    if excluded == 'expired':
        records['r0']['provenance']['valid_until_utc'] = '2026-01-01T00:01:00Z'
    if excluded == 'future':
        records['r0']['provenance']['available_utc'] = '2026-01-01T00:02:00Z'
    if excluded == 'self_match':
        records['r0']['stored_text'] = reader.encode_text('archive query')
    cosine = 0.549 if excluded == 'below_threshold' else (-1.0 if excluded == 'negative' else 1.0)
    target = (cosine, math.sqrt(1.0 - cosine * cosine)) + (0.0,) * 382
    unrelated = (0.0, 1.0) + (0.0,) * 382
    entries = {key: {'w': target if key == 'r0' else unrelated,
                     'd': target if key == 'r0' else unrelated} for key in records}
    class Encoder:
        def query(self, text): return (1.0,) + (0.0,) * 383
    assert reader._q4_complete_semantic_candidates(records, entries, Encoder(), 'archive query', when) == []


def test_complete_semantic_candidate_uses_lexical_rank_as_optional_signal():
    when = reader._utc('2026-01-01T00:02:00Z')
    records = {f'r{i}': {'stored_text': reader.encode_text('archive backup' if i == 1 else f'other {i}'),
        'provenance': {'available_utc': '2026-01-01T00:00:00Z'}} for i in range(5)}
    matching = (1.0,) + (0.0,) * 383
    unrelated = (0.0, 1.0) + (0.0,) * 382
    entries = {key: {'w': matching if key in ('r0', 'r1') else unrelated,
                     'd': matching if key in ('r0', 'r1') else unrelated} for key in records}
    class Encoder:
        def query(self, text): return matching
    assert reader._q4_complete_semantic_candidates(records, entries, Encoder(), 'archive query', when) == ['r1', 'r0']


@pytest.mark.parametrize('generationless', [False, True])
def test_full_prefix_build_query_rederives_source_past_bounded_limits(package, fixtures, tmp_path, monkeypatch, generationless):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    if generationless:
        from waggledance.core.bridge_log_reader import BridgeCursor
        cursor = case['messages']['end_cursor']
        case['messages']['end_cursor'] = BridgeCursor(cursor.offset, cursor.file_identity, None)
        Path(case['messages']['generation_path']).unlink()
        case['messages']['generation_path'] = str(Path(case['messages']['source_path']).with_name('events.generation.json'))
        decision = json.loads(case['decision_bytes'])
        decision['source_prefix']['generation'] = None
        case['decision_bytes'] = fixtures._raw(decision)
        case['decision_sha256'] = reader._sha(case['decision_bytes'])
        source = {key: case[key] for key in reader._INPUT_KEYS}
        case.update(fixtures._snapshot(source, reader.rederive_complete_message_projection(**source)))
    codec = Codec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, FULL_PREFIX_PROFILE))
    monkeypatch.setattr(reader, '_Q4_BOUNDED_V3_MAX_ROWS', 2)
    monkeypatch.setattr(reader, '_Q4_BOUNDED_VECTOR_BYTES', 1)
    monkeypatch.setattr(reader, '_Q4_BOUNDED_V3_ARTIFACT_BYTES', 1)
    monkeypatch.setattr(reader, '_Q4_TOTAL_BYTES', 1)
    result = _complete_query(case, output, codec)
    assert result['status'] == 'ANSWER'
    assert result['results'][0]['source_reference']['verified'] == 'MATCH_WITHIN_PREFIX'


def test_full_prefix_query_handles_257_verified_records(package, fixtures, tmp_path):
    case = _chunk_case(fixtures, tmp_path, 257)
    codec = Codec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, FULL_PREFIX_PROFILE))
    result = _complete_query(case, output, codec)
    assert result['status'] == 'ANSWER'
    assert all(hit['source_reference']['verified'] == 'MATCH_WITHIN_PREFIX' for hit in result['results'])


@pytest.mark.parametrize('fault', ['source_before', 'source_during', 'provider', 'snapshot_during',
                                   'reconstruct_during', 'encoder_during'])
def test_full_prefix_query_refuses_source_change_and_provider_failure(package, fixtures, tmp_path, fault):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, FULL_PREFIX_PROFILE))
    codec.decoded.clear()
    source = Path(case['messages']['source_path'])
    def mutate():
        source.write_bytes(source.read_bytes().replace(b'network retry', b'changed retry', 1))
    if fault == 'source_before': mutate()
    def decoder(raw):
        if fault == 'provider': raise RuntimeError('synthetic private provider failure')
        result = codec.decode(raw)
        if fault == 'source_during': mutate()
        if fault == 'snapshot_during': case['messages']['policy_sha256'] = '0' * 64
        if fault == 'reconstruct_during':
            original = result.reconstruct_n
            def reconstruct(start, count):
                rows = original(start, count)
                mutate()
                return rows
            result.reconstruct_n = reconstruct
        return result
    if fault == 'encoder_during': case['encoder_provider'].on_query = mutate
    stream = fixtures._ObservedStream(fixtures._raw({'situation': 'retry network outage', 'time': fixtures.QUERY_TIME}))
    with pytest.raises(reader.ContentQueryRefused):
        _complete_query(case, output, codec, decoder=decoder, stream=stream)
    assert stream.reads == (1 if fault == 'encoder_during' else 0)
    if fault == 'reconstruct_during':
        assert len(codec.decoded) == 1


@pytest.mark.parametrize('fault', ['load_artifact', 'reconstruct_pointer', 'query_vectors',
                                   'query_removed_key', 'stream_log', 'query_top_level'])
def test_full_query_refuses_any_control_or_cell_mutation_at_callback(package, fixtures, tmp_path, fault):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, FULL_PREFIX_PROFILE))
    cell = next(iter(output['cell_snapshots'].values()))
    def mutate():
        if fault == 'load_artifact': cell['artifacts']['index'] = b'synthetic changed'
        if fault == 'reconstruct_pointer': cell['pointer'] = b'changed'
        if fault == 'query_vectors': cell['artifacts']['vectors'] = b'changed'
        if fault == 'query_removed_key': cell['artifacts'].pop('vectors')
        if fault == 'stream_log': cell['log'] = b'changed'
        if fault == 'query_top_level': values['descriptor_sha256'] = '0' * 64
    def decode(raw):
        index = codec.decode(raw)
        if fault == 'load_artifact': mutate()
        if fault == 'reconstruct_pointer':
            original = index.reconstruct_n
            def reconstruct(start, count):
                rows = original(start, count)
                mutate()
                return rows
            index.reconstruct_n = reconstruct
        return index
    encoder, index = reader.adapt_complete_readonly_providers(
        encoder=case['encoder_provider'], index_decoder=decode,
        **{key: output[key] for key in ('encoder_binding_bytes', 'encoder_binding_sha256',
                                       'index_binding_bytes', 'index_binding_sha256')})
    class Stream(fixtures._ObservedStream):
        def read(self, size=-1):
            data = super().read(size)
            if fault == 'stream_log': mutate()
            return data
    stream = Stream(fixtures._raw({'situation': 'yhteyskatko uudelleenyritys', 'time': fixtures.QUERY_TIME}))
    values = {**{key: case[key] for key in reader._Q4_SOURCE_KEYS}, **output,
              'encoder_provider': encoder, 'index_provider': index, 'stream': stream}
    if fault.startswith('query_'):
        case['encoder_provider'].on_query = mutate
    with pytest.raises(reader.ContentQueryRefused):
        reader._provider_result(lambda: reader._query_complete_content_snapshot(values))


def test_full_prefix_accepts_verified_source_larger_than_legacy_prefix_limit(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    rows = [fixtures._row('synthetic network retry', task_id=f'synthetic-task-{i}',
                          payload={'padding': 'x' * (3 * 1024 * 1024)}) for i in range(2)]
    source = fixtures._all_tasks_case(reader, tmp_path, rows)
    case.update(fixtures._snapshot(source, reader.rederive_complete_message_projection(**source)))
    assert case['messages']['end_cursor'].offset > package.MAX_PREFIX_BYTES
    codec = Codec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, FULL_PREFIX_PROFILE))
    assert json.loads(output['memory_bytes'])['records'] == 2


def test_full_prefix_duplicate_records_refuse_before_provider(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    projection, context, report = reader._verified_complete(args['snapshot'])
    projection['messages']['records'].append(projection['messages']['records'][0])
    monkeypatch.setattr(reader, '_verified_complete', lambda _: (projection, context, report))
    _refuses(package, args)
    assert codec.passages == codec.serialized == codec.decoded == []


def test_full_prefix_uses_verified_capacity_not_bounded_decoder_caps(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    monkeypatch.setattr(reader, '_Q4_BOUNDED_V3_MAX_ROWS', 2)
    monkeypatch.setattr(reader, '_Q4_BOUNDED_VECTOR_BYTES', 1)
    monkeypatch.setattr(reader, '_Q4_BOUNDED_V3_ARTIFACT_BYTES', 1)
    monkeypatch.setattr(reader, '_Q4_TOTAL_BYTES', 1)
    output = package.package_content_snapshot(**_arguments(case, codec, FULL_PREFIX_PROFILE))
    assert json.loads(output['memory_bytes'])['records'] == 4
    assert codec.passages and codec.serialized and codec.decoded


@pytest.mark.parametrize('limits', [None, {}, {'count': -1}, {'count': True}])
def test_full_prefix_cell_validation_refuses_missing_or_invalid_capacity(package, limits):
    with pytest.raises(ValueError):
        reader._q4_complete_cell_controls('general', {}, {}, {}, {}, limits)


def test_full_prefix_rechecks_source_after_each_reconstruction_call(package, fixtures, tmp_path):
    case = _chunk_case(fixtures, tmp_path, 65)
    codec = ChunkCodec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    original_decoder, calls = args['index_decoder'], []
    def decoder(raw):
        index = original_decoder(raw)
        reconstruct = index.reconstruct_n
        def mutate(start, count):
            calls.append((start, count))
            values = reconstruct(start, count)
            path = Path(case['messages']['source_path'])
            path.write_bytes(path.read_bytes().replace(b'network retry', b'changed retry', 1))
            return values
        index.reconstruct_n = mutate
        return index
    args['index_decoder'] = decoder
    _refuses(package, args)
    assert len(calls) == 1


@pytest.mark.parametrize('phase', ['passage', 'serialize', 'decode'])
def test_full_prefix_source_mutation_after_each_provider_refuses(package, fixtures, tmp_path, phase):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, FULL_PREFIX_PROFILE)
    field = {'passage': 'passage_encoder', 'serialize': 'index_serializer', 'decode': 'index_decoder'}[phase]
    original = args[field]
    def mutate(*values):
        value = original(*values)
        path = Path(case['messages']['source_path'])
        path.write_bytes(path.read_bytes().replace(b'network retry', b'changed retry', 1))
        return value
    args[field] = mutate
    _refuses(package, args)
    assert codec.passages
    if phase == 'passage':
        assert codec.serialized == codec.decoded == []
    if phase == 'serialize':
        assert codec.serialized and codec.decoded == []
    if phase == 'decode':
        assert codec.decoded


def test_full_prefix_internal_batches_stream_past_256_without_changing_order(package):
    records = ({"record_id": str(i), "stored_text": reader.encode_text("synthetic task " + str(i))}
               for i in range(257))
    row = struct.pack("<384f", 1.0, *([0.0] * 383))
    staged, calls, checks = [], [], []
    def passage(batch):
        calls.append(batch)
        return row * len(batch)
    def stage(batch):
        staged.extend(batch)
    package._full_prefix_encode_batches(records, passage, lambda: checks.append(True), stage)
    assert [record_id for record_id, _ in staged] == list(map(str, range(257)))
    assert all(vector == row for _, vector in staged)
    assert list(map(len, calls)) == [16] * 16 + [1]
    assert len(checks) == len(calls)


def test_full_prefix_failed_checkpoint_does_not_stage_current_batch(package):
    records = ({"record_id": str(i), "stored_text": reader.encode_text("synthetic")}
               for i in range(17))
    row = struct.pack("<384f", 1.0, *([0.0] * 383))
    staged, checks = [], []
    def checkpoint():
        checks.append(True)
        if len(checks) == 2:
            raise ValueError("synthetic source changed")
    with pytest.raises(ValueError):
        package._full_prefix_encode_batches(records, lambda batch: row * len(batch), checkpoint, staged.extend)
    assert [item[0] for item in staged] == list(map(str, range(16)))


@pytest.mark.parametrize("raw", [b"", b"\0" * 1536, struct.pack("<384f", float("nan"), *([0.0] * 383))])
def test_full_prefix_invalid_vector_never_stages_current_batch(package, raw):
    staged = []
    records = iter([{"record_id": "synthetic", "stored_text": reader.encode_text("synthetic")}])
    with pytest.raises(ValueError):
        package._full_prefix_encode_batches(records, lambda _: raw, lambda: None, staged.extend)
    assert staged == []


@pytest.fixture(scope="module")
def fixtures():
    path = ROOT / "tests/tools/test_wd_mind_content_query_readonly.py"
    spec = importlib.util.spec_from_file_location("snapshot_package_source_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def package():
    path = ROOT / "tools/wd_mind_content_snapshot_package.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("snapshot_package_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Codec:
    """A literal float32 byte transport, deliberately not a FAISS implementation."""
    def __init__(self, fixtures):
        self.fixtures = fixtures
        self.passages = []
        self.serialized = []
        self.decoded = []

    def passage(self, texts):
        self.passages.append(texts)
        rows = []
        for text in texts:
            assert text.startswith("passage: ")
            head = (0.0, 1.0) if text[9:] == self.fixtures.SEMANTIC_TEXTS[0] else (1.0, 0.0)
            rows.append(struct.pack("<384f", *head, *([0.0] * 382)))
        return b"".join(rows)

    def serialize(self, raw, dim):
        assert type(raw) is bytes and dim == 384
        self.serialized.append(raw)
        return b"synthetic-index-f32:" + raw

    def decode(self, raw):
        self.decoded.append(raw)
        assert raw.startswith(b"synthetic-index-f32:")
        vectors = list(struct.iter_unpack("<384f", raw[len(b"synthetic-index-f32:"):]))
        return self.fixtures._SyntheticFlatIP(vectors)


class ChunkCodec(Codec):
    """Fake native index that refuses an unbounded reconstruction request."""
    def __init__(self, fixtures, *, fault=None):
        super().__init__(fixtures)
        self.reconstructions = []
        self.fault = fault

    def decode(self, raw):
        index = super().decode(raw)
        cell_number, reconstruct = len(self.decoded), index.reconstruct_n

        def bounded(start, count):
            self.reconstructions.append((cell_number, start, count))
            if count > 128:
                raise ValueError("synthetic full reconstruction refused")
            rows = reconstruct(start, count)
            if self.fault is not None:
                rows = self.fault(cell_number, index, start, count, rows)
            return rows

        index.reconstruct_n = bounded
        return index


def _chunk_case(fixtures, tmp_path, count=65, *, three_cells=False):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    rows = [fixtures._row("synthetic network retry", task_id=f"synthetic-task-{i}",
                          type="done" if three_cells else "message") for i in range(count)]
    if three_cells:
        rows.extend((fixtures._row("synthetic general record", type="message"),
                     fixtures._row("synthetic learning record", type="test")))
    source = fixtures._all_tasks_case(reader, tmp_path, rows)
    if three_cells:
        policy = json.loads(source["messages"]["policy_bytes"])
        policy["types"].append("test")
        raw = fixtures._raw(policy)
        source["messages"].update(policy_bytes=raw, policy_sha256=reader._sha(raw))
        fixtures._edit_decision(source, message_policy_sha256=reader._sha(raw))
    case.update(fixtures._snapshot(source, reader.rederive_message_projection(**source)))
    return case


def test_named_capacity_profile_accepts_257_records_without_changing_b16(package, fixtures, tmp_path):
    case = _chunk_case(fixtures, tmp_path, 257)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    _refuses(package, args)
    assert codec.passages == codec.serialized == codec.decoded == []
    output = package.package_content_snapshot(**args, capacity_profile=CAPACITY_PROFILE)
    assert json.loads(output["memory_bytes"])["records"] == 257
    assert list(map(len, codec.passages)) == [16] * 16 + [1]
    assert _query(case, output, Codec(fixtures))["status"] == "ANSWER"


def test_capacity_builds_over_2mib_cell_and_supports_small_query_full_abi(package, fixtures, tmp_path):
    case = _chunk_case(fixtures, tmp_path, 683)
    codec = ChunkCodec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, CAPACITY_PROFILE))
    cell = output["cell_snapshots"]["general"]["artifacts"]
    assert len(cell["vectors"]) == 683 * 2 * 1536 > 2097152
    assert len(cell["index"]) > 2097152
    assert max(count for _, _, count in codec.reconstructions) == 128
    assert [len(batch) for batch in codec.passages] == [16] * 42 + [11]
    query_codec, calls = Codec(fixtures), []
    original = query_codec.decode
    def decoded(raw):
        index = original(raw)
        reconstruct = index.reconstruct_n
        def observed(start, count):
            calls.append((start, count))
            return reconstruct(start, count)
        index.reconstruct_n = observed
        return index
    query_codec.decode = decoded
    assert _query(case, output, query_codec)["status"] == "ANSWER"
    assert calls == [(0, 1366)]  # Existing query threshold is unchanged.


def test_capacity_accepts_verified_prefix_over_default_4mib(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    rows = [fixtures._row("synthetic network retry", task_id=f"synthetic-task-{i}",
                           payload={"padding": "x" * (3 * 1024 * 1024)}) for i in range(2)]
    source = fixtures._all_tasks_case(reader, tmp_path, rows)
    case.update(fixtures._snapshot(source, reader.rederive_message_projection(**source)))
    assert 4194304 < case["messages"]["end_cursor"].offset < 268435456
    codec = Codec(fixtures)
    _refuses(package, _arguments(case, codec))
    assert codec.passages == []
    output = package.package_content_snapshot(**_arguments(case, codec, CAPACITY_PROFILE))
    assert json.loads(output["memory_bytes"])["records"] == 2
    assert [len(batch) for batch in codec.passages] == [2]


@pytest.mark.parametrize("profile", [False, True, 0, 1, "", "large", "bounded_v3", [], {}])
def test_unknown_capacity_profiles_refuse_before_source_or_providers(package, monkeypatch, profile):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid profile touched source or provider")
    monkeypatch.setattr(package, "_snapshot", forbidden)
    _refuses(package, dict(enabled=True, capacity_profile=profile, snapshot=object(),
                           passage_encoder=forbidden, index_serializer=forbidden, index_decoder=forbidden))


def test_capacity_profile_must_be_exact_builtin_string(package, monkeypatch):
    class Derived(str):
        pass
    def forbidden(*args, **kwargs):
        pytest.fail("invalid profile touched source")
    monkeypatch.setattr(package, "_snapshot", forbidden)
    _refuses(package, dict(enabled=True, capacity_profile=Derived(CAPACITY_PROFILE), snapshot=object()))


def test_capacity_profile_limits_are_fixed_and_disjoint_from_default(package):
    assert package._capacity(None) is None
    assert package._capacity(CAPACITY_PROFILE) == {
        "records": 32768, "prefix": 268435456, "projection": 67108864,
        "text": 9830400, "artifact": 50331648, "package": 536870912,
        "index_overhead": 65536, "control": 65536}
    assert (package.MAX_RECORDS, package.MAX_PREFIX_BYTES, package.MAX_PROJECTION_BYTES,
            package.MAX_TEXT_BYTES, package.MAX_ARTIFACT_BYTES, package.MAX_PACKAGE_BYTES,
            package.PASSAGE_BATCH) == (256, 4194304, 2097152, 524288, 2097152, 8388608, 16)


@pytest.mark.parametrize("fault", ["depth", "nodes", "utf8", "nul", "size"])
def test_capacity_projection_allocation_refusals_precede_json_parse(package, fixtures, tmp_path, monkeypatch, fault):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    if fault == "depth":
        raw = b"[" * 17 + b"0" + b"]" * 17
    elif fault == "nodes":
        raw = b"[[]" + b",[]" * (256 + 64 * 32768) + b"]"
    elif fault == "utf8":
        raw = b'{"invalid":"\xff"}'
    elif fault == "nul":
        raw = '{"invalid":0}'.encode("utf-16-le")
    else:
        raw = b"x" * (67108864 + 1)
    codec = Codec(fixtures)
    args = _arguments(case, codec, CAPACITY_PROFILE)
    args["snapshot"].update(projection_bytes=raw, projection_sha256=reader._sha(raw))
    original = reader._pinned
    def pinned(data, *values, **kwargs):
        if data is raw:
            pytest.fail("projection reached JSON parser before allocation guard")
        return original(data, *values, **kwargs)
    monkeypatch.setattr(reader, "_pinned", pinned)
    _refuses(package, args)
    assert codec.passages == codec.serialized == codec.decoded == []


@pytest.mark.parametrize("count", [32768, 32769])
@pytest.mark.parametrize("cell_count", [1, 3])
def test_capacity_record_limit_is_global_before_metadata_allocation(package, monkeypatch, count, cell_count):
    calls = []
    def metadata(records, cell_id, limit):
        calls.append(len(records))
        return [], b"", b""
    monkeypatch.setattr(package, "_cell_metadata", metadata)
    sizes = [count // cell_count + (i < count % cell_count) for i in range(cell_count)]
    cells = {key: [None] * size for key, size in zip(("general", "learning", "system"), sizes)}
    capacity = package._capacity(CAPACITY_PROFILE)
    if count == 32769:
        with pytest.raises(ValueError, match="invalid_cell_records"):
            package._capacity_preflight(cells, count, 0, capacity)
        assert calls == []
    else:
        assert set(package._capacity_preflight(cells, count, 0, capacity)) == set(cells)
        assert sum(calls) == 32768
    # This is preflight arithmetic coverage, not a full-scale source/model run.


def test_capacity_projection_budget_accepts_real_32768_record_source_shape(package, fixtures, tmp_path):
    # This validates the genuinely source-derived maximum-count projection,
    # not 2048 passage callbacks or a full-scale index/model build.
    rows = [fixtures._row("synthetic work", task_id=f"synthetic-task-{i}") for i in range(32768)]
    source = fixtures._all_tasks_case(reader, tmp_path, rows)
    projection = reader.rederive_message_projection(**source)
    assert len(projection["messages"]["records"]) == 32768
    snapshot = fixtures._snapshot(source, projection)
    assert 2097152 < len(snapshot["projection_bytes"]) <= 67108864
    assert package._snapshot(snapshot, package._capacity(CAPACITY_PROFILE)) == snapshot
    assert reader.validate_content_snapshot(**snapshot)["status"] == "MATCH"


def test_capacity_projection_grammar_still_uses_authoritative_parser(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, CAPACITY_PROFILE)
    raw = b'{"messages":[}'
    args["snapshot"].update(projection_bytes=raw, projection_sha256=reader._sha(raw))
    original, parsed = reader._pinned, []
    def pinned(data, *values, **kwargs):
        if data is raw:
            parsed.append(True)
        return original(data, *values, **kwargs)
    def forbidden(*values):
        pytest.fail("malformed projection reached source derivation")
    monkeypatch.setattr(reader, "_pinned", pinned)
    monkeypatch.setattr(reader, "_projection", forbidden)
    _refuses(package, args)
    assert parsed == [True] and codec.passages == codec.serialized == codec.decoded == []


def test_capacity_projection_pin_precedes_scan_and_default_does_not_add_scan(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, CAPACITY_PROFILE)
    def forbidden(*values):
        pytest.fail("unexpected projection scan")
    monkeypatch.setattr(reader, "_q4_json_budget", forbidden)
    assert package._snapshot(args["snapshot"]) == args["snapshot"]
    args["snapshot"]["projection_sha256"] = "0" * 64
    _refuses(package, args)
    assert codec.passages == codec.serialized == codec.decoded == []


def test_capacity_prefix_limit_is_checked_before_source_access(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    snapshot = _arguments(case, Codec(fixtures))["snapshot"]
    capacity = package._capacity(CAPACITY_PROFILE)
    for offset in (268435456, 268435457):
        decision = json.loads(snapshot["decision_bytes"])
        decision["source_prefix"]["offset"] = offset
        snapshot["decision_bytes"] = fixtures._raw(decision)
        snapshot["decision_sha256"] = reader._sha(snapshot["decision_bytes"])
        if offset == 268435456:
            assert package._snapshot(snapshot, capacity) == snapshot
        else:
            def forbidden(*args):
                pytest.fail("oversized prefix reached source verification")
            monkeypatch.setattr(reader, "_verified", forbidden)
            codec = Codec(fixtures)
            args = _arguments(case, codec)
            args.update(snapshot=snapshot, capacity_profile=CAPACITY_PROFILE)
            _refuses(package, args)
            assert codec.passages == codec.serialized == codec.decoded == []


def test_capacity_metadata_counts_utf8_and_preserves_exact_bytes(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    record = json.loads(case["projection_bytes"])["messages"]["records"][0]
    record["stored_text"] = reader.encode_text('synthetic r\u00e9seau "quoted" \\ path {brackets}')
    ids, meta, provenance = package._cell_metadata([record], "general", 50331648)
    assert meta == reader._canonical({"dim": 384, "doc_ids": ids,
        "texts": [reader.decode_text(record["stored_text"])] * 2, "metadata": [{}, {}]})
    row = struct.pack("<384f", 0.6, 0.8, -0.0, *([0.0] * 381))
    codec = Codec(fixtures)
    old, _ = package._cell([record], "general", {record["record_id"]: row},
        json.loads(case["encoder_binding_bytes"])["encoder"], case["projection_sha256"],
        fixtures.AVAILABLE, codec.serialize, lambda: None)
    assert (meta, provenance) == (old["artifacts"]["meta"], old["artifacts"]["provenance"])
    exact = max(len(meta), len(provenance))
    assert package._cell_metadata([record], "general", exact) == (ids, meta, provenance)
    with pytest.raises(ValueError, match="artifact_over_budget"):
        package._cell_metadata([record], "general", exact - 1)


def test_capacity_48mib_append_boundary_does_not_allocate_over_budget(package):
    class Counter:
        def __init__(self, size):
            self.size, self.writes = size, []
        def tell(self):
            return self.size
        def write(self, raw):
            self.writes.append(raw)
            self.size += len(raw)
    limit = 50331648
    exact = Counter(limit - 2)
    package._append_bounded(exact, "\u00e4".encode(), limit)
    assert exact.size == limit
    too_large = Counter(limit - 1)
    with pytest.raises(ValueError, match="artifact_over_budget"):
        package._append_bounded(too_large, "\u00e4".encode(), limit)
    assert too_large.writes == []


@pytest.mark.parametrize("fault", ["artifact", "aggregate"])
def test_capacity_last_cell_budget_refuses_before_first_provider(package, fixtures, tmp_path, monkeypatch, fault):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)
    records = json.loads(case["projection_bytes"])["messages"]["records"]
    cells = {}
    for record in records:
        cells.setdefault(reader._Q4_PLACEMENT[record["meta"]["type"]], []).append(record)
    capacity = package._capacity(CAPACITY_PROFILE)
    original = package._cell_metadata
    prepared = {key: original(rows, key, capacity["artifact"]) for key, rows in cells.items()}
    if fault == "artifact":
        capacity["artifact"] = max(len(raw) for key in ("general", "learning") for raw in prepared[key][1:])
    else:
        base = len(case["projection_bytes"]) + len(case["encoder_binding_bytes"]) + len(case["index_binding_bytes"])
        reserved = base + capacity["control"] + sum(
            len(rows) * 2 * 1536 * 2 + capacity["index_overhead"] + capacity["control"]
            + sum(map(len, prepared[key][1:])) for key, rows in cells.items())
        capacity["package"] = reserved
        assert set(package._capacity_preflight(cells, len(records), base, capacity)) == set(cells)
        capacity["package"] = reserved - 1
    visited = []
    def observed(rows, key, limit):
        visited.append(key)
        return original(rows, key, limit)
    monkeypatch.setattr(package, "_capacity", lambda profile: capacity)
    monkeypatch.setattr(package, "_cell_metadata", observed)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    args["capacity_profile"] = CAPACITY_PROFILE
    _refuses(package, args)
    assert visited == ["general", "learning", "system"]
    assert codec.passages == codec.serialized == codec.decoded == []


@pytest.mark.parametrize("extra", [0, 1])
def test_capacity_actual_index_size_must_fit_row_based_reservation(package, fixtures, tmp_path, extra):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def serialized(raw, dim):
        codec.serialized.append(raw)
        return raw + b"x" * (65536 + extra)
    def decoded(raw):
        codec.decoded.append(raw)
        return fixtures._SyntheticFlatIP(list(struct.iter_unpack("<384f", raw[:-65536])))
    args.update(capacity_profile=CAPACITY_PROFILE, index_serializer=serialized, index_decoder=decoded)
    if extra:
        _refuses(package, args)
        assert codec.decoded == []
    else:
        output = package.package_content_snapshot(**args)
        assert len(output["cell_snapshots"]) == 2
        assert len(codec.decoded) == 2


def test_capacity_keeps_full_semantic_source_recheck_after_callback(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def changed(texts):
        result = codec.passage(texts)
        monkeypatch.setattr(reader, "_ALL_TASK_MAX_RECORDS", 0)
        return result
    args.update(capacity_profile=CAPACITY_PROFILE, passage_encoder=changed)
    _refuses(package, args)
    assert codec.passages and codec.serialized == codec.decoded == []


@pytest.mark.parametrize("count", [1, 64, 65, 128, 256])
def test_producer_reconstructs_v3_in_exact_bounded_ranges(package, fixtures, tmp_path, count):
    case = _chunk_case(fixtures, tmp_path, count)
    codec = ChunkCodec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec))
    assert json.loads(output["memory_bytes"])["records"] == count
    assert codec.reconstructions == [(1, start, min(128, count * 2 - start))
                                     for start in range(0, count * 2, 128)]
    if count == 65:
        # The unchanged reader uses full reconstruction below its v3 size
        # threshold, so its provider must support that ABI as well as chunks.
        result = _query(case, output, Codec(fixtures))
        assert result["status"] == "ANSWER"
        assert result["schema"] == "wd.q4-content-snapshot-query.v3"


@pytest.mark.parametrize("fault", ["last_bits", "negative_zero", "nan", "norm", "dimension",
                                  "short", "long", "controls", "exception"])
@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE, FULL_PREFIX_PROFILE])
def test_last_cell_last_chunk_refuses_without_partial_package(package, fixtures, tmp_path, capsys, fault, capacity_profile):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)

    def corrupt(cell_number, index, start, count, rows):
        if cell_number != 3 or start != 128:
            return rows
        if fault == "exception":
            raise MemoryError("synthetic-private-provider-error")
        if fault == "controls":
            index.ntotal += 1
            return rows
        if fault == "short":
            return rows[:-1]
        if fault == "long":
            return rows + rows[:1]
        last = list(rows[-1])
        if fault == "last_bits":
            last[0], last[1] = 0.0, 1.0
        elif fault == "negative_zero":
            last[2] = 0.0
        elif fault == "nan":
            last[0] = float("nan")
        elif fault == "norm":
            last[0] = 2.0
        else:
            last.pop()
        return rows[:-1] + (tuple(last),)

    codec = ChunkCodec(fixtures, fault=corrupt)
    args = _arguments(case, codec, capacity_profile)
    row = struct.pack("<384f", 1.0, 0.0, -0.0, *([0.0] * 381))
    args["passage_encoder"] = lambda texts: row * len(texts)
    _refuses(package, args)
    assert codec.reconstructions == [(1, 0, 2), (2, 0, 2), (3, 0, 128), (3, 128, 2)]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("mutation", ["source", "generation", "input"])
@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE, FULL_PREFIX_PROFILE])
def test_last_reconstruction_source_mutation_is_checked_before_return(package, fixtures, tmp_path, mutation, capacity_profile):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)

    def change(cell_number, index, start, count, rows):
        if cell_number == 3 and start == 128:
            if mutation == "source":
                path = Path(case["messages"]["source_path"])
                path.write_bytes(path.read_bytes().replace(b"network retry", b"changed retry", 1))
            elif mutation == "generation":
                Path(case["messages"]["generation_path"]).write_bytes(b'{"generation":"synthetic-g2"}')
            else:
                args["snapshot"]["decision_sha256"] = "0" * 64
        return rows

    codec = ChunkCodec(fixtures, fault=change)
    args = _arguments(case, codec, capacity_profile)
    _refuses(package, args)
    assert codec.reconstructions[-1] == (3, 128, 2)


@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE, FULL_PREFIX_PROFILE])
def test_last_cell_pair_digest_checked_even_when_retained_bytes_match(package, fixtures, tmp_path, monkeypatch, capacity_profile):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)
    codec = ChunkCodec(fixtures)
    original = reader._q4_cell_controls

    def changed(*args, **kwargs):
        checked = original(*args, **kwargs)
        if args[0] == "system":
            # Isolate the decoded-pair check from the retained-byte check.
            checked[1]["pairs_sha256"] = "0" * 64
        return checked

    monkeypatch.setattr(reader, "_q4_cell_controls", changed)
    _refuses(package, _arguments(case, codec, capacity_profile))
    assert codec.reconstructions[-1] == (3, 128, 2)


@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE, FULL_PREFIX_PROFILE])
def test_last_cell_preflight_still_precedes_every_decoder(package, fixtures, tmp_path, monkeypatch, capacity_profile):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)
    codec = ChunkCodec(fixtures)
    original = package._cell

    def tampered(*args, **kwargs):
        item, pointer = original(*args, **kwargs)
        if args[1] == "system":
            item["artifacts"]["provenance"] += b"synthetic-tamper"
        return item, pointer

    monkeypatch.setattr(package, "_cell", tampered)
    _refuses(package, _arguments(case, codec, capacity_profile))
    assert len(codec.serialized) == 3 and codec.decoded == []


@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_full_only_decoder_has_no_large_reconstruction_fallback(package, fixtures, tmp_path, capacity_profile):
    case = _chunk_case(fixtures, tmp_path)
    codec = Codec(fixtures)
    calls = []

    def full_only(raw):
        index = codec.decode(raw)
        original = index.reconstruct_n

        def reconstruct(start, count):
            calls.append((start, count))
            if start != 0 or count != index.ntotal:
                raise ValueError("synthetic full-only provider")
            return original(start, count)

        index.reconstruct_n = reconstruct
        return index

    args = _arguments(case, codec, capacity_profile)
    args["index_decoder"] = full_only
    _refuses(package, args)
    assert calls == [(0, 128)]


def test_capacity_profile_preserves_small_artifacts_and_every_checkpoint(package, fixtures, tmp_path, monkeypatch):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)
    original = reader._verified
    events = []
    def verified(*args):
        events.append("verified")
        return original(*args)
    monkeypatch.setattr(reader, "_verified", verified)
    outputs, traces = [], []
    for options in ({}, {"capacity_profile": None}, {"capacity_profile": CAPACITY_PROFILE}):
        events.clear()
        codec = Codec(fixtures)
        args = _arguments(case, codec)
        args.update(options)
        for name in ("passage_encoder", "index_serializer", "index_decoder"):
            callback = args[name]
            def observed(*values, callback=callback, name=name):
                events.append(name)
                return callback(*values)
            args[name] = observed
        outputs.append(package.package_content_snapshot(**args))
        traces.append(list(events))
    assert outputs[0] == outputs[1] == outputs[2]
    assert traces[0] == traces[1] == traces[2]
    assert traces[0].count("verified") == 16
    assert traces[0].count("passage_encoder") == 5
    assert traces[0].count("index_serializer") == traces[0].count("index_decoder") == 3


def test_bounded_decode_preserves_artifacts_and_source_checkpoint_order(package, fixtures, tmp_path, monkeypatch):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)
    events = []
    original = reader._verified

    def verified(*args):
        events.append("verified")
        return original(*args)

    monkeypatch.setattr(reader, "_verified", verified)

    def build(codec):
        args = _arguments(case, codec)
        for name in ("passage_encoder", "index_serializer", "index_decoder"):
            callback = args[name]

            def observed(*values, callback=callback, name=name):
                events.append(name)
                return callback(*values)

            args[name] = observed
        return package.package_content_snapshot(**args)

    codec = ChunkCodec(fixtures)
    bounded = build(codec)
    bounded_events = list(events)
    assert codec.reconstructions == [(1, 0, 2), (2, 0, 2), (3, 0, 128), (3, 128, 2)]
    events.clear()
    # Same verified source/artifacts through the previous validation routine.
    monkeypatch.setattr(reader, "_q4_decode_cells_bounded_v3", reader._q4_decode_cells)
    legacy = build(Codec(fixtures))
    assert legacy == bounded
    expected = (["verified"] + ["passage_encoder", "verified"] * 5
                + ["index_serializer", "verified"] * 3
                + ["verified", "index_decoder", "verified"] * 3 + ["verified"])
    assert events == bounded_events == expected
    assert bounded_events.count("verified") == 16


def _arguments(case, codec, capacity_profile=None):
    return dict(enabled=True, snapshot={key: case[key] for key in reader._Q4_SOURCE_KEYS},
                passage_encoder=codec.passage, index_serializer=codec.serialize, index_decoder=codec.decode,
                **({"capacity_profile": capacity_profile} if capacity_profile is not None else {}),
                **{key: case[key] for key in ("encoder_binding_bytes", "encoder_binding_sha256",
                                             "index_binding_bytes", "index_binding_sha256")})


def _query(case, output, codec):
    encoder, index = reader.adapt_readonly_providers(
        encoder=case["encoder_provider"], index_decoder=codec.decode,
        **{key: output[key] for key in ("encoder_binding_bytes", "encoder_binding_sha256",
                                       "index_binding_bytes", "index_binding_sha256")},
        index_sha256s=[reader._sha(cell["artifacts"]["index"]) for cell in output["cell_snapshots"].values()],
        allow_bounded_v3=True)
    return reader.query_content_snapshot(
        **{key: case[key] for key in reader._Q4_SOURCE_KEYS}, **output,
        encoder_provider=encoder, index_provider=index,
        stream=codec.fixtures._ObservedStream(codec.fixtures._raw(
            {"situation": "retry network outage", "time": codec.fixtures.QUERY_TIME})))


def test_two_source_snapshots_round_trip_without_changing_first(package, fixtures, tmp_path):
    assert package is not None, "v3 in-memory snapshot producer is not implemented"
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = fixtures._semantic_case(reader, first_dir, all_tasks=True)
    second = fixtures._semantic_case(reader, second_dir, all_tasks=True)
    # A new complete, independently pinned synthetic source; no live cursor is advanced.
    rows = [fixtures._row(text, type="message" if i < 2 else "done",
                          task_id=f"synthetic-other-task-{i}")
            for i, text in enumerate((*fixtures.SEMANTIC_TEXTS, "new synthetic network retry repair"))]
    source = fixtures._all_tasks_case(reader, second_dir, rows)
    second.update(fixtures._snapshot(source, reader.rederive_message_projection(**source)))
    codec = Codec(fixtures)
    first_output = package.package_content_snapshot(**_arguments(first, codec))
    frozen = json.dumps(first_output, default=lambda raw: raw.hex(), sort_keys=True)
    first_result = _query(first, first_output, codec)
    second_output = package.package_content_snapshot(**_arguments(second, codec))
    second_result = _query(second, second_output, codec)
    assert first_result["status"] == second_result["status"] == "ANSWER"
    assert first_result["schema"] == second_result["schema"] == "wd.q4-content-snapshot-query.v3"
    assert first_result["authority"] == second_result["authority"] == "none"
    assert first_output["memory_sha256"] != second_output["memory_sha256"]
    assert json.loads(first_output["memory_bytes"])["records"] == 4
    assert json.loads(second_output["memory_bytes"])["records"] == 5
    assert json.dumps(first_output, default=lambda raw: raw.hex(), sort_keys=True) == frozen
    assert _query(first, first_output, codec) == first_result


def _refuses(package, args):
    with pytest.raises(package.SnapshotPackageRefused) as caught:
        package.package_content_snapshot(**args)
    assert str(caught.value) == "content_snapshot_package_refused"
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize("enabled", [False, None, 0, 1, "true"])
def test_default_closed_before_snapshot_or_callbacks(package, enabled, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("disabled producer touched source/provider")
    monkeypatch.setattr(reader, "_verified", forbidden)
    _refuses(package, dict(enabled=enabled, snapshot=object(), passage_encoder=forbidden,
                           index_serializer=forbidden, index_decoder=forbidden))
    _refuses(package, {})


@pytest.mark.parametrize("fault", ["source", "generation", "projection_pin", "foreign_projection", "v2",
                                  "decision_schema", "scope_pin", "encoder_binding", "index_binding"])
@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_source_and_version_refusal_precedes_all_callbacks(package, fixtures, tmp_path, fault, capacity_profile):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=fault != "v2")
    codec = Codec(fixtures)
    args = _arguments(case, codec, capacity_profile)
    snapshot = args["snapshot"]
    if fault == "source":
        path = Path(snapshot["messages"]["source_path"])
        path.write_bytes(path.read_bytes().replace(b"network retry", b"changed retry", 1))
    elif fault == "generation":
        Path(snapshot["messages"]["generation_path"]).write_bytes(b'{"generation":"synthetic-g2"}')
    elif fault == "projection_pin":
        snapshot["projection_sha256"] = "0" * 64
    elif fault == "foreign_projection":
        projection = json.loads(snapshot["projection_bytes"])
        projection["messages"]["records"][-1]["stored_text"] = "foreign invented content"
        snapshot["projection_bytes"] = fixtures._serialized(projection)
        snapshot["projection_sha256"] = reader._sha(snapshot["projection_bytes"])
    elif fault == "decision_schema":
        decision = json.loads(snapshot["decision_bytes"])
        decision["schema"] = "wd.mind-content-route-decision.v2"
        snapshot["decision_bytes"] = fixtures._raw(decision)
        snapshot["decision_sha256"] = reader._sha(snapshot["decision_bytes"])
    elif fault == "scope_pin":
        snapshot["approved_tasks_sha256"] = "0" * 64
    elif fault in ("encoder_binding", "index_binding"):
        key = fault + "_bytes"
        binding = json.loads(args[key])
        binding["schema"] = "wd.mind-memory-encoder-binding.v1"
        args[key] = fixtures._raw(binding)
        args[fault + "_sha256"] = reader._sha(args[key])
    _refuses(package, args)
    assert codec.passages == codec.serialized == codec.decoded == []


@pytest.mark.parametrize("phase", ["passage", "serialize", "decode"])
@pytest.mark.parametrize("mutation", ["source", "generation", "input"])
@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_callback_changes_refuse_without_output(package, fixtures, tmp_path, phase, mutation, capacity_profile):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, capacity_profile)
    field = {"passage": "passage_encoder", "serialize": "index_serializer", "decode": "index_decoder"}[phase]
    original = args[field]
    def changed(*values):
        result = original(*values)
        if mutation == "source":
            path = Path(case["messages"]["source_path"])
            path.write_bytes(path.read_bytes().replace(b"network retry", b"changed retry", 1))
        elif mutation == "generation":
            Path(case["messages"]["generation_path"]).write_bytes(b'{"generation":"synthetic-g2"}')
        else:
            args["snapshot"]["decision_sha256"] = "0" * 64
        return result
    args[field] = changed
    _refuses(package, args)
    if phase == "passage":
        assert codec.serialized == codec.decoded == []
    elif phase == "serialize":
        assert len(codec.serialized) == 1 and codec.decoded == []


@pytest.mark.parametrize("fault", ["bytearray", "short", "long", "float64", "zero", "nan", "inf", "norm"])
def test_passage_requires_exact_finite_normalized_float32_bytes(package, fixtures, tmp_path, fault):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def invalid(texts):
        raw = codec.passage(texts)
        if fault == "bytearray":
            return bytearray(raw)
        if fault == "short":
            return raw[:-1]
        if fault == "long":
            return raw + b"\0"
        if fault == "float64":
            return struct.pack("<" + "d" * (len(raw) // 4), *struct.unpack("<" + "f" * (len(raw) // 4), raw))
        first = {"zero": 0.0, "nan": float("nan"), "inf": float("inf"), "norm": 2.0}[fault]
        return struct.pack("<384f", first, *([0.0] * 383)) * len(texts)
    args["passage_encoder"] = invalid
    _refuses(package, args)
    assert codec.serialized == codec.decoded == []


@pytest.mark.parametrize("fault", ["dim", "count", "metric", "last_vector", "changing_controls"])
def test_last_decoded_cell_must_match_all_retained_vectors(package, fixtures, tmp_path, fault):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def invalid(raw):
        index = codec.decode(raw)
        if len(codec.decoded) == 2:
            if fault == "dim":
                index.d = 383
            elif fault == "count":
                index.ntotal += 1
            elif fault == "metric":
                index.metric_type = 1
            elif fault == "last_vector":
                index._vectors = index._vectors[:-1] + ((0.0, 1.0) + (0.0,) * 382,)
            else:
                reconstruct = index.reconstruct_n
                def changing(start, count):
                    rows = reconstruct(start, count)
                    index.ntotal += 1
                    return rows
                index.reconstruct_n = changing
        return index
    args["index_decoder"] = invalid
    _refuses(package, args)
    assert len(codec.serialized) == len(codec.decoded) == 2


def test_float32_bits_and_negative_zero_are_retained(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    row = struct.pack("<384f", 0.6, 0.8, -0.0, *([0.0] * 381))
    args["passage_encoder"] = lambda texts: row * len(texts)
    output = package.package_content_snapshot(**args)
    for cell in output["cell_snapshots"].values():
        raw = cell["artifacts"]["vectors"]
        assert raw == row * (len(raw) // len(row))
    original = codec.decode
    def lose_sign(raw):
        index = original(raw)
        index._vectors = tuple(tuple(0.0 if value == 0.0 else value for value in vector)
                               for vector in index._vectors)
        return index
    args["index_decoder"] = lose_sign
    _refuses(package, args)


@pytest.mark.parametrize("phase", ["passage_encoder", "index_serializer", "index_decoder"])
@pytest.mark.parametrize("exception", [RuntimeError, MemoryError])
def test_callback_exceptions_have_no_private_context(package, fixtures, tmp_path, capsys, phase, exception):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def fail(*unused):
        raise exception("synthetic-private-content-must-not-escape")
    args[phase] = fail
    _refuses(package, args)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("phase", ["passage_encoder", "index_serializer", "index_decoder"])
@pytest.mark.parametrize("exception", [SystemExit, GeneratorExit])
@pytest.mark.parametrize("profile", [None, FULL_PREFIX_PROFILE])
def test_provider_termination_is_fixed_refusal_without_private_context(
        package, fixtures, tmp_path, capsys, phase, exception, profile):
    import traceback

    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, profile)
    marker = "synthetic-provider-private-detail"

    def fail(*unused):
        raise exception(marker)

    args[phase] = fail
    with pytest.raises(package.SnapshotPackageRefused, match="^content_snapshot_package_refused$") as caught:
        package.package_content_snapshot(**args)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert marker not in "".join(traceback.format_exception(caught.value))
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("phase", ["passage_encoder", "index_serializer", "index_decoder"])
@pytest.mark.parametrize("profile", [None, FULL_PREFIX_PROFILE])
def test_provider_keyboard_interrupt_remains_interrupt(package, fixtures, tmp_path, phase, profile):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    args = _arguments(case, Codec(fixtures), profile)

    def interrupt(*unused):
        raise KeyboardInterrupt()

    args[phase] = interrupt
    with pytest.raises(KeyboardInterrupt):
        package.package_content_snapshot(**args)


@pytest.mark.parametrize("phase", ["passage_encoder", "index_serializer", "index_decoder"])
@pytest.mark.parametrize("kind", ["custom", "termination_group", "nested_group"])
def test_snapshot_provider_baseexception_tree_is_fixed_refusal(package, fixtures, tmp_path, capsys, phase, kind):
    import traceback

    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    args = _arguments(case, Codec(fixtures), FULL_PREFIX_PROFILE)
    marker = "synthetic-provider-group-private-detail"
    calls = []
    def fail(*unused):
        calls.append(phase)
        raise fixtures._provider_abort_problem(kind, marker)
    args[phase] = fail
    with pytest.raises(package.SnapshotPackageRefused, match="^content_snapshot_package_refused$") as caught:
        package.package_content_snapshot(**args)
    assert calls == [phase]
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert marker not in "".join(traceback.format_exception(caught.value))
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("phase", ["passage_encoder", "index_serializer", "index_decoder"])
@pytest.mark.parametrize("kind", ["keyboard", "cancelled", "mixed_group"])
def test_snapshot_provider_cancellation_tree_is_preserved_without_detail(package, fixtures, tmp_path, capsys, phase, kind):
    import asyncio

    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    args = _arguments(case, Codec(fixtures), FULL_PREFIX_PROFILE)
    marker = "synthetic-provider-group-private-detail"
    calls = []
    def fail(*unused):
        calls.append(phase)
        raise fixtures._provider_abort_problem(kind, marker)
    args[phase] = fail
    with pytest.raises(BaseException) as caught:
        package.package_content_snapshot(**args)
    expected = ([KeyboardInterrupt] if kind == "keyboard" else [asyncio.CancelledError]
                if kind == "cancelled" else [KeyboardInterrupt, asyncio.CancelledError])
    fixtures._assert_provider_cancel_safe(caught.value, expected, marker)
    assert calls == [phase]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("kind", ["system_exit", "cancelled", "mixed_group"])
def test_snapshot_refusal_suppresses_already_active_caller_context(package, fixtures, tmp_path, capsys, kind):
    import asyncio
    import traceback

    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    args = _arguments(case, Codec(fixtures), FULL_PREFIX_PROFILE)
    marker = "synthetic-caller-provider-detail"
    problem = SystemExit(marker) if kind == "system_exit" else fixtures._provider_abort_problem(kind, marker)
    calls = []
    try:
        raise problem
    except BaseException as active:
        def fail(*unused):
            calls.append(True)
            raise active
        args["passage_encoder"] = fail
        with pytest.raises(BaseException) as caught:
            package.package_content_snapshot(**args)
        result = caught.value
        if kind == "system_exit":
            assert type(result) is package.SnapshotPackageRefused
        elif kind == "cancelled":
            assert type(result) is asyncio.CancelledError
        else:
            assert type(result) is BaseExceptionGroup
            assert [type(item) for item in result.exceptions] == [KeyboardInterrupt, asyncio.CancelledError]
        assert result.__context__ is active and result.__cause__ is None and result.__suppress_context__
        assert marker not in "".join(traceback.format_exception(result))
        assert calls == [True]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_only_screened_source_text_reaches_passage_and_artifacts(package, fixtures, tmp_path, capacity_profile):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    rows = [fixtures._row(text, type="message" if i < 2 else "done", task_id=f"synthetic-task-{i}")
            for i, text in enumerate(fixtures.SEMANTIC_TEXTS)]
    rows.extend((fixtures._row("Synthetic Private Operator confirmed network repair"),
                 fixtures._row("synthetic forbidden RCO-only content", agent="claude-rco-1",
                               agent_uuid=fixtures.ROSTER["claude-rco-1"])))
    source = fixtures._all_tasks_case(reader, tmp_path, rows)
    projection = reader.rederive_message_projection(**source)
    case.update(fixtures._snapshot(source, projection))
    codec = Codec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, capacity_profile))
    passages = [text for batch in codec.passages for text in batch]
    expected = ["passage: " + reader.decode_text(record["stored_text"])[:2000]
                for record in projection["messages"]["records"]]
    # Private-identifier and RCO-authored rows are excluded by the existing
    # source policy, rather than copied or reclassified by the producer.
    assert passages == expected and len(passages) == 4
    combined = " ".join(passages) + str(output)
    assert "Synthetic Private Operator" not in combined
    assert "synthetic forbidden RCO-only content" not in combined
    assert json.loads(output["memory_bytes"])["authority"] == "none"
    assert not {"source_path", "generation_path", "identifier_policy_binding", "status", "authority"} & set(output)


@pytest.mark.parametrize("count", [256, 257])
def test_initial_record_ceiling_and_bounded_passage_calls(package, fixtures, tmp_path, count):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    source = fixtures._all_tasks_case(reader, tmp_path, [
        fixtures._row(f"synthetic record {i} network retry", task_id=f"synthetic-task-{i}") for i in range(count)])
    case.update(fixtures._snapshot(source, reader.rederive_message_projection(**source)))
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    if count > 256:
        _refuses(package, args)
        assert codec.passages == codec.serialized == codec.decoded == []
    else:
        output = package.package_content_snapshot(**args)
        assert json.loads(output["memory_bytes"])["records"] == 256
        assert len(codec.passages) == 16 and all(len(batch) == 16 for batch in codec.passages)


@pytest.mark.parametrize("control", ["MAX_PROJECTION_BYTES", "MAX_PREFIX_BYTES", "MAX_TEXT_BYTES"])
def test_input_budgets_precede_callbacks(package, fixtures, tmp_path, monkeypatch, control):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    monkeypatch.setattr(package, control, 1)
    _refuses(package, _arguments(case, codec))
    assert codec.passages == codec.serialized == codec.decoded == []


def test_serialized_and_aggregate_budgets_precede_decoder(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    args["index_serializer"] = lambda raw, dim: b"x" * (package.MAX_ARTIFACT_BYTES + 1)
    _refuses(package, args)
    assert codec.decoded == []
    args["index_serializer"] = codec.serialize
    monkeypatch.setattr(package, "MAX_PACKAGE_BYTES", 1)
    _refuses(package, args)
    assert len(codec.serialized) == 1 and codec.decoded == []


@pytest.mark.parametrize("artifact", ["index", "vectors", "meta", "provenance", "receipt"])
def test_tampered_package_is_refused_by_current_query(package, fixtures, tmp_path, artifact):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec))
    codec.decoded.clear()
    output["cell_snapshots"]["system"]["artifacts"][artifact] += b"tamper"
    with pytest.raises(reader.ContentQueryRefused):
        _query(case, output, codec)
    assert codec.decoded == [] and case["encoder_provider"].calls == []


@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_success_and_refusal_do_not_write_or_load_native_runtime(package, fixtures, tmp_path, monkeypatch, capacity_profile):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, capacity_profile)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    modules = set(sys.modules)
    original_open = builtins.open
    def readonly_open(file, mode="r", *values, **kwargs):
        assert not set(str(mode)) & set("wax+"), "producer attempted a write"
        return original_open(file, mode, *values, **kwargs)
    monkeypatch.setattr(builtins, "open", readonly_open)
    output = package.package_content_snapshot(**args)
    args["encoder_binding_sha256"] = "0" * 64
    _refuses(package, args)
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after
    assert not {"faiss", "numpy", "torch", "sentence_transformers"} & (set(sys.modules) - modules)
    assert set(output) == {"descriptor_bytes", "descriptor_sha256", "memory_bytes", "memory_sha256",
        "cell_snapshots", "encoder_sha256", "runtime_revision", "encoder_binding_bytes", "encoder_binding_sha256",
        "index_binding_bytes", "index_binding_sha256"}


def test_exact_byte_ceilings_accept_and_do_not_truncate(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    output = package.package_content_snapshot(**args)
    projection = json.loads(case["projection_bytes"])
    monkeypatch.setattr(package, "MAX_PROJECTION_BYTES", len(case["projection_bytes"]))
    monkeypatch.setattr(package, "MAX_PREFIX_BYTES", case["messages"]["end_cursor"].offset)
    monkeypatch.setattr(package, "MAX_TEXT_BYTES", sum(len(reader.decode_text(record["stored_text"]).encode())
                        for record in projection["messages"]["records"]))
    monkeypatch.setattr(package, "MAX_ARTIFACT_BYTES", max(len(raw)
                        for item in output["cell_snapshots"].values() for raw in item["artifacts"].values()))
    total = len(case["projection_bytes"]) + sum(len(output[key]) for key in
        ("memory_bytes", "descriptor_bytes", "encoder_binding_bytes", "index_binding_bytes"))
    total += sum(len(item["pointer"]) + len(item["log"]) + sum(map(len, item["artifacts"].values()))
                 for item in output["cell_snapshots"].values())
    monkeypatch.setattr(package, "MAX_PACKAGE_BYTES", total)
    assert package.package_content_snapshot(**args) == output
    monkeypatch.setattr(package, "MAX_PACKAGE_BYTES", total - 1)
    codec.decoded.clear()
    _refuses(package, args)
    assert codec.decoded == []


def test_malformed_final_passage_batch_never_serializes_any_cell(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    source = fixtures._all_tasks_case(reader, tmp_path, [
        fixtures._row(f"synthetic record {i} network retry", task_id=f"synthetic-task-{i}") for i in range(17)])
    case.update(fixtures._snapshot(source, reader.rederive_message_projection(**source)))
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def bad_final(texts):
        raw = codec.passage(texts)
        return raw[:-1] if len(codec.passages) == 2 else raw
    args["passage_encoder"] = bad_final
    _refuses(package, args)
    assert [len(batch) for batch in codec.passages] == [16, 1]
    assert codec.serialized == codec.decoded == []


def test_source_change_while_reconstructing_first_cell_stops_next_decoder(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def changing(raw):
        index = codec.decode(raw)
        reconstruct = index.reconstruct_n
        def mutated(start, count):
            result = reconstruct(start, count)
            Path(case["messages"]["generation_path"]).write_bytes(b'{"generation":"synthetic-g2"}')
            return result
        index.reconstruct_n = mutated
        return index
    args["index_decoder"] = changing
    _refuses(package, args)
    assert len(codec.decoded) == 1
