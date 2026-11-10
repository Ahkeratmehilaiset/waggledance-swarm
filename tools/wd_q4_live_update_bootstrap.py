"""Fixed-purpose isolated bootstrap for the packaged Q4 live updater.

The invoker must independently pin the interpreter, this script, deployment
manifest and configuration before launching. This process rechecks those pins,
all packaged code and the independently pinned runtime before importing it.
It accepts no module, code or executable selector. It is not authentication,
ACL isolation, a native-DLL/stdlib verifier or protection from same-user races.
"""
import hashlib
import hmac
import io
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import re
import stat
import sys

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_MAX = 8 * 1024 * 1024
_PROJECT = ('tools', 'sandbox', 'core', 'waggledance')
_P1_CONFIG_KEYS = frozenset({'schema', 'route_path', 'route_sha256', 'plan_path', 'anchor_path',
    'policy_path', 'public_policy_path', 'baseline_path', 'baseline_sha256', 'manifest_sha256', 'p1_enabled'})


def _configuration_allowed(config):
    """Keep v1 defaults; v2 is the exact externally pinned P1 opt-in."""
    if type(config) is not dict:
        return False
    if config.get('schema') == 'wd.q4-live-update-once.v1':
        return not any(key.lower() == 'p1_enabled' for key in config)
    return (config.get('schema') == 'wd.q4-live-update-once.v2'
        and set(config) == _P1_CONFIG_KEYS and config['p1_enabled'] is True
        and all(type(config[key]) is str and config[key] for key in _P1_CONFIG_KEYS - {'p1_enabled'})
        and all(_HEX.fullmatch(config[key]) for key in ('route_sha256', 'baseline_sha256', 'manifest_sha256')))



class _PackageMembershipRefused(ValueError):
    pass


class _BoundedOutput(io.StringIO):
    def write(self, value):
        if self.tell() + len(value) > 65536:
            raise ValueError()
        return super().write(value)


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
        read_size = min(limit, max(0, before.st_size)) + 1
        raw = stream.read(read_size)
        after = os.fstat(stream.fileno())
    final = path.stat()
    if (len(raw) != before.st_size or len(raw) > limit or not os.path.samestat(before, opened)
            or not os.path.samestat(opened, after) or not os.path.samestat(after, final)
            or any(info.st_size != before.st_size or info.st_mtime_ns != before.st_mtime_ns
                   for info in (opened, after, final))
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
                raise _PackageMembershipRefused()
        for name in names:
            path = directory / name
            relative = path.relative_to(code).as_posix()
            if relative not in entries or relative in seen or len(seen) >= 50000:
                raise _PackageMembershipRefused()
            _read(path, entries[relative], 1024 * 1024 * 1024)
            seen.add(relative)
    if seen != set(entries):
        raise _PackageMembershipRefused()
    return code


def _inside_code(path, code):
    actual = os.path.normcase(os.path.abspath(os.fspath(path)))
    root = os.path.normcase(os.path.abspath(os.fspath(code)))
    try:
        if os.path.commonpath((actual, root)) != root:
            return False
        for part in (Path(path), *Path(path).parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                return False
        return True
    except (OSError, ValueError):
        return False


def _project_name(name):
    return any(name == root or name.startswith(root + '.') for root in _PROJECT)


def _check_spec(spec, code):
    origin = spec.origin
    locations = spec.submodule_search_locations
    if origin is None and locations is None:
        raise ImportError('project_origin_refused')
    if origin is not None and not _inside_code(origin, code):
        raise ImportError('project_origin_refused')
    if locations is not None and (not locations or any(not _inside_code(path, code) for path in locations)):
        raise ImportError('project_origin_refused')


class _ProjectOriginGuard(importlib.abc.MetaPathFinder):
    def __init__(self, code):
        self.code = code

    def find_spec(self, fullname, path=None, target=None):
        if not _project_name(fullname):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is not None:
            _check_spec(spec, self.code)
        return spec


def _check_project_origins(code):
    for name, module in tuple(sys.modules.items()):
        if _project_name(name) and module is not None:
            spec = getattr(module, '__spec__', None)
            if spec is None:
                raise ImportError('project_origin_refused')
            _check_spec(spec, code)


def main(argv):
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
            raise ValueError()
        if (len(argv) in (6, 8) and argv[0] == '--config' and argv[2] == '--sha256'
                and argv[4] == '--manifest-sha256'):
            config_path, config_pin, manifest_pin = argv[1], argv[3], argv[5]
            module_name = 'wd_q4_live_update_once'
            module_args = ['--once', '--config', config_path, '--sha256', config_pin]
            if len(argv) == 8:
                if argv[6] != '--host-attempt' or not _HEX.fullmatch(argv[7]):
                    raise ValueError()
                module_args += argv[6:8]
        elif (len(argv) in (11, 13) and argv[0] == '--recover' and argv[1] == '--config'
              and argv[3] == '--sha256' and argv[5] == '--manifest-sha256' and argv[7] == '--attempt'):
            config_path, config_pin, manifest_pin = argv[2], argv[4], argv[6]
            if not _HEX.fullmatch(argv[8]):
                raise ValueError()
            if len(argv) == 11 and argv[9] == '--progress-sha256' and _HEX.fullmatch(argv[10]):
                evidence = argv[9:11]
            elif (len(argv) == 13 and argv[9] in ('--runner-receipt-sha256', '--job-receipt-sha256') and _HEX.fullmatch(argv[10])
                  and argv[11] == '--pointer-sha256' and _HEX.fullmatch(argv[12])):
                evidence = argv[9:13]
            else:
                raise ValueError()
            module_name = 'wd_q4_live_update_recovery'
            module_args = ['--recover', '--config', config_path, '--sha256', config_pin, '--attempt', argv[8], *evidence]
        else:
            raise ValueError()
        config = _document(_read(config_path, config_pin, 65536))
        if not _configuration_allowed(config) or config.get('manifest_sha256') != manifest_pin:
            raise ValueError()
        inherited_manifest = os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower()
        if inherited_manifest and inherited_manifest != manifest_pin:
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
        # A constrained Job intentionally inherits no WD_* variables. The
        # independently supplied manifest pin was just checked; expose only
        # that pin to this child after successful package/runtime validation.
        os.environ['WD_REBOOT_EXPECTED_MANIFEST_HASH'] = manifest_pin
        sys.path.insert(0, str(code))
        sys.path.append(str(runtime_site))
        sys.meta_path.insert(0, _ProjectOriginGuard(code))
        import importlib
        module = importlib.import_module('tools.' + module_name)
        if not os.path.samefile(module.__file__, code / 'tools' / (module_name + '.py')):
            raise ValueError()
        _check_project_origins(code)
        output = _BoundedOutput()
        prior_stdout = sys.stdout
        try:
            sys.stdout = output
            result = module.main(module_args)
            _check_project_origins(code)
        finally:
            sys.stdout = prior_stdout
        sys.stdout.write(output.getvalue())
        return result
    except _PackageMembershipRefused:
        sys.stderr.write('{"status":"BOOTSTRAP_REFUSED","reason":"package_membership"}\n')
        return 2
    except Exception:
        pass
    sys.stderr.write('{"status":"BOOTSTRAP_REFUSED"}\n')
    return 2


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
