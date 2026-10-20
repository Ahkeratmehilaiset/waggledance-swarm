"""Dormant stdlib M1-P3 instruments; no corpus/transcript paths are discovered.

Capture requires --execute and two supplied final JSON documents plus an exact
LOCKED_CAPTURE_GO authorization envelope. This module does not grant collection
authority. Capture destinations are fresh children of the persistent audit root;
an intent, even without a terminal receipt, consumes that destination. No repair,
adoption, replacement baseline, case construction, ranking or grading is provided.

Evidence schema is deliberately narrow: supplied UTF-8 JSONL user/assistant
records with sessionId, UUID, timestamp and role/content. Unsupported native record types
refuse. Full-record hashes cover exact JSON bytes excluding the line terminator.
An explicit timestamp resolution upper bound is interpreted conservatively as a
forward uncertainty interval, never as evidence of actual clock synchronization.

Blind packages provide schema/path separation and integrity, not OS isolation,
cryptographic secrecy, semantic gold validation or complete leakage detection.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid


AUDIT_ROOT = Path("C:/Python/project2/.codex-audit")
BUDGET_BYTES = 64 * 1024 * 1024
FREE_RESERVE_BYTES = 3 * 1024 * 1024 * 1024
TERMINAL_RESERVE_BYTES = 512
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_NOT_GENUINE = ("[SYSTEM NOTIFICATION", "<task-notification>",
                "This session is being continued", "<command-name>", "<local-command-stdout>")
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


class CaptureError(Exception):
    """Closed refusal or STOP_NOT_READY; no private payload in the exception."""


def _fail(code):
    raise CaptureError(code)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _json(data):
    if not isinstance(data, bytes) or not data or len(data) > BUDGET_BYTES:
        _fail("json_bytes_invalid")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: _fail("json_nonfinite"))
    except (UnicodeError, ValueError, RecursionError):
        _fail("malformed_json")


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        _fail("schema_keys")


def _hash(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        _fail("sha256_invalid")
    return value


def _id(value):
    if not isinstance(value, str) or not _OPAQUE.fullmatch(value):
        _fail("opaque_id_invalid")
    return value


def parse_utc(value):
    """Only explicit UTC Z, whole seconds or 1..6 fractional digits."""
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        _fail("utc_invalid")
    try:
        return dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail("utc_invalid")


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _path(value, *, audit=False):
    try:
        p = Path(value)
    except (TypeError, ValueError):
        _fail("path_type_invalid")
    # Reject aliases rather than resolving/adopting them. Drive-local C only.
    if (not p.is_absolute() or p.drive.casefold() != "c:" or
            any(part in (".", "..") or ":" in part or part.endswith((".", " "))
                for part in p.parts[1:]) or str(value).startswith(("\\\\", "//"))):
        _fail("path_not_local_c")
    if audit and (p == AUDIT_ROOT or AUDIT_ROOT not in p.parents):
        _fail("outside_audit_root")
    return p


def _disjoint(paths):
    for i, left in enumerate(paths):
        for right in paths[i + 1:]:
            if left == right or left in right.parents or right in left.parents:
                _fail("overlapping_trees")


def _volume_check(path):
    if os.name != "nt":
        _fail("windows_ntfs_required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    if kernel.GetDriveTypeW("C:\\") != 3:  # DRIVE_FIXED; network/removable/RAM refused.
        _fail("fixed_c_drive_required")
    fs = ctypes.create_unicode_buffer(32)
    if not kernel.GetVolumeInformationW(ctypes.c_wchar_p("C:\\"), None, 0, None, None, None, fs, len(fs)):
        _fail("volume_identity_unknown")
    if fs.value != "NTFS":
        _fail("ntfs_required")


@contextlib.contextmanager
def _pin(path):
    """Pin an existing path without following a reparse point; deny write/delete.

    Ancestors must be pinned first. Handle identity is checked after opening to
    close the lstat/open race. Handles remain live until the operation finishes.
    """
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                       ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create.restype = ctypes.c_void_p
    close = kernel.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    handle = create(str(path), 0x80000000, 1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        _fail("path_open_uncertain")
    try:
        class Tag(ctypes.Structure):
            _fields_ = [("attributes", ctypes.c_uint32), ("tag", ctypes.c_uint32)]
        info = Tag()
        getinfo = kernel.GetFileInformationByHandleEx
        getinfo.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        if not getinfo(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            _fail("path_identity_unknown")
        if info.attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            _fail("reparse_refused")
        getpath = kernel.GetFinalPathNameByHandleW
        getpath.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        buffer = ctypes.create_unicode_buffer(32768)
        length = getpath(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer) or buffer.value.casefold() != ("\\\\?\\" + str(path)).casefold():
            _fail("path_identity_mismatch")
        yield
    finally:
        close(handle)


def _pin_chain(stack, path):
    for ancestor in reversed((path, *path.parents)):
        if not ancestor.exists():
            _fail("parent_missing")
        stack.enter_context(_pin(ancestor))


def _space(path):
    usage = shutil.disk_usage(path)
    free = usage.free if hasattr(usage, "free") else usage[2]
    if free < FREE_RESERVE_BYTES + BUDGET_BYTES:
        _fail("free_space_insufficient")


def _exclusive_write(path, data):
    # No overwrite and no adoption. Every call owns a new file or refuses.
    with open(path, "xb") as stream:
        if stream.write(data) != len(data):
            _fail("short_write")
        stream.flush()
        os.fsync(stream.fileno())
    with _pin(path):
        if path.read_bytes() != data:
            _fail("write_readback_mismatch")


def _read_bounded(path, limit=BUDGET_BYTES):
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        _fail("read_budget_exceeded")
    return data


class _Writer:
    def __init__(self, root):
        self.root = root
        self.total = 0

    def put(self, name, data, *, terminal=False):
        limit = BUDGET_BYTES if terminal else BUDGET_BYTES - TERMINAL_RESERVE_BYTES
        if self.total + len(data) > limit:
            _fail("budget_exceeded")
        _space(self.root)
        self.total += len(data)  # A partial write also consumes the allowance.
        _exclusive_write(self.root / name, data)


def _consume(root):
    if os.path.lexists(root):
        _fail("destination_exists")
    root.mkdir()  # Exclusive directory claim. Parent must already exist/pinned.
    return _Writer(root)


def _terminal_failure(writer, exc):
    # Never hide a failed write or erase partial bytes. Intent alone consumes.
    try:
        writer.put("STOP_NOT_READY.json", _canon({"status": "STOP_NOT_READY", "error": str(exc) if isinstance(exc, CaptureError) else type(exc).__name__, "utc": _now()}), terminal=True)
        return True
    except (OSError, CaptureError):
        return False  # Caller explicitly reports terminal_receipt_unavailable.


def _authority(source, destination, preregistration, protocol, identity):
    _keys(identity, ("schema", "status", "study_id", "capture_id", "source_dir", "destination", "preregistration_sha256", "protocol_sha256"))
    if identity["schema"] != "wd.m1-p3-capture-authorization.v1" or identity["status"] != "LOCKED_CAPTURE_GO":
        _fail("capture_authority_not_locked")
    _id(identity["study_id"])
    _id(identity["capture_id"])
    for field, data, schema in (("preregistration", preregistration, "wd.m1-prospective-preregistration.v1"),
                                ("protocol", protocol, "wd.m1-p3-protocol-lock.v1")):
        doc = _json(data)
        if _sha(data) != _hash(identity[field + "_sha256"]):
            _fail("authority_hash_mismatch")
        if not isinstance(doc, dict) or doc.get("schema") != schema or doc.get("status") != "LOCKED" or doc.get("study_id") != identity["study_id"]:
            _fail("authority_document_not_locked")
    if _path(identity["source_dir"]) != source or _path(identity["destination"], audit=True) != destination:
        _fail("authority_path_mismatch")


def _lessons(source):
    files = []
    for child in source.iterdir():
        info = child.lstat()
        if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT or child.is_symlink():
            _fail("reparse_refused")
        if child.suffix.casefold() == ".md" and child.name.casefold() != "memory.md":
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                _fail("lesson_identity_invalid")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.md", child.name, re.I):
                _fail("lesson_name_invalid")
            files.append(child)
    files.sort(key=lambda p: (p.name.casefold(), p.name))
    if not files or len({p.name.casefold() for p in files}) != len(files):
        _fail("corpus_empty_or_ambiguous")
    return files


def capture_baseline(source_dir, destination, *, preregistration, protocol, identity, execute=False):
    """Capture once. Explicit supplied paths only; never invoked on import."""
    if execute is not True:
        _fail("execute_required")
    source, out = _path(source_dir), _path(destination, audit=True)
    _authority(source, out, preregistration, protocol, identity)
    _disjoint((source, out))
    _volume_check(out)
    with contextlib.ExitStack() as stack:
        _pin_chain(stack, source)
        _pin_chain(stack, out.parent)
        if os.path.lexists(out):
            _fail("destination_exists")
        _space(out.parent)
        writer = _consume(out)
        stack.enter_context(_pin(out))
        try:
            started = _now()
            writer.put("intent.json", _canon({"schema": "wd.m1-p3-capture-intent.v1", "identity": identity, "started_utc": started, "attempt_consumed": True}))
            (out / "blobs").mkdir()
            stack.enter_context(_pin(out / "blobs"))
            files = _lessons(source)
            rows, blobs, corpus_bytes = [], set(), 0
            for path in files:
                stack.enter_context(_pin(path))
                before = path.stat()
                if before.st_size > BUDGET_BYTES - writer.total:
                    _fail("budget_exceeded")
                data = path.read_bytes()
                corpus_bytes += len(data)
                if corpus_bytes > BUDGET_BYTES:
                    _fail("corpus_budget_exceeded")
                after = path.stat()
                if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or len(data) != before.st_size):
                    _fail("source_changed")
                digest = _sha(data)
                if digest not in blobs:
                    writer.put("blobs/" + digest, data)
                    blobs.add(digest)
                rows.append({"name": path.name, "sha256": digest, "bytes": len(data)})
            if files != _lessons(source):
                _fail("source_listing_changed")
            for path, row in zip(files, rows):
                if _sha(path.read_bytes()) != row["sha256"]:
                    _fail("source_changed")
            sealed = _now()
            if parse_utc(sealed) < parse_utc(started):
                _fail("clock_rollback")
            manifest = {"schema": "wd.m1-p3-baseline.v1", "identity": identity, "started_utc": started, "sealed_utc": sealed, "files": rows}
            data = _canon(manifest)
            writer.put("manifest.json", data)
            receipt = {"schema": "wd.m1-p3-baseline-seal.v1", "status": "SEALED", "manifest_sha256": _sha(data), "sealed_utc": _now()}
            if parse_utc(receipt["sealed_utc"]) < parse_utc(sealed):
                _fail("clock_rollback")
            writer.put("SEALED.json", _canon(receipt), terminal=True)
            return receipt
        except (OSError, CaptureError) as exc:
            terminal = _terminal_failure(writer, exc)
            raise CaptureError("capture_failed:" + (str(exc) if isinstance(exc, CaptureError) else type(exc).__name__) + ("" if terminal else ":terminal_receipt_unavailable")) from exc


def verify_baseline(destination, *, expected_manifest_sha256):
    """Read and verify supplied sealed output; never repairs or adopts it."""
    expected = _hash(expected_manifest_sha256)
    out = _path(destination, audit=True)
    _volume_check(out)
    with contextlib.ExitStack() as stack:
        _pin_chain(stack, out)
        for name in ("intent.json", "manifest.json", "SEALED.json"):
            stack.enter_context(_pin(out / name))
        receipt = _json(_read_bounded(out / "SEALED.json"))
        _keys(receipt, ("schema", "status", "manifest_sha256", "sealed_utc"))
        manifest_bytes = _read_bounded(out / "manifest.json")
        if receipt["schema"] != "wd.m1-p3-baseline-seal.v1" or receipt["status"] != "SEALED" or _hash(receipt["manifest_sha256"]) != _sha(manifest_bytes) or expected != _sha(manifest_bytes):
            _fail("seal_mismatch")
        manifest = _json(manifest_bytes)
        _keys(manifest, ("schema", "identity", "started_utc", "sealed_utc", "files"))
        if manifest["schema"] != "wd.m1-p3-baseline.v1" or not isinstance(manifest["files"], list) or not manifest["files"]:
            _fail("manifest_invalid")
        intent = _json(_read_bounded(out / "intent.json"))
        _keys(intent, ("schema", "identity", "started_utc", "attempt_consumed"))
        if intent["schema"] != "wd.m1-p3-capture-intent.v1" or intent["attempt_consumed"] is not True or intent["identity"] != manifest["identity"] or intent["started_utc"] != manifest["started_utc"]:
            _fail("intent_mismatch")
        if not parse_utc(manifest["started_utc"]) <= parse_utc(manifest["sealed_utc"]) <= parse_utc(receipt["sealed_utc"]):
            _fail("seal_time_invalid")
        stack.enter_context(_pin(out / "blobs"))
        names, digests = [], set()
        total = 0
        for row in manifest["files"]:
            _keys(row, ("name", "sha256", "bytes"))
            if not isinstance(row["name"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.md", row["name"], re.I) or row["name"].casefold() == "memory.md":
                _fail("manifest_name_invalid")
            digest = _hash(row["sha256"])
            if type(row["bytes"]) is not int or row["bytes"] < 0 or row["bytes"] > BUDGET_BYTES:
                _fail("manifest_size_invalid")
            blob = out / "blobs" / digest
            stack.enter_context(_pin(blob))
            data = _read_bounded(blob, row["bytes"])
            if len(data) != row["bytes"] or _sha(data) != digest:
                _fail("blob_hash_mismatch")
            names.append(row["name"])
            digests.add(digest)
            total += len(data)
        if names != sorted(names, key=lambda n: (n.casefold(), n)) or len(set(n.casefold() for n in names)) != len(names):
            _fail("manifest_order_invalid")
        if set(p.name for p in out.iterdir()) != {"intent.json", "manifest.json", "SEALED.json", "blobs"} or set(p.name for p in (out / "blobs").iterdir()) != digests or total > BUDGET_BYTES:
            _fail("baseline_tree_mismatch")
        return receipt


_OPAQUE_NATIVE_RECORD_TYPES = frozenset({
    "attachment", "queue-operation", "system", "last-prompt", "custom-title",
    "agent-name", "mode", "permission-mode", "atis-latch", "api-request-blob",
    "file-history-delta", "api-request", "file-history-snapshot", "cost-state",
    "api-request-shape", "pr-link",
})
_NATIVE_COMMON_METADATA = frozenset({
    "parentUuid", "cwd", "gitBranch", "isSidechain", "version", "entrypoint",
    "slug", "session_id", "userType", "agentId", "permissionMode", "isMeta",
})
_NATIVE_ASSISTANT_METADATA = frozenset({
    "requestId", "apiBlockIndex", "effort", "perTurnEffort", "advisorModel",
    "thinkingDurationMs", "wireToolInputs",
})
_NATIVE_USER_METADATA = frozenset({
    "promptId", "sourceToolAssistantUUID", "toolUseResult", "permissionDecision",
    "origin", "turnOrigin",
})


def _content(record):
    """Validate declared native shapes; opaque fields never supply context.

    sessionId is the native UUID. Optional session_id is an opaque alias and is
    neither normalized nor equated with sessionId/reference.session_id.
    """
    if not isinstance(record, dict) or record.get("type") not in ("user", "assistant"):
        _fail("unsupported_record_type")
    if not isinstance(record, dict) or not isinstance(record.get("sessionId"), str):
        _fail("session_identity_missing")
    try:
        if str(uuid.UUID(record["sessionId"])) != record["sessionId"]:
            _fail("session_identity_invalid")
    except (ValueError, AttributeError, TypeError):
        _fail("session_identity_invalid")
    metadata = _NATIVE_COMMON_METADATA | (_NATIVE_ASSISTANT_METADATA if record["type"] == "assistant" else _NATIVE_USER_METADATA)
    _keys(record, ("type", "uuid", "timestamp", "message", "sessionId"), metadata)
    if "isMeta" in record and type(record["isMeta"]) is not bool:
        _fail("meta_flag_invalid")
    try:
        if str(uuid.UUID(record["uuid"])) != record["uuid"]:
            _fail("uuid_invalid")
    except (ValueError, AttributeError, TypeError):
        _fail("uuid_invalid")
    parse_utc(record["timestamp"])
    message = record["message"]
    _keys(message, ("role", "content"), ("id", "type", "model", "stop_reason", "stop_sequence", "usage", "container", "context_management"))
    if message["role"] != record["type"]:
        _fail("role_mismatch")
    content = message["content"]
    if isinstance(content, str) and record["type"] == "user":
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        _fail("content_invalid")
    tool_ids = set()
    for item in content:
        if not isinstance(item, dict):
            _fail("content_invalid")
        kind = item.get("type")
        if kind == "text":
            _keys(item, ("type", "text"))
            if not isinstance(item["text"], str):
                _fail("text_invalid")
        elif kind == "tool_use" and record["type"] == "assistant":
            _keys(item, ("type", "id", "name", "input"), ("caller",))
            if not isinstance(item["input"], dict) or not isinstance(item["name"], str) or not item["name"] or not isinstance(item["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item["id"]) or item["id"] in tool_ids:
                _fail("tool_identity_invalid")
            tool_ids.add(item["id"])
        elif kind == "tool_result" and record["type"] == "user":
            _keys(item, ("type", "tool_use_id", "content"), ("is_error",))
            if not isinstance(item["tool_use_id"], str) or not isinstance(item["content"], (str, list)):
                _fail("tool_result_invalid")
            if "is_error" in item and type(item["is_error"]) is not bool:
                _fail("tool_result_invalid")
            if isinstance(item["content"], list):
                for block in item["content"]:
                    _keys(block, ("type", "text"))
                    if block["type"] != "text" or not isinstance(block["text"], str):
                        _fail("tool_result_invalid")
        elif kind == "thinking" and record["type"] == "assistant":
            _keys(item, ("type", "thinking", "signature"))
            if not isinstance(item["thinking"], str) or not isinstance(item["signature"], str):
                _fail("thinking_shape_invalid")
        elif kind == "tool_addition":
            _keys(item, ("type", "tool"))  # Tool definition itself is opaque.
        else:
            _fail("unsupported_content_type")
    return content


def _collapse(text):
    return " ".join(text.split())


def _context_candidate(record, content):
    """Literal predicate; only these candidates can become timed context.

    Non-meta assistant text, or non-meta user text without tool_result blocks,
    after paired reminder removal and the fixed notification exclusions. No
    thinking/signature, tool input/result, tool definition, caller, attachment,
    optional metadata, or action-record text is a source of situation text.
    """
    if content is None or record.get("isMeta", False):
        return None
    text = " ".join(item["text"] for item in content if item["type"] == "text")
    if record["type"] == "user":
        if any(item["type"] == "tool_result" for item in content):
            return None
        text = _REMINDER.sub(" ", text).strip()
        if any(tag in text[:200] for tag in _NOT_GENUINE):
            return None
    text = _collapse(text)
    return (record["type"], text) if text else None


def _native_prefix(jsonl):
    """Parse all supplied bounded bytes, including explicitly opaque records.

    Opaque types require only an object with an explicitly named type; all
    remaining fields have no context/time/session semantics. String UUIDs are
    still checked for collisions across the entire prefix, including opaque
    records, so an opaque record cannot hide a second action identity.
    """
    if not isinstance(jsonl, bytes) or not jsonl or len(jsonl) > BUDGET_BYTES:
        _fail("evidence_bytes_invalid")
    rows, contents, hashes, contexts, seen = [], [], [], [], set()
    counts = {"native_records": 0, "opaque_records": 0, "context_candidate_records": 0,
              "native_without_context_records": 0, "opaque_by_type": {},
              "excluded_blocks": {"thinking": 0, "tool_use": 0, "tool_result": 0, "tool_addition": 0}}
    for line in jsonl.splitlines():
        record = _json(line)
        if not isinstance(record, dict) or not isinstance(record.get("type"), str):
            _fail("record_shape_invalid")
        kind = record["type"]
        if kind in _OPAQUE_NATIVE_RECORD_TYPES:
            content = context = None
            counts["opaque_records"] += 1
            counts["opaque_by_type"][kind] = counts["opaque_by_type"].get(kind, 0) + 1
        else:
            content = _content(record)  # Unknown types/shapes refuse, never skip.
            context = _context_candidate(record, content)
            counts["native_records"] += 1
            counts["context_candidate_records" if context else "native_without_context_records"] += 1
            for block in content:
                if block["type"] in counts["excluded_blocks"]:
                    counts["excluded_blocks"][block["type"]] += 1
        identity = record.get("uuid")
        if isinstance(identity, str):
            if identity in seen:
                _fail("duplicate_uuid")
            seen.add(identity)
        rows.append(record)
        contents.append(content)
        hashes.append(_sha(line))
        contexts.append(context)
    counts["opaque_by_type"] = dict(sorted(counts["opaque_by_type"].items()))
    return rows, contents, hashes, contexts, counts


def native_record_dispositions(jsonl):
    """Pure counted disposition of original bounded bytes, never a data reader.

    Candidate counts precede anchor selection and are NOT admitted-context or
    incident counts. The final action's text can be a candidate here, but is
    always excluded by extract_action_evidence. Payloads are never returned.
    """
    rows, _, _, _, counts = _native_prefix(jsonl)
    return {"schema": "wd.m1-p3-native-record-dispositions.v1",
            "supplied_jsonl_sha256": _sha(jsonl), "supplied_bytes": len(jsonl),
            "records_total": len(rows), **counts}


def _represented_resolution_us(stamp):
    parse_utc(stamp)
    digits = len(stamp.split(".")[1][:-1]) if "." in stamp else 0
    return 10 ** (6 - digits)


def extract_action_evidence(jsonl, *, reference, baseline_utc, collection_start_utc, collection_end_utc, timestamp_resolution_us):
    """Pure adapter; exact action must be the final original supplied record.

    Native resolution (1..1,000,000us) applies to the action and selected literal
    context only; .mmmZ requires at least1000us. Baseline availability always has
    a separate1second forward bound, then a strict additional1second guard.
    Start uses its represented precision's forward upper bound; close uses its
    earliest represented UTC. Whole-second boundaries do not change native
    resolution. Opaque/unused native records never enter context clock checks.

    NEW strict pre-action recipe, not byte-identical DAAE: select the last genuine
    user candidate, plus subsequent pre-action assistant text candidates. Collapse
    whitespace; user over244 -> first120+' .. '+last120; owntext tail250; join
    with ' | '; cap600. No tool description/input, thinking, result or action
    text. Older unselected candidates have no context-time role. Every selected
    candidate's latest possible UTC must be strictly before action earliest UTC;
    no fallback to an older candidate on uncertainty.
    """
    rows, contents, hashes, contexts, _ = _native_prefix(jsonl)
    _keys(reference, ("session_id", "uuid", "tool_use_id", "record_sha256", "action_utc"))
    sessions = {row["sessionId"] for row, content in zip(rows, contents) if content is not None}
    if not isinstance(reference["session_id"], str) or len(sessions) != 1 or reference["session_id"] not in sessions:
        _fail("session_identity_mismatch")
    if type(timestamp_resolution_us) is not int or not 1 <= timestamp_resolution_us <= 1_000_000:
        _fail("timestamp_resolution_unknown")
    delta = dt.timedelta(microseconds=timestamp_resolution_us)
    matches = [i for i, row in enumerate(rows) if row.get("uuid") == reference["uuid"]]
    if len(matches) != 1 or matches[0] != len(rows) - 1:
        _fail("exact_action_not_final")
    index = matches[0]
    action = rows[index]
    when = parse_utc(action["timestamp"])
    if action["type"] != "assistant" or _hash(reference["record_sha256"]) != hashes[index] or reference["action_utc"] != action["timestamp"]:
        _fail("action_identity_mismatch")
    tools = [item for item in contents[index] if item["type"] == "tool_use"]
    if not tools or len(tools) > 1 and reference["tool_use_id"] is None:
        _fail("tool_selection_ambiguous")
    selected = [item for item in tools if item["id"] == reference["tool_use_id"]] if reference["tool_use_id"] is not None else tools
    if len(selected) != 1:
        _fail("tool_identity_mismatch")
    chosen = []
    for i in range(index - 1, -1, -1):
        if contexts[i] is not None:
            chosen.append(i)
            if contexts[i][0] == "user":
                break
    chosen.reverse()
    for i in [*chosen, index]:
        if _represented_resolution_us(rows[i]["timestamp"]) > timestamp_resolution_us:
            _fail("timestamp_resolution_understated")
    baseline, start, end = map(parse_utc, (baseline_utc, collection_start_utc, collection_end_utc))
    try:
        baseline_upper = baseline + dt.timedelta(seconds=1)
        start_upper = start + dt.timedelta(microseconds=_represented_resolution_us(collection_start_utc))
        action_upper = when + delta
        outside = not baseline_upper < start or not start < end or not when > baseline_upper + dt.timedelta(seconds=1) or not when >= start_upper or not action_upper <= end
        times = [parse_utc(rows[i]["timestamp"]) for i in chosen]
        uncertain = any(t + delta >= when for t in times) or times != sorted(times)
    except OverflowError:
        _fail("timestamp_bounds_overflow")
    if outside:
        _fail("action_time_outside_strict_window")
    if uncertain:
        _fail("preaction_time_uncertain")
    user = next((contexts[i][1] for i in chosen if contexts[i][0] == "user"), "")
    own = [contexts[i][1] for i in chosen if contexts[i][0] == "assistant"]
    if len(user) > 244:
        user = user[:120] + " .. " + user[-120:]
    parts = [user, _collapse(" ".join(own))[-250:]]
    situation = " | ".join(part for part in parts if part)[:600]
    if not situation:
        _fail("situation_empty")
    return {"schema": "wd.m1-p3-claude-evidence.v1", "session_id": action["sessionId"], "uuid": action["uuid"], "tool_use_id": selected[0]["id"], "record_sha256": hashes[index], "action_utc": action["timestamp"], "action_latest_utc": (when + delta).isoformat(timespec="microseconds").replace("+00:00", "Z"), "timestamp_resolution_us": timestamp_resolution_us, "supplied_jsonl_sha256": _sha(jsonl), "preaction_record_sha256": hashes[:index], "situation": situation, "situation_sha256": _sha(situation.encode("utf-8"))}


def project_blind_case(row):
    """Exact executor schema. Builder must separately run pinned semantic guards."""
    _keys(row, ("id", "situation", "snapshot_sha256"))
    _id(row["id"])
    _hash(row["snapshot_sha256"])
    text = row["situation"]
    if not isinstance(text, str) or not text.strip() or len(text) > 600 or any(ord(c) < 32 for c in text):
        _fail("blind_situation_invalid")
    if re.search(r"\b(?:gold(?:_basename)?|nomination|nonce|recurred)\b|\[\[|\.md\b|[A-Za-z]:[\\/]|\\|(?:^|\s)\.{1,2}/|(?:^|\s)/", text, re.I):
        _fail("blind_obvious_leak_or_path")
    return {"id": row["id"], "situation": text, "snapshot_sha256": row["snapshot_sha256"]}


def write_blind_package(rows, arm_dir, *, nomination_dir, score_dir, execute=False):
    """Write only validated projected fields into a fresh separate local tree."""
    if execute is not True:
        _fail("execute_required")
    arm, nomination, score = (_path(p, audit=True) for p in (arm_dir, nomination_dir, score_dir))
    _disjoint((arm, nomination, score))
    projected = [project_blind_case(row) for row in rows]
    if not projected or len({row["id"] for row in projected}) != len(projected):
        _fail("blind_empty_or_duplicate")
    data = b"".join(_canon(row) + b"\n" for row in sorted(projected, key=lambda row: row["id"]))
    if len(data) > BUDGET_BYTES:
        _fail("budget_exceeded")
    _volume_check(arm)
    with contextlib.ExitStack() as stack:
        for path in (arm, nomination, score):
            _pin_chain(stack, path if path.exists() else path.parent)
        _space(arm.parent)
        writer = _consume(arm)
        stack.enter_context(_pin(arm))
        try:
            writer.put("intent.json", _canon({"schema": "wd.m1-p3-blind-intent.v1", "attempt_consumed": True, "utc": _now()}))
            writer.put("cases.jsonl", data)
            receipt = {"schema": "wd.m1-p3-blind-package.v1", "cases_sha256": _sha(data), "cases": len(projected), "sealed_utc": _now()}
            writer.put("SEALED.json", _canon(receipt), terminal=True)
            return receipt
        except (OSError, CaptureError) as exc:
            terminal = _terminal_failure(writer, exc)
            raise CaptureError("blind_package_failed" + ("" if terminal else ":terminal_receipt_unavailable")) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--preregistration", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--identity", required=True)
    args = parser.parse_args(argv)
    try:
        if not args.execute:
            _fail("execute_required")
        # Authorization bytes are supplied explicitly; no default or discovery.
        blobs = []
        for supplied in (args.preregistration, args.protocol, args.identity):
            path = _path(supplied, audit=True)
            _volume_check(path)
            with contextlib.ExitStack() as stack:
                _pin_chain(stack, path)
                if path.stat().st_size > BUDGET_BYTES:
                    _fail("json_bytes_invalid")
                blobs.append(_read_bounded(path))
        result = capture_baseline(args.source_dir, args.destination, preregistration=blobs[0], protocol=blobs[1], identity=_json(blobs[2]), execute=True)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (CaptureError, OSError) as exc:
        print(json.dumps({"status": "STOP_NOT_READY", "error": str(exc) if isinstance(exc, CaptureError) else type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
