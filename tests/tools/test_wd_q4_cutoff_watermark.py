"""Synthetic, isolated cooperative cutoff lease; no live bridge or scheduler."""
from dataclasses import asdict
import hashlib
import importlib
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os

import pytest


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def fixture(tmp_path):
    from sandbox.mind.step2 import prefix_private_state as state
    from sandbox.mind.step2 import shared_index_checkpoint as cp
    from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin
    private, shared = tmp_path / 'private', tmp_path / 'checkpoint'
    private.mkdir()
    shared.mkdir()
    prefix = BridgePrefixPin(BridgeCursor(8, 'windows-v1:12345678:0123456789abcdef', 'synthetic'), 'a' * 64)
    pointer_raw = raw({'schema': cp.PREFIX_SCHEMA, 'generation': 'synthetic', 'watermark': asdict(prefix.cursor),
                       'prefix_watermark_sha256': prefix.sha256})
    (shared / cp.POINTER).write_bytes(pointer_raw)
    pointer = sha(pointer_raw)
    state._write_state(private, pointer, prefix)
    return private, shared, prefix, pointer, pointer_raw


def route(prefix, cutoff, private, shared):
    return raw({'schema': 'wd.q4-status-prefix-route.v1', 'cutoff_utc': cutoff,
                'cursor': asdict(prefix.cursor), 'prefix_sha256': prefix.sha256,
                'private_state_root': str(private), 'checkpoint_root': str(shared),
                'source': str(shared.parent / 'synthetic-source'), 'generation_path': str(shared.parent / 'synthetic-sidecar'),
                'trusted_model_root': str(shared.parent / 'synthetic-model'), 'max_bytes': 1024, 'max_rows': 10,
                'plan_sha256': '1' * 64, 'anchor_sha256': '2' * 64, 'policy_sha256': '3' * 64,
                'roster_sha256': '4' * 64, 'public_policy_sha256': '5' * 64})


def receipt(lease, private, pointer, prefix, *, reservation=None):
    from tools import wd_q4_guard_state_binding as roots
    from sandbox.mind.step2.shared_bridge_snapshot import _token
    reservation = lease.reservation if reservation is None else reservation
    marker = raw({'schema': 'wd.q4-runner-reservation.v1', **reservation})
    marker_sha = sha(marker)
    body = raw({'schema': 'wd.q4-status-result.v1', 'marker_token_sha256': marker_sha,
                'marker_identities': [[1, 2], [3, 4]],
                'root_identities': [roots._observe(lease._shared), roots._observe(private), [], [], []],
                'route_sha256': lease.route_sha256, 'prior_pointer_sha256': pointer,
                'plan_sha256': '1' * 64, 'pointer_sha256': pointer,
                'generation': 'synthetic', 'watermark_sha256': sha(_token(prefix.cursor).encode()),
                'prefix_sha256': prefix.sha256, 'status': 'EMPTY_NO_CHANGE'})
    path = private / ('q4-status-result-' + marker_sha + '.json')
    path.write_bytes(body)
    return str(path), sha(body)


def test_disabled_context_does_not_touch_inputs():
    mod = importlib.import_module('tools.wd_q4_cutoff_watermark')
    with mod.reserve(private_root=object(), shared_root=object()) as lease:
        assert lease is None


@pytest.mark.parametrize('kind', ['file', 'directory'])
def test_adoption_pending_blocks_direct_cutoff_before_reservation(fixture, kind):
    mod = importlib.import_module('tools.wd_q4_cutoff_watermark')
    private, shared, prefix, pointer, pointer_raw = fixture
    marker = private / 'q4-continuation-adopt.pending.json'
    if kind == 'file':
        marker.write_bytes(b'synthetic pending')
    else:
        marker.mkdir()
    data = route(prefix, '2026-11-07T10:00:00Z', private, shared)
    with pytest.raises(mod.WatermarkUnknown):
        with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=data,
                         expected_route_sha256=sha(data), expected_pointer_sha256=pointer,
                         expected_previous_record_sha256=None):
            pytest.fail('cutoff admitted with unresolved adoption')
    assert marker.exists()
    assert not (private / 'q4-cutoff.pending.json').exists()
    assert (shared / 'q4-latest.json').read_bytes() == pointer_raw


def test_adoption_pending_checked_after_cutoff_owner_lock(fixture, monkeypatch):
    from contextlib import contextmanager
    mod = importlib.import_module('tools.wd_q4_cutoff_watermark')
    private, shared, prefix, pointer, _ = fixture
    data = route(prefix, '2026-11-07T10:00:00Z', private, shared)
    original = mod.reader_slot

    @contextmanager
    def admission_race(lock, **kwargs):
        with original(lock, **kwargs):
            # A cooperating adoption can publish the marker immediately before
            # the cutoff owner obtains this lock, but not while it holds it.
            (private / 'q4-continuation-adopt.pending.json').write_bytes(b'synthetic pending')
            yield

    monkeypatch.setattr(mod, 'reader_slot', admission_race)
    with pytest.raises(mod.WatermarkUnknown):
        with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=data,
                         expected_route_sha256=sha(data), expected_pointer_sha256=pointer,
                         expected_previous_record_sha256=None):
            pytest.fail('pending marker missed inside owner lock')
    assert not (private / 'q4-cutoff.pending.json').exists()


def test_orchestrator_parent_pending_does_not_block_its_cutoff(fixture):
    mod = importlib.import_module('tools.wd_q4_cutoff_watermark')
    private, shared, prefix, pointer, _ = fixture
    parent = private / 'q4-continuation-orchestrator.pending.json'
    parent.write_bytes(b'synthetic parent')
    data = route(prefix, '2026-11-07T10:00:00Z', private, shared)
    with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=data,
                     expected_route_sha256=sha(data), expected_pointer_sha256=pointer,
                     expected_previous_record_sha256=None) as lease:
        path, pin = receipt(lease, private, pointer, prefix)
        lease.complete(receipt_path=path, expected_receipt_sha256=pin)
    assert parent.read_bytes() == b'synthetic parent'
    assert not (private / 'q4-cutoff.pending.json').exists()


def test_no_change_commits_cutoff_without_changing_pointer_and_rejects_older(fixture):
    mod = importlib.import_module('tools.wd_q4_cutoff_watermark')
    private, shared, prefix, pointer, pointer_raw = fixture
    data = route(prefix, '2026-11-07T10:00:00Z', private, shared)
    with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=data,
                     expected_route_sha256=sha(data), expected_pointer_sha256=pointer,
                     expected_previous_record_sha256=None) as lease:
        original_reservation = lease.reservation
        lease.reservation['token'] = 'f' * 64
        assert lease.reservation == original_reservation
        with pytest.raises(AttributeError):
            lease.route_sha256 = 'f' * 64
        path, pin = receipt(lease, private, pointer, prefix)
        record_pin = lease.complete(receipt_path=path, expected_receipt_sha256=pin)
    assert (shared / 'q4-latest.json').read_bytes() == pointer_raw
    older = route(prefix, '2026-11-07T09:59:59Z', private, shared)
    with pytest.raises(mod.WatermarkRefused):
        with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=older,
                         expected_route_sha256=sha(older), expected_pointer_sha256=pointer,
                         expected_previous_record_sha256=record_pin):
            pytest.fail('older cutoff admitted')
    same = route(prefix, '2026-11-07T10:00:00Z', private, shared)
    with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=same,
                     expected_route_sha256=sha(same), expected_pointer_sha256=pointer,
                     expected_previous_record_sha256=record_pin) as lease:
        path, pin = receipt(lease, private, pointer, prefix)
        newer_pin = lease.complete(receipt_path=path, expected_receipt_sha256=pin)
    assert newer_pin != record_pin and (shared / 'q4-latest.json').read_bytes() == pointer_raw
    later = route(prefix, '2026-11-07T10:01:00Z', private, shared)
    with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=later,
                     expected_route_sha256=sha(later), expected_pointer_sha256=pointer,
                     expected_previous_record_sha256=newer_pin) as lease:
        path, pin = receipt(lease, private, pointer, prefix)
        latest = lease.complete(receipt_path=path, expected_receipt_sha256=pin)
    previous = route(prefix, '2026-11-07T10:00:30Z', private, shared)
    with pytest.raises(mod.WatermarkRefused):
        with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=previous,
                         expected_route_sha256=sha(previous), expected_pointer_sha256=pointer,
                         expected_previous_record_sha256=latest):
            pytest.fail('cutoff below the latest no-change floor was admitted')


def test_interrupted_lease_keeps_pending_and_never_changes_cursor(fixture):
    mod = importlib.import_module('tools.wd_q4_cutoff_watermark')
    private, shared, prefix, pointer, pointer_raw = fixture
    data = route(prefix, '2026-11-07T10:00:00Z', private, shared)
    abandoned = None
    with pytest.raises(RuntimeError):
        with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=data,
                         expected_route_sha256=sha(data), expected_pointer_sha256=pointer,
                         expected_previous_record_sha256=None) as abandoned:
            raise RuntimeError('synthetic interruption')
    assert (private / 'q4-cutoff.pending.json').is_file()
    assert (shared / 'q4-latest.json').read_bytes() == pointer_raw
    path, pin = receipt(abandoned, private, pointer, prefix)
    with pytest.raises(mod.WatermarkUnknown):
        abandoned.complete(receipt_path=path, expected_receipt_sha256=pin)
    with pytest.raises(mod.WatermarkUnknown):
        with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=data,
                         expected_route_sha256=sha(data), expected_pointer_sha256=pointer,
                         expected_previous_record_sha256=None):
            pytest.fail('abandoned attempt admitted')


def test_old_runner_token_cannot_finish_new_reservation(fixture):
    mod = importlib.import_module('tools.wd_q4_cutoff_watermark')
    private, shared, prefix, pointer, _ = fixture
    data = route(prefix, '2026-11-07T10:00:00Z', private, shared)
    with pytest.raises(mod.WatermarkUnknown):
        with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=data,
                         expected_route_sha256=sha(data), expected_pointer_sha256=pointer,
                         expected_previous_record_sha256=None) as lease:
            path, pin = receipt(lease, private, pointer, prefix,
                                reservation={'attempt': 'b' * 64, 'token': 'c' * 64})
            lease.complete(receipt_path=path, expected_receipt_sha256=pin)
    assert (private / 'q4-cutoff.pending.json').is_file()


@pytest.mark.parametrize('field', ['route_sha256', 'plan_sha256', 'prior_pointer_sha256', 'pointer_sha256',
                                   'prefix_sha256', 'watermark_sha256', 'generation', 'root_identities'])
def test_mismatched_receipt_even_with_its_new_hash_stays_unknown(fixture, field):
    mod = importlib.import_module('tools.wd_q4_cutoff_watermark')
    private, shared, prefix, pointer, pointer_raw = fixture
    data = route(prefix, '2026-11-07T10:00:00Z', private, shared)
    with pytest.raises(mod.WatermarkUnknown):
        with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=data,
                         expected_route_sha256=sha(data), expected_pointer_sha256=pointer,
                         expected_previous_record_sha256=None) as lease:
            path, _ = receipt(lease, private, pointer, prefix)
            record = json.loads(Path(path).read_bytes())
            record[field] = [] if field == 'root_identities' else 'f' * 64
            modified = raw(record)
            Path(path).write_bytes(modified)
            lease.complete(receipt_path=path, expected_receipt_sha256=sha(modified))
    assert (private / 'q4-cutoff.pending.json').is_file()
    assert (shared / 'q4-latest.json').read_bytes() == pointer_raw


def test_owner_lock_prevents_concurrent_cooperating_writer(fixture):
    mod = importlib.import_module('tools.wd_q4_cutoff_watermark')
    private, shared, prefix, pointer, pointer_raw = fixture
    data = route(prefix, '2026-11-07T10:00:00Z', private, shared)
    kwargs = dict(enabled=True, private_root=private, shared_root=shared, route_bytes=data,
                  expected_route_sha256=sha(data), expected_pointer_sha256=pointer,
                  expected_previous_record_sha256=None)
    def contender():
        with mod.reserve(**kwargs):
            return 'incorrectly-admitted'
    with mod.reserve(**kwargs) as lease:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(contender)
            with pytest.raises((mod.WatermarkUnknown, mod.WatermarkRefused)):
                future.result(timeout=5)
        path, pin = receipt(lease, private, pointer, prefix)
        lease.complete(receipt_path=path, expected_receipt_sha256=pin)
    assert (shared / 'q4-latest.json').read_bytes() == pointer_raw


@pytest.mark.skipif(os.name != 'nt', reason='fenced synthetic runner requires Windows')
def test_actual_fenced_runner_receipt_completes_same_scoped_lease(tmp_path, monkeypatch):
    pytest.importorskip('faiss')
    mod = importlib.import_module('tools.wd_q4_cutoff_watermark')
    path = Path(__file__).with_name('test_wd_q4_status_prefix_runner.py')
    spec = importlib.util.spec_from_file_location('watermark_real_runner_fixture', path)
    status = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(status)
    runner = status.runner.__wrapped__()
    case = status.case.__wrapped__(tmp_path, monkeypatch, runner)
    _, _, _, shared, private, _ = case
    real_run = runner.run_once
    observed = []
    def run(**kwargs):
        plan = json.loads(kwargs['plan_bytes'])
        pointer = plan['expected_pointer_sha256']
        if pointer is None:
            return real_run(**kwargs)
        value = json.loads(kwargs['route_bytes'])
        value['cutoff_utc'] = '2026-11-01T00:00:00Z'
        route_bytes = raw(value)
        kwargs.update(route_bytes=route_bytes, expected_route_sha256=sha(route_bytes))
        with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=route_bytes,
                         expected_route_sha256=sha(route_bytes), expected_pointer_sha256=pointer,
                         expected_previous_record_sha256=None) as lease:
            result = real_run(**kwargs, recovery_reservation=lease.reservation)
            marker_sha = sha(raw({'schema': 'wd.q4-runner-reservation.v1', **lease.reservation}))
            receipt_path = private / ('q4-status-result-' + marker_sha + '.json')
            record = lease.complete(receipt_path=str(receipt_path),
                                    expected_receipt_sha256=result['runner_receipt_sha256'])
        # Next public row is not yet ready at the pinned cutoff. Run the real
        # producer again through the lease; no pointer/cursor must advance.
        from sandbox.mind.step2 import prefix_private_state as private_state
        with private_state.locked_state(private, shared, enabled=True) as locked:
            prefix = locked.load(result['pointer_sha256'])
        source = Path(value['source'])
        event = json.loads(source.read_bytes().splitlines()[0])
        event['ts_utc'] = '2026-11-01T00:01:00Z'
        with source.open('ab') as stream:
            stream.write(raw(event) + b'\n')
        anchor = json.loads(kwargs['anchor_bytes'])
        anchor['cursor_start'] = asdict(prefix.cursor)
        anchor_raw = raw(anchor)
        plan.update(generation='synthetic-deferred-generation', expected_pointer_sha256=result['pointer_sha256'])
        plan['pins']['anchor_sha256'] = sha(anchor_raw)
        plan_raw = raw(plan)
        value.update(cursor=asdict(prefix.cursor), prefix_sha256=prefix.sha256,
                     anchor_sha256=sha(anchor_raw), plan_sha256=sha(plan_raw))
        deferred_route = raw(value)
        deferred_kwargs = {**kwargs, 'route_bytes': deferred_route, 'expected_route_sha256': sha(deferred_route),
                           'anchor_bytes': anchor_raw, 'plan_bytes': plan_raw}
        with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=deferred_route,
                         expected_route_sha256=sha(deferred_route), expected_pointer_sha256=result['pointer_sha256'],
                         expected_previous_record_sha256=record) as lease:
            deferred = real_run(**deferred_kwargs, recovery_reservation=lease.reservation)
            assert deferred['status'] == 'EMPTY_NO_CHANGE' and deferred['coverage'] == 'partial_ready_prefix'
            assert deferred['pointer_sha256'] == result['pointer_sha256']
            marker_sha = sha(raw({'schema': 'wd.q4-runner-reservation.v1', **lease.reservation}))
            receipt_path = private / ('q4-status-result-' + marker_sha + '.json')
            record = lease.complete(receipt_path=str(receipt_path),
                                    expected_receipt_sha256=deferred['runner_receipt_sha256'])
        older = raw({**value, 'cutoff_utc': '2026-10-31T23:59:59Z'})
        with pytest.raises(mod.WatermarkRefused):
            with mod.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=older,
                             expected_route_sha256=sha(older), expected_pointer_sha256=result['pointer_sha256'],
                             expected_previous_record_sha256=record):
                pytest.fail('older cutoff admitted after real no-change result')
        observed.append((result, record))
        return result
    monkeypatch.setattr(runner, 'run_once', run)
    status.test_real_fenced_child_two_renewable_updates(case, monkeypatch, fault='none')
    assert len(observed) == 1 and observed[0][0]['status'] == 'PUBLISHED_HISTORY'
    assert (private / 'q4-cutoff-current.json').is_file()
    assert not (private / 'q4-cutoff.pending.json').exists()


@pytest.mark.parametrize('fault', ['record', 'head', 'pending'])
def test_interruption_during_commit_preserves_pending_and_pointer(fixture, monkeypatch, fault):
    mod = importlib.import_module('tools.wd_q4_cutoff_watermark')
    private, shared, prefix, pointer, pointer_raw = fixture
    data = route(prefix, '2026-11-07T10:00:00Z', private, shared)
    kwargs = dict(enabled=True, private_root=private, shared_root=shared, route_bytes=data,
                  expected_route_sha256=sha(data), expected_pointer_sha256=pointer,
                  expected_previous_record_sha256=None)
    create, replace, unlink = mod.runner._create, mod.os.replace, mod.os.unlink
    with pytest.raises(mod.WatermarkUnknown):
        with mod.reserve(**kwargs) as lease:
            path, pin = receipt(lease, private, pointer, prefix)
            with monkeypatch.context() as scoped:
                if fault == 'record':
                    def fail_record(target, content):
                        if target.name.startswith('q4-cutoff-record-'):
                            raise OSError('synthetic')
                        return create(target, content)
                    scoped.setattr(mod.runner, '_create', fail_record)
                elif fault == 'head':
                    scoped.setattr(mod.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('synthetic')))
                else:
                    def fail_pending(target):
                        if target.name == 'q4-cutoff.pending.json':
                            raise OSError('synthetic')
                        return unlink(target)
                    scoped.setattr(mod.os, 'unlink', fail_pending)
                lease.complete(receipt_path=path, expected_receipt_sha256=pin)
    assert (private / 'q4-cutoff.pending.json').is_file()
    assert (shared / 'q4-latest.json').read_bytes() == pointer_raw
