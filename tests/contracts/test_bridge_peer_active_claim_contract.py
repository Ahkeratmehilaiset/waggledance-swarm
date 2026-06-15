# SPDX-License-Identifier: Apache-2.0
"""Contract for PR #592 peer active PR-producing claim lifecycle.

The bridge loop must keep a peer's PR-producing claim active across later
non-terminal same-task events, and close it only on exact terminal statuses or
``type=done`` for the same task_id.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tools.bridge_loop_tick import peer_has_active_pr_producing_claim

NOW = datetime(2026, 5, 22, 14, 0, 0, tzinfo=timezone.utc)
TASK_ID = "codex-lead-1/pr592-peer-claim-regression"
OTHER_TASK_ID = "codex-lead-1/unrelated-terminal-task"


def _event(
    *,
    ts: str,
    agent: str = "codex-lead-1",
    event_type: str,
    task_id: str = TASK_ID,
    status: str,
) -> dict[str, str]:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": event_type,
        "task_id": task_id,
        "status": status,
        "message": f"{event_type}/{status}",
    }


def _claim(ts: str = "2026-05-22T13:55:00Z") -> dict[str, str]:
    return _event(ts=ts, event_type="claim", status="active")


def _peer_claim_result(events: list[dict[str, str]]) -> dict[str, object]:
    return peer_has_active_pr_producing_claim(
        events,
        agent="codex-tools-1",
        now_utc=NOW,
    )


def test_pr592_contract_claim_survives_nonterminal_and_closes_on_terminal() -> None:
    """PR #592 invariant: only same-task terminal events close the claim."""
    with_nonterminal = [
        _claim(),
        _event(
            ts="2026-05-22T13:57:00Z",
            event_type="decision",
            status="clarification",
        ),
    ]

    active = _peer_claim_result(with_nonterminal)

    assert active["active"] is True
    assert active["peer"] == "codex-lead-1"
    assert active["task_id"] == TASK_ID
    assert active["reason"] == "peer_has_active_pr_producing_claim"

    closed = _peer_claim_result(
        [
            *with_nonterminal,
            _event(
                ts="2026-05-22T13:58:00Z",
                event_type="message",
                status="blocked",
            ),
        ]
    )

    assert closed["active"] is False
    assert closed["task_id"] == TASK_ID
    assert closed["reason"] == "peer_claim_closed_by_done"


def test_pr592_contract_terminal_on_different_task_does_not_close_claim() -> None:
    events = [
        _claim(),
        _event(
            ts="2026-05-22T13:57:00Z",
            event_type="done",
            task_id=OTHER_TASK_ID,
            status="merged",
        ),
    ]

    result = _peer_claim_result(events)

    assert result["active"] is True
    assert result["task_id"] == TASK_ID
    assert result["reason"] == "peer_has_active_pr_producing_claim"


def test_pr592_contract_terminal_status_requires_exact_match() -> None:
    events = [
        _claim(),
        _event(
            ts="2026-05-22T13:57:00Z",
            event_type="message",
            status="blocked_pending",
        ),
        _event(
            ts="2026-05-22T13:58:00Z",
            event_type="message",
            status="released_candidate",
        ),
    ]

    result = _peer_claim_result(events)

    assert result["active"] is True
    assert result["task_id"] == TASK_ID
    assert result["reason"] == "peer_has_active_pr_producing_claim"
