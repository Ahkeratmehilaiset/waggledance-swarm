"""Narrow, default-OFF checks for a namespace in a verified Q4 runtime.

The caller must load these exact helper bytes from an independently pinned
runtime manifest and supply its already verified path -> (size, sha256) entries.
This checks structure and one pinned namespace root; it does not authenticate a
publisher, protect against a hostile same-user writer, or eliminate filesystem
TOCTOU. No sys.path search or module import is performed by this function.
"""
import importlib.machinery as machinery
import os
from pathlib import Path
import re
import stat
import types

from sandbox.mind.step2 import runtime_manifest


_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def verify_namespace(name, module, *, runtime_root, entries, enabled=False):
    """Return True only for one manifest-backed native namespace location."""
    if enabled is not True:
        return False
    try:
        if (type(name) is not str or len(name) > 512 or _NAME.fullmatch(name) is None
                or type(module) is not types.ModuleType or type(entries) is not dict):
            return False
        fields = vars(module)
        spec = fields.get('__spec__')
        if (type(spec) is not machinery.ModuleSpec or spec.name != name or spec.origin is not None
                or type(spec.loader) is not machinery.NamespaceLoader
                or fields.get('__name__') != name or fields.get('__package__') != name
                or fields.get('__file__') is not None or fields.get('__loader__') is not spec.loader):
            return False
        locations = spec.submodule_search_locations
        if (locations is None or fields.get('__path__') is not locations
                or getattr(spec.loader, '_path', None) is not locations):
            return False
        observed = list(locations)
        if len(observed) != 1 or type(observed[0]) is not str or not os.path.isabs(observed[0]):
            return False
        root = runtime_manifest._check_chain(runtime_root)
        actual = runtime_manifest._check_chain(observed[0])
        if not stat.S_ISDIR(os.lstat(actual).st_mode):
            return False
        norm = lambda value: os.path.normcase(os.path.abspath(os.fspath(value)))
        name_path = Path(*name.split('.'))
        prefixes = ('site', 'assets/code')
        matches = [prefix for prefix in prefixes if norm(root / prefix / name_path) == norm(actual)]
        if len(matches) != 1:
            return False
        # Real package initializers would make this a source/native package, not
        # a namespace, even if somebody supplied a forged NamespaceLoader.
        suffixes = machinery.SOURCE_SUFFIXES + machinery.BYTECODE_SUFFIXES + machinery.EXTENSION_SUFFIXES
        if any(os.path.lexists(actual / ('__init__' + suffix)) for suffix in suffixes):
            return False
        prefix = matches[0] + '/' + name.replace('.', '/') + '/'
        descendants = [(path, pin) for path, pin in entries.items()
                       if type(path) is str and path.startswith(prefix)]
        if not descendants:
            return False
        for path, pin in descendants:
            runtime_manifest._path_parts(path)
            if (type(pin) is not tuple or len(pin) != 2 or type(pin[0]) is not int or pin[0] < 0
                    or type(pin[1]) is not str or _SHA.fullmatch(pin[1]) is None):
                return False
        return (list(locations) == observed and fields.get('__spec__') is spec
                and fields.get('__loader__') is spec.loader and fields.get('__path__') is locations)
    except Exception:
        return False
