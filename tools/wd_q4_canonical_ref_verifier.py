"""Default-off, read-only mapping from public projected refs to bridge event IDs.

This verifies one minute against a *caller-anchored frozen source prefix*. The
caller independently supplies the source-prefix hash, policy/roster pins, and a
coverage receipt established outside this module. The historical argument name
``expected_snapshot_sha256`` means the hash of bytes through the fixed end cursor,
not a hash of any subsequently appended bytes. A matching hash does not prove
source authenticity, an unrotated bridge, a clock guarantee, or that late events
cannot arrive. Results are scoped to the pinned prefix and must not be represented
as global uniqueness or a review/consensus vote.

The existing bridge reader is paged with a verified prefix hash per page; the
last complete prefix must match the caller's pinned snapshot. It is not a time
index. Snapshots exceeding the bounded source, page or hashing budgets, partial
records, missing files, changed bytes, malformed rows, and unproven coverage
return UNKNOWN; a window with more than 2000 rows is refused. Only
public status events admitted by the pinned policy and roster are compared.
Outputs contain no message, payload, paths, projected text or policy contents.
Prefix hashes, cursors and coverage receipts may reveal guesses about private
source bytes: callers must keep them private and not publish them to the bridge.
Returned canonical IDs hash the entire eligible event, including unprojected
metadata such as cwd or pid, and can likewise confirm guesses about that event.
"""
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import re

from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_projection as projection
from tools.bridge_compact_view import event_id
from waggledance.core import bridge_log_reader as bridge

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_MAX_WINDOW_ROWS = 2000
_MAX_SOURCE_BYTES = 512 * 1024 * 1024
_MAX_PAGE_BYTES = 16 * 1024 * 1024
_MAX_PAGE_ROWS = 100000
_MAX_PAGES = 128
_MAX_HASH_BUDGET = 6 * 1024 * 1024 * 1024
_COVERAGE_KEYS = frozenset({'schema', 'source_path', 'generation_path', 'cursor_start', 'cursor_end',
    'prefix_sha256', 'coverage_start_utc', 'coverage_end_utc', 'record_ref', 'record_minute_utc',
    'policy_sha256', 'public_policy_sha256', 'roster_sha256'})


class VerifierRefused(ValueError):
    """Fixed error, carrying no source content or path."""


def _unknown():
    return {'status': 'UNKNOWN', 'canonical_event_refs': [], 'count': 0}


def _timestamp(text):
    if type(text) is not str:
        raise ValueError()
    value = projection.corpus_mod.parse_utc(text)
    if value is None:
        raise ValueError()
    return value


def verify(*, enabled=False, record=None, policy_bytes=None, policy_sha256=None,
           public_policy_bytes=None, public_policy_sha256=None, roster_bytes=None, roster_sha256=None,
           source_path=None, expected_snapshot_sha256=None, generation_path=None,
           coverage_receipt_bytes=None, coverage_receipt_sha256=None):
    """Resolve a public projection within an independently pinned source prefix.

    The pinned coverage receipt is an external half-open coverage claim, not
    inferred or authenticated here. Its exact file identity, sidecar generation,
    cursor end, prefix pin, projected record and policy pins must all agree.
    Results are explicitly suffixed WITHIN_RECEIPT_PREFIX and completeness is
    RECEIPT_CLAIMED, not established by this verifier; UNKNOWN means no
    conclusion. Bad policy,
    malformed input or a window above the row cap raises a fixed VerifierRefused.
    """
    if enabled is False:
        return {'status': 'OFF'}
    if enabled is not True:
        raise VerifierRefused('input_refused')
    bad_input = False
    try:
        tasks, roster = capture.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        public_tasks, public_types = capture.public_status_policy(public_policy_bytes, public_policy_sha256, tasks)
        if type(record) is not dict or type(record.get('provenance')) is not dict:
            raise ValueError()
        provenance = record['provenance']
        ref = provenance.get('event_ref')
        if (provenance.get('source_kind') != 'projected_bridge_metadata'
                or provenance.get('ref_kind') != 'sha256_of_projected_metadata'
                or provenance.get('canonical_event') is not False
                or type(ref) is not str or _HEX.fullmatch(ref) is None):
            raise ValueError()
        minute = _timestamp(record.get('time_utc'))
        if (minute.second != 0 or minute.microsecond != 0
                or projection.corpus_mod.utc_text(minute) != record['time_utc']):
            raise ValueError()
        end = minute + dt.timedelta(seconds=60)
        source_missing = (source_path is None or source_path == '' or expected_snapshot_sha256 is None
                          or expected_snapshot_sha256 == '' or generation_path is None or generation_path == ''
                          or coverage_receipt_bytes is None or coverage_receipt_sha256 is None)
        if not source_missing and (type(source_path) is not str or type(expected_snapshot_sha256) is not str
                                   or _HEX.fullmatch(expected_snapshot_sha256) is None):
            raise ValueError()
    except Exception:
        bad_input = True
    if bad_input:
        record = policy_bytes = public_policy_bytes = roster_bytes = source_path = None
        raise VerifierRefused('input_refused')
    if source_missing:
        return _unknown()
    try:
        if (type(generation_path) is not str or type(coverage_receipt_bytes) is not bytes
                or not 0 < len(coverage_receipt_bytes) <= 65536 or type(coverage_receipt_sha256) is not str
                or _HEX.fullmatch(coverage_receipt_sha256) is None
                or not hmac.compare_digest(hashlib.sha256(coverage_receipt_bytes).hexdigest(), coverage_receipt_sha256)):
            raise ValueError()
        receipt = json.loads(coverage_receipt_bytes.decode('utf-8'), object_pairs_hook=capture._strict_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (type(receipt) is not dict or set(receipt) != _COVERAGE_KEYS
                or receipt['schema'] != 'wd.q4-canonical-coverage.v1'
                or type(receipt['source_path']) is not str or type(receipt['generation_path']) is not str
                or os.path.normcase(os.path.abspath(receipt['source_path'])) != os.path.normcase(os.path.abspath(source_path))
                or os.path.normcase(os.path.abspath(receipt['generation_path'])) != os.path.normcase(os.path.abspath(generation_path))
                or receipt['record_ref'] != ref or receipt['record_minute_utc'] != record['time_utc']
                or receipt['policy_sha256'] != policy_sha256 or receipt['roster_sha256'] != roster_sha256
                or receipt['public_policy_sha256'] != public_policy_sha256
                or receipt['prefix_sha256'] != expected_snapshot_sha256
                or _timestamp(receipt['coverage_start_utc']) > minute or _timestamp(receipt['coverage_end_utc']) < end):
            raise ValueError()
        start, finish = receipt['cursor_start'], receipt['cursor_end']
        if (type(start) is not dict or type(finish) is not dict
                or set(start) != {'offset', 'file_identity', 'generation'} or set(finish) != set(start)
                or type(start['offset']) is not int or start['offset'] != 0
                or type(finish['offset']) is not int or not 0 < finish['offset'] <= _MAX_SOURCE_BYTES
                or type(start['generation']) is not str or not start['generation']
                or start['generation'] != finish['generation'] or start['file_identity'] != finish['file_identity']):
            raise ValueError()
        cursor = bridge.BridgeCursor(**start)
        final_cursor = bridge.BridgeCursor(**finish)
        if bridge._validate_cursor(cursor) or bridge._validate_cursor(final_cursor):
            raise ValueError()
    except Exception:
        return _unknown()
    try:
        result = _scan_prefix(source_path, generation_path, cursor, final_cursor,
                              expected_snapshot_sha256, minute, end, ref, tasks, roster, public_tasks, public_types)
    finally:
        record = receipt = coverage_receipt_bytes = policy_bytes = public_policy_bytes = roster_bytes = source_path = None
    if result['status'] == 'UNKNOWN':
        return result
    result['status'] += '_WITHIN_RECEIPT_PREFIX'
    result['completeness'] = 'RECEIPT_CLAIMED'
    return result


def _scan_prefix(source_path, generation_path, cursor, final_cursor, expected_snapshot_sha256,
                 minute, end, ref, tasks, roster, public_tasks, public_types):
    overflow = False
    invalid = False
    refs = []
    event = read = None
    try:
        fixed_length = final_cursor.offset
        prefix_sha = hashlib.sha256(b'').hexdigest()
        window_count = 0
        pages = hash_budget = 0
        while True:
            pages += 1
            # Initial prefix hash, page hash extension and final prefix hash.
            hash_budget += 2 * min(fixed_length, cursor.offset + _MAX_PAGE_BYTES)
            if pages > _MAX_PAGES or hash_budget > _MAX_HASH_BUDGET:
                invalid = True
                break
            read = bridge.read_bridge_log(source_path, cursor=cursor,
                max_bytes=min(_MAX_PAGE_BYTES, fixed_length - cursor.offset), max_rows=_MAX_PAGE_ROWS,
                generation_path=generation_path, expected_prefix_sha256=prefix_sha, max_prefix_bytes=_MAX_SOURCE_BYTES)
            if (read.status not in (bridge.BridgeReadStatus.OK, bridge.BridgeReadStatus.IDLE)
                    or read.snapshot_length is None or read.snapshot_length < fixed_length or read.candidate_cursor is None
                    or not isinstance(read, bridge.BridgePrefixReadResult) or read.candidate_prefix is None):
                invalid = True
                break
            for event in read.rows:
                when = _timestamp(event.get('ts_utc'))
                if not minute <= when < end:
                    continue
                window_count += 1
                if window_count > _MAX_WINDOW_ROWS:
                    overflow = True
                    break
                capture._event_budget(event)
                if not capture._public_status_event(event, public_tasks, public_types):
                    continue
                agent = event.get('agent')
                if type(agent) is not str or agent not in roster or event.get('agent_uuid') != roster[agent]:
                    invalid = True
                    break
                if capture._exclude_rco_event(event) or capture._exclude_review_signal(event):
                    continue
                fields, _ = projection.approved_fields(event, tasks, include_refs=False, roster=roster,
                                                        exclude_review_signal=True)
                if projection.projected_ref(fields) == ref:
                    refs.append(event_id(event))
            if overflow or invalid:
                break
            next_cursor = read.candidate_cursor
            next_sha = read.candidate_prefix.sha256
            if next_cursor.offset == fixed_length:
                if (next_cursor != final_cursor or not hmac.compare_digest(next_sha, expected_snapshot_sha256)):
                    invalid = True
                with bridge._open_log(Path(source_path)) as stream:
                    if (bridge._file_identity(stream) != final_cursor.file_identity
                            or os.fstat(stream.fileno()).st_size < fixed_length):
                        invalid = True
                if bridge._read_generation(Path(generation_path)) != final_cursor.generation:
                    invalid = True
                break
            if next_cursor.offset <= cursor.offset:
                invalid = True
                break
            cursor, prefix_sha = next_cursor, next_sha
    except Exception:
        invalid = True
    finally:
        event = read = source_path = generation_path = None
    if overflow:
        raise VerifierRefused('window_too_large')
    if invalid:
        return _unknown()
    # An identical event serialized twice has the same canonical event ID.
    # The raw-row window limit above is still checked before deduplication.
    refs = sorted(set(refs))
    return {'status': 'NONE' if not refs else 'MATCH' if len(refs) == 1 else 'AMBIGUOUS',
            'canonical_event_refs': refs, 'count': len(refs)}


def verify_within_prefix(*, enabled=False, record=None, policy_bytes=None, policy_sha256=None,
                         public_policy_bytes=None, public_policy_sha256=None, roster_bytes=None, roster_sha256=None,
                         source_path=None, generation_path=None, end_cursor=None, expected_prefix_sha256=None):
    """Show that a public projected match exists within one caller-pinned prefix.

    This does not use or create an interval-coverage receipt, prove which source
    produced an answer, authenticate a writer, or assert uniqueness or absence
    outside the pinned prefix. No match or unavailable evidence returns UNKNOWN;
    every enabled result explicitly marks completeness UNKNOWN. The caller must
    independently protect the cursor, prefix digest and policy pins.
    """
    if enabled is False:
        return {'status': 'OFF'}
    if enabled is not True:
        raise VerifierRefused('input_refused')
    bad_input = False
    try:
        tasks, roster = capture.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        public_tasks, public_types = capture.public_status_policy(public_policy_bytes, public_policy_sha256, tasks)
        if type(record) is not dict or type(record.get('provenance')) is not dict:
            raise ValueError()
        provenance = record['provenance']
        ref = provenance.get('event_ref')
        if (provenance.get('source_kind') != 'projected_bridge_metadata'
                or provenance.get('ref_kind') != 'sha256_of_projected_metadata'
                or provenance.get('canonical_event') is not False
                or type(ref) is not str or _HEX.fullmatch(ref) is None):
            raise ValueError()
        minute = _timestamp(record.get('time_utc'))
        if (minute.second != 0 or minute.microsecond != 0
                or projection.corpus_mod.utc_text(minute) != record['time_utc']):
            raise ValueError()
        end = minute + dt.timedelta(seconds=60)
    except Exception:
        bad_input = True
    if bad_input:
        record = policy_bytes = public_policy_bytes = roster_bytes = source_path = None
        raise VerifierRefused('input_refused')
    unknown = {'status': 'UNKNOWN', 'canonical_event_refs': [], 'count': 0, 'completeness': 'UNKNOWN'}
    if (type(source_path) is not str or not os.path.isabs(source_path)
            or type(generation_path) is not str or not os.path.isabs(generation_path)
            or type(expected_prefix_sha256) is not str or _HEX.fullmatch(expected_prefix_sha256) is None
            or type(end_cursor) is not bridge.BridgeCursor or bridge._validate_cursor(end_cursor)
            or type(end_cursor.generation) is not str or not end_cursor.generation
            or not 0 < end_cursor.offset <= _MAX_SOURCE_BYTES):
        return unknown
    start = bridge.BridgeCursor(0, end_cursor.file_identity, end_cursor.generation)
    try:
        result = _scan_prefix(source_path, generation_path, start, end_cursor, expected_prefix_sha256,
                              minute, end, ref, tasks, roster, public_tasks, public_types)
    finally:
        record = policy_bytes = public_policy_bytes = roster_bytes = source_path = None
    if result['status'] not in ('MATCH', 'AMBIGUOUS'):
        return unknown
    return {'status': 'MATCH_WITHIN_PREFIX' if result['status'] == 'MATCH' else 'AMBIGUOUS_WITHIN_PREFIX',
            'canonical_event_refs': result['canonical_event_refs'], 'count': result['count'],
            'completeness': 'UNKNOWN'}
