# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""CredentialVault interface + OSKeyringVault impl + NoOpVault stub.

The vault is the ONLY legal source of credential material in v3.13.0.
Connectors (AuthenticatedConnector per Band A schema) hold a
CredentialRef -- a URI of form

    vault://<impl>/<tenant_or_profile>/<scope>/<name>

-- not the secret material itself. At use time the connector calls
vault.get(ref) and receives a CredentialMaterial wrapper whose repr,
str, and pickle are redacted; only an explicit .reveal() call
returns the raw bytes.

Sensitive-mode logging contract (per design spec, three hard rules):
1. Never log material via __repr__ / __str__ / pickle.
2. Never persist material outside the vault.
3. Audit every retrieval via auth.credential_retrieved event.

Design spec:
iterations/anchor_use_case/sprint_1/claude_lane/credential_vault_threat_model.md
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol


# --------------------------------------------------------------------------
# CredentialRef -- URI form vault://<impl>/<tenant_or_profile>/<scope>/<name>
# --------------------------------------------------------------------------


_REF_PATTERN = re.compile(
    r"^vault://"
    r"(?P<impl>[a-z0-9_-]+)/"
    r"(?P<tenant>[a-z0-9_-]+)/"
    r"(?P<scope>[a-z0-9_.-]+)/"
    r"(?P<name>[a-z0-9_.-]+)$"
)


@dataclass(frozen=True)
class CredentialRef:
    """Stable URI reference to a credential. NEVER contains material.

    Format: vault://<impl>/<tenant_or_profile>/<scope>/<name>

    Per Codex RCO edit #10: tenant_or_profile MUST be a non-PII ID
    (e.g. profile_42, never a personal identifier).
    """

    uri: str
    impl: str
    tenant: str
    scope: str
    name: str

    @classmethod
    def parse(cls, uri: str) -> "CredentialRef":
        m = _REF_PATTERN.match(uri)
        if not m:
            # Codex RCO non-blocking hardening: invalid URIs may contain
            # material/PII (callers may accidentally pass a token); do
            # not echo the raw input in the error message. Report only
            # structural info: type, length, and a prefix-suffix sketch
            # bounded to non-sensitive chars.
            kind = type(uri).__name__
            raise ValueError(
                f"invalid CredentialRef URI: {kind} length={_safe_len(uri)} "
                "(content redacted); expected "
                "vault://impl/tenant/scope/name with lowercase "
                "alphanumeric + safe punctuation"
            )
        return cls(
            uri=uri,
            impl=m.group("impl"),
            tenant=m.group("tenant"),
            scope=m.group("scope"),
            name=m.group("name"),
        )


# --------------------------------------------------------------------------
# CredentialMaterial -- redacted wrapper
# --------------------------------------------------------------------------


class CredentialMaterial:
    """Opaque wrapper around credential bytes.

    Rule 1 of the sensitive-mode logging contract: never expose the
    value via repr / str / pickle. Only reveal() unwraps, and reveal
    is audited via auth.material_revealed.

    Vault implementations bind their audit emitter to the material at
    construction time. Per Codex RCO round-2 fix: reveal() fails closed
    when no emitter is bound AND none is passed by the caller, so a
    silent unwrap path cannot exist.

    Construct only via CredentialVault.get(); callers should never
    construct directly outside vault implementations and tests.
    """

    __slots__ = ("_value", "_ref", "_audit_emit")

    def __init__(self, _bytes: bytes, ref: Optional[CredentialRef] = None,
                  *, audit_emit: Optional[Callable[[dict], Any]] = None):
        if not isinstance(_bytes, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"CredentialMaterial requires bytes-like; got {type(_bytes)}"
            )
        object.__setattr__(self, "_value", bytes(_bytes))
        object.__setattr__(self, "_ref", ref)
        object.__setattr__(self, "_audit_emit", audit_emit)

    def __repr__(self) -> str:
        ref_part = f" ref={self._ref.uri}" if self._ref else ""
        return f"<CredentialMaterial redacted{ref_part}>"

    def __str__(self) -> str:
        return "<redacted>"

    def __reduce__(self):
        raise TypeError(
            "CredentialMaterial cannot be pickled; vault round-trip required"
        )

    def __reduce_ex__(self, protocol):
        raise TypeError("CredentialMaterial cannot be pickled")

    def __copy__(self):
        raise TypeError("CredentialMaterial cannot be shallow-copied")

    def __deepcopy__(self, memo):
        raise TypeError("CredentialMaterial cannot be deep-copied")

    def __eq__(self, other):
        # Equality is not exposed for security; comparing materials
        # is a leak surface.
        return NotImplemented

    def __hash__(self):
        # Hashable only by identity, not value.
        return id(self)

    def reveal(self, *, purpose: str = "",
                audit_emit: Optional[Callable[[dict], None]] = None) -> bytes:
        """Explicit unwrap. Audited via auth.material_revealed.

        Callers MUST pass a non-empty purpose so the audit trail
        records why the material was unwrapped.

        Per Codex RCO round-2 fix: emits audit ALWAYS. If neither a
        bound audit_emit (from vault construction) nor a passed
        audit_emit is available, reveal() raises RuntimeError so
        material can never unwrap silently.
        """
        if not purpose or not isinstance(purpose, str):
            raise ValueError(
                "reveal() requires a non-empty purpose string for audit"
            )
        emitter = audit_emit if audit_emit is not None else self._audit_emit
        if emitter is None:
            raise RuntimeError(
                "CredentialMaterial.reveal() refused: no audit emitter "
                "bound at construction and none passed. reveal() must "
                "always be audited; bind one or pass audit_emit=..."
            )
        emitter({
            "event_type": "auth.material_revealed",
            "ref": self._ref.uri if self._ref else None,
            "purpose": purpose,
            "ts_utc": _utc_iso(),
        })
        return self._value

    @property
    def ref(self) -> Optional[CredentialRef]:
        return self._ref


# --------------------------------------------------------------------------
# VaultMetadata + result shapes
# --------------------------------------------------------------------------


@dataclass
class VaultMetadata:
    """Non-secret context attached to a vault entry."""

    provider: str = ""
    expected_auth_mode: str = ""
    last_verified_at: Optional[str] = None
    rotation_due_at: Optional[str] = None
    allowed_scopes: list[str] = field(default_factory=list)
    sensitive_class: str = "internal"
    notes: str = ""


@dataclass
class StoreResult:
    success: bool
    audit_event_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RotateResult:
    success: bool
    prior_value_hash: Optional[str] = None
    audit_event_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RevokeResult:
    success: bool
    audit_event_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class CredentialRefSummary:
    """Vault listing entry. Never contains material."""

    ref: CredentialRef
    metadata: VaultMetadata
    last_rotated_at: Optional[str] = None
    last_used_at: Optional[str] = None
    status: str = "active"          # "active" | "revoked" | "expired"


# --------------------------------------------------------------------------
# CredentialVault Protocol
# --------------------------------------------------------------------------


class CredentialVault(Protocol):
    """The only legal source of credential material in v3.13.0.

    Implementations must never log material. Every retrieval is
    audited via the injected audit_emit callable.
    """

    def has(self, ref: CredentialRef) -> bool: ...
    def get(self, ref: CredentialRef, *, purpose: str = "") -> CredentialMaterial: ...
    def store(self, ref: CredentialRef, material: bytes,
              metadata: VaultMetadata) -> StoreResult: ...
    def rotate(self, ref: CredentialRef,
                new_material: bytes) -> RotateResult: ...
    def revoke(self, ref: CredentialRef, reason: str) -> RevokeResult: ...
    def list_refs(self) -> list[CredentialRefSummary]: ...


# --------------------------------------------------------------------------
# NoOpVault -- sentinel for tests + isolation contexts
# --------------------------------------------------------------------------


class NoOpVault:
    """Sentinel vault that does nothing. Useful in tests and isolation
    contexts where any vault call should fail loudly rather than touch
    a real backend.

    All operations raise NotImplementedError, except has() which
    returns False, and list_refs() which returns []. This way a test
    that accidentally triggers a credential retrieval surfaces the bug
    rather than silently succeeding with empty material.
    """

    def has(self, ref: CredentialRef) -> bool:
        return False

    def get(self, ref: CredentialRef, *, purpose: str = "") -> CredentialMaterial:
        raise NotImplementedError(
            f"NoOpVault refuses get({ref.uri}); test or isolation context "
            "should wire a real or fake vault explicitly"
        )

    def store(self, ref: CredentialRef, material: bytes,
              metadata: VaultMetadata) -> StoreResult:
        raise NotImplementedError("NoOpVault refuses store")

    def rotate(self, ref: CredentialRef,
                new_material: bytes) -> RotateResult:
        raise NotImplementedError("NoOpVault refuses rotate")

    def revoke(self, ref: CredentialRef, reason: str) -> RevokeResult:
        raise NotImplementedError("NoOpVault refuses revoke")

    def list_refs(self) -> list[CredentialRefSummary]:
        return []


# --------------------------------------------------------------------------
# InMemoryVault -- for tests and ephemeral runtime use only
# --------------------------------------------------------------------------


class InMemoryVault:
    """Process-lifetime in-memory vault. Tests and ephemeral runtime
    sessions only. NOT persistent and NOT thread-safe across processes.

    Useful as a fake CredentialVault when a test needs vault-like
    behavior without touching the OS keyring.
    """

    def __init__(self, *, audit_emit: Optional[Callable[[dict], str]] = None):
        self._lock = threading.RLock()
        # ref.uri -> (bytes, VaultMetadata, status, last_rotated_at, last_used_at)
        self._store: dict[str, tuple[bytes, VaultMetadata, str,
                                       Optional[str], Optional[str]]] = {}
        self._audit_emit = audit_emit or (lambda env: None)

    def has(self, ref: CredentialRef) -> bool:
        with self._lock:
            entry = self._store.get(ref.uri)
            return entry is not None and entry[2] == "active"

    def get(self, ref: CredentialRef, *, purpose: str = "") -> CredentialMaterial:
        if not purpose or not isinstance(purpose, str):
            raise ValueError(
                "InMemoryVault.get requires a non-empty purpose for audit"
            )
        with self._lock:
            entry = self._store.get(ref.uri)
            if entry is None:
                raise KeyError(f"no credential at {ref.uri}")
            value, metadata, status, last_rotated, _last_used = entry
            if status != "active":
                raise PermissionError(
                    f"credential at {ref.uri} status={status}; refusing"
                )
            now = _utc_iso()
            self._store[ref.uri] = (value, metadata, status, last_rotated, now)
            self._audit_emit({
                "event_type": "auth.credential_retrieved",
                "ref": ref.uri,
                "purpose": purpose,
                "ts_utc": now,
            })
            return CredentialMaterial(value, ref=ref,
                                          audit_emit=self._audit_emit)

    def store(self, ref: CredentialRef, material: bytes,
                metadata: VaultMetadata) -> StoreResult:
        if not isinstance(material, (bytes, bytearray)):
            return StoreResult(success=False,
                                error="material must be bytes-like")
        with self._lock:
            now = _utc_iso()
            self._store[ref.uri] = (bytes(material), metadata, "active",
                                     now, None)
            self._audit_emit({
                "event_type": "auth.credential_stored",
                "ref": ref.uri,
                "ts_utc": now,
            })
            return StoreResult(success=True)

    def rotate(self, ref: CredentialRef,
                new_material: bytes) -> RotateResult:
        with self._lock:
            entry = self._store.get(ref.uri)
            if entry is None:
                return RotateResult(success=False,
                                     error=f"no credential at {ref.uri}")
            old_value, metadata, _status, _last_rotated, last_used = entry
            import hashlib
            prior_hash = hashlib.sha256(old_value).hexdigest()
            now = _utc_iso()
            self._store[ref.uri] = (bytes(new_material), metadata, "active",
                                     now, last_used)
            self._audit_emit({
                "event_type": "auth.credential_rotated",
                "ref": ref.uri,
                "prior_value_hash": prior_hash,
                "ts_utc": now,
            })
            return RotateResult(success=True, prior_value_hash=prior_hash)

    def revoke(self, ref: CredentialRef, reason: str) -> RevokeResult:
        if not reason:
            return RevokeResult(success=False,
                                 error="revoke requires non-empty reason")
        with self._lock:
            entry = self._store.get(ref.uri)
            if entry is None:
                return RevokeResult(success=False,
                                     error=f"no credential at {ref.uri}")
            value, metadata, _status, last_rotated, last_used = entry
            self._store[ref.uri] = (value, metadata, "revoked",
                                     last_rotated, last_used)
            self._audit_emit({
                "event_type": "auth.credential_revoked",
                "ref": ref.uri,
                "reason": reason,
                "ts_utc": _utc_iso(),
            })
            return RevokeResult(success=True)

    def list_refs(self) -> list[CredentialRefSummary]:
        with self._lock:
            out = []
            for uri, (_value, metadata, status, last_rotated, last_used) \
                    in self._store.items():
                out.append(CredentialRefSummary(
                    ref=CredentialRef.parse(uri),
                    metadata=metadata,
                    last_rotated_at=last_rotated,
                    last_used_at=last_used,
                    status=status,
                ))
            return out


# --------------------------------------------------------------------------
# OSKeyringVault -- production default for single-operator deployment
# --------------------------------------------------------------------------


class OSKeyringVault:
    """Production default. Uses the OS-native credential store via
    python-keyring (Windows Credential Manager / macOS Keychain /
    Linux Secret Service).

    Lazy-imports keyring so the module is importable even when keyring
    is not installed. Calls raise RuntimeError with installation
    instructions if keyring is missing.

    Service name format: 'waggledance.v3_13_0.<impl>.<tenant>.<scope>'
    Account name: the ref's name field.
    """

    SERVICE_PREFIX = "waggledance.v3_13_0"

    def __init__(self, *, audit_emit: Optional[Callable[[dict], str]] = None):
        self._audit_emit = audit_emit or (lambda env: None)
        # last_used_at and last_rotated_at are tracked in a separate
        # process-lifetime cache because OS keyring has no metadata API.
        self._last_used: dict[str, str] = {}
        self._last_rotated: dict[str, str] = {}
        self._known_metadata: dict[str, VaultMetadata] = {}
        self._statuses: dict[str, str] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _keyring():
        try:
            import keyring as _kr
            return _kr
        except ImportError:
            raise RuntimeError(
                "OSKeyringVault requires the 'keyring' package. "
                "Install via 'pip install keyring'. "
                "For tests prefer InMemoryVault or NoOpVault."
            )

    def _service(self, ref: CredentialRef) -> str:
        return f"{self.SERVICE_PREFIX}.{ref.impl}.{ref.tenant}.{ref.scope}"

    def has(self, ref: CredentialRef) -> bool:
        kr = self._keyring()
        try:
            value = kr.get_password(self._service(ref), ref.name)
        except Exception:
            return False
        with self._lock:
            status = self._statuses.get(ref.uri, "active")
        return value is not None and status == "active"

    def get(self, ref: CredentialRef, *, purpose: str = "") -> CredentialMaterial:
        if not purpose:
            raise ValueError(
                "OSKeyringVault.get requires a non-empty purpose for audit"
            )
        kr = self._keyring()
        with self._lock:
            status = self._statuses.get(ref.uri, "active")
            if status != "active":
                raise PermissionError(
                    f"credential at {ref.uri} status={status}"
                )
        value = kr.get_password(self._service(ref), ref.name)
        if value is None:
            raise KeyError(f"no credential at {ref.uri}")
        now = _utc_iso()
        with self._lock:
            self._last_used[ref.uri] = now
        self._audit_emit({
            "event_type": "auth.credential_retrieved",
            "ref": ref.uri,
            "purpose": purpose,
            "ts_utc": now,
        })
        return CredentialMaterial(value.encode("utf-8"), ref=ref,
                                       audit_emit=self._audit_emit)

    def store(self, ref: CredentialRef, material: bytes,
                metadata: VaultMetadata) -> StoreResult:
        kr = self._keyring()
        try:
            kr.set_password(self._service(ref), ref.name,
                             material.decode("utf-8")
                             if isinstance(material, (bytes, bytearray))
                             else material)
        except Exception as exc:
            return StoreResult(success=False, error=str(exc))
        now = _utc_iso()
        with self._lock:
            self._known_metadata[ref.uri] = metadata
            self._statuses[ref.uri] = "active"
            self._last_rotated[ref.uri] = now
        self._audit_emit({
            "event_type": "auth.credential_stored",
            "ref": ref.uri,
            "ts_utc": now,
        })
        return StoreResult(success=True)

    def rotate(self, ref: CredentialRef,
                new_material: bytes) -> RotateResult:
        kr = self._keyring()
        try:
            old = kr.get_password(self._service(ref), ref.name)
            if old is None:
                return RotateResult(success=False,
                                     error=f"no credential at {ref.uri}")
            import hashlib
            prior_hash = hashlib.sha256(
                old.encode("utf-8") if isinstance(old, str) else bytes(old)
            ).hexdigest()
            new_str = (new_material.decode("utf-8")
                        if isinstance(new_material, (bytes, bytearray))
                        else new_material)
            kr.set_password(self._service(ref), ref.name, new_str)
        except Exception as exc:
            return RotateResult(success=False, error=str(exc))
        now = _utc_iso()
        with self._lock:
            self._last_rotated[ref.uri] = now
        self._audit_emit({
            "event_type": "auth.credential_rotated",
            "ref": ref.uri,
            "prior_value_hash": prior_hash,
            "ts_utc": now,
        })
        return RotateResult(success=True, prior_value_hash=prior_hash)

    def revoke(self, ref: CredentialRef, reason: str) -> RevokeResult:
        if not reason:
            return RevokeResult(success=False,
                                 error="revoke requires non-empty reason")
        kr = self._keyring()
        # OS keyring has no revoke semantics; we delete the entry and
        # track the revocation in process state. Subsequent get() will
        # raise PermissionError until store() resets the status.
        try:
            kr.delete_password(self._service(ref), ref.name)
        except Exception as exc:
            return RevokeResult(success=False, error=str(exc))
        with self._lock:
            self._statuses[ref.uri] = "revoked"
        self._audit_emit({
            "event_type": "auth.credential_revoked",
            "ref": ref.uri,
            "reason": reason,
            "ts_utc": _utc_iso(),
        })
        return RevokeResult(success=True)

    def list_refs(self) -> list[CredentialRefSummary]:
        # OS keyring has no list API; we surface only refs that have
        # been touched via this vault instance.
        with self._lock:
            out = []
            for uri, metadata in self._known_metadata.items():
                out.append(CredentialRefSummary(
                    ref=CredentialRef.parse(uri),
                    metadata=metadata,
                    last_rotated_at=self._last_rotated.get(uri),
                    last_used_at=self._last_used.get(uri),
                    status=self._statuses.get(uri, "active"),
                ))
            return out


# --------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _safe_len(value: Any) -> int:
    """Return len(value) if value supports it, else -1.

    Used in error messages so we can report a non-PII length signal
    without echoing potentially-sensitive raw content.
    """
    try:
        return len(value)
    except TypeError:
        return -1


__all__ = [
    "CredentialRef",
    "CredentialMaterial",
    "VaultMetadata",
    "StoreResult",
    "RotateResult",
    "RevokeResult",
    "CredentialRefSummary",
    "CredentialVault",
    "NoOpVault",
    "InMemoryVault",
    "OSKeyringVault",
]
