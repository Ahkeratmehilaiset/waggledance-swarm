"""Explicit, default-off command line for preparing a staged Q4 descriptor.

With no --enable flag this command reports OFF and opens no request, bundle or
stage files. --enable requires all nine named inputs. PREPARED means byte-bound
staging only, never installation, activation, freshness or runtime evidence.
INCOMPLETE and UNKNOWN require inspection before retry; no cleanup is inferred.
"""
import json
import re
import sys

from tools import wd_q4_descriptor_cli as output
from tools import wd_q4_staged_composer as composer

_FLAGS = {'--request': 'request_path', '--stage': 'stage_dir', '--bundle': 'expected_bundle',
          '--commit': 'expected_commit', '--manifest-sha256': 'expected_manifest_sha256',
          '--public-plan-sha256': 'expected_public_plan_sha256', '--generation-root': 'generation_root',
          '--bundle-store-root': 'bundle_store_root', '--active-bundle-root': 'active_bundle_root'}
_RESULT = {'status', 'bundle_commit', 'manifest_sha256', 'binding_sha256', 'descriptor_path',
           'descriptor_sha256', 'pointer_path', 'pointer_sha256'}
_HEX = re.compile(r'[0-9a-f]{64}\Z')


def _emit(value):
    return output._write(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n')


def _parse(argv):
    values = {}
    enabled = False
    pos = 0
    while pos < len(argv):
        flag = argv[pos]
        if flag == '--enable':
            if enabled: raise ValueError()
            enabled = True
            pos += 1
            continue
        if flag not in _FLAGS or _FLAGS[flag] in values or pos + 1 >= len(argv):
            raise ValueError()
        value = argv[pos + 1]
        if type(value) is not str or not value or value.startswith('--'):
            raise ValueError()
        values[_FLAGS[flag]] = value
        pos += 2
    if enabled and set(values) != set(_FLAGS.values()):
        raise ValueError()
    return enabled, values


def _run(argv):
    try:
        enabled, values = _parse(list(argv))
    except Exception:
        _emit({'status': 'REFUSED'})
        return 2
    if not enabled:
        return 0 if _emit({'status': 'OFF'}) else 1
    try:
        result = composer.compose(enabled=True, **values)
        if (type(result) is not dict or set(result) != _RESULT or result['status'] != 'PREPARED'
                or result['bundle_commit'] != values['expected_commit']
                or result['manifest_sha256'] != values['expected_manifest_sha256']
                or any(type(result[key]) is not str or _HEX.fullmatch(result[key]) is None
                       for key in ('binding_sha256', 'descriptor_sha256', 'pointer_sha256'))
                or any(type(result[key]) is not str or not result[key] or not result[key].isprintable()
                       for key in ('descriptor_path', 'pointer_path'))):
            raise ValueError()
    except composer.ComposeRefused:
        _emit({'status': 'REFUSED'})
        return 2
    except composer.ComposeIncomplete:
        _emit({'status': 'INCOMPLETE'})
        return 3
    except KeyboardInterrupt:
        _emit({'status': 'UNKNOWN'})
        return 130
    except BaseException:  # no caller data or exception chain on output
        _emit({'status': 'UNKNOWN'})
        return 3
    return 0 if _emit(result) else 1


def main(argv=None):
    try:
        return _run(sys.argv[1:] if argv is None else argv)
    except KeyboardInterrupt:
        return 130
    except BaseException:
        return 1


if __name__ == '__main__':
    sys.exit(main())
