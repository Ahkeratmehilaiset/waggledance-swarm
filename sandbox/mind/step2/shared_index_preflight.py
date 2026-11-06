"""Lightweight shared checkpoint preflight; pins are supplied by the caller.

Validates public control bytes only, without model, native index, or source authentication.
"""
from dataclasses import asdict
import hashlib
import json
from types import MappingProxyType
from waggledance.core.bridge_log_reader import BridgeCursor
from sandbox.mind.step2 import shared_index_pointer as _pointer_contract
SCHEMA = _pointer_contract.SCHEMA
PREFIX_SCHEMA = _pointer_contract.PREFIX_SCHEMA
HEX = _pointer_contract.HEX
NAME = _pointer_contract.NAME
ARTIFACTS = _pointer_contract.ARTIFACTS
_MAX = _pointer_contract._MAX
_MAX_POINTER_BYTES = _pointer_contract._MAX_POINTER_BYTES
_MAX_ACTIVE_ARTIFACT_BYTES = 16 * 1024 * 1024
_MAX_ACTIVE_SNAPSHOT_BYTES = 64 * 1024 * 1024
CheckpointRefused = _pointer_contract.CheckpointRefused
PUBLIC_AGENTS = frozenset({"codex-lead-1", "codex-tools-1", "fable-5"})

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

def _parse_bound_pointer(data, *, policy_sha, roster_sha, prefix_required):
    """Parse pointer bytes using the shared lightweight contract."""
    return _pointer_contract._parse_bound_pointer(data, policy_sha=policy_sha, roster_sha=roster_sha,
                                                  prefix_required=prefix_required)

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

def _validate_active_pointer_binding(old, actual, policy_sha, roster_sha, expected_source_mode):
    if ({key: actual[key] for key in ARTIFACTS} != old["artifacts"]
            or actual.get("source_mode", _requested_source_mode("legacy", None)) != expected_source_mode
            or actual.get("source_binding") != _source_binding(
                BridgeCursor(**old["cursor_start"]), BridgeCursor(**old["cursor_end"]),
                old["anchor_sha256"], old["artifacts"]["projection_sha256"] if old["schema"] in (SCHEMA, PREFIX_SCHEMA)
                else old["delta_projection_sha256"], policy_sha, roster_sha)):
        _refuse()

_ENCODER_KEYS = frozenset({"model", "revision", "weights_sha256"})

def _encoder_digest(value):
    if (type(value) is not dict or set(value) != _ENCODER_KEYS
            or any(type(value[key]) is not str or not value[key] for key in _ENCODER_KEYS)
            or not (isinstance(value["weights_sha256"], str) and HEX.fullmatch(value["weights_sha256"]) is not None)):
        raise ValueError()
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def _preflight_public_snapshot(pointer, active, tasks, roster, *, public_policy_bytes,
                               public_policy_sha256, expected_source_sha256, expected_encoder_sha256,
                               _public_agents=PUBLIC_AGENTS):
    """Check caller-pinned public controls only; this does not approve disclosure."""
    descriptor = projected = public_tasks = public_types = record = meta = validated = contents = mode = None
    try:
        from sandbox.mind.step2 import shared_capture_adapter as adapter
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
                    or type(meta.get("agent")) is not str or meta["agent"] not in _public_agents):
                _refuse()
        return None
    except Exception:
        pass
    del pointer, active, tasks, roster, public_policy_bytes, descriptor, projected, public_tasks, public_types
    del record, meta, public_policy_sha256, expected_source_sha256, expected_encoder_sha256, validated, contents, mode
    _refuse()
