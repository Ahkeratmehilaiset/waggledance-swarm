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


def test_single_worker_adapter_is_default_off_and_rejects_non_graceful(flat_bundle):
    from tools import wd_mind_e5_snapshot_adapter as adapter
    bundle, pin, client, _ = flat_bundle
    with pytest.raises(ValueError):
        adapter.create_single_flat_adapter(bundle_root=str(bundle), expected_manifest_sha256=pin, client=client)
    with pytest.raises(ValueError):
        adapter.create_single_flat_adapter(enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin,
                                           client=client)
    assert client.calls == []


def test_single_worker_adapter_real_graceful_transport_and_close(flat_bundle, monkeypatch):
    from test_wd_mind_e5_lifecycle import Process, Job, finish
    from test_wd_mind_e5_client import response, supervised
    from tools import (wd_mind_e5_snapshot_adapter as adapter, wd_mind_e5_client as client_module,
                       wd_mind_e5_lifecycle as lifecycle)
    bundle, pin, _, _ = flat_bundle
    runtime_hash = hashlib.sha256((bundle / 'wd-mind-e5-encoder-binding.json').read_bytes()).hexdigest()
    nonce = 'd' * 64
    req = supervised('query', nonce, binding=runtime_hash)
    process = Process(response(0, 0) + response(1, 1, nonce=bytes.fromhex(nonce),
                       digest=bytes.fromhex(req['request_sha256'])))
    owner = lifecycle.reserve(enabled=True, bulk_contract_sha256=client_module.BULK_CONTRACT_SHA256)
    owner.set_job(Job())
    owner.bind(process)
    try:
        client = client_module.GracefulE5WorkerClient(owner, timeout=1, encoder_binding_sha256=runtime_hash,
                                                      bulk_contract_sha256=client_module.BULK_CONTRACT_SHA256)
        owner.mark_ready()
        monkeypatch.setattr(adapter.secrets, 'token_hex', lambda _: nonce)
        single = adapter.create_single_flat_adapter(enabled=True, bundle_root=str(bundle),
                                                     expected_manifest_sha256=pin, client=client)
        assert single.query('sample')[0] == 1.0
        assert single.close(timeout=2)['state'] == 'EXITED'
        with pytest.raises(ValueError):
            single.query('after close')
    finally:
        finish(owner, process)


@pytest.fixture
def single_worker(flat_bundle, monkeypatch):
    from test_wd_mind_e5_lifecycle import Process, Job, finish
    from test_wd_mind_e5_client import response, supervised
    from tools import (wd_mind_e5_snapshot_adapter as adapter, wd_mind_e5_client as client_module,
                       wd_mind_e5_lifecycle as lifecycle)
    bundle, _, _, _ = flat_bundle
    # A single-worker bundle does not contain the deferred rotating client.
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    del manifest['files']['tools-bootstrap/tools/wd_mind_e5_rotating_client.py']
    manifest_raw = _json(manifest)
    manifest_path.write_bytes(manifest_raw)
    pin = hashlib.sha256(manifest_raw).hexdigest()
    runtime_hash = hashlib.sha256((bundle / 'wd-mind-e5-encoder-binding.json').read_bytes()).hexdigest()
    nonce = 'd' * 64
    req = supervised('query', nonce, binding=runtime_hash)
    process = Process(response(0, 0) + response(1, 1, nonce=bytes.fromhex(nonce),
                       digest=bytes.fromhex(req['request_sha256'])), on_eof=False)
    owner = lifecycle.reserve(enabled=True, bulk_contract_sha256=client_module.BULK_CONTRACT_SHA256)
    owner.set_job(Job())
    owner.bind(process)
    try:
        client = client_module.GracefulE5WorkerClient(owner, timeout=1, encoder_binding_sha256=runtime_hash,
                                                      bulk_contract_sha256=client_module.BULK_CONTRACT_SHA256)
        owner.mark_ready()
        monkeypatch.setattr(adapter.secrets, 'token_hex', lambda _: nonce)
        single = adapter.create_single_flat_adapter(enabled=True, bundle_root=str(bundle),
                                                     expected_manifest_sha256=pin, client=client)
        yield single, client, owner, process, bundle
    finally:
        finish(owner, process)


def test_single_worker_requires_no_rotator_and_preserves_hold(single_worker):
    single, client, owner, process, _ = single_worker
    assert single.query('sample')[0] == 1.0
    status = single.close(timeout=0)
    assert status['state'] == 'HOLD' and status['owned'] is True
    assert client.owner is owner and owner.resources.process is process
    with pytest.raises(ValueError):
        single.query('after close')


@pytest.mark.parametrize('fault', ['runtime', 'binding', 'bulk', 'closed', 'owner'])
def test_single_worker_changed_binding_refuses_without_partial_data(single_worker, fault):
    single, client, owner, process, bundle = single_worker
    if fault == 'runtime':
        (bundle / 'wd-mind-e5-encoder-binding.json').write_bytes(b'changed')
    elif fault == 'binding':
        client._binding_sha256 = '0' * 64
    elif fault == 'bulk':
        client._bulk_contract_sha256 = '0' * 64
    elif fault == 'closed':
        client._closed = True
    else:
        client.owner = object()
    before = bytes(process.written)
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        single.query('sample')
    assert bytes(process.written) == before
    client.owner = owner


def test_single_worker_nonce_budget_refuses_without_restarting(single_worker):
    single, client, owner, process, _ = single_worker
    client._nonce_limit = 1  # Small synthetic limit exercises the actual client guard.
    assert single.query('sample')[0] == 1.0
    before = bytes(process.written)
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        single.query('sample')
    assert bytes(process.written) == before and client.owner is owner


@pytest.mark.parametrize('fault', ['binding', 'worker', 'owner_hold'])
def test_single_worker_change_during_callback_never_returns_result(single_worker, fault, monkeypatch, capsys):
    single, client, owner, _, bundle = single_worker
    encode = client.encode_supervised
    def changed_after_response(request, **kwargs):
        output = encode(request, **kwargs)
        if fault == 'binding':
            client._binding_sha256 = '0' * 64
        elif fault == 'worker':
            (bundle / 'tools-bootstrap/tools/wd_mind_e5_worker.py').write_bytes(b'changed')
        else:
            owner._fault('synthetic_hold')
        return output
    monkeypatch.setattr(client, 'encode_supervised', changed_after_response)
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        single.query('sample')
    assert capsys.readouterr() == ('', '') and client.owner is owner


@pytest.mark.parametrize('resource_name', ['process', 'job'])
def test_single_worker_resource_replacement_during_callback_refuses(single_worker, resource_name, monkeypatch):
    single, client, owner, _, _ = single_worker
    resource = getattr(owner.resources, resource_name)
    class Proxy:
        def __getattr__(self, name):
            return getattr(resource, name)
    encode = client.encode_supervised
    def replace_after_response(request, **kwargs):
        output = encode(request, **kwargs)
        setattr(owner.resources, resource_name, Proxy())
        return output
    monkeypatch.setattr(client, 'encode_supervised', replace_after_response)
    try:
        with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
            single.query('sample')
    finally:
        setattr(owner.resources, resource_name, resource)


@pytest.fixture
def b16_worker(flat_bundle, monkeypatch, request):
    from test_wd_mind_e5_lifecycle import Process, Job, finish
    from test_wd_mind_e5_client import response, supervised
    from tools import (wd_mind_e5_snapshot_adapter as adapter, wd_mind_e5_client as client_module,
                       wd_mind_e5_lifecycle as lifecycle)
    bundle, _, _, _ = flat_bundle
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    del manifest['files']['tools-bootstrap/tools/wd_mind_e5_rotating_client.py']
    manifest_raw = _json(manifest)
    manifest_path.write_bytes(manifest_raw)
    pin = hashlib.sha256(manifest_raw).hexdigest()
    runtime_hash = hashlib.sha256((bundle / 'wd-mind-e5-encoder-binding.json').read_bytes()).hexdigest()
    nonce = 'd' * 64
    texts = [f'passage: synthetic {i}' for i in range(16)]
    binding = supervised('refresh', nonce, binding=runtime_hash, texts=texts)
    fault = getattr(request, 'param', None)
    frames = []
    for i in range(1, 5):
        number = 3 if fault == 'frame_order' and i == 2 else i
        digest = bytes.fromhex('0' * 64 if fault == 'digest' and i == 2 else binding['request_sha256'])
        status = 1 if fault == 'mid_frame' and i == 2 else 0
        vector = [0.0] * 384
        vector[i - 1] = 1.0
        frames.append(response(number, 4, vector=vector, status=status, nonce=bytes.fromhex(nonce), digest=digest))
    process = Process(response(0, 0) + b''.join(frames), on_eof=False)
    owner = lifecycle.reserve(enabled=True, bulk_contract_sha256=client_module.BULK_CONTRACT_SHA256)
    owner.set_job(Job())
    owner.bind(process)
    try:
        client = client_module.GracefulE5WorkerClient(owner, timeout=1, encoder_binding_sha256=runtime_hash,
                                                      bulk_contract_sha256=client_module.BULK_CONTRACT_SHA256)
        owner.mark_ready()
        monkeypatch.setattr(adapter.secrets, 'token_hex', lambda _: nonce)
        single = adapter.create_single_flat_adapter(enabled=True, bundle_root=str(bundle),
                                                     expected_manifest_sha256=pin, client=client)
        yield single, client, owner, process, texts, binding
    finally:
        finish(owner, process)


def test_single_b16_one_nonce_four_ordered_frames(b16_worker, monkeypatch):
    single, client, _, process, texts, binding = b16_worker
    original_check = type(single)._check
    checks = []
    def counted_check(adapter):
        original_check(adapter)
        checks.append(1)
    monkeypatch.setattr(type(single), '_check', counted_check)
    result = single.passage(tuple(texts))
    rows = [struct.unpack_from('<384f', result, i * 1536) for i in range(16)]
    assert [row.index(1.0) for row in rows] == [0] * 4 + [1] * 4 + [2] * 4 + [3] * 4
    raw = bytes(process.written)
    frames = []
    while raw:
        size = struct.unpack('<I', raw[:4])[0]
        frames.append(json.loads(raw[4:4 + size]))
        raw = raw[4 + size:]
    assert [frame['id'] for frame in frames] == [1, 2, 3, 4]
    assert [text for frame in frames for text in frame['texts']] == texts
    assert all(frame['nonce'] == binding['nonce'] and frame['request_sha256'] == binding['request_sha256']
               for frame in frames)
    assert client._used_nonces == {binding['nonce']}
    assert len(checks) == 10  # Outer pre/post plus four frames, each pre/post.


@pytest.mark.parametrize('b16_worker', ['frame_order', 'digest', 'mid_frame'], indirect=True)
def test_single_b16_refuses_bad_intermediate_frame_and_keeps_hold(b16_worker):
    single, client, owner, _, texts, _ = b16_worker
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        single.passage(tuple(texts))
    assert client.owner is owner and owner.status()['owned'] is True
    assert single.close(timeout=0)['state'] == 'HOLD'


def test_single_b16_replay_refuses_without_second_output(b16_worker):
    single, client, owner, process, texts, binding = b16_worker
    assert len(single.passage(tuple(texts))) == 16 * 1536
    before = bytes(process.written)
    with pytest.raises(ValueError):
        single.passage(tuple(texts))
    assert bytes(process.written) == before and client.owner is owner
    assert client._used_nonces == {binding['nonce']}


def test_single_b16_nonce_budget_refuses_before_any_packet(b16_worker):
    single, client, owner, process, texts, _ = b16_worker
    assert client._nonce_limit == 8192
    client._used_nonces.update(f'{i:064x}' for i in range(8192))
    with pytest.raises(ValueError):
        single.passage(tuple(texts))
    assert bytes(process.written) == b'' and client.owner is owner


def test_single_b16_keeps_bulk_contract_frame_and_output_limits():
    from tools import (wd_mind_e5_client as client, wd_mind_e5_lifecycle as lifecycle,
                       wd_mind_e5_worker as worker)
    assert lifecycle.BULK_STDOUT_BYTES == 64 * 1024 * 1024
    assert lifecycle.MAX_STDOUT_BUFFER == lifecycle.MAX_PACKET == 65540
    assert client._MAX_BATCH == worker.MAX_BATCH == 4
    assert client.BULK_CONTRACT_SHA256 == lifecycle.BULK_CONTRACT_SHA256 == worker.BULK_CONTRACT_SHA256
    assert hashlib.sha256(lifecycle.BULK_CONTRACT).hexdigest() == lifecycle.BULK_CONTRACT_SHA256
    assert worker._nonce_budget(worker.BULK_CONTRACT_SHA256) == 8192


@pytest.mark.parametrize('fault', ['runtime', 'process', 'job'])
def test_single_b16_refuses_binding_changed_after_first_frame(b16_worker, fault, monkeypatch):
    single, client, owner, process, texts, _ = b16_worker
    real_process, real_job = owner.resources.process, owner.resources.job
    bundle = Path(single._bundle)
    original_request = client._request
    calls = []
    class Proxy:
        def __init__(self, target): self.target = target
        def __getattr__(self, name): return getattr(self.target, name)
    def mutate_after_frame(*args, **kwargs):
        result = original_request(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            if fault == 'runtime':
                (bundle / 'wd-mind-e5-encoder-binding.json').write_bytes(b'changed')
            else:
                setattr(owner.resources, fault, Proxy(getattr(owner.resources, fault)))
        return result
    monkeypatch.setattr(client, '_request', mutate_after_frame)
    try:
        with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
            single.passage(tuple(texts))
        raw = bytes(process.written)
        size = struct.unpack('<I', raw[:4])[0]
        assert len(raw) == 4 + size and len(calls) == 1
        assert client.owner is owner and owner.status()['owned'] is True
    finally:
        owner.resources.process, owner.resources.job = real_process, real_job


def test_single_b16_checks_again_before_next_frame(b16_worker, monkeypatch):
    single, client, owner, process, texts, _ = b16_worker
    original = type(single)._check
    calls = []
    bundle = Path(single._bundle)
    def mutate_after_first_postcheck(adapter):
        original(adapter)
        calls.append(1)
        # The outer B16 check is call 1, then frame 1 pre/post are calls 2/3.
        if len(calls) == 3:
            (bundle / 'wd-mind-e5-encoder-binding.json').write_bytes(b'changed')
    monkeypatch.setattr(type(single), '_check', mutate_after_first_postcheck)
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        single.passage(tuple(texts))
    raw = bytes(process.written)
    size = struct.unpack('<I', raw[:4])[0]
    assert len(raw) == 4 + size and len(calls) == 3
    assert client.owner is owner and owner.status()['owned'] is True


def test_single_b16_last_frame_check_timeout_keeps_owner(b16_worker, monkeypatch):
    import time
    single, client, owner, _, texts, _ = b16_worker
    client._timeout = 0.5
    original = type(single)._check
    checks = []
    def slow_last_check(adapter):
        original(adapter)
        checks.append(1)
        # Outer pre-check followed by four frame pre/post checks.
        if len(checks) == 9:
            time.sleep(0.65)
    monkeypatch.setattr(type(single), '_check', slow_last_check)
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        single.passage(tuple(texts))
    assert len(checks) == 9 and client.owner is owner and owner.status()['owned'] is True


@pytest.mark.parametrize('phase', ['precheck', 'postcheck', 'vector_validation'])
def test_single_b16_total_deadline_covers_adapter_checks_and_vectors(b16_worker, monkeypatch, phase):
    import time
    from tools import wd_mind_content_query_readonly as reader
    single, client, owner, _, texts, _ = b16_worker
    client._timeout = 0.15
    calls, checks, vectors = [], [], []
    def immediate(request, **kwargs):
        calls.append(1)
        return {'nonce': request['nonce'], 'request_sha256': request['request_sha256'],
                'vector_bytes': ROW * len(request['passages'])}
    original_check = type(single)._check
    def delayed_check(adapter):
        original_check(adapter)
        checks.append(1)
        if (phase == 'precheck' and len(checks) == 1) or (phase == 'postcheck' and len(checks) == 2):
            time.sleep(0.25)
    original_vector = reader._q4_vector
    def delayed_vector(row):
        vectors.append(1)
        if phase == 'vector_validation' and len(vectors) == 1:
            time.sleep(0.25)
        return original_vector(row)
    monkeypatch.setattr(client, 'encode_supervised', immediate)
    monkeypatch.setattr(type(single), '_check', delayed_check)
    monkeypatch.setattr(reader, '_q4_vector', delayed_vector)
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        single.passage(tuple(texts))
    assert len(calls) == (0 if phase == 'precheck' else 1)
    assert client.owner is owner and owner.status()['owned'] is True


def test_single_b16_forwards_remaining_deadline_to_client(b16_worker, monkeypatch):
    import time
    single, client, _, _, texts, _ = b16_worker
    client._timeout = 0.5
    seen = []
    def immediate(request, *, frame_check, call_deadline):
        assert callable(frame_check)
        seen.append(call_deadline - time.monotonic())
        return {'nonce': request['nonce'], 'request_sha256': request['request_sha256'],
                'vector_bytes': ROW * len(request['passages'])}
    monkeypatch.setattr(client, 'encode_supervised', immediate)
    assert len(single.passage(tuple(texts))) == 16 * 1536
    assert len(seen) == 1 and 0 < seen[0] <= 0.5
