# SPDX-License-Identifier: BUSL-1.1
"""Bridge next-action request status taxonomy contract.

The bridge liveness loop treats directed request-like events as work until a
terminal status closes them. This contract keeps the status vocabulary exact so
new status strings do not strand agents on already-resolved work or hide still
open requests. It also pins requester-terminal parity across consumers: the
selector and the peer-message notifier must reach the same verdict for the
same requester closure, so a status can never be closed in one surface and
still open in another.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from tools.bridge_next_action import (
    CLOSED_REQUEST_STATUSES,
    recommend_next_action,
)
from tools.notify_unanswered_peer_messages import surface_unanswered_peer_messages

FIXTURE_NOW = datetime.fromisoformat("2026-06-11T09:31:00+00:00")


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
        now_utc=FIXTURE_NOW,
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
        now_utc=FIXTURE_NOW,
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
        now_utc=FIXTURE_NOW,
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
        now_utc=FIXTURE_NOW,
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
        now_utc=FIXTURE_NOW,
    )

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "bridge-status-taxonomy"
    assert report["open_incoming_count"] == 1


def _requester_closure_fixture(closure_status: str) -> list[dict[str, object]]:
    request = {
        "ts_utc": "2026-06-11T09:30:42Z",
        "agent": "codex-lead-1",
        "to": "codex-tools-1",
        "type": "message",
        "task_id": "requester-terminal-parity",
        "status": "review_requested",
        "message": "parity fixture request",
    }
    closure = {
        "ts_utc": "2026-06-11T09:30:50Z",
        "agent": "codex-lead-1",
        "to": "codex-tools-1,operator",
        "type": "decision",
        "task_id": "requester-terminal-parity",
        "status": closure_status,
        "message": f"parity fixture requester closure: {closure_status}",
    }
    return [request, closure]


@pytest.mark.parametrize(
    ("closure_status", "expected_open"),
    [
        ("resolved", False),
        ("done", False),
        ("postmerge_validated", False),
        ("validated", False),
        ("verified_and_closed", False),
        ("unresolved", True),
        ("not_resolved", True),
        ("resolved_pending_follow_up", True),
        ("acknowledged", True),
    ],
)
def test_requester_terminal_verdict_is_identical_across_consumers(
    tmp_path: Path,
    closure_status: str,
    expected_open: bool,
) -> None:
    """The same requester closure must close (or not) in every consumer."""
    events = _requester_closure_fixture(closure_status)

    selector_report = recommend_next_action(
        agent="codex-tools-1",
        events=events,
        claims=[],
        now_utc=FIXTURE_NOW,
    )
    selector_open = selector_report["open_incoming_count"] == 1

    notifier_report = surface_unanswered_peer_messages(
        agent="codex-tools-1",
        events=events,
        out_dir=tmp_path / "inbox" / "codex-tools-1",
        now_utc=FIXTURE_NOW,
        apply=False,
    )
    notifier_open = notifier_report["marker_count"] == 1

    assert selector_open == expected_open
    assert notifier_open == expected_open
