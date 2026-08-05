"""Tests for the scope-bound attestation-log artifact envelope."""

from __future__ import annotations

import hashlib
import json

import pytest

from waggledance.core.orchestration.attestation_log import (
    INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
    build_attestation_log_entry,
    build_attestation_log_snapshot,
    canonicalize_attestation_log_snapshot,
)
from waggledance.core.orchestration.attestation_log_artifact import (
    ATTESTATION_LOG_ARTIFACT_KEYS,
    AttestationLogArtifactError,
    build_attestation_log_artifact,
    canonicalize_attestation_log_artifact,
    parse_attestation_log_artifact,
    verify_attestation_log_artifact,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _snapshot(scope: str, *, with_entry: bool = True):
    entries = []
    if with_entry:
        entries.append(
            build_attestation_log_entry(
                activation_scope_digest=scope,
                admission_challenge_digest=_digest("challenge"),
                evidence_digest=_digest("evidence"),
                ballot_digest=_digest("ballot"),
                attestation_digest=_digest("attestation"),
                reviewer_lineage_digest=_digest("lineage"),
            )
        )
    return build_attestation_log_snapshot(
        generation=0,
        previous_log_head_digest=INITIAL_PREVIOUS_LOG_HEAD_DIGEST,
        entries=entries,
    )


def test_build_parse_verify_and_canonicalize_round_trip() -> None:
    scope = _digest("scope")
    snapshot = _snapshot(scope)
    artifact = build_attestation_log_artifact(
        activation_scope_digest=scope,
        snapshot=snapshot,
        expected_log_head_digest=snapshot.log_head_digest,
    )
    parsed = parse_attestation_log_artifact(
        artifact,
        expected_activation_scope_digest=scope,
        expected_log_head_digest=snapshot.log_head_digest,
    )
    canonical = canonicalize_attestation_log_artifact(
        artifact,
        expected_activation_scope_digest=scope,
        expected_log_head_digest=snapshot.log_head_digest,
    )

    assert set(artifact) == ATTESTATION_LOG_ARTIFACT_KEYS
    assert parsed == artifact
    assert json.loads(canonical) == artifact
    assert canonical == canonicalize_attestation_log_artifact(
        json.loads(canonical),
        expected_activation_scope_digest=scope,
        expected_log_head_digest=snapshot.log_head_digest,
    )
    assert canonicalize_attestation_log_snapshot(snapshot) == (
        canonicalize_attestation_log_snapshot(snapshot.to_mapping())
    )
    assert verify_attestation_log_artifact(
        artifact,
        expected_activation_scope_digest=scope,
        expected_log_head_digest=snapshot.log_head_digest,
    ) == (True, None)
    assert artifact["advisory_only"] is True
    assert artifact["authority_granted"] is False


def test_empty_genesis_artifact_is_bound_to_its_owning_scope() -> None:
    scope_a = _digest("scope:a")
    scope_b = _digest("scope:b")
    empty = _snapshot(scope_a, with_entry=False)
    artifact_a = build_attestation_log_artifact(
        activation_scope_digest=scope_a,
        snapshot=empty,
        expected_log_head_digest=empty.log_head_digest,
    )
    artifact_b = build_attestation_log_artifact(
        activation_scope_digest=scope_b,
        snapshot=empty,
        expected_log_head_digest=empty.log_head_digest,
    )

    assert artifact_a["snapshot"] == artifact_b["snapshot"]
    assert artifact_a["artifact_digest"] != artifact_b["artifact_digest"]
    ok, reason = verify_attestation_log_artifact(
        artifact_a,
        expected_activation_scope_digest=scope_b,
        expected_log_head_digest=empty.log_head_digest,
    )
    assert ok is False
    assert reason == "activation_scope_binding"


def test_entries_from_another_scope_are_refused_even_when_head_is_valid() -> None:
    owning_scope = _digest("owning-scope")
    foreign_snapshot = _snapshot(_digest("foreign-scope"))
    with pytest.raises(AttestationLogArtifactError) as exc_info:
        build_attestation_log_artifact(
            activation_scope_digest=owning_scope,
            snapshot=foreign_snapshot,
            expected_log_head_digest=foreign_snapshot.log_head_digest,
        )
    assert exc_info.value.reason == "activation_scope_binding"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda value: value.update(extra=True), "artifact_keyset"),
        (
            lambda value: value.__setitem__("advisory_only", False),
            "advisory_only",
        ),
        (
            lambda value: value.__setitem__("authority_granted", True),
            "authority_granted",
        ),
        (
            lambda value: value.__setitem__(
                "artifact_digest", _digest("tampered")
            ),
            "artifact_digest_mismatch",
        ),
    ],
)
def test_tampering_and_non_exact_keysets_fail_closed(mutation, reason) -> None:
    scope = _digest("scope")
    snapshot = _snapshot(scope)
    artifact = build_attestation_log_artifact(
        activation_scope_digest=scope,
        snapshot=snapshot,
        expected_log_head_digest=snapshot.log_head_digest,
    )
    mutation(artifact)
    ok, actual = verify_attestation_log_artifact(
        artifact,
        expected_activation_scope_digest=scope,
        expected_log_head_digest=snapshot.log_head_digest,
    )
    assert ok is False
    assert actual == reason


def test_stale_external_head_and_malformed_snapshot_are_payload_free() -> None:
    scope = _digest("scope")
    snapshot = _snapshot(scope)
    artifact = build_attestation_log_artifact(
        activation_scope_digest=scope,
        snapshot=snapshot,
        expected_log_head_digest=snapshot.log_head_digest,
    )
    ok, reason = verify_attestation_log_artifact(
        artifact,
        expected_activation_scope_digest=scope,
        expected_log_head_digest=_digest("stale-head"),
    )
    assert ok is False
    assert reason == "log_head_binding"

    malformed = json.loads(
        canonicalize_attestation_log_artifact(
            artifact,
            expected_activation_scope_digest=scope,
            expected_log_head_digest=snapshot.log_head_digest,
        )
    )
    malformed["snapshot"]["generation"] = True
    ok, reason = verify_attestation_log_artifact(
        malformed,
        expected_activation_scope_digest=scope,
        expected_log_head_digest=snapshot.log_head_digest,
    )
    assert ok is False
    assert reason == "snapshot:generation"


def test_builder_copies_caller_owned_snapshot_before_returning() -> None:
    scope = _digest("scope")
    snapshot = _snapshot(scope).to_mapping()
    artifact = build_attestation_log_artifact(
        activation_scope_digest=scope,
        snapshot=snapshot,
        expected_log_head_digest=snapshot["log_head_digest"],
    )
    snapshot["entries"][0]["evidence_digest"] = _digest("mutated")

    parsed = parse_attestation_log_artifact(
        artifact,
        expected_activation_scope_digest=scope,
        expected_log_head_digest=artifact["log_head_digest"],
    )
    assert parsed == artifact
    assert parsed["snapshot"]["entries"][0]["evidence_digest"] == _digest(
        "evidence"
    )


def test_mapping_subclass_and_non_digest_expectations_are_refused() -> None:
    class _Mapping(dict):
        pass

    scope = _digest("scope")
    snapshot = _snapshot(scope)
    artifact = build_attestation_log_artifact(
        activation_scope_digest=scope,
        snapshot=snapshot,
        expected_log_head_digest=snapshot.log_head_digest,
    )
    with pytest.raises(AttestationLogArtifactError) as subclass_error:
        parse_attestation_log_artifact(
            _Mapping(artifact),
            expected_activation_scope_digest=scope,
            expected_log_head_digest=snapshot.log_head_digest,
        )
    assert subclass_error.value.reason == "artifact_type"

    ok, reason = verify_attestation_log_artifact(
        artifact,
        expected_activation_scope_digest="../scope",
        expected_log_head_digest=snapshot.log_head_digest,
    )
    assert ok is False
    assert reason == "expected_activation_scope_digest"


def test_hostile_digest_values_are_type_checked_before_comparison() -> None:
    class _HostileEquality:
        def __eq__(self, _other):
            raise AssertionError("hostile equality must not execute")

    scope = _digest("scope")
    snapshot = _snapshot(scope)
    artifact = build_attestation_log_artifact(
        activation_scope_digest=scope,
        snapshot=snapshot,
        expected_log_head_digest=snapshot.log_head_digest,
    )
    artifact["activation_scope_digest"] = _HostileEquality()
    ok, reason = verify_attestation_log_artifact(
        artifact,
        expected_activation_scope_digest=scope,
        expected_log_head_digest=snapshot.log_head_digest,
    )
    assert ok is False
    assert reason == "activation_scope_digest"
