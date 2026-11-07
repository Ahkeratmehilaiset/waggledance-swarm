"""Default-off, no-I/O producer of one ``wd.q4-shared-session-config.v2`` descriptor.

Every value is an explicit caller argument: nothing is discovered, defaulted, derived from
"latest" state or read from disk. The fleet bytes are checked only against the caller's pin and
the fleet shape that ``Get-WdQ4SharedSessionConfig.ps1`` requires. The checks mirror that
validator's syntax rules so a refused descriptor is never emitted, but they are not a substitute
for it: fixed-drive, reparse-point and fleet-path/pin collisions need the filesystem and stay the
consumer's job. A matching hash is not authentication, authority, freshness or a runtime proof.

The schema has no query field and the caller must not put query text or secrets into paths or
pins. Output is canonical ASCII JSON (sorted keys, no whitespace, no BOM) plus its sha256; the
caller writes the bytes and pins the digest independently. Refusals carry one closed reason and
never a caller value.
"""
import hashlib
import json
import re

SCHEMA = "wd.q4-shared-session-config.v2"
REASONS = frozenset({"fleet_refused", "lanes_refused", "path_refused", "pin_refused", "limit_refused",
                     "schema_refused"})
_LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
_PATHS = ("bundle_root", "public_plan", "interpreter", "cwd", "binding_path", "fixed_plan_path", "snapshot_path",
          "runtime_manifest_path")
_PINS = ("interpreter_sha256", "binding_sha256", "fixed_plan_sha256", "snapshot_sha256", "runtime_manifest_sha256")
_LIMITS = (("interpreter_max_bytes", 134217728), ("timeout_seconds", 1800), ("max_output_bytes", 65536))
_OUTER_KEYS = frozenset({"outer-path", "outer-sha256", "runtime-root", "runtime-generation", "runtime-revision",
                         "verifier-sha256", "public-outer-sha256", "public-child-sha256", "public-query-sha256",
                         "worker-sha256"})
_BINDING_KEYS = frozenset({"pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                           "roster_sha256", "public_policy_sha256", "descriptor_sha256", "projection_sha256"})
_CARD_BINDING_KEYS = _BINDING_KEYS | {"card_decision_sha256"}
_STAGED_CARD_BINDING_KEYS = _CARD_BINDING_KEYS | {"card_policy_sha256"}
_OUTER_FILE = ("assets", "code", "tools", "wd_q4_session_outer_synthetic.py")
_MAX_FLEET = 1048576
_MAX_DESCRIPTOR = 65536
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_DRIVE = re.compile(r"[A-Za-z]:\\")
_CONTROL = re.compile(r"[\x00-\x1f]")
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE | re.DOTALL)


class DescriptorRefused(Exception):
    """Closed reason only; never carries a caller value."""


def _refuse(reason):
    raise DescriptorRefused(reason)


def _pin(value):
    return type(value) is str and _HEX64.match(value) is not None


def _path(value):
    """Get-WdQ4SharedSessionConfig.ps1 Assert-WdSharedPath, syntax part only."""
    if (type(value) is not str or not value or len(value) > 4096 or not _DRIVE.match(value)
            or ":" in value[2:] or "/" in value or any(c in value for c in '<>|"*?') or _CONTROL.search(value)):
        return False
    for part in value[3:].split("\\"):
        if (not part or part in (".", "..") or len(part) > 255 or part.startswith(" ") or part.endswith(" ")
                or part.endswith(".") or _RESERVED.match(part)):
            return False
    return True


def _fold(text):
    """Per-character upper case that keeps length, close to .NET OrdinalIgnoreCase (never merges ss and sharp s)."""
    return "".join(c.upper() if len(c.upper()) == 1 else c for c in text)


def _strict_object(pairs):
    seen = set()
    for key, _ in pairs:
        folded = _fold(key)
        if folded in seen:
            raise ValueError("duplicate key")
        seen.add(folded)
    return dict(pairs)


def _refuse_constant(_):
    raise ValueError("constant")


def _fleet_ok(fleet_bytes, expected_fleet_sha256):
    if (type(fleet_bytes) is not bytes or not 0 < len(fleet_bytes) <= _MAX_FLEET or not _pin(expected_fleet_sha256)
            or hashlib.sha256(fleet_bytes).hexdigest() != expected_fleet_sha256):
        return False
    try:
        fleet = json.loads(fleet_bytes.decode("utf-8"), object_pairs_hook=_strict_object,
                           parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return False
    if (type(fleet) is not dict or type(fleet.get("lanes")) is not list or len(fleet["lanes"]) != 4
            or type(fleet.get("tools_supervisor")) is not dict):
        return False
    agents = []
    for entry in [*fleet["lanes"], fleet["tools_supervisor"]]:
        if type(entry) is not dict or type(entry.get("agent")) is not str or entry["agent"] not in _LANES:
            return False
        agents.append(entry["agent"])
    return fleet["tools_supervisor"]["agent"] == "codex-tools-1" and len(set(agents)) == 5


def _section(value, keys):
    if (type(value) is not dict or set(value) != keys
            or any(type(v) is not str or not v or len(v) > 32767 or _CONTROL.search(v) for v in value.values())):
        _refuse("schema_refused")
    return dict(value)


def build_shared_descriptor(*, enabled=False, fleet_bytes=None, expected_fleet_sha256=None, lanes=None,
                            bundle_root=None, public_plan=None, interpreter=None, interpreter_sha256=None,
                            interpreter_max_bytes=None, cwd=None, timeout_seconds=None, max_output_bytes=None,
                            binding_path=None, binding_sha256=None, fixed_plan_path=None, fixed_plan_sha256=None,
                            snapshot_path=None, snapshot_sha256=None, runtime_manifest_path=None,
                            runtime_manifest_sha256=None, outer=None, expected_binding=None):
    """``{"descriptor_bytes", "descriptor_sha256"}`` for explicit pins, or ``DescriptorRefused``."""
    if enabled is not True:
        return {"status": "OFF"}
    values = {"bundle_root": bundle_root, "public_plan": public_plan, "interpreter": interpreter, "cwd": cwd,
              "binding_path": binding_path, "fixed_plan_path": fixed_plan_path, "snapshot_path": snapshot_path,
              "runtime_manifest_path": runtime_manifest_path, "interpreter_sha256": interpreter_sha256,
              "binding_sha256": binding_sha256, "fixed_plan_sha256": fixed_plan_sha256,
              "snapshot_sha256": snapshot_sha256, "runtime_manifest_sha256": runtime_manifest_sha256,
              "interpreter_max_bytes": interpreter_max_bytes, "timeout_seconds": timeout_seconds,
              "max_output_bytes": max_output_bytes}
    if not _fleet_ok(fleet_bytes, expected_fleet_sha256):
        _refuse("fleet_refused")
    if (type(lanes) is not list or not 1 <= len(lanes) <= 5
            or any(type(lane) is not str or lane not in _LANES for lane in lanes) or len(set(lanes)) != len(lanes)):
        _refuse("lanes_refused")
    if not all(_pin(values[key]) for key in _PINS):
        _refuse("pin_refused")
    if not all(_path(values[key]) for key in _PATHS):
        _refuse("path_refused")
    for key, maximum in _LIMITS:
        if type(values[key]) is not int or not 1 <= values[key] <= maximum:
            _refuse("limit_refused")
    outer = _section(outer, _OUTER_KEYS)
    if (type(expected_binding) is not dict or frozenset(expected_binding) not in
            (_BINDING_KEYS, _CARD_BINDING_KEYS, _STAGED_CARD_BINDING_KEYS)):
        _refuse("schema_refused")
    binding = _section(expected_binding, frozenset(expected_binding))
    if (not all(_pin(outer[key]) for key in _OUTER_KEYS if key.endswith("sha256"))
            or not all(_pin(value) for value in binding.values())
            or not all(_REVISION.match(outer[key]) for key in ("runtime-generation", "runtime-revision"))):
        _refuse("pin_refused")
    root, outer_path, manifest = outer["runtime-root"], outer["outer-path"], runtime_manifest_path
    if not _path(root) or not _path(outer_path):
        _refuse("path_refused")
    root_folded, manifest_folded = _fold(root), _fold(manifest)
    if (_fold(outer_path) != _fold("\\".join((root, *_OUTER_FILE)))
            or root_folded[:3] != manifest_folded[:3] or manifest_folded == root_folded
            or manifest_folded.startswith(root_folded + "\\")):
        _refuse("path_refused")
    pinned = {}
    for path, pin in ((outer_path, outer["outer-sha256"]), (interpreter, interpreter_sha256),
                      (binding_path, binding_sha256), (fixed_plan_path, fixed_plan_sha256),
                      (snapshot_path, snapshot_sha256), (manifest, runtime_manifest_sha256)):
        if pinned.setdefault(_fold(path), pin) != pin:
            _refuse("pin_refused")
    document = {**values, "schema": SCHEMA, "fleet_sha256": expected_fleet_sha256, "lanes": list(lanes),
                "outer": outer, "expected_binding": binding}
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                     allow_nan=False).encode("ascii")
    if len(raw) > _MAX_DESCRIPTOR:
        _refuse("schema_refused")
    return {"descriptor_bytes": raw, "descriptor_sha256": hashlib.sha256(raw).hexdigest()}
