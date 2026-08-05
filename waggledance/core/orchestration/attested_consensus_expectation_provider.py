# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed control-plane provider for runtime consensus expectations.

The provider reads one inline canonical expectation pin together with the
current activation pointer in a single SQLite snapshot.  It re-verifies every
stored projection and returns only the twelve fields accepted by the off-path
runtime observer.  It authenticates neither the control plane nor its writer,
grants no authority, and never applies an activation or routing change.
"""

from __future__ import annotations

import json
import re
from typing import NoReturn

from waggledance.core.capabilities.activation_snapshot import (
    build_activation_scope,
)
from waggledance.core.cell_identity import CellIdentityV1, verify_cell_identity
from waggledance.core.orchestration.attested_consensus_expectation import (
    canonicalize_attested_consensus_expectation,
    expectation_bindings_from_attested_consensus_expectation,
)

MAX_EXPECTATION_PIN_BYTES = 64 * 1024

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_READ_FAILED = object()

_RECORD_FIELDS = (
    "id",
    "activation_scope_digest",
    "deployment_scope_digest",
    "cell_id",
    "generation",
    "previous_expectation_head_digest",
    "expectation_head_digest",
    "admission_challenge_digest",
    "expected_consensus_policy_digest",
    "expected_query_digest",
    "expected_current_bundle_digest",
    "expected_current_activation_head_digest",
    "expected_current_store_revision",
    "expected_proposed_bundle_digest",
    "expected_proposed_activation_head_digest",
    "expected_proposed_store_revision",
    "expected_trust_registry_head_digest",
    "expected_attestation_log_base_head_digest",
    "expected_attestation_log_closed_head_digest",
    "canonical_expectation",
    "current_activation_bundle_digest",
    "current_activation_head_digest",
    "current_activation_store_revision",
    "scope_status",
    "created_at",
)

_PIN_PROJECTION_FIELDS = (
    "activation_scope_digest",
    "generation",
    "previous_expectation_head_digest",
    "expectation_head_digest",
    "admission_challenge_digest",
    "expected_consensus_policy_digest",
    "expected_query_digest",
    "expected_current_bundle_digest",
    "expected_current_activation_head_digest",
    "expected_current_store_revision",
    "expected_proposed_bundle_digest",
    "expected_proposed_activation_head_digest",
    "expected_proposed_store_revision",
    "expected_trust_registry_head_digest",
    "expected_attestation_log_base_head_digest",
    "expected_attestation_log_closed_head_digest",
)

_INTEGER_RECORD_FIELDS = frozenset(
    {
        "id",
        "generation",
        "expected_current_store_revision",
        "expected_proposed_store_revision",
        "current_activation_store_revision",
    }
)
_BYTES_RECORD_FIELDS = frozenset({"canonical_expectation"})


class AttestedConsensusExpectationProviderError(RuntimeError):
    """A read-only expectation provider boundary failed closed."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"attested consensus expectation refused: {reason}")
        self.reason = reason


def _refuse(reason: str) -> NoReturn:
    raise AttestedConsensusExpectationProviderError(reason)


def _private_identity_mapping(value: object) -> dict[str, str]:
    malformed = False
    snapshot: object = None
    try:
        if type(value) is CellIdentityV1:
            snapshot = {
                "schema_version": value.schema_version,
                "cell_id": value.cell_id,
                "pubkey_digest": value.pubkey_digest,
                "genesis_material_digest": value.genesis_material_digest,
                "created_at_utc": value.created_at_utc,
            }
        elif type(value) is dict:
            snapshot = value.copy()
        else:
            malformed = True
    except BaseException:
        malformed = True
    if malformed:
        _refuse("cell_identity_invalid")
    ok, _reason = verify_cell_identity(snapshot)
    if not ok or type(snapshot) is not dict:
        _refuse("cell_identity_invalid")
    return snapshot.copy()


def _record_snapshot(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    failed = False
    result: dict[str, object] = {}
    try:
        if type(value) is dict:
            supplied = value.copy()
            if any(type(key) is not str for key in supplied) or (
                set(supplied) != set(_RECORD_FIELDS)
            ):
                failed = True
            else:
                result = {
                    field: supplied[field] for field in _RECORD_FIELDS
                }
        else:
            result = {field: getattr(value, field) for field in _RECORD_FIELDS}
    except BaseException:
        failed = True
    if not failed:
        for field, field_value in result.items():
            expected_type = (
                int
                if field in _INTEGER_RECORD_FIELDS
                else bytes
                if field in _BYTES_RECORD_FIELDS
                else str
            )
            if type(field_value) is not expected_type:
                failed = True
                break
    if failed:
        _refuse("expectation_invalid")
    return result


def _exactly_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    try:
        result = left == right
    except BaseException:
        return False
    return type(result) is bool and result


class ControlPlaneAttestedConsensusExpectationProvider:
    """Scope-bound callable returning exact runtime gate expectations."""

    __slots__ = (
        "_cell_identity",
        "_control_plane",
        "_deployment_scope_digest",
        "_expected_scope",
    )

    def __init__(
        self,
        *,
        control_plane: object,
        deployment_scope_digest: str,
        cell_identity: object,
    ) -> None:
        identity = _private_identity_mapping(cell_identity)
        failed = False
        scope: dict[str, str] | None = None
        try:
            scope = build_activation_scope(
                deployment_scope_digest=deployment_scope_digest,
                cell_identity=identity,
            )
        except BaseException:
            failed = True
        if failed or scope is None:
            _refuse("deployment_scope_invalid")
        self._control_plane = control_plane
        self._deployment_scope_digest = scope["deployment_scope_digest"]
        self._cell_identity = identity
        self._expected_scope = scope

    def _read_record(self) -> dict[str, object]:
        result: object = _READ_FAILED
        try:
            reader = getattr(
                self._control_plane,
                "get_current_attested_consensus_expectation",
            )
            if callable(reader):
                result = reader(
                    deployment_scope_digest=self._deployment_scope_digest,
                    cell_identity=self._cell_identity.copy(),
                )
        except BaseException:
            result = _READ_FAILED
        if result is _READ_FAILED:
            _refuse("control_plane_read_failed")
        record = _record_snapshot(result)
        if record is None:
            _refuse("expectation_missing")
        status = record["scope_status"]
        if type(status) is not str:
            _refuse("expectation_invalid")
        if status == "retired":
            _refuse("expectation_retired")
        if status != "active":
            _refuse("expectation_invalid")
        for field in (
            "activation_scope_digest",
            "deployment_scope_digest",
            "cell_id",
        ):
            if not _exactly_equal(record[field], self._expected_scope[field]):
                _refuse(f"pin_{field}_mismatch")
        return record

    @staticmethod
    def _verify_record(
        record: dict[str, object],
    ) -> dict[str, object]:
        canonical = record["canonical_expectation"]
        if type(canonical) is not bytes:
            _refuse("pin_wrong_type")
        if not canonical:
            _refuse("pin_empty")
        if len(canonical) > MAX_EXPECTATION_PIN_BYTES:
            _refuse("pin_oversized")

        malformed = False
        decoded: object = None
        try:
            decoded = json.loads(canonical)
        except BaseException:
            malformed = True
        if malformed:
            _refuse("pin_malformed")

        failed = False
        verified: bytes | None = None
        try:
            verified = canonicalize_attested_consensus_expectation(decoded)
        except BaseException:
            failed = True
        if failed or verified is None:
            _refuse("pin_verification_failed")
        if verified != canonical:
            _refuse("pin_noncanonical")

        # Decode only verifier-produced bytes before projecting comparisons.
        pin = json.loads(verified)
        bindings = expectation_bindings_from_attested_consensus_expectation(
            pin
        )
        expected = {
            "activation_scope_digest": bindings[
                "expected_activation_scope_digest"
            ],
            "generation": pin["generation"],
            "previous_expectation_head_digest": pin[
                "previous_expectation_head_digest"
            ],
            "expectation_head_digest": pin["expectation_head_digest"],
            "admission_challenge_digest": pin[
                "admission_challenge_digest"
            ],
            **{
                field: bindings[field]
                for field in bindings
                if field != "expected_activation_scope_digest"
            },
        }
        for field in _PIN_PROJECTION_FIELDS:
            if not _exactly_equal(record[field], expected[field]):
                _refuse(f"pin_{field}_mismatch")

        actual_activation = (
            record["current_activation_bundle_digest"],
            record["current_activation_head_digest"],
            record["current_activation_store_revision"],
        )
        expected_activation = (
            bindings["expected_current_bundle_digest"],
            bindings["expected_current_activation_head_digest"],
            bindings["expected_current_store_revision"],
        )
        if any(
            not _exactly_equal(actual, expected)
            for actual, expected in zip(
                actual_activation, expected_activation
            )
        ):
            _refuse("activation_pointer_mismatch")
        return bindings

    def __call__(self) -> dict[str, object]:
        before = self._read_record()
        bindings = self._verify_record(before)
        after = self._read_record()

        expectation_fields = tuple(
            field
            for field in _RECORD_FIELDS
            if not field.startswith("current_activation_")
        )
        if any(
            not _exactly_equal(before[field], after[field])
            for field in expectation_fields
        ):
            _refuse("expectation_changed_during_resolution")
        if any(
            not _exactly_equal(before[field], after[field])
            for field in (
                "current_activation_bundle_digest",
                "current_activation_head_digest",
                "current_activation_store_revision",
            )
        ):
            _refuse("activation_pointer_changed_during_resolution")
        self._verify_record(after)
        return {key: bindings[key] for key in bindings}


__all__ = [
    "AttestedConsensusExpectationProviderError",
    "ControlPlaneAttestedConsensusExpectationProvider",
    "MAX_EXPECTATION_PIN_BYTES",
]
