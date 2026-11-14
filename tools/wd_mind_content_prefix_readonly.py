"""Read an anchored bridge prefix for content verification; never publish it.

The cursor, generation and expected digest must come from an independently
verified decision. This reader is not source authentication and, like the
original prefix reader, does not prevent a hostile same-user ABA race.
"""

import hashlib
import hmac
import json
import os
import re
from pathlib import Path

from tools.bridge_compact_view import event_id
from waggledance.core import bridge_log_reader as bridge

MAX_PREFIX_BYTES = 256 * 1024 * 1024
PAGE_BYTES = 4 * 1024 * 1024
MAX_SOURCE_ROWS = 4096
MAX_ROW_BYTES = 65536
MAX_PAGE_ROWS = 100000
MAX_ALL_TASK_PREFIX_BYTES = 1024 * 1024 * 1024
MAX_ALL_TASK_ROWS = 131072
MAX_ALL_TASK_RETAINED_BYTES = 256 * 1024 * 1024
MAX_ALL_TASK_ROW_BYTES = 4 * 1024 * 1024
# A coarse prefilter, not a substitute for content, identity, route or policy
# screening. Keep this set in sync with the content reader's accepted domain.
ALL_TASK_BUILD_LANES = frozenset(('codex-lead-1', 'codex-tools-1', 'fable-5'))
ALL_TASK_TYPES = frozenset(('message', 'wake_request', 'handoff', 'test', 'claim',
                            'release', 'done', 'milestone', 'status', 'progress'))


def _hash_prefix(stream, length):
    digest = hashlib.sha256()
    stream.seek(0)
    remaining = length
    while remaining:
        chunk = stream.read(min(65536, remaining))
        if not chunk:
            raise ValueError('prefix_unavailable')
        digest.update(chunk)
        remaining -= len(chunk)
    return digest


def read_prefix_rows(source_path, generation_path, end_cursor, expected_prefix_sha256, tasks, *,
                     scope='approved_tasks_v1'):
    """Return approved task rows only after the entire anchored prefix verifies.

    No rows escape on any error. Read-time hashes bind the exact bytes parsed,
    with each prefix checked again from the same handle before proceeding.
    """
    return _read_prefix(source_path, generation_path, end_cursor, expected_prefix_sha256, tasks, scope=scope)


class _PrefixProof:
    """Internal in-process evidence, never source or caller authorization."""
    __slots__ = ('binding', 'pages')

    def __init__(self, binding, pages):
        self.binding, self.pages = binding, tuple(pages)


def _proof_binding(source_path, generation_path, cursor, digest, tasks, scope):
    return (source_path, generation_path, cursor.offset, cursor.file_identity, cursor.generation, digest,
            None if tasks is None else frozenset(tasks), scope,
            (MAX_PREFIX_BYTES, PAGE_BYTES, MAX_SOURCE_ROWS, MAX_ROW_BYTES, MAX_PAGE_ROWS,
             MAX_ALL_TASK_PREFIX_BYTES, MAX_ALL_TASK_ROWS, MAX_ALL_TASK_RETAINED_BYTES,
             MAX_ALL_TASK_ROW_BYTES, ALL_TASK_BUILD_LANES, ALL_TASK_TYPES))


def _capture_prefix_proof(source_path, generation_path, end_cursor, expected_prefix_sha256, tasks, *,
                          scope='approved_tasks_v1'):
    """Run the complete existing parser/row-budget path before issuing evidence."""
    return _read_prefix(source_path, generation_path, end_cursor, expected_prefix_sha256, tasks,
                        scope=scope, capture=True)


def _recheck_prefix(proof, source_path, generation_path, end_cursor, expected_prefix_sha256, tasks, *,
                    scope='approved_tasks_v1'):
    """Skip only parsing/row retention for identical previously validated pages.

    All original reads, boundaries and pre/post hash, identity, generation,
    length and cursor checks still run in their original relative order. This
    does not make I/O linear and does not close same-user ABA races.
    """
    if type(proof) is not _PrefixProof:
        raise ValueError('invalid_prefix_proof')
    _read_prefix(source_path, generation_path, end_cursor, expected_prefix_sha256, tasks,
                 scope=scope, proof=proof)


def _read_prefix(source_path, generation_path, end_cursor, expected_prefix_sha256, tasks, *,
                 scope, capture=False, proof=None):
    if (scope not in ('approved_tasks_v1', 'all_tasks_v1')
            or (scope == 'approved_tasks_v1' and
                (type(tasks) not in (set, frozenset) or any(type(task) is not str for task in tasks)))
            or (scope == 'all_tasks_v1' and tasks is not None)):
        raise ValueError('invalid_prefix_input')
    max_prefix = MAX_PREFIX_BYTES if scope == 'approved_tasks_v1' else MAX_ALL_TASK_PREFIX_BYTES
    if (type(source_path) is not str or not os.path.isabs(source_path)
            or type(generation_path) is not str or not os.path.isabs(generation_path)
            or type(end_cursor) is not bridge.BridgeCursor or bridge._validate_cursor(end_cursor)
            or type(end_cursor.generation) is not str or not end_cursor.generation
            or type(end_cursor.offset) is not int or not 0 < end_cursor.offset <= max_prefix
            or type(expected_prefix_sha256) is not str
            or re.fullmatch(r'[0-9a-f]{64}', expected_prefix_sha256) is None):
        raise ValueError('invalid_prefix_input')

    binding = _proof_binding(source_path, generation_path, end_cursor, expected_prefix_sha256, tasks, scope)
    if proof is not None and proof.binding != binding:
        raise ValueError('prefix_proof_binding_changed')
    page_proofs = []
    path, generation = Path(source_path), Path(generation_path)
    rows, offset, pages, retained_bytes = {}, 0, 0, 0
    prior_hash = hashlib.sha256(b'').hexdigest()
    try:
        with bridge._open_log(path) as stream:
            identity = bridge._file_identity(stream)
            if identity != end_cursor.file_identity:
                raise ValueError('identity_mismatch')
            while offset < end_cursor.offset:
                pages += 1
                if pages > max_prefix // PAGE_BYTES + 2:
                    raise ValueError('page_limit')
                before_generation = bridge._read_generation(generation)
                if before_generation != end_cursor.generation:
                    raise ValueError('generation_mismatch')
                snapshot_length = os.fstat(stream.fileno()).st_size
                if snapshot_length < end_cursor.offset or bridge._file_identity(stream) != identity:
                    raise ValueError('source_changed')
                state = _hash_prefix(stream, offset)
                if not hmac.compare_digest(state.hexdigest(), prior_hash):
                    raise ValueError('prefix_changed')
                if offset:
                    stream.seek(offset - 1)
                    if stream.read(1) != b'\n':
                        raise ValueError('cursor_not_lf_boundary')
                requested = min(PAGE_BYTES, end_cursor.offset - offset)
                stream.seek(offset)
                data = stream.read(requested)
                if len(data) != requested:
                    raise ValueError('short_read')
                consumed = bridge._complete_prefix_length(data, max_rows=MAX_PAGE_ROWS)
                if consumed <= 0:
                    raise ValueError('incomplete_record')
                parsed_bytes = data[:consumed]
                if proof is None:
                    page_rows = bridge._parse_rows(parsed_bytes, starts_at_zero=(offset == 0))
                    if capture:
                        page_proofs.append((offset, requested, consumed, hashlib.sha256(parsed_bytes).hexdigest()))
                else:
                    # Do not skip the original parse position on a new page or
                    # a new boundary, even if a later whole-prefix check would
                    # reject it. Evidence was issued only after complete parse
                    # and row-limit validation of these exact bytes/controls.
                    page = (offset, requested, consumed, hashlib.sha256(parsed_bytes).hexdigest())
                    if pages > len(proof.pages) or page != proof.pages[pages - 1]:
                        raise ValueError('prefix_page_changed')
                    page_rows = ()
                state.update(parsed_bytes)
                next_offset = offset + consumed
                if not hmac.compare_digest(state.hexdigest(), _hash_prefix(stream, next_offset).hexdigest()):
                    raise ValueError('prefix_changed')
                if (bridge._file_identity(stream) != identity
                        or os.fstat(stream.fileno()).st_size < snapshot_length
                        or bridge._read_generation(generation) != before_generation):
                    raise ValueError('source_changed')
                for event in page_rows:
                    if (type(event) is not dict or type(event.get('task_id')) is not str
                            or (scope == 'approved_tasks_v1' and event['task_id'] not in tasks)
                            or (scope == 'all_tasks_v1' and
                                (type(event.get('agent')) is not str
                                 or event['agent'] not in ALL_TASK_BUILD_LANES
                                 or type(event.get('type')) is not str
                                 or event['type'] not in ALL_TASK_TYPES))):
                        continue
                    encoded = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                                         allow_nan=False).encode('utf-8')
                    if scope == 'approved_tasks_v1':
                        if len(encoded) > MAX_ROW_BYTES or len(rows) >= MAX_SOURCE_ROWS:
                            raise ValueError('row_limit')
                        rows[event_id(event)] = event
                    else:
                        ref = event_id(event)
                        if len(encoded) > MAX_ALL_TASK_ROW_BYTES:
                            raise ValueError('row_limit')
                        if ref not in rows:
                            if (len(rows) >= MAX_ALL_TASK_ROWS
                                    or retained_bytes + len(encoded) > MAX_ALL_TASK_RETAINED_BYTES):
                                raise ValueError('row_limit')
                            retained_bytes += len(encoded)
                            rows[ref] = event
                offset, prior_hash = next_offset, state.hexdigest()
            if offset != end_cursor.offset or not hmac.compare_digest(prior_hash, expected_prefix_sha256):
                raise ValueError('prefix_mismatch')
            with bridge._open_log(path) as current:
                if (bridge._file_identity(current) != end_cursor.file_identity
                        or os.fstat(current.fileno()).st_size < end_cursor.offset):
                    raise ValueError('source_changed')
            if bridge._read_generation(generation) != end_cursor.generation:
                raise ValueError('generation_mismatch')
    except (OSError, bridge._GenerationError, UnicodeError, RecursionError) as exc:
        raise ValueError('source_unavailable') from exc
    if proof is not None and pages != len(proof.pages):
        raise ValueError('prefix_page_count_changed')
    return _PrefixProof(binding, page_proofs) if capture else rows
