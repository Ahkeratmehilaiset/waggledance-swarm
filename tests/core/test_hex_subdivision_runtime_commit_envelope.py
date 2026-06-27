# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

import pytest

from waggledance.core.hex_topology.canary_mirror import (
    build_canary_route_comparison,
)
from waggledance.core.hex_topology import subdivision_operator as so
from waggledance.core.hex_topology.subdivision_commit import (
    SUBDIVISION_RUNTIME_COMMIT_ACTION,
    SUBDIVISION_RUNTIME_COMMIT_ENVELOPE_SCHEMA,
    build_subdivision_runtime_commit_envelope,
)
from waggledance.core.hex_topology.subdivision_preflight import (
    build_subdivision_activation_preflight,
)
from waggledance.core.magma.canonical import sha256_digest


def _topology() -> dict:
    return {
        "cells": {
            "root": {
                "cell_id": "root",
                "parent_cell_id": None,
                "child_cell_ids": ["thermal"],
                "neighbor_cell_ids": [],
            },
            "thermal": {
                "cell_id": "thermal",
                "parent_cell_id": "root",
                "child_cell_ids": [],
                "neighbor_cell_ids": [],
            },
        },
    }


def _plan() -> so.SubdivisionPlan:
    return so.plan_subdivision(
        parent_cell_id="thermal",
        new_child_cell_ids=("thermal.heating", "thermal.cooling"),
    )


def _canary_records() -> list[dict]:
    return [
        build_canary_route_comparison(
            query="heating load during frost warning",
            intent="thermal",
            production_capability_id="cap.thermal.frost",
            production_cell_id="thermal",
            quality_path="shadow_preflight",
        ),
        build_canary_route_comparison(
            query="hello from the general assistant",
            intent="chat",
            production_capability_id="cap.chat.general",
            production_cell_id="general",
            quality_path="shadow_preflight",
        ),
    ]


def _preflight() -> dict:
    return build_subdivision_activation_preflight(
        topology=_topology(),
        plan=_plan(),
        canary_comparisons=_canary_records(),
    )


def _signature(preflight: dict) -> dict:
    return {
        "action": SUBDIVISION_RUNTIME_COMMIT_ACTION,
        "plan_id": preflight["plan_id"],
        "preflight_digest": preflight["preflight_digest"],
        "signed_by": "operator-fixture",
        "signed_at_utc": "2026-06-27T00:00:00Z",
    }


def test_runtime_commit_envelope_binds_preflight_and_operator_gate():
    preflight = _preflight()
    before = json.loads(json.dumps(preflight, sort_keys=True))
    signature = _signature(preflight)

    envelope = build_subdivision_runtime_commit_envelope(
        preflight=preflight,
        operator_signature=signature,
    )

    assert envelope["ok"] is True
    assert envelope["blockers"] == []
    assert envelope["schema_version"] == (
        SUBDIVISION_RUNTIME_COMMIT_ENVELOPE_SCHEMA
    )
    assert envelope["required_operator_action"] == (
        SUBDIVISION_RUNTIME_COMMIT_ACTION
    )
    assert envelope["plan_id"] == preflight["plan_id"]
    assert envelope["subdivision_preflight_digest"] == (
        preflight["preflight_digest"]
    )
    assert envelope["operator_signature_digest"] == sha256_digest(signature)
    assert envelope["ready_for_runtime_commit_executor"] is True
    assert envelope["post_merge_canary_required"] is True
    assert envelope["canary_signal_kind"] == "p4b_confirmed_regress"
    assert envelope["canary_min_confirmations"] == 2
    assert envelope["canary_fp_threshold"] == 0.01
    assert envelope["auto_rollback_eligibility_required"] is True
    assert envelope["operator_escalate_on_uncertainty"] is True
    assert envelope["runtime_authority_granted"] is False
    assert envelope["runtime_topology_mutation_applied"] is False
    assert envelope["routing_influence_applied"] is False
    assert envelope["transport_performed"] is False
    assert envelope["claim_safe_upgrade"] is False
    assert envelope["runtime_commit_performed"] is False
    assert set(envelope["guardrails"].values()) == {True}
    assert preflight == before

    core = {
        key: value for key, value in envelope.items()
        if key not in (
            "envelope_digest",
            "operator_signature",
            "canary_policy",
            "rollback_policy",
            "subdivision_activation_preflight",
        )
    }
    assert envelope["envelope_digest"] == sha256_digest(core)


def test_runtime_commit_envelope_fails_closed_without_operator_signature():
    envelope = build_subdivision_runtime_commit_envelope(
        preflight=_preflight(),
    )

    assert envelope["ok"] is False
    assert envelope["ready_for_runtime_commit_executor"] is False
    assert "operator_signature_present" in envelope["blockers"]
    assert "operator_signature_action_matches" in envelope["blockers"]
    assert envelope["operator_signature_digest"] is None
    assert envelope["runtime_commit_performed"] is False


@pytest.mark.parametrize(
    ("field", "value", "blocker"),
    [
        ("action", "wrong_action", "operator_signature_action_matches"),
        ("plan_id", "wrong_plan", "operator_signature_plan_matches"),
        (
            "preflight_digest",
            "wrong_digest",
            "operator_signature_preflight_digest_matches",
        ),
        ("signed_by", "", "operator_signature_identity_present"),
        (
            "signed_at_utc",
            "2026-06-27T03:00:00+03:00",
            "operator_signature_timestamp_utc",
        ),
    ],
)
def test_runtime_commit_envelope_rejects_signature_drift(
    field: str,
    value: str,
    blocker: str,
):
    preflight = _preflight()
    signature = {**_signature(preflight), field: value}

    envelope = build_subdivision_runtime_commit_envelope(
        preflight=preflight,
        operator_signature=signature,
    )

    assert envelope["ok"] is False
    assert blocker in envelope["blockers"]
    assert envelope["runtime_commit_performed"] is False


def test_runtime_commit_envelope_rejects_signature_runtime_claims():
    preflight = _preflight()
    signature = {
        **_signature(preflight),
        "runtime_topology_mutation_applied": True,
    }

    envelope = build_subdivision_runtime_commit_envelope(
        preflight=preflight,
        operator_signature=signature,
    )

    assert envelope["ok"] is False
    assert (
        "operator_signature_contains_no_runtime_mutation_claim"
        in envelope["blockers"]
    )
    assert envelope["runtime_topology_mutation_applied"] is False


def test_runtime_commit_envelope_rejects_fixture_signature_flag():
    preflight = _preflight()
    signature = {**_signature(preflight), "fixture_only": True}

    envelope = build_subdivision_runtime_commit_envelope(
        preflight=preflight,
        operator_signature=signature,
    )

    assert envelope["ok"] is False
    assert "operator_signature_not_fixture" in envelope["blockers"]
    assert envelope["runtime_commit_performed"] is False


def test_runtime_commit_envelope_rejects_preflight_digest_drift():
    preflight = {**_preflight(), "canary_sample_count": 0}

    envelope = build_subdivision_runtime_commit_envelope(
        preflight=preflight,
        operator_signature=_signature(preflight),
    )

    assert envelope["ok"] is False
    assert "preflight_digest_rederives" in envelope["blockers"]
    assert envelope["runtime_commit_performed"] is False


def test_runtime_commit_envelope_rejects_weak_canary_policy():
    preflight = _preflight()

    envelope = build_subdivision_runtime_commit_envelope(
        preflight=preflight,
        operator_signature=_signature(preflight),
        canary_policy={
            "post_merge_canary_required": True,
            "signal_kind": "p4b_confirmed_regress",
            "min_confirmations": 1,
            "fp_threshold": 0.01,
        },
    )

    assert envelope["ok"] is False
    assert "post_merge_canary_policy_bound" in envelope["blockers"]


def test_runtime_commit_envelope_rejects_weak_rollback_policy():
    preflight = _preflight()

    envelope = build_subdivision_runtime_commit_envelope(
        preflight=preflight,
        operator_signature=_signature(preflight),
        rollback_policy={
            "auto_rollback_eligibility_required": True,
            "target_must_be_known_green_consensus": True,
            "result_tree_must_equal_target_tree": True,
            "failure_signal_must_be_debounced": False,
            "operator_escalate_on_uncertainty": True,
            "forbidden_surfaces_blocked": True,
        },
    )

    assert envelope["ok"] is False
    assert "auto_rollback_policy_bound" in envelope["blockers"]
