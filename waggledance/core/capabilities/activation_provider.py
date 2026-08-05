# SPDX-License-Identifier: BUSL-1.1
"""Read-only control-plane to activation-mirror snapshot provider.

The provider joins three already separate facts without widening authority:

* the current immutable pointer selected by the control plane;
* the content-addressed activation bundle read from the artifact store; and
* the legacy, off-path activation-mirror projection.

It never treats an artifact as its own current-pointer witness.  Every field
stored in the control-plane projection is compared with a freshly verified
bundle decoded from exact immutable bytes.  Missing, retired, malformed, or
inconsistent inputs fail loudly; there is no previous-snapshot fallback.

This module authenticates neither the control-plane provider nor the artifact
reader and grants no runtime authority.  Wiring code must establish those
trust boundaries separately.
"""

from __future__ import annotations

import json
import re
from typing import Callable, NoReturn

from waggledance.core.capabilities.activation_snapshot import (
    build_activation_scope,
    canonicalize_activation_snapshot_bundle,
    project_activation_snapshot_for_mirror,
)
from waggledance.core.cell_identity import CellIdentityV1, verify_cell_identity


MAX_ACTIVATION_ARTIFACT_BYTES = 64 * 1024 * 1024

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_READ_FAILED = object()

_POINTER_FIELDS = (
    "activation_scope_digest",
    "deployment_scope_digest",
    "cell_id",
    "bundle_digest",
    "store_revision",
    "previous_bundle_digest",
    "activation_head_digest",
    "previous_activation_head_digest",
    "expression_context_digest",
    "expected_profile_head_digest",
    "expected_policy_head_digest",
    "expected_resource_head_digest",
    "expected_domain_head_digest",
    "expected_environment_head_digest",
    "charter_ceiling_digest",
    "expressed_ceiling_digest",
    "scope_status",
)


class ActivationProviderError(RuntimeError):
    """A read-only activation provider boundary failed closed.

    ``reason`` is a stable, payload-free machine classification.  Exception
    text deliberately contains only that classification, never an artifact,
    deployment value, cell identity, or upstream exception message.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"activation snapshot provider refused: {reason}")
        self.reason = reason


def _refuse(reason: str) -> NoReturn:
    raise ActivationProviderError(reason)


def _private_identity_mapping(value: object) -> dict[str, str]:
    """Copy and verify identity input once, then retain only the private copy."""

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
        # Do not retain an exception raised while reading caller-owned input.
        malformed = True
    if malformed:
        _refuse("cell_identity_invalid")
    ok, _reason = verify_cell_identity(snapshot)
    if not ok:
        _refuse("cell_identity_invalid")
    # Successful verification proves the exact string-only identity keyset.
    if type(snapshot) is not dict:  # defensive type narrowing
        _refuse("cell_identity_invalid")
    return snapshot.copy()


def _pointer_snapshot(value: object) -> dict[str, object] | None:
    """Detach the complete pointer projection before reading the artifact."""

    if value is None:
        return None
    failed = False
    result: dict[str, object] = {}
    try:
        if type(value) is dict:
            supplied = value.copy()
            result = {field: supplied[field] for field in _POINTER_FIELDS}
        else:
            result = {field: getattr(value, field) for field in _POINTER_FIELDS}
    except BaseException:
        failed = True
    if failed:
        _refuse("pointer_invalid")
    return result


def _exactly_equal(left: object, right: object) -> bool:
    """Prevent permissive subclasses (and bool-as-int) from passing equality."""

    return type(left) is type(right) and left == right


class ControlPlaneActivationProvider:
    """Callable read-only provider consumed by the legacy activation mirror."""

    __slots__ = (
        "_artifact_reader",
        "_cell_identity",
        "_control_plane",
        "_deployment_scope_digest",
        "_expected_scope",
    )

    def __init__(
        self,
        *,
        control_plane: object,
        artifact_reader: Callable[[str], bytes | None],
        deployment_scope_digest: str,
        cell_identity: object,
    ) -> None:
        if not callable(artifact_reader):
            _refuse("artifact_reader_invalid")
        identity = _private_identity_mapping(cell_identity)
        scope_failed = False
        scope: dict[str, str] | None = None
        try:
            scope = build_activation_scope(
                deployment_scope_digest=deployment_scope_digest,
                cell_identity=identity,
            )
        except BaseException:
            # Sanitize contract and hostile object/protocol failures alike;
            # intentionally preserve no upstream exception payload.
            scope_failed = True
        if scope_failed or scope is None:
            _refuse("deployment_scope_invalid")

        self._control_plane = control_plane
        self._artifact_reader = artifact_reader
        self._deployment_scope_digest = scope["deployment_scope_digest"]
        self._cell_identity = identity
        self._expected_scope = scope

    def _read_pointer(self) -> dict[str, object]:
        result: object = _READ_FAILED
        try:
            reader = getattr(
                self._control_plane,
                "get_current_activation_snapshot_pointer",
            )
            if callable(reader):
                result = reader(
                    deployment_scope_digest=self._deployment_scope_digest,
                    # A fresh throwaway copy prevents the control-plane-like
                    # adapter from mutating the provider's verified identity.
                    cell_identity=self._cell_identity.copy(),
                )
        except BaseException:
            result = _READ_FAILED
        if result is _READ_FAILED:
            _refuse("control_plane_read_failed")

        pointer = _pointer_snapshot(result)
        if pointer is None:
            _refuse("pointer_missing")
        status = pointer["scope_status"]
        if type(status) is not str:
            _refuse("pointer_status_invalid")
        if status == "retired":
            _refuse("pointer_retired")
        if status != "active":
            _refuse("pointer_status_invalid")
        for field in (
            "activation_scope_digest",
            "deployment_scope_digest",
            "cell_id",
        ):
            if not _exactly_equal(pointer[field], self._expected_scope[field]):
                _refuse(f"pointer_{field}_mismatch")
        digest = pointer["bundle_digest"]
        if type(digest) is not str or not _SHA256.fullmatch(digest):
            _refuse("pointer_bundle_digest_invalid")
        return pointer

    def _read_artifact(self, bundle_digest: str) -> bytes:
        result: object = _READ_FAILED
        try:
            result = self._artifact_reader(bundle_digest)
        except BaseException:
            result = _READ_FAILED
        # Raise only after leaving the except suite so the provider exception
        # carries neither the upstream exception as context nor its payload.
        if result is _READ_FAILED:
            _refuse("artifact_reader_failed")
        if result is None:
            _refuse("artifact_missing")
        if type(result) is not bytes:
            _refuse("artifact_wrong_type")
        if not result:
            _refuse("artifact_empty")
        if len(result) > MAX_ACTIVATION_ARTIFACT_BYTES:
            _refuse("artifact_oversized")
        return result

    def __call__(self) -> dict[str, object]:
        pointer = self._read_pointer()
        bundle_digest = pointer["bundle_digest"]
        if type(bundle_digest) is not str:  # defensive type narrowing
            _refuse("pointer_bundle_digest_invalid")
        artifact = self._read_artifact(bundle_digest)

        malformed = False
        decoded: object = None
        try:
            decoded = json.loads(artifact)
        except BaseException:
            malformed = True
        if malformed:
            _refuse("artifact_malformed")

        verification_failed = False
        canonical: bytes | None = None
        try:
            canonical = canonicalize_activation_snapshot_bundle(
                decoded,
                cell_identity=self._cell_identity,
                expected_deployment_scope_digest=(
                    self._deployment_scope_digest
                ),
            )
        except BaseException:
            verification_failed = True
        if verification_failed or canonical is None:
            _refuse("artifact_verification_failed")
        if canonical != artifact:
            _refuse("artifact_noncanonical")

        # Decode only the verifier-produced immutable bytes for all subsequent
        # comparisons and projection.  No caller-owned aggregate is re-read.
        verified_bundle = json.loads(canonical)
        scope = verified_bundle["activation_scope"]
        head = verified_bundle["head"]
        expected_pointer = {
            "activation_scope_digest": scope["activation_scope_digest"],
            "deployment_scope_digest": scope["deployment_scope_digest"],
            "cell_id": scope["cell_id"],
            "bundle_digest": verified_bundle["bundle_digest"],
            "store_revision": verified_bundle["store_revision"],
            "previous_bundle_digest": verified_bundle[
                "previous_bundle_digest"
            ],
            "activation_head_digest": head["head_digest"],
            "previous_activation_head_digest": head[
                "previous_head_digest"
            ],
            "expression_context_digest": head[
                "expression_context_digest"
            ],
            "expected_profile_head_digest": verified_bundle[
                "expected_profile_head_digest"
            ],
            "expected_policy_head_digest": verified_bundle[
                "expected_policy_head_digest"
            ],
            "expected_resource_head_digest": verified_bundle[
                "expected_resource_head_digest"
            ],
            "expected_domain_head_digest": verified_bundle[
                "expected_domain_head_digest"
            ],
            "expected_environment_head_digest": verified_bundle[
                "expected_environment_head_digest"
            ],
            "charter_ceiling_digest": verified_bundle["charter_ceiling"][
                "ceiling_digest"
            ],
            "expressed_ceiling_digest": verified_bundle[
                "expressed_ceiling"
            ]["ceiling_digest"],
        }
        for field, expected in expected_pointer.items():
            if not _exactly_equal(pointer[field], expected):
                _refuse(f"pointer_{field}_mismatch")

        # Artifact I/O happens outside the control-plane read transaction.
        # Establish a second linearization point and require the complete
        # immutable pointer to be unchanged.  Otherwise a concurrent advance
        # or retirement could be mislabeled as the current mirror snapshot.
        current_pointer = self._read_pointer()
        for field in _POINTER_FIELDS:
            if not _exactly_equal(current_pointer[field], pointer[field]):
                _refuse("pointer_changed_during_resolution")

        projection_failed = False
        projection: dict[str, object] | None = None
        try:
            projection = project_activation_snapshot_for_mirror(
                verified_bundle,
                cell_identity=self._cell_identity,
                expected_deployment_scope_digest=(
                    self._deployment_scope_digest
                ),
            )
        except BaseException:
            projection_failed = True
        if projection_failed or projection is None:
            _refuse("mirror_projection_failed")
        return projection


def build_control_plane_activation_provider(
    *,
    control_plane: object,
    artifact_reader: Callable[[str], bytes | None],
    deployment_scope_digest: str,
    cell_identity: object,
) -> ControlPlaneActivationProvider:
    """Build a callable provider without performing any control-plane read."""

    return ControlPlaneActivationProvider(
        control_plane=control_plane,
        artifact_reader=artifact_reader,
        deployment_scope_digest=deployment_scope_digest,
        cell_identity=cell_identity,
    )


__all__ = [
    "ActivationProviderError",
    "ControlPlaneActivationProvider",
    "MAX_ACTIVATION_ARTIFACT_BYTES",
    "build_control_plane_activation_provider",
]
