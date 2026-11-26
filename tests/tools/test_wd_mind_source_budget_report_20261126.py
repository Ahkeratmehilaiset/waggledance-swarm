import datetime as dt
import json

import pytest

from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_source_budget_report as report

NOW = dt.datetime(2026, 11, 26, 12, 0, tzinfo=dt.timezone.utc)


def _log(tmp_path, rows):
    path = tmp_path / 'events.jsonl'
    with path.open('wb') as handle:
        for days_ago, pad in rows:
            stamp = (NOW - dt.timedelta(days=days_ago)).isoformat().replace('+00:00', 'Z')
            handle.write((json.dumps({'ts_utc': stamp, 'message': 'x' * pad}) + '\n').encode())
    return path


def test_default_limit_is_the_reader_eager_limit(tmp_path):
    path = _log(tmp_path, [(1, 10)])
    assert report.measure(path, now=NOW)['limit_bytes'] == reader._max_eager_source_bytes() == 640 * 1024**2


def test_growth_counts_only_rows_inside_the_window(tmp_path):
    path = _log(tmp_path, [(30, 1000), (5, 100), (1, 100)])
    out = report.measure(path, days=14, now=NOW, limit=10**6)
    inside = sum(len(line) for line in path.read_bytes().splitlines(keepends=True)[1:])
    assert out['window_bytes'] == inside
    assert out['growth_bytes_per_day'] == round(inside / 14)
    assert out['days_left'] == (10**6 - path.stat().st_size) * 14 // inside


def test_over_limit_and_no_growth(tmp_path):
    path = _log(tmp_path, [(30, 100)])
    assert report.measure(path, now=NOW, limit=10)['days_left'] == 0
    out = report.measure(path, now=NOW, limit=10**6)
    assert out['days_left'] is None and out['limit_date_utc'] is None


def test_bad_rows_are_skipped_and_text_is_never_reported(tmp_path):
    path = tmp_path / 'events.jsonl'
    good = json.dumps({'ts_utc': '2026-11-25T12:00:00Z', 'message': 'secret words'}).encode()
    path.write_bytes(b'not json\n{"ts_utc": "bad"}\n' + good + b'\n')
    out = report.measure(path, days=2, now=NOW, limit=10**6)
    assert out['window_bytes'] == len(good) + 1
    assert 'secret' not in json.dumps(out)


def test_days_must_be_bounded(tmp_path):
    path = _log(tmp_path, [(1, 1)])
    for bad in (0, 366, 1.5):
        with pytest.raises(ValueError):
            report.measure(path, days=bad, now=NOW)


def test_cli_json(tmp_path, capsys):
    path = _log(tmp_path, [(1, 10)])
    assert report.main(['--log', str(path), '--json']) == 0
    assert json.loads(capsys.readouterr().out)['schema'] == 'wd.mind-source-budget-report.v1'
