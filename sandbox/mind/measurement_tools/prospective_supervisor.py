"""Dormant P3 phase supervisor for an ALREADY Medium Windows parent.

No privilege/token/launcher changes. No arbitrary command or discovery API.
Import is inert. Real CLI seams are unavailable; explicit FakeHost/Popen API
seams exist only for synthetic tests. Original publication/live GO authority
must be verified by the operator: supplied hashes are not signatures.

Only the frozen worker is launched, with pinned Python, exact environment,
private exclusive OS disk stdout/stderr, and its own fresh phase cache prefix.
Accounting/watchdog containment is cooperative, not an OS quota, descendant
process sandbox, DLL closure, or proof of an approved clean Medium route.
Private denotes role/path separation, not an OS confidentiality/ACL claim.
Failure is sticky; no adoption, retry, repair or process enumeration exists.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import shutil
import sys
import time
import types

PHASES = ("prepare", "prepass", "arms")
WORKER_SHA256 = "6511543f33c25f430eabd85b66b40ecc335b683827da7a7856ba7722ad80187d"
WORKER_PATH = Path("C:/Python/project2/sandbox/mind/measurement_tools/prospective_native.py")
PYTHON_PATH = Path("C:/Users/janik/AppData/Local/Programs/Python/Python313/python.exe")
CWD = Path("C:/Python/project2")
ERRORS = frozenset({"dormant", "arguments", "medium", "pins", "schema", "go", "binding", "path",
                    "startup", "consumed", "prior", "clock", "budget", "disk", "timeout", "write",
                    "child_failed", "supervisor_failed"})


class NotReady(Exception):
    def __init__(self, reason):
        self.reason = reason if type(reason) is str and reason in ERRORS else "supervisor_failed"
        super().__init__("NOT_READY:" + self.reason)


def need(ok, reason="schema"):
    if not ok:
        raise NotReady(reason)


def native_api():
    """Source-only import of EXACT frozen worker; never invoke its main/stages."""
    for item in (*reversed(WORKER_PATH.parents), WORKER_PATH):
        info = item.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 1024), "pins")
    before = WORKER_PATH.stat()
    need(stat.S_ISREG(before.st_mode) and not (getattr(before, "st_file_attributes", 0) & 1024), "pins")
    body = WORKER_PATH.read_bytes()
    after = WORKER_PATH.stat()
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
         and hashlib.sha256(body).hexdigest() == WORKER_SHA256, "pins")
    module = types.ModuleType("_p3_frozen_native_source")
    module.__file__ = str(WORKER_PATH)
    exec(compile(body, str(WORKER_PATH), "exec"), module.__dict__)
    need(module.fresh(WORKER_PATH)["sha256"] == WORKER_SHA256, "pins")
    return module


class Host:
    """Actual host methods. CLI always constructs this class, never a test seam."""
    def __init__(self, api):
        self.api, self.io = api, api.Host()
        self.forest = None
        self.peak_bytes = self.last_bytes = None

    def audit_root(self):
        return self.api.AUDIT

    def configure(self, protocol, c):
        """Only the three ORIGINAL protocol roots, never a caller/GO root list."""
        root, audit = self.io.path(c["root"]), self.audit_root()
        need(protocol.get("study_root") == c["root"] and audit in root.parents, "path")
        cfg = protocol.get("collection_controller")
        need(type(cfg) is dict, "path")
        need(self.io.path(cfg.get("audit_root", "")) == audit, "path")
        forest = [root]
        for key in ("control_root", "ledger_root"):
            path = self.io.path(cfg.get(key, ""))
            need(path.parent == audit, "path")
            forest.append(path)
        need(all(x != y and x not in y.parents and y not in x.parents
                 for i, x in enumerate(forest) for y in forest[i + 1:]), "path")
        areas = [self.io.path(cfg.get(key, ""), study=root)
                 for key in ("inbox", "baseline_root", "blind_root", "score_root")]
        need(all(x != y and x not in y.parents and y not in x.parents
                 for i, x in enumerate(areas) for y in areas[i + 1:]), "path")
        self.forest = tuple(forest)
        # Frozen writer retains its exclusive/fsync/hash checks, now with the
        # stronger outer shared-forest accounting, not a second study allowance.
        self.io.check_budget = self.budget

    def identity(self):
        return self.io.identity()

    def now(self):
        return self.io.now()

    def monotonic(self):
        return time.monotonic()

    def pause(self):
        time.sleep(.1)

    def child_identity(self, child):
        """Observe the returned owned process handle, never PID lookup/adoption."""
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetProcessId.argtypes = [wintypes.HANDLE]
        kernel.GetProcessId.restype = wintypes.DWORD
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        handle = wintypes.HANDLE(int(child._handle))
        need(kernel.GetProcessId(handle) == child.pid, "child_failed")
        values = [wintypes.FILETIME() for _ in range(4)]
        need(bool(kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values))), "child_failed")
        return {"pid": child.pid, "creation_filetime_100ns":
                str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)}

    def read(self, ref):
        return self.io.read(ref)

    def budget(self, root, extra=0):
        used = self.account(root, extra)
        need(shutil.disk_usage(root).free >= self.api.FLOOR + self.api.BUDGET, "disk")
        return used

    def account(self, root, extra=0):
        need(self.forest is not None and self.forest[0] == root, "path")
        used = 0
        for tree in self.forest:
            # Validate every existing ancestor even when a future root is absent.
            self.io.path(str(tree))
            if not tree.exists():
                continue
            need(stat.S_ISDIR(self.api.plain(tree).st_mode), "path")
            for directory, dirs, files in os.walk(tree, followlinks=False):
                self.api.plain(directory)
                for name in dirs + files:
                    info = self.api.plain(Path(directory) / name)
                    if stat.S_ISREG(info.st_mode):
                        need(info.st_nlink == 1, "path")
                        used += info.st_size
                    else:
                        need(stat.S_ISDIR(info.st_mode), "path")
        need(used + extra <= self.api.BUDGET, "budget")
        self.last_bytes = used
        self.peak_bytes = max(used, self.peak_bytes or 0)
        return used

    def write(self, root, path, body, *, terminal=False):
        return self.io.write(root, path, body, terminal=terminal)

    def preflight(self, c, phase, identity):
        a = self.api
        root = self.io.path(c["root"])
        need(Path.cwd().absolute() == CWD and a.AUDIT in root.parents and root.is_dir(), "path")
        # Reuse only the frozen startup's ownership/filesystem predicates, not
        # startup itself: the WORKER cache and native directories must stay fresh.
        import ctypes
        fs = ctypes.create_unicode_buffer(32)
        kernel = ctypes.WinDLL("kernel32")
        need(kernel.GetDriveTypeW(ctypes.c_wchar_p("C:\\")) == 3 and kernel.GetVolumeInformationW(
             ctypes.c_wchar_p("C:\\"), None, 0, None, None, None, fs, len(fs)) and fs.value == "NTFS", "path")
        need(sys.flags.isolated == sys.flags.no_site == sys.flags.dont_write_bytecode == 1
             and sys.dont_write_bytecode is True and Path(sys.executable) == PYTHON_PATH, "startup")
        # The supervisor and child must not share the worker's absent prefix.
        prefix = root / "supervisor" / phase / "pycache"
        need(sys.pycache_prefix == str(prefix) and sys._xoptions.get("pycache_prefix") == str(prefix)
             and not prefix.exists(), "startup")
        need(self.io.path(c["python"]["executable"]) == PYTHON_PATH
             and identity.get("executable") == str(PYTHON_PATH)
             and a.fresh(PYTHON_PATH)["sha256"] == a.PYTHON_SHA256, "pins")
        need(self.io.path(c["worker"]["path"]) == WORKER_PATH
             and c["worker"]["sha256"] == WORKER_SHA256
             and a.fresh(WORKER_PATH)["sha256"] == WORKER_SHA256, "pins")
        owner = a.json_bytes(self.read(c["owner"]))
        need(owner == {"schema": "wd.m1-p3-study-root.v1", "study_id": c["study_id"],
                       "protocol_sha256": c["protocol"]["sha256"], "root": c["root"]}, "binding")
        need(self.io.path(c["owner"]["path"], study=root).parent == root, "path")
        self.budget(root, a.RESERVE)

    def paths(self, c):
        root = self.io.path(c["root"])
        outputs = []
        for phase in PHASES:
            for channel in ("stdout", "stderr"):
                p = self.io.path(c["stdio_paths"][phase][channel], study=root)
                need(p.parent == root / "private_stdio", "path")
                outputs.append(p)
        need(len(set(outputs)) == 6, "path")
        # Metadata refs are never allowed to alias output/phase intent paths.
        for ref in (c["protocol"], c["owner"], c["worker"]):
            need(self.io.path(ref["path"]) not in outputs, "path")
        return root

    def exclusive_stdio(self, path):
        self.io.path(str(path))
        self.api.plain(path.parent)
        stream = path.open("xb", buffering=0)
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "path")
        return stream

    def stdio_receipt(self, path, original):
        info = self.api.plain(path)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
             and (info.st_dev, info.st_ino) == original, "path")
        receipt = self.api.fresh(path)
        return {"sha256": receipt["sha256"], "bytes": receipt["bytes"]}


def validate(a, host, c, go, manifest_sha256, phase):
    a.keys(go, ("schema", "status", "study_id", "protocol_sha256", "manifest_sha256", "phase", "authorizer",
                "event_ref", "issued_at_utc", "expires_at_utc", "prior_receipts", "commitments_sha256"))
    need(go["schema"] == "wd.m1-p3-native-go.v1" and go["status"] == "NATIVE_PHASE_GO"
         and go["study_id"] == c["study_id"] and go["protocol_sha256"] == c["protocol"]["sha256"]
         and go["manifest_sha256"] == manifest_sha256 and go["phase"] == phase
         and a.hex64(go["event_ref"]), "go")
    protocol = a.json_bytes(host.read(c["protocol"]))
    need(protocol.get("schema") == "wd.m1-p3-protocol-lock.v1" and protocol.get("status") == "LOCKED"
         and protocol.get("study_id") == c["study_id"]
         and protocol.get("native_authorizer") == go["authorizer"], "go")
    a.keys(go["authorizer"], ("agent", "agent_uuid", "session_id"))
    # This pin is in the ORIGINAL protocol, never supplied by a mutable CLI flag.
    a.keys(protocol.get("native_supervisor"), ("path", "sha256"))
    need(protocol["native_supervisor"]["path"] == str(Path(__file__).absolute())
         and a.fresh(Path(__file__))["sha256"] == protocol["native_supervisor"]["sha256"], "pins")
    issued, expires = a.utc(go["issued_at_utc"]), a.utc(go["expires_at_utc"])
    need(issued <= host.now() < expires and expires - issued <= dt.timedelta(seconds=a.TIMEOUT)
         and a.utc(c["collection_end_utc"]) <= issued, "go")
    prior = PHASES[:PHASES.index(phase)]
    need(type(go["prior_receipts"]) is dict and set(go["prior_receipts"]) == set(prior), "prior")
    need(a.hex64(go["commitments_sha256"]) if phase == "arms" else go["commitments_sha256"] is None, "go")
    host.configure(protocol, c)
    return expires, prior


def stop_owned(child):
    """Never search for or stop another process; only this returned Popen handle."""
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def launch(manifest_path, manifest_sha256, go_path, go_sha256, *, phase,
           execute=False, _test_host=None, _test_popen=None):
    """One consumed launch. Test seams are explicit API-only, never CLI options.

    No input bodies are accepted: the parent identity gate runs BEFORE reading
    even the metadata manifest/GO. A failed child with no bound worker receipt
    leaves actual_data_opened UNKNOWN rather than falsely reporting no data.
    """
    consumed = started = timed_out = False
    opened = False
    child = host = a = root = stage = None
    streams, stdio, originals = {}, {}, {}
    start_utc = None
    result_ref = child_identity = None
    try:
        need(execute is True, "dormant")
        need(phase in PHASES and (_test_host is None) == (_test_popen is None), "arguments")
        a = native_api()
        host = Host(a) if _test_host is None else _test_host
        identity = host.identity()  # FIRST host action; before any private input/IO.
        need(type(identity) is dict and type(identity.get("integrity_rid")) is int
             and identity.get("integrity_rid") == 8192 and identity.get("elevated") is False
             and type(identity.get("pid")) is int and identity["pid"] > 0
             and type(identity.get("creation_filetime_100ns")) is str
             and identity["creation_filetime_100ns"].isascii() and identity["creation_filetime_100ns"].isdigit()
             and int(identity["creation_filetime_100ns"]) > 0 and identity.get("ABI_bits") == 64
             and identity.get("executable") == str(PYTHON_PATH)
             and type(identity.get("python")) is dict
             and identity["python"].get("sha256") == a.PYTHON_SHA256, "medium")
        c = a.manifest_from_bytes(host.read({"path": manifest_path, "sha256": manifest_sha256}), manifest_sha256)
        go = a.json_bytes(host.read({"path": go_path, "sha256": go_sha256}), go_sha256)
        expires, prior = validate(a, host, c, go, manifest_sha256, phase)
        host.preflight(c, phase, identity)
        root = host.paths(c)
        need(all(type(k) is str and type(v) is str and "\x00" not in k and "\x00" not in v
                 for k, v in c["environment"].items()), "schema")
        need(c["worker"]["sha256"] == WORKER_SHA256 and Path(c["worker"]["path"]) == WORKER_PATH
             and Path(c["python"]["executable"]) == PYTHON_PATH, "pins")
        native, supervisory = root / "native", root / "supervisor"
        need(not (supervisory / "HALTED.json").exists() and not (native / "HALTED.json").exists(), "consumed")
        if phase == "prepare":
            need(not native.exists() and not supervisory.exists(), "consumed")
            supervisory.mkdir(exist_ok=False)
        else:
            need(native.is_dir() and supervisory.is_dir(), "prior")
            for previous in prior:
                prior_path = supervisory / previous / "RESULT.json"
                old = a.json_bytes(host.read({"path": str(prior_path), "sha256": a.fresh(prior_path)["sha256"]}))
                need(old.get("status") == "COMPLETE" and old.get("phase") == previous
                     and old.get("manifest_sha256") == manifest_sha256
                     and old.get("study_id") == c["study_id"]
                     and old.get("worker_result_sha256") == go["prior_receipts"][previous], "prior")
        stage = supervisory / phase
        stage.mkdir(exist_ok=False)
        consumed = True
        intent = {"schema": "wd.m1-p3-supervisor-intent.v1", "study_id": c["study_id"], "phase": phase,
                  "manifest_sha256": manifest_sha256, "go_sha256": go_sha256,
                  "protocol_sha256": c["protocol"]["sha256"], "invocation_consumed": True,
                  "child_started": False, "actual_data_opened": False, "parent_identity": identity}
        host.write(root, stage / "INTENT.json", a.canon(intent))
        private = root / "private_stdio"
        private.mkdir(exist_ok=True)
        for channel in ("stdout", "stderr"):
            path = Path(c["stdio_paths"][phase][channel])
            stream = host.exclusive_stdio(path)
            streams[channel] = stream
            info = os.fstat(stream.fileno())
            originals[channel] = info.st_dev, info.st_ino
        host.budget(root, a.RESERVE)
        need(host.now() < expires, "timeout")
        need(not (native / phase).exists() and not (native / phase / "pycache").exists(), "consumed")
        need(phase != "prepare" or not native.exists(), "consumed")
        argv = [str(PYTHON_PATH), "-I", "-B", "-S", "-X", "pycache_prefix=" + str(native / phase / "pycache"),
                str(WORKER_PATH), "--execute", "--manifest", str(manifest_path), "--manifest-sha256", manifest_sha256,
                "--go", str(go_path), "--go-sha256", go_sha256, "--phase", phase]
        # No inherited secrets, PATH, PYTHONPATH, or injection variables. Worker
        # adds only its own shared-study TEMP/TMP during native context entry.
        env = dict(c["environment"])
        env.update(TEMP=str(stage), TMP=str(stage))
        popen = subprocess.Popen if _test_popen is None else _test_popen
        started_at = host.monotonic()
        start_utc = host.now().isoformat(timespec="microseconds").replace("+00:00", "Z")
        child = popen(argv, stdin=subprocess.DEVNULL, stdout=streams["stdout"], stderr=streams["stderr"],
                      shell=False, cwd=str(CWD), env=env, close_fds=True)
        started, opened = True, "UNKNOWN"
        need(type(child.pid) is int and child.pid > 0, "child_failed")
        child_identity = host.child_identity(child)
        host.write(root, stage / "STARTED.json", a.canon({"schema": "wd.m1-p3-supervisor-started.v1",
                   "phase": phase, "child_identity": child_identity, "started_at_utc": start_utc,
                   "manifest_sha256": manifest_sha256, "go_sha256": go_sha256}))
        while child.poll() is None:
            if host.monotonic() - started_at >= a.TIMEOUT or host.now() >= expires:
                timed_out = True
                raise NotReady("timeout")
            host.budget(root, a.RESERVE)
            host.pause()
        need(child.wait(timeout=0) == 0, "child_failed")
        host.budget(root, a.RESERVE)
        need(host.now() < expires and host.monotonic() - started_at < a.TIMEOUT, "timeout")
        worker_result = native / phase / "RESULT.json"
        result_ref = a.fresh(worker_result)
        worker = a.json_bytes(host.read({"path": str(worker_result), "sha256": result_ref["sha256"]}))
        need(worker.get("schema") == "wd.m1-p3-native-phase.v1" and worker.get("status") == "COMPLETE"
             and worker.get("study_id") == c["study_id"] and worker.get("phase") == phase
             and worker.get("manifest_sha256") == manifest_sha256 and worker.get("go_sha256") == go_sha256
             and worker.get("invocation_consumed") is True and worker.get("actual_data_opened") is True, "binding")
        native_identity_path = native / phase / "IDENTITY.json"
        native_identity = a.json_bytes(host.read({"path": str(native_identity_path),
                                                 "sha256": a.fresh(native_identity_path)["sha256"]}))
        need(native_identity.get("pid") == child.pid and native_identity.get("integrity_rid") == 8192
             and native_identity.get("elevated") is False
             and native_identity.get("python", {}).get("sha256") == a.PYTHON_SHA256
             and native_identity.get("creation_filetime_100ns") == child_identity["creation_filetime_100ns"], "medium")
        opened = True
        # Recheck ORIGINAL metadata, not an adopted worker-produced replacement.
        for ref in ({"path": manifest_path, "sha256": manifest_sha256},
                    {"path": go_path, "sha256": go_sha256}, c["protocol"], c["owner"], c["worker"]):
            host.read(ref)
        status, reason = "COMPLETE", None
    except BaseException as error:
        status = "NOT_READY"
        reason = getattr(error, "reason", None)
        reason = reason if type(reason) is str and reason in ERRORS else "consumed" if type(error) is FileExistsError else "supervisor_failed"
    finally:
        if child is not None:
            try:
                stop_owned(child)
            except BaseException:
                status, reason = "NOT_READY", "child_failed"
        for channel, stream in streams.items():
            try:
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()
                stdio[channel] = host.stdio_receipt(Path(c["stdio_paths"][phase][channel]), originals[channel])
            except BaseException:
                status, reason = "NOT_READY", "write"
    public = {"status": status, "phase": phase if phase in PHASES else None,
              "invocation_consumed": consumed, "child_started": started, "actual_data_opened": opened}
    if reason is not None:
        public["reason"] = reason
    if consumed:
        receipt = {"schema": "wd.m1-p3-supervisor-phase.v1", **public, "study_id": c["study_id"],
                   "manifest_sha256": manifest_sha256, "go_sha256": go_sha256,
                   "protocol_sha256": c["protocol"]["sha256"], "worker_sha256": WORKER_SHA256,
                   "pid": child.pid if child is not None else None, "started_at_utc": start_utc,
                   "child_identity": child_identity,
                   "returncode": child.poll() if child is not None else None, "timed_out": timed_out,
                   "stdio": stdio, "worker_result_sha256": result_ref["sha256"] if result_ref else None,
                   "budget": {"roots": [str(p) for p in host.forest], "limit_bytes": a.BUDGET,
                              "last_accounted_bytes": host.last_bytes, "peak_accounted_bytes": host.peak_bytes,
                              "free_floor_bytes": a.FLOOR, "reserve_bytes": a.RESERVE},
                   "containment": "COOPERATIVE_NOT_OS_ISOLATION", "DLL_closure": "UNKNOWN"}
        try:
            if status != "COMPLETE":
                host.write(root, root / "supervisor/HALTED.json", a.canon(receipt), terminal=True)
            ref = host.write(root, stage / "RESULT.json", a.canon(receipt), terminal=True)
            public["result_sha256"] = ref["sha256"]
        except BaseException:
            public["status"], public["reason"] = "NOT_READY", "write"
            # The exclusive consumed stage/INTENT remains even if disk is full.
    return public


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise NotReady("arguments")


def main(argv=None):
    try:
        parser = Parser(add_help=False)
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--phase", choices=PHASES, required=True)
        for name in ("manifest", "manifest-sha256", "go", "go-sha256"):
            parser.add_argument("--" + name, required=True)
        args = parser.parse_args(argv)
        result = launch(args.manifest, args.manifest_sha256, args.go, args.go_sha256,
                        phase=args.phase, execute=args.execute)
    except BaseException:
        result = {"status": "NOT_READY", "reason": "arguments", "invocation_consumed": False,
                  "child_started": False, "actual_data_opened": False}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
