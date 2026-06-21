# SPDX-License-Identifier: BUSL-1.1
"""Bridge peer-block status contract.

The merge preflight must fail closed when peer veto vocabulary is carried by
traffic events, while still ignoring status strings that merely describe an
answer, correction, or advisory context.
"""
from __future__ import annotations

import pytest

from tools.check_bridge_changes_requested import check_bridge_clear_to_merge


AGENT_UUIDS = {
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}


def _event(event_type: str, status: str, *, agent: str = "codex-lead-1") -> dict:
    return {
        "ts_utc": "2026-06-21T01:57:31Z",
        "agent": agent,
        "agent_uuid": AGENT_UUIDS[agent],
        "type": event_type,
        "task_id": "bridge-peer-block-status-contract",
        "status": status,
        "severity": "",
        "to": "codex-tools-1",
        "message": f"contract fixture: {status}",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 0,
        "cwd": "",
    }


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("message", "changes_requested_do_not_merge"),
        ("message", "rco_changes_requested_pr530"),
        ("message", "rco_block_critical"),
        ("message", "blocked_no_fix_yet"),
        ("handoff", "block_without_fix"),
        ("finding", "changes_requested_shape_validation"),
    ],
)
def test_decorated_peer_veto_statuses_block_regardless_of_event_type(
    event_type: str,
    status: str,
) -> None:
    result = check_bridge_clear_to_merge(
        events=[_event(event_type, status)],
        task_id="bridge-peer-block-status-contract",
        merging_agent="codex-tools-1",
        identity_registry=AGENT_UUIDS,
    )

    assert result["clear_to_merge"] is False
    assert result["latest_blocking_event"]["status"] == status


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("message", "changes_requested_payload_corrected"),
        ("message", "answered_changes_requested_forwarded_to_fable"),
        ("message", "ack_blocked_by_lead_changes_requested"),
        ("finding", "pr1344_producer_advisory_resolves_1340_1343_phantom_block"),
        ("status", "changes_requested_addressed_exact_head_ci_pending"),
    ],
)
def test_contextual_traffic_statuses_do_not_create_peer_vetoes(
    event_type: str,
    status: str,
) -> None:
    result = check_bridge_clear_to_merge(
        events=[_event(event_type, status, agent="fable-5")],
        task_id="bridge-peer-block-status-contract",
        merging_agent="codex-tools-1",
        identity_registry=AGENT_UUIDS,
    )

    assert result["clear_to_merge"] is True
    assert result["latest_blocking_event"] is None
