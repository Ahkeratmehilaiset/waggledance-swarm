"""Synthetic differential tests for bridge JSONL record-boundary scanning."""

import itertools
import random

from waggledance.core import bridge_log_reader as reader


def _legacy(payload, *, max_rows):
    rows = 0
    for index, value in enumerate(payload):
        if value != 0x0A:
            continue
        rows += 1
        if rows >= max_rows:
            return index + 1
    last_lf = payload.rfind(b"\n")
    return 0 if last_lf < 0 else last_lf + 1


def test_exhaustive_short_boundary_parity():
    # Also covers CRLF, empty rows and fragments of a three-byte UTF-8 codepoint.
    alphabet = (10, 13, 0, 0x41, 0xE2, 0x82, 0xAC)
    for length in range(7):
        for values in itertools.product(alphabet, repeat=length):
            payload = bytes(values)
            for max_rows in (1, 2, 3, 4):
                assert reader._complete_prefix_length(payload, max_rows=max_rows) == _legacy(payload, max_rows=max_rows)


def test_random_boundaries_and_max_bytes_truncations():
    rng = random.Random(7112026)
    for _ in range(50000):
        payload = rng.randbytes(rng.randrange(1000))
        max_rows = rng.randrange(1, 101)
        assert reader._complete_prefix_length(payload, max_rows=max_rows) == _legacy(payload, max_rows=max_rows)
    samples = [b'{"v":"\xe2\x82\xac"}\n{"v":"\xc3\xa4"}\npartial',
               b'{"v":"\xe2\x82', b'\r\n\n', b'\xef\xbb\xbf{}\n', b'{}\r\n{}\n']
    for payload in samples:
        for size in range(1, len(payload) + 1):
            for max_rows in (1, 2, 3, 8):
                prefix = payload[:size]
                assert reader._complete_prefix_length(prefix, max_rows=max_rows) == _legacy(prefix, max_rows=max_rows)


def test_reader_outputs_match_legacy_for_partial_utf8_and_row_limits(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    marker = tmp_path / "generation.json"
    marker.write_text('{"generation":"synthetic-g1"}', encoding="utf-8")
    optimized = reader._complete_prefix_length
    samples = [b'{"v":"\xe2\x82\xac"}\n{"v":"\xc3\xa4"}\npartial',
               b'{"v":"\xe2\x82', b'{}\r\n{}\n', b'{"x":1}\n\n', b'{"x":"\xff"}\n']
    for payload in samples:
        path.write_bytes(payload)
        for max_bytes in range(1, len(payload) + 1):
            for max_rows in (1, 2, 3):
                monkeypatch.setattr(reader, "_complete_prefix_length", _legacy)
                before = reader.read_bridge_log(path, generation_path=marker, max_bytes=max_bytes, max_rows=max_rows)
                monkeypatch.setattr(reader, "_complete_prefix_length", optimized)
                after = reader.read_bridge_log(path, generation_path=marker, max_bytes=max_bytes, max_rows=max_rows)
                assert before == after


def test_ordinary_payload_does_not_iterate_per_byte_in_python():
    class CountedBytes(bytes):
        visits = 0

        def __iter__(self):
            for value in super().__iter__():
                type(self).visits += 1
                yield value

    payload = CountedBytes(b'{"message":"' + b'a' * 40000 + b'"}\n')
    assert reader._complete_prefix_length(payload, max_rows=100) == len(payload)
    assert CountedBytes.visits < 128
