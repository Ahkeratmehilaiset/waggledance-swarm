"""The content projection records one hash-bound recipient label per record: the single build lane an eligible
event is explicitly addressed to, else None. Reviewer-addressed rows never enter; aliases, lists and extra routes
fail closed. Synthetic rows only."""

import json

import pytest

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2.test_shared_content_projection import (FABLE, LEAD, RCO1, RCO2, TOOLS, build, policy_doc,
                                                                 row, rows_of)

RESPONDER = {"agent_uuid": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "run_id": "run-synthetic",
             "session_id": "session-synthetic"}


def labels(projection):
    return [record["meta"]["to"] for record in projection["records"]]


@pytest.mark.parametrize("lane", [LEAD, TOOLS, FABLE])
def test_an_assignment_to_one_build_lane_carries_that_lane_as_recipient(lane):
    projection = build(rows_of(row(to=lane)))
    assert projection["accepted"] == 1 and labels(projection) == [lane]


def test_every_record_meta_carries_the_recipient_key_even_when_none():
    projection = build(rows_of(row(minute=0), row(("synthetic note " * 50).strip(), minute=1)))
    assert projection["accepted"] >= 3
    assert all("to" in record["meta"] for record in projection["records"])
    assert set(labels(projection)) == {FABLE}


@pytest.mark.parametrize("event", [
    {"to": None}, {"to": ""}, {"to": [FABLE]}, {"to": "FABLE-5"}, {"to": "fable5"}, {"to": " fable-5"},
    {"to": "fable-5 "}, {"to": "mallory"}, {"to": "codex-lead-1,fable-5"},
])
def test_missing_malformed_aliased_or_unknown_recipients_read_as_none(event):
    base = row()
    base.update(event)
    if event.get("to") is None and "to" in event:
        del base["to"]
    projection = build(rows_of(base))
    assert projection["accepted"] == 1 and labels(projection) == [None]


@pytest.mark.parametrize("event", [
    {"cc": [TOOLS]}, {"recipients": [FABLE]}, {"payload": {"recipients": [FABLE]}},
    {"payload": {"request": {"to": FABLE}}}, {"payload": {"target_agents": [FABLE]}}, {"reply_to": LEAD},
    {"send-to": FABLE}, {"payload": {"note": "synthetic", "Recipient_List": [FABLE]}},
    {"expected_responders": {FABLE: dict(RESPONDER), TOOLS: dict(RESPONDER)}},
    {"expected_responders": {TOOLS: dict(RESPONDER)}},
    {"payload": {"expected_responders": {TOOLS: dict(RESPONDER)}}},
])
def test_a_second_route_or_a_wider_responder_set_makes_the_recipient_unclassifiable(event):
    base = row()
    base.update(event)
    projection = build(rows_of(base))
    assert projection["accepted"] >= 1 and set(labels(projection)) == {None}


def test_a_bound_responder_set_naming_exactly_the_recipient_keeps_the_label():
    projection = build(rows_of(row(expected_responders={FABLE: dict(RESPONDER)})))
    assert labels(projection) == [FABLE]


@pytest.mark.parametrize("event", [
    {"to": RCO1}, {"to": RCO2}, {"to": "RCO1"}, {"to": "rco-2"}, {"to": FABLE, "cc": [RCO1]}, {"to": 12},
    {"to": {"lane": FABLE}}, {"to": [FABLE, 1]},
    {"to": FABLE, "payload": {"request": {"to": RCO2}}},
    {"to": FABLE, "expected_responders": {FABLE: dict(RESPONDER), RCO1: dict(RESPONDER)}},
])
def test_reviewer_addressed_or_unclassifiable_rows_never_enter_the_projection(event):
    base = row()
    base.update(event)
    projection = build(rows_of(base))
    assert projection["accepted"] == 0 and projection["records"] == []
    assert "rco" not in json.dumps(projection).lower()


@pytest.mark.parametrize("agent", [RCO1, RCO2])
def test_reviewer_authored_rows_never_enter_the_projection(agent):
    projection = build(rows_of(row(agent=agent, to=FABLE)))
    assert projection["accepted"] == 0 and projection["records"] == []


def test_a_recipient_outside_the_pinned_policy_lanes_reads_as_none():
    projection = build(rows_of(row(to=FABLE)), policy_doc(lanes=[LEAD, TOOLS]))
    assert projection["accepted"] == 1 and labels(projection) == [None]


def test_the_recipient_label_is_part_of_the_hash_bound_projection_bytes():
    one = build(rows_of(row(to=FABLE)))
    other = build(rows_of(row(to=TOOLS)))
    canon = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    assert canon(one) != canon(other)
    (first,), (second,) = one["records"], other["records"]
    assert first["stored_text"] == second["stored_text"]  # the source ref differs: the event itself differs
    assert {**first["meta"], "to": None} == {**second["meta"], "to": None}


def test_the_label_never_adds_recipient_text_to_the_stored_text():
    (record,) = build(rows_of(row(to=TOOLS)))["records"]
    assert TOOLS not in record["stored_text"]


def test_recipient_label_is_a_pure_function_of_the_event_and_the_policy_lanes():
    lanes = frozenset({LEAD, TOOLS, FABLE})
    assert cp.recipient_label(row(to=FABLE), lanes) == FABLE
    assert cp.recipient_label(row(to=RCO1), lanes) is None
    assert cp.recipient_label({"to": FABLE, 7: "x"}, lanes) is None
    assert cp.recipient_label({"to": FABLE, "payload": [{"targets": [FABLE]}]}, lanes) is None
    assert cp.recipient_label({"to": FABLE, "payload": [[{"to": FABLE}]]}, lanes) is None
    assert cp.recipient_label({"to": FABLE, "payload": {"to": FABLE}}, lanes) is None
    assert cp.recipient_label({"to": FABLE, "To": FABLE}, lanes) is None
    assert cp.recipient_label(None, lanes) is None
    assert cp.recipient_label(["to", FABLE], lanes) is None
