"""The fixed startup question orders rows newest first inside a relevance band.

On the real log the e5 cosine of every eligible row is far above the 0.55
floor and tightly packed, so cosine order alone returned rows 18 to 108 days
old to every lane on 2026-11-25. Any other query keeps cosine order.
"""
import ast
import datetime as dt
from pathlib import Path

from tools import wd_mind_full_runtime as runtime

ROOT = Path(__file__).resolve().parents[2]
NOW = dt.datetime(2026, 11, 25, 20, 0, tzinfo=dt.timezone.utc)


def _row(name, score, days, lexical=0):
    return (name, score, lexical, NOW - dt.timedelta(days=days))


def test_startup_question_prefers_newest_rows_inside_the_band():
    rows = [_row('old-best', 0.880, 18), _row('old-2', 0.875, 108), _row('fresh', 0.840, 0.01),
            _row('recent', 0.860, 2), _row('below-band', 0.820, 0.001)]
    ordered = runtime._order_selected(rows, runtime._STARTUP_PIPE_QUERY)
    assert [item[0] for item in ordered] == ['fresh', 'recent', 'old-best', 'old-2', 'below-band']


def test_band_edge_is_inclusive_and_relative_to_the_best_score():
    best = 0.9
    edge = best - runtime._STARTUP_RELEVANCE_BAND
    rows = [_row('best', best, 30), _row('edge', edge, 1), _row('under', edge - 1e-9, 0)]
    ordered = runtime._order_selected(rows, runtime._STARTUP_PIPE_QUERY)
    assert [item[0] for item in ordered] == ['edge', 'best', 'under']


def test_rows_below_a_narrow_band_fill_the_answer_in_cosine_order():
    rows = [_row('best', 0.95, 40), _row('low-new', 0.70, 0.01), _row('low-higher', 0.80, 90)]
    ordered = runtime._order_selected(rows, runtime._STARTUP_PIPE_QUERY)
    assert [item[0] for item in ordered][:3] == ['best', 'low-higher', 'low-new']


def test_other_queries_keep_cosine_then_lexical_then_time_order():
    rows = [_row('a', 0.80, 0.01), _row('b', 0.88, 50, lexical=1), _row('c', 0.88, 40, lexical=1),
            _row('d', 0.88, 1, lexical=2)]
    ordered = runtime._order_selected(rows, 'some other question')
    assert [item[0] for item in ordered] == ['d', 'c', 'b', 'a']


def test_empty_and_single_inputs():
    assert runtime._order_selected([], runtime._STARTUP_PIPE_QUERY) == []
    one = [_row('only', 0.7, 3)]
    assert runtime._order_selected(one, runtime._STARTUP_PIPE_QUERY) == one


def test_equal_time_inside_band_falls_back_to_score_then_id():
    rows = [_row('z', 0.86, 1), _row('y', 0.87, 1), _row('x', 0.87, 1)]
    ordered = runtime._order_selected(rows, runtime._STARTUP_PIPE_QUERY)
    assert [item[0] for item in ordered] == ['x', 'y', 'z']


def test_every_flat_query_path_uses_the_shared_order():
    source = (ROOT / 'tools' / 'wd_mind_full_runtime.py').read_text(encoding='utf-8')
    assert 'selected.sort(' not in source
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == '_order_selected']
    assert len(calls) == 3
