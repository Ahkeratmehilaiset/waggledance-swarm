# SPDX-License-Identifier: BUSL-1.1
"""Bridge next-action request status taxonomy contract.

The bridge liveness loop treats directed request-like events as work until a
terminal status closes them. This contract keeps the status vocabulary exact so
new status strings do not strand agents on already-resolved work or hide still
open requests.
"""

from __future__ import annotations

import pytest

from tools.bridge_next_action import (
    CLOSED_REQUEST_STATUSES,
    recommend_next_action,
)


def _event(status: str) -> dict[str, object]:
    return {
        "ts_utc": "2026-06-11T09:30:42Z",
        "agent": "codex-lead-1",
        "to": "codex-tools-1",
        "type": "message",
        "task_id": "bridge-status-taxonomy",
        "status": status,
        "message": f"status taxonomy fixture: {status}",
    }


@pytest.mark.parametrize("status", sorted(CLOSED_REQUEST_STATUSES))
def test_exact_closed_request_statuses_are_not_open_incoming(status: str) -> None:
    report = recommend_next_action(
        agent="codex-tools-1",
        events=[_event(status)],
        claims=[],
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


@pytest.mark.parametrize(
    "status",
    [
        "request_changes",
        "review_requested",
        "rco_requested",
        "operator_feedback_requested",
    ],
)
def test_request_statuses_remain_open_incoming(status: str) -> None:
    report = recommend_next_action(
        agent="codex-tools-1",
        events=[_event(status)],
        claims=[],
    )

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "bridge-status-taxonomy"
    assert report["open_incoming_count"] == 1


@pytest.mark.parametrize(
    "status",
    [
        "changes_requested_NOT_resolved",
        "blocked_NOT_closed",
        "request_NOT_answered",
    ],
)
def test_negated_terminal_status_words_do_not_close_requests(status: str) -> None:
    report = recommend_next_action(
        agent="codex-tools-1",
        events=[_event(status)],
        claims=[],
    )

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "bridge-status-taxonomy"
    assert report["open_incoming_count"] == 1
