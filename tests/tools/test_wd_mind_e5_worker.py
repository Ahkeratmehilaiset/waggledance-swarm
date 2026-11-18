"""Synthetic E5 worker protocol tests; no actual model is loaded."""
import io
import json
import math
import struct
from types import SimpleNamespace

import pytest

from tools import wd_mind_e5_worker as worker

BULK_HASH = '61998bfeea1342988301f30f21d9781017249ef66761ec81874e5cc34633bc08'


def test_bulk_contract_is_exact_and_default_off():
    assert worker.BULK_CONTRACT_SHA256 == BULK_HASH
    assert worker._nonce_budget(None) == 1024
    assert worker._nonce_budget(BULK_HASH) == 8192
    for value in (True, 1, '', '0' * 64):
        with pytest.raises(ValueError):
            worker._nonce_budget(value)


def test_bulk_nonce_budget_and_replay_refuse_before_next_model_call(monkeypatch):
    monkeypatch.setattr(worker, '_nonce_budget', lambda value: 2)
    base = {'op':'query','texts':['query: synthetic']}
    data = b''.join(request({**base,'id':n,'nonce':f'{n:064x}'}) for n in range(1,4))
    model = FakeModel()
    with pytest.raises(ValueError, match='request_replay'):
        worker.serve(io.BytesIO(data), io.BytesIO(), model, bulk_contract_sha256=BULK_HASH)
    assert len(model.calls) == 2
    replay = request({**base,'id':1,'nonce':'1'*64}) + request({**base,'id':2,'nonce':'2'*64})
    replay += request({**base,'id':3,'nonce':'1'*64})
    model = FakeModel()
    with pytest.raises(ValueError, match='request_replay'):
        worker.serve(io.BytesIO(replay), io.BytesIO(), model, bulk_contract_sha256=BULK_HASH)
    assert len(model.calls) == 2


def request(value):
    value = {"nonce": "a" * 64, "request_sha256": "b" * 64, **value}
    raw = json.dumps(value, separators=(",", ":")).encode()
    return struct.pack("<I", len(raw)) + raw


class FakeModel:
    def __init__(self): self.calls = []
    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        return FakeMatrix([[1.0] + [0.0] * 383 for _ in texts])


class FakeMatrix(list):
    def __init__(self, rows, *, dtype="float32", shape=None):
        super().__init__(rows)
        self.dtype = SimpleNamespace(name=dtype, kind="f", itemsize=4 if dtype == "float32" else 8)
        self.shape = (len(rows), len(rows[0])) if shape is None and rows else shape


def frames(raw):
    result = []
    while raw:
        size = struct.unpack("<I", raw[:4])[0]
        result.append(raw[4:4 + size])
        raw = raw[4 + size:]
    return result


def test_prefixed_passages_and_query_are_encoded_once_as_float32():
    model, out = FakeModel(), io.BytesIO()
    data = request({"id": 1, "op": "passage", "texts": ["passage: synthetic work", "passage: another"]})
    data += request({"id": 2, "op": "query", "texts": ["query: synthetic question"]})
    assert worker.serve(io.BytesIO(data), out, model) == 0
    result = frames(out.getvalue())
    assert worker._RESPONSE.unpack(result[0]) == (b"ME5R", 0, 0, 0, b"\0" * 32, b"\0" * 32)
    assert worker._RESPONSE.unpack(result[1][:worker._RESPONSE.size]) == (b"ME5R", 1, 0, 2, b"\xaa" * 32, b"\xbb" * 32)
    assert worker._RESPONSE.unpack(result[2][:worker._RESPONSE.size]) == (b"ME5R", 2, 0, 1, b"\xaa" * 32, b"\xbb" * 32)
    assert struct.unpack("<384f", result[2][worker._RESPONSE.size:])[0] == 1.0
    assert model.calls[0][0] == ["passage: synthetic work", "passage: another"]
    assert model.calls[1][0] == ["query: synthetic question"]
    assert all(call[1]["normalize_embeddings"] for call in model.calls)
    assert b"synthetic" not in out.getvalue()


@pytest.mark.parametrize("value", [
    {"id": 0, "op": "query", "texts": ["query: text"]},
    {"id": 1, "op": "query", "texts": ["passage: text"]},
    {"id": 1, "op": "query", "texts": ["query: "]},
    {"id": 1, "op": "query", "texts": ["query: x", "query: y"]},
    {"id": 1, "op": "passage", "texts": ["passage: x"] * 5},
    {"id": 1, "op": "passage", "texts": ["passage: x"], "extra": "private"},
])
def test_invalid_requests_refuse_before_encoding(value):
    model = FakeModel()
    with pytest.raises(ValueError):
        worker.serve(io.BytesIO(request(value)), io.BytesIO(), model)
    assert not model.calls


def test_duplicate_request_id_and_duplicate_json_keys_refuse():
    valid = {"id": 1, "op": "query", "texts": ["query: text"]}
    with pytest.raises(ValueError, match="request_order"):
        worker.serve(io.BytesIO(request(valid) + request(valid)), io.BytesIO(), FakeModel())
    raw = b'{"id":1,"id":2,"op":"query","texts":["query: x"]}'
    with pytest.raises(ValueError, match="duplicate_key"):
        worker.serve(io.BytesIO(struct.pack("<I", len(raw)) + raw), io.BytesIO(), FakeModel())


def test_nonce_replay_and_changed_digest_refuse_before_additional_encoding():
    base = {"op": "query", "texts": ["query: text"]}
    model = FakeModel()
    data = request({**base, "id": 1}) + request({**base, "id": 2, "request_sha256": "c" * 64})
    with pytest.raises(ValueError, match="request_binding_changed"):
        worker.serve(io.BytesIO(data), io.BytesIO(), model)
    assert len(model.calls) == 1
    model = FakeModel()
    data = request({**base, "id": 1}) + request({**base, "id": 2, "nonce": "d" * 64})
    data += request({**base, "id": 3, "nonce": "a" * 64})
    with pytest.raises(ValueError, match="request_replay"):
        worker.serve(io.BytesIO(data), io.BytesIO(), model)
    assert len(model.calls) == 2


@pytest.mark.parametrize("row", [[0.0] * 384, [float("nan")] * 384, [1.0] * 383])
def test_invalid_vectors_refuse(row):
    class BadModel:
        def encode(self, *args, **kwargs): return FakeMatrix([row])
    with pytest.raises((ValueError, struct.error, OverflowError)):
        worker._vectors(BadModel(), ["query: text"])


@pytest.mark.parametrize("fault", ["float64", "wrong_shape", "lossy_float32"])
def test_wrong_model_dtype_shape_or_lossy_float32_refuses(fault):
    class BadModel:
        def encode(self, *args, **kwargs):
            row = [1.0] + [0.0] * 383
            if fault == "lossy_float32": row = [0.999999999] + [0.0] * 383
            return FakeMatrix([row], dtype="float64" if fault == "float64" else "float32",
                              shape=(1, 383) if fault == "wrong_shape" else None)
    with pytest.raises(ValueError):
        worker._vectors(BadModel(), ["query: text"])


def test_wrong_weights_refuse_before_model_import(tmp_path, monkeypatch):
    (tmp_path / "model.safetensors").write_bytes(b"synthetic-weights")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    assert worker.main(model_dir=tmp_path, weights_sha256="0" * 64, generation="a" * 40) == 21


def _start_inputs(tmp_path, monkeypatch, *, offline=True, slot=True, load=None):
    """Synthetic start inputs that pass every check up to the chosen stage; no real library is imported.
    ``load`` is None (model object returned), "missing" (the import fails) or an exception to raise at load."""
    import hashlib
    import sys
    import types
    payload = b"synthetic-weights"
    (tmp_path / "model.safetensors").write_bytes(payload)
    generation = "a" * 40
    monkeypatch.setenv("HF_HUB_OFFLINE", "1" if offline else "0")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    if slot:
        monkeypatch.setitem(sys.modules, "sandbox.mind.step2.reader_slot",
                            SimpleNamespace(_generation=generation, reader_slot=lambda *a, **k: None))
    else:
        monkeypatch.delitem(sys.modules, "sandbox.mind.step2.reader_slot", raising=False)
    module = types.ModuleType("sentence_transformers")
    if load is None:
        module.SentenceTransformer = lambda *a, **k: object()
    elif load != "missing":
        def failing(*a, **k):
            raise load
        module.SentenceTransformer = failing
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return dict(model_dir=tmp_path, weights_sha256=hashlib.sha256(payload).hexdigest(), generation=generation)


def _raiser(error):
    def raising(*a, **k):
        raise error
    return raising


def test_stage_table_is_closed_distinct_and_never_the_generic_code():
    codes = worker.STAGE_EXIT_CODES
    assert list(codes) == ["arguments", "model_pin", "offline_environment", "reader_mutex", "library_import",
                           "model_load", "serving"]
    assert list(codes.values()) == [20, 21, 22, 23, 24, 25, 29]
    assert not {0, 1, 2} & set(codes.values())      # 2 stays what the bootstrap or the interpreter returns


@pytest.mark.parametrize("fault, expected", [
    ("arguments_identity", 20), ("arguments_relative_path", 20), ("arguments_bulk_contract", 20),
    ("model_pin_missing_file", 21), ("offline_environment", 22), ("reader_mutex", 23),
    ("library_import", 24), ("model_load", 25), ("model_load_system_exit", 25),
    ("model_load_keyboard_interrupt", 25),
])
def test_each_start_stage_leaves_its_own_code_and_no_output(tmp_path, monkeypatch, capfd, fault, expected):
    load = {"library_import": "missing", "model_load": OSError("synthetic secret detail"),
            "model_load_system_exit": SystemExit("synthetic secret detail"),
            "model_load_keyboard_interrupt": KeyboardInterrupt()}.get(fault)
    options = _start_inputs(tmp_path, monkeypatch, offline=fault != "offline_environment",
                            slot=fault != "reader_mutex", load=load)
    if fault == "arguments_identity":
        options["generation"] = "not-a-generation"
    elif fault == "arguments_relative_path":
        options["model_dir"] = "relative"
    elif fault == "arguments_bulk_contract":
        options["bulk_contract_sha256"] = "0" * 64
    elif fault == "model_pin_missing_file":
        (tmp_path / "model.safetensors").unlink()
    assert worker.main(**options) == expected
    captured = capfd.readouterr()
    assert captured.out == "" and captured.err == ""        # no text from the failure, not even its type


@pytest.mark.parametrize("error", [ValueError("request_shape"), SystemExit(0), SystemExit("synthetic secret detail"),
                                   KeyboardInterrupt()])
def test_failure_after_model_load_has_its_own_code_and_no_output(tmp_path, monkeypatch, capfd, error):
    options = _start_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "serve", _raiser(error))
    assert worker.main(**options) == 29
    captured = capfd.readouterr()
    assert captured.out == "" and captured.err == ""


def test_successful_start_returns_what_serve_returns(tmp_path, monkeypatch):
    options = _start_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "serve", lambda *a, **k: 0)
    assert worker.main(**options) == 0
