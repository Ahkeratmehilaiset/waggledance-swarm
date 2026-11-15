"""Synthetic contract for the default-off content session API.

No real memory, model, native provider, installed publication or live log is
opened. Existing independently constructed content fixtures exercise the real
source verifier through the session seam; smaller spies isolate dispatch rules.
This seam is an in-process API, not provider startup or live context delivery.
"""
from __future__ import annotations

import hashlib
import hmac
import asyncio
import builtins
import io
from concurrent.futures import ThreadPoolExecutor
import threading
import importlib.util
import json
import os
from pathlib import Path
import sys
import struct
from types import SimpleNamespace

import pytest

from tools import wd_bridge_mind_session as session
from tools import wd_mind_content_query_readonly as content


PROVIDER_KEYS = {"encoder_provider", "index_provider"}
INPUT_KEYS = content._Q4_KEYS - PROVIDER_KEYS
TOKEN = "mind_session_content_refused"


@pytest.fixture
def flat_consumer_case(source_fixtures, tmp_path, monkeypatch):
    state = {'lock': threading.Lock(), 'used': set()}
    monkeypatch.setitem(vars(builtins), '_wd_mind_flat_export_nonce_registry_v1', state)
    monkeypatch.setattr(session, '_EXPORT_NONCE_STATE', state, raising=False)
    from tools import wd_mind_content_snapshot_package as package
    path = Path(__file__).with_name('test_wd_mind_content_snapshot_package.py')
    spec = importlib.util.spec_from_file_location('flat_consumer_package_fixtures', path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    owner, proposals, snapshot, clock = helper._flat_owner_fixture(package, source_fixtures, tmp_path, monkeypatch)
    monkeypatch.setattr(session, 'datetime', clock)
    # This fixture exercises the real source/row gate with synthetic controls;
    # independent code-pin behavior is covered separately below.
    monkeypatch.setattr(session, '_pin_flat_session', lambda root, pin: None, raising=False)
    def response():
        return json.dumps({'schema': 'wd.mind-pipe-candidates.v1', 'nonce': '1' * 64,
            'lane': 'codex-lead-1', 'scope': 'shared', 'status': 'CANDIDATES',
            'candidates': proposals}, separators=(',', ':')).encode()
    return package, owner, proposals, snapshot, clock, response


def _export_fixture(owner, snapshot, *, identifier_override=None):
    gate = owner._gate
    message = snapshot['messages']
    identifier = snapshot['identifier_policy_binding'][0] if identifier_override is None else identifier_override
    key = b'A' * 32  # Match the production export contract, not the generic fixture key.
    tag = hmac.new(key, identifier, hashlib.sha256).hexdigest()
    decision = json.loads(snapshot['decision_bytes'])
    decision['identifier_policy_hmac'] = tag
    decision_bytes = content._canonical(decision)
    updated = dict(snapshot, identifier_policy_binding=(identifier, key, tag),
                   decision_bytes=decision_bytes, decision_sha256=content._sha(decision_bytes))
    inputs = {name: value for name, value in updated.items()
              if name not in ('projection_bytes', 'projection_sha256')}
    projection_bytes = content._serialized(content.rederive_complete_message_projection(**inputs))
    flat_manifest = json.loads(gate._flat_raw)
    flat_manifest['projection_sha256'] = content._sha(projection_bytes)
    blocks = (
        ('message_policy', message['policy_bytes']), ('decision', decision_bytes),
        ('nonreview_policy', snapshot['policy_bytes']), ('roster', snapshot['roster_bytes']),
        ('task_scope', snapshot['approved_tasks_bytes']), ('identifier_policy', identifier),
        ('identifier_key', key), ('projection', projection_bytes),
        ('flat_manifest', content._canonical(flat_manifest)), ('ids', content._canonical(list(gate._ids))),
        ('vectors', gate._vectors), ('encoder_binding', gate._encoder),
    )
    cursor = message['end_cursor']
    meta = {'schema':'wd.mind-owner-export.v1', 'authority':'none', 'nonce':'1'*64,
        'source_path':message['source_path'], 'generation_path':message['generation_path'],
        'cursor':{'offset':cursor.offset, 'file_identity':cursor.file_identity, 'generation':cursor.generation},
        'identifier_policy_tag':tag, 'blocks':[{'name':name, 'length':len(value)} for name,value in blocks]}
    return meta, blocks


@pytest.mark.parametrize('fault', ['none','off','nonce','path','policy','encoder','vector','ids',
                                   'unknown','size','order','length','noncanonical'])
def test_flat_export_reconstructs_only_independently_verified_owner(flat_consumer_case, fault):
    package, owner, proposals, snapshot, clock, response = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    blocks = list(blocks)
    if fault == 'nonce': meta['nonce'] = '2'*64
    if fault == 'path': meta['source_path'] += '.unexpected'
    if fault in {'policy','encoder','vector','ids'}:
        name = {'policy':'message_policy','encoder':'encoder_binding','vector':'vectors','ids':'ids'}[fault]
        index = next(i for i, block in enumerate(blocks) if block[0] == name)
        value = blocks[index][1]
        blocks[index] = (name, bytes([value[0] ^ 1]) + value[1:])
    if fault == 'unknown': meta['unexpected'] = True
    if fault == 'order': blocks[0], blocks[1] = blocks[1], blocks[0]
    if fault == 'length': meta['blocks'][0]['length'] += 1
    raw = content._canonical(meta)
    if fault == 'noncanonical': raw += b' '
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    lease = authorization.begin()
    arguments = dict(enabled=fault != 'off', metadata_bytes=raw, blocks=tuple(blocks), lease=lease,
                     bundle_root=owner._root, expected_manifest_sha256=owner._pin,
                     max_total_bytes=1 if fault == 'size' else 64*1024*1024)
    if fault != 'none':
        with pytest.raises(session.MindContentSessionRefused):
            session.prepare_flat_source_owner_from_export(**arguments)
        return
    rebuilt = session.prepare_flat_source_owner_from_export(**arguments)
    result = session.query_flat_source_candidates(enabled=True, owner=rebuilt, ipc_bytes=response(),
        bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        expected_nonce='1'*64, lane='codex-lead-1', scope='shared')
    assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
    assert result['index_match'] == 'UNKNOWN'
    assert [item['source_event_sha256'] for item in result['results']] == [p['source_event_sha256'] for p in proposals]


@pytest.mark.parametrize('fault', ['refusal', 'interrupt', 'cancel'])
def test_flat_export_refusal_and_cancellation_do_not_keep_key_in_session_traceback(flat_consumer_case, monkeypatch, fault):
    package, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    secret = b'A' * 32
    if fault == 'refusal':
        meta['nonce'] = '2' * 64
        exception_type = session.MindContentSessionRefused
    else:
        exception_type = KeyboardInterrupt if fault == 'interrupt' else asyncio.CancelledError
        def fail(**kwargs):
            raise exception_type('synthetic sensitive exception detail')
        monkeypatch.setattr(package, 'create_consumer_flat_source_owner', fail)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    try:
        session.prepare_flat_source_owner_from_export(enabled=True, metadata_bytes=content._canonical(meta),
            blocks=blocks, lease=authorization.begin(), bundle_root=owner._root,
            expected_manifest_sha256=owner._pin, max_total_bytes=64*1024*1024)
    except exception_type as error:
        assert 'synthetic sensitive exception detail' not in str(error)
        frames = []
        trace = error.__traceback__
        while trace:
            if trace.tb_frame.f_code.co_filename == session.__file__:
                frames.append(trace.tb_frame)
            trace = trace.tb_next
        assert frames
        assert all(repr(secret) not in repr(frame.f_locals) for frame in frames)
        assert error.__context__ is None
    else:
        pytest.fail('expected fixed refusal or cancellation')


@pytest.mark.parametrize('base', [KeyboardInterrupt, GeneratorExit, asyncio.CancelledError])
def test_flat_export_sanitizes_hostile_cancellation_subclasses(flat_consumer_case, monkeypatch, base):
    package, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    class Hostile(base):
        count = 0
        def __new__(cls, *args):
            cls.count += 1
            if cls.count > 1:
                raise RuntimeError('untrusted constructor called again')
            return super().__new__(cls)
        def __getattribute__(self, name):
            raise RuntimeError('untrusted attribute read')
        def __setattr__(self, name, value):
            raise RuntimeError('untrusted attribute write')
    signal = Hostile()
    def fail(**kwargs):
        raise signal
    monkeypatch.setattr(package, 'create_consumer_flat_source_owner', fail)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    with pytest.raises(base) as caught:
        session.prepare_flat_source_owner_from_export(enabled=True, metadata_bytes=content._canonical(meta),
            blocks=blocks, lease=authorization.begin(), bundle_root=owner._root,
            expected_manifest_sha256=owner._pin, max_total_bytes=64*1024*1024)
    assert type(caught.value) is base
    assert caught.value.__context__ is None


def test_flat_export_rejects_consistent_peer_policy_substitution(flat_consumer_case):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    private = content._canonical({'schema':'wd.mind-identifier-policy.v2', 'identifiers':[]})
    assert private != snapshot['identifier_policy_binding'][0]
    meta, blocks = _export_fixture(owner, snapshot, identifier_override=private)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    with pytest.raises(session.MindContentSessionRefused):
        session.prepare_flat_source_owner_from_export(enabled=True, metadata_bytes=content._canonical(meta),
            blocks=blocks, lease=authorization.begin(), bundle_root=owner._root,
            expected_manifest_sha256=owner._pin, max_total_bytes=64*1024*1024)


def test_flat_export_nonce_lease_is_single_use_even_on_refusal(flat_consumer_case):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    lease = authorization.begin()
    arguments = dict(enabled=True, metadata_bytes=content._canonical(meta), blocks=blocks, lease=lease,
        bundle_root=owner._root, expected_manifest_sha256=owner._pin, max_total_bytes=64*1024*1024)
    session.prepare_flat_source_owner_from_export(**arguments)
    with pytest.raises(session.MindContentSessionRefused): session.prepare_flat_source_owner_from_export(**arguments)
    with pytest.raises(session.MindContentSessionRefused): authorization.begin()
    with pytest.raises(session.MindContentSessionRefused):
        session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
            expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))


def test_flat_export_failed_validation_consumes_lease(flat_consumer_case):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    lease = authorization.begin()
    arguments = dict(enabled=True, metadata_bytes=content._canonical(dict(meta, nonce='2'*64)),
        blocks=blocks, lease=lease, bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        max_total_bytes=64*1024*1024)
    with pytest.raises(session.MindContentSessionRefused): session.prepare_flat_source_owner_from_export(**arguments)
    arguments['metadata_bytes'] = content._canonical(meta)
    with pytest.raises(session.MindContentSessionRefused): session.prepare_flat_source_owner_from_export(**arguments)


def test_flat_export_nonce_reservation_is_atomic_and_bounded(monkeypatch):
    monkeypatch.setattr(session, '_EXPORT_NONCE_STATE', {'lock':threading.Lock(), 'used':set()})
    monkeypatch.setattr(session, '_EXPORT_NONCE_LIMIT', 1)
    def reserve():
        try:
            session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
                expected_identifier_policy_sha256='a'*64)
            return True
        except session.MindContentSessionRefused:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: reserve(), range(2))) == [False, True]
    with pytest.raises(session.MindContentSessionRefused):
        session.create_flat_export_authorization(enabled=True, expected_nonce='2'*64,
            expected_identifier_policy_sha256='a'*64)


def test_flat_export_alias_imports_share_one_nonce_registry(monkeypatch):
    state = {'lock': threading.Lock(), 'used': set()}
    monkeypatch.setitem(vars(builtins), '_wd_mind_flat_export_nonce_registry_v1', state)
    modules = []
    for name in ('session_alias_a', 'session_alias_b'):
        spec = importlib.util.spec_from_file_location(name, session.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    modules[0].create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256='a'*64)
    with pytest.raises(modules[1].MindContentSessionRefused):
        modules[1].create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
            expected_identifier_policy_sha256='a'*64)


def _synthetic_export_wire(meta, blocks, key, *, omit_final=False):
    nonce = bytes.fromhex(meta['nonce'])
    digest = hashlib.sha256()
    sequence = 0
    chunks = []
    data_count = 0
    payload_total = 0
    def frame(kind, payload):
        nonlocal sequence
        header = struct.pack('>4sB3xII', b'MEX1', ord(kind), sequence, len(payload))
        mac = hmac.new(key, b'wd.mind-export-frame.v1\0' + nonce + header + payload, hashlib.sha256).digest()
        sequence += 1
        chunks.append(header + payload + mac)
    metadata = content._canonical(meta)
    digest.update(metadata)
    payload_total += len(metadata)
    frame('M', metadata)
    for _, block in blocks:
        for pos in range(0, len(block), 97):
            value = block[pos:pos+97]
            frame('D', value)
            digest.update(value)
            payload_total += len(value)
            data_count += 1
    if not omit_final:
        frame('F', struct.pack('>IQ32s', data_count, payload_total, digest.digest()))
    return b''.join(chunks)


def _rewrite_signed_export_frame(wire, index, key, nonce, *, sequence=None, reserved=None, payload_edit=None):
    offset = 0
    for _ in range(index):
        length = struct.unpack('>I', wire[offset+12:offset+16])[0]
        offset += 16 + length + 32
    header = bytearray(wire[offset:offset+16])
    length = struct.unpack('>I', header[12:16])[0]
    value = bytearray(wire[offset+16:offset+16+length])
    if sequence is not None: header[8:12] = struct.pack('>I', sequence)
    if reserved is not None: header[5] = reserved
    if payload_edit is not None: value[payload_edit] ^= 1
    raw_header, raw_value = bytes(header), bytes(value)
    mac = hmac.new(key, b'wd.mind-export-frame.v1\0' + bytes.fromhex(nonce) + raw_header + raw_value,
                   hashlib.sha256).digest()
    return wire[:offset] + raw_header + raw_value + mac + wire[offset+16+length+32:]


@pytest.mark.parametrize('fault', ['none','off','mac','truncated','missing_final','trailing',
                                   'header_length','wire_budget','metadata_budget','timeout'])
def test_flat_export_stream_framing_fails_closed(flat_consumer_case, monkeypatch, fault):
    _, owner, proposals, snapshot, _, response = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    key = b'B'*32
    wire = _synthetic_export_wire(meta, blocks, key, omit_final=fault == 'missing_final')
    if fault == 'mac': wire = wire[:-1] + bytes([wire[-1] ^ 1])
    if fault == 'truncated': wire = wire[:-4]
    if fault == 'trailing': wire += b'x'
    if fault == 'header_length': wire = wire[:12] + struct.pack('>I', 0xffffffff) + wire[16:]
    opened, requests = [], []
    class Stream(io.BytesIO):
        def read(self, n=-1):
            requests.append(n)
            assert 0 <= n <= 65488
            return super().read(n)
    def open_read():
        opened.append(True)
        return Stream(wire)
    if fault == 'timeout':
        ticks = iter([0.0, 0.0, 2.0])
        monkeypatch.setattr(session.time, 'monotonic', lambda: next(ticks, 2.0))
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    arguments = dict(enabled=fault != 'off', authorization=authorization, open_read=open_read, channel_key=key,
        bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        max_total_bytes=1 if fault == 'metadata_budget' else 64*1024*1024,
        max_wire_bytes=1 if fault == 'wire_budget' else 64*1024*1024, timeout_seconds=1)
    if fault != 'none':
        with pytest.raises(session.MindContentSessionRefused): session.read_flat_owner_stream(**arguments)
        if fault == 'off': assert not opened
        if fault == 'header_length': assert requests == [16]
        if fault == 'wire_budget': assert requests == []
        return
    rebuilt = session.read_flat_owner_stream(**arguments)
    result = session.query_flat_source_candidates(enabled=True, owner=rebuilt, ipc_bytes=response(),
        bundle_root=owner._root, expected_manifest_sha256=owner._pin,
        expected_nonce='1'*64, lane='codex-lead-1', scope='shared')
    assert result['authority'] == 'none' and result['index_match'] == 'UNKNOWN'
    assert [item['source_event_sha256'] for item in result['results']] == [p['source_event_sha256'] for p in proposals]


@pytest.mark.parametrize('fault', ['sequence','reserved','final_digest','policy','reuse','close_timeout'])
def test_flat_export_stream_signed_frames_and_lifecycle(flat_consumer_case, monkeypatch, fault):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    identifier = content._canonical({'schema':'wd.mind-identifier-policy.v2', 'identifiers':[]})
    meta, blocks = _export_fixture(owner, snapshot, identifier_override=identifier if fault == 'policy' else None)
    key = b'B'*32
    wire = _synthetic_export_wire(meta, blocks, key)
    if fault == 'sequence': wire = _rewrite_signed_export_frame(wire, 1, key, meta['nonce'], sequence=77)
    if fault == 'reserved': wire = _rewrite_signed_export_frame(wire, 0, key, meta['nonce'], reserved=1)
    if fault == 'final_digest':
        position = 0
        count = 0
        while position < len(wire):
            length = struct.unpack('>I', wire[position+12:position+16])[0]
            position += 16 + length + 32
            count += 1
        wire = _rewrite_signed_export_frame(wire, count-1, key, meta['nonce'], payload_edit=12)
    clock = [0.0]
    class Stream(io.BytesIO):
        def close(self):
            if fault == 'close_timeout': clock[0] = 2.0
            super().close()
    if fault == 'close_timeout': monkeypatch.setattr(session.time, 'monotonic', lambda: clock[0])
    opened = []
    def open_read():
        opened.append(True)
        return Stream(wire)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    arguments = dict(enabled=True, authorization=authorization, open_read=open_read, channel_key=key,
        bundle_root=owner._root, expected_manifest_sha256=owner._pin, max_total_bytes=64*1024*1024,
        max_wire_bytes=64*1024*1024, timeout_seconds=1)
    if fault == 'reuse':
        session.read_flat_owner_stream(**arguments)
        with pytest.raises(session.MindContentSessionRefused): session.read_flat_owner_stream(**arguments)
        assert len(opened) == 1
    else:
        with pytest.raises(session.MindContentSessionRefused): session.read_flat_owner_stream(**arguments)


def test_flat_export_stream_elapsed_deadline_cannot_interrupt_blocked_sync_read(flat_consumer_case):
    """The sync reader stays blocked until released, then refuses its late result.

    This is not an I/O cancellation, process cleanup, or owner-release proof.
    A real Windows caller must arrange cancellable I/O or retain the owner in HOLD.
    """
    _, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    key = b'B'*32
    wire = _synthetic_export_wire(meta, blocks, key)
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    class BlockingStream(io.BytesIO):
        def read(self, count=-1):
            entered.set()
            assert release.wait(5)
            return super().read(count)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    result = []
    def run():
        try:
            session.read_flat_owner_stream(enabled=True, authorization=authorization,
                open_read=lambda: BlockingStream(wire), channel_key=key, bundle_root=owner._root,
                expected_manifest_sha256=owner._pin, max_total_bytes=64*1024*1024,
                max_wire_bytes=64*1024*1024, timeout_seconds=0.05)
        except session.MindContentSessionRefused:
            result.append('refused')
        finally:
            done.set()
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5)
        assert not done.wait(0.15)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and result == ['refused']


@pytest.mark.parametrize('late', ['preflight', 'open'])
def test_flat_export_stream_checks_deadline_before_and_after_open(flat_consumer_case, monkeypatch, late):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    key = b'B'*32
    wire = _synthetic_export_wire(meta, blocks, key)
    clock, opened, reads = [0.0], [], []
    monkeypatch.setattr(session.time, 'monotonic', lambda: clock[0])
    def pin(root, pin):
        if late == 'preflight': clock[0] = 2.0
    monkeypatch.setattr(session, '_pin_flat_session', pin)
    class Stream(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)
    def open_read():
        opened.append(True)
        if late == 'open': clock[0] = 2.0
        return Stream(wire)
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    with pytest.raises(session.MindContentSessionRefused):
        session.read_flat_owner_stream(enabled=True, authorization=authorization, open_read=open_read,
            channel_key=key, bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            max_total_bytes=64*1024*1024, max_wire_bytes=64*1024*1024, timeout_seconds=1)
    assert opened == ([] if late == 'preflight' else [True])
    assert reads == []


@pytest.mark.parametrize('phase', ['read', 'close'])
def test_flat_export_stream_cancellation_scrubs_key_and_preserves_signal(flat_consumer_case, phase):
    _, owner, _, snapshot, _, _ = flat_consumer_case
    meta, blocks = _export_fixture(owner, snapshot)
    key = b'B'*32
    wire = _synthetic_export_wire(meta, blocks, key)
    class HostileCancel(asyncio.CancelledError):
        constructions = 0
        def __new__(cls, *args):
            cls.constructions += 1
            if cls.constructions > 1: raise RuntimeError('unexpected signal constructor')
            return super().__new__(cls)
    signal = HostileCancel()
    class Stream(io.BytesIO):
        def read(self, size=-1):
            if phase == 'read': raise signal
            return super().read(size)
        def close(self):
            super().close()
            if phase == 'close': raise signal
    authorization = session.create_flat_export_authorization(enabled=True, expected_nonce='1'*64,
        expected_identifier_policy_sha256=content._sha(snapshot['identifier_policy_binding'][0]))
    try:
        session.read_flat_owner_stream(enabled=True, authorization=authorization, open_read=lambda: Stream(wire),
            channel_key=key, bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            max_total_bytes=64*1024*1024, max_wire_bytes=64*1024*1024, timeout_seconds=5)
    except asyncio.CancelledError as error:
        assert type(error) is asyncio.CancelledError and error.__context__ is None
        trace = error.__traceback__
        frames = []
        while trace:
            if trace.tb_frame.f_code.co_filename == session.__file__: frames.append(trace.tb_frame)
            trace = trace.tb_next
        assert frames and all(repr(key) not in repr(frame.f_locals) for frame in frames)
    else:
        pytest.fail('expected cancellation')


def test_flat_consumer_is_default_off_before_any_ipc(flat_consumer_case):
    _, owner, _, _, _, response = flat_consumer_case
    with pytest.raises(session.MindContentSessionRefused):
        session.query_flat_source_candidates(owner=owner, ipc_bytes=response(), bundle_root=owner._root,
            expected_manifest_sha256=owner._pin, expected_nonce='1' * 64, lane='codex-lead-1', scope='shared')


@pytest.mark.parametrize('turn', ['first', 'resume'])
def test_flat_consumer_resolves_source_as_untrusted_data(flat_consumer_case, turn):
    _, owner, proposals, _, _, response = flat_consumer_case
    def run():
        return session.query_flat_source_candidates(enabled=True, owner=owner, ipc_bytes=response(),
            bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            expected_nonce='1' * 64, lane='codex-lead-1', scope='shared')
    if turn == 'resume': run()
    result = run()
    assert result['authority'] == 'none' and result['sender_authentication'] == 'UNKNOWN'
    assert result['index_match'] == 'UNKNOWN' and result['fleet_sha256'] == 'a' * 64
    assert [item['source_event_sha256'] for item in result['results']] == [p['source_event_sha256'] for p in proposals]
    assert all(item['event_age_seconds_upper_bound'] < 900 for item in result['results'])


@pytest.mark.parametrize('fault', ['ref', 'source', 'nonce', 'lane', 'scope', 'duplicate', 'oversize',
                                   'duplicate_json_key', 'stale', 'row', 'fleet', 'encoder'])
def test_flat_consumer_refuses_untrusted_or_changed_inputs(flat_consumer_case, monkeypatch, fault):
    from datetime import timedelta
    package, owner, proposals, snapshot, clock, response = flat_consumer_case
    value = json.loads(response())
    if fault == 'ref': value['candidates'][0]['ref_sha256'] = '0' * 64
    if fault == 'source': value['candidates'][0]['source_event_sha256'] = '0' * 64
    if fault == 'nonce': value['nonce'] = '2' * 64
    if fault == 'lane': value['lane'] = 'fable-5'
    if fault == 'scope': value['scope'] = 'own'
    if fault == 'duplicate': value['candidates'].append(dict(value['candidates'][0]))
    if fault == 'stale': clock.value += timedelta(minutes=20)
    if fault == 'row':
        path = Path(snapshot['messages']['source_path'])
        rows = path.read_bytes().splitlines(keepends=True)
        index = next(i for i, row in enumerate(rows)
                     if content._sha(content._canonical(json.loads(row))) == proposals[0]['source_event_sha256'])
        rows[index] = rows[index].replace(b'"message":', b'"messagE":', 1)
        path.write_bytes(b''.join(rows))
    if fault == 'fleet': monkeypatch.setattr(package, '_consumer_bundle_controls', lambda root, pin: None)
    if fault == 'encoder': monkeypatch.setattr(package, '_consumer_flat_encoder', lambda root, pin: b'changed')
    raw = json.dumps(value, separators=(',', ':')).encode()
    if fault == 'oversize': raw += b' ' * 2049
    if fault == 'duplicate_json_key': raw = b'{"nonce":"1","nonce":"2"}'
    with pytest.raises(session.MindContentSessionRefused):
        session.query_flat_source_candidates(enabled=True, owner=owner, ipc_bytes=raw,
            bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            expected_nonce='1' * 64, lane='codex-lead-1', scope='shared')


@pytest.mark.parametrize('fault', ['none', 'manifest', 'session_code', 'origin'])
def test_flat_consumer_independently_checks_pinned_session_code(tmp_path, monkeypatch, fault):
    root = tmp_path / 'bundle'
    source = root / 'tools-bootstrap' / 'tools' / 'wd_bridge_mind_session.py'
    source.parent.mkdir(parents=True)
    source.write_bytes(Path(session.__file__).read_bytes())
    manifest = {'schema_version': 1, 'files': {'tools-bootstrap/tools/wd_bridge_mind_session.py':
                hashlib.sha256(source.read_bytes()).hexdigest()}}
    raw = json.dumps(manifest, separators=(',', ':')).encode()
    (root / 'deployment-manifest.json').write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(session, '__file__', str(source if fault != 'origin' else root / 'other.py'))
    if fault == 'manifest': pin = '0' * 64
    if fault == 'session_code': source.write_bytes(b'# changed')
    if fault == 'none':
        session._pin_flat_session(str(root), pin)
    else:
        with pytest.raises((ValueError, OSError)):
            session._pin_flat_session(str(root), pin)


@pytest.mark.parametrize('fault', ['none', 'manifest', 'fleet', 'policy', 'session', 'gate', 'encoder', 'worker'])
def test_flat_consumer_synthetic_bundle_chain_keeps_pins(tmp_path, fault):
    """The actual pinned gate and session run together in a synthetic bundle."""
    import ast
    import subprocess
    gate_test = Path(__file__).with_name('test_wd_mind_content_snapshot_package.py')
    tree = ast.parse(gate_test.read_text(encoding='utf-8'))
    function = next(item for item in tree.body if isinstance(item, ast.FunctionDef)
                    and item.name == 'test_consumer_owner_real_synthetic_bundle_chain')
    assignment = next(item for item in function.body if isinstance(item, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'script' for target in item.targets))
    script = ast.literal_eval(assignment.value)
    def replace_once(old, new):
        nonlocal script
        assert script.count(old) == 1
        script = script.replace(old, new)
    replace_once("paths = ['tools/__init__.py', ",
                 "paths = ['tools/wd_bridge_mind_session.py', 'tools/wd_bridge_mind_lane_read.py', 'tools/__init__.py', ")
    replace_once('from tools import wd_mind_content_snapshot_package as package\n',
                 'from tools import wd_mind_content_snapshot_package as package\nfrom tools import wd_bridge_mind_session as session\n')
    replace_once('package.datetime = Clock\n', 'package.datetime = Clock\nsession.datetime = Clock\n')
    probe = r'''
fault = sys.argv[3]
frame = reader._canonical({'schema':'wd.mind-pipe-candidates.v1', 'nonce':'1'*64,
    'lane':'codex-lead-1', 'scope':'shared', 'status':'CANDIDATES', 'candidates':proposals})
pins = {'fleet':bundle/'wd-fleet.json', 'policy':bundle/'wd-mind-message-policy.json',
        'session':bootstrap/'tools/wd_bridge_mind_session.py',
        'gate':bootstrap/'tools/wd_mind_content_snapshot_package.py',
        'encoder':bundle/'wd-mind-e5-encoder-binding.json', 'worker':worker}
if fault in pins: pins[fault].write_bytes(b'# changed')
pin = '0'*64 if fault == 'manifest' else reader._sha(manifest)
try:
    found = session.query_flat_source_candidates(enabled=True, owner=flat, ipc_bytes=frame,
        bundle_root=str(bundle), expected_manifest_sha256=pin, expected_nonce='1'*64,
        lane='codex-lead-1', scope='shared')
    assert fault == 'none' and found['results'] and found['authority'] == 'none'
    assert found['index_match'] == 'UNKNOWN' and found['sender_authentication'] == 'UNKNOWN'
    assert found['fleet_sha256'] == reader._sha(fleet)
    assert all(hit['event_age_seconds_upper_bound'] < 900 for hit in found['results'])
except session.MindContentSessionRefused:
    assert fault != 'none'
sys.exit(0)
'''
    replace_once("flat_result = flat.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')\n",
                 probe + "\nflat_result = flat.resolve(proposals=proposals, lane='codex-lead-1', scope='shared')\n")
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(Path(__file__).resolve().parents[2]),
                             str(tmp_path), fault], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('window', ['final_session_pin', 'final_encoder_pin'])
def test_flat_consumer_rechecks_source_after_final_pins(flat_consumer_case, monkeypatch, window):
    _, owner, proposals, snapshot, _, response = flat_consumer_case
    source = Path(snapshot['messages']['source_path'])
    calls = []
    def mutate_selected_row():
        rows = source.read_bytes().splitlines(keepends=True)
        index = next(i for i, row in enumerate(rows)
                     if content._sha(content._canonical(json.loads(row))) == proposals[0]['source_event_sha256'])
        changed = rows[index].replace(b'"message":', b'"messagE":', 1)
        assert changed != rows[index] and len(changed) == len(rows[index])
        rows[index] = changed
        source.write_bytes(b''.join(rows))
    if window == 'final_session_pin':
        def pin(root, digest):
            calls.append(1)
            if len(calls) == 2: mutate_selected_row()
        monkeypatch.setattr(session, '_pin_flat_session', pin)
    else:
        original = type(owner._gate)._check_delivery_binding
        def encoder_check(gate):
            result = original(gate)
            calls.append(1)
            # The first resolve checks the encoder twice; the session's final
            # explicit E5 check is the third invocation.
            if len(calls) == 3: mutate_selected_row()
            return result
        monkeypatch.setattr(type(owner._gate), '_check_delivery_binding', encoder_check)
    with pytest.raises(session.MindContentSessionRefused, match='^mind_session_content_refused$'):
        session.query_flat_source_candidates(enabled=True, owner=owner, ipc_bytes=response(),
            bundle_root=owner._root, expected_manifest_sha256=owner._pin,
            expected_nonce='1' * 64, lane='codex-lead-1', scope='shared')
    assert len(calls) >= (2 if window == 'final_session_pin' else 3)



def _entry():
    result = getattr(session, "query_content_session", None)
    assert callable(result), "default-off content session seam is not implemented"
    return result


def _refused(operation):
    with pytest.raises(ValueError) as caught:
        operation()
    assert type(caught.value).__name__ == "MindContentSessionRefused"
    assert str(caught.value) == TOKEN
    assert caught.value.__context__ is None


def _dummy_inputs():
    return dict({key: object() for key in INPUT_KEYS}, enabled=True)


def _providers():
    def forbidden(*args, **kwargs):
        pytest.fail("provider callback reached before full source verification")
    return (SimpleNamespace(identity={}, query=forbidden),
            SimpleNamespace(identity={}, load=forbidden))


@pytest.fixture
def dispatch(monkeypatch):
    calls = []

    def query(**inputs):
        calls.append(inputs)
        return {"schema": "wd.q4-content-snapshot-query.v2", "authority": "none",
                "status": "ABSTAIN", "provider_authentication": "UNKNOWN", "results": []}

    monkeypatch.setattr(content, "query_content_snapshot", query)
    return calls


@pytest.mark.parametrize("enabled", [False, None, 0, 1, "1", [], {}])
def test_content_session_requires_exact_true_before_query(enabled, dispatch):
    entry = _entry()
    _refused(lambda: entry(enabled=enabled, inputs=_dummy_inputs(), providers=_providers()))
    assert dispatch == []


def test_content_session_is_disabled_by_default(dispatch):
    entry = _entry()
    _refused(lambda: entry(inputs=_dummy_inputs(), providers=_providers()))
    assert dispatch == []


def test_content_session_environment_cannot_enable_it(monkeypatch, dispatch):
    entry = _entry()
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "1")
    monkeypatch.setenv("WD_MIND_CONTENT_ENABLED", "1")
    _refused(lambda: entry(inputs=_dummy_inputs(), providers=_providers()))
    assert dispatch == []


@pytest.mark.parametrize("inputs", [None, {}, [], (), {"enabled": True}])
def test_content_session_missing_input_surface_has_no_query_callback(inputs, dispatch):
    entry = _entry()
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=_providers()))
    assert dispatch == []


@pytest.mark.parametrize("fault", ["none", "list", "short", "long", "encoder_none", "index_none", "all_none"])
def test_content_session_requires_explicit_two_provider_tuple(fault, dispatch):
    entry = _entry()
    providers = _providers()
    providers = {
        "none": None, "list": list(providers), "short": providers[:1],
        "long": providers + (object(),), "encoder_none": (None, providers[1]),
        "index_none": (providers[0], None), "all_none": (None, None),
    }[fault]
    _refused(lambda: entry(enabled=True, inputs=_dummy_inputs(), providers=providers))
    assert dispatch == []


@pytest.mark.parametrize("fault", ["extra", "encoder_provider", "index_provider", "inner_disabled"])
def test_content_session_input_set_is_closed_and_unambiguous(fault, dispatch):
    entry = _entry()
    inputs = _dummy_inputs()
    if fault == "inner_disabled":
        inputs["enabled"] = False
    else:
        inputs[fault] = object()
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=_providers()))
    assert dispatch == []


@pytest.mark.parametrize("missing", sorted(INPUT_KEYS))
def test_content_session_requires_every_explicit_binding(missing, dispatch):
    entry = _entry()
    inputs = _dummy_inputs()
    inputs.pop(missing)
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=_providers()))
    assert dispatch == []


def test_content_session_forwards_exact_owned_inputs_without_defaults_or_mutation(dispatch):
    entry = _entry()
    inputs, providers = _dummy_inputs(), _providers()
    original = dict(inputs)
    result = entry(enabled=True, inputs=inputs, providers=providers)
    assert result["status"] == "ABSTAIN" and result["authority"] == "none"
    assert result["provider_authentication"] == "UNKNOWN"
    assert inputs == original and len(dispatch) == 1
    forwarded = dispatch[0]
    assert set(forwarded) == content._Q4_KEYS
    assert all(forwarded[key] is value for key, value in original.items())
    assert forwarded["encoder_provider"] is providers[0]
    assert forwarded["index_provider"] is providers[1]


@pytest.mark.parametrize("fault", ["reader_refusal", "unsafe_exception"])
def test_content_session_contains_refusals_without_exception_text_or_context(fault, monkeypatch):
    entry = _entry()

    def fail(**inputs):
        if fault == "reader_refusal":
            raise content.ContentQueryRefused("content_query_refused")
        raise RuntimeError("synthetic-private-provider-detail")

    monkeypatch.setattr(content, "query_content_snapshot", fail)
    _refused(lambda: entry(enabled=True, inputs=_dummy_inputs(), providers=_providers()))


def test_existing_status_cli_stays_status_only(monkeypatch, capsys, tmp_path, dispatch):
    # Positive pre-Source regression control: the old status CLI already works.
    agent, uuid = "codex-tools-1", "00000000-0000-4000-8000-000000000002"
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", agent)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", uuid)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(tmp_path / "synthetic-runtime"))
    monkeypatch.setattr(session, "_fleet_roster", lambda *args: {agent: uuid})
    monkeypatch.setattr(session.bridge_compact_view, "_read_view_rows", lambda *args: ([], "synthetic-cursor", None))
    expected = {"schema": "synthetic-status-projection", "authority": "none", "lane": agent}
    monkeypatch.setattr(session.lane_reader, "lane_read", lambda **kwargs: expected)
    audit_root = tmp_path / ".codex-audit"
    latest = audit_root / "mind-live" / "latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text("{}", encoding="utf-8")
    result = session.main(["--latest", str(latest),
                           "--audit-root", str(audit_root),
                           "--fleet", str(tmp_path / "synthetic-fleet"), "--fleet-sha256", "0" * 64])
    captured = capsys.readouterr()
    assert result == 0 and captured.err == ""
    output = json.loads(captured.out)
    assert (output["schema"], output["authority"], output["lane"]) == ("synthetic-status-projection", "none", agent)
    assert output["fleet_sha256"] == "0" * 64 and output["source_event_refs"] == []
    assert output["status_observation"]["schema"] == "wd.mind-direct-status.v1"
    assert output["status_observation"]["observation_status"] == "UNKNOWN"
    assert dispatch == []


@pytest.fixture(scope="module")
def source_fixtures():
    # Reuse the existing independently constructed fixture, not the production
    # projector/writer. Explicit path prevents resolving an installed tests package.
    path = Path(__file__).with_name("test_wd_mind_content_query_readonly.py")
    spec = importlib.util.spec_from_file_location("session_content_source_fixtures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def _split(case):
    values = dict(case)
    providers = values.pop("encoder_provider"), values.pop("index_provider")
    assert set(values) == INPUT_KEYS
    return values, providers


def test_content_session_uses_real_verification_and_synthetic_query(source_fixtures, tmp_path):
    entry = _entry()
    case = source_fixtures._semantic_case(content, tmp_path)
    inputs, providers = _split(case)
    result = entry(enabled=True, inputs=inputs, providers=providers)
    assert result["schema"] == "wd.q4-content-snapshot-query.v2"
    assert result["status"] == "ANSWER" and result["results"]
    assert result["authority"] == "none" and result["provider_authentication"] == "UNKNOWN"
    assert case["stream"].reads == 1 and len(providers[0].calls) == 1
    assert len(providers[1].calls) == len(case["cell_snapshots"])


@pytest.mark.parametrize("fault", ["source_bytes", "descriptor_pin", "second_cell", "provider_identity", "documents"])
def test_content_session_preserves_source_and_binding_refusal_before_callbacks(source_fixtures, tmp_path, fault):
    entry = _entry()
    case = source_fixtures._semantic_case(content, tmp_path)
    inputs, providers = _split(case)
    if fault == "source_bytes":
        path = Path(inputs["messages"]["source_path"])
        raw = path.read_bytes()
        changed = raw.replace(b"network retry", b"altered retry", 1)
        assert changed != raw and len(changed) == len(raw)
        path.write_bytes(changed)
    elif fault == "descriptor_pin":
        inputs["descriptor_sha256"] = "0" * 64
    elif fault == "second_cell":
        inputs["cell_snapshots"]["system"]["artifacts"]["index"] += b"tampered"
    elif fault == "provider_identity":
        providers[0].identity["encoder"]["revision"] = "synthetic-changed"
    else:
        inputs["documents"] = {}
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=providers))
    assert case["stream"].reads == 0 and providers[0].calls == [] and providers[1].calls == []


def test_content_session_late_source_change_never_returns_partial_content(source_fixtures, tmp_path, capsys):
    entry = _entry()
    case = source_fixtures._semantic_case(content, tmp_path)
    inputs, providers = _split(case)
    path = Path(inputs["messages"]["source_path"])

    def change(index):
        path.write_bytes(path.read_bytes().replace(b"network retry", b"altered retry", 1))

    providers[1].on_load = change
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=providers))
    assert providers[1].calls
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_content_session_does_not_write_files_or_change_environment(source_fixtures, tmp_path):
    entry = _entry()
    case = source_fixtures._semantic_case(content, tmp_path)
    inputs, providers = _split(case)
    before_env = dict(os.environ)
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in tmp_path.rglob("*") if path.is_file()}
    assert entry(enabled=True, inputs=inputs, providers=providers)["status"] == "ANSWER"
    assert dict(os.environ) == before_env
    assert {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in tmp_path.rglob("*") if path.is_file()} == before
