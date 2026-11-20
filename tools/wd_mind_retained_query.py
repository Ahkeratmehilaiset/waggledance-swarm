# SPDX-License-Identifier: BUSL-1.1
"""Opt-in, process-local retained semantic state; grants no authority.

The original content reader and its self-pinned decision bytes are unchanged.
The owner never writes a cache. A permanent source/control deviation closes it.
Same-user change-and-restore between checks remains UNKNOWN.
"""
from dataclasses import dataclass
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
from types import MappingProxyType

from tools import wd_mind_content_query_readonly as r


class RetainedCancellationUnknown(BaseException):
    """A bounded exception-group scan could not determine all cancellations."""


def _clean_cancellation(problem):
    pending, signals, visited = [problem], [], 0
    grouped = isinstance(problem, BaseExceptionGroup)
    while pending:
        visited += 1
        if visited > 1024 or len(pending) > 1024:
            return RetainedCancellationUnknown('retained_cancellation_unknown')
        current = pending.pop()
        kind = type(current)
        if issubclass(kind, BaseExceptionGroup):
            pending.extend(reversed(BaseExceptionGroup.exceptions.__get__(current)))
        elif issubclass(kind, RetainedCancellationUnknown):
            return RetainedCancellationUnknown('retained_cancellation_unknown')
        elif issubclass(kind, GeneratorExit):
            signals.append(GeneratorExit())
        elif issubclass(kind, KeyboardInterrupt):
            signals.append(KeyboardInterrupt())
        elif issubclass(kind, asyncio.CancelledError):
            signals.append(asyncio.CancelledError())
    if not signals:
        return None
    return BaseExceptionGroup('provider_cancelled', signals) if grouped else signals[0]


def _public(operation):
    output = cancellation = None
    try:
        output = operation()
    except BaseException as exc:
        cancellation = _clean_cancellation(exc)
    finally:
        operation = None
    if cancellation is not None:
        raise cancellation from None
    if output is None:
        r._deny()
    return output


def _pid():
    return os.getpid()


def _snapshot_copy(value):
    """Capture immutable scalar/bytes bindings, reconstructing only dictionaries."""
    if type(value) is not dict or set(value) != r._Q4_SOURCE_KEYS:
        raise ValueError('snapshot')
    messages = value['messages']
    if type(messages) is not dict:
        raise ValueError('messages')
    binding = value['identifier_policy_binding']
    if type(binding) is not tuple or len(binding) != 3:
        raise ValueError('policy')
    msg_items = tuple(messages.items())
    items = tuple((key, val) for key, val in value.items() if key != 'messages')
    return items, msg_items


def _snapshot_thaw(frozen):
    items, messages = frozen
    result = dict(items)
    result['messages'] = dict(messages)
    return result


def _source_check(context, *, check_cancel=None):
    """Verify the entire pinned prefix, cooperatively checking cancellation.

    A synchronous open/read may itself outlast a deadline. The caller retains
    ownership until this function actually returns; a callback cannot interrupt
    an in-flight native operation.
    """
    if check_cancel is not None and not callable(check_cancel):
        raise ValueError('check_cancel')

    def checkpoint():
        if check_cancel is not None:
            check_cancel()

    checkpoint()
    bridge, absent = r._narrow_dependencies()
    checkpoint()
    messages = context['messages']
    source, sidecar = Path(messages['source_path']), Path(messages['generation_path'])
    cursor = messages['end_cursor']
    digest = hashlib.sha256()
    checkpoint()
    with bridge._open_log(source) as stream:
        checkpoint()
        r._narrow_fence(stream, source, sidecar, cursor, bridge, absent)
        checkpoint()
        remaining = cursor.offset
        while remaining:
            checkpoint()
            block = stream.read(min(1024 * 1024, remaining))
            checkpoint()
            if not block:
                raise ValueError('short_prefix')
            digest.update(block)
            remaining -= len(block)
        if not hmac.compare_digest(digest.hexdigest(), context['decision']['source_prefix']['sha256']):
            raise ValueError('prefix_changed')
        checkpoint()
        r._narrow_fence(stream, source, sidecar, cursor, bridge, absent)
        checkpoint()
    checkpoint()


@dataclass(frozen=True, slots=True, repr=False)
class _VerifiedState:
    snapshot: tuple
    records: object
    entries: object
    report: object
    output_binding: object
    encoder_binding: bytes
    encoder_binding_sha: str
    index_binding: bytes
    index_binding_sha: str
    memory_controls: tuple
    cell_names: tuple


def _prepare_state(values, gate):
    descriptor, memory, encoder_binding, index_binding = r._q4_controls(values)
    if descriptor['scope'] != r._ALL_TASK_ROUTE_SCOPE:
        raise ValueError('scope')
    snapshot = {key: values[key] for key in r._Q4_SOURCE_KEYS}
    snapshot['messages'] = dict(snapshot['messages'])
    projection, context, initial_report = gate.verify_snapshot(snapshot)
    _source_check(context)
    cursor = snapshot['messages']['end_cursor']
    snapshot['messages']['end_cursor'] = type(cursor)(cursor.offset, cursor.file_identity, cursor.generation)
    frozen_snapshot = _snapshot_copy(snapshot)
    frozen_cells = r._q4_freeze_complete_cells(values['cell_snapshots'])
    records_list = projection['messages']['records']
    records = {record['record_id']: record for record in records_list}
    if (not records or len(records) != len(records_list) or type(memory.get('records')) is not int
            or memory['records'] != len(records) or memory.get('views') != ['w', 'd']
            or memory.get('independent_views') is not False
            or memory.get('cutoff_utc') != max(record['available_utc'] for record in records_list)):
        raise ValueError('records')
    limits = r._q4_complete_source_limits(records)
    if set(limits) != set(memory['cells']):
        raise ValueError('cells')
    captured, index_limits = {}, {}
    for cell_id in sorted(memory['cells']):
        captured[cell_id] = r._q4_complete_cell_controls(cell_id, frozen_cells[cell_id],
            memory['cells'][cell_id], memory, records, limits[cell_id])
        digest = r._sha(captured[cell_id][0]['index'])
        count = limits[cell_id]['count']
        if digest in index_limits and index_limits[digest] != count:
            raise ValueError('index_counts')
        index_limits[digest] = count
    encoder, index_provider = values['encoder_provider'], values['index_provider']
    r._q4_provider_identity(encoder, encoder_binding, 'query')
    r._q4_provider_identity(index_provider, index_binding, 'load')
    scalar_keys = r._Q4_KEYS - r._Q4_SOURCE_KEYS - {'encoder_provider', 'index_provider', 'stream', 'cell_snapshots'}
    scalar_items = tuple((key, values[key]) for key in sorted(scalar_keys))

    def checkpoint():
        if values['encoder_provider'] is not encoder or values['index_provider'] is not index_provider:
            raise ValueError('providers_changed')
        if r._q4_freeze_complete_cells(values['cell_snapshots']) != frozen_cells:
            raise ValueError('cells_changed')
        if any(type(values[key]) is not type(value) or values[key] != value for key, value in scalar_items):
            raise ValueError('controls_changed')
        for key, value in _snapshot_thaw(frozen_snapshot).items():
            if type(values[key]) is not type(value) or values[key] != value:
                raise ValueError('source_controls_changed')
        _, _, current = gate.verify_snapshot(snapshot, _return_projection=False)
        if current != initial_report:
            raise ValueError('source_changed')
        r._q4_provider_identity(encoder, encoder_binding, 'query')
        r._q4_provider_identity(index_provider, index_binding, 'load')

    def load(raw):
        index = index_provider.load(raw)
        checkpoint()
        return r._CompleteCheckedQueryIndex(index, checkpoint)

    checkpoint()
    provider = r._CompleteIndexAdapter(load, index_limits)
    entries = r._q4_decode_cells_complete_v1(captured, provider, records, retain=True)
    checkpoint()
    _, end_context, _ = gate.verify_snapshot(snapshot, _return_projection=False)
    _source_check(end_context)
    output_binding = {key: values[key] for key in (
        'descriptor_sha256', 'memory_sha256', 'projection_sha256', 'encoder_sha256',
        'encoder_binding_sha256', 'index_binding_sha256', 'policy_sha256', 'roster_sha256',
        'decision_sha256', 'approved_tasks_sha256')}
    output_binding['task_scope_sha256'] = output_binding.pop('approved_tasks_sha256')
    return _VerifiedState(frozen_snapshot, r._freeze_json(records), r._freeze_json(entries),
        r._freeze_json(initial_report), MappingProxyType(output_binding), values['encoder_binding_bytes'],
        values['encoder_binding_sha256'], values['index_binding_bytes'], values['index_binding_sha256'], scalar_items,
        tuple(sorted(memory['cells'])))


class _RetainedQueryOwner:
    __slots__ = ('_state', '_gate', '_encoder', '_index_provider', '_pid', '_lock')

    def __init__(self, state, gate, encoder, index_provider):
        self._state, self._gate, self._encoder, self._index_provider = state, gate, encoder, index_provider
        self._pid, self._lock = _pid(), threading.Lock()

    def __repr__(self):
        return '<RetainedMindQueryOwner>'

    def _drop(self):
        self._state = self._gate = self._encoder = self._index_provider = None

    def close(self):
        if not self._lock.acquire(blocking=False):
            r._deny()
        try:
            self._drop()
        finally:
            self._lock.release()

    def query(self, query_bytes):
        def run():
            if not self._lock.acquire(blocking=False):
                raise ValueError('busy')
            try:
                if _pid() != self._pid:
                    raise ValueError('foreign_process')
                query, when = _parse_query(query_bytes)
                try:
                    return self._query(query, when)
                except BaseException:
                    self._drop()
                    raise
            finally:
                self._lock.release()
        return _public(run)

    def _query(self, query, when):
        state, gate = self._state, self._gate
        if state is None or gate is None or _pid() != self._pid:
            raise ValueError('closed')
        snapshot = _snapshot_thaw(state.snapshot)
        controls = dict(state.memory_controls)
        controls.update(snapshot)
        encoder_provider, index_provider = self._encoder, self._index_provider
        controls.update(cell_snapshots={name: None for name in state.cell_names},
                        encoder_provider=encoder_provider, index_provider=index_provider, stream=None)
        r._q4_controls(controls)
        gate._selection = None
        _, context, report = gate.verify_snapshot(snapshot, _return_projection=False)
        if r._freeze_json(report) != state.report:
            raise ValueError('report_changed')
        _source_check(context)
        encoder_binding = r._pinned(state.encoder_binding, state.encoder_binding_sha)
        index_binding = r._pinned(state.index_binding, state.index_binding_sha)
        r._q4_provider_identity(encoder_provider, encoder_binding, 'query')
        r._q4_provider_identity(index_provider, index_binding, 'load')

        def checkpoint():
            if (self._state is not state or self._gate is not gate or _pid() != self._pid
                    or self._encoder is not encoder_provider or self._index_provider is not index_provider):
                raise ValueError('owner_changed')
            r._q4_controls(controls)
            _, _, current = gate.verify_snapshot(snapshot, _return_projection=False)
            if r._freeze_json(current) != state.report:
                raise ValueError('report_changed')
            gate.recheck_selection(snapshot)
            r._q4_provider_identity(encoder_provider, encoder_binding, 'query')
            r._q4_provider_identity(index_provider, index_binding, 'load')

        checkpoint()
        encoder = r._CompleteCheckedQueryEncoder(encoder_provider, checkpoint)
        selected = r._q4_rank(state.records, state.entries, encoder, query['situation'], when)
        candidate = not selected
        if candidate:
            selected = r._q4_complete_semantic_candidates(state.records, state.entries, encoder, query['situation'], when)
        hits = [{'ref_sha256': r._sha(state.records[key]['provenance']['event_ref'].encode()),
                 'doc_id_sha256': r._sha(key.encode()), 'ref_kind': 'sha256_of_projected_metadata',
                 'canonical_event': False} for key in selected]
        resolved = gate.resolve(hits, query['time'], snapshot)
        checkpoint()
        gate.recheck_selection(snapshot)
        checkpoint()
        output = {'schema': 'wd.q4-content-snapshot-query.v3', 'status': 'ANSWER' if resolved else 'ABSTAIN',
            'rule': 'q4-rule-v1.2', 'results': resolved, 'binding': dict(state.output_binding), 'authority': 'none',
            'publisher_authentication': 'UNKNOWN', 'provider_authentication': 'UNKNOWN',
            'source_filter_attestation': 'UNKNOWN', 'freshness': 'UNKNOWN', 'same_user_aba': 'UNKNOWN'}
        if candidate and resolved:
            output.update(status='SEMANTIC_CANDIDATE', rule='q4-complete-semantic-candidate-v1', relevance='UNVERIFIED')
        if len(r._canonical(output)) > 1024 * 1024:
            raise ValueError('output')
        # Providers may execute code while their identity attributes are read.
        # Do not invoke them after the final selected-row and prefix checks.
        gate.recheck_selection(snapshot)
        _, context, _ = gate.verify_snapshot(snapshot, _return_projection=False)
        _source_check(context)
        return output


def _parse_query(query_bytes):
    """Reject malformed caller input without invalidating shared state."""
    if type(query_bytes) is not bytes or not 0 < len(query_bytes) <= 65536:
        raise ValueError('query')
    query = json.loads(query_bytes, object_pairs_hook=r._unique_pairs, parse_constant=r._bad_constant)
    if (type(query) is not dict or set(query) != {'situation', 'time'}
            or type(query['situation']) is not str or not query['situation'].strip()
            or len(query['situation']) > 4000):
        raise ValueError('query')
    return query, r._utc(query['time'])


def create_retained_query_owner(*, enabled=False, bundle_root=None,
                                expected_deployment_manifest_sha256=None, **inputs):
    """Create an independently anchored process-only owner; no publication."""
    def build():
        if enabled is not True or 'stream' in inputs or 'enabled' in inputs:
            raise ValueError('disabled')
        values = dict(inputs, enabled=True, stream=None)
        gate = r._NarrowReadGate(bundle_root, expected_deployment_manifest_sha256, values)
        state = _prepare_state(values, gate)
        return _RetainedQueryOwner(state, gate, values['encoder_provider'], values['index_provider'])
    output = cancellation = None
    try:
        output = _public(build)
    except BaseException as exc:
        cancellation = _clean_cancellation(exc)
    finally:
        # The public traceback must not retain the caller's policy/key tuple.
        inputs = build = None
    if cancellation is not None:
        raise cancellation from None
    if output is None:
        r._deny()
    return output
