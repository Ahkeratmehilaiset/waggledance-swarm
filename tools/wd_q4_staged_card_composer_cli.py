"""Default-off CLI for one independently pinned staged-card request.

PREPARED means staged byte consistency only; it does not install, activate,
authenticate an issuer or prove runtime readiness. INCOMPLETE and UNKNOWN
require inspection before retry. Caller pins are not authority by themselves.
"""
import json
from pathlib import Path
import re
import sys

from tools import wd_q4_descriptor_cli as input_files
from tools import wd_q4_descriptor_materializer as materializer
from tools import wd_q4_lane_query as lane_query
from tools import wd_q4_staged_composer as composer

_SCHEMA = 'wd.q4-staged-card-composer-request.v1'
_SHARED_SCHEMA = 'wd.q4-staged-card-composer-request.v2'  # v1 plus shared_stage, which must be exactly true
_PATHS = frozenset({'stage_dir', 'expected_bundle', 'public_plan_path', 'fixed_plan_path',
                    'generation_root', 'bundle_store_root', 'active_bundle_root'})
_LOWER = frozenset({'expected_public_plan_sha256', 'expected_fixed_plan_sha256',
                    'expected_host_sha256', 'expected_state_sha256'})
_KEYS = _PATHS | _LOWER | {'schema', 'generation', 'expected_commit',
                          'expected_manifest_sha256', 'descriptor_options'}
_RESULT = frozenset({'status', 'bundle_commit', 'manifest_sha256', 'binding_sha256',
                     'descriptor_path', 'descriptor_sha256', 'pointer_path', 'pointer_sha256'})
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_HEX_UPPER = re.compile(r'[0-9A-F]{64}\Z')
_REVISION = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_COMMIT = re.compile(r'[0-9a-f]{40}\Z')


def _emit(value):
    return input_files._write(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n')


def _request(path, expected):
    raw = input_files._read_pinned(path, 65536, expected, 'request_refused')
    value = json.loads(raw.decode('ascii', 'strict'), object_pairs_hook=input_files._pairs,
                       parse_constant=input_files._constant)
    shared = type(value) is dict and value.get('schema') == _SHARED_SCHEMA
    if (type(value) is not dict or set(value) != (_KEYS | {'shared_stage'} if shared else _KEYS)
            or value['schema'] != (_SHARED_SCHEMA if shared else _SCHEMA)
            or (shared and value['shared_stage'] is not True)
            or type(value['generation']) is not str or _REVISION.fullmatch(value['generation']) is None
            or type(value['expected_commit']) is not str or _COMMIT.fullmatch(value['expected_commit']) is None
            or type(value['expected_manifest_sha256']) is not str
            or _HEX_UPPER.fullmatch(value['expected_manifest_sha256']) is None
            or any(type(value[key]) is not str or _HEX.fullmatch(value[key]) is None for key in _LOWER)
            or type(value['descriptor_options']) is not dict
            or set(value['descriptor_options']) != input_files._DESCRIPTOR_KEYS):
        raise ValueError()
    for key in _PATHS:
        input_files._check_path(value[key], 'request_refused')
    options = value['descriptor_options']
    expected_binding = options.get('expected_binding')
    if (type(expected_binding) is not dict or set(expected_binding) != lane_query.STAGED_BINDING_KEYS
            or any(type(pin) is not str or _HEX.fullmatch(pin) is None for pin in expected_binding.values())):
        raise ValueError()
    fixed = input_files._read_pinned(value['fixed_plan_path'], 4096,
                                    value['expected_fixed_plan_sha256'], 'source_refused')
    plan = composer.transport._fixed_plan(fixed)
    if plan['backend'] != 'staged-card-wrapper-v1':
        raise ValueError()
    return {key: val for key, val in value.items() if key not in ('schema', 'fixed_plan_path')} | {
        'fixed_plan_bytes': fixed}


def _verify_result(result, values):
    if (type(result) is not dict or set(result) != _RESULT or result['status'] != 'PREPARED'
            or result['bundle_commit'] != values['expected_commit']
            or result['manifest_sha256'] != values['expected_manifest_sha256']
            or any(type(result[key]) is not str or _HEX.fullmatch(result[key]) is None
                   for key in ('binding_sha256', 'descriptor_sha256', 'pointer_sha256'))):
        raise ValueError()
    paths = materializer.plan_generation_paths(values['stage_dir'], values['generation'])
    shared = values.get('shared_stage', False)
    if shared is not True and shared is not False:
        raise ValueError()
    pointer_name = 'candidate-pointer-' + values['generation'] + '.json' if shared else 'candidate-pointer.json'
    pointer_path = str(Path(values['stage_dir']) / pointer_name)
    if result['descriptor_path'] != paths['descriptor_path'] or result['pointer_path'] != pointer_path:
        raise ValueError()
    descriptor_raw = input_files._read_pinned(result['descriptor_path'], 65536, result['descriptor_sha256'], 'result_refused')
    pointer_raw = input_files._read_pinned(result['pointer_path'], 4096, result['pointer_sha256'], 'result_refused')
    binding_raw = input_files._read_pinned(paths['binding_path'], 65536, result['binding_sha256'], 'result_refused')
    materialized_plan = input_files._read_pinned(paths['fixed_plan_path'], 4096,
                                               values['expected_fixed_plan_sha256'], 'result_refused')
    if materialized_plan != values['fixed_plan_bytes']:
        raise ValueError()
    if shared:  # the selector reads the candidate's own public plan copy inside its generation
        public_copy = str(Path(paths['descriptor_path']).parent / 'public-plan.json')
        input_files._read_pinned(public_copy, 65536, values['expected_public_plan_sha256'], 'result_refused')
    fixed = composer.transport._fixed_plan(materialized_plan)
    snapshot = input_files._read_pinned(paths['snapshot_path'], input_files.LIMITS['snapshot'],
                                        fixed['bundle_sha256'], 'result_refused')
    if len(snapshot) != fixed['bundle_bytes']:
        raise ValueError()
    descriptor = json.loads(descriptor_raw.decode('ascii'), object_pairs_hook=input_files._pairs,
                            parse_constant=input_files._constant)
    pointer = json.loads(pointer_raw.decode('ascii'), object_pairs_hook=input_files._pairs,
                         parse_constant=input_files._constant)
    binding = json.loads(binding_raw.decode('ascii'), object_pairs_hook=input_files._pairs,
                         parse_constant=input_files._constant)
    if (type(descriptor) is not dict or type(pointer) is not dict or type(binding) is not dict
            or descriptor.get('expected_binding') != values['descriptor_options']['expected_binding']
            or descriptor.get('binding_sha256') != result['binding_sha256']
            or binding.get('binding') != descriptor['expected_binding']
            or descriptor.get('binding_path') != paths['binding_path']
            or descriptor.get('fixed_plan_path') != paths['fixed_plan_path']
            or descriptor.get('fixed_plan_sha256') != values['expected_fixed_plan_sha256']
            or descriptor.get('snapshot_path') != paths['snapshot_path']
            or descriptor.get('snapshot_sha256') != fixed['bundle_sha256']
            or pointer.get('schema') != 'wd.q4-descriptor-pointer.v1'
            or pointer.get('active_bundle') != values['expected_bundle']
            or pointer.get('bundle_commit') != values['expected_commit']
            or pointer.get('manifest_sha256') != values['expected_manifest_sha256']
            or pointer.get('generation') != values['generation']
            or pointer.get('descriptor_path') != result['descriptor_path']
            or pointer.get('descriptor_sha256') != result['descriptor_sha256']):
        raise ValueError()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 0 if _emit({'status': 'OFF'}) else 1
    if (len(args) != 5 or args[0] != '--enable' or args[1] != '--request' or args[3] != '--sha256'
            or type(args[4]) is not str or _HEX.fullmatch(args[4]) is None):
        return 2 if _emit({'status': 'REFUSED'}) else 1
    try:
        values = _request(args[2], args[4])
    except KeyboardInterrupt:
        return 130 if _emit({'status': 'UNKNOWN'}) else 130
    except Exception:
        return 2 if _emit({'status': 'REFUSED'}) else 1
    except BaseException:
        return 3 if _emit({'status': 'UNKNOWN'}) else 3
    try:
        result = composer.compose_staged_card(enabled=True, **values)
        _verify_result(result, values)
    except composer.ComposeRefused:
        return 2 if _emit({'status': 'REFUSED'}) else 1
    except composer.ComposeIncomplete:
        return 3 if _emit({'status': 'INCOMPLETE'}) else 3
    except KeyboardInterrupt:
        return 130 if _emit({'status': 'UNKNOWN'}) else 130
    except BaseException:
        return 3 if _emit({'status': 'UNKNOWN'}) else 3
    return 0 if _emit(result) else 3


if __name__ == '__main__':
    sys.exit(main())
