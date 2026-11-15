import hashlib
import importlib
import json
import math
import struct

import pytest


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _bindings():
    encoder = _json({'schema': 'wd.mind-readonly-encoder-binding.v1',
                     'encoder': {'model': 'intfloat/multilingual-e5-small', 'revision': 'synthetic',
                                 'weights_sha256': 'a' * 64},
                     'code_sha256': 'b' * 64,
                     'query_policy': {'prefix': 'query: ', 'truncate_chars': 2000,
                                      'normalize': True, 'dtype': 'float32', 'dim': 384}})
    index = _json({'schema': 'wd.mind-readonly-index-binding.v1', 'index_type': 'IndexFlatIP',
                   'metric_type': 0, 'code_sha256': 'c' * 64})
    return {'encoder_binding_bytes': encoder, 'encoder_binding_sha256': hashlib.sha256(encoder).hexdigest(),
            'index_binding_bytes': index, 'index_binding_sha256': hashlib.sha256(index).hexdigest()}


ROW = struct.pack('<384f', 1.0, -0.0, *([0.0] * 382))


def _passage_row(text):
    suffix = text.rsplit(' ', 1)[-1]
    vector = [0.0] * 384
    vector[int(suffix) if suffix.isascii() and suffix.isdigit() else 0] = 1.0
    vector[-1] = -0.0
    return struct.pack('<384f', *vector)


class FakeClient:
    def __init__(self, fault=None):
        self.requests = []
        self.fault = fault

    def encode_supervised(self, request):
        self.requests.append(request)
        unsigned = {k: v for k, v in request.items() if k != 'request_sha256'}
        raw = json.dumps(unsigned, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        assert request['request_sha256'] == hashlib.sha256(raw).hexdigest()
        assert request['schema'] == 'wd.mind-supervised-e5-request.v1'
        assert request['encoder_binding_sha256'] == _bindings()['encoder_binding_sha256']
        if self.fault == 'error' and len(self.requests) == 2:
            raise RuntimeError('synthetic private error')
        data = (b''.join(_passage_row(text) for text in request['passages'])
                if request['operation'] == 'refresh' else ROW)
        if self.fault == 'partial': data = data[:-1]
        if self.fault == 'nan': data = struct.pack('<f', math.nan) + data[4:]
        if self.fault == 'inf': data = struct.pack('<f', math.inf) + data[4:]
        if self.fault == 'norm': data = struct.pack('<f', 0.5) + data[4:]
        return {'nonce': '0' * 64 if self.fault == 'nonce' else request['nonce'],
                'request_sha256': '0' * 64 if self.fault == 'digest' else request['request_sha256'],
                'vector_bytes': data}


def _adapter(client=None, **kwargs):
    module = importlib.import_module('tools.wd_mind_e5_snapshot_adapter')
    return module.create_adapter(enabled=True, client=client or FakeClient(), **(_bindings() | kwargs))


@pytest.mark.parametrize('count', [1, 4, 5, 16])
def test_passages_batches_preserve_order_and_float32_bits(count):
    client = FakeClient()
    adapter = _adapter(client)
    texts = tuple(f'passage: synthetic {i}' for i in range(count))
    assert adapter.passage(texts) == b''.join(_passage_row(text) for text in texts)
    assert [text for request in client.requests for text in request['passages']] == list(texts)
    assert all(1 <= len(r['passages']) <= 4 for r in client.requests)
    assert len({r['nonce'] for r in client.requests}) == len(client.requests)


def test_query_adds_exactly_one_protocol_prefix_preserving_user_input():
    client = FakeClient()
    adapter = _adapter(client)
    text = 'query: ' + 'synthetic ' * 220
    values = adapter.query(text)
    assert client.requests[0]['query'] == 'query: ' + text[:2000]
    assert struct.pack('<384f', *values) == ROW


@pytest.mark.parametrize('fault', ['partial', 'nan', 'inf', 'norm', 'nonce', 'digest', 'error'])
def test_invalid_response_and_partial_failure_refuse(fault):
    with pytest.raises(ValueError):
        _adapter(FakeClient(fault)).passage(tuple('passage: synthetic' for _ in range(5)))


@pytest.mark.parametrize('bad', [(), tuple('passage: x' for _ in range(17)), ('x',),
                                 ('passage: ',), ('passage: bad\x00text',), (123,)])
def test_bad_input_refuses_before_client(bad):
    client = FakeClient()
    with pytest.raises(ValueError): _adapter(client).passage(bad)
    assert not client.requests


def test_default_off_and_missing_or_changed_binding_refuse():
    module = importlib.import_module('tools.wd_mind_e5_snapshot_adapter')
    client = FakeClient()
    with pytest.raises(ValueError): module.create_adapter(client=client, **_bindings())
    with pytest.raises(ValueError): module.create_adapter(enabled=True, client=client)
    with pytest.raises(ValueError): _adapter(client, encoder_binding_sha256='0' * 64)
    assert not client.requests
