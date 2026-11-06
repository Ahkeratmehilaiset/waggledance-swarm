"""Hash-pinned pointer parsing shared by Q4 parent preflight and checkpoint code.

Parsing does not authenticate the caller pin or validate artifact contents/history.
"""
from dataclasses import asdict
import hashlib
import json
import re

from waggledance.core.bridge_log_reader import BridgeCursor

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
_MAX = 16 * 1024 * 1024
_MAX_POINTER_BYTES = 16384


class CheckpointRefused(ValueError):
    """Fixed error; never returns a path, raw input or private backend text."""

    # Preserve the historical qualified name and exception pickle contract.
    __module__ = "sandbox.mind.step2.shared_index_checkpoint"

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

def _cursor(value):
    if (type(value) is not BridgeCursor or type(value.offset) is not int or value.offset < 0
            or not isinstance(value.file_identity, str) or IDENTITY.fullmatch(value.file_identity) is None
            or (value.generation is not None and (not isinstance(value.generation, str)
                                                  or GENERATION.fullmatch(value.generation) is None))):
        _refuse()
    return asdict(value)

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
