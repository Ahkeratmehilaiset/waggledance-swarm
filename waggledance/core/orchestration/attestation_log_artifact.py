# SPDX-License-Identifier: BUSL-1.1
"""Scope-bound, content-addressed envelope for attestation-log snapshots.

The pure attestation-log head does not include an owning activation scope; an
empty genesis snapshot is therefore identical for every cell.  This envelope
adds that storage binding without changing the log contract itself.  It is a
deliberately narrower per-scope partition adapter: mixed-scope snapshots that
remain valid in the global base contract are refused here.  It is an immutable
artifact, not a current-head pointer, writer authentication proof, or grant of
admission/runtime authority.
"""

from __future__ import annotations

import re
from typing import Optional

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest
from waggledance.core.orchestration.attestation_log import (
    AttestationLogContractError,
    parse_attestation_log_snapshot,
)

ATTESTATION_LOG_ARTIFACT_SCHEMA = "wd.attestation_log_artifact.v1"
ATTESTATION_LOG_ARTIFACT_DIGEST_DOMAIN = (
    "wd.attestation_log_artifact.digest.v1"
)

ATTESTATION_LOG_ARTIFACT_CORE_KEYS = frozenset(
    {
        "schema_version",
        "activation_scope_digest",
        "snapshot",
        "log_head_digest",
        "advisory_only",
        "authority_granted",
    }
)
ATTESTATION_LOG_ARTIFACT_KEYS = ATTESTATION_LOG_ARTIFACT_CORE_KEYS | {
    "artifact_digest"
}

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class AttestationLogArtifactError(ValueError):
    """A scope-bound attestation-log artifact violates its exact contract."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _refuse(reason: str, message: str) -> None:
    raise AttestationLogArtifactError(reason, message)


def _digest(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        _refuse(label, f"{label} must be a lowercase sha256 digest")
    return value


def _exact_mapping(value: object) -> dict[str, object]:
    if type(value) is not dict:
        _refuse("artifact_type", "artifact must be an exact dict")
    if dict.__len__(value) != len(ATTESTATION_LOG_ARTIFACT_KEYS):
        _refuse("artifact_keyset", "artifact has a non-exact keyset")
    snapshot = value.copy()
    if any(type(key) is not str for key in snapshot):
        _refuse("artifact_keyset", "artifact keys must be exact strings")
    if set(snapshot) != ATTESTATION_LOG_ARTIFACT_KEYS:
        _refuse("artifact_keyset", "artifact has a non-exact keyset")
    return snapshot


def _core(
    *,
    activation_scope_digest: object,
    snapshot: object,
    expected_log_head_digest: object,
    advisory_only: object,
    authority_granted: object,
) -> dict[str, object]:
    scope = _digest(activation_scope_digest, "activation_scope_digest")
    expected_head = _digest(
        expected_log_head_digest, "expected_log_head_digest"
    )
    if type(advisory_only) is not bool or advisory_only is not True:
        _refuse("advisory_only", "advisory_only must be literal true")
    if type(authority_granted) is not bool or authority_granted is not False:
        _refuse("authority_granted", "authority_granted must be literal false")
    try:
        parsed = parse_attestation_log_snapshot(snapshot)
    except AttestationLogContractError as exc:
        raise AttestationLogArtifactError(
            f"snapshot:{exc.reason}", "attestation-log snapshot refused"
        ) from exc
    if parsed.log_head_digest != expected_head:
        _refuse(
            "log_head_binding",
            "snapshot does not match the independently expected log head",
        )
    if any(
        entry.activation_scope_digest != scope for entry in parsed.entries
    ):
        _refuse(
            "activation_scope_binding",
            "every stored attestation entry must belong to the artifact scope",
        )
    return {
        "schema_version": ATTESTATION_LOG_ARTIFACT_SCHEMA,
        "activation_scope_digest": scope,
        "snapshot": parsed.to_mapping(),
        "log_head_digest": expected_head,
        "advisory_only": True,
        "authority_granted": False,
    }


def _artifact_digest(core: dict[str, object]) -> str:
    return sha256_digest(
        {
            "domain": ATTESTATION_LOG_ARTIFACT_DIGEST_DOMAIN,
            "artifact": core,
        }
    )


def build_attestation_log_artifact(
    *,
    activation_scope_digest: str,
    snapshot: object,
    expected_log_head_digest: str,
) -> dict[str, object]:
    """Build one scope-bound immutable snapshot envelope."""

    core = _core(
        activation_scope_digest=activation_scope_digest,
        snapshot=snapshot,
        expected_log_head_digest=expected_log_head_digest,
        advisory_only=True,
        authority_granted=False,
    )
    return {**core, "artifact_digest": _artifact_digest(core)}


def parse_attestation_log_artifact(
    value: object,
    *,
    expected_activation_scope_digest: str,
    expected_log_head_digest: str,
) -> dict[str, object]:
    """Parse, privately copy, and rebind one artifact to trusted expectations."""

    artifact = _exact_mapping(value)
    if (
        type(artifact["schema_version"]) is not str
        or artifact["schema_version"] != ATTESTATION_LOG_ARTIFACT_SCHEMA
    ):
        _refuse("schema_version", "attestation-log artifact schema refused")
    expected_scope = _digest(
        expected_activation_scope_digest,
        "expected_activation_scope_digest",
    )
    expected_head = _digest(
        expected_log_head_digest,
        "expected_log_head_digest",
    )
    claimed_scope = _digest(
        artifact["activation_scope_digest"], "activation_scope_digest"
    )
    claimed_head = _digest(artifact["log_head_digest"], "log_head_digest")
    if claimed_scope != expected_scope:
        _refuse("activation_scope_binding", "artifact scope is stale")
    if claimed_head != expected_head:
        _refuse("log_head_binding", "artifact log head is stale")
    core = _core(
        activation_scope_digest=claimed_scope,
        snapshot=artifact["snapshot"],
        expected_log_head_digest=claimed_head,
        advisory_only=artifact["advisory_only"],
        authority_granted=artifact["authority_granted"],
    )
    claimed = _digest(artifact["artifact_digest"], "artifact_digest")
    if claimed != _artifact_digest(core):
        _refuse("artifact_digest_mismatch", "artifact digest mismatches content")
    return {**core, "artifact_digest": claimed}


def canonicalize_attestation_log_artifact(
    value: object,
    *,
    expected_activation_scope_digest: str,
    expected_log_head_digest: str,
) -> bytes:
    """Return canonical bytes after full structural and external rebinding."""

    parsed = parse_attestation_log_artifact(
        value,
        expected_activation_scope_digest=expected_activation_scope_digest,
        expected_log_head_digest=expected_log_head_digest,
    )
    return canonical_json_bytes(parsed)


def verify_attestation_log_artifact(
    value: object,
    *,
    expected_activation_scope_digest: str,
    expected_log_head_digest: str,
) -> tuple[bool, Optional[str]]:
    """Verify without raising; success remains structural and authority-free."""

    try:
        parse_attestation_log_artifact(
            value,
            expected_activation_scope_digest=expected_activation_scope_digest,
            expected_log_head_digest=expected_log_head_digest,
        )
    except AttestationLogArtifactError as exc:
        return False, exc.reason
    return True, None


__all__ = [
    "ATTESTATION_LOG_ARTIFACT_CORE_KEYS",
    "ATTESTATION_LOG_ARTIFACT_DIGEST_DOMAIN",
    "ATTESTATION_LOG_ARTIFACT_KEYS",
    "ATTESTATION_LOG_ARTIFACT_SCHEMA",
    "AttestationLogArtifactError",
    "build_attestation_log_artifact",
    "canonicalize_attestation_log_artifact",
    "parse_attestation_log_artifact",
    "verify_attestation_log_artifact",
]
