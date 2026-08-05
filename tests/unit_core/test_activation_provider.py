# SPDX-License-Identifier: BUSL-1.1
"""Adversarial tests for the read-only activation snapshot provider."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

import pytest

import waggledance.core.capabilities.activation_provider as provider_module
from waggledance.core.capabilities.activation_contracts import (
    INITIAL_PREVIOUS_HEAD_DIGEST,
    build_activation_head,
    build_authority_ceiling,
    build_capability_variant,
    build_expression_context,
)
from waggledance.core.capabilities.activation_mirror import (
    ACTIVE_MATCH,
    build_activation_mirror_record,
)
from waggledance.core.capabilities.activation_provider import (
    ActivationProviderError,
    ControlPlaneActivationProvider,
    MAX_ACTIVATION_ARTIFACT_BYTES,
    build_control_plane_activation_provider,
)
from waggledance.core.capabilities.activation_snapshot import (
    INITIAL_PREVIOUS_BUNDLE_DIGEST,
    build_activation_snapshot_bundle,
    canonicalize_activation_snapshot_bundle,
    project_activation_snapshot_for_mirror,
)
from waggledance.core.cell_identity import build_cell_identity, verify_cell_identity


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _identity(label: str = "cell"):
    return build_cell_identity(
        pubkey_digest=_digest(f"pubkey:{label}"),
        genesis_material_digest=_digest(f"genesis:{label}"),
        created_at_utc="2026-08-05T08:00:00Z",
    )


def _bundle(*, identity, deployment: str, suffix: str = "a") -> dict:
    variant_ceiling = build_authority_ceiling(
        max_risk_class="local_artifact",
        authority_scope_digests=[_digest("scope:a"), _digest("scope:b")],
    )
    charter_ceiling = build_authority_ceiling(
        max_risk_class="internal_memory",
        authority_scope_digests=[_digest("scope:a"), _digest("scope:c")],
    )
    expressed_ceiling = build_authority_ceiling(
        max_risk_class="internal_memory",
        authority_scope_digests=[_digest("scope:a")],
    )
    variant = build_capability_variant(
        family_id="detect.fixture",
        risk_class="internal_memory",
        artifact_digest=_digest(f"artifact:{suffix}"),
        input_schema_digest=_digest("input"),
        output_schema_digest=_digest("output"),
        compatibility_digest=_digest("compatibility"),
        authority_ceiling_digest=variant_ceiling.ceiling_digest,
    )
    context = build_expression_context(
        profile_head_digest=_digest(f"profile:{suffix}"),
        policy_head_digest=_digest(f"policy:{suffix}"),
        resource_head_digest=_digest(f"resource:{suffix}"),
        domain_head_digest=_digest(f"domain:{suffix}"),
        environment_head_digest=_digest(f"environment:{suffix}"),
        charter_ceiling_digest=charter_ceiling.ceiling_digest,
        expressed_ceiling_digest=expressed_ceiling.ceiling_digest,
    )
    head = build_activation_head(
        generation=0,
        previous_head_digest=INITIAL_PREVIOUS_HEAD_DIGEST,
        expression_context_digest=context.context_digest,
        active_variant_digests=[variant.variant_digest],
        shadow_variant_digests=[],
    )
    return build_activation_snapshot_bundle(
        deployment_scope_digest=deployment,
        cell_identity=identity,
        store_revision=0,
        previous_bundle_digest=INITIAL_PREVIOUS_BUNDLE_DIGEST,
        head=head.to_mapping(),
        expected_activation_head_digest=head.head_digest,
        context=context.to_mapping(),
        variants=[variant.to_mapping()],
        variant_ceilings=[variant_ceiling.to_mapping()],
        charter_ceiling=charter_ceiling.to_mapping(),
        expressed_ceiling=expressed_ceiling.to_mapping(),
        expected_profile_head_digest=context.profile_head_digest,
        expected_policy_head_digest=context.policy_head_digest,
        expected_resource_head_digest=context.resource_head_digest,
        expected_domain_head_digest=context.domain_head_digest,
        expected_environment_head_digest=context.environment_head_digest,
    )


@dataclass(frozen=True)
class _Pointer:
    activation_scope_digest: str
    deployment_scope_digest: str
    cell_id: str
    bundle_digest: str
    store_revision: int
    previous_bundle_digest: str
    activation_head_digest: str
    previous_activation_head_digest: str
    expression_context_digest: str
    expected_profile_head_digest: str
    expected_policy_head_digest: str
    expected_resource_head_digest: str
    expected_domain_head_digest: str
    expected_environment_head_digest: str
    charter_ceiling_digest: str
    expressed_ceiling_digest: str
    scope_status: str = "active"


def _pointer(bundle: dict) -> _Pointer:
    scope = bundle["activation_scope"]
    head = bundle["head"]
    return _Pointer(
        activation_scope_digest=scope["activation_scope_digest"],
        deployment_scope_digest=scope["deployment_scope_digest"],
        cell_id=scope["cell_id"],
        bundle_digest=bundle["bundle_digest"],
        store_revision=bundle["store_revision"],
        previous_bundle_digest=bundle["previous_bundle_digest"],
        activation_head_digest=head["head_digest"],
        previous_activation_head_digest=head["previous_head_digest"],
        expression_context_digest=head["expression_context_digest"],
        expected_profile_head_digest=bundle["expected_profile_head_digest"],
        expected_policy_head_digest=bundle["expected_policy_head_digest"],
        expected_resource_head_digest=bundle["expected_resource_head_digest"],
        expected_domain_head_digest=bundle["expected_domain_head_digest"],
        expected_environment_head_digest=bundle[
            "expected_environment_head_digest"
        ],
        charter_ceiling_digest=bundle["charter_ceiling"]["ceiling_digest"],
        expressed_ceiling_digest=bundle["expressed_ceiling"][
            "ceiling_digest"
        ],
    )


class _ControlPlane:
    def __init__(self, pointer: object) -> None:
        self.pointer = pointer
        self.calls: list[tuple[str, object]] = []

    def get_current_activation_snapshot_pointer(
        self, *, deployment_scope_digest: str, cell_identity: object
    ) -> object:
        self.calls.append((deployment_scope_digest, cell_identity))
        return self.pointer


@dataclass(frozen=True)
class _Fixture:
    identity: object
    deployment: str
    bundle: dict
    canonical: bytes
    pointer: _Pointer


@pytest.fixture()
def valid() -> _Fixture:
    identity = _identity()
    deployment = _digest("deployment")
    bundle = _bundle(identity=identity, deployment=deployment)
    canonical = canonicalize_activation_snapshot_bundle(
        bundle,
        cell_identity=identity,
        expected_deployment_scope_digest=deployment,
    )
    return _Fixture(
        identity=identity,
        deployment=deployment,
        bundle=bundle,
        canonical=canonical,
        pointer=_pointer(bundle),
    )


def _provider(
    valid: _Fixture,
    *,
    pointer: object | None = None,
    artifact_reader=None,
    identity=None,
    deployment: str | None = None,
) -> ControlPlaneActivationProvider:
    return build_control_plane_activation_provider(
        control_plane=_ControlPlane(valid.pointer if pointer is None else pointer),
        artifact_reader=artifact_reader or (lambda _digest: valid.canonical),
        deployment_scope_digest=deployment or valid.deployment,
        cell_identity=valid.identity if identity is None else identity,
    )


def _reason(exc_info: pytest.ExceptionInfo[ActivationProviderError]) -> str:
    return exc_info.value.reason


def test_happy_projection_is_exact_mirror_compatible_and_authority_free(
    valid: _Fixture,
) -> None:
    control_plane = _ControlPlane(valid.pointer)
    reader_calls: list[str] = []

    def reader(bundle_digest: str) -> bytes:
        reader_calls.append(bundle_digest)
        return valid.canonical

    provider = ControlPlaneActivationProvider(
        control_plane=control_plane,
        artifact_reader=reader,
        deployment_scope_digest=valid.deployment,
        cell_identity=valid.identity,
    )
    snapshot = provider()
    expected = project_activation_snapshot_for_mirror(
        valid.bundle,
        cell_identity=valid.identity,
        expected_deployment_scope_digest=valid.deployment,
    )
    assert snapshot == expected
    assert reader_calls == [valid.pointer.bundle_digest]
    assert len(control_plane.calls) == 2
    for called_deployment, called_identity in control_plane.calls:
        assert called_deployment == valid.deployment
        assert verify_cell_identity(called_identity) == (True, None)
    for forbidden in (
        "provider_authentication_verified",
        "runtime_authority_granted",
        "routing_influence_applied",
        "execution_permission_granted",
    ):
        assert forbidden not in snapshot

    record = build_activation_mirror_record(
        capability_id="detect.fixture", snapshot=snapshot
    )
    assert record["classification"] == ACTIVE_MATCH
    assert record["runtime_authority_granted"] is False
    assert record["routing_influence_applied"] is False
    assert record["execution_permission_granted"] is False


@pytest.mark.parametrize(
    "field",
    [
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
    ],
)
def test_every_pointer_projection_field_is_rebound(
    valid: _Fixture, field: str
) -> None:
    wrong = (
        valid.pointer.store_revision + 1
        if field == "store_revision"
        else _digest(f"wrong:{field}")
    )
    forged = replace(valid.pointer, **{field: wrong})
    with pytest.raises(ActivationProviderError) as exc_info:
        _provider(valid, pointer=forged)()
    assert _reason(exc_info) == f"pointer_{field}_mismatch"


def test_missing_and_retired_pointers_fail_before_artifact_read(
    valid: _Fixture,
) -> None:
    calls = 0

    def reader(_bundle_digest: str) -> bytes:
        nonlocal calls
        calls += 1
        return valid.canonical

    missing_cp = _ControlPlane(None)
    missing = ControlPlaneActivationProvider(
        control_plane=missing_cp,
        artifact_reader=reader,
        deployment_scope_digest=valid.deployment,
        cell_identity=valid.identity,
    )
    with pytest.raises(ActivationProviderError) as exc_info:
        missing()
    assert _reason(exc_info) == "pointer_missing"

    retired = replace(valid.pointer, scope_status="retired")
    with pytest.raises(ActivationProviderError) as exc_info:
        _provider(valid, pointer=retired, artifact_reader=reader)()
    assert _reason(exc_info) == "pointer_retired"
    assert calls == 0


@pytest.mark.parametrize("artifact", [bytearray(b"{}"), memoryview(b"{}"), "{}"])
def test_missing_and_wrong_type_artifacts_fail_closed(
    valid: _Fixture, artifact: object
) -> None:
    with pytest.raises(ActivationProviderError) as exc_info:
        _provider(valid, artifact_reader=lambda _digest: artifact)()
    assert _reason(exc_info) == "artifact_wrong_type"

    with pytest.raises(ActivationProviderError) as exc_info:
        _provider(valid, artifact_reader=lambda _digest: None)()
    assert _reason(exc_info) == "artifact_missing"


def test_empty_oversized_malformed_tampered_and_noncanonical_artifacts(
    valid: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert MAX_ACTIVATION_ARTIFACT_BYTES == 64 * 1024 * 1024
    cases = [
        (b"", "artifact_empty"),
        (b"{", "artifact_malformed"),
        (b" " + valid.canonical, "artifact_noncanonical"),
    ]
    tampered = json.loads(valid.canonical)
    tampered["bundle_digest"] = _digest("forged-bundle")
    cases.append(
        (
            json.dumps(
                tampered,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            "artifact_verification_failed",
        )
    )
    for artifact, reason in cases:
        with pytest.raises(ActivationProviderError) as exc_info:
            _provider(valid, artifact_reader=lambda _digest, a=artifact: a)()
        assert _reason(exc_info) == reason

    monkeypatch.setattr(
        provider_module,
        "MAX_ACTIVATION_ARTIFACT_BYTES",
        len(valid.canonical) - 1,
    )
    with pytest.raises(ActivationProviderError) as exc_info:
        _provider(valid)()
    assert _reason(exc_info) == "artifact_oversized"


@pytest.mark.parametrize("foreign_axis", ["cell", "deployment"])
def test_cross_cell_and_deployment_artifacts_are_refused(
    valid: _Fixture, foreign_axis: str
) -> None:
    foreign_identity = (
        _identity("foreign") if foreign_axis == "cell" else valid.identity
    )
    foreign_deployment = (
        _digest("foreign-deployment")
        if foreign_axis == "deployment"
        else valid.deployment
    )
    foreign_bundle = _bundle(
        identity=foreign_identity,
        deployment=foreign_deployment,
        suffix="foreign",
    )
    foreign_bytes = canonicalize_activation_snapshot_bundle(
        foreign_bundle,
        cell_identity=foreign_identity,
        expected_deployment_scope_digest=foreign_deployment,
    )
    foreign_pointer = _pointer(foreign_bundle)
    provider = build_control_plane_activation_provider(
        control_plane=_ControlPlane(foreign_pointer),
        artifact_reader=lambda _digest: foreign_bytes,
        deployment_scope_digest=valid.deployment,
        cell_identity=valid.identity,
    )
    with pytest.raises(ActivationProviderError) as exc_info:
        provider()
    assert _reason(exc_info) == "pointer_activation_scope_digest_mismatch"


def test_reader_and_control_plane_exceptions_are_payload_free(
    valid: _Fixture,
) -> None:
    secret = "SECRET-BUNDLE-PAYLOAD-AND-DEPLOYMENT"

    def failing_reader(_digest: str):
        raise RuntimeError(secret)

    with pytest.raises(ActivationProviderError) as exc_info:
        _provider(valid, artifact_reader=failing_reader)()
    assert _reason(exc_info) == "artifact_reader_failed"
    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None

    class FailingControlPlane:
        def get_current_activation_snapshot_pointer(self, **_kwargs):
            raise RuntimeError(secret)

    provider = ControlPlaneActivationProvider(
        control_plane=FailingControlPlane(),
        artifact_reader=lambda _digest: valid.canonical,
        deployment_scope_digest=valid.deployment,
        cell_identity=valid.identity,
    )
    with pytest.raises(ActivationProviderError) as exc_info:
        provider()
    assert _reason(exc_info) == "control_plane_read_failed"
    assert secret not in str(exc_info.value)
    assert exc_info.value.__context__ is None


def test_provider_detaches_identity_and_rejects_pointer_change_during_read(
    valid: _Fixture,
) -> None:
    identity_input = valid.identity.to_mapping()

    class MutablePointer:
        pass

    pointer = MutablePointer()
    for field in _Pointer.__dataclass_fields__:
        setattr(pointer, field, getattr(valid.pointer, field))
    control_plane = _ControlPlane(pointer)

    def reader(_bundle_digest: str) -> bytes:
        # The provider must already hold a detached pointer snapshot here.
        pointer.expected_policy_head_digest = _digest("late-mutation")
        pointer.bundle_digest = _digest("late-bundle-mutation")
        return valid.canonical

    provider = ControlPlaneActivationProvider(
        control_plane=control_plane,
        artifact_reader=reader,
        deployment_scope_digest=valid.deployment,
        cell_identity=identity_input,
    )
    identity_input["cell_id"] = _digest("caller-mutated-cell")
    with pytest.raises(ActivationProviderError) as exc_info:
        provider()
    assert _reason(exc_info) == "pointer_changed_during_resolution"
    assert len(control_plane.calls) == 2
    for _called_deployment, called_identity in control_plane.calls:
        assert verify_cell_identity(called_identity) == (True, None)


def test_provider_rejects_retirement_during_artifact_resolution(
    valid: _Fixture,
) -> None:
    class RetiringControlPlane:
        def __init__(self) -> None:
            self.calls = 0

        def get_current_activation_snapshot_pointer(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return valid.pointer
            return replace(valid.pointer, scope_status="retired")

    provider = ControlPlaneActivationProvider(
        control_plane=RetiringControlPlane(),
        artifact_reader=lambda _digest: valid.canonical,
        deployment_scope_digest=valid.deployment,
        cell_identity=valid.identity,
    )
    with pytest.raises(ActivationProviderError) as exc_info:
        provider()
    assert _reason(exc_info) == "pointer_retired"


def test_invalid_constructor_inputs_are_typed_and_do_not_read(
    valid: _Fixture,
) -> None:
    invalid_identity = valid.identity.to_mapping()
    invalid_identity["cell_id"] = _digest("forged")
    with pytest.raises(ActivationProviderError) as exc_info:
        _provider(valid, identity=invalid_identity)
    assert _reason(exc_info) == "cell_identity_invalid"

    with pytest.raises(ActivationProviderError) as exc_info:
        _provider(valid, deployment="not-a-digest")
    assert _reason(exc_info) == "deployment_scope_invalid"
