"""Synthetic retained query tests. No real operator policy or bridge content."""
import hashlib
import json
from pathlib import Path

import pytest

from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_content_snapshot_package as package
import test_wd_mind_content_query_readonly as fixtures
import test_wd_mind_content_snapshot_package as producer_tests


def _make(tmp_path, *, generationless=False):
    from tools import wd_mind_retained_query as retained
    case, codec, output, snapshot, bundle, deploy, _, run = producer_tests._narrow_fixture(
        package, fixtures, tmp_path, generationless=generationless)
    encoder, index = reader.adapt_complete_readonly_providers(
        encoder=case['encoder_provider'], index_decoder=codec.decode,
        **{key: output[key] for key in ('encoder_binding_bytes', 'encoder_binding_sha256',
                                        'index_binding_bytes', 'index_binding_sha256')})
    snapshot = dict(snapshot)
    snapshot.pop('enabled')
    owner = retained.create_retained_query_owner(enabled=True, bundle_root=str(bundle),
        expected_deployment_manifest_sha256=fixtures._sha(deploy), **snapshot, **output,
        encoder_provider=encoder, index_provider=index)
    raw = fixtures._raw({'situation': 'retry network outage', 'time': fixtures.QUERY_TIME})
    return retained, owner, case, codec, snapshot, bundle, raw, run


def test_retained_two_queries_reuse_decoded_entries_and_source_refs(tmp_path):
    retained, owner, _, codec, _, _, raw, run = _make(tmp_path)
    baseline = run()
    decodes = len(codec.decoded)
    first = owner.query(raw)
    second = owner.query(raw)
    assert first == second == baseline
    assert first['authority'] == 'none' and first['same_user_aba'] == 'UNKNOWN'
    assert first['results'] and all(x['source_reference']['verified'] == 'MATCH_SELECTED_ROW' for x in first['results'])
    assert len(codec.decoded) == decodes


@pytest.mark.parametrize('fault', ['append', 'rewrite', 'truncate', 'replace', 'sidecar', 'manifest'])
def test_retained_source_or_control_deviation_refuses_and_drops_state(tmp_path, fault):
    retained, owner, _, _, snapshot, bundle, raw, _ = _make(tmp_path, generationless=True)
    path = Path(snapshot['messages']['source_path'])
    original = path.read_bytes()
    sidecar = Path(snapshot['messages']['generation_path'])
    manifest = bundle / 'deployment-manifest.json'
    manifest_original = manifest.read_bytes()
    if fault == 'append': path.write_bytes(original + b'{}\n')
    elif fault == 'rewrite': path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    elif fault == 'truncate': path.write_bytes(original[:-1])
    elif fault == 'replace':
        replacement = path.with_name('synthetic-replacement.jsonl')
        replacement.write_bytes(original)
        replacement.replace(path)
    elif fault == 'sidecar': sidecar.write_bytes(b'{}')
    elif fault == 'manifest': manifest.write_bytes(manifest_original + b' ')
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    if fault in ('append', 'rewrite', 'truncate'): path.write_bytes(original)
    elif fault == 'sidecar': sidecar.unlink()
    elif fault == 'manifest': manifest.write_bytes(manifest_original)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_rechecks_source_after_encoder_and_drops_state(tmp_path):
    retained, owner, case, _, snapshot, _, raw, _ = _make(tmp_path)
    path = Path(snapshot['messages']['source_path'])
    original = path.read_bytes()
    case['encoder_provider'].on_query = lambda *_: path.write_bytes(original + b'{}\n')
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    path.write_bytes(original)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_rejects_other_process_and_stays_closed(tmp_path, monkeypatch):
    retained, owner, _, _, _, _, raw, _ = _make(tmp_path)
    monkeypatch.setattr(retained, '_pid', lambda: -1)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    monkeypatch.undo()
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_disabled_and_closed_refuse(tmp_path):
    retained, owner, _, _, _, _, raw, _ = _make(tmp_path)
    with pytest.raises(reader.ContentQueryRefused):
        retained.create_retained_query_owner(enabled=False)
    owner.close()
    owner.close()
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_checks_prefix_at_start_and_end_for_each_query(tmp_path, monkeypatch):
    retained, owner, _, _, _, _, raw, _ = _make(tmp_path)
    observed = []
    original = retained._source_check
    def checked(context, expected_size=None):
        observed.append(expected_size)
        return original(context, expected_size)
    monkeypatch.setattr(retained, '_source_check', checked)
    owner.query(raw)
    owner.query(raw)
    assert len(observed) == 4 and all(type(size) is int and size > 0 for size in observed)
    assert len(set(observed)) == 1


def test_retained_cache_is_immutable_and_reader_pin_unchanged(tmp_path):
    import dataclasses
    retained, owner, _, _, snapshot, _, _, _ = _make(tmp_path)
    decision = json.loads(snapshot['decision_bytes'])
    assert {key: decision[key] for key in reader.module_sha256s()} == reader.module_sha256s()
    with pytest.raises(dataclasses.FrozenInstanceError): owner._state.source_size = 0
    with pytest.raises(TypeError): owner._state.records['synthetic'] = {}
    key = next(iter(owner._state.entries))
    with pytest.raises(TypeError): owner._state.entries[key]['w'] = b'changed'


def test_retained_manifest_changed_during_query_is_refused(tmp_path):
    retained, owner, case, _, _, bundle, raw, _ = _make(tmp_path)
    manifest = bundle / 'deployment-manifest.json'
    original = manifest.read_bytes()
    case['encoder_provider'].on_query = lambda *_: manifest.write_bytes(original + b' ')
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    manifest.write_bytes(original)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_hashes_entire_prefix_and_rejects_late_unselected_corruption(tmp_path, monkeypatch):
    retained, owner, _, _, snapshot, _, raw, _ = _make(tmp_path)
    source = Path(snapshot['messages']['source_path'])
    original = source.read_bytes()
    checks = []
    real = retained._source_check
    def corrupt_at_end(context, expected_size=None):
        checks.append(True)
        if len(checks) == 2:
            changed = original.replace(b'"message"', b'"messagE"', 1)
            assert changed != original and len(changed) == len(original)
            source.write_bytes(changed)
        return real(context, expected_size)
    monkeypatch.setattr(retained, '_source_check', corrupt_at_end)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    assert len(checks) == 2 and owner._state is None


def test_watcher_close_defaults_to_600_seconds_and_explicit_override():
    class CompletedThread:
        ident = 1
        def __init__(self): self.timeouts = []
        def join(self, timeout): self.timeouts.append(timeout)
        def is_alive(self): return False
    watcher = package._SourceWatcher()
    thread = CompletedThread()
    watcher.thread = thread
    watcher.stop()
    assert thread.timeouts == [600.0]
    watcher = package._SourceWatcher()
    thread = CompletedThread()
    watcher.thread = thread
    watcher.stop(12.0)
    assert thread.timeouts == [12.0]


def test_retained_refusal_does_not_expose_synthetic_policy_locals(tmp_path):
    import traceback
    from tools import wd_mind_retained_query as retained
    case, codec, output, snapshot, bundle, deploy, _, _ = producer_tests._narrow_fixture(package, fixtures, tmp_path)
    policy, key, _ = snapshot['identifier_policy_binding']
    encoder, index = reader.adapt_complete_readonly_providers(
        encoder=case['encoder_provider'], index_decoder=codec.decode,
        **{name: output[name] for name in ('encoder_binding_bytes', 'encoder_binding_sha256',
                                           'index_binding_bytes', 'index_binding_sha256')})
    snapshot = dict(snapshot)
    snapshot.pop('enabled')
    with pytest.raises(reader.ContentQueryRefused) as caught:
        retained.create_retained_query_owner(enabled=True, bundle_root=str(bundle),
            expected_deployment_manifest_sha256='0' * 64, **snapshot, **output,
            encoder_provider=encoder, index_provider=index)
    for frame, _ in traceback.walk_tb(caught.value.__traceback__):
        if frame.f_code.co_filename == retained.__file__:
            assert repr(policy) not in repr(frame.f_locals)
            assert repr(key) not in repr(frame.f_locals)

@pytest.mark.parametrize('selected', [False, True])
def test_retained_last_provider_identity_mutation_refuses_selected_or_other_row(tmp_path, selected):
    retained, owner, _, _, snapshot, _, raw, _ = _make(tmp_path)
    original_provider = owner._index_provider
    source = Path(snapshot['messages']['source_path'])

    class IdentityMutator:
        def __init__(self):
            self.calls = 0
            self.trigger = None
            self.changed = False
            self.lines = None
        @property
        def identity(self):
            self.calls += 1
            if self.trigger is not None and self.calls == self.trigger:
                source.write_bytes(b''.join(self.lines))
                self.changed = True
            return original_provider.identity
        def load(self, data):
            return original_provider.load(data)

    provider = IdentityMutator()
    owner._index_provider = provider
    answer = owner.query(raw)
    total = provider.calls
    assert total > 0 and answer['results']
    refs = {row['source_reference']['sha256'] for row in answer['results']}
    lines = source.read_bytes().splitlines(keepends=True)
    target = next(i for i, line in enumerate(lines)
                  if (fixtures._sha(reader._canonical(json.loads(line))) in refs) is selected and b'"message"' in line)
    old = lines[target]
    lines[target] = old.replace(b'"message"', b'"messagE"', 1)
    assert old != lines[target] and len(old) == len(lines[target])
    provider.calls, provider.trigger, provider.lines = 0, total, lines
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    assert provider.changed and owner._state is None

def test_retained_init_rechecks_provider_identity_after_real_load(tmp_path):
    from tools import wd_mind_retained_query as retained
    case, codec, output, snapshot, bundle, deploy, _, _ = producer_tests._narrow_fixture(package, fixtures, tmp_path)
    encoder, index = reader.adapt_complete_readonly_providers(
        encoder=case['encoder_provider'], index_decoder=codec.decode,
        **{name: output[name] for name in ('encoder_binding_bytes', 'encoder_binding_sha256',
                                           'index_binding_bytes', 'index_binding_sha256')})
    class MutatingIndex:
        def __init__(self): self.loaded = False
        @property
        def identity(self):
            value = dict(index.identity)
            if self.loaded: value['code_sha256'] = '0' * 64
            return value
        def load(self, data):
            result = index.load(data)
            self.loaded = True
            return result
    synthetic_provider = MutatingIndex()
    snapshot = dict(snapshot)
    snapshot.pop('enabled')
    with pytest.raises(reader.ContentQueryRefused):
        retained.create_retained_query_owner(enabled=True, bundle_root=str(bundle),
            expected_deployment_manifest_sha256=fixtures._sha(deploy), **snapshot, **output,
            encoder_provider=encoder, index_provider=synthetic_provider)
    assert synthetic_provider.loaded


def test_retained_init_final_provider_callback_source_change_refuses(tmp_path):
    from tools import wd_mind_retained_query as retained
    case, codec, output, snapshot, bundle, deploy, _, _ = producer_tests._narrow_fixture(package, fixtures, tmp_path)
    encoder, index = reader.adapt_complete_readonly_providers(
        encoder=case['encoder_provider'], index_decoder=codec.decode,
        **{name: output[name] for name in ('encoder_binding_bytes', 'encoder_binding_sha256',
                                           'index_binding_bytes', 'index_binding_sha256')})
    source = Path(snapshot['messages']['source_path'])
    original = source.read_bytes()
    changed = original.replace(b'"message"', b'"messagE"', 1)
    assert changed != original and len(changed) == len(original)
    class IndexProperty:
        def __init__(self): self.calls, self.trigger, self.changed = 0, None, False
        @property
        def identity(self):
            self.calls += 1
            if self.trigger is not None and self.calls == self.trigger:
                source.write_bytes(changed)
                self.changed = True
            return index.identity
        def load(self, data): return index.load(data)
    provider = IndexProperty()
    snapshot = dict(snapshot)
    snapshot.pop('enabled')
    def build():
        return retained.create_retained_query_owner(enabled=True, bundle_root=str(bundle),
            expected_deployment_manifest_sha256=fixtures._sha(deploy), **snapshot, **output,
            encoder_provider=encoder, index_provider=provider)
    owner = build()
    total = provider.calls
    assert total > 1
    owner.close()
    provider.calls, provider.trigger = 0, total
    with pytest.raises(reader.ContentQueryRefused): build()
    assert provider.changed


@pytest.mark.parametrize('kind', ['refusal', 'keyboard', 'generator', 'cancelled', 'group', 'oversized'])
def test_retained_factory_cleans_synthetic_policy_on_all_exits(monkeypatch, kind):
    import asyncio
    import traceback
    from tools import wd_mind_retained_query as retained
    policy = b'synthetic-policy-only-local'
    key = b'synthetic-key-only-local'
    problems = {
        'refusal': ValueError('synthetic-private-detail'),
        'keyboard': KeyboardInterrupt('synthetic-private-detail'),
        'generator': GeneratorExit('synthetic-private-detail'),
        'cancelled': asyncio.CancelledError('synthetic-private-detail'),
        'group': BaseExceptionGroup('synthetic-private-detail', [ValueError('detail'), GeneratorExit('detail')]),
        'oversized': BaseExceptionGroup('synthetic-private-detail', [ValueError('detail')] * 1025),
    }
    expected = {'refusal': reader.ContentQueryRefused, 'keyboard': KeyboardInterrupt,
                'generator': GeneratorExit, 'cancelled': asyncio.CancelledError,
                'group': BaseExceptionGroup, 'oversized': retained.RetainedCancellationUnknown}[kind]
    def fail(*args, **kwargs): raise problems[kind]
    monkeypatch.setattr(reader, '_NarrowReadGate', fail)
    with pytest.raises(expected) as caught:
        retained.create_retained_query_owner(enabled=True, bundle_root=str(Path.cwd()),
            expected_deployment_manifest_sha256='a' * 64,
            identifier_policy_binding=(policy, key, 'b' * 64))
    exc = caught.value
    assert exc.__cause__ is None and exc.__context__ is None
    assert 'synthetic-private-detail' not in str(exc)
    tb = exc.__traceback__
    while tb and tb.tb_frame.f_code.co_filename != retained.__file__: tb = tb.tb_next
    assert tb is not None
    rendered = ''.join(traceback.TracebackException(type(exc), exc, tb, capture_locals=True).format(chain=True))
    assert repr(policy) not in rendered and repr(key) not in rendered
