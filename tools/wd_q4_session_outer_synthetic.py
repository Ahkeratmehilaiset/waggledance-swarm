"""Opt-in session bootstrap candidate; caller owns the process and all anchors.

Caller must hash-pin and execute these exact bootstrap bytes and independently
pin the interpreter. The runtime manifest is externally authenticated by the
caller and covers BOTH assets/code and site; code from a separate bundle root
is not accepted. This is not an installer, a session identity verifier or a
whole-process watchdog. Same-user replacement races, native DLLs and the
interpreter/stdlib remain outside this boundary. Discard the process after any
return or refusal. The parent must also require Job cleanup confirmation,
exit zero and complete framed output; an in-process result alone is insufficient.
Namespace-package paths can be recalculated if sys.path changes; a worker that
changes import state is not contained. BaseException subclasses are not caught
or scrubbed here: caller and worker traceback frames may retain sensitive data.
"""

import hashlib
import hmac
import importlib
import importlib.machinery
import io
import json
import os
from pathlib import Path
import re
import stat
import sys

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_LIMIT = 2 * 1024 * 1024
_MAX_OUTPUT = 65536
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
_BINDING_KEYS = frozenset({"pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                           "roster_sha256", "public_policy_sha256", "descriptor_sha256", "projection_sha256"})
_TRUST = {"lane_authentication": "UNKNOWN", "freshness": "UNKNOWN", "publisher_authentication": "UNKNOWN",
          "projection_public_attestation": "UNKNOWN", "transport": "caller_injected", "projection": "caller_pinned"}
_TEXT = re.compile(r"[A-Za-z0-9._: -]{1,320}\Z")
_HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class SessionOuterRefused(ValueError):
    """No query, caller pins, paths or exception text in this refusal."""


class SessionOuterCleanupUnknown(SessionOuterRefused):
    """Worker reports an unconfirmed inner cleanup; not the parent Job state."""


class SessionOuterTimeout(SessionOuterRefused):
    """Worker reports its inner deadline expired."""


class SessionOuterInterrupted(SessionOuterRefused):
    """Worker reports interruption."""


def _pinned(path, expected):
    if type(expected) is not str or not _HEX.fullmatch(expected):
        raise ValueError()
    before = os.lstat(path)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > _LIMIT
            or getattr(before, "st_file_attributes", 0) & _REPARSE):
        raise ValueError()
    with open(path, "rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(_LIMIT + 1)
        after = os.fstat(stream.fileno())
    final = os.lstat(path)
    ident = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_nlink)
    if (len(raw) > _LIMIT or ident(before) != ident(opened) or ident(opened) != ident(after)
            or ident(after) != ident(final) or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected)):
        raise ValueError()
    return raw


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _validate_lane_answer(value, expected_lane, expected_binding):
    """Validate the closed CLI result and caller pins, not source authenticity."""
    if (type(expected_lane) is not str or expected_lane not in _LANES
            or type(expected_binding) is not dict or set(expected_binding) != _BINDING_KEYS
            or any(type(v) is not str or not _HEX.fullmatch(v) for v in expected_binding.values())
            or type(value) is not dict
            or set(value) != {"schema", "lane", "status", "reason", "result_count", "results", "binding", "trust"}
            or value["schema"] != "wd.q4-lane-answer.v1" or value["lane"] != expected_lane
            or value["status"] not in ("ANSWER", "ABSTAIN", "DEGRADED") or value["reason"] != ""
            or value["trust"] != _TRUST or type(value["trust"]) is not dict
            or type(value["binding"]) is not dict or set(value["binding"]) != _BINDING_KEYS
            or any(type(value["binding"][k]) is not str
                   or not hmac.compare_digest(value["binding"][k], expected_binding[k]) for k in _BINDING_KEYS)
            or type(value["results"]) is not list or len(value["results"]) > 100
            or type(value["result_count"]) is not int or value["result_count"] != len(value["results"])
            or (value["status"] == "ANSWER" and not value["results"])
            or (value["status"] == "ABSTAIN" and value["results"])):
        raise ValueError()
    seen = set()
    for item in value["results"]:
        if (type(item) is not dict or set(item) != {"ref_sha256", "doc_id_sha256", "stored_text"}
                or any(type(item[k]) is not str or not _HEX.fullmatch(item[k])
                       for k in ("ref_sha256", "doc_id_sha256"))
                or type(item["stored_text"]) is not str or not _TEXT.fullmatch(item["stored_text"])
                or _HEX_RUN.search(item["stored_text"]) or _UUID.search(item["stored_text"])):
            raise ValueError()
        pair = (item["ref_sha256"], item["doc_id_sha256"])
        if pair in seen:
            raise ValueError()
        seen.add(pair)
    return value


def _inside(path, root):
    try:
        path = os.path.normcase(os.path.abspath(os.fspath(path)))
        root = os.path.normcase(os.path.abspath(os.fspath(root)))
        return os.path.commonpath((path, root)) == root
    except (TypeError, ValueError):
        return False


def _physical_path(value):
    """Fail closed on DOS aliases and reparse points before physical comparison."""
    supplied = Path(value)
    if (not supplied.is_absolute() or str(supplied).startswith(("\\\\", "//"))
            or any(part in (".", "..") or re.search(r"~[0-9]+(?:\.[^.]*)?$", part)
                   for part in supplied.parts[1:])):
        raise ValueError()
    current = Path(supplied.anchor)
    root_info = os.lstat(current)
    if stat.S_ISLNK(root_info.st_mode) or getattr(root_info, "st_file_attributes", 0) & _REPARSE:
        raise ValueError()
    for part in supplied.parts[1:]:
        current = current / part
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise ValueError()
    return os.path.realpath(str(supplied), strict=True)


class _BoundedBytes(io.BytesIO):
    def write(self, value):
        if self.tell() + len(value) > _MAX_OUTPUT:
            raise ValueError()
        return super().write(value)


class _ImportGuard:
    """Constrain future filesystem imports; not a sandbox or defense against worker mutation.

    Interpreter stdlib and DLL roots are location-restricted, not hash-verified.
    A worker that changes import machinery or invokes native APIs is outside this guard.
    """
    def __init__(self, code, site):
        self.code, self.site = str(code), str(site)

    def find_spec(self, fullname, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None:
            return None
        paths = [] if spec.origin is None else [spec.origin]
        if spec.submodule_search_locations is not None:
            paths.extend(spec.submodule_search_locations)
        if not paths:
            raise ImportError("session_outer_refused")
        for value in paths:
            if type(value) is not str or not os.path.isabs(value):
                raise ImportError("session_outer_refused")
            stdlib = Path(sys.base_prefix) / "Lib"
            dlls = Path(sys.base_exec_prefix) / "DLLs"
            stdlib_zip = Path(sys.base_prefix) / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
            try:
                supplied = Path(value)
                if any(re.search(r"~[0-9]+(?:\.[^.]*)?$", part) or part in (".", "..")
                       for part in supplied.parts[1:]):
                    raise ValueError()
                if _inside(value, stdlib_zip):
                    # A zipimport origin names a virtual child, not an on-disk
                    # file. Verify the physical archive itself before comparing.
                    components = {part.casefold() for part in supplied.parts}
                    if "site-packages" in components or "dist-packages" in components:
                        raise ValueError()
                    info = os.lstat(stdlib_zip)
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError()
                    _physical_path(stdlib_zip)
                    continue
                candidates = [allowed for allowed in (stdlib, dlls, self.code, self.site) if _inside(value, allowed)]
                if not candidates:
                    raise ValueError()
                physical = _physical_path(value)
                components = {part.casefold() for part in Path(physical).parts}
                approved = False
                for allowed in candidates:
                    under_interpreter = allowed in (stdlib, dlls)
                    if under_interpreter and ("site-packages" in components or "dist-packages" in components):
                        continue
                    if _inside(physical, _physical_path(allowed)):
                        approved = True
                        break
                if not approved:
                    raise ValueError()
            except (OSError, TypeError, ValueError):
                raise ImportError("session_outer_refused") from None
        return spec


def run_session(*, enabled=False, runtime_root=None, runtime_manifest=None, runtime_manifest_sha256=None,
                runtime_generation=None, runtime_revision=None, verifier_sha256=None, outer_sha256=None,
                child_sha256=None, query_sha256=None, worker_sha256=None, argv=None, query=None,
                expected_lane=None, expected_binding=None):
    """Execute a caller-pinned named worker only after manifest verification.

    Output is accepted only as one bounded LF-terminated JSON document with the
    expected public schema and successful worker return. This is framing and
    pin consistency, not an independently authenticated answer or source.
    """
    if enabled is False:
        return {"status": "OFF"}
    old_stdin = old_stdout = old_stderr = None
    critical = None
    try:
        if enabled is not True or not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
            raise ValueError()
        if (type(query) is not bytes or not query or len(query) > 16000 or type(argv) is not list
                or not argv or any(type(arg) is not str or not arg or len(arg) > 32767 for arg in argv)):
            raise ValueError()
        root = Path(runtime_root)
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError()
        code = root / "assets" / "code"
        verifier = code / "sandbox" / "mind" / "step2" / "runtime_manifest.py"
        outer_path = code / "tools" / "wd_q4_public_outer.py"
        worker_path = code / "tools" / "wd_q4_lane_session_cli.py"
        if any(name == "tools" or name.startswith("tools.") or name == "sandbox" or name.startswith("sandbox.")
               or name == "core" or name.startswith("core.") or name == "waggledance"
               or name.startswith("waggledance.") for name in sys.modules):
            raise ValueError()
        verifier_raw = _pinned(verifier, verifier_sha256)
        namespace = {"__name__": "_wd_session_runtime_verifier", "__file__": str(verifier)}
        exec(compile(verifier_raw, str(verifier), "exec"), namespace)
        namespace["verify_runtime"](root, runtime_manifest, runtime_manifest_sha256,
                                    expected_generation=runtime_generation, expected_revision=runtime_revision)
        outer_raw = _pinned(outer_path, outer_sha256)
        worker_raw = _pinned(worker_path, worker_sha256)
        sys.path.insert(0, str(code))
        public_outer = importlib.import_module("tools.wd_q4_public_outer")
        result = public_outer.bootstrap_public_imports(
            enabled=True, code_root=str(code), verifier_path=str(verifier), verifier_sha256=verifier_sha256,
            public_child_sha256=child_sha256, public_query_sha256=query_sha256,
            runtime_root=str(root), runtime_manifest=runtime_manifest, runtime_manifest_sha256=runtime_manifest_sha256,
            runtime_generation=runtime_generation, runtime_revision=runtime_revision)
        if (result.get("status") != "MODULES_IMPORTED" or _pinned(outer_path, outer_sha256) != outer_raw
                or _pinned(worker_path, worker_sha256) != worker_raw):
            raise ValueError()
        guard = _ImportGuard(code, root / "site")
        sys.meta_path.insert(2, guard)
        old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
        raw_output = _BoundedBytes()
        sys.stdin = io.TextIOWrapper(io.BytesIO(query), encoding="utf-8")
        sys.stdout = io.TextIOWrapper(raw_output, encoding="utf-8", newline="\n", write_through=True)
        sys.stderr = io.TextIOWrapper(_BoundedBytes(), encoding="utf-8", write_through=True)
        worker = importlib.import_module("tools.wd_q4_lane_session_cli")
        if worker.__file__ != str(worker_path) or not callable(getattr(worker, "main", None)):
            raise ValueError()
        exit_code = worker.main(argv)
        sys.stdout.flush()
        raw = raw_output.getvalue()
        if type(exit_code) is int and exit_code in (3, 4, 130):
            critical = {3: "cleanup_unknown", 4: "timeout", 130: "interrupted"}[exit_code]
            raise ValueError()
        if (type(exit_code) is not int or exit_code != 0 or not raw or len(raw) > _MAX_OUTPUT
                or not raw.endswith(b"\n") or raw.count(b"\n") != 1 or b"\r" in raw):
            raise ValueError()
        value = json.loads(raw[:-1].decode("utf-8", "strict"), object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return _validate_lane_answer(value, expected_lane, expected_binding)
    except Exception:
        pass
    finally:
        if old_stdin is not None:
            sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr
    # The refusal traceback must not retain caller data in this frame. This
    # does not scrub caller frames or objects retained by the worker itself.
    query = argv = runtime_root = runtime_manifest = runtime_manifest_sha256 = None
    runtime_generation = runtime_revision = verifier_sha256 = outer_sha256 = None
    child_sha256 = query_sha256 = worker_sha256 = expected_lane = expected_binding = None
    old_stdin = old_stdout = old_stderr = None
    root = code = verifier = outer_path = worker_path = None
    verifier_raw = outer_raw = worker_raw = namespace = public_outer = result = guard = None
    raw_output = worker = exit_code = raw = value = None
    if critical == "cleanup_unknown":
        raise SessionOuterCleanupUnknown("worker_cleanup_unknown") from None
    if critical == "timeout":
        raise SessionOuterTimeout("worker_timeout") from None
    if critical == "interrupted":
        raise SessionOuterInterrupted("worker_interrupted") from None
    raise SessionOuterRefused("session_outer_refused") from None
