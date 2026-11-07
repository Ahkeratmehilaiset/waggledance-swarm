"""Default-off fixed CLI for exactly one hash-bound continuation attempt."""
import hmac
import json
import os
import re
import sys

from tools import wd_q4_continuation_orchestrator as orchestrator
from tools.wd_q4_descriptor_cli import _read_pinned
from tools.wd_q4_genesis_cli import _c_path, _doc

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_KEYS = frozenset({'schema', 'bundle_root', 'template_path', 'template_sha256', 'expected_commit',
    'expected_manifest_sha256', 'runtime_path', 'runtime_sha256', 'scratch_root', 'expected_host_sha256',
    'cutoff_utc', 'timeout_seconds'})
_ANSWER = frozenset({'status', 'pointer_sha256', 'runner_receipt_sha256', 'cutoff_utc', 'previous_cutoff_utc',
                     'route_sha256', 'watermark_record_sha256', 'cutoff_monotonicity'})


def _prepare(path, pin):
    config = _doc(_read_pinned(_c_path(path), 65536, pin, 'config_refused'))
    if set(config) != _KEYS or config['schema'] != 'wd.q4-continuation-orchestrator-cli.v1':
        raise ValueError()
    for name in ('template_sha256', 'expected_manifest_sha256', 'runtime_sha256', 'expected_host_sha256'):
        if type(config[name]) is not str or _HEX.fullmatch(config[name]) is None:
            raise ValueError()
    if (type(config['expected_commit']) is not str or re.fullmatch(r'[0-9a-f]{40}', config['expected_commit']) is None
            or type(config['timeout_seconds']) is not int or not 1 <= config['timeout_seconds'] <= 1800
            or not hmac.compare_digest(os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower(),
                                       config['expected_manifest_sha256'])):
        raise ValueError()
    orchestrator.runner._cutoff_datetime(config['cutoff_utc'])
    template = _doc(_read_pinned(_c_path(config['template_path']), 65536, config['template_sha256'], 'template_refused'))
    if not os.path.samefile(_c_path(config['bundle_root']), _c_path(template['bundle_root'])):
        raise ValueError()
    return {key: config[key] for key in _KEYS if key not in ('schema', 'bundle_root')}


def _result(value, inputs):
    if (type(value) is not dict or set(value) != _ANSWER
            or value['status'] not in ('DEFERRED', 'partial_ready_prefix', 'caught_up_to_cutoff')
            or value['cutoff_utc'] != inputs['cutoff_utc']
            or value['cutoff_monotonicity'] != 'COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN'
            or any(type(value[k]) is not str or _HEX.fullmatch(value[k]) is None for k in
                   ('pointer_sha256', 'runner_receipt_sha256', 'route_sha256', 'watermark_record_sha256'))):
        raise ValueError()
    orchestrator.runner._cutoff_datetime(value['previous_cutoff_utc'])
    return value


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print('{"status":"OFF"}')
        return 0
    if len(args) != 5 or args[:2] != ['--enable', '--config'] or args[3] != '--sha256' or _HEX.fullmatch(args[4]) is None:
        print('{"status":"REFUSED"}')
        return 2
    try:
        inputs = _prepare(args[2], args[4])
    except Exception:
        print('{"status":"REFUSED"}')
        return 2
    try:
        answer = _result(orchestrator.run_once(enabled=True, **inputs), inputs)
    except orchestrator.OrchestrationRefused:
        print('{"status":"REFUSED"}')
        return 2
    except BaseException:
        print('{"status":"UNKNOWN"}')
        return 3
    print(json.dumps(answer, sort_keys=True, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
