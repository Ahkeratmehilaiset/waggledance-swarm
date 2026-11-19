"""Synthetic checks of the owner's segment mode for the retained prefix check; never reads a live bridge log.

A hand-built retained state runs ``_prefix`` over a small synthetic file in both modes, so the one difference
between them is stated by a test: a change outside the segments in use is seen at once by the whole-prefix mode,
and by the segment mode only when the scrub turn reaches that segment. The budget formulas are tested as numbers.
"""
import hashlib
from types import SimpleNamespace

import pytest

from tools import wd_mind_absent_generation_prefix_readonly as prefix
from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_source_segments as segments

SEG = segments.MIN_SEGMENT_BYTES
TAIL = 100
END = 5 * SEG + TAIL
LIMIT = 1024 * 1024
ROW = 4 * 1024 * 1024
MODES = pytest.mark.parametrize('segment_bytes', [None, SEG], ids=['whole-prefix', 'segments'])


def _span(index, offset, length=50):
    return index * SEG + offset, length


def _case(tmp_path, monkeypatch, segment_bytes, *, scrub_bytes=SEG, limit=LIMIT):
    """A hand-built retained state over five whole segments plus a short newest one."""
    path = tmp_path / 'synthetic-source'
    path.write_bytes(bytes((index * 31 + index // 251) % 251 for index in range(END)))
    bridge = SimpleNamespace(_open_log=lambda source: open(source, 'rb', buffering=0),
                             _file_identity=lambda stream: 'synthetic-identity')
    fences = []
    monkeypatch.setattr(reader, '_narrow_dependencies', lambda: (bridge, prefix))
    monkeypatch.setattr(reader, '_narrow_fence', lambda *args: fences.append(args[3]))
    monkeypatch.setattr(runtime, '_OWNER_SOURCE_SEGMENT_BYTES', segment_bytes)
    monkeypatch.setattr(runtime, '_OWNER_SOURCE_SCRUB_BYTES', scrub_bytes)
    cursor = SimpleNamespace(offset=END, file_identity='synthetic-identity', generation=None)
    context = {'messages': {'source_path': str(path), 'generation_path': str(tmp_path / 'synthetic-sidecar'),
                            'end_cursor': cursor},
               'decision': {'source_prefix': {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}}
    retained = object.__new__(runtime._RetainedFlatQueryState)
    retained._segments, retained._spans, retained._scrub_due = None, (), False
    budget = prefix._SourceReadBudget(limit)
    return SimpleNamespace(retained=retained, context=context, path=path, budget=budget, fences=fences,
                           cursor=cursor, limit=limit)


def _check(case, spans=(), scrub=False):
    case.retained._spans, case.retained._scrub_due = tuple(spans), scrub
    return case.retained._prefix(case.context, source_budget=case.budget)


def _spent(case):
    return case.limit - case.budget._remaining


def _flip(path, position):
    data = bytearray(path.read_bytes())
    data[position] ^= 1
    path.write_bytes(bytes(data))


def _segment_refusal(reason, case, *args, **kwargs):
    with pytest.raises(segments.SourceSegmentsRefused) as caught:
        _check(case, *args, **kwargs)
    assert caught.value.reason == reason


@MODES
def test_unchanged_prefix_passes_and_charges_the_bytes_of_its_mode(tmp_path, monkeypatch, segment_bytes):
    case = _case(tmp_path, monkeypatch, segment_bytes)
    assert _check(case)[0] == 'synthetic-identity'
    assert _spent(case) == END  # the first check is one whole pass in both modes
    assert (case.retained._segments is None) == (segment_bytes is None)
    _check(case, [_span(1, 10)])
    # whole-prefix mode hashes everything again; segment mode reads segment 1 and the newest segment only
    assert _spent(case) == (2 * END if segment_bytes is None else END + SEG + TAIL)
    _check(case)
    assert _spent(case) == (3 * END if segment_bytes is None else END + SEG + 2 * TAIL)
    assert case.fences == [case.cursor] * 6  # one fence before and one after every check


def test_first_check_builds_a_table_bound_to_the_pinned_digest(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, SEG)
    _check(case)
    table = case.retained._segments
    assert type(table) is segments.SourceGeneration and table.ledger.count == 6
    assert table.ledger.end == END and table.ledger.segment_bytes == SEG
    assert table.ledger.prefix_sha256 == case.context['decision']['source_prefix']['sha256']
    assert table.file_identity == 'synthetic-identity' and table.generation is None
    _check(case)
    assert case.retained._segments is table  # a passed check keeps the same table


def test_first_check_refuses_a_prefix_that_is_not_the_pinned_one(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, SEG)
    _flip(case.path, 2 * SEG + 7)
    _segment_refusal('prefix_mismatch', case)
    assert case.retained._segments is None and _spent(case) == END


def test_row_across_a_segment_edge_checks_both_segments(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, SEG)
    _check(case)
    _check(case, [_span(1, SEG - 10, 20), _span(1, 5), _span(3, 0, SEG)])
    assert _spent(case) == END + 3 * SEG + TAIL  # segments 1, 2 and 3 once each, plus the newest


def test_scrub_turn_reads_the_next_segments_once_and_clears_itself(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, SEG, scrub_bytes=2 * SEG)
    _check(case)
    _check(case, scrub=True)
    assert _spent(case) == END + TAIL + 2 * SEG and case.retained._scrub_due is False
    _check(case)
    assert _spent(case) == END + 2 * TAIL + 2 * SEG  # no second scrub turn without a new query
    _check(case, scrub=True)
    _check(case, scrub=True)
    # three turns of two segments went once around all six segments: 5 * SEG + TAIL in total
    assert _spent(case) == END + 4 * TAIL + (5 * SEG + TAIL)


def test_the_whole_pass_that_builds_the_table_takes_no_scrub_turn(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, SEG)
    _check(case, scrub=True)
    assert _spent(case) == END and case.retained._scrub_due is True  # left for a check that has a table


@MODES
def test_change_outside_the_segments_in_use_is_the_one_difference_between_the_modes(
        tmp_path, monkeypatch, segment_bytes):
    case = _case(tmp_path, monkeypatch, segment_bytes)
    _check(case)
    _flip(case.path, 3 * SEG + 7)
    if segment_bytes is None:
        with pytest.raises(ValueError, match='^prefix_changed$'):
            _check(case, [_span(1, 10)])
        return
    _check(case, [_span(1, 10)])  # segment 3 is neither a row's segment nor the newest: not read by this check
    # The scrub turn reaches it, one segment per turn: segments 0 and 2 pass (segment 1 was just read as the
    # row's segment, so the turn skips it) and segment 3 refuses.
    for _ in range(2):
        _check(case, [_span(1, 10)], scrub=True)
    _segment_refusal('segment_mismatch', case, [_span(1, 10)], scrub=True)
    assert case.retained._segments is None  # a failed check keeps no table
    # Without a table the next check is a whole pass again, and the changed prefix is refused there too.
    _segment_refusal('prefix_mismatch', case)
    assert case.retained._segments is None


@pytest.mark.parametrize('position', [SEG + 7, 5 * SEG + 3], ids=['row-segment', 'newest-segment'])
def test_change_inside_a_segment_in_use_is_refused_by_the_next_check(tmp_path, monkeypatch, position):
    case = _case(tmp_path, monkeypatch, SEG)
    _check(case)
    _flip(case.path, position)
    _segment_refusal('segment_mismatch', case, [_span(1, 10)])
    assert case.retained._segments is None


def test_table_of_another_prefix_is_refused_and_dropped(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, SEG)
    _check(case)
    case.context['decision']['source_prefix']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='^segments_binding$'):
        _check(case)
    assert case.retained._segments is None and _spent(case) == END  # refused before any read
    case.retained._segments = object()
    with pytest.raises(ValueError, match='^segments_binding$'):
        _check(case)
    assert case.retained._segments is None


@pytest.mark.parametrize('first', [True, False], ids=['building', 'checking'])
def test_failed_closing_fence_keeps_no_table(tmp_path, monkeypatch, first):
    case = _case(tmp_path, monkeypatch, SEG)
    if not first:
        _check(case)
    calls = []

    def fence(*args):
        calls.append(args[3])
        if len(calls) == 2:
            raise ValueError('synthetic_fence')
    monkeypatch.setattr(reader, '_narrow_fence', fence)
    with pytest.raises(ValueError, match='^synthetic_fence$'):
        _check(case)
    assert case.retained._segments is None


@MODES
def test_every_read_is_reserved_before_it_is_made(tmp_path, monkeypatch, segment_bytes):
    case = _case(tmp_path, monkeypatch, segment_bytes, limit=END - 1)
    with pytest.raises(ValueError, match='^source_budget$'):
        _check(case)
    assert case.retained._segments is None and case.budget._remaining >= 0
    if segment_bytes is not None:
        case = _case(tmp_path, monkeypatch, segment_bytes, limit=END + TAIL + SEG - 1)
        _check(case)
        with pytest.raises(ValueError, match='^source_budget$'):
            _check(case, [_span(1, 10)])  # needs SEG + TAIL, one byte more than is left
        assert case.retained._segments is None and case.budget._remaining >= 0


def test_strict_mode_ignores_and_drops_a_table(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, SEG)
    _check(case)
    monkeypatch.setattr(runtime, '_OWNER_SOURCE_SEGMENT_BYTES', None)
    _check(case, [_span(1, 10)], scrub=True)
    assert _spent(case) == 2 * END and case.retained._segments is None


def _snapshot(offset):
    return {'messages': {'end_cursor': SimpleNamespace(offset=offset)}}


def test_request_allowance_formula_per_mode(monkeypatch):
    check = segments.max_check_bytes(1024 * 1024, 3, ROW)
    assert check == 16 * 1024 * 1024
    fixed = 3 * check + 64 * 1024 * 1024
    assert runtime._owner_prefix_check_bytes(1000, False) == 1000 + fixed
    assert runtime._owner_prefix_check_bytes(1000, True) == fixed
    monkeypatch.setattr(runtime, '_OWNER_SOURCE_SEGMENT_BYTES', None)
    assert runtime._owner_prefix_check_bytes(1000, False) == 3000
    assert runtime._owner_prefix_check_bytes(1000, True) == 3000  # strict mode has no table to be ready


@pytest.mark.parametrize('budget, rows', [('_startup_owner_source_budget', 3), ('_local_query_source_budget', 6)])
@pytest.mark.parametrize('ready', [False, True], ids=['table-not-built', 'table-ready'])
def test_preflight_boundary_in_segment_mode(monkeypatch, budget, rows, ready):
    offset = 1000
    expected = runtime._owner_prefix_check_bytes(offset, ready) + rows * (ROW + 1) + 3
    call = getattr(runtime, budget)
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_READ_LIMIT', expected - 1)
    with pytest.raises(runtime._StartupSourceBudgetRefused):
        call(_snapshot(offset), segments_ready=ready)
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_READ_LIMIT', expected)
    assert call(_snapshot(offset), segments_ready=ready)._remaining == expected


@pytest.mark.parametrize('budget', ['_startup_owner_source_budget', '_local_query_source_budget'])
def test_request_with_a_table_does_not_depend_on_the_source_size(monkeypatch, budget):
    call, huge = getattr(runtime, budget), 100 * 1024 ** 3
    assert type(call(_snapshot(huge), segments_ready=True)) is prefix._SourceReadBudget
    with pytest.raises(runtime._StartupSourceBudgetRefused):
        call(_snapshot(huge), segments_ready=False)  # the one whole pass still has to fit
    with pytest.raises(runtime._StartupSourceBudgetRefused):
        call(_snapshot(huge))
    monkeypatch.setattr(runtime, '_OWNER_SOURCE_SEGMENT_BYTES', None)
    with pytest.raises(runtime._StartupSourceBudgetRefused):
        call(_snapshot(huge), segments_ready=True)  # strict mode: three whole passes at every request


@pytest.mark.parametrize('budget', ['_startup_owner_source_budget', '_local_query_source_budget'])
@pytest.mark.parametrize('ready', [None, 1, 0, 'yes'])
def test_table_flag_must_be_a_real_boolean(budget, ready):
    with pytest.raises(ValueError, match='^source_budget$'):
        getattr(runtime, budget)(_snapshot(1000), segments_ready=ready)
