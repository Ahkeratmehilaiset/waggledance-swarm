"""Fixed, default-off bootstrap for a pinned continuation CLI and Q4 runtime.

Caller must verify this file and the interpreter before execution. This checks
bytes and import origins again but does not authenticate their owner or defend
against same-user races, unpinned native DLLs, or an unbounded process tree.
"""
import base64
import hashlib
import hmac
import io
import json
from datetime import datetime
import os
from pathlib import Path
import re
import stat
import sys

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_KEYS = frozenset({'schema', 'bundle_root', 'manifest_sha256', 'interpreter_path', 'interpreter_sha256',
                   'runtime_root', 'runtime_manifest_path', 'runtime_manifest_sha256', 'runtime_generation',
                   'runtime_revision', 'verifier_sha256'})
_CONFIG_KEYS = frozenset({'schema', 'bundle_root', 'manifest_sha256', 'baseline_path', 'baseline_sha256',
                          'tuple_path', 'tuple_sha256', 'receipt_path', 'receipt_sha256'})
_CUTOFF = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z')


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


def _cutoff(value):
    if type(value) is not str or _CUTOFF.fullmatch(value) is None:
        raise ValueError()
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')


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


def _parent_pins(manifest, config, runtime, config_pin, runtime_pin, host_pin):
    result = {key: config[key] for key in ('manifest_sha256', 'baseline_sha256', 'tuple_sha256', 'receipt_sha256')}
    result.update(config_sha256=config_pin, runtime_sha256=runtime_pin, host_sha256=host_pin,
                  interpreter_sha256=runtime['interpreter_sha256'],
                  launcher_sha256=manifest['files']['Invoke-WdQ4AdoptRecovery.ps1'].lower(),
                  commit=manifest['source_commit'])
    if any(type(value) is not str or (_HEX.fullmatch(value) is None if key != 'commit' else
            re.fullmatch(r'[0-9a-f]{40}', value) is None) for key, value in result.items()):
        raise ValueError()
    return result


def _bootstrap(args):
    if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or
            len(args) != 13 or args[0] != '--enable' or args[1] != '--config' or args[3] != '--sha256'
            or args[5] != '--manifest-sha256' or args[7] != '--runtime' or args[9] != '--runtime-sha256'
            or args[11] != '--host-sha256' or _HEX.fullmatch(args[12]) is None):
        raise ValueError()
    config_path, config_pin, manifest_pin, runtime_path, runtime_pin = args[2], args[4], args[6], args[8], args[10]
    config = _doc(_read(config_path, config_pin, 65536))
    runtime = _doc(_read(runtime_path, runtime_pin, 65536))
    version = config.get('schema')
    if (set(runtime) != _KEYS or set(config) != _CONFIG_KEYS or runtime['schema'] != 'wd.q4-genesis-runtime.v1'
            or version != 'wd.q4-adopt-recovery-cli.v1'
            or runtime['manifest_sha256'] != manifest_pin or config.get('manifest_sha256') != manifest_pin
            or os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != manifest_pin):
        raise ValueError()
    bundle = Path(__file__).absolute().parents[2]
    if not os.path.samefile(bundle, runtime['bundle_root']) or not os.path.samefile(bundle, config['bundle_root']):
        raise ValueError()
    manifest = _doc(_read(bundle / 'deployment-manifest.json', manifest_pin, 4 * 1024 * 1024))
    if manifest.get('schema_version') != 1 or type(manifest.get('files')) is not dict: raise ValueError()
    files = manifest['files']
    for name, path in (('tools-bootstrap/tools/wd_q4_continuation_adopt_recovery_bootstrap.py', __file__),
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
    tuple_raw = _read(config['tuple_path'], config['tuple_sha256'], 65536)
    document = _doc(tuple_raw)
    route_part = document['route']
    if type(route_part) is not dict or set(route_part) != {'bytes_b64', 'sha256'}:
        raise ValueError()
    route_raw = base64.b64decode(route_part['bytes_b64'], validate=True)
    if (len(route_raw) > 65536 or type(route_part['sha256']) is not str or
            not _HEX.fullmatch(route_part['sha256']) or
            not hmac.compare_digest(hashlib.sha256(route_raw).hexdigest(), route_part['sha256'])):
        raise ValueError()
    route = _doc(route_raw)
    # realpath(strict=True) resolves Windows final names, including 8.3 aliases.
    # A private tuple must never be placed in the published checkpoint tree.
    tuple_final = os.path.normcase(os.path.realpath(config['tuple_path'], strict=True))
    checkpoint_final = os.path.normcase(os.path.realpath(route['checkpoint_root'], strict=True))
    if os.path.commonpath((tuple_final, checkpoint_final)) == checkpoint_final:
        raise ValueError()
    receipt_final = os.path.normcase(os.path.realpath(config['receipt_path'], strict=True))
    if os.path.commonpath((receipt_final, checkpoint_final)) == checkpoint_final:
        raise ValueError()
    model, site = root / 'model', root / 'site'
    if not (site.is_dir() and model.is_dir() and os.path.samefile(model, route['trusted_model_root'])):
        raise ValueError()
    sys.path.insert(0, str(code))
    sys.path.append(str(site))
    sys.meta_path.insert(0, scope['_ProjectOriginGuard'](code))
    import importlib
    module = importlib.import_module('tools.wd_q4_owned_recovery_parent')
    if not os.path.samefile(module.__file__, code / 'tools/wd_q4_owned_recovery_parent.py'):
        raise ValueError()
    reader_path = code / 'tools/wd_q4_owned_recovery_reader.py'
    reader_pin = str(files['tools-bootstrap/tools/wd_q4_owned_recovery_reader.py']).lower()
    _read(reader_path, reader_pin, 1024 * 1024)
    host = Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    host_pin = args[12]
    _read(host, host_pin, 128 * 1024 * 1024)
    owner_pins = _parent_pins(manifest, config, runtime, config_pin, runtime_pin, host_pin)
    receipt_raw = _read(config['receipt_path'], config['receipt_sha256'], 65536)
    scope['_check_project_origins'](code)
    try:
        answer = module.run_owned(enabled=True, baseline_path=config['baseline_path'],
            baseline_sha256=config['baseline_sha256'], manifest_sha256=manifest_pin,
            genesis_tuple_bytes=tuple_raw, genesis_tuple_sha256=config['tuple_sha256'],
            receipt_bytes=receipt_raw, receipt_sha256=config['receipt_sha256'],
            owner_pins=owner_pins, reader_sha256=reader_pin)
    except module.OwnerRefused:
        print('{"status":"REFUSED"}')
        return 2
    except BaseException:
        raise _OutcomeUnknown() from None
    try:
        scope['_check_project_origins'](code)
        if (set(answer) != {'status', 'attempt', 'marker_id', 'marker_sha256'} or
                answer['status'] not in ('ADOPTED', 'NOT_ADOPTED', 'NOT_NEEDED', 'REFUSED') or
                not _HEX.fullmatch(answer['attempt']) or not _HEX.fullmatch(answer['marker_sha256']) or
                re.fullmatch(r'[0-9a-f]{48}', answer['marker_id']) is None):
            raise ValueError()
        print(json.dumps(answer, sort_keys=True, separators=(',', ':')))
    except BaseException:
        raise _OutcomeUnknown() from None
    return 0


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
