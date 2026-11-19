"""Synthetic tests for the fixed-size segment ledger of an append-only source prefix."""
from __future__ import annotations

import contextlib
import hashlib
import json
import random
import threading

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
        self.sizes, self.total, self.limit = [], 0, None

    def __call__(self, stream, size):
        assert self.limit is None or self.total + size <= self.limit, "read past the limit set by the test"
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


def test_rotation_starts_unknown_and_reaches_every_segment_in_turn(tmp_path):
    data = _data(7 * SEG + 5)
    with _source(tmp_path, data).open("rb") as stream:
        ledger = segments.build(stream, len(data), segment_bytes=SEG)
        rotation = segments.Rotation(ledger)
        assert rotation.oldest_age(130.0) is None
        seen, now = [], 130.0
        for _ in range(4):
            plan = rotation.plan(ledger, 2 * SEG)
            assert rotation.plan(ledger, 2 * SEG) == plan
            segments.verify(stream, ledger, plan)
            rotation.mark(plan, now)
            seen += plan
            assert (rotation.oldest_age(now) is None) == (len(seen) < 8)
            now += 1.0
    assert seen == [0, 1, 2, 3, 4, 5, 6, 7] and rotation.oldest_age(now) == now - 130.0
    assert rotation.plan(ledger, SEG) == [0] and rotation.plan(ledger, 10 ** 9) == list(range(8))
    assert rotation.plan(ledger, 2 * SEG - 1) == [0]
    rotation.mark([5], now)
    assert rotation.plan(ledger, SEG) == [0]
    rotation.mark([0], now - 50.0)                       # an out-of-order finish never moves a time back
    assert rotation.oldest_age(now) == now - 130.0
    assert rotation.oldest_age(129.0) is None            # a clock value older than a recorded read is unknown
    for call in (lambda: rotation.mark([8], now), lambda: rotation.mark([0], float("nan")),
                 lambda: rotation.mark(3, now), lambda: rotation.mark([True], now),
                 lambda: rotation.oldest_age(float("inf")), lambda: rotation.oldest_age(None),
                 lambda: rotation.plan(ledger, -1), lambda: rotation.plan(ledger, 0),
                 lambda: rotation.plan(ledger, SEG - 1), lambda: rotation.plan(ledger, 1.5),
                 lambda: segments.Rotation(object())):
        with _refused("input"):
            call()


def test_rotation_after_growth_keeps_old_times_and_marks_only_what_was_re_read(tmp_path):
    data = _data(5 * SEG)
    with _source(tmp_path, data).open("rb") as stream:
        old = segments.build(stream, 2 * SEG + 9, segment_bytes=SEG)
        rotation = segments.Rotation(old)
        rotation.mark([0, 2], 20.0)
        new = segments.extend(stream, old, len(data))
        grown = rotation.resized(old, new, 50.0)
        same = rotation.resized(old, old, 50.0)
        with _refused("input"):
            rotation.resized(new, new, 50.0)
        with _refused("input"):
            grown.resized(new, old, 60.0)
    assert grown.oldest_age(50.0) is None and same.oldest_age(50.0) is None      # segment 1 was never read
    assert grown.plan(new, 10 ** 9) == [1, 2, 3, 4, 0]
    grown.mark([1], 50.0)
    same.mark([1], 50.0)
    assert grown.oldest_age(50.0) == 30.0 and same.oldest_age(50.0) == 30.0      # segment 0 keeps its time
    grown.mark([0], 50.0)
    assert grown.oldest_age(50.0) == 0.0                                         # 2, 3 and 4 were re-read at growth


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


class _Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


def _generation(stream, size, clock=None, **changes):
    args = dict(segment_bytes=SEG, file_identity="volume-1:file-9", generation=None, clock=clock or _Clock())
    args.update(changes)
    return segments.open_generation(stream, size, **args)


def test_generation_checks_every_segment_a_row_touches_plus_the_newest(tmp_path):
    data, counting, clock = _data(9 * SEG + 3), _Counting(), _Clock()
    with _source(tmp_path, data).open("rb") as stream:
        generation = _generation(stream, len(data), clock,
                                 expected_prefix_sha256=hashlib.sha256(data).hexdigest())
        assert generation.oldest_age() == 0.0 and generation.bad is False
        clock.value = 105.0
        # row one touches segments 0 and 1, row two touches 4, 5, 6 and 7, and the newest segment is 9
        hashed = generation.check_rows(stream, [(SEG - 1, 2), (4 * SEG + 1, 3 * SEG)], read=counting)
    assert hashed == counting.total == 6 * SEG + 3
    assert generation.oldest_age() == 5.0
    assert (generation.file_identity, generation.generation) == ("volume-1:file-9", None)
    assert generation.ledger.end == len(data)


def test_any_mismatch_marks_the_generation_bad_for_every_later_call(tmp_path):
    data, clock = _data(4 * SEG + 3), _Clock()
    path = _source(tmp_path, data)
    with path.open("rb") as stream:
        generation = _generation(stream, len(data), clock)
    changed = bytearray(data)
    changed[2 * SEG] ^= 1
    path.write_bytes(bytes(changed))
    with path.open("rb") as stream:
        assert generation.check_rows(stream, [(0, 10)]) == SEG + 3      # the changed segment was not read
        with _refused("segment_mismatch"):
            generation.scrub(stream, 3 * SEG)
    assert generation.bad is True
    path.write_bytes(data)                                              # a file put back stays refused
    with path.open("rb") as stream:
        for call in (lambda: generation.check_rows(stream, [(0, 10)]), lambda: generation.scrub(stream, SEG),
                     lambda: generation.oldest_age(), lambda: generation.extended(stream, len(data)),
                     lambda: generation.dump()):
            with _refused("generation_bad"):
                call()
        counting = _Counting()
        with _refused("generation_bad"):
            generation.check_rows(stream, [(0, 10)], read=counting)
        with _refused("generation_bad"):
            generation.scrub(stream, SEG, read=counting)
        with _refused("generation_bad"):
            generation.extended(stream, len(data) + 1, read=counting)
        assert counting.total == 0                                      # a bad generation reads nothing more


def test_truncation_and_a_changed_tail_mark_the_generation_bad(tmp_path):
    data = _data(3 * SEG + 100)
    path = _source(tmp_path, data)
    with path.open("rb") as stream:
        first, second = _generation(stream, len(data)), _generation(stream, 2 * SEG + 50)
    path.write_bytes(data[:3 * SEG + 99])
    with path.open("rb") as stream, _refused("short_read"):
        first.check_rows(stream, [])
    assert first.bad is True
    changed = bytearray(data)
    changed[2 * SEG + 10] ^= 1
    path.write_bytes(bytes(changed))
    with path.open("rb") as stream, _refused("segment_mismatch"):
        second.extended(stream, len(data))
    assert second.bad is True


def test_an_error_raised_by_the_read_hook_marks_nothing_bad_and_nothing_fresh(tmp_path):
    data, clock = _data(3 * SEG), _Clock()

    class Limit(Exception):
        pass

    def refusing(stream, size):
        raise Limit()

    with _source(tmp_path, data).open("rb") as stream:
        generation = _generation(stream, len(data), clock)
        clock.value = 200.0
        with pytest.raises(Limit):
            generation.scrub(stream, SEG, read=refusing)
        with pytest.raises(Limit):
            generation.check_rows(stream, [(0, 1)], read=refusing)
        assert generation.bad is False and generation.oldest_age() == 100.0
        with _refused("read_type"):
            generation.scrub(stream, SEG, read=lambda stream, size: None)
    assert generation.bad is True


def test_an_extended_generation_keeps_binding_times_and_whole_prefix_hash(tmp_path):
    data, clock = _data(4 * SEG + 2), _Clock()
    with _source(tmp_path, data).open("rb") as stream:
        old = _generation(stream, SEG + 5, clock, generation="gen-1")
        clock.value = 160.0
        new = old.extended(stream, len(data))
        assert new.ledger.prefix_sha256 == hashlib.sha256(data).hexdigest()
        assert (new.file_identity, new.generation) == ("volume-1:file-9", "gen-1")
        assert (old.file_identity, old.generation) == ("volume-1:file-9", "gen-1")
        assert new.oldest_age() == 60.0 and old.oldest_age() == 60.0 and old.ledger.end == SEG + 5
        clock.value = 170.0
        assert new.check_rows(stream, [(0, SEG)]) == SEG + 2
        assert new.oldest_age() == 10.0 and old.bad is False


def test_a_file_seen_shorter_than_the_pinned_prefix_marks_the_generation_bad(tmp_path):
    data = _data(2 * SEG + 9)
    with _source(tmp_path, data).open("rb") as stream:
        generation = _generation(stream, len(data))
        assert generation.extended(stream, len(data)).ledger is generation.ledger
        with _refused("not_extension"):
            generation.extended(stream, len(data) + 0.5)
        assert generation.bad is False
        with _refused("not_extension"):
            generation.extended(stream, len(data) - 1)
        assert generation.bad is True
        with _refused("generation_bad"):
            generation.check_rows(stream, [])


def test_bad_row_spans_and_bad_creation_input_are_refused(tmp_path):
    data = _data(2 * SEG)
    with _source(tmp_path, data).open("rb") as stream:
        generation = _generation(stream, len(data))
        for spans in ([(0, len(data) + 1)], [(-1, 1)], [(0, 0)], [(1,)], [None], 7, [(0, 1.0)]):
            with _refused("input"):
                generation.check_rows(stream, spans)
        assert generation.bad is False and generation.check_rows(stream, [(0, 1), (SEG, 1)]) == 2 * SEG
        for changes in (dict(file_identity=""), dict(file_identity=7), dict(file_identity="a\nb"),
                        dict(file_identity="x" * 513), dict(file_identity="\u00e4"), dict(generation=""),
                        dict(generation=3), dict(clock=7), dict(clock=lambda: float("nan")),
                        dict(clock=lambda: "1")):
            with _refused("input"):
                _generation(stream, len(data), **changes)
        counting = _Counting()
        with _refused("input"):
            _generation(stream, len(data), clock=lambda: float("nan"), read=counting)
        assert counting.total == 0                                      # refused before the full pass
        with _refused("prefix_mismatch"):
            _generation(stream, len(data), expected_prefix_sha256="0" * 64)
        late = _Clock()
        generation = _generation(stream, len(data), late)
        late.value = float("inf")
        for call in (lambda: generation.oldest_age(), lambda: generation.scrub(stream, SEG),
                     lambda: generation.check_rows(stream, []), lambda: generation.extended(stream, len(data))):
            with _refused("input"):
                call()
        assert generation.bad is False
        with _refused("input"):
            generation.scrub(stream, SEG - 1)


def test_a_restored_generation_is_bound_and_not_fresh_until_every_segment_is_re_read(tmp_path):
    data, clock = _data(3 * SEG + 7), _Clock()
    digest = hashlib.sha256(data).hexdigest()
    binding = dict(file_identity="volume-1:file-9", generation="gen-1", expected_prefix_sha256=digest)
    with _source(tmp_path, data).open("rb") as stream:
        raw = _generation(stream, len(data), clock, generation="gen-1").dump()
        restored = segments.restore_generation(raw, clock=clock, expected_sha256=hashlib.sha256(raw).hexdigest(),
                                               **binding)
        assert restored.dump() == raw and restored.bad is False and restored.ledger.prefix_sha256 == digest
        assert restored.oldest_age() is None
        clock.value = 300.0
        assert restored.check_rows(stream, [(0, 1)]) == SEG + 7
        assert restored.oldest_age() is None                       # segments 1 and 2 are still unread
        assert restored.scrub(stream, 2 * SEG) == 2 * SEG
        assert restored.oldest_age() == 0.0
        grown_source = _source(tmp_path, data + b"more")
        with grown_source.open("rb") as grown_stream:
            grown = restored.extended(grown_stream, len(data) + 4)
        assert grown.ledger.prefix_sha256 is None                   # no whole-prefix claim after a restore
        with _refused("binding_mismatch"):
            segments.restore_generation(grown.dump(), **dict(binding, expected_prefix_sha256=digest))
    for change in (dict(file_identity="volume-1:file-8"), dict(generation=None), dict(generation="gen-2"),
                   dict(expected_prefix_sha256="0" * 64)):
        with _refused("binding_mismatch"):
            segments.restore_generation(raw, **dict(binding, **change))
    for change in (dict(file_identity=""), dict(generation=5), dict(expected_prefix_sha256="zz"),
                   dict(expected_prefix_sha256=None), dict(clock=5)):
        with _refused("input"):
            segments.restore_generation(raw, **dict(binding, **change))
    value = json.loads(raw)
    variants = [raw + b" ", raw[:-1], b"", "text", json.dumps(value).encode("ascii"),
                json.dumps(dict(value, extra=1), sort_keys=True, separators=(",", ":")).encode("ascii"),
                json.dumps(dict(value, schema="other"), sort_keys=True, separators=(",", ":")).encode("ascii"),
                json.dumps(dict(value, authority="some"), sort_keys=True, separators=(",", ":")).encode("ascii"),
                json.dumps(dict(value, ledger=[]), sort_keys=True, separators=(",", ":")).encode("ascii"),
                json.dumps(dict(value, file_identity=""), sort_keys=True, separators=(",", ":")).encode("ascii"),
                raw.replace(b'"generation":"gen-1"', b'"generation":"gen-1","generation":"gen-1"')]
    for bad in variants:
        with _refused("ledger_format"):
            segments.restore_generation(bad, **binding)
    with _refused("ledger_format"):
        segments.restore_generation(raw, expected_sha256="0" * 64, **binding)
    with _refused("ledger_format"):
        segments.restore_generation(raw, expected_sha256=7, **binding)


def test_parallel_readers_share_one_generation_and_one_bad_mark(tmp_path):
    data, clock = _data(16 * SEG + 1), _Clock()
    path = _source(tmp_path, data)
    with path.open("rb") as stream:
        generation = _generation(stream, len(data), clock)
    clock.value = 400.0

    def run(turns):
        outcomes, gate = [], threading.Barrier(5)

        def reader(number):
            try:
                with path.open("rb") as stream:
                    gate.wait(timeout=30)
                    for turn in range(turns):
                        generation.check_rows(stream, [((number * 3 + turn) % 16 * SEG, SEG)])
                        generation.scrub(stream, 2 * SEG)
                outcomes.append("clean")
            except segments.SourceSegmentsRefused as error:
                outcomes.append(error.reason)
            except BaseException as error:                      # reported below, never swallowed
                outcomes.append(repr(error))

        threads = [threading.Thread(target=reader, args=(number,), daemon=True) for number in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        assert not any(thread.is_alive() for thread in threads)
        return outcomes

    assert run(40) == ["clean"] * 5
    assert generation.bad is False and generation.oldest_age() == 0.0
    changed = bytearray(data)
    changed[7 * SEG + 3] ^= 1
    path.write_bytes(bytes(changed))
    outcomes = run(40)
    assert len(outcomes) == 5 and set(outcomes) <= {"segment_mismatch", "generation_bad"}
    assert "segment_mismatch" in outcomes and generation.bad is True


def test_times_are_taken_before_reading_and_a_bad_mark_set_during_a_read_refuses_that_read(tmp_path):
    data, clock = _data(3 * SEG), _Clock()
    path = _source(tmp_path, data)

    def slow(stream, size):
        clock.value += 10.0
        return stream.read(size)

    with path.open("rb") as stream, path.open("rb") as other:
        generation = _generation(stream, len(data), clock, read=slow)
        assert clock.value == 130.0 and generation.oldest_age() == 30.0     # counted from before the full pass
        assert generation.scrub(stream, 3 * SEG, read=slow) == 3 * SEG
        assert clock.value == 160.0 and generation.oldest_age() == 30.0     # and from before each re-read
        seen = []

        def interrupted(stream, size):
            if not seen:
                seen.append(size)
                with _refused("not_extension"):
                    generation.extended(other, len(data) - 1)                # another reader sees a shorter file
            return stream.read(size)

        clock.value = 500.0
        with _refused("generation_bad"):
            generation.check_rows(stream, [(0, 1)], read=interrupted)
        assert generation.bad is True and seen == [SEG]


def _no_wait(condition, seconds):
    raise AssertionError("unexpected wait")


def test_refresh_reads_nothing_when_fresh_and_only_the_stale_segments_otherwise(tmp_path):
    data, clock, counting = _data(6 * SEG), _Clock(), _Counting()
    with _source(tmp_path, data).open("rb") as stream:
        generation = _generation(stream, len(data), clock, wait=_no_wait)
        clock.value = 130.0
        assert generation.refresh(stream, 30.0, 500.0, read=counting) == 30.0 and counting.total == 0
        assert generation.check_rows(stream, [(SEG, SEG)]) == 2 * SEG            # segments 1 and 5 re-read at 130
        clock.value = 140.0
        assert generation.refresh(stream, 30.0, 500.0, read=counting) == 10.0   # 0, 2, 3 and 4 re-read at 140
        assert counting.total == 4 * SEG
        assert generation.refresh(stream, 0, 500.0, read=counting) == 0.0       # 1 and 5 are 10 s old
        assert counting.total == 6 * SEG
        clock.value = 139.0                                                     # a clock behind the recorded reads
        counting.limit = 12 * SEG                                               # a second re-read fails at once
        with _refused("not_fresh"):                                             # one re-read, then it gives up
            generation.refresh(stream, 30.0, 500.0, read=counting)
        assert counting.total == 12 * SEG and generation.oldest_age() is None and generation.bad is False
        counting.limit = None
        clock.value = 141.0                                                     # the clock has caught up
        assert generation.refresh(stream, 30.0, 500.0, read=counting) == 1.0 and counting.total == 12 * SEG
        raw = generation.dump()
        restored = segments.restore_generation(raw, file_identity="volume-1:file-9", generation=None, clock=clock,
                                               expected_prefix_sha256=hashlib.sha256(data).hexdigest(), wait=_no_wait)
        assert restored.oldest_age() is None and restored._wait is _no_wait
        assert restored.refresh(stream, 30.0, 500.0, read=counting) == 0.0 and counting.total == 18 * SEG
        for call in (lambda: generation.refresh(stream, -1, 500.0), lambda: generation.refresh(stream, None, 500.0),
                     lambda: generation.refresh(stream, float("nan"), 500.0),
                     lambda: generation.refresh(stream, 30.0, float("inf")),
                     lambda: generation.refresh(stream, 30.0, "9"),
                     lambda: generation.refresh(stream, 30.0, 500.0, byte_allowance=SEG - 1),
                     lambda: generation.refresh(stream, 30.0, 500.0, byte_allowance=2.0 * SEG),
                     lambda: _generation(stream, len(data), wait=7),
                     lambda: segments.restore_generation(raw, file_identity="volume-1:file-9", generation=None, wait=7,
                                                         expected_prefix_sha256=hashlib.sha256(data).hexdigest())):
            with _refused("input"):
                call()


def test_refresh_gives_up_at_its_own_deadline_and_keeps_the_progress(tmp_path):
    data, clock, counting = _data(6 * SEG), _Clock(), _Counting()

    def slow(stream, size):
        clock.value += 10.0
        return stream.read(size)

    with _source(tmp_path, data).open("rb") as stream:
        generation = _generation(stream, len(data), clock, wait=_no_wait)
        clock.value = 200.0
        with _refused("not_fresh"):                                 # 0, 1 at 200 and 2, 3 at 220, then 240 is too late
            generation.refresh(stream, 50.0, 235.0, byte_allowance=2 * SEG, read=slow)
        assert clock.value == 240.0 and generation.bad is False
        with _refused("not_fresh"):                                 # already too late: nothing is read
            generation.refresh(stream, 50.0, 240.0, read=counting)
        assert counting.total == 0
        assert generation.refresh(stream, 50.0, 900.0, read=counting) == 40.0   # only 4 and 5 were still stale
        assert counting.total == 2 * SEG
        clock.value = 1000.0
        with _refused("not_fresh"):                                 # slower than the allowed age: never fresh
            generation.refresh(stream, 5.0, 1300.0, read=slow)
        assert generation.bad is False and clock.value >= 1300.0
        grown = generation.extended(stream, len(data))
        assert grown.refresh(stream, 10 ** 6, 10 ** 7, read=counting) == generation.oldest_age()
        assert grown._wait is _no_wait                          # a grown generation keeps the wait hook


def test_refresh_errors_free_the_flight_and_only_a_mismatch_marks_bad(tmp_path):
    data, clock = _data(3 * SEG), _Clock()
    path = _source(tmp_path, data)

    def broken(stream, size):
        raise OSError("synthetic")

    def interrupted(stream, size):
        raise KeyboardInterrupt()

    with path.open("rb") as stream:
        generation = _generation(stream, len(data), clock, wait=_no_wait)
        clock.value = 300.0
        with pytest.raises(OSError):
            generation.refresh(stream, 10.0, 900.0, read=broken)
        with pytest.raises(KeyboardInterrupt):
            generation.refresh(stream, 10.0, 900.0, read=interrupted)
        assert generation.bad is False
        assert generation.refresh(stream, 10.0, 900.0) == 0.0       # the flight was freed both times
    changed = bytearray(data)
    changed[SEG + 1] ^= 1
    path.write_bytes(bytes(changed))
    clock.value = 400.0
    with path.open("rb") as stream:
        with _refused("segment_mismatch"):
            generation.refresh(stream, 10.0, 900.0)
        with _refused("generation_bad"):
            generation.refresh(stream, 10.0, 900.0)
    assert generation.bad is True


def _overlapping_refreshes(path, generation, callers):
    """Start ``callers`` refresh calls; the one that re-reads is released only when all others wait."""
    release, lock, count, outcomes = threading.Event(), threading.Lock(), [0], []

    def read(stream, size):
        assert release.wait(20)
        block = stream.read(size)
        with lock:
            count[0] += len(block)
        return block

    def query():
        try:
            with path.open("rb") as stream:
                outcomes.append(generation.refresh(stream, 10.0, 900.0, read=read))
        except segments.SourceSegmentsRefused as error:
            outcomes.append(error.reason)
        except BaseException as error:                          # reported through the outcome list
            outcomes.append(repr(error))

    threads = [threading.Thread(target=query, daemon=True) for _ in range(callers)]
    for thread in threads:
        thread.start()
    return release, threads, count, outcomes


def _waiting_hook():
    waiting = threading.Semaphore(0)

    def wait(condition, seconds):
        waiting.release()
        assert condition.wait(20)                               # woken by the reading caller, not by the time limit

    return waiting, wait


def test_overlapping_stale_refreshes_cause_one_traversal(tmp_path):
    data, clock = _data(8 * SEG), _Clock()
    path = _source(tmp_path, data)
    waiting, wait = _waiting_hook()
    with path.open("rb") as stream:
        generation = _generation(stream, len(data), clock, wait=wait)
    clock.value = 500.0
    release, threads, count, outcomes = _overlapping_refreshes(path, generation, 5)
    for _ in range(4):
        assert waiting.acquire(timeout=20)                      # four callers wait for the one that reads
    assert count[0] == 0
    release.set()
    for thread in threads:
        thread.join(timeout=20)
    assert outcomes == [0.0] * 5 and count[0] == len(data) and generation.bad is False


def test_a_mismatch_found_by_the_reading_caller_refuses_every_waiting_caller(tmp_path):
    data, clock = _data(8 * SEG), _Clock()
    path = _source(tmp_path, data)
    waiting, wait = _waiting_hook()
    with path.open("rb") as stream:
        generation = _generation(stream, len(data), clock, wait=wait)
    changed = bytearray(data)
    changed[3] ^= 1
    path.write_bytes(bytes(changed))
    clock.value = 500.0
    release, threads, count, outcomes = _overlapping_refreshes(path, generation, 5)
    for _ in range(4):
        assert waiting.acquire(timeout=20)
    release.set()
    for thread in threads:
        thread.join(timeout=20)
    assert sorted(outcomes) == ["generation_bad"] * 4 + ["segment_mismatch"]
    assert generation.bad is True and count[0] <= len(data)    # one reader only, and it read at most once


def test_a_waiting_caller_gives_up_at_its_own_deadline_without_harming_the_flight(tmp_path):
    data, clock, counting = _data(4 * SEG), _Clock(), _Counting()
    path = _source(tmp_path, data)

    def late(condition, seconds):
        assert 0 < seconds <= 10.0
        clock.value = 520.0                                     # the wait ran out: the clock is past 510

    with path.open("rb") as stream:
        generation = _generation(stream, len(data), clock, wait=late)
        clock.value = 500.0
        release, threads, count, outcomes = _overlapping_refreshes(path, generation, 1)
        while not generation._sweeping:                         # the single caller has become the reader
            assert threads[0].is_alive()
        with _refused("not_fresh"):
            generation.refresh(stream, 10.0, 510.0, read=counting)
        assert counting.total == 0 and generation.bad is False and outcomes == []
        release.set()
        threads[0].join(timeout=20)
        # its first pass was stamped 500, which is 20 s old at 520, so it read once more and ended fresh
        assert outcomes == [0.0] and count[0] == 2 * len(data)
        assert generation.refresh(stream, 10.0, 900.0, read=counting) == 0.0 and counting.total == 0


def test_max_check_bytes_bounds_every_row_check_and_is_reached(tmp_path):
    data, rng = _data(40 * SEG), random.Random(5)
    with _source(tmp_path, data).open("rb") as stream:
        generation = _generation(stream, len(data))
        for _ in range(300):
            rows, longest = rng.randrange(0, 4), rng.randrange(1, 5 * SEG)
            spans = [(rng.randrange(0, len(data) - longest), rng.randrange(1, longest + 1)) for _ in range(rows)]
            assert generation.check_rows(stream, spans) <= segments.max_check_bytes(SEG, rows, longest)
        # reached: three rows of four segments, each starting one byte into a segment, plus the newest segment
        spans = [(1, 4 * SEG), (6 * SEG + 1, 4 * SEG), (12 * SEG + 1, 4 * SEG)]
        assert generation.check_rows(stream, spans) == segments.max_check_bytes(SEG, 3, 4 * SEG) == 16 * SEG
        assert generation.check_rows(stream, [(SEG - 1, 2)]) == segments.max_check_bytes(SEG, 1, 2) == 3 * SEG
        assert generation.check_rows(stream, [(7, 1)]) == segments.max_check_bytes(SEG, 1, 1) == 2 * SEG
        assert generation.check_rows(stream, []) == segments.max_check_bytes(SEG, 0, 1) == SEG
    mib = 1024 * 1024
    assert segments.max_check_bytes(mib, 3, 4 * mib) == 16 * mib        # the session case: 3 rows of 4 MiB
    assert segments.max_check_bytes(4 * mib, 3, 4 * mib) == 28 * mib
    for call in (lambda: segments.max_check_bytes(SEG - 1, 1, 1), lambda: segments.max_check_bytes(SEG + 1, 1, 1),
                 lambda: segments.max_check_bytes(2 * segments.MAX_SEGMENT_BYTES, 1, 1),
                 lambda: segments.max_check_bytes(float(SEG), 1, 1), lambda: segments.max_check_bytes(SEG, -1, 1),
                 lambda: segments.max_check_bytes(SEG, True, 1), lambda: segments.max_check_bytes(SEG, 1, 0),
                 lambda: segments.max_check_bytes(SEG, 1, 1.0)):
        with _refused("input"):
            call()
