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

The watchdog bounds the launched job after the process starts, including
bootstrap execution and retained stdout. Hash preflight, Windows process
creation and job assignment are synchronous and can block outside that bound;
this function does NOT prove a hard wall-clock bound for the whole call. A
separately supervised process with independently verified containment is needed
before claiming such a bound. Output is an intentionally lossy projection; even
hex digests and status values are data channels, not a full privacy guarantee.
"""

import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import stat
import tempfile
import threading
import time


class LaunchRefused(ValueError):
    """Fixed refusal code only; never include raw data or paths."""


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_REASON = re.compile(r"[a-z0-9_]{1,64}\Z")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|\+00:00)\Z")
_REF = re.compile(r"(?:[a-z][a-z0-9_-]{0,15}:)?[0-9a-f]{40,64}\Z")
_KEYS = ("runtime-root", "runtime-manifest", "runtime-manifest-sha256", "runtime-generation",
         "runtime-revision", "memory", "descriptor", "descriptor-sha256", "lock-file", "lock-timeout")
_PATHS = ("runtime-root", "runtime-manifest", "memory", "descriptor", "lock-file")
_MAX_QUERY = 65536
_MAX_OUTPUT = 262144
_SHARED_SCOPE = "wd.q4-shared-projected-events.v1"
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


def _scratch_dir(root, protected):
    """Create one unshared local scratch directory, without deleting anything.

    Caller authorizes the root; fixed drive/reparse checks do not authenticate
    ACLs and cannot prevent same-user replacement or filesystem TOCTOU.
    """
    if root is None:
        raise LaunchRefused("scratch_required")
    try:
        if os.name != "nt":
            raise ValueError()
        path = _path(root)
        if any(part in (".", "..") or part.endswith((" ", ".")) for part in path.parts[1:]):
            raise ValueError()
        drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
        drive_type.argtypes = (wintypes.LPCWSTR,)
        drive_type.restype = wintypes.UINT
        if drive_type(path.anchor) != 3:  # DRIVE_FIXED
            raise ValueError()
        current = path
        while True:
            info = os.lstat(current)
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or (
                    getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                raise ValueError()
            if current.parent == current:
                break
            current = current.parent
        scratch = os.path.normcase(os.path.realpath(str(path)))
        for target in protected:
            other = os.path.normcase(os.path.realpath(str(target)))
            try:
                common = os.path.commonpath((scratch, other))
            except ValueError:  # Different drives cannot overlap lexically.
                continue
            if common in (scratch, other):
                raise ValueError()
        child = Path(tempfile.mkdtemp(prefix="wd-q4-", dir=str(path)))
        info = os.lstat(child)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ValueError()
        return child
    except (OSError, TypeError, ValueError, LaunchRefused):
        raise LaunchRefused("scratch_refused") from None


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
    def __init__(self):
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
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
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
            self.api.CloseHandle(self.handle)
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


def launch(*, interpreter, interpreter_sha256, bootstrap, bootstrap_sha256, verifier_sha256,
           tuple_args, query, timeout, scratch_root=None):
    """Return only validated, reduced Q4 JSON; emit no raw stderr or traceback."""
    if scratch_root is None:
        raise LaunchRefused("scratch_required")
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
    if not _HEX.fullmatch(tuple_args["runtime-manifest-sha256"]) or not _HEX.fullmatch(tuple_args["descriptor-sha256"]):
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
            and _matches(bootstrap.with_name("runtime_manifest.py"), verifier_sha256)):
        raise LaunchRefused("trust_pin_mismatch")
    scratch = _scratch_dir(scratch_root, [bootstrap.parent, interpreter.parent] +
                           [_path(tuple_args[key]) for key in _PATHS])
    args = [str(interpreter), "-I", "-S", "-B", "-c", _TRAMPOLINE, str(bootstrap)]
    for key in _KEYS:
        args.extend(("--" + key, tuple_args[key]))
    started = time.monotonic()
    job, process = _Job(), None
    output, overflow = bytearray(), threading.Event()
    try:
        # Only OS location variables are passed; no PYTHON*, HF*, or caller secrets.
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        env.update(TEMP=str(scratch), TMP=str(scratch))
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
