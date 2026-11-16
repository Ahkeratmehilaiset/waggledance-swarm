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
    assert all(first[key] == 'UNKNOWN' for key in ('freshness', 'publisher_authentication',
        'provider_authentication', 'source_filter_attestation'))
    assert first['results'] and all(x['source_reference']['verified'] == 'MATCH_SELECTED_ROW' for x in first['results'])
    assert len(codec.decoded) == decodes


def test_retained_verified_record_carries_event_time_bound_to_source_ref(tmp_path):
    """The delivery layer must separately display time and age (E5)."""
    retained, owner, _, _, snapshot, _, raw, _ = _make(tmp_path)
    result = owner.query(raw)
    assert result['results']
    source_rows = [json.loads(line) for line in Path(snapshot['messages']['source_path']).read_bytes().splitlines()]
    for hit in result['results']:
        records = [record for key, record in owner._state.records.items()
                   if fixtures._sha(key.encode()) == hit['doc_id_sha256']]
        assert len(records) == 1
        record = records[0]
        assert record['provenance']['source_event_ref'] == hit['source_reference']['sha256']
        rows = [row for row in source_rows if fixtures._sha(reader._canonical(row)) == hit['source_reference']['sha256']]
        assert len(rows) == 1
        assert reader._utc(record['time_utc']) == reader._utc(rows[0]['ts_utc']).replace(second=0, microsecond=0)


def test_retained_snapshot_input_containers_are_copied_at_creation(tmp_path):
    _, owner, _, _, snapshot, _, raw, _ = _make(tmp_path)
    baseline = owner.query(raw)
    snapshot['messages'].clear()
    snapshot.clear()
    assert owner.query(raw) == baseline


def test_retained_valid_query_with_no_visible_results_abstains_without_closing(tmp_path):
    _, owner, _, _, _, _, raw, _ = _make(tmp_path)
    baseline = owner.query(raw)
    early = fixtures._raw({'situation': 'retry network outage', 'time': '2000-01-01T00:00:00Z'})
    result = owner.query(early)
    assert result['status'] == 'ABSTAIN' and result['results'] == []
    assert owner.query(raw) == baseline


@pytest.mark.parametrize('fault', ['rewrite', 'truncate', 'replace', 'sidecar', 'manifest'])
def test_retained_source_or_control_deviation_refuses_and_drops_state(tmp_path, fault):
    retained, owner, _, _, snapshot, bundle, raw, _ = _make(tmp_path, generationless=True)
    path = Path(snapshot['messages']['source_path'])
    original = path.read_bytes()
    sidecar = Path(snapshot['messages']['generation_path'])
    manifest = bundle / 'deployment-manifest.json'
    manifest_original = manifest.read_bytes()
    if fault == 'rewrite': path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    elif fault == 'truncate': path.write_bytes(original[:-1])
    elif fault == 'replace':
        replacement = path.with_name('synthetic-replacement.jsonl')
        replacement.write_bytes(original)
        replacement.replace(path)
    elif fault == 'sidecar': sidecar.write_bytes(b'{}')
    elif fault == 'manifest': manifest.write_bytes(manifest_original + b' ')
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    if fault in ('rewrite', 'truncate'): path.write_bytes(original)
    elif fault == 'sidecar': sidecar.unlink()
    elif fault == 'manifest': manifest.write_bytes(manifest_original)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_accepts_normal_appends_between_calls(tmp_path):
    _, owner, _, _, snapshot, _, raw, _ = _make(tmp_path)
    baseline = owner.query(raw)
    with Path(snapshot['messages']['source_path']).open('ab') as stream:
        stream.write(b'{"synthetic":"appended-after-offset"}\n')
    assert owner.query(raw) == baseline


def test_retained_accepts_normal_append_during_query(tmp_path):
    _, owner, case, _, snapshot, _, raw, _ = _make(tmp_path)
    baseline = owner.query(raw)
    path = Path(snapshot['messages']['source_path'])
    calls = []
    def append_once(*_):
        if not calls:
            calls.append(True)
            with path.open('ab') as stream:
                stream.write(b'{"synthetic":"appended-during-query"}\n')
    case['encoder_provider'].on_query = append_once
    assert owner.query(raw) == baseline
    assert calls == [True]
    case['encoder_provider'].on_query = None
    assert owner.query(raw) == baseline


def test_retained_accepts_normal_append_during_creation(tmp_path, monkeypatch):
    from tools import wd_mind_retained_query as retained
    real_check = retained._source_check
    calls = []
    def append_after_initial_check(context, *args, **kwargs):
        result = real_check(context, *args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            with Path(context['messages']['source_path']).open('ab') as stream:
                stream.write(b'{"synthetic":"appended-during-creation"}\n')
        return result
    monkeypatch.setattr(retained, '_source_check', append_after_initial_check)
    _, owner, _, _, _, _, raw, _ = _make(tmp_path)
    assert owner.query(raw)['status'] in ('ANSWER', 'SEMANTIC_CANDIDATE', 'ABSTAIN')


@pytest.mark.parametrize('when', ['between', 'during'])
def test_change_and_restore_is_explicitly_unknown(tmp_path, when):
    import os
    _, owner, case, _, snapshot, _, raw, _ = _make(tmp_path)
    baseline = owner.query(raw)
    path = Path(snapshot['messages']['source_path'])
    original, stat = path.read_bytes(), path.stat()
    changed = bytes([original[0] ^ 1]) + original[1:]
    def rewrite_and_restore(*_):
        path.write_bytes(changed)
        path.write_bytes(original)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    if when == 'between': rewrite_and_restore()
    else: case['encoder_provider'].on_query = rewrite_and_restore
    result = owner.query(raw)
    assert result == baseline and result['same_user_aba'] == 'UNKNOWN'


@pytest.mark.parametrize('bad', [
    b'',
    None,
    'not-bytes',
    b'x' * 65537,
    b'{"situation":"", "time":"2026-01-01T00:00:00Z"}',
    b'{"situation":"   ", "time":"2026-01-01T00:00:00Z"}',
    b'{"situation":"' + b'x' * 4001 + b'", "time":"2026-01-01T00:00:00Z"}',
    b'{"time":"2026-01-01T00:00:00Z"}',
    b'{"situation":"a", "time":"2026-01-01T00:00:00Z", "extra":1}',
    b'{"situation":"a", "situation":"b", "time":"2026-01-01T00:00:00Z"}',
    b'{"situation":"a", "time":NaN}',
    b'{"situation":"a", "time":"bad"}',
    b'\xff',
], ids=['empty', 'none', 'text', 'oversized-bytes', 'empty-situation', 'blank-situation',
        'oversized-situation', 'missing-key', 'extra-key', 'duplicate-key', 'nan', 'bad-time', 'bad-utf8'])
def test_invalid_caller_query_does_not_close_shared_owner(tmp_path, bad):
    _, owner, _, _, _, _, raw, _ = _make(tmp_path)
    baseline = owner.query(raw)
    with pytest.raises(reader.ContentQueryRefused):
        owner.query(bad)
    assert owner.query(raw) == baseline


def test_retained_rechecks_source_after_encoder_and_drops_state(tmp_path):
    retained, owner, case, _, snapshot, _, raw, _ = _make(tmp_path)
    path = Path(snapshot['messages']['source_path'])
    original = path.read_bytes()
    case['encoder_provider'].on_query = lambda *_: path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    path.write_bytes(original)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_rejects_other_process_without_closing_owner(tmp_path, monkeypatch):
    retained, owner, _, _, _, _, raw, _ = _make(tmp_path)
    monkeypatch.setattr(retained, '_pid', lambda: -1)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    monkeypatch.undo()
    assert owner.query(raw)['status'] in ('ANSWER', 'SEMANTIC_CANDIDATE', 'ABSTAIN')


def test_retained_disabled_and_closed_refuse(tmp_path):
    retained, owner, _, _, _, _, raw, _ = _make(tmp_path)
    with pytest.raises(reader.ContentQueryRefused):
        retained.create_retained_query_owner(enabled=False)
    owner.close()
    owner.close()
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


@pytest.mark.parametrize('extra', [{'stream': None}, {'stream': b'synthetic'}])
def test_retained_creation_rejects_stream_input(extra):
    from tools import wd_mind_retained_query as retained
    with pytest.raises(reader.ContentQueryRefused):
        retained.create_retained_query_owner(enabled=True, **extra)


@pytest.mark.parametrize('alteration', ['scope', 'count', 'cutoff'])
def test_retained_creation_rejects_incompatible_scope_and_memory_controls(tmp_path, monkeypatch, alteration):
    real_controls = reader._q4_controls
    def changed_controls(values):
        descriptor, memory, encoder, index = real_controls(values)
        descriptor, memory = dict(descriptor), dict(memory)
        if alteration == 'scope': descriptor['scope'] = 'synthetic-non-all-task-scope'
        elif alteration == 'count': memory['records'] += 1
        else: memory['cutoff_utc'] = '2000-01-01T00:00:00Z'
        return descriptor, memory, encoder, index
    monkeypatch.setattr(reader, '_q4_controls', changed_controls)
    with pytest.raises(reader.ContentQueryRefused): _make(tmp_path)


def test_retained_refusal_has_only_fixed_public_token_and_no_cause(tmp_path):
    _, owner, _, _, _, _, _, _ = _make(tmp_path)
    with pytest.raises(reader.ContentQueryRefused) as caught:
        owner.query(b'{"synthetic-private":"secret-looking-input"}')
    assert caught.value.args == ('content_query_refused',)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_retained_checks_prefix_at_start_and_end_for_each_query(tmp_path, monkeypatch):
    retained, owner, _, _, _, _, raw, _ = _make(tmp_path)
    observed = []
    original = retained._source_check
    def checked(context, *args, **kwargs):
        observed.append(True)
        return original(context, *args, **kwargs)
    monkeypatch.setattr(retained, '_source_check', checked)
    owner.query(raw)
    owner.query(raw)
    assert len(observed) == 4


def test_retained_cache_is_immutable_and_reader_pin_unchanged(tmp_path):
    import dataclasses
    retained, owner, _, _, snapshot, _, _, _ = _make(tmp_path)
    decision = json.loads(snapshot['decision_bytes'])
    assert {key: decision[key] for key in reader.module_sha256s()} == reader.module_sha256s()
    with pytest.raises(dataclasses.FrozenInstanceError): owner._state.cell_names = ()
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


def test_retained_narrow_manifest_tamper_closes_even_when_restored(tmp_path):
    _, owner, _, _, _, bundle, raw, _ = _make(tmp_path)
    path = bundle / 'wd-mind-narrow-source-manifest.json'
    original = path.read_bytes()
    path.write_bytes(original + b' ')
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    path.write_bytes(original)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_source_path_replaced_by_directory_closes(tmp_path):
    _, owner, _, _, snapshot, _, raw, _ = _make(tmp_path)
    path = Path(snapshot['messages']['source_path'])
    moved = path.with_name('synthetic-original.jsonl')
    path.rename(moved)
    path.mkdir()
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    path.rmdir()
    moved.rename(path)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_source_file_symlink_closes_even_if_original_returns(tmp_path):
    import os
    _, owner, _, _, snapshot, _, raw, _ = _make(tmp_path)
    path = Path(snapshot['messages']['source_path'])
    moved = path.with_name('synthetic-before-link.jsonl')
    path.rename(moved)
    try:
        try:
            os.symlink(moved, path, target_is_directory=False)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                pytest.skip('Windows account cannot create a symbolic link')
            raise
        with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    finally:
        if path.is_symlink(): path.unlink()
        moved.rename(path)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_source_reparse_attribute_closes_even_if_original_returns(tmp_path, monkeypatch):
    import os
    _, owner, _, _, snapshot, _, raw, _ = _make(tmp_path)
    source = Path(snapshot['messages']['source_path'])
    real_lstat = os.lstat
    class ReparseStat:
        st_file_attributes = 0x400
        def __init__(self, stat): self.stat = stat
        def __getattr__(self, name): return getattr(self.stat, name)
    def synthetic_reparse(path, *args, **kwargs):
        value = real_lstat(path, *args, **kwargs)
        return ReparseStat(value) if Path(path) == source else value
    with monkeypatch.context() as scoped:
        scoped.setattr(os, 'lstat', synthetic_reparse)
        with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_generation_change_closes_even_if_original_returns(tmp_path):
    _, owner, _, _, snapshot, _, raw, _ = _make(tmp_path)
    path = Path(snapshot['messages']['generation_path'])
    original = path.read_bytes()
    path.write_bytes(b'{"generation":"synthetic-other-generation"}')
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    path.write_bytes(original)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_provider_identity_deviation_closes(tmp_path):
    _, owner, _, _, _, _, raw, _ = _make(tmp_path)
    original = owner._index_provider
    class ChangedProvider:
        @property
        def identity(self):
            value = dict(original.identity)
            value['code_sha256'] = '0' * 64
            return value
        def load(self, data): return original.load(data)
    owner._index_provider = ChangedProvider()
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    owner._index_provider = original
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_changed_pinned_module_bytes_close(tmp_path, monkeypatch):
    _, owner, _, _, _, _, raw, _ = _make(tmp_path)
    real_read = reader._bounded_source_bytes
    def altered_reader(path, *args, **kwargs):
        data = real_read(path, *args, **kwargs)
        return data + b'# synthetic changed pinned reader' if Path(path) == Path(reader.__file__) else data
    with monkeypatch.context() as scoped:
        scoped.setattr(reader, '_bounded_source_bytes', altered_reader)
        with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_provider_error_closes_owner(tmp_path):
    _, owner, case, _, _, _, raw, _ = _make(tmp_path)
    def fail(*_): raise RuntimeError('synthetic-provider-error')
    case['encoder_provider'].on_query = fail
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    case['encoder_provider'].on_query = None
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


@pytest.mark.parametrize('kind', ['keyboard', 'cancelled'])
def test_retained_provider_interruption_propagates_without_details_and_closes(tmp_path, kind):
    import asyncio
    _, owner, case, _, _, _, raw, _ = _make(tmp_path)
    exception_type = KeyboardInterrupt if kind == 'keyboard' else asyncio.CancelledError
    def interrupt(*_): raise exception_type('synthetic-provider-private-detail')
    case['encoder_provider'].on_query = interrupt
    with pytest.raises(exception_type) as caught:
        owner.query(raw)
    assert caught.value.args == ()
    assert caught.value.__cause__ is None
    assert 'synthetic-provider-private-detail' not in str(caught.value)
    case['encoder_provider'].on_query = None
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


@pytest.mark.parametrize('kind', ['shape', 'nonfinite'])
def test_retained_bad_provider_vector_closes_owner(tmp_path, kind):
    _, owner, _, _, _, _, raw, _ = _make(tmp_path)
    original = owner._encoder
    class BadVector:
        @property
        def identity(self): return original.identity
        def query(self, text): return (float('nan'),) if kind == 'nonfinite' else ()
    owner._encoder = BadVector()
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    owner._encoder = original
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_recursive_query_and_close_are_refused_without_dropping_outer_call(tmp_path):
    _, owner, case, _, _, _, raw, _ = _make(tmp_path)
    baseline = owner.query(raw)
    outcomes = []
    def nested(*_):
        with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
        with pytest.raises(reader.ContentQueryRefused): owner.close()
        outcomes.append(True)
    case['encoder_provider'].on_query = nested
    assert owner.query(raw) == baseline
    assert outcomes
    case['encoder_provider'].on_query = None
    assert owner.query(raw) == baseline


def test_retained_hashes_entire_prefix_and_rejects_late_unselected_corruption(tmp_path, monkeypatch):
    retained, owner, _, _, snapshot, _, raw, _ = _make(tmp_path)
    source = Path(snapshot['messages']['source_path'])
    original = source.read_bytes()
    checks = []
    real = retained._source_check
    def corrupt_at_end(context, *args, **kwargs):
        checks.append(True)
        if len(checks) == 2:
            changed = original.replace(b'"message"', b'"messagE"', 1)
            assert changed != original and len(changed) == len(original)
            source.write_bytes(changed)
        return real(context, *args, **kwargs)
    monkeypatch.setattr(retained, '_source_check', corrupt_at_end)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    assert len(checks) == 2 and owner._state is None


def test_final_streaming_hash_detects_change_in_not_yet_read_block(tmp_path, monkeypatch):
    """A6: flip a later byte after the hash has consumed its first block."""
    retained, owner, _, _, snapshot, _, raw, _ = _make(tmp_path)
    source = Path(snapshot['messages']['source_path'])
    original = source.read_bytes()
    assert len(original) > 128
    source_offset = len(original) - 3
    bridge, absent = reader._narrow_dependencies()
    real_open, real_check = bridge._open_log, retained._source_check
    calls, mutated = [], []
    def checked(context, *args, **kwargs):
        calls.append(True)
        if len(calls) != 2:
            return real_check(context, *args, **kwargs)
        class StreamProxy:
            def __init__(self, stream): self.stream = stream
            def __getattr__(self, name): return getattr(self.stream, name)
            def __enter__(self): self.stream.__enter__(); return self
            def __exit__(self, *exc): return self.stream.__exit__(*exc)
            def read(self, size=-1):
                block = self.stream.read(min(size, 64) if size >= 0 else size)
                if not mutated and self.stream.tell() < source_offset:
                    with source.open('r+b') as writer:
                        writer.seek(source_offset)
                        writer.write(bytes([original[source_offset] ^ 1]))
                    mutated.append(True)
                return block
        def wrapped_open(*open_args, **open_kwargs):
            return StreamProxy(real_open(*open_args, **open_kwargs))
        with monkeypatch.context() as scoped:
            scoped.setattr(retained.r, '_narrow_dependencies', lambda: (bridge, absent))
            scoped.setattr(bridge, '_open_log', wrapped_open)
            return real_check(context, *args, **kwargs)
    monkeypatch.setattr(retained, '_source_check', checked)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    assert mutated == [True]
    source.write_bytes(original)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


@pytest.mark.parametrize('restore_mtime', [False, True])
def test_final_hash_rewrite_disclosed_and_detected_next_call(tmp_path, monkeypatch, restore_mtime):
    """A7: mutation after the hash read, before its comparison, never uses real logs."""
    import os
    retained, owner, _, _, snapshot, _, raw, _ = _make(tmp_path)
    source = Path(snapshot['messages']['source_path'])
    original = source.read_bytes()
    changed = original.replace(b'"message"', b'"messagE"', 1)
    assert original != changed and len(original) == len(changed)
    before = source.stat()
    real_source_check = retained._source_check
    real_compare = retained.hmac.compare_digest
    calls, touched = [], []

    def check_with_rewrite(context, *args, **kwargs):
        calls.append(True)
        if len(calls) != 2:
            return real_source_check(context, *args, **kwargs)
        prefix = context['decision']['source_prefix']['sha256']
        def comparison(left, right):
            matched = real_compare(left, right)
            if not touched and left == prefix and right == prefix:
                source.write_bytes(changed)
                os.utime(source, ns=(before.st_atime_ns,
                    before.st_mtime_ns if restore_mtime else before.st_mtime_ns + 2_000_000_000))
                touched.append(True)
            return matched
        with monkeypatch.context() as scoped:
            scoped.setattr(retained.hmac, 'compare_digest', comparison)
            return real_source_check(context, *args, **kwargs)

    monkeypatch.setattr(retained, '_source_check', check_with_rewrite)
    result = owner.query(raw)
    assert result['same_user_aba'] == 'UNKNOWN'
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    source.write_bytes(original)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    assert touched == [True]


def test_concurrent_caller_refused_without_closing_running_owner(tmp_path):
    import threading
    _, owner, case, _, _, _, raw, _ = _make(tmp_path)
    entered, release = threading.Event(), threading.Event()
    first_results, first_errors = [], []
    def wait_once(*_):
        entered.set()
        assert release.wait(15)
    case['encoder_provider'].on_query = wait_once
    def run_first():
        try:
            first_results.append(owner.query(raw))
        except BaseException as error:
            first_errors.append(error)
    worker = threading.Thread(target=run_first)
    worker.start()
    try:
        assert entered.wait(15)
        with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    finally:
        release.set()
        worker.join(timeout=15)
    assert not worker.is_alive() and not first_errors and len(first_results) == 1
    case['encoder_provider'].on_query = None
    assert owner.query(raw) == first_results[0]


def test_retained_hundred_calls_do_not_accumulate_unbounded_process_memory(tmp_path):
    """D6: synthetic process RSS guard; production peak is measured separately."""
    import ctypes
    import gc
    import os
    import sys
    if sys.platform == 'win32':
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        kernel32, psapi = ctypes.WinDLL('kernel32', use_last_error=True), ctypes.WinDLL('psapi', use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        def process_memory():
            info = Counters()
            info.cb = ctypes.sizeof(info)
            if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(info), info.cb):
                raise ctypes.WinError(ctypes.get_last_error())
            return info.WorkingSetSize, info.PeakWorkingSetSize
    elif sys.platform.startswith('linux'):
        import resource
        def process_memory():
            current_pages = int(Path('/proc/self/statm').read_text().split()[1])
            return current_pages * os.sysconf('SC_PAGE_SIZE'), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    else:
        pytest.skip('process working-set observation unavailable on this platform')
    _, owner, _, _, _, _, raw, _ = _make(tmp_path)
    baseline = owner.query(raw)
    gc.collect()
    initial, initial_peak = process_memory()
    for _ in range(100):
        assert owner.query(raw) == baseline
    gc.collect()
    final, final_peak = process_memory()
    assert final_peak >= initial_peak
    assert final - initial < 64 * 1024 * 1024


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
