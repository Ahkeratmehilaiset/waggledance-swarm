"""Request closure is not merge approval or completion of deferred work."""

import pytest

from tools.bridge_next_action import recommend_next_action


@pytest.mark.parametrize("status", ["deferred_with_reason", "autonomous_merge_receipt"])
@pytest.mark.parametrize("actor", ["codex-lead-1", "codex-tools-1", "fable-5"])
def test_terminal_response_requires_request_participant(status, actor):
    events = [
        {"ts_utc": "2026-09-11T06:00:00Z", "agent": "codex-lead-1",
         "to": "codex-tools-1", "type": "handoff", "task_id": "review-a",
         "status": "ready_for_review", "message": "Please review."},
        {"ts_utc": "2026-09-11T06:01:00Z", "agent": actor,
         "to": "codex-tools-1", "type": "decision", "task_id": "review-a",
         "status": status, "message": "Disposition for this request."},
    ]
    result = recommend_next_action(agent="codex-tools-1", events=events, claims=[])
    assert result["open_incoming_count"] == (1 if actor == "fable-5" else 0)


@pytest.mark.parametrize("response_task", ["different-task", "review-a"])
def test_old_or_other_task_disposition_does_not_close_new_request(response_task):
    events = [
        {"ts_utc": "2026-09-11T06:00:00Z", "agent": "codex-lead-1",
         "to": "codex-tools-1", "type": "decision", "task_id": response_task,
         "status": "deferred_with_reason", "message": "Earlier disposition."},
        {"ts_utc": "2026-09-11T06:01:00Z", "agent": "codex-lead-1",
         "to": "codex-tools-1", "type": "handoff", "task_id": "review-a",
         "status": "ready_for_review", "message": "New review needed."},
    ]
    result = recommend_next_action(agent="codex-tools-1", events=events, claims=[])
    assert result["open_incoming_count"] == 1
