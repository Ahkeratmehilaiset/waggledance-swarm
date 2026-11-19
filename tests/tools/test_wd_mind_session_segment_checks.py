"""Synthetic checks of the selected-row resolver's segment mode; never reads a live bridge log.

The first group drives ``_prefix_check`` over a small synthetic file with both modes side by side, so the one
difference between them is stated by a test: a change outside the segments in use is seen by the whole-prefix mode
and not by the segment mode. The second group runs the real resolver over the existing synthetic source fixtures.
"""
import hashlib
import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import wd_bridge_mind_session as session
from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_source_segments as segments

SEG = segments.MIN_SEGMENT_BYTES
TAIL = 100
END = 5 * SEG + TAIL
LIMIT = 1024 * 1024 * 1024
MODES = pytest.mark.parametrize('segment_bytes', [None, SEG], ids=['whole-prefix', 'segments'])


def _row(index, offset, length=50):
    return {'source_offset': index * SEG + offset, 'source_length': length}


def _case(tmp_path, monkeypatch, segment_bytes, limit=LIMIT):
    """A hand-built resolver over five whole segments plus a short newest one."""
    path = tmp_path / 'synthetic-source'
    path.write_bytes(bytes((index * 31 + index // 251) % 251 for index in range(END)))
    bridge = SimpleNamespace(_open_log=lambda source: open(source, 'rb', buffering=0))
    fences = []
    monkeypatch.setattr(reader, '_narrow_dependencies', lambda: (bridge, object()))
    monkeypatch.setattr(reader, '_narrow_fence', lambda *args: fences.append(args[3]))
    resolver = object.__new__(session._SelectedRowResolver)
    resolver._cursor = SimpleNamespace(offset=END, file_identity='synthetic-identity', generation=None)
    resolver._source_budget = session._SourceReadBudget(limit)
    with open(path, 'rb', buffering=0) as stream:
        resolver._generation = None if segment_bytes is None else segments.open_generation(
            stream, END, segment_bytes=segment_bytes, file_identity='synthetic-identity', generation=None)
    context = {'messages': {'source_path': str(path), 'generation_path': str(tmp_path / 'synthetic-sidecar'),
                            'end_cursor': resolver._cursor},
               'decision': {'source_prefix': {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}}
    return resolver, context, path, fences


def _flip(path, position):
    data = bytearray(path.read_bytes())
    data[position] ^= 1
    path.write_bytes(bytes(data))


def _spent(resolver, limit=LIMIT):
    return limit - resolver._source_budget._remaining


def _segment_refusal(reason, call, *args):
    with pytest.raises(segments.SourceSegmentsRefused) as caught:
        call(*args)
    assert caught.value.reason == reason


@MODES
def test_unchanged_prefix_passes_and_charges_the_bytes_of_its_mode(tmp_path, monkeypatch, segment_bytes):
    resolver, context, _, fences = _case(tmp_path, monkeypatch, segment_bytes)
    resolver._prefix_check(context, [_row(1, 10)])
    # whole-prefix mode reserves the whole prefix; segment mode reads segment 1 and the newest segment only
    assert _spent(resolver) == (END if segment_bytes is None else SEG + TAIL)
    assert fences == [resolver._cursor, resolver._cursor]
    resolver._prefix_check(context)
    assert _spent(resolver) == (2 * END if segment_bytes is None else SEG + 2 * TAIL)


def test_row_across_a_segment_edge_checks_both_segments(tmp_path, monkeypatch):
    resolver, context, _, _ = _case(tmp_path, monkeypatch, SEG)
    resolver._prefix_check(context, [_row(1, SEG - 10, 20), _row(1, 5), _row(3, 0, SEG)])
    assert _spent(resolver) == 3 * SEG + TAIL  # segments 1, 2 and 3 once each, plus the newest


@MODES
@pytest.mark.parametrize('position', [SEG + 2000, 5 * SEG + 1], ids=['same-segment-as-the-row', 'newest-segment'])
def test_change_in_a_segment_in_use_refuses_in_both_modes(tmp_path, monkeypatch, segment_bytes, position):
    resolver, context, path, _ = _case(tmp_path, monkeypatch, segment_bytes)
    resolver._prefix_check(context, [_row(1, 10)])
    _flip(path, position)  # outside the row itself
    if segment_bytes is None:
        with pytest.raises(ValueError, match='prefix_changed'):
            resolver._prefix_check(context, [_row(1, 10)])
        return
    _segment_refusal('segment_mismatch', resolver._prefix_check, context, [_row(1, 10)])
    assert resolver._generation.bad is True
    _flip(path, position)  # restored bytes do not revive the generation
    _segment_refusal('generation_bad', resolver._prefix_check, context, [_row(1, 10)])


def test_change_outside_the_segments_in_use_is_seen_only_by_the_whole_prefix_mode(tmp_path, monkeypatch):
    """The stated difference between the modes: segment 3 holds no selected row and is not the newest."""
    whole, context, path, _ = _case(tmp_path, monkeypatch, None)
    _flip(path, 3 * SEG + 7)
    with pytest.raises(ValueError, match='prefix_changed'):
        whole._prefix_check(context, [_row(1, 10)])
    _flip(path, 3 * SEG + 7)
    by_segment, context, path, _ = _case(tmp_path, monkeypatch, SEG)
    _flip(path, 3 * SEG + 7)
    by_segment._prefix_check(context, [_row(1, 10)])
    assert by_segment._generation.bad is False
    # a row that lies in the changed segment is refused
    _segment_refusal('segment_mismatch', by_segment._prefix_check, context, [_row(3, 100)])


def test_row_outside_the_frozen_prefix_is_refused_before_any_read(tmp_path, monkeypatch):
    resolver, context, _, _ = _case(tmp_path, monkeypatch, SEG)
    _segment_refusal('input', resolver._prefix_check, context, [{'source_offset': END - 10, 'source_length': 11}])
    assert _spent(resolver) == 0 and resolver._generation.bad is False


def test_segment_check_never_reads_past_the_budget(tmp_path, monkeypatch):
    resolver, context, _, _ = _case(tmp_path, monkeypatch, SEG, limit=SEG + TAIL - 1)
    with pytest.raises(ValueError, match='source_budget'):
        resolver._prefix_check(context, [_row(1, 10)])
    assert resolver._source_budget._remaining == TAIL - 1  # segment 1 was read, the newest segment was not
    assert resolver._generation.bad is False


# The real resolver over the existing independently generated synthetic source and controls.
_spec = importlib.util.spec_from_file_location('segment_source_fixtures',
                                               Path(__file__).with_name('test_wd_mind_session_content.py'))
_fixtures = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _fixtures
_spec.loader.exec_module(_fixtures)
source_fixtures = _fixtures.source_fixtures
flat_consumer_case = _fixtures.flat_consumer_case
TAIL_RESERVE = 64 * 1024 * 1024 + 1
STABLE = ('text', 'source_event_sha256', 'event_time_utc_floor', 'source_agent')


def _resolve(resolver, frame):
    return resolver.resolve(ipc_bytes=reader._canonical(frame), expected_nonce='1' * 64, lane='codex-lead-1',
                            scope='shared', deadline_monotonic=time.monotonic() + 60)


def _rows(result):
    return [{key: row[key] for key in STABLE} for row in result['results']]


def test_segment_mode_returns_the_rows_of_the_whole_prefix_mode(flat_consumer_case, monkeypatch):
    whole, frame, _, _ = _fixtures._located_candidates(flat_consumer_case, monkeypatch)
    by_segment, _, _, _ = _fixtures._located_candidates(flat_consumer_case, monkeypatch, source_segment_bytes=SEG)
    assert whole._generation is None and type(by_segment._generation) is segments.SourceGeneration
    assert by_segment._generation.ledger.end == by_segment._cursor.offset == whole._cursor.offset
    assert by_segment._prefix_sha == whole._prefix_sha == by_segment._generation.ledger.prefix_sha256
    expected = _resolve(whole, frame)
    result = _resolve(by_segment, frame)
    assert _rows(result) == _rows(expected) and result['results']
    assert {key: value for key, value in result.items() if key != 'results'} == {
        key: value for key, value in expected.items() if key != 'results'}


def test_segment_mode_charges_one_pass_and_the_segments_in_use(flat_consumer_case, monkeypatch):
    resolver, frame, _, _ = _fixtures._located_candidates(flat_consumer_case, monkeypatch,
                                                         source_read_limit=LIMIT, source_segment_bytes=SEG)
    ledger, offset = resolver._generation.ledger, resolver._cursor.offset
    newest = ledger.span(ledger.count - 1)[1]
    # the tail reservation, the boundary byte, one whole pass and the newest segment once
    assert _spent(resolver) == TAIL_RESERVE + 1 + offset + newest
    before = resolver._source_budget._remaining
    assert _resolve(resolver, frame)['results']
    used = {ledger.count - 1}
    for item in frame['candidates']:
        used.update(segments.indexes_for(ledger, item['source_offset'], item['source_length']))
    check = sum(ledger.span(index)[1] for index in used)
    rows = sum(item['source_length'] + (item['source_offset'] > 0) for item in frame['candidates'])
    assert before - resolver._source_budget._remaining == 2 * check + 2 * rows
    assert check <= session._selected_segment_check_bytes(SEG)
    # the whole query stayed inside what the preflight asked for
    assert _spent(resolver) - TAIL_RESERVE <= (1 + offset + SEG + 2 * session._selected_segment_check_bytes(SEG)
                                               + 2 * 3 * (4 * 1024 * 1024 + 1))


def test_segment_mode_refuses_a_source_changed_in_a_segment_in_use(flat_consumer_case, monkeypatch):
    resolver, frame, _, _ = _fixtures._located_candidates(flat_consumer_case, monkeypatch, source_segment_bytes=SEG)
    ledger, source = resolver._generation.ledger, Path(resolver._controls[0])
    spans = [(item['source_offset'], item['source_length']) for item in frame['candidates']]
    used = set()
    for offset, length in spans:
        used.update(segments.indexes_for(ledger, offset, length))
    # a byte of a segment that holds a selected row, outside every selected row
    position = next(place for index in sorted(used)
                    for place in range(ledger.span(index)[0], sum(ledger.span(index)))
                    if not any(offset <= place < offset + length for offset, length in spans))
    original = source.read_bytes()
    try:
        _flip(source, position)
        with pytest.raises(session.MindContentSessionRefused):
            _resolve(resolver, frame)
    finally:
        source.write_bytes(original)
    assert resolver._generation.bad is True


def test_startup_preflight_boundary_with_one_mebibyte_segments():
    budget = session._SourceReadBudget(LIMIT)
    budget.reserve(TAIL_RESERVE)
    assert session._STARTUP_SOURCE_SEGMENT_BYTES == 1024 * 1024
    assert session._selected_segment_check_bytes(1024 * 1024) == 16 * 1024 * 1024
    # the boundary byte, one pass, the newest segment, two checks of at most 16 segments, two reads of three rows
    wall = LIMIT - TAIL_RESERVE - 1 - 1024 * 1024 - 2 * 16 * 1024 * 1024 - 2 * 3 * (4 * 1024 * 1024 + 1)
    assert wall == 946864120
    session._preflight_selected_source_prefix(budget, wall, 1024 * 1024)
    with pytest.raises(session._SelectedSourcePrefixBudgetRefused):
        session._preflight_selected_source_prefix(budget, wall + 1, 1024 * 1024)
    # the whole-prefix mode keeps its own boundary
    session._preflight_selected_source_prefix(budget, 251658239)
    with pytest.raises(session._SelectedSourcePrefixBudgetRefused):
        session._preflight_selected_source_prefix(budget, 251658240)


@pytest.mark.parametrize('invalid', [True, 1.5, '4096', SEG + 1, SEG // 2, 3 * SEG])
def test_invalid_segment_size_is_refused(flat_consumer_case, monkeypatch, invalid):
    with pytest.raises(session.MindContentSessionRefused):
        _fixtures._located_candidates(flat_consumer_case, monkeypatch, source_segment_bytes=invalid)
