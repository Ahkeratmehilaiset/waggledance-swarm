# SPDX-License-Identifier: BUSL-1.1
# Based on sandbox/mind/step2/runtime_launcher.py at Git commit e98c5aef5bd3bd66c61e5692e738f17a4ecf6beb; verifier/import paths adapted.
"""Bounded Windows Q4 launcher; not an installer, identity or isolation boundary.

All trust pins and paths come from the caller, never from an environment variable.
The interpreter starts a fixed isolated stdlib trampoline which waits for a byte
before running the verified bootstrap.  The parent sends that byte only after
assigning the process to a kill-on-close Windows job.  Descendants normally stay
in that job; a failure to create or join it is a refusal.  This does not protect
against malicious native code, independently breakaway jobs, hostile same-user
mutation, reparse changes, native DLL substitution, or file-check/use races.
The manifest verifier, caller, actual ACLs, network isolation and all runtime
assets must be independently trusted.  No caller identity is authenticated.

The legacy watchdog bounds the launched job after the process starts, including
bootstrap execution and retained stdout. Hash preflight, Windows process
creation and job assignment are synchronous and can block outside that bound;
this function does NOT prove a hard wall-clock bound for the whole call. A
separately supervised process with independently verified containment is needed
before claiming such a bound. Output is an intentionally lossy projection; even
hex digests and status values are data channels, not a full privacy guarantee.

start_e5_worker_graceful is a separate, default-off cooperative path. Its Job
does not kill on close; a pinned non-daemon owner retains a timed-out child in
HOLD. This bounds pipe waits, not the lifetime of a stuck native operation.
"""

import ctypes
from ctypes import wintypes
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import stat
import threading
import time


class LaunchRefused(ValueError):
    """Fixed refusal code only; never include raw data or paths."""


_HEX = re.compile(r"[0-9a-f]{64}\Z")
BULK_CONTRACT = b'{"frame_bytes":65536,"inflight_bytes":65540,"nonce_limit":8192,"schema":"wd.mind-e5-bulk-contract.v1","stdout_bytes":67108864}'
BULK_CONTRACT_SHA256 = '61998bfeea1342988301f30f21d9781017249ef66761ec81874e5cc34633bc08'
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_REASON = re.compile(r"[a-z0-9_]{1,64}\Z")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|\+00:00)\Z")
_REF = re.compile(r"(?:[a-z][a-z0-9_-]{0,15}:)?[0-9a-f]{40,64}\Z")
_KEYS = ("runtime-root", "runtime-manifest", "runtime-manifest-sha256", "runtime-generation",
         "runtime-revision", "mutex-sha256", "memory", "descriptor", "descriptor-sha256", "lock-file", "lock-timeout")
_PUBLIC_KEYS = tuple(key for key in _KEYS if key != "mutex-sha256")
_WORKER_ONLY_KEYS = tuple(key for key in _PUBLIC_KEYS if key not in ('memory', 'descriptor', 'descriptor-sha256'))
_PATHS = ("runtime-root", "runtime-manifest", "memory", "descriptor", "lock-file")
_MAX_QUERY = 65536
_MAX_OUTPUT = 262144
_SHARED_SCOPE = "wd.q4-shared-projected-events.v1"
# One fixed fleet-wide lock name preserves the reader-slot sharing contract.
# It is an inert sentinel under the operator's audit tree; no lock file is opened.
_AUDIT_ROOT = Path(r"C:\Python\project2\.codex-audit")
_BOOTSTRAP_REFUSALS = frozenset({"isolation_required", "runtime_verification_failed", "reader_execution_failed"})
_READER_REFUSALS = frozenset({
    "stdin_unreadable", "stdin_over_budget", "stdin_not_json", "query_shape", "situation_invalid", "time_invalid",
    "descriptor_pin_invalid", "descriptor_over_budget", "descriptor_pin_mismatch", "descriptor_unreadable",
    "descriptor_scope_mismatch", "descriptor_shape", "memory_descriptor_mismatch", "memory_descriptor_unreadable",
    "memory_unreadable", "memory_descriptor_changed", "memory_dim_mismatch", "memory_no_healthy_cell",
    "encoder_identity_mismatch", "memory_scope_mismatch", "encoder_runtime_unavailable", "encoder_runtime_mismatch",
    "q4_result_shape", "runtime_revision_mismatch", "encoder_load_failed", "q4_execution_failed", "output_guarded",
    "reader_lock_invalid", "reader_busy", "reader_lock_unavailable", "runtime_unavailable",
})
_TRAMPOLINE = ("import sys,runpy; "
               "assert sys.stdin.buffer.read(1)==b'\\0'; "
               "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')")


def _path(value):
    try:
        path = Path(value)
        text = str(path)
        if not path.is_absolute() or text.startswith(("\\\\", "//")) or "\x00" in text:
            raise ValueError()
        # Drive-relative and alternate data streams are not local ordinary paths.
        if os.name == "nt" and (len(text) < 3 or not text[0].isalpha() or text[1:3] != ":\\" or ":" in text[2:]):
            raise ValueError()
        return path
    except (TypeError, ValueError, OSError):
        raise LaunchRefused("invalid_path") from None


def _validate_audit_write_scope(lock_file, scratch_root):
    """Check the fixed inert sentinel and refuse any requested scratch path."""
    try:
        audit = _path(_AUDIT_ROOT)
        lock = _path(lock_file)
        if scratch_root is not None or any(".." in path.parts for path in (audit, lock)):
            raise ValueError()
        scope = audit / "wd-mind-runtime"
        expected_lock = scope / "q4-reader.lock"
        same = lambda a, b: os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
        if not same(lock, expected_lock):
            raise ValueError()
        # Existing ancestor directories only; never create during validation.
        for directory in (scope,):
            current = Path(directory.anchor)
            for part in directory.parts[1:]:
                if ":" in part or part.endswith((".", " ")):
                    raise ValueError()
                current /= part
                info = os.lstat(current)
                if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & 0x400):
                    raise ValueError()
        try:
            info = os.lstat(lock)
        except FileNotFoundError:
            info = None
        if info is not None and (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                                 or getattr(info, "st_file_attributes", 0) & 0x400
                                 or info.st_nlink != 1):
            raise ValueError()
    except Exception:
        raise LaunchRefused("audit_write_scope_refused") from None


def _require_safe_writer_scope(lock_file, scratch_root):
    _validate_audit_write_scope(lock_file, scratch_root)
    # The bootstrap must install the pinned named-mutex shim before importing
    # the historical reader. Its lock argument is only a fixed inert sentinel.
    # Scratch creation is disabled, though native-library I/O is not sandboxed.


def _matches(path, expected):
    if not isinstance(expected, str) or not _HEX.fullmatch(expected):
        return False
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return False
    return digest.hexdigest() == expected


class _BasicLimit(ctypes.Structure):
    _fields_ = [("per_process_time", ctypes.c_int64), ("per_job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("basic", _BasicLimit), ("io", _IoCounters), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t)]


class _Job:
    def __init__(self, *, kill_on_close=True, _owner=None):
        self.handle, self.kill_on_close = None, kill_on_close
        if _owner is not None:
            if kill_on_close is not False:
                raise LaunchRefused("job_ownership_refused")
            # Retain even a partially initialized Job before allocating a handle.
            _owner.resources.job = self
        if os.name != "nt":
            raise LaunchRefused("windows_job_required")
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        api.SetInformationJobObject.restype = wintypes.BOOL
        api.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        api.AssignProcessToJobObject.restype = wintypes.BOOL
        api.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        api.TerminateJobObject.restype = wintypes.BOOL
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.api = api
        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle:
            raise LaunchRefused("job_unavailable")
        limits = _ExtendedLimit()
        limits.basic.flags = 0x2000 if kill_on_close else 0  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            if _owner is None:
                self.close()
            raise LaunchRefused("job_unavailable")

    def attach(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise LaunchRefused("job_assignment_failed")

    def terminate(self):
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            closed = self.api.CloseHandle(self.handle)
            if self.kill_on_close is False and not closed:
                raise LaunchRefused("job_close_failed")
            self.handle = None


def _object_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate")
        obj[key] = value
    return obj


def _validate_output(raw, exit_code, expected_descriptor):
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(data, dict) or data.get("status") not in ("ANSWER", "ABSTAIN", "DEGRADED", "REFUSED"):
            raise ValueError()
        status = data["status"]
        reason = data.get("reason")
        if reason is not None and (not isinstance(reason, str) or not _REASON.fullmatch(reason)):
            raise ValueError()
        if data.get("schema") == "wd.q4-runtime-bootstrap.v1":
            if (status != "REFUSED" or exit_code != 2 or set(data) != {"schema", "status", "reason"}
                    or reason not in _BOOTSTRAP_REFUSALS):
                raise ValueError()
            return {"schema": "wd.q4-runtime-bootstrap.v1", "status": "REFUSED", "reason": reason}
        # Import failures happen before the reader defines IDENTITY.  Recognize
        # only its exact fixed refusal, never a successful or caller-bound result.
        if (data.get("schema") == "wd.bridge-mind-q4-private-read.v1" and status == "REFUSED"
                and exit_code == 2 and reason == "runtime_unavailable" and data.get("results") == []
                and set(data) == {"schema", "status", "reason", "results"}):
            return {"schema": "wd.bridge-mind-q4-private-read.v1", "status": "REFUSED",
                    "reason": "runtime_unavailable", "results": []}
        if data.get("schema") != "wd.bridge-mind-q4-private-read.v1" or data.get("identity") != "caller_declared_not_authenticated":
            raise ValueError()
        if (status == "REFUSED") != (exit_code == 2) or exit_code not in (0, 2):
            raise ValueError()
        results = data.get("results")
        if not isinstance(results, list) or len(results) > 100:
            raise ValueError()
        out = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": status, "reason": "reader_refused" if status == "REFUSED" else None, "results": []}
        if status == "REFUSED":
            if results or reason not in _READER_REFUSALS:
                raise ValueError()
            out["reason"] = reason
            return out
        binding = data.get("binding")
        if not isinstance(binding, dict) or binding.get("descriptor_sha256") != expected_descriptor:
            raise ValueError()
        if binding.get("scope") != _SHARED_SCOPE:
            raise ValueError()
        if not isinstance(binding.get("memory_sha256"), str) or not _HEX.fullmatch(binding["memory_sha256"]):
            raise ValueError()
        rule = data.get("rule")
        if not isinstance(rule, str) or not _TOKEN.fullmatch(rule):
            raise ValueError()
        out.update(scope=_SHARED_SCOPE, descriptor_sha256=expected_descriptor, memory_sha256=binding["memory_sha256"])
        for item in results:
            if not isinstance(item, dict):
                raise ValueError()
            clean = {}
            for key in ("ref", "doc_id"):
                value = item.get(key)
                if value is not None and (not isinstance(value, str) or not _REF.fullmatch(value)):
                    raise ValueError()
                h = item.get(key + "_sha256")
                if not isinstance(h, str) or not _HEX.fullmatch(h):
                    raise ValueError()
                if value is not None and hashlib.sha256(value.encode("utf-8")).hexdigest() != h:
                    raise ValueError()
                clean[key + "_sha256"] = h
                if key == "ref":
                    reference = value
            for key in ("kind", "cell", "tier"):
                if not isinstance(item.get(key), str) or not _TOKEN.fullmatch(item[key]):
                    raise ValueError()
            when = item.get("time_utc")
            if when is not None and (not isinstance(when, str) or not _TIME.fullmatch(when)):
                raise ValueError()
            kind, canonical = item.get("ref_kind"), item.get("canonical_event")
            projected = (kind == "sha256_of_projected_metadata" and canonical is False
                         and item["kind"] == "event" and isinstance(reference, str)
                         and _HEX.fullmatch(reference) is not None)
            if not projected:
                raise ValueError()
            clean.update(ref_kind=kind, canonical_event=canonical)
            for key in ("score", "fused"):
                value = item.get(key)
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError()
            out["results"].append(clean)
        return out
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError, KeyError):
        raise LaunchRefused("invalid_output") from None


def _launch_historical(*, interpreter, interpreter_sha256, bootstrap, bootstrap_sha256, verifier_sha256,
           tuple_args, query, timeout, scratch_root=None, interpreter_identity=None):
    """Return only validated, reduced Q4 JSON; emit no raw stderr or traceback."""
    if type(tuple_args) is not dict or "lock-file" not in tuple_args:
        raise LaunchRefused("audit_write_scope_refused")
    _require_safe_writer_scope(tuple_args["lock-file"], scratch_root)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 1800:
        raise LaunchRefused("invalid_timeout")
    if not isinstance(query, bytes) or len(query) > _MAX_QUERY:
        raise LaunchRefused("invalid_query")
    if not isinstance(tuple_args, dict) or set(tuple_args) != set(_KEYS) or not all(
            isinstance(value, str) and value and "\x00" not in value for value in tuple_args.values()):
        raise LaunchRefused("invalid_arguments")
    interpreter, bootstrap = _path(interpreter), _path(bootstrap)
    for key in _PATHS:
        _path(tuple_args[key])
    if (not _HEX.fullmatch(tuple_args["runtime-manifest-sha256"]) or
            not _HEX.fullmatch(tuple_args["descriptor-sha256"]) or not _HEX.fullmatch(tuple_args["mutex-sha256"])):
        raise LaunchRefused("invalid_arguments")
    if not all(_TOKEN.fullmatch(tuple_args[key]) for key in ("runtime-generation", "runtime-revision")):
        raise LaunchRefused("invalid_arguments")
    try:
        lock_timeout = float(tuple_args["lock-timeout"])
        if not math.isfinite(lock_timeout) or not 0 <= lock_timeout <= 30:
            raise ValueError()
    except ValueError:
        raise LaunchRefused("invalid_arguments") from None
    if not (_matches(interpreter, interpreter_sha256) and _matches(bootstrap, bootstrap_sha256)
            and _matches(bootstrap.with_name("wd_mind_runtime_manifest.py"), verifier_sha256)):
        raise LaunchRefused("trust_pin_mismatch")
    if interpreter_identity is not None:
        # An opt-in pinned tree check, not caller authentication or a proof
        # about files loaded after this check. Legacy calls stay unchanged.
        verified = False
        try:
            if (type(interpreter_identity) is not dict
                    or set(interpreter_identity) != {"root", "descriptor_bytes", "expected_sha256"}
                    or not isinstance(interpreter_identity["root"], (str, Path))
                    or type(interpreter_identity["descriptor_bytes"]) is not bytes
                    or type(interpreter_identity["expected_sha256"]) is not str
                    or _HEX.fullmatch(interpreter_identity["expected_sha256"]) is None):
                raise ValueError()
            from tools.wd_mind_interpreter_identity import verify_interpreter_identity
            result = verify_interpreter_identity(
                enabled=True, root=interpreter_identity["root"],
                descriptor_bytes=interpreter_identity["descriptor_bytes"],
                expected_sha256=interpreter_identity["expected_sha256"],
                expected_interpreter=interpreter, expected_interpreter_sha256=interpreter_sha256)
            verified = type(result) is dict and result.get("status") == "VERIFIED"
        except Exception:
            pass
        if not verified:
            raise LaunchRefused("interpreter_identity_refused") from None
    args = [str(interpreter), "-I", "-S", "-B", "-c", _TRAMPOLINE, str(bootstrap)]
    for key in _KEYS:
        args.extend(("--" + key, tuple_args[key]))
    started = time.monotonic()
    job, process = _Job(), None
    output, overflow = bytearray(), threading.Event()
    try:
        # Only OS location variables are passed; no PYTHON*, HF*, or caller secrets.
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        env.update(TEMP=os.devnull, TMP=os.devnull, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        for name in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
                     "HF_ASSETS_CACHE", "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "CUDA_CACHE_PATH"):
            env[name] = os.devnull
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   cwd=str(bootstrap.parent), env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        job.attach(process)

        def write_input():
            try:
                process.stdin.write(b"\0" + query)
                process.stdin.close()
            except (OSError, ValueError):
                pass

        def read_output():
            try:
                while True:
                    block = process.stdout.read(8192)
                    if not block:
                        return
                    if len(output) + len(block) > _MAX_OUTPUT:
                        overflow.set()
                        job.terminate()
                        return
                    output.extend(block)
            except (OSError, ValueError):
                return

        writer = threading.Thread(target=write_input, daemon=True)
        reader = threading.Thread(target=read_output, daemon=True)
        writer.start()
        reader.start()
        remaining = timeout - (time.monotonic() - started)
        try:
            process.wait(timeout=max(0, remaining))
        except subprocess.TimeoutExpired:
            job.terminate()
            process.wait(timeout=5)
            raise LaunchRefused("watchdog_timeout") from None
        # A child retaining stdout must not extend the watchdog or escape it.
        remaining = max(0, timeout - (time.monotonic() - started))
        reader.join(remaining)
        if reader.is_alive():
            job.terminate()
            raise LaunchRefused("watchdog_timeout")
        if overflow.is_set():
            raise LaunchRefused("output_limit")
        return _validate_output(bytes(output), process.returncode, tuple_args["descriptor-sha256"])
    except LaunchRefused:
        raise
    except (OSError, subprocess.SubprocessError, ValueError):
        raise LaunchRefused("launcher_unavailable") from None
    finally:
        job.terminate()
        job.close()
        if process is not None and process.poll() is None:
            process.kill()


def launch(*, bundle_root, deployment_manifest_sha256, interpreter, interpreter_sha256,
           interpreter_root, tuple_args, query, timeout, scratch_root):
    """Launch the historical offline Q4 reader only after bundle-bound preflight.

    The caller must supply an independently anchored deployment-manifest hash;
    neither a caller-selected runtime hash nor returned reference hashes grant
    authority. The bundle must contain a runtime-tree manifest and an
    interpreter identity descriptor as separately pinned files. The historical
    child emits reference hashes only; it is not the current content resolver.

    Synchronous preflight and process creation remain outside the historical
    Windows Job deadline. Network and native DLL isolation and same-user races
    are not proven; offline/cache environment flags are not an OS firewall.
    """
    try:
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 1800
                or type(query) is not bytes or len(query) > _MAX_QUERY
                or type(tuple_args) is not dict or set(tuple_args) != set(_PUBLIC_KEYS)):
            raise ValueError("invalid_request")
        _require_safe_writer_scope(tuple_args["lock-file"], scratch_root)
        from tools import wd_mind_runtime_process as anchored
        bundle = Path(bundle_root)
        if not bundle.is_absolute() or ".." in bundle.parts:
            raise ValueError("bundle")
        manifest_bytes = anchored._read_pinned_file(
            bundle / "deployment-manifest.json", deployment_manifest_sha256,
            limit=4 * 1024 * 1024, retain=True)
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"),
                              object_pairs_hook=anchored._unique_json_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
        if (type(manifest) is not dict or manifest.get("schema_version") != 1
                or type(manifest.get("files")) is not dict):
            raise ValueError("manifest")

        def pin(relative):
            digest = manifest["files"].get(relative)
            if type(digest) is not str or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise ValueError("pin")
            path = bundle.joinpath(*relative.split("/"))
            data = anchored._read_pinned_file(path, digest.lower(), limit=8 * 1024 * 1024, retain=True)
            return path, digest.lower(), data

        bootstrap, bootstrap_sha256, _ = pin("tools-bootstrap/tools/wd_mind_runtime_bootstrap.py")
        verifier, verifier_sha256, _ = pin("tools-bootstrap/tools/wd_mind_runtime_manifest.py")
        _, mutex_sha256, _ = pin("tools-bootstrap/tools/wd_mind_runtime_mutex.py")
        process_file, _, _ = pin("tools-bootstrap/tools/wd_mind_runtime_process.py")
        child_file, _, _ = pin("tools-bootstrap/tools/wd_mind_runtime_child.py")
        identity_file, _, _ = pin("tools-bootstrap/tools/wd_mind_interpreter_identity.py")
        def same_path(left, right):
            return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(os.path.abspath(os.fspath(right)))
        if not same_path(__file__, child_file) or not same_path(anchored.__file__, process_file):
            raise ValueError("unpackaged_process_code")
        # Verify module resolution before importing the identity adapter; the
        # packaged verifier is already checked by preflight below. Existing
        # module origin metadata is consistency evidence, not authentication.
        for name, expected in (("tools.wd_mind_interpreter_identity", identity_file),
                               ("tools.wd_mind_runtime_manifest", verifier)):
            loaded = sys.modules.get(name)
            origin = getattr(loaded, "__file__", None) if loaded is not None else None
            if loaded is None:
                spec = importlib.util.find_spec(name)
                origin = None if spec is None else spec.origin
            if not origin or not same_path(origin, expected):
                raise ValueError("unpackaged_module")
        runtime_manifest, runtime_sha256, _ = pin("wd-mind-runtime-manifest.json")
        _, identity_sha256, identity_bytes = pin("wd-mind-interpreter-identity.json")
        if (type(tuple_args) is not dict or
                not same_path(tuple_args["runtime-manifest"], runtime_manifest) or
                tuple_args["runtime-manifest-sha256"] != runtime_sha256):
            raise ValueError("runtime_manifest_binding")
        anchored.verify_runtime_process_preflight(
            interpreter=interpreter, interpreter_sha256=interpreter_sha256,
            bootstrap=bootstrap, bootstrap_sha256=bootstrap_sha256,
            bundle_root=bundle, deployment_manifest_sha256=deployment_manifest_sha256,
            verifier=verifier, verifier_sha256=verifier_sha256,
            runtime_root=tuple_args["runtime-root"], runtime_manifest=runtime_manifest,
            runtime_manifest_sha256=runtime_sha256, generation=tuple_args["runtime-generation"],
            revision=tuple_args["runtime-revision"])
        internal_args = dict(tuple_args, **{"mutex-sha256": mutex_sha256})
        historical = _launch_historical(
            interpreter=interpreter, interpreter_sha256=interpreter_sha256,
            bootstrap=bootstrap, bootstrap_sha256=bootstrap_sha256, verifier_sha256=verifier_sha256,
            tuple_args=internal_args, query=query, timeout=timeout, scratch_root=scratch_root,
            interpreter_identity={"root": interpreter_root, "descriptor_bytes": identity_bytes,
                                  "expected_sha256": identity_sha256})
        return {"schema": "wd.mind-runtime-child-result.v1", "authority": "none",
                "status": historical["status"], "source_binding": "NOT_PROVEN",
                "sender_authentication": "UNKNOWN", "publisher_authentication": "UNKNOWN",
                "network_isolation": "UNKNOWN", "native_closure": "UNKNOWN", "same_user_race": "UNKNOWN",
                "runtime_result": historical}
    except LaunchRefused:
        raise
    except Exception:
        pass
    raise LaunchRefused("runtime_child_refused") from None


def start_e5_worker(*, bundle_root, deployment_manifest_sha256, interpreter, interpreter_sha256,
                    interpreter_root, tuple_args, timeout, scratch_root=None):
    """Start an offline private-pipe encoder after externally anchored preflight.

    Returns its client and the bundle-pinned encoder binding, not a source or
    production-readiness decision. Synchronous preflight, process creation and
    job attachment are not covered by the pipe handshake deadline; caller must
    schedule/supervise this as prestart work. Network and native isolation and
    same-user races remain UNKNOWN.
    """
    return _start_e5_worker(bundle_root=bundle_root, deployment_manifest_sha256=deployment_manifest_sha256,
        interpreter=interpreter, interpreter_sha256=interpreter_sha256, interpreter_root=interpreter_root,
        tuple_args=tuple_args, timeout=timeout, scratch_root=scratch_root, cooperative=False)


def start_e5_worker_graceful(*, enabled=False, **options):
    """Explicit opt-in; requires a separately supplied pin for the new owner code.

    READY returns client and owner. A post-creation refusal returns client=None,
    owner and a visible HOLD/REFUSED status. Keep the owner session alive while
    HOLD persists; its non-daemon keeper intentionally prevents ordinary Python
    exit. No restart or termination is attempted. Refusals before reserving an
    owner raise the fixed LaunchRefused token. After reservation, even Job-only
    cleanup returns the owner and HOLD/REFUSED status. Existing deployed bundles
    are not upgraded here.
    """
    if enabled is not True or "cooperative" in options:
        raise LaunchRefused("mind_e5_start_refused")
    return _start_e5_worker(cooperative=True, **options)


def _start_e5_worker(*, bundle_root, deployment_manifest_sha256, interpreter, interpreter_sha256,
                     interpreter_root, tuple_args, timeout, scratch_root=None, cooperative,
                     bulk_contract_sha256=None, worker_only=False, owner_sink=None):
    job = process = owner = None
    try:
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 1800
                or (bulk_contract_sha256 is not None and
                    (not cooperative or type(bulk_contract_sha256) is not str
                     or bulk_contract_sha256 != BULK_CONTRACT_SHA256
                     or hashlib.sha256(BULK_CONTRACT).hexdigest() != BULK_CONTRACT_SHA256))
                or type(worker_only) is not bool or (worker_only and not cooperative)
                or (owner_sink is not None and (not cooperative or not callable(owner_sink)))
                or type(tuple_args) is not dict
                or set(tuple_args) != set(_WORKER_ONLY_KEYS if worker_only else _PUBLIC_KEYS)):
            raise ValueError("request")
        deadline = time.monotonic() + timeout
        _require_safe_writer_scope(tuple_args["lock-file"], scratch_root)
        from tools import wd_mind_runtime_process as anchored
        bundle = Path(bundle_root)
        if not bundle.is_absolute() or ".." in bundle.parts:
            raise ValueError("bundle")
        manifest_raw = anchored._read_pinned_file(bundle / "deployment-manifest.json", deployment_manifest_sha256,
                                                 limit=4 * 1024 * 1024, retain=True)
        manifest = json.loads(manifest_raw.decode("utf-8-sig"), object_pairs_hook=anchored._unique_json_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
        if type(manifest) is not dict or manifest.get("schema_version") != 1 or type(manifest.get("files")) is not dict:
            raise ValueError("manifest")

        def pin(relative):
            value = manifest["files"].get(relative)
            if type(value) is not str or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
                raise ValueError("pin")
            path = bundle.joinpath(*relative.split("/"))
            return path, value.lower(), anchored._read_pinned_file(path, value.lower(), limit=8 * 1024 * 1024, retain=True)

        def same_path(left, right):
            return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(os.path.abspath(os.fspath(right)))

        bootstrap, bootstrap_sha, bootstrap_raw = pin("tools-bootstrap/tools/wd_mind_runtime_bootstrap.py")
        verifier, verifier_sha, _ = pin("tools-bootstrap/tools/wd_mind_runtime_manifest.py")
        _, mutex_sha, _ = pin("tools-bootstrap/tools/wd_mind_runtime_mutex.py")
        worker_path, worker_sha, worker_raw = pin("tools-bootstrap/tools/wd_mind_e5_worker.py")
        client_path, _, _ = pin("tools-bootstrap/tools/wd_mind_e5_client.py")
        child_path, _, _ = pin("tools-bootstrap/tools/wd_mind_runtime_child.py")
        process_path, _, _ = pin("tools-bootstrap/tools/wd_mind_runtime_process.py")
        identity_path, _, _ = pin("tools-bootstrap/tools/wd_mind_interpreter_identity.py")
        lifecycle_path = pin("tools-bootstrap/tools/wd_mind_e5_lifecycle.py")[0] if cooperative else None
        if bulk_contract_sha256 is not None and (BULK_CONTRACT not in bootstrap_raw or BULK_CONTRACT not in worker_raw):
            raise ValueError('bulk_contract_missing_from_pinned_code')
        if not same_path(__file__, child_path) or not same_path(anchored.__file__, process_path):
            raise ValueError("unpackaged_process_code")
        origins = [("tools.wd_mind_interpreter_identity", identity_path),
                               ("tools.wd_mind_runtime_manifest", verifier),
                               ("tools.wd_mind_e5_client", client_path)]
        if cooperative:
            origins.append(("tools.wd_mind_e5_lifecycle", lifecycle_path))
        for name, expected in origins:
            loaded = sys.modules.get(name)
            origin = getattr(loaded, "__file__", None) if loaded is not None else None
            if loaded is None:
                spec = importlib.util.find_spec(name)
                origin = None if spec is None else spec.origin
            if not origin or not same_path(origin, expected):
                raise ValueError("unpackaged_module")
        runtime_manifest, runtime_sha, runtime_raw = pin("wd-mind-runtime-manifest.json")
        _, identity_sha, identity_bytes = pin("wd-mind-interpreter-identity.json")
        _, binding_sha, binding_bytes = pin("wd-mind-e5-encoder-binding.json")
        if (not same_path(tuple_args["runtime-manifest"], runtime_manifest)
                or tuple_args["runtime-manifest-sha256"] != runtime_sha):
            raise ValueError("runtime_manifest_binding")
        binding = json.loads(binding_bytes.decode("utf-8"), object_pairs_hook=anchored._unique_json_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
        policy = {"truncate_chars": 2000, "normalize": True, "dtype": "float32", "dim": 384}
        if (type(binding) is not dict or set(binding) != {"schema", "encoder", "code_sha256", "passage_policy", "query_policy"}
                or binding["schema"] != "wd.mind-memory-encoder-binding.v1" or binding["code_sha256"] != worker_sha
                or binding["passage_policy"] != dict(policy, prefix="passage: ")
                or binding["query_policy"] != dict(policy, prefix="query: ")
                or type(binding["passage_policy"].get("normalize")) is not bool
                or type(binding["query_policy"].get("normalize")) is not bool
                or type(binding["passage_policy"].get("truncate_chars")) is not int
                or type(binding["query_policy"].get("truncate_chars")) is not int
                or type(binding["passage_policy"].get("dim")) is not int
                or type(binding["query_policy"].get("dim")) is not int
                or type(binding["encoder"]) is not dict
                or set(binding["encoder"]) != {"model", "revision", "weights_sha256"}
                or binding["encoder"]["model"] != "intfloat/multilingual-e5-small"
                or binding["encoder"]["revision"] != tuple_args["runtime-revision"]
                or type(binding["encoder"]["weights_sha256"]) is not str
                or _HEX.fullmatch(binding["encoder"]["weights_sha256"]) is None):
            raise ValueError("encoder_binding")
        runtime_document = json.loads(runtime_raw.decode("utf-8"), object_pairs_hook=anchored._unique_json_object)
        weights = [row for row in runtime_document.get("files", [])
                   if type(row) is dict and row.get("path") == "model/model.safetensors"]
        if len(weights) != 1 or weights[0].get("sha256") != binding["encoder"]["weights_sha256"]:
            raise ValueError("model_binding")
        anchored.verify_runtime_process_preflight(
            interpreter=interpreter, interpreter_sha256=interpreter_sha256,
            bootstrap=bootstrap, bootstrap_sha256=bootstrap_sha, bundle_root=bundle,
            deployment_manifest_sha256=deployment_manifest_sha256, verifier=verifier, verifier_sha256=verifier_sha,
            runtime_root=tuple_args["runtime-root"], runtime_manifest=runtime_manifest, runtime_manifest_sha256=runtime_sha,
            generation=tuple_args["runtime-generation"], revision=tuple_args["runtime-revision"])
        identity = importlib.import_module("tools.wd_mind_interpreter_identity")
        identity.verify_interpreter_identity(enabled=True, root=interpreter_root,
            descriptor_bytes=identity_bytes, expected_sha256=identity_sha,
            expected_interpreter=interpreter, expected_interpreter_sha256=interpreter_sha256)
        client_module = importlib.import_module("tools.wd_mind_e5_client")
        internal = dict(tuple_args, **{"mutex-sha256": mutex_sha})
        args = [str(_path(interpreter)), "-I", "-S", "-B", "-c", _TRAMPOLINE, str(bootstrap)]
        for key in (_KEYS if not worker_only else tuple(k for k in _KEYS if k not in ('memory','descriptor','descriptor-sha256'))):
            args.extend(("--" + key, internal[key]))
        if worker_only:
            args.append('--worker-only')
        args.extend(("--worker-sha256", worker_sha, "--weights-sha256", binding["encoder"]["weights_sha256"]))
        if bulk_contract_sha256 is not None:
            args.extend(('--bulk-contract-sha256', bulk_contract_sha256))
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        env.update(TEMP=os.devnull, TMP=os.devnull, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        for name in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "HF_ASSETS_CACHE",
                     "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "CUDA_CACHE_PATH"):
            env[name] = os.devnull
        if cooperative:
            lifecycle = importlib.import_module("tools.wd_mind_e5_lifecycle")
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
            if bulk_contract_sha256 is not None:
                if (lifecycle.BULK_CONTRACT != BULK_CONTRACT or lifecycle.BULK_CONTRACT_SHA256 != BULK_CONTRACT_SHA256
                        or lifecycle.BULK_STDOUT_BYTES != 64 * 1024 * 1024
                        or client_module.BULK_CONTRACT_SHA256 != BULK_CONTRACT_SHA256):
                    raise ValueError('bulk_contract_mismatch')
                owner = lifecycle.reserve(enabled=True, bulk_contract_sha256=bulk_contract_sha256)
            else:
                owner = lifecycle.reserve(enabled=True)
            if owner_sink is not None:
                owner_sink(owner)
            owner.resources.job = job = _Job(kill_on_close=False, _owner=owner)
            owner.set_job(job)
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
        else:
            job = _Job()
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE if cooperative else subprocess.DEVNULL,
                                   cwd=str(bootstrap.parent), env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        if cooperative:
            # Store the returned process before calling the fallible bind hook.
            owner.resources.process = process
            owner.bind(process)
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
        job.attach(process)
        if cooperative:
            owner.write(b"\0", deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("deadline")
            if bulk_contract_sha256 is not None:
                client = client_module.GracefulE5WorkerClient(owner, timeout=remaining,
                    encoder_binding_sha256=binding_sha, bulk_contract_sha256=bulk_contract_sha256)
            else:
                client = client_module.GracefulE5WorkerClient(owner, timeout=remaining,
                                                             encoder_binding_sha256=binding_sha)
            owner.mark_ready()
        else:
            process.stdin.write(b"\0")
            process.stdin.flush()
            client = client_module.E5WorkerClient(process, job, timeout=timeout,
                                               encoder_binding_sha256=binding_sha)
        result = {"client": client, "encoder_binding_bytes": binding_bytes, "encoder_binding_sha256": binding_sha,
                "authority": "none", "network_isolation": "UNKNOWN", "native_closure": "UNKNOWN",
                "same_user_race": "UNKNOWN", "source_binding": "NOT_PROVEN"}
        if cooperative:
            result.update(status="READY", owner=owner, lifecycle=owner.status())
            if bulk_contract_sha256 is not None:
                result['bulk_contract_sha256'] = bulk_contract_sha256
        return result
    except Exception:
        if cooperative:
            if owner is not None:
                status = owner.shutdown(timeout=0)
                return {"status": "HOLD" if status["owned"] else "REFUSED", "client": None,
                        "owner": owner, "lifecycle": status, "authority": "none"}
            raise LaunchRefused("mind_e5_start_refused") from None
        if job is not None:
            try:
                job.terminate()
                job.close()
            except Exception:
                pass
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
            except Exception:
                pass
    raise LaunchRefused("mind_e5_start_refused") from None
