"""Fixed-size segment digests for one append-only source prefix. Authority: none.

This module only computes and compares SHA-256 digests of byte ranges that the
caller has already fenced (file identity, minimum length, generation). It
authenticates neither the source nor its writers and makes no claim about
same-user replacement between two checks.

One streaming pass yields the whole-prefix digest and one digest per segment, so
a ledger can be bound to an already pinned whole-prefix digest without a new
trust root. Later checks re-hash only chosen segments, and appended bytes extend
a ledger by re-reading the last partial segment and the new bytes. Memory use is
one read block plus 32 bytes per segment, independent of the prefix length.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from array import array

SCHEMA = 'wd.mind-source-segments.v1'
MIN_SEGMENT_BYTES = 4096
MAX_SEGMENT_BYTES = 64 * 1024 * 1024
MAX_SEGMENTS = 1024 * 1024
MAX_LEDGER_BYTES = 80 * 1024 * 1024
READ_BLOCK_BYTES = 1024 * 1024
REASONS = frozenset({'input', 'limit', 'short_read', 'read_type', 'prefix_mismatch', 'segment_mismatch',
                     'not_extension', 'ledger_format'})
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_DIGEST = hashlib.sha256().digest_size
_KEYS = frozenset({'schema', 'authority', 'segment_bytes', 'end', 'prefix_sha256', 'root_sha256', 'digests'})


class SourceSegmentsRefused(ValueError):
    """Fail-closed refusal. ``reason`` is one fixed token and never carries source data."""

    def __init__(self, reason):
        self.reason = reason if reason in REASONS else 'input'
        super().__init__('mind_source_segments_refused')


def _refuse(reason):
    raise SourceSegmentsRefused(reason) from None


def _geometry(end, segment_bytes):
    if (type(end) is not int or end <= 0 or type(segment_bytes) is not int
            or not MIN_SEGMENT_BYTES <= segment_bytes <= MAX_SEGMENT_BYTES
            or segment_bytes & (segment_bytes - 1)):
        _refuse('input')
    count = (end + segment_bytes - 1) // segment_bytes
    if count > MAX_SEGMENTS:
        _refuse('limit')
    return count


def _feed(stream, start, length, read, *hashers):
    """Read ``[start, start + length)`` in bounded blocks into every hasher."""
    stream.seek(start)
    remaining = length
    while remaining:
        size = min(READ_BLOCK_BYTES, remaining)
        block = stream.read(size) if read is None else read(stream, size)
        if type(block) is not bytes or len(block) > size:
            _refuse('read_type')
        if not block:
            _refuse('short_read')
        for hasher in hashers:
            hasher.update(block)
        remaining -= len(block)


class SegmentLedger:
    """Digests of the consecutive fixed-size segments of the prefix ``[0, end)``.

    ``prefix_sha256`` is the whole-prefix digest when a full pass or an in-process
    extension produced it, otherwise ``None``. An instance is never changed after
    creation; ``extend`` returns a new one.
    """

    __slots__ = ('_segment_bytes', '_end', '_digests', '_prefix_sha256', '_hasher')

    def __init__(self, segment_bytes, end, digests, prefix_sha256, hasher=None):
        count = _geometry(end, segment_bytes)
        if (type(digests) is not bytes or len(digests) != count * _DIGEST
                or not (prefix_sha256 is None
                        or (type(prefix_sha256) is str and _HEX.fullmatch(prefix_sha256) is not None))
                or (hasher is not None and prefix_sha256 is None)):
            _refuse('input')
        self._segment_bytes, self._end, self._digests = segment_bytes, end, digests
        self._prefix_sha256, self._hasher = prefix_sha256, hasher

    segment_bytes = property(lambda self: self._segment_bytes)
    end = property(lambda self: self._end)
    prefix_sha256 = property(lambda self: self._prefix_sha256)
    count = property(lambda self: len(self._digests) // _DIGEST)

    def span(self, index):
        """Byte range ``(start, length)`` of one segment inside the prefix."""
        if type(index) is not int or not 0 <= index < self.count:
            _refuse('input')
        start = index * self._segment_bytes
        return start, min(self._segment_bytes, self._end - start)

    def digest(self, index):
        self.span(index)
        return self._digests[index * _DIGEST:(index + 1) * _DIGEST]

    def root_sha256(self):
        """One digest over the geometry and every segment digest."""
        head = json.dumps([SCHEMA, self._segment_bytes, self._end], separators=(',', ':')).encode('ascii')
        return hashlib.sha256(head + bytes(1) + self._digests).hexdigest()


def build(stream, end, *, segment_bytes, expected_prefix_sha256=None, read=None):
    """One streaming pass over ``[0, end)``: whole-prefix digest and one digest per segment.

    With ``expected_prefix_sha256`` the ledger is returned only when that same pass
    reproduces the pinned whole-prefix digest. ``read(stream, size)`` may replace
    the plain read, for example to charge a caller's byte allowance.
    """
    count = _geometry(end, segment_bytes)
    if expected_prefix_sha256 is not None and (type(expected_prefix_sha256) is not str
                                               or _HEX.fullmatch(expected_prefix_sha256) is None):
        _refuse('input')
    whole, digests = hashlib.sha256(), bytearray()
    for index in range(count):
        start, part = index * segment_bytes, hashlib.sha256()
        _feed(stream, start, min(segment_bytes, end - start), read, part, whole)
        digests += part.digest()
    prefix = whole.hexdigest()
    if expected_prefix_sha256 is not None and not hmac.compare_digest(prefix, expected_prefix_sha256):
        _refuse('prefix_mismatch')
    return SegmentLedger(segment_bytes, end, bytes(digests), prefix, whole)


def extend(stream, ledger, new_end, *, read=None):
    """Extend to ``new_end`` by reading only the last partial segment and the appended bytes.

    The old part of a partial last segment must still match its recorded digest.
    The whole-prefix digest continues only from live in-process hash state; a
    ledger restored by ``load`` yields ``prefix_sha256 is None`` after extension.
    """
    if type(ledger) is not SegmentLedger or type(new_end) is not int or new_end < ledger.end:
        _refuse('not_extension')
    if new_end == ledger.end:
        return ledger
    size, old_end = ledger.segment_bytes, ledger.end
    count = _geometry(new_end, size)
    whole = None if ledger._hasher is None else ledger._hasher.copy()
    tail = () if whole is None else (whole,)
    kept = old_end // size
    digests = bytearray(ledger._digests[:kept * _DIGEST])
    if old_end % size:
        start, part = kept * size, hashlib.sha256()
        _feed(stream, start, old_end - start, read, part)
        if not hmac.compare_digest(part.digest(), ledger.digest(kept)):
            _refuse('segment_mismatch')
        _feed(stream, old_end, min(start + size, new_end) - old_end, read, part, *tail)
        digests += part.digest()
        kept += 1
    for index in range(kept, count):
        start, part = index * size, hashlib.sha256()
        _feed(stream, start, min(size, new_end - start), read, part, *tail)
        digests += part.digest()
    return SegmentLedger(size, new_end, bytes(digests), None if whole is None else whole.hexdigest(), whole)


def indexes_for(ledger, offset, length):
    """Segments that contain the byte range ``[offset, offset + length)``."""
    if (type(ledger) is not SegmentLedger or type(offset) is not int or type(length) is not int
            or offset < 0 or length <= 0 or offset + length > ledger.end):
        _refuse('input')
    return range(offset // ledger.segment_bytes, (offset + length - 1) // ledger.segment_bytes + 1)


def verify(stream, ledger, indexes, *, read=None):
    """Re-hash the chosen segments against the ledger; return the number of bytes hashed."""
    if type(ledger) is not SegmentLedger:
        _refuse('input')
    try:
        chosen = sorted(set(indexes))
    except TypeError:
        _refuse('input')
    hashed = 0
    for index in chosen:
        start, length = ledger.span(index)
        part = hashlib.sha256()
        _feed(stream, start, length, read, part)
        if not hmac.compare_digest(part.digest(), ledger.digest(index)):
            _refuse('segment_mismatch')
        hashed += length
    return hashed


def dump(ledger):
    """Canonical JSON bytes. Live hash state is never written."""
    if type(ledger) is not SegmentLedger:
        _refuse('input')
    return json.dumps({'schema': SCHEMA, 'authority': 'none', 'segment_bytes': ledger.segment_bytes,
                       'end': ledger.end, 'prefix_sha256': ledger.prefix_sha256,
                       'root_sha256': ledger.root_sha256(), 'digests': ledger._digests.hex()},
                      sort_keys=True, separators=(',', ':')).encode('ascii')


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate_key')
        value[key] = item
    return value


def load(raw, *, expected_sha256=None):
    """Parse ``dump`` output strictly; only the exact canonical bytes are accepted."""
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_LEDGER_BYTES:
            raise ValueError('raw')
        if expected_sha256 is not None and (
                type(expected_sha256) is not str or _HEX.fullmatch(expected_sha256) is None
                or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)):
            raise ValueError('pin')
        value = json.loads(raw.decode('ascii'), object_pairs_hook=_unique)
        if (type(value) is not dict or set(value) != _KEYS or value['schema'] != SCHEMA
                or value['authority'] != 'none' or type(value['digests']) is not str):
            raise ValueError('shape')
        ledger = SegmentLedger(value['segment_bytes'], value['end'], bytes.fromhex(value['digests']),
                               value['prefix_sha256'])
        if not hmac.compare_digest(dump(ledger), raw):
            raise ValueError('canonical')
    except Exception:
        _refuse('ledger_format')
    return ledger


class Rotation:
    """When each segment was last verified, in caller-supplied monotonic seconds."""

    __slots__ = ('_checked', '_cursor')

    def __init__(self, ledger, now):
        if type(ledger) is not SegmentLedger or not _clock(now):
            _refuse('input')
        self._checked, self._cursor = array('d', [now]) * ledger.count, 0

    def plan(self, ledger, byte_allowance):
        """Next segments in turn whose total length fits the allowance; never fewer than one."""
        if (type(ledger) is not SegmentLedger or ledger.count != len(self._checked)
                or type(byte_allowance) is not int or byte_allowance < 0):
            _refuse('input')
        chosen, total, index = [], 0, self._cursor
        while len(chosen) < ledger.count:
            length = ledger.span(index)[1]
            if chosen and total + length > byte_allowance:
                break
            chosen.append(index)
            total += length
            index = (index + 1) % ledger.count
        return chosen

    def mark(self, indexes, now):
        """Record a successful verification of ``indexes`` at ``now``."""
        try:
            chosen = set(indexes)
        except TypeError:
            _refuse('input')
        if not _clock(now) or any(type(index) is not int or not 0 <= index < len(self._checked)
                                  or now < self._checked[index] for index in chosen):
            _refuse('input')
        for index in chosen:
            self._checked[index] = now
        for _ in range(len(self._checked)):
            if self._cursor not in chosen:
                break
            self._cursor = (self._cursor + 1) % len(self._checked)

    def oldest_age(self, now):
        """Seconds since the least recently verified segment was checked."""
        oldest = min(self._checked)
        if not _clock(now) or now < oldest:
            _refuse('input')
        return now - oldest

    def resized(self, old, new, now):
        """Rotation for an extended ledger: untouched complete segments keep their times."""
        if (type(old) is not SegmentLedger or type(new) is not SegmentLedger or old.count != len(self._checked)
                or new.segment_bytes != old.segment_bytes or new.end < old.end or not _clock(now)):
            _refuse('input')
        kept = old.count if new.end == old.end else old.end // old.segment_bytes
        result = Rotation(new, now)
        result._checked[:kept] = self._checked[:kept]
        result._cursor = self._cursor if self._cursor < new.count else 0
        return result


def _clock(now):
    return type(now) in (int, float) and math.isfinite(now)
