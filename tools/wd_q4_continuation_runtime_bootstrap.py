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


def _bootstrap(args):
    if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or
            len(args) != 11 or args[0] != '--enable' or args[1] != '--config' or args[3] != '--sha256'
            or args[5] != '--manifest-sha256' or args[7] != '--runtime' or args[9] != '--runtime-sha256'):
        raise ValueError()
    config_path, config_pin, manifest_pin, runtime_path, runtime_pin = args[2], args[4], args[6], args[8], args[10]
    config = _doc(_read(config_path, config_pin, 65536))
    runtime = _doc(_read(runtime_path, runtime_pin, 65536))
    version = config.get('schema')
    if (set(runtime) != _KEYS or runtime['schema'] != 'wd.q4-genesis-runtime.v1'
            or version not in ('wd.q4-continuation-cli.v1', 'wd.q4-continuation-cli.v2', 'wd.q4-continuation-cli.v3')
            or runtime['manifest_sha256'] != manifest_pin or config.get('manifest_sha256') != manifest_pin
            or os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != manifest_pin):
        raise ValueError()
    if version in ('wd.q4-continuation-cli.v2', 'wd.q4-continuation-cli.v3'):
        if _cutoff(config['cutoff_utc']) < _cutoff(config['previous_cutoff_utc']):
            raise ValueError()
    if version == 'wd.q4-continuation-cli.v3':
        prior = config.get('watermark_previous_record_sha256')
        if 'watermark_previous_record_sha256' not in config or (prior is not None and
                (type(prior) is not str or not _HEX.fullmatch(prior))):
            raise ValueError()
    bundle = Path(__file__).absolute().parents[2]
    if not os.path.samefile(bundle, runtime['bundle_root']) or not os.path.samefile(bundle, config['bundle_root']):
        raise ValueError()
    manifest = _doc(_read(bundle / 'deployment-manifest.json', manifest_pin, 4 * 1024 * 1024))
    if manifest.get('schema_version') != 1 or type(manifest.get('files')) is not dict: raise ValueError()
    files = manifest['files']
    for name, path in (('tools-bootstrap/tools/wd_q4_continuation_runtime_bootstrap.py', __file__),
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
    model, site = root / 'model', root / 'site'
    if not (site.is_dir() and model.is_dir() and os.path.samefile(model, route['trusted_model_root'])):
        raise ValueError()
    sys.path.insert(0, str(code))
    sys.path.append(str(site))
    sys.meta_path.insert(0, scope['_ProjectOriginGuard'](code))
    import importlib
    module = importlib.import_module('tools.wd_q4_production_continuation_cli')
    if not os.path.samefile(module.__file__, code / 'tools/wd_q4_production_continuation_cli.py'): raise ValueError()
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
        limit = 1024 if version == 'wd.q4-continuation-cli.v3' else 512
        if not 0 < len(raw) <= limit or raw.count(b'\n') != 1 or not raw.endswith(b'\n'):
            raise ValueError()
        answer = _doc(raw)
        success = answer.get('status') in ('partial_ready_prefix', 'caught_up_to_cutoff', 'DEFERRED')
        success_keys = {'status', 'pointer_sha256', 'runner_receipt_sha256'}
        if version in ('wd.q4-continuation-cli.v2', 'wd.q4-continuation-cli.v3'):
            success_keys |= {'cutoff_utc', 'previous_cutoff_utc', 'route_sha256', 'cutoff_monotonicity'}
        if version == 'wd.q4-continuation-cli.v3':
            success_keys.add('watermark_record_sha256')
        if ((success and (result != 0 or set(answer) != success_keys)) or
                (not success and (set(answer) != {'status'} or
                    (result, answer.get('status')) not in ((2, 'REFUSED'), (3, 'UNKNOWN')))) or
                any(type(answer[k]) is not str or _HEX.fullmatch(answer[k]) is None
                    for k in ('pointer_sha256', 'runner_receipt_sha256') if k in answer)):
            raise ValueError()
        if success and version in ('wd.q4-continuation-cli.v2', 'wd.q4-continuation-cli.v3'):
            if (answer['cutoff_utc'] != config['cutoff_utc']
                    or answer['previous_cutoff_utc'] != config['previous_cutoff_utc']
                    or answer['cutoff_monotonicity'] != ('COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN'
                        if version == 'wd.q4-continuation-cli.v3' else 'CALLER_ASSERTED_REPLAY_CRASH_UNPROVEN')
                    or type(answer['route_sha256']) is not str or _HEX.fullmatch(answer['route_sha256']) is None):
                raise ValueError()
        if success and version == 'wd.q4-continuation-cli.v3':
            record_pin = answer['watermark_record_sha256']
            if type(record_pin) is not str or _HEX.fullmatch(record_pin) is None:
                raise ValueError()
            private = Path(route['private_state_root'])
            head_path = private / 'q4-cutoff-current.json'
            expected_head = json.dumps({'schema': 'wd.q4-cutoff-head.v1', 'record_sha256': record_pin},
                                       sort_keys=True, separators=(',', ':')).encode('ascii')
            if _read(head_path, hashlib.sha256(expected_head).hexdigest(), 4096) != expected_head:
                raise ValueError()
            record = _doc(_read(private / ('q4-cutoff-record-' + record_pin + '.json'), record_pin, 8192))
            _read(Path(route['checkpoint_root']) / 'q4-latest.json', answer['pointer_sha256'], 16384)
            if (set(record) != {'schema', 'previous_record_sha256', 'cutoff_utc', 'attempt', 'route_sha256',
                                'runner_receipt_sha256', 'pointer_sha256'}
                    or record['schema'] != 'wd.q4-cutoff-record.v1'
                    or record['previous_record_sha256'] != config['watermark_previous_record_sha256']
                    or record['cutoff_utc'] != config['cutoff_utc']
                    or any(record[k] != answer[k] for k in ('route_sha256', 'runner_receipt_sha256', 'pointer_sha256'))
                    or type(record['attempt']) is not str or _HEX.fullmatch(record['attempt']) is None
                    or os.path.lexists(private / 'q4-cutoff.pending.json')):
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
