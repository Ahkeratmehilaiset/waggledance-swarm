import datetime as dt
import hashlib
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



def _status_inputs(tmp_path):
    commit = '6' * 40
    bundle = tmp_path / commit
    target = bundle / report.READER_IN_BUNDLE
    target.parent.mkdir(parents=True)
    target.write_text(READER_TEXT.replace('10 * 1024**3', '40000'), encoding='utf-8')
    shared = tmp_path / 'private-runtime' / 'shared'
    shared.mkdir(parents=True)
    path = _log(shared, [(1, 11), (20, 20)])
    fleet = bundle / 'wd-fleet.json'
    fleet.write_text(json.dumps({'schema_version': 2, 'runtime_root': str(shared.parent)}), encoding='utf-8')
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_text(json.dumps({
        'schema_version': 1, 'source_commit': commit,
        'files': {name: hashlib.sha256(file.read_bytes()).hexdigest()
                  for name, file in ((report.READER_IN_BUNDLE.as_posix(), target), ('wd-fleet.json', fleet))},
    }), encoding='utf-8')
    pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
    pointer = tmp_path / 'pointer.json'
    pointer.write_text(json.dumps({
        'schema_version': 1, 'active_bundle': str(bundle), 'source_commit': commit,
        'manifest_sha256': pin.upper(), 'final_commit': commit, 'final_manifest_sha256': pin.upper(),
        'installed_at_utc': '2026-11-26T10:00:00.1234567Z',
    }), encoding='utf-8')
    receipt = tmp_path / 'private-receipt.json'
    evidence = {
        'source_commit': commit, 'manifest_sha256': pin, 'status': 'VERIFIED_NONEMPTY',
        'observed_utc': '2026-11-26T11:40:00.1234567+00:00', 'native_exit': 0,
        'result_count': 1, 'result_schema': 'wd.mind-flat-verified-session.v1', 'private': 'never project this',
    }
    receipt.write_text(json.dumps(evidence), encoding='utf-8')
    return path, target, pointer, receipt, evidence


def test_status_projects_only_receipt_observation_and_budget(tmp_path):
    path, _, pointer, receipt, _ = _status_inputs(tmp_path)
    status = report.measure_status(pointer=pointer, owner_receipt=receipt, now=NOW)
    budget = report.measure(path, days=(14, 30), now=NOW, limit=1000)
    assert status == {
        'schema': 'wd.mind-operator-status.v1', 'authority': 'none',
        'owner_ready_receipt': {'state': 'VERIFIED_NONEMPTY', 'observed_utc': '2026-11-26T11:40:00.123456Z'},
        'source_budget': {'used_percent': budget['used_percent'],
                          'days_left': [{'days': w['days'], 'days_left': w['days_left']} for w in budget['windows']]},
    }


@pytest.mark.parametrize('change', [
    {'source_commit': '7' * 40},
    {'manifest_sha256': 'a' * 64},
    {'status': 'READY private text'},
    {'observed_utc': '2026-11-26T09:00:00Z'},
    {'observed_utc': '2026-11-26T13:00:00Z'},
    {'observed_utc': '2026-11-26T11:40:00'},
    {'result_count': False},
    {'native_exit': 1},
])
def test_status_refuses_unbound_or_invalid_ready_receipt(tmp_path, change):
    _, _, pointer, receipt, evidence = _status_inputs(tmp_path)
    receipt.write_text(json.dumps(evidence | change), encoding='utf-8')
    status = report.measure_status(pointer=pointer, owner_receipt=receipt, now=NOW)
    assert status['owner_ready_receipt'] == {'state': 'UNKNOWN', 'observed_utc': None}
    assert status['source_budget']['used_percent'] > 0


def test_status_reports_prior_canary_failure_and_missing_source_independently(tmp_path):
    path, _, pointer, receipt, evidence = _status_inputs(tmp_path)
    path.unlink()
    receipt.write_text(json.dumps(evidence | {'status': 'REFUSED', 'native_exit': 2}), encoding='utf-8')
    result = report.measure_status(pointer=pointer, owner_receipt=receipt, days=(14,), now=NOW)
    assert result['owner_ready_receipt']['state'] == 'REFUSED'
    assert result['source_budget'] == {'used_percent': None, 'days_left': [{'days': 14, 'days_left': None}]}


def test_status_ignores_private_paths_and_does_not_fall_back_after_pin_mismatch(tmp_path, monkeypatch, capsys):
    _, target, pointer, receipt, _ = _status_inputs(tmp_path)
    target.write_text(READER_TEXT, encoding='utf-8')
    original = report.measure_status
    monkeypatch.setattr(report, 'measure_status', lambda **kwargs: original(pointer=pointer, now=NOW, **kwargs))
    assert report.main(['--status', '--owner-receipt', str(receipt), '--days', '14']) == 0
    output = capsys.readouterr()
    status = json.loads(output.out)
    assert status['owner_ready_receipt']['state'] == 'UNKNOWN'
    assert status['source_budget'] == {'used_percent': None, 'days_left': [{'days': 14, 'days_left': None}]}
    assert 'private' not in output.out + output.err
    assert str(tmp_path) not in output.out + output.err

def test_status_discards_observations_when_deployment_pointer_changes(tmp_path, monkeypatch):
    _, _, pointer, receipt, _ = _status_inputs(tmp_path)
    window = report._window

    def rotate(*args):
        result = window(*args)
        pointer.write_text('{}', encoding='utf-8')
        return result

    monkeypatch.setattr(report, '_window', rotate)
    result = report.measure_status(pointer=pointer, owner_receipt=receipt, days=(14,), now=NOW)
    assert result['owner_ready_receipt'] == {'state': 'UNKNOWN', 'observed_utc': None}
    assert result['source_budget'] == {'used_percent': None, 'days_left': [{'days': 14, 'days_left': None}]}
