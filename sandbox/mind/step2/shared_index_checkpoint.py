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
HISTORY_SCHEMA = "wd.q4-shared-checkpoint.v4"
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
_MAX_HISTORY_BYTES = 64 * 1024 * 1024


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


def _requested_source_mode(expected_mode, expected_public_policy_sha256):
    if expected_mode == "legacy" and expected_public_policy_sha256 is None:
        return {"status_mode": "legacy", "public_policy_sha256": None}
    if (expected_mode == "public-v1" and isinstance(expected_public_policy_sha256, str)
            and HEX.fullmatch(expected_public_policy_sha256) is not None):
        return {"status_mode": "public-v1", "public_policy_sha256": expected_public_policy_sha256}
    _refuse()


def _source_mode(memory, receipt):
    """Require an identical persistent mode/policy in both hashed artifacts.

    Only old artifacts with the field absent from both are treated as legacy.
    The hashes are integrity pins, not proof against a hostile same-user writer.
    """
    if type(memory) is not dict or type(receipt) is not dict:
        _refuse()
    if "source_mode" not in memory and "source_mode" not in receipt:
        return _requested_source_mode("legacy", None)
    if "source_mode" not in memory or "source_mode" not in receipt:
        _refuse()
    mode = memory["source_mode"]
    if (type(mode) is not dict or set(mode) != {"status_mode", "public_policy_sha256"}
            or receipt["source_mode"] != mode):
        _refuse()
    return _requested_source_mode(mode["status_mode"], mode["public_policy_sha256"])


def _snapshot_mode_args(tasks, mode, public_policy_bytes, anchor_bytes):
    if mode["status_mode"] == "legacy":
        if public_policy_bytes is not None or anchor_bytes is not None:
            _refuse()
        return {}
    from sandbox.mind.step2.shared_capture_adapter import public_status_policy
    public_status_policy(public_policy_bytes, mode["public_policy_sha256"], tasks)
    if type(anchor_bytes) is not bytes:
        _refuse()
    return {"expected_mode": "public-v1", "expected_public_policy_sha256": mode["public_policy_sha256"],
            "expected_anchor_bytes": anchor_bytes}


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
    return {"generation": name, **hashes, "source_binding": receipt.get("source_binding"),
            "source_mode": _source_mode(memory, receipt)}


def _write_new(path, data):
    _secure(path, missing_leaf=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _archived_generation_mode(root, pointer, budget):
    """Hash-check only the retained metadata needed for mode continuity.

    Historical FAISS cells are intentionally not opened here; the existing
    active-generation verifier still opens only the active index. The metadata
    bytes counted here share the bounded history-transition budget.
    """
    values = {}
    for kind in ("memory_sha256", "receipt_sha256"):
        raw = _read(root / pointer["generation"] / ARTIFACTS[kind])
        budget[0] += len(raw)
        if budget[0] > _MAX_HISTORY_BYTES or _sha(raw) != pointer["artifacts"][kind]:
            _refuse()
        values[kind] = _parse(raw)
    return _source_mode(values["memory_sha256"], values["receipt_sha256"])


def _validate_history_transition(root, current, previous, previous_hash, tasks, roster, policy_sha, roster_sha,
                                 policy_bytes, roster_bytes, budget, *, delta_bytes=None):
    """Rebuild one union from retained metadata deltas; hashes are not authentication.

    Each v4 transition retains its delta. Historical v3 transitions predate
    this protocol and cannot be reconstructed from their receipts alone.
    The generation directories, delta files and archives must remain available.
    """
    from sandbox.mind.step2 import shared_history, shared_projection as sp
    from sandbox.mind.step2.shared_bridge_snapshot import _token

    if policy_bytes is None or roster_bytes is None:
        _refuse()

    def bounded(path):
        data = _read(path)
        budget[0] += len(data)
        if budget[0] > _MAX_HISTORY_BYTES:
            _refuse()
        return data

    def artifact(pointer, kind):
        data = bounded(root / pointer["generation"] / ARTIFACTS[kind])
        if _sha(data) != pointer["artifacts"][kind]:
            _refuse()
        return data

    previous_raw = artifact(previous, "projection_sha256")
    merged_raw = artifact(current, "projection_sha256")
    previous_projection = _parse(previous_raw)
    merged_projection = _parse(merged_raw)
    for obj, raw in ((previous_projection, previous_raw), (merged_projection, merged_raw)):
        if json.dumps(obj, sort_keys=True, indent=1).encode("utf-8") != raw:
            _refuse()
        sp.validate_projection(obj, approved_tasks=tasks, include_refs=False, roster=roster,
                               exclude_review_signal=True)
    digest = current["delta_projection_sha256"]
    raw_delta = bounded(root / ("q4-delta-" + digest + ".json")) if delta_bytes is None else delta_bytes
    if delta_bytes is not None:
        budget[0] += len(raw_delta)
        if budget[0] > _MAX_HISTORY_BYTES:
            _refuse()
    delta = _parse(raw_delta)
    if (_sha(raw_delta) != digest or json.dumps(delta, sort_keys=True, indent=1).encode("utf-8") != raw_delta
            or not delta.get("records")):
        _refuse()
    sp.validate_projection(delta, approved_tasks=tasks, include_refs=False, roster=roster,
                           exclude_review_signal=True)
    merged = shared_history.merge_projections(previous_projection, delta, policy_bytes=policy_bytes,
                                               policy_sha256=policy_sha, roster_bytes=roster_bytes,
                                               roster_sha256=roster_sha)
    if json.dumps(merged, sort_keys=True, indent=1).encode("utf-8") != merged_raw:
        _refuse()

    receipt = _parse(artifact(current, "receipt_sha256"))
    memory = _parse(artifact(current, "memory_sha256"))
    old_receipt = _parse(artifact(previous, "receipt_sha256"))
    old_binding = old_receipt.get("source_binding")
    previous_delta = (previous["artifacts"]["projection_sha256"] if previous["schema"] == SCHEMA
                      else previous["delta_projection_sha256"])
    if _canonical(old_binding) != _canonical(_source_binding(
            BridgeCursor(**previous["cursor_start"]), BridgeCursor(**previous["cursor_end"]),
            previous["anchor_sha256"], previous_delta, policy_sha, roster_sha)):
        _refuse()
    binding = _source_binding(BridgeCursor(**current["cursor_start"]), BridgeCursor(**current["cursor_end"]),
                              current["anchor_sha256"], digest, policy_sha, roster_sha)
    if _canonical(receipt.get("source_binding")) != _canonical(binding):
        _refuse()
    previous_ids = {item["record_id"] for item in previous_projection["records"]}
    delta_ids = {item["record_id"] for item in delta["records"]}
    summary = {"memory_sha256": previous["artifacts"]["memory_sha256"],
               "projection_sha256": previous["artifacts"]["projection_sha256"],
               "build_receipt_sha256": previous["artifacts"]["receipt_sha256"],
               "records": len(previous_projection["records"]), "source_bound": True,
               "cursor_end_sha256": old_binding["cursor_end_sha256"]}
    reported = receipt.get("history")
    if type(reported) is not dict:
        _refuse()
    route = reported.get("continuity_route")
    if route == "receipt_end":
        if previous["watermark"] != previous["cursor_end"]:
            _refuse()
        watermark = None
    elif route == "verified_watermark":
        watermark = {"pointer_sha256": previous_hash,
                     "watermark_sha256": _sha(_token(BridgeCursor(**previous["watermark"])).encode("utf-8")),
                     "generation": previous["generation"]}
    else:
        _refuse()
    expected = {"schema": "wd.q4-shared-index-history.v1", "helper": "sandbox.mind.step2.shared_history",
                "predecessor": summary, "delta_projection_sha256": digest,
                "delta_records": len(delta["records"]), "delta_refused": dict(sorted(delta["refused"].items())),
                "overlap_records": len(previous_ids & delta_ids), "merged_records": len(merged["records"]),
                "merged_projection_sha256": current["artifacts"]["projection_sha256"],
                "continuity_checked": True, "continuity_route": route, "watermark": watermark}
    expected_memory = {"predecessor_memory_sha256": summary["memory_sha256"],
                       "predecessor_projection_sha256": summary["projection_sha256"],
                       "delta_projection_sha256": digest}
    if (receipt.get("delta_projection_sha256") != digest
            or _canonical(reported) != _canonical(expected)
            or _canonical(memory.get("history")) != _canonical(expected_memory)):
        _refuse()


def _validated_previous(root, raw, tasks, roster, policy_sha, roster_sha, *, reserve=0,
                        policy_bytes=None, roster_bytes=None, history_budget=None, expected_source_mode=None):
    """Validate bounded archived transitions and the current index binding.

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
        schema = value.get("schema")
        if (schema not in (SCHEMA, HISTORY_SCHEMA)
                or set(value) != (fields if schema == SCHEMA else fields | {"delta_projection_sha256"})
                or (schema == HISTORY_SCHEMA and (not isinstance(value["delta_projection_sha256"], str)
                    or HEX.fullmatch(value["delta_projection_sha256"]) is None))
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
    expected_source_mode = (expected_source_mode if expected_source_mode is not None
                            else _requested_source_mode("legacy", None))
    if ({key: actual[key] for key in ARTIFACTS} != old["artifacts"]
            or actual.get("source_mode", _requested_source_mode("legacy", None)) != expected_source_mode
            or actual.get("source_binding") != _source_binding(
                BridgeCursor(**old["cursor_start"]), BridgeCursor(**old["cursor_end"]),
                old["anchor_sha256"], old["artifacts"]["projection_sha256"] if old["schema"] == SCHEMA
                else old["delta_projection_sha256"], policy_sha, roster_sha)):
        _refuse()

    checked = {(old["generation"], _canonical(old["artifacts"]))}
    current = old
    seen = {_sha(raw)}
    history_bytes = [0] if history_budget is None else history_budget
    for depth in range(_MAX_POINTER_HISTORY):
        advance = current["empty_advance"]
        previous_hash = current["previous_pointer_sha256"]
        if advance is None and current["watermark"] != current["cursor_end"]:
            _refuse()
        if previous_hash is None:
            if (current["schema"] != SCHEMA or current["previous"] is not None or advance is not None
                    or depth + 1 + reserve > _MAX_POINTER_HISTORY):
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
        generation_key = (previous["generation"], _canonical(previous["artifacts"]))
        # Historical generation artifacts were never required for old v3
        # legacy chains. Do not change that contract for legacy readers.
        # Public histories instead require each retained generation to carry
        # the same independently pinned public-mode artifact binding.
        if expected_source_mode["status_mode"] == "public-v1" and generation_key not in checked:
            if _archived_generation_mode(root, previous, history_bytes) != expected_source_mode:
                _refuse()
            checked.add(generation_key)
        if current["previous"] != previous["generation"]:
            _refuse()
        if current["schema"] == SCHEMA and previous["schema"] == HISTORY_SCHEMA:
            _refuse()  # A v4 history can never be replaced by legacy delta-only publication.
        if advance is None:
            if expected_source_mode["status_mode"] == "public-v1" and current["schema"] == SCHEMA:
                _refuse()  # A non-genesis public generation must prove its history union.
            if current["generation"] == previous["generation"] or current["cursor_start"] != previous["watermark"]:
                _refuse()
            if current["schema"] == HISTORY_SCHEMA:
                _validate_history_transition(root, current, previous, previous_hash, tasks, roster,
                                             policy_sha, roster_sha, policy_bytes, roster_bytes, history_bytes)
        else:
            advance_fields = {"schema", "anchor_sha256", "projection_sha256", "cursor_start_sha256",
                              "cursor_end_sha256", "policy_sha256", "roster_sha256"}
            if expected_source_mode["status_mode"] == "public-v1":
                advance_fields.add("source_mode")
                advance_schema = "wd.q4-empty-watermark.v2"
            else:
                advance_schema = "wd.q4-empty-watermark.v1"
            if (type(advance) is not dict or set(advance) != advance_fields
                    or advance["schema"] != advance_schema
                    or (advance_schema.endswith(".v2") and advance["source_mode"] != expected_source_mode)
                    or any(not isinstance(advance[k], str) or HEX.fullmatch(advance[k]) is None for k in
                           ("anchor_sha256", "projection_sha256", "cursor_start_sha256", "cursor_end_sha256"))
                    or advance["policy_sha256"] != policy_sha or advance["roster_sha256"] != roster_sha
                    or advance["projection_sha256"] != empty_hash
                    or any(current[k] != previous[k] for k in
                           ("generation", "artifacts", "cursor_start", "cursor_end", "anchor_sha256"))):
                _refuse()
            if (current["schema"] != previous["schema"]
                    or (current["schema"] == HISTORY_SCHEMA
                        and current["delta_projection_sha256"] != previous["delta_projection_sha256"])):
                _refuse()
            start, end = (BridgeCursor(**value) for value in (previous["watermark"], current["watermark"]))
            if (start.file_identity != end.file_identity or start.generation != end.generation
                    or start.offset >= end.offset
                    or advance["cursor_start_sha256"] != _sha(_token(start).encode("utf-8"))
                    or advance["cursor_end_sha256"] != _sha(_token(end).encode("utf-8"))):
                _refuse()
        current = previous
    _refuse()


def read_verified_pointer(root, approved_tasks, *, expected_pointer_sha256,
                          policy_bytes, policy_sha256, roster_bytes, roster_sha256,
                          expected_mode="legacy", expected_public_policy_sha256=None):
    """Return a narrow, hash-pinned view of the current verified checkpoint.

    This function is read-only and does not create a lock. It checks the
    pointer again after verifying its active artifacts and bounded archive
    chain. This detects an ordinary concurrent publication during verification,
    but is not a lease: callers must revalidate before a dependent write.
    Caller pins are integrity assertions, not source or writer authentication.
    """
    try:
        from sandbox.mind.step2.shared_capture_adapter import strict_pins
        from sandbox.mind.step2.shared_bridge_snapshot import _token

        root = _secure(root)
        if (not root.is_dir() or not isinstance(expected_pointer_sha256, str)
                or HEX.fullmatch(expected_pointer_sha256) is None):
            _refuse()
        tasks, roster = strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        if type(approved_tasks) is not frozenset or approved_tasks != tasks:
            _refuse()
        path = root / POINTER
        raw = _read(path, max_bytes=_MAX_POINTER_BYTES)
        if _sha(raw) != expected_pointer_sha256:
            _refuse()
        value = _validated_previous(root, raw, tasks, roster, policy_sha256, roster_sha256,
                                    policy_bytes=policy_bytes, roster_bytes=roster_bytes,
                                    expected_source_mode=_requested_source_mode(expected_mode, expected_public_policy_sha256))
        if _read(path, max_bytes=_MAX_POINTER_BYTES) != raw:
            _refuse()
        return {"pointer_sha256": expected_pointer_sha256, "generation": value["generation"],
                "artifacts": dict(value["artifacts"]), "anchor_sha256": value["anchor_sha256"],
                "cursor_end_sha256": _sha(_token(BridgeCursor(**value["cursor_end"])).encode("utf-8")),
                "watermark_sha256": _sha(_token(BridgeCursor(**value["watermark"])).encode("utf-8"))}
    except Exception:
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
                        expected_anchor_sha256, timeout=0.2, expected_mode="legacy",
                        expected_public_policy_sha256=None, public_policy_bytes=None, expected_anchor_bytes=None):
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
        mode = _requested_source_mode(expected_mode, expected_public_policy_sha256)
        mode_args = _snapshot_mode_args(tasks, mode, public_policy_bytes, expected_anchor_bytes)
        delta = snapshot_result.get("projection")
        if snapshot.verify_anchored_projection(snapshot_result, delta, expected_anchor_sha256=expected_anchor_sha256,
                expected_policy_sha256=expected_policy_sha256, expected_roster_sha256=expected_roster_sha256,
                **mode_args) is not True:
            _refuse()
        projection.validate_projection(delta, approved_tasks=tasks, include_refs=False, roster=roster,
                                       exclude_review_signal=True)
        if delta["accepted"] != 0 or delta["records"] != [] or delta["refused"] != {} or delta["dropped_fields"] != 0:
            _refuse()
        empty_delta_bytes = _canonical(delta)
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
            old = _validated_previous(root, raw, tasks, roster, expected_policy_sha256, expected_roster_sha256,
                                      policy_bytes=policy_bytes, roster_bytes=roster_bytes, expected_source_mode=mode)
            start = BridgeCursor(**old["watermark"])
            begin = _cursor(start)
            if (source.get("cursor_start_sha256") != _sha(snapshot._token(start).encode("utf-8"))
                    or begin["file_identity"] != finish["file_identity"] or begin["generation"] != finish["generation"]
                    or finish["offset"] < begin["offset"]):
                _refuse()
            # Recheck the in-process binding immediately before any write.
            if snapshot.verify_anchored_projection(snapshot_result, delta, expected_anchor_sha256=expected_anchor_sha256,
                    expected_policy_sha256=expected_policy_sha256, expected_roster_sha256=expected_roster_sha256,
                    **mode_args) is not True:
                _refuse()
            if finish == begin:
                return {"status": "no_change", "pointer_sha256": expected_pointer_sha256}
            # Only a real advance needs room for another pointer. Revalidate
            # under the same writer lock before publishing that pointer.
            _validated_previous(root, raw, tasks, roster, expected_policy_sha256, expected_roster_sha256,
                                reserve=1, policy_bytes=policy_bytes, roster_bytes=roster_bytes,
                                expected_source_mode=mode)
            # The caller owns this mutable object; the writer lock does not.
            # Never publish a hash computed from a changed empty projection.
            if (snapshot.verify_anchored_projection(snapshot_result, delta, expected_anchor_sha256=expected_anchor_sha256,
                    expected_policy_sha256=expected_policy_sha256, expected_roster_sha256=expected_roster_sha256,
                    **mode_args) is not True or _canonical(delta) != empty_delta_bytes):
                _refuse()
            value = dict(old)
            value.update(watermark=finish, previous=old["generation"], previous_pointer_sha256=expected_pointer_sha256,
                empty_advance={"schema": "wd.q4-empty-watermark.v1", "anchor_sha256": expected_anchor_sha256,
                    "projection_sha256": _sha(empty_delta_bytes),
                    "cursor_start_sha256": _sha(snapshot._token(start).encode("utf-8")),
                    "cursor_end_sha256": _sha(snapshot._token(end).encode("utf-8")),
                    "policy_sha256": expected_policy_sha256, "roster_sha256": expected_roster_sha256})
            if mode["status_mode"] == "public-v1":
                value["empty_advance"]["schema"] = "wd.q4-empty-watermark.v2"
                value["empty_advance"]["source_mode"] = dict(mode)
            _publish_pointer(root, pointer, raw, expected_pointer_sha256, value)
            return {"status": "advanced", "pointer_sha256": _sha(_canonical(value))}
    except Exception:
        _refuse()


def commit_generation(root, generation, start, end, approved_tasks, *, expected_pointer_sha256,
                      expected_artifacts, initial_cursor=None, timeout=0.2,
                      policy_bytes=None, expected_policy_sha256=None, roster_bytes=None,
                      expected_roster_sha256=None, expected_anchor_sha256=None, expected_mode="legacy",
                      expected_public_policy_sha256=None, public_policy_bytes=None, expected_anchor_bytes=None,
                      snapshot_result=None):
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
        mode = _requested_source_mode(expected_mode, expected_public_policy_sha256)
        mode_args = _snapshot_mode_args(tasks, mode, public_policy_bytes, expected_anchor_bytes)
        if mode["status_mode"] == "public-v1":
            from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
            if type(snapshot_result) is not dict:
                _refuse()
            delta = snapshot_result.get("projection")
            source = snapshot_result.get("source")
            if (snapshot.verify_anchored_projection(snapshot_result, delta, expected_anchor_sha256=expected_anchor_sha256,
                    expected_policy_sha256=expected_policy_sha256, expected_roster_sha256=expected_roster_sha256,
                    **mode_args) is not True
                    or type(source) is not dict
                    or source.get("cursor_start_sha256") != _sha(snapshot._token(start).encode("utf-8"))
                    or source.get("cursor_end_sha256") != _sha(snapshot._token(end).encode("utf-8"))
                    or _cursor(snapshot_result.get("candidate_cursor")) != finish
                    or _sha(json.dumps(delta, sort_keys=True, indent=1).encode("utf-8"))
                       != expected_artifacts["projection_sha256"]):
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
                old = _validated_previous(root, raw, tasks, roster, policy_sha, expected_roster_sha256, reserve=1,
                                          policy_bytes=policy_bytes, roster_bytes=roster_bytes,
                                          expected_source_mode=mode)
                if mode["status_mode"] == "public-v1":
                    _refuse()  # Subsequent public generations must preserve history via the union/CAS route.
                if old["schema"] != SCHEMA or old["watermark"] != begin or old["generation"] == generation:
                    _refuse()
            elif expected_pointer_sha256 is not None or initial_cursor is None or _cursor(initial_cursor) != begin:
                _refuse()
            actual = _verify_generation(root, generation, tasks, roster, policy_sha, expected_roster_sha256)
            if ({key: actual[key] for key in ARTIFACTS} != expected_artifacts
                    or actual.get("source_mode", _requested_source_mode("legacy", None)) != mode
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
            if (mode["status_mode"] == "public-v1" and
                    snapshot.verify_anchored_projection(snapshot_result, delta, expected_anchor_sha256=expected_anchor_sha256,
                        expected_policy_sha256=expected_policy_sha256, expected_roster_sha256=expected_roster_sha256,
                        **mode_args) is not True):
                _refuse()
            _publish_pointer(root, pointer, None if old is None else raw, expected_pointer_sha256, value)
            return value
    except Exception:
        _refuse()


def commit_history_generation(root, generation, start, end, approved_tasks, *, snapshot_result,
                              expected_pointer_sha256, expected_artifacts, timeout=0.2,
                              policy_bytes, expected_policy_sha256, roster_bytes, expected_roster_sha256,
                              expected_anchor_sha256, expected_mode="legacy", expected_public_policy_sha256=None,
                              public_policy_bytes=None, expected_anchor_bytes=None):
    """Publish a union only after verifying the exact bound delta under the writer lock.

    The delta is retained in the private state directory to permit later union
    checks. Publication may leave immutable orphan files after an OS failure;
    no retry is safe until the caller re-reads the pointer. The snapshot is an
    in-process integrity binding, not authenticated source or caller identity.
    This primitive has no scheduler, live wiring, or production authorization.
    """
    try:
        from sandbox.mind.step2 import shared_bridge_snapshot as snapshot, shared_capture_adapter as adapter
        root = _secure(root)
        if (not root.is_dir() or not isinstance(generation, str) or NAME.fullmatch(generation) is None
                or not isinstance(expected_pointer_sha256, str) or HEX.fullmatch(expected_pointer_sha256) is None
                or not isinstance(expected_anchor_sha256, str) or HEX.fullmatch(expected_anchor_sha256) is None
                or type(expected_artifacts) is not dict or set(expected_artifacts) != set(ARTIFACTS)
                or any(not isinstance(h, str) or HEX.fullmatch(h) is None for h in expected_artifacts.values())
                or type(snapshot_result) is not dict):
            _refuse()
        begin, finish = _cursor(start), _cursor(end)
        if (begin["file_identity"] != finish["file_identity"] or begin["generation"] != finish["generation"]
                or finish["offset"] < begin["offset"] or _cursor(snapshot_result.get("candidate_cursor")) != finish):
            _refuse()
        tasks, roster = adapter.strict_pins(policy_bytes, expected_policy_sha256, roster_bytes, expected_roster_sha256)
        if type(approved_tasks) is not frozenset or approved_tasks != tasks:
            _refuse()
        mode = _requested_source_mode(expected_mode, expected_public_policy_sha256)
        mode_args = _snapshot_mode_args(tasks, mode, public_policy_bytes, expected_anchor_bytes)
        delta = snapshot_result.get("projection")
        source = snapshot_result.get("source")
        if (type(source) is not dict or source.get("coverage") != "delta_only_not_complete_history"
                or source.get("cursor_start_sha256") != _sha(snapshot._token(start).encode("utf-8"))
                or source.get("cursor_end_sha256") != _sha(snapshot._token(end).encode("utf-8"))):
            _refuse()
        pointer, lock = root / POINTER, root / LOCK
        _secure(lock, missing_leaf=True)
        with reader_slot(lock, timeout_seconds=timeout):
            _secure(lock)
            raw = _read(pointer, max_bytes=_MAX_POINTER_BYTES)
            if _sha(raw) != expected_pointer_sha256:
                _refuse()
            history_bytes = [0]
            old = _validated_previous(root, raw, tasks, roster, expected_policy_sha256, expected_roster_sha256,
                                      reserve=1, policy_bytes=policy_bytes, roster_bytes=roster_bytes,
                                      history_budget=history_bytes, expected_source_mode=mode)
            # A reader of the new generation also checks the formerly active
            # generation as archived metadata. Reserve those exact bytes before
            # publishing, using the same budget and mode check as that reader.
            if mode["status_mode"] == "public-v1":
                if _archived_generation_mode(root, old, history_bytes) != mode:
                    _refuse()
            if old["watermark"] != begin or old["generation"] == generation:
                _refuse()
            if snapshot.verify_anchored_projection(snapshot_result, delta, expected_anchor_sha256=expected_anchor_sha256,
                    expected_policy_sha256=expected_policy_sha256, expected_roster_sha256=expected_roster_sha256,
                    **mode_args) is not True:
                _refuse()
            delta_bytes = json.dumps(delta, sort_keys=True, indent=1).encode("utf-8")
            if len(delta_bytes) > _MAX:
                _refuse()
            digest = _sha(delta_bytes)
            actual = _verify_generation(root, generation, tasks, roster, expected_policy_sha256, expected_roster_sha256)
            if ({key: actual[key] for key in ARTIFACTS} != expected_artifacts
                    or actual.get("source_mode", _requested_source_mode("legacy", None)) != mode):
                _refuse()
            value = {"schema": HISTORY_SCHEMA, "generation": generation, "artifacts": dict(expected_artifacts),
                     "policy_sha256": expected_policy_sha256, "roster_sha256": expected_roster_sha256,
                     "anchor_sha256": expected_anchor_sha256, "cursor_start": begin, "cursor_end": finish,
                     "watermark": finish, "empty_advance": None, "previous": old["generation"],
                     "previous_pointer_sha256": expected_pointer_sha256, "delta_projection_sha256": digest}
            _validate_history_transition(root, value, old, expected_pointer_sha256, tasks, roster,
                                         expected_policy_sha256, expected_roster_sha256,
                                         policy_bytes, roster_bytes, history_bytes, delta_bytes=delta_bytes)
            if (snapshot.verify_anchored_projection(snapshot_result, delta, expected_anchor_sha256=expected_anchor_sha256,
                    expected_policy_sha256=expected_policy_sha256, expected_roster_sha256=expected_roster_sha256,
                    **mode_args) is not True
                    or json.dumps(delta, sort_keys=True, indent=1).encode("utf-8") != delta_bytes
                    or _read(pointer, max_bytes=_MAX_POINTER_BYTES) != raw):
                _refuse()
            delta_path = root / ("q4-delta-" + digest + ".json")
            if os.path.lexists(delta_path):
                if _read(delta_path) != delta_bytes:
                    _refuse()
            else:
                staging = root / (".q4-delta-" + secrets.token_hex(16) + ".staging")
                _write_new(staging, delta_bytes)
                if _read(staging) != delta_bytes:
                    _refuse()
                _secure(delta_path, missing_leaf=True)
                os.rename(staging, delta_path)
            stored_delta = _read(delta_path)
            if (stored_delta != delta_bytes or _sha(stored_delta) != digest
                    or _read(pointer, max_bytes=_MAX_POINTER_BYTES) != raw
                    or snapshot.verify_anchored_projection(snapshot_result, delta,
                        expected_anchor_sha256=expected_anchor_sha256,
                        expected_policy_sha256=expected_policy_sha256,
                        expected_roster_sha256=expected_roster_sha256, **mode_args) is not True
                    or json.dumps(delta, sort_keys=True, indent=1).encode("utf-8") != delta_bytes):
                _refuse()
            _publish_pointer(root, pointer, raw, expected_pointer_sha256, value)
            return value
    except Exception:
        _refuse()
