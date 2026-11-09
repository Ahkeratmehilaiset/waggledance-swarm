"""Synthetic host contract; never opens a real bridge or starts a service."""
import copy
import importlib
import json
from pathlib import Path
import threading

import pytest

from tools.bridge_compact_view import event_id
from tests.tools.test_bridge_situation_picture import (
    LEAD, TOOLS, RCO, ROSTER, row, write, stamp, request,
)


def make(path, **kwargs):
    module = importlib.import_module('tools.bridge_situation_host')
    return module.SituationHost(events_path=path, roster=kwargs.pop('roster', ROSTER), **kwargs)


def refused():
    return row(agent=RCO, type='finding', status='changes_requested',
        payload={'result': {'status': 'changes_requested', 'head': 'e' * 40}})


def unknown(report, reason=None):
    assert report['schema'] == 'wd.bridge-situation-host.v1'
    assert report['authority'] == 'none' and report['coverage'] == 'UNKNOWN'
    if reason is not None:
        assert report['reason'] == reason
    if report['snapshot'] is not None:
        assert report['snapshot']['coverage'] == 'UNKNOWN'


@pytest.mark.parametrize('key,value', [
    ('max_pages_per_tick', True), ('max_pages_per_tick', 0),
    ('max_pages_per_tick', 129), ('max_pages_per_tick', 1.0),
    ('max_rows', True), ('max_rows', 0), ('max_rows', 100001), ('max_rows', 1.0),
    ('max_bytes', True), ('max_bytes', 0), ('max_bytes', 67108865), ('max_bytes', 1.0),
    ('max_events', True), ('max_events', 0), ('max_events', 200001),
    ('ttl_seconds', True), ('ttl_seconds', 0),
])
def test_invalid_budget_is_rejected_before_read(tmp_path, key, value):
    with pytest.raises(ValueError):
        make(tmp_path / 'missing.jsonl', **{key: value})
    assert list(tmp_path.iterdir()) == []


def test_paths_and_nested_roster_are_frozen_at_construction(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / 'events.jsonl'
    generation = tmp_path / 'generation.json'
    write(source, [row()])
    generation.write_text(json.dumps({'generation': 'synthetic-generation'}))
    roster = copy.deepcopy(ROSTER)
    host = make(Path('events.jsonl'), roster=roster, generation_path=Path('generation.json'))
    roster[LEAD]['session_id'] = 'synthetic-caller-mutation'
    other = tmp_path / 'other'
    other.mkdir()
    write(other / 'events.jsonl', [row(task_id='synthetic-wrong-root')])
    monkeypatch.chdir(other)
    report = host.tick(now=stamp(10))
    assert report['coverage'] == 'COMPLETE'
    assert report['snapshot']['lanes'][LEAD]['task']['value'] == 'synthetic-work'
    assert report['snapshot']['cursor']['generation'] == 'synthetic-generation'


def test_tick_cap_continues_same_instance_until_eof_then_stops(tmp_path, monkeypatch):
    source = tmp_path / 'events.jsonl'
    write(source, [row(seconds=i) for i in range(3)])
    host = make(source, max_rows=1, max_pages_per_tick=2)
    engine = host._picture
    calls = []
    original = engine.update
    def update(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(engine, 'update', update)
    first = host.tick(now=stamp(10))
    unknown(first, 'incomplete_snapshot')
    assert first['update_calls'] == 2 and len(calls) == 2
    second = host.tick(now=stamp(11))
    assert second['coverage'] == 'COMPLETE' and second['update_calls'] == 1
    assert host._picture is engine and len(engine._events) == 3
    assert len(calls) == 3
    idle = host.tick(now=stamp(12))
    assert idle['update_calls'] == 1 and len(engine._events) == 3


def test_ignored_rows_need_raw_coverage_but_not_retained_capacity(tmp_path):
    source = tmp_path / 'events.jsonl'
    write(source, [row(session_id='synthetic-wrong') for _ in range(4)] + [row()])
    host = make(source, max_events=1, max_rows=2)
    unknown(host.tick(now=stamp(10)), 'incomplete_snapshot')
    unknown(host.tick(now=stamp(11)), 'incomplete_snapshot')
    report = host.tick(now=stamp(12))
    assert report['coverage'] == 'COMPLETE' and len(host._picture._events) == 1


def test_open_request_and_veto_survive_later_ticks_and_ttl(tmp_path):
    source = tmp_path / 'events.jsonl'
    sent, veto = request(), refused()
    write(source, [sent, veto])
    host = make(source, ttl_seconds=1, max_events=10)
    assert host.tick(now=stamp(10))['coverage'] == 'COMPLETE'
    write(source, [row(seconds=i, task_id='synthetic-later-%d' % i) for i in range(11, 15)], mode='a')
    report = host.tick(now=stamp(1000))['snapshot']
    assert report['waiting_for']['value'][0]['request_ref'] == event_id(sent)
    assert report['recognized_veto_observations']['value'][0]['source_event_ref'] == event_id(veto)
    assert report['recognized_veto_observations']['value'][0]['age_seconds'] == 1000
    assert report['merge_eligibility'] == 'NOT_EVALUATED'


def test_overflow_preserves_cursor_and_facts_until_explicit_reset(tmp_path, monkeypatch):
    source = tmp_path / 'events.jsonl'
    sent, veto = request(), refused()
    write(source, [sent, veto])
    host = make(source, max_events=2)
    first = host.tick(now=stamp(10))['snapshot']
    write(source, [row(seconds=11)], mode='a')
    overflow = host.tick(now=stamp(20))
    unknown(overflow, 'state_budget_exceeded')
    assert overflow['snapshot']['cursor'] == first['cursor']
    assert [record[1] for record in host._picture._events] == [event_id(sent), event_id(veto)]
    monkeypatch.setattr(host._picture, 'update', lambda *a, **k: pytest.fail('No update before explicit reset'))
    blocked = host.tick(now=stamp(21))
    unknown(blocked, 'reset_required')
    assert blocked['update_calls'] == 0 and blocked['snapshot'] is None


def test_partial_tail_resumes_without_reset_and_without_writing_files(tmp_path):
    source = tmp_path / 'events.jsonl'
    write(source, [request()])
    host = make(source)
    first = host.tick(now=stamp(10))
    with source.open('ab') as stream:
        stream.write(json.dumps(refused()).encode())
    before = source.read_bytes()
    partial = host.tick(now=stamp(20))
    unknown(partial, 'incomplete_snapshot')
    assert partial['snapshot']['cursor'] == first['snapshot']['cursor']
    assert source.read_bytes() == before and list(tmp_path.iterdir()) == [source]
    with source.open('ab') as stream:
        stream.write(b'\n')
    assert host.tick(now=stamp(21))['coverage'] == 'COMPLETE'


@pytest.mark.parametrize('operation', ['rotate', 'truncate', 'rewrite'])
def test_history_changes_require_explicit_reset(tmp_path, operation):
    source = tmp_path / 'events.jsonl'
    write(source, [request(), refused()])
    host = make(source)
    assert host.tick(now=stamp(10))['coverage'] == 'COMPLETE'
    if operation == 'rotate':
        source.rename(tmp_path / 'old.jsonl')
        write(source, [row(seconds=11)])
    elif operation == 'truncate':
        source.write_bytes(b'')
    else:
        source.write_bytes(source.read_bytes().replace(b'synthetic-work', b'synthetic-fake'))
    unknown(host.tick(now=stamp(20)))
    blocked = host.tick(now=stamp(21))
    unknown(blocked, 'reset_required')
    assert blocked['snapshot'] is None
    unknown(host.reset(), 'reset_not_read')
    assert host.tick(now=stamp(22))['coverage'] == 'COMPLETE'


def test_returned_nested_report_is_defensive_copy(tmp_path, monkeypatch):
    source = tmp_path / 'events.jsonl'
    write(source, [row()])
    host = make(source)
    original = host._picture.update(source, now=stamp(10))
    original['synthetic_extension'] = {'nested': [{'value': 'original'}]}
    monkeypatch.setattr(host._picture, 'update', lambda *a, **k: original)
    first = host.tick(now=stamp(10))
    first['snapshot']['synthetic_extension']['nested'][0]['value'] = 'caller mutation'
    first['snapshot']['lanes'][LEAD]['task']['value'] = 'caller mutation'
    second = host.tick(now=stamp(11))
    assert second['snapshot']['synthetic_extension']['nested'][0]['value'] == 'original'
    assert second['snapshot']['lanes'][LEAD]['task']['value'] == 'synthetic-work'


@pytest.mark.parametrize('bad_now', ['synthetic-sensitive-clock', None, stamp(9)])
def test_invalid_or_backward_clock_cannot_return_old_complete(tmp_path, bad_now):
    source = tmp_path / 'events.jsonl'
    write(source, [row()])
    host = make(source)
    assert host.tick(now=stamp(10))['coverage'] == 'COMPLETE'
    failed = host.tick(now=bad_now)
    unknown(failed, 'host_update_failed')
    assert failed['snapshot'] is None and 'synthetic-sensitive-clock' not in json.dumps(failed)
    unknown(host.tick(now=stamp(11)), 'reset_required')
    unknown(host.reset(), 'reset_not_read')
    assert host.tick(now=stamp(12))['coverage'] == 'COMPLETE'


@pytest.mark.parametrize('variant', ['update', 'copy', 'malformed'])
def test_update_copy_and_shape_errors_are_generic_and_sticky(tmp_path, monkeypatch, variant):
    source = tmp_path / 'events.jsonl'
    write(source, [row()])
    host = make(source)
    report = host.tick(now=stamp(10))['snapshot']
    calls = []
    class BadCopy:
        def __deepcopy__(self, memo):
            raise RuntimeError('synthetic-sensitive-detail')
    def update(*args, **kwargs):
        calls.append(1)
        if variant == 'update':
            raise OSError('synthetic-sensitive-detail')
        if variant == 'malformed':
            return {'coverage': 'COMPLETE'}
        report['synthetic_extension'] = BadCopy()
        return report
    monkeypatch.setattr(host._picture, 'update', update)
    failed = host.tick(now=stamp(11))
    unknown(failed, 'host_update_failed')
    assert failed['snapshot'] is None and 'synthetic-sensitive-detail' not in json.dumps(failed)
    unknown(host.tick(now=stamp(12)), 'reset_required')
    assert calls == [1]


def test_missing_source_is_sticky_until_explicit_reset(tmp_path):
    source = tmp_path / 'missing.jsonl'
    host = make(source)
    unknown(host.tick(now=stamp(10)))
    write(source, [row()])
    unknown(host.tick(now=stamp(11)), 'reset_required')
    unknown(host.reset(), 'reset_not_read')
    assert host.tick(now=stamp(12))['coverage'] == 'COMPLETE'


def test_failed_reset_cannot_clear_error_latch_or_leak_exception(tmp_path, monkeypatch):
    source = tmp_path / 'events.jsonl'
    write(source, [row()])
    host = make(source)
    assert host.tick(now=stamp(10))['coverage'] == 'COMPLETE'
    original = host._picture.reset
    def fail():
        raise RuntimeError('synthetic-sensitive-detail')
    monkeypatch.setattr(host._picture, 'reset', fail)
    report = host.reset()
    unknown(report, 'host_reset_failed')
    assert report['snapshot'] is None and 'synthetic-sensitive-detail' not in json.dumps(report)
    unknown(host.tick(now=stamp(11)), 'reset_required')
    monkeypatch.setattr(host._picture, 'reset', original)
    unknown(host.reset(), 'reset_not_read')
    assert host.tick(now=stamp(12))['coverage'] == 'COMPLETE'


def test_tick_copy_and_reset_hold_one_serial_lock(tmp_path, monkeypatch):
    source = tmp_path / 'events.jsonl'
    write(source, [row()])
    host = make(source)
    engine = host._picture
    snapshot = engine.update(source, now=stamp(10))
    real_lock = threading.Lock()
    entered, release, waiting, reset_called = (threading.Event() for _ in range(4))
    errors, results = [], {}
    class ObservedLock:
        def __enter__(self):
            if real_lock.locked():
                waiting.set()
            real_lock.acquire()
        def __exit__(self, *args):
            real_lock.release()
    class CopyProbe:
        def __deepcopy__(self, memo):
            assert real_lock.locked()
            return 'copied-under-lock'
    snapshot['synthetic_extension'] = CopyProbe()
    def update(*args, **kwargs):
        assert real_lock.locked()
        entered.set()
        assert release.wait(3)
        return snapshot
    original_reset = engine.reset
    def reset():
        assert real_lock.locked()
        reset_called.set()
        original_reset()
    monkeypatch.setattr(host, '_lock', ObservedLock())
    monkeypatch.setattr(engine, 'update', update)
    monkeypatch.setattr(engine, 'reset', reset)
    def run(label, fn):
        try:
            results[label] = fn()
        except BaseException as error:
            errors.append(error)
    first = threading.Thread(target=run, args=('tick', lambda: host.tick(now=stamp(10))), daemon=True)
    second = threading.Thread(target=run, args=('reset', host.reset), daemon=True)
    try:
        first.start()
        assert entered.wait(3)
        second.start()
        assert waiting.wait(3) and not reset_called.is_set()
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
    assert not first.is_alive() and not second.is_alive() and not errors
    assert reset_called.is_set() and len(results) == 2
    assert results['tick']['snapshot']['synthetic_extension'] == 'copied-under-lock'


@pytest.mark.parametrize('abort_type', [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize('stage', ['update', 'copy', 'reset'])
def test_baseexception_propagates_unchanged_and_latches_every_mutable_stage(tmp_path, monkeypatch, abort_type, stage):
    source = tmp_path / 'events.jsonl'
    write(source, [row()])
    host = make(source)
    assert host.tick(now=stamp(10))['coverage'] == 'COMPLETE'
    engine = host._picture
    original_update, original_reset = engine.update, engine.reset
    abort = abort_type('synthetic-interruption')
    class InterruptedCopy:
        def __deepcopy__(self, memo):
            raise abort
    def update(*args, **kwargs):
        if stage == 'update':
            raise abort
        report = original_update(*args, **kwargs)
        report['synthetic_extension'] = InterruptedCopy()
        return report
    def reset():
        raise abort
    if stage == 'reset':
        monkeypatch.setattr(engine, 'reset', reset)
        operation = host.reset
    else:
        monkeypatch.setattr(engine, 'update', update)
        operation = lambda: host.tick(now=stamp(11))
    with pytest.raises(abort_type) as caught:
        operation()
    assert caught.value is abort
    assert host._lock.acquire(blocking=False)
    host._lock.release()
    monkeypatch.setattr(engine, 'update', original_update)
    monkeypatch.setattr(engine, 'reset', original_reset)
    blocked = host.tick(now=stamp(12))
    unknown(blocked, 'reset_required')
    assert blocked['snapshot'] is None and blocked['update_calls'] == 0
    unknown(host.reset(), 'reset_not_read')
    assert host.tick(now=stamp(13))['coverage'] == 'COMPLETE'
    assert len(engine._events) == 1


@pytest.mark.parametrize('abort_type', [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_interruption_between_staging_and_cursor_commit_cannot_duplicate_facts(tmp_path, abort_type):
    source = tmp_path / 'events.jsonl'
    sent, veto = request(), refused()
    write(source, [sent, veto])
    host = make(source)
    abort = abort_type('synthetic-after-extend-before-cursor')
    class InterruptedEvents(list):
        armed = True
        def extend(self, values):
            super().extend(values)
            if self.armed:
                self.armed = False
                raise abort
    engine = host._picture
    engine._events = InterruptedEvents()
    with pytest.raises(abort_type) as caught:
        host.tick(now=stamp(10))
    assert caught.value is abort
    assert engine._cursor is None and len(engine._events) == 2
    blocked = host.tick(now=stamp(11))
    assert len(engine._events) == 2, 'unlatched reuse duplicated partially staged events'
    unknown(blocked, 'reset_required')
    assert blocked['snapshot'] is None and blocked['update_calls'] == 0
    unknown(host.reset(), 'reset_not_read')
    report = host.tick(now=stamp(12))
    assert report['coverage'] == 'COMPLETE'
    assert [record[1] for record in engine._events] == [event_id(sent), event_id(veto)]
    assert report['snapshot']['waiting_for']['value'][0]['request_ref'] == event_id(sent)
    assert len(report['snapshot']['recognized_veto_observations']['value']) == 1
