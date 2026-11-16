# SPDX-License-Identifier: BUSL-1.1
"""Opt-in data-format adapter for a caller-supplied supervised E5 client.

This module neither starts a client nor attaches itself to snapshot/query flows.
The caller owns the trusted client, source and binding selection, lifecycle,
serializer and policy. Hashes and response echoes are consistency checks, not
proof of model execution, client identity, sender authority or absence of races.
In particular, IPC sub-batches here are not source checkpoints: the existing
producer verifies its frozen source after its whole B16 callback returns.

The single-worker path remains independent of the opt-in rotating-client
adapter. The caller must still retain every worker owner through HOLD.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import struct
import os
import math
import threading
import time
import importlib
import importlib.util
import types
from pathlib import Path
import re
import sys

from tools import wd_mind_content_query_readonly as reader

_ROW = struct.Struct('<384f')
_BATCH = 4
_MAX_PASSAGES = 16
_ROTATOR_LOAD_LOCK = threading.Lock()
_ROTATOR_ATTESTATION = None


class SnapshotAdapterRefused(ValueError):
    """Fixed refusal without caller text or provider exception details."""


def _run(operation):
    try:
        return operation()
    except Exception:
        pass
    raise SnapshotAdapterRefused('mind_e5_snapshot_adapter_refused') from None


class _Adapter:
    __slots__ = ('_client', '_binding_sha256')

    def __init__(self, client, binding_sha256):
        self._client = client
        self._binding_sha256 = binding_sha256

    def _encode_supervised(self, payload):
        return self._client.encode_supervised(payload)

    def _request(self, operation, texts):
        nonce = secrets.token_hex(32)
        field = 'passages' if operation == 'refresh' else 'query'
        payload = {'schema': 'wd.mind-supervised-e5-request.v1', 'operation': operation,
                   'nonce': nonce, 'encoder_binding_sha256': self._binding_sha256,
                   field: list(texts) if operation == 'refresh' else texts[0]}
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        payload['request_sha256'] = digest
        response = self._encode_supervised(payload)
        if (type(response) is not dict or set(response) != {'nonce', 'request_sha256', 'vector_bytes'}
                or type(response['nonce']) is not str or not hmac.compare_digest(response['nonce'], nonce)
                or type(response['request_sha256']) is not str
                or not hmac.compare_digest(response['request_sha256'], digest)):
            raise ValueError('binding')
        vectors = response['vector_bytes']
        if type(vectors) is not bytes or len(vectors) != len(texts) * _ROW.size:
            raise ValueError('vector_bytes')
        for offset in range(0, len(vectors), _ROW.size):
            reader._q4_vector(_ROW.unpack_from(vectors, offset))
        return vectors

    def passage(self, texts):
        return _run(lambda: self._passage(texts))

    def _passage(self, texts):
        if (type(texts) not in (tuple, list) or not 1 <= len(texts) <= _MAX_PASSAGES
                or any(type(text) is not str or not text.startswith('passage: ')
                       or not text[9:].strip() or len(text) > 2009 or '\x00' in text for text in texts)):
            raise ValueError('passage')
        frozen = tuple(texts)
        chunks = []
        for start in range(0, len(frozen), _BATCH):
            chunks.append(self._request('refresh', frozen[start:start + _BATCH]))
        return b''.join(chunks)

    def query(self, text):
        return _run(lambda: self._query(text))

    def _query(self, text):
        if type(text) is not str or not 0 < len(text) <= 4000 or '\x00' in text or not text[:2000].strip():
            raise ValueError('query')
        raw = self._request('query', ('query: ' + text[:2000],))
        return _ROW.unpack(raw)


def create_adapter(*, enabled=False, client=None, encoder_binding_bytes=None, encoder_binding_sha256=None,
                   index_binding_bytes=None, index_binding_sha256=None,
                   runtime_binding_bytes=None, runtime_binding_sha256=None):
    """Build only an adapter; callers must independently establish trust and own all I/O."""
    def create():
        if enabled is not True or client is None or isinstance(client, type):
            raise ValueError('disabled')
        encoder = reader._pinned(encoder_binding_bytes, encoder_binding_sha256)
        index = reader._pinned(index_binding_bytes, index_binding_sha256)
        reader._q4_bindings(encoder, index)
        worker_binding_sha256 = encoder_binding_sha256
        if runtime_binding_bytes is not None or runtime_binding_sha256 is not None:
            runtime = reader._pinned(runtime_binding_bytes, runtime_binding_sha256)
            expected = {'prefix': 'query: ', 'truncate_chars': 2000,
                        'normalize': True, 'dtype': 'float32', 'dim': 384}
            if (type(runtime) is not dict
                    or set(runtime) != {'schema', 'encoder', 'code_sha256', 'passage_policy', 'query_policy'}
                    or runtime['schema'] != 'wd.mind-memory-encoder-binding.v1'
                    or runtime['encoder'] != encoder['encoder']
                    or runtime['code_sha256'] != encoder['code_sha256']
                    or runtime['query_policy'] != expected
                    or encoder['query_policy'] != expected
                    or runtime['passage_policy'] != dict(expected, prefix='passage: ')
                    or any(type(policy.get('normalize')) is not bool
                           or type(policy.get('truncate_chars')) is not int
                           or type(policy.get('dim')) is not int
                           for policy in (runtime['query_policy'], runtime['passage_policy'],
                                          encoder['query_policy']))):
                raise ValueError('runtime_binding_mismatch')
            worker_binding_sha256 = runtime_binding_sha256
        if not callable(getattr(client, 'encode_supervised', None)):
            raise ValueError('client')
        return _Adapter(client, worker_binding_sha256)
    return _run(create)


def _flat_bundle_bindings(bundle_root, expected_manifest_sha256):
    """Derive the read-only query binding solely from the pinned runtime one."""
    from tools import wd_mind_absent_generation_prefix_readonly as prefix
    from tools import wd_mind_e5_lifecycle as lifecycle
    from tools import wd_mind_e5_client as e5_client
    if (type(bundle_root) is not str or not Path(bundle_root).is_absolute()
            or '..' in Path(bundle_root).parts or type(expected_manifest_sha256) is not str
            or re.fullmatch(r'[0-9a-fA-F]{64}', expected_manifest_sha256) is None):
        raise ValueError('bundle')
    bundle = Path(bundle_root)
    manifest_raw = prefix._read_pinned(bundle/'deployment-manifest.json', expected_manifest_sha256, 4*1024*1024)
    manifest = json.loads(manifest_raw.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                          parse_constant=reader._bad_constant)
    if (type(manifest) is not dict or type(manifest.get('schema_version')) is not int
            or manifest['schema_version'] != 1 or type(manifest.get('files')) is not dict):
        raise ValueError('manifest')
    files = manifest['files']
    def pinned(relative, limit):
        pin = files.get(relative)
        return prefix._read_pinned(bundle.joinpath(*relative.split('/')), pin, limit)
    modules = ((sys.modules[__name__], 'wd_mind_e5_snapshot_adapter.py'),
                             (reader, 'wd_mind_content_query_readonly.py'),
                             (prefix, 'wd_mind_absent_generation_prefix_readonly.py'),
                             (lifecycle, 'wd_mind_e5_lifecycle.py'),
                             (e5_client, 'wd_mind_e5_client.py'))
    for module, filename in modules:
        origin = getattr(module, '__file__', None)
        if type(origin) is not str:
            raise ValueError('code_origin')
        relative = 'tools-bootstrap/tools/' + filename
        expected = bundle.joinpath(*relative.split('/'))
        if os.path.normcase(os.path.abspath(origin)) != os.path.normcase(os.path.abspath(expected)):
            raise ValueError('code_origin')
        pinned(relative, 8*1024*1024)
    worker = 'tools-bootstrap/tools/wd_mind_e5_worker.py'
    pinned(worker, 8*1024*1024)
    runtime_raw = pinned('wd-mind-e5-encoder-binding.json', 65536)
    runtime = json.loads(runtime_raw.decode('utf-8'), object_pairs_hook=prefix._unique_object,
                         parse_constant=reader._bad_constant)
    policy = {'prefix':'query: ', 'truncate_chars':2000, 'normalize':True, 'dtype':'float32', 'dim':384}
    if (type(runtime) is not dict
            or set(runtime) != {'schema','encoder','code_sha256','query_policy','passage_policy'}
            or runtime['schema'] != 'wd.mind-memory-encoder-binding.v1'
            or type(files.get(worker)) is not str
            or runtime['code_sha256'] != files[worker].lower()
            or runtime['query_policy'] != policy or runtime['passage_policy'] != dict(policy,prefix='passage: ')
            or any(type(item.get('normalize')) is not bool or type(item.get('truncate_chars')) is not int
                   or type(item.get('dim')) is not int
                   for item in (runtime['query_policy'], runtime['passage_policy']))):
        raise ValueError('runtime_binding')
    reader._q4_identity(runtime['encoder'])
    query = {'schema':'wd.mind-readonly-encoder-binding.v1', 'encoder':runtime['encoder'],
             'code_sha256':runtime['code_sha256'], 'query_policy':runtime['query_policy']}
    query_raw = reader._canonical(query)
    return query_raw, hashlib.sha256(runtime_raw).hexdigest()


class _SingleFlatAdapter(_Adapter):
    """One caller-owned graceful worker; this object never starts a process."""
    __slots__ = ('_bundle', '_manifest', '_query_binding', '_runtime_hash',
                 '_owner', '_owned_process', '_owned_job', '_call_state')

    def __init__(self, client, bundle, manifest, query_binding, runtime_hash):
        super().__init__(client, runtime_hash)
        self._bundle, self._manifest = bundle, manifest
        self._query_binding, self._runtime_hash = query_binding, runtime_hash
        self._owner = client.owner
        self._owned_process = self._owner.resources.process
        self._owned_job = self._owner.resources.job
        self._call_state = threading.local()

    @property
    def encoder_binding_bytes(self):
        return self._query_binding

    @property
    def encoder_binding_sha256(self):
        return hashlib.sha256(self._query_binding).hexdigest()

    @property
    def runtime_binding_sha256(self):
        return self._runtime_hash

    def _request(self, operation, texts):
        self._check()
        output = super()._request(operation, texts)
        self._check()
        return output

    def _require_deadline(self):
        deadline = getattr(self._call_state, 'deadline', None)
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError('deadline')
        return deadline

    def _timed_call(self, operation):
        if getattr(self._call_state, 'deadline', None) is not None:
            raise ValueError('reentrant_call')
        timeout = self._client._timeout
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 1800:
            raise ValueError('timeout')
        self._call_state.deadline = time.monotonic() + float(timeout)
        try:
            result = operation()
            self._require_deadline()
            return result
        finally:
            del self._call_state.deadline

    def passage(self, texts):
        return _run(lambda: self._timed_call(lambda: super(_SingleFlatAdapter, self).passage(texts)))

    def query(self, text):
        return _run(lambda: self._timed_call(lambda: super(_SingleFlatAdapter, self).query(text)))

    def _check(self):
        self._require_deadline()
        from tools import wd_mind_e5_client as e5_client, wd_mind_e5_lifecycle as lifecycle
        client = self._client
        if (type(client) is not e5_client.GracefulE5WorkerClient
                or type(self._owner) is not lifecycle._Owner or client.owner is not self._owner
                or client._process is not client._transport or client._transport.owner is not self._owner
                or self._owned_process is None or self._owner.resources.process is not self._owned_process
                or self._owned_job is None or self._owner.resources.job is not self._owned_job
                or client._binding_sha256 != self._runtime_hash
                or client._bulk_contract_sha256 != e5_client.BULK_CONTRACT_SHA256
                or self._owner._bulk_contract_sha256 != e5_client.BULK_CONTRACT_SHA256
                or client._closed is not False
                or _flat_bundle_bindings(self._bundle, self._manifest)
                   != (self._query_binding, self._runtime_hash)):
            raise ValueError('single_flat_binding_changed')
        status = self._owner.status()
        if (type(status) is not dict or status.get('state') != 'RUNNING'
                or status.get('owned') is not True or status.get('reason') is not None):
            raise ValueError('single_flat_owner_unavailable')
        self._require_deadline()

    def _passage(self, texts):
        # One bounded B16 request has one nonce/digest. The existing client
        # sends up to four transport frames with the same binding, retaining
        # request ordering and all 4-record frame/output limits.
        if (type(texts) not in (tuple, list) or not 1 <= len(texts) <= _MAX_PASSAGES
                or any(type(text) is not str or not text.startswith('passage: ')
                       or not text[9:].strip() or len(text) > 2009 or '\x00' in text for text in texts)):
            raise ValueError('passage')
        return self._request('refresh', tuple(texts))

    def _encode_supervised(self, payload):
        # Recheck every independently transported group, not merely the
        # enclosing B16, while the existing client owns the request lock.
        deadline = self._require_deadline()
        if deadline is None:
            raise ValueError('missing_call_deadline')
        return self._client.encode_supervised(payload, frame_check=self._check, call_deadline=deadline)

    def close(self, *, timeout=0):
        """Request cooperative EOF; a HOLD keeps the owner and handles reachable."""
        def close_owned():
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
                raise ValueError('timeout')
            from tools import wd_mind_e5_client as e5_client
            if (type(self._client) is not e5_client.GracefulE5WorkerClient or self._client.owner is not self._owner
                    or self._owner.resources.process is not self._owned_process
                    or self._owner.resources.job is not self._owned_job):
                raise ValueError('owner_changed')
            return self._client.close(timeout=timeout)
        return _run(close_owned)


def create_single_flat_adapter(*, enabled=False, bundle_root=None, expected_manifest_sha256=None, client=None):
    """Bind one already-owned worker, without rotation or an independent supervisor.

    The caller independently authorizes the manifest, process and model, retains
    the adapter/owner through HOLD, and controls the source and launch lifecycle.
    The binding proves consistency only, not execution, sender authentication,
    or resistance to changes reverted between checks (same-user ABA).
    Its deadline rejects a late return but cannot interrupt a blocked check.
    """
    def create():
        from tools import wd_mind_e5_client as e5_client
        if enabled is not True or type(client) is not e5_client.GracefulE5WorkerClient:
            raise ValueError('disabled_or_client')
        query_raw, runtime_hash = _flat_bundle_bindings(bundle_root, expected_manifest_sha256)
        result = _SingleFlatAdapter(client, bundle_root, expected_manifest_sha256, query_raw, runtime_hash)
        result._check()
        return result
    return _run(create)


def _rotating_bundle_bindings(bundle_root, expected_manifest_sha256, rotating_module):
    """Check loaded modules against the same independently pinned bundle."""
    from tools import wd_mind_absent_generation_prefix_readonly as prefix
    runtime = sys.modules.get('tools.wd_mind_full_runtime')
    if runtime is None:
        raise ValueError('runtime_not_loaded')
    query_raw, runtime_hash = _flat_bundle_bindings(bundle_root, expected_manifest_sha256)
    bundle = Path(bundle_root)
    raw = prefix._read_pinned(bundle / 'deployment-manifest.json', expected_manifest_sha256, 4 * 1024 * 1024)
    document = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                          parse_constant=reader._bad_constant)
    for module, name in ((rotating_module, 'wd_mind_e5_rotating_client.py'),
                         (runtime, 'wd_mind_full_runtime.py')):
        relative = 'tools-bootstrap/tools/' + name
        path = bundle.joinpath(*relative.split('/'))
        canonical_name = 'tools.' + name[:-3]
        if (type(module) is not types.ModuleType or sys.modules.get(canonical_name) is not module
                or type(getattr(module, '__file__', None)) is not str
                or os.path.normcase(os.path.abspath(module.__file__)) != os.path.normcase(os.path.abspath(path))):
            raise ValueError('rotator_origin')
        prefix._read_pinned(path, document['files'].get(relative), 8 * 1024 * 1024)
    return query_raw, runtime_hash


def _load_pinned_rotating_module(bundle_root, expected_manifest_sha256):
    if not _ROTATOR_LOAD_LOCK.acquire(blocking=False):
        raise ValueError('rotator_load_busy')
    try:
        return _load_pinned_rotating_module_locked(bundle_root, expected_manifest_sha256)
    finally:
        _ROTATOR_LOAD_LOCK.release()


def _load_pinned_rotating_module_locked(bundle_root, expected_manifest_sha256):
    """Authenticate the rotator path and bytes before permitting an import.

    The caller must already have loaded the separately verified runtime owner.
    A same-user change reverted between checks remains outside this guarantee.
    """
    global _ROTATOR_ATTESTATION
    from tools import wd_mind_absent_generation_prefix_readonly as prefix
    _flat_bundle_bindings(bundle_root, expected_manifest_sha256)
    bundle = Path(bundle_root)
    raw = prefix._read_pinned(bundle / 'deployment-manifest.json', expected_manifest_sha256, 4 * 1024 * 1024)
    document = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                          parse_constant=reader._bad_constant)
    relative = 'tools-bootstrap/tools/wd_mind_e5_rotating_client.py'
    path = bundle.joinpath(*relative.split('/'))
    name = 'tools.wd_mind_e5_rotating_client'
    module = sys.modules.get(name)
    package = sys.modules.get('tools')
    if type(package) is not types.ModuleType:
        raise ValueError('package_not_loaded')
    if module is None:
        spec = importlib.util.find_spec(name)
        origin = None if spec is None else spec.origin
        # Never replace an existing package attribute with a different module.
        if getattr(package, 'wd_mind_e5_rotating_client', None) is not None:
            raise ValueError('rotator_module_conflict')
    else:
        if (type(_ROTATOR_ATTESTATION) is not tuple or len(_ROTATOR_ATTESTATION) != 3
                or _ROTATOR_ATTESTATION[0] is not module
                or _ROTATOR_ATTESTATION[1:] != (str(path), expected_manifest_sha256)):
            raise ValueError('rotator_unverified_preload')
        origin = getattr(module, '__file__', None)
        if getattr(package, 'wd_mind_e5_rotating_client', module) is not module:
            raise ValueError('rotator_module_conflict')
    if (type(origin) is not str
            or os.path.normcase(os.path.abspath(origin)) != os.path.normcase(os.path.abspath(path))):
        raise ValueError('rotator_origin')
    verified = prefix._read_pinned(path, document['files'].get(relative), 8 * 1024 * 1024)
    if module is None:
        # Execute the authenticated bytes directly: a conventional import can
        # use a stale or substituted bytecode cache after the source check.
        module = types.ModuleType(name)
        module.__file__, module.__package__, module.__spec__ = str(path), 'tools', spec
        sys.modules[name] = module
        try:
            exec(compile(verified, str(path), 'exec', dont_inherit=True), module.__dict__)
            _rotating_bundle_bindings(bundle_root, expected_manifest_sha256, module)
            present = getattr(package, 'wd_mind_e5_rotating_client', None)
            if present is not None and present is not module:
                raise ValueError('rotator_module_conflict')
            setattr(package, 'wd_mind_e5_rotating_client', module)
            _ROTATOR_ATTESTATION = (module, str(path), expected_manifest_sha256)
        except BaseException:
            if sys.modules.get(name) is module:
                del sys.modules[name]
            raise
    else:
        _rotating_bundle_bindings(bundle_root, expected_manifest_sha256, module)
    return module


class _RotatingFlatAdapter(_Adapter):
    """A pinned rotating owner; only the owner may start or retire workers."""
    __slots__ = ('_bundle', '_manifest', '_query_binding', '_runtime_hash', '_snapshot',
                 '_rotator', '_rotating_module', '_factory', '_factory_bytes', '_source_check',
                 '_timeout', '_call_state')

    def __init__(self, client, bundle, manifest, query_binding, runtime_hash, snapshot, rotating_module):
        runtime = sys.modules.get('tools.wd_mind_full_runtime')
        if runtime is None:
            raise ValueError('runtime_not_loaded')
        super().__init__(client, runtime_hash)
        self._bundle, self._manifest, self._query_binding = bundle, manifest, query_binding
        self._runtime_hash, self._snapshot, self._rotator = runtime_hash, snapshot, client
        self._rotating_module = rotating_module
        self._factory, self._source_check = client._factory, client._source_check
        self._factory_bytes = runtime._SingleManifestOwner._factory_snapshot(self._factory)
        self._timeout = client.call_timeout
        self._call_state = threading.local()

    @property
    def encoder_binding_bytes(self):
        return self._query_binding

    @property
    def encoder_binding_sha256(self):
        return hashlib.sha256(self._query_binding).hexdigest()

    @property
    def runtime_binding_sha256(self):
        return self._runtime_hash

    def _require_deadline(self):
        deadline = getattr(self._call_state, 'deadline', None)
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError('deadline')
        return deadline

    def _static_check(self):
        rotating = self._rotating_module
        e5_client = sys.modules.get('tools.wd_mind_e5_client')
        runtime = sys.modules.get('tools.wd_mind_full_runtime')
        if (sys.modules.get('tools.wd_mind_e5_rotating_client') is not rotating
                or type(_ROTATOR_ATTESTATION) is not tuple or len(_ROTATOR_ATTESTATION) != 3
                or _ROTATOR_ATTESTATION[0] is not rotating
                or _ROTATOR_ATTESTATION[1:] != (str(Path(self._bundle) / 'tools-bootstrap' / 'tools' / 'wd_mind_e5_rotating_client.py'), self._manifest)
                or e5_client is None or runtime is None):
            raise ValueError('rotating_module_changed')
        rotator = self._client
        if (rotator is not self._rotator or type(rotator) is not rotating.RotatingGracefulE5Client
                or rotator._factory is not self._factory or type(self._factory) is not runtime._ManifestWorkerFactory
                or rotator._source_check is not self._source_check
                or self._factory._bundle != self._bundle or self._factory._manifest != self._manifest
                or self._factory._bulk != e5_client.BULK_CONTRACT_SHA256
                or rotator._binding != self._runtime_hash or rotator._snapshot != self._snapshot
                or rotator._bulk_contract_sha256 != e5_client.BULK_CONTRACT_SHA256
                or rotator._closed is not False or rotator._failed is not False or rotator._hold is not False
                or type(rotator._owner_conflicts) is not list or rotator._owner_conflicts
                or rotator.call_timeout != self._timeout or self._factory._timeout != self._timeout
                or not hmac.compare_digest(runtime._SingleManifestOwner._factory_snapshot(self._factory),
                                           self._factory_bytes)
                or _rotating_bundle_bindings(self._bundle, self._manifest, rotating)
                   != (self._query_binding, self._runtime_hash)):
            raise ValueError('rotating_flat_binding_changed')
        self._require_deadline()

    def _worker_identity(self):
        e5_client = sys.modules.get('tools.wd_mind_e5_client')
        lifecycle = sys.modules.get('tools.wd_mind_e5_lifecycle')
        if e5_client is None or lifecycle is None:
            raise ValueError('worker_module_changed')
        rotator = self._client
        worker, owner = rotator.active_client, rotator.owner
        worker_timeout = rotator._worker_timeout
        if (type(worker) is not e5_client.GracefulE5WorkerClient or type(owner) is not lifecycle._Owner
                or worker.owner is not owner or worker._process is not worker._transport
                or worker._transport.owner is not owner or worker._closed is not False
                or worker._binding_sha256 != self._runtime_hash
                or worker._bulk_contract_sha256 != e5_client.BULK_CONTRACT_SHA256
                or type(worker_timeout) not in (int, float) or not math.isfinite(worker_timeout)
                or not 0 < worker_timeout <= self._timeout
                or type(worker._timeout) is not type(worker_timeout)
                or worker._timeout != worker_timeout
                or owner._bulk_contract_sha256 != e5_client.BULK_CONTRACT_SHA256
                or owner.resources.process is None or owner.resources.job is None
                or owner.resources.process is not rotator._owned_process
                or owner.resources.job is not rotator._owned_job):
            raise ValueError('rotating_worker_changed')
        status = owner.status()
        if (type(status) is not dict or status.get('state') != 'RUNNING'
                or status.get('owned') is not True or status.get('reason') is not None):
            raise ValueError('rotating_worker_unavailable')
        return worker, owner, owner.resources.process, owner.resources.job

    def _check(self):
        self._static_check()
        status = self._client.status()
        if type(status) is not dict:
            raise ValueError('rotating_status')
        if status.get('state') == 'NEW' and status.get('owned') is False:
            if (self._client.active_client is not None or self._client.owner is not None
                    or self._client._owned_process is not None or self._client._owned_job is not None):
                raise ValueError('rotating_unowned_worker')
        elif status.get('state') == 'RUNNING' and status.get('owned') is True:
            self._worker_identity()
        else:
            raise ValueError('rotating_unavailable')
        self._require_deadline()

    def _frame_check(self):
        # Called with the rotator lock held: status() would return BUSY here.
        self._static_check()
        identity = self._worker_identity()
        anchor = getattr(self._call_state, 'frame_anchor', None)
        if anchor is None:
            self._call_state.frame_anchor = identity
        elif any(current is not previous for current, previous in zip(identity, anchor)):
            raise ValueError('rotating_generation_changed')
        self._call_state.frame_checks += 1
        self._require_deadline()

    def _timed_call(self, operation):
        if getattr(self._call_state, 'deadline', None) is not None:
            raise ValueError('reentrant_call')
        if type(self._timeout) not in (int, float) or not math.isfinite(self._timeout) or not 0 < self._timeout <= 1800:
            raise ValueError('timeout')
        self._call_state.deadline = time.monotonic() + float(self._timeout)
        self._call_state.frame_anchor = None
        self._call_state.frame_checks = 0
        try:
            result = operation()
            self._require_deadline()
            return result
        finally:
            del self._call_state.frame_checks
            del self._call_state.frame_anchor
            del self._call_state.deadline

    def _request(self, operation, texts):
        self._check()
        output = super()._request(operation, texts)
        anchor = self._call_state.frame_anchor
        if anchor is None or self._call_state.frame_checks != 2 * ((len(texts) + _BATCH - 1) // _BATCH):
            raise ValueError('missing_frame_checks')
        self._check()
        identity = self._worker_identity()
        if any(current is not previous for current, previous in zip(identity, anchor)):
            raise ValueError('rotating_generation_changed')
        return output

    def _passage(self, texts):
        if (type(texts) not in (tuple, list) or not 1 <= len(texts) <= _MAX_PASSAGES
                or any(type(text) is not str or not text.startswith('passage: ')
                       or not text[9:].strip() or len(text) > 2009 or '\x00' in text for text in texts)):
            raise ValueError('passage')
        return self._request('refresh', tuple(texts))

    def _encode_supervised(self, payload):
        deadline = self._require_deadline()
        if deadline is None:
            raise ValueError('missing_call_deadline')
        return self._client.encode_supervised(payload, frame_check=self._frame_check, call_deadline=deadline)

    def passage(self, texts):
        return _run(lambda: self._timed_call(lambda: self._passage(texts)))

    def query(self, text):
        return _run(lambda: self._timed_call(lambda: self._query(text)))

    def close(self, *, timeout=0):
        def close_owned():
            rotating = self._rotating_module
            if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 30
                    or self._client is not self._rotator
                    or sys.modules.get('tools.wd_mind_e5_rotating_client') is not rotating
                    or type(self._rotator) is not rotating.RotatingGracefulE5Client):
                raise ValueError('rotating_close')
            return self._rotator.close(timeout=timeout)
        return _run(close_owned)


def create_rotating_flat_adapter(*, enabled=False, bundle_root=None, expected_manifest_sha256=None,
                                 client=None, expected_snapshot_sha256=None):
    """Bind an inert or running exact rotator; never start a worker here."""
    def create():
        if (enabled is not True
                or type(expected_snapshot_sha256) is not str or not reader._hex(expected_snapshot_sha256)):
            raise ValueError('disabled_or_client')
        rotating = _load_pinned_rotating_module(bundle_root, expected_manifest_sha256)
        if type(client) is not rotating.RotatingGracefulE5Client:
            raise ValueError('disabled_or_client')
        query_raw, runtime_hash = _rotating_bundle_bindings(bundle_root, expected_manifest_sha256, rotating)
        result = _RotatingFlatAdapter(client, bundle_root, expected_manifest_sha256,
                                      query_raw, runtime_hash, expected_snapshot_sha256, rotating)
        result._check()
        return result
    return _run(create)
