# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/bridge_rule12_review_eligibility.py (Rule 12, unwired)."""

from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import bridge_rule12_review_eligibility as r12  # noqa: E402

TASK = "fable-5/example-change-20261006"
H = "a" * 40
OLD = "b" * 40
DIFF = "d" * 64
NOW = "2026-10-06T17:00:00.0000000Z"
FABLE_AUTHOR = [{"agent": "fable-5", "role": "author"}]


def ev(agent, type_, status, head=H, task=TASK, ts="2026-10-06T16:50:00Z", **extra):
    event = {"agent": agent, "type": type_, "status": status, "task_id": task, "ts_utc": ts}
    if head is not None:
        event["payload"] = {"head": head}
    event.update(extra)
    return event


def rco_pass(agent, head=H, **kw):
    return ev(agent, "decision", "rco_pass", head=head, **kw)


def build_pass(agent, head=H, **kw):
    return ev(agent, "decision", "build_consensus_pass", head=head, **kw)


def recused(agent, head=H, **kw):
    return ev(agent, "message", "rco_recused", head=head, **kw)


def grok(**overrides):
    consultation = {
        "request_id": "0123456789abcdef0123456789abcdef",
        "requester": "claude-rco-1",
        "task_id": TASK,
        "head": H,
        "slot": "opposite_family",
        "effort": "high",
        "prompt_sha256": "1" * 64,
        "input_sha256": DIFF,
        "answer_sha256": "2" * 64,
        "coverage": {"complete": True, "files_total": 3, "files_reviewed": 3},
        "verdict": "approve",
    }
    consultation.update(overrides)
    return consultation


def evaluate(events, contributors=FABLE_AUTHOR, consultations=(), **kw):
    kw.setdefault("now_utc", NOW)
    kw.setdefault("expected_diff_sha256", DIFF)
    return r12.evaluate_rule12_review_eligibility(
        task_id=kw.pop("task_id", TASK),
        head=kw.pop("head", H),
        contributors=contributors,
        events=events,
        grok_consultations=consultations,
        **kw,
    )


GPT_RECUSED = [recused("codex-lead-1"), ev("codex-tools-1", "message", "review_recused")]
BOTH_RCO = [rco_pass("claude-rco-1"), rco_pass("claude-rco-2")]


def test_claude_authored_needs_gpt_approval_and_both_eligible_rcos():
    result = evaluate([build_pass("codex-lead-1"), *BOTH_RCO])
    assert result["decision"] == "satisfied"
    assert result["slots"]["opposite_family"]["holders"] == ["codex-lead-1"]
    assert result["slots"]["rco"]["holders"] == ["claude-rco-1", "claude-rco-2"]
    assert result["wired"] is False


def test_missing_one_eligible_rco_pass_is_not_satisfied():
    result = evaluate([build_pass("codex-lead-1"), rco_pass("claude-rco-1")])
    assert result["decision"] == "not_satisfied"
    assert result["slots"]["rco"]["missing_rco_pass"] == ["claude-rco-2"]


@pytest.mark.parametrize("role", ["author", "concept", "design", "measurement"])
def test_rco_that_is_an_implementer_is_ineligible_and_its_pass_does_not_count(role):
    contributors = [*FABLE_AUTHOR, {"agent": "claude-rco-2", "role": role}]
    result = evaluate([build_pass("codex-lead-1"), rco_pass("claude-rco-1")], contributors)
    assert result["decision"] == "satisfied"
    assert result["slots"]["rco"]["standing"]["claude-rco-2"] == "implementer"
    only_self = evaluate([build_pass("codex-lead-1"), rco_pass("claude-rco-2")], contributors)
    assert only_self["decision"] == "not_satisfied"


def test_wrong_head_approvals_do_not_count():
    result = evaluate([build_pass("codex-lead-1", head=OLD), rco_pass("claude-rco-1", head=OLD),
                       rco_pass("claude-rco-2", head=OLD)])
    assert result["decision"] == "not_satisfied"
    assert result["slots"]["rco"]["state"] == "pending"
    assert result["slots"]["opposite_family"]["state"] == "pending"


def test_approvals_on_another_task_or_without_head_do_not_count():
    other = "codex-lead-1/other-task"
    result = evaluate([build_pass("codex-lead-1", task=other), rco_pass("claude-rco-1", head=None),
                       rco_pass("claude-rco-2", task=other)])
    assert result["decision"] == "not_satisfied"


def test_conflicting_structured_heads_bind_nothing():
    event = rco_pass("claude-rco-1")
    event["payload"]["exact_head"] = OLD
    result = evaluate([build_pass("codex-lead-1"), event, rco_pass("claude-rco-2")])
    assert result["slots"]["rco"]["missing_rco_pass"] == ["claude-rco-1"]


def test_author_cannot_approve_its_own_change():
    contributors = [{"agent": "codex-lead-1", "role": "author"}]
    result = evaluate([build_pass("codex-lead-1"), build_pass("codex-tools-1"), *BOTH_RCO],
                      contributors)
    # GPT-authored: opposite family is Claude; the RCO passes count there, Lead's own does not.
    assert result["decision"] == "satisfied"
    assert "codex-lead-1" not in result["slots"]["opposite_family"]["standing"]
    gpt_only = evaluate([build_pass("codex-lead-1"), build_pass("codex-tools-1")], contributors)
    assert gpt_only["decision"] == "not_satisfied"


def test_same_family_only_approvals_are_not_satisfied_and_grok_cannot_replace_present_primaries():
    result = evaluate([build_pass("fable-5"), *BOTH_RCO], consultations=[grok()])
    assert result["decision"] == "not_satisfied"
    assert result["slots"]["opposite_family"]["state"] == "pending"
    assert result["grok_fallback"]["filled"] == []


@pytest.mark.parametrize("status", ["medium", "effort_medium", "low_effort", "busy", "recused"])
def test_medium_effort_or_other_status_is_not_a_recusal(status):
    events = [ev("codex-tools-1", "message", status), ev("codex-lead-1", "message", status), *BOTH_RCO]
    result = evaluate(events, consultations=[grok()])
    assert result["slots"]["opposite_family"]["standing"]["codex-tools-1"] == "eligible"
    assert result["decision"] == "not_satisfied"
    assert result["grok_fallback"]["filled"] == []


def test_recusal_must_be_self_posted_and_bound_to_task_and_head():
    events = [
        ev("codex-lead-1", "message", "rco_recused", message="codex-tools-1 recused"),
        recused("codex-tools-1", head=OLD),
        recused("codex-tools-1", task="fable-5/other"),
        ev("codex-tools-1", "decision", "rco_recused"),  # wrong type: not a recusal
        *BOTH_RCO,
    ]
    result = evaluate(events, consultations=[grok()])
    assert result["slots"]["opposite_family"]["standing"]["codex-tools-1"] == "eligible"
    assert result["slots"]["opposite_family"]["standing"]["codex-lead-1"] == "recused"
    assert result["decision"] == "not_satisfied"


def test_grok_fills_vacant_opposite_slot_when_lead_and_tools_are_recused():
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[grok()])
    assert result["decision"] == "satisfied"
    slot = result["slots"]["opposite_family"]
    assert slot["state"] == "held_by_grok_fallback"
    assert slot["grok"]["request_id"] == "0123456789abcdef0123456789abcdef"
    assert slot["grok"]["answer_sha256"] == "2" * 64
    assert result["grok_fallback"]["filled"] == ["opposite_family"]
    assert "grok-scout-1" not in result["slots"]["rco"]["holders"]


def test_grok_fills_vacant_rco_slot_when_both_rcos_are_ineligible():
    contributors = [*FABLE_AUTHOR, {"agent": "claude-rco-2", "role": "design"}]
    consultation = grok(slot="rco", requester="fable-5")
    result = evaluate([build_pass("codex-tools-1"), recused("claude-rco-1")], contributors,
                      [consultation])
    assert result["decision"] == "not_satisfied"  # requester fable-5 is the author
    consultation = grok(slot="rco", requester="codex-lead-1")
    result = evaluate([build_pass("codex-tools-1"), recused("claude-rco-1")], contributors,
                      [consultation])
    assert result["decision"] == "satisfied"
    assert result["slots"]["rco"]["state"] == "held_by_grok_fallback"
    assert "holders" not in result["slots"]["rco"]  # never recorded as an rco_pass


def test_two_vacant_slots_exceed_grok_max_one_slot():
    events = [*GPT_RECUSED, recused("claude-rco-1"), recused("claude-rco-2")]
    result = evaluate(events, consultations=[grok(), grok(slot="rco", request_id="f" * 32)])
    assert result["decision"] == "not_satisfied"
    assert result["grok_fallback"]["filled"] == []
    assert "at most 1" in result["grok_fallback"]["reasons"][0]


@pytest.mark.parametrize(
    "overrides",
    [
        {"head": OLD},
        {"task_id": "fable-5/other"},
        {"verdict": "reject"},
        {"verdict": ""},
        {"verdict": "APPROVE with concerns"},
        {"effort": "medium"},
        {"effort": ""},
        {"coverage": {"complete": False, "files_total": 3, "files_reviewed": 2}},
        {"coverage": {"complete": True, "files_total": 3, "files_reviewed": 2}},
        {"coverage": {"complete": True, "files_total": 0, "files_reviewed": 0}},
        {"coverage": {"complete": True, "files_total": True, "files_reviewed": True}},
        {"coverage": None},
        {"input_sha256": "e" * 64},
        {"answer_sha256": ""},
        {"prompt_sha256": "XYZ"},
        {"request_id": ""},
        {"request_id": "not-a-ledger-id"},
        {"requester": "fable-5"},
        {"requester": "codex-lead-1"},
        {"requester": "grok-scout-1"},
        {"requester": "operator"},
    ],
)
def test_grok_answer_that_is_not_fully_bound_and_positive_leaves_slot_empty(overrides):
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[grok(**overrides)])
    assert result["decision"] == "not_satisfied"
    assert result["slots"]["opposite_family"]["state"] == "vacant"
    assert result["grok_fallback"]["filled"] == []


def test_grok_requires_the_expected_exact_head_diff_hash():
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[grok()], expected_diff_sha256="")
    assert result["decision"] == "not_satisfied"


def test_only_the_first_grok_answer_at_the_head_counts_no_answer_shopping():
    first = grok(verdict="reject", request_id="1" * 32)
    second = grok(request_id="2" * 32)
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[first, second])
    assert result["decision"] == "not_satisfied"
    slotless = grok(slot="", verdict="unclear", request_id="3" * 32)
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[slotless, second])
    assert result["decision"] == "not_satisfied"
    other_head_first = grok(head=OLD, verdict="reject", request_id="4" * 32)
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[other_head_first, second])
    assert result["decision"] == "satisfied"


def test_grok_that_implemented_the_change_cannot_fill_a_slot():
    contributors = [*FABLE_AUTHOR, {"agent": "grok-scout-1", "role": "concept"}]
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], contributors, [grok()])
    assert result["decision"] == "not_satisfied"


@pytest.mark.parametrize("agent", ["grok-scout-1", "fable-5", "codex-lead-1"])
def test_fabricated_rco_pass_from_unrecognized_agent_never_counts(agent):
    result = evaluate([build_pass("codex-lead-1"), rco_pass("claude-rco-1"), rco_pass(agent)])
    assert result["slots"]["rco"]["missing_rco_pass"] == ["claude-rco-2"]
    assert result["decision"] == "not_satisfied"


@pytest.mark.parametrize(
    "late_block",
    [
        ("finding", "changes_requested"),
        ("finding", "rco_pass"),
        ("blocked", "acknowledged"),
        ("decision", "rco_pass_withheld"),
        ("decision", "not_approved"),
        ("rco_review", "hold"),
    ],
)
def test_late_rco_block_after_pass_outranks_every_approval_including_grok(late_block):
    type_, status = late_block
    events = [*GPT_RECUSED, *BOTH_RCO, ev("claude-rco-2", type_, status)]
    result = evaluate(events, consultations=[grok()])
    assert result["decision"] == "blocked"
    assert result["blocking_rcos"] == ["claude-rco-2"]


def test_unbound_or_malformed_head_rco_block_blocks():
    for block in (ev("claude-rco-1", "finding", "changes_requested", head=None),
                  ev("claude-rco-1", "finding", "changes_requested", head="xyz")):
        result = evaluate([build_pass("codex-lead-1"), *BOTH_RCO, block])
        assert result["decision"] == "blocked"


def test_block_from_recused_or_implementer_rco_still_blocks_and_recusal_never_clears_it():
    contributors = [*FABLE_AUTHOR, {"agent": "claude-rco-2", "role": "design"}]
    events = [build_pass("codex-lead-1"), rco_pass("claude-rco-1"),
              ev("claude-rco-2", "finding", "changes_requested"), recused("claude-rco-2")]
    assert evaluate(events, contributors)["decision"] == "blocked"


def test_own_later_pass_at_head_clears_earlier_block_and_old_head_block_is_superseded():
    events = [build_pass("codex-lead-1"), ev("claude-rco-1", "finding", "changes_requested"),
              *BOTH_RCO, ev("claude-rco-2", "finding", "changes_requested", head=OLD)]
    assert evaluate(events)["decision"] == "satisfied"


def test_other_rco_pass_never_clears_a_veto():
    events = [build_pass("codex-lead-1"), ev("claude-rco-2", "finding", "changes_requested"),
              rco_pass("claude-rco-1"), rco_pass("claude-rco-1")]
    assert evaluate(events)["decision"] == "blocked"


def request_to(agent, ts, head=H):
    return ev("codex-lead-1", "message", "requested", head=head, ts=ts, to=agent,
              request_id="lead-req-1")


def test_primary_absent_after_60_minutes_without_answer_is_not_required():
    events = [build_pass("codex-lead-1"), request_to("claude-rco-2", "2026-10-06T15:59:00Z"),
              rco_pass("claude-rco-1")]
    result = evaluate(events)
    assert result["slots"]["rco"]["standing"]["claude-rco-2"] == "absent"
    assert result["decision"] == "satisfied"


@pytest.mark.parametrize("variant", ["too_recent", "answered", "other_head", "bad_ts"])
def test_primary_is_not_absent_without_a_full_unanswered_hour_at_the_head(variant):
    ts = "2026-10-06T16:01:00Z" if variant == "too_recent" else "2026-10-06T15:59:00Z"
    if variant == "bad_ts":
        ts = "yesterday"
    events = [build_pass("codex-lead-1"),
              request_to("claude-rco-2", ts, head=OLD if variant == "other_head" else H),
              rco_pass("claude-rco-1")]
    if variant == "answered":
        events.append(ev("claude-rco-2", "message", "info", message="reviewing"))
    result = evaluate(events)
    assert result["slots"]["rco"]["standing"]["claude-rco-2"] == "eligible"
    assert result["decision"] == "not_satisfied"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"head": "A" * 40},
        {"head": "abc"},
        {"task_id": " "},
        {"now_utc": "now"},
        {"contributors": []},
        {"contributors": [{"agent": "operator", "role": "author"}]},
        {"contributors": [{"agent": "fable-5", "role": "reviewer"}]},
    ],
)
def test_invalid_input_is_refused(kwargs):
    contributors = kwargs.pop("contributors", FABLE_AUTHOR)
    result = evaluate([build_pass("codex-lead-1"), *BOTH_RCO], contributors, **kwargs)
    assert result["decision"] == "refused"
    assert result["reasons"]


def test_inputs_are_not_mutated():
    events = [*GPT_RECUSED, *BOTH_RCO]
    consultations = [grok()]
    snapshot = copy.deepcopy((events, consultations))
    evaluate(events, consultations=consultations)
    assert (events, consultations) == snapshot


def test_drift_guard_recognized_rcos_and_pass_statuses_match_the_gates():
    from tools.check_promotion_eligible import DEFAULT_RCO_AGENTS
    from tools.check_rco_pass_present import RCO_PASS_STATUSES

    assert tuple(r12.RECOGNIZED_RCOS) == tuple(DEFAULT_RCO_AGENTS)
    assert r12.RCO_PASS_STATUSES == RCO_PASS_STATUSES
    assert set(r12.RECOGNIZED_RCOS) <= set(r12.FAMILIES)
