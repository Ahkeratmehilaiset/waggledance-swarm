# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

from waggledance.core.hex_topology.canary_mirror import (
    build_canary_route_comparison,
)
from waggledance.core.hex_topology import subdivision_operator as so
from waggledance.core.hex_topology.subdivision_preflight import (
    build_subdivision_activation_preflight,
)
from waggledance.core.hex_topology.subdivision_rehearsal import (
    SUBDIVISION_RUNTIME_REHEARSAL_SCHEMA,
    build_subdivision_runtime_rehearsal,
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


def test_runtime_rehearsal_derives_candidate_topology_without_commit():
    topology = _topology()
    before = json.loads(json.dumps(topology, sort_keys=True))
    preflight = _preflight()

    rehearsal = build_subdivision_runtime_rehearsal(
        topology=topology,
        preflight=preflight,
    )

    assert rehearsal["ok"] is True
    assert rehearsal["blockers"] == []
    assert rehearsal["schema_version"] == SUBDIVISION_RUNTIME_REHEARSAL_SCHEMA
    assert rehearsal["required_next_gate"] == (
        "operator_signed_runtime_subdivision_commit"
    )
    assert rehearsal["ready_for_operator_commit_gate"] is True
    assert rehearsal["runtime_authority_granted"] is False
    assert rehearsal["runtime_topology_mutation_applied"] is False
    assert rehearsal["routing_influence_applied"] is False
    assert rehearsal["transport_performed"] is False
    assert rehearsal["claim_safe_upgrade"] is False
    assert rehearsal["runtime_commit_performed"] is False
    assert rehearsal["post_merge_canary_required"] is True
    assert rehearsal["auto_rollback_eligibility_required"] is True
    assert topology == before

    candidate = rehearsal["candidate_topology"]
    shadow = preflight["shadow_activation_packet"]["shadow_topology"]
    assert candidate == shadow
    assert rehearsal["candidate_topology_digest"] == sha256_digest(candidate)
    assert rehearsal["shadow_topology_digest"] == sha256_digest(shadow)
    assert rehearsal["source_topology_digest_before"] == (
        rehearsal["source_topology_digest_after"]
    )
    assert set(rehearsal["guardrails"].values()) == {True}

    core = {
        key: value for key, value in rehearsal.items()
        if key not in (
            "rehearsal_digest",
            "candidate_topology",
            "subdivision_activation_preflight",
        )
    }
    assert rehearsal["rehearsal_digest"] == sha256_digest(core)


def test_runtime_rehearsal_rejects_tampered_preflight_digest():
    preflight = {**_preflight(), "canary_sample_count": 0}

    rehearsal = build_subdivision_runtime_rehearsal(
        topology=_topology(),
        preflight=preflight,
    )

    assert rehearsal["ok"] is False
    assert "preflight_digest_rederives" in rehearsal["blockers"]
    assert rehearsal["runtime_commit_performed"] is False


def test_runtime_rehearsal_rejects_missing_source_parent():
    topology = {"cells": {"root": {"cell_id": "root"}}}

    rehearsal = build_subdivision_runtime_rehearsal(
        topology=topology,
        preflight=_preflight(),
    )

    assert rehearsal["ok"] is False
    assert "candidate_topology_buildable" in rehearsal["blockers"]
    assert "candidate_matches_shadow_packet_topology" in rehearsal["blockers"]
    assert "unknown parent_cell_id" in rehearsal[
        "candidate_topology_build_error"
    ]
    assert rehearsal["runtime_commit_performed"] is False


def test_runtime_rehearsal_rejects_authority_drift():
    preflight = {**_preflight(), "runtime_authority_granted": True}

    rehearsal = build_subdivision_runtime_rehearsal(
        topology=_topology(),
        preflight=preflight,
    )

    assert rehearsal["ok"] is False
    assert "preflight_digest_rederives" in rehearsal["blockers"]
    assert "preflight_runtime_authority_false" in rehearsal["blockers"]
    assert rehearsal["runtime_authority_granted"] is False


def test_runtime_rehearsal_rejects_plan_identity_drift():
    preflight = {**_preflight(), "plan_id": "subdiv_wrong"}

    rehearsal = build_subdivision_runtime_rehearsal(
        topology=_topology(),
        preflight=preflight,
    )

    assert rehearsal["ok"] is False
    assert "preflight_digest_rederives" in rehearsal["blockers"]
    assert "plan_rebuildable_from_preflight" in rehearsal["blockers"]
    assert "candidate_topology_buildable" in rehearsal["blockers"]
    assert rehearsal["runtime_commit_performed"] is False
