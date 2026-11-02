"""Inactive single-writer Q4 publication checkpoint; no scheduler or live wiring.

Only cooperating writers using the same persistent lock are serialized. The
opt-in prefix fence (open_prefix_fence / resolve_prefix_fence, default-off and
not wired to any caller) further restricts those cooperating writers to one
bound prior -> attempt transition; it cannot fence a noncooperating writer. This
does not authenticate the caller, root, policy, generation or operating-system
clock, and cannot defeat hostile same-user replacement or filesystem TOCTOU.
The caller must externally anchor those inputs and the manifest/pointer it uses.
No shadow producer state is read or written by this module.
The source_binding is a caller-supplied build-receipt commitment, not proof
that an independently authenticated source actually produced the delta.
"""
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType
import re
import secrets
import stat
import ctypes

from sandbox.mind.step2.reader_slot import reader_slot
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin

SCHEMA = "wd.q4-shared-checkpoint.v3"
HISTORY_SCHEMA = "wd.q4-shared-checkpoint.v4"
PREFIX_SCHEMA = "wd.q4-shared-prefix-checkpoint.v1"
PREFIX_HISTORY_SCHEMA = "wd.q4-shared-prefix-checkpoint.v2"
HEX = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
IDENTITY = re.compile(r"(?:windows-v1:[0-9a-f]{8}:[0-9a-f]{16}|posix-v1:[0-9a-f]+:[0-9a-f]+)\Z")
GENERATION = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
ARTIFACTS = {"descriptor_sha256": "descriptor.json", "memory_sha256": "memory.json",
             "projection_sha256": "projection.json", "receipt_sha256": "build_receipt.json"}
POINTER = "q4-latest.json"
LOCK = "q4-checkpoint.lock"
FENCE = "q4-prefix-fence.json"
FENCE_SCHEMA = "wd.q4-prefix-fence.v1"
_FENCE_OUTCOMES = ("landed", "not_landed")
_MAX_FENCE_BYTES = 512
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_MAX = 16 * 1024 * 1024
# Fail closed at a bounded history depth. Retention/compaction requires a
# separately designed, externally anchored checkpoint protocol.
_MAX_POINTER_HISTORY = 256
_MAX_POINTER_BYTES = 16384
_MAX_HISTORY_BYTES = 64 * 1024 * 1024
_MAX_ACTIVE_ARTIFACT_BYTES = 16 * 1024 * 1024
_MAX_ACTIVE_SNAPSHOT_BYTES = 64 * 1024 * 1024
_MAX_VERIFIED_SNAPSHOT_BYTES = 642 * 1024 * 1024


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


def _validate_generation_bytes(contents, *, name, tasks, roster, policy_sha256, roster_sha256):
    """Validate active control bytes only; no cells, I/O, or authentication."""
    try:
        return _validate_generation_bytes_inner(
            contents, name=name, tasks=tasks, roster=roster,
            policy_sha256=policy_sha256, roster_sha256=roster_sha256)
    except Exception:
        pass
    del contents, name, tasks, roster, policy_sha256, roster_sha256
    _refuse()


def _validate_generation_bytes_inner(contents, *, name, tasks, roster, policy_sha256, roster_sha256):
    from sandbox.mind.step2 import q4_descriptor as descriptor_parser, shared_projection as sp
    if (not isinstance(name, str) or NAME.fullmatch(name) is None or type(contents) is not dict
            or set(contents) != set(ARTIFACTS)
            or any(type(data) is not bytes or len(data) > _MAX for data in contents.values())):
        _refuse()
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
    descriptor = descriptor_parser.read_descriptor_bytes(contents["descriptor_sha256"],
                                                          hashes["descriptor_sha256"],
                                                          error_type=CheckpointRefused)
    def strict_policy(value):
        return (type(value) is dict
                and set(value) == {"authorship", "review_signal_excluded", "policy_sha256", "roster_sha256"}
                and value["authorship"] == sp.AUTHORSHIP_ROSTER
                and value["review_signal_excluded"] is True
                and value["policy_sha256"] == policy_sha256
                and value["roster_sha256"] == roster_sha256)
    if (descriptor["memory_sha256"] != hashes["memory_sha256"]
            or memory.get("scope") != descriptor_parser.SHARED_SCOPE or memory.get("projection_schema") != sp.SCHEMA
            or not strict_policy(memory.get("policy")) or not strict_policy(receipt.get("policy"))
            or memory.get("projection_sha256") != hashes["projection_sha256"]
            or receipt.get("schema") != "wd.bridge-mind-shared-index-build-receipt.v1"
            or receipt.get("scope") != descriptor_parser.SHARED_SCOPE
            or receipt.get("descriptor_schema") != descriptor_parser.DESCRIPTOR_SCHEMA
            or receipt.get("projection_schema") != sp.SCHEMA or receipt.get("projection_file") != "projection.json"
            or receipt.get("encoder") != descriptor["encoder"] or receipt.get("dim") != descriptor["dim"]
            or receipt.get("memory_sha256") != hashes["memory_sha256"]
            or receipt.get("descriptor_sha256") != hashes["descriptor_sha256"]
            or receipt.get("projection_sha256") != hashes["projection_sha256"]
            or receipt.get("memory_name") != name):
        _refuse()
    return {"hashes": hashes, "projection": projection, "memory": memory, "receipt": receipt,
            "descriptor": descriptor}


def _validate_projection_cell_views(projection, opened):
    """Compare the existing paired cell rows and texts to the verified projection."""
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


def _verify_generation(root, name, tasks, roster, policy_sha256, roster_sha256):
    """Verify the reader-compatible builder artifacts and healthy cell receipts."""
    from sandbox.mind.step2 import wd_q4_private_read as reader
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
    validated = _validate_generation_bytes(contents, name=name, tasks=tasks, roster=roster,
                                           policy_sha256=policy_sha256, roster_sha256=roster_sha256)
    hashes, projection = validated["hashes"], validated["projection"]
    memory, receipt = validated["memory"], validated["receipt"]
    # Preserve the legacy path's separate descriptor reread and hash check.
    descriptor = reader.read_descriptor(directory / "descriptor.json", hashes["descriptor_sha256"])
    if descriptor != validated["descriptor"]:
        _refuse()
    opened = reader.bind_memory(directory, descriptor)
    if (not opened.views or set(opened.views) != set(memory.get("cells", {}))
            or any(not view.healthy for view in opened.views.values())):
        _refuse()
    # Bind both stored views back to the approved projection, not merely its
    # declared digest. No extra or missing record/view may be published.
    _validate_projection_cell_views(projection, opened)
    coverage = receipt.get("source_coverage")
    if coverage is not None:
        from sandbox.mind.step2.shared_bridge_snapshot import _cutoff, _ready_projection
        if (type(coverage) is not dict or set(coverage) != {"kind", "end_reason", "cutoff_utc"}
                or (coverage["kind"], coverage["end_reason"]) not in
                   (("partial_ready_prefix", "deferred"), ("partial_ready_prefix", "max_windows"),
                    ("partial_ready_prefix", "partial_record"), ("ready_caught_up", "snapshot_end"))
                or type(coverage["cutoff_utc"]) is not str
                or _cutoff(datetime.fromisoformat(coverage["cutoff_utc"])) != coverage["cutoff_utc"]):
            _refuse()
        try:
            if not _ready_projection(projection, coverage["cutoff_utc"]):
                _refuse()
        except Exception:  # noqa: BLE001 - invalid or early history must fail closed
            _refuse()
    result = {"generation": name, **hashes, "source_binding": receipt.get("source_binding"),
              "source_mode": _source_mode(memory, receipt), "source_coverage": coverage}
    return result


def _write_new(path, data):
    _secure(path, missing_leaf=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


class _BoundActiveArtifactBytes:
    """Private hash-bound bytes; this is not a verified checkpoint."""

    __slots__ = ("__values",)

    def __init__(self, values):
        object.__setattr__(self, "_BoundActiveArtifactBytes__values", MappingProxyType(values))

    def __setattr__(self, name, value):
        raise AttributeError("bound_active_artifact_bytes_read_only")

    def read(self, name):
        if type(name) is not str or name not in self.__values:
            _refuse()
        return self.__values[name]

    def __repr__(self):
        return "<bound-active-artifact-bytes: private, checkpoint unverified>"


def _bind_active_artifact_bytes(artifact_bytes, *, expected_hashes, enabled=False):
    """Bind four in-memory bytes against independently supplied caller pins.

    The caller must authenticate those pins. This checks only exact names, byte
    budgets and hashes; it does not validate policy, projection, source, cells,
    current pointer/log or history, and is not wired to a production reader.
    """
    captured = pins = None
    try:
        if (enabled is not True or type(artifact_bytes) is not dict or type(expected_hashes) is not dict
                or len(artifact_bytes) != len(ARTIFACTS) or len(expected_hashes) != len(ARTIFACTS)):
            _refuse()
        captured, pins = dict(artifact_bytes), dict(expected_hashes)
        if (set(captured) != set(ARTIFACTS.values()) or set(pins) != set(ARTIFACTS)
                or any(type(key) is not str for key in captured)
                or any(type(key) is not str for key in pins)
                or any(type(value) is not bytes or len(value) > _MAX_ACTIVE_ARTIFACT_BYTES
                       for value in captured.values())
                or sum(map(len, captured.values())) > _MAX_ACTIVE_SNAPSHOT_BYTES
                or any(type(value) is not str or HEX.fullmatch(value) is None for value in pins.values())):
            _refuse()
        for kind, filename in ARTIFACTS.items():
            if _sha(captured[filename]) != pins[kind]:
                _refuse()
        return _BoundActiveArtifactBytes(captured)
    except Exception:
        pass
    del captured, pins, artifact_bytes, expected_hashes
    _refuse()


def _freeze_history_snapshot(snapshot):
    """Copy a bounded logical-name-to-bytes map, without reading the filesystem.

    Inputs and the caller's pins require independent authentication; this is
    only a snapshot of metadata, not a verified active generation or cell.
    """
    if type(snapshot) is not dict or len(snapshot) > 2048:
        _refuse()
    frozen = {}
    total = 0
    for key, value in snapshot.items():
        if type(key) is not str or type(value) is not bytes or len(value) > _MAX:
            _refuse()
        parts = key.split("/")
        artifact = (len(parts) == 2 and NAME.fullmatch(parts[0]) is not None
                    and parts[1] in ARTIFACTS.values())
        archive = (len(parts) == 1 and re.fullmatch(r"q4-(?:pointer|delta)-[0-9a-f]{64}\.json", key))
        if not artifact and not archive:
            _refuse()
        total += len(value)
        if total > _MAX_HISTORY_BYTES:
            _refuse()
        frozen[key] = value
    return frozen


def _history_snapshot_read(snapshot, key):
    try:
        return snapshot[key]
    except (KeyError, TypeError):
        _refuse()


def _archived_generation_mode(root, pointer, budget, *, snapshot_bytes=None):
    """Hash-check only the retained metadata needed for mode continuity.

    Historical FAISS cells are intentionally not opened here; the existing
    active-generation verifier still opens only the active index. The metadata
    bytes counted here share the bounded history-transition budget.
    """
    snapshot = None if snapshot_bytes is None else _freeze_history_snapshot(snapshot_bytes)
    values = {}
    for kind in ("memory_sha256", "receipt_sha256"):
        raw = (_read(root / pointer["generation"] / ARTIFACTS[kind]) if snapshot is None
               else _history_snapshot_read(snapshot, pointer["generation"] + "/" + ARTIFACTS[kind]))
        budget[0] += len(raw)
        if budget[0] > _MAX_HISTORY_BYTES or _sha(raw) != pointer["artifacts"][kind]:
            _refuse()
        values[kind] = _parse(raw)
    return _source_mode(values["memory_sha256"], values["receipt_sha256"])


def _validate_history_transition(root, current, previous, previous_hash, tasks, roster, policy_sha, roster_sha,
                                 policy_bytes, roster_bytes, budget, *, delta_bytes=None, snapshot_bytes=None):
    """Rebuild one union from retained metadata deltas; hashes are not authentication.

    Each v4 transition retains its delta. Historical v3 transitions predate
    this protocol and cannot be reconstructed from their receipts alone.
    The generation directories, delta files and archives must remain available.
    """
    from sandbox.mind.step2 import shared_history, shared_projection as sp
    from sandbox.mind.step2.shared_bridge_snapshot import _token

    if policy_bytes is None or roster_bytes is None or (snapshot_bytes is not None and delta_bytes is not None):
        _refuse()
    snapshot = None if snapshot_bytes is None else _freeze_history_snapshot(snapshot_bytes)

    def bounded(name):
        data = (_read(root / name) if snapshot is None else _history_snapshot_read(snapshot, name))
        budget[0] += len(data)
        if budget[0] > _MAX_HISTORY_BYTES:
            _refuse()
        return data

    def artifact(pointer, kind):
        data = bounded(pointer["generation"] + "/" + ARTIFACTS[kind])
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
    raw_delta = bounded("q4-delta-" + digest + ".json") if delta_bytes is None else delta_bytes
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
    previous_delta = (previous["artifacts"]["projection_sha256"] if previous["schema"] in (SCHEMA, PREFIX_SCHEMA)
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


def _parse_bound_pointer(data, *, policy_sha, roster_sha, prefix_required):
    """Parse pointer bytes; does not verify the generation or its history."""
    fields = {"schema", "generation", "artifacts", "policy_sha256", "roster_sha256", "anchor_sha256",
              "cursor_start", "cursor_end", "watermark", "empty_advance", "previous", "previous_pointer_sha256"}
    if not isinstance(data, bytes) or len(data) > _MAX_POINTER_BYTES:
        _refuse()
    value = _parse(data)
    schema = value.get("schema")
    genesis_schema = PREFIX_SCHEMA if prefix_required else SCHEMA
    history_schema = PREFIX_HISTORY_SCHEMA if prefix_required else HISTORY_SCHEMA
    expected_fields = fields | ({"prefix_start_sha256", "prefix_watermark_sha256"} if prefix_required else set())
    if (schema not in (genesis_schema, history_schema)
            or set(value) != (expected_fields if schema == genesis_schema else expected_fields | {"delta_projection_sha256"})
            or (prefix_required and any(not isinstance(value[k], str) or HEX.fullmatch(value[k]) is None
                                        for k in ("prefix_start_sha256", "prefix_watermark_sha256")))
            or (schema == history_schema and (not isinstance(value["delta_projection_sha256"], str)
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


def _parse_pointer_snapshot(data, *, expected_pointer_sha256, policy_sha, roster_sha, prefix_required):
    """Parse independently pinned bytes, without validating artifacts or history.

    The caller must authenticate the pin independently. This private helper's
    result includes private pointer fields and must never be exposed as output.
    """
    try:
        if (type(data) is not bytes or len(data) > _MAX_POINTER_BYTES
                or not isinstance(expected_pointer_sha256, str)
                or HEX.fullmatch(expected_pointer_sha256) is None
                or _sha(data) != expected_pointer_sha256):
            _refuse()
        return _parse_bound_pointer(data, policy_sha=policy_sha, roster_sha=roster_sha,
                                    prefix_required=prefix_required)
    except Exception:
        pass
    del data
    _refuse()


def _validated_previous(root, raw, tasks, roster, policy_sha, roster_sha, *, reserve=0,
                        policy_bytes=None, roster_bytes=None, history_budget=None, expected_source_mode=None,
                        prefix_required=False):
    """Validate bounded archived transitions and the current index binding.

    The current pointer still needs an independent caller-provided anchor;
    historical index files are not re-opened or claimed to be retrievable.
    """
    old = _parse_bound_pointer(raw, policy_sha=policy_sha, roster_sha=roster_sha,
                               prefix_required=prefix_required)
    actual = _verify_generation(root, old["generation"], tasks, roster, policy_sha, roster_sha)
    expected_source_mode = (expected_source_mode if expected_source_mode is not None
                            else _requested_source_mode("legacy", None))
    _validate_active_pointer_binding(old, actual, policy_sha, roster_sha, expected_source_mode)
    return _validate_pointer_history(root, raw, old, tasks, roster, policy_sha, roster_sha,
                                     reserve=reserve, policy_bytes=policy_bytes, roster_bytes=roster_bytes,
                                     history_budget=history_budget, expected_source_mode=expected_source_mode,
                                     prefix_required=prefix_required)


def _validate_active_pointer_binding(old, actual, policy_sha, roster_sha, expected_source_mode):
    if ({key: actual[key] for key in ARTIFACTS} != old["artifacts"]
            or actual.get("source_mode", _requested_source_mode("legacy", None)) != expected_source_mode
            or actual.get("source_binding") != _source_binding(
                BridgeCursor(**old["cursor_start"]), BridgeCursor(**old["cursor_end"]),
                old["anchor_sha256"], old["artifacts"]["projection_sha256"] if old["schema"] in (SCHEMA, PREFIX_SCHEMA)
                else old["delta_projection_sha256"], policy_sha, roster_sha)):
        _refuse()


def _validate_pointer_history(root, raw, old, tasks, roster, policy_sha, roster_sha, *, reserve=0,
                              policy_bytes=None, roster_bytes=None, history_budget=None,
                              expected_source_mode=None, prefix_required=False, snapshot_bytes=None):
    """Validate only the history of an already-parsed pointer, not active cells."""
    from sandbox.mind.step2 import shared_projection as sp
    from sandbox.mind.step2.shared_bridge_snapshot import _token

    snapshot = None if snapshot_bytes is None else _freeze_history_snapshot(snapshot_bytes)
    expected_source_mode = (expected_source_mode if expected_source_mode is not None
                            else _requested_source_mode("legacy", None))
    empty_hash = _sha(_canonical(sp._projection([], {}, 0, roster, True)))
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
            if (current["schema"] != (PREFIX_SCHEMA if prefix_required else SCHEMA)
                    or current["previous"] is not None or advance is not None
                    or depth + 1 + reserve > _MAX_POINTER_HISTORY):
                _refuse()
            return old
        if (not isinstance(previous_hash, str) or HEX.fullmatch(previous_hash) is None
                or previous_hash in seen):
            _refuse()
        seen.add(previous_hash)
        archive_name = "q4-pointer-" + previous_hash + ".json"
        previous_raw = (_read(root / archive_name, max_bytes=_MAX_POINTER_BYTES) if snapshot is None
                        else _history_snapshot_read(snapshot, archive_name))
        if len(previous_raw) > _MAX_POINTER_BYTES:
            _refuse()
        if _sha(previous_raw) != previous_hash:
            _refuse()
        previous = _parse_bound_pointer(previous_raw, policy_sha=policy_sha, roster_sha=roster_sha,
                                        prefix_required=prefix_required)
        if prefix_required and current["prefix_start_sha256"] != previous["prefix_watermark_sha256"]:
            _refuse()
        generation_key = (previous["generation"], _canonical(previous["artifacts"]))
        # Historical generation artifacts were never required for old v3
        # legacy chains. Do not change that contract for legacy readers.
        # Public histories instead require each retained generation to carry
        # the same independently pinned public-mode artifact binding.
        if expected_source_mode["status_mode"] == "public-v1" and generation_key not in checked:
            archive_mode = (_archived_generation_mode(root, previous, history_bytes) if snapshot is None
                            else _archived_generation_mode(root, previous, history_bytes, snapshot_bytes=snapshot))
            if archive_mode != expected_source_mode:
                _refuse()
            checked.add(generation_key)
        if current["previous"] != previous["generation"]:
            _refuse()
        if current["schema"] in (SCHEMA, PREFIX_SCHEMA) and previous["schema"] in (HISTORY_SCHEMA, PREFIX_HISTORY_SCHEMA):
            _refuse()  # A v4 history can never be replaced by legacy delta-only publication.
        if advance is None:
            if expected_source_mode["status_mode"] == "public-v1" and current["schema"] in (SCHEMA, PREFIX_SCHEMA):
                _refuse()  # A non-genesis public generation must prove its history union.
            if current["generation"] == previous["generation"] or current["cursor_start"] != previous["watermark"]:
                _refuse()
            if current["schema"] in (HISTORY_SCHEMA, PREFIX_HISTORY_SCHEMA):
                args = (root, current, previous, previous_hash, tasks, roster,
                        policy_sha, roster_sha, policy_bytes, roster_bytes, history_bytes)
                if snapshot is None:
                    _validate_history_transition(*args)
                else:
                    _validate_history_transition(*args, snapshot_bytes=snapshot)
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
                    or (current["schema"] in (HISTORY_SCHEMA, PREFIX_HISTORY_SCHEMA)
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


def _read_snapshot_checkpoint(raw, *, expected_pointer_sha256, tasks, roster, policy_sha, roster_sha,
                              snapshot_bytes, prefix_required=False, expected_source_mode=None,
                              policy_bytes=None, roster_bytes=None):
    """Inspect pinned snapshot history but refuse: active cell bytes are not verified.

    This private experimental entry never returns a verified checkpoint. It
    does not authenticate caller pins or connect to the production read path.
    """
    pointer = None
    try:
        if type(snapshot_bytes) is not dict:
            _refuse()
        pointer = _parse_pointer_snapshot(raw, expected_pointer_sha256=expected_pointer_sha256,
                                          policy_sha=policy_sha, roster_sha=roster_sha,
                                          prefix_required=prefix_required)
        _validate_pointer_history(None, raw, pointer, tasks, roster, policy_sha, roster_sha,
                                  policy_bytes=policy_bytes, roster_bytes=roster_bytes,
                                  expected_source_mode=expected_source_mode,
                                  prefix_required=prefix_required, snapshot_bytes=snapshot_bytes)
    except Exception:
        pass
    del raw, pointer, snapshot_bytes, policy_bytes, roster_bytes
    _refuse()


def _copy_snapshot_cells(cell_snapshots):
    """Freeze bounded cell inputs; this is not validation of their contents."""
    from sandbox.mind.step2 import cell, memory
    from waggledance.core.hex_cell_topology import ALL_CELLS
    if type(cell_snapshots) is not dict or len(cell_snapshots) > len(ALL_CELLS):
        _refuse()
    outer = dict(cell_snapshots)
    copied, total = {}, 0
    for cell_id, original in outer.items():
        if type(cell_id) is not str or cell_id not in ALL_CELLS or type(original) is not dict or len(original) != 3:
            _refuse()
        item = dict(original)
        if (set(item) != {"pointer", "log", "artifacts"} or type(item["pointer"]) is not bytes
                or len(item["pointer"]) > cell.MAX_POINTER_BYTES or type(item["log"]) is not bytes
                or len(item["log"]) > cell.MAX_LOG_BYTES or type(item["artifacts"]) is not dict
                or len(item["artifacts"]) not in (4, 5)):
            _refuse()
        artifacts = dict(item["artifacts"])
        if (not {"receipt", "index", "meta", "provenance"} <= artifacts.keys()
                or not artifacts.keys() <= {"receipt", "index", "meta", "provenance", "vectors"}
                or any(type(key) is not str for key in artifacts)
                or any(type(value) is not bytes for value in artifacts.values())):
            _refuse()
        total += len(item["pointer"]) + len(item["log"]) + sum(map(len, artifacts.values()))
        if total > memory.MAX_MEMORY_SNAPSHOT_BYTES:
            _refuse()
        copied[cell_id] = {"pointer": item["pointer"], "log": item["log"], "artifacts": artifacts}
    return copied, total


def _preflight_public_snapshot(pointer, active, tasks, roster, *, public_policy_bytes,
                               public_policy_sha256, expected_source_sha256, expected_encoder_sha256):
    """Check caller-pinned public controls only; this does not approve disclosure."""
    descriptor = projected = public_tasks = public_types = record = meta = validated = contents = mode = None
    try:
        from sandbox.mind.step2 import shared_capture_adapter as adapter
        from tools import wd_q4_public_pointer_resolver as resolver
        from tools.wd_q4_public_query import _encoder_digest
        if (type(expected_source_sha256) is not str or not HEX.fullmatch(expected_source_sha256)
                or type(expected_encoder_sha256) is not str or not HEX.fullmatch(expected_encoder_sha256)
                or pointer["anchor_sha256"] != expected_source_sha256):
            _refuse()
        public_tasks, public_types = adapter.public_status_policy(public_policy_bytes, public_policy_sha256, tasks)
        contents = {key: active.read(path) for key, path in ARTIFACTS.items()}
        validated = _validate_generation_bytes(contents, name=pointer["generation"], tasks=tasks, roster=roster,
                                               policy_sha256=pointer["policy_sha256"],
                                               roster_sha256=pointer["roster_sha256"])
        mode = _requested_source_mode("public-v1", public_policy_sha256)
        _validate_active_pointer_binding(pointer, {**validated["hashes"],
            "source_binding": validated["receipt"].get("source_binding"),
            "source_mode": _source_mode(validated["memory"], validated["receipt"])},
            pointer["policy_sha256"], pointer["roster_sha256"], mode)
        descriptor = validated["descriptor"]
        if _encoder_digest(descriptor["encoder"]) != expected_encoder_sha256:
            _refuse()
        projected = validated["projection"]
        for record in projected["records"]:
            meta = record["meta"]
            if (meta.get("task_id") not in public_tasks or meta.get("type") not in public_types
                    or type(meta.get("agent")) is not str or meta["agent"] not in resolver._PUBLIC_AGENTS):
                _refuse()
        return None
    except Exception:
        pass
    del pointer, active, tasks, roster, public_policy_bytes, descriptor, projected, public_tasks, public_types
    del record, meta, public_policy_sha256, expected_source_sha256, expected_encoder_sha256, validated, contents, mode
    _refuse()


def read_verified_snapshot_checkpoint(*, pointer_bytes, expected_pointer_sha256, approved_tasks,
                                      policy_bytes, policy_sha256, roster_bytes, roster_sha256,
                                      expected_public_policy_sha256, active_artifact_bytes,
                                      cell_snapshots, history_bytes, public_policy_bytes=None,
                                      expected_source_sha256=None, expected_encoder_sha256=None, enabled=False):
    """Experimental preflight only: always refuses until safe result projection exists.

    The caller authenticates all pins and obtains a coherent snapshot. This is
    not publisher authentication, freshness, process isolation, a native memory
    bound, or a production route. Raw memory is never returned.
    """
    pointer = active = frozen_history = frozen_cells = None
    preflight_memory = preflight_receipt = tasks = roster = mode = None
    try:
        if enabled is not True:
            _refuse()
        from sandbox.mind.step2.shared_capture_adapter import strict_pins
        tasks, roster = strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        if type(approved_tasks) is not frozenset or approved_tasks != tasks:
            _refuse()
        mode = _requested_source_mode("public-v1", expected_public_policy_sha256)
        pointer = _parse_pointer_snapshot(pointer_bytes, expected_pointer_sha256=expected_pointer_sha256,
                                          policy_sha=policy_sha256, roster_sha=roster_sha256,
                                          prefix_required=True)
        active = _bind_active_artifact_bytes(active_artifact_bytes, expected_hashes=pointer["artifacts"], enabled=True)
        frozen_history = _freeze_history_snapshot(history_bytes)
        frozen_cells, cells_size = _copy_snapshot_cells(cell_snapshots)
        total = (len(pointer_bytes) + len(policy_bytes) + len(roster_bytes) + cells_size
                 + sum(len(active.read(name)) for name in ARTIFACTS.values())
                 + sum(map(len, frozen_history.values())))
        if total > _MAX_VERIFIED_SNAPSHOT_BYTES:
            _refuse()
        _validate_pointer_history(None, pointer_bytes, pointer, tasks, roster, policy_sha256, roster_sha256,
                                  policy_bytes=policy_bytes, roster_bytes=roster_bytes, expected_source_mode=mode,
                                  prefix_required=True, snapshot_bytes=frozen_history)
        _preflight_public_snapshot(pointer, active, tasks, roster, public_policy_bytes=public_policy_bytes,
                                   public_policy_sha256=expected_public_policy_sha256,
                                   expected_source_sha256=expected_source_sha256,
                                   expected_encoder_sha256=expected_encoder_sha256)
        # Source mode and the pointer's build-receipt commitment are cheap to
        # reject before any cell index reaches the native deserializer.
        preflight_memory = _parse(active.read(ARTIFACTS["memory_sha256"]))
        preflight_receipt = _parse(active.read(ARTIFACTS["receipt_sha256"]))
        _validate_active_pointer_binding(pointer, {**pointer["artifacts"],
            "source_binding": preflight_receipt.get("source_binding"),
            "source_mode": _source_mode(preflight_memory, preflight_receipt)}, policy_sha256, roster_sha256, mode)
        # Disclosure requires a separately reviewed narrow result projection.
        # Never hand a raw Memory (which exposes extra fields) to a caller.
        _refuse()
    except Exception:
        pass
    del pointer_bytes, policy_bytes, roster_bytes, active_artifact_bytes, cell_snapshots, history_bytes
    del pointer, active, frozen_history, frozen_cells, preflight_memory, preflight_receipt, public_policy_bytes
    del tasks, roster, mode, approved_tasks, expected_pointer_sha256, policy_sha256, roster_sha256
    del expected_public_policy_sha256, expected_source_sha256, expected_encoder_sha256, enabled
    _refuse()


def query_public_snapshot(*, pointer_bytes=None, expected_pointer_sha256=None, approved_tasks=None,
                          policy_bytes=None, policy_sha256=None, roster_bytes=None, roster_sha256=None,
                          expected_public_policy_sha256=None, active_artifact_bytes=None,
                          cell_snapshots=None, history_bytes=None, public_policy_bytes=None,
                          expected_source_sha256=None, expected_encoder_sha256=None,
                          query_bytes=None, model_dir=None, expected_runtime_revision=None, enabled=False):
    """Query an opt-in pinned byte snapshot and return only approved projected metadata.

    Experimental in-process primitive, not a production or transport boundary.
    No filesystem checkpoint fallback, lane authentication, source or publisher
    authentication, freshness, native-allocation bound, model-asset pinning,
    backend stdout/stderr containment or same-user isolation is provided. The
    caller independently authenticates the tuple and model, obtains a coherent
    snapshot and holds any required lock. Cell/projection equality does not
    establish that vectors were computed from those texts by the declared
    encoder, nor establish recall or semantic relevance. The byte snapshots
    are deserialized twice: once to validate the cells against the public
    projection, once inside the existing Q4 reader. BaseException and process
    isolation remain the responsibility of the containing runtime. The local
    wall-clock future-time check is not an authenticated clock or a freshness
    guarantee.
    Projected references are noncanonical; they are not raw bridge event IDs.
    """
    if enabled is False:
        return {"status": "OFF"}
    pointer = active = frozen_history = frozen_cells = memory_doc = opened = descriptor = None
    tasks = roster = mode = projection = private = clean = query_time = result = binding = query_instant = None
    try:
        import datetime as dt
        import io
        from sandbox.mind.step2 import memory, shared_capture_adapter as adapter
        from sandbox.mind.step2 import shared_result_resolver as resolver, wd_q4_private_read as reader
        if (enabled is not True or type(query_bytes) is not bytes or not query_bytes
                or len(query_bytes) > reader.STDIN_BUDGET or type(model_dir) is not str or not model_dir
                or type(expected_runtime_revision) is not str or not expected_runtime_revision):
            _refuse()
        tasks, roster = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        if type(approved_tasks) is not frozenset or approved_tasks != tasks:
            _refuse()
        mode = _requested_source_mode("public-v1", expected_public_policy_sha256)
        pointer = _parse_pointer_snapshot(pointer_bytes, expected_pointer_sha256=expected_pointer_sha256,
                                          policy_sha=policy_sha256, roster_sha=roster_sha256,
                                          prefix_required=True)
        active = _bind_active_artifact_bytes(active_artifact_bytes, expected_hashes=pointer["artifacts"], enabled=True)
        frozen_history = _freeze_history_snapshot(history_bytes)
        frozen_cells, cell_size = _copy_snapshot_cells(cell_snapshots)
        total = (len(pointer_bytes) + len(policy_bytes) + len(roster_bytes) + cell_size
                 + sum(len(active.read(name)) for name in ARTIFACTS.values())
                 + sum(map(len, frozen_history.values())))
        if total > _MAX_VERIFIED_SNAPSHOT_BYTES:
            _refuse()
        _validate_pointer_history(None, pointer_bytes, pointer, tasks, roster, policy_sha256, roster_sha256,
                                  policy_bytes=policy_bytes, roster_bytes=roster_bytes, expected_source_mode=mode,
                                  prefix_required=True, snapshot_bytes=frozen_history)
        _preflight_public_snapshot(pointer, active, tasks, roster, public_policy_bytes=public_policy_bytes,
                                   public_policy_sha256=expected_public_policy_sha256,
                                   expected_source_sha256=expected_source_sha256,
                                   expected_encoder_sha256=expected_encoder_sha256)
        descriptor = reader.read_descriptor_bytes(active.read(ARTIFACTS["descriptor_sha256"]),
                                                   pointer["artifacts"]["descriptor_sha256"])
        if descriptor["encoder"]["revision"] != expected_runtime_revision:
            _refuse()
        query_time = reader.read_query(io.BytesIO(query_bytes))[1]
        if re.search(r"[.,]\d{7,}", query_time):
            _refuse()
        query_instant = reader.corpus_mod.parse_utc(query_time)
        if query_instant is None or query_instant > dt.datetime.now(dt.timezone.utc):
            _refuse()
        # Reject a mismatched runtime encoder before any native cell index is
        # loaded. The reader repeats this check closer to actual model use;
        # neither check alone prevents replacement between check and use.
        reader.bind_runtime_encoder(descriptor, model_dir)
        memory_doc = active.read(ARTIFACTS["memory_sha256"])
        projection = _parse(active.read(ARTIFACTS["projection_sha256"]))
        # Deserialize only after controls/public policy are bound, then compare
        # every cell row and text to the exact approved projection. The reader
        # subsequently receives the same copied bytes, never a fresh path read.
        opened = memory.Memory.from_snapshots(manifest_bytes=memory_doc,
            expected_sha256=pointer["artifacts"]["memory_sha256"], cell_snapshots=frozen_cells, enabled=True)
        reader._validate_bound_memory(opened, descriptor)
        if not opened.views or set(opened.views) != set(opened.memory.get("cells", {})):
            _refuse()
        if any(not view.healthy for view in opened.views.values()):
            _refuse()
        _validate_projection_cell_views(projection, opened)
        private = reader.private_read_snapshot(descriptor_bytes=active.read(ARTIFACTS["descriptor_sha256"]),
            descriptor_sha256=pointer["artifacts"]["descriptor_sha256"], manifest_bytes=memory_doc,
            expected_memory_sha256=pointer["artifacts"]["memory_sha256"], cell_snapshots=frozen_cells,
            stream=io.BytesIO(query_bytes), model_dir=model_dir,
            expected_runtime_revision=expected_runtime_revision, enabled=True)
        binding = private.get("binding")
        if (private.get("status") not in ("ANSWER", "ABSTAIN", "DEGRADED") or type(binding) is not dict
                or binding.get("memory_sha256") != pointer["artifacts"]["memory_sha256"]
                or binding.get("descriptor_sha256") != pointer["artifacts"]["descriptor_sha256"]
                or binding.get("encoder") != descriptor["encoder"] or type(private.get("results")) is not list):
            _refuse()
        keys = ("ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event")
        clean = [{key: item[key] for key in keys} for item in private["results"]]
        if (private["status"] == "ANSWER" and not clean) or (private["status"] == "ABSTAIN" and clean):
            _refuse()
        result = resolver.resolve_results(memory_manifest=memory_doc,
            expected_memory_sha256=pointer["artifacts"]["memory_sha256"],
            projection_bytes=active.read(ARTIFACTS["projection_sha256"]), results=clean, query_utc=query_time,
            approved_tasks=tasks, policy_bytes=policy_bytes, policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256)
        return {"status": private["status"], "results": result, "source_authentication": "UNKNOWN",
                "freshness": "UNKNOWN"}
    except Exception:
        pass
    finally:
        # Clear this frame even on KeyboardInterrupt/SystemExit; a backend's
        # own frames, OS logs and native stdout/stderr remain outside scope.
        del pointer_bytes, policy_bytes, roster_bytes, active_artifact_bytes, cell_snapshots, history_bytes, query_bytes
        del pointer, active, frozen_history, frozen_cells, memory_doc, opened, descriptor, tasks, roster, mode
        del projection, private, clean, query_time, result, binding, query_instant, public_policy_bytes, model_dir
        del approved_tasks, expected_pointer_sha256, policy_sha256, roster_sha256, expected_public_policy_sha256
        del expected_source_sha256, expected_encoder_sha256, expected_runtime_revision, enabled
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


def _publish_pointer(root, pointer, raw, old_hash, value, fenced=False):
    _fence_gate(root, None if raw is None else _sha(raw), _sha(_canonical(value)), fenced)
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


def read_verified_prefix_pointer(root, approved_tasks, *, expected_pointer_sha256,
                                 policy_bytes, policy_sha256, roster_bytes, roster_sha256,
                                 expected_public_policy_sha256):
    """Trusted-caller view plus protected pin; never publish the returned pin.

    Caller anchors the pointer digest and protects the source, writer and state
    directory. This accessor verifies integrity, not identity or freshness.
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
            policy_bytes=policy_bytes, roster_bytes=roster_bytes, prefix_required=True,
            expected_source_mode=_requested_source_mode("public-v1", expected_public_policy_sha256))
        if _read(path, max_bytes=_MAX_POINTER_BYTES) != raw:
            _refuse()
        mark = BridgeCursor(**value["watermark"])
        view = {"pointer_sha256": expected_pointer_sha256, "generation": value["generation"],
                "artifacts": dict(value["artifacts"]), "anchor_sha256": value["anchor_sha256"],
                "cursor_end_sha256": _sha(_token(BridgeCursor(**value["cursor_end"])).encode("utf-8")),
                "watermark_sha256": _sha(_token(mark).encode("utf-8"))}
        return view, BridgePrefixPin(mark, value["prefix_watermark_sha256"])
    except Exception:
        _refuse()


def _fence_bytes(prior, attempt):
    """The only accepted fence encoding: canonical, binding the exact prior and attempt pointer digests."""
    if ((prior is not None and (type(prior) is not str or HEX.fullmatch(prior) is None))
            or type(attempt) is not str or HEX.fullmatch(attempt) is None or prior == attempt):
        _refuse()
    return _canonical({"schema": FENCE_SCHEMA, "prior_pointer_sha256": prior, "attempt_sha256": attempt})


def _fence_record(root, attempt, outcome):
    return _secure(root / ("q4-prefix-fence-" + attempt + "." + outcome + ".json"), missing_leaf=True)


def _current_pointer(root):
    """Digest of the pointer bytes (None when absent); the caller holds the writer lock."""
    pointer = _secure(root / POINTER, missing_leaf=True)
    if not os.path.lexists(pointer):
        return None
    return _sha(_read(pointer, max_bytes=_MAX_POINTER_BYTES))


def _active_fence(root):
    """The open fence as (prior, attempt, bytes), or None; any other content refuses every writer."""
    path = _secure(root / FENCE, missing_leaf=True)
    if not os.path.lexists(path):
        return None
    raw = _read(path, max_bytes=_MAX_FENCE_BYTES)
    value = _parse(raw)
    if set(value) != {"schema", "prior_pointer_sha256", "attempt_sha256"} or value["schema"] != FENCE_SCHEMA:
        _refuse()
    if _fence_bytes(value["prior_pointer_sha256"], value["attempt_sha256"]) != raw:
        _refuse()
    return value["prior_pointer_sha256"], value["attempt_sha256"], raw


def _fence_gate(root, prior, attempt, fenced):
    """Every pointer replacement passes here, under the cooperating writer lock.

    No fence and no not-landed record: legacy behaviour, unchanged. An open
    fence admits only a fenced publication of exactly its prior -> attempt
    transition; a resolved not-landed attempt never lands afterwards.
    """
    if os.path.lexists(_fence_record(root, attempt, "not_landed")):
        _refuse()
    active = _active_fence(root)
    if active is None:
        if fenced:
            _refuse()
        return
    if fenced is not True or active[:2] != (prior, attempt):
        _refuse()


def _fence_lock(root, timeout):
    root = _secure(root)
    if not root.is_dir():
        _refuse()
    lock = root / LOCK
    _secure(lock, missing_leaf=True)
    return root, reader_slot(lock, timeout_seconds=timeout)


def open_prefix_fence(root, *, expected_prior_pointer_sha256, expected_attempt_sha256, enabled=False, timeout=0.2):
    """Opt-in: fence the pointer for exactly one prior -> attempt transition.

    Under the writer lock the pointer must still be the prior (None = no
    pointer yet). While the fence is open every cooperating writer of this
    module refuses unless it publishes exactly that attempt with fenced=True.
    Created by an exclusive link from an fsynced stage: never overwrites. Waits
    are bounded by the lock timeout. Synthetic/inactive: no caller is wired.
    """
    if enabled is not True:
        _refuse()
    try:
        raw = _fence_bytes(expected_prior_pointer_sha256, expected_attempt_sha256)
        root, slot = _fence_lock(root, timeout)
        with slot:
            _secure(root / LOCK)
            if (_active_fence(root) is not None
                    or any(os.path.lexists(_fence_record(root, expected_attempt_sha256, outcome))
                           for outcome in _FENCE_OUTCOMES)
                    or _current_pointer(root) != expected_prior_pointer_sha256):
                _refuse()
            target = _secure(root / FENCE, missing_leaf=True)
            stage = root / (".q4-prefix-fence-" + secrets.token_hex(16) + ".staging")
            _write_new(stage, raw)
            try:
                os.link(stage, target)
            finally:
                os.unlink(stage)
            if _read(target, max_bytes=_MAX_FENCE_BYTES) != raw:
                _refuse()
            return {"status": "fenced"}
    except Exception:
        _refuse()


def resolve_prefix_fence(root, *, expected_prior_pointer_sha256, expected_attempt_sha256, enabled=False,
                         timeout=0.2):
    """Opt-in: decide the fenced transition for cooperating current writers.

    Under the writer lock: pointer == attempt is landed, pointer == prior is
    not_landed (the fence still excludes every cooperating writer at that
    moment, and the not-landed record refuses that attempt while retained). Any other
    pointer is a non-cooperating write: refused, fence kept. The outcome is a
    record (a hard link of the fence bytes) made before the fence is removed.
    Repeated calls and process interruptions are covered only while the relevant
    filesystem entries and the same lock persist. Power-loss metadata durability,
    older already-running writers and non-cooperating writers are not established.
    """
    if enabled is not True:
        _refuse()
    try:
        prior, attempt = expected_prior_pointer_sha256, expected_attempt_sha256
        raw = _fence_bytes(prior, attempt)
        root, slot = _fence_lock(root, timeout)
        with slot:
            _secure(root / LOCK)
            records = {outcome: _fence_record(root, attempt, outcome) for outcome in _FENCE_OUTCOMES}
            present = [outcome for outcome in _FENCE_OUTCOMES if os.path.lexists(records[outcome])]
            if len(present) > 1 or any(_read(records[outcome], max_bytes=_MAX_FENCE_BYTES) != raw
                                       for outcome in present):
                _refuse()
            active = _active_fence(root)
            if active is None:
                if not present:
                    _refuse()
                return {"status": present[0]}
            if active[2] != raw:
                _refuse()
            current = _current_pointer(root)
            if present:
                outcome = present[0]  # recorded before an interruption; the fence kept everyone out since
            elif current == attempt:
                outcome = "landed"
            elif current == prior:
                outcome = "not_landed"
            else:
                _refuse()
            if current != (attempt if outcome == "landed" else prior):
                _refuse()
            fence = _secure(root / FENCE)
            if not present:
                os.link(fence, records[outcome])
            if _read(records[outcome], max_bytes=_MAX_FENCE_BYTES) != raw:
                _refuse()
            os.unlink(fence)
            return {"status": outcome}
    except Exception:
        _refuse()


def _attempt(value, expected):
    """Bind an opt-in intent to the exact bytes subsequently published."""
    digest = _sha(_canonical(value))
    if expected is not None and (type(expected) is not str or HEX.fullmatch(expected) is None or expected != digest):
        _refuse()
    return digest


def commit_prefix_genesis(*args, expected_start_prefix, expected_attempt_sha256=None, fenced=False, **kwargs):
    """Explicit new series only; never migrate an existing pointer."""
    value = commit_generation(*args, _prefix_required=True, _expected_start_prefix=expected_start_prefix,
                              _expected_attempt_sha256=expected_attempt_sha256, _fenced=fenced, **kwargs)
    return {"status": "committed", "pointer_sha256": _sha(_canonical(value))}


def advance_prefix_empty(*args, expected_start_prefix, expected_attempt_sha256=None, fenced=False,
                         allow_partial_window=False, **kwargs):
    return advance_empty_delta(*args, _prefix_required=True, _expected_start_prefix=expected_start_prefix,
                               _expected_attempt_sha256=expected_attempt_sha256, _fenced=fenced,
                               _allow_partial_window=allow_partial_window, **kwargs)


def commit_prefix_history(*args, expected_start_prefix, expected_attempt_sha256=None, fenced=False, **kwargs):
    value = commit_history_generation(*args, _prefix_required=True, _expected_start_prefix=expected_start_prefix,
                                      _expected_attempt_sha256=expected_attempt_sha256, _fenced=fenced, **kwargs)
    return {"status": "committed", "pointer_sha256": _sha(_canonical(value))}


def prepare_prefix_genesis(*args, expected_start_prefix, enabled=False, **kwargs):
    """Compute a candidate without publishing; retain the CAS inputs privately.

    Preparation can create the persistent slot file, but no pointer or history.
    The digest is not a reservation: commit rechecks the predecessor under the
    cooperating-writer lock. The caller must persist its own intent and resolve
    ambiguous OS failures; this function implements neither crash recovery nor
    authentication against a noncooperating writer.
    """
    if enabled is not True:
        _refuse()
    value = commit_generation(*args, _prefix_required=True, _expected_start_prefix=expected_start_prefix,
                              _prepare_attempt=True, **kwargs)
    return {"status": "prepared", "attempt_sha256": _attempt(value, None)}


def prepare_prefix_history(*args, expected_start_prefix, enabled=False, **kwargs):
    """Prepare without publishing or reserving; may create the persistent slot file."""
    if enabled is not True:
        _refuse()
    value = commit_history_generation(*args, _prefix_required=True, _expected_start_prefix=expected_start_prefix,
                                      _prepare_attempt=True, **kwargs)
    return {"status": "prepared", "attempt_sha256": _attempt(value, None)}


def prepare_prefix_empty(*args, expected_start_prefix, enabled=False, allow_partial_window=False, **kwargs):
    """Prepare without publishing; may create the persistent slot file.

    For no_change, attempt_sha256 identifies the existing pointer, not a new one.
    """
    if enabled is not True:
        _refuse()
    return advance_empty_delta(*args, _prefix_required=True, _expected_start_prefix=expected_start_prefix,
                               _prepare_attempt=True, _allow_partial_window=allow_partial_window, **kwargs)


def advance_empty_delta(root, snapshot_result, approved_tasks, *, expected_pointer_sha256,
                        policy_bytes, expected_policy_sha256, roster_bytes, expected_roster_sha256,
                        expected_anchor_sha256, timeout=0.2, expected_mode="legacy",
                        expected_public_policy_sha256=None, public_policy_bytes=None, expected_anchor_bytes=None,
                        _prefix_required=False, _expected_start_prefix=None,
                        _prepare_attempt=False, _expected_attempt_sha256=None, _fenced=False,
                        _allow_partial_window=False):
    """Advance only a verified strict empty delta, without altering the indexed interval.

    Inactive primitive: the caller must protect source identity, cursor, policy,
    roster, pointer and code from forgery. The snapshot binding is in-process,
    not source authentication. Pointer files contain source offsets and belong
    in a private state directory, not a public event stream. An ambiguous write
    requires re-reading the current pointer, never a blind retry.
    """
    try:
        if type(_prepare_attempt) is not bool or ((_prepare_attempt or _expected_attempt_sha256 is not None)
                and not _prefix_required):
            _refuse()
        # A fenced publication is opt-in, prefix-only and bound to a prepared attempt digest.
        if type(_fenced) is not bool or (_fenced and (_prepare_attempt or _expected_attempt_sha256 is None)):
            _refuse()
        if (type(_allow_partial_window) is not bool or (_allow_partial_window and
                (not _prefix_required or not (_prepare_attempt or _fenced)))):
            _refuse()
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
        if _prefix_required:
            if mode["status_mode"] != "public-v1" or type(_expected_start_prefix) is not BridgePrefixPin:
                _refuse()
            mode_args["expected_start_prefix"] = _expected_start_prefix
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
                or (source.get("coverage") != "delta_only_not_complete_history" and
                    not (_allow_partial_window and source.get("coverage") == "partial_window_not_complete"))):
            _refuse()
        pointer, lock = root / POINTER, root / LOCK
        _secure(lock, missing_leaf=True)
        with reader_slot(lock, timeout_seconds=timeout):
            _secure(lock)
            raw = _read(pointer, max_bytes=_MAX_POINTER_BYTES)
            if _sha(raw) != expected_pointer_sha256:
                _refuse()
            old = _validated_previous(root, raw, tasks, roster, expected_policy_sha256, expected_roster_sha256,
                                      policy_bytes=policy_bytes, roster_bytes=roster_bytes, expected_source_mode=mode,
                                      prefix_required=_prefix_required)
            start = BridgeCursor(**old["watermark"])
            begin = _cursor(start)
            if _prefix_required and (_expected_start_prefix.cursor != start or
                    _expected_start_prefix.sha256 != old["prefix_watermark_sha256"]):
                _refuse()
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
                if _fenced or (_expected_attempt_sha256 is not None and _expected_attempt_sha256 != expected_pointer_sha256):
                    _refuse()
                if _prepare_attempt:
                    return {"status": "no_change", "attempt_sha256": expected_pointer_sha256}
                return {"status": "no_change", "pointer_sha256": expected_pointer_sha256}
            # Only a real advance needs room for another pointer. Revalidate
            # under the same writer lock before publishing that pointer.
            _validated_previous(root, raw, tasks, roster, expected_policy_sha256, expected_roster_sha256,
                                reserve=1, policy_bytes=policy_bytes, roster_bytes=roster_bytes,
                                expected_source_mode=mode, prefix_required=_prefix_required)
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
            if _prefix_required:
                candidate_prefix = snapshot_result.get("candidate_prefix")
                if type(candidate_prefix) is not BridgePrefixPin or candidate_prefix.cursor != end:
                    _refuse()
                value["prefix_start_sha256"] = _expected_start_prefix.sha256
                value["prefix_watermark_sha256"] = candidate_prefix.sha256
            attempt = _attempt(value, _expected_attempt_sha256)
            if _prepare_attempt:
                return {"status": "prepared", "attempt_sha256": attempt}
            _publish_pointer(root, pointer, raw, expected_pointer_sha256, value, fenced=_fenced)
            return {"status": "advanced", "pointer_sha256": _sha(_canonical(value))}
    except Exception:
        _refuse()


def _catchup_coverage(source):
    if source is None or source.get("mode") not in ("public-catchup-partial-v1", "public-catchup-ready-v1"):
        return None
    from sandbox.mind.step2.shared_bridge_snapshot import _cutoff
    partial = source["mode"] == "public-catchup-partial-v1"
    reason = source["catchup_result"]["end_reason"] if partial else "snapshot_end"
    if partial and reason not in ("deferred", "max_windows", "partial_record"):
        _refuse()
    return {"kind": "partial_ready_prefix" if partial else "ready_caught_up",
            "end_reason": reason,
            "cutoff_utc": _cutoff(source["expected_cutoff_utc"])}


def _checked_catchup_delta(source, snapshot_result, start, end, *, policy_bytes, expected_policy_sha256,
                           roster_bytes, expected_roster_sha256, expected_anchor_sha256,
                           expected_public_policy_sha256, public_policy_bytes, expected_anchor_bytes,
                           expected_start_prefix):
    """Reverify one complete chain and return its bounded delta, never inventing an anchor."""
    from sandbox.mind.step2 import shared_index_builder as builder
    if (type(source) is not dict or source.get("catchup_result") != snapshot_result
            or source.get("expected_anchor_sha256") != expected_anchor_sha256
            or source.get("public_policy_sha256") != expected_public_policy_sha256
            or source.get("public_policy_bytes") != public_policy_bytes
            or source.get("anchor_bytes") != expected_anchor_bytes
            or source.get("expected_start_prefix") != expected_start_prefix
            or type(expected_start_prefix) is not BridgePrefixPin or expected_start_prefix.cursor != start):
        _refuse()
    if source.get("mode") == "public-catchup-partial-v1":
        delta, frozen = builder.verified_partial_catchup_projection(source, enabled=True, policy_bytes=policy_bytes,
            policy_sha256=expected_policy_sha256, roster_bytes=roster_bytes, roster_sha256=expected_roster_sha256)
    else:
        delta, frozen = builder.verified_catchup_projection(source, policy_bytes=policy_bytes,
            policy_sha256=expected_policy_sha256, roster_bytes=roster_bytes, roster_sha256=expected_roster_sha256)
    if (frozen["candidate_cursor"] != end or type(frozen["candidate_prefix"]) is not BridgePrefixPin
            or frozen["candidate_prefix"].cursor != end):
        _refuse()
    return delta, frozen


def commit_generation(root, generation, start, end, approved_tasks, *, expected_pointer_sha256,
                      expected_artifacts, initial_cursor=None, timeout=0.2,
                      policy_bytes=None, expected_policy_sha256=None, roster_bytes=None,
                      expected_roster_sha256=None, expected_anchor_sha256=None, expected_mode="legacy",
                      expected_public_policy_sha256=None, public_policy_bytes=None, expected_anchor_bytes=None,
                      snapshot_result=None, _prefix_required=False, _expected_start_prefix=None,
                      _catchup_source=None, _prepare_attempt=False, _expected_attempt_sha256=None, _fenced=False):
    """Commit index + cursor together. CAS requires exact predecessor bytes.

    For a first publication expected_pointer_sha256 must be None and the
    caller must explicitly provide initial_cursor equal to start. Existing
    pointer history is saved immutably before replacing the pointer. A refusal
    never licenses a retry: after a potentially uncertain OS failure the caller
    must reread and verify the pointer before deciding what happened.
    """
    try:
        if type(_prepare_attempt) is not bool or ((_prepare_attempt or _expected_attempt_sha256 is not None)
                and not _prefix_required):
            _refuse()
        # A fenced publication is opt-in, prefix-only and bound to a prepared attempt digest.
        if type(_fenced) is not bool or (_fenced and (_prepare_attempt or _expected_attempt_sha256 is None)):
            _refuse()
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
        if _prefix_required:
            if (mode["status_mode"] != "public-v1" or type(_expected_start_prefix) is not BridgePrefixPin
                    or _expected_start_prefix.cursor != start or expected_pointer_sha256 is not None):
                _refuse()
            mode_args["expected_start_prefix"] = _expected_start_prefix
        if mode["status_mode"] == "public-v1":
            from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
            if type(snapshot_result) is not dict:
                _refuse()
            if _catchup_source is not None:
                if not _prefix_required:
                    _refuse()
                delta, frozen_catchup = _checked_catchup_delta(_catchup_source, snapshot_result, start, end,
                    policy_bytes=policy_bytes, expected_policy_sha256=expected_policy_sha256, roster_bytes=roster_bytes,
                    expected_roster_sha256=expected_roster_sha256, expected_anchor_sha256=expected_anchor_sha256,
                    expected_public_policy_sha256=expected_public_policy_sha256, public_policy_bytes=public_policy_bytes,
                    expected_anchor_bytes=expected_anchor_bytes, expected_start_prefix=_expected_start_prefix)
                if _sha(json.dumps(delta, sort_keys=True, indent=1).encode("utf-8")) != expected_artifacts["projection_sha256"]:
                    _refuse()
            else:
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
        elif _catchup_source is not None:
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
                    or actual.get("source_coverage") != _catchup_coverage(_catchup_source)
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
            if _prefix_required:
                candidate_prefix = (frozen_catchup if _catchup_source is not None else snapshot_result).get("candidate_prefix")
                if type(candidate_prefix) is not BridgePrefixPin or candidate_prefix.cursor != end:
                    _refuse()
                value.update(schema=PREFIX_SCHEMA, prefix_start_sha256=_expected_start_prefix.sha256,
                             prefix_watermark_sha256=candidate_prefix.sha256)
            if _catchup_source is not None:
                checked, checked_chain = _checked_catchup_delta(_catchup_source, snapshot_result, start, end,
                    policy_bytes=policy_bytes, expected_policy_sha256=expected_policy_sha256, roster_bytes=roster_bytes,
                    expected_roster_sha256=expected_roster_sha256, expected_anchor_sha256=expected_anchor_sha256,
                    expected_public_policy_sha256=expected_public_policy_sha256, public_policy_bytes=public_policy_bytes,
                    expected_anchor_bytes=expected_anchor_bytes, expected_start_prefix=_expected_start_prefix)
                if (json.dumps(checked, sort_keys=True, indent=1).encode() !=
                        json.dumps(delta, sort_keys=True, indent=1).encode()
                        or checked_chain["candidate_prefix"] != candidate_prefix):
                    _refuse()
            elif (mode["status_mode"] == "public-v1" and
                    snapshot.verify_anchored_projection(snapshot_result, delta, expected_anchor_sha256=expected_anchor_sha256,
                        expected_policy_sha256=expected_policy_sha256, expected_roster_sha256=expected_roster_sha256,
                        **mode_args) is not True):
                _refuse()
            _attempt(value, _expected_attempt_sha256)
            if _prepare_attempt:
                return value
            _publish_pointer(root, pointer, None if old is None else raw, expected_pointer_sha256, value, fenced=_fenced)
            return value
    except Exception:
        _refuse()


def commit_history_generation(root, generation, start, end, approved_tasks, *, snapshot_result,
                              expected_pointer_sha256, expected_artifacts, timeout=0.2,
                              policy_bytes, expected_policy_sha256, roster_bytes, expected_roster_sha256,
                              expected_anchor_sha256, expected_mode="legacy", expected_public_policy_sha256=None,
                              public_policy_bytes=None, expected_anchor_bytes=None,
                              _prefix_required=False, _expected_start_prefix=None, _catchup_source=None,
                              _prepare_attempt=False, _expected_attempt_sha256=None, _fenced=False):
    """Publish a union only after verifying the exact bound delta under the writer lock.

    The delta is retained in the private state directory to permit later union
    checks. Publication may leave immutable orphan files after an OS failure;
    no retry is safe until the caller re-reads the pointer. The snapshot is an
    in-process integrity binding, not authenticated source or caller identity.
    This primitive has no scheduler, live wiring, or production authorization.
    """
    try:
        if type(_prepare_attempt) is not bool or ((_prepare_attempt or _expected_attempt_sha256 is not None)
                and not _prefix_required):
            _refuse()
        # A fenced publication is opt-in, prefix-only and bound to a prepared attempt digest.
        if type(_fenced) is not bool or (_fenced and (_prepare_attempt or _expected_attempt_sha256 is None)):
            _refuse()
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
        if _prefix_required:
            if (mode["status_mode"] != "public-v1" or type(_expected_start_prefix) is not BridgePrefixPin
                    or _expected_start_prefix.cursor != start):
                _refuse()
            mode_args["expected_start_prefix"] = _expected_start_prefix
        if _catchup_source is not None:
            if not _prefix_required or mode["status_mode"] != "public-v1":
                _refuse()
            delta, frozen_catchup = _checked_catchup_delta(_catchup_source, snapshot_result, start, end,
                policy_bytes=policy_bytes, expected_policy_sha256=expected_policy_sha256, roster_bytes=roster_bytes,
                expected_roster_sha256=expected_roster_sha256, expected_anchor_sha256=expected_anchor_sha256,
                expected_public_policy_sha256=expected_public_policy_sha256, public_policy_bytes=public_policy_bytes,
                expected_anchor_bytes=expected_anchor_bytes, expected_start_prefix=_expected_start_prefix)
        else:
            delta = snapshot_result.get("projection")
            source = snapshot_result.get("source")
            if (type(source) is not dict or source.get("coverage") != "delta_only_not_complete_history"
                    or source.get("cursor_start_sha256") != _sha(snapshot._token(start).encode("utf-8"))
                    or source.get("cursor_end_sha256") != _sha(snapshot._token(end).encode("utf-8"))):
                _refuse()
        def reverify_delta():
            if _catchup_source is None:
                return snapshot.verify_anchored_projection(snapshot_result, delta,
                    expected_anchor_sha256=expected_anchor_sha256, expected_policy_sha256=expected_policy_sha256,
                    expected_roster_sha256=expected_roster_sha256, **mode_args) is True
            checked, checked_chain = _checked_catchup_delta(_catchup_source, snapshot_result, start, end,
                policy_bytes=policy_bytes, expected_policy_sha256=expected_policy_sha256, roster_bytes=roster_bytes,
                expected_roster_sha256=expected_roster_sha256, expected_anchor_sha256=expected_anchor_sha256,
                expected_public_policy_sha256=expected_public_policy_sha256, public_policy_bytes=public_policy_bytes,
                expected_anchor_bytes=expected_anchor_bytes, expected_start_prefix=_expected_start_prefix)
            return (json.dumps(checked, sort_keys=True, indent=1).encode("utf-8") ==
                    json.dumps(delta, sort_keys=True, indent=1).encode("utf-8") and
                    checked_chain["candidate_prefix"] == frozen_catchup["candidate_prefix"])
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
                                      history_budget=history_bytes, expected_source_mode=mode,
                                      prefix_required=_prefix_required)
            if _prefix_required and _expected_start_prefix.sha256 != old["prefix_watermark_sha256"]:
                _refuse()
            # A reader of the new generation also checks the formerly active
            # generation as archived metadata. Reserve those exact bytes before
            # publishing, using the same budget and mode check as that reader.
            if mode["status_mode"] == "public-v1":
                if _archived_generation_mode(root, old, history_bytes) != mode:
                    _refuse()
            if old["watermark"] != begin or old["generation"] == generation:
                _refuse()
            if not reverify_delta():
                _refuse()
            delta_bytes = json.dumps(delta, sort_keys=True, indent=1).encode("utf-8")
            if len(delta_bytes) > _MAX:
                _refuse()
            digest = _sha(delta_bytes)
            actual = _verify_generation(root, generation, tasks, roster, expected_policy_sha256, expected_roster_sha256)
            if ({key: actual[key] for key in ARTIFACTS} != expected_artifacts
                    or actual.get("source_mode", _requested_source_mode("legacy", None)) != mode
                    or actual.get("source_coverage") != _catchup_coverage(_catchup_source)):
                _refuse()
            value = {"schema": HISTORY_SCHEMA, "generation": generation, "artifacts": dict(expected_artifacts),
                     "policy_sha256": expected_policy_sha256, "roster_sha256": expected_roster_sha256,
                     "anchor_sha256": expected_anchor_sha256, "cursor_start": begin, "cursor_end": finish,
                     "watermark": finish, "empty_advance": None, "previous": old["generation"],
                     "previous_pointer_sha256": expected_pointer_sha256, "delta_projection_sha256": digest}
            if _prefix_required:
                candidate_prefix = (frozen_catchup if _catchup_source is not None else snapshot_result).get("candidate_prefix")
                if type(candidate_prefix) is not BridgePrefixPin or candidate_prefix.cursor != end:
                    _refuse()
                value.update(schema=PREFIX_HISTORY_SCHEMA, prefix_start_sha256=_expected_start_prefix.sha256,
                             prefix_watermark_sha256=candidate_prefix.sha256)
            _validate_history_transition(root, value, old, expected_pointer_sha256, tasks, roster,
                                         expected_policy_sha256, expected_roster_sha256,
                                         policy_bytes, roster_bytes, history_bytes, delta_bytes=delta_bytes)
            if (not reverify_delta()
                    or json.dumps(delta, sort_keys=True, indent=1).encode("utf-8") != delta_bytes
                    or _read(pointer, max_bytes=_MAX_POINTER_BYTES) != raw):
                _refuse()
            attempt = _attempt(value, _expected_attempt_sha256)
            if _prepare_attempt:
                return value
            _fence_gate(root, expected_pointer_sha256, attempt, _fenced)  # before any delta file is written
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
                    or not reverify_delta()
                    or json.dumps(delta, sort_keys=True, indent=1).encode("utf-8") != delta_bytes):
                _refuse()
            _attempt(value, _expected_attempt_sha256)
            _publish_pointer(root, pointer, raw, expected_pointer_sha256, value, fenced=_fenced)
            return value
    except Exception:
        _refuse()
