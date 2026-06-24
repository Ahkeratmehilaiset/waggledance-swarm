# SPDX-License-Identifier: BUSL-1.1
"""T0b — fail-closed three-identity bridge-consensus approver.

Covers the forge cases RCO committed to probe: missing RCO_PASS, 2-of-3,
stale-head approvals, duplicate/stand-in identity, and a block that
invalidates an earlier approval. The happy path must pass; every forge must
fail closed (never default-allow).
"""

from __future__ import annotations

import json
from pathlib import Path

import waggledance.core.bridge_identity_registry as identity_registry_module
from tools.idle_consensus_auto_merge import (
    evaluate_auto_merge_gate,
    verify_bridge_consensus as _verify_bridge_consensus,
)

HEAD = "1234567890abcdef1234567890abcdef12345678"
OTHER_HEAD = "00000000000000000000000000000000deadbeef"
TASK = "codex-lead/t0b-consensus-approver-20260529"

LEAD = "codex-lead-1"
TOOLS = "codex-tools-1"
RCO = "claude-rco-1"
RCO2 = "claude-rco-2"
AUTHOR = "fable-5"
ROOT = Path(__file__).resolve().parents[2]
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}


def verify_bridge_consensus(*args, **kwargs):
    kwargs.setdefault("author_agent", AUTHOR)
    return _verify_bridge_consensus(*args, **kwargs)


def test_idle_loop_runbook_keeps_rco_timeout_fail_closed() -> None:
    text = (ROOT / "docs" / "architecture" / "IDLE_LOOP_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    section = text.split("### RCO wakeup window", 1)[1].split("## Recovery", 1)[0]
    bridge_tick = (ROOT / "tools" / "bridge_loop_tick.py").read_text(encoding="utf-8")

    assert "must not self-merge without an explicit head-bound" in section
    assert "operator_review_required" in section
    assert "silence never default-allows" in section
    assert "self-merge **without** the peer's RCO" not in section
    assert "will self-merge" not in section
    assert "self-merge timeout" not in bridge_tick
    assert "operator_review_required" in bridge_tick


def _approval(
    agent: str, status: str, *, head: str = HEAD, ts: str, in_message: bool = False
) -> dict:
    """A head-bound decision/approval event for `agent`."""
    event = {
        "ts_utc": ts,
        "agent": agent,
        "type": "decision",
        "status": status,
        "task_id": TASK,
        "message": f"approving at head {head}" if in_message else "",
        "payload": {} if in_message else {"head": head},
    }
    if agent in AGENT_UUIDS:
        event["agent_uuid"] = AGENT_UUIDS[agent]
    return event


def _block(
    agent: str,
    *,
    ts: str,
    task_id: str = TASK,
    payload: dict | None = None,
) -> dict:
    event = {
        "ts_utc": ts,
        "agent": agent,
        "type": "finding",
        "status": "changes_requested",
        "task_id": task_id,
        "message": "blocking",
        "payload": {} if payload is None else payload,
    }
    if agent in AGENT_UUIDS:
        event["agent_uuid"] = AGENT_UUIDS[agent]
    return event


def _full_consensus() -> list[dict]:
    return [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        _approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
        # RCO uses the message-text head binding path on purpose.
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
    ]


# --- direct verify_bridge_consensus verdicts ------------------------------


def test_happy_path_three_distinct_head_bound_identities() -> None:
    result = verify_bridge_consensus(
        events=_full_consensus(), task_id=TASK, head_sha=HEAD
    )
    assert result["ok"] is True
    assert result["decision"] == "bridge_consensus_verified"
    assert result["identities"]["build_lead"]["approved"] is True
    assert result["identities"]["build_tools"]["approved"] is True
    assert result["identities"]["rco"]["approved"] is True
    assert result["rco_pass_ref"]["agent"] == RCO
    assert result["canonical_task_id"] == TASK
    assert result["build_consensus_reemit_guidance"] == []
    assert result["head_sha"] == HEAD


def test_missing_default_identity_registry_refuses_bridge_consensus(
    monkeypatch,
    tmp_path: Path,
) -> None:
    missing_registry = tmp_path / "missing_bridge_identity_registry.json"
    monkeypatch.setattr(
        identity_registry_module,
        "DEFAULT_BRIDGE_IDENTITY_REGISTRY_PATH",
        missing_registry,
    )

    result = verify_bridge_consensus(
        events=_full_consensus(), task_id=TASK, head_sha=HEAD
    )

    assert result["ok"] is False
    assert result["decision"] == "invalid_identity_registry"
    assert any(
        "bridge identity registry not found" in reason for reason in result["reasons"]
    )


def test_backup_rco_can_satisfy_rco_slot() -> None:
    events = [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        _approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
        _approval(RCO2, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
    ]
    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)
    assert result["ok"] is True
    assert result["identities"]["rco"]["approved"] is True
    assert result["rco_pass_ref"]["agent"] == RCO2


def test_backup_rco_uuid_mismatch_does_not_satisfy_rco_slot() -> None:
    forged_rco = _approval(
        RCO2,
        "rco_pass",
        ts="2026-05-29T13:02:00Z",
        in_message=True,
    )
    forged_rco["agent_uuid"] = AGENT_UUIDS["fable-5"]
    events = [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        _approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
        forged_rco,
    ]

    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)

    assert result["ok"] is False
    assert result["identities"]["rco"]["approved"] is False
    assert result["ignored_identity_mismatch_events"][0]["agent"] == RCO2
    assert (
        result["ignored_identity_mismatch_events"][0]["identity_binding_status"]
        == "mismatch_uuid"
    )


def test_build_consensus_shaped_event_reports_invalid_shape() -> None:
    malformed_tools_event = {
        **_approval(TOOLS, "answered_build_consensus_pass", ts="2026-05-29T13:01:00Z"),
        "type": "message",
        "task_id": "codex-lead-t0b-consensus-approver-20260529",
    }
    events = [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        malformed_tools_event,
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
    ]

    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)

    assert result["ok"] is False
    tools_identity = result["identities"]["build_tools"]
    assert tools_identity["approved"] is False
    assert tools_identity["shape_mismatch"] == {
        "type": "message",
        "status": "answered_build_consensus_pass",
        "task_id": "codex-lead-t0b-consensus-approver-20260529",
        "type_ok": False,
        "status_ok": False,
        "task_id_ok": False,
    }
    reason = "\n".join(result["reasons"])
    assert "build_tools (codex-tools-1)" in reason
    assert "head-bound build-consensus-shaped event has invalid shape" in reason
    assert "type 'message' is not one of" in reason
    assert "status 'answered_build_consensus_pass' is not a recognized" in reason
    assert "task_id 'codex-lead-t0b-consensus-approver-20260529'" in reason
    assert f"canonical {TASK!r}" in reason


def test_descriptive_build_task_id_does_not_count_when_payload_head_matches() -> None:
    events = [
        {
            **_approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
            "task_id": "lead-descriptive-refresh",
            "payload": {"head": HEAD},
        },
        {
            **_approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
            "task_id": "tools-descriptive-refresh",
            "payload": {"head": HEAD},
        },
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
    ]

    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)

    assert result["ok"] is False
    assert result["identities"]["build_lead"]["approved"] is False
    assert result["identities"]["build_tools"]["approved"] is False
    assert (
        result["identities"]["build_lead"]["task_id_mismatch"]
        == "lead-descriptive-refresh"
    )
    assert (
        result["identities"]["build_tools"]["task_id_mismatch"]
        == "tools-descriptive-refresh"
    )
    assert result["canonical_task_id"] == TASK
    assert result["build_consensus_reemit_guidance"] == [
        {
            "role": "build_lead",
            "agent": LEAD,
            "reason": "non_canonical_task_id",
            "observed_task_id": "lead-descriptive-refresh",
            "expected_type": "decision",
            "expected_status": "build_consensus_pass",
            "expected_task_id": TASK,
            "expected_payload_head": HEAD,
        },
        {
            "role": "build_tools",
            "agent": TOOLS,
            "reason": "non_canonical_task_id",
            "observed_task_id": "tools-descriptive-refresh",
            "expected_type": "decision",
            "expected_status": "build_consensus_pass",
            "expected_task_id": TASK,
            "expected_payload_head": HEAD,
        },
    ]
    assert any("non-canonical task_id" in reason for reason in result["reasons"])


def test_descriptive_build_task_id_with_stale_payload_head_does_not_count() -> None:
    events = [
        {
            **_approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
            "task_id": "lead-descriptive-refresh",
            "payload": {"head": OTHER_HEAD},
        },
        {
            **_approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
            "task_id": "tools-descriptive-refresh",
            "payload": {"head": HEAD},
        },
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
    ]

    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)

    assert result["ok"] is False
    assert result["identities"]["build_lead"]["approval_index"] is None


def test_duplicate_descriptive_build_identity_still_fails_closed() -> None:
    events = [
        {
            **_approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
            "task_id": "lead-descriptive-refresh",
            "payload": {"head": HEAD},
        },
        {
            **_approval(LEAD, "build_consensus", ts="2026-05-29T13:01:00Z"),
            "task_id": "lead-second-descriptive-refresh",
            "payload": {"head": HEAD},
        },
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
    ]

    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)

    assert result["ok"] is False
    assert result["identities"]["build_lead"]["approved"] is False
    assert (
        result["identities"]["build_lead"]["task_id_mismatch"]
        == "lead-second-descriptive-refresh"
    )
    assert result["identities"]["build_tools"]["approved"] is False


def test_descriptive_build_event_without_payload_head_or_pr_does_not_count() -> None:
    events = [
        {
            **_approval(
                LEAD,
                "build_consensus",
                ts="2026-05-29T13:00:00Z",
                in_message=True,
            ),
            "task_id": "lead-descriptive-refresh",
            "payload": {},
        },
        {
            **_approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
            "task_id": "tools-descriptive-refresh",
            "payload": {"head": HEAD},
        },
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
    ]

    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)

    assert result["ok"] is False
    assert result["identities"]["build_lead"]["approval_index"] is None


def test_descriptive_build_block_with_payload_head_invalidates_approval() -> None:
    events = [
        {
            **_approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
            "task_id": "lead-descriptive-refresh",
            "payload": {"head": HEAD},
        },
        {
            **_approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
            "task_id": "tools-descriptive-refresh",
            "payload": {"head": HEAD},
        },
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
        _block(
            LEAD,
            ts="2026-05-29T13:03:00Z",
            task_id="lead-descriptive-veto",
            payload={"head": HEAD},
        ),
    ]

    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)

    assert result["ok"] is False
    assert result["identities"]["build_lead"]["approved"] is False
    assert result["identities"]["build_lead"]["block_index"] == 3


def test_descriptive_build_block_with_stale_payload_head_still_fails_closed() -> None:
    events = [
        {
            **_approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
            "task_id": "lead-descriptive-refresh",
            "payload": {"head": HEAD},
        },
        {
            **_approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
            "task_id": "tools-descriptive-refresh",
            "payload": {"head": HEAD},
        },
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
        _block(
            LEAD,
            ts="2026-05-29T13:03:00Z",
            task_id="lead-descriptive-veto",
            payload={"head": OTHER_HEAD},
        ),
    ]

    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)

    assert result["ok"] is False
    assert result["identities"]["build_lead"]["approved"] is False
    assert result["identities"]["build_lead"]["block_index"] is None
    assert (
        result["identities"]["build_lead"]["task_id_mismatch"]
        == "lead-descriptive-refresh"
    )


def test_descriptive_rco_block_with_payload_head_invalidates_approval() -> None:
    events = [
        {
            **_approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
            "task_id": "lead-descriptive-refresh",
            "payload": {"head": HEAD},
        },
        {
            **_approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
            "task_id": "tools-descriptive-refresh",
            "payload": {"head": HEAD},
        },
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
        _block(
            RCO,
            ts="2026-05-29T13:03:00Z",
            task_id="rco-descriptive-veto",
            payload={"head": HEAD},
        ),
    ]

    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)

    assert result["ok"] is False
    assert result["identities"]["rco"]["approved"] is False


def test_author_rco_self_pass_does_not_satisfy_rco_slot() -> None:
    events = [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        _approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
        _approval(RCO2, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
    ]
    result = verify_bridge_consensus(
        events=events,
        task_id=TASK,
        head_sha=HEAD,
        author_agent=RCO2,
    )
    assert result["ok"] is False
    assert result["identities"]["rco"]["by_agent"][RCO2]["eligible"] is False
    assert result["rco_pass_ref"] is None
    assert any("recognized non-author RCO" in reason for reason in result["reasons"])


def test_author_lead_self_pass_does_not_satisfy_build_slot() -> None:
    result = verify_bridge_consensus(
        events=_full_consensus(),
        task_id=TASK,
        head_sha=HEAD,
        author_agent=LEAD,
    )

    assert result["ok"] is False
    assert result["identities"]["build_lead"]["eligible"] is False
    assert result["identities"]["build_lead"]["approved"] is False
    assert result["identities"]["build_lead"]["self_approval_ignored"] is True
    assert any(
        "author_agent cannot satisfy its own reviewer slot" in reason
        for reason in result["reasons"]
    )


def test_author_tools_self_pass_does_not_satisfy_build_slot() -> None:
    result = verify_bridge_consensus(
        events=_full_consensus(),
        task_id=TASK,
        head_sha=HEAD,
        author_agent=TOOLS,
    )

    assert result["ok"] is False
    assert result["identities"]["build_tools"]["eligible"] is False
    assert result["identities"]["build_tools"]["approved"] is False
    assert result["identities"]["build_tools"]["self_approval_ignored"] is True
    assert any(
        "author_agent cannot satisfy its own reviewer slot" in reason
        for reason in result["reasons"]
    )


def test_veto_from_either_recognized_rco_blocks_consensus() -> None:
    events = [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        _approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
        _block(RCO2, ts="2026-05-29T13:03:00Z"),
    ]
    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)
    assert result["ok"] is False
    assert result["blocking_rco_agents"] == [RCO2]
    assert result["identities"]["rco"]["approved"] is False


def test_missing_rco_pass_fails_closed() -> None:
    events = [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        _approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
    ]
    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)
    assert result["ok"] is False
    assert result["rco_pass_ref"] is None
    assert any("rco" in r for r in result["reasons"])


def test_rco_only_two_of_three_fails_closed() -> None:
    events = [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z"),
    ]
    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)
    assert result["ok"] is False
    assert result["identities"]["build_tools"]["approved"] is False


def test_stale_head_approvals_fail_closed() -> None:
    events = [
        _approval(LEAD, "build_consensus", head=OTHER_HEAD, ts="2026-05-29T13:00:00Z"),
        _approval(TOOLS, "build_consensus", head=OTHER_HEAD, ts="2026-05-29T13:01:00Z"),
        _approval(
            RCO, "rco_pass", head=OTHER_HEAD, ts="2026-05-29T13:02:00Z", in_message=True
        ),
    ]
    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)
    assert result["ok"] is False
    # none bind the requested head
    assert result["identities"]["build_lead"]["approval_index"] is None


def test_duplicate_identity_standin_fails_closed() -> None:
    # Two lead approvals try to stand in for the missing tools peer.
    events = [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:01:00Z"),
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z"),
    ]
    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)
    assert result["ok"] is False
    assert result["identities"]["build_tools"]["approved"] is False


def test_later_block_invalidates_earlier_approval() -> None:
    events = [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        _approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z"),
        _block(RCO, ts="2026-05-29T13:03:00Z"),
    ]
    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)
    assert result["ok"] is False
    assert result["identities"]["rco"]["approved"] is False


def test_type_agnostic_block_invalidates_approval() -> None:
    # RCO T0b case 11 (fail-open regression guard): a veto posted as
    # type=blocked/status=blocked AFTER an rco_pass must still invalidate
    # consensus, even though type=blocked is not in DECISION_EVENT_TYPES.
    events = [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        _approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z"),
        {
            "ts_utc": "2026-05-29T13:03:00Z",
            "agent": RCO,
            "agent_uuid": AGENT_UUIDS[RCO],
            "type": "blocked",
            "status": "blocked",
            "task_id": TASK,
            "message": "veto via non-decision type",
            "payload": {},
        },
    ]
    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)
    assert result["ok"] is False
    assert result["identities"]["rco"]["approved"] is False


def test_non_authoritative_block_status_does_not_invalidate_consensus() -> None:
    for event_type, status in [
        ("message", "changes_requested"),
        ("handoff", "changes_requested_do_not_merge"),
        ("status", "rco_block_critical"),
    ]:
        events = [
            *_full_consensus(),
            {
                "ts_utc": "2026-05-29T13:03:00Z",
                "agent": TOOLS,
                "agent_uuid": AGENT_UUIDS[TOOLS],
                "type": event_type,
                "status": status,
                "task_id": TASK,
                "message": "bridge coordination chatter",
                "payload": {"head": HEAD},
            },
        ]
        result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)

        assert result["ok"] is True
        assert result["identities"]["build_tools"]["approved"] is True


def test_consensus_mirror_ignores_peer_gate_non_veto_block_diagnostics() -> None:
    for status in [
        "tools_peer_block_clear_needed_after_reattribution",
        "peer_block_is_g4_classifier_artifact_no_real_veto",
        "approved_waiver_block_cleared",
        "fable_1368_failclosed_endorse_verify_block_cleared_coverage",
        "block_cleared_no_remaining_issues",
        "block_resolved_still_monitoring",
        "block_cleared_open_followup",
    ]:
        events = [
            *_full_consensus(),
            {
                "ts_utc": "2026-05-29T13:03:00Z",
                "agent": TOOLS,
                "agent_uuid": AGENT_UUIDS[TOOLS],
                "type": "finding",
                "status": status,
                "task_id": TASK,
                "message": "diagnostic status, not a veto",
                "payload": {"head": HEAD},
            },
        ]
        result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)

        assert result["ok"] is True
        assert result["identities"]["build_tools"]["approved"] is True


def test_consensus_mirror_still_invalidates_authoritative_vetoes() -> None:
    for event_type, status in [
        ("decision", "changes_requested"),
        ("finding", "changes_requested_shape_validation"),
        ("rco_review", "rco_block_critical"),
        ("finding", "block_active_clear_label"),
    ]:
        events = [
            *_full_consensus(),
            {
                "ts_utc": "2026-05-29T13:03:00Z",
                "agent": RCO,
                "agent_uuid": AGENT_UUIDS[RCO],
                "type": event_type,
                "status": status,
                "task_id": TASK,
                "message": "authoritative veto",
                "payload": {"head": HEAD},
            },
        ]
        result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)

        assert result["ok"] is False
        assert result["identities"]["rco"]["approved"] is False


def test_acknowledged_is_not_a_build_vote() -> None:
    # RCO T0b note N2: a bare "acknowledged" is a receipt ack, not an approval.
    events = [
        _approval(LEAD, "acknowledged", ts="2026-05-29T13:00:00Z"),
        _approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z"),
    ]
    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)
    assert result["ok"] is False
    assert result["identities"]["build_lead"]["approved"] is False


def test_fresh_approval_overrides_older_block() -> None:
    events = [
        _block(RCO, ts="2026-05-29T13:00:00Z"),
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:01:00Z"),
        _approval(TOOLS, "build_consensus", ts="2026-05-29T13:02:00Z"),
        _approval(RCO, "rco_pass", ts="2026-05-29T13:03:00Z"),
    ]
    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)
    assert result["ok"] is True


def test_non_decision_event_type_does_not_count_as_approval() -> None:
    # An rco_pass posted as a bare 'message' (not decision/rco_review/finding)
    # must not satisfy the RCO identity.
    events = _full_consensus()
    events[2]["type"] = "message"
    result = verify_bridge_consensus(events=events, task_id=TASK, head_sha=HEAD)
    assert result["ok"] is False
    assert result["identities"]["rco"]["approved"] is False


def test_invalid_head_fails_closed() -> None:
    result = verify_bridge_consensus(
        events=_full_consensus(), task_id=TASK, head_sha="not-a-sha"
    )
    assert result["ok"] is False
    assert result["decision"] == "invalid_consensus_head"


# --- end-to-end through evaluate_auto_merge_gate --------------------------


def _status(**overrides) -> dict:
    status = {
        "pr_number": 781,
        "head_sha": HEAD,
        "title": "T0b consensus approver",
        "mergeable": "clean",
        "receipt_verified": True,
        "author_agent": RCO2,
        # An allowlisted, non-denylisted path: the gate itself is now
        # self-modification-denylisted (T0a), so it cannot be the changed path.
        "changed_paths": ["tools/idle_daily_summary.py"],
        "diff_text": "+ def helper():\n+     return 1\n",
        "checks": [
            {"name": "test (3.13)", "state": "success"},
            {"name": "unified", "state": "success"},
        ],
    }
    status.update(overrides)
    return status


def _events_path(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(e, sort_keys=True) for e in events), encoding="utf-8"
    )
    return path


def test_gate_blocks_when_consensus_required_but_incomplete(tmp_path: Path) -> None:
    events = [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        _approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
        # no RCO_PASS
    ]
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        consensus_proposal_id=TASK,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, events),
        require_bridge_consensus=True,
    )
    assert report["ok"] is False
    assert report["operator_review_required"] is True
    assert report["bridge_consensus"]["required"] is True
    assert report["bridge_consensus"]["ok"] is False
    assert any("bridge consensus incomplete" in r for r in report["reasons"])


def test_gate_allows_when_consensus_complete(tmp_path: Path) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        consensus_proposal_id=TASK,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _full_consensus()),
        require_bridge_consensus=True,
    )
    assert report["ok"] is True
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["bridge_consensus"]["ok"] is True
    assert report["bridge_consensus"]["rco_pass_ref"]["agent"] == RCO


def test_gate_unchanged_when_consensus_not_required(tmp_path: Path) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(),
        expected_head=HEAD,
        consensus_proposal_id=TASK,
        receipt_bundle_path="docs/receipts/manifest.json",
    )
    assert report["ok"] is True
    assert report["bridge_consensus"]["required"] is False
    assert report["bridge_consensus"]["decision"] == "not_required"
