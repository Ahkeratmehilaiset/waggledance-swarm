# SPDX-License-Identifier: BUSL-1.1
"""Pure validation for an externally owned Codex-session descriptor.

This module is deliberately an observation boundary.  It neither discovers nor
connects to an endpoint, starts or stops a process, reads a descriptor from disk,
or sends a model/API request.  A caller must supply two separate objects:

* an **untrusted descriptor**, which may be copied from an observation channel;
* **trusted launcher evidence**, supplied by the already verified owning
  launcher, binding the exact descriptor digest and session identity.

The validator cannot turn a PID, a resume UUID, or a self-asserted boolean into
control of a live terminal.  Even a valid result is observation-only:
``control_allowed`` is structurally false on every return path.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit


DESCRIPTOR_SCHEMA = "wd.owning-session-descriptor.v1"
LAUNCHER_EVIDENCE_SCHEMA = "wd.launcher-ownership-evidence.v1"
VALIDATION_SCHEMA = "wd.owning-session-validation.v1"

MAX_DESCRIPTOR_BYTES = 32 * 1024
MAX_LIFETIME = timedelta(minutes=10)
MAX_REPLAYED_DESCRIPTOR_IDS = 4096

BINDING_FIELDS = (
    "agent",
    "agent_uuid",
    "run_id",
    "session_id",
    "thread_id",
    "native_pid",
    "native_process_start_utc",
    "launcher_pid",
    "launcher_process_start_utc",
    "generation",
    "cli_sha256",
)

_AGENT_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$"
)
_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def descriptor_digest(descriptor: Mapping[str, Any]) -> str:
    """Return the exact canonical SHA-256 which launcher evidence must bind.

    This hashes supplied data only.  It does not authenticate that data; the
    caller's verified launcher-evidence channel remains the trust boundary.
    """
    encoded = _canonical_json(descriptor)
    return hashlib.sha256(encoded).hexdigest()


def validate_owning_session_descriptor(
    descriptor: Any,
    launcher_evidence: Any,
    *,
    now_utc: Any,
    replayed_descriptor_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate one descriptor against independently supplied launcher evidence.

    ``now_utc`` is explicit so callers can make a bounded, reproducible idle
    observation without reading a clock or any external state.  Replay history
    is also caller-supplied; keeping it outside this function prevents hidden
    storage and preserves the no-I/O property.
    """
    now = _parse_utc(now_utc)
    if now is None:
        return _result(False, "invalid_now_utc")
    if not isinstance(descriptor, Mapping):
        return _result(False, "descriptor_not_object")
    descriptor_map = dict(descriptor)
    if "owner_verified" in descriptor_map:
        # A descriptor is untrusted input.  Its own attestation is never an
        # ownership proof, regardless of whether it says true or false.
        return _result(False, "descriptor_self_attestation_forbidden")

    try:
        encoded = _canonical_json(descriptor_map)
    except ValueError:
        return _result(False, "descriptor_not_canonical_json")
    if len(encoded) > MAX_DESCRIPTOR_BYTES:
        return _result(False, "descriptor_too_large")

    descriptor_id = descriptor_map.get("descriptor_id")
    if not _valid_uuid(descriptor_id):
        return _result(False, "invalid_descriptor_id")
    if descriptor_map.get("schema") != DESCRIPTOR_SCHEMA:
        return _result(False, "invalid_descriptor_schema", descriptor_id)

    surface = descriptor_map.get("conversation_surface")
    if surface == "native_terminal":
        return _result(False, "native_terminal_forbidden", descriptor_id)
    if surface != "app_server_owned":
        return _result(False, "unsupported_conversation_surface", descriptor_id)
    scope = descriptor_map.get("readiness_scope")
    if scope == "native_cli_only":
        return _result(False, "native_cli_only_forbidden", descriptor_id)
    if scope != "owner_bound_observation":
        return _result(False, "unsupported_readiness_scope", descriptor_id)
    adapter_kind = descriptor_map.get("adapter_kind")
    if adapter_kind == "capacity_collector":
        return _result(False, "collector_descriptor_forbidden", descriptor_id)
    if adapter_kind != "owning_session_adapter":
        return _result(False, "unsupported_adapter_kind", descriptor_id)

    endpoint = descriptor_map.get("endpoint_uri")
    if not _valid_endpoint(endpoint):
        return _result(False, "missing_or_invalid_endpoint", descriptor_id)

    binding = descriptor_map.get("binding")
    binding_error = _binding_error(binding)
    if binding_error:
        return _result(False, binding_error, descriptor_id)

    issued = _parse_utc(descriptor_map.get("issued_at_utc"))
    expires = _parse_utc(descriptor_map.get("expires_at_utc"))
    if issued is None or expires is None:
        return _result(False, "invalid_descriptor_lifetime", descriptor_id)
    if issued > now:
        return _result(False, "descriptor_not_yet_valid", descriptor_id)
    if expires <= now or expires <= issued or expires - issued > MAX_LIFETIME:
        return _result(False, "descriptor_stale_or_invalid_lifetime", descriptor_id)

    replayed = _normalise_replayed_ids(replayed_descriptor_ids)
    if replayed is None:
        return _result(False, "invalid_replay_guard", descriptor_id)
    if descriptor_id in replayed:
        return _result(False, "descriptor_replayed", descriptor_id)

    evidence_error = _evidence_error(
        launcher_evidence,
        descriptor=descriptor_map,
        descriptor_sha256=hashlib.sha256(encoded).hexdigest(),
    )
    if evidence_error:
        return _result(False, evidence_error, descriptor_id)

    return _result(True, "verified_launcher_bound_observation", descriptor_id)


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("not canonical JSON") from exc


def _result(valid: bool, reason: str, descriptor_id: str | None = None) -> dict[str, Any]:
    return {
        "schema": VALIDATION_SCHEMA,
        "valid": valid,
        "reason": reason,
        "descriptor_id": descriptor_id,
        "observation_allowed": valid,
        # Structural safety invariant: validation never grants a control path.
        "control_allowed": False,
        "live_capability": "none",
        "model_calls": 0,
        "io_operations": 0,
    }


def _valid_text(value: Any, *, maximum: int = 256) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum and value == value.strip()


def _valid_uuid(value: Any) -> bool:
    return isinstance(value, str) and _UUID_RE.fullmatch(value) is not None


def _valid_pid(value: Any) -> bool:
    return type(value) is int and 0 < value <= 2_147_483_647


def _parse_utc(value: Any) -> datetime | None:
    if not _valid_text(value, maximum=64):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _valid_endpoint(value: Any) -> bool:
    if not _valid_text(value, maximum=1024):
        return False
    try:
        parsed = urlsplit(value)
        # ``urlsplit`` defers malformed bracketed hosts and ports until these
        # properties are accessed.  Force that validation here so supplied
        # descriptor data cannot make this pure fail-closed validator raise.
        _ = parsed.hostname
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"ws", "wss"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _binding_error(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return "binding_not_object"
    binding = dict(value)
    if not _AGENT_RE.fullmatch(str(binding.get("agent") or "")):
        return "invalid_binding:agent"
    if not _valid_uuid(binding.get("agent_uuid")):
        return "invalid_binding:agent_uuid"
    for field in ("run_id", "session_id", "thread_id"):
        if not _valid_text(binding.get(field)):
            return f"invalid_binding:{field}"
    for field in ("native_pid", "launcher_pid"):
        if not _valid_pid(binding.get(field)):
            return f"invalid_binding:{field}"
    for field in ("native_process_start_utc", "launcher_process_start_utc"):
        if _parse_utc(binding.get(field)) is None:
            return f"invalid_binding:{field}"
    if not isinstance(binding.get("generation"), str) or not _HEX_40_RE.fullmatch(
        binding["generation"]
    ):
        return "invalid_binding:generation"
    if not isinstance(binding.get("cli_sha256"), str) or not _HEX_64_RE.fullmatch(
        binding["cli_sha256"]
    ):
        return "invalid_binding:cli_sha256"
    return None


def _normalise_replayed_ids(value: Any) -> frozenset[str] | None:
    if isinstance(value, (str, bytes)):
        return None
    try:
        iterator = iter(value)
    except TypeError:
        return None
    values: set[str] = set()
    try:
        # Do not materialise an untrusted iterable: its cardinality need not
        # be knowable beforehand and duplicates would otherwise evade a set
        # size limit.  The 4,097th supplied item is enough to reject it.
        for count, item in enumerate(iterator, start=1):
            if count > MAX_REPLAYED_DESCRIPTOR_IDS or not _valid_uuid(item):
                return None
            values.add(item)
    except Exception:
        # An arbitrary iterator is untrusted input.  Preserve the validator's
        # fail-closed contract while intentionally not catching BaseException.
        return None
    return frozenset(values)


def _evidence_error(
    value: Any,
    *,
    descriptor: Mapping[str, Any],
    descriptor_sha256: str,
) -> str | None:
    if not isinstance(value, Mapping):
        return "launcher_evidence_not_object"
    evidence = dict(value)
    if evidence.get("schema") != LAUNCHER_EVIDENCE_SCHEMA:
        return "invalid_launcher_evidence_schema"
    if evidence.get("owner_verified") is not True:
        return "launcher_owner_not_verified"
    if evidence.get("descriptor_sha256") != descriptor_sha256:
        return "trusted_descriptor_digest_mismatch"
    trusted_binding = evidence.get("binding")
    if not isinstance(trusted_binding, Mapping):
        return "trusted_binding_not_object"
    for field in BINDING_FIELDS:
        if trusted_binding.get(field) != descriptor["binding"].get(field):
            return f"trusted_binding_mismatch:{field}"
    for field in ("conversation_surface", "readiness_scope", "adapter_kind", "endpoint_uri"):
        if evidence.get(field) != descriptor.get(field):
            return f"trusted_descriptor_mismatch:{field}"
    return None


__all__ = [
    "BINDING_FIELDS",
    "DESCRIPTOR_SCHEMA",
    "LAUNCHER_EVIDENCE_SCHEMA",
    "MAX_REPLAYED_DESCRIPTOR_IDS",
    "VALIDATION_SCHEMA",
    "descriptor_digest",
    "validate_owning_session_descriptor",
]
