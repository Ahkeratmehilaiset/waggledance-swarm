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
    build_subdivision_runtime_execution_request,
)
from waggledance.core.hex_topology.subdivision_runtime_executor_admission import (
    SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER,
    SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_SCHEMA,
    build_subdivision_runtime_executor_admission,
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


def _execution_request() -> dict:
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
    application = build_subdivision_runtime_commit_application(
        topology=topology,
        commit_envelope=envelope,
        runtime_rehearsal=rehearsal,
    )
    return build_subdivision_runtime_execution_request(
        runtime_application=application,
        request_metadata={
            "requested_action": SUBDIVISION_RUNTIME_EXECUTION_REQUEST_ACTION,
            "application_digest": application["application_digest"],
            "plan_id": application["plan_id"],
            "requested_by": "codex-lead-1",
            "requested_at_utc": "2026-06-27T00:00:00Z",
            "operator_approval": False,
            "live_runtime_execution_authorized": False,
            "runtime_executor_invoked": False,
        },
    )


def test_executor_admission_blocks_live_runtime_until_operator_cutover():
    request = _execution_request()

    admission = build_subdivision_runtime_executor_admission(
        execution_request=request,
    )

    assert admission["ok"] is True
    assert admission["blockers"] == []
    assert admission["schema_version"] == (
        SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_SCHEMA
    )
    assert admission["required_next_gate"] == (
        SUBDIVISION_RUNTIME_EXECUTION_REQUEST_NEXT_GATE
    )
    assert admission["ready_for_runtime_executor_admission"] is False
    assert admission["admission_blockers"] == [
        SUBDIVISION_RUNTIME_EXECUTOR_ADMISSION_BLOCKER
    ]
    assert admission["live_runtime_execution_authorized"] is False
    assert admission["runtime_executor_invoked"] is False
    assert admission["live_runtime_commit_authorized"] is False
    assert admission["runtime_authority_granted"] is False
    assert admission["runtime_topology_mutation_applied"] is False
    assert admission["routing_influence_applied"] is False
    assert admission["transport_performed"] is False
    assert admission["claim_safe_upgrade"] is False
    assert admission["runtime_commit_performed"] is False
    assert admission["runtime_execution_request_digest"] == (
        request["execution_request_digest"]
    )
    assert set(admission["guardrails"].values()) == {True}

    core = {
        key: value for key, value in admission.items()
        if key not in (
            "executor_admission_digest",
            "execution_request",
            "cutover_authorization",
        )
    }
    assert admission["executor_admission_digest"] == sha256_digest(core)


def test_executor_admission_rejects_request_digest_drift():
    request = {**_execution_request(), "requested_action": "drifted"}

    admission = build_subdivision_runtime_executor_admission(
        execution_request=request,
    )

    assert admission["ok"] is False
    assert "execution_request_digest_rederives" in admission["blockers"]
    assert "execution_request_action_is_review_only" in admission["blockers"]
    assert admission["runtime_executor_invoked"] is False


def test_executor_admission_rejects_request_not_ok():
    request = {**_execution_request(), "ok": False, "blockers": ["blocked"]}

    admission = build_subdivision_runtime_executor_admission(
        execution_request=request,
    )

    assert admission["ok"] is False
    assert "execution_request_ok" in admission["blockers"]
    assert "execution_request_has_no_blockers" in admission["blockers"]
    assert admission["ready_for_runtime_executor_admission"] is False


def test_executor_admission_rejects_runtime_claim_drift():
    request = {
        **_execution_request(),
        "live_runtime_execution_authorized": True,
        "runtime_executor_invoked": True,
    }

    admission = build_subdivision_runtime_executor_admission(
        execution_request=request,
    )

    assert admission["ok"] is False
    assert "execution_request_digest_rederives" in admission["blockers"]
    assert "execution_request_runtime_flags_false" in admission["blockers"]
    assert admission["live_runtime_execution_authorized"] is False
    assert admission["runtime_executor_invoked"] is False


def test_executor_admission_rejects_metadata_runtime_claim():
    request = _execution_request()
    metadata = {
        **request["request_metadata"],
        "operator_approval": True,
        "runtime_executor_invoked": True,
    }
    request = {**request, "request_metadata": metadata}

    admission = build_subdivision_runtime_executor_admission(
        execution_request=request,
    )

    assert admission["ok"] is False
    assert "request_metadata_digest_rederives" in admission["blockers"]
    assert "request_metadata_no_operator_approval" in admission["blockers"]
    assert "request_metadata_no_runtime_claim" in admission["blockers"]
    assert admission["transport_performed"] is False


def test_executor_admission_does_not_accept_cutover_authorization():
    request = _execution_request()

    admission = build_subdivision_runtime_executor_admission(
        execution_request=request,
        cutover_authorization={
            "operator_verified_runtime_subdivision_executor_cutover": True,
            "plan_id": request["plan_id"],
        },
    )

    assert admission["ok"] is False
    assert "cutover_authorization_absent" in admission["blockers"]
    assert admission["cutover_authorization"] is not None
    assert admission["ready_for_runtime_executor_admission"] is False
    assert admission["runtime_commit_performed"] is False
