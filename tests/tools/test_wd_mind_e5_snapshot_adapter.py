import hashlib
import importlib
import json
import math
import struct
from pathlib import Path

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


def _runtime_binding(**changes):
    query = json.loads(_bindings()['encoder_binding_bytes'])
    policy = query['query_policy']
    runtime = {'schema': 'wd.mind-memory-encoder-binding.v1', 'encoder': query['encoder'],
               'code_sha256': query['code_sha256'], 'query_policy': policy,
               'passage_policy': dict(policy, prefix='passage: ')}
    runtime.update(changes)
    raw = _json(runtime)
    return {'runtime_binding_bytes': raw, 'runtime_binding_sha256': hashlib.sha256(raw).hexdigest()}


def test_separate_bindings_work_with_real_graceful_protocol(monkeypatch):
    from test_wd_mind_e5_lifecycle import Process, bind, finish
    from test_wd_mind_e5_client import response, supervised
    from tools.wd_mind_e5_client import GracefulE5WorkerClient
    from tools import wd_mind_e5_snapshot_adapter as module
    runtime = _runtime_binding()
    nonce = 'd' * 64
    req = supervised('query', nonce, binding=runtime['runtime_binding_sha256'])
    frame = response(1, 1, nonce=bytes.fromhex(nonce), digest=bytes.fromhex(req['request_sha256']))
    process = Process(response(0, 0) + frame)
    owner, _ = bind(process)
    try:
        client = GracefulE5WorkerClient(owner, timeout=1, encoder_binding_sha256=runtime['runtime_binding_sha256'])
        monkeypatch.setattr(module.secrets, 'token_hex', lambda n: nonce)
        adapter = module.create_adapter(enabled=True, client=client, **_bindings(), **runtime)
        assert adapter.query('sample')[0] == 1.0
        assert client.close(timeout=2)['state'] == 'EXITED'
    finally:
        finish(owner, process)


@pytest.mark.parametrize('fault', ['model', 'revision', 'weights', 'code', 'query', 'passage', 'schema', 'digest', 'missing'])
def test_runtime_and_query_bindings_must_match_before_client(fault):
    bindings = _bindings()
    data = json.loads(_runtime_binding()['runtime_binding_bytes'])
    if fault == 'model': data['encoder']['model'] = 'other'
    if fault == 'revision': data['encoder']['revision'] = 'other'
    if fault == 'weights': data['encoder']['weights_sha256'] = 'f' * 64
    if fault == 'code': data['code_sha256'] = 'f' * 64
    if fault == 'query': data['query_policy']['truncate_chars'] = 100
    if fault == 'passage': data['passage_policy']['prefix'] = 'query: '
    if fault == 'schema': data['schema'] = 'wrong'
    raw = _json(data)
    runtime = {'runtime_binding_bytes': raw, 'runtime_binding_sha256': hashlib.sha256(raw).hexdigest()}
    if fault == 'digest': runtime['runtime_binding_sha256'] = '0' * 64
    if fault == 'missing': runtime['runtime_binding_bytes'] = None
    client = FakeClient()
    with pytest.raises(ValueError):
        importlib.import_module('tools.wd_mind_e5_snapshot_adapter').create_adapter(
            enabled=True, client=client, **bindings, **runtime)
    assert not client.requests


@pytest.fixture
def flat_bundle(tmp_path, monkeypatch):
    from tools import (wd_mind_e5_snapshot_adapter as adapter, wd_mind_content_query_readonly as reader,
        wd_mind_absent_generation_prefix_readonly as prefix, wd_mind_e5_rotating_client as rotating,
        wd_mind_e5_lifecycle as lifecycle, wd_mind_e5_client as client_module)
    bundle = tmp_path / 'bundle'; bundle.mkdir()
    files = {}
    for module in (adapter,reader,prefix,rotating,lifecycle,client_module):
        relative = 'tools-bootstrap/tools/' + Path(module.__file__).name
        path = bundle / relative; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(Path(module.__file__).read_bytes())
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        monkeypatch.setattr(module,'__file__',str(path))
    worker = bundle/'tools-bootstrap/tools/wd_mind_e5_worker.py'
    worker.write_bytes(b'synthetic pinned worker only; no native code')
    files['tools-bootstrap/tools/wd_mind_e5_worker.py'] = hashlib.sha256(worker.read_bytes()).hexdigest()
    data = json.loads(_runtime_binding()['runtime_binding_bytes'])
    data['code_sha256'] = files['tools-bootstrap/tools/wd_mind_e5_worker.py']
    raw = _json(data); path=bundle/'wd-mind-e5-encoder-binding.json';path.write_bytes(raw)
    files[path.name]=hashlib.sha256(raw).hexdigest()
    manifest=_json({'schema_version':1,'files':files}); (bundle/'deployment-manifest.json').write_bytes(manifest)
    class SyntheticBulkRotator:
        def __init__(self):
            self._binding=hashlib.sha256(raw).hexdigest()
            self._bulk_contract_sha256=lifecycle.BULK_CONTRACT_SHA256
            self._failed=self._closed=False
            self.calls=[]
        def encode_supervised(self, request):
            self.calls.append(request)
            assert request['encoder_binding_sha256'] == self._binding
            assert request['request_sha256'] == hashlib.sha256(_json(
                {k:v for k,v in request.items() if k != 'request_sha256'})).hexdigest()
            data = (b''.join(_passage_row(text) for text in request['passages'])
                    if request['operation'] == 'refresh' else ROW)
            return {'nonce':request['nonce'],'request_sha256':request['request_sha256'],'vector_bytes':data}
    monkeypatch.setattr(rotating,'RotatingGracefulE5Client',SyntheticBulkRotator)
    return bundle, hashlib.sha256(manifest).hexdigest(), SyntheticBulkRotator(), data


def test_flat_adapter_uses_pinned_runtime_and_no_index_binding(flat_bundle):
    from tools import wd_mind_e5_snapshot_adapter as adapter
    bundle,pin,client,runtime=flat_bundle
    result=adapter.create_flat_adapter(enabled=True,bundle_root=str(bundle),expected_manifest_sha256=pin,client=client)
    query_binding=json.loads(result.encoder_binding_bytes)
    assert query_binding['encoder'] == runtime['encoder'] and query_binding['code_sha256'] == runtime['code_sha256']
    assert query_binding['query_policy'] == runtime['query_policy']
    assert result.encoder_binding_sha256 == hashlib.sha256(result.encoder_binding_bytes).hexdigest()
    assert result.passage(('passage: synthetic 1','passage: synthetic 2')) == _passage_row('passage: synthetic 1')+_passage_row('passage: synthetic 2')
    assert struct.pack('<384f',*result.query('sample')) == ROW
    assert all(call['encoder_binding_sha256'] == client._binding for call in client.calls)


@pytest.mark.parametrize('fault',['default_off','manifest','runtime','worker','rotator_binding','not_bulk','wrong_client'])
def test_flat_adapter_refuses_untrusted_bindings_without_calls(flat_bundle, fault):
    from tools import wd_mind_e5_snapshot_adapter as adapter
    bundle,pin,client,_=flat_bundle
    if fault == 'manifest': pin='0'*64
    if fault == 'runtime': (bundle/'wd-mind-e5-encoder-binding.json').write_bytes(b'{}')
    if fault == 'worker': (bundle/'tools-bootstrap/tools/wd_mind_e5_worker.py').write_bytes(b'tampered')
    if fault == 'rotator_binding': client._binding='0'*64
    if fault == 'not_bulk': client._bulk_contract_sha256=None
    if fault == 'wrong_client': client=FakeClient()
    with pytest.raises(ValueError): adapter.create_flat_adapter(
        enabled=fault != 'default_off',bundle_root=str(bundle),expected_manifest_sha256=pin,client=client)
    assert client.calls == [] if hasattr(client,'calls') else client.requests == []


@pytest.mark.parametrize('fault',['schema','worker','query','query_bool','passage','model','revision','weights'])
def test_flat_adapter_rejects_repinned_invalid_runtime_policy(flat_bundle, fault):
    from tools import wd_mind_e5_snapshot_adapter as adapter
    bundle,_,client,runtime=flat_bundle
    if fault == 'schema': runtime['schema']='other'
    if fault == 'worker': runtime['code_sha256']='f'*64
    if fault == 'query': runtime['query_policy']['prefix']='passage: '
    if fault == 'query_bool': runtime['query_policy']['dim']=True
    if fault == 'passage': runtime['passage_policy']['truncate_chars']=100
    if fault == 'model': runtime['encoder']['model']='other'
    if fault == 'revision': runtime['encoder']['revision']=''
    if fault == 'weights': runtime['encoder']['weights_sha256']='not-a-hash'
    raw=_json(runtime); (bundle/'wd-mind-e5-encoder-binding.json').write_bytes(raw)
    manifest_path=bundle/'deployment-manifest.json'; manifest=json.loads(manifest_path.read_bytes())
    manifest['files']['wd-mind-e5-encoder-binding.json']=hashlib.sha256(raw).hexdigest()
    manifest_bytes=_json(manifest); manifest_path.write_bytes(manifest_bytes)
    client._binding=hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError): adapter.create_flat_adapter(enabled=True,bundle_root=str(bundle),
        expected_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),client=client)
    assert not client.calls


@pytest.mark.parametrize('fault',['runtime_bytes','bulk_contract','closed','binding'])
def test_flat_adapter_detects_changes_after_callback_without_partial_data(flat_bundle, fault, capsys):
    from tools import wd_mind_e5_snapshot_adapter as adapter
    bundle,pin,client,_=flat_bundle
    flat=adapter.create_flat_adapter(enabled=True,bundle_root=str(bundle),expected_manifest_sha256=pin,client=client)
    original=client.encode_supervised
    def mutate(request):
        result=original(request)
        if fault == 'runtime_bytes': (bundle/'wd-mind-e5-encoder-binding.json').write_bytes(b'changed')
        if fault == 'bulk_contract': client._bulk_contract_sha256='0'*64
        if fault == 'closed': client._closed=True
        if fault == 'binding': client._binding='0'*64
        return result
    client.encode_supervised=mutate
    with pytest.raises(ValueError,match='^mind_e5_snapshot_adapter_refused$'):
        flat.passage(('passage: synthetic',))
    assert capsys.readouterr() == ('','')


def test_flat_adapter_refuses_even_pinned_wrong_module_origin(flat_bundle, monkeypatch):
    from tools import wd_mind_e5_snapshot_adapter as adapter, wd_mind_content_query_readonly as reader
    bundle,_,client,_=flat_bundle
    alias=bundle/'tools-bootstrap/tools/reader_alias.py'
    alias.write_bytes(Path(reader.__file__).read_bytes())
    monkeypatch.setattr(reader,'__file__',str(alias))
    manifest_path=bundle/'deployment-manifest.json';manifest=json.loads(manifest_path.read_bytes())
    manifest['files']['tools-bootstrap/tools/reader_alias.py']=hashlib.sha256(alias.read_bytes()).hexdigest()
    raw=_json(manifest);manifest_path.write_bytes(raw)
    with pytest.raises(ValueError):
        adapter.create_flat_adapter(enabled=True,bundle_root=str(bundle),
            expected_manifest_sha256=hashlib.sha256(raw).hexdigest(),client=client)
    assert not client.calls
