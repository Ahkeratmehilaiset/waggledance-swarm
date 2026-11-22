"""Observe a bridge prefix when its generation sidecar is absent.

This is a low-level, default-off building block.  The caller must independently
authorize the deployment manifest pin and the selected tasks.  A matching hash
does not authenticate the manifest, the operator, the bridge source or senders.
The returned rows are PRIVATE, unscreened source data: they must never be
logged, sent to a lane, or treated as instructions.  A separate content policy
must screen them before use.  This module writes nothing and does not activate
any runtime, model, IPC, producer or bridge sidecar.

The explicit full_prefix_v1 scope scans the complete frozen LF prefix without
fixed prefix-byte, task-count or event-count ceilings. It still retains a
per-line byte limit and materializes selected rows and their bindings in memory;
there is no hard RSS guarantee, and allocation failure refuses. Identical
canonical events share one content-addressed row. This is not a disk-streaming
or full production-memory implementation.

The sidecar is checked absent at discrete points.  File identity and two full
hash passes fence observed changes, not hostile same-user ABA or a change after
the last check.  Generation and rotation continuity remain UNKNOWN.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import threading

from tools.bridge_compact_view import event_id
from waggledance.core import bridge_log_reader as bridge

SCHEMA = "wd.mind-observed-prefix-no-generation.v1"
MAX_PREFIX_BYTES = 256 * 1024 * 1024
MAX_CONTROL_BYTES = 4 * 1024 * 1024
MAX_ROW_BYTES = 4 * 1024 * 1024
MAX_SELECTED_ROW_BYTES = 65536
MAX_ROWS = 1_000_000
MAX_SELECTED_ROWS = 4096
MAX_TASKS = 128
CHUNK_BYTES = 1024 * 1024
_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")
_TASK = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")


class PrefixSnapshotRefused(ValueError):
    """Fixed refusal without paths, source content or chained exceptions."""


class _ObservedPrefixBudgetRefused(PrefixSnapshotRefused):
    """The fenced local tail exceeds the caller's full-prefix allowance."""


class _SourceReadBudget:
    """Shared in-process byte allowance; reserve before reading the source."""

    __slots__ = ('_remaining', '_lock')

    def __init__(self, limit):
        if type(limit) is not int or limit <= 0:
            raise ValueError('source_budget')
        self._remaining = limit
        self._lock = threading.Lock()

    def reserve(self, size):
        if type(size) is not int or size < 0:
            raise ValueError('source_budget')
        with self._lock:
            if size > self._remaining:
                raise ValueError('source_budget')
            self._remaining -= size

    def _refund(self, size):
        with self._lock:
            self._remaining += size


def _budgeted_read(stream, size, source_budget=None, *, line=False):
    if type(size) is not int or size < 0:
        raise ValueError('source_budget')
    if source_budget is not None:
        if type(source_budget) is not _SourceReadBudget:
            raise ValueError('source_budget')
        source_budget.reserve(size)
    data = stream.readline(size) if line else stream.read(size)
    if type(data) is not bytes or len(data) > size:
        raise ValueError('source_read')
    if source_budget is not None:
        source_budget._refund(size - len(data))
    return data


@dataclass(frozen=True, slots=True)
class ObservedPrefix:
    """Private candidate. Its rows are not privacy screened or authenticated."""

    metadata: dict = field(repr=False)
    rows: dict = field(repr=False)


@dataclass(frozen=True, slots=True)
class PrefixBinding:
    """Private immutable values retained independently before provider work.

    This is an in-process consistency token, not an authentication or isolation
    boundary. Never expose it or its unscreened rows to a provider. The caller
    must retain it separately and not recreate it after provider work.
    """

    metadata_bytes: bytes = field(repr=False)
    rows_bytes: bytes = field(repr=False)
    tasks: tuple[str, ...] | None = field(repr=False)


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _tasks(tasks) -> tuple[str, ...]:
    if (type(tasks) not in (set, frozenset) or not 1 <= len(tasks) <= MAX_TASKS
            or any(type(task) is not str or _TASK.fullmatch(task) is None for task in tasks)):
        raise ValueError("invalid_tasks")
    return tuple(sorted(tasks))


def _scope_tasks(scope, tasks):
    if type(scope) is not str:
        raise ValueError("invalid_scope")
    if scope == "selected_tasks_v1":
        return _tasks(tasks)
    if scope == "full_prefix_v1" and tasks is None:
        return None
    raise ValueError("invalid_scope")


def bind_prefix(snapshot, *, expected_tasks, scope="selected_tasks_v1"):
    """Freeze the observed result BEFORE a callback, independently of it.

    The caller must authorize the task set and retain the returned binding
    outside callback control. observed_utc is informational and is not trusted
    for freshness, even though its original bytes are retained for consistency.
    """
    result = None
    try:
        selected = _scope_tasks(scope, expected_tasks)
        if (type(snapshot) is not ObservedPrefix or type(snapshot.metadata) is not dict
                or type(snapshot.rows) is not dict
                or snapshot.metadata.get("scope") != ("full_observed_prefix" if selected is None else "bounded_observed_prefix")):
            raise ValueError("invalid_snapshot")
        for ref, row in snapshot.rows.items():
            if (type(ref) is not str or type(row) is not dict
                    or (selected is not None and row.get("task_id") not in selected)
                    or event_id(row) != ref):
                raise ValueError("invalid_row")
        result = PrefixBinding(_canonical(snapshot.metadata), _canonical(snapshot.rows), selected)
    except Exception:
        pass
    if result is None:
        raise PrefixSnapshotRefused("mind_prefix_snapshot_refused") from None
    return result


def _plain(path: Path, *, directory: bool) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid_path")
    if os.name == "nt":
        text = str(path)
        if (len(text) < 3 or not text[0].isalpha() or text[1:3] != ":\\"
                or ":" in text[2:] or any(part.endswith((".", " ")) for part in path.parts[1:])):
            raise ValueError("invalid_path")
    current = path
    first = True
    while True:
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("reparse_path")
        if first:
            if directory and not stat.S_ISDIR(info.st_mode):
                raise ValueError("not_directory")
            if not directory and not stat.S_ISREG(info.st_mode):
                raise ValueError("not_regular")
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError("invalid_parent")
        first = False
        if current.parent == current:
            break
        current = current.parent


def _sidecar_absent(path: Path) -> None:
    # Only positive ENOENT under an existing plain directory establishes the
    # observed state. A permission error, inaccessible parent or any entry
    # including a symlink/reparse point is a refusal.
    _plain(path.parent, directory=True)
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    raise ValueError("generation_present")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _read_pinned(path: Path, expected: str, limit: int) -> bytes:
    if type(expected) is not str or _HEX.fullmatch(expected) is None:
        raise ValueError("pin_invalid")
    _plain(path, directory=False)
    with bridge._open_log(path) as stream:
        identity = bridge._file_identity(stream)
        before = os.fstat(stream.fileno()).st_size
        if not 0 < before <= limit:
            raise ValueError("control_size")
        raw = stream.read(limit + 1)
        if (len(raw) != before or os.fstat(stream.fileno()).st_size != before
                or bridge._file_identity(stream) != identity
                or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected.lower())):
            raise ValueError("control_changed")
        _plain(path, directory=False)
        with bridge._open_log(path) as current:
            if bridge._file_identity(current) != identity:
                raise ValueError("control_replaced")
    return raw


def _deployment_source(bundle_root: str, expected_manifest_sha256: str):
    if type(bundle_root) is not str:
        raise ValueError("bundle_invalid")
    root = Path(bundle_root)
    _plain(root, directory=True)
    raw_manifest = _read_pinned(root / "deployment-manifest.json", expected_manifest_sha256, MAX_CONTROL_BYTES)
    manifest = json.loads(raw_manifest.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
    if (type(manifest) is not dict or type(manifest.get("schema_version")) is not int
            or manifest.get("schema_version") != 1 or type(manifest.get("files")) is not dict):
        raise ValueError("manifest_invalid")
    fleet_pin = manifest["files"].get("wd-fleet.json")
    raw_fleet = _read_pinned(root / "wd-fleet.json", fleet_pin, 1024 * 1024)
    fleet = json.loads(raw_fleet.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
    if (type(fleet) is not dict or type(fleet.get("schema_version")) is not int
            or fleet.get("schema_version") != 2):
        raise ValueError("fleet_invalid")
    runtime = fleet.get("runtime_root")
    if type(runtime) is not str:
        raise ValueError("fleet_invalid")
    source = Path(runtime) / "shared" / "events.jsonl"
    _plain(source, directory=False)
    return source, source.with_name("events.generation.json"), fleet_pin.lower()


def _fence(stream, source: Path, sidecar: Path, identity: str, minimum_size: int) -> int:
    _sidecar_absent(sidecar)
    _plain(source, directory=False)
    size = os.fstat(stream.fileno()).st_size
    if bridge._file_identity(stream) != identity or size < minimum_size:
        raise ValueError("source_changed")
    with bridge._open_log(source) as current:
        if bridge._file_identity(current) != identity or os.fstat(current.fileno()).st_size < minimum_size:
            raise ValueError("source_replaced")
    _sidecar_absent(sidecar)
    return size


def _hash_prefix(stream, end: int, *, source_budget=None) -> str:
    stream.seek(0)
    digest = hashlib.sha256()
    remaining = end
    while remaining:
        data = _budgeted_read(stream, min(CHUNK_BYTES, remaining), source_budget)
        if not data:
            raise ValueError("short_read")
        digest.update(data)
        remaining -= len(data)
    return digest.hexdigest()


def _scan(stream, end: int, tasks: frozenset[str] | None, *, source_budget=None):
    stream.seek(0)
    digest, pending, rows = hashlib.sha256(), bytearray(), {}
    remaining, scanned, first = end, 0, True
    while remaining:
        data = _budgeted_read(stream, min(CHUNK_BYTES, remaining), source_budget)
        if not data:
            raise ValueError("short_read")
        remaining -= len(data)
        digest.update(data)
        pending.extend(data)
        start = 0
        while True:
            newline = pending.find(b"\n", start)
            if newline < 0:
                break
            if newline + 1 - start > MAX_ROW_BYTES:
                raise ValueError("row_over_budget")
            line = bytes(pending[start:newline + 1])
            parsed = bridge._parse_rows(line, starts_at_zero=first)
            first = False
            scanned += 1
            if len(parsed) != 1 or (tasks is not None and scanned > MAX_ROWS):
                raise ValueError("row_count")
            row = parsed[0]
            if tasks is None or (type(row.get("task_id")) is str and row["task_id"] in tasks):
                if tasks is not None:
                    raw = json.dumps(row, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")
                    if len(raw) > MAX_SELECTED_ROW_BYTES or len(rows) >= MAX_SELECTED_ROWS:
                        raise ValueError("selected_row_over_budget")
                rows[event_id(row)] = row
            start = newline + 1
        if start:
            del pending[:start]
        if len(pending) >= MAX_ROW_BYTES:
            raise ValueError("row_over_budget")
    if pending or first:
        raise ValueError("incomplete_prefix")
    return digest.hexdigest(), rows


def read_complete_generationless_prefix_rows(source_path, generation_path, end_cursor, expected_prefix_sha256,
                                             *, source_budget=None):
    """Read every canonical event in one caller-anchored LF-complete prefix.

    The caller must independently bind the exact source, sidecar, cursor and
    digest to its approved manifest and policy. Absence at observed checks and
    equality to a caller hash authenticate neither source nor senders. No
    generation or continuity is asserted. Identical canonical events share a
    reference; rows are private, unfiltered, and materialized in memory. The
    two hash passes fence observed changes but cannot rule out same-user ABA.
    """
    result = None
    try:
        if (type(source_path) is not str or type(generation_path) is not str
                or type(end_cursor) is not bridge.BridgeCursor or bridge._validate_cursor(end_cursor)
                or end_cursor.generation is not None or end_cursor.offset <= 0
                or type(expected_prefix_sha256) is not str or _HEX.fullmatch(expected_prefix_sha256) is None):
            raise ValueError("invalid_input")
        source, sidecar = Path(source_path), Path(generation_path)
        if not source.is_absolute() or not sidecar.is_absolute() or sidecar != source.with_name("events.generation.json"):
            raise ValueError("invalid_sidecar")
        _plain(source, directory=False)
        _sidecar_absent(sidecar)
        with bridge._open_log(source) as stream:
            identity = bridge._file_identity(stream)
            if identity != end_cursor.file_identity:
                raise ValueError("identity_mismatch")
            size = _fence(stream, source, sidecar, identity, end_cursor.offset)
            if source_budget is None:
                first_hash, rows = _scan(stream, end_cursor.offset, None)
            else:
                first_hash, rows = _scan(stream, end_cursor.offset, None, source_budget=source_budget)
            if not hmac.compare_digest(first_hash, expected_prefix_sha256.lower()):
                raise ValueError("prefix_mismatch")
            size = _fence(stream, source, sidecar, identity, size)
            final_hash = (_hash_prefix(stream, end_cursor.offset) if source_budget is None else
                          _hash_prefix(stream, end_cursor.offset, source_budget=source_budget))
            if not hmac.compare_digest(final_hash, first_hash):
                raise ValueError("prefix_changed")
            _fence(stream, source, sidecar, identity, size)
        result = rows
    except Exception:
        pass
    if result is None:
        raise PrefixSnapshotRefused("mind_prefix_snapshot_refused") from None
    return result


def observe_prefix(*, enabled=False, bundle_root=None, expected_manifest_sha256=None, tasks=None,
                   scope="selected_tasks_v1", max_prefix_bytes=None):
    """Observe an LF-complete source prefix; return PRIVATE unscreened rows.

    ``expected_manifest_sha256`` must be anchored independently by the trusted
    caller; this function does not accept an arbitrary log path. The task set is
    a narrowing selector, not authorization. A live reader needs a separately
    approved content policy and must call ``verify_prefix`` after provider work.
    """
    result = None
    over_budget = False
    try:
        if enabled is not True:
            raise ValueError("invalid_input")
        if max_prefix_bytes is not None and (type(max_prefix_bytes) is not int or max_prefix_bytes <= 0):
            raise ValueError("invalid_prefix_limit")
        scoped_tasks = _scope_tasks(scope, tasks)
        selected = None if scoped_tasks is None else frozenset(scoped_tasks)
        source, sidecar, fleet_pin = _deployment_source(bundle_root, expected_manifest_sha256)
        _sidecar_absent(sidecar)
        with bridge._open_log(source) as stream:
            identity = bridge._file_identity(stream)
            initial_size = os.fstat(stream.fileno()).st_size
            tail = bridge.read_bridge_log_tail_lines(source, tail_rows=1, max_bytes=bridge.MAX_MAX_BYTES,
                                                     generation_path=sidecar)
            if (tail.status not in (bridge.BridgeReadStatus.OK, bridge.BridgeReadStatus.IDLE)
                    or tail.generation is not None or tail.file_identity != identity
                    or type(tail.end_offset) is not int or tail.end_offset <= 0
                    or (selected is not None and tail.end_offset > MAX_PREFIX_BYTES)):
                raise ValueError("tail_invalid")
            end = tail.end_offset
            size = _fence(stream, source, sidecar, identity, max(initial_size, end))
            if max_prefix_bytes is not None and end > max_prefix_bytes:
                over_budget = True
                raise _ObservedPrefixBudgetRefused()
            first_hash, rows = _scan(stream, end, selected)
            size = _fence(stream, source, sidecar, identity, max(initial_size, size, end))
            second_hash = _hash_prefix(stream, end)
            if not hmac.compare_digest(first_hash, second_hash):
                raise ValueError("prefix_changed")
            size = _fence(stream, source, sidecar, identity, max(initial_size, size, end))
            final_source, final_sidecar, final_fleet_pin = _deployment_source(bundle_root, expected_manifest_sha256)
            if (final_source != source or final_sidecar != sidecar or final_fleet_pin != fleet_pin):
                raise ValueError("deployment_changed")
            _fence(stream, source, sidecar, identity, max(initial_size, size, end))
        metadata = {"schema": SCHEMA, "scope": "full_observed_prefix" if selected is None else "bounded_observed_prefix", "authority": "none",
                    "generation": "UNKNOWN", "sidecar_observation": "ABSENT_AT_CHECKS",
                    "continuity": "UNKNOWN", "source_authentication": "UNKNOWN",
                    "file_identity": identity, "end_offset": end, "prefix_sha256": first_hash,
                    "fleet_sha256": fleet_pin, "manifest_sha256": expected_manifest_sha256.lower(),
                    "observed_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")}
        result = ObservedPrefix(metadata=metadata, rows=rows)
    except Exception as exc:
        over_budget = over_budget and type(exc) is _ObservedPrefixBudgetRefused
    if over_budget:
        raise _ObservedPrefixBudgetRefused("SOURCE_BUDGET") from None
    if result is None:
        raise PrefixSnapshotRefused("mind_prefix_snapshot_refused") from None
    return result


def verify_prefix(snapshot, *, expected_binding, expected_tasks, enabled=False,
                  bundle_root=None, expected_manifest_sha256=None, scope="selected_tasks_v1"):
    """Re-derive exact selected rows against an independently retained binding.

    The binding is captured with bind_prefix before any callback and must be
    retained outside callback control. Neither it nor observed_utc establishes
    event authenticity, trusted time, privacy, or protection from same-user ABA.
    A caller must consume its separately retained immutable, screened payload;
    a successful check cannot protect a mutable snapshot after this call returns.
    """
    ok = False
    try:
        if (enabled is not True or type(snapshot) is not ObservedPrefix
                or type(snapshot.metadata) is not dict or type(snapshot.rows) is not dict
                or type(expected_binding) is not PrefixBinding
                or _scope_tasks(scope, expected_tasks) != expected_binding.tasks
                or not hmac.compare_digest(_canonical(snapshot.metadata), expected_binding.metadata_bytes)
                or not hmac.compare_digest(_canonical(snapshot.rows), expected_binding.rows_bytes)):
            raise ValueError("invalid_input")
        m = snapshot.metadata
        required = {"schema", "scope", "authority", "generation", "sidecar_observation", "continuity",
                    "source_authentication", "file_identity", "end_offset", "prefix_sha256", "fleet_sha256",
                    "manifest_sha256", "observed_utc"}
        full = expected_binding.tasks is None
        if (set(m) != required or m["schema"] != SCHEMA
                or m["scope"] != ("full_observed_prefix" if full else "bounded_observed_prefix")
                or m["authority"] != "none" or m["generation"] != "UNKNOWN"
                or m["sidecar_observation"] != "ABSENT_AT_CHECKS" or m["continuity"] != "UNKNOWN"
                or m["source_authentication"] != "UNKNOWN" or type(m["file_identity"]) is not str
                or not m["file_identity"] or type(m["end_offset"]) is not int
                or m["end_offset"] <= 0 or (not full and m["end_offset"] > MAX_PREFIX_BYTES)
                or type(m["prefix_sha256"]) is not str
                or _HEX.fullmatch(m["prefix_sha256"]) is None):
            raise ValueError("invalid_snapshot")
        source, sidecar, fleet_pin = _deployment_source(bundle_root, expected_manifest_sha256)
        if (m["manifest_sha256"] != expected_manifest_sha256.lower() or m["fleet_sha256"] != fleet_pin):
            raise ValueError("pin_changed")
        _sidecar_absent(sidecar)
        with bridge._open_log(source) as stream:
            initial_size = _fence(stream, source, sidecar, m["file_identity"], m["end_offset"])
            tasks = None if full else frozenset(expected_binding.tasks)
            derived_hash, derived_rows = _scan(stream, m["end_offset"], tasks)
            if (not hmac.compare_digest(derived_hash, m["prefix_sha256"])
                    or not hmac.compare_digest(_canonical(derived_rows), expected_binding.rows_bytes)):
                raise ValueError("rows_changed")
            size = _fence(stream, source, sidecar, m["file_identity"], initial_size)
            if not hmac.compare_digest(_hash_prefix(stream, m["end_offset"]), m["prefix_sha256"]):
                raise ValueError("prefix_changed")
            stream.seek(m["end_offset"] - 1)
            if stream.read(1) != b"\n":
                raise ValueError("boundary_changed")
            size = _fence(stream, source, sidecar, m["file_identity"], size)
            final_source, final_sidecar, final_fleet_pin = _deployment_source(bundle_root, expected_manifest_sha256)
            if (final_source != source or final_sidecar != sidecar or final_fleet_pin != fleet_pin):
                raise ValueError("deployment_changed")
            _fence(stream, source, sidecar, m["file_identity"], size)
            if (not hmac.compare_digest(_canonical(snapshot.metadata), expected_binding.metadata_bytes)
                    or not hmac.compare_digest(_canonical(snapshot.rows), expected_binding.rows_bytes)
                    or _scope_tasks(scope, expected_tasks) != expected_binding.tasks):
                raise ValueError("snapshot_changed")
        ok = True
    except Exception:
        pass
    if not ok:
        raise PrefixSnapshotRefused("mind_prefix_snapshot_refused") from None
    return True
