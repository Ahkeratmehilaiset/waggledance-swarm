"""Default-off cooperative cutoff lease; not a scheduler or writer authentication.

An owner lock spans the caller's bounded runner invocation. The existing private
and checkpoint locks are held only during reservation and completion. A crash or
uncertain write leaves a durable pending reservation and requires external
reconciliation; a missing OS lock or a receipt alone never clears it. Old writers
that ignore this protocol, same-user rollback/TOCTOU, power-loss durability and
process-tree cleanup remain outside this component's proof.
"""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets

from sandbox.mind.step2 import prefix_private_state as private_state
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2.shared_bridge_snapshot import _token
from sandbox.mind.step2.reader_slot import reader_slot
from tools import wd_q4_status_prefix_runner as runner


class WatermarkRefused(ValueError):
    """No pending reservation was created."""


class WatermarkUnknown(RuntimeError):
    """An abandoned reservation or uncertain write needs reconciliation."""


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _pin(value):
    return type(value) is str and runner._HEX.fullmatch(value) is not None


def _load_head(private, expected):
    path = private / 'q4-cutoff-current.json'
    if not os.path.lexists(path):
        if expected is not None:
            raise ValueError()
        return None
    if not _pin(expected):
        raise ValueError()
    head = runner._json(checkpoint._read(checkpoint._secure(path), max_bytes=4096), 4096)
    if head != {'schema': 'wd.q4-cutoff-head.v1', 'record_sha256': expected}:
        raise ValueError()
    record = checkpoint._read(checkpoint._secure(private / ('q4-cutoff-record-' + expected + '.json')), max_bytes=8192)
    if _sha(record) != expected:
        raise ValueError()
    doc = runner._json(record, 8192)
    if (set(doc) != {'schema', 'previous_record_sha256', 'cutoff_utc', 'attempt', 'route_sha256',
                     'runner_receipt_sha256', 'pointer_sha256'} or doc['schema'] != 'wd.q4-cutoff-record.v1'):
        raise ValueError()
    if ((doc['previous_record_sha256'] is not None and not _pin(doc['previous_record_sha256']))
            or any(not _pin(doc[key]) for key in ('attempt', 'route_sha256', 'runner_receipt_sha256', 'pointer_sha256'))):
        raise ValueError()
    runner._cutoff_datetime(doc['cutoff_utc'])
    return doc


def _current(state, shared, pointer, route=None):
    if checkpoint._active_fence(shared) is not None or private_state._shared_pointer(shared) != pointer:
        raise ValueError()
    prefix = state.load(pointer)
    private_state._require_candidate_binding(shared, pointer, prefix, current=pointer)
    if route is not None and (route.get('cursor') != asdict(prefix.cursor) or route.get('prefix_sha256') != prefix.sha256):
        raise ValueError()
    return prefix


class _Lease:
    def __init__(self, private, shared, route, route_sha, pointer, previous, reservation, pending, pending_info):
        self._private, self._shared, self._route = private, shared, route
        self._route_sha256, self._pointer, self._previous = route_sha, pointer, previous
        self._reservation, self._pending, self._pending_info = dict(reservation), pending, pending_info
        self._active = True

    @property
    def reservation(self):
        """The runner gets a copy; mutating it cannot retarget this lease."""
        return dict(self._reservation)

    @property
    def route_sha256(self):
        return self._route_sha256

    def complete(self, *, receipt_path, expected_receipt_sha256):
        if not self._active:
            raise WatermarkUnknown('cutoff_watermark_unknown')
        try:
            if not _pin(expected_receipt_sha256):
                raise ValueError()
            marker = _raw({'schema': 'wd.q4-runner-reservation.v1', **self._reservation})
            marker_sha = _sha(marker)
            path = self._private / ('q4-status-result-' + marker_sha + '.json')
            if Path(receipt_path) != path:
                raise ValueError()
            data = checkpoint._read(checkpoint._secure(path), max_bytes=65536)
            receipt = runner._json(data, 65536)
            receipt_keys = {'schema', 'marker_token_sha256', 'marker_identities', 'root_identities', 'route_sha256',
                            'plan_sha256', 'prior_pointer_sha256', 'pointer_sha256', 'generation', 'status',
                            'watermark_sha256', 'prefix_sha256'}
            expected_roots = json.loads(_raw([runner.roots._observe(self._shared), runner.roots._observe(self._private)]))
            if (set(receipt) != receipt_keys or type(receipt['root_identities']) is not list
                    or len(receipt['root_identities']) != 5 or receipt['root_identities'][:2] != expected_roots
                    or type(receipt['marker_identities']) is not list or len(receipt['marker_identities']) != 2
                    or _sha(data) != expected_receipt_sha256 or receipt.get('schema') != 'wd.q4-status-result.v1'
                    or receipt.get('marker_token_sha256') != marker_sha or receipt.get('route_sha256') != self.route_sha256
                    or receipt.get('plan_sha256') != self._route['plan_sha256']
                    or receipt.get('prior_pointer_sha256') != self._pointer
                    or receipt.get('status') not in ('PUBLISHED_HISTORY', 'EMPTY_ADVANCED', 'EMPTY_NO_CHANGE')
                    or not _pin(receipt.get('pointer_sha256'))):
                raise ValueError()
            if ((receipt['status'] == 'EMPTY_NO_CHANGE') != (receipt['pointer_sha256'] == self._pointer)):
                raise ValueError()
            with private_state.locked_state(self._private, self._shared, enabled=True) as state:
                with private_state._checkpoint_lock(self._shared, 0.2):
                    runner._verify_own(self._private / 'q4-cutoff.pending.json', self._pending, self._pending_info)
                    if any(os.path.lexists(root / runner._PENDING) for root in (self._private, self._shared)):
                        raise ValueError()
                    prefix = _current(state, self._shared, receipt['pointer_sha256'])
                    pointer_doc = checkpoint._parse(checkpoint._read(
                        checkpoint._secure(self._shared / checkpoint.POINTER), max_bytes=checkpoint._MAX_POINTER_BYTES))
                    if (prefix.sha256 != receipt.get('prefix_sha256')
                            or receipt['generation'] != pointer_doc.get('generation')
                            or receipt['watermark_sha256'] != _sha(_token(prefix.cursor).encode('utf-8'))):
                        raise ValueError()
                    _load_head(self._private, self._previous)
                    if checkpoint._read(checkpoint._secure(path), max_bytes=65536) != data:
                        raise ValueError()
                    record = _raw({'schema': 'wd.q4-cutoff-record.v1', 'previous_record_sha256': self._previous,
                                   'cutoff_utc': self._route['cutoff_utc'], 'attempt': self._reservation['attempt'],
                                   'route_sha256': self.route_sha256, 'runner_receipt_sha256': expected_receipt_sha256,
                                   'pointer_sha256': receipt['pointer_sha256']})
                    pin = _sha(record)
                    record_path = self._private / ('q4-cutoff-record-' + pin + '.json')
                    info = runner._create(record_path, record)
                    runner._verify_own(record_path, record, info)
                    head = _raw({'schema': 'wd.q4-cutoff-head.v1', 'record_sha256': pin})
                    stage = self._private / ('.q4-cutoff-head-' + secrets.token_hex(16) + '.tmp')
                    stage_info = runner._create(stage, head)
                    runner._verify_own(stage, head, stage_info)
                    os.replace(stage, self._private / 'q4-cutoff-current.json')
                    if checkpoint._read(checkpoint._secure(self._private / 'q4-cutoff-current.json'), max_bytes=4096) != head:
                        raise ValueError()
                    if checkpoint._read(checkpoint._secure(path), max_bytes=65536) != data:
                        raise ValueError()
                    runner._verify_own(self._private / 'q4-cutoff.pending.json', self._pending, self._pending_info)
                    os.unlink(self._private / 'q4-cutoff.pending.json')
                    self._active = False
                    return pin
        except Exception:
            self._active = False
            raise WatermarkUnknown('cutoff_watermark_unknown') from None


@contextmanager
def reserve(*, enabled=False, private_root=None, shared_root=None, route_bytes=None,
            expected_route_sha256=None, expected_pointer_sha256=None, expected_previous_record_sha256=None):
    if enabled is False:
        yield None
        return
    try:
        if (enabled is not True or not _pin(expected_route_sha256) or not _pin(expected_pointer_sha256)
                or _sha(route_bytes) != expected_route_sha256):
            raise ValueError()
        route = runner._json(route_bytes, 65536)
        if (route.get('schema') != 'wd.q4-status-prefix-route.v1'
                or set(route) not in (runner._ROUTE_KEYS | {'cutoff_utc'}, runner._ROUTE_KEYS | {'cutoff_utc', 'batch_rows'})
                or not _pin(route.get('plan_sha256')) or ('batch_rows' in route and route['batch_rows'] is not True)):
            raise ValueError()
        cutoff = runner._cutoff_datetime(route.get('cutoff_utc'))
        private = runner.roots._root(str(private_root))
        shared = runner.roots._root(str(shared_root))
        runner.roots._observe(private)
        runner.roots._observe(shared)
        if Path(route['private_state_root']) != private or Path(route['checkpoint_root']) != shared:
            raise ValueError()
    except Exception:
        raise WatermarkRefused('cutoff_watermark_refused') from None
    lease = None
    try:
        owner_lock = checkpoint._secure(private / 'q4-cutoff-owner.lock', missing_leaf=True)
        with reader_slot(owner_lock, timeout_seconds=0.2):
            checkpoint._secure(owner_lock)
            with private_state.locked_state(private, shared, enabled=True) as state:
                with private_state._checkpoint_lock(shared, 0.2):
                    if os.path.lexists(private / 'q4-cutoff.pending.json'):
                        raise WatermarkUnknown('cutoff_watermark_unknown')
                    previous = _load_head(private, expected_previous_record_sha256)
                    if previous is not None and cutoff < runner._cutoff_datetime(previous['cutoff_utc']):
                        raise WatermarkRefused('cutoff_watermark_refused')
                    _current(state, shared, expected_pointer_sha256, route)
                    reservation = {'attempt': secrets.token_hex(32), 'token': secrets.token_hex(32)}
                    pending = _raw({'schema': 'wd.q4-cutoff-pending.v1', 'reservation': reservation,
                                    'route_sha256': expected_route_sha256, 'cutoff_utc': route['cutoff_utc'],
                                    'previous_record_sha256': expected_previous_record_sha256,
                                    'pointer_sha256': expected_pointer_sha256})
                    try:
                        info = runner._create(private / 'q4-cutoff.pending.json', pending)
                        runner._verify_own(private / 'q4-cutoff.pending.json', pending, info)
                    except Exception:
                        raise WatermarkUnknown('cutoff_watermark_unknown') from None
                    lease = _Lease(private, shared, route, expected_route_sha256, expected_pointer_sha256,
                                   expected_previous_record_sha256, reservation, pending, info)
            try:
                yield lease
            finally:
                lease._active = False
    except (WatermarkUnknown, WatermarkRefused):
        raise
    except Exception:
        if lease is not None or os.path.lexists(private / 'q4-cutoff.pending.json'):
            raise WatermarkUnknown('cutoff_watermark_unknown') from None
        raise WatermarkRefused('cutoff_watermark_refused') from None
