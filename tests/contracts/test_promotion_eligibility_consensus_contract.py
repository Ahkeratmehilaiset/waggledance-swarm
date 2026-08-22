# SPDX-License-Identifier: BUSL-1.1
"""Contracts for promotion bridge-consensus refusal boundaries."""

from __future__ import annotations

from tools.check_promotion_eligible import evaluate_promotion_eligibility

HEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
TASK = "codex-lead-1/promotion-consensus-contract-20260607"
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
}
CLEAR_ACCEPTED_QUEUE = {
    "ok": True,
    "complete": True,
    "decision": "accepted_queue_absent",
}


def _status() -> dict:
    return {
        "pr_number": 944,
        "head_sha": HEAD,
        "base_sha": BASE,
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "is_draft": True,
        "changed_paths": ["tools/idle_daily_summary.py"],
        "diff_text": "+ def helper():\n+     return 1\n",
        "checks": [
            {"name": "unified", "state": "success"},
            {"name": "test (3.13)", "conclusion": "success"},
        ],
    }


def _event(
    agent: str,
    status: str,
    *,
    event_type: str = "decision",
    task_id: str = TASK,
    head: str = HEAD,
    ts: str = "2026-06-07T02:15:00Z",
    payload: dict | None = None,
    message: str | None = None,
) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": event_type,
        "status": status,
        "task_id": task_id,
        "message": message if message is not None else f"{status} exact head {head}",
        "payload": {"head": head, "pr": 944} if payload is None else payload,
        "agent_uuid": AGENT_UUIDS.get(agent),
    }


def _evaluate(events: list[dict], *, author_agent: str = "fable-5") -> dict:
    return evaluate_promotion_eligibility(
        pr_status=_status(),
        events=events,
        task_id=TASK,
        head=HEAD,
        origin_main_sha=BASE,
        author_agent=author_agent,
        accepted_queue_preflight=CLEAR_ACCEPTED_QUEUE,
    )


def test_build_consensus_passhead_typo_fails_closed() -> None:
    report = _evaluate(
        [
            _event(
                "codex-lead-1",
                "build_consensus_passhead",
                ts="2026-06-07T02:15:00Z",
            ),
            _event(
                "codex-tools-1",
                "build_consensus_pass",
                ts="2026-06-07T02:16:00Z",
            ),
            _event("claude-rco-1", "rco_pass", ts="2026-06-07T02:17:00Z"),
        ]
    )

    assert report["eligible"] is False
    assert "bridge consensus incomplete" in report["reasons"]
    assert report["gate_results"]["rco_pass"]["ok"] is True
    consensus = report["gate_results"]["bridge_consensus"]["by_agent"][
        "claude-rco-1"
    ]
    assert consensus["identities"]["build_lead"]["approved"] is False
    assert consensus["identities"]["build_tools"]["approved"] is True
    assert consensus["identities"]["rco"]["approved"] is True


def test_descriptive_build_consensus_without_head_binding_fails_closed() -> None:
    report = _evaluate(
        [
            _event(
                "codex-lead-1",
                "build_consensus_pass",
                task_id="lead-descriptive-refresh",
                payload={},
                message="build consensus for descriptive task without head binding",
                ts="2026-06-07T02:15:00Z",
            ),
            _event(
                "codex-tools-1",
                "build_consensus_pass",
                task_id="tools-descriptive-refresh",
                payload={},
                message="build consensus for descriptive task without head binding",
                ts="2026-06-07T02:16:00Z",
            ),
            _event("claude-rco-1", "rco_pass", ts="2026-06-07T02:17:00Z"),
        ]
    )

    assert report["eligible"] is False
    assert "bridge consensus incomplete" in report["reasons"]
    consensus = report["gate_results"]["bridge_consensus"]["by_agent"][
        "claude-rco-1"
    ]
    assert consensus["identities"]["build_lead"]["approved"] is False
    assert consensus["identities"]["build_tools"]["approved"] is False
    assert consensus["identities"]["rco"]["approved"] is True


def test_rco_pass_on_noncanonical_task_fails_closed() -> None:
    report = _evaluate(
        [
            _event(
                "codex-lead-1",
                "build_consensus_pass",
                ts="2026-06-07T02:15:00Z",
            ),
            _event(
                "codex-tools-1",
                "build_consensus_pass",
                ts="2026-06-07T02:16:00Z",
            ),
            _event(
                "claude-rco-1",
                "rco_pass",
                task_id="codex-tools-1/main-after-pr941-942-20260607",
                ts="2026-06-07T02:17:00Z",
            ),
        ]
    )

    assert report["eligible"] is False
    assert "missing exact-head RCO_PASS from recognized non-author RCO" in (
        report["reasons"]
    )
    assert report["gate_results"]["rco_pass"]["ok"] is False


def test_tools_validation_events_do_not_count_as_build_consensus() -> None:
    report = _evaluate(
        [
            _event(
                "codex-lead-1",
                "build_consensus_pass",
                ts="2026-06-07T02:15:00Z",
            ),
            _event(
                "codex-tools-1",
                "pass_ci_green",
                event_type="test",
                ts="2026-06-07T02:16:00Z",
            ),
            _event(
                "codex-tools-1",
                "done",
                event_type="done",
                ts="2026-06-07T02:16:30Z",
            ),
            _event("claude-rco-1", "rco_pass", ts="2026-06-07T02:17:00Z"),
        ]
    )

    assert report["eligible"] is False
    assert "bridge consensus incomplete" in report["reasons"]
    assert report["gate_results"]["rco_pass"]["ok"] is True
    consensus = report["gate_results"]["bridge_consensus"]["by_agent"][
        "claude-rco-1"
    ]
    assert consensus["identities"]["build_lead"]["approved"] is True
    assert consensus["identities"]["build_tools"]["approved"] is False
    assert consensus["identities"]["rco"]["approved"] is True


def test_canonical_exact_head_build_consensus_remains_eligible() -> None:
    report = _evaluate(
        [
            _event(
                "codex-lead-1",
                "build_consensus_pass",
                ts="2026-06-07T02:15:00Z",
            ),
            _event(
                "codex-tools-1",
                "build_consensus_pass",
                ts="2026-06-07T02:16:00Z",
            ),
            _event("claude-rco-1", "rco_pass", ts="2026-06-07T02:17:00Z"),
        ]
    )

    assert report["eligible"] is True
    assert report["decision"] == "promotion_eligible"
    assert report["gate_results"]["bridge_consensus"]["ok"] is True


def test_status_like_build_decision_after_pass_supersedes_build_approval() -> None:
    # Informational post-pass updates from build identities must use status/message.
    # A block-shaped type=decision is an authoritative later verdict and withdraws
    # that identity's build slot until it explicitly re-posts build_consensus_pass.
    report = _evaluate(
        [
            _event(
                "codex-lead-1",
                "build_consensus_pass",
                ts="2026-06-07T02:15:00Z",
            ),
            _event(
                "codex-tools-1",
                "build_consensus_pass",
                ts="2026-06-07T02:16:00Z",
            ),
            _event("claude-rco-1", "rco_pass", ts="2026-06-07T02:17:00Z"),
            _event(
                "codex-lead-1",
                "merge_blocked_operator_or_driver",
                ts="2026-06-07T02:18:00Z",
                message="status note accidentally emitted as a decision",
            ),
        ]
    )

    assert report["eligible"] is False
    assert "bridge consensus incomplete" in report["reasons"]
    consensus = report["gate_results"]["bridge_consensus"]["by_agent"][
        "claude-rco-1"
    ]
    build_lead = consensus["identities"]["build_lead"]
    assert build_lead["approved"] is False
    assert build_lead["approval_index"] is not None
    assert build_lead["block_index"] is not None
    assert build_lead["block_index"] > build_lead["approval_index"]
    assert consensus["identities"]["build_tools"]["approved"] is True
    assert consensus["identities"]["rco"]["approved"] is True


def test_lead_authored_promotion_uses_build_author_slot_waiver() -> None:
    report = _evaluate(
        [
            _event(
                "codex-lead-1",
                "build_consensus_pass",
                ts="2026-06-07T02:15:00Z",
            ),
            _event(
                "codex-tools-1",
                "build_consensus_pass",
                ts="2026-06-07T02:16:00Z",
            ),
            _event("claude-rco-1", "rco_pass", ts="2026-06-07T02:17:00Z"),
        ],
        author_agent="codex-lead-1",
    )

    assert report["eligible"] is True
    consensus = report["gate_results"]["bridge_consensus"]["by_agent"][
        "claude-rco-1"
    ]
    assert consensus["build_author_slot_waivers"] == ["codex-lead-1"]
    assert consensus["identities"]["build_lead"]["approved"] is True
    assert consensus["identities"]["build_lead"]["direct_approval"] is False
    assert (
        consensus["identities"]["build_lead"]["build_author_slot_waived"] is True
    )
    assert consensus["identities"]["build_tools"]["approved"] is True


def test_build_author_slot_waiver_does_not_replace_other_build_peer() -> None:
    report = _evaluate(
        [
            _event(
                "codex-tools-1",
                "build_consensus_pass",
                ts="2026-06-07T02:16:00Z",
            ),
            _event("claude-rco-1", "rco_pass", ts="2026-06-07T02:17:00Z"),
        ],
        author_agent="codex-tools-1",
    )

    assert report["eligible"] is False
    assert "bridge consensus incomplete" in report["reasons"]
    consensus = report["gate_results"]["bridge_consensus"]["by_agent"][
        "claude-rco-1"
    ]
    assert consensus["build_author_slot_waivers"] == ["codex-tools-1"]
    assert consensus["identities"]["build_tools"]["approved"] is True
    assert consensus["identities"]["build_tools"]["direct_approval"] is False
    assert (
        consensus["identities"]["build_tools"]["build_author_slot_waived"] is True
    )
    assert consensus["identities"]["build_lead"]["approved"] is False
