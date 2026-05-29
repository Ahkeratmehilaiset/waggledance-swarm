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

from tools.idle_consensus_auto_merge import (
    evaluate_auto_merge_gate,
    verify_bridge_consensus,
)

HEAD = "1234567890abcdef1234567890abcdef12345678"
OTHER_HEAD = "00000000000000000000000000000000deadbeef"
TASK = "codex-lead/t0b-consensus-approver-20260529"

LEAD = "codex-lead-1"
TOOLS = "codex-tools-1"
RCO = "claude-rco-1"


def _approval(agent: str, status: str, *, head: str = HEAD, ts: str, in_message: bool = False) -> dict:
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
    return event


def _block(agent: str, *, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": "finding",
        "status": "changes_requested",
        "task_id": TASK,
        "message": "blocking",
        "payload": {},
    }


def _full_consensus() -> list[dict]:
    return [
        _approval(LEAD, "build_consensus", ts="2026-05-29T13:00:00Z"),
        _approval(TOOLS, "build_consensus", ts="2026-05-29T13:01:00Z"),
        # RCO uses the message-text head binding path on purpose.
        _approval(RCO, "rco_pass", ts="2026-05-29T13:02:00Z", in_message=True),
    ]


# --- direct verify_bridge_consensus verdicts ------------------------------


def test_happy_path_three_distinct_head_bound_identities() -> None:
    result = verify_bridge_consensus(events=_full_consensus(), task_id=TASK, head_sha=HEAD)
    assert result["ok"] is True
    assert result["decision"] == "bridge_consensus_verified"
    assert result["identities"]["build_lead"]["approved"] is True
    assert result["identities"]["build_tools"]["approved"] is True
    assert result["identities"]["rco"]["approved"] is True
    assert result["rco_pass_ref"]["agent"] == RCO
    assert result["head_sha"] == HEAD


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
        _approval(RCO, "rco_pass", head=OTHER_HEAD, ts="2026-05-29T13:02:00Z", in_message=True),
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
    result = verify_bridge_consensus(events=_full_consensus(), task_id=TASK, head_sha="not-a-sha")
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
