import datetime as dt
import json

import pytest

from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_source_budget_report as report

NOW = dt.datetime(2026, 11, 26, 12, 0, tzinfo=dt.timezone.utc)
READER_TEXT = ("_COMPLETE_SOURCE_PREFIX_BYTES = 10 * 1024**3\n"
               "_EAGER_MEMORY_BUDGET_BYTES = 10 * 1024**3\n"
               "_EAGER_MEMORY_BYTES_PER_SOURCE_BYTE = 40\n")


def _log(tmp_path, rows):
    path = tmp_path / 'events.jsonl'
    with path.open('wb') as handle:
        for days_ago, pad in rows:
            stamp = (NOW - dt.timedelta(days=days_ago)).isoformat().replace('+00:00', 'Z')
            handle.write((json.dumps({'ts_utc': stamp, 'message': 'x' * pad}) + '\n').encode())
    return path


def _bundle(tmp_path, text=READER_TEXT):
    bundle = tmp_path / 'bundle'
    target = bundle / report.READER_IN_BUNDLE
    target.parent.mkdir(parents=True)
    target.write_text(text, encoding='utf-8')
    pointer = tmp_path / 'pointer.json'
    pointer.write_text(json.dumps({'active_bundle': str(bundle), 'source_commit': '6' * 40}), encoding='utf-8')
    return pointer


def test_installed_limit_is_used_and_the_difference_is_reported(tmp_path):
    path = _log(tmp_path, [(1, 10)])
    out = report.measure(path, now=NOW, pointer=_bundle(tmp_path))
    assert out['limit_source'] == 'active_bundle'
    assert out['limit_bytes'] == out['active_bundle_limit_bytes'] == 256 * 1024**2
    assert out['code_limit_bytes'] == reader._max_eager_source_bytes() == 640 * 1024**2
    assert out['limits_differ'] is True and out['active_bundle'] == '6' * 40


def test_code_limit_when_the_bundle_cannot_be_read(tmp_path):
    path = _log(tmp_path, [(1, 10)])
    out = report.measure(path, now=NOW, pointer=tmp_path / 'missing.json')
    assert out['limit_source'] == 'code' and out['active_bundle_limit_bytes'] is None
    assert out['limit_bytes'] == 640 * 1024**2 and out['limits_differ'] is False


def test_override_wins(tmp_path):
    path = _log(tmp_path, [(1, 10)])
    out = report.measure(path, now=NOW, limit=1000, pointer=_bundle(tmp_path))
    assert out['limit_source'] == 'override' and out['limit_bytes'] == 1000


def test_reader_text_parse_matches_the_imported_reader():
    text = (report.ROOT / 'tools' / 'wd_mind_content_query_readonly.py').read_text(encoding='utf-8')
    assert report.limit_from_reader_source(text) == reader._max_eager_source_bytes()


@pytest.mark.parametrize('text', [
    READER_TEXT.replace('= 40', '= int("40")'),
    READER_TEXT.replace('= 40', '= 0'),
    READER_TEXT + '_EAGER_MEMORY_BYTES_PER_SOURCE_BYTE = 1\n',
    READER_TEXT.replace('_EAGER_MEMORY_BUDGET_BYTES = 10 * 1024**3\n', ''),
    READER_TEXT.replace('1024**3', '1024**999'),
])
def test_reader_text_parse_refuses_anything_but_plain_int_constants(text):
    with pytest.raises(ValueError):
        report.limit_from_reader_source(text)


def test_growth_counts_only_rows_inside_each_window(tmp_path):
    path = _log(tmp_path, [(30, 1000), (20, 100), (5, 100), (1, 100)])
    out = report.measure(path, days=(14, 30), now=NOW, limit=10**6)
    lines = path.read_bytes().splitlines(keepends=True)
    in14, in30 = sum(map(len, lines[2:])), sum(map(len, lines[0:]))
    assert [w['window_bytes'] for w in out['windows']] == [in14, in30]
    assert out['windows'][0]['days_left'] == (10**6 - path.stat().st_size) * 14 // in14


def test_over_limit_and_no_growth(tmp_path):
    path = _log(tmp_path, [(40, 100)])
    assert report.measure(path, days=(14,), now=NOW, limit=10)['windows'][0]['days_left'] == 0
    window = report.measure(path, days=(14,), now=NOW, limit=10**6)['windows'][0]
    assert window['days_left'] is None and window['limit_date_utc'] is None


def test_bad_rows_are_skipped_and_text_is_never_reported(tmp_path):
    path = tmp_path / 'events.jsonl'
    good = json.dumps({'ts_utc': '2026-11-25T12:00:00Z', 'message': 'secret words'}).encode()
    path.write_bytes(b'not json\n{"ts_utc": "bad"}\n' + good + b'\n')
    out = report.measure(path, days=(2,), now=NOW, limit=10**6)
    assert out['windows'][0]['window_bytes'] == len(good) + 1
    assert 'secret' not in json.dumps(out)


def test_days_and_limit_must_be_bounded(tmp_path):
    path = _log(tmp_path, [(1, 1)])
    for bad in (0, 366, 1.5):
        with pytest.raises(ValueError):
            report.measure(path, days=(bad,), now=NOW, limit=10)
    with pytest.raises(ValueError):
        report.measure(path, days=(14,), now=NOW, limit=0)


def test_cli_json(tmp_path, capsys):
    path = _log(tmp_path, [(1, 10)])
    assert report.main(['--log', str(path), '--json', '--limit', '5000']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['schema'] == 'wd.mind-source-budget-report.v2' and out['limit_source'] == 'override'
