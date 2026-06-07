# SPDX-License-Identifier: BUSL-1.1
"""Contracts for promotion bridge-consensus refusal boundaries."""

from __future__ import annotations

from tools.check_promotion_eligible import evaluate_promotion_eligibility

HEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
TASK = "codex-lead-1/promotion-consensus-contract-20260607"


def _status() -> dict:
    return {
        "pr_number": 944,
        "head_sha": HEAD,
        "base_sha": BASE,
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
    task_id: str = TASK,
    head: str = HEAD,
    ts: str = "2026-06-07T02:15:00Z",
    payload: dict | None = None,
    message: str | None = None,
) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": "decision",
        "status": status,
        "task_id": task_id,
        "message": message if message is not None else f"{status} exact head {head}",
        "payload": {"head": head, "pr": 944} if payload is None else payload,
    }


def _evaluate(events: list[dict]) -> dict:
    return evaluate_promotion_eligibility(
        pr_status=_status(),
        events=events,
        task_id=TASK,
        head=HEAD,
        origin_main_sha=BASE,
        author_agent="codex-lead-1",
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
