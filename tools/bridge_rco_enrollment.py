"""Read-only RCO3 enrollment at an explicit operator-approved manifest boundary.

This does not register, provision, launch or observe a process. ``rco_enrollments``
in a FUTURE installed deployment manifest is an OPERATOR provisioning attestation,
not an agent's self-attestation about history, authorship, cases or token state.
The current bundle without that record is not_provisioned, never enrolled.
The record's PID/start/session/run fields are the prior registration observation
receipt, NOT future restart pins: final manifest bytes must freeze before launch.
Durable binding is role UUID + actually observed conversation + target generation.
The current event needs its own nonblank session/run and observed positive
PID/launcher lifetime. This Source contract alone does not make cold adoption ready.

Trust is the externally supplied manifest SHA256 plus the existing same-user
bridge trust. Event execution evidence is still unsigned: equality is consistency,
NOT cryptographic origin, OS isolation or independent native observation. A
loader-issued context prevents accidental JSON/event-derived authority and detects
report mutation; it is NOT a security boundary against code in this Python process.

Record v1 has EXACTLY the keys in _RECORD_KEYS. Other ordinary manifest fields
and other event/evidence metadata are permitted, but cannot supply missing proof.
No pointer rebinding, writes, process queries, network, launches or environment
changes occur. Defaults use only WD_BRIDGE_BUNDLE_ROOT and
WD_REBOOT_EXPECTED_MANIFEST_HASH; an explicitly invalid argument never falls back.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from uuid import UUID


MAX_MANIFEST_BYTES = 1024 * 1024
RCO3_AGENT = "claude-rco-3"
RCO3_ROLE_UUID = "3c7f49d1-5ac4-4ca3-8ff5-6c97c07bcd50"
_LEGACY_RCOS = frozenset({"claude-rco-1", "claude-rco-2"})
_ISSUER = object()
_HEX40 = re.compile(r"[0-9a-fA-F]{40}\Z")
_HEX64 = re.compile(r"[0-9a-fA-F]{64}\Z")
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
_UTC = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(?:Z|\+00:00)\Z")
_RECORD_KEYS = frozenset({
    "schema", "agent", "agent_uuid", "generation", "session_id", "run_id",
    "native_conversation_id", "cli_kind", "native_pid", "launcher_pid",
    "native_process_start_utc", "observed_at_utc", "token_integrity", "elevated",
    "fresh_history_free", "no_mind_authorship", "no_measurement_case_access",
})


class _ReadOnlyDict(dict):
    def _refuse(self, *args, **kwargs):
        raise TypeError("loader-issued enrollment reports are read-only")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _refuse


class _EnrollmentContext(_ReadOnlyDict):
    __slots__ = ("_issuer", "_digest", "_validated")

    def __init__(self, report, validated, issuer):
        if issuer is not _ISSUER:
            raise TypeError("enrollment context must be issued by the loader")
        dict.__init__(self, report)
        self._issuer = issuer
        self._digest = _fingerprint(self)
        # Only scalar values, in immutable tuples; never retain aliases to report dicts.
        self._validated = validated


def _fingerprint(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")).digest()


def _context(status, reason, *, generation=None, manifest_sha256=None, record=None):
    frozen_record = _ReadOnlyDict(record) if record is not None else None
    report = {
        "schema": "wd.rco-enrollment-context.v1", "status": status,
        "agent": RCO3_AGENT, "generation": generation,
        "manifest_sha256": manifest_sha256, "enrollment": frozen_record, "reason": reason,
    }
    validated = (status, generation, manifest_sha256,
                 tuple(record.items()) if record is not None else ())
    return _EnrollmentContext(report, validated, _ISSUER)


def _validated_context(context):
    if context is None:
        context = load_rco_enrollment_context()
    if type(context) is not _EnrollmentContext or context._issuer is not _ISSUER:
        return None
    try:
        if _fingerprint(context) != context._digest:
            return None
    except (TypeError, ValueError, RecursionError):
        return None
    return context._validated


def _hex(value, pattern):
    return value.lower() if type(value) is str and pattern.fullmatch(value) else None


def _uuid(value):
    if type(value) is not str or not _UUID.fullmatch(value):
        return None
    return str(UUID(value))


def _nonblank(value):
    return type(value) is str and bool(value) and value == value.strip()


def _utc(value):
    if type(value) is not str:
        return None
    match = _UTC.fullmatch(value)
    if match is None:
        return None
    try:
        whole_second = datetime.strptime(match[1], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    # Keep .NET's seventh fractional digit; datetime alone would truncate it.
    return whole_second, int((match[2] or "").ljust(9, "0"))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("non-finite JSON constant")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _no_reparse(path):
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("reparse path")


def _manifest_bytes(root):
    _no_reparse(root)
    if not root.is_dir():
        raise ValueError("bundle root is not a directory")
    path = root / "deployment-manifest.json"
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400
            or before.st_size > MAX_MANIFEST_BYTES):
        raise ValueError("manifest is linked, non-regular or oversized")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("manifest changed before open")
        data = stream.read(MAX_MANIFEST_BYTES)
        after = os.fstat(stream.fileno())
    final = path.lstat()
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if identity(before) != identity(after) or identity(after) != identity(final) or len(data) != before.st_size:
        raise ValueError("manifest changed or ended ambiguously")
    return data


def _record_valid(record, generation):
    if type(record) is not dict or set(record) != _RECORD_KEYS:
        return False
    if (record["schema"] != "wd.rco-enrollment.v1" or record["agent"] != RCO3_AGENT
            or record["agent_uuid"] != RCO3_ROLE_UUID
            or _hex(record["generation"], _HEX40) != generation
            or not _nonblank(record["session_id"]) or not _nonblank(record["run_id"])
            or _uuid(record["native_conversation_id"]) in (None, RCO3_ROLE_UUID)
            or record["cli_kind"] != "claude.exe"):
        return False
    if any(type(record[key]) is not int or record[key] <= 0 for key in ("native_pid", "launcher_pid")):
        return False
    started, observed = _utc(record["native_process_start_utc"]), _utc(record["observed_at_utc"])
    return (started is not None and observed is not None and started <= observed
            and record["token_integrity"] == "Medium" and record["elevated"] is False
            and all(record[key] is True for key in (
                "fresh_history_free", "no_mind_authorship", "no_measurement_case_access")))


def load_rco_enrollment_context(*, bundle_root=None, expected_manifest_sha256=None) -> dict:
    """Load one bounded externally pinned manifest, or return an unknown context.

    Missing RCO3 entry in a VERIFIED manifest means not_provisioned. Missing,
    malformed, unreadable or conflicting trust inputs mean unknown instead.
    """
    root_value = os.environ.get("WD_BRIDGE_BUNDLE_ROOT") if bundle_root is None else bundle_root
    pin_value = (os.environ.get("WD_REBOOT_EXPECTED_MANIFEST_HASH")
                 if expected_manifest_sha256 is None else expected_manifest_sha256)
    pin = _hex(pin_value, _HEX64)
    if pin is None or not isinstance(root_value, (str, os.PathLike)) or not os.fspath(root_value):
        return _context("unknown", "missing_or_invalid_external_pin_or_root")
    try:
        root = Path(root_value)
        if not root.is_absolute() or ".." in root.parts:
            return _context("unknown", "bundle_root_not_absolute_generation_root")
        data = _manifest_bytes(root)
        if hashlib.sha256(data).hexdigest() != pin:
            return _context("unknown", "manifest_hash_mismatch")
        # JSON is never interpreted before the independent hash anchor matches.
        manifest = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                              parse_constant=_constant, parse_float=_finite_float)
        if type(manifest) is not dict:
            return _context("unknown", "manifest_not_object")
        generation = _hex(manifest.get("source_commit"), _HEX40)
        if generation is None or root.name.lower() != generation:
            return _context("unknown", "manifest_generation_root_mismatch")
        if "rco_enrollments" not in manifest:
            return _context("not_provisioned", "verified_manifest_has_no_enrollment",
                            generation=generation, manifest_sha256=pin)
        entries = manifest["rco_enrollments"]
        if type(entries) is not dict:
            return _context("unknown", "enrollments_not_object")
        if RCO3_AGENT not in entries:
            return _context("not_provisioned", "verified_manifest_has_no_rco3_entry",
                            generation=generation, manifest_sha256=pin)
        record = entries[RCO3_AGENT]
        if not _record_valid(record, generation):
            return _context("unknown", "malformed_or_conflicting_enrollment")
        return _context("enrolled", "operator_manifest_attestation_validated",
                        generation=generation, manifest_sha256=pin, record=record)
    except (OSError, TypeError, ValueError, OverflowError, RecursionError):
        return _context("unknown", "manifest_unreadable_or_invalid")


def rco_enrollment_status(agent, *, context=None) -> str:
    """not_required / not_provisioned / enrolled / unknown; never infer enrollment."""
    if not _nonblank(agent):
        return "unknown"
    if agent != RCO3_AGENT:
        return "not_required"
    validated = _validated_context(context)
    return validated[0] if validated is not None else "unknown"


def rco_event_enrollment_matches(event, *, context=None) -> bool:
    """Bind durable enrollment identity and current observed execution, not old PIDs."""
    if not isinstance(event, Mapping):
        return False
    agent = event.get("agent")
    if type(agent) is str and agent in _LEGACY_RCOS:
        return True
    if agent != RCO3_AGENT:
        return False
    validated = _validated_context(context)
    if validated is None or validated[0] != "enrolled":
        return False
    _, generation, manifest_hash, items = validated
    record = dict(items)
    if any(type(event.get(key)) is not str or event[key] != record[key]
           for key in ("agent", "agent_uuid")):
        return False
    if not all(_nonblank(event.get(key)) for key in ("session_id", "run_id")):
        return False
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return False
    proof = payload.get("execution_evidence")
    if not isinstance(proof, Mapping):
        return False
    for key in ("pin_error", "ancestry_error"):
        value = proof.get(key)
        if value is not None and not (type(value) is str and value == ""):
            return False
    if (proof.get("pin_status") != "manifest_and_launcher_verified"
            or proof.get("observed_agent") != RCO3_AGENT
            or proof.get("cli_kind") != record["cli_kind"]
            or _uuid(proof.get("native_conversation_id")) != _uuid(record["native_conversation_id"])
            or _hex(proof.get("generation"), _HEX40) != generation
            or _hex(proof.get("manifest_sha256"), _HEX64) != manifest_hash):
        return False
    started = _utc(proof.get("native_process_start_utc"))
    observed = _utc(proof.get("observed_at_utc"))
    return (started is not None and observed is not None and started <= observed
            and all(type(proof.get(key)) is int and proof[key] > 0
                    for key in ("native_pid", "launcher_pid")))
