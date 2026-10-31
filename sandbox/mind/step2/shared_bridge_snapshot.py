"""Read one caller-anchored bridge delta into a metadata-only projection.

No producer, raw-capture/state writer, network or indexer is invoked. The
reader returns parsed objects rather than original JSONL row bytes: event IDs
and row hashes are computed over the existing canonical serializations of
those objects. A caller must independently authenticate the source path, start
cursor and task policy. This is one bounded delta, not complete history or a
proof of currentness after the snapshot; no pagination is implemented.
"""

import base64
import datetime as dt
import json
from dataclasses import asdict

from sandbox.mind import mind_capture_producer as producer
from sandbox.mind.step2 import shared_capture_adapter as adapter
from tools.bridge_compact_view import event_id
from waggledance.core.bridge_log_reader import BridgeCursor, BridgeReadStatus, read_bridge_log

MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 10000


class SnapshotRefused(ValueError):
    """Fixed failure; no source path or row contents."""


def _token(cursor):
    raw = json.dumps(asdict(cursor), sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return "bc1." + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def read_shared_delta(path, cursor, approved_non_review_tasks, *, max_bytes, max_rows, generation_path=None):
    """Return projection, caller-asserted source metadata and an uncommitted candidate cursor."""
    try:
        if (type(cursor) is not BridgeCursor or type(cursor.offset) is not int or cursor.offset < 0
                or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES
                or type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS
                or type(approved_non_review_tasks) is not frozenset):
            raise ValueError()
        result = read_bridge_log(path, cursor=cursor, max_bytes=max_bytes, max_rows=max_rows,
                                 generation_path=generation_path)
        observed = dt.datetime.now(dt.timezone.utc).isoformat()
        candidate = result.candidate_cursor
        if (type(result.status) is not BridgeReadStatus or result.status not in (BridgeReadStatus.OK, BridgeReadStatus.IDLE)
                or type(candidate) is not BridgeCursor or type(result.snapshot_length) is not int
                or result.snapshot_length < 0 or type(result.requested_offset) is not int
                or result.requested_offset != cursor.offset or not isinstance(result.rows, tuple)
                or candidate.file_identity != cursor.file_identity or candidate.generation != cursor.generation
                or type(candidate.offset) is not int or candidate.offset != result.snapshot_length
                or candidate.offset < cursor.offset or type(result.bytes_consumed) is not int
                or result.bytes_consumed != candidate.offset - cursor.offset
                or type(result.bytes_read) is not int
                or result.bytes_read != result.bytes_consumed + (1 if cursor.offset else 0)
                or result.bytes_consumed > max_bytes or len(result.rows) > max_rows
                or (result.status == BridgeReadStatus.IDLE and (result.rows or result.bytes_consumed != 0))
                or (result.status == BridgeReadStatus.OK and (not result.rows or result.bytes_consumed == 0))):
            raise ValueError()
        rows, budget = [], 0
        for event in result.rows:
            budget += adapter._event_budget(event)
            if budget > adapter._BATCH_LIMIT:
                raise ValueError()
            rows.append({"event": event, "ref": event_id(event), "row_sha256": producer.row_digest(event),
                         "source": "detail"})
        snap = {"file_identity": cursor.file_identity, "cursor_start": _token(cursor),
                "cursor_end": _token(candidate), "generation": cursor.generation,
                "snapshot_length": result.snapshot_length, "position": candidate.offset,
                "unconsumed_bytes": result.snapshot_length - candidate.offset, "observed_utc": observed}
        value = adapter.adapt_rows(rows, snap, approved_non_review_tasks, ttl_seconds=900)
        value["source"]["coverage"] = "delta_only_not_complete_history"
        value["candidate_cursor"] = candidate
        return value
    except Exception:
        raise SnapshotRefused("snapshot_refused") from None
