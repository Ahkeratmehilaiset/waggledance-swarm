# SPDX-License-Identifier: BUSL-1.1
"""Head authority for the RCO pass gate: structured claims are authoritative, and free text never overrides them.

A pass carries its head in ``payload.exact_head`` and/or ``payload.head``. Once either is present it is the
event's claim: a claim for another head, a malformed claim, or two claims that disagree bind the requested
head to nothing, and a message that happens to name the requested head cannot rescue them. ``payload.exact_head``
alone satisfies the gate; ``payload.head`` alone never does (the message must name it too); an event with no
structured head at all is still bound by its message (legacy events).
"""
from __future__ import annotations

import pytest

from test_check_rco_pass_present import (  # sibling test module: its constants and event builder are the fixtures
    HEAD,
    OTHER_HEAD,
    TASK,
    _rco_event,
    check_rco_pass_present,
)


def qualifies(event: dict, head: str) -> bool:
    return check_rco_pass_present(events=[event], task_id=TASK, head=head)["has_qualifying_rco_pass_at_head"]


def test_a_retraction_message_naming_a_rejected_head_does_not_make_that_head_look_approved() -> None:
    event = _rco_event(message=f"RCO_PASS at exact head {HEAD}; retracts my changes_requested at {OTHER_HEAD}",
                       payload={"head": HEAD, "exact_head": HEAD})
    assert qualifies(event, HEAD) is True
    assert qualifies(event, OTHER_HEAD) is False


@pytest.mark.parametrize("payload", [{"head": HEAD}, {"exact_head": HEAD}, {"head": HEAD, "exact_head": HEAD}],
                         ids=["head-only", "exact_head-only", "both"])
def test_a_structured_claim_binds_its_head_and_only_that_head(payload: dict) -> None:
    event = _rco_event(message=f"RCO_PASS at exact head {HEAD}; base is {OTHER_HEAD}", payload=payload)
    assert qualifies(event, HEAD) is True
    assert qualifies(event, OTHER_HEAD) is False


def test_payload_head_alone_never_satisfies_the_gate_but_exact_head_alone_does() -> None:
    assert qualifies(_rco_event(message="RCO_PASS (head not mentioned)", payload={"head": HEAD}), HEAD) is False
    assert qualifies(_rco_event(message="RCO_PASS (head not mentioned)", payload={"exact_head": HEAD}), HEAD) is True


@pytest.mark.parametrize("key", ["head", "exact_head"])
def test_a_structured_claim_for_another_head_is_not_overridden_by_the_message(key: str) -> None:
    event = _rco_event(message=f"RCO_PASS for {OTHER_HEAD}; requested SHA mention: {HEAD}", payload={key: OTHER_HEAD})
    assert qualifies(event, HEAD) is False
    assert qualifies(event, OTHER_HEAD) is True


@pytest.mark.parametrize("payload", [{"head": HEAD, "exact_head": OTHER_HEAD}, {"head": OTHER_HEAD, "exact_head": HEAD}],
                         ids=["head-matches", "exact_head-matches"])
def test_conflicting_structured_claims_bind_nothing(payload: dict) -> None:
    event = _rco_event(message=f"RCO_PASS at {HEAD} and {OTHER_HEAD}", payload=payload)
    assert qualifies(event, HEAD) is False
    assert qualifies(event, OTHER_HEAD) is False


@pytest.mark.parametrize("key", ["head", "exact_head"])
@pytest.mark.parametrize("bad", [None, "", "   ", 7, True, [HEAD], {"sha": HEAD}, HEAD[:8], HEAD + "f",
                                 HEAD[:20] + "​" + HEAD[20:]],
                         ids=["null", "empty", "blank", "int", "bool", "list", "dict", "short", "long", "zero-width"])
def test_a_malformed_structured_claim_is_not_rescued_by_the_message(key: str, bad: object) -> None:
    event = _rco_event(message=f"RCO_PASS at exact head {HEAD}", payload={key: bad})
    assert qualifies(event, HEAD) is False


def test_a_malformed_claim_next_to_a_valid_one_binds_nothing() -> None:
    event = _rco_event(message=f"RCO_PASS at exact head {HEAD}", payload={"head": HEAD, "exact_head": [HEAD]})
    assert qualifies(event, HEAD) is False


@pytest.mark.parametrize("key", ["head", "exact_head"])
def test_a_structured_claim_tolerates_case_and_surrounding_whitespace(key: str) -> None:
    event = _rco_event(message=f"RCO_PASS at exact head {HEAD}", payload={key: f"  {HEAD.upper()}\n"})
    assert qualifies(event, HEAD) is True


@pytest.mark.parametrize("payload", [{}, {"note": "no head here"}, {"summary": HEAD}], ids=["empty", "other-keys", "sha-in-other-key"])
def test_an_event_with_no_structured_head_is_still_bound_by_its_message(payload: dict) -> None:
    event = _rco_event(message=f"RCO_PASS at exact head {HEAD}", payload=payload)
    assert qualifies(event, HEAD) is True
    assert qualifies(_rco_event(message="RCO_PASS, no sha", payload=payload), HEAD) is False


@pytest.mark.parametrize("payload", ["free text", [1, 2], 7], ids=["string", "list", "int"])
def test_a_payload_that_is_not_an_object_makes_no_structured_claim_and_is_bound_by_its_message(payload: object) -> None:
    assert qualifies(_rco_event(message=f"RCO_PASS at exact head {HEAD}", payload=payload), HEAD) is True
    assert qualifies(_rco_event(message="RCO_PASS, no sha", payload=payload), HEAD) is False


def test_a_head_only_pass_for_another_head_is_reported_stale_with_that_head() -> None:
    event = _rco_event(message=f"RCO_PASS mentions {HEAD}", payload={"head": OTHER_HEAD})
    result = check_rco_pass_present(events=[event], task_id=TASK, head=HEAD)
    assert result["decision"] == "no_qualifying_pass"
    assert result["has_stale_rco_pass_at_other_head"] is True
    assert result["stale_rco_pass_events"][0]["referenced_heads"] == [OTHER_HEAD]


def test_a_malformed_claim_is_not_reported_as_a_stale_head() -> None:
    event = _rco_event(message=f"RCO_PASS at exact head {HEAD}", payload={"head": 7})
    result = check_rco_pass_present(events=[event], task_id=TASK, head=HEAD)
    assert result["decision"] == "no_qualifying_pass"
    assert result["has_stale_rco_pass_at_other_head"] is False


def test_stale_reporting_lists_only_the_well_formed_claims() -> None:
    event = _rco_event(message=f"RCO_PASS mentions {HEAD}", payload={"head": OTHER_HEAD, "exact_head": "not-a-sha"})
    result = check_rco_pass_present(events=[event], task_id=TASK, head=HEAD)
    assert result["has_stale_rco_pass_at_other_head"] is True
    assert result["stale_rco_pass_events"][0]["referenced_heads"] == [OTHER_HEAD]
