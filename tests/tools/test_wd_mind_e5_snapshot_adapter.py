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
        wd_mind_absent_generation_prefix_readonly as prefix, wd_mind_e5_lifecycle as lifecycle,
        wd_mind_e5_client as client_module)
    bundle = tmp_path / 'bundle'; bundle.mkdir()
    files = {}
    for module in (adapter, reader, prefix, lifecycle, client_module):
        relative = 'tools-bootstrap/tools/' + Path(module.__file__).name
        path = bundle / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Path(module.__file__).read_bytes())
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        monkeypatch.setattr(module, '__file__', str(path))
    worker = bundle / 'tools-bootstrap/tools/wd_mind_e5_worker.py'
    worker.write_bytes(b'synthetic pinned worker only; no native code')
    files['tools-bootstrap/tools/wd_mind_e5_worker.py'] = hashlib.sha256(worker.read_bytes()).hexdigest()
    data = json.loads(_runtime_binding()['runtime_binding_bytes'])
    data['code_sha256'] = files['tools-bootstrap/tools/wd_mind_e5_worker.py']
    raw = _json(data); path = bundle / 'wd-mind-e5-encoder-binding.json'; path.write_bytes(raw)
    files[path.name] = hashlib.sha256(raw).hexdigest()
    manifest = _json({'schema_version': 1, 'files': files}); (bundle / 'deployment-manifest.json').write_bytes(manifest)
    return bundle, hashlib.sha256(manifest).hexdigest(), FakeClient(), data


def test_single_worker_adapter_is_default_off_and_rejects_non_graceful(flat_bundle):
    from tools import wd_mind_e5_snapshot_adapter as adapter
    bundle, pin, client, _ = flat_bundle
    with pytest.raises(ValueError):
        adapter.create_single_flat_adapter(bundle_root=str(bundle), expected_manifest_sha256=pin, client=client)
    with pytest.raises(ValueError):
        adapter.create_single_flat_adapter(enabled=True, bundle_root=str(bundle), expected_manifest_sha256=pin,
                                           client=client)
    assert client.requests == []


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
    assert 'tools-bootstrap/tools/wd_mind_e5_rotating_client.py' not in manifest['files']
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


@pytest.mark.parametrize('fault', ['schema', 'worker', 'query', 'query_bool', 'passage', 'model', 'revision', 'weights'])
def test_single_worker_rejects_repinned_invalid_runtime_policy(single_worker, fault):
    from tools import wd_mind_e5_snapshot_adapter as adapter
    _, client, owner, process, bundle = single_worker
    runtime_path = bundle / 'wd-mind-e5-encoder-binding.json'
    data = json.loads(runtime_path.read_bytes())
    if fault == 'schema': data['schema'] = 'other'
    if fault == 'worker': data['code_sha256'] = 'f' * 64
    if fault == 'query': data['query_policy']['prefix'] = 'passage: '
    if fault == 'query_bool': data['query_policy']['dim'] = True
    if fault == 'passage': data['passage_policy']['truncate_chars'] = 100
    if fault == 'model': data['encoder']['model'] = 'other'
    if fault == 'revision': data['encoder']['revision'] = ''
    if fault == 'weights': data['encoder']['weights_sha256'] = 'not-a-hash'
    raw = _json(data)
    runtime_path.write_bytes(raw)
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files'][runtime_path.name] = hashlib.sha256(raw).hexdigest()
    manifest_raw = _json(manifest)
    manifest_path.write_bytes(manifest_raw)
    client._binding_sha256 = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        adapter.create_single_flat_adapter(enabled=True, bundle_root=str(bundle),
            expected_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(), client=client)
    assert bytes(process.written) == b'' and client.owner is owner


def test_single_worker_refuses_wrong_module_origin(single_worker, monkeypatch):
    from tools import wd_mind_e5_snapshot_adapter as adapter, wd_mind_content_query_readonly as reader
    _, client, owner, process, bundle = single_worker
    alias = bundle / 'tools-bootstrap/tools/reader_alias.py'
    alias.write_bytes(Path(reader.__file__).read_bytes())
    monkeypatch.setattr(reader, '__file__', str(alias))
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['tools-bootstrap/tools/reader_alias.py'] = hashlib.sha256(alias.read_bytes()).hexdigest()
    manifest_raw = _json(manifest)
    manifest_path.write_bytes(manifest_raw)
    with pytest.raises(ValueError):
        adapter.create_single_flat_adapter(enabled=True, bundle_root=str(bundle),
            expected_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(), client=client)
    assert bytes(process.written) == b'' and client.owner is owner


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
    assert 'tools-bootstrap/tools/wd_mind_e5_rotating_client.py' not in manifest['files']
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


@pytest.fixture
def stable_b16_worker(b16_worker, monkeypatch):
    """Isolate protocol/deadline tests from repeated filesystem timing.

    The real bundle binding is checked once here and separately exercised by
    the binding-change tests. A per-module clock avoids patching Python's
    process-wide time module or changing any production timeout.
    """
    from types import SimpleNamespace
    import time
    from tools import (wd_mind_e5_snapshot_adapter as adapter, wd_mind_e5_client as client_module,
                       wd_mind_e5_lifecycle as lifecycle)
    single = b16_worker[0]
    expected = adapter._flat_bundle_bindings(single._bundle, single._manifest)

    def checked_binding(bundle, manifest):
        assert (bundle, manifest) == (single._bundle, single._manifest)
        return expected

    clock = [100.0]
    fake_time = SimpleNamespace(monotonic=lambda: clock[0], sleep=time.sleep)
    monkeypatch.setattr(adapter, '_flat_bundle_bindings', checked_binding)
    monkeypatch.setattr(adapter, 'time', fake_time)
    monkeypatch.setattr(client_module, 'time', fake_time)
    monkeypatch.setattr(lifecycle, 'time', fake_time)
    return (*b16_worker, clock)


def test_single_b16_one_nonce_four_ordered_frames(stable_b16_worker, monkeypatch):
    single, client, _, process, texts, binding, _ = stable_b16_worker
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


def test_single_b16_replay_refuses_without_second_output(stable_b16_worker):
    single, client, owner, process, texts, binding, _ = stable_b16_worker
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


def test_single_b16_last_frame_check_timeout_keeps_owner(stable_b16_worker, monkeypatch):
    single, client, owner, _, texts, _, clock = stable_b16_worker
    client._timeout = 0.5
    original = type(single)._check
    checks = []
    def slow_last_check(adapter):
        original(adapter)
        checks.append(1)
        # Outer pre-check followed by four frame pre/post checks.
        if len(checks) == 9:
            clock[0] += 0.65
    monkeypatch.setattr(type(single), '_check', slow_last_check)
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        single.passage(tuple(texts))
    assert len(checks) == 9 and client.owner is owner and owner.status()['owned'] is True


@pytest.mark.parametrize('phase', ['precheck', 'postcheck', 'vector_validation'])
def test_single_b16_total_deadline_covers_adapter_checks_and_vectors(stable_b16_worker, monkeypatch, phase):
    from tools import wd_mind_content_query_readonly as reader
    single, client, owner, _, texts, _, clock = stable_b16_worker
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
            clock[0] += 0.25
    original_vector = reader._q4_vector
    def delayed_vector(row):
        vectors.append(1)
        if phase == 'vector_validation' and len(vectors) == 1:
            clock[0] += 0.25
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


@pytest.fixture
def rotating_bundle(flat_bundle, monkeypatch):
    from tools import (wd_mind_e5_snapshot_adapter as adapter,
                       wd_mind_e5_rotating_client as rotating,
                       wd_mind_e5_client as client_module,
                       wd_mind_full_runtime as runtime)
    bundle, _, _, _ = flat_bundle
    # The existing synthetic fixture has already rebound module origins into
    # this bundle; add the independently pinned rotating module in the same way.
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    for module in (rotating, runtime):
        source = Path(module.__file__)
        relative = 'tools-bootstrap/tools/' + source.name
        target = bundle.joinpath(*relative.split('/'))
        target.write_bytes(source.read_bytes())
        manifest['files'][relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        monkeypatch.setattr(module, '__file__', str(target))
    raw = _json(manifest)
    manifest_path.write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    # Synthetic tests that exercise adapter behavior use an already-imported
    # fake worker; the separate loader tests exercise real byte attestation.
    rotator_path = bundle / 'tools-bootstrap' / 'tools' / 'wd_mind_e5_rotating_client.py'
    monkeypatch.setattr(adapter, '_ROTATOR_ATTESTATION', (rotating, str(rotator_path), pin))
    runtime_hash = hashlib.sha256((bundle / 'wd-mind-e5-encoder-binding.json').read_bytes()).hexdigest()
    factory = runtime._ManifestWorkerFactory(str(bundle), pin, {'schema': 'synthetic'},
                                             bundle / 'unused.lock', 30.0, 1.0,
                                             client_module.BULK_CONTRACT_SHA256)
    def make_rotator():
        return rotating.RotatingGracefulE5Client(factory=factory, encoder_binding_sha256=runtime_hash,
            snapshot_sha256='a' * 64, source_check=lambda: 'a' * 64, shutdown_timeout=1.0,
            call_timeout=30.0, bulk_contract_sha256=client_module.BULK_CONTRACT_SHA256)
    return adapter, rotating, factory, make_rotator, bundle, pin


@pytest.fixture
def rotating_case(b16_worker, rotating_bundle):
    adapter, rotating, _, make_rotator, bundle, pin = rotating_bundle
    _, worker, owner, process, texts, _ = b16_worker
    worker._timeout = 30.0
    rotator = make_rotator()
    rotator._client, rotator._owner = worker, owner
    rotator._owned_process, rotator._owned_job = owner.resources.process, owner.resources.job
    rotator._worker_timeout = worker._timeout
    return adapter, rotating, rotator, bundle, pin, owner, process, texts


def test_rotating_adapter_requires_opt_in_exact_type_and_snapshot(rotating_case):
    adapter, rotating, rotator, bundle, pin, _, _, _ = rotating_case
    kwargs = dict(bundle_root=str(bundle), expected_manifest_sha256=pin,
                  client=rotator, expected_snapshot_sha256='a' * 64)
    with pytest.raises(ValueError):
        adapter.create_rotating_flat_adapter(**kwargs)
    with pytest.raises(ValueError):
        adapter.create_rotating_flat_adapter(enabled=True, **dict(kwargs, client=FakeClient()))
    class DerivedRotator(rotating.RotatingGracefulE5Client):
        pass
    with pytest.raises(ValueError):
        adapter.create_rotating_flat_adapter(enabled=True, **dict(kwargs, client=object.__new__(DerivedRotator)))
    with pytest.raises(ValueError):
        adapter.create_rotating_flat_adapter(enabled=True, **dict(kwargs, expected_snapshot_sha256='b' * 64))
    result = adapter.create_rotating_flat_adapter(enabled=True, **kwargs)
    assert result._client is rotator


def test_rotating_adapter_can_bind_new_owner_without_starting_a_worker(rotating_bundle):
    adapter, _, _, make_rotator, bundle, pin = rotating_bundle
    rotator = make_rotator()
    result = adapter.create_rotating_flat_adapter(enabled=True, bundle_root=str(bundle),
        expected_manifest_sha256=pin, client=rotator, expected_snapshot_sha256='a' * 64)
    assert result._client is rotator and rotator.owner is None
    assert rotator.status()['state'] == 'NEW'


def test_rotating_loader_checks_pinned_bytes_before_import(rotating_bundle, monkeypatch):
    import sys
    import tools
    from types import SimpleNamespace
    adapter, rotating, _, _, bundle, pin = rotating_bundle
    target = Path(rotating.__file__)
    target.write_bytes(b'changed untrusted module')
    monkeypatch.delitem(sys.modules, 'tools.wd_mind_e5_rotating_client')
    monkeypatch.delattr(tools, 'wd_mind_e5_rotating_client', raising=False)
    monkeypatch.setattr(adapter.importlib.util, 'find_spec', lambda name: SimpleNamespace(origin=str(target)))
    calls = []
    monkeypatch.setattr(adapter, 'compile', lambda *a, **k: calls.append(a), raising=False)
    with pytest.raises(ValueError):
        adapter._load_pinned_rotating_module(str(bundle), pin)
    assert calls == []


def test_rotating_loader_rejects_unattested_preloaded_module(rotating_bundle, monkeypatch):
    adapter, rotating, _, _, bundle, pin = rotating_bundle
    monkeypatch.setattr(adapter, '_ROTATOR_ATTESTATION', None)
    with pytest.raises(ValueError, match='rotator_unverified_preload'):
        adapter._load_pinned_rotating_module(str(bundle), pin)


def test_rotating_loader_rejects_foreign_origin_before_import(rotating_bundle, monkeypatch):
    import sys
    import tools
    from types import SimpleNamespace
    adapter, _, _, _, bundle, pin = rotating_bundle
    monkeypatch.delitem(sys.modules, 'tools.wd_mind_e5_rotating_client')
    monkeypatch.delattr(tools, 'wd_mind_e5_rotating_client', raising=False)
    monkeypatch.setattr(adapter.importlib.util, 'find_spec',
                        lambda name: SimpleNamespace(origin=str(bundle / 'foreign.py')))
    calls = []
    monkeypatch.setattr(adapter, 'compile', lambda *a, **k: calls.append(a), raising=False)
    with pytest.raises(ValueError):
        adapter._load_pinned_rotating_module(str(bundle), pin)
    assert calls == []


def test_rotating_loader_executes_verified_source_bytes_on_cold_load(rotating_bundle, monkeypatch):
    import sys
    import tools
    from types import SimpleNamespace
    adapter, rotating, _, _, bundle, pin = rotating_bundle
    target = Path(rotating.__file__)
    monkeypatch.delitem(sys.modules, 'tools.wd_mind_e5_rotating_client')
    monkeypatch.delattr(tools, 'wd_mind_e5_rotating_client', raising=False)
    class PoisonLoader:
        def create_module(self, spec):
            pytest.fail('unverified loader must not create a module')
        def exec_module(self, module):
            pytest.fail('unverified loader must not execute a module')
    monkeypatch.setattr(adapter.importlib.util, 'find_spec',
                        lambda name: SimpleNamespace(origin=str(target), loader=PoisonLoader()))
    loaded = adapter._load_pinned_rotating_module(str(bundle), pin)
    assert loaded is sys.modules['tools.wd_mind_e5_rotating_client']
    assert loaded is tools.wd_mind_e5_rotating_client and loaded is not rotating
    assert loaded.__file__ == str(target) and callable(loaded.RotatingGracefulE5Client)


def test_rotating_loader_refuses_concurrent_initialization(rotating_bundle):
    adapter, _, _, _, bundle, pin = rotating_bundle
    assert adapter._ROTATOR_LOAD_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(ValueError):
            adapter._load_pinned_rotating_module(str(bundle), pin)
    finally:
        adapter._ROTATOR_LOAD_LOCK.release()


def test_rotating_loader_discards_module_if_pinned_file_changes_during_import(rotating_bundle, monkeypatch):
    import sys
    import tools
    from types import SimpleNamespace
    adapter, rotating, _, _, bundle, _ = rotating_bundle
    target = Path(rotating.__file__)
    target.write_bytes(b"from pathlib import Path\nPath(__file__).write_bytes(b'changed')\n")
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['tools-bootstrap/tools/wd_mind_e5_rotating_client.py'] = hashlib.sha256(target.read_bytes()).hexdigest()
    raw = _json(manifest); manifest_path.write_bytes(raw); pin = hashlib.sha256(raw).hexdigest()
    monkeypatch.delitem(sys.modules, 'tools.wd_mind_e5_rotating_client')
    monkeypatch.delattr(tools, 'wd_mind_e5_rotating_client', raising=False)
    monkeypatch.setattr(adapter.importlib.util, 'find_spec', lambda name: SimpleNamespace(origin=str(target)))
    with pytest.raises(ValueError):
        adapter._load_pinned_rotating_module(str(bundle), pin)
    assert 'tools.wd_mind_e5_rotating_client' not in sys.modules
    assert not hasattr(tools, 'wd_mind_e5_rotating_client')


@pytest.mark.parametrize('fault', ['source', 'snapshot', 'factory', 'timeout', 'bulk', 'module', 'runtime_module'])
def test_rotating_adapter_refuses_changed_bindings_before_transport(rotating_case, monkeypatch, fault):
    adapter, rotating, rotator, bundle, pin, owner, process, texts = rotating_case
    current = adapter.create_rotating_flat_adapter(enabled=True, bundle_root=str(bundle),
        expected_manifest_sha256=pin, client=rotator, expected_snapshot_sha256='a' * 64)
    if fault == 'source':
        rotator._source_check = lambda: 'a' * 64
    elif fault == 'snapshot':
        rotator._snapshot = 'b' * 64
    elif fault == 'factory':
        rotator._factory._location = {'schema': 'retargeted'}
    elif fault == 'timeout':
        rotator._timeout += 1
    elif fault == 'bulk':
        rotator._bulk_contract_sha256 = '0' * 64
    elif fault == 'module':
        Path(rotating.__file__).write_bytes(b'changed synthetic module')
    else:
        from tools import wd_mind_full_runtime as runtime
        Path(runtime.__file__).write_bytes(b'changed synthetic runtime')
    monkeypatch.setattr(rotating.RotatingGracefulE5Client, 'encode_supervised',
                        lambda *a, **k: pytest.fail('changed pins must not reach transport'))
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        current.passage(tuple(texts))
    assert not process.written and rotator.owner is owner


@pytest.mark.parametrize('bound,worker', [
    (0, 0), (-1, -1), (float('nan'), float('nan')), (float('inf'), float('inf')),
    (True, True), (30.001, 30.001), (29, 29.0), (29.99, 29.98),
])
def test_rotating_adapter_refuses_changed_or_invalid_worker_timeout(rotating_case, monkeypatch, bound, worker):
    adapter, rotating, rotator, bundle, pin, owner, process, texts = rotating_case
    current = adapter.create_rotating_flat_adapter(enabled=True, bundle_root=str(bundle),
        expected_manifest_sha256=pin, client=rotator, expected_snapshot_sha256='a' * 64)
    rotator._worker_timeout = bound
    rotator.active_client._timeout = worker
    monkeypatch.setattr(rotating.RotatingGracefulE5Client, 'encode_supervised',
                        lambda *a, **k: pytest.fail('invalid worker timeout must not reach transport'))
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        current.passage(tuple(texts))
    assert not process.written and rotator.owner is owner


def test_rotating_frame_timeout_change_never_returns_partial_vectors(rotating_case, monkeypatch):
    adapter, rotating, rotator, bundle, pin, owner, _, texts = rotating_case
    def encode(self, payload, *, frame_check, call_deadline):
        assert self._lock.acquire(blocking=False)
        try:
            frame_check()
            self.active_client._timeout = 29.99
            frame_check()
            pytest.fail('changed worker timeout must be refused')
        finally:
            self._lock.release()
    monkeypatch.setattr(rotating.RotatingGracefulE5Client, 'encode_supervised', encode)
    current = adapter.create_rotating_flat_adapter(enabled=True, bundle_root=str(bundle),
        expected_manifest_sha256=pin, client=rotator, expected_snapshot_sha256='a' * 64)
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        current.passage(tuple(texts))
    assert rotator.owner is owner


def test_rotating_b16_checks_each_frame_without_reentering_rotator_lock(rotating_case, monkeypatch):
    adapter, rotating, rotator, bundle, pin, owner, _, texts = rotating_case
    checks = []
    def encode(self, payload, *, frame_check, call_deadline):
        assert self._lock.acquire(blocking=False)
        try:
            for _ in range(4):
                frame_check(); checks.append('before')
                frame_check(); checks.append('after')
        finally:
            self._lock.release()
        return {'nonce': payload['nonce'], 'request_sha256': payload['request_sha256'],
                'vector_bytes': ROW * len(payload['passages'])}
    monkeypatch.setattr(rotating.RotatingGracefulE5Client, 'encode_supervised', encode)
    current = adapter.create_rotating_flat_adapter(enabled=True, bundle_root=str(bundle),
        expected_manifest_sha256=pin, client=rotator, expected_snapshot_sha256='a' * 64)
    assert len(current.passage(tuple(texts))) == 16 * 1536
    assert checks == ['before', 'after'] * 4 and rotator.owner is owner


def test_rotating_frame_owner_change_never_returns_partial_vectors(rotating_case, monkeypatch):
    adapter, rotating, rotator, bundle, pin, owner, _, texts = rotating_case
    original_job = owner.resources.job
    def encode(self, payload, *, frame_check, call_deadline):
        assert self._lock.acquire(blocking=False)
        try:
            frame_check()
            owner.resources.job = object()
            frame_check()
            pytest.fail('changed worker job must be refused')
        finally:
            owner.resources.job = original_job
            self._lock.release()
    monkeypatch.setattr(rotating.RotatingGracefulE5Client, 'encode_supervised', encode)
    current = adapter.create_rotating_flat_adapter(enabled=True, bundle_root=str(bundle),
        expected_manifest_sha256=pin, client=rotator, expected_snapshot_sha256='a' * 64)
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        current.passage(tuple(texts))
    assert rotator.owner is owner


@pytest.mark.parametrize('checks', [0, 1, 7, 9])
def test_rotating_response_without_all_frame_checks_is_refused(rotating_case, monkeypatch, checks):
    adapter, rotating, rotator, bundle, pin, owner, _, texts = rotating_case
    def unchecked(self, payload, *, frame_check, call_deadline):
        assert self._lock.acquire(blocking=False)
        try:
            for _ in range(checks):
                frame_check()
        finally:
            self._lock.release()
        return {'nonce': payload['nonce'], 'request_sha256': payload['request_sha256'],
                'vector_bytes': ROW * len(payload['passages'])}
    monkeypatch.setattr(rotating.RotatingGracefulE5Client, 'encode_supervised', unchecked)
    current = adapter.create_rotating_flat_adapter(enabled=True, bundle_root=str(bundle),
        expected_manifest_sha256=pin, client=rotator, expected_snapshot_sha256='a' * 64)
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        current.passage(tuple(texts))
    assert rotator.owner is owner


def test_rotating_frame_deadline_refuses_late_result_and_retains_owner(rotating_case, monkeypatch):
    import time
    adapter, rotating, rotator, bundle, pin, owner, _, texts = rotating_case
    rotator._factory._timeout = rotator._timeout = rotator.active_client._timeout = 0.15
    rotator._worker_timeout = 0.15
    def delayed(self, payload, *, frame_check, call_deadline):
        assert self._lock.acquire(blocking=False)
        try:
            frame_check()
            time.sleep(0.25)
            frame_check()
            pytest.fail('late frame must be refused')
        finally:
            self._lock.release()
    monkeypatch.setattr(rotating.RotatingGracefulE5Client, 'encode_supervised', delayed)
    current = adapter.create_rotating_flat_adapter(enabled=True, bundle_root=str(bundle),
        expected_manifest_sha256=pin, client=rotator, expected_snapshot_sha256='a' * 64)
    with pytest.raises(ValueError, match='^mind_e5_snapshot_adapter_refused$'):
        current.passage(tuple(texts))
    assert rotator.owner is owner


def test_rotating_close_preserves_hold_owner(rotating_case, monkeypatch):
    adapter, rotating, rotator, bundle, pin, owner, _, _ = rotating_case
    monkeypatch.setattr(rotating.RotatingGracefulE5Client, 'close',
                        lambda self, *, timeout=0: {'state': 'HOLD', 'owned': True})
    current = adapter.create_rotating_flat_adapter(enabled=True, bundle_root=str(bundle),
        expected_manifest_sha256=pin, client=rotator, expected_snapshot_sha256='a' * 64)
    assert current.close(timeout=1) == {'state': 'HOLD', 'owned': True}
    assert rotator.owner is owner


def test_rotating_adapter_uses_real_cooperative_pumps_across_rotation(rotating_bundle, monkeypatch):
    from test_wd_mind_e5_client import response
    from test_wd_mind_e5_lifecycle import Job, Process
    from tools import wd_mind_e5_client as e5_client, wd_mind_e5_lifecycle as lifecycle
    adapter, rotating, factory, make_rotator, bundle, pin = rotating_bundle
    budget = 79 + 2 * (79 + 1536)
    monkeypatch.setattr(rotating, 'BULK_STDOUT_BYTES', budget)
    monkeypatch.setattr(lifecycle, 'BULK_STDOUT_BYTES', budget)
    processes, nonces = [], iter(['a' * 64, 'b' * 64, 'c' * 64])
    monkeypatch.setattr(adapter.secrets, 'token_hex', lambda _: next(nonces))

    def launch(self, *, owner_sink, call_deadline):
        assert self is factory
        process = Process(response(0, 0))
        processes.append(process)

        class Input:
            def write(self, packet):
                size = int.from_bytes(packet[:4], 'little')
                body = json.loads(packet[4:].decode('ascii'))
                assert size == len(packet) - 4
                frame = response(body['id'], len(body['texts']), nonce=bytes.fromhex(body['nonce']),
                                 digest=bytes.fromhex(body['request_sha256']))
                with process.stdout.condition:
                    process.stdout.data.extend(frame)
                    process.stdout.condition.notify_all()
                return len(packet)
            def flush(self): pass
            def close(self): process.finish(0)

        process.stdin = Input()
        owner = lifecycle.reserve(enabled=True, bulk_contract_sha256=e5_client.BULK_CONTRACT_SHA256)
        owner_sink(owner)
        owner.set_job(Job()); owner.bind(process)
        # The real launcher spends part of its timeout on startup before
        # constructing the client with its remaining deadline.
        client = e5_client.GracefulE5WorkerClient(owner, timeout=29.99,
            encoder_binding_sha256=rotator._binding, bulk_contract_sha256=e5_client.BULK_CONTRACT_SHA256)
        owner.mark_ready()
        return {'status': 'READY', 'owner': owner, 'client': client,
                'bulk_contract_sha256': e5_client.BULK_CONTRACT_SHA256}

    monkeypatch.setattr(type(factory), '__call__', launch)
    rotator = make_rotator()
    current = adapter.create_rotating_flat_adapter(enabled=True, bundle_root=str(bundle),
        expected_manifest_sha256=pin, client=rotator, expected_snapshot_sha256='a' * 64)
    try:
        for _ in range(3):
            assert current.query('synthetic question')[0] == 1.0
        assert len(processes) == 2 and processes[0].poll() == 0
        assert current.close(timeout=2)['state'] == 'EXITED'
    finally:
        for process in processes:
            process.finish(0)
        if rotator.owner is not None:
            rotator.close(timeout=2)
