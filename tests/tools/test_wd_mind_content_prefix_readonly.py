import hashlib
import os

import pytest

from tools import wd_mind_content_prefix_readonly as reader
from waggledance.core import bridge_log_reader as bridge


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'events.jsonl'
    generation = tmp_path / 'generation.json'
    raw = b'{"task_id":"one","message":"alpha"}\n{"task_id":"two"}\n'
    path.write_bytes(raw)
    generation.write_bytes(b'{"generation":"g1"}')
    with bridge._open_log(path) as stream:
        identity = bridge._file_identity(stream)
    return path, generation, raw, bridge.BridgeCursor(len(raw), identity, 'g1')


def read(source, *, cursor=None, digest=None):
    path, generation, raw, end = source
    return reader.read_prefix_rows(str(path), str(generation), cursor or end,
                                   digest or hashlib.sha256(raw).hexdigest(), {'one'})


def test_expected_rows_and_digest(source):
    rows = read(source)
    assert len(rows) == 1
    assert next(iter(rows.values()))['message'] == 'alpha'


def test_bad_digest_refuses(source):
    with pytest.raises(ValueError):
        read(source, digest='0' * 64)


def test_identity_mismatch_refuses(source):
    end = source[3]
    with pytest.raises(ValueError):
        read(source, cursor=bridge.BridgeCursor(end.offset, 'wrong', end.generation))


def test_generation_mismatch_refuses(source):
    source[1].write_bytes(b'{"generation":"g2"}')
    with pytest.raises(ValueError):
        read(source)


def test_end_cursor_inside_line_refuses(source):
    end = source[3]
    with pytest.raises(ValueError):
        read(source, cursor=bridge.BridgeCursor(end.offset - 2, end.file_identity, end.generation))


def test_cursor_budget_refuses_without_source(source):
    end = source[3]
    with pytest.raises(ValueError):
        read(source, cursor=bridge.BridgeCursor(reader.MAX_PREFIX_BYTES + 1, end.file_identity, end.generation))


def test_malformed_json_refuses(source):
    path, generation, _, end = source
    raw = b'{"task_id":"one","x":NaN}\n'
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        read((path, generation, raw, bridge.BridgeCursor(len(raw), end.file_identity, end.generation)))


def test_parsed_bytes_mutated_before_post_hash_refuses(source, monkeypatch):
    path, _, raw, _ = source
    original = bridge._parse_rows

    def altered(payload, **kwargs):
        result = original(payload, **kwargs)
        path.write_bytes(raw.replace(b'alpha', b'omega'))
        return result

    monkeypatch.setattr(bridge, '_parse_rows', altered)
    with pytest.raises(ValueError):
        read(source)


def test_generation_changes_during_parse_refuses(source, monkeypatch):
    original = bridge._parse_rows

    def altered(payload, **kwargs):
        result = original(payload, **kwargs)
        source[1].write_bytes(b'{"generation":"g2"}')
        return result

    monkeypatch.setattr(bridge, '_parse_rows', altered)
    with pytest.raises(ValueError):
        read(source)


def test_rotation_during_parse_refuses(source, monkeypatch):
    path, _, raw, _ = source
    replacement = path.with_name('replacement.jsonl')
    replacement.write_bytes(raw)
    original = bridge._parse_rows

    def altered(payload, **kwargs):
        result = original(payload, **kwargs)
        os.replace(replacement, path)
        return result

    monkeypatch.setattr(bridge, '_parse_rows', altered)
    with pytest.raises(ValueError):
        read(source)


def test_page_chain_retains_all_approved_rows(source, monkeypatch):
    path, generation, _, end = source
    raw = b'{"task_id":"one","n":1}\n{"task_id":"one","n":2}\n'
    path.write_bytes(raw)
    monkeypatch.setattr(reader, 'PAGE_BYTES', 26)
    rows = read((path, generation, raw, bridge.BridgeCursor(len(raw), end.file_identity, end.generation)))
    assert {row['n'] for row in rows.values()} == {1, 2}


def test_duplicate_and_case_colliding_json_keys_refuse(source):
    path, generation, _, end = source
    raw = b'{"task_id":"one","TASK_ID":"one"}\n'
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        read((path, generation, raw, bridge.BridgeCursor(len(raw), end.file_identity, end.generation)))


def test_mutation_of_previous_page_refuses(source, monkeypatch):
    path, generation, _, end = source
    raw = b'{"task_id":"one","n":1}\n{"task_id":"one","n":2}\n'
    path.write_bytes(raw)
    monkeypatch.setattr(reader, 'PAGE_BYTES', 26)
    original = bridge._parse_rows
    calls = 0

    def altered(payload, **kwargs):
        nonlocal calls
        result = original(payload, **kwargs)
        calls += 1
        if calls == 2:
            path.write_bytes(raw.replace(b'"n":1', b'"n":3'))
        return result

    monkeypatch.setattr(bridge, '_parse_rows', altered)
    with pytest.raises(ValueError):
        read((path, generation, raw, bridge.BridgeCursor(len(raw), end.file_identity, end.generation)))


def test_approved_row_limit_refuses(source, monkeypatch):
    path, generation, _, end = source
    raw = b'{"task_id":"one","n":1}\n{"task_id":"one","n":2}\n'
    path.write_bytes(raw)
    monkeypatch.setattr(reader, 'MAX_SOURCE_ROWS', 1)
    with pytest.raises(ValueError):
        read((path, generation, raw, bridge.BridgeCursor(len(raw), end.file_identity, end.generation)))


def test_append_after_anchored_prefix_is_allowed(source):
    path, _, _, _ = source
    with path.open('ab') as stream:
        stream.write(b'{"task_id":"one","message":"later"}\n')
    assert len(read(source)) == 1


def test_record_longer_than_page_refuses(source, monkeypatch):
    monkeypatch.setattr(reader, 'PAGE_BYTES', 10)
    with pytest.raises(ValueError):
        read(source)


@pytest.mark.parametrize('raw', [b'\xef\xbb\xbf{"task_id":"one"}\n', b'{"task_id":"one","x":"\xff"}\n'])
def test_invalid_utf8_and_bom_refuse(source, raw):
    path, generation, _, end = source
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        read((path, generation, raw, bridge.BridgeCursor(len(raw), end.file_identity, end.generation)))


def test_missing_generation_refuses(source):
    source[1].unlink()
    with pytest.raises(ValueError):
        read(source)


def test_truncate_during_parse_refuses(source, monkeypatch):
    original = bridge._parse_rows

    def altered(payload, **kwargs):
        result = original(payload, **kwargs)
        with source[0].open('r+b') as stream:
            stream.truncate(2)
        return result

    monkeypatch.setattr(bridge, '_parse_rows', altered)
    with pytest.raises(ValueError):
        read(source)


def test_rotation_on_later_page_refuses(source, monkeypatch):
    path, generation, _, end = source
    raw = b'{"task_id":"one","n":1}\n{"task_id":"one","n":2}\n'
    path.write_bytes(raw)
    replacement = path.with_name('second-replacement.jsonl')
    replacement.write_bytes(raw)
    monkeypatch.setattr(reader, 'PAGE_BYTES', 26)
    original = bridge._parse_rows
    calls = 0

    def altered(payload, **kwargs):
        nonlocal calls
        result = original(payload, **kwargs)
        calls += 1
        if calls == 2:
            os.replace(replacement, path)
        return result

    monkeypatch.setattr(bridge, '_parse_rows', altered)
    with pytest.raises(ValueError):
        read((path, generation, raw, bridge.BridgeCursor(len(raw), end.file_identity, end.generation)))
