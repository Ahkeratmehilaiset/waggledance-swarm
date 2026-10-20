"""Inert, stdlib-only prospective incident recording primitive.

This is NOT a collector, case builder, scorer, or authorization mechanism.
Callers must obtain the study's collection permission separately. Binding
capsules below are small explicit adapters, not a replacement preregistration.
They contain identifiers, times and digests only; no lesson or action bodies.
An action receipt proves byte/tuple consistency, NOT authenticity or that an
error really recurred. A clean case builder must verify the referenced evidence.

Usage: Bindings.from_bytes(protocol_capsule, baseline_capsule, capsule_hash_A,
capsule_hash_B, protocol_sha256=original_final_protocol_hash,
baseline_sha256=original_baseline_manifest_hash); Recorder(audit_root, fresh_root, bindings,
mode="record"); initialize(); enroll(record, action_receipt_bytes); close().
One instance owns one fresh directory and its process-level lock throughout.
There is deliberately NO reopen/resume/adopt/retry/cleanup API. After process
death or uncertain IO, preserve the directory for independent disposition.
Files are exclusive-created, flushed, fsynced and read back. They are logically
immutable, not OS write-protected. This is cooperative containment on C: NTFS,
not a hostile-process sandbox, ACL/privacy proof, crash-atomic directory, quota,
or continuously running producer. Do not put this private ledger in Git/prompts.

CLI: explicit --mode record plus --audit-root, --root, --protocol,
--protocol-sha256, --baseline, --baseline-sha256, --protocol-capsule-sha256,
--baseline-capsule-sha256 and --input. Input is a finite
JSON {"records":[{"record":{...},"action_utf8":"<exact receipt JSON>"}]}.
The batch is NOT transactional; on refusal earlier successful files remain.
Only sanitized counts/hash receipts are printed. No operation occurs on import.
The _test_host seam is explicit API-only injection; CLI always uses Host().
Refusal disposition is closed: REFUSE_CASE for expected invalid case/dedupe;
STOP_NOT_READY for integrity/hash/identity/clock/budget/IO and all admission
failures. STOP is sticky. Aggregate refusal counts are process-lifetime receipt
counts, not a crash-durable counter log; a crashed study stays NOT_READY.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid


BUDGET_BYTES = 64 * 1024 * 1024
MIN_FREE_BYTES = 3 * 1024 * 1024 * 1024
CAPSULE_BYTES = 256 * 1024
RECORD_BYTES = 4096
ACTION_BYTES = 2048
INPUT_BYTES = 1024 * 1024
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_UUID = re.compile(r"(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\Z")
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\Z")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_AGENT = re.compile(r"[a-z][a-z0-9-]{0,39}\Z")
_RUN = re.compile(r"[a-z0-9][a-z0-9_-]{0,95}\Z")
REFUSE_CASE = "REFUSE_CASE"
STOP_NOT_READY = "STOP_NOT_READY"
REFUSAL_REASONS = frozenset({
    "schema", "mode", "binding", "size", "time", "clock", "identity", "reparse",
    "destination", "filesystem", "lock_busy", "uncertain_write", "budget",
    "already_initialized", "disk", "destination_exists", "io", "corruption",
    "duplicate", "limit", "not_initialized", "closed", "halted", "arguments",
})
CASE_REASONS = frozenset({"schema", "size", "time"})


class Refusal(Exception):
    """A fixed reason token only; never include input or an OS error message."""
    def __init__(self, reason):
        # Also sanitize an injected adapter's exception; never echo its text.
        super().__init__(reason if type(reason) is str and reason in REFUSAL_REASONS else "io")


def _require(condition, reason="schema"):
    if not condition:
        raise Refusal(reason)


def _matches(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _keys(value, keys):
    _require(type(value) is dict and set(value) == set(keys))


def _time(value):
    _require(_matches(value, _UTC), "time")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise Refusal("time") from None


def _dump(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise Refusal("schema") from None


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _object(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value)
        value[key] = item
    return value


def _json(data, limit):
    _require(type(data) is bytes and 0 < len(data) <= limit, "size")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refusal("schema")))
    except (UnicodeError, ValueError, RecursionError):
        raise Refusal("schema") from None


def _identity(value):
    try:
        _keys(value, ("agent", "agent_uuid", "session_id"))
        _require(_matches(value["agent"], _AGENT)
                 and _matches(value["agent_uuid"], _UUID)
                 and _matches(value["session_id"], _SESSION))
    except Refusal:
        raise Refusal("identity") from None


@dataclass(frozen=True)
class Bindings:
    study_id: str
    protocol_sha256: str
    baseline_sha256: str
    start: datetime
    end: datetime
    sealed: datetime
    max_delay: int
    max_records: int
    lesson_blobs: tuple[tuple[str, str], ...]
    reporters: tuple[tuple[str, str, str], ...]
    protocol_capsule_sha256: str
    baseline_capsule_sha256: str

    @classmethod
    def from_bytes(cls, protocol, baseline, protocol_capsule_sha256, baseline_capsule_sha256,
                   *, protocol_sha256, baseline_sha256):
        """Verify exact supplied bytes before parsing; no discovery or fallback."""
        _require(_matches(protocol_sha256, _HEX64)
                 and _matches(baseline_sha256, _HEX64)
                 and _matches(protocol_capsule_sha256, _HEX64)
                 and _matches(baseline_capsule_sha256, _HEX64), "binding")
        _require(type(protocol) is bytes and type(baseline) is bytes, "binding")
        _require(_hash(protocol) == protocol_capsule_sha256
                 and _hash(baseline) == baseline_capsule_sha256, "binding")
        p = _json(protocol, CAPSULE_BYTES)
        b = _json(baseline, CAPSULE_BYTES)
        _keys(p, ("schema", "study_id", "protocol_sha256", "collection_start_utc", "collection_end_utc",
                  "max_record_delay_seconds", "max_records", "allowed_reporters"))
        _keys(b, ("schema", "study_id", "protocol_sha256", "baseline_sha256", "sealed_at_utc",
                  "sealed_time_resolution_seconds", "lesson_blobs"))
        _require(p["schema"] == "wd.prospective-protocol.v1"
                 and b["schema"] == "wd.prospective-baseline.v1")
        _require(_matches(p["study_id"], _HEX32) and b["study_id"] == p["study_id"]
                 and p["protocol_sha256"] == b["protocol_sha256"] == protocol_sha256
                 and b["baseline_sha256"] == baseline_sha256, "binding")
        start, end, sealed = _time(p["collection_start_utc"]), _time(p["collection_end_utc"]), _time(b["sealed_at_utc"])
        _require(type(b["sealed_time_resolution_seconds"]) is int
                 and b["sealed_time_resolution_seconds"] == 1, "time")
        # Supplied seconds denote conservative intervals, never exact instants.
        # Root's source adapter must freeze the original clock/rounding predicate.
        sealed += timedelta(seconds=1)
        _require(sealed < start < end, "time")
        delay, count = p["max_record_delay_seconds"], p["max_records"]
        _require(type(delay) is int and 1 <= delay <= 86400
                 and type(count) is int and 1 <= count <= 10000)
        blobs = b["lesson_blobs"]
        _require(type(blobs) is dict and 1 <= len(blobs) <= 1024)
        for key, value in blobs.items():
            _require(_matches(key, _HEX32) and _matches(value, _HEX64))
        _require(type(p["allowed_reporters"]) is list and 1 <= len(p["allowed_reporters"]) <= 32, "identity")
        reporters = []
        for reporter in p["allowed_reporters"]:
            _identity(reporter)
            _require(reporter["agent"] not in ("fable-5", "claude-rco-1"), "identity")
            reporters.append(tuple(reporter[key] for key in ("agent", "agent_uuid", "session_id")))
        _require(len(set(reporters)) == len(reporters), "identity")
        return cls(p["study_id"], protocol_sha256, baseline_sha256, start, end, sealed,
                   delay, count, tuple(sorted(blobs.items())), tuple(reporters),
                   protocol_capsule_sha256, baseline_capsule_sha256)

    def validate_record(self, value, action, now):
        data = _dump(value)
        _require(len(data) <= RECORD_BYTES, "size")
        _keys(value, ("schema", "study_id", "protocol_sha256", "baseline_sha256", "incident_id",
                      "incident_kind", "reporter", "action_time_utc", "recorded_at_utc", "action_ref",
                      "action_time_resolution_seconds", "action_sha256", "action_receipt_sha256",
                      "lesson_ref", "baseline_blob_sha256"))
        _require(value["schema"] == "wd.prospective-incident.v1")
        _require(value["study_id"] == self.study_id
                 and value["protocol_sha256"] == self.protocol_sha256
                 and value["baseline_sha256"] == self.baseline_sha256, "binding")
        _require(_matches(value["incident_id"], _HEX32) and _matches(value["action_ref"], _HEX32))
        _require(type(value["incident_kind"]) is str and value["incident_kind"] in ("recurred", "near_miss"))
        _identity(value["reporter"])
        _require(tuple(value["reporter"][key] for key in ("agent", "agent_uuid", "session_id"))
                 in self.reporters, "identity")
        _require(type(value["action_time_resolution_seconds"]) is int
                 and value["action_time_resolution_seconds"] == 1, "time")
        action_time, recorded = _time(value["action_time_utc"]), _time(value["recorded_at_utc"])
        _require(self.sealed < self.start < action_time
                 and action_time + timedelta(seconds=1) < self.end and self.sealed <= now < self.end
                 and action_time + timedelta(seconds=1) <= recorded == now
                 and (recorded - action_time).total_seconds() <= self.max_delay, "time")
        _require(_matches(value["lesson_ref"], _HEX32)
                 and _matches(value["baseline_blob_sha256"], _HEX64)
                 and dict(self.lesson_blobs).get(value["lesson_ref"]) == value["baseline_blob_sha256"], "binding")
        _require(_matches(value["action_sha256"], _HEX64)
                 and _matches(value["action_receipt_sha256"], _HEX64), "binding")
        receipt = _json(action, ACTION_BYTES)
        _require(_hash(action) == value["action_receipt_sha256"], "binding")
        _keys(receipt, ("schema", "action_ref", "reporter", "action_time_utc",
                        "action_time_resolution_seconds", "source_record_sha256"))
        _require(receipt["schema"] == "wd.prospective-action.v1")
        _identity(receipt["reporter"])
        _require(_matches(receipt["source_record_sha256"], _HEX64)
                 and receipt["source_record_sha256"] == value["action_sha256"], "binding")
        for key in ("action_ref", "reporter", "action_time_utc", "action_time_resolution_seconds"):
            _require(receipt[key] == value[key], "binding")
        return data


class _Lock:
    def __init__(self, path):
        self.stream = path.open("r+b", buffering=0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise Refusal("lock_busy") from None
        self.identity = (os.fstat(self.stream.fileno()).st_dev, os.fstat(self.stream.fileno()).st_ino)

    def close(self):
        # Closing the owning handle releases the OS lock. The file is retained.
        self.stream.close()


class Host:
    """Production defaults: actual UTC clock, local C: NTFS, no injected IO."""
    def utc_now(self):
        return datetime.now(timezone.utc)

    def free_bytes(self, root):
        import shutil
        return shutil.disk_usage(root).free

    def volume_type(self, path):
        _require(os.name == "nt", "destination")
        fs = ctypes.create_unicode_buffer(32)
        try:
            function = ctypes.WinDLL("kernel32", use_last_error=True).GetVolumeInformationW
            function.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
                                 ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
                                 ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p, ctypes.c_uint32)
            function.restype = ctypes.c_int
            ok = function(str(path), None, 0, None, None, None, fs, len(fs))
            _require(ok != 0, "filesystem")
            return fs.value
        except (AttributeError, OSError):
            raise Refusal("filesystem") from None

    def require_ntfs(self, path):
        _require(self.volume_type(path) == "NTFS", "filesystem")

    def validate_tree(self, path, *, recurse=True):
        # Check ancestors and all immediate ledger entries. Ledger directories
        # are deliberately flat; no recursive traversal or symlink following.
        for part in (path, *path.parents):
            item = part.lstat()
            _require(not stat.S_ISLNK(item.st_mode)
                     and not (getattr(item, "st_file_attributes", 0) & 0x400), "reparse")
        if recurse:
            for entry in path.iterdir():
                item = entry.lstat()
                _require(stat.S_ISREG(item.st_mode) and not entry.is_symlink()
                         and not (getattr(item, "st_file_attributes", 0) & 0x400)
                         and item.st_nlink == 1, "reparse")

    def validate_destination(self, audit, root):
        for path in (audit, root):
            _require(os.name == "nt" and path.is_absolute() and path.drive.upper() == "C:", "destination")
            _require(all(part not in (".", "..") and ":" not in part
                         for part in path.parts[1:]), "destination")
        _require(audit.name == ".codex-audit" and root.parent == audit
                 and _matches(root.name, _RUN), "destination")
        _require(audit.is_dir() and audit.resolve(strict=True) == audit, "destination")
        self.validate_tree(audit, recurse=False)
        self.require_ntfs(Path("C:/"))

    def lock(self, path):
        return _Lock(path)

    def write_new(self, path, data):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            _require(stream.write(data) == len(data), "uncertain_write")
            stream.flush()
            os.fsync(stream.fileno())
        _require(self.read(path, len(data)) == data, "uncertain_write")

    def read(self, path, limit):
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        _require(len(data) <= limit, "size")
        return data


class Recorder:
    """Explicit single-process, fresh-ledger lifetime. No filesystem IO in init."""
    def __init__(self, audit_root, root, bindings, *, mode=None, _test_host=None):
        self.audit = Path(audit_root)
        self.root = Path(root)
        self.bindings = bindings
        self.mode = mode
        self.host = Host() if _test_host is None else _test_host
        self._lock = None
        self._initialized = self._halted = self._closed = False
        self._files = {}
        self._ids = set()
        self._actions = set()
        self._primary = self._near = self._total_bytes = 0
        self._reported = self._refused_cases = self._duplicates = 0

    def _now(self):
        value = self.host.utc_now()
        _require(type(value) is datetime and value.tzinfo is timezone.utc, "clock")
        return value.replace(microsecond=0)

    @staticmethod
    def _refused(reason, disposition=STOP_NOT_READY):
        return {"status": "refused", "reason": str(Refusal(reason)), "disposition": disposition,
                "primary_candidates": None, "near_miss": None, "reported": None,
                "accepted": None, "refused_cases": None, "duplicates": None}

    def _counts(self):
        return {"primary_candidates": self._primary, "near_miss": self._near,
                "reported": self._reported, "accepted": len(self._ids),
                "refused_cases": self._refused_cases, "duplicates": self._duplicates}

    def _case_refusal(self, reason):
        self._refused_cases += 1
        self._duplicates += int(reason == "duplicate")
        return {**self._refused(reason, REFUSE_CASE), **self._counts()}

    def _ready(self):
        _require(self.mode == "record", "mode")
        _require(not self._closed, "closed")
        _require(not self._halted, "halted")

    def _disk(self, parent):
        free = self.host.free_bytes(parent)
        _require(type(free) is int and free >= MIN_FREE_BYTES + BUDGET_BYTES, "disk")

    def _write(self, name, data):
        # Reserve enough space for a fixed halt marker even if record IO fails.
        _require(self._total_bytes + len(data) + 256 <= BUDGET_BYTES, "budget")
        self.host.validate_tree(self.root, recurse=False)
        self.host.write_new(self.root / name, data)
        # Register neither a hash nor a count until the caller has independently
        # read back the bytes returned by its IO boundary. A failed/lying adapter
        # must leave enrollment halted, not silently register the intended bytes.
        _require(self.host.read(self.root / name, len(data)) == data, "uncertain_write")
        self._files[name] = (len(data), _hash(data))
        self._total_bytes += len(data)

    def _halt(self, reason):
        self._halted = True
        try:
            # A detected directory escape must never be followed to write even
            # an alarm. In-memory halt + no-reopen remains authoritative if the
            # marker itself cannot be safely persisted.
            self.host.validate_tree(self.root, recurse=False)
            self.host.write_new(self.root / "HALTED", _dump({"status": "halted", "reason": reason}))
        except BaseException:
            # Failure remains an explicit refusal and in-memory stop. Never
            # remove partial bytes. Existing roots cannot be reopened anyway.
            pass

    def initialize(self):
        touched = False
        try:
            self._ready()
            _require(type(self.bindings) is Bindings, "binding")
            _require(not self._initialized, "already_initialized")
            self.host.validate_destination(self.audit, self.root)
            now = self._now()
            _require(self.bindings.sealed <= now < self.bindings.end, "time")
            self._disk(self.audit)
            _require(not os.path.lexists(self.root), "destination_exists")
            self.root.mkdir()  # No parents=True or exist_ok/adoption.
            touched = True
            self.host.validate_tree(self.root)
            self._write("LOCK", b"0")
            self._lock = self.host.lock(self.root / "LOCK")
            header = _dump({"schema": "wd.prospective-ledger.v1", "run_id": uuid.uuid4().hex,
                            "study_id": self.bindings.study_id,
                            "protocol_sha256": self.bindings.protocol_sha256,
                            "baseline_sha256": self.bindings.baseline_sha256,
                            "protocol_capsule_sha256": self.bindings.protocol_capsule_sha256,
                            "baseline_capsule_sha256": self.bindings.baseline_capsule_sha256})
            self._write("ledger.json", header)
            self._initialized = True
            return {"status": "initialized", "ledger_sha256": _hash(header),
                    **self._counts()}
        except Refusal as error:
            if touched:
                self._halt("initialization")
            return self._refused(str(error))
        except Exception:
            if touched:
                self._halt("initialization")
            return self._refused("io")

    def _verify(self):
        try:
            self.host.validate_tree(self.root)
            entries = {path.name for path in self.root.iterdir()}
            _require(entries == set(self._files), "corruption")
            item = (self.root / "LOCK").stat()
            _require((item.st_dev, item.st_ino) == self._lock.identity and item.st_size == 1, "corruption")
            for name, (size, expected) in self._files.items():
                if name != "LOCK":  # Its byte is locked; do not read through another handle.
                    _require(_hash(self.host.read(self.root / name, size)) == expected, "corruption")
        except Exception:
            self._halt("corruption")
            raise Refusal("corruption") from None

    def enroll(self, record, action_receipt):
        writing = False
        try:
            self._ready()
            _require(self._initialized, "not_initialized")
            self._reported += 1
            # Global integrity/resource/clock conditions precede case refusal;
            # malformed nominations cannot hide a broken collector.
            self._verify()
            now = self._now()
            _require(self.bindings.start < now < self.bindings.end, "clock")
            self._disk(self.root)
            _require(self._total_bytes + 256 <= BUDGET_BYTES, "budget")
            # Freeze the caller's object; mutable inputs cannot change mid-write.
            try:
                value = _json(_dump(record), RECORD_BYTES)
                data = self.bindings.validate_record(value, action_receipt, now)
            except Refusal as error:
                if str(error) in CASE_REASONS:
                    return self._case_refusal(str(error))
                raise
            _require(self._total_bytes + len(data) + 256 <= BUDGET_BYTES, "budget")
            if value["incident_id"] in self._ids or value["action_ref"] in self._actions:
                return self._case_refusal("duplicate")
            _require(len(self._ids) < self.bindings.max_records, "limit")
            writing = True
            self._write("incident-" + value["incident_id"] + ".json", data)
            writing = False
            self._ids.add(value["incident_id"])
            self._actions.add(value["action_ref"])
            if value["incident_kind"] == "recurred":
                self._primary += 1
            else:
                self._near += 1
            return {"status": "recorded", "record_sha256": _hash(data),
                    **self._counts()}
        except Refusal as error:
            if writing:
                self._halt("uncertain_write")
                return self._refused("uncertain_write")
            if self._initialized and not self._halted and not self._closed:
                self._halt(str(error))
            return self._refused(str(error))
        except BaseException:
            if writing:
                self._halt("uncertain_write")
                return self._refused("uncertain_write")
            self._halt("io")
            return self._refused("io")

    def close(self):
        self._closed = True
        if self._lock is not None:
            self._lock.close()
            self._lock = None


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refusal("arguments")


def main(argv=None):
    """One explicitly requested finite batch, aggregate output, no raw errors."""
    recorder = None
    try:
        parser = _Parser(add_help=False)
        parser.add_argument("--mode", required=True, choices=("record",))
        for flag in ("audit-root", "root", "protocol", "protocol-sha256", "baseline", "baseline-sha256",
                     "protocol-capsule-sha256", "baseline-capsule-sha256", "input"):
            parser.add_argument("--" + flag, required=True)
        args = parser.parse_args(argv)
        host = Host()
        def read_input(name, limit):
            path = Path(name)
            _require(os.name == "nt" and path.is_absolute() and path.drive.upper() == "C:", "destination")
            host.validate_tree(path, recurse=False)
            _require(path.is_file() and path.stat().st_nlink == 1, "destination")
            return host.read(path, limit)
        bindings = Bindings.from_bytes(read_input(args.protocol, CAPSULE_BYTES),
                                       read_input(args.baseline, CAPSULE_BYTES),
                                       args.protocol_capsule_sha256, args.baseline_capsule_sha256,
                                       protocol_sha256=args.protocol_sha256, baseline_sha256=args.baseline_sha256)
        batch = _json(read_input(args.input, INPUT_BYTES), INPUT_BYTES)
        _keys(batch, ("records",))
        _require(type(batch["records"]) is list and 1 <= len(batch["records"]) <= bindings.max_records)
        # Parse/check every envelope before any destination creation.
        for envelope in batch["records"]:
            _keys(envelope, ("record", "action_utf8"))
            _require(type(envelope["action_utf8"]) is str)
        recorder = Recorder(Path(args.audit_root), Path(args.root), bindings, mode=args.mode)
        result = recorder.initialize()
        if result["status"] == "initialized":
            for envelope in batch["records"]:
                result = recorder.enroll(envelope["record"], envelope["action_utf8"].encode("utf-8"))
                if result["status"] != "recorded":
                    break
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["status"] == "recorded" else 2
    except Refusal as error:
        print(json.dumps(Recorder._refused(str(error)), sort_keys=True, separators=(",", ":")))
        return 2
    except Exception:
        print(json.dumps(Recorder._refused("io"), sort_keys=True, separators=(",", ":")))
        return 2
    finally:
        if recorder is not None:
            recorder.close()


if __name__ == "__main__":
    raise SystemExit(main())
