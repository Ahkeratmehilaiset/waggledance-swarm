# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

from waggledance.core.hex_topology.canary_mirror import (
    build_canary_route_comparison,
)
from waggledance.core.hex_topology import subdivision_operator as so
from waggledance.core.hex_topology.subdivision_commit import (
    SUBDIVISION_RUNTIME_COMMIT_ACTION,
    build_subdivision_runtime_commit_envelope,
)
from waggledance.core.hex_topology.subdivision_preflight import (
    build_subdivision_activation_preflight,
)
from waggledance.core.hex_topology.subdivision_rehearsal import (
    build_subdivision_runtime_rehearsal,
)
from waggledance.core.hex_topology.subdivision_runtime_commit import (
    SUBDIVISION_RUNTIME_COMMIT_APPLICATION_SCHEMA,
    build_subdivision_runtime_commit_application,
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


def _ready_evidence() -> tuple[dict, dict, dict]:
    topology = _topology()
    preflight = _preflight()
    envelope = build_subdivision_runtime_commit_envelope(
        preflight=preflight,
        operator_signature=_signature(preflight),
    )
    rehearsal = build_subdivision_runtime_rehearsal(
        topology=topology,
        preflight=preflight,
    )
    return topology, envelope, rehearsal


def test_runtime_commit_application_prepares_candidate_without_live_commit():
    topology, envelope, rehearsal = _ready_evidence()
    before = json.loads(json.dumps(topology, sort_keys=True))

    application = build_subdivision_runtime_commit_application(
        topology=topology,
        commit_envelope=envelope,
        runtime_rehearsal=rehearsal,
    )

    assert application["ok"] is True
    assert application["blockers"] == []
    assert application["schema_version"] == (
        SUBDIVISION_RUNTIME_COMMIT_APPLICATION_SCHEMA
    )
    assert application["commit_candidate_prepared"] is True
    assert application["live_runtime_commit_authorized"] is False
    assert application["runtime_authority_granted"] is False
    assert application["runtime_topology_mutation_applied"] is False
    assert application["routing_influence_applied"] is False
    assert application["transport_performed"] is False
    assert application["claim_safe_upgrade"] is False
    assert application["runtime_commit_performed"] is False
    assert application["post_merge_canary_required"] is True
    assert application["auto_rollback_eligibility_required"] is True
    assert topology == before

    assert application["commit_candidate_topology"] == (
        rehearsal["candidate_topology"]
    )
    assert application["commit_candidate_topology_digest"] == (
        rehearsal["candidate_topology_digest"]
    )
    assert application["source_topology_digest_before"] == (
        application["source_topology_digest_after"]
    )
    assert set(application["guardrails"].values()) == {True}

    core = {
        key: value for key, value in application.items()
        if key not in (
            "application_digest",
            "commit_candidate_topology",
            "commit_envelope",
            "runtime_rehearsal",
        )
    }
    assert application["application_digest"] == sha256_digest(core)


def test_runtime_commit_application_rejects_unsigned_envelope():
    topology = _topology()
    preflight = _preflight()
    envelope = build_subdivision_runtime_commit_envelope(preflight=preflight)
    rehearsal = build_subdivision_runtime_rehearsal(
        topology=topology,
        preflight=preflight,
    )

    application = build_subdivision_runtime_commit_application(
        topology=topology,
        commit_envelope=envelope,
        runtime_rehearsal=rehearsal,
    )

    assert application["ok"] is False
    assert "commit_envelope_ok" in application["blockers"]
    assert "commit_envelope_ready_for_executor" in application["blockers"]
    assert application["runtime_commit_performed"] is False


def test_runtime_commit_application_rejects_envelope_digest_drift():
    topology, envelope, rehearsal = _ready_evidence()
    envelope = {**envelope, "canary_signal_kind": "drifted"}

    application = build_subdivision_runtime_commit_application(
        topology=topology,
        commit_envelope=envelope,
        runtime_rehearsal=rehearsal,
    )

    assert application["ok"] is False
    assert "commit_envelope_digest_rederives" in application["blockers"]
    assert application["runtime_commit_performed"] is False


def test_runtime_commit_application_rejects_rehearsal_digest_drift():
    topology, envelope, rehearsal = _ready_evidence()
    rehearsal = {**rehearsal, "candidate_topology_digest": "drifted"}

    application = build_subdivision_runtime_commit_application(
        topology=topology,
        commit_envelope=envelope,
        runtime_rehearsal=rehearsal,
    )

    assert application["ok"] is False
    assert "runtime_rehearsal_digest_rederives" in application["blockers"]
    assert "commit_candidate_digest_matches_rehearsal" in (
        application["blockers"]
    )
    assert application["runtime_topology_mutation_applied"] is False


def test_runtime_commit_application_rejects_source_topology_drift():
    _, envelope, rehearsal = _ready_evidence()

    application = build_subdivision_runtime_commit_application(
        topology={"cells": {"root": {"cell_id": "root"}}},
        commit_envelope=envelope,
        runtime_rehearsal=rehearsal,
    )

    assert application["ok"] is False
    assert "source_topology_matches_rehearsal_input" in (
        application["blockers"]
    )
    assert "commit_candidate_topology_buildable" in application["blockers"]
    assert application["runtime_commit_performed"] is False


def test_runtime_commit_application_rejects_rehearsal_candidate_drift():
    topology, envelope, rehearsal = _ready_evidence()
    drifted_candidate = json.loads(
        json.dumps(rehearsal["candidate_topology"], sort_keys=True)
    )
    drifted_candidate["cells"]["thermal.heating"]["live_state"] = "live"
    rehearsal = {
        **rehearsal,
        "candidate_topology": drifted_candidate,
    }

    application = build_subdivision_runtime_commit_application(
        topology=topology,
        commit_envelope=envelope,
        runtime_rehearsal=rehearsal,
    )

    assert application["ok"] is False
    assert "commit_candidate_matches_rehearsal" in application["blockers"]
    assert "commit_candidate_digest_matches_rehearsal" in (
        application["blockers"]
    )
    assert application["live_runtime_commit_authorized"] is False
