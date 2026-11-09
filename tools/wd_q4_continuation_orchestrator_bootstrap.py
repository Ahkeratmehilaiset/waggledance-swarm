"""Fixed isolated bootstrap for the opt-in one-shot continuation orchestrator.

Pinned hashes are local integrity checks, not process or publisher authentication.
No scheduler, retry, live installation, or host-crash recovery is provided.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_RUNTIME_KEYS = frozenset({'schema', 'bundle_root', 'manifest_sha256', 'interpreter_path', 'interpreter_sha256',
    'runtime_root', 'runtime_manifest_path', 'runtime_manifest_sha256', 'runtime_generation', 'runtime_revision',
    'verifier_sha256'})
_ANSWER = frozenset({'status', 'pointer_sha256', 'runner_receipt_sha256', 'cutoff_utc', 'previous_cutoff_utc',
                     'route_sha256', 'watermark_record_sha256', 'cutoff_monotonicity'})


class _Unknown(RuntimeError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _doc(raw):
    obj = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(obj) is not dict:
        raise ValueError()
    return obj


def _read(value, pin, limit):
    if type(pin) is not str or not _HEX.fullmatch(pin):
        raise ValueError()
    path = Path(value)
    if (os.name != 'nt' or not path.is_absolute() or path.drive.upper() != 'C:'
            or '..' in path.parts or str(value).startswith('\\\\')):
        raise ValueError()
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError()
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
        raise ValueError()
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(raw) != before.st_size or len(raw) > limit or not os.path.samestat(before, opened)
            or not os.path.samestat(opened, after) or not os.path.samestat(after, path.stat())
            or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pin)):
        raise ValueError()
    return raw


def _bootstrap(args):
    if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or len(args) != 11
            or args[0:2] != ['--enable', '--config'] or args[3] != '--sha256'
            or args[5] != '--manifest-sha256' or args[7] != '--runtime' or args[9] != '--runtime-sha256'):
        raise ValueError()
    config_path, config_pin, manifest_pin, runtime_path, runtime_pin = args[2], args[4], args[6], args[8], args[10]
    config = _doc(_read(config_path, config_pin, 65536))
    runtime = _doc(_read(runtime_path, runtime_pin, 65536))
    if (config.get('schema') != 'wd.q4-continuation-orchestrator-cli.v1'
            or type(runtime) is not dict or set(runtime) != _RUNTIME_KEYS
            or runtime['schema'] != 'wd.q4-genesis-runtime.v1'
            or config.get('expected_manifest_sha256') != manifest_pin or runtime['manifest_sha256'] != manifest_pin
            or config.get('runtime_sha256') != runtime_pin
            or not os.path.samefile(config['runtime_path'], runtime_path)
            or os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != manifest_pin):
        raise ValueError()
    bundle = Path(__file__).absolute().parents[2]
    if not os.path.samefile(bundle, config['bundle_root']) or not os.path.samefile(bundle, runtime['bundle_root']):
        raise ValueError()
    manifest = _doc(_read(bundle / 'deployment-manifest.json', manifest_pin, 4 * 1024 * 1024))
    if (manifest.get('schema_version') != 1 or type(manifest.get('files')) is not dict
            or manifest.get('source_commit') != config.get('expected_commit')):
        raise ValueError()
    files = manifest['files']
    self_name = 'tools-bootstrap/tools/wd_q4_continuation_orchestrator_bootstrap.py'
    helper_name = 'tools-bootstrap/tools/wd_q4_live_update_bootstrap.py'
    _read(__file__, str(files.get(self_name, '')).lower(), 1024 * 1024)
    helper_raw = _read(bundle / helper_name, str(files.get(helper_name, '')).lower(), 1024 * 1024)
    if not os.path.samefile(sys.executable, runtime['interpreter_path']):
        raise ValueError()
    _read(sys.executable, runtime['interpreter_sha256'], 128 * 1024 * 1024)
    helper = {'__name__': '_wd_q4_pinned_orchestrator_helper'}
    exec(compile(helper_raw, '<wd-pinned-orchestrator-helper>', 'exec'), helper)
    code = helper['_verify_package'](bundle, manifest)
    verifier_raw = _read(code / 'sandbox/mind/step2/runtime_manifest.py', runtime['verifier_sha256'], 1024 * 1024)
    verifier = {'__name__': '_wd_q4_pinned_orchestrator_verifier'}
    exec(compile(verifier_raw, '<wd-pinned-runtime-verifier>', 'exec'), verifier)
    root = Path(runtime['runtime_root'])
    verifier['verify_runtime'](root, Path(runtime['runtime_manifest_path']), runtime['runtime_manifest_sha256'],
        expected_generation=runtime['runtime_generation'], expected_revision=runtime['runtime_revision'])
    site, model = root / 'site', root / 'model'
    if not site.is_dir() or not model.is_dir():
        raise ValueError()
    sys.path.insert(0, str(code))
    sys.path.append(str(site))
    sys.meta_path.insert(0, helper['_ProjectOriginGuard'](code))
    import importlib
    module = importlib.import_module('tools.wd_q4_continuation_orchestrator_cli')
    if not os.path.samefile(module.__file__, code / 'tools/wd_q4_continuation_orchestrator_cli.py'):
        raise ValueError()
    helper['_check_project_origins'](code)
    output = helper['_BoundedOutput']()
    previous = sys.stdout
    try:
        sys.stdout = output
        try:
            result = module.main(['--enable', '--config', config_path, '--sha256', config_pin])
            helper['_check_project_origins'](code)
        except BaseException:
            raise _Unknown() from None
    finally:
        sys.stdout = previous
    try:
        raw = output.getvalue().encode('ascii')
        if not 0 < len(raw) <= 1024 or raw.count(b'\n') != 1 or not raw.endswith(b'\n'):
            raise ValueError()
        answer = _doc(raw)
        success = answer.get('status') in ('DEFERRED', 'partial_ready_prefix', 'caught_up_to_cutoff')
        if ((success and (result != 0 or set(answer) != _ANSWER or answer['cutoff_utc'] != config['cutoff_utc']
                 or answer['cutoff_monotonicity'] != 'COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN'
                 or any(type(answer[k]) is not str or _HEX.fullmatch(answer[k]) is None for k in
                        ('pointer_sha256', 'runner_receipt_sha256', 'route_sha256', 'watermark_record_sha256'))))
                or (not success and (set(answer) != {'status'} or
                    (result, answer.get('status')) not in ((2, 'REFUSED'), (3, 'UNKNOWN'))))):
            raise ValueError()
    except Exception:
        raise _Unknown() from None
    print(json.dumps(answer, sort_keys=True, separators=(',', ':')))
    return result


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print('{"status":"OFF"}')
        return 0
    try:
        return _bootstrap(args)
    except _Unknown:
        print('{"status":"UNKNOWN"}')
        return 3
    except Exception:
        print('{"status":"REFUSED"}')
        return 2
    except BaseException:
        print('{"status":"UNKNOWN"}')
        return 3


if __name__ == '__main__':
    sys.exit(main())
