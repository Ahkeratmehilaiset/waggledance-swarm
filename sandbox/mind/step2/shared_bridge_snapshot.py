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
import hashlib
import hmac
import json
import os
import re
from dataclasses import asdict, dataclass, field

from sandbox.mind import mind_capture_producer as producer
from sandbox.mind.step2 import shared_capture_adapter as adapter
from tools.bridge_compact_view import event_id
from waggledance.core.bridge_log_reader import BridgeCursor, BridgeReadStatus, read_bridge_log

MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 10000
_ANCHOR_MAX_BYTES = 16384
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class SnapshotRefused(ValueError):
    """Fixed failure; no source path or row contents."""


def _token(cursor):
    raw = json.dumps(asdict(cursor), sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return "bc1." + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def read_shared_delta(path, cursor, approved_non_review_tasks=None, *, max_bytes, max_rows, generation_path=None,
                      policy_bytes=None, policy_sha256=None, roster_bytes=None, roster_sha256=None,
                      _commitment_anchor=None, _public_tasks=None, _public_types=None):
    """Return an uncommitted delta; unpinned positional-policy calls are legacy only."""
    try:
        pins = (policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        strict = any(value is not None for value in pins) or approved_non_review_tasks is None
        if strict:
            tasks, _ = adapter.strict_pins(*pins)
            if approved_non_review_tasks is not None and approved_non_review_tasks != tasks:
                raise ValueError()
            approved_non_review_tasks = tasks
        if _commitment_anchor is not None:
            if (not strict or not isinstance(_commitment_anchor, str)
                    or _SHA256.fullmatch(_commitment_anchor) is None):
                raise ValueError()
            chain = _semantic_start(cursor, _commitment_anchor, policy_sha256, roster_sha256)
        else:
            chain = None
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
        for index, event in enumerate(result.rows):
            budget += adapter._event_budget(event)
            if budget > adapter._BATCH_LIMIT:
                raise ValueError()
            ref = event_id(event)
            if chain is not None:
                chain = _semantic_row(chain, index, ref)
            rows.append({"event": event, "ref": ref, "row_sha256": producer.row_digest(event),
                         "source": "detail"})
        snap = {"file_identity": cursor.file_identity, "cursor_start": _token(cursor),
                "cursor_end": _token(candidate), "generation": cursor.generation,
                "snapshot_length": result.snapshot_length, "position": candidate.offset,
                "unconsumed_bytes": result.snapshot_length - candidate.offset, "observed_utc": observed}
        kwargs = (dict(policy_bytes=policy_bytes, policy_sha256=policy_sha256,
                       roster_bytes=roster_bytes, roster_sha256=roster_sha256) if strict else {})
        if _public_tasks is not None or _public_types is not None:
            if _commitment_anchor is None:
                raise ValueError()
            kwargs.update(_public_tasks=_public_tasks, _public_types=_public_types)
        value = adapter.adapt_rows(rows, snap, approved_non_review_tasks, ttl_seconds=900, **kwargs)
        value["source"]["coverage"] = "delta_only_not_complete_history"
        value["candidate_cursor"] = candidate
        if chain is not None:
            value["_semantic_state"] = (chain, len(result.rows))
        return value
    except Exception:
        raise SnapshotRefused("snapshot_refused") from None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _frame(value):
    return len(value).to_bytes(8, "big") + value


def _semantic_start(cursor, anchor_sha256, policy_sha256, roster_sha256):
    if type(cursor) is not BridgeCursor:
        raise ValueError()
    fields = (_canonical(asdict(cursor)), bytes.fromhex(anchor_sha256),
              bytes.fromhex(policy_sha256), bytes.fromhex(roster_sha256))
    return hashlib.sha256(b"wd.q4.semantic-delta.start.v1\x00" + b"".join(_frame(x) for x in fields)).digest()


def _semantic_row(chain, index, ref):
    return hashlib.sha256(b"wd.q4.semantic-delta.row.v1\x00" + _frame(chain)
                          + index.to_bytes(8, "big") + _frame(bytes.fromhex(ref))).digest()


def _semantic_end(chain, row_count, cursor, projection_bytes):
    parts = (chain, row_count.to_bytes(8, "big"), _canonical(asdict(cursor)),
             hashlib.sha256(projection_bytes).digest())
    return hashlib.sha256(b"wd.q4.semantic-delta.end.v1\x00" + b"".join(_frame(x) for x in parts)).hexdigest()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _absolute_path(value):
    value = os.fspath(value)
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError()
    return os.path.normcase(os.path.normpath(value))


@dataclass(frozen=True, slots=True)
class _ProjectionBinding:
    """Accidental-mixup guard, not a capability or an authenticity proof."""
    projection: bytes
    source: bytes
    candidate: BridgeCursor
    anchor_sha256: str
    policy_sha256: str
    roster_sha256: str
    row_commitment: bytes = field(repr=False)
    row_count: int = field(repr=False)
    semantic_commitment: str = field(repr=False)
    status_mode: str = "legacy"
    public_policy_sha256: str | None = None


def read_anchored_delta(path, cursor, tasks, *, anchor_bytes, anchor_sha256,
                        generation_path, max_bytes, max_rows,
                        policy_bytes, policy_sha256, roster_bytes, roster_sha256,
                        public_policy_bytes=None, public_policy_sha256=None):
    """Read using an externally authenticated anchor and retain a delta binding.

    The caller must independently authenticate anchor_bytes and its expected
    digest, source path, producer, policy and starting cursor. A matching hash
    is integrity checking only. This does not prevent same-user replacement,
    same-file log rewrites or filesystem TOCTOU; it neither publishes nor
    binds the resulting projection and cursor to an atomic index checkpoint.
    The in-memory semantic commitment includes all parsed rows in order before
    filtering, including duplicates; it is not a hash of original JSONL bytes
    and does not prove origin or correct computation by an untrusted caller.
    Do not publish it: it can expose guesses about filtered private rows.
    """
    try:
        if (type(anchor_bytes) is not bytes or len(anchor_bytes) > _ANCHOR_MAX_BYTES
                or not isinstance(anchor_sha256, str) or _SHA256.fullmatch(anchor_sha256) is None
                or not hmac.compare_digest(hashlib.sha256(anchor_bytes).hexdigest(), anchor_sha256)
                or type(cursor) is not BridgeCursor or cursor.generation is None
                or type(tasks) is not frozenset or generation_path is None):
            raise ValueError()
        parsed_tasks, _ = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        if tasks != parsed_tasks:
            raise ValueError()
        public_mode = public_policy_bytes is not None or public_policy_sha256 is not None
        if public_mode:
            public_tasks, public_types = adapter.public_status_policy(public_policy_bytes, public_policy_sha256, tasks)
        else:
            public_tasks = public_types = None
        anchor = json.loads(anchor_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        expected_keys = {"schema", "source_path", "generation_path", "cursor_start", "policy_sha256", "roster_sha256"}
        if public_mode:
            expected_keys |= {"status_mode", "public_policy_sha256"}
        if (type(anchor) is not dict
                or set(anchor) != expected_keys
                or anchor["schema"] != "wd.q4-source-anchor.v1"
                or (public_mode and (anchor["status_mode"] != "public-v1"
                                     or anchor["public_policy_sha256"] != public_policy_sha256))
                or _absolute_path(anchor["source_path"]) != _absolute_path(path)
                or _absolute_path(anchor["generation_path"]) != _absolute_path(generation_path)
                or type(anchor["cursor_start"]) is not dict
                or _canonical(anchor["cursor_start"]) != _canonical(asdict(cursor))
                or not isinstance(anchor["policy_sha256"], str)
                or not hmac.compare_digest(anchor["policy_sha256"], policy_sha256)
                or not isinstance(anchor["roster_sha256"], str)
                or not hmac.compare_digest(anchor["roster_sha256"], roster_sha256)):
            raise ValueError()
        result = read_shared_delta(path, cursor, tasks, max_bytes=max_bytes, max_rows=max_rows,
                                   generation_path=generation_path, policy_bytes=policy_bytes,
                                   policy_sha256=policy_sha256, roster_bytes=roster_bytes,
                                   roster_sha256=roster_sha256, _commitment_anchor=anchor_sha256,
                                   _public_tasks=public_tasks, _public_types=public_types)
        chain, count = result.pop("_semantic_state")
        projection_data = _canonical(result["projection"])
        commitment = _semantic_end(chain, count, result["candidate_cursor"], projection_data)
        result["_projection_binding"] = _ProjectionBinding(
            projection_data, _canonical(result["source"]), result["candidate_cursor"],
            anchor_sha256, policy_sha256, roster_sha256, chain, count, commitment,
            "public-v1" if public_mode else "legacy", public_policy_sha256 if public_mode else None)
        return result
    except Exception:
        raise SnapshotRefused("snapshot_refused") from None


def verify_public_anchor(snapshot_result, anchor_bytes, *, expected_anchor_sha256, expected_policy_sha256,
                         expected_roster_sha256, expected_public_policy_sha256):
    """Recheck caller-anchored public-policy bytes and cursor linkage in-process.

    This prevents accidental legacy-mode splicing; it does not authenticate
    the caller or defend against a hostile actor in this Python process.
    """
    try:
        if (type(anchor_bytes) is not bytes or len(anchor_bytes) > _ANCHOR_MAX_BYTES
                or not isinstance(expected_anchor_sha256, str) or _SHA256.fullmatch(expected_anchor_sha256) is None
                or not hmac.compare_digest(hashlib.sha256(anchor_bytes).hexdigest(), expected_anchor_sha256)):
            raise ValueError()
        anchor = json.loads(anchor_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (type(anchor) is not dict or set(anchor) != {"schema", "source_path", "generation_path", "cursor_start",
                "policy_sha256", "roster_sha256", "status_mode", "public_policy_sha256"}
                or anchor["schema"] != "wd.q4-source-anchor.v1" or anchor["status_mode"] != "public-v1"
                or anchor["policy_sha256"] != expected_policy_sha256
                or anchor["roster_sha256"] != expected_roster_sha256
                or anchor["public_policy_sha256"] != expected_public_policy_sha256
                or type(anchor["cursor_start"]) is not dict):
            raise ValueError()
        _absolute_path(anchor["source_path"])
        _absolute_path(anchor["generation_path"])
        start = BridgeCursor(**anchor["cursor_start"])
        end = snapshot_result["candidate_cursor"]
        source = snapshot_result["source"]
        if (type(start.offset) is not int or start.offset < 0 or not isinstance(start.file_identity, str)
                or not isinstance(start.generation, str) or type(end) is not BridgeCursor
                or type(end.offset) is not int or end.offset < start.offset
                or end.file_identity != start.file_identity or end.generation != start.generation
                or type(source) is not dict
                or source.get("cursor_start_sha256") != hashlib.sha256(_token(start).encode("utf-8")).hexdigest()
                or source.get("cursor_end_sha256") != hashlib.sha256(_token(end).encode("utf-8")).hexdigest()):
            raise ValueError()
        return True
    except Exception:
        raise SnapshotRefused("snapshot_refused") from None


def verify_anchored_projection(snapshot_result, projection, *, expected_anchor_sha256,
                               expected_policy_sha256, expected_roster_sha256,
                               expected_mode="legacy", expected_public_policy_sha256=None,
                               expected_anchor_bytes=None):
    """Reject accidental snapshot/projection splicing in this process only.

    Binding objects and their caller are not authenticated. In particular this
    method does not establish that a producer or filesystem is trustworthy.
    """
    try:
        if type(snapshot_result) is not dict:
            raise ValueError()
        if expected_mode == "public-v1":
            verify_public_anchor(snapshot_result, expected_anchor_bytes, expected_anchor_sha256=expected_anchor_sha256,
                expected_policy_sha256=expected_policy_sha256, expected_roster_sha256=expected_roster_sha256,
                expected_public_policy_sha256=expected_public_policy_sha256)
        binding = snapshot_result.get("_projection_binding")
        if (type(binding) is not _ProjectionBinding or type(projection) is not dict
                or expected_mode not in ("legacy", "public-v1")
                or binding.status_mode != expected_mode
                or (expected_mode == "legacy" and expected_public_policy_sha256 is not None)
                or (expected_mode == "public-v1" and (not isinstance(expected_public_policy_sha256, str)
                    or _SHA256.fullmatch(expected_public_policy_sha256) is None))
                or binding.public_policy_sha256 != expected_public_policy_sha256
                or any(not isinstance(value, str) or _SHA256.fullmatch(value) is None
                       for value in (expected_anchor_sha256, expected_policy_sha256, expected_roster_sha256))
                or not hmac.compare_digest(binding.anchor_sha256, expected_anchor_sha256)
                or not hmac.compare_digest(binding.policy_sha256, expected_policy_sha256)
                or not hmac.compare_digest(binding.roster_sha256, expected_roster_sha256)
                or type(binding.row_commitment) is not bytes or len(binding.row_commitment) != 32
                or type(binding.row_count) is not int or not 0 <= binding.row_count <= MAX_ROWS
                or not isinstance(binding.semantic_commitment, str)
                or _SHA256.fullmatch(binding.semantic_commitment) is None
                or not hmac.compare_digest(binding.semantic_commitment,
                         _semantic_end(binding.row_commitment, binding.row_count,
                                       binding.candidate, binding.projection))
                or type(snapshot_result.get("candidate_cursor")) is not BridgeCursor
                or snapshot_result["candidate_cursor"] != binding.candidate
                or _canonical(snapshot_result.get("source")) != binding.source
                or _canonical(snapshot_result.get("projection")) != binding.projection
                or _canonical(projection) != binding.projection):
            raise ValueError()
        return True
    except Exception:
        raise SnapshotRefused("snapshot_refused") from None
