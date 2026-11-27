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

The explicit operator status view adds the state and observation time of a
selected canary receipt for the active deployment. It outputs only budget
aggregates, uses the source named by the pinned fleet, and is read-only.
VERIFIED_NONEMPTY describes the prior canary, with no live owner guarantee.

    python -I -B -X utf8 tools/wd_mind_source_budget_report.py --status [--owner-receipt <canary.json>] [--days 14]

    python -I -B -X utf8 tools/wd_mind_source_budget_report.py --log <events.jsonl> [--days 14 --days 30] [--json]
"""
import argparse
import ast
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import wd_mind_content_query_readonly as reader  # noqa: E402
from tools.wd_mind_absent_generation_prefix_readonly import (  # noqa: E402
    MAX_CONTROL_BYTES, _deployment_source, _plain, _read_pinned, _unique_object,
)

STATE_POINTER = Path(r"C:\Python\WD_REBOOT_STATE_CURRENT.json")
READER_IN_BUNDLE = Path("tools-bootstrap") / "tools" / "wd_mind_content_query_readonly.py"
_LIMIT_NAMES = ("_COMPLETE_SOURCE_PREFIX_BYTES", "_EAGER_MEMORY_BUDGET_BYTES", "_EAGER_MEMORY_BYTES_PER_SOURCE_BYTE")
_STATUS_CONTROL_BYTES = 64 * 1024
_STATUS_READ_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError, SyntaxError, RecursionError, ArithmeticError)
_OWNER_RECEIPT_STATES = (
    'PRECHECK_FAILED', 'WRAPPER_FAILED', 'REFUSED', 'INVALID_RESULT', 'EMPTY_SUCCESS', 'VERIFIED_NONEMPTY',
)


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
    remaining = size
    with path.open('rb') as handle:
        while remaining:
            line = handle.readline(remaining)
            if not line:
                raise ValueError('source_truncated')
            remaining -= len(line)
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


def _read_status_control(path):
    _plain(path, directory=False)
    with path.open('rb') as handle:
        raw = handle.read(_STATUS_CONTROL_BYTES + 1)
    if not 0 < len(raw) <= _STATUS_CONTROL_BYTES:
        raise ValueError('control_size')
    return raw


def _control_object(raw):
    value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object)
    if type(value) is not dict:
        raise ValueError('control_object')
    return value


def _utc_stamp(value):
    if type(value) is not str:
        raise ValueError('receipt_time')
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    stamp = dt.datetime.fromisoformat(value)
    if stamp.utcoffset() != dt.timedelta(0):
        raise ValueError('receipt_time')
    return stamp


def _installed_status_binding(raw):
    state = _control_object(raw)
    bundle = Path(state['active_bundle'])
    commit = state['source_commit']
    manifest_pin = state['manifest_sha256']
    if (type(state.get('schema_version')) is not int or state['schema_version'] != 1
            or not re.fullmatch(r'[0-9a-f]{40}', commit) or bundle.name != commit
            or state['final_commit'] != commit or state['final_manifest_sha256'] != manifest_pin):
        raise ValueError('active_deployment')
    manifest = _control_object(_read_pinned(bundle / 'deployment-manifest.json', manifest_pin, MAX_CONTROL_BYTES))
    if manifest.get('source_commit') != commit:
        raise ValueError('manifest_commit')
    reader_pin = manifest['files'][READER_IN_BUNDLE.as_posix()]
    installed_source = _read_pinned(bundle / READER_IN_BUNDLE, reader_pin, MAX_CONTROL_BYTES)
    limit = limit_from_reader_source(installed_source.decode('utf-8'))
    if limit <= 0:
        raise ValueError('installed_limit')
    return bundle, commit, manifest_pin.lower(), _utc_stamp(state['installed_at_utc']), limit


def _owner_observation(path, commit, manifest_pin, installed_at, now):
    value = _control_object(_read_status_control(Path(path)))
    if value['source_commit'] != commit or value['manifest_sha256'].lower() != manifest_pin:
        raise ValueError('receipt_binding')
    state = value['status']
    observed = _utc_stamp(value['observed_utc'])
    if state not in _OWNER_RECEIPT_STATES or not installed_at <= observed <= now:
        raise ValueError('receipt_observation')
    if state in ('VERIFIED_NONEMPTY', 'EMPTY_SUCCESS'):
        count = value['result_count']
        native_exit = value['native_exit']
        if (value['result_schema'] != 'wd.mind-flat-verified-session.v1'
                or type(native_exit) is not int or native_exit != 0
                or type(count) is not int or not 0 <= count <= 3
                or (count > 0) != (state == 'VERIFIED_NONEMPTY')):
            raise ValueError('receipt_result')
    return {'state': state, 'observed_utc': observed.isoformat().replace('+00:00', 'Z')}


def measure_status(*, owner_receipt=None, days=(14, 30), now=None, pointer=STATE_POINTER):
    """Project the selected canary observation and the active source's budget as aggregates."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if any(type(day) is not int or not 1 <= day <= 365 for day in days):
        raise ValueError('days')
    unknown_owner = {'state': 'UNKNOWN', 'observed_utc': None}
    unknown_budget = {'used_percent': None, 'days_left': [{'days': day, 'days_left': None} for day in days]}
    result = {'schema': 'wd.mind-operator-status.v1', 'authority': 'none',
              'owner_ready_receipt': unknown_owner, 'source_budget': unknown_budget}
    pointer = Path(pointer)
    try:
        raw = _read_status_control(pointer)
        bundle, commit, manifest_pin, installed_at, limit = _installed_status_binding(raw)
    except _STATUS_READ_ERRORS:
        return result

    if owner_receipt is not None:
        try:
            result['owner_ready_receipt'] = _owner_observation(owner_receipt, commit, manifest_pin, installed_at, now)
        except _STATUS_READ_ERRORS:
            pass

    try:
        source, _, _ = _deployment_source(str(bundle), manifest_pin)
        before = source.stat()
        windows = [_window(source, before.st_size, limit, day, now) for day in days]
        after = source.stat()
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino) or after.st_size < before.st_size:
            raise ValueError('source_changed')
        result['source_budget'] = {
            'used_percent': round(100 * before.st_size / limit, 1),
            'days_left': [{'days': w['days'], 'days_left': w['days_left']} for w in windows],
        }
    except _STATUS_READ_ERRORS:
        pass

    try:
        if _read_status_control(pointer) == raw:
            return result
    except _STATUS_READ_ERRORS:
        pass
    return {**result, 'owner_ready_receipt': unknown_owner, 'source_budget': unknown_budget}


def _mib(value):
    return '%.1f MiB' % (value / 1024**2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--log')
    parser.add_argument('--days', type=int, action='append')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--status', action='store_true', help='print the operator status view as JSON')
    parser.add_argument('--owner-receipt', help='manual canary JSON for the active deployment')
    args = parser.parse_args(argv)
    days = tuple(args.days or (14, 30))
    if any(not 1 <= day <= 365 for day in days):
        parser.error('--days must be between 1 and 365')
    if args.status:
        if args.log is not None or args.limit is not None:
            parser.error('--status selects the active source and limit')
        status = measure_status(owner_receipt=args.owner_receipt, days=days)
        print(json.dumps(status, sort_keys=True))
        return 0
    if args.log is None or args.owner_receipt is not None:
        parser.error('use --log for the budget report and --owner-receipt with --status')
    report = measure(args.log, days=days, limit=args.limit)
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
