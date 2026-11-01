"""Default-off, bounded comparison of a protected log prefix to an observed end.

The caller must authenticate and protect the log path, generation document and
the watermark (including its prefix digest), and bind that watermark to the
same verified snapshot used by the consumer. The reader is not a lease: an
append, path replacement or same-user ABA may occur after or during observation.
This is not source authentication, a clock, continuous freshness, a launcher,
or a production connection. Never expose the private watermark or log rows.
Each observation rehashes the protected prefix and candidate; identity, generation
and offset alone are not a safe cache key. Cost grows with prefix length and the
caller must bound that work. Incomplete suffixes and exceeded budgets are UNKNOWN.
"""
import os
from pathlib import Path
import re
import stat

from waggledance.core.bridge_log_reader import (
    BridgeCursor, BridgePrefixPin, BridgePrefixReadResult, BridgeReadStatus,
    MAX_PREFIX_BYTES, MAX_MAX_BYTES, MAX_MAX_ROWS, read_bridge_log,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _local_path(value):
    if (type(value) is not str or not value or not os.path.isabs(value)
            or value.startswith(("\\\\", "//")) or ".." in Path(value).parts
            or (os.name == "nt" and ":" in value[2:])):
        return False
    path = Path(value)
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            return False
    return True


def check_snapshot_position(*, log_path=None, generation_path=None, watermark=None,
                            max_prefix_bytes=None, max_lag_bytes=0, enabled=False):
    """Return a closed observation; caller owns all trust and time guarantees."""
    if enabled is False:
        return {"status": "OFF"}
    try:
        if (enabled is not True or type(watermark) is not BridgePrefixPin
                or type(watermark.cursor) is not BridgeCursor
                or type(watermark.cursor.offset) is not int or watermark.cursor.offset < 0
                or type(watermark.cursor.file_identity) is not str or not watermark.cursor.file_identity
                or type(watermark.cursor.generation) is not str or not watermark.cursor.generation
                or type(watermark.sha256) is not str or _SHA256.fullmatch(watermark.sha256) is None
                or type(max_prefix_bytes) is not int
                or not watermark.cursor.offset <= max_prefix_bytes <= MAX_PREFIX_BYTES
                or type(max_lag_bytes) is not int or not 0 <= max_lag_bytes <= MAX_MAX_BYTES
                or not _local_path(log_path) or not _local_path(generation_path)):
            raise ValueError()
        observed = read_bridge_log(log_path, cursor=watermark.cursor, generation_path=generation_path,
                                   expected_prefix_sha256=watermark.sha256,
                                   max_prefix_bytes=max_prefix_bytes, max_bytes=max(1, max_lag_bytes),
                                   max_rows=MAX_MAX_ROWS)
        if (type(observed) is not BridgePrefixReadResult
                or observed.status not in (BridgeReadStatus.IDLE, BridgeReadStatus.OK)
                or type(observed.snapshot_length) is not int
                or type(observed.candidate_cursor) is not BridgeCursor
                or type(observed.candidate_prefix) is not BridgePrefixPin):
            raise ValueError()
        lag = observed.snapshot_length - watermark.cursor.offset
        candidate = observed.candidate_cursor
        if (not 0 <= lag <= max_lag_bytes or observed.bytes_consumed != lag
                or candidate.offset != observed.snapshot_length
                or candidate.file_identity != watermark.cursor.file_identity
                or candidate.generation != watermark.cursor.generation
                or observed.candidate_prefix.cursor != candidate
                or (lag == 0 and observed.candidate_prefix != watermark)):
            raise ValueError()
        return {"status": "BOUNDED_BY_LOG_POSITION", "lag_bytes": lag,
                "source_authentication": "UNKNOWN", "freshness": "UNKNOWN"}
    except Exception:
        pass
    return {"status": "UNKNOWN"}
