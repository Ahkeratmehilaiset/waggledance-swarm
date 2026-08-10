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
    _is_requester_terminal_closure,
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
        "changes_requested_pending_withdrawn_payload",
        "review_requested_retracted_by_operator",
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
        "changes_requested_withdrawn",
        "changes_requested_retracted",
        "finding_retracted",
        "rco_finding_withdrawn",
        "rco_finding_retracted",
    ],
)
def test_retraction_statuses_are_not_open_incoming(status: str) -> None:
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
        "stale_review_request_after_rebase_observed",
        "review_request_observed",
        "rco_pass_task_id_mismatch_observed",
    ],
)
def test_observed_statuses_are_not_open_incoming(status: str) -> None:
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
        "changes_requested_NOT_resolved",
        "blocked_NOT_closed",
        "request_NOT_answered",
        "changes_requested_NOT_retracted",
        "changes_requested_NOT_withdrawn",
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


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("status", "superseded"),
        ("status", "completed_after_review"),
        ("status", "changes_requested_retracted"),
        ("status", "changes_requested_resolved"),
        ("status", "changes_requested_withdrawn"),
        ("done", "closed_current_main_reconciled"),
        ("decision", "rco_finding_withdrawn"),
        ("message", "superseded_by_new_head"),
    ],
)
def test_requester_terminal_closure_contract(
    event_type: str,
    status: str,
) -> None:
    event = _event(status)
    event["type"] = event_type

    assert _is_requester_terminal_closure(event) is True


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("status", "received"),
        ("status", "working"),
        ("done", "request"),
        ("decision", "done_not"),
        ("decision", "changes_requested_resolved_pending"),
        ("message", "completed_after_review"),
    ],
)
def test_requester_nonterminal_closure_contract(
    event_type: str,
    status: str,
) -> None:
    event = _event(status)
    event["type"] = event_type

    assert _is_requester_terminal_closure(event) is False
