"""Default-off in-memory complete snapshot assembly; no live installation.

The caller independently authorizes the manifest, interpreter and code.
Source authentication, same-user ABA resistance and continuity remain UNKNOWN.
The HMAC key lives only in this process and is not a cross-restart key store.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import struct
import sys
import threading

from tools import wd_mind_absent_generation_prefix_readonly as prefix
from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_startup_content as startup
from waggledance.core import bridge_log_reader as bridge

_POLICY = 'wd-mind-message-policy.json'


def resolve_manifest_runtime_location(*, enabled=False, bundle_root=None, expected_manifest_sha256=None):
    """Verify the five manifest-pinned runtime descriptors and two asset trees.

    Read-only evidence for the observed interval; it neither starts a worker
    nor authenticates the caller, interpreter process or later filesystem state.
    """
    result = None
    try:
        if (enabled is not True or type(bundle_root) is not str or not Path(bundle_root).is_absolute()
                or '..' in Path(bundle_root).parts or not reader._hex(expected_manifest_sha256)):
            raise ValueError('input')
        bundle = Path(bundle_root)
        controls = _bundle_inputs(bundle, expected_manifest_sha256)
        manifest_bytes = prefix._read_pinned(bundle / 'deployment-manifest.json', expected_manifest_sha256,
                                             prefix.MAX_CONTROL_BYTES)
        document = json.loads(manifest_bytes.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                              parse_constant=reader._bad_constant)
        commit = document.get('source_commit')
        if type(commit) is not str or re.fullmatch(r'[0-9a-f]{40}', commit) is None:
            raise ValueError('source_commit')
        descriptors = {
            'wd-mind-runtime-manifest.json': ('wd.q4-runtime-manifest.v1', 8 * 1024 * 1024),
            'wd-mind-interpreter-identity.json': ('wd.q4-interpreter-identity.v1', 1024 * 1024),
            'wd-mind-e5-encoder-binding.json': ('wd.mind-memory-encoder-binding.v1', 65536),
            'wd-mind-runtime-location.json': ('wd.mind-runtime-location.v1', 16384),
            'wd-mind-message-policy.json': ('wd.mind-content-policy.v3', 65536),
        }
        captured, parsed = {}, {}
        for name, (schema, limit) in descriptors.items():
            pin = document['files'].get(name)
            raw = prefix._read_pinned(bundle / name, pin, limit)
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=prefix._unique_object,
                               parse_constant=reader._bad_constant)
            if type(value) is not dict or value.get('schema') != schema:
                raise ValueError('descriptor')
            captured[name], parsed[name] = raw, value
        location = parsed['wd-mind-runtime-location.json']
        keys = {'schema', 'source_commit', 'runtime_root', 'interpreter_root', 'runtime_manifest_sha256',
                'interpreter_identity_sha256', 'encoder_binding_sha256'}
        if type(location) is not dict or set(location) != keys or location['source_commit'] != commit:
            raise ValueError('location')
        for field, name in (('runtime_manifest_sha256', 'wd-mind-runtime-manifest.json'),
                            ('interpreter_identity_sha256', 'wd-mind-interpreter-identity.json'),
                            ('encoder_binding_sha256', 'wd-mind-e5-encoder-binding.json')):
            if not reader._hex(location[field]) or not hmac.compare_digest(location[field], reader._sha(captured[name])):
                raise ValueError('cross_pin')
        roots = []
        for field in ('runtime_root', 'interpreter_root'):
            value = location[field]
            if type(value) is not str or not Path(value).is_absolute() or '..' in Path(value).parts:
                raise ValueError('root')
            roots.append(Path(value))
        left, right = (os.path.normcase(os.path.abspath(str(path))) for path in roots)
        common = os.path.normcase(os.path.commonpath((left, right)))
        if common in (left, right):
            raise ValueError('root_overlap')
        from tools import wd_mind_runtime_manifest as runtime_manifest
        from tools import wd_mind_interpreter_identity as interpreter_identity
        for module, name in ((runtime_manifest, 'wd_mind_runtime_manifest.py'),
                             (interpreter_identity, 'wd_mind_interpreter_identity.py')):
            relative = 'tools-bootstrap/tools/' + name
            expected = bundle.joinpath(*relative.split('/'))
            if type(getattr(module, '__file__', None)) is not str or not startup._same_path(Path(module.__file__), expected):
                raise ValueError('code_origin')
            prefix._read_pinned(expected, document['files'].get(relative), 8 * 1024 * 1024)
        from tools import wd_mind_e5_snapshot_adapter as adapter_module
        _, binding_digest = adapter_module._flat_bundle_bindings(bundle_root, expected_manifest_sha256)
        if not hmac.compare_digest(binding_digest, location['encoder_binding_sha256']):
            raise ValueError('binding')
        runtime_document = parsed['wd-mind-runtime-manifest.json']
        if set(runtime_document) != {'schema', 'generation', 'revision', 'files'}:
            raise ValueError('runtime')
        binding_document = parsed['wd-mind-e5-encoder-binding.json']
        weights = [entry for entry in runtime_document['files']
                   if type(entry) is dict and entry.get('path') == 'model/model.safetensors']
        if (len(weights) != 1 or binding_document['encoder']['revision'] != runtime_document['revision']
                or binding_document['encoder']['weights_sha256'] != weights[0].get('sha256')):
            raise ValueError('model_binding')
        runtime_path = bundle / 'wd-mind-runtime-manifest.json'
        identity_bytes = captured['wd-mind-interpreter-identity.json']
        parsed_identity = interpreter_identity.parse_pinned_interpreter_descriptor(
            identity_bytes, location['interpreter_identity_sha256'])
        _, identity_files, _, _, relative_exe = parsed_identity
        executable = roots[1].joinpath(*relative_exe.split('/'))
        executable_sha = identity_files[relative_exe][1]
        for _ in range(2):
            verified_runtime = runtime_manifest.verify_runtime(str(roots[0]), str(runtime_path),
                location['runtime_manifest_sha256'], expected_generation=runtime_document['generation'],
                expected_revision=runtime_document['revision'])
            verified_interpreter = interpreter_identity.verify_interpreter_identity(enabled=True, root=str(roots[1]),
                descriptor_bytes=identity_bytes, expected_sha256=location['interpreter_identity_sha256'],
                expected_interpreter=str(executable), expected_interpreter_sha256=executable_sha)
            if (type(verified_runtime) is not dict or type(verified_interpreter) is not dict
                    or verified_interpreter.get('status') != 'VERIFIED'):
                raise ValueError('verification')
        if _bundle_inputs(bundle, expected_manifest_sha256) != controls:
            raise ValueError('bundle_changed')
        for name, (_, limit) in descriptors.items():
            if prefix._read_pinned(bundle / name, document['files'].get(name), limit) != captured[name]:
                raise ValueError('descriptor_changed')
        result = {'runtime_root': str(roots[0]), 'runtime_manifest': str(runtime_path),
            'runtime_manifest_sha256': location['runtime_manifest_sha256'],
            'runtime_generation': runtime_document['generation'], 'runtime_revision': runtime_document['revision'],
            'interpreter_root': str(roots[1]), 'interpreter': str(executable), 'interpreter_sha256': executable_sha,
            'encoder_binding_sha256': location['encoder_binding_sha256'],
            'runtime_status': 'VERIFIED', 'interpreter_status': 'VERIFIED', 'authority': 'none',
            'later_filesystem_state': 'UNKNOWN'}
    except Exception:
        pass
    if result is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return result


def _fixed_worker_audit_lock():
    """The deployed worker's inert sentinel is fixed, not caller-selected."""
    return Path(r'C:\Python\project2\.codex-audit\wd-mind-runtime\q4-reader.lock')


def _worker_factory_preflight(bundle_root, manifest_sha256, expected_lock):
    from tools import wd_mind_runtime_child as child
    from tools import wd_mind_runtime_process as process
    bundle = Path(bundle_root)
    raw = prefix._read_pinned(bundle / 'deployment-manifest.json', manifest_sha256, prefix.MAX_CONTROL_BYTES)
    manifest = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                          parse_constant=reader._bad_constant)
    for module, relative in ((child, 'tools-bootstrap/tools/wd_mind_runtime_child.py'),
                             (process, 'tools-bootstrap/tools/wd_mind_runtime_process.py')):
        path = bundle.joinpath(*relative.split('/'))
        if type(getattr(module, '__file__', None)) is not str or not startup._same_path(Path(module.__file__), path):
            raise ValueError('worker_origin')
        prefix._read_pinned(path, manifest['files'].get(relative), 8 * 1024 * 1024)
    for relative in ('tools-bootstrap/tools/wd_mind_runtime_bootstrap.py',
                     'tools-bootstrap/tools/wd_mind_runtime_mutex.py'):
        prefix._read_pinned(bundle.joinpath(*relative.split('/')), manifest['files'].get(relative), 8 * 1024 * 1024)
    lock = _fixed_worker_audit_lock()
    if not startup._same_path(lock, expected_lock):
        raise ValueError('audit_lock_changed')
    child._require_safe_writer_scope(str(lock), None)
    return child


class _ManifestWorkerFactory:
    """Reusable rotator factory; no worker is created by its constructor."""
    __slots__ = ('_bundle', '_manifest', '_location', '_lock', '_timeout', '_lock_timeout', '_bulk')

    def __init__(self, bundle, manifest, location, lock, timeout, lock_timeout, bulk):
        self._bundle, self._manifest, self._location, self._lock = bundle, manifest, location, lock
        self._timeout, self._lock_timeout, self._bulk = timeout, lock_timeout, bulk

    def __repr__(self):
        return '<PrivateMindManifestWorkerFactory>'

    def __call__(self, *, owner_sink=None):
        try:
            current = resolve_manifest_runtime_location(enabled=True, bundle_root=self._bundle,
                                                         expected_manifest_sha256=self._manifest)
            if current != self._location:
                raise ValueError('location_changed')
            child = _worker_factory_preflight(self._bundle, self._manifest, self._lock)
            args = {'runtime-root': current['runtime_root'], 'runtime-manifest': current['runtime_manifest'],
                    'runtime-manifest-sha256': current['runtime_manifest_sha256'],
                    'runtime-generation': current['runtime_generation'], 'runtime-revision': current['runtime_revision'],
                    'lock-file': str(self._lock), 'lock-timeout': format(self._lock_timeout, '.15g')}
        except Exception:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        # Preserve the graceful launcher's returned owner, including HOLD. Do
        # not add a fallible post-launch check that could discard ownership.
        return child.start_e5_worker_graceful(enabled=True, bundle_root=self._bundle,
            deployment_manifest_sha256=self._manifest, interpreter=current['interpreter'],
            interpreter_sha256=current['interpreter_sha256'], interpreter_root=current['interpreter_root'],
            tuple_args=args, timeout=self._timeout, scratch_root=None, worker_only=True,
            bulk_contract_sha256=self._bulk, owner_sink=owner_sink)


def create_manifest_worker_factory(*, enabled=False, bundle_root=None, expected_manifest_sha256=None,
                                   worker_timeout_seconds=None, lock_timeout_seconds=None,
                                   bulk_contract_sha256=None):
    """Prepare a pinned graceful factory, without creating a worker or files."""
    result = None
    try:
        if (enabled is not True or type(worker_timeout_seconds) not in (int, float)
                or not math.isfinite(worker_timeout_seconds) or not 0 < worker_timeout_seconds <= 1800
                or type(lock_timeout_seconds) not in (int, float) or not math.isfinite(lock_timeout_seconds)
                or not 0 <= lock_timeout_seconds <= 30):
            raise ValueError('timeout')
        # The pinned worker contract is shared by the single-worker route.
        # Preparing a factory must not require or start the optional rotator.
        from tools import wd_mind_e5_client as worker_client
        if bulk_contract_sha256 != worker_client.BULK_CONTRACT_SHA256:
            raise ValueError('bulk')
        location = resolve_manifest_runtime_location(enabled=True, bundle_root=bundle_root,
                                                      expected_manifest_sha256=expected_manifest_sha256)
        lock = _fixed_worker_audit_lock()
        _worker_factory_preflight(bundle_root, expected_manifest_sha256, lock)
        result = _ManifestWorkerFactory(bundle_root, expected_manifest_sha256, location, lock,
                                        float(worker_timeout_seconds), float(lock_timeout_seconds), bulk_contract_sha256)
    except Exception:
        pass
    if result is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return result


class FullRuntimeRefused(ValueError):
    """Fixed refusal without source paths, contents or key material."""


class _PrivateBinding:
    __slots__ = ('__snapshot', '__bundle', '__manifest', '__controls', '__args')
    authority = 'none'

    def __init__(self, snapshot, bundle, manifest, controls, args):
        self.__snapshot = snapshot
        self.__bundle, self.__manifest, self.__controls, self.__args = bundle, manifest, controls, args

    def __repr__(self):
        return '<PrivateMindFullRuntimeBinding>'

    def snapshot_for_query(self):
        result = None
        try:
            if _bundle_inputs(self.__bundle, self.__manifest) != self.__controls:
                raise ValueError('bundle_changed')
            startup.bind_startup_source(**self.__args)
            reader._verified_complete(self.__snapshot)
            result = dict(self.__snapshot)
            result['messages'] = dict(result['messages'])
        except Exception:
            pass
        if result is None:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return result


def _bundle_inputs(bundle, expected_manifest):
    manifest_bytes = prefix._read_pinned(bundle / 'deployment-manifest.json', expected_manifest,
                                        prefix.MAX_CONTROL_BYTES)
    manifest = json.loads(manifest_bytes.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                          parse_constant=reader._bad_constant)
    if (type(manifest) is not dict or type(manifest.get('schema_version')) is not int
            or manifest['schema_version'] != 1 or type(manifest.get('files')) is not dict):
        raise ValueError('manifest')
    files = manifest['files']
    def pinned(relative, limit):
        return prefix._read_pinned(bundle.joinpath(*relative.split('/')), files.get(relative), limit)
    for name, relative in (('tools.wd_mind_full_runtime', 'tools-bootstrap/tools/wd_mind_full_runtime.py'),
                           ('tools.wd_mind_content_query_readonly',
                            'tools-bootstrap/tools/wd_mind_content_query_readonly.py')):
        module = sys.modules.get(name)
        origin = getattr(module, '__file__', None)
        path = bundle.joinpath(*relative.split('/'))
        if type(origin) is not str or not startup._same_path(Path(origin), path):
            raise ValueError('code_origin')
        pinned(relative, 8 * 1024 * 1024)
    startup._check_packaged_code(bundle, expected_manifest)
    fleet_bytes = pinned('wd-fleet.json', 1024 * 1024)
    policy_bytes = pinned(_POLICY, 65536)
    def load(data):
        return json.loads(data.decode('utf-8'), object_pairs_hook=prefix._unique_object,
                          parse_constant=reader._bad_constant)
    fleet, policy = load(fleet_bytes), load(policy_bytes)
    if type(fleet) is not dict:
        raise ValueError('fleet')
    lanes, supervisor = fleet.get('lanes'), fleet.get('tools_supervisor')
    if type(lanes) is not list or type(supervisor) is not dict:
        raise ValueError('fleet_roster')
    entries = lanes + [supervisor]
    if any(type(entry) is not dict or type(entry.get('agent')) is not str
           or type(entry.get('agent_uuid')) is not str for entry in entries):
        raise ValueError('fleet_roster')
    roster = {'schema': 'wd.q4-roster-snapshot.v1',
              'lanes': sorted(({'agent': entry['agent'], 'agent_uuid': entry['agent_uuid']} for entry in lanes),
                              key=lambda entry: entry['agent']),
              'tools_supervisor': {'agent': supervisor['agent'], 'agent_uuid': supervisor['agent_uuid']}}
    reader._roster(roster)
    roster_bytes = reader._canonical(roster)
    parsed_policy = reader._message_policy(policy, None)
    if parsed_policy['lanes'] != reader._BUILD_LANES:
        raise ValueError('message_scope')
    return fleet_bytes, roster_bytes, policy_bytes


def assemble_full_runtime(*, enabled=False, bundle_root=None, expected_manifest_sha256=None):
    """Return a private verified snapshot; never accept caller-selected log paths."""
    result = None
    try:
        if (enabled is not True or type(bundle_root) is not str or not Path(bundle_root).is_absolute()
                or '..' in Path(bundle_root).parts or type(expected_manifest_sha256) is not str
                or not reader._hex(expected_manifest_sha256)):
            raise ValueError('input')
        bundle = Path(bundle_root)
        controls = _bundle_inputs(bundle, expected_manifest_sha256)
        observed = prefix.observe_prefix(enabled=True, bundle_root=bundle_root,
                                         expected_manifest_sha256=expected_manifest_sha256,
                                         scope='full_prefix_v1')
        meta = dict(observed.metadata)
        observed = None  # Unscreened events are never returned or logged.
        if (meta['manifest_sha256'] != expected_manifest_sha256 or meta['generation'] != 'UNKNOWN'
                or meta['sidecar_observation'] != 'ABSENT_AT_CHECKS'):
            raise ValueError('observation')
        source, sidecar, fleet_pin = prefix._deployment_source(bundle_root, expected_manifest_sha256)
        if not hmac.compare_digest(meta['fleet_sha256'], fleet_pin):
            raise ValueError('fleet')
        cursor = bridge.BridgeCursor(meta['end_offset'], meta['file_identity'], None)
        args = dict(enabled=True, bundle_root=bundle_root, expected_manifest_sha256=expected_manifest_sha256,
                    source_path=str(source), generation_path=str(sidecar), cursor=cursor,
                    expected_prefix_sha256=meta['prefix_sha256'])
        bound = startup.bind_startup_source(**args)
        scope = reader._ALL_TASK_SCOPE_BYTES
        nonreview = reader._canonical({'schema': 'wd.q4-non-review-policy.v2', 'task_scope': 'all_tasks'})
        identifier = reader._canonical({'schema': 'wd.mind-identifier-policy.v2', 'identifiers': []})
        key = secrets.token_bytes(32)
        if type(key) is not bytes or len(key) != 32:
            raise ValueError('key')
        tag = hmac.new(key, identifier, hashlib.sha256).hexdigest()
        roster_bytes, message_policy = controls[1], controls[2]
        sha = lambda data: hashlib.sha256(data).hexdigest()
        decision = dict(reader.module_sha256s(), schema=reader._ALL_TASK_DECISION_SCHEMA,
                        decision='enable_content_route', projection_schema=reader._ALL_TASK_COMBINED_SCHEMA,
                        scope=reader._ALL_TASK_ROUTE_SCOPE, nonreview_policy_sha256=sha(nonreview),
                        roster_sha256=sha(roster_bytes), message_policy_sha256=sha(message_policy),
                        doc_policy_sha256=None, source_prefix={'sha256': bound.prefix_sha256,
                            'offset': bound.cursor.offset, 'generation': None},
                        message_projection_schema=reader._ALL_TASK_MESSAGE_SCHEMA,
                        review_signal_excluded=False, approved_tasks_sha256=sha(scope),
                        identifier_policy_hmac=tag)
        decision_bytes = reader._canonical(decision)
        inputs = {'enabled': True, 'messages': {'source_path': bound.source_path,
            'generation_path': bound.generation_path, 'end_cursor': bound.cursor,
            'policy_bytes': message_policy, 'policy_sha256': sha(message_policy)}, 'documents': None,
            'decision_bytes': decision_bytes, 'decision_sha256': sha(decision_bytes),
            'policy_bytes': nonreview, 'policy_sha256': sha(nonreview),
            'roster_bytes': roster_bytes, 'roster_sha256': sha(roster_bytes),
            'approved_tasks_bytes': scope, 'approved_tasks_sha256': sha(scope),
            'identifier_policy_binding': (identifier, key, tag)}
        projection = reader.rederive_complete_message_projection(**inputs)
        projection_bytes = reader._serialized(projection)
        snapshot = dict(inputs, projection_bytes=projection_bytes, projection_sha256=sha(projection_bytes))
        reader._verified_complete(snapshot)
        if _bundle_inputs(bundle, expected_manifest_sha256) != controls:
            raise ValueError('bundle_changed')
        startup.bind_startup_source(**args)
        reader._verified_complete(snapshot)
        result = _PrivateBinding(snapshot, bundle, expected_manifest_sha256, controls, args)
    except Exception:
        pass
    if result is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return result


class _PrivateFlatMemory:
    __slots__ = ('binding', 'snapshot', 'manifest', 'ids', 'vectors', 'encoder_binding')

    def __init__(self, binding, snapshot, manifest, ids, vectors, encoder_binding):
        self.binding, self.snapshot, self.manifest = binding, snapshot, manifest
        self.ids, self.vectors, self.encoder_binding = ids, vectors, encoder_binding

    def __repr__(self):
        return '<PrivateMindFlatMemory>'


def _checked_encoder_binding(raw, expected):
    binding = reader._pinned(raw, expected)
    if (type(binding) is not dict
            or set(binding) != {'schema', 'encoder', 'code_sha256', 'query_policy'}
            or binding['schema'] != 'wd.mind-readonly-encoder-binding.v1'
            or not reader._hex(binding['code_sha256'])
            or binding['query_policy'] != {'prefix': 'query: ', 'truncate_chars': 2000,
                'normalize': True, 'dtype': 'float32', 'dim': 384}
            or type(binding['query_policy'].get('normalize')) is not bool
            or type(binding['query_policy'].get('dim')) is not int):
        raise ValueError('encoder_binding')
    reader._q4_identity(binding['encoder'])
    return binding


def _flat_snapshot(binding, frozen):
    if type(binding) is not _PrivateBinding:
        raise ValueError('binding')
    current = binding.snapshot_for_query()
    if current != frozen:
        raise ValueError('snapshot_changed')
    projection, _, report = reader._verified_complete(frozen)
    if report['status'] != 'MATCH':
        raise ValueError('snapshot_refused')
    return projection


def _flat_hashes(ids, vectors):
    if type(ids) is not tuple or type(vectors) is not bytes or len(vectors) != len(ids) * 384 * 4:
        raise ValueError('flat_shape')
    pairs = hashlib.sha256()
    for index, record_id in enumerate(ids):
        if type(record_id) is not str:
            raise ValueError('flat_id')
        vector = vectors[index * 1536:(index + 1) * 1536]
        reader._q4_vector(struct.unpack('<384f', vector))
        pairs.update(record_id.encode('utf-8') + b'\x00' + vector)
    return {'ids_sha256': reader._sha(reader._canonical(list(ids))),
            'vectors_sha256': reader._sha(vectors), 'pairs_sha256': pairs.hexdigest()}


def build_flat_memory(*, enabled=False, binding=None, passage_encoder=None,
                      encoder_binding_bytes=None, encoder_binding_sha256=None):
    """Build one memory-only float32 vector per screened, source-bound record.

    Provider identity and supplied encoder metadata are caller-trusted; this
    format makes no FAISS, model-authentication or cross-process claim.
    """
    result = None
    try:
        if (enabled is not True or type(binding) is not _PrivateBinding
                or not callable(passage_encoder) or isinstance(passage_encoder, type)):
            raise ValueError('input')
        _checked_encoder_binding(encoder_binding_bytes, encoder_binding_sha256)
        snapshot = binding.snapshot_for_query()
        projection = _flat_snapshot(binding, snapshot)
        records = projection['messages']['records']
        by_id = {record['record_id']: record for record in records}
        if not records or len(by_id) != len(records):
            raise ValueError('records')
        ids = tuple(sorted(by_id))
        vectors = []
        for start in range(0, len(ids), 16):
            chosen = ids[start:start + 16]
            texts = tuple('passage: ' + reader.decode_text(by_id[key]['stored_text'])[:2000] for key in chosen)
            _flat_snapshot(binding, snapshot)
            raw = passage_encoder(texts)
            _flat_snapshot(binding, snapshot)
            if type(raw) is not bytes or len(raw) != len(chosen) * 1536:
                raise ValueError('passage')
            for index in range(len(chosen)):
                reader._q4_vector(struct.unpack('<384f', raw[index * 1536:(index + 1) * 1536]))
            vectors.append(raw)
        vectors = b''.join(vectors)
        manifest = {'schema': 'wd.mind-flat-f32-memory.v1', 'authority': 'none',
                    'projection_sha256': snapshot['projection_sha256'], 'records': len(ids),
                    'encoder_binding_sha256': encoder_binding_sha256, **_flat_hashes(ids, vectors)}
        _flat_snapshot(binding, snapshot)
        result = _PrivateFlatMemory(binding, snapshot, reader._canonical(manifest), ids, vectors,
                                    encoder_binding_bytes)
    except Exception:
        pass
    if result is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return result


def _flat_check(memory):
    if type(memory) is not _PrivateFlatMemory or type(memory.binding) is not _PrivateBinding:
        raise ValueError('memory')
    projection = _flat_snapshot(memory.binding, memory.snapshot)
    records = projection['messages']['records']
    by_id = {record['record_id']: record for record in records}
    if len(by_id) != len(records) or tuple(sorted(by_id)) != memory.ids:
        raise ValueError('records_changed')
    expected = {'schema': 'wd.mind-flat-f32-memory.v1', 'authority': 'none',
                'projection_sha256': memory.snapshot['projection_sha256'], 'records': len(memory.ids),
                'encoder_binding_sha256': reader._sha(memory.encoder_binding),
                **_flat_hashes(memory.ids, memory.vectors)}
    if type(memory.manifest) is not bytes or not hmac.compare_digest(memory.manifest, reader._canonical(expected)):
        raise ValueError('flat_changed')
    _checked_encoder_binding(memory.encoder_binding, expected['encoder_binding_sha256'])
    return by_id


def query_flat_memory(*, enabled=False, memory=None, lane=None, own_lane_only=False,
                      query=None, as_of_utc=None, query_encoder=None, max_age_seconds=899):
    """Return source-referenced candidates; never infer a lane's latest task."""
    result = None
    try:
        if (enabled is not True or type(memory) is not _PrivateFlatMemory
                or type(lane) is not str or lane not in reader._LANES
                or type(own_lane_only) is not bool or type(query) is not str or not query.strip()
                or len(query) > 4000 or not callable(query_encoder) or isinstance(query_encoder, type)
                or type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 899):
            raise ValueError('query')
        when = reader._utc(as_of_utc)
        records = _flat_check(memory)
        frozen_snapshot = dict(memory.snapshot)
        frozen_snapshot['messages'] = dict(frozen_snapshot['messages'])
        cursor = frozen_snapshot['messages']['end_cursor']
        frozen_snapshot['messages']['end_cursor'] = type(cursor)(cursor.offset, cursor.file_identity, cursor.generation)
        frozen_fields = (memory.binding, memory.manifest, memory.ids, memory.vectors, memory.encoder_binding)
        def checkpoint():
            if (memory.binding is not frozen_fields[0] or memory.snapshot != frozen_snapshot
                    or type(memory.manifest) is not bytes or memory.manifest != frozen_fields[1]
                    or type(memory.ids) is not tuple or memory.ids != frozen_fields[2]
                    or type(memory.vectors) is not bytes or memory.vectors != frozen_fields[3]
                    or type(memory.encoder_binding) is not bytes or memory.encoder_binding != frozen_fields[4]):
                raise ValueError('flat_changed')
            return _flat_check(memory)
        eligible = []
        for index, record_id in enumerate(memory.ids):
            record = records[record_id]
            if own_lane_only and record['meta']['agent'] != lane:
                continue
            stamp = reader._utc(record['time_utc'])
            age = (when - stamp).total_seconds()
            provenance = record['provenance']
            if (age < 0 or age > max_age_seconds
                    or reader._utc(provenance['available_utc']) >= when - reader.dt.timedelta(seconds=1)
                    or ('valid_until_utc' in provenance and when >= reader._utc(provenance['valid_until_utc']))
                    or reader._q4_self_match(query, reader.decode_text(record['stored_text']))):
                continue
            eligible.append((index, record_id))
        selected = []
        if eligible:
            checkpoint()
            query_vector = reader._provider_vector(query_encoder(query))
            checkpoint()
            query_vector = reader._q4_vector(struct.unpack('<384f', struct.pack('<384f', *query_vector)))
            tokens = set(reader._Q4_TOKEN.findall(query.lower()))
            for index, record_id in eligible:
                values = struct.unpack('<384f', memory.vectors[index * 1536:(index + 1) * 1536])
                score = sum(a * b for a, b in zip(values, query_vector))
                if math.isfinite(score) and score >= 0.55:
                    record = records[record_id]
                    lexical = len(tokens & set(reader._Q4_TOKEN.findall(reader.decode_text(record['stored_text']).lower())))
                    selected.append((record_id, score, lexical, reader._utc(record['time_utc'])))
            selected.sort(key=lambda item: (-item[1], -item[2], -item[3].timestamp(), item[0]))
        selected = selected[:3]
        requested = [{'ref_sha256': reader._sha(records[key]['provenance']['event_ref'].encode()),
                      'doc_id_sha256': reader._sha(key.encode()), 'ref_kind': 'sha256_of_projected_metadata',
                      'canonical_event': False} for key, _, _, _ in selected]
        resolved = reader.resolve_content_results(requested, as_of_utc, _complete=True, **memory.snapshot)
        lookup = {reader._sha(key.encode()): records[key] for key, _, _, _ in selected}
        for hit in resolved:
            record = lookup[hit['doc_id_sha256']]
            hit['source_agent'] = record['meta']['agent']
            hit['source_time_utc'] = record['time_utc']
        checkpoint()
        result = {'schema': 'wd.mind-flat-f32-query.v1',
                  'status': 'SEMANTIC_CANDIDATE' if resolved else 'ABSTAIN',
                  'relevance': 'UNVERIFIED', 'authority': 'none', 'index_match': 'NOT_APPLICABLE',
                  'own_latest_task': 'UNKNOWN', 'results': resolved,
                  'provider_authentication': 'UNKNOWN', 'source_authentication': 'UNKNOWN',
                  'continuity': 'UNKNOWN', 'same_user_aba': 'UNKNOWN'}
    except Exception:
        pass
    if result is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return result


def _query_historical_flat_memory(*, enabled=False, memory=None, lane=None, own_lane_only=False,
                                 query=None, query_encoder=None):
    """Explicit historical candidate query; never a current-state assertion."""
    result = None
    try:
        if (enabled is not True or type(memory) is not _PrivateFlatMemory
                or type(lane) is not str or lane not in reader._LANES
                or type(own_lane_only) is not bool or type(query) is not str or not query.strip()
                or len(query) > 4000 or not callable(query_encoder) or isinstance(query_encoder, type)):
            raise ValueError('query')
        when = reader.dt.datetime.now(reader.dt.timezone.utc)
        records = _flat_check(memory)
        frozen_snapshot = dict(memory.snapshot)
        frozen_snapshot['messages'] = dict(frozen_snapshot['messages'])
        cursor = frozen_snapshot['messages']['end_cursor']
        frozen_snapshot['messages']['end_cursor'] = type(cursor)(cursor.offset, cursor.file_identity, cursor.generation)
        frozen_fields = (memory.binding, memory.manifest, memory.ids, memory.vectors, memory.encoder_binding)
        def checkpoint():
            if (memory.binding is not frozen_fields[0] or memory.snapshot != frozen_snapshot
                    or type(memory.manifest) is not bytes or memory.manifest != frozen_fields[1]
                    or type(memory.ids) is not tuple or memory.ids != frozen_fields[2]
                    or type(memory.vectors) is not bytes or memory.vectors != frozen_fields[3]
                    or type(memory.encoder_binding) is not bytes or memory.encoder_binding != frozen_fields[4]):
                raise ValueError('flat_changed')
            return _flat_check(memory)
        eligible = []
        for index, record_id in enumerate(memory.ids):
            record = records[record_id]
            if own_lane_only and record['meta']['agent'] != lane:
                continue
            stamp = reader._utc(record['time_utc'])
            age = (when - stamp).total_seconds()
            provenance = record['provenance']
            if (age < 0
                    or reader._utc(provenance['available_utc']) >= when - reader.dt.timedelta(seconds=1)
                    or ('valid_until_utc' in provenance and when >= reader._utc(provenance['valid_until_utc']))
                    or reader._q4_self_match(query, reader.decode_text(record['stored_text']))):
                continue
            eligible.append((index, record_id))
        selected = []
        if eligible:
            checkpoint()
            query_vector = reader._provider_vector(query_encoder(query))
            checkpoint()
            query_vector = reader._q4_vector(struct.unpack('<384f', struct.pack('<384f', *query_vector)))
            tokens = set(reader._Q4_TOKEN.findall(query.lower()))
            for index, record_id in eligible:
                values = struct.unpack('<384f', memory.vectors[index * 1536:(index + 1) * 1536])
                score = sum(a * b for a, b in zip(values, query_vector))
                if math.isfinite(score) and score >= 0.55:
                    record = records[record_id]
                    lexical = len(tokens & set(reader._Q4_TOKEN.findall(reader.decode_text(record['stored_text']).lower())))
                    selected.append((record_id, score, lexical, reader._utc(record['time_utc'])))
            selected.sort(key=lambda item: (-item[1], -item[2], -item[3].timestamp(), item[0]))
        selected = selected[:3]
        requested = [{'ref_sha256': reader._sha(records[key]['provenance']['event_ref'].encode()),
                      'doc_id_sha256': reader._sha(key.encode()), 'ref_kind': 'sha256_of_projected_metadata',
                      'canonical_event': False} for key, _, _, _ in selected]
        resolved = reader.resolve_content_results(requested, reader._utc_text(when), _complete=True, **memory.snapshot)
        lookup = {reader._sha(key.encode()): records[key] for key, _, _, _ in selected}
        for hit in resolved:
            record = lookup[hit['doc_id_sha256']]
            hit['source_agent'] = record['meta']['agent']
            hit['source_time_utc'] = record['time_utc']
        checkpoint()
        finished = reader.dt.datetime.now(reader.dt.timezone.utc)
        if finished < when:
            raise ValueError('clock_changed')
        visible = []
        for hit in resolved:
            record = lookup[hit['doc_id_sha256']]
            expiry = record['provenance'].get('valid_until_utc')
            if expiry is not None and finished >= reader._utc(expiry):
                raise ValueError('expired_during_query')
            display = reader.decode_text(hit['stored_text'])
            if not 1 <= len(display) <= 320 or any(reader.unicodedata.category(c).startswith('C') for c in display):
                raise ValueError('display')
            visible.append({'ref_sha256':hit['ref_sha256'], 'doc_id_sha256':hit['doc_id_sha256'],
                'source_reference':hit['source_reference'], 'source_time_utc':record['time_utc'],
                'age_seconds':(finished - reader._utc(record['time_utc'])).total_seconds(),
                'display_text':display})
        result = {'schema': 'wd.mind-flat-f32-historical-query.v1',
                  'scope': 'historical', 'as_of_utc': reader._utc_text(finished),
                  'snapshot_freshness': 'UNKNOWN',
                  'status': 'SEMANTIC_CANDIDATE' if resolved else 'ABSTAIN',
                  'relevance': 'UNVERIFIED', 'authority': 'none', 'index_match': 'NOT_APPLICABLE',
                  'own_latest_task': 'UNKNOWN', 'results': visible,
                  'provider_authentication': 'UNKNOWN', 'source_authentication': 'UNKNOWN',
                  'sender_authentication': 'UNKNOWN', 'event_time_authentication': 'UNKNOWN',
                  'continuity': 'UNKNOWN', 'same_user_aba': 'UNKNOWN'}
    except Exception:
        pass
    if result is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return result


class _FlatRequestHandler:
    """In-process owner; caller retains this object through any HOLD."""
    __slots__ = ('_binding', '_snapshot', '_lane', '_lanes', '_rotator', '_adapter', '_memory',
                 '_frozen_flat', '_closed', '_failed', '_lock', '_factory', '_timeout', '_bulk', '_pending_rotator',
                 '_refresh_controller')

    def __init__(self, binding, snapshot, lane, lanes, rotator, adapter, factory, timeout, bulk):
        self._binding, self._snapshot, self._lane = binding, snapshot, lane
        self._lanes = frozenset(lanes)
        self._rotator, self._adapter = rotator, adapter
        self._memory = self._frozen_flat = None
        self._factory, self._timeout, self._bulk, self._pending_rotator = factory, timeout, bulk, None
        self._refresh_controller = None
        self._closed = self._failed = False
        self._lock = threading.Lock()

    def __repr__(self):
        return '<PrivateMindFlatRequestHandler>'

    def _source_check(self):
        _flat_snapshot(self._binding, self._snapshot)
        if self._memory is not None:
            memory = self._memory
            if ((memory.manifest, memory.ids, memory.vectors, memory.encoder_binding) != self._frozen_flat
                    or memory.binding is not self._binding or memory.snapshot != self._snapshot):
                raise ValueError('flat_changed')
            _flat_check(memory)
        return self._snapshot['projection_sha256']

    def build(self):
        if not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        try:
            if self._closed or self._failed or self._memory is not None:
                raise ValueError('state')
            memory = build_flat_memory(enabled=True, binding=self._binding, passage_encoder=self._adapter.passage,
                encoder_binding_bytes=self._adapter.encoder_binding_bytes,
                encoder_binding_sha256=self._adapter.encoder_binding_sha256)
            self._memory = memory
            self._frozen_flat = (memory.manifest, memory.ids, memory.vectors, memory.encoder_binding)
            self._source_check()
            self._running_owner()
            return {'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'}
        except Exception:
            self._failed = True
        finally:
            self._lock.release()
        raise FullRuntimeRefused('mind_full_runtime_refused') from None

    def refresh(self):
        # Deferred: rotation/refresh is outside the single-owner slice.
        raise FullRuntimeRefused('mind_full_runtime_refused') from None

    def handle(self, request):
        return self._handle_for_lane(request, self._lane)

    def lane_view(self, lane):
        """Trusted caller binds a fleet lane; this does not authenticate a peer."""
        if type(lane) is not str or lane not in self._lanes:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return _FlatLaneView(self, lane)

    def _running_owner(self):
        owner = self._rotator.owner
        status = owner.status() if owner is not None else None
        if type(status) is not dict or status.get('state') != 'RUNNING' or status.get('owned') is not True:
            raise ValueError('owner')

    def _handle_for_lane(self, request, bound_lane):
        if not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        response = None
        try:
            if type(request) is not dict:
                raise ValueError('request')
            frozen = dict(request)
            if (self._closed or self._failed or self._memory is None
                    or set(frozen) != {'schema', 'lane', 'query', 'scope'}
                    or frozen['schema'] != 'wd.mind-flat-owner-request.v1'
                    or type(frozen['lane']) is not str or frozen['lane'] != bound_lane
                    or type(frozen['scope']) is not str or frozen['scope'] not in ('own', 'shared')
                    or type(frozen['query']) is not str or not frozen['query'].strip()
                    or len(frozen['query']) > 4000 or '\x00' in frozen['query']):
                raise ValueError('request')
            self._running_owner()
            self._source_check()
            now = reader.dt.datetime.now(reader.dt.timezone.utc)
            result = query_flat_memory(enabled=True, memory=self._memory, lane=bound_lane,
                own_lane_only=frozen['scope'] == 'own',
                query=frozen['query'], as_of_utc=reader._utc_text(now), query_encoder=self._adapter.query)
            self._source_check()
            self._running_owner()
            if request != frozen:
                raise ValueError('request_changed')
            expires = None
            if result['results']:
                expires = min(reader._utc(hit['source_time_utc']) + reader.dt.timedelta(seconds=899)
                              for hit in result['results'])
                if reader.dt.datetime.now(reader.dt.timezone.utc) > expires:
                    result = dict(result, status='ABSTAIN', results=[])
                    expires = None
            response = {'schema': 'wd.mind-flat-owner-response.v1', 'lane': bound_lane, 'scope': frozen['scope'],
                        'authority': 'none', 'expires_utc': None if expires is None else reader._utc_text(expires),
                        'expiry_scope': 'record_age_only', 'snapshot_freshness': 'UNKNOWN', 'result': result}
        except Exception:
            pass
        finally:
            self._lock.release()
        if response is None:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return response

    def close(self):
        """EOF only; HOLD keeps the rotator and its owner reachable here."""
        if not self._lock.acquire(blocking=False):
            return {'state': 'BUSY', 'owned': None}
        try:
            if self._refresh_controller is not None:
                return {'state': 'BUSY', 'owned': None}
            self._closed = True
            outcomes = []
            for rotator in (self._pending_rotator, self._rotator):
                if rotator is None:
                    continue
                try:
                    status = rotator.close()
                    if type(status) is dict and status.get('state') == 'EXITED' and status.get('owned') is False:
                        outcomes.append('EXITED')
                        continue
                except Exception:
                    pass
                try:
                    owner = rotator.owner
                    status = owner.status() if owner is not None else None
                    outcomes.append('HOLD' if type(status) is dict and status.get('owned') is True else 'UNKNOWN')
                except Exception:
                    outcomes.append('UNKNOWN')
            if 'HOLD' in outcomes:
                return {'state': 'HOLD', 'owned': True}
            if outcomes and all(item == 'EXITED' for item in outcomes):
                return {'state': 'EXITED', 'owned': False}
            return {'state': 'UNKNOWN', 'owned': None}
        finally:
            self._lock.release()

    def owner_status(self):
        """Inspect retained ownership without disclosing PID, source or text."""
        if not self._lock.acquire(blocking=False):
            return {'state': 'BUSY', 'owned': None}
        try:
            owner = (self._pending_rotator.owner if self._pending_rotator is not None
                     and self._pending_rotator.owner is not None else self._rotator.owner)
            if owner is None:
                if (getattr(self._rotator, '_client', None) is not None
                        or (self._pending_rotator is not None
                            and getattr(self._pending_rotator, '_client', None) is not None)):
                    return {'state': 'UNKNOWN', 'owned': None}
                return {'state': 'NO_OWNER', 'owned': False}
            status = owner.status()
            if (type(status) is dict and type(status.get('state')) is str
                    and type(status.get('owned')) is bool):
                return {'state': status['state'], 'owned': status['owned']}
        except Exception:
            pass
        finally:
            self._lock.release()
        return {'state': 'UNKNOWN', 'owned': None}


def query_historical_flat_memory(*, enabled=False, handler=None, lane=None, own_lane_only=False, query=None):
    # Deferred: the single-owner slice provides no legacy rotator or guardian.
    raise FullRuntimeRefused('mind_full_runtime_refused') from None


class _FastBuildFence:
    """Frozen projection and source fences for one internal owner build."""
    def __init__(self, handler, snapshot, records):
        self.handler, self.binding = handler, handler._binding
        self.snapshot = dict(snapshot)
        self.snapshot['messages'] = dict(snapshot['messages'])
        cursor = self.snapshot['messages']['end_cursor']
        self.snapshot['messages']['end_cursor'] = type(cursor)(cursor.offset, cursor.file_identity, cursor.generation)
        self.bundle = self.binding._PrivateBinding__bundle
        self.manifest = self.binding._PrivateBinding__manifest
        self.controls = self.binding._PrivateBinding__controls
        self.args = dict(self.binding._PrivateBinding__args)
        self.rows = tuple(sorted((r['record_id'], 'passage: ' + reader.decode_text(r['stored_text'])[:2000])
                                 for r in records))
        if len(self.rows) != len(records) or len({key for key, _ in self.rows}) != len(self.rows):
            raise ValueError('records')
        self.rows_sha = reader._sha(reader._canonical(self.rows))
        self.batch, self.chunks, self.position, self.pending = (), (), 0, None
        self.consumed = 0
        self.nonces = set()

    def _verify_arm(self, rotator):
        from tools import wd_mind_e5_snapshot_adapter as adapter_module
        handler = self.handler
        if (type(handler) is not _FlatRequestHandler or type(handler._lock) is not type(threading.Lock())
                or not handler._lock.locked() or handler._rotator is not rotator
                or handler._binding is not self.binding or type(self.binding) is not _PrivateBinding
                or type(handler._factory) is not _ManifestWorkerFactory or rotator._factory is not handler._factory
                or type(handler._adapter) is not adapter_module._FlatAdapter or handler._adapter._client is not rotator
                or handler._closed or handler._failed or handler._memory is not None
                or handler._pending_rotator is not None or self.batch or self.chunks or self.consumed
                or self.pending is not None or self.position != 0 or self.nonces):
            raise ValueError('unbound_fence')
        factory = handler._factory
        if (factory._bundle != str(self.binding._PrivateBinding__bundle)
                or factory._manifest != self.binding._PrivateBinding__manifest
                or handler._snapshot != self.snapshot or self.binding._PrivateBinding__snapshot != self.snapshot):
            raise ValueError('fence_binding')
        _worker_factory_preflight(factory._bundle, factory._manifest, factory._lock)
        if resolve_manifest_runtime_location(enabled=True, bundle_root=factory._bundle,
                expected_manifest_sha256=factory._manifest) != factory._location:
            raise ValueError('fence_runtime')
        projection = _flat_snapshot(self.binding, self.snapshot)
        rows = tuple(sorted((r['record_id'], 'passage: ' + reader.decode_text(r['stored_text'])[:2000])
                            for r in projection['messages']['records']))
        if not rows or rows != self.rows or reader._sha(reader._canonical(rows)) != self.rows_sha:
            raise ValueError('fence_projection')
        handler._adapter._check()

    def set_batch(self, chosen):
        if self.pending is not None or self.position != len(self.chunks):
            raise ValueError('incomplete_batch')
        consumed = self.consumed + len(self.batch)
        expected = tuple(text for _, text in self.rows[consumed:consumed+16])
        if not expected or type(chosen) is not tuple or chosen != expected:
            raise ValueError('foreign_batch')
        self.consumed, self.batch = consumed, chosen
        self.batch_sha = reader._sha(reader._canonical(self.batch))
        self.chunks = tuple(self.batch[i:i+4] for i in range(0, len(self.batch), 4))
        self.position = 0

    def request(self, phase, raw):
        if (type(raw) is not bytes or self.rows != self.handler._rotator._build_rows
                or reader._sha(reader._canonical(self.batch)) != self.batch_sha):
            raise ValueError('batch_changed')
        if phase == 'before':
            if self.pending is not None or self.position >= len(self.chunks):
                raise ValueError('batch_order')
            payload = json.loads(raw.decode('utf-8'), object_pairs_hook=prefix._unique_object,
                                 parse_constant=reader._bad_constant)
            keys = {'schema','operation','nonce','encoder_binding_sha256','request_sha256','passages'}
            if (type(payload) is not dict or set(payload) != keys
                    or payload['schema'] != 'wd.mind-supervised-e5-request.v1'
                    or payload['operation'] != 'refresh' or type(payload['passages']) is not list
                    or tuple(payload['passages']) != self.chunks[self.position]
                    or not reader._hex(payload['nonce']) or payload['nonce'] in self.nonces
                    or payload['encoder_binding_sha256'] != self.handler._adapter.runtime_binding_sha256
                    or reader._canonical(payload) != raw):
                raise ValueError('request')
            unsigned = dict(payload); digest = unsigned.pop('request_sha256')
            if not reader._hex(digest) or not hmac.compare_digest(reader._sha(reader._canonical(unsigned)), digest):
                raise ValueError('request_digest')
            self.nonces.add(payload['nonce'])
            self.pending = raw
        elif phase == 'after':
            if self.pending is None or not hmac.compare_digest(self.pending, raw):
                raise ValueError('request_changed')
            self.pending = None
            self.position += 1
        else:
            raise ValueError('phase')

    def check(self):
        if (self.binding._PrivateBinding__snapshot != self.snapshot or self.handler._snapshot != self.snapshot
                or self.binding._PrivateBinding__controls != self.controls
                or self.binding._PrivateBinding__args != self.args
                or self.rows != self.handler._rotator._build_rows
                or reader._sha(reader._canonical(self.rows)) != self.rows_sha
                or self.batch != tuple(text for _, text in self.rows[self.consumed:self.consumed+16])
                or reader._sha(reader._canonical(self.batch)) != self.batch_sha):
            raise ValueError('frozen_changed')
        if _bundle_inputs(self.bundle, self.manifest) != self.controls:
            raise ValueError('bundle_changed')
        inputs = dict(self.snapshot)
        raw, digest = inputs.pop('projection_bytes'), inputs.pop('projection_sha256')
        if type(raw) is not bytes or not hmac.compare_digest(reader._sha(raw), digest):
            raise ValueError('projection')
        reader._inputs(inputs, complete_source=True)
        source, sidecar, fleet = prefix._deployment_source(str(self.bundle), self.manifest)
        cursor = self.snapshot['messages']['end_cursor']
        if (not startup._same_path(source, Path(self.args['source_path']))
                or not startup._same_path(sidecar, Path(self.args['generation_path']))
                or fleet != reader._sha(self.controls[0]) or cursor != self.args['cursor']):
            raise ValueError('source')
        prefix._plain(source, directory=False)
        prefix._sidecar_absent(sidecar)
        with bridge._open_log(source) as stream:
            if bridge._file_identity(stream) != cursor.file_identity:
                raise ValueError('identity')
            size = prefix._fence(stream, source, sidecar, cursor.file_identity, cursor.offset)
            stream.seek(cursor.offset - 1)
            if stream.read(1) != b'\n':
                raise ValueError('boundary')
            if not hmac.compare_digest(prefix._hash_prefix(stream, cursor.offset), self.args['expected_prefix_sha256']):
                raise ValueError('prefix')
            size = prefix._fence(stream, source, sidecar, cursor.file_identity, size)
            if not hmac.compare_digest(prefix._hash_prefix(stream, cursor.offset), self.args['expected_prefix_sha256']):
                raise ValueError('prefix_changed')
            prefix._fence(stream, source, sidecar, cursor.file_identity, size)
        if _bundle_inputs(self.bundle, self.manifest) != self.controls:
            raise ValueError('bundle_changed')
        return digest


def build_fast_fenced_owner(*, enabled=False, handler=None):
    # Deferred: the single-owner slice provides no legacy rotator or guardian.
    raise FullRuntimeRefused('mind_full_runtime_refused') from None


class _FlatLaneView:
    """Immutable ordinary-Python lane binding; trusted caller authenticates peers."""
    __slots__ = ('__handler', '__lane')

    def __init__(self, handler, lane):
        object.__setattr__(self, '_FlatLaneView__handler', handler)
        object.__setattr__(self, '_FlatLaneView__lane', lane)

    def __setattr__(self, name, value):
        raise AttributeError('immutable')

    def handle(self, request):
        return self.__handler._handle_for_lane(request, self.__lane)


class _PrivateConsumerExport:
    """Opaque in-process preparation only; live handle export is unavailable."""
    __slots__ = ('__metadata', '__blocks', '__total', '__active')

    def __init__(self, metadata, blocks, total):
        self.__metadata, self.__blocks, self.__total = metadata, blocks, total
        self.__active = True

    def __repr__(self):
        return '<PrivateMindConsumerExport>'

    def status(self):
        return {'schema': 'wd.mind-owner-export-status.v1',
                'state': 'PREPARED_LOCAL_ONLY' if self.__active else 'DISCARDED',
                'authority': 'none', 'receiver_authentication': 'UNKNOWN',
                'transmission': 'NOT_AVAILABLE', 'block_count': len(self.__blocks),
                'total_bytes': self.__total if self.__active else 0}

    def discard(self):
        # Python immutable bytes cannot be promised to be securely erased.
        self.__metadata, self.__blocks, self.__total, self.__active = b'', (), 0, False

    def write_to_handle(self, handle):
        # No receiver provenance has yet been proved for any inherited handle.
        raise FullRuntimeRefused('mind_full_runtime_refused') from None


class _SingleManifestOwner:
    """Own one graceful worker and an optional in-process flat handler.

    The caller must retain this object through HOLD. This is not an IPC server,
    an authenticated lane, an installed startup path, or an automatic refresh.
    """
    __slots__ = ('_binding', '_snapshot', '_lane', '_lanes', '_factory', '_factory_anchor', '_factory_bytes', '_timeout',
                 '_state', '_launch', '_owner', '_client', '_adapter', '_handler', '_lock',
                 '_launch_thread', '_launch_done', '_launch_cancel', '_launch_error', '_owner_conflicts')

    def __init__(self, binding, snapshot, lane, lanes, factory, timeout):
        self._binding, self._snapshot, self._lane = binding, snapshot, lane
        self._lanes, self._factory, self._timeout = frozenset(lanes), factory, timeout
        self._factory_anchor, self._factory_bytes = factory, self._factory_snapshot(factory)
        self._state, self._launch, self._owner = 'NEW', None, None
        self._client = self._adapter = self._handler = None
        self._lock = threading.Lock()
        self._launch_thread = None
        self._launch_done, self._launch_cancel = threading.Event(), threading.Event()
        self._launch_error = None
        self._owner_conflicts = []

    def _capture_owner(self, owner):
        # The launch thread records the reservation before any native spawn.
        if self._owner is not None:
            self._owner_conflicts.append(owner)
            raise ValueError('multiple_launch_owners')
        self._owner = owner

    def _run_launch(self):
        try:
            self._launch = self._factory(owner_sink=self._capture_owner)
            # This runs even when the original caller has already cancelled.
            # Retain both possible owners before publishing launch completion.
            if (type(self._launch) is dict and
                    (self._owner is None or self._launch.get('owner') is not self._owner)):
                self._owner_conflicts.append(self._launch.get('owner'))
        except BaseException as error:
            self._launch_error = error
        finally:
            if self._launch_cancel.is_set() and self._owner is not None and not self._owner_conflicts:
                try:
                    self._owner.shutdown(timeout=0)
                except BaseException:
                    pass
            self._launch_done.set()

    def _wait_for_launch(self):
        self._launch_done.wait()

    def __repr__(self):
        return '<PrivateMindSingleManifestOwner>'

    @staticmethod
    def _factory_snapshot(factory):
        return reader._canonical({'bundle': factory._bundle, 'manifest': factory._manifest,
            'location': factory._location, 'lock': str(factory._lock), 'timeout': factory._timeout,
            'lock_timeout': factory._lock_timeout, 'bulk': factory._bulk})

    def _check_factory(self):
        if (self._factory is not self._factory_anchor
                or not hmac.compare_digest(self._factory_snapshot(self._factory), self._factory_bytes)):
            raise ValueError('factory_changed')

    def _check_factory_or_hold(self):
        accepted = False
        try:
            self._check_factory()
            accepted = True
        except (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError):
            self._state = 'HOLD'
            raise
        except BaseException:
            self._state = 'HOLD'
        if not accepted:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None

    def _status(self):
        if self._owner_conflicts:
            self._state = 'HOLD'
            return {'state': 'HOLD', 'owned': None, 'authority': 'none'}
        if self._owner is None:
            return {'state': self._state, 'owned': False if self._state == 'NEW' else None,
                    'authority': 'none'}
        try:
            current = self._owner.status()
            owned = current.get('owned') if type(current) is dict else None
            if type(owned) is not bool:
                owned = None
            if self._state == 'READY' and (owned is not True or current.get('state') != 'RUNNING'):
                self._state = 'HOLD'
        except (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError):
            self._state = 'HOLD'
            raise
        except BaseException:
            owned = None
            if self._state == 'READY':
                self._state = 'HOLD'
        return {'state': self._state, 'owned': owned, 'authority': 'none'}

    def status(self):
        if not self._lock.acquire(blocking=False):
            return {'state': 'BUSY', 'owned': None, 'authority': 'none'}
        try:
            return self._status()
        finally:
            self._lock.release()

    def start(self):
        if not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        try:
            if self._state != 'NEW':
                raise FullRuntimeRefused('mind_full_runtime_refused') from None
            self._state = 'STARTING'
            try:
                self._check_factory()
                _flat_snapshot(self._binding, self._snapshot)
                self._launch_thread = threading.Thread(target=self._run_launch,
                    name='mind-e5-launch', daemon=False)
                self._launch_thread.start()
                self._wait_for_launch()
                if self._launch_error is not None:
                    raise self._launch_error
                launched = self._launch
                # Retain the entire factory result before any fallible binding
                # or model work, including the owner on a returned HOLD.
                self._launch = launched
                if type(launched) is not dict:
                    raise ValueError('launch')
                if self._owner is None or launched.get('owner') is not self._owner:
                    self._owner_conflicts.append(launched.get('owner'))
                    raise ValueError('launch_owner_changed')
                self._client = launched.get('client')
                self._check_factory()
                if (launched.get('status') != 'READY' or self._owner is None
                        or self._client is None or getattr(self._client, 'owner', None) is not self._owner):
                    raise ValueError('launch_not_ready')
                from tools import wd_mind_e5_snapshot_adapter as adapter_module
                bundle, manifest = self._binding._PrivateBinding__bundle, self._binding._PrivateBinding__manifest
                self._adapter = adapter_module.create_single_flat_adapter(enabled=True, bundle_root=str(bundle),
                    expected_manifest_sha256=manifest, client=self._client)
                self._handler = _FlatRequestHandler(self._binding, self._snapshot, self._lane, self._lanes,
                    self._client, self._adapter, self._factory, self._timeout, self._factory._bulk)
                result = self._handler.build()
                if result != {'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'}:
                    raise ValueError('build')
                self._check_factory()
                self._state = 'READY'
            except (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError):
                self._launch_cancel.set()
                self._state = 'HOLD'
                raise
            except BaseException:
                # Unknown post-launch states are never declared exited. The
                # saved owner (if present) remains reachable for explicit EOF.
                self._state = 'HOLD'
            return self._status()
        finally:
            self._lock.release()

    def prepare_consumer_export(self, *, enabled=False, expected_nonce=None,
                                expected_identifier_policy_sha256=None, max_total_bytes=None):
        """Prepare pinned snapshot/flat data in memory; never transfer it.

        The caller must independently obtain the policy pin from an approved
        launcher context. The nonce is correlation data, not receiver authority.
        A trusted, separately inherited channel is required before any export.
        """
        if not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        result = None
        checking_integrity = False
        cancelled_type = None
        try:
            if (enabled is not True or self._state != 'READY' or self._handler is None
                    or type(expected_nonce) is not str or not reader._hex(expected_nonce)
                    or type(expected_identifier_policy_sha256) is not str
                    or not reader._hex(expected_identifier_policy_sha256)
                    or type(max_total_bytes) is not int or not 0 < max_total_bytes < 2**63):
                raise ValueError('export_input')
            checking_integrity = True
            self._check_factory()
            self._handler._running_owner()
            memory = self._handler._memory
            _flat_check(memory)
            snapshot = self._binding.snapshot_for_query()
            if memory.binding is not self._binding or memory.snapshot != snapshot:
                raise ValueError('export_snapshot')
            messages = snapshot['messages']
            identifier, key, tag = snapshot['identifier_policy_binding']
            cursor = messages['end_cursor']
            if (type(identifier) is not bytes or type(key) is not bytes or len(key) != 32
                    or not hmac.compare_digest(reader._sha(identifier), expected_identifier_policy_sha256)
                    or type(tag) is not str or not reader._hex(tag)
                    or not hmac.compare_digest(hmac.new(key, identifier, hashlib.sha256).hexdigest(), tag)
                    or type(cursor) is not bridge.BridgeCursor or type(cursor.offset) is not int
                    or cursor.offset < 0 or type(cursor.file_identity) is not str
                    or (cursor.generation is not None and type(cursor.generation) is not str)
                    or type(messages['source_path']) is not str or type(messages['generation_path']) is not str):
                raise ValueError('export_binding')
            blocks = (('message_policy', messages['policy_bytes']), ('decision', snapshot['decision_bytes']),
                ('nonreview_policy', snapshot['policy_bytes']), ('roster', snapshot['roster_bytes']),
                ('task_scope', snapshot['approved_tasks_bytes']), ('identifier_policy', identifier),
                ('identifier_key', key), ('projection', snapshot['projection_bytes']),
                ('flat_manifest', memory.manifest), ('ids', reader._canonical(list(memory.ids))),
                ('vectors', memory.vectors), ('encoder_binding', memory.encoder_binding))
            if any(type(value) is not bytes for _, value in blocks):
                raise ValueError('export_blocks')
            metadata = reader._canonical({'schema':'wd.mind-owner-export.v1', 'authority':'none',
                'nonce':expected_nonce, 'source_path':messages['source_path'],
                'generation_path':messages['generation_path'], 'cursor':{'offset':cursor.offset,
                    'file_identity':cursor.file_identity, 'generation':cursor.generation},
                'identifier_policy_tag':tag,
                'blocks':[{'name':name,'length':len(value)} for name, value in blocks]})
            total = len(metadata) + sum(len(value) for _, value in blocks)
            if total > max_total_bytes:
                checking_integrity = False
                raise ValueError('export_size')
            _flat_check(memory)
            self._check_factory()
            self._handler._running_owner()
            result = _PrivateConsumerExport(metadata, blocks, total)
        except KeyboardInterrupt:
            self._state = 'HOLD'
            cancelled_type = KeyboardInterrupt
        except GeneratorExit:
            self._state = 'HOLD'
            cancelled_type = GeneratorExit
        except asyncio.CancelledError:
            self._state = 'HOLD'
            cancelled_type = asyncio.CancelledError
        except Exception:
            if checking_integrity:
                self._state = 'HOLD'
        except BaseException:
            self._state = 'HOLD'
        finally:
            # Refusals can be inspected with capture_locals=True.
            key = identifier = tag = blocks = snapshot = messages = None
            metadata = memory = cursor = None
            self._lock.release()
        if cancelled_type is not None:
            raise cancelled_type() from None
        if result is None:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return result

    def candidates(self, request):
        if not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        try:
            if self._state != 'READY' or self._handler is None:
                raise FullRuntimeRefused('mind_full_runtime_refused') from None
            self._check_factory_or_hold()
            # Pipe candidates contain references only, not the message body.
            response = map_pipe_candidates(enabled=True, handler=self._handler, request=request)
            self._check_factory_or_hold()
            return response
        finally:
            self._lock.release()

    def close(self):
        if not self._lock.acquire(blocking=False):
            return {'state': 'BUSY', 'owned': None, 'authority': 'none'}
        try:
            if self._state == 'NEW':
                self._state = 'STOPPED'
                return {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
            if self._launch_thread is not None and self._launch_thread.is_alive():
                self._state = 'HOLD'
                return self._status()
            if self._owner_conflicts:
                self._state = 'HOLD'
                return self._status()
            if self._owner is None:
                self._state = 'HOLD'
                return self._status()
            try:
                if self._client is not None:
                    result = self._client.close(timeout=self._timeout)
                else:
                    result = self._owner.shutdown(timeout=self._timeout)
                if (type(result) is dict and result.get('state') == 'EXITED'
                        and result.get('owned') is False):
                    self._state = 'STOPPED'
                    return {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
            except (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError):
                self._state = 'HOLD'
                raise
            except Exception:
                pass
            self._state = 'HOLD'
            return self._status()
        finally:
            self._lock.release()


def create_single_manifest_owner(*, enabled=False, binding=None, lane=None, worker_factory=None,
                                 shutdown_timeout=None):
    """Create an inert single-worker owner, retaining it through any HOLD.

    The manifest and lane must already be independently bound. No worker is
    launched until start(); no rotation, guardian, listener or refresh is added.
    """
    result = None
    try:
        from tools import wd_mind_e5_client as worker_client
        if (enabled is not True or type(binding) is not _PrivateBinding
                or type(worker_factory) is not _ManifestWorkerFactory or type(lane) is not str
                or lane not in reader._LANES or type(shutdown_timeout) not in (int, float)
                or not math.isfinite(shutdown_timeout) or not 0 <= shutdown_timeout <= 15):
            raise ValueError('input')
        bundle, manifest = binding._PrivateBinding__bundle, binding._PrivateBinding__manifest
        if (worker_factory._bundle != str(bundle) or worker_factory._manifest != manifest
                or worker_factory._bulk != worker_client.BULK_CONTRACT_SHA256):
            raise ValueError('factory_binding')
        snapshot = binding.snapshot_for_query()
        _flat_snapshot(binding, snapshot)
        fleet = json.loads(binding._PrivateBinding__controls[0].decode('utf-8'),
                           object_pairs_hook=prefix._unique_object, parse_constant=reader._bad_constant)
        names = {entry['agent'] for entry in fleet['lanes']} | {fleet['tools_supervisor']['agent']}
        if lane not in names:
            raise ValueError('lane')
        result = _SingleManifestOwner(binding, snapshot, lane, names, worker_factory, float(shutdown_timeout))
    except Exception:
        pass
    if result is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return result


def create_flat_request_handler(*, enabled=False, binding=None, lane=None, worker_factory=None,
                                shutdown_timeout=None, bulk_contract_sha256=None):
    # Deferred: the single-owner slice provides no legacy rotator or guardian.
    raise FullRuntimeRefused('mind_full_runtime_refused') from None


_STARTUP_PIPE_QUERY = 'current task active work next step recent status'


def map_pipe_candidates(*, enabled=False, handler=None, request=None):
    """Map one fixed public in-process query to source hashes, never text.

    The caller must authenticate and bind the peer separately; accepting a
    lane string here is not proof of caller identity. No listener is created.
    """
    result = None
    try:
        if enabled is not True or type(handler) is not _FlatRequestHandler or type(request) is not dict:
            raise ValueError('input')
        frozen = dict(request)
        if (set(frozen) != {'schema', 'query_id', 'nonce', 'lane', 'scope'}
                or any(type(frozen[key]) is not str for key in frozen)
                or frozen['schema'] != 'wd.mind-pipe-request.v1'
                or frozen['query_id'] != 'startup_work_overview'
                or not reader._hex(frozen['nonce']) or frozen['lane'] not in reader._LANES
                or frozen['scope'] not in ('own', 'shared')):
            raise ValueError('request')
        reply = handler.lane_view(frozen['lane']).handle({
            'schema': 'wd.mind-flat-owner-request.v1', 'lane': frozen['lane'],
            'scope': frozen['scope'], 'query': _STARTUP_PIPE_QUERY})
        if (type(reply) is not dict
                or set(reply) != {'schema', 'lane', 'scope', 'authority', 'expires_utc',
                                  'expiry_scope', 'snapshot_freshness', 'result'}
                or reply['schema'] != 'wd.mind-flat-owner-response.v1'
                or reply['lane'] != frozen['lane'] or reply['scope'] != frozen['scope']
                or reply['authority'] != 'none' or reply['expiry_scope'] != 'record_age_only'
                or reply['snapshot_freshness'] != 'UNKNOWN' or type(reply['result']) is not dict):
            raise ValueError('reply')
        content = reply['result']
        if (content.get('schema') != 'wd.mind-flat-f32-query.v1'
                or content.get('status') not in ('SEMANTIC_CANDIDATE', 'ABSTAIN')
                or content.get('authority') != 'none' or content.get('relevance') != 'UNVERIFIED'
                or content.get('index_match') != 'NOT_APPLICABLE' or content.get('own_latest_task') != 'UNKNOWN'
                or type(content.get('results')) is not list or len(content['results']) > 3
                or ((content['status'] == 'SEMANTIC_CANDIDATE') != bool(content['results']))):
            raise ValueError('content')
        candidates, seen = [], set()
        for hit in content['results']:
            if type(hit) is not dict or type(hit.get('source_reference')) is not dict:
                raise ValueError('hit')
            reference = hit['source_reference']
            if (set(reference) != {'sha256', 'kind', 'verified', 'sender_authentication'}
                    or reference['kind'] != 'sha256_of_canonical_event_json'
                    or reference['verified'] != 'MATCH_WITHIN_PREFIX'
                    or reference['sender_authentication'] != 'UNKNOWN'):
                raise ValueError('reference')
            row = {'ref_sha256': hit.get('ref_sha256'), 'doc_id_sha256': hit.get('doc_id_sha256'),
                   'source_event_sha256': reference['sha256']}
            if any(not reader._hex(value) for value in row.values()):
                raise ValueError('hash')
            key = tuple(row[name] for name in ('ref_sha256', 'doc_id_sha256', 'source_event_sha256'))
            if key in seen:
                raise ValueError('duplicate')
            seen.add(key)
            candidates.append(row)
        if candidates:
            if (type(reply['expires_utc']) is not str
                    or reader.dt.datetime.now(reader.dt.timezone.utc) > reader._utc(reply['expires_utc'])):
                candidates = []
        elif reply['expires_utc'] is not None:
            raise ValueError('expiry')
        if request != frozen:
            raise ValueError('request_changed')
        result = {'schema': 'wd.mind-pipe-candidates.v1', 'nonce': frozen['nonce'],
                  'lane': frozen['lane'], 'scope': frozen['scope'],
                  'status': 'CANDIDATES' if candidates else 'ABSTAIN', 'candidates': candidates}
    except Exception:
        pass
    if result is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return result


class _FlatAutoRefresh:
    """One retained owner and one non-daemon scheduler, with no cycle quota."""
    __slots__ = ('_handler', '_interval', '_stop', '_state_lock', '_thread', '_state', '_last')

    def __init__(self, handler, interval):
        self._handler, self._interval = handler, interval
        self._stop, self._state_lock = threading.Event(), threading.Lock()
        self._thread, self._state, self._last = None, 'DORMANT', None

    def __repr__(self):
        return '<PrivateMindFlatAutoRefresh>'

    def start(self):
        """Begin only after the caller has retained this controller object."""
        with self._state_lock:
            if self._state != 'DORMANT':
                raise FullRuntimeRefused('mind_full_runtime_refused') from None
            self._thread = threading.Thread(target=self._run, name='mind-flat-refresh', daemon=False)
            self._state = 'RUNNING'
            try:
                self._thread.start()
            except Exception:
                self._state = 'REFUSED'
                raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return None

    def _run(self):
        try:
            while not self._stop.wait(self._interval):
                refreshed = self._handler.refresh()
                if (type(refreshed) is not dict or refreshed.get('schema') != 'wd.mind-flat-owner-refresh.v1'
                        or refreshed.get('status') not in ('REFRESHED', 'UNCHANGED')):
                    raise ValueError('refresh')
                with self._state_lock:
                    self._last = refreshed['status']
            with self._state_lock:
                self._state = 'STOPPED'
        except BaseException:
            # Even an unexpected scheduler exception must prevent later reads
            # from presenting the old snapshot as the current one. Retain the
            # handler and any pending owner; never terminate it here.
            with self._handler._lock:
                self._handler._failed = True
            with self._state_lock:
                self._state = 'REFUSED'

    def status(self):
        with self._state_lock:
            state, last = self._state, self._last
        return {'state': state, 'last_result': last, 'owner': self._handler.owner_status()}

    def stop(self, *, timeout=0):
        """Request stop; BUSY retains the controller/handler and does not kill."""
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 <= timeout <= 1800):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        self._stop.set()
        with self._state_lock:
            thread, state = self._thread, self._state
            if state == 'DORMANT':
                self._state = 'STOPPED'
        if thread is not None:
            thread.join(float(timeout))
            if thread.is_alive():
                return {'state': 'BUSY', 'last_result': self._last, 'owner': self._handler.owner_status()}
        if self._handler._lock.acquire(blocking=False):
            try:
                if self._handler._refresh_controller is self:
                    self._handler._refresh_controller = None
            finally:
                self._handler._lock.release()
        return self.status()


def create_flat_auto_refresh(*, enabled=False, handler=None, interval_seconds=None):
    # Deferred: the single-owner slice provides no legacy rotator or guardian.
    raise FullRuntimeRefused('mind_full_runtime_refused') from None


class _ManifestCoordinator:
    """Own one bound flat handler, scheduler, and reference-only service.

    READY is a transient local observation, not an installation, peer identity,
    successful next turn, or continuity claim. Retain this object through HOLD.
    """
    __slots__ = ('_args', '_binding', '_factory', '_handler', '_controller', '_service',
                 '_state', '_built', '_lock')

    def __init__(self, args):
        self._args = args
        self._binding = self._factory = self._handler = self._controller = self._service = None
        self._state, self._built, self._lock = 'NEW', False, threading.Lock()

    def __repr__(self):
        return '<PrivateMindManifestCoordinator>'

    def _candidates(self, request):
        if not self._built or self._state not in ('STARTING', 'ACTIVE'):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return map_pipe_candidates(enabled=True, handler=self._handler, request=request)['candidates']

    def start(self):
        if not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        if self._state != 'NEW':
            self._lock.release()
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        try:
            self._state = 'STARTING'
            a = self._args
            self._binding = assemble_full_runtime(enabled=True, bundle_root=a['bundle_root'],
                                                  expected_manifest_sha256=a['expected_manifest_sha256'])
            self._factory = create_manifest_worker_factory(enabled=True, bundle_root=a['bundle_root'],
                expected_manifest_sha256=a['expected_manifest_sha256'], worker_timeout_seconds=a['worker_timeout_seconds'],
                lock_timeout_seconds=a['lock_timeout_seconds'], bulk_contract_sha256=a['bulk_contract_sha256'])
            self._handler = create_flat_request_handler(enabled=True, binding=self._binding, lane=a['lane'],
                worker_factory=self._factory, shutdown_timeout=a['shutdown_timeout'],
                bulk_contract_sha256=a['bulk_contract_sha256'])
            built = (build_fast_fenced_owner(enabled=True, handler=self._handler)
                     if a['fast_build'] else self._handler.build())
            if built != {'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'}:
                raise ValueError('build')
            if self._handler.owner_status() != {'state':'RUNNING', 'owned':True}:
                raise ValueError('owner')
            self._built = True
            self._controller = create_flat_auto_refresh(enabled=True, handler=self._handler,
                                                         interval_seconds=a['refresh_interval_seconds'])
            from tools import wd_mind_runtime_process as process
            self._service = process.create_candidate_service(enabled=True, bundle_root=a['bundle_root'],
                deployment_manifest_sha256=a['expected_manifest_sha256'], handler=self._candidates,
                receive_timeout=5, reply_timeout=5,
                callback_timeout=process._CANDIDATE_CALLBACK_TIMEOUT_SECONDS)
            self._controller.start()
            self._service.start()
            self._state = 'ACTIVE'
        except BaseException:
            self._state = 'HOLD'
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        finally:
            self._lock.release()
        return self.status()

    def status(self):
        if not self._lock.acquire(blocking=False):
            return {'state':'BUSY', 'authority':'none', 'lane_authentication':'UNKNOWN'}
        try:
            state = self._state
            if state == 'ACTIVE':
                service = self._service.status()
                controller = self._controller.status()
                owner = self._handler.owner_status()
                if (type(service) is dict and service.get('owned') is True
                        and service.get('state') in ('RECEIVING', 'CALLBACK', 'REPLYING')
                        and type(controller) is dict and controller.get('state') == 'RUNNING'
                        and owner == {'state':'RUNNING', 'owned':True}):
                    state = 'READY'
                elif (type(service) is dict and service.get('state') == 'STARTING'
                      and type(controller) is dict and controller.get('state') == 'RUNNING'):
                    state = 'STARTING'
                else:
                    state = 'HOLD'
            return {'state':state, 'authority':'none', 'lane_authentication':'UNKNOWN'}
        except Exception:
            return {'state':'HOLD', 'authority':'none', 'lane_authentication':'UNKNOWN'}
        finally:
            self._lock.release()

    def stop(self, *, timeout=0):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 15:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        if not self._lock.acquire(blocking=False):
            return {'state':'BUSY', 'authority':'none', 'lane_authentication':'UNKNOWN'}
        try:
            self._state = 'STOPPING'
            if self._service is not None:
                stopped = self._service.stop(timeout=timeout)
                if type(stopped) is not dict or stopped.get('state') != 'STOPPED' or stopped.get('owned') is not False:
                    self._state = 'HOLD'
                    return {'state':'HOLD', 'authority':'none', 'lane_authentication':'UNKNOWN'}
            if self._controller is not None:
                stopped = self._controller.stop(timeout=timeout)
                if type(stopped) is not dict or stopped.get('state') != 'STOPPED':
                    self._state = 'HOLD'
                    return {'state':'HOLD', 'authority':'none', 'lane_authentication':'UNKNOWN'}
            if self._handler is not None:
                stopped = self._handler.close()
                if type(stopped) is not dict or stopped.get('state') != 'EXITED' or stopped.get('owned') is not False:
                    self._state = 'HOLD'
                    return {'state':'HOLD', 'authority':'none', 'lane_authentication':'UNKNOWN'}
            self._state = 'STOPPED'
            return {'state':'STOPPED', 'authority':'none', 'lane_authentication':'UNKNOWN'}
        except BaseException:
            self._state = 'HOLD'
            return {'state':'HOLD', 'authority':'none', 'lane_authentication':'UNKNOWN'}
        finally:
            self._lock.release()


def create_manifest_coordinator(*, enabled=False, bundle_root=None, expected_manifest_sha256=None,
                                lane=None, worker_timeout_seconds=None, lock_timeout_seconds=None,
                                shutdown_timeout=None, refresh_interval_seconds=None, bulk_contract_sha256=None,
                                fast_build=False):
    # Deferred: the single-owner slice provides no legacy rotator or guardian.
    raise FullRuntimeRefused('mind_full_runtime_refused') from None


def _owner_preflight(*, bundle_root, manifest_sha256):
    """Refuse the deferred legacy owner path before accessing resources."""
    raise FullRuntimeRefused('mind_full_runtime_refused') from None


def _run_owner_loop(coordinator, stdin, stdout):
    """Control retained owner; do not abandon a pending start or failed stop."""
    stop_requested, start_done = threading.Event(), threading.Event()
    start_failed = []
    def controls():
        try:
            while True:
                line = stdin.readline()
                if line == '' or line == 'STOP\n' or line == 'STOP\r\n':
                    stop_requested.set()
                    return
                # Unrecognised control is refusal, never executable input.
                stop_requested.set()
                return
        except BaseException:
            stop_requested.set()
    def launch():
        try:
            coordinator.start()
        except BaseException:
            start_failed.append(True)
        finally:
            start_done.set()
    last = None
    def publish(state):
        nonlocal last
        if last == state:
            return
        try:
            stdout.write(json.dumps({'schema':'wd.mind-owner-status.v1','state':state},
                                    sort_keys=True, separators=(',', ':')) + '\n')
            stdout.flush()
            last = state
        except BaseException:
            stop_requested.set()
    publish('STARTING')
    threading.Thread(target=controls, name='mind-owner-control', daemon=True).start()
    launch_thread = threading.Thread(target=launch, name='mind-owner-start', daemon=False)
    launch_thread.start()
    while True:
        if stop_requested.is_set():
            if not start_done.is_set():
                publish('HOLD')
                start_done.wait(0.1)
                continue
            launch_thread.join()
            try: stopped = coordinator.stop(timeout=5)
            except BaseException: stopped = None
            if type(stopped) is dict and stopped.get('state') == 'STOPPED':
                return 0
            publish('HOLD')
            # The object may retain pending IO, callback, scheduler or worker.
            # Keep the owner process alive; do not kill, replay or discard it.
            while True:
                threading.Event().wait(60)
        if start_done.is_set():
            if start_failed:
                publish('HOLD')
            else:
                try: state = coordinator.status().get('state')
                except BaseException: state = 'HOLD'
                publish('READY' if state == 'READY' else 'STARTING' if state in ('NEW','STARTING','BUSY') else 'HOLD')
        start_done.wait(0.1) if not start_done.is_set() else stop_requested.wait(0.1)


def _owner_fast_release(*, bundle_root, manifest_sha256):
    """Require exact v2 fast release bytes anchored by the external manifest."""
    if type(bundle_root) is not str or not Path(bundle_root).is_absolute() or not reader._hex(manifest_sha256):
        raise ValueError('fast_release')
    raw = prefix._read_pinned(Path(bundle_root) / 'deployment-manifest.json', manifest_sha256,
                              prefix.MAX_CONTROL_BYTES)
    document = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                          parse_constant=reader._bad_constant)
    if (type(document) is not dict or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1):
        raise ValueError('fast_release')
    config = document.get('mind_startup_guardian')
    if (type(config) is not dict
            or set(config) != {'schema','enabled','entrypoint','ready_timeout_seconds','fast_build'}
            or config['schema'] != 'wd.mind-startup-guardian.v2' or config['enabled'] is not True
            or config['entrypoint'] != 'tools/wd_mind_runtime_process.py'
            or type(config['ready_timeout_seconds']) is not int
            or not 15 <= config['ready_timeout_seconds'] <= 600
            or config['fast_build'] is not True):
        raise ValueError('fast_release')
    return raw


def _owner_entrypoint(argv, stdin, stdout):
    # Deferred: the single-owner slice provides no legacy rotator or guardian.
    return 2


if __name__ == '__main__':
    # A module invoked with -m is registered as __main__; the bundle verifier
    # requires its canonical module name and confirms the actual origin/pin.
    if 'tools.wd_mind_full_runtime' not in sys.modules:
        sys.modules['tools.wd_mind_full_runtime'] = sys.modules[__name__]
    raise SystemExit(_owner_entrypoint(sys.argv[1:], sys.stdin, sys.stdout))
