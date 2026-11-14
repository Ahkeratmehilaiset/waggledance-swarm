"""Synthetic source-checkpoint equivalence; no model, index or live source."""
import importlib.util
from contextlib import contextmanager
import json
import sys
from pathlib import Path

import pytest

from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_content_prefix_readonly as prefix


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def fixtures():
    path = ROOT / "tests/tools/test_wd_mind_content_query_readonly.py"
    spec = importlib.util.spec_from_file_location("source_recheck_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def prototype():
    path = ROOT / "tools/wd_mind_content_source_recheck.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("source_recheck_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(fixtures, tmp_path, all_tasks=True):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=all_tasks)
    return {key: case[key] for key in reader._Q4_SOURCE_KEYS}


def test_validated_snapshot_rechecks_without_event_or_projection_parse(prototype, fixtures, tmp_path, monkeypatch):
    monkeypatch.setattr(prefix, "PAGE_BYTES", 512)
    snapshot = _snapshot(fixtures, tmp_path)
    assert reader.validate_content_snapshot(**snapshot)["status"] == "MATCH"
    assert prototype is not None, "source recheck prototype is not implemented"
    checkpoint = prototype.prepare_source_recheck(enabled=True, snapshot=snapshot)
    def forbidden(*args, **kwargs):
        pytest.fail("checkpoint parsed previously validated event/projection bytes")
    original_pinned = reader._pinned
    def pinned(raw, *args, **kwargs):
        if raw == snapshot["projection_bytes"]:
            forbidden()
        return original_pinned(raw, *args, **kwargs)
    monkeypatch.setattr(prefix.bridge, "_parse_rows", forbidden)
    monkeypatch.setattr(reader, "_pinned", pinned)
    assert checkpoint.check() is None
    assert checkpoint.check() is None


def _refused(prototype, operation):
    with pytest.raises(prototype.SourceRecheckRefused) as caught:
        operation()
    assert str(caught.value) == 'content_source_recheck_refused'
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def _source_trace(monkeypatch):
    """Observe actual source reads/seeks and checks; never record source text."""
    calls = []
    bridge = prefix.bridge
    original_open = bridge._open_log
    class Stream:
        def __init__(self, stream):
            self.stream = stream
        def fileno(self):
            return self.stream.fileno()
        def read(self, count):
            result = self.stream.read(count)
            calls.append(('read', count, len(result)))
            return result
        def seek(self, offset, *args):
            calls.append(('seek', offset, *args))
            return self.stream.seek(offset, *args)
    @contextmanager
    def opened(path):
        calls.append(('open', Path(path).name))
        with original_open(path) as stream:
            yield Stream(stream)
    monkeypatch.setattr(bridge, '_open_log', opened)
    for owner, name, label in ((bridge, '_file_identity', 'identity'),
                                (bridge, '_read_generation', 'generation'),
                                (prefix.os, 'fstat', 'length'),
                                (bridge, '_complete_prefix_length', 'boundary'),
                                (prefix, '_hash_prefix', 'hash')):
        original = getattr(owner, name)
        def wrapped(*args, _original=original, _label=label, **kwargs):
            if _label == 'hash':
                calls.append(('hash-before', args[1]))
            result = _original(*args, **kwargs)
            if _label == 'hash':
                value = result.hexdigest()
            elif _label == 'length':
                value = result.st_size
            else:
                value = result
            calls.append((_label, value))
            return result
        monkeypatch.setattr(owner, name, wrapped)
    return calls


@pytest.mark.parametrize('page_bytes,max_rows', [(512, 100000), (4096, 1), (777, 2)])
def test_exact_source_io_and_check_order_matches_full_verifier(
        prototype, fixtures, tmp_path, monkeypatch, page_bytes, max_rows):
    monkeypatch.setattr(prefix, 'PAGE_BYTES', page_bytes)
    monkeypatch.setattr(prefix, 'MAX_PAGE_ROWS', max_rows)
    snapshot = _snapshot(fixtures, tmp_path)
    checkpoint = prototype.prepare_source_recheck(enabled=True, snapshot=snapshot)
    calls = _source_trace(monkeypatch)
    reader._verified(snapshot)
    expected = calls[:]
    calls.clear()
    checkpoint.check()
    assert calls == expected
    hashes = [item[1] for item in calls if item[0] == 'hash-before']
    assert len(hashes) >= 4 and hashes[0] == 0
    assert hashes[-1] == snapshot['messages']['end_cursor'].offset


@pytest.mark.parametrize('enabled', [False, None, 0, 1, 'true'])
def test_disabled_never_validates_source(prototype, monkeypatch, enabled):
    monkeypatch.setattr(reader, '_inputs', lambda *args: pytest.fail('disabled input read'))
    _refused(prototype, lambda: prototype.prepare_source_recheck(enabled=enabled, snapshot=object()))


@pytest.mark.parametrize('fault', ['v2', 'projection_pin', 'repinned_projection', 'source', 'generation'])
def test_invalid_snapshot_cannot_issue_parse_skip_evidence(prototype, fixtures, tmp_path, fault):
    snapshot = _snapshot(fixtures, tmp_path, all_tasks=fault != 'v2')
    if fault == 'projection_pin':
        snapshot['projection_sha256'] = '0' * 64
    elif fault == 'repinned_projection':
        projection = json.loads(snapshot['projection_bytes'])
        projection['messages']['records'][-1]['stored_text'] = 'synthetic forged record'
        snapshot['projection_bytes'] = fixtures._serialized(projection)
        snapshot['projection_sha256'] = reader._sha(snapshot['projection_bytes'])
    elif fault == 'source':
        path = Path(snapshot['messages']['source_path'])
        path.write_bytes(path.read_bytes().replace(b'network retry', b'changed retry', 1))
    elif fault == 'generation':
        Path(snapshot['messages']['generation_path']).write_bytes(b'{"generation":"other"}')
    _refused(prototype, lambda: prototype.prepare_source_recheck(enabled=True, snapshot=snapshot))


@pytest.mark.parametrize('fault', ['projection', 'decision', 'policy', 'roster', 'identifier',
                                  'scope', 'cursor', 'cursor_object', 'source_path'])
def test_changed_original_controls_cannot_reuse_evidence(prototype, fixtures, tmp_path, fault):
    snapshot = _snapshot(fixtures, tmp_path)
    checkpoint = prototype.prepare_source_recheck(enabled=True, snapshot=snapshot)
    if fault in ('projection', 'decision', 'policy', 'roster'):
        key = fault + '_bytes'
        snapshot[key] += b' '
        snapshot[fault + '_sha256'] = reader._sha(snapshot[key])
    elif fault == 'identifier':
        values = snapshot['identifier_policy_binding']
        snapshot['identifier_policy_binding'] = (values[0], b'changed-key' * 4, values[2])
    elif fault == 'scope':
        snapshot['approved_tasks_bytes'] = b'{}'
        snapshot['approved_tasks_sha256'] = reader._sha(b'{}')
    elif fault == 'source_path':
        snapshot['messages']['source_path'] = str(tmp_path / 'not-the-source.jsonl')
    elif fault == 'cursor_object':
        object.__setattr__(snapshot['messages']['end_cursor'], 'file_identity', 'other')
    else:
        import dataclasses
        cursor = snapshot['messages']['end_cursor']
        snapshot['messages']['end_cursor'] = dataclasses.replace(cursor, offset=cursor.offset - 1)
    _refused(prototype, checkpoint.check)


@pytest.mark.parametrize('control', ['PAGE_BYTES', 'MAX_PAGE_ROWS', 'MAX_ALL_TASK_ROWS',
                                    'MAX_ALL_TASK_ROW_BYTES', 'MAX_ALL_TASK_RETAINED_BYTES'])
def test_changed_prefix_limits_do_not_reuse_old_validation(prototype, fixtures, tmp_path, monkeypatch, control):
    snapshot = _snapshot(fixtures, tmp_path)
    checkpoint = prototype.prepare_source_recheck(enabled=True, snapshot=snapshot)
    monkeypatch.setattr(prefix, control, 1)
    _refused(prototype, checkpoint.check)


@pytest.mark.parametrize('target', ['prototype', 'reader', 'prefix', 'bridge'])
def test_changed_code_pin_refuses_before_source_read(prototype, fixtures, tmp_path, monkeypatch, target):
    snapshot = _snapshot(fixtures, tmp_path)
    checkpoint = prototype.prepare_source_recheck(enabled=True, snapshot=snapshot)
    names = {'prototype': 'wd_mind_content_source_recheck.py', 'reader': 'wd_mind_content_query_readonly.py',
             'prefix': 'wd_mind_content_prefix_readonly.py', 'bridge': 'bridge_log_reader.py'}
    original = reader._bounded_source_bytes
    def changed(path, *args, **kwargs):
        raw = original(path, *args, **kwargs)
        return raw + b'\n# synthetic mutation' if Path(path).name == names[target] else raw
    monkeypatch.setattr(reader, '_bounded_source_bytes', changed)
    monkeypatch.setattr(prefix.bridge, '_open_log', lambda *args: pytest.fail('source read after bad code pin'))
    _refused(prototype, checkpoint.check)


@pytest.mark.parametrize('fault', ['edit', 'truncate', 'replace', 'generation'])
@pytest.mark.parametrize('operation', ['full', 'checkpoint'])
def test_source_mutations_are_refused_by_both_paths(prototype, fixtures, tmp_path, fault, operation):
    snapshot = _snapshot(fixtures, tmp_path)
    checkpoint = prototype.prepare_source_recheck(enabled=True, snapshot=snapshot)
    path = Path(snapshot['messages']['source_path'])
    raw = path.read_bytes()
    if fault == 'edit':
        path.write_bytes(raw.replace(b'network retry', b'changed retry', 1))
    elif fault == 'truncate':
        path.write_bytes(raw[:-1])
    elif fault == 'replace':
        path.rename(tmp_path / 'old-source.jsonl')
        path.write_bytes(raw)
    else:
        Path(snapshot['messages']['generation_path']).write_bytes(b'{"generation":"other"}')
    if operation == 'full':
        with pytest.raises(ValueError):
            reader._verified(snapshot)
    else:
        _refused(prototype, checkpoint.check)


@pytest.mark.parametrize('operation', ['full', 'checkpoint'])
@pytest.mark.parametrize('phase', ['before_page', 'after_page', 'next_before', 'final_generation'])
def test_mutation_during_existing_hash_and_generation_phases_refuses(
        prototype, fixtures, tmp_path, monkeypatch, operation, phase):
    monkeypatch.setattr(prefix, 'PAGE_BYTES', 512)
    snapshot = _snapshot(fixtures, tmp_path)
    checkpoint = prototype.prepare_source_recheck(enabled=True, snapshot=snapshot)
    source = Path(snapshot['messages']['source_path'])
    original_hash = prefix._hash_prefix
    calls = 0
    def hashing(stream, length):
        nonlocal calls
        calls += 1
        trigger = {'before_page': 1, 'after_page': 2, 'next_before': 3}.get(phase)
        if calls == trigger:
            raw = source.read_bytes()
            source.write_bytes(raw.replace(b'network retry', b'changed retry', 1))
        return original_hash(stream, length)
    monkeypatch.setattr(prefix, '_hash_prefix', hashing)
    original_generation = prefix.bridge._read_generation
    def generation(path):
        # Final generation read follows the second source open.
        if phase == 'final_generation' and opened[0] == 2:
            Path(path).write_bytes(b'{"generation":"other"}')
        return original_generation(path)
    original_open = prefix.bridge._open_log
    opened = [0]
    def opening(path):
        opened[0] += 1
        return original_open(path)
    monkeypatch.setattr(prefix.bridge, '_open_log', opening)
    monkeypatch.setattr(prefix.bridge, '_read_generation', generation)
    if operation == 'full':
        with pytest.raises(ValueError):
            reader._verified(snapshot)
    else:
        _refused(prototype, checkpoint.check)


def test_callback_checkpoint_schedule_is_retained_without_integrating_package(prototype, fixtures, tmp_path):
    snapshot = _snapshot(fixtures, tmp_path)
    checkpoint = prototype.prepare_source_recheck(enabled=True, snapshot=snapshot)
    callbacks = []
    for phase in ('passage', 'serializer', 'decoder-before', 'decoder-after'):
        checkpoint.check()
        callbacks.append(phase)
    Path(snapshot['messages']['generation_path']).write_bytes(b'{"generation":"changed-by-callback"}')
    _refused(prototype, checkpoint.check)
    assert callbacks == ['passage', 'serializer', 'decoder-before', 'decoder-after']


def test_append_outside_prefix_is_consistently_accepted_without_writes(prototype, fixtures, tmp_path):
    snapshot = _snapshot(fixtures, tmp_path)
    checkpoint = prototype.prepare_source_recheck(enabled=True, snapshot=snapshot)
    path = Path(snapshot['messages']['source_path'])
    with path.open('ab') as handle:
        handle.write(b'{"synthetic":"outside anchored prefix"}\n')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    reader._verified(snapshot)
    assert checkpoint.check() is None
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


@pytest.mark.parametrize('crlf', [False, True])
def test_page_evidence_binds_utf8_escaped_text_and_line_boundaries(
        prototype, fixtures, tmp_path, monkeypatch, crlf):
    import dataclasses
    rows = [fixtures._row(f'network retry café ☃ "[braces]" \\ sequence {i}',
                          task_id=f'synthetic-unicode-{i}') for i in range(4)]
    case = fixtures._all_tasks_case(reader, tmp_path, rows)
    path = Path(case['messages']['source_path'])
    if crlf:
        raw = path.read_bytes().replace(b'\n', b'\r\n')
        path.write_bytes(raw)
        cursor = dataclasses.replace(case['messages']['end_cursor'], offset=len(raw))
        case['messages']['end_cursor'] = cursor
        fixtures._edit_decision(case, source_prefix=dict(sha256=reader._sha(raw), offset=len(raw),
                                                        generation=cursor.generation))
    monkeypatch.setattr(prefix, 'PAGE_BYTES', 512)
    snapshot = fixtures._snapshot(case, reader.rederive_message_projection(**case))
    checkpoint = prototype.prepare_source_recheck(enabled=True, snapshot=snapshot)
    assert checkpoint.check() is None
    raw = path.read_bytes()
    path.write_bytes(raw.replace('café'.encode(), 'cafe'.encode() + b' ', 1))
    monkeypatch.setattr(prefix.bridge, '_parse_rows', lambda *a, **k: pytest.fail('changed page parsed'))
    _refused(prototype, checkpoint.check)
