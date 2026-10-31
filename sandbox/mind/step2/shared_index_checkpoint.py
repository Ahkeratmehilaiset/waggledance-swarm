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

SCHEMA = "wd.q4-shared-checkpoint.v3"
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
# Fail closed at a bounded history depth. Retention/compaction requires a
# separately designed, externally anchored checkpoint protocol.
_MAX_POINTER_HISTORY = 256
_MAX_POINTER_BYTES = 16384


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


def _read(path, *, max_bytes=_MAX):
    _secure(path)
    if not path.is_file():
        _refuse()
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
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


def _validated_previous(root, raw, tasks, roster, policy_sha, roster_sha, *, reserve=0):
    """Validate bounded archived v3 transitions and the current index binding.

    The current pointer still needs an independent caller-provided anchor;
    historical index files are not re-opened or claimed to be retrievable.
    """
    from sandbox.mind.step2 import shared_projection as sp
    from sandbox.mind.step2.shared_bridge_snapshot import _token

    fields = {"schema", "generation", "artifacts", "policy_sha256", "roster_sha256", "anchor_sha256",
              "cursor_start", "cursor_end", "watermark", "empty_advance", "previous", "previous_pointer_sha256"}
    empty_hash = _sha(_canonical(sp._projection([], {}, 0, roster, True)))

    def parse_pointer(data):
        if not isinstance(data, bytes) or len(data) > _MAX_POINTER_BYTES:
            _refuse()
        value = _parse(data)
        if (set(value) != fields or value["schema"] != SCHEMA
                or value["policy_sha256"] != policy_sha or value["roster_sha256"] != roster_sha
                or not isinstance(value["anchor_sha256"], str) or HEX.fullmatch(value["anchor_sha256"]) is None
                or not isinstance(value["generation"], str) or NAME.fullmatch(value["generation"]) is None
                or type(value["artifacts"]) is not dict or set(value["artifacts"]) != set(ARTIFACTS)
                or any(not isinstance(v, str) or HEX.fullmatch(v) is None for v in value["artifacts"].values())):
            _refuse()
        begin, finish, mark = (_cursor(BridgeCursor(**value[key]))
                               for key in ("cursor_start", "cursor_end", "watermark"))
        if (begin["file_identity"] != finish["file_identity"] or begin["generation"] != finish["generation"]
                or mark["file_identity"] != finish["file_identity"] or mark["generation"] != finish["generation"]
                or finish["offset"] < begin["offset"] or mark["offset"] < finish["offset"]):
            _refuse()
        return value

    old = parse_pointer(raw)
    actual = _verify_generation(root, old["generation"], tasks, roster, policy_sha, roster_sha)
    if ({key: actual[key] for key in ARTIFACTS} != old["artifacts"]
            or actual.get("source_binding") != _source_binding(
                BridgeCursor(**old["cursor_start"]), BridgeCursor(**old["cursor_end"]),
                old["anchor_sha256"], old["artifacts"]["projection_sha256"], policy_sha, roster_sha)):
        _refuse()

    current = old
    seen = {_sha(raw)}
    for depth in range(_MAX_POINTER_HISTORY):
        advance = current["empty_advance"]
        previous_hash = current["previous_pointer_sha256"]
        if advance is None and current["watermark"] != current["cursor_end"]:
            _refuse()
        if previous_hash is None:
            if current["previous"] is not None or advance is not None or depth + 1 + reserve > _MAX_POINTER_HISTORY:
                _refuse()
            return old
        if (not isinstance(previous_hash, str) or HEX.fullmatch(previous_hash) is None
                or previous_hash in seen):
            _refuse()
        seen.add(previous_hash)
        previous_raw = _read(root / ("q4-pointer-" + previous_hash + ".json"), max_bytes=_MAX_POINTER_BYTES)
        if _sha(previous_raw) != previous_hash:
            _refuse()
        previous = parse_pointer(previous_raw)
        if current["previous"] != previous["generation"]:
            _refuse()
        if advance is None:
            if current["generation"] == previous["generation"] or current["cursor_start"] != previous["watermark"]:
                _refuse()
        else:
            if (type(advance) is not dict or set(advance) != {"schema", "anchor_sha256", "projection_sha256",
                    "cursor_start_sha256", "cursor_end_sha256", "policy_sha256", "roster_sha256"}
                    or advance["schema"] != "wd.q4-empty-watermark.v1"
                    or any(not isinstance(advance[k], str) or HEX.fullmatch(advance[k]) is None for k in
                           ("anchor_sha256", "projection_sha256", "cursor_start_sha256", "cursor_end_sha256"))
                    or advance["policy_sha256"] != policy_sha or advance["roster_sha256"] != roster_sha
                    or advance["projection_sha256"] != empty_hash
                    or any(current[k] != previous[k] for k in
                           ("generation", "artifacts", "cursor_start", "cursor_end", "anchor_sha256"))):
                _refuse()
            start, end = (BridgeCursor(**value) for value in (previous["watermark"], current["watermark"]))
            if (start.file_identity != end.file_identity or start.generation != end.generation
                    or start.offset >= end.offset
                    or advance["cursor_start_sha256"] != _sha(_token(start).encode("utf-8"))
                    or advance["cursor_end_sha256"] != _sha(_token(end).encode("utf-8"))):
                _refuse()
        current = previous
    _refuse()


def _publish_pointer(root, pointer, raw, old_hash, value):
    if raw is not None:
        archive = root / ("q4-pointer-" + old_hash + ".json")
        if os.path.lexists(archive):
            if _read(archive) != raw:
                _refuse()
        else:
            archive_stage = root / (".q4-pointer-" + secrets.token_hex(16) + ".staging")
            _write_new(archive_stage, raw)
            _secure(archive, missing_leaf=True)
            os.rename(archive_stage, archive)
    staging = root / (".q4-latest-" + secrets.token_hex(16) + ".staging")
    _write_new(staging, _canonical(value))
    _secure(pointer, missing_leaf=True)
    os.replace(staging, pointer)


def advance_empty_delta(root, snapshot_result, approved_tasks, *, expected_pointer_sha256,
                        policy_bytes, expected_policy_sha256, roster_bytes, expected_roster_sha256,
                        expected_anchor_sha256, timeout=0.2):
    """Advance only a verified strict empty delta, without altering the indexed interval.

    Inactive primitive: the caller must protect source identity, cursor, policy,
    roster, pointer and code from forgery. The snapshot binding is in-process,
    not source authentication. Pointer files contain source offsets and belong
    in a private state directory, not a public event stream. An ambiguous write
    requires re-reading the current pointer, never a blind retry.
    """
    try:
        from sandbox.mind.step2 import shared_bridge_snapshot as snapshot, shared_capture_adapter as adapter
        from sandbox.mind.step2 import shared_projection as projection
        root = _secure(root)
        if (not root.is_dir() or not isinstance(expected_pointer_sha256, str)
                or HEX.fullmatch(expected_pointer_sha256) is None
                or not isinstance(expected_anchor_sha256, str) or HEX.fullmatch(expected_anchor_sha256) is None):
            _refuse()
        tasks, roster = adapter.strict_pins(policy_bytes, expected_policy_sha256, roster_bytes, expected_roster_sha256)
        if type(approved_tasks) is not frozenset or tasks != approved_tasks or type(snapshot_result) is not dict:
            _refuse()
        delta = snapshot_result.get("projection")
        if snapshot.verify_anchored_projection(snapshot_result, delta, expected_anchor_sha256=expected_anchor_sha256,
                expected_policy_sha256=expected_policy_sha256, expected_roster_sha256=expected_roster_sha256) is not True:
            _refuse()
        projection.validate_projection(delta, approved_tasks=tasks, include_refs=False, roster=roster,
                                       exclude_review_signal=True)
        if delta["accepted"] != 0 or delta["records"] != [] or delta["refused"] != {} or delta["dropped_fields"] != 0:
            _refuse()
        end = snapshot_result.get("candidate_cursor")
        finish = _cursor(end)
        source = snapshot_result.get("source")
        if (type(source) is not dict or source.get("cursor_end_sha256") != _sha(snapshot._token(end).encode("utf-8"))
                or source.get("coverage") != "delta_only_not_complete_history"):
            _refuse()
        pointer, lock = root / POINTER, root / LOCK
        _secure(lock, missing_leaf=True)
        with reader_slot(lock, timeout_seconds=timeout):
            _secure(lock)
            raw = _read(pointer, max_bytes=_MAX_POINTER_BYTES)
            if _sha(raw) != expected_pointer_sha256:
                _refuse()
            old = _validated_previous(root, raw, tasks, roster, expected_policy_sha256, expected_roster_sha256)
            start = BridgeCursor(**old["watermark"])
            begin = _cursor(start)
            if (source.get("cursor_start_sha256") != _sha(snapshot._token(start).encode("utf-8"))
                    or begin["file_identity"] != finish["file_identity"] or begin["generation"] != finish["generation"]
                    or finish["offset"] < begin["offset"]):
                _refuse()
            # Recheck the in-process binding immediately before any write.
            if snapshot.verify_anchored_projection(snapshot_result, delta, expected_anchor_sha256=expected_anchor_sha256,
                    expected_policy_sha256=expected_policy_sha256, expected_roster_sha256=expected_roster_sha256) is not True:
                _refuse()
            if finish == begin:
                return {"status": "no_change", "pointer_sha256": expected_pointer_sha256}
            # Only a real advance needs room for another pointer. Revalidate
            # under the same writer lock before publishing that pointer.
            _validated_previous(root, raw, tasks, roster, expected_policy_sha256, expected_roster_sha256,
                                reserve=1)
            value = dict(old)
            value.update(watermark=finish, previous=old["generation"], previous_pointer_sha256=expected_pointer_sha256,
                empty_advance={"schema": "wd.q4-empty-watermark.v1", "anchor_sha256": expected_anchor_sha256,
                    "projection_sha256": _sha(_canonical(delta)),
                    "cursor_start_sha256": _sha(snapshot._token(start).encode("utf-8")),
                    "cursor_end_sha256": _sha(snapshot._token(end).encode("utf-8")),
                    "policy_sha256": expected_policy_sha256, "roster_sha256": expected_roster_sha256})
            _publish_pointer(root, pointer, raw, expected_pointer_sha256, value)
            return {"status": "advanced", "pointer_sha256": _sha(_canonical(value))}
    except Exception:
        _refuse()


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
                raw = _read(pointer, max_bytes=_MAX_POINTER_BYTES)
                if (not isinstance(expected_pointer_sha256, str) or HEX.fullmatch(expected_pointer_sha256) is None
                        or _sha(raw) != expected_pointer_sha256 or initial_cursor is not None):
                    _refuse()
                old = _validated_previous(root, raw, tasks, roster, policy_sha, expected_roster_sha256, reserve=1)
                if old["watermark"] != begin or old["generation"] == generation:
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
                     "watermark": finish, "empty_advance": None,
                     "previous": None if old is None else old["generation"],
                     "previous_pointer_sha256": None if old is None else expected_pointer_sha256}
            _publish_pointer(root, pointer, None if old is None else raw, expected_pointer_sha256, value)
            return value
    except Exception:
        _refuse()
