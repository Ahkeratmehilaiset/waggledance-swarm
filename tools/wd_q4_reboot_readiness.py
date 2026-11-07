"""Default-off, read-only observation of a pinned Q4 reboot candidate.

This is not an installer, reservation, query, cleanup witness, source or lane
authentication, or proof of a later turn. The independently anchored bundle
manifest, descriptor and commit are supplied by the caller; the inherited
manifest pin must match. The launcher must verify this module and its imports
against that bundle before use. Guard observations are momentary and may race.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

LANES = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
_RUNTIME_ONLY = frozenset({'sandbox/mind/step2/q4_controlled_imports.py',
    'sandbox/mind/step2/q4_namespace_origin.py', 'sandbox/mind/step2/q4_native_additions.py'})
_REQUIRED_CODE = _RUNTIME_ONLY | frozenset({'tools/wd_q4_secure_transport.py',
    'tools/wd_q4_staged_fixed_backend.py', 'tools/wd_q4_lane_caller_cli.py',
    'sandbox/mind/step2/runtime_manifest.py'})


def _inspect_guards(fleet, fleet_sha256):
    from tools import wd_q4_guard_state_binding as roots
    result = {}
    for lane in LANES:
        try:
            bound = roots.bind_state_root(fleet_bytes=fleet, expected_fleet_sha256=fleet_sha256, lane=lane)
            path = bound.revalidate() / (lane + '.pending.json')
            try:
                os.lstat(path)
            except FileNotFoundError:
                state = 'CLEAR_AT_CHECK'
            else:
                state = 'PENDING'
            bound.revalidate()
            result[lane] = state
        except roots.GuardStateUnavailable:
            result[lane] = 'UNAVAILABLE'
        except Exception:
            result[lane] = 'UNKNOWN'
    return result


def _check_runtime(plan, runtime_bytes):
    from sandbox.mind.step2 import runtime_manifest
    from tools import wd_q4_lane_caller_cli as caller
    from tools import wd_q4_secure_transport as fixed
    if (type(runtime_bytes) is not bytes
            or hashlib.sha256(runtime_bytes).hexdigest() != plan['runtime_manifest_sha256']):
        raise ValueError('runtime_refused')
    runtime_manifest.verify_runtime(Path(plan['runtime_root']), Path(plan['runtime_manifest_path']),
        plan['runtime_manifest_sha256'], expected_generation=plan['runtime_generation'],
        expected_revision=plan['runtime_revision'])
    document = caller._json(runtime_bytes)
    records = document.get('files')
    if type(records) is not list:
        raise ValueError('runtime_refused')
    entries = {item['path']:item for item in records if type(item) is dict and type(item.get('path')) is str}
    if (len(entries) != len(records) or not all('assets/code/'+name in entries for name in _REQUIRED_CODE)
            or not any(name.startswith('site/') for name in entries)
            or not any(name.startswith('model/') for name in entries)):
        raise ValueError('runtime_refused')
    bundle = Path(plan['bundle_root'])
    manifest_raw = caller._read(str(bundle/'deployment-manifest.json'), caller.MAX_MANIFEST,
                                plan['deployment_manifest_sha256'])
    manifest = caller._json(manifest_raw)
    files = manifest.get('files')
    if type(files) is not dict:
        raise ValueError('runtime_refused')
    for name, item in entries.items():
        if not name.startswith('assets/code/') or not name.endswith('.py'):
            continue
        relative = name[len('assets/code/'):]
        if relative in _RUNTIME_ONLY:
            continue
        pin = files.get('tools-bootstrap/'+relative)
        if type(pin) is not str or pin.lower() != item.get('sha256'):
            raise ValueError('runtime_refused')
        caller._read(str(bundle/'tools-bootstrap'/relative), runtime_manifest.MAX_FILE_BYTES, pin)
    peer = Path(plan['runtime_root'])/'assets'/'code'/'tools'/'wd_q4_secure_transport.py'
    try:
        fixed._read_staged_backend(str(peer), plan)
    except Exception:
        raise ValueError('runtime_refused') from None


def inspect(*, enabled=False, descriptor_path=None, descriptor_sha256=None,
            manifest_sha256=None, expected_commit=None):
    """Return closed observation classes, never a lease or a query result."""
    if enabled is False:
        return {'status':'OFF'}
    try:
        if enabled is not True:
            raise ValueError()
        from tools import wd_q4_lane_caller_cli as caller
        from tools import wd_q4_lane_binding as binding
        from tools import wd_q4_secure_transport as fixed
        manifest_pin = caller._hex(manifest_sha256)
        if caller._hex(caller._env('WD_REBOOT_EXPECTED_MANIFEST_HASH')) != manifest_pin:
            raise ValueError()
        descriptor, raw, pin, fleet, pins, inputs = caller._configuration(descriptor_path, descriptor_sha256)
        if set(descriptor['lanes']) != set(LANES) or len(descriptor['lanes']) != len(LANES):
            raise ValueError()
        manifest_raw = caller._read(str(Path(descriptor['bundle_root'])/'deployment-manifest.json'),
                                    caller.MAX_MANIFEST, manifest_pin)
        manifest = caller._json(manifest_raw)
        if (type(expected_commit) is not str or manifest.get('source_commit') != expected_commit
                or len(expected_commit) != 40 or any(c not in '0123456789abcdef' for c in expected_commit)):
            raise ValueError()
        caller._require_current_snapshot(descriptor, pins, inputs)
        plan = fixed._fixed_plan(inputs['fixed_plan_bytes'])
        if (plan['backend'] != 'staged-card-wrapper-v1' or plan['bundle_commit'] != expected_commit
                or plan['deployment_manifest_sha256'] != manifest_pin
                or plan['bundle_root'] != descriptor['bundle_root']
                or plan['bundle_bytes'] != len(inputs['fixed_bundle_bytes'])
                or plan['bundle_sha256'] != hashlib.sha256(inputs['fixed_bundle_bytes']).hexdigest()
                or plan['interpreter_sha256'] != descriptor['interpreter_sha256']
                or plan['runtime_manifest_sha256'] != descriptor['runtime_manifest_sha256']
                or plan['runtime_manifest_path'] != descriptor['runtime_manifest_path']
                or plan['runtime_root'] != descriptor['outer']['runtime-root']
                or plan['runtime_generation'] != descriptor['outer']['runtime-generation']
                or plan['runtime_revision'] != descriptor['outer']['runtime-revision']):
            raise ValueError()
        for lane in LANES:
            resolved = binding.resolve_lane_binding(fleet, inputs['expected_fleet_sha256'], inputs['binding_bytes'],
                inputs['expected_binding_sha256'], lane=lane, enabled=True)
            if (resolved['expected_commit'] != expected_commit or resolved['expected_manifest_sha256'] != manifest_pin
                    or resolved['expected_binding'] != pins):
                raise ValueError()
        _check_runtime(plan, inputs['runtime_manifest_bytes'])
        guards = _inspect_guards(fleet, inputs['expected_fleet_sha256'])
        descriptor2, raw2, pin2, fleet2, pins2, inputs2 = caller._configuration(descriptor_path, descriptor_sha256)
        if ((descriptor2,raw2,pin2,fleet2,pins2,inputs2) != (descriptor,raw,pin,fleet,pins,inputs)
                or caller._read(str(Path(descriptor['bundle_root'])/'deployment-manifest.json'),
                                caller.MAX_MANIFEST, manifest_pin) != manifest_raw):
            raise RuntimeError()
        caller._require_current_snapshot(descriptor2, pins2, inputs2)
        if _inspect_guards(fleet2, inputs2['expected_fleet_sha256']) != guards:
            raise RuntimeError()
        status = ('UNKNOWN' if 'UNKNOWN' in guards.values() else
                  'PRECHECK_PASSED' if all(value == 'CLEAR_AT_CHECK' for value in guards.values()) else 'UNAVAILABLE')
        return {'status':status, 'guards':guards, 'observed_utc':datetime.now(timezone.utc).isoformat(),
                'next_turn':'UNKNOWN', 'source_authentication':'UNKNOWN', 'query_chain':'NOT_CHECKED',
                'native_launch':'UNKNOWN', 'freshness':'UNKNOWN'}
    except (TimeoutError, KeyboardInterrupt, SystemExit, RuntimeError):
        return {'status':'UNKNOWN'}
    except Exception:
        return {'status':'UNAVAILABLE'}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print('{"status":"OFF"}')
        return 0
    if (len(args) != 9 or args[0] != '--enable' or args[1] != '--descriptor'
            or args[3] != '--descriptor-sha256' or args[5] != '--manifest-sha256' or args[7] != '--commit'):
        print('{"status":"UNAVAILABLE"}')
        return 2
    result = inspect(enabled=True, descriptor_path=args[2], descriptor_sha256=args[4],
                     manifest_sha256=args[6], expected_commit=args[8])
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return {'PRECHECK_PASSED':0, 'UNAVAILABLE':2, 'UNKNOWN':3}.get(result['status'], 3)


if __name__ == '__main__':
    sys.exit(main())
