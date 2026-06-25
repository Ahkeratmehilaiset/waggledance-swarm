# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

import pytest

from waggledance.core.hex_topology.canary_mirror import (
    CanaryMirrorError,
    build_canary_route_comparison,
)
from waggledance.core.hex_topology import ring_messaging as rm
from waggledance.core.hex_topology import subdivision_operator as so
from waggledance.core.hex_topology.subdivision_preflight import (
    SUBDIVISION_ACTIVATION_PREFLIGHT_NEXT_GATE,
    SUBDIVISION_ACTIVATION_PREFLIGHT_SCHEMA,
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


def test_subdivision_preflight_binds_packet_and_canary_without_authority():
    topology = _topology()
    before = json.loads(json.dumps(topology, sort_keys=True))

    report = build_subdivision_activation_preflight(
        topology=topology,
        plan=_plan(),
        canary_comparisons=_canary_records(),
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["schema_version"] == SUBDIVISION_ACTIVATION_PREFLIGHT_SCHEMA
    assert report["required_next_gate"] == (
        SUBDIVISION_ACTIVATION_PREFLIGHT_NEXT_GATE
    )
    assert report["target_state"] == "subdivision_in_shadow"
    assert report["no_runtime_mutation"] is True
    assert report["runtime_authority_granted"] is False
    assert report["runtime_topology_mutation_applied"] is False
    assert report["routing_influence_applied"] is False
    assert report["transport_performed"] is False
    assert report["claim_safe_upgrade"] is False
    assert topology == before

    packet = report["shadow_activation_packet"]
    assert packet["ok"] is True
    assert packet["runtime_activation_authority_granted"] is False
    assert packet["delivery_summary"]["blocked_count"] == 0
    assert packet["checks"]["input_topology_unchanged"] is True
    assert report["shadow_activation_packet_digest"] == sha256_digest(packet)

    canary = report["canary_mirror_report"]
    assert canary["sample_count"] == 2
    assert canary["runtime_authority_granted"] is False
    assert canary["routing_influence_applied"] is False
    assert report["canary_mirror_report_digest"] == (
        canary["canonical_digest"]
    )

    core = {
        key: value for key, value in report.items()
        if key not in (
            "preflight_digest",
            "shadow_activation_packet",
            "canary_mirror_report",
        )
    }
    assert report["preflight_digest"] == sha256_digest(core)
    assert set(report["guardrails"].values()) == {True}


def test_subdivision_preflight_requires_canary_evidence():
    report = build_subdivision_activation_preflight(
        topology=_topology(),
        plan=_plan(),
        canary_comparisons=[],
    )

    assert report["ok"] is False
    assert "canary_evidence_present" in report["blockers"]
    assert report["runtime_authority_granted"] is False


def test_subdivision_preflight_fails_closed_on_blocked_delivery(monkeypatch):
    def _blocked_batch(topology, messages):
        return [
            rm.RingDelivery(
                message_id_seq=index,
                delivered=False,
                blocked_reason="forced block",
                blocked_category=rm.RING_BLOCK_SCHEMA_INVALID,
                msg=message,
            )
            for index, message in enumerate(messages)
        ]

    monkeypatch.setattr(so, "deliver_batch", _blocked_batch)

    report = build_subdivision_activation_preflight(
        topology=_topology(),
        plan=_plan(),
        canary_comparisons=_canary_records(),
    )

    assert report["ok"] is False
    assert "shadow_activation_packet_ok" in report["blockers"]
    assert "activation_messages_delivered" in report["blockers"]
    assert report["shadow_activation_packet"]["delivery_summary"][
        "blocked_count"
    ] == 6
    assert report["runtime_authority_granted"] is False


def test_subdivision_preflight_refuses_canary_authority_drift():
    forged = _canary_records()
    forged[0] = {**forged[0], "runtime_authority_granted": True}

    with pytest.raises(CanaryMirrorError, match="runtime_authority_granted"):
        build_subdivision_activation_preflight(
            topology=_topology(),
            plan=_plan(),
            canary_comparisons=forged,
        )
