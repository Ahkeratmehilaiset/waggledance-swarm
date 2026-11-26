"""The fixed startup question takes at most one row per task before repeating a task.

Offline proxy on the real log, 2026-11-26 (aggregates only): with the 0.05
band alone a startup answer covered 2.26 distinct tasks on average; one row
per task gave 3.0. Other queries keep the plain top three.
"""
import ast
import datetime as dt
from pathlib import Path

from tools import wd_mind_full_runtime as runtime

ROOT = Path(__file__).resolve().parents[2]
NOW = dt.datetime(2026, 11, 26, 8, 0, tzinfo=dt.timezone.utc)
Q = runtime._STARTUP_PIPE_QUERY


def _rows(*names):
    return [(name, 0.9 - index * 0.001, 0, NOW - dt.timedelta(minutes=index)) for index, name in enumerate(names)]


def _records(**tasks):
    return {name: {'meta': {'agent': 'codex-lead-1', 'task_id': task}} for name, task in tasks.items()}


def test_one_row_per_task_first():
    rows = _rows('a1', 'a2', 'b1', 'a3', 'c1', 'd1')
    records = _records(a1='A', a2='A', b1='B', a3='A', c1='C', d1='D')
    assert [item[0] for item in runtime._first_three(rows, records, Q)] == ['a1', 'b1', 'c1']


def test_repeats_fill_when_fewer_than_three_tasks():
    rows = _rows('a1', 'a2', 'b1', 'a3')
    records = _records(a1='A', a2='A', b1='B', a3='A')
    assert [item[0] for item in runtime._first_three(rows, records, Q)] == ['a1', 'b1', 'a2']


def test_single_task_keeps_order():
    rows = _rows('a1', 'a2', 'a3', 'a4')
    records = _records(a1='A', a2='A', a3='A', a4='A')
    assert [item[0] for item in runtime._first_three(rows, records, Q)] == ['a1', 'a2', 'a3']


def test_other_queries_take_the_plain_top_three():
    rows = _rows('a1', 'a2', 'b1', 'c1')
    records = _records(a1='A', a2='A', b1='B', c1='C')
    assert [item[0] for item in runtime._first_three(rows, records, 'another question')] == ['a1', 'a2', 'b1']


def test_short_and_empty_inputs():
    assert runtime._first_three([], {}, Q) == []
    rows = _rows('a1', 'a2')
    records = _records(a1='A', a2='A')
    assert [item[0] for item in runtime._first_three(rows, records, Q)] == ['a1', 'a2']


def test_newer_row_of_a_chosen_task_is_never_hidden():
    """Grok and Lead on PR 2192: old top three [A_old, A_new, B] must not become [A_old, B, C]."""
    rows = [('A_old', 0.91, 0, NOW - dt.timedelta(days=3)), ('A_new', 0.84, 0, NOW - dt.timedelta(hours=1)),
            ('B', 0.80, 0, NOW - dt.timedelta(days=1)), ('C', 0.77, 0, NOW - dt.timedelta(days=2))]
    records = _records(A_old='A', A_new='A', B='B', C='C')
    ordered = runtime._order_selected(rows, Q)
    assert [item[0] for item in runtime._first_three(ordered, records, Q)] == ['A_new', 'B', 'C']


def test_fill_rows_come_newest_first_within_a_task():
    rows = [('a_best', 0.9, 0, NOW - dt.timedelta(days=5)), ('a_mid', 0.85, 0, NOW - dt.timedelta(days=2)),
            ('a_new', 0.7, 0, NOW - dt.timedelta(hours=2))]
    records = _records(a_best='A', a_mid='A', a_new='A')
    assert [item[0] for item in runtime._first_three(rows, records, Q)] == ['a_new', 'a_mid', 'a_best']


def test_every_flat_query_path_uses_the_shared_cut():
    source = (ROOT / 'tools' / 'wd_mind_full_runtime.py').read_text(encoding='utf-8')
    assert 'selected = selected[:3]' not in source
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == '_first_three']
    assert len(calls) == 3
