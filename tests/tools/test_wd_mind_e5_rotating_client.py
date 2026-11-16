"""Cooperative worker rotation with small synthetic budgets; no model or disk I/O."""
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from tools import wd_mind_e5_rotating_client as rotation


BINDING = '1' * 64
SNAPSHOT = '2' * 64
BULK = '61998bfeea1342988301f30f21d9781017249ef66761ec81874e5cc34633bc08'


def request(nonce, texts=('passage: synthetic',)):
    value = {'schema': 'wd.mind-supervised-e5-request.v1', 'operation': 'refresh',
             'nonce': format(nonce, '064x'), 'encoder_binding_sha256': BINDING,
             'passages': list(texts)}
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    value['request_sha256'] = hashlib.sha256(raw).hexdigest()
    return value


class FakeOwner:
    def __init__(self, *, hold=False):
        self.hold = hold
        self.owned = True
        self.closed = 0
        self._bulk_contract_sha256 = BULK
        self._condition = threading.Condition()
        self._counts = {'stdout': 79, 'stderr': 0}
        self._output = bytearray()
        self.resources = SimpleNamespace(process=object(), job=object())

    def status(self):
        return {'state': 'RUNNING' if self.owned else 'EXITED', 'owned': self.owned,
                'reason': None, 'exit_code': None if self.owned else 0}

    def shutdown(self, *, timeout=0):
        self.closed += 1
        if self.hold:
            return {'state': 'HOLD', 'owned': True, 'exit_code': None}
        self.owned = False
        return {'state': 'CANCELLED', 'owned': False, 'exit_code': None}


class FakeClient:
    def __init__(self, owner):
        self.owner = owner
        self._binding_sha256 = BINDING
        self._bulk_contract_sha256 = BULK
        self._closed = False
        self._next_id = 1
        self._used_nonces = set()
        self._timeout = 30.0

    def encode_supervised(self, value, *, frame_check=None, call_deadline=None):
        if frame_check is not None:
            frame_check()
        self._used_nonces.add(value['nonce'])
        frames = (len(value['passages']) + 3) // 4
        self._next_id += frames
        with self.owner._condition:
            self.owner._counts['stdout'] += frames * 79 + len(value['passages']) * 1536
        if frame_check is not None:
            frame_check()
        return {'nonce': value['nonce'], 'request_sha256': value['request_sha256'],
                'vector_bytes': b'v' * (1536 * len(value['passages']))}

    def close(self, *, timeout=0):
        self._closed = True
        self.owner.closed += 1
        if self.owner.hold:
            return {'state': 'HOLD', 'owned': True, 'exit_code': None}
        self.owner.owned = False
        return {'state': 'EXITED', 'owned': False, 'exit_code': 0}


class Factory:
    def __init__(self, *, hold=False, fail_after_sink=False):
        self.hold, self.fail_after_sink = hold, fail_after_sink
        self.owners = []

    def __call__(self, *, owner_sink=None):
        owner = FakeOwner(hold=self.hold)
        self.owners.append(owner)
        if owner_sink is not None:
            owner_sink(owner)
        if self.fail_after_sink:
            raise RuntimeError('synthetic launch failure')
        return {'status': 'READY', 'owner': owner, 'client': FakeClient(owner),
                'bulk_contract_sha256': BULK}


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setattr(rotation, 'GracefulE5WorkerClient', FakeClient)


def create(factory, check=lambda: SNAPSHOT):
    return rotation.RotatingGracefulE5Client(
        factory=factory, encoder_binding_sha256=BINDING, snapshot_sha256=SNAPSHOT,
        source_check=check, shutdown_timeout=1, call_timeout=30, bulk_contract_sha256=BULK)


def test_rotates_before_exceeding_cumulative_bytes_and_preserves_result(fake_client, monkeypatch):
    # Exact room for a handshake and two single-vector response frames.
    monkeypatch.setattr(rotation, 'BULK_STDOUT_BYTES', 79 + 2 * (79 + 1536))
    checks, factory = [], Factory()
    client = create(factory, lambda: checks.append(True) or SNAPSHOT)
    try:
        values = [client.encode_supervised(request(n)) for n in (1, 2, 3)]
        assert [value['vector_bytes'] for value in values] == [b'v' * 1536] * 3
        assert len(factory.owners) == 2 and factory.owners[0].closed == 1
        assert factory.owners[0].owned is False and factory.owners[1].owned is True
        assert len(checks) >= 6
    finally:
        client.close(timeout=1)


def test_rotates_before_nonce_limit(fake_client, monkeypatch):
    monkeypatch.setattr(rotation, '_BULK_NONCE_LIMIT', 2)
    factory = Factory()
    client = create(factory)
    try:
        for nonce in (1, 2, 3):
            client.encode_supervised(request(nonce))
        assert len(factory.owners) == 2 and factory.owners[0].owned is False
    finally:
        client.close(timeout=1)


def test_failed_retirement_retains_owner_and_refuses_successor(fake_client, monkeypatch):
    monkeypatch.setattr(rotation, '_BULK_NONCE_LIMIT', 1)
    factory = Factory(hold=True)
    client = create(factory)
    client.encode_supervised(request(1))
    with pytest.raises(rotation.RotationRefused):
        client.encode_supervised(request(2))
    assert len(factory.owners) == 1 and factory.owners[0].owned is True
    assert client.owner is factory.owners[0]


def test_changed_source_after_response_never_returns_partial_result(fake_client):
    calls = []
    def changed():
        calls.append(True)
        return SNAPSHOT if len(calls) == 1 else '3' * 64
    factory = Factory()
    client = create(factory, changed)
    with pytest.raises(rotation.RotationRefused):
        client.encode_supervised(request(1))
    assert len(factory.owners) == 1
    with pytest.raises(rotation.RotationRefused):
        client.encode_supervised(request(2))


def test_factory_failure_after_owner_sink_retains_owner(fake_client):
    factory = Factory(fail_after_sink=True)
    client = create(factory)
    with pytest.raises(rotation.RotationRefused):
        client.encode_supervised(request(1))
    assert len(factory.owners) == 1 and client.owner is factory.owners[0]
    assert factory.owners[0].owned is True


def test_close_can_reconcile_owner_captured_before_launch_failure(fake_client):
    factory = Factory(fail_after_sink=True)
    client = create(factory)
    with pytest.raises(rotation.RotationRefused):
        client.encode_supervised(request(1))
    assert client.status()['state'] == 'HOLD'
    assert client.close(timeout=1)['owned'] is False
    assert client.owner is None


def test_replay_is_refused_across_rotation(fake_client, monkeypatch):
    monkeypatch.setattr(rotation, '_BULK_NONCE_LIMIT', 1)
    factory = Factory()
    client = create(factory)
    try:
        client.encode_supervised(request(1))
        client.encode_supervised(request(2))
        with pytest.raises(rotation.RotationRefused):
            client.encode_supervised(request(1))
    finally:
        client.close(timeout=1)


def test_expired_deadline_never_starts_worker(fake_client):
    factory = Factory()
    client = create(factory)
    with pytest.raises(rotation.RotationRefused):
        client.encode_supervised(request(1), call_deadline=time.monotonic() - 1)
    assert not factory.owners


def test_source_failure_before_launch_reports_failed_without_owner(fake_client):
    factory = Factory()
    client = create(factory, lambda: '3' * 64)
    with pytest.raises(rotation.RotationRefused):
        client.encode_supervised(request(1))
    assert not factory.owners and client.owner is None
    assert client.status() == {'state': 'FAILED', 'owned': False, 'authority': 'none'}
    assert client.close(timeout=0)['owned'] is False
    assert client.status() == {'state': 'FAILED', 'owned': False, 'authority': 'none'}


def test_unexpected_worker_stdout_refuses_without_replacement(fake_client):
    factory = Factory()
    client = create(factory)
    client.encode_supervised(request(1))
    with client.owner._condition:
        client.owner._counts['stdout'] += 1
    with pytest.raises(rotation.RotationRefused):
        client.encode_supervised(request(2))
    assert len(factory.owners) == 1


def test_bad_input_does_not_retire_healthy_owner(fake_client):
    factory = Factory()
    client = create(factory)
    try:
        client.encode_supervised(request(1))
        with pytest.raises(rotation.RotationRefused):
            client.encode_supervised({'invalid': True})
        assert client.owner is factory.owners[0] and factory.owners[0].owned
        assert client.encode_supervised(request(2))['vector_bytes'] == b'v' * 1536
    finally:
        client.close(timeout=1)


def test_status_does_not_wait_for_an_inflight_call(fake_client):
    client = create(Factory())
    assert client._lock.acquire(blocking=False)
    try:
        assert client.status()['state'] == 'BUSY'
    finally:
        client._lock.release()


def test_call_and_shutdown_timeouts_remain_distinct(fake_client):
    client = create(Factory())
    try:
        assert client.call_timeout == 30
        assert client._timeout == 30 and client._shutdown_timeout == 1
        client.encode_supervised(request(1))
        assert client.active_client is not None
    finally:
        client.close(timeout=1)


def test_zero_shutdown_timeout_never_waits_and_new_owner_closes_cleanly(fake_client, monkeypatch):
    monkeypatch.setattr(rotation, '_BULK_NONCE_LIMIT', 1)
    factory = Factory(hold=True)
    client = rotation.RotatingGracefulE5Client(
        factory=factory, encoder_binding_sha256=BINDING, snapshot_sha256=SNAPSHOT,
        source_check=lambda: SNAPSHOT, shutdown_timeout=0, call_timeout=30,
        bulk_contract_sha256=BULK)
    assert client.status()['state'] == 'NEW' and client.status()['owned'] is False
    client.encode_supervised(request(1))
    with pytest.raises(rotation.RotationRefused):
        client.encode_supervised(request(2))
    assert len(factory.owners) == 1 and client.owner is factory.owners[0]
    empty = rotation.RotatingGracefulE5Client(
        factory=Factory(), encoder_binding_sha256=BINDING, snapshot_sha256=SNAPSHOT,
        source_check=lambda: SNAPSHOT, shutdown_timeout=0, call_timeout=30,
        bulk_contract_sha256=BULK)
    assert empty.close(timeout=0) == {'state': 'EXITED', 'owned': False, 'exit_code': 0}


def test_resource_replacement_in_frame_check_refuses_without_result(fake_client):
    factory = Factory()
    client = create(factory)
    def change_resource():
        client.owner.resources.job = object()
    with pytest.raises(rotation.RotationRefused):
        client.encode_supervised(request(1), frame_check=change_resource)
    assert len(factory.owners) == 1


def test_owner_conflicts_are_retained_and_no_second_worker_is_started(fake_client):
    owners = [FakeOwner(), FakeOwner()]
    def bad_factory(*, owner_sink):
        owner_sink(owners[0])
        owner_sink(owners[1])
    client = create(bad_factory)
    with pytest.raises(rotation.RotationRefused):
        client.encode_supervised(request(1))
    assert client.status()['state'] == 'HOLD'
    assert client.retained_owners == tuple(owners)
    with pytest.raises(rotation.RotationRefused):
        client.encode_supervised(request(2))


def test_interruption_after_sink_preserves_captured_owner(fake_client):
    owner = FakeOwner()
    def interrupted(*, owner_sink):
        owner_sink(owner)
        raise KeyboardInterrupt()
    client = create(interrupted)
    with pytest.raises(KeyboardInterrupt):
        client.encode_supervised(request(1))
    assert client.owner is owner and owner.owned
    assert client.status()['state'] == 'HOLD'


def test_rotation_uses_real_cooperative_pumps_and_protocol(monkeypatch):
    """Exercise the real owner/client on synthetic responding pipes, not a model."""
    from test_wd_mind_e5_client import response
    from test_wd_mind_e5_lifecycle import Job, Process
    from tools import wd_mind_e5_client as e5_client, wd_mind_e5_lifecycle as lifecycle

    budget = 79 + 2 * (79 + 1536)
    monkeypatch.setattr(rotation, 'BULK_STDOUT_BYTES', budget)
    monkeypatch.setattr(lifecycle, 'BULK_STDOUT_BYTES', budget)
    processes = []

    def factory(*, owner_sink):
        process = Process(response(0, 0))
        processes.append(process)

        class Input:
            def write(self, packet):
                size = int.from_bytes(packet[:4], 'little')
                body = json.loads(packet[4:].decode('ascii'))
                assert size == len(packet) - 4
                frame = response(body['id'], len(body['texts']),
                                 nonce=bytes.fromhex(body['nonce']),
                                 digest=bytes.fromhex(body['request_sha256']))
                with process.stdout.condition:
                    process.stdout.data.extend(frame)
                    process.stdout.condition.notify_all()
                return len(packet)

            def flush(self):
                pass

            def close(self):
                process.finish(0)

        process.stdin = Input()
        owner = lifecycle.reserve(enabled=True, bulk_contract_sha256=BULK)
        owner_sink(owner)
        owner.set_job(Job())
        owner.bind(process)
        client = e5_client.GracefulE5WorkerClient(owner, timeout=30,
            encoder_binding_sha256=BINDING, bulk_contract_sha256=BULK)
        owner.mark_ready()
        return {'status': 'READY', 'owner': owner, 'client': client,
                'bulk_contract_sha256': BULK}

    client = create(factory)
    checks = []
    try:
        for nonce in (1, 2, 3):
            result = client.encode_supervised(request(nonce), frame_check=lambda: checks.append(True),
                                              call_deadline=time.monotonic() + 10)
            assert len(result['vector_bytes']) == 1536
        assert len(processes) == 2
        assert len(checks) == 6
        assert processes[0].poll() == 0 and client.status()['state'] == 'RUNNING'
        assert client.close(timeout=2)['owned'] is False
    finally:
        for process in processes:
            process.finish(0)
        if client.owner is not None:
            client.close(timeout=2)
