"""Read-only report: how much of the Mind source limit the bridge log uses, and for how long it lasts.

The Mind owner reads the bridge message log eagerly up to
``reader._max_eager_source_bytes()`` (640 MiB after PR 2189). Past that the
owner refuses with SOURCE_BUDGET and every lane gets no Mind context. This
report gives the operator the numbers to act in time: the log size, the limit,
the share used, the growth per day over a recent window, and the days left at
that rate. It reads the log as bytes and the timestamps of its rows. It never
imports or writes anything else and prints no event text.

    python -I -B -X utf8 tools/wd_mind_source_budget_report.py --log <events.jsonl> [--days 14] [--json]
"""
import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import wd_mind_content_query_readonly as reader  # noqa: E402


def _stamp(line):
    try:
        value = json.loads(line).get('ts_utc')
        return dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(dt.timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None


def measure(log_path, *, days=14, now=None, limit=None):
    """Return the budget figures for ``log_path``; growth counts bytes of rows stamped in the window."""
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError('days')
    path = Path(log_path)
    size = path.stat().st_size
    limit = reader._max_eager_source_bytes() if limit is None else limit
    now = now or dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=days)
    window_bytes, first, last = 0, None, None
    with path.open('rb') as handle:
        for line in handle:
            stamp = _stamp(line)
            if stamp is None or stamp < start or stamp > now:
                continue
            window_bytes += len(line)
            first = stamp if first is None or stamp < first else first
            last = stamp if last is None or stamp > last else last
    per_day = window_bytes / days
    left = limit - size
    if left <= 0:
        days_left = 0
    elif per_day <= 0:
        days_left = None
    else:
        days_left = math.floor(left / per_day)
    try:
        limit_date = None if days_left is None else (now + dt.timedelta(days=days_left)).date().isoformat()
    except OverflowError:
        limit_date = None
    return {'schema': 'wd.mind-source-budget-report.v1', 'authority': 'none',
            'log_bytes': size, 'limit_bytes': limit,
            'used_percent': round(100 * size / limit, 1),
            'window_days': days, 'window_bytes': window_bytes,
            'growth_bytes_per_day': round(per_day),
            'days_left': days_left,
            'limit_date_utc': limit_date,
            'window_rows_from_utc': None if first is None else first.isoformat(timespec='seconds'),
            'window_rows_to_utc': None if last is None else last.isoformat(timespec='seconds')}


def _mib(value):
    return '%.1f MiB' % (value / 1024**2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--log', required=True)
    parser.add_argument('--days', type=int, default=14)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    report = measure(args.log, days=args.days)
    if args.json:
        print(json.dumps(report, sort_keys=True))
        return 0
    if report['days_left'] is None:
        left = 'no growth in the window'
    else:
        left = '%d days (about %s)' % (report['days_left'], report['limit_date_utc'] or 'beyond the calendar')
    print('Mind source: %s of %s (%.1f %%)' % (_mib(report['log_bytes']), _mib(report['limit_bytes']),
                                                report['used_percent']))
    print('Growth: %s per day over the last %d days' % (_mib(report['growth_bytes_per_day']), report['window_days']))
    print('Left at that rate: ' + left)
    return 0


if __name__ == '__main__':
    sys.exit(main())
