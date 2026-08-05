# SPDX-License-Identifier: BUSL-1.1
"""Adversarial tests for the authority-free activation runtime mirror."""

from __future__ import annotations

import hashlib

import pytest

from waggledance.core.capabilities.activation_contracts import (
    INITIAL_PREVIOUS_HEAD_DIGEST,
    MAX_VARIANTS_PER_SET,
    build_activation_head,
    build_authority_ceiling,
    build_capability_variant,
    build_expression_context,
)
from waggledance.core.capabilities.activation_mirror import (
    ACTIVE_MATCH,
    ACTIVE_MISS,
    ActivationMirrorError,
    MAX_MIRROR_RECORDS,
    NO_ACTIVE_FAMILY,
    SELECTION_INVALID,
    SNAPSHOT_SCHEMA,
    build_activation_mirror_record,
    summarize_activation_mirror,
    verify_activation_mirror_record,
    verify_activation_mirror_report,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _snapshot(
    *,
    family: str = "detect.fixture",
    active: bool = True,
    ambiguous_active: bool = False,
) -> dict:
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

    def variant(artifact: str):
        return build_capability_variant(
            family_id=family,
            risk_class="internal_memory",
            artifact_digest=_digest(f"artifact:{artifact}"),
            input_schema_digest=_digest("input"),
            output_schema_digest=_digest("output"),
            compatibility_digest=_digest("compatibility"),
            authority_ceiling_digest=variant_ceiling.ceiling_digest,
        )

    first = variant("one")
    variants = [first]
    selected = [first.variant_digest]
    if ambiguous_active:
        second = variant("two")
        variants.append(second)
        selected.append(second.variant_digest)
    context = build_expression_context(
        profile_head_digest=_digest("profile"),
        policy_head_digest=_digest("policy"),
        resource_head_digest=_digest("resource"),
        domain_head_digest=_digest("domain"),
        environment_head_digest=_digest("environment"),
        charter_ceiling_digest=charter_ceiling.ceiling_digest,
        expressed_ceiling_digest=expressed_ceiling.ceiling_digest,
    )
    head = build_activation_head(
        generation=0,
        previous_head_digest=INITIAL_PREVIOUS_HEAD_DIGEST,
        expression_context_digest=context.context_digest,
        active_variant_digests=selected if active else [],
        shadow_variant_digests=[] if active else selected,
    )
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "head": head.to_mapping(),
        "expected_activation_head_digest": head.head_digest,
        "context": context.to_mapping(),
        "variants": [item.to_mapping() for item in variants],
        "variant_ceilings": [variant_ceiling.to_mapping()],
        "charter_ceiling": charter_ceiling.to_mapping(),
        "expressed_ceiling": expressed_ceiling.to_mapping(),
        "expected_profile_head_digest": context.profile_head_digest,
        "expected_policy_head_digest": context.policy_head_digest,
        "expected_resource_head_digest": context.resource_head_digest,
        "expected_domain_head_digest": context.domain_head_digest,
        "expected_environment_head_digest": context.environment_head_digest,
    }


def test_valid_active_match_is_sanitized_digest_bound_and_authority_free() -> None:
    capability_id = "detect.fixture"
    snapshot = _snapshot()
    record = build_activation_mirror_record(
        capability_id=capability_id,
        snapshot=snapshot,
    )
    assert record["selection_valid"] is True
    assert record["active_family_match"] is True
    assert record["classification"] == ACTIVE_MATCH
    assert record["failure_code"] is None
    assert record["matched_variant_digest"] is not None
    assert record["runtime_authority_granted"] is False
    assert record["routing_influence_applied"] is False
    assert record["production_decision_unchanged"] is True
    assert record["execution_permission_granted"] is False
    assert capability_id not in repr(record)
    assert "query" not in record
    assert "context" not in record
    assert verify_activation_mirror_record(
        record,
        capability_id=capability_id,
        snapshot=snapshot,
    ) == (True, None)
    assert verify_activation_mirror_record(
        record,
        capability_id=capability_id,
        snapshot=_snapshot(active=False),
    ) == (
        False,
        "activation mirror record does not match source selection",
    )


def test_shadow_only_and_unselected_family_never_count_as_active() -> None:
    shadow = build_activation_mirror_record(
        capability_id="detect.fixture",
        snapshot=_snapshot(active=False),
    )
    foreign = build_activation_mirror_record(
        capability_id="solve.math",
        snapshot=_snapshot(),
    )
    for record in (shadow, foreign):
        assert record["selection_valid"] is True
        assert record["active_family_match"] is False
        assert record["classification"] == ACTIVE_MISS
        assert record["failure_code"] == NO_ACTIVE_FAMILY
        assert record["matched_variant_digest"] is None


@pytest.mark.parametrize(
    "field",
    [
        "expected_activation_head_digest",
        "expected_profile_head_digest",
        "expected_policy_head_digest",
        "expected_resource_head_digest",
        "expected_domain_head_digest",
        "expected_environment_head_digest",
    ],
)
def test_every_stale_current_head_fails_closed(field: str) -> None:
    snapshot = _snapshot()
    snapshot[field] = _digest(f"stale:{field}")
    record = build_activation_mirror_record(
        capability_id="detect.fixture", snapshot=snapshot
    )
    assert record["selection_valid"] is False
    assert record["active_family_match"] is False
    assert record["classification"] == SELECTION_INVALID
    assert record["runtime_authority_granted"] is False


def test_ambiguous_active_family_and_malformed_snapshot_fail_closed() -> None:
    ambiguous = build_activation_mirror_record(
        capability_id="detect.fixture",
        snapshot=_snapshot(ambiguous_active=True),
    )
    assert ambiguous["classification"] == SELECTION_INVALID
    assert ambiguous["selection_valid"] is False

    smuggled = {**_snapshot(), "execution_permission_granted": True}
    malformed = build_activation_mirror_record(
        capability_id="detect.fixture", snapshot=smuggled
    )
    assert malformed["classification"] == SELECTION_INVALID
    assert malformed["activation_head_digest"] is None
    assert malformed["execution_permission_granted"] is False

    tuple_wire = _snapshot()
    tuple_wire["variants"] = tuple(tuple_wire["variants"])
    refused = build_activation_mirror_record(
        capability_id="detect.fixture", snapshot=tuple_wire
    )
    assert refused["classification"] == SELECTION_INVALID


def test_oversized_snapshot_is_rejected_before_variant_parsing() -> None:
    snapshot = _snapshot()
    snapshot["variants"] = [object()] * (MAX_VARIANTS_PER_SET * 2 + 1)
    record = build_activation_mirror_record(
        capability_id="detect.fixture", snapshot=snapshot
    )
    assert record["classification"] == SELECTION_INVALID
    assert record["selection_valid"] is False


def test_report_recomputes_from_sources_and_refuses_authority_drift() -> None:
    records = [
        build_activation_mirror_record(
            capability_id="detect.fixture", snapshot=_snapshot()
        ),
        build_activation_mirror_record(
            capability_id="solve.math", snapshot=_snapshot()
        ),
        build_activation_mirror_record(
            capability_id="detect.fixture",
            snapshot={**_snapshot(), "expected_policy_head_digest": _digest("old")},
        ),
    ]
    report = summarize_activation_mirror(records)
    assert report["sample_count"] == 3
    assert report["active_match_count"] == 1
    assert report["active_miss_count"] == 1
    assert report["selection_invalid_count"] == 1
    assert verify_activation_mirror_report(report, records=records) == (True, None)
    assert verify_activation_mirror_report(report, records=[]) == (
        False,
        "activation mirror report does not match source records",
    )

    forged = dict(report)
    forged["runtime_authority_granted"] = True
    ok, reason = verify_activation_mirror_report(forged, records=records)
    assert ok is False
    assert reason == "activation mirror flag runtime_authority_granted drifted"


def test_record_and_report_buffers_are_bounded_and_tamper_evident() -> None:
    record = build_activation_mirror_record(
        capability_id="detect.fixture", snapshot=_snapshot()
    )
    forged = dict(record)
    forged["active_family_match"] = False
    assert verify_activation_mirror_record(
        forged,
        capability_id="detect.fixture",
        snapshot=_snapshot(),
    )[0] is False

    with pytest.raises(ActivationMirrorError, match="exceed bound"):
        summarize_activation_mirror([record] * (MAX_MIRROR_RECORDS + 1))
