"""Fixed, default-off bootstrap for the packaged real-card snapshot CLI under the pinned E5 runtime.

The bridge-only Python site has no E5 encoder, so the real-card producer must run under the pinned runtime
interpreter with the manifest-verified runtime site. Run isolated (``python -I -S -B``) from the deployed
``tools-bootstrap/tools`` copy of this file with::

    --enable --request PATH --request-sha256 HEX --manifest-sha256 HEX --runtime PATH --runtime-sha256 HEX

Before the CLI starts it checks, and refuses with ``{"status":"REFUSED"}`` (exit 2) on any doubt:
the caller pins of the request and of the closed runtime document (schema ``wd.q4-real-card-runtime.v1``, so a
genesis runtime document cannot authorize the card producer); the manifest pin equals the anchored
``WD_REBOOT_EXPECTED_MANIFEST_HASH`` and both documents; this file, the shared bootstrap helper and the whole
``tools-bootstrap`` package against the deployment manifest; the interpreter path and bytes; the pinned runtime
verifier and the exact runtime manifest (generation, revision, site, model); and that the request's
``trusted_model_root`` is that runtime's model tree. Then it imports only the fixed module
``tools.wd_q4_real_card_snapshot_cli`` from the verified package, with the runtime site appended and project
imports confined to the package, and passes the exact request path and pin.

Once the CLI has started, only two outcomes are trusted: a validated staged answer (exit 0, one line, closed key
set, every authenticity and freshness field UNKNOWN, request bytes unchanged) and a clean CLI refusal (exit 2).
Anything else, including an exception or interrupt, is ``{"status":"UNKNOWN"}`` (exit 3) and must be reconciled,
never retried. CLI output is captured and never echoed except the validated answer. This does not authenticate the
owner of any file, defend against same-user races, pin native DLLs or bound a process tree.
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
_KEYS = frozenset({'schema', 'bundle_root', 'manifest_sha256', 'interpreter_path', 'interpreter_sha256',
                   'runtime_root', 'runtime_manifest_path', 'runtime_manifest_sha256', 'runtime_generation',
                   'runtime_revision', 'verifier_sha256'})
_RUNTIME_SCHEMA = 'wd.q4-real-card-runtime.v1'
_REQUEST_SCHEMA = 'wd.q4-real-card-snapshot-request.v1'
_SELF = 'tools-bootstrap/tools/wd_q4_real_card_snapshot_runtime_bootstrap.py'
_HELPER = 'tools-bootstrap/tools/wd_q4_live_update_bootstrap.py'
_CLI_MODULE = 'tools.wd_q4_real_card_snapshot_cli'
_CLI_FILE = 'tools/wd_q4_real_card_snapshot_cli.py'
_REQUEST_LIMIT = 1024 * 1024
_ANSWER_KEYS = frozenset({'status', 'pointer_sha256', 'memory_sha256', 'descriptor_sha256', 'completeness',
                          'source_authentication', 'publisher_authentication', 'freshness',
                          'rejected_invalid_expiry', 'source_mode'})
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
    if not 0 < len(raw) <= 1024 or raw.count(b'\n') != 1 or not raw.endswith(b'\n'):
        raise ValueError()
    answer = _doc(raw)
    if (set(answer) != _ANSWER_KEYS or answer['status'] != 'VALIDATED_STAGED'
            or answer['source_mode'] != request['source_mode']
            or type(answer['rejected_invalid_expiry']) is not int
            or not 0 <= answer['rejected_invalid_expiry'] <= 4096
            or any(answer[key] != 'UNKNOWN' for key in
                   ('completeness', 'source_authentication', 'publisher_authentication', 'freshness'))
            or any(type(answer[key]) is not str or not _HEX.fullmatch(answer[key])
                   for key in ('pointer_sha256', 'memory_sha256', 'descriptor_sha256'))):
        raise ValueError()
    return answer


def _bootstrap(args):
    if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or len(args) != 11
            or args[0] != '--enable' or args[1] != '--request' or args[3] != '--request-sha256'
            or args[5] != '--manifest-sha256' or args[7] != '--runtime' or args[9] != '--runtime-sha256'):
        raise ValueError()
    request_path, request_pin, manifest_pin, runtime_path, runtime_pin = args[2], args[4], args[6], args[8], args[10]
    request_raw = _read(request_path, request_pin, _REQUEST_LIMIT)
    request = _doc(request_raw)
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
            and os.path.samefile(model, _readable_path(request.get('trusted_model_root')))):
        raise ValueError()
    sys.path.insert(0, str(code))
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
    except Exception:
        raise _OutcomeUnknown() from None
    print(json.dumps(answer, sort_keys=True, separators=(',', ':')))
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print('{"status":"OFF"}')
        return 0
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
