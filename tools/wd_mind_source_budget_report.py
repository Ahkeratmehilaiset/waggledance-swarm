"""Read-only report: how much of the Mind source limit the bridge log uses, and for how long it lasts.

The Mind owner reads the bridge message log eagerly up to its reader's
``_max_eager_source_bytes()``. Past that the owner refuses with SOURCE_BUDGET
and every lane gets no Mind context. This report gives the operator the
numbers to act in time: the log size, the limit, the share used, the growth
per day over recent windows, and the days left at each rate.

The limit that matters is the one the INSTALLED bundle enforces, which can
differ from the code this tool runs from (RCO1 on PR 2193: the repo said
640 MiB while the installed 68370cc4 still enforced 256 MiB). The tool reads
the active bundle from WD_REBOOT_STATE_CURRENT.json and parses the three
limit constants from that bundle's reader AS TEXT (it never imports or runs
bundle code). It reports both limits, uses the installed one, and says so
when they differ. ``--limit`` overrides both.

It reads the log as bytes and the timestamps of its rows, prints no event
text and writes nothing.

    python -I -B -X utf8 tools/wd_mind_source_budget_report.py --log <events.jsonl> [--days 14 --days 30] [--json]
"""
import argparse
import ast
import datetime as dt
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import wd_mind_content_query_readonly as reader  # noqa: E402

STATE_POINTER = Path(r"C:\Python\WD_REBOOT_STATE_CURRENT.json")
READER_IN_BUNDLE = Path("tools-bootstrap") / "tools" / "wd_mind_content_query_readonly.py"
_LIMIT_NAMES = ("_COMPLETE_SOURCE_PREFIX_BYTES", "_EAGER_MEMORY_BUDGET_BYTES", "_EAGER_MEMORY_BYTES_PER_SOURCE_BYTE")


def _int_expr(node):
    """Evaluate an int literal built only from +, *, ** and //; anything else is refused."""
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult, ast.Pow, ast.FloorDiv)):
        left, right = _int_expr(node.left), _int_expr(node.right)
        if isinstance(node.op, ast.Pow) and not 0 <= right <= 64:
            raise ValueError('limit_expression')
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Pow):
            return left ** right
        return left // right
    raise ValueError('limit_expression')


def limit_from_reader_source(text):
    """Parse the eager source limit from a reader's source text, without importing it."""
    values = {}
    for node in ast.parse(text).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in _LIMIT_NAMES):
            if node.targets[0].id in values:
                raise ValueError('limit_assigned_twice')
            values[node.targets[0].id] = _int_expr(node.value)
    if set(values) != set(_LIMIT_NAMES) or values["_EAGER_MEMORY_BYTES_PER_SOURCE_BYTE"] <= 0:
        raise ValueError('limit_constants')
    return min(values["_COMPLETE_SOURCE_PREFIX_BYTES"],
               values["_EAGER_MEMORY_BUDGET_BYTES"] // values["_EAGER_MEMORY_BYTES_PER_SOURCE_BYTE"])


def active_bundle_limit(pointer=STATE_POINTER):
    """Return (limit, commit) of the active bundle, or (None, reason) when it cannot be read."""
    try:
        state = json.loads(Path(pointer).read_text(encoding="utf-8-sig"))
        bundle = Path(state["active_bundle"])
        text = (bundle / READER_IN_BUNDLE).read_text(encoding="utf-8")
        return limit_from_reader_source(text), str(state.get("source_commit") or bundle.name)
    except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
        return None, type(exc).__name__


def _stamp(line):
    try:
        value = json.loads(line).get('ts_utc')
        return dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(dt.timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None


def _window(path, size, limit, days, now):
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError('days')
    start = now - dt.timedelta(days=days)
    window_bytes = 0
    with path.open('rb') as handle:
        for line in handle:
            stamp = _stamp(line)
            if stamp is not None and start <= stamp <= now:
                window_bytes += len(line)
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
    return {'days': days, 'window_bytes': window_bytes, 'growth_bytes_per_day': round(per_day),
            'days_left': days_left, 'limit_date_utc': limit_date}


def measure(log_path, *, days=(14, 30), now=None, limit=None, pointer=STATE_POINTER):
    """Return the budget figures for ``log_path``; growth counts bytes of rows stamped in each window."""
    path = Path(log_path)
    size = path.stat().st_size
    now = now or dt.datetime.now(dt.timezone.utc)
    code_limit = reader._max_eager_source_bytes()
    bundle_limit, bundle_note = active_bundle_limit(pointer)
    if limit is not None:
        if type(limit) is not int or limit <= 0:
            raise ValueError('limit')
        used, source = limit, 'override'
    elif bundle_limit is not None:
        used, source = bundle_limit, 'active_bundle'
    else:
        used, source = code_limit, 'code'
    windows = [_window(path, size, used, item, now) for item in (days if isinstance(days, (tuple, list)) else (days,))]
    return {'schema': 'wd.mind-source-budget-report.v2', 'authority': 'none',
            'log_bytes': size, 'limit_bytes': used, 'limit_source': source,
            'code_limit_bytes': code_limit,
            'active_bundle_limit_bytes': bundle_limit,
            'active_bundle': bundle_note,
            'limits_differ': bundle_limit is not None and bundle_limit != code_limit,
            'used_percent': round(100 * size / used, 1),
            'windows': windows}


def _mib(value):
    return '%.1f MiB' % (value / 1024**2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--log', required=True)
    parser.add_argument('--days', type=int, action='append')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    report = measure(args.log, days=tuple(args.days or (14, 30)), limit=args.limit)
    if args.json:
        print(json.dumps(report, sort_keys=True))
        return 0
    print('Mind source: %s of %s (%.1f %%), limit from %s' % (
        _mib(report['log_bytes']), _mib(report['limit_bytes']), report['used_percent'], report['limit_source']))
    if report['limits_differ']:
        print('NOTE: the installed bundle (%s) enforces %s; this code would allow %s. The installed limit applies '
              'until the next installation.' % (report['active_bundle'][:8], _mib(report['active_bundle_limit_bytes']),
                                                _mib(report['code_limit_bytes'])))
    elif report['active_bundle_limit_bytes'] is None and report['limit_source'] != 'override':
        print('NOTE: the active bundle limit could not be read (%s); the limit shown is this code\'s.'
              % report['active_bundle'])
    for window in report['windows']:
        if window['days_left'] is None:
            left = 'no growth in the window'
        else:
            left = '%d days (about %s)' % (window['days_left'], window['limit_date_utc'] or 'beyond the calendar')
        print('Last %d days: %s per day, left at that rate: %s' % (
            window['days'], _mib(window['growth_bytes_per_day']), left))
    return 0


if __name__ == '__main__':
    sys.exit(main())
