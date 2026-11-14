"""Private-pipe client tests with fake model responses and no real process."""
import io
import hashlib
import json
import struct
import time
from types import SimpleNamespace

import pytest

from tools import wd_mind_e5_client as client


def response(request_id, count, *, vector=None, status=0, magic=b"ME5R", nonce=None, digest=None):
    vector = [1.0] + [0.0] * 383 if vector is None else vector
    nonce = bytes.fromhex("a" * 64) if nonce is None and request_id else (nonce or b"\0" * 32)
    digest = bytes.fromhex("b" * 64) if digest is None and request_id else (digest or b"\0" * 32)
    body = client._RESPONSE.pack(magic, request_id, status, count, nonce, digest)
    body += struct.pack("<" + "f" * len(vector), *vector) * count
    return struct.pack("<I", len(body)) + body


class Output(io.BytesIO):
    def flush(self): pass


class Job:
    def __init__(self): self.terminated = self.closed = False
    def terminate(self): self.terminated = True
    def close(self): self.closed = True


class Process:
    def __init__(self, responses): self.stdin, self.stdout = Output(), io.BytesIO(responses)
    def wait(self, timeout): return 0


def test_batches_exact_prefixed_inputs_and_returns_builtin_float32_vectors(monkeypatch):
    monkeypatch.setattr(client.E5WorkerClient, "_direct_binding", staticmethod(lambda *args: ("a" * 64, "b" * 64)))
    responses = response(0, 0) + response(1, 4) + response(2, 4) + response(3, 1) + response(4, 1)
    process, job = Process(responses), Job()
    subject = client.E5WorkerClient(process, job, timeout=2)
    values = subject.passage_encoder(tuple("passage: item " + str(i) for i in range(9)))
    query = subject.query_encoder("query: target")
    assert len(values) == 9 and all(type(row) is list and len(row) == 384 for row in values)
    assert type(query) is list and query[0] == 1.0
    raw = process.stdin.getvalue()
    packets = []
    while raw:
        size = struct.unpack("<I", raw[:4])[0]
        packets.append(json.loads(raw[4:4 + size]))
        raw = raw[4 + size:]
    assert [packet["id"] for packet in packets] == [1, 2, 3, 4]
    assert packets[0]["texts"][0] == "passage: item 0"
    assert packets[-1]["texts"] == ["query: target"]
    subject.close()
    assert job.terminated and job.closed


@pytest.mark.parametrize("bad", [b"", response(4, 0), response(0, 0, magic=b"BAD!"),
                                struct.pack("<I", 65537)])
def test_bad_handshake_refuses_and_terminates_job(bad):
    job = Job()
    with pytest.raises(client.E5ClientRefused, match="^mind_e5_worker_refused$"):
        client.E5WorkerClient(Process(bad), job, timeout=0.2)
    assert job.terminated and job.closed


@pytest.mark.parametrize("bad", [response(2, 1), response(1, 1, status=1), response(1, 1, vector=[0.0] * 384),
                                response(1, 1, vector=[float("nan")] * 384),
                                response(1, 1, nonce=b"\x01" * 32), response(1, 1, digest=b"\x02" * 32), b"truncated"])
def test_bad_response_never_returns_partial_vectors(bad, monkeypatch):
    monkeypatch.setattr(client.E5WorkerClient, "_direct_binding", staticmethod(lambda *args: ("a" * 64, "b" * 64)))
    job = Job()
    subject = client.E5WorkerClient(Process(response(0, 0) + bad), job, timeout=0.2)
    with pytest.raises(client.E5ClientRefused, match="^mind_e5_worker_refused$"):
        subject.query_encoder("query: private")
    assert job.terminated and job.closed


def test_blocked_read_hits_deadline_and_terminates_job():
    class Blocking:
        def read(self, size): time.sleep(0.1); return b""
    job = Job()
    process = SimpleNamespace(stdin=Output(), stdout=Blocking(), wait=lambda timeout: 0)
    start = time.monotonic()
    with pytest.raises(client.E5ClientRefused):
        client.E5WorkerClient(process, job, timeout=0.02)
    assert time.monotonic() - start < 1 and job.terminated and job.closed


def test_passage_batches_share_one_total_deadline(monkeypatch):
    class Delayed(io.BytesIO):
        def __init__(self, data): super().__init__(data); self.reads = 0
        def read(self, size):
            self.reads += 1
            if self.reads in (4, 6): time.sleep(0.05)
            return super().read(size)
    monkeypatch.setattr(client.E5WorkerClient, "_direct_binding", staticmethod(lambda *args: ("a" * 64, "b" * 64)))
    process = Process(b"")
    process.stdout = Delayed(response(0, 0) + response(1, 4) + response(2, 4))
    job = Job()
    subject = client.E5WorkerClient(process, job, timeout=0.08)
    with pytest.raises(client.E5ClientRefused):
        subject.passage_encoder(tuple("passage: item " + str(i) for i in range(8)))
    assert job.terminated and job.closed


def test_wrong_prefix_is_refused_without_echo(capsys):
    job = Job()
    subject = client.E5WorkerClient(Process(response(0, 0)), job, timeout=0.2)
    with pytest.raises(client.E5ClientRefused) as exc:
        subject.query_encoder("passage: synthetic-private")
    assert "synthetic-private" not in str(exc.value)
    assert capsys.readouterr() == ("", "") and job.terminated


def supervised(operation, nonce, *, binding="c" * 64, texts=None):
    request = {"schema": "wd.mind-supervised-e5-request.v1", "operation": operation,
               "nonce": nonce, "encoder_binding_sha256": binding}
    if operation == "refresh":
        request["passages"] = texts or ["passage: sample"]
    else:
        request["query"] = "query: sample"
    raw = json.dumps(request, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    request["request_sha256"] = hashlib.sha256(raw).hexdigest()
    return request


def test_supervised_batches_preserve_raw_bytes_order_and_bindings():
    refresh = supervised("refresh", "a" * 64, texts=[f"passage: {i}" for i in range(9)])
    query = supervised("query", "b" * 64)
    def frame(index, count, request, axis):
        vector = [0.0] * 384
        vector[axis] = 1.0
        return response(index, count, vector=vector, nonce=bytes.fromhex(request["nonce"]),
                        digest=bytes.fromhex(request["request_sha256"]))
    chunks = [frame(1, 4, refresh, 0), frame(2, 4, refresh, 1), frame(3, 1, refresh, 2)]
    process = Process(response(0, 0) + b"".join(chunks) + frame(4, 1, query, 3))
    subject = client.E5WorkerClient(process, Job(), timeout=2, encoder_binding_sha256="c" * 64)
    result = subject.encode_supervised(refresh)
    assert result == {"nonce": refresh["nonce"], "request_sha256": refresh["request_sha256"],
                      "vector_bytes": b"".join(chunk[4 + client._RESPONSE.size:] for chunk in chunks)}
    result = subject.encode_supervised(query)
    assert result["nonce"] == query["nonce"] and len(result["vector_bytes"]) == 384 * 4
    assert struct.unpack("<384f", result["vector_bytes"])[3] == 1.0
    raw = process.stdin.getvalue()
    packets = []
    while raw:
        size = struct.unpack("<I", raw[:4])[0]
        packets.append(json.loads(raw[4:4 + size]))
        raw = raw[4 + size:]
    assert [p["texts"] for p in packets[:3]] == [refresh["passages"][:4], refresh["passages"][4:8], refresh["passages"][8:]]
    assert all(p["nonce"] == refresh["nonce"] and p["request_sha256"] == refresh["request_sha256"] for p in packets[:3])
    subject.close()


@pytest.mark.parametrize("mutation", ["digest", "binding", "nonce", "reply_nonce", "reply_digest"])
def test_supervised_rejects_unbound_or_tampered_requests(mutation):
    request = supervised("query", "a" * 64)
    nonce = bytes.fromhex(request["nonce"])
    digest = bytes.fromhex(request["request_sha256"])
    if mutation == "digest": request["query"] = "query: tampered"
    if mutation == "binding": request["encoder_binding_sha256"] = "d" * 64
    if mutation == "nonce": request["nonce"] = "bad"
    if mutation == "reply_nonce": nonce = b"\x01" * 32
    if mutation == "reply_digest": digest = b"\x02" * 32
    job = Job()
    subject = client.E5WorkerClient(Process(response(0, 0) + response(1, 1, nonce=nonce, digest=digest)), job,
                                    timeout=1, encoder_binding_sha256="c" * 64)
    with pytest.raises(client.E5ClientRefused):
        subject.encode_supervised(request)
    assert job.terminated and job.closed


def test_supervised_replay_refuses_and_terminates():
    request = supervised("query", "a" * 64)
    job = Job()
    subject = client.E5WorkerClient(Process(response(0, 0) + response(1, 1, nonce=bytes.fromhex(request["nonce"]),
                                    digest=bytes.fromhex(request["request_sha256"]))), job,
                                    timeout=1, encoder_binding_sha256="c" * 64)
    subject.encode_supervised(request)
    with pytest.raises(client.E5ClientRefused):
        subject.encode_supervised(request)
    assert job.terminated and job.closed


def test_supervised_refresh_uses_one_deadline_across_batches():
    request = supervised("refresh", "a" * 64, texts=[f"passage: {i}" for i in range(8)])
    class Delayed(io.BytesIO):
        def __init__(self, data): super().__init__(data); self.reads = 0
        def read(self, size):
            self.reads += 1
            if self.reads in (4, 6): time.sleep(0.05)
            return super().read(size)
    nonce, digest = bytes.fromhex(request["nonce"]), bytes.fromhex(request["request_sha256"])
    process = Process(b"")
    process.stdout = Delayed(response(0, 0) + response(1, 4, nonce=nonce, digest=digest)
                             + response(2, 4, nonce=nonce, digest=digest))
    job = Job()
    subject = client.E5WorkerClient(process, job, timeout=0.08, encoder_binding_sha256="c" * 64)
    with pytest.raises(client.E5ClientRefused):
        subject.encode_supervised(request)
    assert job.terminated and job.closed
