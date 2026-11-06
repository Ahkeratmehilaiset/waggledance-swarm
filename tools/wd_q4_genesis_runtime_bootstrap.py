"""Fixed, default-off bootstrap for a pinned genesis CLI and Q4 runtime.

Caller must verify this file and the interpreter before execution. This checks
bytes and import origins again but does not authenticate their owner or defend
against same-user races, unpinned native DLLs, or an unbounded process tree.

Besides the v1 statuses (checked unchanged) it passes through exactly one bounded progress answer:
GENESIS_SKIP_PROGRESS with exit code 4 and only a lowercase 64-hex
skip_receipt_sha256 (late-public skip receipt advanced, nothing published).
Exit code 4 with any other answer is UNKNOWN, and so is that status or key with
any other exit code, because the v1 check admits neither.
"""
import hashlib
import hmac
import io
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


def _bootstrap(args):
    if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or
            len(args) != 11 or args[0] != '--enable' or args[1] != '--config' or args[3] != '--sha256'
            or args[5] != '--manifest-sha256' or args[7] != '--runtime' or args[9] != '--runtime-sha256'):
        raise ValueError()
    config_path, config_pin, manifest_pin, runtime_path, runtime_pin = args[2], args[4], args[6], args[8], args[10]
    config = _doc(_read(config_path, config_pin, 65536))
    runtime = _doc(_read(runtime_path, runtime_pin, 65536))
    if (set(runtime) != _KEYS or runtime['schema'] != 'wd.q4-genesis-runtime.v1'
            or config.get('schema') != 'wd.q4-genesis-cli.v1'
            or runtime['manifest_sha256'] != manifest_pin or config.get('manifest_sha256') != manifest_pin
            or os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != manifest_pin):
        raise ValueError()
    bundle = Path(__file__).absolute().parents[2]
    if not os.path.samefile(bundle, runtime['bundle_root']) or not os.path.samefile(bundle, config['bundle_root']):
        raise ValueError()
    manifest = _doc(_read(bundle / 'deployment-manifest.json', manifest_pin, 4 * 1024 * 1024))
    if manifest.get('schema_version') != 1 or type(manifest.get('files')) is not dict: raise ValueError()
    files = manifest['files']
    for name, path in (('tools-bootstrap/tools/wd_q4_genesis_runtime_bootstrap.py', __file__),
                       ('tools-bootstrap/tools/wd_q4_live_update_bootstrap.py', bundle / 'tools-bootstrap/tools/wd_q4_live_update_bootstrap.py')):
        _read(path, str(files.get(name, '')).lower(), 1024 * 1024)
    if not os.path.samefile(_readable_path(runtime['interpreter_path']), _readable_path(sys.executable)):
        raise ValueError()
    _read(sys.executable, runtime['interpreter_sha256'], 128 * 1024 * 1024)
    helper_path = bundle / 'tools-bootstrap/tools/wd_q4_live_update_bootstrap.py'
    helper = _read(helper_path, str(files['tools-bootstrap/tools/wd_q4_live_update_bootstrap.py']).lower(), 1024 * 1024)
    scope = {'__name__': '_wd_genesis_pinned_helper'}
    exec(compile(helper, '<wd-pinned-bootstrap-helper>', 'exec'), scope)
    code = scope['_verify_package'](bundle, manifest)
    verifier = code / 'sandbox/mind/step2/runtime_manifest.py'
    verifier_raw = _read(verifier, runtime['verifier_sha256'], 1024 * 1024)
    verified = {'__name__': '_wd_genesis_pinned_verifier'}
    exec(compile(verifier_raw, '<wd-pinned-runtime-verifier>', 'exec'), verified)
    root = Path(runtime['runtime_root'])
    verified['verify_runtime'](root, Path(runtime['runtime_manifest_path']), runtime['runtime_manifest_sha256'],
        expected_generation=runtime['runtime_generation'], expected_revision=runtime['runtime_revision'])
    model, site = root / 'model', root / 'site'
    if not (site.is_dir() and model.is_dir() and os.path.samefile(model, config['trusted_model_root'])):
        raise ValueError()
    sys.path.insert(0, str(code))
    sys.path.append(str(site))
    sys.meta_path.insert(0, scope['_ProjectOriginGuard'](code))
    import importlib
    module = importlib.import_module('tools.wd_q4_genesis_cli')
    if not os.path.samefile(module.__file__, code / 'tools/wd_q4_genesis_cli.py'): raise ValueError()
    scope['_check_project_origins'](code)
    output = scope['_BoundedOutput']()
    previous = sys.stdout
    try:
        sys.stdout = output
        try:
            result = module.main(['--enable', '--config', config_path, '--sha256', config_pin])
            scope['_check_project_origins'](code)
        except BaseException:
            raise _OutcomeUnknown() from None
    finally:
        sys.stdout = previous
    try:
        raw = output.getvalue().encode('ascii')
        if not 0 < len(raw) <= 512 or raw.count(b'\n') != 1 or not raw.endswith(b'\n'):
            raise ValueError()
        answer = _doc(raw)
        if result == 4:
            if (answer.get('status') != 'GENESIS_SKIP_PROGRESS' or set(answer) != {'status', 'skip_receipt_sha256'} or
                    type(answer['skip_receipt_sha256']) is not str or _HEX.fullmatch(answer['skip_receipt_sha256']) is None):
                raise ValueError()
        elif (not set(answer) <= {'status', 'pointer_sha256', 'runner_receipt_sha256'} or
                answer.get('status') not in ('PUBLISHED_GENESIS', 'PUBLISHED_GENESIS_PARTIAL', 'REFUSED', 'UNKNOWN') or
                (result == 0) != (answer['status'] in ('PUBLISHED_GENESIS', 'PUBLISHED_GENESIS_PARTIAL')) or
                any(type(answer[k]) is not str or _HEX.fullmatch(answer[k]) is None
                    for k in ('pointer_sha256', 'runner_receipt_sha256') if k in answer)):
            raise ValueError()
    except Exception:
        raise _OutcomeUnknown() from None
    print(json.dumps(answer, sort_keys=True, separators=(',', ':')))
    return result


def _readable_path(value):
    path = Path(value)
    if not path.is_absolute() or path.drive.upper() != 'C:' or '..' in path.parts: raise ValueError()
    return path


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
        print('{"status":"REFUSED"}')
        return 2
    except BaseException:
        print('{"status":"UNKNOWN"}')
        return 3


if __name__ == '__main__':
    sys.exit(main())
