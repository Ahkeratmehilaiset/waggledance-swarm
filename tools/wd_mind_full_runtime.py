"""Default-off in-memory complete snapshot assembly; no live installation.

The caller independently authorizes the manifest, interpreter and code.
Source authentication, same-user ABA resistance and continuity remain UNKNOWN.
The HMAC key lives only in this process and is not a cross-restart key store.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import struct
import sys
import threading
import time
import types
from types import MappingProxyType

from tools import wd_mind_absent_generation_prefix_readonly as prefix
from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_startup_content as startup
from waggledance.core import bridge_log_reader as bridge

_POLICY = 'wd-mind-message-policy.json'
_POLICY_APPROVAL_DESCRIPTOR = 'wd-mind-identifier-policy-approval.json'
_SNAPSHOT_MODULE_LOCK = threading.Lock()
_SNAPSHOT_MODULE_ATTESTATION = None


class _ManifestPolicyCandidate:
    """Manifest-bound metadata, not verified operator approval."""
    __slots__ = ('_policy_sha256', '_approval_reference_sha256')

    def __init__(self, policy_sha256, approval_reference_sha256):
        self._policy_sha256, self._approval_reference_sha256 = policy_sha256, approval_reference_sha256

    def __repr__(self):
        return '<PrivateMindManifestPolicyCandidate>'

    def status(self):
        return {'state':'CANDIDATE', 'approval':'UNVERIFIED', 'authority':'none',
                'identifier_policy_sha256':self._policy_sha256,
                'approval_reference_sha256':self._approval_reference_sha256}


class _TrustedManifestPolicyAnchor:
    """Reserved type; no trusted approval provider is implemented yet."""
    __slots__ = ('bundle_root', 'manifest_sha256', 'policy_sha256', 'approval_reference_sha256')

    def __new__(cls, *args, **kwargs):
        raise FullRuntimeRefused('mind_full_runtime_refused') from None

    def __setattr__(self, name, value):
        raise AttributeError('immutable')

    def status(self):
        return {'policy_binding':'MATCH', 'approval_reference_binding':'MATCH',
                'authorization_source':'operator_supplied_pins', 'operator_authentication':'UNKNOWN',
                'observation':'observed_at_creation', 'reaudit_required':True}


def inspect_manifest_policy_candidate(*, enabled=False, bundle_root=None, expected_manifest_sha256=None):
    """Inspect proposed descriptor in a pinned bundle, without voting approval.

    The descriptor path is a proposal, absent from the current installed bundle.
    Provisioning it requires a future operator-approved deployment manifest; a
    hash field alone never authenticates an operator or enables execution.
    """
    result = None
    try:
        if (enabled is not True or type(bundle_root) is not str or not Path(bundle_root).is_absolute()
                or '..' in Path(bundle_root).parts or type(expected_manifest_sha256) is not str
                or not reader._hex(expected_manifest_sha256)):
            raise ValueError('input')
        bundle = Path(bundle_root)
        manifest_raw = prefix._read_pinned(bundle / 'deployment-manifest.json', expected_manifest_sha256,
                                           prefix.MAX_CONTROL_BYTES)
        manifest = json.loads(manifest_raw.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                              parse_constant=reader._bad_constant)
        if (type(manifest) is not dict or type(manifest.get('schema_version')) is not int
                or manifest['schema_version'] != 1 or type(manifest.get('files')) is not dict):
            raise ValueError('manifest')
        descriptor_sha = manifest['files'].get(_POLICY_APPROVAL_DESCRIPTOR)
        raw = prefix._read_pinned(bundle / _POLICY_APPROVAL_DESCRIPTOR, descriptor_sha, 65536)
        descriptor = json.loads(raw.decode('utf-8'), object_pairs_hook=prefix._unique_object,
                                parse_constant=reader._bad_constant)
        if (type(descriptor) is not dict or set(descriptor) != {'schema','identifier_policy_sha256',
                'approval_reference_sha256'} or descriptor['schema'] != 'wd.mind-identifier-policy-approval.v1'
                or not reader._hex(descriptor['identifier_policy_sha256'])
                or not reader._hex(descriptor['approval_reference_sha256'])):
            raise ValueError('descriptor')
        if (prefix._read_pinned(bundle / 'deployment-manifest.json', expected_manifest_sha256,
                               prefix.MAX_CONTROL_BYTES) != manifest_raw
                or prefix._read_pinned(bundle / _POLICY_APPROVAL_DESCRIPTOR, descriptor_sha, 65536) != raw):
            raise ValueError('changed')
        result = _ManifestPolicyCandidate(descriptor['identifier_policy_sha256'],
                                          descriptor['approval_reference_sha256'])
    except Exception:
        pass
    if result is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return result


def create_operator_pinned_policy_anchor(*, enabled=False, bundle_root=None, expected_manifest_sha256=None,
                                         operator_policy_sha256=None,
                                         operator_approval_reference_sha256=None):
    """Bind independently operator-supplied pins to a pinned bundle descriptor.

    The caller must obtain the two operator inputs from the existing manual
    operator channel, independently of the bundle and MCT1 bytes. Equality of
    hashes does not cryptographically authenticate a human or that channel.
    This creates no trusted parent/handle authority and does not enable export.
    """
    result = None
    try:
        if (enabled is not True or type(operator_policy_sha256) is not str
                or not reader._hex(operator_policy_sha256)
                or type(operator_approval_reference_sha256) is not str
                or not reader._hex(operator_approval_reference_sha256)):
            raise ValueError('operator_pins')
        candidate = inspect_manifest_policy_candidate(enabled=True, bundle_root=bundle_root,
            expected_manifest_sha256=expected_manifest_sha256).status()
        if (not hmac.compare_digest(candidate['identifier_policy_sha256'], operator_policy_sha256)
                or not hmac.compare_digest(candidate['approval_reference_sha256'],
                                           operator_approval_reference_sha256)):
            raise ValueError('operator_pin_mismatch')
        anchor = object.__new__(_TrustedManifestPolicyAnchor)
        object.__setattr__(anchor, 'bundle_root', bundle_root)
        object.__setattr__(anchor, 'manifest_sha256', expected_manifest_sha256)
        object.__setattr__(anchor, 'policy_sha256', operator_policy_sha256)
        object.__setattr__(anchor, 'approval_reference_sha256', operator_approval_reference_sha256)
        result = anchor
    except Exception:
        pass
    if result is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return result


def start_manifest_bound_owner(*, enabled=False, policy_anchor: _TrustedManifestPolicyAnchor | None = None):
    """Refuse until a trusted approval provider and parent transport exist."""
    raise FullRuntimeRefused('mind_full_runtime_refused') from None


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
    __slots__ = ('_bundle', '_manifest', '_location', '_lock', '_timeout', '_lock_timeout', '_bulk',
                 '_admission', '_admission_owner')

    def __init__(self, bundle, manifest, location, lock, timeout, lock_timeout, bulk, admission=None, admission_owner=None):
        self._bundle, self._manifest, self._location, self._lock = bundle, manifest, location, lock
        self._timeout, self._lock_timeout, self._bulk = timeout, lock_timeout, bulk
        self._admission, self._admission_owner = admission, admission_owner

    def __repr__(self):
        return '<PrivateMindManifestWorkerFactory>'

    def __call__(self, *, owner_sink=None, call_deadline=None):
        try:
            if (call_deadline is not None and
                    (type(call_deadline) not in (int, float) or not math.isfinite(call_deadline))):
                raise ValueError('deadline')
            current = resolve_manifest_runtime_location(enabled=True, bundle_root=self._bundle,
                                                         expected_manifest_sha256=self._manifest)
            if current != self._location:
                raise ValueError('location_changed')
            child = _worker_factory_preflight(self._bundle, self._manifest, self._lock)
            args = {'runtime-root': current['runtime_root'], 'runtime-manifest': current['runtime_manifest'],
                    'runtime-manifest-sha256': current['runtime_manifest_sha256'],
                    'runtime-generation': current['runtime_generation'], 'runtime-revision': current['runtime_revision'],
                    'lock-file': str(self._lock), 'lock-timeout': format(self._lock_timeout, '.15g')}
            remaining = self._timeout if call_deadline is None else min(self._timeout, call_deadline - time.monotonic())
            if not 0 < remaining <= self._timeout:
                raise ValueError('deadline')
        except Exception:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        # Preserve the graceful launcher's returned owner, including HOLD. Do
        # not add a fallible post-launch check that could discard ownership.
        if type(self._admission) is not child._LocalAdmission:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        self._admission.begin_worker(self._admission_owner, self)
        try:
            return child.start_e5_worker_graceful(enabled=True, bundle_root=self._bundle,
                deployment_manifest_sha256=self._manifest, interpreter=current['interpreter'],
                interpreter_sha256=current['interpreter_sha256'], interpreter_root=current['interpreter_root'],
                tuple_args=args, timeout=remaining, scratch_root=None, worker_only=True,
                bulk_contract_sha256=self._bulk, owner_sink=owner_sink,
                _admission=self._admission, _admission_owner=self._admission_owner, _admission_factory=self)
        finally:
            self._admission.finish_worker_start(self._admission_owner, self)


def create_manifest_worker_factory(*, enabled=False, bundle_root=None, expected_manifest_sha256=None,
                                   worker_timeout_seconds=None, lock_timeout_seconds=None,
                                   bulk_contract_sha256=None, _admission=None, _admission_owner=None):
    """Prepare a pinned graceful factory, without creating a worker or files."""
    result = None
    try:
        from tools import wd_mind_runtime_child as child
        if (enabled is not True or type(_admission) is not child._LocalAdmission
                or type(_admission_owner) is not child._LocalManifestQuery
                or type(worker_timeout_seconds) not in (int, float)
                or not math.isfinite(worker_timeout_seconds) or not 0 < worker_timeout_seconds <= 1800
                or type(lock_timeout_seconds) not in (int, float) or not math.isfinite(lock_timeout_seconds)
                or not 0 <= lock_timeout_seconds <= 30):
            raise ValueError('timeout')
        _admission.assert_owner(_admission_owner)
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
                                        float(worker_timeout_seconds), float(lock_timeout_seconds), bulk_contract_sha256,
                                        _admission, _admission_owner)
        _admission.bind_factory(_admission_owner, result)
    except Exception:
        result = None
    if result is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return result


class FullRuntimeRefused(ValueError):
    """Fixed refusal without source paths, contents or key material."""


class _EagerSourceBudgetRefused(FullRuntimeRefused):
    """The local source exceeds the effective eager projection limit (640 MiB)."""


class _CacheCapacityRefused(FullRuntimeRefused):
    """The verified projection exceeds the pinned cache format's row limit."""


class _GenerationInputRefused(FullRuntimeRefused):
    """A generation-delivery request is invalid before any provider call."""


class _GenerationSizeRefused(FullRuntimeRefused):
    """A verified export exceeds the caller's existing size limit."""


class FullRuntimeSnapshotHold(FullRuntimeRefused):
    """A source verifier remains owned; no memory was returned."""


class FullRuntimeCancellationUnknown(BaseException):
    """A bounded cancellation inspection could not establish all group leaves."""
    def __init__(self):
        super().__init__('mind_full_runtime_cancellation_unknown')


def _flat_cancellation(exc):
    """Copy cancellation signals with bounded inspection and fixed text."""
    kind = type(exc)
    if issubclass(kind, BaseExceptionGroup):
        pending, found, visited = [exc], [], 0
        try:
            while pending:
                visited += 1
                if visited > 1024 or len(pending) + len(found) > 1024:
                    return FullRuntimeCancellationUnknown()
                item = pending.pop()
                item_type = type(item)
                if issubclass(item_type, BaseExceptionGroup):
                    children = BaseExceptionGroup.exceptions.__get__(item)
                    if len(pending) + len(found) + len(children) > 1024:
                        return FullRuntimeCancellationUnknown()
                    pending.extend(reversed(children))
                else:
                    for signal_type in (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError,
                                        FullRuntimeCancellationUnknown):
                        if issubclass(item_type, signal_type):
                            found.append(signal_type())
                            break
            return BaseExceptionGroup('provider_cancelled', found) if found else None
        except BaseException:
            return FullRuntimeCancellationUnknown()
    for signal_type in (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError, FullRuntimeCancellationUnknown):
        if issubclass(kind, signal_type):
            return signal_type()
    return None


class _OwnedSnapshotWatcher:
    """Retain only the verifier reserved for this owner, including on HOLD."""
    __slots__ = ('_watcher', '_lock', '_closed')

    def __init__(self):
        self._watcher = None
        self._lock = threading.Lock()
        self._closed = False

    def _capture(self, watcher):
        with self._lock:
            if self._closed or (self._watcher is not None and self._held_unlocked() is not False):
                raise ValueError('watcher_already_owned_or_closed')
            self._watcher = watcher

    def _held(self):
        with self._lock:
            return self._held_unlocked()

    def _begin_close(self):
        with self._lock:
            # Seal before testing liveness so no later reservation can race a
            # STOPPED result. A held verifier remains retained for retry.
            self._closed = True
            return self._held_unlocked()

    def _current(self):
        with self._lock:
            return self._watcher

    def _held_unlocked(self):
        if self._watcher is None:
            return False
        pending_cancel = None
        try:
            observed = self._watcher.alive()
            return observed if type(observed) is bool else None
        except (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError):
            raise
        except (BaseExceptionGroup, FullRuntimeCancellationUnknown) as exc:
            pending_cancel = _flat_cancellation(exc)
        except BaseException:
            return None
        if pending_cancel is not None:
            raise pending_cancel from None
        return None


def _reserve_owned_snapshot_watcher(package, lease):
    """Capture the specific reservation before it can start a verifier."""
    registry = package._WATCHER_REGISTRY
    with registry['lock']:
        current = registry['owner']
        if current is not None and not (current.finished.is_set() and not current.alive()):
            raise package.SnapshotPackageHold('content_snapshot_watcher_hold')
        watcher = package._SourceWatcher()
        lease._capture(watcher)
        registry['owner'] = watcher
        return watcher


class _PrivateBinding:
    __slots__ = ('__snapshot', '__bundle', '__manifest', '__controls', '__args', '__policy_pin')
    authority = 'none'

    def __init__(self, snapshot, bundle, manifest, controls, args, policy_pin=None):
        self.__snapshot = snapshot
        self.__bundle, self.__manifest, self.__controls, self.__args = bundle, manifest, controls, args
        self.__policy_pin = policy_pin

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

    def _immutable_checkpoint(self, frozen, *, source_budget=None):
        """Check controls and source identity without rehashing the prefix."""
        if self.__snapshot != frozen or _bundle_inputs(self.__bundle, self.__manifest) != self.__controls:
            raise ValueError('binding_changed')
        source, sidecar, _ = prefix._deployment_source(str(self.__bundle), self.__manifest)
        args = self.__args
        if (set(args) != {'enabled', 'bundle_root', 'expected_manifest_sha256', 'source_path',
                          'generation_path', 'cursor', 'expected_prefix_sha256'}
                or args['enabled'] is not True or type(args['bundle_root']) is not str
                or not startup._same_path(Path(args['bundle_root']), self.__bundle)
                or args['expected_manifest_sha256'] != self.__manifest):
            raise ValueError('startup_binding_changed')
        cursor = args['cursor']
        if (not startup._same_path(source, Path(args['source_path']))
                or not startup._same_path(sidecar, Path(args['generation_path']))
                or cursor.generation is not None):
            raise ValueError('source_changed')
        controls = dict(frozen)
        controls.pop('projection_bytes')
        controls.pop('projection_sha256')
        context = reader._inputs(controls, complete_source=True)
        if (context['decision']['source_prefix']['sha256'] != args['expected_prefix_sha256']
                or context['messages']['end_cursor'] != cursor
                or not startup._same_path(Path(context['messages']['source_path']), source)
                or not startup._same_path(Path(context['messages']['generation_path']), sidecar)):
            raise ValueError('anchor_changed')
        with bridge._open_log(source) as stream:
            length = prefix._fence(stream, source, sidecar, cursor.file_identity, cursor.offset)
            stream.seek(cursor.offset - 1)
            if prefix._budgeted_read(stream, 1, source_budget) != b'\n':
                raise ValueError('incomplete_cursor')
            prefix._fence(stream, source, sidecar, cursor.file_identity, length)
        if _bundle_inputs(self.__bundle, self.__manifest) != self.__controls:
            raise ValueError('binding_changed')

    def _snapshot_support(self):
        """Execute the verified watcher bytes, never cached or preloaded code."""
        global _SNAPSHOT_MODULE_ATTESTATION
        if not _SNAPSHOT_MODULE_LOCK.acquire(blocking=False):
            raise ValueError('watcher_loader')
        try:
            raw = prefix._read_pinned(self.__bundle / 'deployment-manifest.json', self.__manifest,
                                      prefix.MAX_CONTROL_BYTES)
            manifest = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                                  parse_constant=reader._bad_constant)
            if type(manifest) is not dict or type(manifest.get('files')) is not dict:
                raise ValueError('watcher_manifest')
            relative = 'tools-bootstrap/tools/wd_mind_content_snapshot_package.py'
            path, name = self.__bundle.joinpath(*relative.split('/')), 'tools.wd_mind_content_snapshot_package'
            digest = manifest['files'].get(relative)
            if type(digest) is not str or not reader._hex(digest.lower()):
                raise ValueError('watcher_pin')
            digest = digest.lower()
            verified = prefix._read_pinned(path, digest, 8 * 1024 * 1024)
            package = sys.modules.get('tools')
            if type(package) is not types.ModuleType:
                raise ValueError('watcher_package')
            module = sys.modules.get(name)
            published = False
            if module is None:
                if getattr(package, 'wd_mind_content_snapshot_package', None) is not None:
                    raise ValueError('watcher_module_conflict')
                spec = importlib.util.find_spec(name)
                if spec is None or type(spec.origin) is not str or not startup._same_path(Path(spec.origin), path):
                    raise ValueError('watcher_origin')
                module = types.ModuleType(name)
                module.__file__, module.__package__, module.__spec__ = str(path), 'tools', spec
                try:
                    # Execute privately: the normal import fast path must not
                    # observe this module before source and manifest rechecks.
                    exec(compile(verified, str(path), 'exec', dont_inherit=True), module.__dict__)
                    if getattr(module, 'reader', None) is not reader:
                        raise ValueError('watcher_origin')
                    prefix._read_pinned(self.__bundle / 'deployment-manifest.json', self.__manifest,
                                        prefix.MAX_CONTROL_BYTES)
                    prefix._read_pinned(path, digest, 8 * 1024 * 1024)
                    # Attest our verified module before publishing it. An
                    # independent competing import may run separately; refuse
                    # rather than adopt its module if it wins publication.
                    _SNAPSHOT_MODULE_ATTESTATION = (module, str(path), self.__manifest, digest)
                    if sys.modules.setdefault(name, module) is not module:
                        raise ValueError('watcher_module_conflict')
                    existing = getattr(package, 'wd_mind_content_snapshot_package', None)
                    if existing is not None and existing is not module:
                        raise ValueError('watcher_module_conflict')
                    setattr(package, 'wd_mind_content_snapshot_package', module)
                    published = True
                except BaseException:
                    if sys.modules.get(name) is module:
                        del sys.modules[name]
                    if getattr(package, 'wd_mind_content_snapshot_package', None) is module:
                        delattr(package, 'wd_mind_content_snapshot_package')
                    _SNAPSHOT_MODULE_ATTESTATION = None
                    raise
            elif (type(module) is not types.ModuleType
                  or type(_SNAPSHOT_MODULE_ATTESTATION) is not tuple
                  or len(_SNAPSHOT_MODULE_ATTESTATION) != 4
                  or _SNAPSHOT_MODULE_ATTESTATION[0] is not module
                  or _SNAPSHOT_MODULE_ATTESTATION[1:] != (str(path), self.__manifest, digest)):
                raise ValueError('watcher_unverified_preload')
            try:
                if (sys.modules.get(name) is not module
                        or getattr(package, 'wd_mind_content_snapshot_package', None) is not module
                        or type(getattr(module, '__file__', None)) is not str
                        or not startup._same_path(Path(module.__file__), path) or module.reader is not reader):
                    raise ValueError('watcher_origin')
                prefix._read_pinned(self.__bundle / 'deployment-manifest.json', self.__manifest,
                                    prefix.MAX_CONTROL_BYTES)
                prefix._read_pinned(path, digest, 8 * 1024 * 1024)
            except BaseException:
                if published:
                    if sys.modules.get(name) is module:
                        del sys.modules[name]
                    if getattr(package, 'wd_mind_content_snapshot_package', None) is module:
                        delattr(package, 'wd_mind_content_snapshot_package')
                    if (type(_SNAPSHOT_MODULE_ATTESTATION) is tuple
                            and len(_SNAPSHOT_MODULE_ATTESTATION) == 4
                            and _SNAPSHOT_MODULE_ATTESTATION[0] is module):
                        _SNAPSHOT_MODULE_ATTESTATION = None
                raise
            return module
        finally:
            _SNAPSHOT_MODULE_LOCK.release()


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


def _fixed_group_cancellation(group):
    """Copy bounded builtin cancellation signals without provider hooks/data."""
    pending, signals, current, children, visited = [group], [], None, None, 0
    try:
        while pending:
            visited += 1
            if visited > 1024 or len(pending) + len(signals) > 1024:
                return None
            current = pending.pop()
            kind = type(current)
            if issubclass(kind, BaseExceptionGroup):
                children = BaseExceptionGroup.exceptions.__get__(current)
                if len(pending) + len(signals) + len(children) > 1024:
                    return None
                pending.extend(reversed(children))
            elif issubclass(kind, KeyboardInterrupt):
                signals.append(KeyboardInterrupt())
            elif issubclass(kind, GeneratorExit):
                signals.append(GeneratorExit())
            elif issubclass(kind, asyncio.CancelledError):
                signals.append(asyncio.CancelledError())
        return BaseExceptionGroup('provider_cancelled', signals) if signals else None
    except BaseException:
        return None
    finally:
        group = current = pending = children = None


class _BootstrapValues:
    """Keep tuple values out of frame-local repr during transfer."""
    __slots__ = ('__values',)

    def __init__(self, values):
        self.__values = values

    def __repr__(self):
        return '<PrivateMindBootstrapValues>'

    def reveal(self):
        return self.__values


class _PrivateParentStream:
    """Hide the retained input object from ordinary traceback-local repr."""
    __slots__ = ('__stream',)

    def __init__(self, stream):
        self.__stream = stream

    def __repr__(self):
        return '<PrivateMindParentStream>'

    def readinto(self, view):
        return self.__stream.readinto(view)


class _ParentControlBuffer:
    """Receive MCT1 in a private buffer; caller retains I/O and deadline."""
    __slots__ = ('__stream', '__data', '__length', '__state', '__lock')

    def __init__(self, stream, *, prefix_budget=0):
        if type(prefix_budget) is not int or prefix_budget not in (0, 133):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        self.__stream, self.__data = stream, bytearray(136 + 65536 + 1 + prefix_budget)
        self.__length, self.__state = 0, 'NEW'
        self.__lock = threading.Lock()

    def __repr__(self):
        return '<PrivateMindParentControlBuffer>'

    def read_from(self, *, enabled=False):
        cancelled_type = cancellation_group = view = None
        success = False
        try:
            if enabled is not True or type(self.__stream) is not _PrivateParentStream or self.__state != 'NEW':
                raise ValueError('input')
            self.__state = 'READING'
            while self.__length < len(self.__data):
                view = memoryview(self.__data)[self.__length:]
                count = self.__stream.readinto(view)
                view = None
                if type(count) is not int or not 0 <= count <= len(self.__data) - self.__length:
                    raise ValueError('read')
                if count == 0:
                    self.__state, success = 'SEALED', True
                    break
                self.__length += count
            if not success:
                raise ValueError('too_large')
        except KeyboardInterrupt:
            cancelled_type = KeyboardInterrupt
        except GeneratorExit:
            cancelled_type = GeneratorExit
        except asyncio.CancelledError:
            cancelled_type = asyncio.CancelledError
        except BaseExceptionGroup as group:
            cancellation_group = _fixed_group_cancellation(group)
        except BaseException:
            pass
        finally:
            view = None
            if not success:
                self.__state = 'FAILED'
        if cancelled_type is not None:
            raise cancelled_type() from None
        if cancellation_group is not None:
            raise cancellation_group from None
        if not success:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None

    def snapshot(self):
        if self.__state != 'SEALED':
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return bytes(memoryview(self.__data)[:self.__length])

    def take_snapshot(self):
        with self.__lock:
            if self.__state != 'SEALED':
                raise FullRuntimeRefused('mind_full_runtime_refused') from None
            self.__state = 'CONSUMED'
            return bytes(memoryview(self.__data)[:self.__length])


class _ParentBootstrap:
    """Single-use in-memory MCT1 values; possession is not parent proof."""
    __slots__ = ('__values', '__lock')

    def __init__(self, values):
        self.__values, self.__lock = _BootstrapValues(values), threading.Lock()

    def __repr__(self):
        return '<PrivateMindParentBootstrap>'

    def take(self):
        values = cancelled_type = cancellation_group = None
        try:
            try:
                with self.__lock:
                    values, self.__values = self.__values, None
                if values is None:
                    raise ValueError('consumed')
            except KeyboardInterrupt:
                cancelled_type, values = KeyboardInterrupt, None
            except GeneratorExit:
                cancelled_type, values = GeneratorExit, None
            except asyncio.CancelledError:
                cancelled_type, values = asyncio.CancelledError, None
            except BaseExceptionGroup as group:
                cancellation_group, values = _fixed_group_cancellation(group), None
            except BaseException:
                values = None
            if cancelled_type is not None:
                raise cancelled_type() from None
            if cancellation_group is not None:
                raise cancellation_group from None
            if values is None:
                raise FullRuntimeRefused('mind_full_runtime_refused') from None
            return values.reveal()
        finally:
            values = None


def _parse_parent_control_bytes(*, enabled=False, control_bytes=None, expected_manifest_sha256=None,
                                expected_identifier_policy_sha256=None):
    """Parse exact MCT1 bytes with independent pins, without opening a handle.

    A caller must prove the trusted parent and source both pins independently.
    This parser alone does not authenticate a sender, enforce deadlines, prove
    EOF, or prevent replay; the complete bounded input must include EOF proof.
    """
    output = cancelled_type = cancellation_group = None
    try:
        if (enabled is not True or type(control_bytes) is not bytes
                or not 137 <= len(control_bytes) <= 136 + 65536
                or type(expected_manifest_sha256) is not str
                or not reader._hex(expected_manifest_sha256)
                or type(expected_identifier_policy_sha256) is not str
                or not reader._hex(expected_identifier_policy_sha256)):
            raise ValueError('input')
        magic, policy_length = struct.unpack('>4sI', control_bytes[:8])
        if magic != b'MCT1' or not 1 <= policy_length <= 65536 or len(control_bytes) != 136 + policy_length:
            raise ValueError('shape')
        manifest, nonce, channel, policy_sha, policy = (control_bytes[8:40], control_bytes[40:72],
            control_bytes[72:104], control_bytes[104:136], control_bytes[136:])
        if (not hmac.compare_digest(manifest.hex(), expected_manifest_sha256)
                or not hmac.compare_digest(policy_sha.hex(), expected_identifier_policy_sha256)
                or not hmac.compare_digest(hashlib.sha256(policy).digest(), policy_sha)
                or hmac.compare_digest(nonce, channel)):
            raise ValueError('pin')
        # This HMAC permits schema validation only; the independent SHA is the
        # policy anchor and this fixed validation key authenticates nothing.
        validation_key = bytes(32)
        tag = hmac.new(validation_key, policy, hashlib.sha256).hexdigest()
        reader._load_identifier_policy(policy, validation_key, tag, allow_empty_v2=True)
        output = _ParentBootstrap((manifest.hex(), nonce, channel, policy_sha.hex(), policy))
    except KeyboardInterrupt:
        cancelled_type, output = KeyboardInterrupt, None
    except GeneratorExit:
        cancelled_type, output = GeneratorExit, None
    except asyncio.CancelledError:
        cancelled_type, output = asyncio.CancelledError, None
    except BaseExceptionGroup as group:
        cancellation_group, output = _fixed_group_cancellation(group), None
    except BaseException:
        output = None
    finally:
        control_bytes = manifest = nonce = channel = policy_sha = policy = validation_key = tag = None
    if cancelled_type is not None:
        raise cancelled_type() from None
    if cancellation_group is not None:
        raise cancellation_group from None
    if output is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return output


def _parse_parent_control(*, enabled=False, control_buffer=None, expected_manifest_sha256=None,
                          expected_identifier_policy_sha256=None):
    """Isolate parsing from raw control bytes and provider tracebacks."""
    output = cancelled_type = cancellation_group = None
    try:
        if enabled is not True or type(control_buffer) is not _ParentControlBuffer:
            raise ValueError('input')
        output = _parse_parent_control_bytes(enabled=True, control_bytes=control_buffer.take_snapshot(),
            expected_manifest_sha256=expected_manifest_sha256,
            expected_identifier_policy_sha256=expected_identifier_policy_sha256)
    except KeyboardInterrupt:
        cancelled_type, output = KeyboardInterrupt, None
    except GeneratorExit:
        cancelled_type, output = GeneratorExit, None
    except asyncio.CancelledError:
        cancelled_type, output = asyncio.CancelledError, None
    except BaseExceptionGroup as group:
        cancellation_group, output = _fixed_group_cancellation(group), None
    except BaseException:
        output = None
    finally:
        control_buffer = None
    if cancelled_type is not None:
        raise cancelled_type() from None
    if cancellation_group is not None:
        raise cancellation_group from None
    if output is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return output


def _prepare_owner_from_control_values(values, *, bundle_root, expected_manifest_sha256, lane,
                                       worker_timeout_seconds, lock_timeout_seconds,
                                       shutdown_timeout, bulk_contract_sha256, policy_anchor):
    manifest, nonce, channel_key, policy_sha, policy = values
    if manifest != expected_manifest_sha256:
        raise ValueError('manifest')
    binding = assemble_full_runtime(enabled=True, bundle_root=bundle_root,
        expected_manifest_sha256=expected_manifest_sha256, identifier_policy_bytes=policy,
        expected_identifier_policy_sha256=policy_sha)
    factory = create_manifest_worker_factory(enabled=True, bundle_root=bundle_root,
        expected_manifest_sha256=expected_manifest_sha256, worker_timeout_seconds=worker_timeout_seconds,
        lock_timeout_seconds=lock_timeout_seconds, bulk_contract_sha256=bulk_contract_sha256)
    owner = create_single_manifest_owner(enabled=True, binding=binding, lane=lane,
                                         worker_factory=factory, shutdown_timeout=shutdown_timeout,
                                         policy_anchor=policy_anchor)
    return {'state':'PREPARED_NOT_RUNNING', 'authority':'none', 'owner':owner,
            'bootstrap':_ParentBootstrap(values)}


def prepare_manifest_bound_owner(*, enabled=False, policy_anchor: _TrustedManifestPolicyAnchor | None = None,
                                 control_buffer=None, bundle_root=None, expected_manifest_sha256=None,
                                 lane=None, worker_timeout_seconds=None, lock_timeout_seconds=None,
                                 shutdown_timeout=None, bulk_contract_sha256=None):
    """Prepare a retained inert owner after trusted control and policy checks.

    The trusted provider is absent and therefore production always refuses.
    The returned owner has not been started; its parent must retain it before
    starting and retain any worker/pipe in HOLD through uncertain completion.
    """
    output = cancelled_type = cancellation_group = bootstrap = None
    try:
        if (enabled is not True or type(policy_anchor) is not _TrustedManifestPolicyAnchor
                or type(control_buffer) is not _ParentControlBuffer or type(bundle_root) is not str
                or type(expected_manifest_sha256) is not str
                or not startup._same_path(Path(bundle_root), Path(policy_anchor.bundle_root))
                or expected_manifest_sha256 != policy_anchor.manifest_sha256):
            raise ValueError('input')
        _require_trusted_parent_export(owner=None, stream=None, bootstrap=control_buffer)
        candidate = inspect_manifest_policy_candidate(enabled=True, bundle_root=bundle_root,
            expected_manifest_sha256=expected_manifest_sha256).status()
        if (candidate['identifier_policy_sha256'] != policy_anchor.policy_sha256
                or candidate['approval_reference_sha256'] != policy_anchor.approval_reference_sha256):
            raise ValueError('approval_binding')
        bootstrap = _parse_parent_control(enabled=True, control_buffer=control_buffer,
            expected_manifest_sha256=expected_manifest_sha256,
            expected_identifier_policy_sha256=policy_anchor.policy_sha256)
        output = _prepare_owner_from_control_values(bootstrap.take(), bundle_root=bundle_root,
            expected_manifest_sha256=expected_manifest_sha256, lane=lane,
            worker_timeout_seconds=worker_timeout_seconds, lock_timeout_seconds=lock_timeout_seconds,
            shutdown_timeout=shutdown_timeout, bulk_contract_sha256=bulk_contract_sha256,
            policy_anchor=policy_anchor)
    except KeyboardInterrupt:
        cancelled_type, output = KeyboardInterrupt, None
    except GeneratorExit:
        cancelled_type, output = GeneratorExit, None
    except asyncio.CancelledError:
        cancelled_type, output = asyncio.CancelledError, None
    except BaseExceptionGroup as group:
        cancellation_group, output = _fixed_group_cancellation(group), None
    except BaseException:
        output = None
    finally:
        control_buffer = bootstrap = None
    if cancelled_type is not None:
        raise cancelled_type() from None
    if cancellation_group is not None:
        raise cancellation_group from None
    if output is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return output


def assemble_full_runtime(*, enabled=False, bundle_root=None, expected_manifest_sha256=None,
                          identifier_policy_bytes=None, expected_identifier_policy_sha256=None):
    """Build a private snapshot using a separately anchored identifier policy.

    The caller must obtain the policy bytes and its approved SHA independently
    of the owner export. A matching caller-supplied pair alone is not evidence
    of approval or of a trusted parent channel.
    """
    result = None
    over_budget = False
    cancelled_type = cancellation_group = None
    try:
        if (enabled is not True or type(bundle_root) is not str or not Path(bundle_root).is_absolute()
                or '..' in Path(bundle_root).parts or type(expected_manifest_sha256) is not str
                or not reader._hex(expected_manifest_sha256)
                or type(identifier_policy_bytes) is not bytes
                or not 0 < len(identifier_policy_bytes) <= 65536
                or type(expected_identifier_policy_sha256) is not str
                or not reader._hex(expected_identifier_policy_sha256)
                or not hmac.compare_digest(reader._sha(identifier_policy_bytes),
                                           expected_identifier_policy_sha256)):
            raise ValueError('input')
        identifier = identifier_policy_bytes
        key = secrets.token_bytes(32)
        if type(key) is not bytes or len(key) != 32:
            raise ValueError('key')
        tag = hmac.new(key, identifier, hashlib.sha256).hexdigest()
        reader._load_identifier_policy(identifier, key, tag, allow_empty_v2=True)
        bundle = Path(bundle_root)
        controls = _bundle_inputs(bundle, expected_manifest_sha256)
        try:
            observed = prefix.observe_prefix(enabled=True, bundle_root=bundle_root,
                expected_manifest_sha256=expected_manifest_sha256, scope='full_prefix_v1',
                max_prefix_bytes=reader._max_eager_source_bytes())
        except prefix._ObservedPrefixBudgetRefused as exc:
            over_budget = type(exc) is prefix._ObservedPrefixBudgetRefused
            raise
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
        result = _PrivateBinding(snapshot, bundle, expected_manifest_sha256, controls, args,
                                 expected_identifier_policy_sha256)
    except KeyboardInterrupt:
        cancelled_type, result = KeyboardInterrupt, None
    except GeneratorExit:
        cancelled_type, result = GeneratorExit, None
    except asyncio.CancelledError:
        cancelled_type, result = asyncio.CancelledError, None
    except BaseExceptionGroup as group:
        cancellation_group, result = _fixed_group_cancellation(group), None
    except BaseException:
        pass
    finally:
        identifier_policy_bytes = identifier = key = tag = controls = observed = meta = None
        inputs = projection = projection_bytes = snapshot = decision = decision_bytes = None
        message_policy = roster_bytes = nonreview = scope = bound = args = None
    if cancelled_type is not None:
        raise cancelled_type() from None
    if cancellation_group is not None:
        raise cancellation_group from None
    if over_budget:
        raise _EagerSourceBudgetRefused('SOURCE_BUDGET') from None
    if result is None:
        raise FullRuntimeRefused('mind_full_runtime_refused') from None
    return result


class _PrivateFlatMemory:
    __slots__ = ('binding', 'snapshot', 'manifest', 'ids', 'vectors', 'encoder_binding', 'cache_status')

    def __init__(self, binding, snapshot, manifest, ids, vectors, encoder_binding, cache_status='DISABLED'):
        self.binding, self.snapshot, self.manifest = binding, snapshot, manifest
        self.ids, self.vectors, self.encoder_binding = ids, vectors, encoder_binding
        self.cache_status = cache_status

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


def _cache_record_keys(record):
    """Derive cache keys from screened content without persisting that content."""
    if type(record) is not dict or type(record.get('record_id')) is not str:
        raise ValueError('cache_record')
    passage = 'passage: ' + reader.decode_text(record['stored_text'])[:2000]
    return reader._sha(reader._canonical(record)), reader._sha(passage.encode('utf-8'))


class _CacheProjectionMismatch(ValueError):
    """A complete cache does not match the screened source projection."""


_CACHE_CONTENT_PATHS = (
    'wd-mind-runtime-manifest.json',
    'wd-mind-interpreter-identity.json',
    'tools-bootstrap/tools/wd_mind_e5_worker.py',
    'tools-bootstrap/tools/wd_mind_e5_client.py',
    'tools-bootstrap/tools/wd_mind_e5_rotating_client.py',
    'tools-bootstrap/tools/wd_mind_e5_lifecycle.py',
    'tools-bootstrap/tools/wd_mind_e5_snapshot_adapter.py',
    'tools-bootstrap/tools/wd_mind_runtime_bootstrap.py',
    'tools-bootstrap/tools/wd_mind_runtime_child.py',
    'tools-bootstrap/tools/wd_mind_runtime_manifest.py',
    'tools-bootstrap/tools/wd_mind_full_runtime.py',
)


def _cache_content_pins(binding):
    """Read a closed set of content-affecting pins from the verified bundle."""
    bundle, manifest_pin = binding._PrivateBinding__bundle, binding._PrivateBinding__manifest
    manifest_bytes = prefix._read_pinned(bundle / 'deployment-manifest.json', manifest_pin,
                                         prefix.MAX_CONTROL_BYTES)
    document = json.loads(manifest_bytes.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                          parse_constant=reader._bad_constant)
    if type(document) is not dict or type(document.get('files')) is not dict:
        raise ValueError('cache_manifest')
    pins = {}
    for relative in _CACHE_CONTENT_PATHS:
        pin = document['files'].get(relative)
        if type(pin) is not str or not reader._hex(pin.lower()):
            raise ValueError('cache_content_pin')
        pin = pin.lower()
        path = bundle.joinpath(*relative.split('/'))
        raw = prefix._read_pinned(path, pin, 8 * 1024 * 1024)
        if relative in ('wd-mind-runtime-manifest.json', 'wd-mind-interpreter-identity.json'):
            pins[relative] = _cache_descriptor_content_sha(raw, relative)
        else:
            pins[relative] = pin
    return pins


def _cache_descriptor_content_sha(raw, relative):
    """Hash descriptor file identities without binding runtime generation."""
    document = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                          parse_constant=reader._bad_constant)
    runtime = relative == 'wd-mind-runtime-manifest.json'
    expected_keys = {'schema', 'generation', 'revision', 'files'} if runtime else {'schema', 'files'}
    schema = 'wd.q4-runtime-manifest.v1' if runtime else 'wd.q4-interpreter-identity.v1'
    if (type(document) is not dict or set(document) != expected_keys or document['schema'] != schema
            or type(document['files']) is not list or not document['files']):
        raise ValueError('cache_descriptor')
    expected_row_keys = {'path', 'size', 'sha256'} if runtime else {'path', 'size', 'sha256', 'role'}
    seen, rows = set(), []
    for row in document['files']:
        if (type(row) is not dict or set(row) != expected_row_keys or type(row['path']) is not str
                or not row['path'] or type(row['size']) is not int or row['size'] < 0
                or type(row['sha256']) is not str or not reader._hex(row['sha256'].lower())
                or row['path'].casefold() in seen
                or (not runtime and row['role'] not in ('interpreter', 'stdlib', 'dll'))):
            raise ValueError('cache_descriptor')
        seen.add(row['path'].casefold())
        normalized = dict(row)
        normalized['sha256'] = row['sha256'].lower()
        rows.append(normalized)
    rows.sort(key=lambda row: row['path'])
    return reader._sha(reader._canonical({'schema': schema, 'files': rows}))


def _cache_projection_sha256(projection):
    """Bind screened records without persisting the per-process policy HMAC."""
    if (type(projection) is not dict or type(projection.get('messages')) is not dict
            or type(projection['messages'].get('records')) is not list):
        raise ValueError('cache_projection')
    return reader._sha(reader._canonical(projection['messages']['records']))


def _cache_snapshot_binding(binding, snapshot, projection, encoder_binding_bytes, encoder_binding_sha256):
    """Pin inputs that affect screened passages or vectors, independently of bundle ID."""
    if type(binding) is not _PrivateBinding or type(snapshot) is not dict:
        raise ValueError('cache_binding')
    _checked_encoder_binding(encoder_binding_bytes, encoder_binding_sha256)
    args = binding._PrivateBinding__args
    controls = binding._PrivateBinding__controls
    cursor = args['cursor']
    if (type(cursor.offset) is not int or cursor.offset < 0
            or type(cursor.file_identity) is not str or not cursor.file_identity
            or type(controls) is not tuple or len(controls) != 3
            or not all(type(item) is bytes for item in controls)
            or not reader._hex(binding._PrivateBinding__policy_pin)
            or not reader._hex(args['expected_prefix_sha256'])
            or snapshot['messages']['end_cursor'] != cursor):
        raise ValueError('cache_binding')
    policy = {'roster_sha256': reader._sha(controls[1]),
              'message_policy_sha256': reader._sha(controls[2]),
              'identifier_policy_sha256': binding._PrivateBinding__policy_pin,
              'scope_sha256': reader._sha(snapshot['approved_tasks_bytes'])}
    content_pins = _cache_content_pins(binding)
    projection_code = {'modules': reader.module_sha256s(), 'content_pins': content_pins,
                       'dependencies': reader._REQUIRED_CODE,
                       'passage_algorithm': 'passage-prefix-utf8-truncate-2000-v1'}
    return {'source_identity_sha256': reader._sha(reader._canonical(cursor.file_identity)),
            'source_generation_sha256': reader._sha(reader._canonical(cursor.generation)),
            'prefix_offset': cursor.offset, 'prefix_sha256': args['expected_prefix_sha256'],
            'projection_sha256': _cache_projection_sha256(projection),
            'model_sha256': reader._sha(reader._canonical({'encoder_binding_sha256': encoder_binding_sha256,
                                                           'content_pins': content_pins})),
            'policy_sha256': reader._sha(reader._canonical(policy)),
            'projection_code_sha256': reader._sha(reader._canonical(projection_code))}


def _cache_vectors_from_rows(by_id, rows):
    """Accept the entire old screened set or reject the entire cache."""
    if type(by_id) is not dict or type(rows) is not tuple:
        raise ValueError('cache_rows')
    current = {}
    for record_id, record in by_id.items():
        if (type(record_id) is not str or type(record) is not dict
                or record.get('record_id') != record_id):
            raise ValueError('cache_record')
        keys = _cache_record_keys(record)
        if keys[0] in current:
            raise ValueError('cache_duplicate')
        current[keys[0]] = (record_id, keys[1])
    reusable = {}
    previous = ''
    for row in rows:
        if (type(row) is not tuple or len(row) != 3 or type(row[0]) is not str
                or type(row[1]) is not str or type(row[2]) is not bytes
                or not reader._hex(row[0]) or not reader._hex(row[1])
                or row[0] <= previous or len(row[2]) != 1536):
            raise _CacheProjectionMismatch('cache_row')
        previous = row[0]
        item = current.get(row[0])
        if item is None or not hmac.compare_digest(item[1], row[1]):
            raise _CacheProjectionMismatch('cache_projection_changed')
        try:
            reader._q4_vector(struct.unpack('<384f', row[2]))
        except ValueError:
            raise _CacheProjectionMismatch('cache_vector') from None
        reusable[item[0]] = row[2]
    return reusable


def _cache_old_projection(snapshot, offset, prefix_sha256):
    """Re-screen the old prefix with the current, independently pinned policies."""
    if (type(snapshot) is not dict or type(offset) is not int or offset < 0
            or not reader._hex(prefix_sha256)):
        raise ValueError('cache_old_prefix')
    inputs = dict(snapshot)
    inputs.pop('projection_bytes')
    inputs.pop('projection_sha256')
    inputs['messages'] = dict(inputs['messages'])
    cursor = inputs['messages']['end_cursor']
    if offset > cursor.offset:
        raise ValueError('cache_old_prefix')
    inputs['messages']['end_cursor'] = type(cursor)(offset, cursor.file_identity, cursor.generation)
    decision = reader._pinned(inputs['decision_bytes'], inputs['decision_sha256'])
    decision['source_prefix'] = {'offset': offset, 'sha256': prefix_sha256, 'generation': cursor.generation}
    inputs['decision_bytes'] = reader._canonical(decision)
    inputs['decision_sha256'] = reader._sha(inputs['decision_bytes'])
    return reader.rederive_complete_message_projection(**inputs)


def _cache_verified_reusable(snapshot, projection, by_id, cache_result):
    """Require the complete old screened record set before using any vector."""
    stored, rows = cache_result.binding, cache_result.rows
    offset, old_sha = stored['prefix_offset'], stored['prefix_sha256']
    cursor = snapshot['messages']['end_cursor']
    old_projection = projection if offset == cursor.offset else _cache_old_projection(snapshot, offset, old_sha)
    if not hmac.compare_digest(_cache_projection_sha256(old_projection), stored['projection_sha256']):
        raise _CacheProjectionMismatch('cache_projection_changed')
    old_records = old_projection['messages']['records']
    old_keys = sorted(_cache_record_keys(record)[0] for record in old_records)
    if (len(set(old_keys)) != len(old_keys) or tuple(old_keys) != tuple(row[0] for row in rows)):
        raise _CacheProjectionMismatch('cache_record_set_changed')
    return _cache_vectors_from_rows(by_id, rows)


def _cache_rows_from_screened_projection(projection, ids, vectors):
    """Prepare hash-only cache rows from the already verified screened projection."""
    _flat_hashes(ids, vectors)
    records = projection['messages']['records']
    by_id = {record['record_id']: record for record in records}
    if len(by_id) != len(records) or tuple(sorted(by_id)) != ids:
        raise ValueError('cache_records')
    rows = []
    for index, record_id in enumerate(ids):
        record_sha, passage_sha = _cache_record_keys(by_id[record_id])
        rows.append((record_sha, passage_sha, vectors[index * 1536:(index + 1) * 1536]))
    rows.sort(key=lambda row: row[0])
    if len({row[0] for row in rows}) != len(rows):
        raise ValueError('cache_duplicate')
    return tuple(rows)


class _PrivateVectorCache:
    """Owner-only adapter; the installed route must supply a pinned module."""
    __slots__ = ('_module', '_path', '_verify')

    def __init__(self, module, path, verify):
        if not callable(verify) or not callable(getattr(module, 'load_cache', None)) or not callable(getattr(module, 'write_cache', None)):
            raise ValueError('cache_provider')
        self._module, self._path, self._verify = module, path, verify

    def load(self, binding, verify_prefix):
        self._verify()
        try:
            value = self._module.load_cache(self._path, binding, verify_prefix)
        except Exception:
            self._verify()
            raise
        self._verify()
        return value

    def preflight(self):
        self._verify()
        try:
            status = self._module.preflight(self._path)
        except Exception:
            self._verify()
            raise
        self._verify()
        if status not in ('MISSING', 'PRESENT'):
            raise ValueError('cache_preflight')
        return status

    def has_capacity(self, count):
        self._verify()
        try:
            limit = getattr(self._module, '_MAX_ROWS', None)
        except Exception:
            self._verify()
            raise
        self._verify()
        if type(count) is not int or count < 0 or type(limit) is not int or limit != 250000:
            raise ValueError('cache_controls')
        return count <= limit

    def write(self, binding, rows):
        self._verify()
        try:
            self._module.write_cache(self._path, binding, rows)
        except Exception:
            self._verify()
            raise
        self._verify()


_CACHE_MODULE_LOCK = threading.Lock()
_CACHE_MODULE_ATTESTATION = None


def _load_pinned_vector_cache(binding):
    """Load verified bundle bytes; never accept an unverified preloaded module."""
    global _CACHE_MODULE_ATTESTATION
    if type(binding) is not _PrivateBinding or not _CACHE_MODULE_LOCK.acquire(blocking=False):
        raise ValueError('cache_loader')
    try:
        bundle, manifest_pin = binding._PrivateBinding__bundle, binding._PrivateBinding__manifest
        relative = 'tools-bootstrap/tools/wd_mind_vector_cache.py'
        path, name = bundle.joinpath(*relative.split('/')), 'tools.wd_mind_vector_cache'
        raw = prefix._read_pinned(bundle / 'deployment-manifest.json', manifest_pin, prefix.MAX_CONTROL_BYTES)
        manifest = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=prefix._unique_object,
                              parse_constant=reader._bad_constant)
        if type(manifest) is not dict or type(manifest.get('files')) is not dict:
            raise ValueError('cache_manifest')
        digest = manifest['files'].get(relative)
        if type(digest) is not str or not reader._hex(digest.lower()):
            raise ValueError('cache_code_pin')
        digest = digest.lower()
        verified = prefix._read_pinned(path, digest, 8 * 1024 * 1024)
        package = sys.modules.get('tools')
        if type(package) is not types.ModuleType:
            raise ValueError('cache_package')
        module = sys.modules.get(name)
        if module is None:
            if getattr(package, 'wd_mind_vector_cache', None) is not None:
                raise ValueError('cache_module_conflict')
            spec = importlib.util.find_spec(name)
            if spec is None or type(spec.origin) is not str or not startup._same_path(Path(spec.origin), path):
                raise ValueError('cache_origin')
            module = types.ModuleType(name)
            module.__file__, module.__package__, module.__spec__ = str(path), 'tools', spec
            sys.modules[name] = module
            try:
                exec(compile(verified, str(path), 'exec', dont_inherit=True), module.__dict__)
                setattr(package, 'wd_mind_vector_cache', module)
                _CACHE_MODULE_ATTESTATION = (module, str(path), manifest_pin, digest)
            except BaseException:
                if sys.modules.get(name) is module:
                    del sys.modules[name]
                raise
        elif (type(module) is not types.ModuleType
              or type(_CACHE_MODULE_ATTESTATION) is not tuple
              or len(_CACHE_MODULE_ATTESTATION) != 4
              or _CACHE_MODULE_ATTESTATION[0] is not module
              or _CACHE_MODULE_ATTESTATION[1:] != (str(path), manifest_pin, digest)):
            raise ValueError('cache_unverified_preload')

        def verify():
            if (sys.modules.get(name) is not module or getattr(package, 'wd_mind_vector_cache', None) is not module
                    or type(getattr(module, '__file__', None)) is not str
                    or not startup._same_path(Path(module.__file__), path)):
                raise ValueError('cache_origin')
            prefix._read_pinned(bundle / 'deployment-manifest.json', manifest_pin, prefix.MAX_CONTROL_BYTES)
            prefix._read_pinned(path, digest, 8 * 1024 * 1024)

        verify()
        return _PrivateVectorCache(module, r'C:\wd-mind-private\vector-cache\vectors.bin', verify)
    finally:
        _CACHE_MODULE_LOCK.release()


def _build_flat_memory_immutable(binding, passage_encoder, encoder_binding_bytes,
                                 encoder_binding_sha256, watcher_join_timeout_seconds, watcher_lease,
                                 prior_memory=None, vector_cache=None):
    """Use the pinned immutable snapshot helper and retain watcher ownership on HOLD.

    This captures the full prefix as immutable bytes and can use substantial RAM;
    neither its size nor process memory is bounded by an IPC transfer limit.
    """
    if watcher_join_timeout_seconds is not None and (
            type(watcher_join_timeout_seconds) not in (int, float)
            or not math.isfinite(watcher_join_timeout_seconds)
            or not 0 < watcher_join_timeout_seconds <= 600):
        raise ValueError('invalid_join_timeout')
    snapshot = binding.snapshot_for_query()
    package = binding._snapshot_support()
    watcher, output, problem, cleanup_problem, held = None, None, None, None, False
    over_capacity = False
    try:
        try:
            watcher = (package._reserve_watcher() if watcher_lease is None else
                       _reserve_owned_snapshot_watcher(package, watcher_lease))
        except package.SnapshotPackageHold:
            held = True
        if not held:
            frozen = package._snapshot(snapshot, {'streamed': True})
            projection, context, report = reader._verified_complete(frozen)
            if vector_cache is not None:
                if type(vector_cache) is not _PrivateVectorCache:
                    raise ValueError('cache_provider')
                records = projection['messages']['records']
                if type(records) is not list:
                    raise ValueError('records')
                # Refuse before capturing another full prefix or encoding if
                # the cache cannot hold this cold build or refresh.
                if not vector_cache.has_capacity(len(records)):
                    over_capacity = True
                    raise _CacheCapacityRefused('CACHE_CAPACITY')
            immutable_prefix = package._immutable_prefix(context)
            projection = json.loads(frozen['projection_bytes'].decode('utf-8'),
                                    object_pairs_hook=reader._unique_pairs, parse_constant=reader._bad_constant)
            if reader._serialized(projection) != frozen['projection_bytes']:
                raise ValueError('projection_changed')

            def source_unchanged():
                # Background form of the source check. The full re-derivation
                # is minutes of interpreter work on a real log; run back to
                # back in the watcher thread it starved this thread until the
                # worker launch outlived its bound (first real start,
                # 2026-11-24). The projection is a pure function of the
                # anchored prefix and the pinned controls, so the pins plus
                # byte identity of the on-disk prefix state the same thing.
                # The full re-derivation still runs before the watcher starts
                # and again after it has ended.
                controls = dict(frozen)
                controls.pop('projection_bytes')
                controls.pop('projection_sha256')
                current = reader._inputs(controls, complete_source=True)
                if (current['decision_sha256'] != report['decision_sha256']
                        or dict(current['decision']['source_prefix']) != report['source_prefix']):
                    raise ValueError('source_changed')
                if package._immutable_prefix(current, retain=False) is not None:
                    raise ValueError('prefix_retained')

            watcher.start(frozen, report, source_unchanged)
            records = projection['messages']['records']
            by_id = {record['record_id']: record for record in records}
            if not records or len(by_id) != len(records):
                raise ValueError('records')
            ids, vectors = tuple(sorted(by_id)), []
            reusable, cache_binding = {}, None
            cache_status = 'DISABLED' if vector_cache is None else 'MISS'
            if vector_cache is not None and prior_memory is None:
                if type(vector_cache) is not _PrivateVectorCache:
                    raise ValueError('cache_provider')
                cache_binding = _cache_snapshot_binding(binding, frozen, projection,
                                                        encoder_binding_bytes, encoder_binding_sha256)
                try:
                    def verify_old_prefix(offset, expected):
                        return (type(offset) is int and 0 <= offset <= len(immutable_prefix)
                                and reader._hex(expected)
                                and hmac.compare_digest(reader._sha(memoryview(immutable_prefix)[:offset]), expected))
                    cached = vector_cache.load(cache_binding, verify_old_prefix)
                    reusable = _cache_verified_reusable(frozen, projection, by_id, cached)
                    cache_status = 'HIT'
                except (vector_cache._module.CacheMiss, _CacheProjectionMismatch):
                    reusable, cache_status = {}, 'MISS'
            if prior_memory is not None:
                # The old content is reused only after both complete source
                # snapshots, all controls and the encoder have been checked.
                if type(prior_memory) is not _PrivateFlatMemory:
                    raise ValueError('refresh_memory')
                old_binding = prior_memory.binding
                old_args, new_args = old_binding._PrivateBinding__args, binding._PrivateBinding__args
                old_cursor, new_cursor = old_args['cursor'], new_args['cursor']
                if (old_binding._PrivateBinding__bundle != binding._PrivateBinding__bundle
                        or old_binding._PrivateBinding__manifest != binding._PrivateBinding__manifest
                        or old_binding._PrivateBinding__controls != binding._PrivateBinding__controls
                        or old_binding._PrivateBinding__policy_pin != binding._PrivateBinding__policy_pin
                        or prior_memory.encoder_binding != encoder_binding_bytes
                        or not startup._same_path(Path(old_args['source_path']), Path(new_args['source_path']))
                        or not startup._same_path(Path(old_args['generation_path']), Path(new_args['generation_path']))
                        or old_cursor.file_identity != new_cursor.file_identity
                        or old_cursor.generation != new_cursor.generation
                        or new_cursor.offset < old_cursor.offset):
                    raise ValueError('refresh_basis_changed')
                old_records = _flat_check(prior_memory)
                for index, record_id in enumerate(prior_memory.ids):
                    if record_id in by_id and old_records[record_id] == by_id[record_id]:
                        reusable[record_id] = prior_memory.vectors[index * 1536:(index + 1) * 1536]

            def checkpoint():
                watcher.check()
                if snapshot != frozen or binding._snapshot_support() is not package:
                    raise ValueError('snapshot_changed')
                binding._immutable_checkpoint(frozen)
                _checked_encoder_binding(encoder_binding_bytes, encoder_binding_sha256)

            missing = tuple(record_id for record_id in ids if record_id not in reusable)
            for start in range(0, len(missing), 16):
                chosen = missing[start:start + 16]
                texts = tuple('passage: ' + reader.decode_text(by_id[key]['stored_text'])[:2000] for key in chosen)
                checkpoint()
                raw = passage_encoder(texts)
                checkpoint()
                if type(raw) is not bytes or len(raw) != len(chosen) * 1536:
                    raise ValueError('passage')
                for index in range(len(chosen)):
                    reader._q4_vector(struct.unpack('<384f', raw[index * 1536:(index + 1) * 1536]))
                for index, record_id in enumerate(chosen):
                    reusable[record_id] = raw[index * 1536:(index + 1) * 1536]
            vectors = b''.join(reusable[record_id] for record_id in ids)
            if prior_memory is not None:
                _flat_check(prior_memory)
            manifest = {'schema': 'wd.mind-flat-f32-memory.v1', 'authority': 'none',
                        'projection_sha256': snapshot['projection_sha256'], 'records': len(ids),
                        'encoder_binding_sha256': encoder_binding_sha256, **_flat_hashes(ids, vectors)}
            checkpoint()
            watcher.stop(watcher_join_timeout_seconds)
            package._unchanged(snapshot, frozen, report, complete=True)
            _flat_snapshot(binding, frozen)
            if (reader._sha(immutable_prefix) != report['source_prefix']['sha256']
                    or reader._serialized(projection) != frozen['projection_bytes']):
                raise ValueError('immutable_snapshot_changed')
            output = _PrivateFlatMemory(binding, frozen, reader._canonical(manifest), ids, vectors,
                                        encoder_binding_bytes, cache_status)
            if vector_cache is not None and prior_memory is None and cache_binding is not None:
                cache_rows = _cache_rows_from_screened_projection(projection, ids, vectors)
                vector_cache.write(cache_binding, cache_rows)
                output.cache_status = cache_status + '_STORED'
                package._unchanged(snapshot, frozen, report, complete=True)
                _flat_snapshot(binding, frozen)
    except BaseException as exc:
        problem = exc
    finally:
        if watcher is None and watcher_lease is not None:
            # A cancellation can arrive after reservation but before the
            # returned watcher is stored in this frame.
            watcher = watcher_lease._current()
        if watcher is not None:
            try:
                watcher.stop(watcher_join_timeout_seconds)
            except package.SnapshotPackageHold:
                held = True
            except BaseException as exc:
                cleanup_problem = exc
            finally:
                package._release_watcher(watcher)
    signals = [_flat_cancellation(exc) for exc in (problem, cleanup_problem) if exc is not None]
    signals = [signal for signal in signals if signal is not None]
    if signals:
        problem = cleanup_problem = None
        if len(signals) == 1:
            raise signals[0] from None
        raise BaseExceptionGroup('provider_cancelled', signals) from None
    if held:
        raise FullRuntimeSnapshotHold('mind_full_runtime_snapshot_hold') from None
    if over_capacity and cleanup_problem is None and type(problem) is _CacheCapacityRefused:
        problem = None
        raise _CacheCapacityRefused('CACHE_CAPACITY') from None
    if cleanup_problem is not None or problem is not None:
        raise ValueError('snapshot_build_refused') from None
    return output


def build_flat_memory(*, enabled=False, binding=None, passage_encoder=None,
                      encoder_binding_bytes=None, encoder_binding_sha256=None,
                      source_verification=None, watcher_join_timeout_seconds=None, _watcher_lease=None,
                      _prior_memory=None, _vector_cache=None):
    """Build one memory-only float32 vector per screened, source-bound record.

    Provider identity and supplied encoder metadata are caller-trusted; this
    format makes no FAISS, model-authentication or cross-process claim.
    """
    immutable_build = False
    result = None
    try:
        if (enabled is not True or type(binding) is not _PrivateBinding
                or not callable(passage_encoder) or isinstance(passage_encoder, type)
                or (_watcher_lease is not None and type(_watcher_lease) is not _OwnedSnapshotWatcher)):
            raise ValueError('input')
        _checked_encoder_binding(encoder_binding_bytes, encoder_binding_sha256)
        if source_verification is not None:
            if type(source_verification) is not str or source_verification != 'verified_immutable_snapshot_v1':
                raise ValueError('source_verification')
            immutable_build = True
            return _build_flat_memory_immutable(binding, passage_encoder, encoder_binding_bytes,
                                                encoder_binding_sha256, watcher_join_timeout_seconds, _watcher_lease,
                                                _prior_memory, _vector_cache)
        if _prior_memory is not None:
            raise ValueError('refresh_requires_immutable_snapshot')
        if _vector_cache is not None:
            raise ValueError('cache_requires_immutable_snapshot')
        if watcher_join_timeout_seconds is not None:
            raise ValueError('unexpected_join_timeout')
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
    except FullRuntimeSnapshotHold:
        raise
    except _CacheCapacityRefused as exc:
        if immutable_build and type(exc) is _CacheCapacityRefused:
            raise
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
                      query=None, as_of_utc=None, query_encoder=None, max_age_seconds=899,
                      include_historical=False):
    """Return source-referenced candidates; never infer a lane's latest task."""
    result = None
    try:
        if (enabled is not True or type(memory) is not _PrivateFlatMemory
                or type(lane) is not str or lane not in reader._LANES
                or type(own_lane_only) is not bool or type(query) is not str or not query.strip()
                or len(query) > 4000 or not callable(query_encoder) or isinstance(query_encoder, type)
                or type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 899
                or type(include_historical) is not bool):
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
            if (age < 0 or (not include_historical and age > max_age_seconds)
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
            selected = _order_selected(selected, query)
        selected = _first_three(selected, records, query)
        requested = [{'ref_sha256': reader._sha(records[key]['provenance']['event_ref'].encode()),
                      'doc_id_sha256': reader._sha(key.encode()), 'ref_kind': 'sha256_of_projected_metadata',
                      'canonical_event': False} for key, _, _, _ in selected]
        resolved = reader.resolve_content_results(requested, as_of_utc, _complete=True, **memory.snapshot)
        lookup = {reader._sha(key.encode()): records[key] for key, _, _, _ in selected}
        for hit in resolved:
            record = lookup[hit['doc_id_sha256']]
            hit['source_agent'] = record['meta']['agent']
            hit['source_time_utc'] = record['time_utc']
            if include_historical:
                hit['event_age_seconds_upper_bound'] = math.ceil((when - reader._utc(record['time_utc'])).total_seconds())
                hit['source_valid_until_utc'] = record['provenance'].get('valid_until_utc')
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
            selected = _order_selected(selected, query)
        selected = _first_three(selected, records, query)
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


# Source size and total I/O are separate limits. Each operation still reserves
# every planned full-prefix pass and selected-row read before touching source.
_STARTUP_MAX_SOURCE_PREFIX_BYTES = reader._COMPLETE_SOURCE_PREFIX_BYTES
_STARTUP_OWNER_SOURCE_READ_LIMIT = (
    3 * _STARTUP_MAX_SOURCE_PREFIX_BYTES + 6 * (4 * 1024 * 1024 + 1) + 3
)
_STARTUP_PREWARM_SOURCE_READ_LIMIT = (
    7 * _STARTUP_MAX_SOURCE_PREFIX_BYTES + 4 * 1024 * 1024 + 1
)
_STARTUP_OWNER_SOURCE_BUDGET_ENABLED = True


class _StartupSourceBudgetRefused(ValueError):
    pass


def _startup_owner_source_budget(snapshot):
    """Preflight known worst-case reads without touching the shared owner."""
    cursor = snapshot['messages']['end_cursor']
    offset = cursor.offset
    # Three prefix hashes, three maximum-sized selected rows with their
    # boundary bytes, and three rotation source-check bytes.
    if type(offset) is not int or offset <= 0:
        raise ValueError('source_budget')
    if offset > _STARTUP_MAX_SOURCE_PREFIX_BYTES:
        raise _StartupSourceBudgetRefused('source_budget')
    if 3 * offset + 3 * (4 * 1024 * 1024 + 1) + 3 > _STARTUP_OWNER_SOURCE_READ_LIMIT:
        raise _StartupSourceBudgetRefused('source_budget')
    return prefix._SourceReadBudget(_STARTUP_OWNER_SOURCE_READ_LIMIT)


def _prewarm_source_fits(snapshot, *, extra_prefix_passes=0):
    """Preflight all planned prefix passes before passage encoding starts."""
    offset = snapshot['messages']['end_cursor'].offset
    if (type(offset) is not int or offset <= 0 or type(extra_prefix_passes) is not int
            or extra_prefix_passes not in (0, 2)):
        raise ValueError('source_budget')
    if offset > _STARTUP_MAX_SOURCE_PREFIX_BYTES:
        raise _StartupSourceBudgetRefused('source_budget')
    if (5 + extra_prefix_passes) * offset + 4 * 1024 * 1024 + 1 > _STARTUP_PREWARM_SOURCE_READ_LIMIT:
        raise _StartupSourceBudgetRefused('source_budget')


def _prewarm_source_budget(snapshot, *, extra_prefix_passes=0):
    """Reserve readline headroom, prewarm and optional cache-check passes."""
    _prewarm_source_fits(snapshot, extra_prefix_passes=extra_prefix_passes)
    return prefix._SourceReadBudget(_STARTUP_PREWARM_SOURCE_READ_LIMIT)


def _local_query_source_budget(snapshot):
    """Preflight two selected-row resolutions plus query and rotation reads."""
    offset = snapshot['messages']['end_cursor'].offset
    if type(offset) is not int or offset <= 0:
        raise ValueError('source_budget')
    if offset > _STARTUP_MAX_SOURCE_PREFIX_BYTES:
        raise _StartupSourceBudgetRefused('source_budget')
    if 3 * offset + 6 * (4 * 1024 * 1024 + 1) + 3 > _STARTUP_OWNER_SOURCE_READ_LIMIT:
        raise _StartupSourceBudgetRefused('source_budget')
    return prefix._SourceReadBudget(_STARTUP_OWNER_SOURCE_READ_LIMIT)


class _RetainedFlatQueryState:
    """One owner's verified flat rows and selected-row gate, retained in memory."""
    __slots__ = ('_owner', '_generation', '_handler', '_memory', '_flat', '_snapshot',
                 '_source_owner', '_records', '_policy_pin', '_binding_pin', '_pid', '_package')

    def __init__(self, owner, *, source_budget=None):
        generation = owner._generation
        binding, snapshot, handler = generation
        memory = handler._memory
        if (type(binding) is not _PrivateBinding or type(handler) is not _FlatRequestHandler
                or type(memory) is not _PrivateFlatMemory or memory.binding is not binding
                or memory.snapshot != snapshot or handler._binding is not binding
                or handler._snapshot != snapshot):
            raise ValueError('retained_binding')
        owner._check_factory()
        owner._check_policy_anchor()
        package = binding._snapshot_support()
        policy_pin = _local_generation_policy_pin(owner)
        source_args = dict(enabled=True, bundle_root=str(binding._PrivateBinding__bundle),
            expected_manifest_sha256=binding._PrivateBinding__manifest, snapshot=snapshot,
            flat_manifest_bytes=memory.manifest, flat_manifest_sha256=reader._sha(memory.manifest),
            ids=memory.ids, vectors=memory.vectors, encoder_binding_bytes=memory.encoder_binding)
        if source_budget is not None:
            source_args['source_budget'] = source_budget
        source_owner = package.create_consumer_flat_source_owner(**source_args)
        projection, _, _ = source_owner._gate.verify_snapshot(source_owner._snapshot)
        records = {row['record_id']:row for row in projection['messages']['records']}
        if tuple(sorted(records)) != memory.ids or len(records) != len(projection['messages']['records']):
            raise ValueError('retained_records')
        self._owner, self._generation, self._handler, self._memory = owner, generation, handler, memory
        self._flat = (memory.manifest, memory.ids, memory.vectors, memory.encoder_binding)
        self._snapshot = dict(source_owner._snapshot, messages=dict(source_owner._snapshot['messages']))
        self._source_owner, self._records = source_owner, reader._freeze_json(records)
        self._policy_pin = policy_pin
        self._binding_pin = (binding._PrivateBinding__bundle, binding._PrivateBinding__manifest,
                             binding._PrivateBinding__controls)
        self._pid, self._package = os.getpid(), package
        self._controls()

    def _controls(self):
        owner, memory = self._owner, self._memory
        if (os.getpid() != self._pid or owner._state not in ('READY', 'STARTING')
                or owner._generation is not self._generation or owner._handler is not self._handler
                or not _refresh_handler_matches_owner(owner, self._handler)
                or self._handler._memory is not memory or memory.binding is not owner._binding
                or (owner._binding._PrivateBinding__bundle, owner._binding._PrivateBinding__manifest,
                    owner._binding._PrivateBinding__controls) != self._binding_pin
                or memory.snapshot != self._snapshot or owner._snapshot != self._snapshot
                or (memory.manifest, memory.ids, memory.vectors, memory.encoder_binding) != self._flat
                or self._handler._frozen_flat != self._flat
                or not hmac.compare_digest(_local_generation_policy_pin(owner), self._policy_pin)):
            raise ValueError('retained_changed')
        owner._check_factory()
        owner._check_policy_anchor()
        self._handler._running_owner()
        package = owner._binding._snapshot_support()
        if package is not self._package:
            raise ValueError('retained_package_changed')
        consumer = self._source_owner
        if (os.path.normcase(os.path.abspath(consumer._root)) != os.path.normcase(os.path.abspath(str(self._binding_pin[0])))
                or consumer._pin != self._binding_pin[1]
                or consumer._gate._manifest_pin != self._binding_pin[1]
                or package._consumer_bundle_controls(consumer._root, consumer._pin) != consumer._controls
                or consumer._snapshot != self._snapshot):
            raise ValueError('retained_controls')
        consumer._gate._check_delivery_binding()
        _, context, _ = consumer._gate.verify_snapshot(self._snapshot)
        return context

    def _prefix(self, context, *, source_budget=None):
        source = Path(context['messages']['source_path'])
        sidecar = Path(context['messages']['generation_path'])
        cursor = context['messages']['end_cursor']
        native_bridge, absent = reader._narrow_dependencies()
        with native_bridge._open_log(source) as stream:
            before = os.fstat(stream.fileno())
            identity = native_bridge._file_identity(stream)
            reader._narrow_fence(stream, source, sidecar, cursor, native_bridge, absent)
            digest = hashlib.sha256()
            remaining = cursor.offset
            while remaining:
                block = absent._budgeted_read(stream, min(1024 * 1024, remaining), source_budget)
                if not block:
                    raise ValueError('short_prefix')
                digest.update(block)
                remaining -= len(block)
            if not hmac.compare_digest(digest.hexdigest(), context['decision']['source_prefix']['sha256']):
                raise ValueError('prefix_changed')
            reader._narrow_fence(stream, source, sidecar, cursor, native_bridge, absent)
            after = os.fstat(stream.fileno())
            if after.st_size < before.st_size or (after.st_size == before.st_size and after.st_mtime_ns != before.st_mtime_ns):
                raise ValueError('source_changed_during_hash')
            return identity, after.st_size, after.st_mtime_ns

    def query(self, *, lane, scope, query):
        if self._handler._retained is not self:
            raise ValueError('retained_owner_changed')
        context = self._controls()
        source_budget = getattr(self._owner, '_source_budget', None)
        before = (self._prefix(context) if source_budget is None else
                  self._prefix(context, source_budget=source_budget))
        when = reader.dt.datetime.now(reader.dt.timezone.utc)
        memory, records = self._memory, self._records
        eligible = []
        for index, record_id in enumerate(memory.ids):
            record = records[record_id]
            if scope == 'own' and record['meta']['agent'] != lane:
                continue
            stamp, provenance = reader._utc(record['time_utc']), record['provenance']
            if ((when - stamp).total_seconds() < 0
                    or reader._utc(provenance['available_utc']) >= when - reader.dt.timedelta(seconds=1)
                    or ('valid_until_utc' in provenance and when >= reader._utc(provenance['valid_until_utc']))
                    or reader._q4_self_match(query, reader.decode_text(record['stored_text']))):
                continue
            eligible.append((index, record_id))
        selected = []
        if eligible:
            vector = reader._provider_vector(self._handler._adapter.query(query))
            self._controls()
            vector = reader._q4_vector(struct.unpack('<384f', struct.pack('<384f', *vector)))
            tokens = set(reader._Q4_TOKEN.findall(query.lower()))
            for index, record_id in eligible:
                values = struct.unpack('<384f', memory.vectors[index * 1536:(index + 1) * 1536])
                score = sum(a * b for a, b in zip(values, vector))
                if math.isfinite(score) and score >= 0.55:
                    record = records[record_id]
                    lexical = len(tokens & set(reader._Q4_TOKEN.findall(reader.decode_text(record['stored_text']).lower())))
                    selected.append((record_id, score, lexical, reader._utc(record['time_utc'])))
            selected = _order_selected(selected, query)
        selected = _first_three(selected, records, query)
        proposals = [{'ref_sha256':reader._sha(records[key]['provenance']['event_ref'].encode()),
                      'doc_id_sha256':reader._sha(key.encode()),
                      'source_event_sha256':records[key]['provenance']['source_event_ref']}
                     for key, _, _, _ in selected]
        if proposals:
            resolve_args = dict(proposals=proposals, lane=lane, scope=scope)
            if source_budget is not None:
                resolve_args['source_budget'] = source_budget
            resolved = self._source_owner.resolve(**resolve_args)['results']
        else:
            resolved = []
        if len(resolved) != len(selected):
            raise ValueError('retained_resolution')
        hits = []
        manifest = self._source_owner._gate._manifest()
        for proposal, item, (key, _, _, _) in zip(proposals, resolved, selected):
            record = records[key]
            entry = manifest['entries'][proposal['source_event_sha256']]
            hits.append({'ref_sha256':proposal['ref_sha256'], 'doc_id_sha256':proposal['doc_id_sha256'],
                'ref_kind':'sha256_of_projected_metadata', 'canonical_event':False,
                'stored_text':item['text'], 'source_reference':{'sha256':item['source_event_sha256'],
                    'kind':'sha256_of_canonical_event_json', 'verified':'MATCH_SELECTED_ROW',
                    'sender_authentication':'UNKNOWN'}, 'source_agent':item['source_agent'],
                'source_time_utc':item['event_time_utc_floor'],
                'event_age_seconds_upper_bound':item['event_age_seconds_upper_bound'],
                'source_valid_until_utc':record['provenance'].get('valid_until_utc'),
                'source_offset':entry['offset'], 'source_length':entry['length'],
                'chunk_index':record['provenance']['chunk_index']})
        context = self._controls()
        after = (self._prefix(context) if source_budget is None else
                 self._prefix(context, source_budget=source_budget))
        if (before[0] != after[0] or after[1] < before[1]
                or (after[1] == before[1] and after[2] != before[2])):
            raise ValueError('source_changed_during_query')
        return {'schema':'wd.mind-flat-f32-query.v1', 'status':'SEMANTIC_CANDIDATE' if hits else 'ABSTAIN',
                'relevance':'UNVERIFIED', 'authority':'none', 'index_match':'NOT_APPLICABLE',
                'own_latest_task':'UNKNOWN', 'results':hits, 'provider_authentication':'UNKNOWN',
                'source_authentication':'UNKNOWN', 'continuity':'UNKNOWN', 'same_user_aba':'UNKNOWN'}


class _FlatRequestHandler:
    """In-process owner; caller retains this object through any HOLD."""
    __slots__ = ('_binding', '_snapshot', '_lane', '_lanes', '_rotator', '_adapter', '_memory',
                 '_frozen_flat', '_closed', '_failed', '_lock', '_factory', '_timeout', '_bulk', '_pending_rotator',
                 '_refresh_controller', '_watcher_lease', '_retained', '_vector_cache')

    def __init__(self, binding, snapshot, lane, lanes, rotator, adapter, factory, timeout, bulk, watcher_lease=None,
                 vector_cache=None):
        self._binding, self._snapshot, self._lane = binding, snapshot, lane
        self._lanes = frozenset(lanes)
        self._rotator, self._adapter = rotator, adapter
        self._memory = self._frozen_flat = None
        self._factory, self._timeout, self._bulk, self._pending_rotator = factory, timeout, bulk, None
        self._refresh_controller = None
        self._watcher_lease = watcher_lease
        self._vector_cache = vector_cache
        self._retained = None
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
        return self._build(None)

    def _build(self, prior_memory):
        if not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        building_memory = False
        try:
            if self._closed or self._failed or self._memory is not None:
                raise ValueError('state')
            passage = self._adapter.passage
            encoder_bytes = self._adapter.encoder_binding_bytes
            encoder_pin = self._adapter.encoder_binding_sha256
            building_memory = True
            memory = build_flat_memory(enabled=True, binding=self._binding, passage_encoder=passage,
                encoder_binding_bytes=encoder_bytes, encoder_binding_sha256=encoder_pin,
                source_verification='verified_immutable_snapshot_v1', _watcher_lease=self._watcher_lease,
                _prior_memory=prior_memory, _vector_cache=self._vector_cache)
            building_memory = False
            self._memory = memory
            self._frozen_flat = (memory.manifest, memory.ids, memory.vectors, memory.encoder_binding)
            self._source_check()
            if self._vector_cache is not None and self._rotator.owner is None:
                # An all-cache hit sends no passage request, so start and
                # validate the query worker without re-embedding old rows.
                reader._q4_vector(self._adapter.query('Mind startup readiness'))
            self._running_owner()
            return {'schema': 'wd.mind-flat-owner-build.v1', 'status': 'BUILT_IN_MEMORY', 'authority': 'none'}
        except Exception as exc:
            self._failed = True
            if building_memory and type(exc) is _CacheCapacityRefused:
                raise
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
            if self._retained is None:
                self._source_check()
                now = reader.dt.datetime.now(reader.dt.timezone.utc)
                result = query_flat_memory(enabled=True, memory=self._memory, lane=bound_lane,
                    own_lane_only=frozen['scope'] == 'own',
                    query=frozen['query'], as_of_utc=reader._utc_text(now), query_encoder=self._adapter.query,
                    include_historical=True)
                self._source_check()
            else:
                if type(self._retained) is not _RetainedFlatQueryState or self._retained._handler is not self:
                    raise ValueError('retained_binding')
                result = self._retained.query(lane=bound_lane, scope=frozen['scope'], query=frozen['query'])
            self._running_owner()
            if request != frozen:
                raise ValueError('request_changed')
            expires = None
            if result['results']:
                source_expiries = [reader._utc(hit['source_valid_until_utc'])
                                   for hit in result['results'] if hit['source_valid_until_utc'] is not None]
                expires = min(source_expiries) if source_expiries else None
                if expires is not None and reader.dt.datetime.now(reader.dt.timezone.utc) >= expires:
                    result = dict(result, status='ABSTAIN', results=[])
                    expires = None
            response = {'schema': 'wd.mind-flat-owner-response.v1', 'lane': bound_lane, 'scope': frozen['scope'],
                        'authority': 'none', 'expires_utc': None if expires is None else reader._utc_text(expires),
                        'expiry_scope': 'source_validity_only', 'snapshot_freshness': 'UNKNOWN', 'result': result}
            if self._closed or self._failed:
                response = None
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
    __slots__ = ('__metadata', '__blocks', '__total', '__active', '__owner', '__memory')

    def __init__(self, metadata, blocks, total, owner, memory):
        self.__metadata, self.__blocks, self.__total = metadata, blocks, total
        self.__owner, self.__memory = owner, memory
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

    def _send_parent_stream(self, *, enabled=False, stream=None, bootstrap=None,
                            max_wire_bytes=None, deadline_monotonic=None):
        """Guarded sender; the production parent authorization currently refuses.

        The caller must run this synchronous operation under retained ownership:
        a deadline check cannot interrupt a blocked write, close, or source gate.
        """
        result = cancelled_type = cancellation_group = None
        try:
            if (enabled is not True or type(stream) is not _PrivateExportStream
                    or type(bootstrap) is not _ParentBootstrap):
                raise ValueError('input')
            _require_trusted_parent_export(owner=self.__owner, stream=stream, bootstrap=bootstrap)
            result = self._send_parent_stream_inner(stream, bootstrap.take(), max_wire_bytes,
                                                    deadline_monotonic)
        except KeyboardInterrupt:
            cancelled_type, result = KeyboardInterrupt, None
        except GeneratorExit:
            cancelled_type, result = GeneratorExit, None
        except asyncio.CancelledError:
            cancelled_type, result = asyncio.CancelledError, None
        except BaseExceptionGroup as group:
            cancellation_group, result = _fixed_group_cancellation(group), None
        except BaseException:
            result = None
        finally:
            stream = bootstrap = None
        if cancelled_type is not None:
            raise cancelled_type() from None
        if cancellation_group is not None:
            raise cancellation_group from None
        if result is None:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return result

    def _send_parent_stream_inner(self, stream, credentials, max_wire_bytes, deadline):
        from tools import wd_mind_runtime_child as child
        if (type(credentials) is not tuple or len(credentials) != 5
                or type(deadline) not in (int, float) or not math.isfinite(deadline)
                or type(max_wire_bytes) is not int or not self.__active):
            raise ValueError('input')
        manifest, nonce, channel_key, policy_sha, policy = credentials
        owner, memory = self.__owner, self.__memory
        if type(owner) is not _SingleManifestOwner:
            raise ValueError('owner')
        if owner._state != 'READY':
            raise ValueError('owner_not_ready')
        # Conservatively fence the owner before acquiring. If interruption
        # lands after the native acquire but before Python stores its return,
        # the lock may stay held, but the owner cannot report READY.
        owner._state = 'HOLD'
        succeeded = acquired = False
        try:
            acquired = owner._lock.acquire(blocking=False)
            if not acquired:
                raise ValueError('owner_busy')
            if (owner._state != 'HOLD' or owner._handler is None or owner._handler._memory is not memory
                    or type(nonce) is not bytes or len(nonce) != 32
                    or type(channel_key) is not bytes or len(channel_key) != 32
                    or type(policy) is not bytes or reader._sha(policy) != policy_sha
                    or owner._binding._PrivateBinding__manifest != manifest
                    or owner._binding.snapshot_for_query()['identifier_policy_binding'][0] != policy
                    or json.loads(self.__metadata.decode('utf-8'))['nonce'] != nonce.hex()):
                raise ValueError('binding')
            def check_source():
                if owner._handler._memory is not memory or memory.binding is not owner._binding:
                    raise ValueError('memory')
                owner._check_factory()
                owner._handler._running_owner()
                _flat_check(memory)
                if owner._binding.snapshot_for_query() != memory.snapshot:
                    raise ValueError('snapshot')
            check_source()
            child._export_wire_size(enabled=True, metadata_length=len(self.__metadata),
                block_lengths=tuple(len(value) for _, value in self.__blocks), max_wire_bytes=max_wire_bytes)
            digest = hashlib.sha256()
            sequence = data_count = total = 0
            def send(kind, payload):
                nonlocal sequence
                if time.monotonic() >= deadline:
                    raise ValueError('deadline')
                frame = child._encode_export_frame(enabled=True, channel_key=channel_key,
                    nonce=nonce, kind=kind, sequence=sequence, payload=payload)
                offset = 0
                while offset < len(frame):
                    if time.monotonic() >= deadline:
                        raise ValueError('deadline')
                    count = stream.write(memoryview(frame)[offset:])
                    if type(count) is not int or not 0 < count <= len(frame) - offset:
                        raise ValueError('write')
                    offset += count
                if time.monotonic() >= deadline:
                    raise ValueError('deadline')
                sequence += 1
            digest.update(self.__metadata)
            total += len(self.__metadata)
            send(b'M', self.__metadata)
            for _, block in self.__blocks:
                digest.update(block)
                total += len(block)
                for offset in range(0, len(block), 65488):
                    send(b'D', block[offset:offset + 65488])
                    data_count += 1
            if total != self.__total:
                raise ValueError('total')
            check_source()
            send(b'F', struct.pack('>IQ32s', data_count, total, digest.digest()))
            stream.close()
            if time.monotonic() >= deadline:
                raise ValueError('deadline')
            succeeded = True
            owner._state = 'READY'
            return {'status':'SENT', 'authority':'none', 'receiver_authentication':'UNKNOWN'}
        finally:
            if acquired:
                self.discard()
                if not succeeded:
                    owner._state = 'HOLD'
                try:
                    if not succeeded:
                        try:
                            stream.close()
                        except (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError):
                            raise
                        except Exception:
                            pass  # HOLD remains; closure is unproven.
                finally:
                    owner._lock.release()


class _PrivateExportStream:
    """Opaque retained write endpoint; provenance is checked separately."""
    __slots__ = ('__stream',)

    def __init__(self, stream):
        self.__stream = stream

    def __repr__(self):
        return '<PrivateMindExportStream>'

    def write(self, data):
        return self.__stream.write(data)

    def close(self):
        return self.__stream.close()


def _require_trusted_parent_export(*, owner, stream, bootstrap):
    # There is no proved production parent/control/approval provider yet.
    raise FullRuntimeRefused('mind_full_runtime_refused') from None


def _refresh_handler_matches_owner(owner, handler):
    return (type(handler) is _FlatRequestHandler and handler._lane == owner._lane
            and handler._lanes == owner._lanes and handler._rotator is owner._client
            and handler._adapter is owner._adapter and handler._factory is owner._factory
            and handler._timeout == owner._timeout and handler._bulk == owner._factory._bulk
            and handler._watcher_lease is owner._watcher_lease
            and handler._closed is False and handler._failed is False)


def _verify_refresh_basis(owner):
    """Recheck the original caller-supplied policy pin and current generation."""
    binding, snapshot, handler = owner._generation
    if type(binding) is not _PrivateBinding or not _refresh_handler_matches_owner(owner, handler):
        raise ValueError('refresh_binding')
    pin = binding._PrivateBinding__policy_pin
    current = binding.snapshot_for_query()
    if (current != snapshot or handler._binding is not binding or handler._snapshot != snapshot
            or type(handler._memory) is not _PrivateFlatMemory or handler._memory.binding is not binding
            or handler._memory.snapshot != snapshot or not reader._hex(pin)
            or handler._frozen_flat != (handler._memory.manifest, handler._memory.ids,
                                        handler._memory.vectors, handler._memory.encoder_binding)):
        raise ValueError('refresh_binding')
    policy, key, tag = snapshot['identifier_policy_binding']
    if type(policy) is not bytes or not hmac.compare_digest(reader._sha(policy), pin):
        raise ValueError('refresh_policy_pin')
    reader._load_identifier_policy(policy, key, tag, allow_empty_v2=True)
    _flat_check(handler._memory)
    owner._check_factory()
    handler._running_owner()
    return str(binding._PrivateBinding__bundle), binding._PrivateBinding__manifest, policy, pin


_REFRESH_SOURCE_BUDGET = object()
_REFRESH_CACHE_CAPACITY = object()


def _stage_refresh_generation(owner):
    bundle, manifest, policy, policy_pin = _verify_refresh_basis(owner)
    try:
        binding = assemble_full_runtime(enabled=True, bundle_root=bundle, expected_manifest_sha256=manifest,
            identifier_policy_bytes=policy, expected_identifier_policy_sha256=policy_pin)
    except _EagerSourceBudgetRefused as exc:
        if type(exc) is _EagerSourceBudgetRefused:
            return _REFRESH_SOURCE_BUDGET
        raise
    snapshot = binding.snapshot_for_query()
    # A refreshed-cache commit needs two additional budgeted prefix checks.
    # Refuse before _build starts passage encoding if they cannot fit.
    if owner._vector_cache_enabled and _STARTUP_OWNER_SOURCE_BUDGET_ENABLED:
        _prewarm_source_fits(snapshot, extra_prefix_passes=2)
    handler = _FlatRequestHandler(binding, snapshot, owner._lane, owner._lanes, owner._client,
        owner._adapter, owner._factory, owner._timeout, owner._factory._bulk, owner._watcher_lease,
        owner._handler._vector_cache)
    try:
        handler._build(owner._handler._memory)
    except _CacheCapacityRefused as exc:
        if type(exc) is _CacheCapacityRefused:
            return _REFRESH_CACHE_CAPACITY
        raise
    return {'binding':binding, 'snapshot':snapshot, 'memory':handler._memory, 'handler':handler}


def _validate_refresh_generation(owner, staged):
    if (type(staged) is not dict or set(staged) != {'binding', 'snapshot', 'memory', 'handler'}
            or type(staged['binding']) is not _PrivateBinding
            or type(staged['handler']) is not _FlatRequestHandler
            or type(staged['memory']) is not _PrivateFlatMemory):
        raise ValueError('refresh_candidate')
    binding, snapshot, memory, handler = (staged[k] for k in ('binding', 'snapshot', 'memory', 'handler'))
    old = owner._binding
    if (binding._PrivateBinding__bundle != old._PrivateBinding__bundle
            or binding._PrivateBinding__manifest != old._PrivateBinding__manifest
            or binding._PrivateBinding__controls != old._PrivateBinding__controls
            or binding._PrivateBinding__policy_pin != old._PrivateBinding__policy_pin
            or not _refresh_handler_matches_owner(owner, handler)
            or binding.snapshot_for_query() != snapshot or handler._binding is not binding
            or handler._snapshot != snapshot or handler._memory is not memory
            or memory.binding is not binding or memory.snapshot != snapshot
            or handler._frozen_flat != (memory.manifest, memory.ids, memory.vectors, memory.encoder_binding)):
        raise ValueError('refresh_candidate')
    _flat_check(memory)
    handler._running_owner()
    owner._check_factory()
    _verify_refresh_basis(owner)
    _flat_check(memory)
    if binding.snapshot_for_query() != snapshot:
        raise ValueError('refresh_candidate_changed')
    return binding, snapshot, handler


def _write_refresh_vector_cache(owner, old_handler, *, source_budget=None):
    """Commit only vectors and pins while the refreshed generation is private."""
    generation = owner._generation
    binding, snapshot, handler = generation
    cache = old_handler._vector_cache
    memory = handler._memory
    if (type(cache) is not _PrivateVectorCache or handler._vector_cache is not cache
            or type(memory) is not _PrivateFlatMemory or memory.binding is not binding
            or memory.snapshot != snapshot):
        raise ValueError('refresh_cache_binding')

    def verify_source():
        if owner._generation is not generation:
            raise ValueError('refresh_generation_changed')
        owner._check_factory()
        if owner._policy_pins is not None:
            owner._check_policy_anchor()
        if source_budget is None:
            _flat_check(memory)
        else:
            retained = owner._retained
            if type(retained) is not _RetainedFlatQueryState:
                raise ValueError('refresh_retained_missing')
            retained._prefix(retained._controls(), source_budget=source_budget)

    verify_source()
    raw = snapshot['projection_bytes']
    if (type(raw) is not bytes or not reader._hex(snapshot['projection_sha256'])
            or not hmac.compare_digest(reader._sha(raw), snapshot['projection_sha256'])):
        raise ValueError('refresh_projection_pin')
    projection = json.loads(raw.decode('utf-8'), object_pairs_hook=reader._unique_pairs,
                            parse_constant=reader._bad_constant)
    if reader._serialized(projection) != raw:
        raise ValueError('refresh_projection_changed')
    encoder_sha = reader._sha(memory.encoder_binding)
    cache_binding = _cache_snapshot_binding(binding, snapshot, projection, memory.encoder_binding, encoder_sha)
    rows = _cache_rows_from_screened_projection(projection, memory.ids, memory.vectors)
    cache.write(cache_binding, rows)
    verify_source()
    memory.cache_status = 'REFRESH_STORED'


def _local_generation_policy_pin(owner):
    """Check the retained policy bytes and independent pin without reading the log."""
    original = current = policy = key = tag = None
    try:
        binding = owner._binding
        if type(binding) is not _PrivateBinding or type(owner._snapshot) is not dict:
            raise ValueError('policy_binding')
        original = binding._PrivateBinding__snapshot['identifier_policy_binding']
        current = owner._snapshot['identifier_policy_binding']
        pin = binding._PrivateBinding__policy_pin
        if (type(original) is not tuple or len(original) != 3 or type(current) is not tuple
                or len(current) != 3 or original != current or not reader._hex(pin)):
            raise ValueError('policy_binding')
        policy, key, tag = original
        if type(policy) is not bytes or not hmac.compare_digest(reader._sha(policy), pin):
            raise ValueError('policy_binding')
        reader._load_identifier_policy(policy, key, tag, allow_empty_v2=True)
        return pin
    finally:
        original = current = policy = key = tag = None


def _prepare_generation_delivery(owner, request, expected_nonce, policy_pin, max_total_bytes):
    """Prepare reference candidates and an untransmitted export under owner lock."""
    if (type(request) is not dict
            or set(request) != {'schema', 'query_id', 'nonce', 'lane', 'scope'}
            or any(type(request[key]) is not str for key in request)
            or request['schema'] != 'wd.mind-pipe-request.v1'
            or request['query_id'] != 'startup_work_overview'
            or request['lane'] not in owner._lanes or request['lane'] not in reader._LANES
            or request['scope'] not in ('own', 'shared')
            or type(expected_nonce) is not str or not reader._hex(expected_nonce)
            or request['nonce'] != expected_nonce or type(policy_pin) is not str
            or not reader._hex(policy_pin)
            or type(max_total_bytes) is not int or not 0 < max_total_bytes <= 64 * 1024 * 1024):
        raise _GenerationInputRefused('mind_full_runtime_refused') from None
    if not hmac.compare_digest(policy_pin, _local_generation_policy_pin(owner)):
        raise _GenerationInputRefused('mind_full_runtime_refused') from None
    owner._check_factory_or_hold()
    owner._check_policy_anchor()
    owner._ensure_retained_locked()
    candidates = map_pipe_candidates(enabled=True, handler=owner._handler, request=request)
    if len(reader._canonical(candidates)) > 2048:
        raise ValueError('candidate_size')
    prepared = owner._prepare_consumer_export_locked(enabled=True, expected_nonce=expected_nonce,
        expected_identifier_policy_sha256=policy_pin, max_total_bytes=max_total_bytes)
    owner._check_factory_or_hold()
    owner._check_policy_anchor()
    return {'schema':'wd.mind-generation-delivery.v1', 'authority':'none',
            'generation_sha256':owner._snapshot['projection_sha256'],
            'candidates':candidates, 'export':prepared}


_START_ERROR_TYPES = {
    ValueError: 'ValueError', TypeError: 'TypeError', RuntimeError: 'RuntimeError',
    OSError: 'OSError', FileNotFoundError: 'FileNotFoundError', PermissionError: 'PermissionError',
    TimeoutError: 'TimeoutError', KeyboardInterrupt: 'KeyboardInterrupt',
    GeneratorExit: 'GeneratorExit', asyncio.CancelledError: 'CancelledError',
    FullRuntimeRefused: 'FullRuntimeRefused', FullRuntimeCancellationUnknown: 'FullRuntimeCancellationUnknown',
    BaseExceptionGroup: 'BaseExceptionGroup', ExceptionGroup: 'ExceptionGroup',
}


class _SingleManifestOwner:
    """Own a graceful worker, or an explicitly enabled rotator, and flat handler.

    The caller must retain this object through HOLD. This is not an IPC server,
    an authenticated lane, an installed startup path, or an automatic refresh.
    """
    __slots__ = ('_generation', '_lane', '_lanes', '_factory', '_factory_anchor', '_factory_bytes',
                 '_factory_admission_anchor', '_factory_admission_owner_anchor', '_timeout',
                 '_state', '_launch', '_owner', '_client', '_adapter', '_lock',
                 '_launch_thread', '_launch_done', '_launch_cancel', '_launch_error', '_owner_conflicts',
                 '_watcher_lease', '_retained', '_policy_pins', '_rotation_enabled', '_rotation_anchor',
                 '_rotation_type', '_vector_cache_enabled', '_cache_status', '_source_budget',
                 '_prewarm_generation', '_start_failure')

    def __init__(self, binding, snapshot, lane, lanes, factory, timeout, rotation_enabled=False,
                 vector_cache_enabled=False):
        self._generation = (binding, snapshot, None)
        self._lane = lane
        self._lanes, self._factory, self._timeout = frozenset(lanes), factory, timeout
        self._factory_anchor, self._factory_bytes = factory, self._factory_snapshot(factory)
        self._factory_admission_anchor = factory._admission
        self._factory_admission_owner_anchor = factory._admission_owner
        self._state, self._launch, self._owner = 'NEW', None, None
        self._client = self._adapter = None
        self._lock = threading.Lock()
        self._launch_thread = None
        self._launch_done, self._launch_cancel = threading.Event(), threading.Event()
        self._launch_error = None
        self._owner_conflicts = []
        self._watcher_lease = _OwnedSnapshotWatcher()
        self._retained = None
        self._policy_pins = None
        self._rotation_enabled = rotation_enabled
        self._rotation_anchor = None
        self._rotation_type = None
        self._vector_cache_enabled = vector_cache_enabled
        self._cache_status = 'NOT_STARTED' if vector_cache_enabled else 'DISABLED'
        self._source_budget = None
        self._prewarm_generation = None
        self._start_failure = None

    @property
    def _binding(self):
        return self._generation[0]

    @_binding.setter
    def _binding(self, value):
        generation = getattr(self, '_generation', (None, None, None))
        self._generation = (value, generation[1], generation[2])

    @property
    def _snapshot(self):
        return self._generation[1]

    @_snapshot.setter
    def _snapshot(self, value):
        generation = getattr(self, '_generation', (None, None, None))
        self._generation = (generation[0], value, generation[2])

    @property
    def _handler(self):
        return self._generation[2]

    @_handler.setter
    def _handler(self, value):
        generation = getattr(self, '_generation', (None, None, None))
        self._generation = (generation[0], generation[1], value)

    def _bind_policy_anchor(self, anchor):
        """Bind operator-supplied hashes once, before launching the owner."""
        if (self._state != 'NEW' or self._policy_pins is not None
                or type(anchor) is not _TrustedManifestPolicyAnchor):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        binding = self._binding
        if (type(binding) is not _PrivateBinding
                or not startup._same_path(Path(anchor.bundle_root), binding._PrivateBinding__bundle)
                or anchor.manifest_sha256 != binding._PrivateBinding__manifest
                or anchor.policy_sha256 != binding._PrivateBinding__policy_pin):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        create_operator_pinned_policy_anchor(enabled=True, bundle_root=anchor.bundle_root,
            expected_manifest_sha256=anchor.manifest_sha256, operator_policy_sha256=anchor.policy_sha256,
            operator_approval_reference_sha256=anchor.approval_reference_sha256)
        self._policy_pins = (anchor.bundle_root, anchor.manifest_sha256, anchor.policy_sha256,
                             anchor.approval_reference_sha256)

    def _check_policy_anchor(self):
        """Recheck the independently supplied policy and approval pins."""
        pins, binding = self._policy_pins, self._binding
        if (type(pins) is not tuple or len(pins) != 4 or type(binding) is not _PrivateBinding
                or not startup._same_path(Path(pins[0]), binding._PrivateBinding__bundle)
                or pins[1] != binding._PrivateBinding__manifest
                or pins[2] != binding._PrivateBinding__policy_pin):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        create_operator_pinned_policy_anchor(enabled=True, bundle_root=pins[0],
            expected_manifest_sha256=pins[1], operator_policy_sha256=pins[2],
            operator_approval_reference_sha256=pins[3])

    def _rotation_source_check(self):
        """Revalidate real source and operator controls without taking the owner lock.

        Rotation runs while start/query already owns that lock. This callback
        must never echo an unverified caller-provided digest or re-enter it.
        """
        self._check_factory()
        self._check_policy_anchor()
        snapshot = self._snapshot
        if self._source_budget is None:
            self._binding._immutable_checkpoint(snapshot)
        else:
            self._binding._immutable_checkpoint(snapshot, source_budget=self._source_budget)
        self._check_factory()
        return snapshot['projection_sha256']

    def _rotation_is_bound(self):
        if self._client is None or self._rotation_anchor is None:
            return self._client is None and self._rotation_anchor is None
        return (self._client is self._rotation_anchor and type(self._rotation_type) is type
                and type(self._client) is self._rotation_type)

    def _capture_owner(self, owner):
        # The launch thread records the reservation before any native spawn.
        if self._owner is not None:
            self._owner_conflicts.append(owner)
            raise ValueError('multiple_launch_owners')
        self._owner = owner

    def _refresh_generation(self, *, enabled=False):
        """Explicit in-process refresh; no scheduling, publishing or activation."""
        previous = staged = generation = old_handler = None
        old_rotation_snapshot = old_adapter_snapshot = None
        accepted = handler_locked = rotator_locked = contention_only = cache_publish_started = False
        pending = None
        if enabled is not True or not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        try:
            previous = self._generation
            if self._state != 'READY':
                raise ValueError('owner_not_ready')
            old_handler = self._handler
            if type(old_handler) is not _FlatRequestHandler:
                raise ValueError('refresh_handler')
            handler_locked = old_handler._lock.acquire(blocking=False)
            if not handler_locked:
                # Only this pre-state-change contention is a benign refusal.
                contention_only = True
                raise FullRuntimeRefused('mind_full_runtime_refused')
            if old_handler._pending_rotator is not None or old_handler._refresh_controller is not None:
                raise ValueError('refresh_handler_busy')
            self._state = 'HOLD'
            _verify_refresh_basis(self)
            staged = _stage_refresh_generation(self)
            if staged is _REFRESH_SOURCE_BUDGET or staged is _REFRESH_CACHE_CAPACITY:
                raise ValueError('refresh_resource_limit')
            generation = _validate_refresh_generation(self, staged)
            if self._rotation_enabled:
                if not self._rotation_is_bound() or self._adapter is None:
                    raise ValueError('rotation_binding')
                if not self._client._lock.acquire(blocking=False):
                    raise ValueError('rotation_busy')
                rotator_locked = True
                old_rotation_snapshot = self._client._snapshot
                old_adapter_snapshot = self._adapter._snapshot
                if (old_rotation_snapshot != previous[1]['projection_sha256']
                        or old_adapter_snapshot != old_rotation_snapshot
                        or self._adapter._rotator is not self._client):
                    raise ValueError('rotation_binding')
                self._adapter._static_check()
                self._client._check_worker_identity()
                self._generation = generation
                self._client._snapshot = generation[1]['projection_sha256']
                self._adapter._snapshot = generation[1]['projection_sha256']
                self._adapter._static_check()
                self._client._check_worker_identity()
                self._client._check_source()
            else:
                self._generation = generation
            if _STARTUP_OWNER_SOURCE_BUDGET_ENABLED:
                # The replacement generation is still private under the owner
                # lock. Prewarm it before publishing READY; a source failure
                # follows the existing rollback-to-HOLD path below.
                self._retained = None
                self._state = 'STARTING'
                self._source_budget = (_prewarm_source_budget(self._snapshot, extra_prefix_passes=2)
                    if self._vector_cache_enabled else _prewarm_source_budget(self._snapshot))
                self._ensure_retained_locked(source_budget=self._source_budget)
                self._prewarm_generation = self._generation
            if self._vector_cache_enabled:
                # Publication can have reached os.replace even if it raises.
                cache_publish_started = True
                _write_refresh_vector_cache(self, old_handler, source_budget=self._source_budget)
                self._cache_status = 'REFRESH_STORED'
            self._source_budget = None
            # Retire direct lane views without closing the shared worker.
            old_handler._closed = True
            old_handler._retained = None
            old_handler._rotator = None
            old_handler._adapter = None
            if not _STARTUP_OWNER_SOURCE_BUDGET_ENABLED:
                self._retained = None
            self._state = 'READY'
            accepted = True
        except BaseException as exc:
            if not contention_only:
                if cache_publish_started:
                    self._cache_status = 'REFRESH_UNKNOWN'
                if previous is not None:
                    self._generation = previous
                if rotator_locked:
                    self._client._snapshot = old_rotation_snapshot
                    self._adapter._snapshot = old_adapter_snapshot
                if type(old_handler) is _FlatRequestHandler:
                    old_handler._closed = True
                    old_handler._retained = None
                if self._handler is not None:
                    self._handler._retained = None
                self._retained = None
                self._state = 'HOLD'
            if staged is _REFRESH_SOURCE_BUDGET:
                pending = _EagerSourceBudgetRefused('SOURCE_BUDGET')
            elif staged is _REFRESH_CACHE_CAPACITY:
                pending = _CacheCapacityRefused('CACHE_CAPACITY')
            else:
                pending = _flat_cancellation(exc)
        finally:
            # These objects may contain identifier-policy and HMAC key bytes.
            # Keep them out of this frame before any refusal or cancellation.
            if rotator_locked:
                self._client._lock.release()
            if handler_locked:
                old_handler._lock.release()
            previous = staged = generation = old_handler = None
            self._lock.release()
        if pending is not None:
            raise pending from None
        if not accepted:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return {'schema':'wd.mind-flat-owner-refresh.v1', 'status':'REFRESHED', 'authority':'none'}

    def _ensure_retained_locked(self, *, source_budget=None):
        if self._retained is None:
            retained = (_RetainedFlatQueryState(self) if source_budget is None else
                        _RetainedFlatQueryState(self, source_budget=source_budget))
            self._handler._retained = retained
            self._retained = retained
        if (type(self._retained) is not _RetainedFlatQueryState
                or self._handler._retained is not self._retained
                or self._retained._generation is not self._generation):
            raise ValueError('retained_binding')
        return self._retained

    def query_local(self, *, enabled=False, lane=None, scope=None, query=None):
        """Query this retained owner in-process; the caller is not authenticated here."""
        if (enabled is not True or type(lane) is not str or lane not in self._lanes
                or type(scope) is not str or scope not in ('own', 'shared')
                or type(query) is not str or not query[:2000].strip() or len(query) > 4000 or '\x00' in query):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        try:
            query.encode('utf-8')
        except UnicodeEncodeError:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        # The legacy in-process API is closed until all source accounting is enabled.
        if not _STARTUP_OWNER_SOURCE_BUDGET_ENABLED:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        if not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        output, pending, checking, generation = None, None, False, None
        try:
            if self._state != 'READY' or type(self._handler) is not _FlatRequestHandler:
                raise ValueError('owner_not_ready')
            checking = True
            if (self._prewarm_generation is not self._generation or self._retained is None
                    or self._handler._retained is not self._retained):
                raise ValueError('prewarm_unavailable')
            # Preflight refusal does not damage a healthy owner. No source read yet.
            try:
                self._source_budget = _local_query_source_budget(self._snapshot)
            except _StartupSourceBudgetRefused:
                checking = False  # A known size limit is not source corruption.
                raise
            checking = True
            generation = self._generation
            self._check_factory()
            self._check_policy_anchor()
            policy_pin = _local_generation_policy_pin(self)
            retained = self._ensure_retained_locked()
            reply = self._handler._handle_for_lane({'schema':'wd.mind-flat-owner-request.v1',
                'lane':lane, 'query':query, 'scope':scope}, lane)
            if (type(reply) is not dict or reply.get('schema') != 'wd.mind-flat-owner-response.v1'
                    or reply.get('lane') != lane or reply.get('scope') != scope
                    or reply.get('authority') != 'none' or reply.get('snapshot_freshness') != 'UNKNOWN'
                    or type(reply.get('result')) is not dict):
                raise ValueError('owner_reply')
            result = reply['result']
            hits = result.get('results')
            if (result.get('schema') != 'wd.mind-flat-f32-query.v1'
                    or result.get('authority') != 'none' or result.get('status') not in ('SEMANTIC_CANDIDATE', 'ABSTAIN')
                    or type(hits) is not list or len(hits) > 3
                    or (result['status'] == 'SEMANTIC_CANDIDATE') != bool(hits)):
                raise ValueError('query_result')
            proposals = []
            for hit in hits:
                if (type(hit) is not dict or not reader._hex(hit.get('ref_sha256'))
                        or not reader._hex(hit.get('doc_id_sha256'))):
                    raise ValueError('query_hit')
                reference = hit.get('source_reference')
                if (type(reference) is not dict or reference.get('verified') != 'MATCH_SELECTED_ROW'
                        or not reader._hex(reference.get('sha256'))):
                    raise ValueError('selected_reference')
                proposals.append({'ref_sha256':hit['ref_sha256'], 'doc_id_sha256':hit['doc_id_sha256'],
                                  'source_event_sha256':reference['sha256']})
            verified = (retained._source_owner.resolve(proposals=proposals, lane=lane, scope=scope,
                                                      source_budget=self._source_budget)['results']
                        if proposals else [])
            if len(verified) != len(hits):
                raise ValueError('selected_count')
            records = retained._records
            by_doc = {reader._sha(key.encode()):record for key, record in records.items()}
            visible, selected_records = [], []
            for hit, fresh in zip(hits, verified):
                record = by_doc.get(hit['doc_id_sha256'])
                if (type(record) not in (dict, MappingProxyType)
                        or fresh.get('text') != hit.get('stored_text')
                        or fresh.get('source_event_sha256') != hit['source_reference']['sha256']
                        or fresh['source_event_sha256'] != record['provenance']['source_event_ref']
                        or record['meta']['agent'] not in reader._BUILD_LANES
                        or hit.get('source_agent') != record['meta']['agent']
                        or hit.get('source_time_utc') != record['time_utc']
                        or (scope == 'own' and record['meta']['agent'] != lane)):
                    raise ValueError('selected_source')
                stamp = reader._utc(record['time_utc'])
                finished = reader.dt.datetime.now(reader.dt.timezone.utc)
                age = (finished - stamp).total_seconds()
                expiry = record['provenance'].get('valid_until_utc')
                if age < 0 or (expiry is not None and finished >= reader._utc(expiry)):
                    raise ValueError('selected_time')
                visible.append({'text':reader.decode_text(fresh['text']),
                    'source_event_sha256':fresh['source_event_sha256'],
                    'event_time_utc_floor':record['time_utc'], 'source_agent':record['meta']['agent'],
                    'event_age_seconds_upper_bound':math.ceil(age)})
                selected_records.append(record)
            context = retained._controls()
            retained._prefix(context, source_budget=self._source_budget)
            self._handler._running_owner()
            self._check_factory()
            self._check_policy_anchor()
            if (self._generation is not generation or self._state != 'READY'
                    or not hmac.compare_digest(policy_pin, _local_generation_policy_pin(self))):
                raise ValueError('controls_changed')
            finished = reader.dt.datetime.now(reader.dt.timezone.utc)
            for hit, record in zip(visible, selected_records):
                age = (finished - reader._utc(record['time_utc'])).total_seconds()
                expiry = record['provenance'].get('valid_until_utc')
                if age < 0 or (expiry is not None and finished >= reader._utc(expiry)):
                    raise ValueError('selected_time_changed')
                hit['event_age_seconds_upper_bound'] = math.ceil(age)
            controls = self._binding._PrivateBinding__controls
            if type(controls) is not tuple or not controls or type(controls[0]) is not bytes:
                raise ValueError('fleet_binding')
            output = {'schema':'wd.mind-flat-verified-session.v1', 'authority':'none', 'lane':lane,
                      'fleet_sha256':reader._sha(controls[0]), 'sender_authentication':'UNKNOWN',
                      'index_match':'UNKNOWN', 'same_user_aba':'UNKNOWN',
                      'current_status_freshness':'UNKNOWN', 'results':visible}
        except BaseException as exc:
            pending = _flat_cancellation(exc)
            if checking or pending is not None:
                self._state = 'HOLD'
                self._retained = None
                if self._handler is not None:
                    self._handler._retained = None
        finally:
            # A HOLD may retain an uncertain callback. Keep its budget alive
            # until owner shutdown is proven rather than enabling unbounded I/O.
            if self._state == 'READY':
                self._source_budget = None
            generation = None
            self._lock.release()
        if pending is not None:
            raise pending from None
        if output is None:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return output

    def _prepare_consistent_delivery(self, *, enabled=False, request=None, expected_nonce=None,
                                     expected_identifier_policy_sha256=None, max_total_bytes=None):
        """Return one generation's candidates and prepared export, never transmit."""
        if (enabled is not True or type(max_total_bytes) is not int
                or not 0 < max_total_bytes <= 64 * 1024 * 1024
                or not self._lock.acquire(blocking=False)):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        result, pending, generation = None, None, None
        try:
            if self._state != 'READY' or self._handler is None:
                raise ValueError('owner_not_ready')
            generation = self._generation
            result = _prepare_generation_delivery(self, request, expected_nonce,
                expected_identifier_policy_sha256, max_total_bytes)
            if self._generation is not generation or self._state != 'READY':
                self._state = 'HOLD'
                raise ValueError('generation_changed')
        except BaseException as exc:
            result = None
            pending = _flat_cancellation(exc)
            if (pending is not None or
                    not isinstance(exc, (_GenerationInputRefused, _GenerationSizeRefused))):
                self._state = 'HOLD'
                self._retained = None
                if self._handler is not None:
                    self._handler._retained = None
        finally:
            generation = None
            self._lock.release()
        if pending is not None:
            raise pending from None
        if result is None:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return result

    def _check_generation_export(self, *, enabled=False, export=None):
        if enabled is not True or not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        accepted = False
        try:
            if (self._state != 'READY' or type(export) is not _PrivateConsumerExport
                    or export._PrivateConsumerExport__owner is not self
                    or not export._PrivateConsumerExport__active
                    or export._PrivateConsumerExport__memory is not self._handler._memory):
                raise ValueError('stale_export')
            self._check_factory()
            self._handler._running_owner()
            _flat_check(self._handler._memory)
            accepted = True
        except Exception:
            pass
        finally:
            self._lock.release()
        if not accepted:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return True

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
                or self._factory._admission is not self._factory_admission_anchor
                or self._factory._admission_owner is not self._factory_admission_owner_anchor
                or not hmac.compare_digest(self._factory_snapshot(self._factory), self._factory_bytes)):
            raise ValueError('factory_changed')
        from tools import wd_mind_runtime_child as child
        if type(self._factory_admission_anchor) is not child._LocalAdmission:
            raise ValueError('factory_admission')
        self._factory_admission_anchor.assert_owner(self._factory_admission_owner_anchor)

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
        worker_cancel = None
        if self._owner_conflicts:
            self._state = 'HOLD'
            return {'state': 'HOLD', 'owned': None, 'authority': 'none'}
        if self._rotation_enabled and not self._rotation_is_bound():
            self._state = 'HOLD'
            return {'state': 'HOLD', 'owned': None, 'authority': 'none'}
        if self._rotation_enabled and self._client is not None:
            try:
                current = self._client.status()
                owned = current.get('owned') if type(current) is dict else None
                if type(owned) is not bool:
                    owned = None
                if self._state == 'READY' and (owned is not True or current.get('state') != 'RUNNING'):
                    self._state = 'HOLD'
            except (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError):
                self._state = 'HOLD'
                raise
            except (BaseExceptionGroup, FullRuntimeCancellationUnknown) as exc:
                self._state = 'HOLD'
                owned = None
                worker_cancel = _flat_cancellation(exc)
            except BaseException:
                self._state, owned = 'HOLD', None
        elif self._owner is None:
            owned = False if self._state == 'NEW' else None
        else:
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
            except (BaseExceptionGroup, FullRuntimeCancellationUnknown) as exc:
                self._state = 'HOLD'
                owned = None
                worker_cancel = _flat_cancellation(exc)
            except BaseException:
                owned = None
                if self._state == 'READY':
                    self._state = 'HOLD'
        if worker_cancel is not None:
            raise worker_cancel from None
        pending_cancel = None
        try:
            watcher_owned = self._watcher_lease._held()
        except (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError):
            self._state = 'HOLD'
            raise
        except (BaseExceptionGroup, FullRuntimeCancellationUnknown) as exc:
            self._state = 'HOLD'
            watcher_owned = None
            pending_cancel = _flat_cancellation(exc)
        except BaseException:
            self._state = 'HOLD'
            watcher_owned = None
        if pending_cancel is not None:
            raise pending_cancel from None
        if watcher_owned is True:
            self._state, owned = 'HOLD', True
        elif watcher_owned is None:
            self._state = 'HOLD'
            owned = True if owned is True else None
        result = {'state': self._state, 'owned': owned, 'authority': 'none'}
        if self._vector_cache_enabled:
            result['cache_status'] = self._cache_status
        return result

    def invalidate_external_policy(self):
        """Fence this owner after its caller detects an external policy-pin failure.

        Keep the underlying worker owned for an explicit graceful shutdown.  The
        lock serializes invalidation with any in-flight use of retained data.
        """
        with self._lock:
            self._state = 'HOLD'
            self._retained = None
            if self._handler is not None:
                self._handler._retained = None
            return self._status()

    def status(self):
        if not self._lock.acquire(blocking=False):
            return {'state': 'BUSY', 'owned': None, 'authority': 'none'}
        try:
            return self._status()
        finally:
            self._lock.release()

    def start_diagnostic(self):
        """Return fixed codes only; never expose exception messages or locals."""
        if not self._lock.acquire(blocking=False):
            return {'reason': 'BUSY', 'exception_type': 'UNKNOWN', 'authority': 'none'}
        try:
            value = self._start_failure
            if value is None:
                return {'reason': 'NONE', 'exception_type': 'NONE', 'authority': 'none'}
            return {'reason': value[0], 'exception_type': value[1], 'authority': 'none'}
        finally:
            self._lock.release()

    def worker_exit_diagnostic(self):
        """Observe the retained worker, without waiting or changing ownership."""
        if not self._lock.acquire(blocking=False):
            return ('UNKNOWN', None)
        try:
            target = self._client if self._rotation_enabled else self._owner
            if self._owner_conflicts or target is None:
                return ('UNKNOWN', None)
            try:
                return target.worker_exit_diagnostic()
            except BaseException:
                return ('UNKNOWN', None)
        finally:
            self._lock.release()

    def start(self):
        if not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        pending_cancel = None
        try:
            if self._state != 'NEW':
                raise FullRuntimeRefused('mind_full_runtime_refused') from None
            self._state = 'STARTING'
            stage = 'BINDING_CHECK'
            try:
                self._check_factory()
                _flat_snapshot(self._binding, self._snapshot)
                if _STARTUP_OWNER_SOURCE_BUDGET_ENABLED:
                    # Refuse a known over-budget snapshot before cache or worker
                    # setup can trigger an expensive passage build.
                    stage = 'SOURCE_PREWARM'
                    try:
                        _prewarm_source_fits(self._snapshot)
                    except _StartupSourceBudgetRefused as exc:
                        if type(exc) is _StartupSourceBudgetRefused:
                            stage = 'SOURCE_BUDGET'
                        raise
                vector_cache = None
                if self._vector_cache_enabled:
                    stage = 'CACHE_PREFLIGHT'
                    try:
                        vector_cache = _load_pinned_vector_cache(self._binding)
                        vector_cache.preflight()
                        self._cache_status = 'BUILDING'
                    except BaseException:
                        self._cache_status = 'PREFLIGHT_REFUSED'
                        raise
                stage = 'WORKER_SETUP'
                from tools import wd_mind_e5_snapshot_adapter as adapter_module
                bundle, manifest = self._binding._PrivateBinding__bundle, self._binding._PrivateBinding__manifest
                if self._rotation_enabled:
                    _, runtime_hash = adapter_module._flat_bundle_bindings(str(bundle), manifest)
                    # Do not execute an unverified candidate module: this loader
                    # checks its manifest pin and exact origin before import.
                    rotation = adapter_module._load_pinned_rotating_module(str(bundle), manifest)
                    self._rotation_type = rotation.RotatingGracefulE5Client
                    if type(self._rotation_type) is not type:
                        raise ValueError('rotation_type')
                    # Rotator construction is inert. Store it before adapter or
                    # worker activity so any later HOLD remains reachable.
                    self._client = self._rotation_type(factory=self._factory,
                        encoder_binding_sha256=runtime_hash, snapshot_sha256=self._snapshot['projection_sha256'],
                        source_check=self._rotation_source_check, call_timeout=self._factory._timeout,
                        shutdown_timeout=self._timeout, bulk_contract_sha256=self._factory._bulk)
                    self._rotation_anchor = self._client
                    self._adapter = adapter_module.create_rotating_flat_adapter(enabled=True, bundle_root=str(bundle),
                        expected_manifest_sha256=manifest, client=self._client,
                        expected_snapshot_sha256=self._snapshot['projection_sha256'])
                else:
                    self._launch_thread = threading.Thread(target=self._run_launch,
                        name='mind-e5-launch', daemon=False)
                    self._launch_thread.start()
                    self._wait_for_launch()
                    if self._launch_error is not None:
                        raise self._launch_error
                    launched = self._launch
                    # Retain the entire result before fallible binding checks.
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
                    self._adapter = adapter_module.create_single_flat_adapter(enabled=True, bundle_root=str(bundle),
                        expected_manifest_sha256=manifest, client=self._client)
                self._handler = _FlatRequestHandler(self._binding, self._snapshot, self._lane, self._lanes,
                    self._client, self._adapter, self._factory, self._timeout, self._factory._bulk,
                    self._watcher_lease, vector_cache)
                stage = 'HANDLER_BUILD'
                result = self._handler.build()
                if result != {'schema':'wd.mind-flat-owner-build.v1', 'status':'BUILT_IN_MEMORY', 'authority':'none'}:
                    raise ValueError('build')
                stage = 'CACHE_RESULT'
                if (self._vector_cache_enabled and
                        (type(self._handler._memory) is not _PrivateFlatMemory
                         or self._handler._memory.cache_status not in ('HIT_STORED', 'MISS_STORED'))):
                    self._cache_status = 'NOT_STORED'
                    raise ValueError('cache_not_stored')
                if self._vector_cache_enabled:
                    self._cache_status = self._handler._memory.cache_status
                stage = 'FINAL_BINDING_CHECK'
                self._check_factory()
                if _STARTUP_OWNER_SOURCE_BUDGET_ENABLED:
                    stage = 'SOURCE_PREWARM'
                    try:
                        self._source_budget = _prewarm_source_budget(self._snapshot)
                    except _StartupSourceBudgetRefused as exc:
                        if type(exc) is _StartupSourceBudgetRefused:
                            stage = 'SOURCE_BUDGET'
                        raise
                    self._ensure_retained_locked(source_budget=self._source_budget)
                    self._prewarm_generation = self._generation
                    self._source_budget = None
                self._state = 'READY'
            except (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError) as exc:
                self._start_failure = (stage, next((name for kind, name in _START_ERROR_TYPES.items() if type(exc) is kind), 'UNKNOWN'))
                self._launch_cancel.set()
                self._state = 'HOLD'
                raise
            except (BaseExceptionGroup, FullRuntimeCancellationUnknown) as exc:
                self._start_failure = (stage, next((name for kind, name in _START_ERROR_TYPES.items() if type(exc) is kind), 'UNKNOWN'))
                self._launch_cancel.set()
                self._state = 'HOLD'
                pending_cancel = _flat_cancellation(exc)
            except BaseException as exc:
                if stage == 'HANDLER_BUILD' and type(exc) is _CacheCapacityRefused:
                    stage = 'CACHE_CAPACITY'
                self._start_failure = (stage, next((name for kind, name in _START_ERROR_TYPES.items() if type(exc) is kind), 'UNKNOWN'))
                # Unknown post-launch states are never declared exited. The
                # saved owner (if present) remains reachable for explicit EOF.
                self._state = 'HOLD'
            if pending_cancel is not None:
                raise pending_cancel from None
            return self._status()
        finally:
            self._lock.release()

    def prepare_consumer_export(self, *, enabled=False, expected_nonce=None,
                                expected_identifier_policy_sha256=None, max_total_bytes=None):
        if not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        try:
            return self._prepare_consumer_export_locked(enabled=enabled, expected_nonce=expected_nonce,
                expected_identifier_policy_sha256=expected_identifier_policy_sha256, max_total_bytes=max_total_bytes)
        finally:
            self._lock.release()

    def _prepare_consumer_export_locked(self, *, enabled=False, expected_nonce=None,
                                        expected_identifier_policy_sha256=None, max_total_bytes=None):
        """Prepare pinned snapshot/flat data in memory; never transfer it.

        The caller must independently obtain the policy pin from an approved
        launcher context. The nonce is correlation data, not receiver authority.
        A trusted, separately inherited channel is required before any export.
        """
        result = None
        size_refused = False
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
                size_refused = True
                raise ValueError('export_size')
            _flat_check(memory)
            self._check_factory()
            self._handler._running_owner()
            result = _PrivateConsumerExport(metadata, blocks, total, self, memory)
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
        if cancelled_type is not None:
            raise cancelled_type() from None
        if result is None:
            if size_refused:
                raise _GenerationSizeRefused('mind_full_runtime_refused') from None
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return result

    def _check_candidate_delivery(self, *, source_budget=None):
        retained = self._retained
        if (type(retained) is not _RetainedFlatQueryState or self._handler is None
                or self._handler._retained is not retained):
            raise ValueError('retained_binding')
        context = retained._controls()
        if source_budget is None:
            retained._prefix(context)
        else:
            retained._prefix(context, source_budget=source_budget)

    def _candidate_request(self, request, *, located):
        expected = 'wd.mind-pipe-request.v2' if located else 'wd.mind-pipe-request.v1'
        if (type(request) is not dict or set(request) != {'schema','query_id','nonce','lane','scope'}
                or any(type(value) is not str for value in request.values())
                or request['schema'] != expected or request['query_id'] != 'startup_work_overview'
                or not reader._hex(request['nonce']) or request['lane'] not in self._lanes
                or request['lane'] not in reader._LANES or request['scope'] not in ('own','shared')):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        # A disabled budget gate refuses both candidate protocols before I/O.
        if not _STARTUP_OWNER_SOURCE_BUDGET_ENABLED:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        frozen = dict(request, schema='wd.mind-pipe-request.v1')
        if not self._lock.acquire(blocking=False):
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        response, pending, checking = None, None, False
        try:
            if self._state != 'READY' or self._handler is None:
                raise ValueError('owner_not_ready')
            checking = True
            if self._prewarm_generation is not self._generation:
                raise ValueError('prewarm_unavailable')
            try:
                self._source_budget = _startup_owner_source_budget(self._snapshot)
            except _StartupSourceBudgetRefused:
                checking = False
                raise
            self._check_factory_or_hold()
            self._check_policy_anchor()
            # This path may never lazily rebuild retained state: prewarm has a
            # separate allowance and finished before this generation was ready.
            if self._retained is None or self._handler._retained is not self._retained:
                raise ValueError('prewarm_changed')
            response = map_pipe_candidates(enabled=True, handler=self._handler, request=frozen,
                                           _include_locations=located)
            self._check_factory_or_hold()
            self._check_policy_anchor()
            # The caller can mutate its original dict during the query. Complete
            # all source/control checks before refusing that caller-only error.
            self._check_candidate_delivery(source_budget=self._source_budget)
            self._check_factory_or_hold()
            self._check_policy_anchor()
            try:
                latest = dict(request)
                caller_changed = (len(latest) != len(frozen)
                    or any(type(value) is not str for value in latest.values())
                    or latest != dict(frozen, schema=expected))
            except Exception:
                caller_changed = True
            if caller_changed:
                response = None
        except BaseException as exc:
            response = None
            pending = _flat_cancellation(exc)
            if checking or pending is not None:
                self._state = 'HOLD'
                self._retained = None
                if self._handler is not None:
                    self._handler._retained = None
        finally:
            if self._state == 'READY':
                self._source_budget = None
            self._lock.release()
        if pending is not None:
            raise pending from None
        if response is None:
            raise FullRuntimeRefused('mind_full_runtime_refused') from None
        return response

    def candidates(self, request):
        return self._candidate_request(request, located=False)

    def located_candidates(self, request):
        """Return untrusted row locations; the receiver must independently verify them."""
        return self._candidate_request(request, located=True)

    def close(self):
        if not self._lock.acquire(blocking=False):
            return {'state': 'BUSY', 'owned': None, 'authority': 'none'}
        try:
            pending_cancel = None
            watcher_check_failed = False
            try:
                if self._watcher_lease._begin_close() is not False:
                    self._state = 'HOLD'
                    return self._status()
            except (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError):
                self._state = 'HOLD'
                raise
            except (BaseExceptionGroup, FullRuntimeCancellationUnknown) as exc:
                self._state = 'HOLD'
                watcher_check_failed = True
                pending_cancel = _flat_cancellation(exc)
            except BaseException:
                self._state = 'HOLD'
                return self._status()
            if pending_cancel is not None:
                raise pending_cancel from None
            if watcher_check_failed:
                return self._status()
            if self._state == 'NEW':
                self._state = 'STOPPED'
                return {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
            # A failure before the launch thread is even constructed cannot
            # have invoked the worker factory. Keep every ambiguous state in
            # HOLD, including a Thread.start() failure after construction.
            if (self._state == 'HOLD' and self._launch_thread is None
                    and self._owner is None and self._launch is None and self._client is None
                    and self._adapter is None and self._handler is None and not self._owner_conflicts
                    and self._launch_error is None and not self._launch_done.is_set()):
                self._state = 'STOPPED'
                return {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
            if self._launch_thread is not None and self._launch_thread.is_alive():
                self._state = 'HOLD'
                return self._status()
            if self._rotation_enabled and not self._rotation_is_bound():
                self._state = 'HOLD'
                return {'state': 'HOLD', 'owned': None, 'authority': 'none'}
            if self._rotation_enabled and self._client is not None:
                try:
                    result = self._client.close(timeout=self._timeout)
                    if (type(result) is dict and result.get('state') == 'EXITED'
                            and result.get('owned') is False and result.get('exit_code') == 0):
                        self._retained = None
                        if self._handler is not None:
                            self._handler._retained = None
                        self._source_budget = None
                        self._state = 'STOPPED'
                        return {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
                except (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError):
                    self._state = 'HOLD'
                    raise
                except (BaseExceptionGroup, FullRuntimeCancellationUnknown) as exc:
                    self._state = 'HOLD'
                    pending_cancel = _flat_cancellation(exc)
                    if pending_cancel is not None:
                        raise pending_cancel from None
                except BaseException:
                    pass
                self._state = 'HOLD'
                return self._status()
            if self._owner_conflicts:
                self._state = 'HOLD'
                return self._status()
            if self._owner is None:
                self._state = 'HOLD'
                return self._status()
            worker_cancel = None
            try:
                if self._client is not None:
                    result = self._client.close(timeout=self._timeout)
                else:
                    result = self._owner.shutdown(timeout=self._timeout)
                if (type(result) is dict and result.get('state') == 'EXITED'
                        and result.get('owned') is False):
                    self._retained = None
                    if self._handler is not None:
                        self._handler._retained = None
                    self._source_budget = None
                    self._state = 'STOPPED'
                    return {'state': 'STOPPED', 'owned': False, 'authority': 'none'}
            except (KeyboardInterrupt, GeneratorExit, asyncio.CancelledError):
                self._state = 'HOLD'
                raise
            except (BaseExceptionGroup, FullRuntimeCancellationUnknown) as exc:
                self._state = 'HOLD'
                worker_cancel = _flat_cancellation(exc)
            except Exception:
                pass
            if worker_cancel is not None:
                raise worker_cancel from None
            self._state = 'HOLD'
            return self._status()
        finally:
            self._lock.release()


def create_single_manifest_owner(*, enabled=False, binding=None, lane=None, worker_factory=None,
                                 shutdown_timeout=None, policy_anchor=None, worker_rotation=False,
                                 vector_cache_enabled=False):
    """Create an inert owner, retaining its worker or explicit rotator on HOLD.

    The manifest and lane must already be independently bound. No worker is
    launched until start(). Rotation defaults off; no guardian or listener is added.
    """
    result = None
    try:
        from tools import wd_mind_e5_client as worker_client
        if (enabled is not True or type(binding) is not _PrivateBinding
                or type(worker_factory) is not _ManifestWorkerFactory or type(lane) is not str
                or lane not in reader._LANES or type(worker_rotation) is not bool or type(vector_cache_enabled) is not bool
                or type(shutdown_timeout) not in (int, float)
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
        result = _SingleManifestOwner(binding, snapshot, lane, names, worker_factory, float(shutdown_timeout),
                                      rotation_enabled=worker_rotation, vector_cache_enabled=vector_cache_enabled)
        if policy_anchor is not None:
            result._bind_policy_anchor(policy_anchor)
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

# Relevance band for the fixed startup question only. On the real log the e5
# cosine of every eligible row is far above the 0.55 floor and tightly packed
# (median about 0.81, best about 0.88), so cosine order alone returned rows 18
# to 108 days old. Rows within this band of the best score are equally
# relevant for "current ... recent status"; the newest of them come first.
_STARTUP_RELEVANCE_BAND = 0.05


def _order_selected(selected, query):
    """Order (record_id, score, lexical, time) rows; recency inside the band only for the startup question."""
    ordered = sorted(selected, key=lambda item: (-item[1], -item[2], -item[3].timestamp(), item[0]))
    if query != _STARTUP_PIPE_QUERY or not ordered:
        return ordered
    floor = ordered[0][1] - _STARTUP_RELEVANCE_BAND
    band = [item for item in ordered if item[1] >= floor]
    band.sort(key=lambda item: (-item[3].timestamp(), -item[1], -item[2], item[0]))
    # A narrow band must not shrink the answer: rows below it follow in the
    # old cosine order, so a lane still gets up to three rows.
    return band + [item for item in ordered if item[1] < floor]



def _first_three(selected, records, query):
    """Cut ordered rows to three; the startup question takes one row per task first.

    Measured 2026-11-26 on the real log (aggregates only): with the band alone
    a startup answer covered 2.26 distinct tasks on average and 14 % of
    answers held a near-duplicate pair; one row per task gave 3.0 tasks.
    Rows of an already chosen task fill the answer only if fewer than three
    tasks are eligible, so the answer never shrinks. The trade-off: a second,
    more relevant row of a chosen task yields to a weaker row of another task.
    The reader refuses events without a task_id, so every record has one; a
    missing key would group such rows under None.
    """
    if query != _STARTUP_PIPE_QUERY:
        return selected[:3]
    chosen, later, tasks = [], [], set()
    for item in selected:
        if len(chosen) == 3:
            break
        task = records[item[0]]['meta'].get('task_id')
        if task in tasks:
            later.append(item)
        else:
            tasks.add(task)
            chosen.append(item)
    return (chosen + later)[:3]

def map_pipe_candidates(*, enabled=False, handler=None, request=None, _include_locations=False):
    """Map one fixed public in-process query to source hashes, never text.

    The caller must authenticate and bind the peer separately; accepting a
    lane string here is not proof of caller identity. No listener is created.
    """
    result = None
    try:
        if (enabled is not True or type(handler) is not _FlatRequestHandler or type(request) is not dict
                or type(_include_locations) is not bool
                or (_include_locations and type(handler._retained) is not _RetainedFlatQueryState)):
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
                or reply['authority'] != 'none' or reply['expiry_scope'] != 'source_validity_only'
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
                    or reference['verified'] != ('MATCH_SELECTED_ROW' if handler._retained is not None
                                                 else 'MATCH_WITHIN_PREFIX')
                    or reference['sender_authentication'] != 'UNKNOWN'):
                raise ValueError('reference')
            row = {'ref_sha256': hit.get('ref_sha256'), 'doc_id_sha256': hit.get('doc_id_sha256'),
                   'source_event_sha256': reference['sha256']}
            if any(not reader._hex(value) for value in row.values()):
                raise ValueError('hash')
            if _include_locations:
                offset, length, chunk = hit.get('source_offset'), hit.get('source_length'), hit.get('chunk_index')
                if (type(offset) is not int or type(length) is not int or type(chunk) is not int
                        or offset < 0 or not 1 <= length <= 4 * 1024 * 1024
                        or not 0 <= chunk <= 4 * 1024 * 1024 or offset + length > 2**63 - 1):
                    raise ValueError('location')
                row.update(source_offset=offset, source_length=length, chunk_index=chunk)
            key = tuple(row[name] for name in ('ref_sha256', 'doc_id_sha256', 'source_event_sha256'))
            if key in seen:
                raise ValueError('duplicate')
            seen.add(key)
            candidates.append(row)
        if candidates:
            if (reply['expires_utc'] is not None and
                    (type(reply['expires_utc']) is not str
                     or reader.dt.datetime.now(reader.dt.timezone.utc) >= reader._utc(reply['expires_utc']))):
                candidates = []
        elif reply['expires_utc'] is not None:
            raise ValueError('expiry')
        if request != frozen:
            raise ValueError('request_changed')
        result = {'schema': ('wd.mind-pipe-candidates.v2' if _include_locations else 'wd.mind-pipe-candidates.v1'),
                  'nonce': frozen['nonce'],
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
