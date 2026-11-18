"""Synthetic immutable builder/cache integration; no live inputs or model.

Actual cache codec, full reader and snapshot watcher run. Windows ACL/handle
operations and deployment binding are replaced only at explicit test seams;
this does not establish production ACL, bundle pin, or process isolation.
"""
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest
from wd_mind_owned_compat import run_owned

from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_vector_cache as cache
from tools import wd_mind_content_snapshot_package as package
import test_wd_mind_content_query_readonly as fixtures


reader = runtime.reader
VECTOR = struct.pack('<384f', 1.0, *([0.0] * 383))


def _synthetic_case(tmp_path, monkeypatch, *, existing=False):
    assert '.codex-audit' in str(tmp_path).lower()
    # Synthetic checkout seam: retain hash enforcement against this checkout's
    # exact bytes after verifying any delta is only Windows CRLF conversion.
    # This is not a test of the shipped bundle's code pins.
    root = Path(reader.__file__).resolve().parents[1]
    assert root == Path(__file__).resolve().parents[2]
    checkout_pins = {}
    for relative, expected in reader._REQUIRED_CODE.items():
        raw = (root / relative).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        normalized = hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()
        assert digest == expected or normalized == expected
        checkout_pins[relative] = digest
    monkeypatch.setattr(reader, '_REQUIRED_CODE', checkout_pins)
    source_dir = tmp_path / 'synthetic-source'
    cache_root = tmp_path / 'synthetic-cache'
    if existing:
        assert source_dir.is_dir() and cache_root.is_dir()
        template_dir = tmp_path / 'synthetic-control-template'
        template_dir.mkdir()
    else:
        source_dir.mkdir()
        cache_root.mkdir()
        template_dir = source_dir
    cache_path = cache_root / 'vectors.bin'
    removed = fixtures._row('synthetic removed build record')
    rows = [fixtures._row('synthetic allowed alpha work'),
            fixtures._row('synthetic allowed beta work'),
            fixtures._row('password=syntheticfixturevalue'),
            fixtures._row('synthetic private review', agent='claude-rco-1',
                          agent_uuid=fixtures.ROSTER['claude-rco-1']), removed]
    case = fixtures._all_tasks_case(reader, template_dir, rows)
    if existing:
        from waggledance.core import bridge_log_reader as bridge
        source = source_dir / 'synthetic-events.jsonl'
        generation = source_dir / 'synthetic-generation.json'
        with bridge._open_log(source) as stream:
            identity = bridge._file_identity(stream)
        cursor = bridge.BridgeCursor(source.stat().st_size, identity, 'synthetic-g1')
        case['messages'].update(source_path=str(source), generation_path=str(generation), end_cursor=cursor)
    policy = json.loads(case['messages']['policy_bytes'])
    policy['removed_source_refs'] = [fixtures._sha(fixtures._raw(removed))]
    case['messages']['policy_bytes'] = fixtures._raw(policy)
    case['messages']['policy_sha256'] = fixtures._sha(case['messages']['policy_bytes'])
    fixtures._edit_decision(case, message_policy_sha256=case['messages']['policy_sha256'])
    source = Path(case['messages']['source_path'])

    def snapshot():
        raw = source.read_bytes()
        old = case['messages']['end_cursor']
        cursor = type(old)(len(raw), old.file_identity, old.generation)
        case['messages']['end_cursor'] = cursor
        digest = fixtures._sha(raw)
        fixtures._edit_decision(case, source_prefix={'offset': cursor.offset,
                                'sha256': digest, 'generation': cursor.generation})
        projection = reader.rederive_complete_message_projection(**case)
        frozen = fixtures._snapshot(case, projection)
        args = {'cursor': cursor, 'expected_prefix_sha256': digest}
        controls = (b'synthetic-fleet', case['roster_bytes'], case['messages']['policy_bytes'])
        binding = runtime._PrivateBinding(frozen, tmp_path / 'synthetic-bundle', 'a'*64,
                                          controls, args, 'b'*64)
        return binding, projection

    # No real deployment manifest is read: emulate only bundle binding while
    # retaining real source re-derivation and watcher integrity checks.
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query',
                        lambda self: self._PrivateBinding__snapshot)
    monkeypatch.setattr(runtime._PrivateBinding, '_snapshot_support', lambda self: package)
    monkeypatch.setattr(runtime._PrivateBinding, '_immutable_checkpoint',
                        lambda self, frozen: reader._verified_complete(frozen))
    monkeypatch.setattr(runtime, '_cache_content_pins', lambda binding: {'synthetic-code': 'd'*64})

    def checked_path(path):
        assert Path(path) == cache_path
        return cache_path
    def checked_directory(path):
        assert Path(path) == cache_root
        return nullcontext(SimpleNamespace(native=None))
    def checked_lock(directory, path):
        assert Path(path) == cache_path
        return nullcontext()
    def synthetic_fd(native, path, disposition, access, sharing, flags):
        path = Path(path)
        assert path.parent == cache_root
        assert path.name == 'vectors.bin' or (path.name.startswith('vectors.bin.') and path.name.endswith('.tmp'))
        if disposition == 1:
            flags |= os.O_CREAT | os.O_EXCL
        return os.open(path, flags | getattr(os, 'O_BINARY', 0), 0o600)
    monkeypatch.setattr(cache, '_plain_path', checked_path)
    monkeypatch.setattr(cache, '_Directory', checked_directory)
    monkeypatch.setattr(cache, '_WriterLock', checked_lock)
    monkeypatch.setattr(cache, '_fd', synthetic_fd)
    checks = []
    adapter = runtime._PrivateVectorCache(cache, cache_path, lambda: checks.append('synthetic-pin'))
    encoder = fixtures._raw({'schema':'wd.mind-readonly-encoder-binding.v1',
        'encoder':{'model':'intfloat/multilingual-e5-small', 'revision':'synthetic-revision', 'weights_sha256':'e'*64},
        'code_sha256':'f'*64, 'query_policy':{'prefix':'query: ', 'truncate_chars':2000,
        'normalize':True, 'dtype':'float32', 'dim':384}})
    passages = []
    def encode(values):
        passages.extend(values)
        return VECTOR * len(values)
    def build(binding):
        return runtime._build_flat_memory_immutable(binding, encode, encoder, fixtures._sha(encoder),
                                                     5, None, vector_cache=adapter)
    return SimpleNamespace(case=case, source=source, cache_path=cache_path, snapshot=snapshot,
                           passages=passages, checks=checks, build=build, adapter=adapter)


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    return _synthetic_case(tmp_path, monkeypatch)


def test_cold_warm_and_append_use_only_screened_vectors(synthetic):
    cold_binding, cold_projection = synthetic.snapshot()
    cold = synthetic.build(cold_binding)
    assert cold.cache_status == 'MISS_STORED'
    assert set(synthetic.passages) == {'passage: synthetic allowed alpha work', 'passage: synthetic allowed beta work'}
    assert len(cold_projection['messages']['records']) == 2
    cache_bytes = synthetic.cache_path.read_bytes()
    assert all(text not in cache_bytes for text in
               (b'password=', b'synthetic allowed', b'synthetic private', b'synthetic removed'))
    synthetic.passages.clear()
    warm_binding, _ = synthetic.snapshot()
    warm = synthetic.build(warm_binding)
    assert warm_binding is not cold_binding and warm.cache_status == 'HIT_STORED'
    assert synthetic.passages == [] and warm.ids == cold.ids and warm.vectors == cold.vectors
    new_row = fixtures._row('synthetic allowed appended work')
    with synthetic.source.open('ab') as stream:
        stream.write(fixtures._raw(new_row) + b'\n')
    new_binding, new_projection = synthetic.snapshot()
    newer = synthetic.build(new_binding)
    assert newer.cache_status == 'HIT_STORED'
    assert synthetic.passages == ['passage: synthetic allowed appended work']
    assert len(new_projection['messages']['records']) == 3 and len(newer.ids) == 3
    assert synthetic.checks


@pytest.mark.parametrize('fault', ['missing_cache', 'changed_cache', 'changed_prefix'])
def test_invalid_cache_or_changed_prefix_reencodes_entire_screened_set(synthetic, fault):
    binding, _ = synthetic.snapshot()
    assert synthetic.build(binding).cache_status == 'MISS_STORED'
    synthetic.passages.clear()
    if fault == 'missing_cache':
        synthetic.cache_path.unlink()
    elif fault == 'changed_cache':
        raw = bytearray(synthetic.cache_path.read_bytes())
        raw[-1] ^= 1
        synthetic.cache_path.write_bytes(raw)
    else:
        raw = synthetic.source.read_bytes()
        assert b'alpha work' in raw
        synthetic.source.write_bytes(raw.replace(b'alpha work', b'gamma work', 1))
    changed_binding, projection = synthetic.snapshot()
    rebuilt = synthetic.build(changed_binding)
    assert rebuilt.cache_status == 'MISS_STORED'
    assert len(projection['messages']['records']) == 2 and len(synthetic.passages) == 2
    assert all('password' not in text and 'private review' not in text and 'removed' not in text
               for text in synthetic.passages)


_PROCESS_ACTOR = r'''
import hashlib, json, os, pathlib, sys
root, location, phase = sys.argv[1:]
root, location = pathlib.Path(root), pathlib.Path(location)
if not root.is_absolute() or not location.is_absolute() or '.codex-audit' not in str(location).lower():
    raise RuntimeError('synthetic path required')
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'tests' / 'tools'))
import pytest
import test_wd_mind_cache_builder_integration as harness
patcher = pytest.MonkeyPatch()
try:
    case = harness._synthetic_case(location, patcher, existing=(phase == 'warm'))
    before = hashlib.sha256(case.cache_path.read_bytes()).hexdigest() if case.cache_path.exists() else None
    binding, projection = case.snapshot()
    memory = case.build(binding)
    initial = {'cache_status': memory.cache_status, 'passage_count':len(case.passages),
               'ids_sha256':hashlib.sha256(harness.reader._canonical(list(memory.ids))).hexdigest(),
               'vectors_sha256':hashlib.sha256(memory.vectors).hexdigest(),
               'cache_before_sha256':before,
               'cache_after_sha256':hashlib.sha256(case.cache_path.read_bytes()).hexdigest(),
               'source_sha256':hashlib.sha256(case.source.read_bytes()).hexdigest(),
               'screened_records':len(projection['messages']['records'])}
    result = {'pid':os.getpid(), 'phase':phase, 'initial':initial}
    if phase == 'warm':
        case.passages.clear()
        row = harness.fixtures._row('synthetic allowed process append')
        with case.source.open('ab') as stream:
            stream.write(harness.fixtures._raw(row) + b'\n')
        later_binding, projection = case.snapshot()
        later = case.build(later_binding)
        result['append'] = {'cache_status':later.cache_status, 'passage_count':len(case.passages),
                            'only_new':case.passages == ['passage: synthetic allowed process append'],
                            'screened_records':len(projection['messages']['records']),
                            'source_sha256':hashlib.sha256(case.source.read_bytes()).hexdigest(),
                            'cache_sha256':hashlib.sha256(case.cache_path.read_bytes()).hexdigest()}
    print(json.dumps(result, sort_keys=True), flush=True)
finally:
    patcher.undo()
'''


def _run_synthetic_process(location, phase):
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith(('AGENT_BRIDGE_', 'WD_BRIDGE_', 'WD_REBOOT_', 'WD_MIND_')):
            del environment[name]
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    # Empty input closes the owned stdin pipe and preserves non-interactive EOF.
    # On timeout/interruption the helper retains the process and both outputs
    # until actual exit/EOF; uncertain spawn remains HOLD and is never killed.
    process = run_owned([sys.executable, '-I', '-B', '-c', _PROCESS_ACTOR,
        str(root), str(location), phase], cwd=root, env=environment, input='',
        capture_output=True, text=True, encoding='utf-8', timeout=60,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert process.returncode == 0, process.stderr
    data = json.loads(process.stdout)
    # A venv launcher can report a PID different from its Popen wrapper. EOF
    # and wrapper exit do not independently prove a descendant has exited.
    assert type(data['pid']) is int and data['pid'] > 0 and data['phase'] == phase
    return data



@pytest.mark.parametrize('deadline', [False, True], ids=['success', 'deadline_owned_drain'])
def test_synthetic_process_driver_owned_eof(monkeypatch, tmp_path, deadline):
    import wd_mind_owned_compat as owned
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', 'synthetic-must-not-propagate')
    events = []
    class Process:
        returncode = 0
        def communicate(self, input=None, timeout=None):
            events.append(('communicate', input, timeout))
            if deadline and len(events) == 2:
                raise subprocess.TimeoutExpired('synthetic', 60)
            return json.dumps({'pid':123, 'phase':'cold'}), ''
    def spawn(argv, **kwargs):
        events.append(('spawn', argv, kwargs))
        assert kwargs['stdin'] is subprocess.PIPE
        assert kwargs['stdout'] is subprocess.PIPE and kwargs['stderr'] is subprocess.PIPE
        assert kwargs['text'] is True and kwargs['encoding'] == 'utf-8'
        assert 'WD_REBOOT_EXPECTED_MANIFEST_HASH' not in kwargs['env']
        return Process()
    monkeypatch.setattr(owned.subprocess, 'Popen', spawn)
    monkeypatch.setattr(owned.time, 'sleep', lambda _seconds: None)
    if deadline:
        with pytest.raises(AssertionError, match='late output discarded'):
            _run_synthetic_process(tmp_path, 'cold')
        assert len(events) == 3 and events[2][0] == 'communicate'
    else:
        assert _run_synthetic_process(tmp_path, 'cold') == {'pid':123, 'phase':'cold'}
        assert len(events) == 2
    assert events[1][0] == 'communicate' and events[1][1] == ''


def test_sequential_processes_reuse_then_append_without_old_reencoding(tmp_path):
    location = tmp_path / 'synthetic-process-case'
    location.mkdir()
    cold = _run_synthetic_process(location, 'cold')
    warm = _run_synthetic_process(location, 'warm')
    assert cold['pid'] != warm['pid']
    first, second = cold['initial'], warm['initial']
    assert first['cache_status'] == 'MISS_STORED' and first['passage_count'] == 2
    assert first['cache_before_sha256'] is None and first['screened_records'] == 2
    assert second['cache_status'] == 'HIT_STORED' and second['passage_count'] == 0
    assert second['cache_before_sha256'] == first['cache_after_sha256'] == second['cache_after_sha256']
    assert second['source_sha256'] == first['source_sha256']
    assert second['ids_sha256'] == first['ids_sha256']
    assert second['vectors_sha256'] == first['vectors_sha256']
    appended = warm['append']
    assert appended['cache_status'] == 'HIT_STORED' and appended['passage_count'] == 1
    assert appended['only_new'] is True and appended['screened_records'] == 3
    assert appended['source_sha256'] != first['source_sha256']
    assert appended['cache_sha256'] != first['cache_after_sha256']


def test_incomplete_but_well_formed_cache_is_full_miss(synthetic):
    binding, _ = synthetic.snapshot()
    assert synthetic.build(binding).cache_status == 'MISS_STORED'
    raw = synthetic.cache_path.read_bytes()
    length = struct.unpack('<I', raw[len(cache._MAGIC):len(cache._MAGIC) + 4])[0]
    start = len(cache._MAGIC) + 4
    stored = json.loads(raw[start:start + length])['binding']
    loaded = cache.load_cache(synthetic.cache_path, stored, lambda offset, digest: True)
    cache.write_cache(synthetic.cache_path, stored, loaded.rows[:-1])
    synthetic.passages.clear()
    fresh, _ = synthetic.snapshot()
    assert synthetic.build(fresh).cache_status == 'MISS_STORED'
    assert len(synthetic.passages) == 2


@pytest.mark.parametrize('fault', ['manifest_pin', 'cache_origin', 'unexpected_read',
                                   'memory_error_read', 'unexpected_write', 'memory_error_write'])
def test_unexpected_cache_or_control_failures_do_not_become_miss(synthetic, monkeypatch, fault):
    binding, _ = synthetic.snapshot()
    if fault == 'manifest_pin':
        def fail(binding): raise ValueError('synthetic_manifest_pin')
        monkeypatch.setattr(runtime, '_cache_content_pins', fail)
    elif fault == 'cache_origin':
        def fail(): raise ValueError('synthetic_cache_origin')
        synthetic.adapter._verify = fail
    elif fault in ('unexpected_read', 'memory_error_read'):
        def fail(*args):
            raise MemoryError('synthetic_memory') if fault == 'memory_error_read' else RuntimeError('synthetic_read')
        monkeypatch.setattr(cache, 'load_cache', fail)
    else:
        def fail(*args):
            raise MemoryError('synthetic_memory') if fault == 'memory_error_write' else RuntimeError('synthetic_write')
        monkeypatch.setattr(cache, 'write_cache', fail)
    with pytest.raises(ValueError, match='snapshot_build_refused'):
        synthetic.build(binding)
    if fault not in ('unexpected_write', 'memory_error_write'):
        assert synthetic.passages == []
