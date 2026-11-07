"""Fixed-peer controlled imports; invoked only from independently pinned bytes.

Experimental process-local consistency layer. It does not authenticate same-user
code, native DLL dependencies, or prevent filesystem replacement races.
"""
import hmac
import json
import os
from pathlib import Path
import sys
import types


def install(runtime_root, manifest_path, manifest_sha256, *, _check, _sha, _unique_pairs,
            _interpreter_roots, _under):
    """Constrain new path imports in this isolated child; not an OS sandbox.

    The independently pinned manifest bounds runtime files. Interpreter files
    remain trusted as in _module_origins. Native DLL dependencies, direct
    sys.modules insertion, same-user mutation and check/use races remain outside
    this control; the existing origin checks still run before and after query.
    ZIP imports are deliberately unsupported on this fixed peer route.
    """
    import importlib.machinery as machinery
    import importlib.util as import_util

    _check(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
    standard = (machinery.BuiltinImporter, machinery.FrozenImporter, machinery.PathFinder)
    _check(type(sys.meta_path) is list and tuple(sys.meta_path) == standard
           and type(sys.path_hooks) is list and type(sys.path_importer_cache) is dict)
    root = Path(runtime_root).resolve(strict=True)
    with open(manifest_path, 'rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    _check(len(raw) <= 8 * 1024 * 1024 and hmac.compare_digest(_sha(raw), manifest_sha256))
    document = json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=_unique_pairs)
    _check(type(document) is dict and type(document.get('files')) is list)
    entries = {}
    for item in document['files']:
        _check(type(item) is dict and type(item.get('path')) is str and item['path'] not in entries
               and type(item.get('size')) is int and item['size'] >= 0 and type(item.get('sha256')) is str)
        entries[item['path']] = (item['size'], item['sha256'])
    runtime_roots = tuple(os.path.normcase(os.path.realpath(root / part)) for part in ('assets/code', 'site'))
    interpreter_roots = tuple(_interpreter_roots())
    roots = runtime_roots + interpreter_roots
    excluded = os.path.normcase(os.path.realpath(Path(sys.base_prefix) / 'Lib' / 'site-packages'))
    loader_types = (machinery.ExtensionFileLoader, machinery.SourceFileLoader, machinery.SourcelessFileLoader)
    hook = machinery.FileFinder.path_hook((loader_types[0], machinery.EXTENSION_SUFFIXES),
                                          (loader_types[1], machinery.SOURCE_SUFFIXES),
                                          (loader_types[2], machinery.BYTECODE_SUFFIXES))
    methods = tuple(vars(item).get('find_spec') for item in standard)

    def verified_file(path, *, source):
        _check(type(path) is str and os.path.isabs(path))
        file = Path(path).resolve(strict=True)
        normalized = os.path.normcase(os.path.realpath(file))
        _check(any(_under(normalized, allowed) for allowed in roots) and not _under(normalized, excluded))
        try:
            relative = file.relative_to(root).as_posix()
        except ValueError:
            _check(any(_under(normalized, allowed) for allowed in interpreter_roots))
            relative = None
        if relative is not None:
            _check(relative in entries)
            size, digest = entries[relative]
            with open(file, 'rb') as stream:
                data = stream.read(size + 1)
            _check(len(data) == size and hmac.compare_digest(_sha(data), digest))
        elif source:
            with open(file, 'rb') as stream:
                data = stream.read(64 * 1024 * 1024 + 1)
            _check(len(data) <= 64 * 1024 * 1024)
        else:
            data = None  # interpreter binaries are within the existing trust boundary
        return file, data

    original_native_create = machinery.ExtensionFileLoader.create_module
    original_native_exec = machinery.ExtensionFileLoader.exec_module
    original_spec_from_file = import_util.spec_from_file_location
    original_spec_from_loader = import_util.spec_from_loader
    omitted = object()
    absent = object()
    witnessed = {}

    def code_tree(code):
        result = [code]
        for value in code.co_consts:
            if type(value) is types.CodeType:
                result.extend(code_tree(value))
        return result

    def capture_function(function, source_globals, codes, states, seen):
        _check(type(function) is types.FunctionType and function.__globals__ is source_globals
               and any(function.__code__ is code for code in codes)
               and function.__defaults__ is None and function.__kwdefaults__ is None)
        if id(function) in seen:
            return
        seen.add(id(function))
        cells = function.__closure__ or ()
        values = tuple(cell.cell_contents for cell in cells)
        globals_used = tuple((name, source_globals.get(name, absent)) for name in function.__code__.co_names)
        states.append((function, function.__code__, function.__closure__, values, globals_used, source_globals))
        for value in values:
            if type(value) is types.FunctionType:
                capture_function(value, source_globals, codes, states, seen)
            else:
                _check(type(value) in (str, bytes, int, bool, type(None)))

    def verify_witness(name, module, *, check_registry=True):
        _check(name in witnessed)
        expected, attributes, states = witnessed[name]
        _check(module is expected and type(module) is types.ModuleType
               and (not check_registry or sys.modules.get(name) is module))
        current = vars(module)
        _check(set(current) == set(attributes) and all(current[key] is value for key, value in attributes.items()))
        for function, code, closure, values, globals_used, source_globals in states:
            _check(function.__code__ is code and function.__globals__ is source_globals
                   and function.__closure__ is closure and function.__defaults__ is None
                   and function.__kwdefaults__ is None)
            _check(all(cell.cell_contents is value for cell, value in zip(closure or (), values)))
            _check(all(source_globals.get(key, absent) is value for key, value in globals_used))

    def capture_created(before, code, source_globals):
        codes = code_tree(code)
        allowed = {'__name__', '__doc__', '__package__', '__loader__', '__spec__', '__file__',
                   '__getattr__', '__dir__'}
        for name, module in list(sys.modules.items()):
            if before.get(name, absent) is module or type(module) is not types.ModuleType:
                continue
            attrs = vars(module)
            getter = attrs.get('__getattr__')
            if (type(getter) is not types.FunctionType or getter.__globals__ is not source_globals
                    or not any(getter.__code__ is candidate for candidate in codes)):
                continue
            if not set(attrs) <= allowed or '__file__' not in attrs:
                continue
            _check(attrs.get('__name__') == name and attrs.get('__spec__') is None
                   and attrs.get('__loader__') is None and attrs.get('__file__') is None)
            if name in before:
                _check(name in witnessed and before[name] is witnessed[name][0])
                verify_witness(name, before[name], check_registry=False)
            states = []
            seen = set()
            capture_function(getter, source_globals, codes, states, seen)
            directory = attrs.get('__dir__')
            if directory is not None:
                capture_function(directory, source_globals, codes, states, seen)
            witnessed[name] = (module, dict(attrs), states)

    def source_code(loader, requested):
        _check(type(loader) is loader_types[1] and loader.name == requested)
        file, data = verified_file(loader.path, source=True)
        return compile(data, str(file), 'exec', dont_inherit=True)

    def source_exec(loader, module):
        _check(type(loader) is loader_types[1] and module.__name__ == loader.name)
        code = source_code(loader, module.__name__)
        names = {name for nested in code_tree(code) for name in nested.co_names}
        before = dict(sys.modules) if 'modules' in names and 'ModuleType' in names else None
        if before is not None:
            for name, (existing, _, _) in tuple(witnessed.items()):
                _check(before.get(name) is existing)
                verify_witness(name, existing, check_registry=False)
        exec(code, module.__dict__)
        if before is not None:
            capture_created(before, code, module.__dict__)

    def bytecode_refused(*args, **kwargs):
        _check(False)

    def native_create(loader, spec):
        _check(type(loader) is loader_types[0] and spec.loader is loader)
        verified_file(loader.path, source=False)
        return original_native_create(loader, spec)

    def native_exec(loader, module):
        _check(type(loader) is loader_types[0])
        verified_file(loader.path, source=False)
        return original_native_exec(loader, module)

    def direct_spec(name, location=None, *, loader=None, submodule_search_locations=omitted):
        _check(loader is None or type(loader) in loader_types[:2])
        kwargs = {'loader': loader}
        if submodule_search_locations is not omitted:
            kwargs['submodule_search_locations'] = submodule_search_locations
        spec = original_spec_from_file(name, location, **kwargs)
        if spec is not None:
            _check(type(spec.loader) in loader_types[:2] and type(spec.origin) is str)
            verified_file(spec.origin, source=type(spec.loader) is loader_types[1])
        return spec

    def direct_loader_spec(name, loader, *, origin=None, is_package=None):
        _check(loader is None or type(loader) in loader_types[:2])
        if loader is not None:
            verified_file(loader.path, source=type(loader) is loader_types[1])
        return original_spec_from_loader(name, loader, origin=origin, is_package=is_package)

    # Standard direct SourceFileLoader and spec_from_file_location calls must
    # obey the same byte check as path imports. Arbitrary exec/custom native
    # code is outside this process-local consistency boundary.
    machinery.SourceFileLoader.get_code = source_code
    machinery.SourceFileLoader.exec_module = source_exec
    machinery.SourcelessFileLoader.get_code = bytecode_refused
    machinery.SourcelessFileLoader.exec_module = bytecode_refused
    machinery.ExtensionFileLoader.create_module = native_create
    machinery.ExtensionFileLoader.exec_module = native_exec
    import_util.spec_from_file_location = direct_spec
    import_util.spec_from_loader = direct_loader_spec
    _check(type(sys.path) is list and all(type(item) is str and os.path.isabs(item) for item in sys.path))
    # -I may retain the interpreter prefix and a ZIP probe outside the two
    # trusted interpreter subdirectories. Remove all such search entries.
    sys.path[:] = [item for item in sys.path if any(_under(os.path.normcase(os.path.realpath(item)), r)
                                                     for r in roots)
                   and not _under(os.path.normcase(os.path.realpath(item)), excluded)]
    sys.path_hooks[:] = [hook]
    sys.path_importer_cache.clear()

    class ControlledFinder:
        def accept_created(self, name, module):
            self.check()
            verify_witness(name, module)

        def check(self):
            _check(type(sys.meta_path) is tuple and sys.meta_path == (self,) + standard[:2]
                   and type(sys.path_hooks) is list and sys.path_hooks == [hook]
                   and type(sys.path_importer_cache) is dict
                   and all(value is None or type(value) is machinery.FileFinder
                           for value in sys.path_importer_cache.values())
                   and tuple(vars(item).get('find_spec') for item in standard) == methods
                   and machinery.SourceFileLoader.get_code is source_code
                   and machinery.SourceFileLoader.exec_module is source_exec
                   and machinery.SourcelessFileLoader.get_code is bytecode_refused
                   and machinery.SourcelessFileLoader.exec_module is bytecode_refused
                   and machinery.ExtensionFileLoader.create_module is native_create
                   and machinery.ExtensionFileLoader.exec_module is native_exec
                   and import_util.spec_from_file_location is direct_spec
                   and import_util.spec_from_loader is direct_loader_spec)

        def find_spec(self, fullname, path=None, target=None):
            self.check()
            if (standard[0].find_spec(fullname, path, target) is not None or
                    standard[1].find_spec(fullname, path, target) is not None):
                return None
            search = sys.path if path is None else path
            for entry in search:
                _check(type(entry) is str and os.path.isabs(entry))
                resolved = os.path.normcase(os.path.realpath(entry))
                _check(any(_under(resolved, allowed) for allowed in roots) and not _under(resolved, excluded))
                _check(not os.path.isfile(entry))  # no unverified ZIP importer
            # Discard any FileFinder internal state before invoking it again.
            sys.path_importer_cache.clear()
            spec = machinery.PathFinder.find_spec(fullname, path, target)
            if spec is None:
                return None  # the remaining standard finders produce Python's normal missing semantics
            if spec.origin is None:
                locations = list(spec.submodule_search_locations or ())
                _check(locations and all(type(p) is str and any(_under(os.path.normcase(os.path.realpath(p)), r)
                       for r in roots) and not _under(os.path.normcase(os.path.realpath(p)), excluded)
                       for p in locations))
                return spec
            # Never load a cache-only .pyc: its normal loader reopens the file
            # after this check. Source files use the exact checked bytes below.
            _check(type(spec.loader) in loader_types[:2] and type(spec.origin) is str)
            file = Path(spec.origin).resolve(strict=True)
            normalized = os.path.normcase(os.path.realpath(file))
            _check(any(_under(normalized, allowed) for allowed in roots) and not _under(normalized, excluded))
            try:
                relative = file.relative_to(root).as_posix()
            except ValueError:
                _check(any(_under(normalized, allowed) for allowed in interpreter_roots))
                relative = None
            if relative is not None:
                _check(relative in entries)
                size, digest = entries[relative]
                with open(file, 'rb') as source:
                    data = source.read(size + 1)
                _check(len(data) == size and hmac.compare_digest(_sha(data), digest))
            elif type(spec.loader) is machinery.SourceFileLoader:
                with open(file, 'rb') as source:
                    data = source.read(64 * 1024 * 1024 + 1)
                _check(len(data) <= 64 * 1024 * 1024)
            return spec

        def find_distributions(self, context=None):
            # importlib.metadata uses this hook (not find_spec). Return only
            # distribution directories whose complete bytes match the runtime
            # manifest; never fall through to an ambient site or ZIP archive.
            import importlib.metadata
            import stat
            self.check()
            requested = sys.path if context is None else context.path
            for entry in requested:
                _check(type(entry) is str and os.path.isabs(entry))
                normalized = os.path.normcase(os.path.realpath(entry))
                _check(any(_under(normalized, allowed) for allowed in roots) and not _under(normalized, excluded)
                       and not os.path.isfile(entry))
            discovered = machinery.PathFinder.find_distributions(context=context)
            result = []
            for distribution in discovered:
                directory = Path(distribution._path)
                _check(not directory.is_symlink() and directory.is_dir())
                directory = directory.resolve(strict=True)
                site = (root / 'site').resolve(strict=True)
                _check(directory.parent == site and directory.name.endswith('.dist-info'))
                prefix = directory.relative_to(root).as_posix() + '/'
                expected = {name for name in entries if name.startswith(prefix)}
                actual = set()
                verified_data = {}
                total = 0
                pending = [directory]
                while pending:
                    parent = pending.pop()
                    with os.scandir(parent) as iterator:
                        for item in iterator:
                            # Windows DirEntry.stat may report st_nlink=0.
                            info = os.lstat(item.path)
                            _check(not getattr(info, 'st_file_attributes', 0) & 0x400)
                            item_path = Path(item.path)
                            if stat.S_ISDIR(info.st_mode):
                                pending.append(item_path)
                            else:
                                _check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
                                name = item_path.relative_to(root).as_posix()
                                _check(name in expected and name not in actual)
                                size, digest = entries[name]
                                total += size
                                _check(size <= 8 * 1024 * 1024 and total <= 32 * 1024 * 1024)
                                with open(item_path, 'rb') as stream:
                                    data = stream.read(size + 1)
                                _check(len(data) == size and hmac.compare_digest(_sha(data), digest))
                                actual.add(name)
                                verified_data[name[len(prefix):]] = data
                _check(actual == expected and actual)
                # A PathDistribution would reopen METADATA/RECORD after the
                # checks. Its replacement serves the exact checked bytes.
                class VerifiedDistribution(importlib.metadata.Distribution):
                    def __init__(self, data, base):
                        self._verified_data, self._base = data, base

                    def read_text(self, filename):
                        if type(filename) is not str:
                            return None
                        data = self._verified_data.get(filename.replace('\\', '/'))
                        return None if data is None else data.decode('utf-8')

                    def locate_file(self, path):
                        return self._base / path
                result.append(VerifiedDistribution(verified_data, site))
            return result

    finder = ControlledFinder()
    sys.meta_path = (finder,) + standard[:2]
    return finder

