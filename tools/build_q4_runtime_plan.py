"""Candidate input PLAN for the existing Q4 runtime candidate builder.

This default-off module is not an installer, builder, activation, trust anchor
or running Mind, and its output is an unverified candidate, never a proof of
full runtime closure (dynamic imports and native DLL closure are not proven).
It only reads: committed Git blobs of one exact commit, and two existing local
trees (``site`` and ``model``). It returns the ``pins`` map that
``tools.build_q4_runtime_candidate.build_candidate_from_sources`` consumes,
keyed ``assets/code/...``, ``site/...`` and ``model/...``, with exact size and
SHA256 values. The code set is the static import closure of caller-named roots
plus caller-listed extra paths. Imports made through dynamic loaders
(importlib, exec, runpy, file specs) cannot be followed statically; the files
using them are reported and the caller must list what they load. The pins are
the committed blob bytes, so the builder's assets source must hold those exact
bytes (an autocrlf checkout will not match).

The git executable is caller-trusted, not hash-pinned here; the GIT_*
environment, global/system config and replace objects are excluded and blob
ids are recomputed from the bytes read. Site and model trees reuse the
runtime verifier's own walk and record policy: reparse points, alternate data
streams, hard-link aliases, short-name or otherwise invalid segments, case
collisions and its file/byte caps refuse. The model directory must match the
caller's exact file list and expected weights digest. The two roots must be
disjoint by string AND by the file identity (volume, file id) of every
ancestor, so a short-name, SUBST or other alias of an overlapping directory
refuses; an identity the OS cannot report refuses too. Site provenance and
dependency closure are NOT verified; same-user replacement after the plan
remains possible. Refusals carry only a fixed token.
"""

import ast
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys

from sandbox.mind.step2 import runtime_builder, runtime_manifest

SCHEMA = "wd.q4-runtime-input-plan.v1"
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ALIAS = re.compile(r"~[0-9]+(?:\.[^.]*)?\Z")
_LOCAL = frozenset({"tools", "sandbox", "core", "waggledance"})
_DYNAMIC = frozenset({"import_module", "__import__", "exec", "run_path", "run_module", "spec_from_file_location"})
_MAX_LISTING = 64 * 1024 * 1024
_TRUST = {"site_provenance": "UNKNOWN", "dependency_closure": "NOT_VERIFIED",
          "dynamic_imports": "CALLER_LISTED", "git_executable": "CALLER_TRUSTED",
          "code_closure": "STATIC_CANDIDATE", "native_dll_closure": "NOT_VERIFIED"}


class RuntimePlanRefused(ValueError):
    """A fixed refusal token; no path, file content or input text."""


def _fail(token):
    raise RuntimePlanRefused(token)


def _git(git_executable, repository, args, timeout):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"})
    try:
        result = subprocess.run([git_executable, "--no-replace-objects", "-C", repository, *args], env=env,
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, shell=False)
    except (OSError, subprocess.SubprocessError):
        _fail("git_unavailable")
    if result.returncode != 0 or len(result.stdout) > _MAX_LISTING:
        return None
    return result.stdout


def _listing(git_executable, repository, commit, timeout):
    resolved = _git(git_executable, repository, ["rev-parse", "--verify", "--end-of-options", commit + "^{commit}"],
                    timeout)
    if resolved is None or resolved.decode("ascii", "replace").strip() != commit:
        _fail("commit_invalid")
    raw = _git(git_executable, repository, ["ls-tree", "-r", "-z", "--full-tree", commit], timeout)
    if raw is None:
        _fail("commit_invalid")
    entries = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, kind, oid = meta.decode("ascii").split(" ")
        entries[name.decode("utf-8", "strict")] = (mode, kind, oid)
    return entries


def _blob(git_executable, repository, oid, timeout):
    data = _git(git_executable, repository, ["cat-file", "blob", oid], timeout)
    if data is None or len(data) > runtime_manifest.MAX_FILE_BYTES:
        _fail("code_entry_invalid")
    header = b"blob %d\0" % len(data)
    recomputed = (hashlib.sha1(header + data) if len(oid) == 40 else hashlib.sha256(header + data)).hexdigest()
    if recomputed != oid:
        _fail("code_entry_invalid")
    return data


def _code_path(value):
    if (type(value) is not str or not value or "\\" in value or value.startswith("/")
            or any(part in ("", ".", "..") for part in value.split("/"))):
        _fail("code_roots_invalid")
    return value


class _Closure:
    def __init__(self, entries):
        self.entries = entries
        self.directories = {name.rsplit("/", 1)[0] for name in entries if "/" in name}
        self.directories |= {d.rsplit("/", i)[0] for d in list(self.directories) for i in range(1, d.count("/") + 1)}

    def module(self, dotted):
        base = dotted.replace(".", "/")
        for candidate in (base + ".py", base + "/__init__.py"):
            if candidate in self.entries:
                return candidate
        return None

    def namespace(self, dotted):
        return dotted.replace(".", "/") in self.directories


def _imports(tree, path, closure):
    """Return (local module files, third-party top names, uses dynamic loading)."""
    package = path.rsplit("/", 1)[0].replace("/", ".") if "/" in path else ""
    if path.endswith("/__init__.py"):
        package = path[:-len("/__init__.py")].replace("/", ".")
    found, third, dynamic = set(), set(), False

    def need(dotted):
        parts = dotted.split(".")
        for count in range(1, len(parts) + 1):
            hit = closure.module(".".join(parts[:count]))
            if hit:
                found.add(hit)
        if closure.module(dotted) is None and not closure.namespace(dotted):
            _fail("code_closure_incomplete")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _LOCAL:
                    need(alias.name)
                elif top not in sys.stdlib_module_names:
                    third.add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                if node.level - 1 > len(parts):
                    _fail("code_closure_incomplete")
                base = ".".join(parts[:len(parts) - node.level + 1])
                module = ".".join(item for item in (base, node.module) if item)
            else:
                module = node.module or ""
            top = module.split(".")[0]
            if top not in _LOCAL:
                if top and top not in sys.stdlib_module_names:
                    third.add(top)
                continue
            need(module)
            for alias in node.names:
                child = module + "." + alias.name
                hit = closure.module(child)
                if hit:
                    need(child)
                elif closure.module(module) is None and not closure.namespace(child):
                    _fail("code_closure_incomplete")  # a namespace package has no attributes to import
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            dynamic = dynamic or name in _DYNAMIC
    return found, third, dynamic


def _code_plan(git_executable, repository, commit, roots, extras, timeout):
    if (type(roots) not in (list, tuple) or not roots or len(set(roots)) != len(roots)
            or type(extras) not in (list, tuple) or len(set(extras)) != len(extras)):
        _fail("code_roots_invalid")
    roots = [_code_path(item) for item in roots]
    extras = [_code_path(item) for item in extras]
    if any(not item.endswith(".py") for item in roots):
        _fail("code_roots_invalid")
    entries = _listing(git_executable, repository, commit, timeout)
    if any(item not in entries for item in roots + extras):
        _fail("code_roots_invalid")
    closure = _Closure(entries)
    data, third, dynamic = {}, set(), set()
    pending = list(roots) + list(extras)
    while pending:
        path = pending.pop()
        if path in data:
            continue
        mode, kind, oid = entries[path]
        if kind != "blob" or mode not in ("100644", "100755"):
            _fail("code_entry_invalid")
        data[path] = _blob(git_executable, repository, oid, timeout)
        if path.endswith(".py"):
            try:
                tree = ast.parse(data[path].decode("utf-8", "strict"), filename=path)
            except (SyntaxError, UnicodeError, ValueError):
                _fail("code_closure_incomplete")
            found, names, uses = _imports(tree, path, closure)
            third |= names
            if uses:
                dynamic.add(path)
            pending.extend(sorted(found - set(data)))
    return data, sorted(third), sorted(dynamic)


def _root(value):
    if type(value) is not str or not value:
        _fail("roots_invalid")
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or str(path).startswith(("\\\\", "//"))
            or any(_ALIAS.search(part) for part in path.parts[1:])):
        _fail("roots_invalid")
    try:
        checked = runtime_manifest._check_chain(path)
    except ValueError:
        _fail("roots_invalid")
    if not checked.is_dir() or checked == Path(checked.anchor):
        _fail("roots_invalid")
    return checked


def _related(left, right):
    try:
        common = os.path.normcase(os.path.commonpath((str(left), str(right))))
    except ValueError:
        return False
    return common in (os.path.normcase(str(left)), os.path.normcase(str(right)))


def _identity(path):
    info = os.stat(path)
    if not info.st_ino or not info.st_dev:
        _fail("roots_invalid")  # without a file id, an alias cannot be excluded
    return info.st_dev, info.st_ino


def _physically_related(left, right):
    """True when either root is the other or one of its ancestors by file identity."""
    for inner, outer in ((left, right), (right, left)):
        target = _identity(outer)
        if any(_identity(item) == target for item in (inner, *inner.parents)):
            return True
    return False


def _tree_pins(root):
    files, _ = runtime_manifest._tree(root)
    pins = {}
    for relative in sorted(files):
        size, digest, _ = runtime_builder._hash_file(root.joinpath(*relative.split("/")))
        pins[relative] = {"size": size, "sha256": digest}
    return pins, files


def plan_runtime_inputs(*, git_executable, repository, commit, code_roots, site_root, model_root,
                        expected_model_files, expected_weights_sha256, generation, revision, max_bytes,
                        extra_code_paths=(), expected_site_pins=None, timeout_seconds=60, enabled=False):
    """Return the exact candidate-builder pins and identity, or refuse with a fixed token."""
    if enabled is False:
        return {"status": "OFF"}
    token = None
    try:
        if (enabled is not True or type(git_executable) is not str or not os.path.isabs(git_executable)
                or type(repository) is not str or not os.path.isabs(repository)
                or type(commit) is not str or type(generation) is not str
                or not runtime_manifest.IDENTITY.fullmatch(generation)
                or type(revision) is not str or not runtime_manifest.IDENTITY.fullmatch(revision)
                or type(max_bytes) is not int or max_bytes <= 0
                or type(expected_weights_sha256) is not str or not _SHA256.fullmatch(expected_weights_sha256)
                or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600
                or type(expected_model_files) not in (list, tuple, set, frozenset)
                or any(type(item) is not str for item in expected_model_files)
                or (expected_site_pins is not None and type(expected_site_pins) is not dict)):
            _fail("inputs_invalid")
        if not _COMMIT.fullmatch(commit):
            _fail("commit_invalid")
        site, model = _root(site_root), _root(model_root)
        if _related(site, model) or _physically_related(site, model):
            _fail("roots_invalid")
        code, third, dynamic = _code_plan(git_executable, repository, commit, code_roots, extra_code_paths,
                                          timeout_seconds)
        site_pins, site_files = _tree_pins(site)
        model_pins, model_files = _tree_pins(model)
        wanted = set(expected_model_files)
        if "model.safetensors" not in wanted or set(model_pins) != wanted or len(wanted) != len(expected_model_files):
            _fail("model_membership_mismatch")
        if model_pins["model.safetensors"]["sha256"] != expected_weights_sha256:
            _fail("model_weights_mismatch")
        if expected_site_pins is not None and expected_site_pins != site_pins:
            _fail("site_pin_mismatch")
        # The trees must still have exactly the hashed membership after every read.
        if runtime_manifest._tree(site)[0] != site_files or runtime_manifest._tree(model)[0] != model_files:
            _fail("tree_changed")
        pins = {"assets/code/" + path: {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                for path, raw in sorted(code.items())}
        pins.update({"site/" + path: value for path, value in site_pins.items()})
        pins.update({"model/" + path: value for path, value in model_pins.items()})
        files, _ = runtime_manifest._validate_records([{"path": key, **value} for key, value in pins.items()])
        total = sum(size for size, _ in files.values())
        if total > max_bytes:
            _fail("budget_insufficient")
        return {"schema": SCHEMA, "status": "CANDIDATE_UNVERIFIED", "commit": commit, "generation": generation,
                "revision": revision, "code_paths": sorted(code), "dynamic_loaders": dynamic,
                "third_party": third, "pins": pins, "file_count": len(pins), "total_bytes": total,
                "weights_sha256": expected_weights_sha256, "trust": dict(_TRUST)}
    except RuntimePlanRefused as exc:
        token = exc.args[0]
    except ValueError as exc:
        reason = exc.args[0] if exc.args else ""
        token = reason if type(reason) is str and re.fullmatch(r"[a-z_]{1,64}", reason) else "runtime_plan_refused"
    except (OSError, UnicodeError, RecursionError, MemoryError):
        token = "runtime_plan_refused"
    raise RuntimePlanRefused(token)
