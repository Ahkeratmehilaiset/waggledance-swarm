"""Inactive single-writer Q4 publication checkpoint; no scheduler or live wiring.

Only cooperating writers using the same persistent lock are serialized. This
does not authenticate the caller, root, policy, generation or operating-system
clock, and cannot defeat hostile same-user replacement or filesystem TOCTOU.
The caller must externally anchor those inputs and the manifest/pointer it uses.
No shadow producer state is read or written by this module.
The source_binding is a caller-supplied build-receipt commitment, not proof
that an independently authenticated source actually produced the delta.
"""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import ctypes

from sandbox.mind.step2.reader_slot import reader_slot
from waggledance.core.bridge_log_reader import BridgeCursor

SCHEMA = "wd.q4-shared-checkpoint.v2"
HEX = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
IDENTITY = re.compile(r"(?:windows-v1:[0-9a-f]{8}:[0-9a-f]{16}|posix-v1:[0-9a-f]+:[0-9a-f]+)\Z")
GENERATION = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
ARTIFACTS = {"descriptor_sha256": "descriptor.json", "memory_sha256": "memory.json",
             "projection_sha256": "projection.json", "receipt_sha256": "build_receipt.json"}
POINTER = "q4-latest.json"
LOCK = "q4-checkpoint.lock"
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_MAX = 16 * 1024 * 1024


class CheckpointRefused(ValueError):
    """Fixed error; never returns a path, raw input or private backend text."""


def _refuse():
    raise CheckpointRefused("checkpoint_refused") from None


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _refuse()
        value[key] = item
    return value


def _parse(raw):
    if not isinstance(raw, bytes) or len(raw) > _MAX:
        _refuse()
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                       parse_constant=lambda _: _refuse())
    if not isinstance(value, dict):
        _refuse()
    return value


def _secure(path, *, missing_leaf=False):
    path = Path(path)
    # This inactive implementation supports fixed Windows volumes only. Python's
    # portable API cannot prove that a POSIX mount is local rather than network.
    if os.name != "nt":
        _refuse()
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        _refuse()
    if os.name == "nt" and (":" in str(path)[2:] or "/" in str(path)):
        _refuse()
    get_drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    get_drive_type.argtypes = (ctypes.c_wchar_p,)
    get_drive_type.restype = ctypes.c_uint
    if get_drive_type(path.anchor) != 3:  # DRIVE_FIXED, not removable/network
        _refuse()
    if any(part in (".", "..") or part.endswith((" ", ".")) for part in path.parts[1:]):
        _refuse()
    current = path
    while True:
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if not (missing_leaf and current == path):
                _refuse()
        else:
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
                _refuse()
        parent = current.parent
        if parent == current:
            break
        current = parent
    return path


def _read(path):
    _secure(path)
    if not path.is_file():
        _refuse()
    with path.open("rb") as stream:
        data = stream.read(_MAX + 1)
    if len(data) > _MAX:
        _refuse()
    return data


def _cursor(value):
    if (type(value) is not BridgeCursor or type(value.offset) is not int or value.offset < 0
            or not isinstance(value.file_identity, str) or IDENTITY.fullmatch(value.file_identity) is None
            or (value.generation is not None and (not isinstance(value.generation, str)
                                                  or GENERATION.fullmatch(value.generation) is None))):
        _refuse()
    return asdict(value)


def _source_binding(start, end, anchor, projection, policy, roster):
    from sandbox.mind.step2.shared_bridge_snapshot import _token
    return {"schema": "wd.q4-shared-index-source-binding.v1", "anchor_sha256": anchor,
            "projection_sha256": projection, "cursor_start_sha256": _sha(_token(start).encode("utf-8")),
            "cursor_end_sha256": _sha(_token(end).encode("utf-8")), "policy_sha256": policy,
            "roster_sha256": roster}


def _verify_generation(root, name, tasks, roster, policy_sha256, roster_sha256):
    """Verify the reader-compatible builder artifacts and healthy cell receipts."""
    from sandbox.mind.step2 import shared_projection as sp, wd_q4_private_read as reader
    if not isinstance(name, str) or NAME.fullmatch(name) is None:
        _refuse()
    directory = _secure(root / name)
    if not directory.is_dir():
        _refuse()
    # Cell loaders follow paths: reject existing reparse points across the tree.
    for base, dirs, files in os.walk(directory, followlinks=False):
        for item in dirs + files:
            _secure(Path(base) / item)
    contents = {key: _read(directory / path) for key, path in ARTIFACTS.items()}
    hashes = {key: _sha(data) for key, data in contents.items()}
    projection = _parse(contents["projection_sha256"])
    if json.dumps(projection, sort_keys=True, indent=1).encode("utf-8") != contents["projection_sha256"]:
        _refuse()
    sp.validate_projection(projection, approved_tasks=tasks, include_refs=False,
                           roster=roster, exclude_review_signal=True)
    for record in projection["records"]:
        meta = record["meta"]
        if meta["task_id"] == sp.UNKNOWN or meta["task_id"] not in tasks or meta["agent"] in ("claude-rco-1", "claude-rco-2"):
            _refuse()
    memory = _parse(contents["memory_sha256"])
    receipt = _parse(contents["receipt_sha256"])
    descriptor = reader.read_descriptor(directory / "descriptor.json", hashes["descriptor_sha256"])
    def strict_policy(value):
        return (type(value) is dict
                and set(value) == {"authorship", "review_signal_excluded", "policy_sha256", "roster_sha256"}
                and value["authorship"] == sp.AUTHORSHIP_ROSTER
                and value["review_signal_excluded"] is True
                and value["policy_sha256"] == policy_sha256
                and value["roster_sha256"] == roster_sha256)
    if (descriptor["memory_sha256"] != hashes["memory_sha256"]
            or memory.get("scope") != reader.SHARED_SCOPE or memory.get("projection_schema") != sp.SCHEMA
            or not strict_policy(memory.get("policy")) or not strict_policy(receipt.get("policy"))
            or memory.get("projection_sha256") != hashes["projection_sha256"]
            or receipt.get("schema") != "wd.bridge-mind-shared-index-build-receipt.v1"
            or receipt.get("scope") != reader.SHARED_SCOPE
            or receipt.get("descriptor_schema") != reader.DESCRIPTOR_SCHEMA
            or receipt.get("projection_schema") != sp.SCHEMA or receipt.get("projection_file") != "projection.json"
            or receipt.get("encoder") != descriptor["encoder"] or receipt.get("dim") != descriptor["dim"]
            or receipt.get("memory_sha256") != hashes["memory_sha256"]
            or receipt.get("descriptor_sha256") != hashes["descriptor_sha256"]
            or receipt.get("projection_sha256") != hashes["projection_sha256"]
            or receipt.get("memory_name") != name):
        _refuse()
    opened = reader.bind_memory(directory, descriptor)
    if (not opened.views or set(opened.views) != set(memory.get("cells", {}))
            or any(not view.healthy for view in opened.views.values())):
        _refuse()
    # Bind both stored views back to the approved projection, not merely its
    # declared digest. No extra or missing record/view may be published.
    approved = {record["record_id"]: record for record in projection["records"]}
    expected = {(record_id, view) for record_id in approved for view in ("w", "d")}
    observed = set()
    for view in opened.views.values():
        if len(view.rows) != len(view.collection._texts):
            _refuse()
        for row, text in zip(view.rows, view.collection._texts):
            provenance = row["provenance"]
            key = (provenance["record"], provenance["view"])
            record = approved.get(key[0])
            if (key not in expected or key in observed or record is None or text != record["stored_text"]
                    or row["time_utc"] != record["time_utc"]
                    or provenance["available_utc"] != record["provenance"]["available_utc"]
                    or provenance["event_ref"] != record["provenance"]["event_ref"]):
                _refuse()
            observed.add(key)
    if observed != expected:
        _refuse()
    return {"generation": name, **hashes, "source_binding": receipt.get("source_binding")}


def _write_new(path, data):
    _secure(path, missing_leaf=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def commit_generation(root, generation, start, end, approved_tasks, *, expected_pointer_sha256,
                      expected_artifacts, initial_cursor=None, timeout=0.2,
                      policy_bytes=None, expected_policy_sha256=None, roster_bytes=None,
                      expected_roster_sha256=None, expected_anchor_sha256=None):
    """Commit index + cursor together. CAS requires exact predecessor bytes.

    For a first publication expected_pointer_sha256 must be None and the
    caller must explicitly provide initial_cursor equal to start. Existing
    pointer history is saved immutably before replacing the pointer. A refusal
    never licenses a retry: after a potentially uncertain OS failure the caller
    must reread and verify the pointer before deciding what happened.
    """
    try:
        root = _secure(root)
        if (not root.is_dir() or not isinstance(generation, str) or NAME.fullmatch(generation) is None
                or not isinstance(expected_artifacts, dict) or set(expected_artifacts) != set(ARTIFACTS)
                or any(not isinstance(h, str) or HEX.fullmatch(h) is None for h in expected_artifacts.values())):
            _refuse()
        begin, finish = _cursor(start), _cursor(end)
        if (begin["file_identity"] != finish["file_identity"] or begin["generation"] != finish["generation"]
                or finish["offset"] < begin["offset"]):
            _refuse()
        from sandbox.mind.step2.shared_capture_adapter import strict_pins
        if not isinstance(expected_anchor_sha256, str) or HEX.fullmatch(expected_anchor_sha256) is None:
            _refuse()
        tasks, roster = strict_pins(policy_bytes, expected_policy_sha256, roster_bytes, expected_roster_sha256)
        if type(approved_tasks) is not frozenset or approved_tasks != tasks:
            _refuse()
        policy_sha = expected_policy_sha256
        pointer, lock = root / POINTER, root / LOCK
        _secure(lock, missing_leaf=True)
        with reader_slot(lock, timeout_seconds=timeout):
            _secure(lock)
            old = None
            if os.path.lexists(pointer):
                raw = _read(pointer)
                if (not isinstance(expected_pointer_sha256, str) or HEX.fullmatch(expected_pointer_sha256) is None
                        or _sha(raw) != expected_pointer_sha256 or initial_cursor is not None):
                    _refuse()
                old = _parse(raw)
                if (set(old) != {"schema", "generation", "artifacts", "policy_sha256", "roster_sha256", "anchor_sha256", "cursor_start", "cursor_end",
                                 "previous", "previous_pointer_sha256"} or old["schema"] != SCHEMA
                        or old["policy_sha256"] != policy_sha or old["roster_sha256"] != expected_roster_sha256
                        or not isinstance(old["anchor_sha256"], str) or HEX.fullmatch(old["anchor_sha256"]) is None
                        or old["cursor_end"] != begin
                        or not isinstance(old["generation"], str) or NAME.fullmatch(old["generation"]) is None
                        or old["generation"] == generation
                        or not isinstance(old["artifacts"], dict) or set(old["artifacts"]) != set(ARTIFACTS)):
                    _refuse()
                previous_artifacts = _verify_generation(root, old["generation"], tasks, roster,
                                                        policy_sha, expected_roster_sha256)
                old_start = BridgeCursor(**old["cursor_start"])
                old_end = BridgeCursor(**old["cursor_end"])
                _cursor(old_start)
                _cursor(old_end)
                if ({key: previous_artifacts[key] for key in ARTIFACTS} != old["artifacts"]
                        or previous_artifacts.get("source_binding") != _source_binding(
                            old_start, old_end, old["anchor_sha256"], old["artifacts"]["projection_sha256"],
                            policy_sha, expected_roster_sha256)):
                    _refuse()
            elif expected_pointer_sha256 is not None or initial_cursor is None or _cursor(initial_cursor) != begin:
                _refuse()
            actual = _verify_generation(root, generation, tasks, roster, policy_sha, expected_roster_sha256)
            if ({key: actual[key] for key in ARTIFACTS} != expected_artifacts
                    or actual.get("source_binding") != _source_binding(
                        start, end, expected_anchor_sha256, expected_artifacts["projection_sha256"],
                        policy_sha, expected_roster_sha256)):
                _refuse()
            value = {"schema": SCHEMA, "generation": generation, "artifacts": dict(expected_artifacts),
                     "policy_sha256": policy_sha, "roster_sha256": expected_roster_sha256,
                     "anchor_sha256": expected_anchor_sha256, "cursor_start": begin, "cursor_end": finish,
                     "previous": None if old is None else old["generation"],
                     "previous_pointer_sha256": None if old is None else expected_pointer_sha256}
            if old is not None:
                archive = root / ("q4-pointer-" + expected_pointer_sha256 + ".json")
                if os.path.lexists(archive):
                    if _read(archive) != raw:
                        _refuse()
                else:
                    # Complete and sync a private staging file before publishing
                    # the hash-named archive. On Windows rename is no-replace;
                    # an interrupted stage cannot poison the permanent name.
                    archive_stage = root / (".q4-pointer-" + secrets.token_hex(16) + ".staging")
                    _write_new(archive_stage, raw)
                    _secure(archive, missing_leaf=True)
                    os.rename(archive_stage, archive)
            staging = root / (".q4-latest-" + secrets.token_hex(16) + ".staging")
            _write_new(staging, _canonical(value))
            _secure(pointer, missing_leaf=True)
            os.replace(staging, pointer)
            return value
    except Exception:
        _refuse()
