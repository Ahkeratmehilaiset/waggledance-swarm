"""Dormant P3 collection glue. No model, discovery, bridge or autostart.

Only explicit execute=True plus hash-bound LOCKED protocol and a separate
LOCKED_COLLECTION_GO permit effects. This module does not authenticate a GO or
grant study authority. The study controller/caller must obey a STOP, including
pre-admission STOPs where no authorized destination could safely be consumed.
An existing output directory consumes the attempt; no resume/adopt/repair API.

Protocol schema wd.m1-p3-protocol-lock.v1 must have status LOCKED, study_id and
an exact collection_controller object (see CONFIG_FIELDS), and reporter_sources
entries with exact reporter tuples and observed canonical native UUIDs covering
allowed_reporters exactly. Bridge session labels are not native UUIDs. No
identity normalization, inference or cross-reporter fallback is performed. Other scientific
protocol fields are hashed whole, never interpreted or changed here. The GO
schema is wd.m1-p3-collection-go.v1, status LOCKED_COLLECTION_GO, study_id,
protocol_sha256 and baseline_manifest_sha256. Baseline paths and source policy
are fixed by the original protocol, not discovered from nomination content.

Dependencies are verified exact bytes from DEPENDENCIES, then compiled from
those bytes, not a worktree-relative import. Capsule hashes and ORIGINAL final
protocol/baseline digests stay distinct. The capture adapter's exact full-record
hash, supplied-prefix hash and the recorder tuple-receipt hash also stay distinct.

One writer lifetime, two flat owned output roots, one shared monotonic 64 MiB
budget, exclusive files and retained intent/partials/refusal journals. Private
gold mapping and references stay in nomination storage. Public results contain
only aggregate counts and hash receipts. No directory atomicity, OS isolation,
ACL confidentiality, independent source-authenticity or scientific utility claim.
Synthetic runtime injection is explicit API-only; CLI always uses real checks.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import types
import uuid


DEPENDENCIES = {
    "recorder": ("C:/Python/project2/sandbox/mind/measurement_tools/prospective_incidents.py",
                 "f015a09c56c192de474fe5a9590f6c26e603386adc0c62c1e4ba27e5cff5cdd0"),
    "capture": ("C:/Python/project2/sandbox/mind/measurement_tools/prospective_capture.py",
                "4203b3215c446020b57940b69deccbf3694af4b936af468685a439867a300b12"),
}
BUDGET = 64 * 1024 * 1024
FREE_RESERVE = 3 * 1024 * 1024 * 1024
TERMINAL_RESERVE = 4096
CLOCK_DRIFT_TOLERANCE_SECONDS = 2  # Frozen guard, not a source timestamp guess.
MAX_INPUT = 2 * 1024 * 1024
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
NOM_FILE = re.compile(r"([0-9a-f]{32})\.json\Z")
REASONS = frozenset({
    "execute_required", "already_attempted", "binding", "schema", "identity", "clock",
    "paths", "dependencies", "baseline", "lesson_collision", "budget", "disk", "io",
    "corruption", "destination_exists", "lock", "input_changed", "input_bounds",
    "unsupported_native_schema", "unsupported_native_record", "unsupported_resolution",
    "outside_window", "empty_context", "duplicate", "recorder", "journal_uncertain",
    "manual_stop_invalid", "walltime", "collection_closed", "manual_stop", "unknown",
    "source_boundary", "source_cursor",
})
CASE_REASONS = frozenset({"schema", "unsupported_native_schema", "unsupported_native_record",
                          "unsupported_resolution", "outside_window", "empty_context", "duplicate"})
CONFIG_FIELDS = ("audit_root", "control_root", "ledger_root", "inbox", "baseline_root",
                 "blind_root", "score_root", "stop_signal", "collection_start_utc",
                 "collection_end_utc", "baseline_resolution_us", "max_record_delay_seconds",
                 "max_records", "allowed_reporters", "sources", "poll_ms", "max_wall_seconds")
CAPTURE_CASE = frozenset({"schema_keys", "unsupported_record_type", "unsupported_content_type",
                          "content_invalid", "text_invalid", "tool_result_invalid", "malformed_json",
                          "duplicate_json_key", "json_nonfinite", "json_bytes_invalid",
                          "record_shape_invalid", "thinking_shape_invalid", "meta_flag_invalid", "role_mismatch"})


class Stop(Exception):
    def __init__(self, reason):
        super().__init__(reason if type(reason) is str and reason in REASONS else "unknown")


class Refuse(Stop):
    pass


def require(condition, reason="schema"):
    if not condition:
        raise Stop(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canon(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError):
        raise Stop("schema") from None


def keys(obj, expected):
    require(type(obj) is dict and set(obj) == set(expected))


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def parse(data, limit=MAX_INPUT):
    require(type(data) is bytes and 0 < len(data) <= limit, "input_bounds")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Stop("schema")))
    except (UnicodeError, ValueError, RecursionError):
        raise Stop("schema") from None


def digest(value):
    require(type(value) is str and HEX64.fullmatch(value) is not None, "binding")
    return value


def opaque(value):
    require(type(value) is str and HEX32.fullmatch(value) is not None)
    return value


def lesson_id(name):
    return sha(name.encode("utf-8"))[:32]


def native_uuid(value):
    require(type(value) is str, "identity")
    try:
        require(str(uuid.UUID(value)) == value, "identity")
    except ValueError:
        raise Stop("identity") from None
    return value


def source_identity(value):
    keys(value, ("device", "inode"))
    require(all(type(value[key]) is int and 0 <= value[key] < 2**64 for key in value)
            and value["inode"] != 0, "identity")
    return value


def bounded_range(stream, *, start, end, expected_identity, limit):
    """Read a caller-locked exact range, NEVER scan/reset/read the whole file.

    The caller must pin the authorized path/ancestors before opening the stream.
    The initial cursor is an observed final-lock input, not inferred here.
    This proves byte boundaries/identity, not semantic completeness of context.
    """
    source_identity(expected_identity)
    require(type(start) is int and type(end) is int and type(limit) is int
            and 0 <= start < end < 2**63 and 0 < end-start <= limit <= MAX_INPUT, "input_bounds")
    before = os.fstat(stream.fileno())
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            and {"device":before.st_dev,"inode":before.st_ino} == expected_identity, "identity")
    require(end <= before.st_size, "source_boundary")
    read_bytes = 0
    if start:
        stream.seek(start-1)
        require(stream.read(1) == b"\n", "source_boundary")
        read_bytes += 1
    stream.seek(start)
    data = stream.read(end-start)
    read_bytes += len(data)
    require(len(data) == end-start and data.endswith(b"\n"), "source_boundary")
    after = os.fstat(stream.fileno())
    require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns) ==
            (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns), "input_changed")
    return data, {"file_identity":expected_identity,"start":start,"end":end,
                  "source_size":before.st_size,"read_bytes":read_bytes,"range_sha256":sha(data)}


def load_dependencies():
    """Read only the two literal code paths; compile exactly hashed bytes."""
    loaded = {}
    try:
        for role, (name, expected) in DEPENDENCIES.items():
            path = Path(name)
            for part in (path, *path.parents):
                info = part.lstat()
                require(not part.is_symlink() and not (getattr(info, "st_file_attributes", 0) & 0x400), "dependencies")
            before = path.stat()
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= 100000, "dependencies")
            data = path.read_bytes()
            after = path.stat()
            require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    and sha(data) == expected, "dependencies")
            module = types.ModuleType("_p3_" + role + "_" + expected[:8] + "_" + str(id(loaded)))
            sys.modules[module.__name__] = module
            try:
                exec(compile(data, str(path), "exec"), module.__dict__)
            except BaseException:
                sys.modules.pop(module.__name__, None)
                raise
            loaded[role] = module
        return loaded["recorder"], loaded["capture"]
    except Exception:
        raise Stop("dependencies") from None


class Runtime:
    """Real local C/NTFS checks. Nothing is started by constructing this object."""
    def __init__(self, recorder, capture):
        self.rec, self.cap = recorder, capture
        self.host = recorder.Host()
        self.stack = contextlib.ExitStack()

    def now(self):
        return datetime.now(timezone.utc)

    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)

    def validate(self, cfg):
        audit = Path(cfg["audit_root"])
        require(audit == self.cap.AUDIT_ROOT, "paths")
        self.cap._volume_check(audit)
        for name in ("control_root", "ledger_root"):
            self.host.validate_destination(audit, Path(cfg[name]))
        trees = [Path(cfg[key]) for key in ("control_root", "ledger_root", "inbox", "baseline_root", "blind_root", "score_root")]
        for path in trees:
            self.cap._path(path, audit=True)
        self.cap._disjoint(trees)
        require(Path(cfg["stop_signal"]) == Path(cfg["inbox"]) / "STOP.json", "paths")
        for path in (audit, Path(cfg["inbox"]), Path(cfg["baseline_root"])):
            self.cap._pin_chain(self.stack, path)
        for source in cfg["sources"]:
            path = self.cap._path(source["evidence_path"])
            self.cap._disjoint((path.parent, *trees))
            self.cap._pin_chain(self.stack, path.parent)

    def pin_new(self, root):
        self.stack.enter_context(self.cap._pin(root))

    def read(self, path, limit):
        with contextlib.ExitStack() as stack:
            self.cap._pin_chain(stack, path)
            before = path.stat()
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit, "input_bounds")
            data = self.host.read(path, limit)
            after = path.stat()
            require(len(data) == before.st_size and
                    (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "input_changed")
            return data

    def baseline(self, root, expected):
        self.cap.verify_baseline(root, expected_manifest_sha256=expected)
        data = self.read(root / "manifest.json", MAX_INPUT)
        require(sha(data) == expected, "baseline")
        seal = parse(self.read(root / "SEALED.json", MAX_INPUT))
        return parse(data), seal

    def read_source_range(self, source, start, end):
        path = Path(source["evidence_path"])
        with contextlib.ExitStack() as stack:
            self.cap._pin_chain(stack, path)
            with path.open("rb", buffering=0) as stream:
                data, receipt = bounded_range(stream, start=start, end=end,
                    expected_identity=source["file_identity"], limit=source["max_range_bytes"])
                current = path.stat()
                require({"device":current.st_dev,"inode":current.st_ino} == source["file_identity"], "identity")
                return data, receipt

    def close(self):
        self.stack.close()


class BudgetHost:
    """Real production host composition; explicit fake runtime only in tests."""
    def __init__(self, runtime):
        self.runtime, self.base = runtime, runtime.host
        self.total = 0
        self.files = {}
        self.last_failure = None

    def utc_now(self):
        return self.runtime.now()

    def free_bytes(self, path):
        return self.base.free_bytes(path)

    def validate_destination(self, audit, root):
        return self.base.validate_destination(audit, root)

    def validate_tree(self, path, **kwargs):
        return self.base.validate_tree(path, **kwargs)

    def lock(self, path):
        return self.base.lock(path)

    def read(self, path, limit):
        return self.base.read(path, limit)

    def write_new(self, path, data):
        terminal = path.name in ("STOP_NOT_READY.json", "HALTED", "RESULT.json")
        limit = BUDGET if terminal else BUDGET - TERMINAL_RESERVE
        try:
            require(self.total + len(data) <= limit, "budget")
            require(self.free_bytes(path.parent) >= FREE_RESERVE + BUDGET, "disk")
            self.total += len(data)  # Failed/partial IO consumes its entire reservation.
            self.base.write_new(path, data)
            require(self.base.read(path, len(data)) == data, "journal_uncertain")
            item = path.stat()
            self.files[path] = (len(data), sha(data), item.st_dev, item.st_ino)
        except BaseException as error:
            self.last_failure = str(error) if isinstance(error, Stop) else "io"
            raise

    def verify(self, roots):
        seen = set()
        for root in roots:
            self.base.validate_tree(root)
            seen.update(root.iterdir())
        require(seen == set(self.files), "corruption")
        for path, (length, expected, dev, ino) in self.files.items():
            item = path.stat()
            require((item.st_dev, item.st_ino, item.st_size) == (dev, ino, length), "corruption")
            if path.name != "LOCK":
                require(sha(self.base.read(path, length)) == expected, "corruption")


def configuration(protocol, go, protocol_sha256, go_sha256, rec):
    digest(protocol_sha256); digest(go_sha256)
    require(sha(protocol) == protocol_sha256 and sha(go) == go_sha256, "binding")
    p, g = parse(protocol), parse(go)
    require(type(p) is dict and p.get("schema") == "wd.m1-p3-protocol-lock.v1"
            and p.get("status") == "LOCKED", "binding")
    opaque(p.get("study_id"))
    keys(g, ("schema", "status", "study_id", "protocol_sha256", "baseline_manifest_sha256"))
    require(g["schema"] == "wd.m1-p3-collection-go.v1" and g["status"] == "LOCKED_COLLECTION_GO"
            and g["study_id"] == p["study_id"] and g["protocol_sha256"] == protocol_sha256, "binding")
    digest(g["baseline_manifest_sha256"])
    cfg = p.get("collection_controller")
    keys(cfg, CONFIG_FIELDS)
    start, end = map(rec._time, (cfg["collection_start_utc"], cfg["collection_end_utc"]))
    require(start < end, "clock")
    for field, lo, hi in (("poll_ms", 10, 1000), ("max_wall_seconds", 1, 259200),
                          ("baseline_resolution_us", 1, 1000000), ("max_records", 1, 10000),
                          ("max_record_delay_seconds", 1, 86400)):
        require(type(cfg[field]) is int and lo <= cfg[field] <= hi)
    require(cfg["baseline_resolution_us"] == 1000000, "clock")
    require(type(cfg["sources"]) is list and 1 <= len(cfg["sources"]) <= 32)
    require(type(cfg["allowed_reporters"]) is list and 1 <= len(cfg["allowed_reporters"]) <= 32, "identity")
    allowed = []
    for reporter in cfg["allowed_reporters"]:
        rec._identity(reporter)
        require(reporter["agent"] not in ("fable-5", "claude-rco-1"), "identity")
        allowed.append(canon(reporter))
    require(len(set(allowed)) == len(allowed), "identity")
    mappings = p.get("reporter_sources")
    require(type(mappings) is list and len(mappings) == len(allowed), "identity")
    reporter_native, native_sessions = {}, set()
    for entry in mappings:
        keys(entry, ("reporter", "native_session_id"))
        rec._identity(entry["reporter"])
        reporter_key = canon(entry["reporter"])
        native = native_uuid(entry["native_session_id"])
        require(reporter_key in allowed and reporter_key not in reporter_native
                and native not in native_sessions, "identity")
        reporter_native[reporter_key] = native
        native_sessions.add(native)
    require(set(reporter_native) == set(allowed), "identity")
    source_ids = set()
    for source in cfg["sources"]:
        keys(source, ("id", "schema", "reporter", "native_session_id", "evidence_path", "file_identity",
                      "initial_cursor", "max_range_bytes", "timestamp_resolution_us"))
        opaque(source["id"])
        require(source["id"] not in source_ids, "identity"); source_ids.add(source["id"])
        require(canon(source["reporter"]) in allowed, "identity")
        require(type(source["schema"]) is str and 1 <= len(source["schema"]) <= 80)
        require(native_uuid(source["native_session_id"]) == reporter_native[canon(source["reporter"])], "identity")
        source_identity(source["file_identity"])
        require(type(source["evidence_path"]) is str and len(source["evidence_path"]) <= 4096, "paths")
        require(type(source["initial_cursor"]) is int and 0 <= source["initial_cursor"] < 2**63, "source_cursor")
        require(type(source["max_range_bytes"]) is int and 1 <= source["max_range_bytes"] <= MAX_INPUT, "input_bounds")
        require(type(source["timestamp_resolution_us"]) is int and 1 <= source["timestamp_resolution_us"] <= 1000000, "clock")
    return p, g, cfg, start, end


def capsules(p, g, cfg, manifest, seal, rec, cap):
    """Deterministic adapters, not substitute original protocol/baseline IDs."""
    cap._keys(manifest, ("schema", "identity", "started_utc", "sealed_utc", "files"))
    require(manifest["schema"] == "wd.m1-p3-baseline.v1"
            and manifest["identity"].get("study_id") == p["study_id"]
            and manifest["identity"].get("protocol_sha256") == g["protocol_sha256"], "baseline")
    cap._keys(seal, ("schema", "status", "manifest_sha256", "sealed_utc"))
    require(seal["schema"] == "wd.m1-p3-baseline-seal.v1" and seal["status"] == "SEALED"
            and seal["manifest_sha256"] == g["baseline_manifest_sha256"], "baseline")
    actual = cap.parse_utc(seal["sealed_utc"])
    # A fractional native seal plus its separate ONE SECOND forward bound must
    # be contained in the whole-second recorder capsule, never rounded down.
    sealed_start = actual.replace(microsecond=0) + timedelta(seconds=bool(actual.microsecond))
    require(cfg["baseline_resolution_us"] == 1000000 and
            actual + timedelta(seconds=1) <= sealed_start + timedelta(seconds=1), "clock")
    mapping, blobs, names = [], {}, set()
    require(type(manifest["files"]) is list and 1 <= len(manifest["files"]) <= 1024, "baseline")
    for row in manifest["files"]:
        cap._keys(row, ("name", "sha256", "bytes"))
        name = row["name"]
        require(type(name) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.md", name)
                and name.lower() != "memory.md", "baseline")
        require(name.casefold() not in names, "lesson_collision"); names.add(name.casefold())
        ref = lesson_id(name)
        opaque(ref); digest(row["sha256"])
        require(ref not in blobs, "lesson_collision")
        blobs[ref] = row["sha256"]
        mapping.append({"name": name, "lesson_ref": ref, "sha256": row["sha256"]})
    mapping.sort(key=lambda row: row["name"])
    protocol = canon({"schema": "wd.prospective-protocol.v1", "study_id": p["study_id"],
                      "protocol_sha256": g["protocol_sha256"],
                      **{key: cfg[key] for key in ("collection_start_utc", "collection_end_utc",
                         "max_record_delay_seconds", "max_records", "allowed_reporters")}})
    baseline = canon({"schema": "wd.prospective-baseline.v1", "study_id": p["study_id"],
                      "protocol_sha256": g["protocol_sha256"], "baseline_sha256": g["baseline_manifest_sha256"],
                      "sealed_at_utc": sealed_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "sealed_time_resolution_seconds": 1, "lesson_blobs": blobs})
    bound = rec.Bindings.from_bytes(protocol, baseline, sha(protocol), sha(baseline),
                                   protocol_sha256=g["protocol_sha256"], baseline_sha256=g["baseline_manifest_sha256"])
    return protocol, baseline, mapping, bound


class Controller:
    def __init__(self, protocol, go, *, protocol_sha256, go_sha256, _test_runtime=None):
        self.protocol, self.go = protocol, go
        self.protocol_sha256, self.go_sha256 = protocol_sha256, go_sha256
        self.test_runtime = _test_runtime
        self.attempted = False
        self.counts = {"reported": 0, "accepted": 0, "primary_candidates": 0, "near_miss": 0,
                       "refused": 0, "duplicates": 0}
        self.journal_hashes = []
        self.input_hashes = {}
        self.recorder = self.runtime = self.host = self.control_lock = None
        self.roots = []
        self.consumed = False
        self.last_clock = None
        self.source_ranges = {}
        self.source_cursors = {}
        self.pending_proof = None

    def clock(self):
        now, mono = self.runtime.now(), self.runtime.monotonic()
        require(type(now) is datetime and now.tzinfo is timezone.utc
                and type(mono) in (int, float) and math.isfinite(mono), "clock")
        if self.last_clock:
            require(now >= self.last_clock[0] and mono >= self.last_clock[1], "clock")
            require(abs((now - self.last_clock[0]).total_seconds() -
                        (mono - self.last_clock[1])) <= CLOCK_DRIFT_TOLERANCE_SECONDS, "clock")
        self.last_clock = now, mono
        return now, mono

    def put(self, name, value):
        data = canon(value)
        self.host.write_new(self.control / name, data)
        return sha(data)

    def journal(self, result, nomination_hash, incident=None, proof=None):
        row = {"schema": "wd.m1-p3-collection-event.v1", "sequence": self.counts["reported"],
               "status": result, "nomination_sha256": nomination_hash, "incident_id": incident,
               "proof": proof, "counts": dict(self.counts)}
        digest_value = self.put("event-%08d.json" % self.counts["reported"], row)
        self.journal_hashes.append(digest_value)

    def source_range(self, source, interval, expected_hash):
        keys(interval, ("start", "end"))
        start, end = interval["start"], interval["end"]
        require(type(start) is int and type(end) is int, "source_cursor")
        key = (source["id"], start, end)
        replay = self.source_ranges.get(key)
        is_replay = replay is not None
        cursor = self.source_cursors.get(source["id"], source["initial_cursor"])
        lookback = None
        if not is_replay:
            require(end > cursor and start <= cursor, "source_cursor")
            if start < cursor:
                previous_receipt = self.source_cursors.get((source["id"], "receipt"))
                candidates = [row for row in self.source_ranges.values()
                              if row["source_id"] == source["id"] and row["end"] == cursor
                              and row["receipt_sha256"] == previous_receipt and row["start"] <= start]
                require(len(candidates) == 1, "source_cursor")
                lookback = candidates[0]
        if replay is not None:
            require(replay["range_sha256"] == expected_hash, "binding")
        data, receipt = self.runtime.read_source_range(source, start, end)
        require(receipt["range_sha256"] == digest(expected_hash), "binding")
        if replay is None:
            validation = None
            if lookback is not None:
                # A bounded, hash-verified lookback is not a cursor reset. It
                # preserves prior genuine-user context for consecutive actions.
                old, observed = self.runtime.read_source_range(source, lookback["start"], cursor)
                require(sha(old) == lookback["range_sha256"] and
                        data[:cursor-start] == old[start-lookback["start"]:], "binding")
                validation = {"receipt_sha256":lookback["receipt_sha256"],
                              "range_sha256":sha(old),"read_bytes":observed["read_bytes"]}
            receipt = {"schema":"wd.m1-p3-source-range.v1","source_id":source["id"],
                       "cursor_before":cursor,"overlap_bytes":cursor-start,
                       "lookback_validation":validation,
                       "previous_range_receipt_sha256":self.source_cursors.get((source["id"], "receipt")),
                       **receipt}
            receipt_sha = self.put("source-range-%08d.json" % self.counts["reported"], receipt)
            self.source_ranges[key] = {**receipt, "receipt_sha256":receipt_sha}
            self.source_cursors[source["id"]] = end
            self.source_cursors[(source["id"], "receipt")] = receipt_sha
            replay = self.source_ranges[key]
        # No cursor advances unless exclusive durable receipt/readback succeeded.
        return data, {**replay, "replay":is_replay,
                      "cursor_after":self.source_cursors[source["id"]]}

    def nomination(self, raw, file_id):
        rec, cap, cfg = self.rec, self.cap, self.cfg
        try:
            n = parse(raw)
            keys(n, ("schema", "study_id", "protocol_sha256", "baseline_manifest_sha256",
                     "incident_id", "incident_kind", "reporter", "gold_basename", "source_id",
                     "evidence_file", "source_range", "evidence_prefix_sha256", "reference"))
            require(n["schema"] == "wd.m1-p3-nomination.v1")
            opaque(n["incident_id"]); require(n["incident_id"] == file_id)
            require(n["incident_kind"] in ("recurred", "near_miss") and type(n["incident_kind"]) is str)
        except Stop as error:
            if str(error) in ("schema", "input_bounds"):
                raise Refuse("schema") from None
            raise
        require(n["study_id"] == self.p["study_id"] and n["protocol_sha256"] == self.protocol_sha256
                and n["baseline_manifest_sha256"] == self.g["baseline_manifest_sha256"], "binding")
        rec._identity(n["reporter"])
        require(canon(n["reporter"]) in {canon(x) for x in cfg["allowed_reporters"]}, "identity")
        sources = [x for x in cfg["sources"] if x["id"] == n["source_id"]]
        require(len(sources) == 1 and sources[0]["reporter"] == n["reporter"], "identity")
        source = sources[0]
        mapped = [entry["native_session_id"] for entry in self.p["reporter_sources"]
                  if entry["reporter"] == n["reporter"]]
        reference = n["reference"]
        require(len(mapped) == 1 and type(reference) is dict
                and reference.get("session_id") == source["native_session_id"] == mapped[0], "identity")
        if source["schema"] != "wd.m1-p3-claude-jsonl.v1":
            raise Refuse("unsupported_native_schema")
        require(type(n["evidence_file"]) is str and n["evidence_file"] == Path(source["evidence_path"]).name, "paths")
        evidence, range_proof = self.source_range(source, n["source_range"], n["evidence_prefix_sha256"])
        self.pending_proof = {"source_range":range_proof}
        try:
            dispositions = cap.native_record_dispositions(evidence)
            self.pending_proof["native_record_dispositions"] = dispositions
            result = cap.extract_action_evidence(evidence, reference=reference,
                        baseline_utc=self.seal["sealed_utc"],
                        collection_start_utc=cfg["collection_start_utc"],
                        collection_end_utc=cfg["collection_end_utc"],
                        timestamp_resolution_us=source["timestamp_resolution_us"])
        except cap.CaptureError as error:
            code = str(error)
            if code in CAPTURE_CASE: raise Refuse("unsupported_native_record") from None
            if code == "action_time_outside_strict_window": raise Refuse("outside_window") from None
            if code == "situation_empty": raise Refuse("empty_context") from None
            if "identity" in code or code in ("uuid_invalid", "duplicate_uuid"): raise Stop("identity") from None
            if "time" in code or "utc" in code: raise Stop("clock") from None
            raise Stop("binding") from None
        action = cap.parse_utc(result["action_utc"])
        floor = action.replace(microsecond=0)
        if cap.parse_utc(result["action_latest_utc"]) > floor + timedelta(seconds=1):
            raise Refuse("unsupported_resolution")
        lessons = [row for row in self.mapping if row["name"] == n["gold_basename"]]
        if len(lessons) != 1: raise Refuse("schema")
        selected = lessons[0]
        ref = sha(canon({"session_id": result["session_id"], "uuid": result["uuid"],
                        "tool_use_id": result["tool_use_id"]}))[:32]
        action_time = floor.strftime("%Y-%m-%dT%H:%M:%SZ")
        receipt = canon({"schema": "wd.prospective-action.v1", "action_ref": ref, "reporter": n["reporter"],
                         "action_time_utc": action_time, "action_time_resolution_seconds": 1,
                         "source_record_sha256": result["record_sha256"]})
        now, _ = self.clock()
        require(now < self.end, "clock")
        record = {"schema": "wd.prospective-incident.v1", "study_id": self.p["study_id"],
                  "protocol_sha256": self.protocol_sha256, "baseline_sha256": self.g["baseline_manifest_sha256"],
                  "incident_id": n["incident_id"], "incident_kind": n["incident_kind"], "reporter": n["reporter"],
                  "action_time_utc": action_time, "action_time_resolution_seconds": 1,
                  "recorded_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "action_ref": ref,
                  "action_sha256": result["record_sha256"], "action_receipt_sha256": sha(receipt),
                  "lesson_ref": selected["lesson_ref"], "baseline_blob_sha256": selected["sha256"]}
        proof = {key: result[key] for key in ("schema", "session_id", "uuid", "tool_use_id", "record_sha256",
                 "action_utc", "action_latest_utc", "timestamp_resolution_us", "supplied_jsonl_sha256")}
        proof["evidence_file"] = n["evidence_file"]
        proof["source_id"] = n["source_id"]
        proof.update(self.pending_proof)
        proof["action_receipt_sha256"] = sha(receipt)
        # Never export/store the adapter's private situation text in a receipt.
        response = self.recorder.enroll(record, receipt)
        if response["status"] == "refused":
            if response["disposition"] == "REFUSE_CASE":
                code = response["reason"]
                raise Refuse("duplicate" if code == "duplicate" else "outside_window" if code == "time" else "schema")
            raise Stop(self.host.last_failure or "recorder")
        require(response["status"] == "recorded", "recorder")
        self.counts["accepted"] += 1
        self.counts["primary_candidates"] += int(n["incident_kind"] == "recurred")
        self.counts["near_miss"] += int(n["incident_kind"] == "near_miss")
        proof["record_sha256_receipt"] = response["record_sha256"]
        return n["incident_id"], proof

    def result(self, status, reason, terminal_hash=None):
        return {"status": status, "reason": reason, "counts": dict(self.counts),
                "terminal_sha256": terminal_hash, "controlled_output_bytes": self.host.total if self.host else 0}

    def manual_stop_requested(self):
        signal = Path(self.cfg["stop_signal"])
        if not os.path.lexists(signal):
            return False
        document = parse(self.runtime.read(signal, 1024))
        require(document == {"schema": "wd.m1-p3-manual-stop.v1", "study_id": self.p["study_id"],
                             "protocol_sha256": self.protocol_sha256}, "manual_stop_invalid")
        return True

    def finish(self, reason):
        terminal = self.put("RESULT.json", {"schema": "wd.m1-p3-collection-result.v1", "status": "CLOSED",
                            "reason": reason, "counts": self.counts,
                            "journal_sha256": sha(canon(self.journal_hashes))})
        return self.result("CLOSED", reason, terminal)

    def run(self, *, execute=False):
        if execute is not True: return self.result("STOP_NOT_READY", "execute_required")
        if self.attempted: return self.result("STOP_NOT_READY", "already_attempted")
        self.attempted = True
        try:
            self.rec, self.cap = load_dependencies()
            self.p, self.g, self.cfg, self.start, self.end = configuration(
                self.protocol, self.go, self.protocol_sha256, self.go_sha256, self.rec)
            self.runtime = self.test_runtime or Runtime(self.rec, self.cap)
            self.runtime.validate(self.cfg)
            self.host = BudgetHost(self.runtime)
            now, self.start_mono = self.clock()
            require(self.start < now < self.end, "clock")
            self.control = Path(self.cfg["control_root"])
            self.ledger = Path(self.cfg["ledger_root"])
            require(not os.path.lexists(self.control) and not os.path.lexists(self.ledger), "destination_exists")
            require(self.host.free_bytes(self.control.parent) >= FREE_RESERVE + BUDGET, "disk")
            self.control.mkdir(); self.consumed = True; self.roots = [self.control]
            self.runtime.pin_new(self.control)
            self.put("intent.json", {"schema": "wd.m1-p3-collection-intent.v1", "attempt_consumed": True,
                     "study_id": self.p["study_id"], "protocol_sha256": self.protocol_sha256,
                     "go_sha256": self.go_sha256, "baseline_manifest_sha256": self.g["baseline_manifest_sha256"],
                     "started_utc": now.isoformat(timespec="microseconds").replace("+00:00", "Z")})
            self.host.write_new(self.control / "LOCK", b"0")
            try:
                self.control_lock = self.host.lock(self.control / "LOCK")
            except Exception:
                raise Stop("lock") from None
            if self.manual_stop_requested():
                return self.finish("manual_stop")
            self.manifest, self.seal = self.runtime.baseline(Path(self.cfg["baseline_root"]), self.g["baseline_manifest_sha256"])
            pc, bc, self.mapping, bindings = capsules(self.p, self.g, self.cfg, self.manifest, self.seal, self.rec, self.cap)
            self.host.write_new(self.control / "protocol-capsule.json", pc)
            self.host.write_new(self.control / "baseline-capsule.json", bc)
            self.put("lesson-mapping.json", self.mapping)
            self.recorder = self.rec.Recorder(Path(self.cfg["audit_root"]), self.ledger, bindings,
                                             mode="record", _test_host=self.host)
            admission = self.recorder.initialize()
            require(admission["status"] == "initialized", self.host.last_failure or "recorder")
            self.roots.append(self.ledger); self.runtime.pin_new(self.ledger)
            while True:
                now, mono = self.clock()
                self.host.verify(self.roots)
                require(self.host.free_bytes(self.control) >= FREE_RESERVE + BUDGET, "disk")
                if now >= self.end: reason = "collection_closed"; break
                require(mono - self.start_mono < self.cfg["max_wall_seconds"], "walltime")
                if self.manual_stop_requested(): reason = "manual_stop"; break
                inbox = Path(self.cfg["inbox"])
                self.runtime.host.validate_tree(inbox)
                paths = sorted(inbox.iterdir())
                require(len(paths) <= 4096, "input_bounds")
                current = set()
                read_bytes = 0
                for path in paths:
                    if self.manual_stop_requested(): return self.finish("manual_stop")
                    now, mono = self.clock()
                    require(now < self.end and mono - self.start_mono < self.cfg["max_wall_seconds"], "walltime")
                    match = NOM_FILE.fullmatch(path.name)
                    require(match is not None, "schema")
                    current.add(path.name)
                    data = self.runtime.read(path, MAX_INPUT)
                    read_bytes += len(data)
                    require(read_bytes <= BUDGET, "input_bounds")
                    expected = sha(data)
                    if path.name in self.input_hashes:
                        require(self.input_hashes[path.name] == expected, "input_changed")
                        continue
                    self.counts["reported"] += 1
                    self.pending_proof = None
                    try:
                        incident, proof = self.nomination(data, match[1])
                        self.journal({"disposition": "RECORDED", "reason": None}, expected, incident, proof)
                    except Refuse as refusal:
                        require(str(refusal) in CASE_REASONS, "unknown")
                        self.counts["refused"] += 1
                        self.counts["duplicates"] += int(str(refusal) == "duplicate")
                        self.journal({"disposition": "REFUSE_CASE", "reason": str(refusal)}, expected, proof=self.pending_proof)
                    self.input_hashes[path.name] = expected
                    now, mono = self.clock()
                    require(now < self.end and mono - self.start_mono < self.cfg["max_wall_seconds"], "walltime")
                require(set(self.input_hashes) <= current, "input_changed")
                self.runtime.sleep(min(self.cfg["poll_ms"] / 1000, (self.end - now).total_seconds(),
                                       self.cfg["max_wall_seconds"] - (mono - self.start_mono)))
            return self.finish(reason)
        except BaseException as error:
            reason = str(error) if isinstance(error, Stop) else "identity" if self.recorder_error(error, "identity") else "io" if isinstance(error, OSError) else "unknown"
            terminal = None
            if self.consumed and self.host is not None:
                try:
                    self.runtime.host.validate_tree(self.control, recurse=False)
                    terminal = self.put("STOP_NOT_READY.json", {"schema": "wd.m1-p3-collection-stop.v1",
                         "status": "STOP_NOT_READY", "reason": reason, "counts": self.counts,
                         "journal_sha256": sha(canon(self.journal_hashes))})
                except BaseException:
                    pass  # Explicit null terminal receipt, retained intent/partials.
            return self.result("STOP_NOT_READY", reason, terminal)
        finally:
            if self.recorder is not None: self.recorder.close()
            if self.control_lock is not None: self.control_lock.close()
            if self.runtime is not None: self.runtime.close()

    @staticmethod
    def recorder_error(error, reason):
        return type(error).__name__ == "Refusal" and str(error) == reason


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Stop("schema")


def main(argv=None):
    try:
        parser = Parser(add_help=False)
        parser.add_argument("--execute", action="store_true")
        for flag in ("protocol", "protocol-sha256", "go", "go-sha256"):
            parser.add_argument("--" + flag, required=True)
        args = parser.parse_args(argv)
        require(args.execute is True, "execute_required")
        rec, cap = load_dependencies()
        runtime = Runtime(rec, cap)
        try:
            documents = []
            for name in (args.protocol, args.go):
                path = cap._path(name, audit=True); cap._volume_check(path)
                documents.append(runtime.read(path, MAX_INPUT))
        finally:
            runtime.close()
        result = Controller(*documents, protocol_sha256=args.protocol_sha256, go_sha256=args.go_sha256).run(execute=True)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["status"] == "CLOSED" else 2
    except Exception as error:
        reason = str(error) if isinstance(error, Stop) else "unknown"
        print(json.dumps({"status": "STOP_NOT_READY", "reason": reason}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
