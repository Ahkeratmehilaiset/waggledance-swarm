"""Fixed default-off isolated entry for the pinned card candidate orchestrator.

This is staging only. It validates exact bundle, runtime, nested request and
model pins, and never selects an arbitrary module. A verified candidate is not
activation or source authentication. After entry uncertainty is UNKNOWN.
External bounded Job containment is a separate responsibility.
"""
import sys
import os

# The pinned bridge wrapper runs -S -B. Remove all inherited import paths
# before importing other modules; the isolated child repeats every check.
if not sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode:
    _base = os.path.normcase(os.path.abspath(sys.base_prefix))
    def _stdlib_only(value):
        try:
            if not value: return False
            full = os.path.normcase(os.path.abspath(value))
            return os.path.commonpath((_base, full)) == _base and 'site-packages' not in full.lower()
        except (OSError, ValueError): return False
    sys.path[:] = [value for value in sys.path if _stdlib_only(value)]

import hashlib
import hmac
import json
from pathlib import Path
import re
import stat
import subprocess

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_KEYS = frozenset({'schema', 'bundle_root', 'manifest_sha256', 'interpreter_path', 'interpreter_sha256',
                   'runtime_root', 'runtime_manifest_path', 'runtime_manifest_sha256', 'runtime_generation',
                   'runtime_revision', 'verifier_sha256'})
_RUNTIME_SCHEMA = 'wd.q4-card-orchestrator-runtime.v1'
_REQUEST_SCHEMA = 'wd.q4-card-real-candidate-request.v1'
_SELF = 'tools-bootstrap/tools/wd_q4_card_orchestrator_runtime_bootstrap.py'
_HELPER = 'tools-bootstrap/tools/wd_q4_live_update_bootstrap.py'
_CLI_MODULE = 'tools.wd_q4_card_audit_orchestrator'
_CLI_FILE = 'tools/wd_q4_card_audit_orchestrator.py'
_REQUEST_LIMIT = 1024 * 1024
_ANSWER_KEYS = frozenset({'status', 'pointer_path', 'pointer_sha256'})
_REFUSED = '{"status":"REFUSED"}\n'


class _OutcomeUnknown(RuntimeError):
    pass


def _pairs(items):
    out = {}
    for key, value in items:
        if key in out: raise ValueError()
        out[key] = value
    return out


def _doc(raw):
    obj = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(obj) is not dict: raise ValueError()
    return obj


def _read(value, pin, limit):
    if type(pin) is not str or not _HEX.fullmatch(pin): raise ValueError()
    path = Path(value)
    if (os.name != 'nt' or not path.is_absolute() or path.drive.upper() != 'C:'
            or '..' in path.parts or str(value).startswith('\\\\')):
        raise ValueError()
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400: raise ValueError()
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit: raise ValueError()
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(raw) != before.st_size or len(raw) > limit or not os.path.samestat(before, opened)
            or not os.path.samestat(opened, after) or not os.path.samestat(after, path.stat())
            or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pin)):
        raise ValueError()
    return raw


def _readable_path(value):
    if type(value) is not str: raise ValueError()
    path = Path(value)
    if not path.is_absolute() or path.drive.upper() != 'C:' or '..' in path.parts: raise ValueError()
    return path


def _answer(raw, request):
    if not 0 < len(raw) <= 4096 or raw.count(b'\n') != 1 or not raw.endswith(b'\n'):
        raise ValueError()
    answer = _doc(raw)
    expected = _readable_path(request['generation_root']) / 'stage' / 'candidate-pointer.json'
    if (set(answer) != _ANSWER_KEYS or answer['status'] != 'PREPARED'
            or type(answer['pointer_path']) is not str or answer['pointer_path'] != str(expected)
            or type(answer['pointer_sha256']) is not str or not _HEX.fullmatch(answer['pointer_sha256'])):
        raise ValueError()
    _read(expected, answer['pointer_sha256'], 4096)
    return answer


def _bootstrap(args):
    if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or len(args) != 11
            or args[0] not in ('--enable', '--child') or args[1] != '--request' or args[3] != '--request-sha256'
            or args[5] != '--manifest-sha256' or args[7] != '--runtime' or args[9] != '--runtime-sha256'):
        raise ValueError()
    request_path, request_pin, manifest_pin, runtime_path, runtime_pin = args[2], args[4], args[6], args[8], args[10]
    request_raw = _read(request_path, request_pin, _REQUEST_LIMIT)
    request = _doc(request_raw)
    if (set(request) != {'schema','decision','snapshot_template','output_root','finish','generation_root'}
            or type(request['snapshot_template']) is not dict
            or set(request['snapshot_template']) != {'path','sha256'}):
        raise ValueError()
    snapshot_spec = request['snapshot_template']
    snapshot_raw = _read(snapshot_spec['path'], snapshot_spec['sha256'], _REQUEST_LIMIT)
    snapshot = _doc(snapshot_raw)
    if snapshot.get('schema') != 'wd.q4-real-card-snapshot-request.v1': raise ValueError()
    runtime = _doc(_read(runtime_path, runtime_pin, 65536))
    if (set(runtime) != _KEYS or runtime['schema'] != _RUNTIME_SCHEMA or request.get('schema') != _REQUEST_SCHEMA
            or type(manifest_pin) is not str or not _HEX.fullmatch(manifest_pin)
            or runtime['manifest_sha256'] != manifest_pin
            or os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != manifest_pin):
        raise ValueError()
    bundle = Path(__file__).absolute().parents[2]
    if not os.path.samefile(bundle, _readable_path(runtime['bundle_root'])):
        raise ValueError()
    manifest = _doc(_read(bundle / 'deployment-manifest.json', manifest_pin, 4 * 1024 * 1024))
    if manifest.get('schema_version') != 1 or type(manifest.get('files')) is not dict: raise ValueError()
    files = manifest['files']
    _read(__file__, str(files.get(_SELF, '')).lower(), 1024 * 1024)
    helper = _read(bundle / _HELPER, str(files.get(_HELPER, '')).lower(), 1024 * 1024)
    if not os.path.samefile(_readable_path(runtime['interpreter_path']), _readable_path(sys.executable)):
        raise ValueError()
    _read(sys.executable, runtime['interpreter_sha256'], 128 * 1024 * 1024)
    scope = {'__name__': '_wd_real_card_pinned_helper'}
    exec(compile(helper, '<wd-pinned-bootstrap-helper>', 'exec'), scope)
    code = scope['_verify_package'](bundle, manifest)
    verifier_raw = _read(code / 'sandbox/mind/step2/runtime_manifest.py', runtime['verifier_sha256'], 1024 * 1024)
    verified = {'__name__': '_wd_real_card_pinned_verifier'}
    exec(compile(verifier_raw, '<wd-pinned-runtime-verifier>', 'exec'), verified)
    root = _readable_path(runtime['runtime_root'])
    verified['verify_runtime'](root, _readable_path(runtime['runtime_manifest_path']),
        runtime['runtime_manifest_sha256'], expected_generation=runtime['runtime_generation'],
        expected_revision=runtime['runtime_revision'])
    model, site = root / 'model', root / 'site'
    if not (site.is_dir() and model.is_dir()
            and os.path.samefile(model, _readable_path(snapshot.get('trusted_model_root')))):
        raise ValueError()
    # Preserve the isolated interpreter's standard-library precedence.
    sys.path.append(str(code))
    sys.path.append(str(site))
    sys.meta_path.insert(0, scope['_ProjectOriginGuard'](code))
    import importlib
    module = importlib.import_module(_CLI_MODULE)
    if not os.path.samefile(module.__file__, code / _CLI_FILE): raise ValueError()
    scope['_check_project_origins'](code)
    output, errors = scope['_BoundedOutput'](), scope['_BoundedOutput']()
    previous = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = output, errors
        try:
            result = module.main(['--enabled', '--request', request_path, '--request-sha256', request_pin])
            scope['_check_project_origins'](code)
        except BaseException:
            raise _OutcomeUnknown() from None
    finally:
        sys.stdout, sys.stderr = previous
    try:
        out, err = output.getvalue().encode('ascii'), errors.getvalue().encode('ascii')
        if type(result) is int and result == 2 and out == b'' and err == _REFUSED.encode('ascii'):
            print(_REFUSED, end='')
            return 2
        if type(result) is not int or result != 0 or err != b'':
            raise ValueError()
        answer = _answer(out, request)
        if _read(request_path, request_pin, _REQUEST_LIMIT) != request_raw:
            raise ValueError()
        if _read(snapshot_spec['path'], snapshot_spec['sha256'], _REQUEST_LIMIT) != snapshot_raw:
            raise ValueError()
    except Exception:
        raise _OutcomeUnknown() from None
    print(json.dumps(answer, sort_keys=True, separators=(',', ':')))
    return 0


def _relay(args):
    """Bridge-wrapper parent: no model import, no interpreter or pin substitution."""
    started = False
    try:
        if (not sys.flags.no_site or not sys.dont_write_bytecode or len(args) != 11
                or args[0] != '--enable' or args[1] != '--request' or args[3] != '--request-sha256'
                or args[5] != '--manifest-sha256' or args[7] != '--runtime' or args[9] != '--runtime-sha256'):
            raise ValueError()
        request = _doc(_read(args[2], args[4], _REQUEST_LIMIT))
        runtime = _doc(_read(args[8], args[10], 65536))
        if (set(runtime) != _KEYS or runtime['schema'] != _RUNTIME_SCHEMA
                or request.get('schema') != _REQUEST_SCHEMA or runtime['manifest_sha256'] != args[6]
                or os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != args[6]):
            raise ValueError()
        bundle = _readable_path(runtime['bundle_root'])
        manifest = _doc(_read(bundle / 'deployment-manifest.json', args[6], 4 * 1024 * 1024))
        if manifest.get('schema_version') != 1 or type(manifest.get('files')) is not dict: raise ValueError()
        own = bundle / _SELF
        if not os.path.samefile(_readable_path(__file__), own): raise ValueError()
        _read(own, str(manifest['files'][_SELF]).lower(), 1024 * 1024)
        interpreter = _readable_path(runtime['interpreter_path'])
        _read(interpreter, runtime['interpreter_sha256'], 128 * 1024 * 1024)
        child = ['--child'] + args[1:]
        started = True
        result = subprocess.run([str(interpreter), '-I', '-S', '-B', str(own), *child], check=False)
        return result.returncode if result.returncode in (0, 2, 3) else 3
    except BaseException:
        print('{"status":"UNKNOWN"}' if started else _REFUSED, end='\n' if started else '')
        return 3 if started else 2


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print('{"status":"OFF"}')
        return 0
    if not sys.flags.isolated:
        return _relay(args)
    try:
        return _bootstrap(args)
    except _OutcomeUnknown:
        print('{"status":"UNKNOWN"}')
        return 3
    except Exception:
        print(_REFUSED, end='')
        return 2
    except BaseException:
        print('{"status":"UNKNOWN"}')
        return 3


if __name__ == '__main__':
    sys.exit(main())
