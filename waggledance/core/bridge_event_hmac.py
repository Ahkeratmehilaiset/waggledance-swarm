# SPDX-License-Identifier: BUSL-1.1
"""Bridge-event HMAC identity binding — Phase A (sign + log-only verify).

The bridge's "3-identity consensus" currently trusts a self-declared
``agent`` field. This module is the cryptographic core that makes those
identities verifiable: a per-agent secret key (provisioned by the operator
OUTSIDE the repo) signs the identity-binding fields of every event, and an
auditor can verify the signature against the same key.

PHASE A CONTRACT (this module):
- SIGN + VERIFY primitives only. Nothing here is wired into any gate;
  verification results are for LOG-ONLY auditing. Flipping any gate to
  enforce signatures is Phase B — a separate, operator-signed change.
- NO KEY MATERIAL in the repo. Keys are raw 32-byte secrets stored as hex
  in ``<key_dir>/<agent>.key``; ``key_dir`` always comes from the caller
  (or the ``WD_AGENT_KEY_DIR`` environment variable). ``key_id`` is a
  digest prefix and never reveals the key.
- NO EVENT SCHEMA CHANGE. The signature travels inside the event's free
  ``payload`` object under ``payload["hmac"]`` so existing validators and
  readers are untouched.

Signed fields (exact, closed set): agent, ts_utc, type, status, task_id,
and the sha256 digest of the message text. Canonical JSON encoding makes
the binding deterministic and separator-injection-safe; tampering with any
bound field (or the message) invalidates the signature.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import os
import secrets
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest

BRIDGE_EVENT_HMAC_SCHEME = "wd.bridge_event_hmac.v0"
KEY_DIR_ENV = "WD_AGENT_KEY_DIR"
_KEY_BYTES = 32

# Verification statuses (closed set; "valid" is the only success).
SIG_VALID = "valid"
SIG_INVALID = "invalid"
SIG_UNSIGNED = "unsigned"
SIG_UNVERIFIABLE = "unverifiable"
SIGNATURE_STATUSES = (SIG_VALID, SIG_INVALID, SIG_UNSIGNED, SIG_UNVERIFIABLE)


class BridgeEventHmacError(ValueError):
    """Raised on unsafe/ambiguous signing inputs (fail-closed)."""


def _require_str(name: str, value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise BridgeEventHmacError(f"{name} must be a string")
    if not allow_empty and not value.strip():
        raise BridgeEventHmacError(f"{name} must be non-empty")
    return value


def canonical_signing_bytes(
    *,
    agent: str,
    ts_utc: str,
    event_type: str,
    status: str,
    task_id: str,
    message: str,
) -> bytes:
    """Deterministic, injection-safe byte encoding of the bound fields.

    The message is bound via its sha256 digest (the raw text never enters
    the signed structure), and canonical JSON encoding guarantees field
    boundaries cannot be forged by crafted separator content.
    """
    agent = _require_str("agent", agent)
    ts_utc = _require_str("ts_utc", ts_utc)
    event_type = _require_str("event_type", event_type)
    status = _require_str("status", status, allow_empty=True)
    task_id = _require_str("task_id", task_id, allow_empty=True)
    message = _require_str("message", message, allow_empty=True)
    return canonical_json_bytes(
        {
            "scheme": BRIDGE_EVENT_HMAC_SCHEME,
            "agent": agent,
            "ts_utc": ts_utc,
            "type": event_type,
            "status": status,
            "task_id": task_id,
            "message_digest": sha256_digest({"message": message}),
        }
    )


def key_id_for(key: bytes) -> str:
    """Non-reversible short identifier for a key (safe to publish)."""
    return "k:" + hashlib.sha256(key).hexdigest()[:16]


def sign_event_fields(
    *,
    key: bytes,
    agent: str,
    ts_utc: str,
    event_type: str,
    status: str = "",
    task_id: str = "",
    message: str = "",
) -> dict[str, str]:
    """Return the ``payload.hmac`` object binding this event to ``key``."""
    if not isinstance(key, (bytes, bytearray)) or len(key) < 16:
        raise BridgeEventHmacError("key must be at least 16 raw bytes")
    digest = _hmac.new(
        bytes(key),
        canonical_signing_bytes(
            agent=agent,
            ts_utc=ts_utc,
            event_type=event_type,
            status=status,
            task_id=task_id,
            message=message,
        ),
        hashlib.sha256,
    ).hexdigest()
    return {
        "scheme": BRIDGE_EVENT_HMAC_SCHEME,
        "sig": f"hmac-sha256:{digest}",
        "key_id": key_id_for(bytes(key)),
    }


def verify_event_signature(
    event: Mapping[str, Any],
    key_lookup: Callable[[str], Optional[bytes]],
) -> dict[str, Any]:
    """Log-only verification verdict for one bridge event.

    Closed verdict set: ``valid`` (signature recomputes under the agent's
    key), ``invalid`` (present but does not recompute, or malformed —
    fail-closed), ``unsigned`` (no ``payload.hmac``), ``unverifiable``
    (signed, but no key available to check). Phase A NEVER enforces —
    the caller records the verdict, nothing more.
    """
    agent = str(event.get("agent") or "")
    payload = event.get("payload")
    hmac_obj = payload.get("hmac") if isinstance(payload, Mapping) else None
    base = {
        "agent": agent,
        "ts_utc": str(event.get("ts_utc") or ""),
        "scheme": BRIDGE_EVENT_HMAC_SCHEME,
        "enforcement_applied": False,
    }
    if not isinstance(hmac_obj, Mapping) or not hmac_obj.get("sig"):
        return {**base, "status": SIG_UNSIGNED, "key_id": None}
    key = key_lookup(agent) if agent else None
    claimed_key_id = hmac_obj.get("key_id")
    if not isinstance(key, (bytes, bytearray)) or not key:
        return {
            **base,
            "status": SIG_UNVERIFIABLE,
            "key_id": claimed_key_id if isinstance(claimed_key_id, str) else None,
        }
    try:
        expected = sign_event_fields(
            key=bytes(key),
            agent=agent,
            ts_utc=str(event.get("ts_utc") or ""),
            event_type=str(event.get("type") or ""),
            status=str(event.get("status") or ""),
            task_id=str(event.get("task_id") or ""),
            message=str(event.get("message") or ""),
        )
    except BridgeEventHmacError:
        return {**base, "status": SIG_INVALID, "key_id": key_id_for(bytes(key))}
    sig = hmac_obj.get("sig")
    valid = isinstance(sig, str) and _hmac.compare_digest(
        sig, expected["sig"]
    )
    return {
        **base,
        "status": SIG_VALID if valid else SIG_INVALID,
        "key_id": expected["key_id"],
    }


def resolve_key_dir(key_dir: Optional[Path] = None) -> Optional[Path]:
    """Caller-supplied key directory, else WD_AGENT_KEY_DIR, else None."""
    if key_dir is not None:
        return Path(key_dir)
    env_value = os.environ.get(KEY_DIR_ENV, "").strip()
    return Path(env_value) if env_value else None


def load_agent_key(agent: str, key_dir: Optional[Path]) -> Optional[bytes]:
    """Load ``<key_dir>/<agent>.key`` (hex). Missing anything -> None."""
    agent = _require_str("agent", agent)
    directory = resolve_key_dir(key_dir)
    if directory is None:
        return None
    path = directory / f"{agent}.key"
    try:
        text = path.read_text(encoding="utf-8").strip()
        key = bytes.fromhex(text)
    except (OSError, ValueError):
        return None
    return key if len(key) >= 16 else None


def generate_agent_key(agent: str, key_dir: Path) -> Path:
    """Create a new random key file for ``agent`` (never overwrites).

    Operator-run provisioning helper; the key never leaves ``key_dir``.
    """
    agent = _require_str("agent", agent)
    directory = Path(key_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{agent}.key"
    if path.exists():
        raise BridgeEventHmacError(
            f"key for {agent!r} already exists (refusing to overwrite)"
        )
    path.write_text(secrets.token_bytes(_KEY_BYTES).hex(), encoding="utf-8")
    return path
