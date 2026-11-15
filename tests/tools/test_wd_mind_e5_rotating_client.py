"""Synthetic tests for bounded cooperative E5 rotation; no model is loaded."""
import hashlib
import json
import struct

import pytest

from tools import wd_mind_e5_rotating_client as rotating

BULK_HASH = '61998bfeea1342988301f30f21d9781017249ef66761ec81874e5cc34633bc08'


class Owner:
    def __init__(self):
        self.closed = False

    def status(self):
        return {"state": "RUNNING", "owned": True}


class Client:
    def __init__(self, *, hold=False):
        self.owner = Owner()
        self._binding_sha256 = "b" * 64
        self._closed, self._next_id, self._used_nonces = False, 1, set()
        self.hold = hold
        self.requests = []
        self.close_calls = 0

    def encode_supervised(self, request):
        self.requests.append(request)
        return {"nonce": request["nonce"], "request_sha256": request["request_sha256"],
                "vector_bytes": b"\0" * (len(request["passages"]) * 1536)}

    def close(self, *, timeout):
        self.close_calls += 1
        self.owner.closed = True
        return {"state": "HOLD" if self.hold else "EXITED", "owned": self.hold,
                "exit_code": None if self.hold else 0}


def request(index, count=4):
    request = {"schema": "wd.mind-supervised-e5-request.v1", "operation": "refresh",
               "nonce": f"{index:064x}", "encoder_binding_sha256": "b" * 64,
               "passages": ["passage: synthetic"] * count}
    raw = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    request["request_sha256"] = hashlib.sha256(raw).hexdigest()
    return request


def create(monkeypatch, *, hold=False, source=None):
    clients = []
    monkeypatch.setattr(rotating, "GracefulE5WorkerClient", Client)
    def factory():
        clients.append(Client(hold=hold))
        return {"status": "READY", "client": clients[-1], "owner": clients[-1].owner}
    adapter = rotating.RotatingGracefulE5Client(factory=factory,
        encoder_binding_sha256="b" * 64, snapshot_sha256="a" * 64,
        source_check=source or (lambda: "a" * 64), shutdown_timeout=2)
    return adapter, clients


def create_bulk(monkeypatch, *, hold=False):
    clients = []
    monkeypatch.setattr(rotating, 'GracefulE5WorkerClient', Client)
    def factory():
        client = Client(hold=hold)
        client._bulk_contract_sha256 = BULK_HASH
        client.owner._bulk_contract_sha256 = BULK_HASH
        clients.append(client)
        return {'status':'READY','client':client,'owner':client.owner,'bulk_contract_sha256':BULK_HASH}
    return rotating.RotatingGracefulE5Client(factory=factory,encoder_binding_sha256='b'*64,
        snapshot_sha256='a'*64,source_check=lambda:'a'*64,shutdown_timeout=2,
        bulk_contract_sha256=BULK_HASH),clients


def test_bulk_extends_worker_budget_without_losing_replay_guard(monkeypatch):
    adapter, clients = create_bulk(monkeypatch)
    for index in range(1, 170): adapter.encode_supervised(request(index))
    assert len(clients) == 1 and len(clients[0].requests) == 169
    with pytest.raises(ValueError): adapter.encode_supervised(request(1))
    assert clients[0].owner.closed


def test_bulk_rotation_hold_never_starts_a_replacement(monkeypatch):
    adapter, clients = create_bulk(monkeypatch, hold=True)
    adapter._request_limit = 2
    adapter.encode_supervised(request(1)); adapter.encode_supervised(request(2))
    with pytest.raises(ValueError): adapter.encode_supervised(request(3))
    assert len(clients) == 1 and adapter.owner is clients[0].owner and clients[0].close_calls == 1


def test_bulk_rejects_worker_without_matching_contract(monkeypatch):
    monkeypatch.setattr(rotating, 'GracefulE5WorkerClient', Client)
    client = Client()
    adapter = rotating.RotatingGracefulE5Client(factory=lambda:{'status':'READY','client':client,'owner':client.owner},
        encoder_binding_sha256='b'*64,snapshot_sha256='a'*64,source_check=lambda:'a'*64,shutdown_timeout=2,
        bulk_contract_sha256=BULK_HASH)
    with pytest.raises(ValueError): adapter.encode_supervised(request(1))
    assert client.requests == []


def test_rotate_before_budget_supports_more_than_one_thousand_calls(monkeypatch):
    adapter, clients = create(monkeypatch)
    for index in range(1, 1026):
        adapter.encode_supervised(request(index, 4))
    assert len(clients) > 1
    assert all(c.owner.closed for c in clients[:-1])
    assert all(len(c.requests) <= 168 for c in clients)
    assert adapter.close()["state"] == "EXITED"


def test_replay_of_request_after_worker_rotation_is_refused(monkeypatch):
    adapter, clients = create(monkeypatch)
    first = request(1, 256)
    adapter.encode_supervised(first)
    adapter.encode_supervised(request(2, 256))
    with pytest.raises(ValueError):
        adapter.encode_supervised(first)
    assert len(clients) == 1
    assert clients[0].owner.closed


def test_previous_worker_nonce_is_rejected_after_replacement_is_running(monkeypatch):
    adapter, clients = create(monkeypatch)
    first = request(1, 256)
    adapter.encode_supervised(first)
    adapter.encode_supervised(request(2, 256))
    adapter.encode_supervised(request(3, 256))
    assert len(clients) == 2
    with pytest.raises(ValueError):
        adapter.encode_supervised(first)
    assert len(clients) == 2
    assert all(client.owner.closed for client in clients)


def test_session_nonce_guard_fails_closed_at_budget_before_opening_new_worker(monkeypatch):
    monkeypatch.setattr(rotating, "_MAX_SESSION_NONCES", 2, raising=False)
    adapter, clients = create(monkeypatch)
    adapter.encode_supervised(request(1, 256))
    adapter.encode_supervised(request(2, 256))
    with pytest.raises(ValueError):
        adapter.encode_supervised(request(3, 256))
    assert len(clients) == 1
    assert clients[0].owner.closed


def test_hold_preserves_owner_and_forbids_replacement(monkeypatch):
    adapter, clients = create(monkeypatch, hold=True)
    for index in range(1, 169):
        adapter.encode_supervised(request(index, 4))
    with pytest.raises(ValueError):
        adapter.encode_supervised(request(169, 4))
    assert len(clients) == 1 and clients[0].owner.closed and clients[0].close_calls == 1
    assert adapter.owner is clients[0].owner


def test_source_change_rejects_result_and_requests_eof(monkeypatch):
    calls = iter(["a" * 64, "c" * 64])
    adapter, clients = create(monkeypatch, source=lambda: next(calls))
    with pytest.raises(ValueError):
        adapter.encode_supervised(request(1))
    assert len(clients) == 1 and clients[0].owner.closed


def test_wrong_encoder_binding_never_passes_data(monkeypatch):
    adapter, clients = create(monkeypatch)
    req = request(1)
    req["encoder_binding_sha256"] = "c" * 64
    with pytest.raises(ValueError):
        adapter.encode_supervised(req)
    assert not clients


def test_oversized_text_refuses_before_worker_is_started(monkeypatch):
    adapter, clients = create(monkeypatch)
    req = request(1)
    req["passages"] = ["passage: " + "x" * 2010]
    with pytest.raises(ValueError):
        adapter.encode_supervised(req)
    assert not clients


def test_untrusted_client_is_never_closed_or_used(monkeypatch):
    class UnsafeClient:
        def close(self, **kwargs):
            pytest.fail("untrusted close could terminate a process")

    owner = Owner()
    adapter = rotating.RotatingGracefulE5Client(
        factory=lambda: {"status": "READY", "client": UnsafeClient(), "owner": owner},
        encoder_binding_sha256="b" * 64, snapshot_sha256="a" * 64,
        source_check=lambda: "a" * 64, shutdown_timeout=2)
    with pytest.raises(ValueError):
        adapter.encode_supervised(request(1))
    assert adapter.owner is owner


def test_real_cooperative_codec_and_owner_rotate_before_total_stdout_limit():
    from test_wd_mind_e5_lifecycle import Process, bind
    from test_wd_mind_e5_client import response
    from tools.wd_mind_e5_client import GracefulE5WorkerClient

    owners = []
    processes = []

    def factory():
        process = Process(response(0, 0))
        original_write = process.stdin.write

        def write(packet):
            written = original_write(packet)
            length = struct.unpack("<I", packet[:4])[0]
            data = json.loads(packet[4:4 + length])
            frame = response(data["id"], len(data["texts"]), nonce=bytes.fromhex(data["nonce"]),
                             digest=bytes.fromhex(data["request_sha256"]))
            with process.stdout.condition:
                process.stdout.data.extend(frame)
                process.stdout.condition.notify_all()
            return written

        process.stdin.write = write
        owner, _ = bind(process)
        client = GracefulE5WorkerClient(owner, timeout=5, encoder_binding_sha256="b" * 64)
        owner.mark_ready()
        owners.append(owner)
        processes.append(process)
        return {"status": "READY", "client": client, "owner": owner}

    adapter = rotating.RotatingGracefulE5Client(factory=factory,
        encoder_binding_sha256="b" * 64, snapshot_sha256="a" * 64,
        source_check=lambda: "a" * 64, shutdown_timeout=2)
    try:
        for index in range(1, 170):
            assert len(adapter.encode_supervised(request(index, 4))["vector_bytes"]) == 4 * 1536
        assert len(owners) == 2
        assert owners[0].status()["state"] == "EXITED"
        assert owners[0].status()["owned"] is False
        assert processes[0].stdin.closed
        assert adapter.close()["state"] == "EXITED"
    finally:
        for process, owner in zip(processes, owners):
            process.finish()
            if owner.status()["owned"]:
                owner.shutdown(timeout=2)


def test_bulk_real_cooperative_codec_keeps_worker_past_legacy_budget():
    from test_wd_mind_e5_lifecycle import Process, Job
    from test_wd_mind_e5_client import response
    from tools.wd_mind_e5_client import GracefulE5WorkerClient
    from tools import wd_mind_e5_lifecycle as lifecycle
    owners, processes = [], []
    def factory():
        process = Process(response(0, 0))
        original_write = process.stdin.write
        def write(packet):
            written = original_write(packet)
            data = json.loads(packet[4:4+struct.unpack('<I',packet[:4])[0]])
            frame = response(data['id'],len(data['texts']),nonce=bytes.fromhex(data['nonce']),
                             digest=bytes.fromhex(data['request_sha256']))
            with process.stdout.condition:
                process.stdout.data.extend(frame); process.stdout.condition.notify_all()
            return written
        process.stdin.write = write
        owner = lifecycle.reserve(enabled=True,bulk_contract_sha256=BULK_HASH)
        owner.set_job(Job()); owner.bind(process)
        client = GracefulE5WorkerClient(owner, timeout=5, encoder_binding_sha256='b'*64,
                                        bulk_contract_sha256=BULK_HASH)
        owner.mark_ready(); owners.append(owner); processes.append(process)
        return {'status':'READY','client':client,'owner':owner,'bulk_contract_sha256':BULK_HASH}
    adapter = rotating.RotatingGracefulE5Client(factory=factory,encoder_binding_sha256='b'*64,
        snapshot_sha256='a'*64,source_check=lambda:'a'*64,shutdown_timeout=2,bulk_contract_sha256=BULK_HASH)
    try:
        for index in range(1, 170):
            assert len(adapter.encode_supervised(request(index))['vector_bytes']) == 4*1536
        assert len(owners) == 1 and owners[0].status()['state'] == 'RUNNING'
        assert adapter.close()['state'] == 'EXITED'
    finally:
        for process, owner in zip(processes,owners):
            process.finish()
            if owner.status()['owned']: owner.shutdown(timeout=2)


def test_opt_in_build_fence_receives_immutable_request_before_and_after(monkeypatch):
    monkeypatch.setattr(rotating, '_validate_build_fence', lambda *args:tuple((str(i),'passage: synthetic') for i in range(16)))  # Synthetic transport seam.
    adapter, clients = create(monkeypatch)
    observations = []
    def fence(phase, payload): observations.append((phase, payload))
    adapter.arm_build_fence(enabled=True, source_check=lambda:'a'*64, request_fence=fence)
    req = request(1)
    adapter.encode_supervised(req)
    assert [item[0] for item in observations] == ['before','after']
    assert observations[0][1] == observations[1][1]
    assert json.loads(observations[0][1]) == req
    assert adapter.disarm_build_fence(enabled=True) is None
    assert adapter.close()['state'] == 'EXITED'


def test_opt_in_build_fence_refuses_client_mutation(monkeypatch):
    monkeypatch.setattr(rotating, '_validate_build_fence', lambda *args:tuple((str(i),'passage: synthetic') for i in range(16)))  # Synthetic transport seam.
    adapter, clients = create(monkeypatch)
    original=Client.encode_supervised
    def mutate(self, req):
        result=original(self, req)
        req['passages'][0]='passage: tampered'
        return result
    monkeypatch.setattr(Client,'encode_supervised',mutate)
    adapter.arm_build_fence(enabled=True, source_check=lambda:'a'*64, request_fence=lambda phase, payload:None)
    with pytest.raises(ValueError): adapter.encode_supervised(request(1))
    assert clients[0].owner.closed


def test_build_fence_is_default_off_and_cannot_arm_after_first_request(monkeypatch):
    adapter, clients = create(monkeypatch)
    with pytest.raises(ValueError): adapter.arm_build_fence(source_check=lambda:'a'*64, request_fence=lambda *args:None)
    adapter.encode_supervised(request(1))
    with pytest.raises(ValueError): adapter.arm_build_fence(enabled=True, source_check=lambda:'a'*64, request_fence=lambda *args:None)
    adapter.close()


def test_build_fence_after_refusal_closes_owner_and_no_result(monkeypatch):
    monkeypatch.setattr(rotating, '_validate_build_fence', lambda *args:tuple((str(i),'passage: synthetic') for i in range(16)))  # Synthetic transport seam.
    adapter, clients=create(monkeypatch)
    def observer(phase, payload):
        if phase == 'after': raise ValueError('synthetic fence failure')
    adapter.arm_build_fence(enabled=True,source_check=lambda:'a'*64,request_fence=observer)
    with pytest.raises(ValueError): adapter.encode_supervised(request(1))
    assert len(clients)==1 and clients[0].owner.closed


def test_build_fence_disarm_restores_original_source_check(monkeypatch):
    monkeypatch.setattr(rotating, '_validate_build_fence', lambda *args:tuple((str(i),'passage: synthetic') for i in range(16)))  # Synthetic transport seam.
    calls=[]
    adapter, clients=create(monkeypatch,source=lambda:calls.append('original') or 'a'*64)
    adapter.arm_build_fence(enabled=True,source_check=lambda:'a'*64,request_fence=lambda *args:None)
    adapter.encode_supervised(request(1))
    assert not calls
    adapter.disarm_build_fence(enabled=True)
    adapter.encode_supervised(request(2))
    assert len(calls)>=2
    assert adapter.close()['state']=='EXITED'


def test_build_fence_replay_refuses_before_observer_called_again(monkeypatch):
    monkeypatch.setattr(rotating, '_validate_build_fence', lambda *args:tuple((str(i),'passage: synthetic') for i in range(16)))  # Synthetic transport seam.
    adapter, clients=create(monkeypatch)
    calls=[]
    adapter.arm_build_fence(enabled=True,source_check=lambda:'a'*64,request_fence=lambda phase,raw:calls.append(phase))
    first=request(1);adapter.encode_supervised(first)
    with pytest.raises(ValueError): adapter.encode_supervised(first)
    assert calls == ['before','after']


def test_constant_source_callback_cannot_arm_fast_fence(monkeypatch):
    adapter, clients=create(monkeypatch)
    with pytest.raises(ValueError):
        adapter.arm_build_fence(enabled=True, source_check=lambda:'a'*64, request_fence=lambda *args:None)
    assert clients == []


def test_duck_fence_cannot_arm_fast_fence(monkeypatch):
    adapter, clients=create(monkeypatch)
    class FakeFence:
        def check(self): return 'a'*64
        def request(self,*args): pass
    fake=FakeFence()
    with pytest.raises(ValueError):
        adapter.arm_build_fence(enabled=True, source_check=fake.check, request_fence=fake.request)
    assert clients == []


def test_exact_fence_with_mismatched_owner_cannot_arm(monkeypatch):
    from tools import wd_mind_full_runtime as runtime
    adapter, clients=create(monkeypatch)
    fence=object.__new__(runtime._FastBuildFence)
    fence.handler=object()
    with pytest.raises(ValueError):
        adapter.arm_build_fence(enabled=True, source_check=fence.check, request_fence=fence.request)
    assert clients == []
