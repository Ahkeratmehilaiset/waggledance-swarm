# SPDX-License-Identifier: BUSL-1.1
"""W2A CellIdentityV1 -- content-addressed Genesis cell identity (pure contract).

A cell's identity IS the domain-separated sha256 digest of its immutable
genesis facts: the digest of its public key, the digest of its genesis
material, and its creation timestamp. Consequences, by construction:

* **Not self-writable.** Changing any field yields a different ``cell_id``;
  there is no way to keep an identity while altering what it commits to.
* **Verifiers recompute.** ``verify_cell_identity`` derives ``cell_id`` from
  the primitive fields and compares; a stored/claimed id is never trusted.
* **Zero authority.** The record carries no grants, budgets, or capability
  fields -- the exact-keyset check makes smuggling one a validation failure.
  Authority lives only in the separately signed CellCharterSliceV1.
* **No ambient inputs.** ``created_at_utc`` is an input; this module never
  reads a clock and has no randomness, so identity derivation is replayable.

Identity binds to a KEY (``pubkey_digest``), not to an agent name: a name can
be spoofed, a key digest has to be backed by the key. Signature verification
against that key is the charter layer's job, not this record's.

This is measurement/naming substrate for the hex organism: because the recipe
is deterministic, a cell can be rebuilt from backup in place or elsewhere and
provably reclaim the SAME identity (the W2C acceptance harness's restore test).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional

from waggledance.core.magma.canonical import sha256_digest

SCHEMA_VERSION = "wd.cell_identity.v1"
DIGEST_DOMAIN = "wd.cell_identity.digest.v1"

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
# ASCII digits ONLY. Python's ``\d`` matches Unicode decimals (Arabic-Indic
# ٠-٩, fullwidth ０-９), which would pass shape yet be a different lexeme --
# and Unicode digits are not a canonical content-addressed key.
_CREATED_AT_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$"
)

# The complete wire shape. Exact-keyset: extras (e.g. a smuggled authority
# flag) are rejected, not ignored.
IDENTITY_KEYS = frozenset(
    {
        "schema_version",
        "cell_id",
        "pubkey_digest",
        "genesis_material_digest",
        "created_at_utc",
    }
)


class CellIdentityError(ValueError):
    """The value is outside the CellIdentityV1 contract."""


def _freeze_mapping(value: object) -> Optional[dict]:
    """Snapshot an untrusted Mapping into a plain dict, reading each key once,
    so a flapping/live Mapping cannot present different values to successive
    reads across the shape check and the digest recompute. None if not a
    Mapping."""
    if not isinstance(value, Mapping):
        return None
    return {key: value[key] for key in list(value.keys())}


def _require_sha256(value: object, label: str) -> str:
    # EXACT str only: a str subclass (e.g. one overriding __eq__ to compare
    # equal to anything) would defeat the later == recompute, so subclasses
    # are rejected here before any comparison.
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise CellIdentityError(f"{label} must be a sha256:<64 hex> digest")
    return value


def _require_created_at(value: object) -> str:
    if type(value) is not str or not _CREATED_AT_UTC.fullmatch(value):
        raise CellIdentityError(
            "created_at_utc must be an ISO-8601 UTC instant with Z suffix"
        )
    # The regex pins the canonical SHAPE; the parse pins calendar REALITY --
    # shape-only acceptance would let 2026-99-99T99:99:99Z mint an identity.
    try:
        datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise CellIdentityError(
            "created_at_utc must be a real calendar instant"
        ) from exc
    # One canonical LEXEME per instant: a fractional part must not end in a
    # zero, else 00Z / 00.0Z / 00.00Z (one instant, three spellings) would
    # mint three different cell_ids and break content-addressed identity.
    if "." in value and value[value.index(".") + 1 : -1].endswith("0"):
        raise CellIdentityError(
            "created_at_utc fractional seconds must not have trailing zeros"
        )
    return value


def derive_cell_id(
    *,
    pubkey_digest: str,
    genesis_material_digest: str,
    created_at_utc: str,
) -> str:
    """The one true derivation. Everything else recomputes through here."""

    _require_sha256(pubkey_digest, "pubkey_digest")
    _require_sha256(genesis_material_digest, "genesis_material_digest")
    _require_created_at(created_at_utc)
    return sha256_digest(
        {
            "domain": DIGEST_DOMAIN,
            "schema_version": SCHEMA_VERSION,
            "pubkey_digest": pubkey_digest,
            "genesis_material_digest": genesis_material_digest,
            "created_at_utc": created_at_utc,
        }
    )


@dataclass(frozen=True)
class CellIdentityV1:
    """Immutable identity record. Constructing one re-derives and enforces
    ``cell_id``, so a forged instance cannot exist in a passing process."""

    cell_id: str
    pubkey_digest: str
    genesis_material_digest: str
    created_at_utc: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or (
            self.schema_version != SCHEMA_VERSION
        ):
            raise CellIdentityError("cell identity schema_version refused")
        # Validate cell_id as an EXACT str before the == recompute, so a
        # subclass with a permissive __eq__ cannot self-certify a forged id.
        _require_sha256(self.cell_id, "cell_id")
        expected = derive_cell_id(
            pubkey_digest=self.pubkey_digest,
            genesis_material_digest=self.genesis_material_digest,
            created_at_utc=self.created_at_utc,
        )
        if self.cell_id != expected:
            raise CellIdentityError(
                "cell_id does not match the derived identity digest"
            )

    def to_mapping(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "cell_id": self.cell_id,
            "pubkey_digest": self.pubkey_digest,
            "genesis_material_digest": self.genesis_material_digest,
            "created_at_utc": self.created_at_utc,
        }


def build_cell_identity(
    *,
    pubkey_digest: str,
    genesis_material_digest: str,
    created_at_utc: str,
) -> CellIdentityV1:
    return CellIdentityV1(
        cell_id=derive_cell_id(
            pubkey_digest=pubkey_digest,
            genesis_material_digest=genesis_material_digest,
            created_at_utc=created_at_utc,
        ),
        pubkey_digest=pubkey_digest,
        genesis_material_digest=genesis_material_digest,
        created_at_utc=created_at_utc,
    )


def verify_cell_identity(value: object) -> tuple[bool, Optional[str]]:
    """Fail-closed wire-shape verifier: exact keyset, per-field shapes, and an
    internal ``cell_id`` recompute. Returns ``(ok, reason)``; every failure
    names its clause. Absent key, explicit null, and wrong type are three
    distinct inputs and all three reject."""

    snapshot = _freeze_mapping(value)
    if snapshot is None:
        return False, "not_mapping"
    if set(snapshot.keys()) != IDENTITY_KEYS:
        return False, "keyset"
    if type(snapshot.get("schema_version")) is not str or (
        snapshot["schema_version"] != SCHEMA_VERSION
    ):
        return False, "schema_version"
    for key in ("cell_id", "pubkey_digest", "genesis_material_digest"):
        field = snapshot.get(key)
        if type(field) is not str or not _SHA256.fullmatch(field):
            return False, key
    try:
        _require_created_at(snapshot.get("created_at_utc"))
    except CellIdentityError:
        return False, "created_at_utc"
    expected = derive_cell_id(
        pubkey_digest=snapshot["pubkey_digest"],
        genesis_material_digest=snapshot["genesis_material_digest"],
        created_at_utc=snapshot["created_at_utc"],
    )
    if snapshot["cell_id"] != expected:
        return False, "cell_id_mismatch"
    return True, None
