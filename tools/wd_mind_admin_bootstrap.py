"""Verified-bytes bootstrap for a pinned operator administration candidate.

This module is data for a separately reviewed operator command; executing this
file by name is deliberately refused. The command must pin and execute these
exact bytes from an externally anchored deployment manifest before calling
``_run``. This candidate is not evidence of installation or human authentication.
Abnormal exit or absent unambiguous receipt means UNKNOWN: never automatically
retry or revert a reset. Same-account changes,
interpreter/stdlib/DLL integrity and changes between checks remain UNKNOWN.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys


_BUNDLE_STORE = Path(r'C:\Python\wd-reboot-bundles')
_CURRENT_POINTER = Path(r'C:\Python\WD_REBOOT_STATE_CURRENT.json')
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_COMMIT = re.compile(r'[0-9a-f]{40}\Z')
_MAX_CONTROL = 4 * 1024 * 1024
_MAX_SOURCE = 8 * 1024 * 1024
_MAX_SOURCES_TOTAL = 64 * 1024 * 1024

# Copy this independently reviewed literal into the operator's own trusted
# terminal as $ReviewedLiteral; do not import or execute this file to obtain
# it. The caller separately supplies the absolute $PinnedPython, its approved
# $PythonSha256, $ExactCommit and $ManifestSha256, then runs this reviewed form:
#   $f=[IO.File]::Open($PinnedPython,'Open','Read','Read')
#   try {
#       $h=[Security.Cryptography.SHA256]::Create()
#       try { $d=[BitConverter]::ToString($h.ComputeHash($f)).Replace('-','') }
#       finally { $h.Dispose() }
#       if ($d -ine $PythonSha256) { throw 'ADMIN_BOOTSTRAP_REFUSED' }
#       & $PinnedPython -I -S -B -c $ReviewedLiteral $ExactCommit $ManifestSha256 create
#   } finally { $f.Dispose() }
# Choose the literal argument `reset` instead of `create` only when authorized.
# Retaining the executable handle reduces replacement opportunities but does
# not verify its loaded libraries or establish immunity to same-account ABA.
# No generic bridge tool entrypoint or caller-selected filesystem root exists.
OPERATOR_LITERAL = r'''
import hashlib, hmac, json, os, pathlib, re, stat, sys
def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate')
        result[key] = value
    return result
def bad_constant(value): raise ValueError('constant')
def read(path, limit):
    for part in (path, *path.parents):
        item = os.lstat(part)
        if stat.S_ISLNK(item.st_mode) or getattr(item, 'st_file_attributes', 0) & 0x400: raise ValueError('path')
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not before.st_ino: raise ValueError('file')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    identity = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_nlink)
    if identity(before) != identity(opened) or identity(opened) != identity(after) or len(data) != opened.st_size or len(data) > limit: raise ValueError('changed')
    return data
try:
    if not (sys.flags.isolated and sys.flags.safe_path and sys.flags.no_site and sys.dont_write_bytecode): raise ValueError('isolation')
    commit, pin, operation = sys.argv[1:]
    if not re.fullmatch('[0-9a-f]{40}', commit) or not re.fullmatch('[0-9a-fA-F]{64}', pin) or operation not in ('create', 'reset'): raise ValueError('input')
    root = pathlib.Path(r'C:\Python\wd-reboot-bundles') / commit
    manifest_raw = read(root / 'deployment-manifest.json', 4 * 1024 * 1024)
    if not hmac.compare_digest(hashlib.sha256(manifest_raw).hexdigest(), pin.lower()): raise ValueError('manifest')
    manifest = json.loads(manifest_raw.decode('utf-8-sig'), object_pairs_hook=unique, parse_constant=bad_constant)
    relative = 'tools-bootstrap/tools/wd_mind_admin_bootstrap.py'
    source_pin = manifest['files'][relative]
    if type(source_pin) is not str or not re.fullmatch('[0-9a-fA-F]{64}', source_pin): raise ValueError('pin')
    path = root.joinpath(*relative.split('/'))
    source = read(path, 8 * 1024 * 1024)
    if not hmac.compare_digest(hashlib.sha256(source).hexdigest(), source_pin.lower()): raise ValueError('source')
    namespace = {'__name__':'wd_mind_admin_verified_bootstrap', '__file__':str(path), '__package__':None}
    exec(compile(source, str(path), 'exec', dont_inherit=True), namespace)
    result = namespace['_run'](commit, pin, operation)
    if type(result) is not int: raise ValueError('exit')
except Exception:
    print('ADMIN_BOOTSTRAP_REFUSED', file=sys.stderr)
    raise SystemExit(2) from None
raise SystemExit(result)
'''


class BootstrapRefused(ValueError):
    """A fixed message only; no paths, host details or underlying exception."""


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate')
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError('constant')


def _json(raw):
    value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique,
                       parse_constant=_bad_constant)
    if type(value) is not dict:
        raise ValueError('object')
    return value


def _plain_chain(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('path')
    for part in (path, *path.parents):
        entry = os.lstat(part)
        if getattr(entry, 'st_file_attributes', 0) & 0x400 or stat.S_ISLNK(entry.st_mode):
            raise ValueError('reparse')


def _read_file(path, limit, expected=None):
    path = Path(path)
    _plain_chain(path)
    before = os.lstat(path)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or not before.st_ino or before.st_size > limit):
        raise ValueError('file')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_nlink)
    if (identity(before) != identity(opened) or identity(opened) != identity(after)
            or len(raw) != opened.st_size or len(raw) > limit):
        raise ValueError('changed')
    if expected is not None and (type(expected) is not str or _HEX.fullmatch(expected.lower()) is None
            or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected.lower())):
        raise ValueError('pin')
    return raw


def _pin(pins, relative):
    value = pins.get(relative)
    if type(value) is not str or _HEX.fullmatch(value.lower()) is None:
        raise ValueError('missing_pin')
    return value


class _PinnedFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, blobs):
        self._blobs = blobs

    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'sandbox' or fullname.startswith('sandbox.'):
            raise ModuleNotFoundError('unsupported package module')
        if fullname.split('.')[0] not in ('tools', 'waggledance'):
            return None
        if fullname not in self._blobs:
            raise ModuleNotFoundError('unlisted package module')
        return importlib.util.spec_from_loader(fullname, self, is_package=self._blobs[fullname][2])

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        path, source, package = self._blobs[module.__name__]
        module.__file__ = path
        if package:
            module.__path__ = []
        exec(compile(source, path, 'exec', dont_inherit=True), module.__dict__)


def _require_isolation():
    if not (sys.flags.isolated and sys.flags.safe_path and sys.flags.no_site and sys.dont_write_bytecode):
        raise ValueError('isolation')


def _run(commit, manifest_sha256, operation):
    """Called only after the external literal has verified these source bytes."""
    try:
        _require_isolation()
        if (type(commit) is not str or _COMMIT.fullmatch(commit) is None
                or type(manifest_sha256) is not str or _HEX.fullmatch(manifest_sha256.lower()) is None
                or operation not in ('create', 'reset')):
            raise ValueError('input')
        pin = manifest_sha256.lower()
        root = _BUNDLE_STORE / commit
        pointer_raw = _read_file(_CURRENT_POINTER, 65536)
        pointer = _json(pointer_raw)
        if (pointer.get('source_commit') != commit or pointer.get('final_commit') != commit
                or type(pointer.get('active_bundle')) is not str
                or os.path.normcase(os.path.abspath(pointer['active_bundle'])) != os.path.normcase(os.path.abspath(root))
                or type(pointer.get('manifest_sha256')) is not str
                or pointer['manifest_sha256'].lower() != pin
                or type(pointer.get('final_manifest_sha256')) is not str
                or pointer['final_manifest_sha256'].lower() != pin):
            raise ValueError('pointer')
        manifest_path = root / 'deployment-manifest.json'
        manifest_raw = _read_file(manifest_path, _MAX_CONTROL, pin)
        manifest = _json(manifest_raw)
        if (type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1
                or manifest.get('source_commit') != commit or type(manifest.get('files')) is not dict):
            raise ValueError('manifest')
        pins = manifest['files']
        own = root / 'tools-bootstrap/tools/wd_mind_admin_bootstrap.py'
        if not os.path.samefile(own, __file__):
            raise ValueError('source')
        _read_file(own, _MAX_SOURCE, _pin(pins, 'tools-bootstrap/tools/wd_mind_admin_bootstrap.py'))
        definition_raw = _read_file(root / 'bridge-code-files.json', _MAX_CONTROL, _pin(pins, 'bridge-code-files.json'))
        definition = _json(definition_raw)
        paths = definition.get('python_files')
        if (type(paths) is not list or not all(type(p) is str for p in paths)
                or len(paths) != len(set(paths)) or definition.get('schema') != 'wd.bridge-code-package.v1'
                or definition.get('package_relative_root') != 'tools-bootstrap'
                or type(definition.get('python_entrypoints')) is not dict
                or 'tools/wd_mind_owner_reservation_admin.py' not in paths
                or 'tools/wd_mind_admin_bootstrap.py' not in paths
                or any(p in definition['python_entrypoints'].values() for p in
                       ('tools/wd_mind_owner_reservation_admin.py', 'tools/wd_mind_admin_bootstrap.py'))):
            raise ValueError('definition')
        blobs, total = {}, 0
        for relative in paths:
            parts = relative.split('/')
            if not relative.endswith('.py') or parts[0] not in ('tools', 'waggledance'):
                continue
            if any(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:\.py)?', part) is None for part in parts):
                raise ValueError('path')
            path = root / 'tools-bootstrap' / Path(*parts)
            source = _read_file(path, _MAX_SOURCE, _pin(pins, 'tools-bootstrap/' + relative))
            total += len(source)
            if total > _MAX_SOURCES_TOTAL:
                raise ValueError('size')
            package = parts[-1] == '__init__.py'
            name = '.'.join(parts[:-1] if package else parts[:-1] + [parts[-1][:-3]])
            if name in blobs:
                raise ValueError('module')
            blobs[name] = (str(path), source, package)
        if ('tools.wd_mind_owner_reservation_admin' not in blobs
                or not blobs.get('tools', (None, None, False))[2]
                or any(k == 'tools' or k.startswith('tools.') or k == 'waggledance'
                       or k.startswith('waggledance.') or k == 'sandbox' or k.startswith('sandbox.')
                       for k in sys.modules)):
            raise ValueError('closure')
        if (_read_file(_CURRENT_POINTER, 65536) != pointer_raw
                or _read_file(manifest_path, _MAX_CONTROL, pin) != manifest_raw):
            raise ValueError('changed')
        finder = _PinnedFinder(blobs)
        try:
            sys.meta_path.insert(0, finder)
            from tools import wd_mind_owner_reservation_admin as admin
            if (_read_file(_CURRENT_POINTER, 65536) != pointer_raw
                    or _read_file(manifest_path, _MAX_CONTROL, pin) != manifest_raw):
                raise ValueError('changed')
            result = admin.main([operation, '--source-commit', commit, '--manifest-sha256', pin])
        finally:
            try:
                sys.meta_path.remove(finder)
            except ValueError:
                # Registration may itself have failed before insertion.
                pass
        if type(result) is not int:
            raise ValueError('exit')
        return result
    except Exception:
        raise BootstrapRefused('ADMIN_BOOTSTRAP_REFUSED') from None


if __name__ == '__main__':
    print('owner_marker_bootstrap_requires_verified_literal', file=sys.stderr)
    raise SystemExit(2)
