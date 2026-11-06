"""Fixed-purpose isolated bootstrap for the packaged Q4 live updater.

The invoker must independently pin the interpreter, this script, deployment
manifest and configuration before launching. This process rechecks those pins,
all packaged code and the independently pinned runtime before importing it.
It accepts no module, code or executable selector. It is not authentication,
ACL isolation, a native-DLL/stdlib verifier or protection from same-user races.
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
_MAX = 8 * 1024 * 1024


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pairs(values):
    out = {}
    for key, value in values:
        if key in out:
            raise ValueError()
        out[key] = value
    return out


def _document(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))


def _checked_path(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or (os.name == 'nt' and path.anchor.startswith('\\\\')):
        raise ValueError()
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError()
    if not path.is_file():
        raise ValueError()
    return path


def _read(path, expected, limit=_MAX):
    if type(expected) is not str or not _HEX.fullmatch(expected):
        raise ValueError()
    path = _checked_path(path)
    before = path.stat()
    if before.st_size > limit:
        raise ValueError()
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(raw) != before.st_size or len(raw) > limit or not os.path.samestat(before, opened)
            or not os.path.samestat(opened, after) or not os.path.samestat(after, path.stat())
            or not hmac.compare_digest(_sha(raw), expected)):
        raise ValueError()
    return raw


def _verify_package(bundle, manifest):
    files = manifest['files']
    if type(files) is not dict:
        raise ValueError()
    prefix = 'tools-bootstrap/'
    entries = {name[len(prefix):]: digest.lower() for name, digest in files.items()
               if type(name) is str and name.startswith(prefix) and type(digest) is str}
    if not entries or any(not _HEX.fullmatch(pin) for pin in entries.values()):
        raise ValueError()
    code = bundle / 'tools-bootstrap'
    seen = set()
    for current, dirs, names in os.walk(code, followlinks=False):
        directory = Path(current)
        for name in dirs:
            item = directory / name
            info = item.lstat()
            if (name == '__pycache__' or stat.S_ISLNK(info.st_mode)
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ValueError()
        for name in names:
            path = directory / name
            relative = path.relative_to(code).as_posix()
            if relative not in entries or relative in seen or len(seen) >= 50000:
                raise ValueError()
            _read(path, entries[relative], 1024 * 1024 * 1024)
            seen.add(relative)
    if seen != set(entries):
        raise ValueError()
    return code


def main(argv):
    try:
        if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode
                or len(argv) != 6 or argv[0] != '--config' or argv[2] != '--sha256'
                or argv[4] != '--manifest-sha256'):
            raise ValueError()
        config_path, config_pin, manifest_pin = argv[1], argv[3], argv[5]
        config = _document(_read(config_path, config_pin, 65536))
        if config.get('schema') != 'wd.q4-live-update-once.v1' or config.get('manifest_sha256') != manifest_pin:
            raise ValueError()
        if os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower() != manifest_pin:
            raise ValueError()
        bundle = Path(__file__).absolute().parents[2]
        manifest = _document(_read(bundle / 'deployment-manifest.json', manifest_pin, 4 * 1024 * 1024))
        if type(manifest) is not dict or manifest.get('schema_version') != 1:
            raise ValueError()
        code = _verify_package(bundle, manifest)
        baseline = _document(_read(config['baseline_path'], config['baseline_sha256'], 65536))
        fixed = _document(_read(baseline['fixed_plan_path'], baseline['fixed_plan_sha256'], 65536))
        if (fixed['interpreter_sha256'] != baseline['interpreter_sha256']
                or fixed['runtime_manifest_sha256'] != baseline['runtime_manifest_sha256']
                or fixed['runtime_manifest_path'] != baseline['runtime_manifest_path']
                or not os.path.samefile(_checked_path(baseline['interpreter']), _checked_path(sys.executable))):
            raise ValueError()
        _read(sys.executable, baseline['interpreter_sha256'], 128 * 1024 * 1024)
        verifier = code / 'sandbox' / 'mind' / 'step2' / 'runtime_manifest.py'
        verifier_raw = _read(verifier, fixed['verifier_sha256'], 1024 * 1024)
        scope = {'__name__': '_wd_pinned_live_runtime_manifest'}
        exec(compile(verifier_raw, '<wd-live-runtime-verifier>', 'exec'), scope)
        root = Path(fixed['runtime_root'])
        scope['verify_runtime'](root, Path(fixed['runtime_manifest_path']), fixed['runtime_manifest_sha256'],
            expected_generation=fixed['runtime_generation'], expected_revision=fixed['runtime_revision'])
        runtime_code, runtime_site, runtime_model = root / 'assets' / 'code', root / 'site', root / 'model'
        if not all(path.is_dir() for path in (code, runtime_code, runtime_site, runtime_model)):
            raise ValueError()
        sys.path.insert(0, str(code))
        sys.path.append(str(runtime_code))
        sys.path.append(str(runtime_site))
        import importlib
        module = importlib.import_module('tools.wd_q4_live_update_once')
        if not os.path.samefile(module.__file__, code / 'tools' / 'wd_q4_live_update_once.py'):
            raise ValueError()
        return module.main(['--once', '--config', config_path, '--sha256', config_pin])
    except Exception:
        pass
    sys.stderr.write('{"status":"BOOTSTRAP_REFUSED"}\n')
    return 2


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
