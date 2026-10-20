"""Dormant P3 native phase worker. Import is inert; CLI requires explicit GO.

Runs only prepare/prepass/arms from a NEW original P3 manifest and phase GO.
Never reads nominations, sealed cases, gold labels or nonces; never imports a
grader. The SAFE BlindCase bytes are identical for prepass and arms, with kept
commitments a subset. One immutable baseline is encoded ONCE into shared memory.
The old pinned runner/factory/comparator and SUT corpus/encode/memory APIs are
unchanged. Source predicates or missing prerequisites refuse, never fall back.

Launch with pinned Python -I -B -S -X pycache_prefix=<fresh phase>/pycache.
The caller must verify original publication and live phase-GO origin; supplied
hashes/typed identities are integrity bindings, not signatures or authority.
Medium RID8192/nonElevated is checked INSIDE the worker BEFORE private inputs or
SUT imports. Invocation consumption and actual_data_opened are separate facts.

Whole-Python manifest pinning runs once per worker invocation; actual module
files are observed before/after native stages. Fileless modules stay UNKNOWN.
This is cooperative file/budget containment, NOT OS isolation, DLL closure,
hard disk quota, cache/process sandboxing or a medium-token launcher. Root must
supply an approved actual Medium execution route. No diagnostic/model runs here.
Production also requires actual OS stdout/stderr to be distinct, declared
private local disk files opened by that supervisor before startup. Terminal or
pipe output refuses before native effects, including C-level output. This file
does not manufacture those handles or claim their creation was exclusive.

Public output contains fixed status/count/hash fields only. Helper stdout/stderr
are hashed without retaining bodies. Failed/partial phases stay consumed and
halt subsequent phases. There is no retry/adopt/reset/repair API. Test injection
is explicit API-only and unavailable from CLI; tests never load the real SUT.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
from ctypes import wintypes
import datetime as dt
import gzip
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import threading
import time
import types


PHASES = ("prepare", "prepass", "arms")
MEASURED_COMMIT = "6cf81dbbefe2b0e19a4c8306100beb30784ac4e4"
RUNNER_SHA256 = "2011c5dca6b5e8f8880a7117911136d446faf5fa7d87faf24dbb6c032408b862"
BM25_SHA256 = "a1667a27da015dcaed0f97960bbede3281dab3540018106d2458879db213579a"
OBSERVER_SOURCE_SHA256 = "6023134566de862aee637e24b592b20f02be39e2a07cef01814c1242f753bb4b"
PYTHON_SHA256 = "d932e5e2f324d57f392e8fd063dcf6d0185be8a664c57c6d24e7762ed02c28ca"
PYTHON_MANIFEST_SHA256 = "125c45e3910ece276c6cf6122c2a58be70ae9d1b92a39a63e6567bf8e2acf771"
PYTHON_RECEIPT_SHA256 = "8c0c7c4c245193060e79d3e385ea9633614cb83409f12ac7cdad759180f8154d"
PYTHON_ROOT_SHA256 = "4c18a3d91c99a448b94cefb32d39fbf89158a1f452eaf166537b209a1f1f9306"
CACHE_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
AUDIT = Path("C:/Python/project2/.codex-audit")
BUDGET = 64 * 1024**2
FLOOR = 3 * 1024**3
RESERVE = 256 * 1024
TIMEOUT = 1800
ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "FAISS_OPT_LEVEL": "GENERIC",
       "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
       "NUMEXPR_NUM_THREADS": "1", "PYTHONNOUSERSITE": "1", "TOKENIZERS_PARALLELISM": "false"}
ERRORS = frozenset({"dormant", "schema", "go", "binding", "path", "pins", "startup", "medium",
                    "consumed", "prior", "clock", "snapshot", "budget", "disk", "timeout",
                    "write", "modules", "native_failed", "arguments"})
HEX = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[0-9a-f]{32}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.md\Z", re.ASCII)
UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z", re.ASCII)


class NotReady(Exception):
    def __init__(self, reason):
        self.reason = reason if type(reason) is str and reason in ERRORS else "native_failed"
        super().__init__("NOT_READY:" + self.reason)


def need(ok, reason="schema"):
    if not ok:
        raise NotReady(reason)


def sha(body):
    return hashlib.sha256(body).hexdigest()


def canon(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def digest(value):
    return sha(canon(value))


def hex64(value):
    return type(value) is str and HEX.fullmatch(value) is not None


def keys(value, fields):
    need(type(value) is dict and set(value) == set(fields))


def pairs(items):
    out = {}
    for key, value in items:
        need(key not in out)
        out[key] = value
    return out


def json_bytes(body, expected=None):
    need(type(body) is bytes and 0 < len(body) <= BUDGET, "budget")
    if expected is not None:
        need(hex64(expected) and sha(body) == expected, "binding")
    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(NotReady("schema")))
    except (UnicodeError, ValueError, RecursionError):
        raise NotReady("schema") from None


def utc(value):
    need(type(value) is str and UTC.fullmatch(value), "clock")
    try:
        return dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise NotReady("clock") from None


def plain(path):
    path = Path(path)
    for item in (*reversed(path.parents), path):
        info = item.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 1024), "path")
    return info


def file_key(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def fresh(path):
    """Approved observer's stat/open/read/stat identity check; content is not emitted."""
    path = Path(path)
    before = plain(path)
    need(stat.S_ISREG(before.st_mode), "path")
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        need(file_key(os.fstat(stream.fileno())) == file_key(before), "pins")
        for chunk in iter(lambda: stream.read(4 * 1024**2), b""):
            checksum.update(chunk)
        need(file_key(os.fstat(stream.fileno())) == file_key(before), "pins")
    after = plain(path)
    need(file_key(before) == file_key(after), "pins")
    return {"sha256": checksum.hexdigest(), "bytes": before.st_size,
            "before": list(file_key(before)), "after": list(file_key(after))}


def token_identity():
    """Copied from hash-verified approved60231345 observer; no privilege change."""
    need(os.name == "nt", "medium")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    advapi.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    advapi.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    handle = wintypes.HANDLE()
    need(bool(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(handle))), "medium")
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(handle, 25, None, 0, ctypes.byref(size))
        need(0 < size.value < 65536, "medium")
        buffer = ctypes.create_string_buffer(size.value)
        need(bool(advapi.GetTokenInformation(handle, 25, buffer, size, ctypes.byref(size))), "medium")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        count = advapi.GetSidSubAuthorityCount(sid)[0]
        need(1 <= count <= 15, "medium")
        rid = int(advapi.GetSidSubAuthority(sid, count - 1)[0])
        elevated, used = wintypes.DWORD(), wintypes.DWORD()
        need(bool(advapi.GetTokenInformation(handle, 20, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(used))), "medium")
        times = [wintypes.FILETIME() for _ in range(4)]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        need(bool(kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(value) for value in times))), "medium")
        return {"pid": os.getpid(), "creation_filetime_100ns": str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime),
                "integrity_rid": rid, "elevated": bool(elevated.value), "executable": sys.executable,
                "python": fresh(Path(sys.executable)), "ABI_bits": ctypes.sizeof(ctypes.c_void_p) * 8}
    finally:
        need(bool(kernel.CloseHandle(handle)), "medium")


def manifest_from_bytes(body, expected):
    c = json_bytes(body, expected)
    keys(c, ("schema", "study_id", "protocol", "worker", "root", "owner", "python", "source_root",
             "source_files", "package_roots", "cache_root", "cache_files", "environment", "binding",
             "helpers", "inputs", "stdio_paths", "corpus_as_of_utc", "collection_start_utc", "collection_end_utc"))
    need(c["schema"] == "wd.m1-p3-native-manifest.v1" and type(c["study_id"]) is str and ID.fullmatch(c["study_id"]))
    for field in ("protocol", "worker", "owner"):
        keys(c[field], ("path", "sha256"))
        need(hex64(c[field]["sha256"]), "binding")
    keys(c["python"], ("executable", "sha256", "root", "manifest_path", "manifest_sha256", "receipt_path", "receipt_sha256", "root_sha256"))
    need(tuple(c["python"][key] for key in ("sha256", "manifest_sha256", "receipt_sha256", "root_sha256"))
         == (PYTHON_SHA256, PYTHON_MANIFEST_SHA256, PYTHON_RECEIPT_SHA256, PYTHON_ROOT_SHA256), "pins")
    keys(c["helpers"], ("runner", "comparator"))
    for name, expected_sha in (("runner", RUNNER_SHA256), ("comparator", BM25_SHA256)):
        keys(c["helpers"][name], ("path", "sha256"))
        need(c["helpers"][name]["sha256"] == expected_sha, "pins")
    for group in ("source_files", "cache_files"):
        need(type(c[group]) is dict and bool(c[group]) and all(type(p) is str and hex64(h) for p, h in c[group].items()), "pins")
    binding = c["binding"]
    need(type(binding) is dict and binding.get("source_commit") == MEASURED_COMMIT
         and binding.get("source_files") == c["source_files"] and type(binding.get("encoder")) is dict, "pins")
    for key, expected_rule in (("guard_band_s", 1.0), ("bm25_k1", 1.2), ("bm25_b", .75)):
        need(type(binding.get("rule", {}).get(key)) in (int, float) and binding["rule"][key] == expected_rule, "pins")
    for key, value in {"revision": CACHE_REVISION, "device": "cpu", "torch_threads": 1, "dim": 384,
                       "prefix_passage": "passage: ", "prefix_query": "query: ", "truncate_chars": 2000,
                       "passage_batch": 32, "query_batch": 1, "normalize": True, "dtype": "float32"}.items():
        need(binding["encoder"].get(key) == value, "pins")
    need(type(c["environment"]) is dict and all(c["environment"].get(k) == v for k, v in ENV.items()), "pins")
    need(set(c["environment"]) == set(ENV) | {"USERPROFILE", "HF_HOME", "SystemRoot"}, "pins")
    need(type(c["package_roots"]) is list and 0 < len(c["package_roots"]) <= 64, "pins")
    keys(c["inputs"], ("runner_cases", "snapshot", "baseline_manifest", "baseline_seal", "baseline_blobs", "commitments_path"))
    for name in ("runner_cases", "snapshot", "baseline_manifest", "baseline_seal"):
        keys(c["inputs"][name], ("path", "sha256"))
        need(hex64(c["inputs"][name]["sha256"]), "binding")
    need(type(c["inputs"]["baseline_blobs"]) is dict and bool(c["inputs"]["baseline_blobs"]), "snapshot")
    keys(c["stdio_paths"], PHASES)
    for paths in c["stdio_paths"].values():
        keys(paths, ("stdout", "stderr"))
        need(type(paths["stdout"]) is str and type(paths["stderr"]) is str and paths["stdout"] != paths["stderr"], "startup")
    need(utc(c["corpus_as_of_utc"]) < utc(c["collection_start_utc"]) < utc(c["collection_end_utc"]), "clock")
    return c


class Host:
    """Actual Windows host. No injected identity, observer or IO on the CLI."""
    def now(self):
        return dt.datetime.now(dt.timezone.utc)

    def path(self, value, *, study=None):
        p = Path(value)
        need(p.is_absolute() and p.drive.casefold() == "c:" and not str(value).startswith(("\\\\", "//"))
             and all(x not in (".", "..") and ":" not in x and not x.endswith((".", " ")) for x in p.parts[1:]), "path")
        if study is not None:
            need(p != study and study in p.parents, "path")
        for item in (p, *p.parents):
            if item.exists() or item.is_symlink():
                plain(item)
        return p

    def read(self, ref):
        p = self.path(ref["path"])
        info = fresh(p)
        need(info["sha256"] == ref["sha256"] and info["bytes"] <= BUDGET, "binding")
        data = p.read_bytes()
        need(len(data) == info["bytes"] and sha(data) == ref["sha256"], "binding")
        return data

    def private_read(self, ref):
        """Set the observation only after an actual private file handle opens."""
        p = self.path(ref["path"])
        before = plain(p)
        need(stat.S_ISREG(before.st_mode) and before.st_size <= BUDGET, "path")
        with p.open("rb") as stream:
            self.actual_data_opened = True
            need(file_key(os.fstat(stream.fileno())) == file_key(before), "binding")
            body = stream.read(BUDGET + 1)
            need(file_key(os.fstat(stream.fileno())) == file_key(before), "binding")
        need(len(body) <= BUDGET and file_key(plain(p)) == file_key(before)
             and hex64(ref["sha256"]) and sha(body) == ref["sha256"], "binding")
        return body

    def startup(self, c, phase):
        root = self.path(c["root"])
        need(Path.cwd().absolute() == AUDIT.parent and root != AUDIT and AUDIT in root.parents and root.is_dir(), "path")
        fs = ctypes.create_unicode_buffer(32)
        need(os.name == "nt", "path")
        kernel = ctypes.WinDLL("kernel32")
        kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        need(kernel.GetDriveTypeW("C:\\") == 3 and kernel.GetVolumeInformationW(
             ctypes.c_wchar_p("C:\\"), None, 0, None, None, None, fs, len(fs)) and fs.value == "NTFS", "path")
        need(sys.flags.isolated == sys.flags.no_site == sys.flags.dont_write_bytecode == 1
             and sys.dont_write_bytecode is True, "startup")
        prefix = root / "native" / phase / "pycache"
        need(sys.pycache_prefix == str(prefix) and sys._xoptions.get("pycache_prefix") == str(prefix)
             and not prefix.exists(), "startup")
        owner = json_bytes(self.read(c["owner"]))
        need(owner == {"schema": "wd.m1-p3-study-root.v1", "study_id": c["study_id"],
                       "protocol_sha256": c["protocol"]["sha256"], "root": c["root"]}, "binding")
        need(self.path(c["owner"]["path"], study=root).parent == root, "path")
        self.check_budget(root)

    def identity(self):
        return token_identity()

    def private_stdio(self, c, phase):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetFileType.argtypes = [wintypes.HANDLE]
        kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        roots = []
        for channel, number in (("stdout", -11), ("stderr", -12)):
            expected = self.path(c["stdio_paths"][phase][channel], study=Path(c["root"]))
            handle = kernel.GetStdHandle(wintypes.DWORD(number & 0xffffffff))
            need(handle and handle != ctypes.c_void_p(-1).value and kernel.GetFileType(handle) == 1, "startup")
            buffer = ctypes.create_unicode_buffer(32768)
            length = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
            need(0 < length < len(buffer) and buffer.value.casefold() == ("\\\\?\\" + str(expected)).casefold(), "startup")
            info = plain(expected)
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "startup")
            roots.append(expected)
        need(roots[0] != roots[1], "startup")

    def check_budget(self, root, extra=0):
        used = 0
        for p in root.rglob("*"):
            info = plain(p)
            if stat.S_ISREG(info.st_mode):
                need(info.st_nlink == 1, "path")
                used += info.st_size
        need(used + extra <= BUDGET, "budget")
        need(shutil.disk_usage(root).free >= FLOOR + BUDGET, "disk")
        return used

    def write(self, root, path, body, *, terminal=False):
        need(type(body) is bytes, "write")
        self.path(str(path), study=root)
        self.check_budget(root, len(body) + (0 if terminal else RESERVE))
        plain(path.parent)
        with path.open("xb") as stream:
            need(stream.write(body) == len(body), "write")
            stream.flush()
            os.fsync(stream.fileno())
        need(fresh(path)["sha256"] == sha(body) and path.read_bytes() == body, "write")
        return {"sha256": sha(body), "bytes": len(body)}

    def pin_all(self, c):
        python = c["python"]
        need(Path(sys.executable) == self.path(python["executable"])
             and fresh(Path(sys.executable))["sha256"] == PYTHON_SHA256, "pins")
        need(fresh(self.path(c["worker"]["path"]))["sha256"] == c["worker"]["sha256"]
             and self.path(c["worker"]["path"]) == Path(__file__).absolute(), "pins")
        compressed = self.read({"path": python["manifest_path"], "sha256": PYTHON_MANIFEST_SHA256})
        receipt = json_bytes(self.read({"path": python["receipt_path"], "sha256": PYTHON_RECEIPT_SHA256}))
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            body = stream.read(BUDGET + 1)
        need(len(body) <= BUDGET and sha(body) == PYTHON_ROOT_SHA256 and receipt.get("complete") is True
             and receipt.get("root_sha256") == PYTHON_ROOT_SHA256, "pins")
        rows = [json_bytes(line) for line in body.splitlines()]
        root = self.path(python["root"])
        need(rows[0].get("schema") == "wd.python-whole-root.v1" and Path(rows[0]["root"]) == root
             and rows[-1].get("complete") is True and rows[-1].get("count") == 62958
             and rows[-1].get("total_file_bytes") == 7867124414 and not rows[-1].get("errors"), "pins")
        expected = {}
        for row in rows[1:-1]:
            need(row.get("status") == "HASHED" and type(row.get("path")) is str and hex64(row.get("sha256")), "pins")
            rel = Path(row["path"])
            need(not rel.is_absolute() and ".." not in rel.parts and rel.as_posix() not in expected, "pins")
            expected[rel.as_posix()] = row["sha256"]
        need(len(expected) == 62958, "pins")
        actual = set()
        for p in root.rglob("*"):
            info = plain(p)
            if stat.S_ISREG(info.st_mode):
                name = p.relative_to(root).as_posix()
                need(name in expected and fresh(p)["sha256"] == expected[name], "pins")
                actual.add(name)
        need(actual == set(expected), "pins")
        for group in ("source_files", "cache_files"):
            for name, wanted in c[group].items():
                need(fresh(self.path(name))["sha256"] == wanted, "pins")
        for ref in c["helpers"].values():
            need(fresh(self.path(ref["path"]))["sha256"] == ref["sha256"], "pins")
        source = self.path(c["source_root"])
        need(not source.is_relative_to(Path(c["root"])) and not root.is_relative_to(Path(c["root"]))
             and all(self.path(name).is_relative_to(source) for name in c["source_files"]), "pins")
        need(all(self.path(p).is_relative_to(root) and self.path(p).is_dir() for p in c["package_roots"]), "pins")
        need(len({str(self.path(p)).casefold() for p in c["package_roots"]}) == len(c["package_roots"]), "pins")
        profile = self.path(c["environment"]["USERPROFILE"])
        cache = self.path(c["cache_root"])
        need(cache == profile / ".cache/huggingface/hub/models--intfloat--multilingual-e5-small"
             and c["environment"]["HF_HOME"] == str(cache.parent.parent), "pins")
        ref = cache / "refs/main"
        need(str(ref) in c["cache_files"] and ref.read_text(encoding="ascii").strip() == CACHE_REVISION, "pins")
        return expected

    def observe(self, c, runtime):
        """Actual sys.modules membership; adapted approved60231345 observer."""
        membership = tuple(sys.modules.items())
        rows, rejected = [], []
        python_root = Path(c["python"]["root"])
        external = dict(c["source_files"])
        external.update({ref["path"]: ref["sha256"] for ref in c["helpers"].values()})
        external[c["worker"]["path"]] = c["worker"]["sha256"]
        for name, module in membership:
            row = {"name": name, "status": "UNKNOWN"}
            if module is None:
                row["status"] = "NONE_MODULE_ENTRY"
            elif not any(base is types.ModuleType for base in type.__dict__["__mro__"].__get__(type(module))):
                row["status"] = "NON_MODULE_ENTRY"
            else:
                fields = types.ModuleType.__dict__["__dict__"].__get__(module)
                file, spec = fields.get("__file__"), fields.get("__spec__")
                spec_fields = object.__getattribute__(spec, "__dict__") if type(spec) is importlib.machinery.ModuleSpec else None
                origin = spec_fields.get("origin") if type(spec_fields) is dict else None
                invalid = (spec is not None and type(spec) is not importlib.machinery.ModuleSpec) or (spec_fields is not None and
                    (type(spec_fields.get("name")) is not str or (origin is not None and type(origin) is not str)))
                if invalid:
                    row["status"] = "INVALID_SPEC_OR_ORIGIN"
                elif file is None:
                    row["status"] = "BUILTIN" if origin == "built-in" else "FROZEN" if origin == "frozen" else "ACTUAL_FILELESS_MODULE"
                    if row["status"] == "ACTUAL_FILELESS_MODULE":
                        row.update(origin="UNKNOWN", file_hash_status="NOT_APPLICABLE_NO_FILE", dynamic_provenance="UNKNOWN_NOT_OS_ISOLATION")
                elif type(file) is str:
                    p = Path(file)
                    row["path"] = file
                    try:
                        wanted = runtime.get(p.relative_to(python_root).as_posix()) if p.is_relative_to(python_root) else external.get(str(p))
                        need(hex64(wanted), "modules")
                        observed = fresh(p)
                        row.update(observed)
                        row["status"] = "ACTUAL_FILE_HASH" if observed["sha256"] == wanted else "MODULE_HASH_MISMATCH"
                    except Exception:
                        row["status"] = "MODULE_HASH_UNKNOWN"
            rows.append(row)
            if row["status"] not in ("BUILTIN", "FROZEN", "ACTUAL_FILE_HASH", "ACTUAL_FILELESS_MODULE"):
                rejected.append({"name": name, "status": row["status"]})
        return {"membership_count": len(membership), "rows": rows, "rejections": rejected,
                "expected_membership_substitution": False, "DLL_closure": "UNKNOWN", "observer_source_sha256": OBSERVER_SOURCE_SHA256}

    def watchdog(self, root, stage, expires):
        stopped = threading.Event()
        def monitor():
            started = time.monotonic()
            while not stopped.wait(.025):
                try:
                    need(time.monotonic() - started < TIMEOUT and self.now() < expires, "timeout")
                    self.check_budget(root)
                except BaseException:
                    try:
                        self.write(root, stage / "HALTED.json", canon({"status": "NOT_READY", "reason": "timeout_or_budget"}), terminal=True)
                    finally:
                        os._exit(124)  # Exactly this owned worker, never PID/name search.
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        return stopped


class HashSink:
    def __init__(self):
        self.checksum, self.bytes = hashlib.sha256(), 0
    def write(self, text):
        need(type(text) is str)
        body = text.encode("utf-8", errors="backslashreplace")
        self.checksum.update(body)
        self.bytes += len(body)
        return len(text)
    def flush(self):
        pass
    def receipt(self):
        return {"sha256": self.checksum.hexdigest(), "bytes": self.bytes, "bodies_retained": False}


class NativeBackend:
    """Only explicit actual GO can reach these unchanged pinned native APIs."""
    @contextlib.contextmanager
    def context(self, c):
        old_path, old_env = list(sys.path), dict(os.environ)
        original = importlib.machinery.SourceFileLoader.get_code
        source = Path(c["source_root"])
        def checked(loader, fullname):
            path = Path(loader.path).absolute()
            if path.is_relative_to(source):
                wanted = c["source_files"].get(str(path))
                need(hex64(wanted), "pins")
                before = fresh(path)
                body = path.read_bytes()
                need(before["sha256"] == sha(body) == wanted and fresh(path)["sha256"] == wanted, "pins")
                return compile(body, str(path), "exec", dont_inherit=True)
            return original(loader, fullname)
        try:
            os.environ.update(c["environment"])
            os.environ["TEMP"] = os.environ["TMP"] = str(Path(c["root"]) / "native")
            for path in c["package_roots"]:
                if path not in sys.path:
                    sys.path.append(path)
            sys.path.insert(0, str(source))
            importlib.machinery.SourceFileLoader.get_code = checked
            yield
        finally:
            importlib.machinery.SourceFileLoader.get_code = original
            sys.path[:] = old_path
            os.environ.clear()
            os.environ.update(old_env)

    def module(self, path, name, expected):
        need(fresh(path)["sha256"] == expected, "pins")
        need(name not in sys.modules, "startup")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        need(fresh(path)["sha256"] == expected, "pins")
        return module

    def source(self, c, relative, name):
        path = Path(c["source_root"]) / relative
        wanted = c["source_files"].get(str(path))
        need(hex64(wanted), "pins")
        sys.path.insert(0, str(path.parent))
        return self.module(path, name, wanted)

    def blind(self, c):
        comparator = self.module(Path(c["helpers"]["comparator"]["path"]), "bm25_comparator", BM25_SHA256)
        blind = self.module(Path(c["helpers"]["runner"]["path"]), "m1_blind_runner", RUNNER_SHA256)
        need(blind.bm25 is comparator, "pins")
        return blind

    def prepare(self, c, folder):
        corpus = self.source(c, "sandbox/mind/step2/corpus.py", "corpus")
        encode = self.source(c, "sandbox/mind/step2/encode.py", "encode")
        memory = self.source(c, "sandbox/mind/step2/memory.py", "memory")
        need(str(encode.HF_CACHE) == c["cache_root"], "pins")
        c["_observe"]("POST_IMPORT")
        corpus.build(folder / "lessons", [], corpus.parse_utc(c["corpus_as_of_utc"]), folder / "corpus")
        corpus_manifest, records = corpus.read_corpus(folder / "corpus")
        need(utc(corpus_manifest["cutoff_utc"]) == utc(c["corpus_as_of_utc"]), "snapshot")
        for record in records:
            provenance = record["provenance"]
            name = provenance["file"]
            expected = c["_snapshot"].get(name)
            need(record["kind"] == "lesson" and type(expected) is dict and provenance.get("transform") is None
                 and provenance["source_sha256"] == provenance["used_sha256"] == expected["sha256"]
                 and utc(provenance["mtime_utc"]) == utc(expected["mtime"]), "snapshot")
        encode.build(folder / "corpus", folder / "vectors")
        built = memory.build(folder / "corpus", folder / "vectors", folder / "memory")
        need(built["encoder"] == c["binding"]["encoder"], "pins")
        return self.blind(c)

    def runner(self, c, prepared, design_sha, values):
        blind = self.blind(c)
        v21 = self.source(c, "sandbox/mind/remember/remember_v21.py", "m1_p3_v21")
        need(v21.v3.v2.q4_mod.RULE == c["binding"]["rule"], "pins")
        items = [blind.BlindCase(row["id"], row["situation"], row["time"], row["snapshot_sha256"]) for row in values]
        factory = blind.prepared_native_factory(v21, c["binding"], prepared)
        runner = blind.BlindRunner(c["binding"], factory, blind.FileJournal(Path(c["root"]) / "native/journal", BUDGET), design_sha)
        return runner, items


def read_inputs(c, host):
    """Private input boundary: call only AFTER actual Medium identity passes."""
    root, refs = Path(c["root"]), c["inputs"]
    for name in ("runner_cases", "snapshot"):
        path = host.path(refs[name]["path"], study=root / "blind")
        need(path.name not in ("cases.json", "cases.pre.json"), "path")
    for name in ("baseline_manifest", "baseline_seal"):
        host.path(refs[name]["path"], study=root / "baseline")
    host.path(refs["commitments_path"], study=root / "blind")
    raw_cases = host.private_read(refs["runner_cases"])
    values, snapshot = json_bytes(raw_cases), json_bytes(host.private_read(refs["snapshot"]))
    baseline, seal = json_bytes(host.private_read(refs["baseline_manifest"])), json_bytes(host.private_read(refs["baseline_seal"]))
    need(type(values) is list and len(values) <= 10000 and raw_cases == canon(values), "binding")
    need(type(snapshot) is dict and bool(snapshot) and len(snapshot) <= 1024, "snapshot")
    need(baseline.get("schema") == "wd.m1-p3-baseline.v1" and type(baseline.get("identity")) is dict
         and baseline["identity"].get("study_id") == c["study_id"]
         and baseline["identity"].get("protocol_sha256") == c["protocol"]["sha256"], "binding")
    need(seal.get("schema") == "wd.m1-p3-baseline-seal.v1" and seal.get("status") == "SEALED"
         and seal.get("manifest_sha256") == refs["baseline_manifest"]["sha256"], "binding")
    need(utc(baseline["started_utc"]) <= utc(baseline["sealed_utc"]) <= utc(seal["sealed_utc"]), "clock")
    available = utc(seal["sealed_utc"]) + dt.timedelta(seconds=1)
    need(available < utc(c["corpus_as_of_utc"]) < utc(c["collection_start_utc"]), "clock")
    need(type(baseline.get("files")) is list and {r["name"] for r in baseline["files"]} == set(snapshot), "snapshot")
    names, digests = [], set()
    for row in baseline["files"]:
        keys(row, ("name", "sha256", "bytes"))
        name = row["name"]
        need(type(name) is str and NAME.fullmatch(name) and name.casefold() != "memory.md", "snapshot")
        keys(snapshot[name], ("sha256", "mtime"))
        need(snapshot[name]["sha256"] == row["sha256"] and utc(snapshot[name]["mtime"]) == available, "snapshot")
        need(hex64(row["sha256"]) and type(row["bytes"]) is int and row["bytes"] >= 0, "snapshot")
        path = host.path(refs["baseline_blobs"].get(row["sha256"]), study=root / "baseline")
        observed = fresh(path)
        need(observed["sha256"] == row["sha256"] and observed["bytes"] == row["bytes"], "snapshot")
        body = host.private_read({"path": str(path), "sha256": row["sha256"]})
        try:
            body.decode("utf-8")
        except UnicodeError:
            raise NotReady("snapshot") from None
        names.append(name)
        digests.add(row["sha256"])
    need(names == sorted(names, key=lambda n: (n.casefold(), n)) and len(set(n.casefold() for n in names)) == len(names)
         and set(refs["baseline_blobs"]) == digests, "snapshot")
    ids = set()
    for row in values:
        keys(row, ("id", "situation", "time", "snapshot_sha256"))
        need(type(row["id"]) is str and ID.fullmatch(row["id"]) and row["id"] not in ids
             and row["snapshot_sha256"] == refs["snapshot"]["sha256"], "binding")
        need(type(row["situation"]) is str and 0 < len(row["situation"].strip()) <= 600, "schema")
        when = utc(row["time"])
        need(when.strftime("%Y-%m-%dT%H:%M:%SZ") == row["time"]
             and available + dt.timedelta(seconds=1) < when
             and utc(c["collection_start_utc"]) + dt.timedelta(seconds=1) <= when
             and when + dt.timedelta(seconds=1) < utc(c["collection_end_utc"]), "clock")
        ids.add(row["id"])
    need([r["id"] for r in values] == sorted(ids), "binding")
    return values, snapshot


def run_phase(c, phase, manifest_sha, go, host, backend, write, observe):
    values, snapshot = read_inputs(c, host)
    c["_snapshot"] = snapshot
    root, shared = Path(c["root"]), Path(c["root"]) / "prepared/shared"
    outputs = {}
    if phase == "prepare":
        need(not shared.exists() and not (root / "native/PREPARED.json").exists(), "consumed")
        if not values:
            outputs["PREPARED"] = write(root / "native/PREPARED.json", canon({}))
            outputs["native_prepare_executed"] = False
            return 0, outputs
        shared.mkdir(parents=True, exist_ok=False)
        lessons = shared / "lessons"
        lessons.mkdir()
        for name, info in snapshot.items():
            data = host.private_read({"path": c["inputs"]["baseline_blobs"][info["sha256"]], "sha256": info["sha256"]})
            write(lessons / name, data)
            elapsed = utc(info["mtime"]) - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
            ns = (elapsed.days * 86400 + elapsed.seconds) * 1_000_000_000 + elapsed.microseconds * 1000
            os.utime(lessons / name, ns=(ns, ns))
            need(fresh(lessons / name)["sha256"] == info["sha256"]
                 and (lessons / name).stat().st_mtime_ns == ns, "snapshot")
        blind = backend.prepare(c, shared)
        observe("POST_PREPARE")
        memory_hash = blind.tree_sha256(shared / "memory")
        prepared = {row["id"]: {"snapshot_sha256": row["snapshot_sha256"], "binding_sha256": digest(c["binding"]),
                                "memory_dir": str(shared / "memory"), "memory_sha256": memory_hash} for row in values}
        outputs["PREPARED"] = write(root / "native/PREPARED.json", canon(prepared))
        outputs["memory_tree_sha256"] = memory_hash
        outputs["native_prepare_executed"] = True
    else:
        prepared_path = root / "native/PREPARED.json"
        prepared = json_bytes(host.private_read({"path": str(prepared_path), "sha256": c["_prior_outputs"][prepared_path.relative_to(root).as_posix()]["sha256"]}))
        need(set(prepared) == {row["id"] for row in values}, "binding")
        need(len({(v["snapshot_sha256"], v["memory_dir"], v["memory_sha256"], v["binding_sha256"]) for v in prepared.values()}) <= 1, "binding")
        need(all(v["memory_dir"] == str(shared / "memory") and v["binding_sha256"] == digest(c["binding"])
                 and v["snapshot_sha256"] == c["inputs"]["snapshot"]["sha256"] for v in prepared.values()), "binding")
        need(not (root / "native/journal" / phase).exists(), "consumed")
        runner, items = backend.runner(c, prepared, c["protocol"]["sha256"], values)
        observe("POST_IMPORT")
        if phase == "prepass":
            native_result = runner.prepass(items)
        else:
            prepass_path = root / "native/journal/prepass/RESULT.json"
            host.read({"path": str(prepass_path), "sha256": c["_prior_outputs"][prepass_path.relative_to(root).as_posix()]["sha256"]})
            commits_path = host.path(c["inputs"]["commitments_path"], study=root)
            commits = json_bytes(host.read({"path": str(commits_path), "sha256": go["commitments_sha256"]}))
            need(type(commits) is dict and set(commits) <= {r["id"] for r in values}
                 and len(commits) <= 60 and all(hex64(v) for v in commits.values()), "binding")
            native_result = runner.arms(items, runner.journal.completed("prepass"), commits)
        observe("POST_" + phase.upper())
        journal = root / "native/journal" / phase
        names = ("guard.json", "applicable.json", "RESULT.json") if phase == "prepass" else ("arm_A.json", "arm_B.json", "RESULT.json")
        for name in names:
            info = fresh(journal / name)
            outputs[name] = {"sha256": info["sha256"], "bytes": info["bytes"]}
        need(outputs["RESULT.json"]["sha256"] == digest(native_result), "write")
        for name, field in (("guard.json", "guarded"), ("applicable.json", "applicable")) if phase == "prepass" else (("arm_A.json", "arm_A"), ("arm_B.json", "arm_B")):
            need(outputs[name]["sha256"] == digest(native_result[field]), "write")
        if phase == "arms":
            run = {"schema": "wd.m1-p3-native-run.v1", "protocol_sha256": c["protocol"]["sha256"],
                   "manifest_sha256": manifest_sha, "binding_sha256": digest(c["binding"]),
                   "runner_cases_sha256": c["inputs"]["runner_cases"]["sha256"],
                   "snapshot_sha256": c["inputs"]["snapshot"]["sha256"], "commitments_sha256": go["commitments_sha256"],
                   "arm_files": {name: outputs[name] for name in names}, "prior_receipts": go["prior_receipts"],
                   "scorer_unlocked": False, "DLL_closure": "UNKNOWN"}
            outputs["RUN_MANIFEST.json"] = write(root / "native/RUN_MANIFEST.json", canon(run))
    return len(values), outputs


def execute(manifest, manifest_sha256, go_bytes, go_sha256, *, phase, execute=False,
            _test_host=None, _test_backend=None):
    """Single explicit phase. Public return is fixed aggregate/hash metadata."""
    consumed = opened = False
    root = stage = host = stopped = None
    try:
        need(execute is True, "dormant")
        need(phase in PHASES, "go")
        c, go = manifest_from_bytes(manifest, manifest_sha256), json_bytes(go_bytes, go_sha256)
        keys(go, ("schema", "status", "study_id", "protocol_sha256", "manifest_sha256", "phase", "authorizer",
                  "event_ref", "issued_at_utc", "expires_at_utc", "prior_receipts", "commitments_sha256"))
        need(go["schema"] == "wd.m1-p3-native-go.v1" and go["status"] == "NATIVE_PHASE_GO"
             and go["study_id"] == c["study_id"] and go["protocol_sha256"] == c["protocol"]["sha256"]
             and go["manifest_sha256"] == manifest_sha256 and go["phase"] == phase and hex64(go["event_ref"]), "go")
        host = Host() if _test_host is None else _test_host
        host.actual_data_opened = False
        need((_test_host is None) == (_test_backend is None), "schema")
        backend = NativeBackend() if _test_backend is None else _test_backend
        protocol = json_bytes(host.read(c["protocol"]))
        need(protocol.get("schema") == "wd.m1-p3-protocol-lock.v1" and protocol.get("status") == "LOCKED"
             and protocol.get("study_id") == c["study_id"] and go["authorizer"] == protocol.get("native_authorizer")
             and type(go["authorizer"]) is dict and bool(go["authorizer"]), "go")
        keys(go["authorizer"], ("agent", "agent_uuid", "session_id"))
        issued, expires = utc(go["issued_at_utc"]), utc(go["expires_at_utc"])
        need(issued <= host.now() < expires and expires - issued <= dt.timedelta(seconds=TIMEOUT)
             and utc(c["collection_end_utc"]) <= issued, "go")
        prior_names = PHASES[:PHASES.index(phase)]
        need(type(go["prior_receipts"]) is dict and set(go["prior_receipts"]) == set(prior_names), "prior")
        need(hex64(go["commitments_sha256"]) if phase == "arms" else go["commitments_sha256"] is None, "go")
        host.startup(c, phase)
        root = Path(c["root"])
        native = root / "native"
        if phase == "prepare":
            need(not native.exists(), "consumed")
            native.mkdir()
        else:
            need(native.is_dir(), "prior")
        need(not (native / "HALTED.json").exists(), "consumed")
        stage = native / phase
        stage.mkdir(exist_ok=False)  # Exclusive consumed latch, never adopted.
        consumed = True
        lock = threading.RLock()
        def write(path, body, terminal=False):
            with lock:
                ref = host.write(root, path, body, terminal=terminal)
                need(type(ref) is dict and ref.get("sha256") == sha(body) and ref.get("bytes") == len(body)
                     and fresh(path)["sha256"] == sha(body), "write")
                return ref
        write(stage / "INTENT.json", canon({"schema": "wd.m1-p3-native-intent.v1", "study_id": c["study_id"],
              "manifest_sha256": manifest_sha256, "go_sha256": go_sha256, "phase": phase,
              "invocation_consumed": True, "actual_data_opened": False}))
        identity = host.identity()
        need(identity.get("integrity_rid") == 8192 and identity.get("elevated") is False
             and identity.get("python", {}).get("sha256") == PYTHON_SHA256, "medium")
        write(stage / "IDENTITY.json", canon(identity))
        host.private_stdio(c, phase)
        stopped = host.watchdog(root, stage, expires)
        runtime = host.pin_all(c)
        c["_prior_outputs"] = {}
        for previous in prior_names:
            ref = {"path": str(native / previous / "RESULT.json"), "sha256": go["prior_receipts"][previous]}
            old = json_bytes(host.read(ref))
            need(old.get("status") == "COMPLETE" and old.get("phase") == previous
                 and old.get("manifest_sha256") == manifest_sha256 and old.get("study_id") == c["study_id"], "prior")
            for relative, info in old["retained_outputs"].items():
                path = host.path(str(root / relative), study=root)
                need(fresh(path)["sha256"] == info["sha256"], "prior")
                c["_prior_outputs"][relative] = info
        observations = []
        def observe(label):
            snapshot = host.observe(c, runtime)
            ref = write(stage / ("MODULES_" + label + ".json.gz"), gzip.compress(canon(snapshot), mtime=0))
            observations.append({"stage": label, **ref})
            need(not snapshot["rejections"], "modules")
        c["_observe"] = observe
        observe("BEFORE_NATIVE")
        out, err = HashSink(), HashSink()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), backend.context(c):
            count, outputs = run_phase(c, phase, manifest_sha256, go, host, backend, write, observe)
        opened = host.actual_data_opened
        observe("AFTER_NATIVE")
        for relative, info in c["_prior_outputs"].items():
            need(fresh(root / relative)["sha256"] == info["sha256"], "prior")
        host.check_budget(root)
        need(host.now() < expires, "timeout")
        retained = {}
        for path in (native / "PREPARED.json", native / "RUN_MANIFEST.json"):
            if path.is_file():
                retained[path.relative_to(root).as_posix()] = {"sha256": fresh(path)["sha256"]}
        if phase in ("prepass", "arms"):
            for path in (native / "journal" / phase).iterdir():
                need(path.is_file(), "path")
                retained[path.relative_to(root).as_posix()] = {"sha256": fresh(path)["sha256"]}
        result = {"schema": "wd.m1-p3-native-phase.v1", "status": "COMPLETE", "study_id": c["study_id"],
                  "phase": phase, "manifest_sha256": manifest_sha256, "go_sha256": go_sha256,
                  "invocation_consumed": True, "actual_data_opened": True, "SAFE_cases": count,
                  "outputs": outputs, "retained_outputs": retained, "observations": observations,
                  "stdout": out.receipt(), "stderr": err.receipt(), "DLL_closure": "UNKNOWN"}
        receipt = write(stage / "RESULT.json", canon(result), terminal=True)
        return {"status": "COMPLETE", "phase": phase, "invocation_consumed": True,
                "actual_data_opened": True, "SAFE_cases": count, "result_sha256": receipt["sha256"]}
    except BaseException as error:
        opened = bool(getattr(host, "actual_data_opened", False))
        reason = error.reason if type(error) is NotReady else "consumed" if type(error) is FileExistsError else "native_failed"
        if consumed:
            alarm = canon({"status": "NOT_READY", "reason": reason, "phase": phase,
                           "invocation_consumed": True, "actual_data_opened": opened})
            for path in (stage / "VOID.json", root / "native/HALTED.json"):
                try:
                    host.write(root, path, alarm, terminal=True)
                except BaseException:
                    pass  # Consumed directory persists even if the alarm write fails.
        return {"status": "NOT_READY", "reason": reason, "phase": phase if phase in PHASES else None,
                "invocation_consumed": consumed, "actual_data_opened": opened}
    finally:
        if stopped is not None:
            stopped.set()


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise NotReady("arguments")


def main(argv=None):
    try:
        parser = Parser(add_help=False)
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--phase", choices=PHASES, required=True)
        for field in ("manifest", "manifest-sha256", "go", "go-sha256"):
            parser.add_argument("--" + field, required=True)
        args = parser.parse_args(argv)
        need(args.execute, "dormant")
        host = Host()
        result = execute(host.read({"path": args.manifest, "sha256": args.manifest_sha256}), args.manifest_sha256,
                         host.read({"path": args.go, "sha256": args.go_sha256}), args.go_sha256,
                         phase=args.phase, execute=True)
    except BaseException as error:
        result = {"status": "NOT_READY", "reason": error.reason if type(error) is NotReady else "native_failed",
                  "invocation_consumed": False, "actual_data_opened": False}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
