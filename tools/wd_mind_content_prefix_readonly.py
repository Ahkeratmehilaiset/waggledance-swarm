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
MAX_COMPLETE_ROW_BYTES = 4 * 1024 * 1024


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


def read_complete_prefix_rows(source_path, generation_path, end_cursor, expected_prefix_sha256):
    """Verify the complete frozen prefix and return its rows, with no task or row-count cap.

    Pages are parsed and hashed once, then the complete prefix is independently
    rehashed before any result escapes. The result is currently held in memory:
    resource exhaustion is an error, not a truncated or successful projection.
    As with the existing reader, the supplied anchor is not sender authentication
    and same-user ABA modifications are outside this function's guarantees.
    """
    if (type(source_path) is not str or not os.path.isabs(source_path)
            or type(generation_path) is not str or not os.path.isabs(generation_path)
            or type(end_cursor) is not bridge.BridgeCursor or bridge._validate_cursor(end_cursor)
            or type(end_cursor.generation) is not str or not end_cursor.generation
            or type(end_cursor.offset) is not int or end_cursor.offset <= 0
            or type(expected_prefix_sha256) is not str
            or re.fullmatch(r'[0-9a-f]{64}', expected_prefix_sha256) is None):
        raise ValueError('invalid_prefix_input')
    path, generation = Path(source_path), Path(generation_path)
    rows, offset, digest = {}, 0, hashlib.sha256()
    try:
        with bridge._open_log(path) as stream:
            identity = bridge._file_identity(stream)
            if identity != end_cursor.file_identity:
                raise ValueError('identity_mismatch')
            while offset < end_cursor.offset:
                if bridge._read_generation(generation) != end_cursor.generation:
                    raise ValueError('generation_mismatch')
                snapshot_length = os.fstat(stream.fileno()).st_size
                if bridge._file_identity(stream) != identity or snapshot_length < end_cursor.offset:
                    raise ValueError('source_changed')
                requested = min(PAGE_BYTES, end_cursor.offset - offset)
                stream.seek(offset)
                data = stream.read(requested)
                if len(data) != requested:
                    raise ValueError('short_read')
                consumed = bridge._complete_prefix_length(data, max_rows=MAX_PAGE_ROWS)
                if consumed <= 0:
                    raise ValueError('incomplete_record')
                parsed_bytes = data[:consumed]
                page_rows = bridge._parse_rows(parsed_bytes, starts_at_zero=(offset == 0))
                if (bridge._file_identity(stream) != identity
                        or os.fstat(stream.fileno()).st_size < snapshot_length
                        or bridge._read_generation(generation) != end_cursor.generation):
                    raise ValueError('source_changed')
                digest.update(parsed_bytes)
                for event in page_rows:
                    encoded = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                                         allow_nan=False).encode('utf-8')
                    if len(encoded) > MAX_COMPLETE_ROW_BYTES:
                        raise ValueError('row_limit')
                    rows[event_id(event)] = event
                offset += consumed
            if (offset != end_cursor.offset or not hmac.compare_digest(digest.hexdigest(), expected_prefix_sha256)
                    or not hmac.compare_digest(_hash_prefix(stream, offset).hexdigest(), expected_prefix_sha256)):
                raise ValueError('prefix_mismatch')
            if (bridge._file_identity(stream) != identity or os.fstat(stream.fileno()).st_size < offset
                    or bridge._read_generation(generation) != end_cursor.generation):
                raise ValueError('source_changed')
            with bridge._open_log(path) as current:
                if bridge._file_identity(current) != identity or os.fstat(current.fileno()).st_size < offset:
                    raise ValueError('source_changed')
            if bridge._read_generation(generation) != end_cursor.generation:
                raise ValueError('generation_mismatch')
    except (OSError, bridge._GenerationError, UnicodeError, RecursionError, MemoryError) as exc:
        raise ValueError('source_unavailable') from exc
    return rows


def read_prefix_rows(source_path, generation_path, end_cursor, expected_prefix_sha256, tasks, *,
                     scope='approved_tasks_v1'):
    """Return approved task rows only after the entire anchored prefix verifies.

    No rows escape on any error. Read-time hashes bind the exact bytes parsed,
    with each prefix checked again from the same handle before proceeding.
    """
    if scope == 'all_tasks_v1':
        if tasks is not None:
            raise ValueError('invalid_prefix_input')
        return read_complete_prefix_rows(source_path, generation_path, end_cursor, expected_prefix_sha256)
    if scope != 'approved_tasks_v1':
        raise ValueError('invalid_prefix_input')
    if (type(source_path) is not str or not os.path.isabs(source_path)
            or type(generation_path) is not str or not os.path.isabs(generation_path)
            or type(end_cursor) is not bridge.BridgeCursor or bridge._validate_cursor(end_cursor)
            or type(end_cursor.generation) is not str or not end_cursor.generation
            or type(end_cursor.offset) is not int or not 0 < end_cursor.offset <= MAX_PREFIX_BYTES
            or type(expected_prefix_sha256) is not str
            or re.fullmatch(r'[0-9a-f]{64}', expected_prefix_sha256) is None
            or type(tasks) not in (set, frozenset) or any(type(task) is not str for task in tasks)):
        raise ValueError('invalid_prefix_input')

    path, generation = Path(source_path), Path(generation_path)
    rows, offset, pages = {}, 0, 0
    prior_hash = hashlib.sha256(b'').hexdigest()
    try:
        with bridge._open_log(path) as stream:
            identity = bridge._file_identity(stream)
            if identity != end_cursor.file_identity:
                raise ValueError('identity_mismatch')
            while offset < end_cursor.offset:
                pages += 1
                if pages > MAX_PREFIX_BYTES // PAGE_BYTES + 2:
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
                page_rows = bridge._parse_rows(parsed_bytes, starts_at_zero=(offset == 0))
                state.update(parsed_bytes)
                next_offset = offset + consumed
                if not hmac.compare_digest(state.hexdigest(), _hash_prefix(stream, next_offset).hexdigest()):
                    raise ValueError('prefix_changed')
                if (bridge._file_identity(stream) != identity
                        or os.fstat(stream.fileno()).st_size < snapshot_length
                        or bridge._read_generation(generation) != before_generation):
                    raise ValueError('source_changed')
                for event in page_rows:
                    if type(event) is not dict or type(event.get('task_id')) is not str or event['task_id'] not in tasks:
                        continue
                    encoded = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                                         allow_nan=False).encode('utf-8')
                    if len(encoded) > MAX_ROW_BYTES or len(rows) >= MAX_SOURCE_ROWS:
                        raise ValueError('row_limit')
                    rows[event_id(event)] = event
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
    return rows
