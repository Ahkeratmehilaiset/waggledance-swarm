"""Synthetic tests for the fixed-size segment ledger of an append-only source prefix."""
from __future__ import annotations

import contextlib
import hashlib
import json

import pytest

from tools import wd_mind_source_segments as segments

SEG = segments.MIN_SEGMENT_BYTES


def _data(size: int, seed: bytes = b"synthetic") -> bytes:
    return hashlib.shake_256(seed).digest(size)


def _source(tmp_path, data: bytes):
    path = tmp_path / "events.jsonl"
    path.write_bytes(data)
    return path


def _expected(data: bytes, size: int = SEG) -> bytes:
    return b"".join(hashlib.sha256(data[start:start + size]).digest() for start in range(0, len(data), size))


@contextlib.contextmanager
def _refused(reason: str):
    with pytest.raises(segments.SourceSegmentsRefused) as caught:
        yield
    assert caught.value.reason == reason and reason in segments.REASONS
    assert str(caught.value) == "mind_source_segments_refused"


class _Counting:
    def __init__(self):
        self.sizes, self.total = [], 0

    def __call__(self, stream, size):
        block = stream.read(size)
        self.sizes.append(size)
        self.total += len(block)
        return block


@pytest.mark.parametrize("size", [1, SEG - 1, SEG, SEG + 1, 3 * SEG, 3 * SEG + 17])
def test_build_gives_the_whole_prefix_hash_and_one_hash_per_segment(tmp_path, size):
    data = _data(size)
    with _source(tmp_path, data + b"later bytes are not part of the prefix").open("rb") as stream:
        ledger = segments.build(stream, size, segment_bytes=SEG,
                                expected_prefix_sha256=hashlib.sha256(data).hexdigest())
    assert ledger.end == size and ledger.segment_bytes == SEG
    assert ledger.count == -(-size // SEG)
    assert ledger.prefix_sha256 == hashlib.sha256(data).hexdigest()
    assert b"".join(ledger.digest(index) for index in range(ledger.count)) == _expected(data)
    assert sum(ledger.span(index)[1] for index in range(ledger.count)) == size


def test_build_refuses_when_the_pinned_prefix_hash_is_not_reproduced(tmp_path):
    data = _data(2 * SEG + 5)
    with _source(tmp_path, data).open("rb") as stream, _refused("prefix_mismatch"):
        segments.build(stream, len(data), segment_bytes=SEG,
                       expected_prefix_sha256=hashlib.sha256(data[:-1] + b"x").hexdigest())


def test_build_reads_bounded_blocks_and_every_byte_once(tmp_path):
    size = 2 * segments.READ_BLOCK_BYTES + 3 * SEG + 1
    data, counting = _data(size), _Counting()
    with _source(tmp_path, data).open("rb") as stream:
        ledger = segments.build(stream, size, segment_bytes=4 * segments.READ_BLOCK_BYTES, read=counting)
    assert counting.total == size and max(counting.sizes) <= segments.READ_BLOCK_BYTES
    assert ledger.count == 1 and ledger.digest(0) == hashlib.sha256(data).digest()


def test_build_refuses_a_source_shorter_than_the_prefix(tmp_path):
    with _source(tmp_path, _data(SEG)).open("rb") as stream, _refused("short_read"):
        segments.build(stream, SEG + 1, segment_bytes=SEG)


@pytest.mark.parametrize("read", [lambda stream, size: bytearray(stream.read(size)),
                                  lambda stream, size: stream.read(size) + b"x", lambda stream, size: None])
def test_a_read_hook_returning_the_wrong_type_or_too_much_is_refused(tmp_path, read):
    with _source(tmp_path, _data(SEG)).open("rb") as stream, _refused("read_type"):
        segments.build(stream, SEG, segment_bytes=SEG, read=read)


@pytest.mark.parametrize("end, size", [(0, SEG), (-1, SEG), (True, SEG), (1.0, SEG), (SEG, SEG - 1), (SEG, 3 * SEG),
                                       (SEG, 2 * segments.MAX_SEGMENT_BYTES), (SEG, float(SEG)), (SEG, True)])
def test_invalid_geometry_is_refused_before_any_read(end, size):
    with _refused("input"):
        segments.build(None, end, segment_bytes=size)


def test_too_many_segments_is_refused_before_any_read():
    with _refused("limit"):
        segments.build(None, segments.MAX_SEGMENTS * SEG + 1, segment_bytes=SEG)
    with _refused("input"):
        segments.build(None, SEG, segment_bytes=SEG, expected_prefix_sha256="A" * 64)


def test_verify_finds_one_changed_byte_in_its_own_segment_only(tmp_path):
    data = _data(5 * SEG + 100)
    path = _source(tmp_path, data)
    with path.open("rb") as stream:
        ledger = segments.build(stream, len(data), segment_bytes=SEG)
        assert segments.verify(stream, ledger, range(ledger.count)) == len(data)
    changed = bytearray(data)
    changed[3 * SEG + 7] ^= 1
    path.write_bytes(bytes(changed))
    with path.open("rb") as stream:
        assert segments.verify(stream, ledger, [0, 1, 2, 4, 5, 5, 0]) == 4 * SEG + 100
        with _refused("segment_mismatch"):
            segments.verify(stream, ledger, [3])
        with _refused("segment_mismatch"):
            segments.verify(stream, ledger, segments.indexes_for(ledger, 3 * SEG + 7, 1))


def test_verify_refuses_a_truncated_source_and_bad_indexes(tmp_path):
    data = _data(2 * SEG)
    path = _source(tmp_path, data)
    with path.open("rb") as stream:
        ledger = segments.build(stream, len(data), segment_bytes=SEG)
        for bad in ([2], [-1], [True], [0.0], ["0"], [[0]], 7):
            with _refused("input"):
                segments.verify(stream, ledger, bad)
        with _refused("input"):
            segments.verify(stream, object(), [0])
    path.write_bytes(data[:SEG + 1])
    with path.open("rb") as stream, _refused("short_read"):
        segments.verify(stream, ledger, [1])


def test_indexes_for_follows_segment_edges(tmp_path):
    data = _data(3 * SEG + 10)
    with _source(tmp_path, data).open("rb") as stream:
        ledger = segments.build(stream, len(data), segment_bytes=SEG)
    assert list(segments.indexes_for(ledger, 0, 1)) == [0]
    assert list(segments.indexes_for(ledger, SEG - 1, 1)) == [0]
    assert list(segments.indexes_for(ledger, SEG - 1, 2)) == [0, 1]
    assert list(segments.indexes_for(ledger, SEG, SEG)) == [1]
    assert list(segments.indexes_for(ledger, 0, len(data))) == [0, 1, 2, 3]
    assert list(segments.indexes_for(ledger, 3 * SEG + 9, 1)) == [3]
    for offset, length in ((-1, 1), (0, 0), (0, len(data) + 1), (len(data), 1), (0.0, 1), (0, True)):
        with _refused("input"):
            segments.indexes_for(ledger, offset, length)


@pytest.mark.parametrize("old, new", [(1, 2), (SEG - 1, SEG), (SEG, SEG + 1), (SEG + 5, SEG + 6), (SEG + 5, 2 * SEG),
                                      (SEG + 5, 4 * SEG + 9), (2 * SEG, 5 * SEG), (3 * SEG + 1, 3 * SEG + 1)])
def test_extend_equals_a_full_rebuild_and_reads_only_the_tail(tmp_path, old, new):
    data, counting = _data(new), _Counting()
    with _source(tmp_path, data).open("rb") as stream:
        first = segments.build(stream, old, segment_bytes=SEG)
        before = segments.dump(first)
        grown = segments.extend(stream, first, new, read=counting)
        rebuilt = segments.build(stream, new, segment_bytes=SEG)
    assert segments.dump(grown) == segments.dump(rebuilt)
    assert grown.prefix_sha256 == hashlib.sha256(data).hexdigest()
    assert counting.total == (0 if new == old else old % SEG + new - old)
    assert segments.dump(first) == before and first.prefix_sha256 == hashlib.sha256(data[:old]).hexdigest()


def test_extend_can_be_chained_and_repeated_from_the_same_ledger(tmp_path):
    data = _data(6 * SEG + 3)
    with _source(tmp_path, data).open("rb") as stream:
        first = segments.build(stream, SEG + 1, segment_bytes=SEG)
        second = segments.extend(stream, first, 2 * SEG + 2)
        third = segments.extend(stream, second, len(data))
        again = segments.extend(stream, first, len(data))
        rebuilt = segments.build(stream, len(data), segment_bytes=SEG)
    assert segments.dump(third) == segments.dump(again) == segments.dump(rebuilt)


def test_extend_refuses_when_the_old_partial_segment_changed_or_the_prefix_shrinks(tmp_path):
    data = _data(3 * SEG)
    path = _source(tmp_path, data)
    with path.open("rb") as stream:
        ledger = segments.build(stream, SEG + 10, segment_bytes=SEG)
        with _refused("not_extension"):
            segments.extend(stream, ledger, SEG + 9)
        with _refused("not_extension"):
            segments.extend(stream, object(), 2 * SEG)
        with _refused("not_extension"):
            segments.extend(stream, ledger, float(2 * SEG))
        with _refused("short_read"):
            segments.extend(stream, ledger, len(data) + 1)
    changed = bytearray(data)
    changed[SEG + 3] ^= 1
    path.write_bytes(bytes(changed))
    with path.open("rb") as stream, _refused("segment_mismatch"):
        segments.extend(stream, ledger, 2 * SEG + 1)


def test_a_restored_ledger_extends_without_claiming_a_whole_prefix_hash(tmp_path):
    data = _data(4 * SEG + 1)
    with _source(tmp_path, data).open("rb") as stream:
        built = segments.build(stream, SEG + 7, segment_bytes=SEG)
        raw = segments.dump(built)
        restored = segments.load(raw, expected_sha256=hashlib.sha256(raw).hexdigest())
        assert segments.dump(restored) == raw and restored.prefix_sha256 == built.prefix_sha256
        grown = segments.extend(stream, restored, len(data))
        rebuilt = segments.build(stream, len(data), segment_bytes=SEG)
    assert grown.prefix_sha256 is None
    assert grown.root_sha256() == rebuilt.root_sha256()
    assert [grown.digest(i) for i in range(grown.count)] == [rebuilt.digest(i) for i in range(rebuilt.count)]


def test_load_accepts_only_the_exact_canonical_bytes(tmp_path):
    data = _data(2 * SEG + 1)
    with _source(tmp_path, data).open("rb") as stream:
        raw = segments.dump(segments.build(stream, len(data), segment_bytes=SEG))
    value = json.loads(raw)
    assert value["authority"] == "none" and value["schema"] == segments.SCHEMA

    def encoded(**changes):
        return json.dumps(dict(value, **changes), sort_keys=True, separators=(",", ":")).encode("ascii")

    flipped = value["digests"][:-1] + ("0" if value["digests"][-1] != "0" else "1")
    bad = [raw + b" ", raw.replace(b",", b", ", 1), raw[:-1], b"", b"[]", bytearray(raw), raw.decode("ascii"),
           encoded(digests=flipped), encoded(digests=value["digests"].upper()), encoded(end=value["end"] + 1),
           encoded(end=float(value["end"])), encoded(segment_bytes=2 * SEG), encoded(authority="gate"),
           encoded(schema="wd.mind-source-segments.v0"), encoded(extra=1), encoded(prefix_sha256="0" * 63),
           encoded(root_sha256="0" * 64), encoded(digests=value["digests"][:-64]),
           raw[:-1] + b',"end":' + str(value["end"]).encode("ascii") + b"}"]
    for item in bad:
        with _refused("ledger_format"):
            segments.load(item)
    with _refused("ledger_format"):
        segments.load(raw, expected_sha256="0" * 64)
    with _refused("ledger_format"):
        segments.load(raw, expected_sha256=hashlib.sha256(raw).hexdigest().upper())
    with _refused("input"):
        segments.dump(value)
    assert segments.dump(segments.load(encoded(prefix_sha256=None))) == encoded(prefix_sha256=None)


def test_rotation_reaches_every_segment_and_reports_the_oldest_check(tmp_path):
    data = _data(7 * SEG + 5)
    with _source(tmp_path, data).open("rb") as stream:
        ledger = segments.build(stream, len(data), segment_bytes=SEG)
        rotation = segments.Rotation(ledger, 100.0)
        assert rotation.oldest_age(130.0) == 30.0
        seen, now = [], 130.0
        for _ in range(4):
            plan = rotation.plan(ledger, 2 * SEG)
            assert rotation.plan(ledger, 2 * SEG) == plan
            segments.verify(stream, ledger, plan)
            rotation.mark(plan, now)
            seen += plan
            now += 1.0
    assert seen == [0, 1, 2, 3, 4, 5, 6, 7] and rotation.oldest_age(now) == now - 130.0
    assert rotation.plan(ledger, 0) == [0] and rotation.plan(ledger, 10 ** 9) == list(range(8))
    rotation.mark([5], now)
    assert rotation.plan(ledger, SEG) == [0]
    for call in (lambda: rotation.mark([8], now), lambda: rotation.mark([0], now - 50.0),
                 lambda: rotation.mark([0], float("nan")), lambda: rotation.mark(3, now),
                 lambda: rotation.oldest_age(0.0), lambda: rotation.plan(ledger, -1),
                 lambda: rotation.plan(ledger, 1.5), lambda: segments.Rotation(object(), now)):
        with _refused("input"):
            call()


def test_rotation_keeps_the_times_of_untouched_complete_segments_after_growth(tmp_path):
    data = _data(5 * SEG)
    with _source(tmp_path, data).open("rb") as stream:
        old = segments.build(stream, 2 * SEG + 9, segment_bytes=SEG)
        rotation = segments.Rotation(old, 10.0)
        rotation.mark([0], 20.0)
        new = segments.extend(stream, old, len(data))
        grown = rotation.resized(old, new, 50.0)
        same = rotation.resized(old, old, 50.0)
        with _refused("input"):
            rotation.resized(new, new, 50.0)
        with _refused("input"):
            grown.resized(new, old, 60.0)
    assert grown.oldest_age(50.0) == 40.0 and same.oldest_age(50.0) == 40.0
    assert grown.plan(new, 10 ** 9) == [1, 2, 3, 4, 0]
    grown.mark([1], 50.0)
    assert grown.oldest_age(50.0) == 30.0


def test_a_ledger_cannot_be_created_with_inconsistent_parts():
    one = hashlib.sha256(b"x").digest()
    assert segments.SegmentLedger(SEG, 1, one, None).count == 1
    for args in ((SEG, 1, one, None, hashlib.sha256()), (SEG, 1, one + one, None), (SEG, SEG + 1, one, None),
                 (SEG, 1, bytearray(one), None), (SEG, 1, one[:-1], None), (SEG, 1, one, "0" * 63),
                 (SEG, 1, one, "G" * 64), (SEG, 1, one, b"0" * 64), (SEG - 1, 1, one, None), (SEG, 0, b"", None)):
        with _refused("input"):
            segments.SegmentLedger(*args)
    ledger = segments.SegmentLedger(SEG, 1, one, "0" * 64)
    for index in (1, -1, True, 0.0, None):
        with _refused("input"):
            ledger.digest(index)
        with _refused("input"):
            ledger.span(index)
