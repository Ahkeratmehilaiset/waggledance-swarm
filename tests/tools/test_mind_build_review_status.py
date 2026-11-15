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


@pytest.mark.parametrize("changes", [
    {"type": "done"},
    {"to": "operator"},
    {"to": "external-consumer"},
    {"to": ""},
    {"to": "codex-tools-1,"},
    {"payload": {"nested": {"recipients": []}}},
    {"payload": {"nested": {"recipients": ["codex-tools-1", "external-consumer"]}}},
    {"payload": {"nested": [{"target-agent": "claude-rco-2"}]}},
    {"payload": {"nested": {"expected_responders": {}}}},
    {"payload": {"nested": {"expected_responders": {"codex-tools-1": {
        "agent_uuid": "00000000-0000-4000-8000-000000000099", "run_id": "run", "session_id": "session"}}}}},
])
def test_review_requested_requires_message_and_only_explicit_build_recipients(tmp_path, changes):
    event = _row(**changes)
    assert _project(tmp_path, event)["messages"]["records"] == []


def test_review_requested_missing_to_refuses_even_with_nested_recipient(tmp_path):
    event = _row(payload={"recipient": "codex-tools-1"})
    del event["to"]
    assert _project(tmp_path, event)["messages"]["records"] == []


@pytest.mark.parametrize("key", sorted(reader._ROUTING_KEYS))
def test_each_known_nested_routing_field_refuses_an_external_recipient(tmp_path, key):
    event = _row(payload={"nested": [{key: "external-consumer"}]})
    assert _project(tmp_path, event)["messages"]["records"] == []


@pytest.mark.parametrize("target", [None, 42, [], ["codex-tools-1", None], "codex-tools-1,,fable-5"])
def test_review_requested_refuses_malformed_nested_recipients(tmp_path, target):
    event = _row(payload={"nested": {"recipient-list": target}})
    assert _project(tmp_path, event)["messages"]["records"] == []


@pytest.mark.parametrize("changes", [
    {"to": "codex-tools-1, fable-5"},
    {"payload": {"nested": {"recipients": ["codex-tools-1", "fable-5"]}}},
    {"payload": {"nested": {"expected_responders": {"codex-tools-1": {
        "agent_uuid": fixture.ROSTER["codex-tools-1"], "run_id": "run", "session_id": "session"}}}}},
])
def test_review_requested_allows_only_bound_build_recipients(tmp_path, changes):
    event = _row(**changes)
    projection = _project(tmp_path, event)
    records = projection["messages"]["records"]
    assert len(records) == 1
    assert records[0]["provenance"]["source_event_ref"] == fixture._sha(fixture._raw(event))


@pytest.mark.parametrize("changes", [
    {"status": "in_progress", "to": "external-consumer"},
    {"status": "in_progress", "type": "done", "to": "external-consumer"},
    {"status": "requested", "to": ""},
])
def test_other_existing_statuses_keep_their_previous_scope(tmp_path, changes):
    assert len(_project(tmp_path, _row(**changes))["messages"]["records"]) == 1

