"""Synthetic absence-of-generation contracts; no live bridge input."""
import hashlib
import importlib
import json

import pytest


@pytest.fixture
def full_fixture(tmp_path):
    bundle = tmp_path / 'bundle'
    shared = tmp_path / 'runtime' / 'shared'
    bundle.mkdir()
    shared.mkdir(parents=True)
    rows = [{'task_id': f'synthetic-task-{i}', 'message': 'synthetic'} for i in range(257)]
    rows.append({'message': 'synthetic event without a task'})
    source = shared / 'events.jsonl'
    source.write_bytes(b''.join(json.dumps(row, separators=(',', ':')).encode() + b'\n' for row in rows))
    fleet = json.dumps({'schema_version': 2, 'runtime_root': str(shared.parent)}, separators=(',', ':')).encode()
    (bundle / 'wd-fleet.json').write_bytes(fleet)
    manifest = json.dumps({'schema_version': 1, 'files': {'wd-fleet.json': hashlib.sha256(fleet).hexdigest()}},
                          separators=(',', ':')).encode()
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    return bundle, shared, source, hashlib.sha256(manifest).hexdigest(), rows


def test_explicit_full_prefix_does_not_apply_task_row_or_prefix_caps(full_fixture, monkeypatch):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    bundle, shared, source, pin, rows = full_fixture
    monkeypatch.setattr(module, 'MAX_TASKS', 1)
    monkeypatch.setattr(module, 'MAX_ROWS', 1)
    monkeypatch.setattr(module, 'MAX_SELECTED_ROWS', 1)
    monkeypatch.setattr(module, 'MAX_PREFIX_BYTES', 16)
    result = module.observe_prefix(enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin,
                                   scope='full_prefix_v1', tasks=None)
    assert result.metadata['scope'] == 'full_observed_prefix'
    assert result.metadata['end_offset'] == source.stat().st_size
    assert result.metadata['generation'] == result.metadata['continuity'] == 'UNKNOWN'
    assert result.metadata['source_authentication'] == 'UNKNOWN'
    assert len(result.rows) == len(rows)
    binding = module.bind_prefix(result, scope='full_prefix_v1', expected_tasks=None)
    assert module.verify_prefix(result, expected_binding=binding, scope='full_prefix_v1', expected_tasks=None,
                                enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin)
    assert not (shared / 'events.generation.json').exists()


@pytest.mark.parametrize('fault', ['sidecar', 'source', 'manifest'])
def test_explicit_full_prefix_recheck_refuses_changes(full_fixture, fault):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    bundle, shared, source, pin, _ = full_fixture
    result = module.observe_prefix(enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin,
                                   scope='full_prefix_v1', tasks=None)
    binding = module.bind_prefix(result, scope='full_prefix_v1', expected_tasks=None)
    if fault == 'sidecar': (shared / 'events.generation.json').write_bytes(b'{}')
    if fault == 'source': source.write_bytes(source.read_bytes().replace(b'synthetic-task-0', b'synthetic-task-Z', 1))
    if fault == 'manifest': (bundle / 'deployment-manifest.json').write_bytes(b'{}')
    with pytest.raises(module.PrefixSnapshotRefused):
        module.verify_prefix(result, expected_binding=binding, scope='full_prefix_v1', expected_tasks=None,
                             enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin)


def test_full_prefix_cannot_repurpose_bounded_scope(full_fixture):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    bundle, _, _, pin, _ = full_fixture
    with pytest.raises(module.PrefixSnapshotRefused):
        module.observe_prefix(enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin,
                              scope='full_prefix_v1', tasks={'synthetic-task-0'})


def test_full_prefix_metadata_only_matches_rows_without_retaining_them(full_fixture, monkeypatch):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    bundle, _, source, pin, rows = full_fixture
    first_line = source.read_bytes().splitlines(keepends=True)[0]
    with source.open('ab') as stream:
        stream.write(first_line)
    scans, hashes, canonical_calls = [], [], []
    original_scan, original_hash, original_event_id = module._scan, module._hash_prefix, module.event_id
    def scan(*args, **kwargs):
        digest, retained = original_scan(*args, **kwargs)
        scans.append((kwargs.get('metadata_only', False), digest, len(retained)))
        return digest, retained
    def hash_prefix(*args, **kwargs):
        digest = original_hash(*args, **kwargs)
        hashes.append(digest)
        return digest
    def canonical(row):
        canonical_calls.append(True)
        return original_event_id(row)
    monkeypatch.setattr(module, '_scan', scan)
    monkeypatch.setattr(module, '_hash_prefix', hash_prefix)
    monkeypatch.setattr(module, 'event_id', canonical)
    old = module.observe_prefix(enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin,
                                scope='full_prefix_v1')
    metadata = module.observe_full_prefix_metadata(enabled=True, bundle_root=str(bundle),
                                                   expected_manifest_sha256=pin)
    assert type(metadata) is dict and 'rows' not in metadata
    assert {k: v for k, v in metadata.items() if k != 'observed_utc'} == {
        k: v for k, v in old.metadata.items() if k != 'observed_utc'}
    assert scans == [(False, metadata['prefix_sha256'], len(rows)), (True, metadata['prefix_sha256'], 0)]
    assert hashes == [metadata['prefix_sha256'], metadata['prefix_sha256']]
    assert len(canonical_calls) == 2 * (len(rows) + 1)


def test_metadata_only_releases_private_tail_before_full_scan(full_fixture, monkeypatch):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    bundle, _, _, pin, _ = full_fixture
    released, scanned = [], []
    original_tail, original_scan = module.bridge.read_bridge_log_tail_lines, module._scan
    class TailProxy:
        def __init__(self, wrapped): self.wrapped = wrapped
        def __getattr__(self, name): return getattr(self.wrapped, name)
        def __del__(self): released.append(True)
    def tail(*args, **kwargs):
        return TailProxy(original_tail(*args, **kwargs))
    def scan(*args, **kwargs):
        assert released == [True], 'private tail result was retained across full scan'
        scanned.append(True)
        return original_scan(*args, **kwargs)
    monkeypatch.setattr(module.bridge, 'read_bridge_log_tail_lines', tail)
    monkeypatch.setattr(module, '_scan', scan)
    metadata = module.observe_full_prefix_metadata(enabled=True, bundle_root=str(bundle),
                                                   expected_manifest_sha256=pin)
    assert type(metadata) is dict and scanned == [True] and released == [True]


@pytest.mark.parametrize('fault', ['rewrite', 'truncate', 'identity', 'sidecar', 'fleet', 'manifest'])
def test_metadata_only_observation_refuses_changes_at_existing_fences(full_fixture, monkeypatch, fault):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    bundle, shared, source, pin, _ = full_fixture
    original_scan, original_identity = module._scan, module.bridge._file_identity
    changed = []
    def identity(stream):
        return 'synthetic-changed-identity' if changed and fault == 'identity' else original_identity(stream)
    def scan(*args, **kwargs):
        result = original_scan(*args, **kwargs)
        assert kwargs.get('metadata_only') is True
        if fault == 'rewrite':
            before = source.read_bytes()
            after = before.replace(b'synthetic-task-0', b'synthetic-task-Z', 1)
            assert after != before and len(after) == len(before)
            source.write_bytes(after)
        if fault == 'truncate': source.write_bytes(b'')
        if fault == 'sidecar': (shared / 'events.generation.json').write_bytes(b'{}')
        if fault == 'fleet': (bundle / 'wd-fleet.json').write_bytes(b'{}')
        if fault == 'manifest': (bundle / 'deployment-manifest.json').write_bytes(b'{}')
        changed.append(True)
        return result
    monkeypatch.setattr(module.bridge, '_file_identity', identity)
    monkeypatch.setattr(module, '_scan', scan)
    with pytest.raises(module.PrefixSnapshotRefused, match='^mind_prefix_snapshot_refused$') as caught:
        module.observe_full_prefix_metadata(enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert changed == [True]


def test_metadata_only_observation_keeps_original_prefix_when_source_appends(full_fixture, monkeypatch):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    bundle, _, source, pin, _ = full_fixture
    before = source.read_bytes()
    original, called = module._scan, []
    def scan(*args, **kwargs):
        result = original(*args, **kwargs)
        with source.open('ab') as stream:
            stream.write(b'{"message":"synthetic append"}\n')
        called.append(True)
        return result
    monkeypatch.setattr(module, '_scan', scan)
    metadata = module.observe_full_prefix_metadata(enabled=True, bundle_root=str(bundle),
                                                   expected_manifest_sha256=pin)
    assert called == [True] and metadata['end_offset'] == len(before)
    assert metadata['prefix_sha256'] == hashlib.sha256(before).hexdigest()


def test_metadata_only_observation_preserves_partial_tail_behavior(full_fixture):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    bundle, _, source, pin, _ = full_fixture
    complete = source.read_bytes()
    with source.open('ab') as stream:
        stream.write(b'{"message":"synthetic incomplete tail')
    old = module.observe_prefix(enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin,
                                scope='full_prefix_v1')
    metadata = module.observe_full_prefix_metadata(enabled=True, bundle_root=str(bundle),
                                                   expected_manifest_sha256=pin)
    assert {k: v for k, v in metadata.items() if k != 'observed_utc'} == {
        k: v for k, v in old.metadata.items() if k != 'observed_utc'}
    assert metadata['end_offset'] == len(complete)
    assert metadata['prefix_sha256'] == hashlib.sha256(complete).hexdigest()


@pytest.mark.parametrize('bad', [b'{"message": invalid}\n', b'{"message":"bad-\xff"}\n',
                                 b'{"message":"\\ud800"}\n', b'{"message":"unterminated"',
                                 b'{"message":"' + b'x' * (4 * 1024 * 1024) + b'"}\n'],
                         ids=['invalid-json', 'invalid-utf8', 'invalid-canonical', 'partial-row', 'over-4mib'])
def test_metadata_only_and_raw_observation_both_refuse_bad_interior_row(full_fixture, bad):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    bundle, _, source, pin, _ = full_fixture
    good = b'{"task_id":"synthetic-task","message":"synthetic"}\n'
    source.write_bytes(good + bad + good)
    for observe in (lambda: module.observe_prefix(enabled=True, bundle_root=str(bundle),
                                                  expected_manifest_sha256=pin, scope='full_prefix_v1'),
                    lambda: module.observe_full_prefix_metadata(enabled=True, bundle_root=str(bundle),
                                                                expected_manifest_sha256=pin)):
        with pytest.raises(module.PrefixSnapshotRefused, match='^mind_prefix_snapshot_refused$') as caught:
            observe()
        assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize('kind', ['memory', 'keyboard', 'cancelled'])
def test_metadata_only_preserves_existing_failure_and_cancel_semantics(full_fixture, monkeypatch, kind):
    import asyncio
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    bundle, _, _, pin, _ = full_fixture
    signal = {'memory': MemoryError('synthetic'), 'keyboard': KeyboardInterrupt(),
              'cancelled': asyncio.CancelledError()}[kind]
    called = []
    def fail(*_args, **_kwargs):
        called.append(True)
        raise signal
    monkeypatch.setattr(module, '_scan', fail)
    expected = module.PrefixSnapshotRefused if kind == 'memory' else type(signal)
    with pytest.raises(expected) as caught:
        module.observe_full_prefix_metadata(enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin)
    if kind != 'memory': assert caught.value is signal
    else: assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert called == [True]


def _anchored_cursor(source):
    from waggledance.core import bridge_log_reader as bridge
    with bridge._open_log(source) as stream:
        identity = bridge._file_identity(stream)
    return bridge.BridgeCursor(offset=source.stat().st_size, file_identity=identity, generation=None)


def test_complete_generationless_cursor_reads_all_canonical_events_and_allows_append(full_fixture, monkeypatch):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    from tools.bridge_compact_view import event_id
    _, shared, source, _, rows = full_fixture
    cursor, digest = _anchored_cursor(source), hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(module, 'MAX_ROWS', 1)
    monkeypatch.setattr(module, 'MAX_SELECTED_ROWS', 1)
    monkeypatch.setattr(module, 'MAX_PREFIX_BYTES', 16)
    with source.open('ab') as stream:
        stream.write(b'{"message":"later synthetic append"}\n')
    result = module.read_complete_generationless_prefix_rows(str(source), str(shared / 'events.generation.json'),
                                                              cursor, digest)
    assert result == {event_id(row): row for row in rows}


@pytest.mark.parametrize('fault', ['hash', 'identity', 'generation', 'sidecar', 'wrong_sidecar', 'partial'])
def test_complete_generationless_cursor_rejects_bad_anchor(full_fixture, fault):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    from waggledance.core import bridge_log_reader as bridge
    _, shared, source, _, _ = full_fixture
    cursor, digest = _anchored_cursor(source), hashlib.sha256(source.read_bytes()).hexdigest()
    generation = shared / 'events.generation.json'
    if fault == 'hash': digest = '0' * 64
    if fault == 'identity': cursor = bridge.BridgeCursor(cursor.offset, 'synthetic-wrong', None)
    if fault == 'generation': cursor = bridge.BridgeCursor(cursor.offset, cursor.file_identity, 'synthetic')
    if fault == 'sidecar': generation.write_bytes(b'{}')
    if fault == 'wrong_sidecar': generation = shared / 'different.json'
    if fault == 'partial':
        cursor = bridge.BridgeCursor(cursor.offset - 1, cursor.file_identity, None)
        digest = hashlib.sha256(source.read_bytes()[:cursor.offset]).hexdigest()
    with pytest.raises(ValueError):
        module.read_complete_generationless_prefix_rows(str(source), str(generation), cursor, digest)


@pytest.mark.parametrize('fault', ['rewrite', 'truncate', 'sidecar', 'rotate', 'memory'])
def test_complete_generationless_cursor_rejects_mutation_during_scan(full_fixture, monkeypatch, fault):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    _, shared, source, _, _ = full_fixture
    cursor, digest = _anchored_cursor(source), hashlib.sha256(source.read_bytes()).hexdigest()
    original = module._scan
    rotation = {'succeeded': False, 'denied': False}
    def mutate(*args):
        out = original(*args)
        if fault == 'rewrite': source.write_bytes(source.read_bytes().replace(b'synthetic-task-0', b'synthetic-task-Z', 1))
        if fault == 'truncate': source.write_bytes(b'')
        if fault == 'sidecar': (shared / 'events.generation.json').write_bytes(b'{}')
        if fault == 'rotate':
            replacement = source.with_name('synthetic-replacement')
            replacement.write_bytes(source.read_bytes())
            try:
                os.replace(replacement, source)
            except PermissionError:
                rotation['denied'] = True
                raise
            rotation['succeeded'] = True
        if fault == 'memory': raise MemoryError('synthetic allocation failure')
        return out
    monkeypatch.setattr(module, '_scan', mutate)
    with pytest.raises(ValueError):
        module.read_complete_generationless_prefix_rows(str(source), str(shared / 'events.generation.json'),
                                                        cursor, digest)
    if fault == 'rotate':
        if rotation['denied']:
            pytest.skip('platform refused replacement while source handle was open')
        assert rotation['succeeded'], 'rotation must actually occur before testing its detection'


def test_complete_generationless_sidecar_permission_error_is_not_absence(full_fixture, monkeypatch):
    module = importlib.import_module('tools.wd_mind_absent_generation_prefix_readonly')
    _, shared, source, _, _ = full_fixture
    cursor, digest = _anchored_cursor(source), hashlib.sha256(source.read_bytes()).hexdigest()
    sidecar = shared / 'events.generation.json'
    original = module.os.lstat
    def refused(path, *args, **kwargs):
        if str(path) == str(sidecar):
            raise PermissionError('synthetic denied')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(module.os, 'lstat', refused)
    with pytest.raises(ValueError):
        module.read_complete_generationless_prefix_rows(str(source), str(sidecar), cursor, digest)


import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from tools import wd_mind_absent_generation_prefix_readonly as reader
from tools.bridge_compact_view import event_id
from waggledance.core import bridge_log_reader as bridge


def _raw(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@pytest.fixture
def fixture(tmp_path):
    bundle = tmp_path / "synthetic-bundle"
    root = tmp_path / "synthetic-runtime"
    shared = root / "shared"
    bundle.mkdir()
    shared.mkdir(parents=True)
    source = shared / "events.jsonl"
    rows = [{"task_id": "work-one", "message": "synthetic alpha"},
            {"task_id": "work-two", "message": "synthetic beta"}]
    payload = b"".join(_raw(row) + b"\n" for row in rows)
    source.write_bytes(payload)
    fleet = _raw({"schema_version": 2, "runtime_root": str(root)})
    (bundle / "wd-fleet.json").write_bytes(fleet)
    manifest = _raw({"schema_version": 1, "files": {"wd-fleet.json": hashlib.sha256(fleet).hexdigest()}})
    (bundle / "deployment-manifest.json").write_bytes(manifest)
    return {"bundle": bundle, "source": source, "sidecar": shared / "events.generation.json",
            "manifest": manifest, "pin": hashlib.sha256(manifest).hexdigest(), "rows": rows, "payload": payload}


def _observe(fixture, **kwargs):
    return reader.observe_prefix(enabled=True, bundle_root=str(fixture["bundle"]),
                                 expected_manifest_sha256=fixture["pin"], tasks={"work-one"}, **kwargs)


def _verify(fixture, snapshot, binding=None, tasks=None):
    if tasks is None:
        tasks = {"work-one"}
    if binding is None:
        binding = reader.bind_prefix(snapshot, expected_tasks=tasks)
    return reader.verify_prefix(snapshot, expected_binding=binding, expected_tasks=tasks,
                                enabled=True, bundle_root=str(fixture["bundle"]),
                                expected_manifest_sha256=fixture["pin"])


def _refused(call):
    with pytest.raises(reader.PrefixSnapshotRefused) as caught:
        call()
    assert str(caught.value) == "mind_prefix_snapshot_refused"
    assert caught.value.__cause__ is None


def test_default_off_does_not_inspect_inputs():
    class Untouchable:
        def __getattribute__(self, _):
            pytest.fail("disabled path touched input")
    _refused(lambda: reader.observe_prefix(bundle_root=Untouchable(), tasks=Untouchable()))


def test_observe_is_private_read_only_and_binds_canonical_ref(fixture):
    before = fixture["source"].read_bytes()
    snapshot = _observe(fixture)
    assert snapshot.metadata["schema"] == reader.SCHEMA
    assert snapshot.metadata["generation"] == snapshot.metadata["continuity"] == "UNKNOWN"
    assert snapshot.metadata["source_authentication"] == "UNKNOWN"
    assert snapshot.metadata["authority"] == "none"
    assert snapshot.metadata["sidecar_observation"] == "ABSENT_AT_CHECKS"
    assert snapshot.metadata["prefix_sha256"] == hashlib.sha256(before).hexdigest()
    assert snapshot.metadata["end_offset"] == len(before)
    assert snapshot.rows == {event_id(fixture["rows"][0]): fixture["rows"][0]}
    assert "synthetic alpha" not in repr(snapshot)
    assert snapshot.metadata["prefix_sha256"] not in repr(snapshot)
    assert _verify(fixture, snapshot) is True
    assert fixture["source"].read_bytes() == before and not fixture["sidecar"].exists()
    assert sorted(path.name for path in fixture["bundle"].iterdir()) == ["deployment-manifest.json", "wd-fleet.json"]


def test_append_after_frozen_cutoff_is_allowed(fixture, monkeypatch):
    original = reader._scan
    def append_after_scan(*args):
        result = original(*args)
        with fixture["source"].open("ab") as stream:
            stream.write(b'{"task_id":"work-one","message":"later"}\n')
        return result
    monkeypatch.setattr(reader, "_scan", append_after_scan)
    snapshot = _observe(fixture)
    assert snapshot.metadata["prefix_sha256"] == hashlib.sha256(fixture["payload"]).hexdigest()
    assert snapshot.metadata["end_offset"] == len(fixture["payload"])
    assert len(snapshot.rows) == 1 and _verify(fixture, snapshot)


@pytest.mark.parametrize("stage", ["before", "after_tail", "during_parse", "after_scan", "during_second_hash"])
def test_sidecar_appearance_is_refused(fixture, monkeypatch, stage):
    def appear():
        fixture["sidecar"].write_bytes(b'{"generation":"synthetic"}')
    if stage == "before":
        appear()
    elif stage == "after_tail":
        original = bridge.read_bridge_log_tail_lines
        def hook(*args, **kwargs):
            value = original(*args, **kwargs)
            appear()
            return value
        monkeypatch.setattr(bridge, "read_bridge_log_tail_lines", hook)
    elif stage == "during_parse":
        original = bridge._parse_rows
        def hook(*args, **kwargs):
            value = original(*args, **kwargs)
            appear()
            return value
        monkeypatch.setattr(bridge, "_parse_rows", hook)
    elif stage == "after_scan":
        original = reader._scan
        def hook(*args, **kwargs):
            value = original(*args, **kwargs)
            appear()
            return value
        monkeypatch.setattr(reader, "_scan", hook)
    else:
        original = reader._hash_prefix
        def hook(*args, **kwargs):
            value = original(*args, **kwargs)
            appear()
            return value
        monkeypatch.setattr(reader, "_hash_prefix", hook)
    _refused(lambda: _observe(fixture))


@pytest.mark.parametrize("fault", ["rewrite", "truncate", "rotate"])
def test_source_mutation_during_parse_is_refused(fixture, monkeypatch, fault):
    original = bridge._parse_rows
    called = False
    def mutate(*args, **kwargs):
        nonlocal called
        value = original(*args, **kwargs)
        if not called:
            called = True
            path = fixture["source"]
            if fault == "rewrite":
                path.write_bytes(fixture["payload"].replace(b"alpha", b"omega"))
            elif fault == "truncate":
                path.write_bytes(b'{}\n')
            else:
                replacement = path.with_name("synthetic-replacement.jsonl")
                replacement.write_bytes(fixture["payload"])
                os.replace(replacement, path)
        return value
    monkeypatch.setattr(bridge, "_parse_rows", mutate)
    _refused(lambda: _observe(fixture))


@pytest.mark.parametrize("name", ["deployment-manifest.json", "wd-fleet.json"])
def test_deployment_changes_during_parse_are_refused(fixture, monkeypatch, name):
    original = reader._scan
    def alter(*args):
        result = original(*args)
        (fixture["bundle"] / name).write_bytes(b'{}')
        return result
    monkeypatch.setattr(reader, "_scan", alter)
    _refused(lambda: _observe(fixture))


def test_sidecar_permission_failure_is_not_absence(fixture, monkeypatch):
    original = reader.os.lstat
    def lstat(path, *args, **kwargs):
        if Path(path) == fixture["sidecar"]:
            raise PermissionError("synthetic private path")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(reader.os, "lstat", lstat)
    _refused(lambda: _observe(fixture))


@pytest.mark.parametrize("bad", [b'\xef\xbb\xbf{}\n', b'\xff\n', b'{"x":1,"x":2}\n',
    b'{"x":1,"X":2}\n', b'{"x":NaN}\n', b'{}\n{\n'])
def test_lexical_or_complete_line_failure_refuses(fixture, bad):
    fixture["source"].write_bytes(bad)
    _refused(lambda: _observe(fixture))


def test_partial_tail_is_not_included(fixture):
    with fixture["source"].open("ab") as stream:
        stream.write(b'{"unfinished":')
    result = _observe(fixture)
    assert result.metadata["end_offset"] == len(fixture["payload"])
    assert result.metadata["prefix_sha256"] == hashlib.sha256(fixture["payload"]).hexdigest()


@pytest.mark.parametrize("fault", ["wrong_manifest_pin", "fleet_tamper", "outside_task_budget", "source_reparse"])
def test_pins_budget_and_reparse_refuse(fixture, monkeypatch, fault):
    if fault == "wrong_manifest_pin":
        fixture["pin"] = "f" * 64
    elif fault == "fleet_tamper":
        (fixture["bundle"] / "wd-fleet.json").write_bytes(b'{}')
    elif fault == "outside_task_budget":
        _refused(lambda: reader.observe_prefix(enabled=True, bundle_root=str(fixture["bundle"]),
            expected_manifest_sha256=fixture["pin"], tasks={f"task-{n}" for n in range(129)}))
        return
    else:
        original = reader.os.lstat
        def lstat(path, *args, **kwargs):
            if Path(path) == fixture["source"]:
                return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(reader.os, "lstat", lstat)
    _refused(lambda: _observe(fixture))


@pytest.mark.parametrize("fault", ["rewrite", "rotation", "sidecar", "manifest", "fleet"])
def test_recheck_after_provider_work_refuses_changed_source_or_pins(fixture, fault):
    snapshot = _observe(fixture)
    binding = reader.bind_prefix(snapshot, expected_tasks={"work-one"})
    if fault == "rewrite":
        fixture["source"].write_bytes(fixture["payload"].replace(b"alpha", b"omega"))
    elif fault == "rotation":
        replacement = fixture["source"].with_name("replacement.jsonl")
        replacement.write_bytes(fixture["payload"])
        os.replace(replacement, fixture["source"])
    elif fault == "sidecar":
        fixture["sidecar"].write_bytes(b'{"generation":"synthetic"}')
    elif fault == "manifest":
        (fixture["bundle"] / "deployment-manifest.json").write_bytes(b'{}')
    else:
        (fixture["bundle"] / "wd-fleet.json").write_bytes(b'{}')
    _refused(lambda: _verify(fixture, snapshot, binding))


@pytest.mark.parametrize("fault", ["row", "ref", "tasks", "repin", "timestamp"])
def test_callback_cannot_change_snapshot_or_coherently_repin(fixture, fault):
    snapshot = _observe(fixture)
    binding = reader.bind_prefix(snapshot, expected_tasks={"work-one"})
    tasks = {"work-one"}
    if fault == "row":
        next(iter(snapshot.rows.values()))["message"] = "synthetic changed"
    elif fault == "ref":
        row = snapshot.rows.pop(next(iter(snapshot.rows)))
        snapshot.rows["a" * 64] = row
    elif fault == "tasks":
        tasks = {"work-two"}
    elif fault == "repin":
        data = fixture["payload"].replace(b"alpha", b"omega")
        fixture["source"].write_bytes(data)
        snapshot.metadata["prefix_sha256"] = hashlib.sha256(data).hexdigest()
        row = dict(fixture["rows"][0], message="synthetic omega")
        snapshot.rows.clear()
        snapshot.rows[event_id(row)] = row
    else:
        snapshot.metadata["observed_utc"] = "2099-01-01T00:00:00Z"
    _refused(lambda: _verify(fixture, snapshot, binding, tasks))


def test_rederived_source_rows_must_match_even_if_result_was_altered_before_binding(fixture):
    snapshot = _observe(fixture)
    row = dict(fixture["rows"][0], message="synthetic invented")
    snapshot.rows.clear()
    snapshot.rows[event_id(row)] = row
    binding = reader.bind_prefix(snapshot, expected_tasks={"work-one"})
    _refused(lambda: _verify(fixture, snapshot, binding))


@pytest.mark.parametrize("fault", ["row", "metadata", "tasks"])
def test_snapshot_or_tasks_mutated_during_verification_are_refused(fixture, monkeypatch, fault):
    snapshot = _observe(fixture)
    tasks = {"work-one"}
    binding = reader.bind_prefix(snapshot, expected_tasks=tasks)
    original = reader._scan
    def mutate_after_scan(*args):
        result = original(*args)
        if fault == "row":
            next(iter(snapshot.rows.values()))["message"] = "synthetic changed"
        elif fault == "metadata":
            snapshot.metadata["observed_utc"] = "2099-01-01T00:00:00Z"
        else:
            tasks.add("work-two")
        return result
    monkeypatch.setattr(reader, "_scan", mutate_after_scan)
    _refused(lambda: _verify(fixture, snapshot, binding, tasks))


@pytest.mark.parametrize(("name", "limit"), [
    ("MAX_PREFIX_BYTES", 10), ("MAX_ROWS", 1), ("MAX_ROW_BYTES", 10),
    ("MAX_SELECTED_ROW_BYTES", 10), ("MAX_SELECTED_ROWS", 0),
])
def test_source_budgets_are_enforced(fixture, monkeypatch, name, limit):
    monkeypatch.setattr(reader, name, limit)
    _refused(lambda: _observe(fixture))
