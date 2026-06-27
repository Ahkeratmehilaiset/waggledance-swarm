# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

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
    build_subdivision_runtime_commit_application,
)
from waggledance.core.hex_topology.subdivision_runtime_execution_request import (
    SUBDIVISION_RUNTIME_EXECUTION_REQUEST_ACTION,
    SUBDIVISION_RUNTIME_EXECUTION_REQUEST_NEXT_GATE,
    SUBDIVISION_RUNTIME_EXECUTION_REQUEST_SCHEMA,
    build_subdivision_runtime_execution_request,
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


def _ready_application() -> dict:
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
    return build_subdivision_runtime_commit_application(
        topology=topology,
        commit_envelope=envelope,
        runtime_rehearsal=rehearsal,
    )


def _request_metadata(application: dict) -> dict:
    return {
        "requested_action": SUBDIVISION_RUNTIME_EXECUTION_REQUEST_ACTION,
        "application_digest": application["application_digest"],
        "plan_id": application["plan_id"],
        "requested_by": "codex-lead-1",
        "requested_at_utc": "2026-06-27T00:00:00Z",
        "operator_approval": False,
        "live_runtime_execution_authorized": False,
        "runtime_executor_invoked": False,
    }


def test_execution_request_handoffs_application_without_live_runtime():
    application = _ready_application()

    request = build_subdivision_runtime_execution_request(
        runtime_application=application,
        request_metadata=_request_metadata(application),
    )

    assert request["ok"] is True
    assert request["blockers"] == []
    assert request["schema_version"] == (
        SUBDIVISION_RUNTIME_EXECUTION_REQUEST_SCHEMA
    )
    assert request["requested_action"] == (
        SUBDIVISION_RUNTIME_EXECUTION_REQUEST_ACTION
    )
    assert request["required_next_gate"] == (
        SUBDIVISION_RUNTIME_EXECUTION_REQUEST_NEXT_GATE
    )
    assert request["ready_for_runtime_executor_handoff"] is True
    assert request["live_runtime_execution_authorized"] is False
    assert request["live_runtime_commit_authorized"] is False
    assert request["runtime_authority_granted"] is False
    assert request["runtime_topology_mutation_applied"] is False
    assert request["routing_influence_applied"] is False
    assert request["transport_performed"] is False
    assert request["claim_safe_upgrade"] is False
    assert request["runtime_commit_performed"] is False
    assert request["runtime_executor_invoked"] is False
    assert request["post_merge_canary_required"] is True
    assert request["auto_rollback_eligibility_required"] is True
    assert request["runtime_application_digest"] == (
        application["application_digest"]
    )
    assert request["commit_candidate_topology_digest"] == (
        application["commit_candidate_topology_digest"]
    )
    assert set(request["guardrails"].values()) == {True}

    core = {
        key: value for key, value in request.items()
        if key not in (
            "execution_request_digest",
            "runtime_application",
            "request_metadata",
        )
    }
    assert request["execution_request_digest"] == sha256_digest(core)


def test_execution_request_rejects_application_digest_drift():
    application = _ready_application()
    drifted = {**application, "application_status": "drifted"}

    request = build_subdivision_runtime_execution_request(
        runtime_application=drifted,
        request_metadata=_request_metadata(application),
    )

    assert request["ok"] is False
    assert "runtime_application_status_ready" in request["blockers"]
    assert "runtime_application_digest_rederives" in request["blockers"]
    assert request["runtime_commit_performed"] is False


def test_execution_request_rejects_application_not_ok():
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

    request = build_subdivision_runtime_execution_request(
        runtime_application=application,
        request_metadata=_request_metadata(application),
    )

    assert request["ok"] is False
    assert "runtime_application_ok" in request["blockers"]
    assert "runtime_application_has_no_blockers" in request["blockers"]
    assert request["live_runtime_execution_authorized"] is False


def test_execution_request_rejects_mismatched_request_metadata():
    application = _ready_application()
    metadata = {
        **_request_metadata(application),
        "application_digest": "bad",
        "plan_id": "wrong",
    }

    request = build_subdivision_runtime_execution_request(
        runtime_application=application,
        request_metadata=metadata,
    )

    assert request["ok"] is False
    assert "request_application_digest_matches" in request["blockers"]
    assert "request_plan_id_matches" in request["blockers"]
    assert request["runtime_executor_invoked"] is False


def test_execution_request_rejects_request_runtime_claims():
    application = _ready_application()
    metadata = {
        **_request_metadata(application),
        "operator_approval": True,
        "live_runtime_execution_authorized": True,
        "runtime_executor_invoked": True,
    }

    request = build_subdivision_runtime_execution_request(
        runtime_application=application,
        request_metadata=metadata,
    )

    assert request["ok"] is False
    assert "request_contains_no_operator_approval" in request["blockers"]
    assert "request_contains_no_runtime_claim" in request["blockers"]
    assert request["live_runtime_execution_authorized"] is False
    assert request["runtime_executor_invoked"] is False


def test_execution_request_rejects_bad_request_timestamp():
    application = _ready_application()
    metadata = {
        **_request_metadata(application),
        "requested_at_utc": "2026-06-27T00:00:00",
    }

    request = build_subdivision_runtime_execution_request(
        runtime_application=application,
        request_metadata=metadata,
    )

    assert request["ok"] is False
    assert "request_timestamp_utc" in request["blockers"]
    assert request["transport_performed"] is False
