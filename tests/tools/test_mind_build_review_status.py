"""Synthetic build-lane review requests, never live bridge or evaluation data."""
import json

import pytest

import test_wd_mind_content_query_readonly as fixture
from tools import wd_mind_content_query_readonly as reader
from waggledance.core.bridge_event_schema import validate_event


def _row(message="Review needed: retry handling has a pending fix", **changes):
    event = fixture._row(message, task_id="synthetic-admission-task", type="message",
                         status="review_requested", to="codex-tools-1",
                         pid=1234, cwd=r"C:\Synthetic\lead")
    event.update(changes)
    return event


def _project(tmp_path, event):
    validate_event(event)
    return reader.rederive_message_projection(**fixture._all_tasks_case(reader, tmp_path, [event]))


def test_build_lane_review_requested_keeps_text_and_canonical_source_ref(tmp_path):
    event = _row()
    projection = _project(tmp_path, event)
    records = projection["messages"]["records"]
    assert projection["authority"] == projection["messages"]["authority"] == "none"
    assert len(records) == 1
    assert reader.decode_text(records[0]["stored_text"]) == event["message"]
    assert records[0]["provenance"]["source_event_ref"] == fixture._sha(fixture._raw(event))


@pytest.mark.parametrize("changes", [
    {"agent": "claude-rco-1", "agent_uuid": fixture.ROSTER["claude-rco-1"]},
    {"to": "claude-rco-2"},
    {"payload": {"to": "claude-rco-2"}},
    {"message": "password = synthetic-fake-secret"},
    {"message": "sealed synthetic content"},
    {"message": "Synthetic Private Operator reported an issue"},
    {"message": "Operaattorilla on vakava sairaus"},
    {"status": "arbitrary_future_status"},
])
def test_review_status_does_not_bypass_existing_exclusions(tmp_path, changes):
    event = _row(**changes)
    projection = _project(tmp_path, event)
    assert projection["messages"]["records"] == []
    assert event["message"] not in json.dumps(projection)

