"""Fixed-peer controlled imports; invoked only from independently pinned bytes.

Experimental process-local consistency layer. It does not authenticate same-user
code, native DLL dependencies, or prevent filesystem replacement races.
"""
import hmac
import gc
import json
import os
from pathlib import Path
import sys
import types


class _NativeRegistrations:
    """Record bounded registration observations, never native object creation.

    Cooperative same-process evidence only: GC-untracked objects, malicious
    native code and hostile same-user writes are not authenticated or excluded.
    Nested or concurrent intervals are not proof.
    """
    def __init__(self, check):
        self._check = check
        self._records = {}
        self._deferred = {}

    def bind_objects(self, before, after, tainted, *, owner, single_thread):
        """Observe newly GC-tracked objects across one verified native call.

        Observation is cooperative evidence, not proof of native allocation.
        Keep strong references to exclude borrowed objects and identity reuse.
        """
        self._check(type(before) is tuple and type(after) is tuple and type(tainted) is set
                    and type(owner) is str and owner != ''
                    and single_thread is True)
        known = {id(module) for module in before} | tainted
        for module in after:
            if type(module) is not types.ModuleType or id(module) in known:
                continue
            attrs = vars(module)
            if (type(attrs.get('__name__')) is not str or not attrs['__name__'].startswith(owner + '.')
                    or attrs.get('__file__') is not None
                    or attrs.get('__spec__') is not None or attrs.get('__loader__') is not None):
                continue
            self._deferred[id(module)] = (module, dict(attrs))

    def bind_source(self, before, after, tainted, *, single_thread):
        """Bind a later registration to the same observed object, not its name."""
        self._check(type(before) is dict and type(after) is dict and type(tainted) is set
                    and single_thread is True)
        previously_registered = {id(module) for module in before.values()}
        for name, module in after.items():
            if name in before or name in tainted or type(name) is not str or id(module) in previously_registered:
                continue
            candidate = self._deferred.get(id(module))
            if candidate is None or candidate[0] is not module:
                continue
            attrs = vars(module)
            saved = candidate[1]
            if (attrs.get('__name__') != name or set(attrs) != set(saved)
                    or any(attrs[key] is not value for key, value in saved.items())):
                continue
            self._check(name not in self._records)
            self._records[name] = (module, dict(attrs))
            del self._deferred[id(module)]

    def bind(self, before, after, tainted, *, single_thread, known_objects):
        self._check(type(before) is dict and type(after) is dict and type(tainted) is set
                    and type(known_objects) is tuple and single_thread is True)
        known = {id(value) for value in before.values()} | {id(value) for value in known_objects}
        for name, module in after.items():
            if (name in before or name in tainted or type(name) is not str
                    or type(module) is not types.ModuleType or id(module) in known):
                continue
            attrs = types.ModuleType.__getattribute__(module, '__dict__')
            if (attrs.get('__name__') != name or attrs.get('__file__') is not None
                    or attrs.get('__spec__') is not None or attrs.get('__loader__') is not None):
                continue
            self._check(name not in self._records)
            self._records[name] = (module, dict(attrs))

    def accept(self, name, module, registry):
        self._check(name in self._records and type(registry) is dict and registry.get(name) is module)
        expected, attrs = self._records[name]
        self._check(module is expected and type(module) is types.ModuleType)
        current = types.ModuleType.__getattribute__(module, '__dict__')
        self._check(set(current) == set(attrs) and all(current[key] is value for key, value in attrs.items()))


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
    subclass_witnessed = {}
    inherited_witnessed = {}
    deferred_source_aliases = {}
    tracked_source_aliases = {}
    source_alias_dependencies = {}
    verified_sources = {}
    active_sources = set()
    failed_sources = set()
    native_witnessed = _NativeRegistrations(_check)
    native_stack = []
    source_stack = []
    bootstrap_main = sys.modules.get('__main__')
    _check(type(bootstrap_main) is types.ModuleType)
    initial_main_alias = sys.modules.get('__mp_main__', absent)
    _check(initial_main_alias is absent or initial_main_alias is bootstrap_main)
    main_alias_witnessed = False
    multiprocessing_source = os.path.normcase(os.path.realpath(
        Path(sys.base_prefix) / 'Lib' / 'multiprocessing' / '__init__.py'))

    def check_main_alias():
        attrs = types.ModuleType.__getattribute__(bootstrap_main, '__dict__')
        _check(type(bootstrap_main) is types.ModuleType and attrs.get('__name__') == '__main__'
               and attrs.get('__file__') is None and attrs.get('__spec__') is None
               and sys.modules.get('__main__') is bootstrap_main
               and sys.modules.get('__mp_main__') is bootstrap_main)

    def modules_in_gc():
        return tuple(value for value in gc.get_objects() if type(value) is types.ModuleType)

    def source_modules_in_gc():
        # Include ModuleType subclasses without calling an instance's custom
        # __class__ or attribute hooks while observing nested executions.
        return tuple(value for value in gc.get_objects()
                     if types.ModuleType in type.__getattribute__(type(value), '__mro__'))

    def mark_nested(before, known_objects):
        if not native_stack:
            return
        after = sys.modules
        changed = {name for name in set(before) | set(after)
                   if before.get(name, absent) is not after.get(name, absent)}
        known_ids = {id(module) for module in known_objects}
        changed_objects = {id(module) for module in modules_in_gc() if id(module) not in known_ids}
        for event in native_stack:
            event['names'].update(changed)
            event['objects'].update(changed_objects)

    def mark_source_nested(before, known_objects=None):
        if source_stack:
            after = sys.modules
            changed = {name for name in set(before) | set(after)
                       if before.get(name, absent) is not after.get(name, absent)}
            for event in source_stack:
                event['names'].update(changed)
            if known_objects is not None and any(event['watch'] for event in source_stack):
                known = {id(module) for module in known_objects}
                changed_objects = {id(module) for module in source_modules_in_gc() if id(module) not in known}
                for event in source_stack:
                    if event['watch']:
                        event['objects'].update(changed_objects)

    def code_tree(code):
        result = [code]
        for value in code.co_consts:
            if type(value) is types.CodeType:
                result.extend(code_tree(value))
        return result

    # Interpreter files loaded before this guard retain the pre-existing trusted
    # interpreter boundary. Save exact code objects (including nested decorator
    # code) now; a filename string alone cannot mint a later witness.
    baseline_lru_type = None
    for baseline_name, baseline_module in tuple(sys.modules.items()):
        if type(baseline_module) is not types.ModuleType:
            continue
        baseline_globals = types.ModuleType.__getattribute__(baseline_module, '__dict__')
        baseline_path = baseline_globals.get('__file__')
        if type(baseline_path) is not str or not os.path.isabs(baseline_path):
            continue
        normalized = os.path.normcase(os.path.realpath(baseline_path))
        if not any(_under(normalized, allowed) for allowed in interpreter_roots) or _under(normalized, excluded):
            continue
        if baseline_name == 'functools' and type(baseline_globals.get('_lru_cache_wrapper')) is type:
            baseline_lru_type = baseline_globals['_lru_cache_wrapper']
        baseline_codes = []
        for value in tuple(baseline_globals.values()):
            if type(value) is types.FunctionType and value.__globals__ is baseline_globals:
                baseline_codes.extend(code_tree(value.__code__))
        if baseline_codes:
            verified_sources[id(baseline_globals)] = (baseline_globals, tuple(baseline_codes))

    def capture_function(function, source_globals, codes, states, seen, allowed_class=None, cross_source=None):
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
                if cross_source is not None and value.__globals__ is not source_globals:
                    cross_source(value)
                else:
                    capture_function(value, source_globals, codes, states, seen, allowed_class, cross_source)
            elif value is allowed_class and allowed_class is not None:
                continue
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

    def capture_inherited(module, source_module, *, require_original=True):
        """Bind inherited wrappers to previously verified Python source objects.

        This is same-process observation, not authentication of native state or
        protection against hostile same-user mutation between checks.
        """
        classes, objects, functions, seen_classes, seen_functions = [], [], [], set(), set()

        def source_for_class(cls):
            matches = []
            for source_globals, codes in verified_sources.values():
                if (source_globals.get('__name__') == cls.__module__
                        and source_globals.get(cls.__name__) is cls):
                    candidates = [item for item in codes if item.co_qualname == cls.__qualname__]
                    if len(candidates) == 1:
                        matches.append((source_globals, code_tree(candidates[0])))
            _check(len(matches) == 1)
            return matches[0]

        def add_function(function, allowed_class=None):
            record = verified_sources.get(id(function.__globals__))
            _check(record is not None and record[0] is function.__globals__)
            capture_function(function, record[0], record[1], functions, seen_functions, allowed_class, add_function)

        def visit_class(cls):
            if cls in (object, types.ModuleType) or cls in seen_classes:
                return
            _check(type(cls) is type and len(cls.__bases__) == 1)
            seen_classes.add(cls)
            visit_class(cls.__bases__[0])
            owner, class_codes = source_for_class(cls)
            attrs = dict(vars(cls))
            classes.append((cls, attrs, owner))
            metadata = {'__module__', '__qualname__', '__doc__', '__annotations__',
                        '__firstlineno__', '__static_attributes__', '__dict__', '__weakref__'}
            for name, value in attrs.items():
                if name in metadata:
                    continue
                if type(value) is types.FunctionType:
                    add_function(value, cls)
                elif type(value) is staticmethod:
                    function = value.__func__
                    _check(type(function) is types.FunctionType)
                    add_function(function, cls)
                elif type(value) is property:
                    for function in (value.fget, value.fset, value.fdel):
                        if function is not None:
                            _check(type(function) is types.FunctionType)
                            add_function(function, cls)
                elif baseline_lru_type is not None and type(value) is baseline_lru_type:
                    state = dict(object.__getattribute__(value, '__dict__'))
                    _check(type(state.get('__wrapped__')) is types.FunctionType)
                    objects.append((value, baseline_lru_type, state))
                    for item in state.values():
                        if type(item) is types.FunctionType:
                            add_function(item, cls)
                elif type(value) in (str, int, float, bool, type(None)):
                    continue
                else:
                    descriptor_cls = type(value)
                    visit_class(descriptor_cls)
                    _check(descriptor_cls not in (object, types.ModuleType))
                    state = dict(object.__getattribute__(value, '__dict__'))
                    objects.append((value, descriptor_cls, state))
                    for item in state.values():
                        if type(item) is types.FunctionType:
                            add_function(item)

        cls = type(module)
        _check(type(cls) is type and issubclass(cls, types.ModuleType))
        visit_class(cls)
        attrs = types.ModuleType.__getattribute__(module, '__dict__')
        if require_original:
            _check(attrs.get('m') is source_module)
        return cls, dict(attrs), classes, objects, functions

    def capture_source_aliases(source_globals, source_module, known_objects, tainted_objects):
        known = {id(module) for module in known_objects} | tainted_objects
        for module in tuple(source_globals.values()):
            cls = type(module)
            if (id(module) in known or type(cls) is not type or cls is types.ModuleType
                    or not issubclass(cls, types.ModuleType)
                    or cls.__module__ != source_globals.get('__name__')
                    or source_globals.get(cls.__name__) is not cls):
                continue
            attrs = types.ModuleType.__getattribute__(module, '__dict__')
            name = attrs.get('__name__')
            if (type(name) is not str or '.' not in name or attrs.get('__file__') is not None
                    or attrs.get('__spec__') is not None or attrs.get('__loader__') is not None):
                continue
            cls, attrs, classes, objects, functions = capture_inherited(module, source_module, require_original=False)
            candidate = (module, cls, attrs, classes, objects, functions)
            deferred_source_aliases[id(module)] = candidate
            tracked_source_aliases[id(module)] = candidate
            source_alias_dependencies[id(module)] = {id(source_globals)}

    def observe_source_aliases(*, allow_additions, source_id):
        for identity, (module, cls, attrs, classes, objects, functions) in tracked_source_aliases.items():
            _check(type(module) is cls)
            current = types.ModuleType.__getattribute__(module, '__dict__')
            _check(set(attrs) <= set(current) and all(current[key] is value for key, value in attrs.items()))
            added = set(current) - set(attrs)
            if added:
                _check(allow_additions and source_id is not None and len(sys._current_frames()) == 1)
                attrs.update((key, current[key]) for key in added)
                source_alias_dependencies[identity].add(source_id)

    def bind_source_aliases(before, after, tainted, parent_name):
        for name, module in after.items():
            if (type(name) is not str or name in before or name in tainted
                    or not name.startswith(parent_name + '.')):
                continue
            candidate = deferred_source_aliases.get(id(module))
            if candidate is None or candidate[0] is not module or candidate[2].get('__name__') != name:
                continue
            _check(name not in inherited_witnessed)
            inherited_witnessed[name] = candidate
            del deferred_source_aliases[id(module)]

    def verify_inherited(name, module):
        expected, cls, attrs, classes, objects, functions = inherited_witnessed[name]
        _check(module is expected and type(module) is cls and sys.modules.get(name) is module)
        dependencies = source_alias_dependencies.get(id(module), set())
        _check(not (dependencies & active_sources) and not (dependencies & failed_sources))
        current = types.ModuleType.__getattribute__(module, '__dict__')
        _check(set(current) == set(attrs) and all(current[key] is value for key, value in attrs.items()))
        for observed_cls, expected_attrs, owner in classes:
            _check(id(owner) not in active_sources and id(owner) not in failed_sources
                   and owner.get(observed_cls.__name__) is observed_cls)
            current_attrs = vars(observed_cls)
            _check(set(current_attrs) == set(expected_attrs)
                   and all(current_attrs[key] is value for key, value in expected_attrs.items()))
        for obj, observed_cls, expected_attrs in objects:
            _check(type(obj) is observed_cls)
            current_attrs = object.__getattribute__(obj, '__dict__')
            _check(set(current_attrs) == set(expected_attrs)
                   and all(current_attrs[key] is value for key, value in expected_attrs.items()))
        for function, code, closure, values, globals_used, source_globals in functions:
            _check(id(source_globals) not in active_sources and id(source_globals) not in failed_sources
                   and function.__code__ is code and function.__globals__ is source_globals
                   and function.__closure__ is closure and function.__defaults__ is None
                   and function.__kwdefaults__ is None)
            _check(all(cell.cell_contents is value for cell, value in zip(closure or (), values)))
            _check(all(source_globals.get(key, absent) is value for key, value in globals_used))

    def capture_created(before, code, source_globals, source_module, loader):
        codes = code_tree(code)
        allowed = {'__name__', '__doc__', '__package__', '__loader__', '__spec__', '__file__',
                   '__getattr__', '__dir__'}
        for name, module in list(sys.modules.items()):
            if before.get(name, absent) is module:
                continue
            if type(module) is not types.ModuleType:
                cls = type(module)
                if (name != loader.name or before.get(name) is not source_module
                        or type(cls) is not type or not issubclass(cls, types.ModuleType)
                        or not any(value is cls for value in source_globals.values())):
                    continue
                original = types.ModuleType.__getattribute__(source_module, '__dict__')
                original_spec = original.get('__spec__')
                _check(original_spec is not None and original_spec.loader is loader
                       and original_spec.name == name and original.get('__loader__') is loader
                       and original.get('__file__') == loader.path)
                instance_attrs = types.ModuleType.__getattribute__(module, '__dict__')
                if instance_attrs.get('m') is source_module:
                    cls, attrs, classes, objects, functions = capture_inherited(module, source_module)
                    _check(attrs.get('__name__') == name and attrs.get('__file__') is None
                           and attrs.get('__spec__') is None and attrs.get('__loader__') is None)
                    inherited_witnessed[name] = (module, cls, attrs, classes, objects, functions)
                    continue
                class_attrs = dict(vars(cls))
                allowed_class_attrs = {'__module__', '__qualname__', '__doc__', '__annotations__',
                                       '__init__', '__getattr__', '__firstlineno__', '__static_attributes__'}
                if (not set(class_attrs) <= allowed_class_attrs or class_attrs.get('__module__') != name
                        or type(class_attrs.get('__init__')) is not types.FunctionType
                        or type(class_attrs.get('__getattr__')) is not types.FunctionType):
                    continue
                class_codes = [item for item in codes if item.co_name == cls.__name__]
                if len(class_codes) != 1:
                    continue
                method_codes = code_tree(class_codes[0])
                states, seen = [], set()
                capture_function(class_attrs['__init__'], source_globals, method_codes, states, seen, cls)
                capture_function(class_attrs['__getattr__'], source_globals, method_codes, states, seen, cls)
                attrs = types.ModuleType.__getattribute__(module, '__dict__')
                _check(attrs.get('__name__') == name and attrs.get('__file__') is None
                       and attrs.get('__spec__') is None and attrs.get('__loader__') is None)
                subclass_witnessed[name] = (module, dict(attrs), cls, class_attrs, states)
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
        nonlocal main_alias_witnessed
        _check(type(loader) is loader_types[1] and module.__name__ == loader.name)
        if tracked_source_aliases:
            observe_source_aliases(allow_additions=bool(source_stack),
                                   source_id=source_stack[-1]['id'] if source_stack else None)
        code = source_code(loader, module.__name__)
        names = {name for nested in code_tree(code) for name in nested.co_names}
        before = dict(sys.modules) if 'modules' in names else None
        previous = verified_sources.get(id(module.__dict__))
        previous_codes = previous[1] if previous is not None and previous[0] is module.__dict__ else ()
        verified_sources[id(module.__dict__)] = (module.__dict__, previous_codes + tuple(code_tree(code)))
        source_before = dict(sys.modules)
        nested_before = source_before if native_stack else None
        watch_objects = 'ModuleType' in names
        parent_watches_objects = any(event['watch'] for event in source_stack)
        nested_objects = (source_modules_in_gc() if parent_watches_objects or watch_objects
                          else modules_in_gc() if native_stack else None)
        source_event = {'names':set(), 'objects':set(), 'watch':watch_objects, 'id':id(module.__dict__)}
        source_tainted = source_event['names']
        if before is not None:
            for name, (existing, _, _) in tuple(witnessed.items()):
                _check(before.get(name) is existing)
                verify_witness(name, existing, check_registry=False)
        source_id = id(module.__dict__)
        _check(source_id not in active_sources)
        source_stack.append(source_event)
        active_sources.add(source_id)
        succeeded = False
        try:
            exec(code, module.__dict__)
            if tracked_source_aliases:
                observe_source_aliases(allow_additions=True, source_id=source_id)
            if before is not None:
                capture_created(before, code, module.__dict__, module, loader)
            if (loader.name == 'multiprocessing'
                    and os.path.normcase(os.path.realpath(loader.path)) == multiprocessing_source):
                _check(source_before.get('__main__') is bootstrap_main
                       and '__mp_main__' not in source_tainted
                       and len(sys._current_frames()) == 1)
                existing_alias = source_before.get('__mp_main__', absent)
                _check(existing_alias is absent or (existing_alias is bootstrap_main
                                                     and initial_main_alias is bootstrap_main))
                check_main_alias()
                if existing_alias is absent:
                    main_alias_witnessed = True
            if native_witnessed._deferred and len(sys._current_frames()) == 1:
                native_witnessed.bind_source(source_before, dict(sys.modules), source_tainted,
                                             single_thread=True)
            if watch_objects and len(sys._current_frames()) == 1:
                capture_source_aliases(module.__dict__, module, nested_objects, source_event['objects'])
            if deferred_source_aliases and len(sys._current_frames()) == 1:
                bind_source_aliases(source_before, dict(sys.modules), source_tainted, loader.name)
            succeeded = True
        finally:
            active_sources.discard(source_id)
            if not succeeded:
                failed_sources.add(source_id)
            _check(source_stack.pop() is source_event)
            mark_source_nested(source_before, nested_objects)
            if nested_before is not None:
                mark_nested(nested_before, nested_objects)

    def bytecode_refused(*args, **kwargs):
        _check(False)

    def native_call(loader, callback, argument):
        file, _ = verified_file(loader.path, source=False)
        try:
            relative = file.relative_to(root).as_posix()
        except ValueError:
            # The interpreter's own extensions retain the old trust boundary,
            # but cannot mint manifest-bound native registration observations.
            # Their nested effects must not be credited to an enclosing
            # manifest-native or verified-source execution window either.
            if not native_stack and not source_stack:
                return callback(loader, argument)
            interpreter_before = dict(sys.modules)
            interpreter_objects = (source_modules_in_gc() if any(event['watch'] for event in source_stack)
                                   else modules_in_gc() if native_stack else None)
            try:
                return callback(loader, argument)
            finally:
                mark_nested(interpreter_before, interpreter_objects)
                mark_source_nested(interpreter_before, interpreter_objects)
        _check(relative in entries and len(sys._current_frames()) == 1)
        before = dict(sys.modules)
        # Keep strong references so an already existing, unregistered module
        # cannot be presented as new merely through a registry assignment.
        known_objects = modules_in_gc()
        known_source_objects = source_modules_in_gc() if any(event['watch'] for event in source_stack) else None
        tainted = {'names': set(), 'objects': set()}
        native_stack.append(tainted)
        succeeded = False
        try:
            value = callback(loader, argument)
            succeeded = True
        finally:
            _check(native_stack.pop() is tainted)
            mark_nested(before, known_objects)
            mark_source_nested(before, known_source_objects)
        if succeeded:
            verified_file(loader.path, source=False)
            native_witnessed.bind(before, dict(sys.modules), tainted['names'],
                                  single_thread=len(sys._current_frames()) == 1, known_objects=known_objects)
            native_witnessed.bind_objects(known_objects, modules_in_gc(), tainted['objects'],
                                          owner=loader.name, single_thread=len(sys._current_frames()) == 1)
        return value

    def native_create(loader, spec):
        _check(type(loader) is loader_types[0] and spec.loader is loader)
        return native_call(loader, original_native_create, spec)

    def native_exec(loader, module):
        _check(type(loader) is loader_types[0])
        return native_call(loader, original_native_exec, module)

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
    namespace_checker = None

    def load_namespace_checker():
        nonlocal namespace_checker
        if namespace_checker is not None:
            return namespace_checker
        helper_path = root / 'assets/code/sandbox/mind/step2/q4_namespace_origin.py'
        verifier_path = root / 'assets/code/sandbox/mind/step2/runtime_manifest.py'
        try:
            _, data = verified_file(str(helper_path), source=True)
            verified_file(str(verifier_path), source=True)
            code = compile(data, '<wd-q4-namespace-origin>', 'exec', dont_inherit=True)
            namespace = {'__name__':'_wd_q4_namespace_origin'}
            exec(code, namespace)
            verifier = sys.modules.get('sandbox.mind.step2.runtime_manifest')
            _check(type(verifier) is types.ModuleType)
            attrs = vars(verifier)
            spec = attrs.get('__spec__')
            _check(type(spec) is machinery.ModuleSpec and type(spec.loader) is machinery.SourceFileLoader
                   and attrs.get('__loader__') is spec.loader and type(attrs.get('__file__')) is str
                   and os.path.normcase(os.path.realpath(attrs['__file__'])) == os.path.normcase(os.path.realpath(verifier_path)))
            record = verified_sources.get(id(attrs))
            _check(record is not None and record[0] is attrs
                   and id(attrs) not in active_sources and id(attrs) not in failed_sources)
            checker = namespace.get('verify_namespace')
            _check(type(checker) is types.FunctionType and checker.__globals__ is namespace
                   and any(checker.__code__ is item for item in code_tree(code)))
            verified_file(str(helper_path), source=True)
            verified_file(str(verifier_path), source=True)
            namespace_checker = checker
            return checker
        except Exception:
            _check(False)

    class ControlledFinder:
        def accept_namespace(self, name, module):
            self.check()
            return load_namespace_checker()(name, module, runtime_root=root, entries=entries, enabled=True)

        def accept_created(self, name, module):
            self.check()
            if name == '__mp_main__':
                _check(main_alias_witnessed and module is bootstrap_main)
                check_main_alias()
            elif name in inherited_witnessed:
                verify_inherited(name, module)
            elif name in subclass_witnessed:
                expected, attrs, cls, class_attrs, states = subclass_witnessed[name]
                _check(module is expected and type(module) is cls and sys.modules.get(name) is module)
                current = types.ModuleType.__getattribute__(module, '__dict__')
                _check(set(current) == set(attrs) and all(current[key] is value for key, value in attrs.items()))
                current_class = vars(cls)
                _check(set(current_class) == set(class_attrs)
                       and all(current_class[key] is value for key, value in class_attrs.items()))
                for function, code, closure, values, globals_used, source_globals in states:
                    _check(function.__code__ is code and function.__globals__ is source_globals
                           and function.__closure__ is closure and function.__defaults__ is None
                           and function.__kwdefaults__ is None)
                    _check(all(cell.cell_contents is value for cell, value in zip(closure or (), values)))
                    _check(all(source_globals.get(key, absent) is value for key, value in globals_used))
            elif name in witnessed:
                verify_witness(name, module)
            else:
                native_witnessed.accept(name, module, sys.modules)

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

