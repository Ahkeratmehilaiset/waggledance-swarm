"""Opt-in isolated public-import bootstrap; caller owns all trust anchors.

The caller must verify the whole CodeRoot package, interpreter and these
expected digests before invoking this module in a fresh ``python -I -S -B``
process. The separately pinned runtime manifest covers runtime/site, model and
runtime/assets/code; it does NOT establish that runtime/assets/code is equal
to CodeRoot. This helper authenticates neither the caller nor source/pins,
and does not close same-user TOCTOU, native DLL search or replay. It does not
run a query, activate a service, import a model, or claim production readiness.
"""
import ctypes
import hashlib
import hmac
import importlib
import importlib.machinery
import importlib._bootstrap_external
import os
from pathlib import Path
import re
import stat
import sys
import types
import zipimport

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_LIMIT = 2 * 1024 * 1024
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_PUBLIC = ("tools.wd_q4_public_child", "tools.wd_q4_public_query")


class PublicOuterRefused(ValueError):
    """Fixed refusal; never contains a path, caller input, or exception text."""


def _reject_cached_bytecode(root):
    """Refuse cached bytecode/reparse trees, without authenticating the tree."""
    pending = [root]
    count = 0
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                count += 1
                if count > 100000 or entry.name.casefold() == "__pycache__" or entry.name.casefold().endswith(".pyc"):
                    raise ValueError()
                # On Windows DirEntry.stat() may return st_nlink == 0;
                # lstat supplies the hard-link count used by runtime_manifest.
                info = os.lstat(entry.path)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
                    raise ValueError()
                if stat.S_ISDIR(info.st_mode):
                    pending.append(entry.path)
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError()


def _check_import_state(root, tools_path, site=None):
    """Reject protected-path cache injections, not arbitrary preloads or same-user races."""
    details = importlib._bootstrap_external._get_supported_file_loaders()
    expected_hook = importlib.machinery.FileFinder.path_hook(*details)
    if len(sys.path_hooks) != 2 or sys.path_hooks[0] is not zipimport.zipimporter:
        raise ValueError()
    hook = sys.path_hooks[1]
    if (type(hook) is not types.FunctionType or hook.__code__ is not expected_hook.__code__
            or hook.__globals__ is not expected_hook.__globals__ or hook.__defaults__ is not None
            or hook.__kwdefaults__ is not None or hook.__closure__ is None
            or len(hook.__closure__) != 2 or hook.__closure__[0].cell_contents is not importlib.machinery.FileFinder):
        raise ValueError()
    hook_details = hook.__closure__[1].cell_contents
    if type(hook_details) is not tuple or len(hook_details) != len(details):
        raise ValueError()
    for actual, (loader, suffixes) in zip(hook_details, details):
        if (type(actual) is not tuple or len(actual) != 2 or actual[0] is not loader
                or type(actual[1]) is not list or len(actual[1]) != len(suffixes)
                or any(type(x) is not str or x != y for x, y in zip(actual[1], suffixes))):
            raise ValueError()
    expected_loaders = [(suffix, loader) for loader, suffixes in details for suffix in suffixes]
    for required in (str(root), tools_path):
        if required not in sys.path_importer_cache:
            raise ValueError()
    root_key = os.path.normcase(os.path.abspath(root))
    site_key = os.path.normcase(os.path.abspath(site)) if site is not None else None
    def contained(parent, child):
        try:
            return os.path.commonpath((parent, child)) == parent
        except ValueError:
            return False  # different Windows volumes
    for path, finder in tuple(sys.path_importer_cache.items()):
        if type(path) is not str:
            raise ValueError()
        if path.startswith(("\\\\?\\", "\\\\.\\")):
            raise ValueError()
        normalized = os.path.normcase(os.path.abspath(path))
        under_root = contained(root_key, normalized)
        under_site = site_key is not None and contained(site_key, normalized)
        if under_site:
            raise ValueError()
        if not under_root:
            continue
        # Reject relative and dot-segment aliases and reparse paths rather than
        # assuming that a lexical match authenticates the actual directory.
        if not os.path.isabs(path) or os.path.normpath(path) != path:
            raise ValueError()
        if not _local(path).is_dir():
            raise ValueError()
        if type(finder) is not importlib.machinery.FileFinder:
            raise ValueError()
        state = vars(finder)
        if set(state) != {"_loaders", "path", "_path_mtime", "_path_cache", "_relaxed_path_cache"}:
            raise ValueError()
        loaders = state["_loaders"]
        if (type(state["path"]) is not str or state["path"] != path or type(loaders) is not list
                or len(loaders) != len(expected_loaders)):
            raise ValueError()
        for item, (suffix, loader) in zip(loaders, expected_loaders):
            if (type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
                    or item[0] != suffix or item[1] is not loader):
                raise ValueError()


def _local(path):
    path = Path(path)
    if (os.name != "nt" or not path.is_absolute() or str(path).startswith(("\\\\", "//"))
            or ".." in path.parts or any(":" in part or part.endswith((" ", ".")) for part in path.parts[1:])):
        raise ValueError()
    drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    drive_type.argtypes = (ctypes.c_wchar_p,)
    drive_type.restype = ctypes.c_uint
    if drive_type(path.anchor) != 3:
        raise ValueError()
    for current in (path, *path.parents):
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise ValueError()
    return path


def _pinned(path, expected):
    if type(expected) is not str or _HASH.fullmatch(expected) is None:
        raise ValueError()
    path = _local(path)
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > _LIMIT:
        raise ValueError()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(_LIMIT + 1)
        after = os.fstat(stream.fileno())
    final = os.lstat(path)
    identity = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_nlink)
    if (len(raw) > _LIMIT or identity(before) != identity(opened) or identity(opened) != identity(after)
            or identity(after) != identity(final) or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected)):
        raise ValueError()
    return raw


def bootstrap_public_imports(*, enabled=False, code_root=None, verifier_path=None, verifier_sha256=None,
                             public_child_sha256=None, public_query_sha256=None, runtime_root=None,
                             runtime_manifest=None, runtime_manifest_sha256=None, runtime_generation=None,
                             runtime_revision=None):
    """Verify then import the public modules; no query or authentication occurs.

    CodeRoot package integrity is independently established by the caller. On
    verification refusal the process search path is left unchanged. On import
    refusal any site entry added here is removed, but this is not a general
    rollback of module side effects: discard that child process on any refusal.
    """
    inserted = None
    installed = []
    try:
        if type(enabled) is not bool:
            raise ValueError()
        if not enabled:
            return {"status": "OFF"}
        if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
                and sys.dont_write_bytecode and sys.pycache_prefix is None):
            raise ValueError()
        expected_finders = (importlib.machinery.BuiltinImporter, importlib.machinery.FrozenImporter,
                            importlib.machinery.PathFinder)
        if (len(sys.meta_path) != len(expected_finders)
                or any(actual is not expected for actual, expected in zip(sys.meta_path, expected_finders))):
            raise ValueError()
        root = _local(code_root)
        verifier = _local(verifier_path)
        if (not root.is_dir() or verifier != root / "sandbox/mind/step2/runtime_manifest.py"
                or any(name.startswith("tools.") and name != __name__ for name in sys.modules)
                or any(name == "sandbox" or name.startswith("sandbox.") for name in sys.modules)
                or any(name == "core" or name.startswith("core.") for name in sys.modules)
                or any(name == "waggledance" or name.startswith("waggledance.") for name in sys.modules)
                or any(name == "numpy" or name.startswith("numpy.") or name == "faiss" or name.startswith("faiss.")
                       for name in sys.modules)):
            raise ValueError()
        tools = sys.modules.get("tools")
        tools_path = getattr(tools, "__path__", None)
        tools_spec = getattr(tools, "__spec__", None)
        if (tools is None or not isinstance(getattr(tools, "__file__", None), str)
                or os.path.normcase(os.path.abspath(tools.__file__)) !=
                   os.path.normcase(os.path.abspath(root / "tools/__init__.py"))
                or type(tools_path) is not list or len(tools_path) != 1 or type(tools_path[0]) is not str
                or os.path.normcase(os.path.abspath(tools_path[0])) != os.path.normcase(os.path.abspath(root / "tools"))
                or getattr(tools_spec, "submodule_search_locations", None) is not tools_path):
            raise ValueError()
        if (not sys.path or type(sys.path[0]) is not str
                or os.path.normcase(os.path.abspath(sys.path[0])) != os.path.normcase(os.path.abspath(root))):
            raise ValueError()
        _check_import_state(root, tools_path[0])
        _reject_cached_bytecode(root)
        # Isolated Python's remaining default entries must stay under its own
        # base prefix. This is not a digest check of Python's stdlib or DLLs.
        base = os.path.normcase(os.path.abspath(sys.base_prefix))
        for item in sys.path[1:]:
            if type(item) is not str or not item or not os.path.isabs(item):
                raise ValueError()
            if os.path.commonpath((base, os.path.normcase(os.path.abspath(item)))) != base:
                raise ValueError()
        child_path, query_path = (root / "tools/wd_q4_public_child.py", root / "tools/wd_q4_public_query.py")
        verifier_bytes = _pinned(verifier, verifier_sha256)
        child_bytes = _pinned(child_path, public_child_sha256)
        query_bytes = _pinned(query_path, public_query_sha256)
        # Execute the exact pinned bytes, never a second path read. The caller
        # is responsible for authenticating the verifier digest itself.
        namespace = {"__name__": "_wd_public_outer_verifier", "__file__": str(verifier)}
        exec(compile(verifier_bytes, str(verifier), "exec"), namespace)
        namespace["verify_runtime"](runtime_root, runtime_manifest, runtime_manifest_sha256,
                                    expected_generation=runtime_generation, expected_revision=runtime_revision)
        site = _local(Path(runtime_root) / "site")
        if not site.is_dir() or any(os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(site))
                                    for p in sys.path if isinstance(p, str) and p):
            raise ValueError()
        _check_import_state(root, tools_path[0], site)
        _reject_cached_bytecode(root)
        if (_pinned(verifier, verifier_sha256) != verifier_bytes or _pinned(child_path, public_child_sha256) != child_bytes
                or _pinned(query_path, public_query_sha256) != query_bytes):
            raise ValueError()
        # No site.addsitedir: .pth files must not be executed. Runtime site is
        # after the interpreter's existing stdlib and CodeRoot search entries.
        inserted = str(site)
        sys.path.append(inserted)
        # Compile and execute the exact caller-pinned snapshots. PathFinder
        # may otherwise prefer an unpinned package, extension or bytecode.
        def load_snapshot(name, path, raw):
            short = name.rsplit(".", 1)[1]
            if name in sys.modules or hasattr(tools, short):
                raise ValueError()
            module = types.ModuleType(name)
            module.__file__ = str(path)
            module.__package__ = "tools"
            module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, origin=str(path))
            sys.modules[name] = module
            installed.append((name, short, module))
            exec(compile(raw, str(path), "exec"), module.__dict__)
            setattr(tools, short, module)
            return module
        child = load_snapshot(_PUBLIC[0], child_path, child_bytes)
        query = load_snapshot(_PUBLIC[1], query_path, query_bytes)
        if not callable(getattr(child, "run_public", None)) or not callable(getattr(query, "query_public", None)):
            raise ValueError()
        return {"status": "MODULES_IMPORTED", "child": child, "query": query, "authority": "none",
                "caller_authentication": "UNKNOWN", "source_authentication": "UNKNOWN",
                "code_root_closure": "NOT_VERIFIED",
                "runtime_to_code_compatibility": "UNKNOWN", "same_user_toctou": "UNKNOWN"}
    except Exception:
        for name, short, module in reversed(installed):
            if sys.modules.get(name) is module:
                del sys.modules[name]
            if getattr(sys.modules.get("tools"), short, None) is module:
                delattr(sys.modules["tools"], short)
        if inserted is not None:
            try:
                sys.path.remove(inserted)
            except ValueError:
                pass
        raise PublicOuterRefused("public_outer_refused") from None
