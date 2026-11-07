"""Candidate input PLAN for the existing Q4 runtime candidate builder.

This default-off module is not an installer, builder, activation, trust anchor
or running Mind, and its output is an unverified candidate, never a proof of
full runtime closure (dynamic imports and native DLL closure are not proven).
It only reads: committed Git blobs of one exact commit, and two existing local
trees (``site`` and ``model``). It returns the ``pins`` map that
``tools.build_q4_runtime_candidate.build_candidate_from_sources`` consumes,
keyed ``assets/code/...``, ``site/...`` and ``model/...``, with exact size and
SHA256 values. The code set is the static import closure of caller-named roots
plus caller-listed extra paths, labelled unverified. When that closure contains
the Q4 secure transport or public snapshot reader, it also requires their
known path-loaded import helper, namespace verifier and native-addition verifier.
Imports made through
dynamic loaders (importlib, exec, runpy, file specs) cannot be followed
statically. A conservative AST heuristic flags files that import importlib,
runpy or builtins under any alias, or name a known loader, eval, exec,
compile or __builtins__, also through assignment aliases or getattr-style
string names; the caller must list what they load. This heuristic
cannot detect all dynamic execution, so no result claims a complete dynamic
or native closure. Methods merely named eval, exec or compile on other
objects are not flagged. The pins are the committed blob bytes, so the
builder's assets source must hold those exact bytes (an autocrlf checkout
will not match).

The git executable is caller-trusted, not hash-pinned here; the GIT_*
environment, global/system config and replace objects are excluded and blob
ids are recomputed from the bytes read. Git output is capped while it is read
(stdout per call, stderr), an excess or the timeout ends the process and
refuses, and each blob's listed size is checked before its bytes are read.
timeout_seconds bounds the total git wall time of one plan (plus a short
reader join grace). max_bytes bounds the input bytes read for pins: each
blob's listed size is checked against the remaining budget before cat-file
and its stdout is capped at that size; each site and model file is checked
before it is opened, then opened with the remaining budget as its size limit
(the opened identity, size included, must equal the checked one) and read
unbuffered for at most its opened size plus a one-byte end probe; git pipe
reads may fetch up to one 64 KiB chunk past a cap before refusing. It bounds input,
not process peak memory: one blob, the AST of one parsed file, the ls-tree
listing (up to 64 MiB) and git stderr (up to 64 KiB) are held besides. Root and extra modules also pin
their parent package __init__ files. Lazy fetching is disabled
(GIT_NO_LAZY_FETCH, git 2.45 or newer) and the git, file, ssh, http, https
and ext transports are denied on the command line, but the repository's own
local config is still read, so network-free behaviour is NOT claimed for
arbitrary local config or remote helpers. Only the git process itself is
ended on excess or timeout; processes it starts are not tracked, so no
process-tree cleanup is claimed. Site and model trees reuse the
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
import threading
import time

from sandbox.mind.step2 import runtime_manifest

SCHEMA = "wd.q4-runtime-input-plan.v1"
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ALIAS = re.compile(r"~[0-9]+(?:\.[^.]*)?\Z")
_LOCAL = frozenset({"tools", "sandbox", "core", "waggledance"})
_SECURE_TRANSPORT = "tools/wd_q4_secure_transport.py"
_PUBLIC_SNAPSHOT_READER = "sandbox/mind/step2/shared_index_checkpoint.py"
_CONTROLLED_IMPORTS = "sandbox/mind/step2/q4_controlled_imports.py"
_NAMESPACE_ORIGIN = "sandbox/mind/step2/q4_namespace_origin.py"
_NATIVE_ADDITIONS = "sandbox/mind/step2/q4_native_additions.py"
_DYNAMIC_MODULES = frozenset({"importlib", "runpy", "builtins"})
_DYNAMIC_NAMES = frozenset({"__import__", "exec", "eval", "compile", "__builtins__"})
_DYNAMIC_ATTRS = frozenset({"import_module", "__import__", "run_path", "run_module", "spec_from_file_location",
                            "spec_from_loader", "module_from_spec", "exec_module", "load_module", "__builtins__"})
_DYNAMIC_STRINGS = _DYNAMIC_MODULES | _DYNAMIC_NAMES | _DYNAMIC_ATTRS
_MAX_SMALL = 4096
_MAX_LISTING = 64 * 1024 * 1024
_MAX_STDERR = 64 * 1024
_CHUNK = 65536
_JOIN_GRACE = 1.0
_GIT_CONFIG = tuple(item for name in ("protocol.allow", "protocol.git.allow", "protocol.file.allow",
                                      "protocol.ssh.allow", "protocol.http.allow", "protocol.https.allow",
                                      "protocol.ext.allow")
                    for item in ("-c", name + "=never")) + ("-c", "core.fsmonitor=false")
_TRUST = {"site_provenance": "UNKNOWN", "dependency_closure": "NOT_VERIFIED",
          "dynamic_imports": "HEURISTIC_FLAGS_CALLER_LISTED", "git_executable": "CALLER_TRUSTED",
          "code_closure": "STATIC_UNVERIFIED", "native_dll_closure": "NOT_VERIFIED"}
# Fixed runtime_manifest refusal vocabulary; never echo arbitrary exception text.
_MANIFEST_REFUSALS = frozenset({
    "case_collision", "digest_invalid", "duplicate_json_key", "duplicate_path", "expected_identity_invalid",
    "file_changed", "file_digest_mismatch", "file_directory_collision", "file_invalid", "file_read_unavailable",
    "file_size_mismatch", "file_unavailable", "files_invalid", "json_constant_invalid", "manifest_changed",
    "manifest_identity_mismatch", "manifest_inside_tree", "manifest_invalid", "manifest_pin_invalid",
    "manifest_pin_mismatch", "manifest_read_unavailable", "named_stream", "path_invalid", "path_reparse",
    "path_unavailable", "record_invalid", "root_invalid", "runtime_over_budget", "size_invalid",
    "stream_unavailable", "tree_changed", "tree_mismatch", "tree_nonregular", "tree_over_budget", "tree_reparse",
    "tree_unavailable",
})


class RuntimePlanRefused(ValueError):
    """A fixed refusal token; no path, file content or input text."""


def _fail(token):
    raise RuntimePlanRefused(token)


def _drain(stream, limit, sink, flags, process):
    """Read one pipe in chunks; past ``limit`` bytes, flag it and end the process instead of reading on."""
    total = 0
    try:
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                return
            total += len(chunk)
            if total > limit:
                flags.add("too_large")
                break
            sink.extend(chunk)
    except (OSError, ValueError):
        flags.add("error")
    try:
        process.kill()
    except OSError:
        pass


def _git(git_executable, repository, args, deadline, limit):
    """Return git stdout (at most ``limit`` bytes) or None on a non-zero exit; excess or the deadline refuses."""
    if deadline - time.monotonic() <= 0:
        _fail("git_timeout")
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
                "GIT_NO_LAZY_FETCH": "1"})
    try:
        process = subprocess.Popen([git_executable, "--no-replace-objects", *_GIT_CONFIG, "-C", repository, *args],
                                   env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, bufsize=0, shell=False)
    except (OSError, ValueError, subprocess.SubprocessError):
        _fail("git_unavailable")
    out, flags = bytearray(), set()
    readers = [threading.Thread(target=_drain, args=(process.stdout, limit, out, flags, process), daemon=True),
               threading.Thread(target=_drain, args=(process.stderr, _MAX_STDERR, bytearray(), flags, process),
                                daemon=True)]
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=max(deadline - time.monotonic(), 0.001))
    except subprocess.TimeoutExpired:
        flags.add("timeout")
        process.kill()
        process.wait()
    for reader in readers:
        reader.join(max(deadline - time.monotonic(), _JOIN_GRACE))
    if "timeout" in flags or any(reader.is_alive() for reader in readers):
        _fail("git_timeout")
    process.stdout.close()
    process.stderr.close()
    if "too_large" in flags:
        _fail("git_output_too_large")
    if "error" in flags:
        _fail("git_unavailable")
    if process.returncode != 0:
        return None
    return out


def _listing(git_executable, repository, commit, deadline):
    resolved = _git(git_executable, repository, ["rev-parse", "--verify", "--end-of-options", commit + "^{commit}"],
                    deadline, _MAX_SMALL)
    if resolved is None or resolved.decode("ascii", "replace").strip() != commit:
        _fail("commit_invalid")
    raw = _git(git_executable, repository, ["ls-tree", "-r", "-z", "-l", "--full-tree", commit], deadline,
               _MAX_LISTING)
    if raw is None:
        _fail("commit_invalid")
    entries = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, kind, oid, size = meta.decode("ascii").split()
        entries[name.decode("utf-8", "strict")] = (mode, kind, oid, int(size) if size.isdigit() else None)
    return entries


def _blob(git_executable, repository, oid, size, deadline):
    if size is None or size > runtime_manifest.MAX_FILE_BYTES:
        _fail("code_entry_invalid")  # refused from the listed size, before any byte is read
    data = _git(git_executable, repository, ["cat-file", "blob", oid], deadline, size)
    if data is None or len(data) != size:
        _fail("code_entry_invalid")
    recomputed = hashlib.sha1() if len(oid) == 40 else hashlib.sha256()
    recomputed.update(b"blob %d\0" % len(data))
    recomputed.update(data)  # no header-plus-data copy
    if recomputed.hexdigest() != oid:
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
        if isinstance(node, ast.Name):
            dynamic = dynamic or node.id in _DYNAMIC_NAMES
        elif isinstance(node, ast.Attribute):
            dynamic = dynamic or node.attr in _DYNAMIC_ATTRS
        elif isinstance(node, ast.Constant):
            dynamic = dynamic or (type(node.value) is str and node.value in _DYNAMIC_STRINGS)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                dynamic = dynamic or top in _DYNAMIC_MODULES
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
            dynamic = (dynamic or (not node.level and top in _DYNAMIC_MODULES)
                       or any(alias.name in _DYNAMIC_STRINGS for alias in node.names))
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
    return found, third, dynamic


def _parents(path, entries):
    """Package __init__ files that importing ``path`` as a module also executes."""
    parts = path.split("/")[:-1]
    candidates = ("/".join(parts[:count]) + "/__init__.py" for count in range(1, len(parts) + 1))
    return {item for item in candidates if item in entries}


def _code_plan(git_executable, repository, commit, roots, extras, deadline, budget):
    if (type(roots) not in (list, tuple) or not roots or len(set(roots)) != len(roots)
            or type(extras) not in (list, tuple) or len(set(extras)) != len(extras)):
        _fail("code_roots_invalid")
    roots = [_code_path(item) for item in roots]
    extras = [_code_path(item) for item in extras]
    if any(not item.endswith(".py") for item in roots):
        _fail("code_roots_invalid")
    entries = _listing(git_executable, repository, commit, deadline)
    if any(item not in entries for item in roots + extras):
        _fail("code_roots_invalid")
    closure = _Closure(entries)
    records, third, dynamic, used = {}, set(), set(), 0
    pending = list(roots) + list(extras)
    while pending:
        path = pending.pop()
        if path in records:
            continue
        if path in (_SECURE_TRANSPORT, _PUBLIC_SNAPSHOT_READER):
            if _CONTROLLED_IMPORTS not in entries:
                _fail("code_closure_incomplete")
            pending.append(_CONTROLLED_IMPORTS)
        if path == _CONTROLLED_IMPORTS:
            if _NAMESPACE_ORIGIN not in entries or _NATIVE_ADDITIONS not in entries:
                _fail("code_closure_incomplete")
            pending.extend((_NAMESPACE_ORIGIN, _NATIVE_ADDITIONS))
        mode, kind, oid, size = entries[path]
        if kind != "blob" or mode not in ("100644", "100755"):
            _fail("code_entry_invalid")
        if size is not None and used + size > budget:
            _fail("budget_insufficient")  # checked from the listed size, before the blob is read
        raw = _blob(git_executable, repository, oid, size, deadline)
        used += len(raw)
        records[path] = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        if path.endswith(".py"):
            try:
                tree = ast.parse(raw.decode("utf-8", "strict"), filename=path)
            except (SyntaxError, UnicodeError, ValueError):
                _fail("code_closure_incomplete")
            found, names, uses = _imports(tree, path, closure)
            third |= names
            if uses:
                dynamic.add(path)
            pending.extend(sorted((found | _parents(path, entries)) - set(records)))
        del raw  # only sizes and digests are kept, so at most one blob is in memory
    return records, sorted(third), sorted(dynamic), used


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


def _hash_bounded(path, limit):
    """runtime_builder._hash_file with every read bound to ``limit`` bytes of the file actually opened."""
    runtime_manifest._check_chain(path)
    runtime_manifest._check_no_named_streams(path)
    # _open_regular refuses an lstat size above the limit and an opened identity (size included) that differs.
    stream, initial = runtime_manifest._open_regular(path, min(limit, runtime_manifest.MAX_FILE_BYTES))
    try:
        with stream:
            if initial.st_size > limit:
                _fail("budget_insufficient")
            raw = stream.raw  # unbuffered: the buffered reader would prefetch past the opened size
            digest = hashlib.sha256()
            remaining = initial.st_size
            while remaining:
                block = raw.read(min(1024 * 1024, remaining))
                if not block:
                    _fail("file_changed")
                digest.update(block)
                remaining -= len(block)
            if raw.read(1) or not runtime_manifest._same_file(path, stream, initial):
                _fail("file_changed")
    except OSError:
        _fail("file_read_unavailable")
    return initial.st_size, digest.hexdigest()


def _tree_pins(root, budget, used, expected_members=None, mismatch_token="tree_changed"):
    files, _ = runtime_manifest._tree(root)
    if expected_members is not None and set(files) != expected_members:
        _fail(mismatch_token)
    pins = {}
    for relative in sorted(files):
        path = root.joinpath(*relative.split("/"))
        if used + os.lstat(path).st_size > budget:
            _fail("budget_insufficient")  # checked before the file is opened
        size, digest = _hash_bounded(path, budget - used)  # and bound again to the opened file
        used += size
        pins[relative] = {"size": size, "sha256": digest}
    return pins, files, used


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
        # Reject unexpected members before opening any site or model file.  This is
        # a membership guard, not proof that caller-selected roots are trustworthy.
        wanted = set(expected_model_files)
        if "model.safetensors" not in wanted or len(wanted) != len(expected_model_files):
            _fail("model_membership_mismatch")
        model_members, _ = runtime_manifest._tree(model)
        if set(model_members) != wanted:
            _fail("model_membership_mismatch")
        if expected_site_pins is not None:
            site_members, _ = runtime_manifest._tree(site)
            if set(site_members) != set(expected_site_pins):
                _fail("site_pin_mismatch")
        deadline = time.monotonic() + timeout_seconds  # one wall-time budget for every git call of this plan
        code, third, dynamic, used = _code_plan(git_executable, repository, commit, code_roots, extra_code_paths,
                                                deadline, max_bytes)
        site_pins, site_files, used = _tree_pins(
            site, max_bytes, used, set(expected_site_pins) if expected_site_pins is not None else None,
            "site_pin_mismatch")
        model_pins, model_files, used = _tree_pins(model, max_bytes, used, wanted, "model_membership_mismatch")
        if "model.safetensors" not in wanted or set(model_pins) != wanted or len(wanted) != len(expected_model_files):
            _fail("model_membership_mismatch")
        if model_pins["model.safetensors"]["sha256"] != expected_weights_sha256:
            _fail("model_weights_mismatch")
        if expected_site_pins is not None and expected_site_pins != site_pins:
            _fail("site_pin_mismatch")
        # The trees must still have exactly the hashed membership after every read.
        if runtime_manifest._tree(site)[0] != site_files or runtime_manifest._tree(model)[0] != model_files:
            _fail("tree_changed")
        pins = {"assets/code/" + path: value for path, value in sorted(code.items())}
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
        reason = exc.args[0] if type(exc) is ValueError and exc.args else None
        token = reason if type(reason) is str and reason in _MANIFEST_REFUSALS else "runtime_plan_refused"
    except Exception:
        token = "runtime_plan_refused"
    raise RuntimePlanRefused(token)
