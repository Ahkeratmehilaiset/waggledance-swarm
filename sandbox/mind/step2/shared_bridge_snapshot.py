"""Read one caller-anchored bridge delta into a metadata-only projection.

No producer, raw-capture/state writer, network or indexer is invoked. The
reader returns parsed objects rather than original JSONL row bytes: event IDs
and row hashes are computed over the existing canonical serializations of
those objects. A caller must independently authenticate the source path, start
cursor and task policy. This is one bounded delta, not complete history or a
proof of currentness after the snapshot. The only pagination is the opt-in,
OFF-by-default ``read_public_catchup``: a bounded chain of public-status
windows from one anchored start cursor with a re-pinned prefix per window;
partial chains are reported as partial and rows are never skipped.
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
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin, BridgeReadStatus, read_bridge_log

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
                      _commitment_anchor=None, _public_tasks=None, _public_types=None,
                      expected_prefix_sha256=None, max_prefix_bytes=None, expected_snapshot_sha256=None,
                      _allow_partial=False):
    """Return an uncommitted delta; unpinned positional-policy calls are legacy only.

    ``_allow_partial`` is the private chained catch-up mode: the window may end before the snapshot end,
    and then requires an anchored public-mode read with a prefix pin and no whole-snapshot pin.
    Ordinary refusals discard row-bearing exception frames; BaseException (including
    KeyboardInterrupt) is not caught and requires isolation by the caller.
    """
    result = rows = event = snap = value = chain = prefix = None
    pins = prefix_args = kwargs = tasks = candidate = observed = checked_roster = None
    try:
        if type(_allow_partial) is not bool or (_allow_partial and (
                _commitment_anchor is None or _public_tasks is None or _public_types is None
                or expected_prefix_sha256 is None or max_prefix_bytes is None
                or expected_snapshot_sha256 is not None)):
            raise ValueError()
        pins = (policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        strict = any(value is not None for value in pins) or approved_non_review_tasks is None
        if strict:
            tasks, checked_roster = adapter.strict_pins(*pins)
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
        prefix_requested = expected_prefix_sha256 is not None or max_prefix_bytes is not None
        prefix_args = (dict(expected_prefix_sha256=expected_prefix_sha256, max_prefix_bytes=max_prefix_bytes)
                       if prefix_requested else {})
        if expected_snapshot_sha256 is not None:
            prefix_args["expected_snapshot_sha256"] = expected_snapshot_sha256
        result = read_bridge_log(path, cursor=cursor, max_bytes=max_bytes, max_rows=max_rows,
                                 generation_path=generation_path, **prefix_args)
        observed = dt.datetime.now(dt.timezone.utc).isoformat()
        candidate = result.candidate_cursor
        lead = 1 if cursor.offset else 0
        if (type(result.status) is not BridgeReadStatus or result.status not in (BridgeReadStatus.OK, BridgeReadStatus.IDLE)
                or type(candidate) is not BridgeCursor or type(result.snapshot_length) is not int
                or result.snapshot_length < 0 or type(result.requested_offset) is not int
                or result.requested_offset != cursor.offset or not isinstance(result.rows, tuple)
                or candidate.file_identity != cursor.file_identity or candidate.generation != cursor.generation
                or type(candidate.offset) is not int
                or (candidate.offset != result.snapshot_length if not _allow_partial
                    else candidate.offset > result.snapshot_length)
                or candidate.offset < cursor.offset or type(result.bytes_consumed) is not int
                or result.bytes_consumed != candidate.offset - cursor.offset
                or type(result.bytes_read) is not int
                or (result.bytes_read != result.bytes_consumed + lead if not _allow_partial
                    else not result.bytes_consumed + lead <= result.bytes_read <= max_bytes + lead)
                or result.bytes_consumed > max_bytes or len(result.rows) > max_rows
                or (result.status == BridgeReadStatus.IDLE and (result.rows or result.bytes_consumed != 0))
                or (result.status == BridgeReadStatus.OK and (not result.rows or result.bytes_consumed == 0))):
            raise ValueError()
        rows, budget = [], 0
        for index, event in enumerate(result.rows):
            budget += (adapter._public_event_budget(event, _public_tasks, checked_roster, approved_non_review_tasks)
                       if _public_tasks is not None else adapter._event_budget(event))
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
        if _allow_partial:
            kwargs["_allow_partial"] = True
        value = adapter.adapt_rows(rows, snap, approved_non_review_tasks, ttl_seconds=900, **kwargs)
        if value["source"]["unconsumed_bytes"] != snap["unconsumed_bytes"]:
            raise ValueError()
        value["source"]["coverage"] = ("delta_only_not_complete_history" if snap["unconsumed_bytes"] == 0
                                       else "partial_window_not_complete")
        value["candidate_cursor"] = candidate
        if prefix_requested:
            prefix = getattr(result, "candidate_prefix", None)
            if prefix is None or prefix.cursor != candidate:
                raise ValueError()
            value["start_prefix"] = BridgePrefixPin(cursor, expected_prefix_sha256)
            value["candidate_prefix"] = prefix
        if chain is not None:
            value["_semantic_state"] = (chain, len(result.rows))
        return value
    except Exception:
        pass
    # Do not retain raw source rows in either a chained exception or this frame.
    del result, rows, event, snap, value, chain, prefix, pins, prefix_args, kwargs, tasks, candidate, observed, checked_roster
    del path, cursor, approved_non_review_tasks, generation_path, policy_bytes, roster_bytes
    del expected_prefix_sha256, expected_snapshot_sha256, _commitment_anchor
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
    candidate_prefix: BridgePrefixPin | None = field(default=None, repr=False)
    start_prefix: BridgePrefixPin | None = field(default=None, repr=False)


def read_anchored_delta(path, cursor, tasks, *, anchor_bytes, anchor_sha256,
                        generation_path, max_bytes, max_rows,
                        policy_bytes, policy_sha256, roster_bytes, roster_sha256,
                        public_policy_bytes=None, public_policy_sha256=None,
                        expected_prefix_sha256=None, max_prefix_bytes=None, expected_snapshot_sha256=None):
    """Read using an externally authenticated anchor and retain a delta binding.

    The caller must independently authenticate anchor_bytes and its expected
    digest, source path, producer, policy and starting cursor. A matching hash
    is integrity checking only. This does not prevent same-user replacement,
    same-file log rewrites or filesystem TOCTOU; it neither publishes nor
    binds the resulting projection and cursor to an atomic index checkpoint.
    The in-memory semantic commitment includes all parsed rows in order before
    filtering, including duplicates; it is not a hash of original JSONL bytes
    and does not prove origin or correct computation by an untrusted caller.
    Do not publish it: it can expose guesses about filtered private rows. An
    optional separately trusted prefix pin detects ordinary prior rewrites;
    it does not authenticate origin or defeat concurrent same-user ABA. Prefix
    hashes can reveal guesses about private source bytes and are not public.
    """
    public_tasks = public_types = None
    try:
        public_tasks, public_types = _checked_anchor(
            path, cursor, tasks, anchor_bytes=anchor_bytes, anchor_sha256=anchor_sha256,
            generation_path=generation_path, policy_bytes=policy_bytes, policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256, public_policy_bytes=public_policy_bytes,
            public_policy_sha256=public_policy_sha256)
        return _anchored_window(path, cursor, tasks, anchor_sha256=anchor_sha256, generation_path=generation_path,
                                max_bytes=max_bytes, max_rows=max_rows, policy_bytes=policy_bytes,
                                policy_sha256=policy_sha256, roster_bytes=roster_bytes, roster_sha256=roster_sha256,
                                public_tasks=public_tasks, public_types=public_types,
                                public_policy_sha256=public_policy_sha256,
                                expected_prefix_sha256=expected_prefix_sha256, max_prefix_bytes=max_prefix_bytes,
                                expected_snapshot_sha256=expected_snapshot_sha256, allow_partial=False)
    except Exception:
        pass
    del path, cursor, tasks, anchor_bytes, generation_path, policy_bytes, roster_bytes, public_policy_bytes
    del public_tasks, public_types
    del anchor_sha256, expected_prefix_sha256, expected_snapshot_sha256
    raise SnapshotRefused("snapshot_refused") from None


def _checked_anchor(path, cursor, tasks, *, anchor_bytes, anchor_sha256, generation_path, policy_bytes,
                    policy_sha256, roster_bytes, roster_sha256, public_policy_bytes, public_policy_sha256):
    """Validate the caller-authenticated anchor against ``cursor``; returns the public policy sets or None."""
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
    return public_tasks, public_types


def _anchored_window(path, cursor, tasks, *, anchor_sha256, generation_path, max_bytes, max_rows, policy_bytes,
                     policy_sha256, roster_bytes, roster_sha256, public_tasks, public_types, public_policy_sha256,
                     expected_prefix_sha256, max_prefix_bytes, expected_snapshot_sha256, allow_partial):
    """One window read after the anchor was checked; returns the result with its in-process binding."""
    public_mode = public_tasks is not None
    snapshot_args = (dict(expected_snapshot_sha256=expected_snapshot_sha256)
                     if expected_snapshot_sha256 is not None else {})
    if allow_partial:
        snapshot_args["_allow_partial"] = True
    result = read_shared_delta(path, cursor, tasks, max_bytes=max_bytes, max_rows=max_rows,
                               generation_path=generation_path, policy_bytes=policy_bytes,
                               policy_sha256=policy_sha256, roster_bytes=roster_bytes,
                               roster_sha256=roster_sha256, _commitment_anchor=anchor_sha256,
                               _public_tasks=public_tasks, _public_types=public_types,
                               expected_prefix_sha256=expected_prefix_sha256,
                               max_prefix_bytes=max_prefix_bytes, **snapshot_args)
    chain, count = result.pop("_semantic_state")
    projection_data = _canonical(result["projection"])
    commitment = _semantic_end(chain, count, result["candidate_cursor"], projection_data)
    result["_projection_binding"] = _ProjectionBinding(
        projection_data, _canonical(result["source"]), result["candidate_cursor"],
        anchor_sha256, policy_sha256, roster_sha256, chain, count, commitment,
        "public-v1" if public_mode else "legacy", public_policy_sha256 if public_mode else None,
        result.get("candidate_prefix"), result.get("start_prefix"))
    return result


CATCHUP_SCHEMA = "wd.q4-public-catchup.v1"
CATCHUP_MAX_WINDOWS = 64
_CAUGHT_UP = "chained_delta_caught_up_to_final_snapshot_not_history"
_PARTIAL = "chained_delta_partial_not_caught_up"
_END_REASONS = frozenset({"snapshot_end", "max_windows", "partial_record", "deferred"})


@dataclass(frozen=True, slots=True)
class _CatchupBinding:
    """Accidental-splice guard for one chain, not a capability or an authenticity proof."""
    anchor_sha256: str
    policy_sha256: str
    roster_sha256: str
    public_policy_sha256: str
    start_prefix: BridgePrefixPin = field(repr=False)
    candidate_prefix: BridgePrefixPin = field(repr=False)
    window_count: int
    caught_up: bool
    end_reason: str
    chain_commitment: str = field(repr=False)
    cutoff_utc: str | None = None
    deferred: bool = False


def _cutoff(value):
    """Normalize an explicitly supplied aware clock; None leaves legacy behavior unchanged."""
    if value is None:
        return None
    if not isinstance(value, dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError()
    return value.astimezone(dt.timezone.utc).isoformat()


def _ready_projection(value, cutoff):
    """A public event is ready only strictly after its conservative availability boundary."""
    if type(value) is not dict or type(value.get("records")) is not list:
        raise ValueError()
    instant = dt.datetime.fromisoformat(cutoff)
    for record in value["records"]:
        if type(record) is not dict or type(record.get("available_utc")) is not str:
            raise ValueError()
        available = dt.datetime.fromisoformat(record["available_utc"].replace("Z", "+00:00"))
        if available.tzinfo is None or available.utcoffset() is None:
            raise ValueError()
        if available >= instant:
            return False
    return True


def _catchup_start(anchor_sha256, policy_sha256, roster_sha256, public_policy_sha256, start_prefix):
    fields = (bytes.fromhex(anchor_sha256), bytes.fromhex(policy_sha256), bytes.fromhex(roster_sha256),
              bytes.fromhex(public_policy_sha256), _canonical(asdict(start_prefix.cursor)),
              bytes.fromhex(start_prefix.sha256))
    return hashlib.sha256(b"wd.q4.public-catchup.start.v1\x00" + b"".join(_frame(x) for x in fields)).digest()


def _catchup_step(chain, index, binding, candidate_prefix):
    fields = (chain, index.to_bytes(8, "big"), bytes.fromhex(binding.semantic_commitment),
              hashlib.sha256(binding.source).digest(), _canonical(asdict(candidate_prefix.cursor)),
              bytes.fromhex(candidate_prefix.sha256))
    return hashlib.sha256(b"wd.q4.public-catchup.step.v1\x00" + b"".join(_frame(x) for x in fields)).digest()


def read_public_catchup(path, cursor, tasks, *, anchor_bytes, anchor_sha256, generation_path, max_bytes, max_rows,
                        max_windows, policy_bytes, policy_sha256, roster_bytes, roster_sha256,
                        public_policy_bytes, public_policy_sha256, expected_prefix_sha256, max_prefix_bytes,
                        enabled=False, cutoff_utc=None):
    """Opt-in bounded chained public-status catch-up over successive nonterminal windows (OFF by default).

    The caller-authenticated anchor binds the chain START cursor; it is checked once. Every window re-reads
    the byte prefix from offset zero against the previous window's pin (the first against the caller's
    independently pinned prefix), keeps the start file identity and generation, and goes through the
    unchanged public admission policy and bounded per-event/batch budgets. A structurally valid row whose
    task is outside public policy may exceed the normal event limit up to the aggregate budget; it remains
    integrity-checked and committed, but is not projected. Eligible, ambiguous, malformed or over-budget
    rows refuse the whole call: rows are never skipped. At most
    ``max_windows`` windows are read. ``caught_up`` is True only when the final window reached the end of
    the snapshot observed by that window; this is still a delta from the start cursor, not complete history
    and not currentness after the read. Otherwise the result is partial and resumable from
    ``candidate_cursor`` with ``candidate_prefix`` and a new caller-authenticated anchor for that cursor.
    Records are deduplicated per window only. Prefix digests may disclose guesses about private bytes and
    must not be published. This does not authenticate the caller, the source or the filesystem.
    With the optional aware ``cutoff_utc``, every window contains at most one source row. A projected
    public row is held until its next-minute availability is strictly before that fixed cutoff. The
    held row and everything after it are not committed; an initial hold returns zero windows and the
    original prefix. Non-public rows remain excluded by the unchanged public policy. The cutoff and
    cutoff must also be supplied/checked by ``verify_public_catchup``; the deferred status itself is
    an in-process assertion, not an independent proof about the unread row. The slower
    per-row prefix hashing is bounded by the existing window and prefix budgets. This does not prove
    source freshness, authenticate the clock, or make this catch-up object a single-window publication.
    Ordinary refusals discard exception frames; BaseException is caller-owned and may retain input data.
    """
    if enabled is not True:
        del path, cursor, tasks, anchor_bytes, generation_path, policy_bytes, roster_bytes, public_policy_bytes
        del expected_prefix_sha256, anchor_sha256, policy_sha256, roster_sha256, public_policy_sha256
        raise SnapshotRefused("catchup_disabled")
    public_tasks = public_types = start_prefix = chain = windows = current = end_reason = None
    cutoff = None
    deferred = False
    window = nxt = caught_up = last = result = None
    try:
        if (type(max_windows) is not int or not 1 <= max_windows <= CATCHUP_MAX_WINDOWS
                or type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS
                or public_policy_bytes is None or public_policy_sha256 is None
                or type(expected_prefix_sha256) is not str or _SHA256.fullmatch(expected_prefix_sha256) is None
                or max_prefix_bytes is None):
            raise ValueError()
        cutoff = _cutoff(cutoff_utc)
        public_tasks, public_types = _checked_anchor(
            path, cursor, tasks, anchor_bytes=anchor_bytes, anchor_sha256=anchor_sha256,
            generation_path=generation_path, policy_bytes=policy_bytes, policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256, public_policy_bytes=public_policy_bytes,
            public_policy_sha256=public_policy_sha256)
        if public_tasks is None:
            raise ValueError()
        start_prefix = BridgePrefixPin(cursor, expected_prefix_sha256)
        chain = _catchup_start(anchor_sha256, policy_sha256, roster_sha256, public_policy_sha256, start_prefix)
        windows, current, end_reason = [], start_prefix, "max_windows"
        for index in range(max_windows):
            window = _anchored_window(path, current.cursor, tasks, anchor_sha256=anchor_sha256,
                                      generation_path=generation_path, max_bytes=max_bytes,
                                      max_rows=1 if cutoff is not None else max_rows,
                                      policy_bytes=policy_bytes, policy_sha256=policy_sha256,
                                      roster_bytes=roster_bytes, roster_sha256=roster_sha256,
                                      public_tasks=public_tasks, public_types=public_types,
                                      public_policy_sha256=public_policy_sha256,
                                      expected_prefix_sha256=current.sha256, max_prefix_bytes=max_prefix_bytes,
                                      expected_snapshot_sha256=None, allow_partial=True)
            nxt = window["candidate_prefix"]
            if (window["start_prefix"] != current or type(nxt) is not BridgePrefixPin
                    or nxt.cursor != window["candidate_cursor"]
                    or (nxt.cursor.file_identity, nxt.cursor.generation) != (cursor.file_identity, cursor.generation)):
                raise ValueError()
            if cutoff is not None and not _ready_projection(window["projection"], cutoff):
                deferred = True
                end_reason = "deferred"
                break
            chain = _catchup_step(chain, index, window["_projection_binding"], nxt)
            windows.append(window)
            if window["source"]["unconsumed_bytes"] == 0:
                end_reason = "snapshot_end"
                break
            if nxt.cursor.offset == current.cursor.offset:
                end_reason = "partial_record"
                break
            current = nxt
        caught_up = end_reason == "snapshot_end"
        last = windows[-1] if windows else None
        candidate_prefix = last["candidate_prefix"] if last is not None else start_prefix
        result = {"schema": CATCHUP_SCHEMA, "windows": tuple(windows), "window_count": len(windows),
                  "caught_up": caught_up, "end_reason": end_reason, "coverage": _CAUGHT_UP if caught_up else _PARTIAL,
                  "start_prefix": start_prefix, "candidate_cursor": candidate_prefix.cursor,
                  "candidate_prefix": candidate_prefix}
        if cutoff is not None:
            result["cutoff_utc"] = cutoff
        result["_catchup_binding"] = _CatchupBinding(
            anchor_sha256, policy_sha256, roster_sha256, public_policy_sha256, start_prefix,
            candidate_prefix, len(windows), caught_up, end_reason, chain.hex(), cutoff, deferred)
        return result
    except Exception:
        pass
    del path, cursor, tasks, anchor_bytes, generation_path, policy_bytes, roster_bytes, public_policy_bytes
    del expected_prefix_sha256, anchor_sha256, policy_sha256, roster_sha256, public_policy_sha256
    del public_tasks, public_types, start_prefix, chain, windows, current, end_reason, window, nxt, caught_up, last, result
    raise SnapshotRefused("snapshot_refused") from None


def verify_public_catchup(result, *, expected_anchor_bytes, expected_anchor_sha256, expected_policy_sha256,
                          expected_roster_sha256, expected_public_policy_sha256, expected_start_prefix,
                          expected_cutoff_utc=None):
    """Reject accidental window splicing, reordering or relabelling of one catch-up result in this process.

    Recomputes every window's semantic commitment, the prefix/cursor linkage and the chain commitment.
    Binding objects and their caller are not authenticated; this is not an origin proof.
    """
    try:
        cutoff = _cutoff(expected_cutoff_utc)
        pins = (expected_anchor_sha256, expected_policy_sha256, expected_roster_sha256, expected_public_policy_sha256)
        if (type(result) is not dict or any(not isinstance(v, str) or _SHA256.fullmatch(v) is None for v in pins)
                or type(expected_anchor_bytes) is not bytes or len(expected_anchor_bytes) > _ANCHOR_MAX_BYTES
                or not hmac.compare_digest(hashlib.sha256(expected_anchor_bytes).hexdigest(), expected_anchor_sha256)
                or type(expected_start_prefix) is not BridgePrefixPin):
            raise ValueError()
        anchor = json.loads(expected_anchor_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        binding = result.get("_catchup_binding")
        windows = result.get("windows")
        if (type(anchor) is not dict or anchor.get("schema") != "wd.q4-source-anchor.v1"
                or anchor.get("status_mode") != "public-v1"
                or anchor.get("policy_sha256") != expected_policy_sha256
                or anchor.get("roster_sha256") != expected_roster_sha256
                or anchor.get("public_policy_sha256") != expected_public_policy_sha256
                or type(anchor.get("cursor_start")) is not dict
                or _canonical(anchor["cursor_start"]) != _canonical(asdict(expected_start_prefix.cursor))
                or type(binding) is not _CatchupBinding
                or not hmac.compare_digest(binding.anchor_sha256, expected_anchor_sha256)
                or not hmac.compare_digest(binding.policy_sha256, expected_policy_sha256)
                or not hmac.compare_digest(binding.roster_sha256, expected_roster_sha256)
                or not hmac.compare_digest(binding.public_policy_sha256, expected_public_policy_sha256)
                or binding.start_prefix != expected_start_prefix or result.get("start_prefix") != expected_start_prefix
                or result.get("schema") != CATCHUP_SCHEMA
                or type(windows) is not tuple or not 0 <= len(windows) <= CATCHUP_MAX_WINDOWS
                or (not windows and binding.end_reason != "deferred")
                or result.get("window_count") != len(windows) or binding.window_count != len(windows)
                or binding.end_reason not in _END_REASONS or result.get("end_reason") != binding.end_reason
                or binding.cutoff_utc != cutoff
                or (cutoff is None and "cutoff_utc" in result)
                or (cutoff is not None and result.get("cutoff_utc") != cutoff)
                or type(binding.deferred) is not bool
                or (binding.end_reason == "deferred") != binding.deferred
                or (binding.end_reason == "deferred" and cutoff is None)
                or type(binding.caught_up) is not bool or result.get("caught_up") is not binding.caught_up
                or binding.caught_up != (binding.end_reason == "snapshot_end")
                or result.get("coverage") != (_CAUGHT_UP if binding.caught_up else _PARTIAL)):
            raise ValueError()
        chain = _catchup_start(expected_anchor_sha256, expected_policy_sha256, expected_roster_sha256,
                               expected_public_policy_sha256, expected_start_prefix)
        current = expected_start_prefix
        for index, window in enumerate(windows):
            last = index == len(windows) - 1
            wb = window.get("_projection_binding") if type(window) is dict else None
            nxt = window.get("candidate_prefix") if type(window) is dict else None
            source = window.get("source") if type(window) is dict else None
            if (type(wb) is not _ProjectionBinding or wb.status_mode != "public-v1"
                    or wb.public_policy_sha256 != expected_public_policy_sha256
                    or not hmac.compare_digest(wb.anchor_sha256, expected_anchor_sha256)
                    or wb.policy_sha256 != expected_policy_sha256 or wb.roster_sha256 != expected_roster_sha256
                    or window.get("start_prefix") != current or wb.start_prefix != current
                    or type(nxt) is not BridgePrefixPin or wb.candidate_prefix != nxt
                    or nxt.cursor != wb.candidate or window.get("candidate_cursor") != wb.candidate
                    or (wb.candidate.file_identity, wb.candidate.generation)
                    != (current.cursor.file_identity, current.cursor.generation)
                    or wb.candidate.offset < current.cursor.offset
                    or type(wb.row_commitment) is not bytes or len(wb.row_commitment) != 32
                    or type(wb.row_count) is not int or not 0 <= wb.row_count <= MAX_ROWS
                    or (cutoff is not None and (wb.row_count > 1
                        or not _ready_projection(window.get("projection"), cutoff)))
                    or not hmac.compare_digest(wb.semantic_commitment, _semantic_end(
                        wb.row_commitment, wb.row_count, wb.candidate, wb.projection))
                    or _canonical(window.get("projection")) != wb.projection
                    or _canonical(source) != wb.source or type(source) is not dict
                    or source.get("cursor_start_sha256")
                    != hashlib.sha256(_token(current.cursor).encode("utf-8")).hexdigest()
                    or source.get("cursor_end_sha256") != hashlib.sha256(_token(wb.candidate).encode("utf-8")).hexdigest()
                    or type(source.get("unconsumed_bytes")) is not int
                    or source.get("coverage") != ("delta_only_not_complete_history" if source["unconsumed_bytes"] == 0
                                                  else "partial_window_not_complete")
                    or (not last and (source["unconsumed_bytes"] == 0 or nxt.cursor.offset == current.cursor.offset))
                    or (last and binding.end_reason == "snapshot_end" and source["unconsumed_bytes"] != 0)
                    or (last and binding.end_reason == "partial_record"
                        and (source["unconsumed_bytes"] == 0 or nxt.cursor.offset != current.cursor.offset))
                    or (last and binding.end_reason == "max_windows"
                        and (source["unconsumed_bytes"] == 0 or nxt.cursor.offset == current.cursor.offset))
                    or (last and binding.end_reason == "deferred"
                        and (source["unconsumed_bytes"] == 0 or nxt.cursor.offset == current.cursor.offset))):
                raise ValueError()
            chain = _catchup_step(chain, index, wb, nxt)
            current = nxt
        if (not hmac.compare_digest(chain.hex(), binding.chain_commitment) or binding.candidate_prefix != current
                or result.get("candidate_prefix") != current or result.get("candidate_cursor") != current.cursor):
            raise ValueError()
        return True
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
                               expected_anchor_bytes=None, expected_start_prefix=None):
    """Reject accidental snapshot/projection splicing in this process only.

    Binding objects and their caller are not authenticated. In particular this
    method does not establish that a producer or filesystem is trustworthy.
    Prefix-checked results additionally require the caller's independently
    protected prior cursor and digest; do not publish those digest values.
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
                or (binding.start_prefix is None and
                    ("start_prefix" in snapshot_result or expected_start_prefix is not None
                     or binding.candidate_prefix is not None))
                or (binding.start_prefix is not None and
                    (type(binding.start_prefix) is not BridgePrefixPin
                     or type(binding.start_prefix.sha256) is not str
                     or _SHA256.fullmatch(binding.start_prefix.sha256) is None
                     or type(snapshot_result.get("start_prefix")) is not BridgePrefixPin
                     or snapshot_result.get("start_prefix") != binding.start_prefix
                     or type(expected_start_prefix) is not BridgePrefixPin
                     or expected_start_prefix != binding.start_prefix
                     or binding.candidate_prefix is None))
                or (binding.candidate_prefix is None and "candidate_prefix" in snapshot_result)
                or (binding.candidate_prefix is not None and
                    (type(binding.candidate_prefix) is not BridgePrefixPin
                     or binding.candidate_prefix.cursor != binding.candidate
                     or type(binding.candidate_prefix.sha256) is not str
                     or _SHA256.fullmatch(binding.candidate_prefix.sha256) is None
                     or type(snapshot_result.get("candidate_prefix")) is not BridgePrefixPin
                     or snapshot_result.get("candidate_prefix") != binding.candidate_prefix))
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
