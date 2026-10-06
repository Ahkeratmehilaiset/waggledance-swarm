# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/bridge_rule12_review_eligibility.py (Rule 12, unwired)."""

from __future__ import annotations

import copy
import hashlib
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
NONCE = "c" * 32
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
    verdict = overrides.pop("verdict", "approve")
    answer = overrides.pop(
        "answer", ("APPROVE" if verdict == "approve" else verdict) + "\nFull review of 3 files."
    )
    consultation = {
        "request_id": "0123456789abcdef0123456789abcdef",
        "requester": "claude-rco-1",
        "task_id": TASK,
        "head": H,
        "slot": "opposite_family",
        "effort": "high",
        "prompt_sha256": "1" * 64,
        "input_sha256": DIFF,
        "answer_text": answer,
        "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        "nonce": NONCE,
        "coverage": {"complete": True, "files_total": 3, "files_reviewed": 3},
        "started_utc": "2026-10-06T16:40:00Z",
    }
    consultation.update(overrides)
    return consultation


def evaluate(events, contributors=FABLE_AUTHOR, consultations=(), **kw):
    kw.setdefault("now_utc", NOW)
    kw.setdefault("expected_diff_sha256", DIFF)
    kw.setdefault("expected_prompt_sha256", "1" * 64)
    kw.setdefault("expected_files_total", 3)
    kw.setdefault("expected_nonce", NONCE)
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
    result = evaluate([build_pass("codex-lead-1"), build_pass("codex-tools-1"), *BOTH_RCO,
                       build_pass("fable-5")], contributors)
    # GPT-authored: opposite family is Claude outside the RCO slot (fable-5); Lead's own pass
    # and Tools' same-family pass never count there.
    assert result["decision"] == "satisfied"
    assert result["slots"]["opposite_family"]["holders"] == ["fable-5"]
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
    assert slot["grok"]["answer_sha256"] == hashlib.sha256(
        b"APPROVE\nFull review of 3 files.").hexdigest()
    assert slot["grok"]["reviewer"] == "grok-scout-1"
    assert result["grok_fallback"]["filled"] == ["opposite_family"]
    assert "grok-scout-1" not in result["slots"]["rco"]["holders"]


def test_grok_fills_vacant_rco_slot_when_both_rcos_are_ineligible():
    contributors = [*FABLE_AUTHOR, {"agent": "claude-rco-2", "role": "design"}]
    # The requester is a pure relay: even the author may relay, it never supplies the verdict.
    consultation = grok(slot="rco", requester="fable-5")
    result = evaluate([build_pass("codex-tools-1"), recused("claude-rco-1")], contributors,
                      [consultation])
    assert result["decision"] == "satisfied"
    assert result["slots"]["rco"]["state"] == "held_by_grok_fallback"
    assert result["slots"]["rco"]["holders"] == []  # never recorded as an rco_pass


def test_two_vacant_slots_without_a_wholly_ineligible_pool_exceed_grok_max_one_slot():
    absent_rco2 = ev("codex-lead-1", "message", "requested", ts="2026-10-06T15:00:00Z",
                     to="claude-rco-2", request_id="lead-req")
    absent_rco2["agent"] = "claude-rco-1"
    events = [*GPT_RECUSED, recused("claude-rco-1"), absent_rco2]
    result = evaluate(events, consultations=[grok(slot="external_review")])
    assert result["grok_fallback"]["pool_standing"]["claude-rco-2"] == "absent"
    assert result["grok_fallback"]["whole_pool_ineligible"] is False
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
        {"prompt_sha256": "9" * 64},
        {"coverage": {"complete": True, "files_total": 1, "files_reviewed": 1}},
        {"started_utc": ""},
        {"started_utc": "later"},
        {"request_id": ""},
        {"request_id": "not-a-ledger-id"},
        {"nonce": ""},
        {"nonce": "e" * 32},
        {"answer_text": None},
        {"answer_text": "APPROVE\ntampered after hashing"},
        {"answer": "REJECT\nAPPROVE"},
        {"answer": "**REJECT** as a gate"},
        {"answer": "\n\n"},
        {"answer": "APPROVE, conditionally"},
        {"answer": "Looks fine. APPROVE"},
        {"requester": "grok-scout-1"},
        {"requester": "operator"},
    ],
)
def test_grok_answer_that_is_not_fully_bound_and_positive_leaves_slot_empty(overrides):
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[grok(**overrides)])
    assert result["decision"] == "not_satisfied"
    assert result["slots"]["opposite_family"]["state"] == "vacant"
    assert result["grok_fallback"]["filled"] == []


@pytest.mark.parametrize(
    "missing",
    [{"expected_diff_sha256": ""}, {"expected_prompt_sha256": ""}, {"expected_files_total": 0},
     {"expected_files_total": True}, {"expected_nonce": ""}, {"expected_nonce": "short"}],
)
def test_grok_requires_gate_computed_diff_prompt_file_count_and_nonce(missing):
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[grok()], **missing)
    assert result["decision"] == "not_satisfied"


def test_only_the_first_grok_answer_at_the_head_counts_no_answer_shopping():
    first = grok(verdict="reject", request_id="1" * 32, started_utc="2026-10-06T16:41:00Z")
    second = grok(request_id="2" * 32, started_utc="2026-10-06T16:42:00Z")
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[first, second])
    assert result["decision"] == "not_satisfied"
    slotless = grok(slot="", verdict="unclear", request_id="3" * 32,
                    started_utc="2026-10-06T16:41:00Z")
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[slotless, second])
    assert result["decision"] == "not_satisfied"
    other_head_first = grok(head=OLD, verdict="reject", request_id="4" * 32,
                            started_utc="2026-10-06T16:41:00Z")
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[other_head_first, second])
    assert result["decision"] == "satisfied"


def test_grok_that_authored_the_change_cannot_fill_a_slot():
    contributors = [*FABLE_AUTHOR, {"agent": "grok-scout-1", "role": "author"}]
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], contributors, [grok()])
    assert result["decision"] == "not_satisfied"
    assert result["grok_fallback"]["reasons"] == ["Grok is an implementer of this change"]


@pytest.mark.parametrize("role", ["concept", "design", "measurement"])
def test_grok_advice_or_read_only_review_does_not_make_it_ineligible(role):
    contributors = [*FABLE_AUTHOR, {"agent": "grok-scout-1", "role": role}]
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], contributors, [grok()])
    assert result["decision"] == "satisfied"
    assert result["implementers"] == ["fable-5"]


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
    events = [*GPT_RECUSED, *BOTH_RCO, ev("claude-rco-2", type_, status, ts="2026-10-06T16:55:00Z")]
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


def test_own_strictly_later_pass_at_head_clears_earlier_blocks_at_any_head():
    early = "2026-10-06T16:40:00Z"
    events = [build_pass("codex-lead-1"), ev("claude-rco-1", "finding", "changes_requested", ts=early),
              *BOTH_RCO, ev("claude-rco-2", "finding", "changes_requested", head=OLD, ts=early)]
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


# --- Grok self-challenge e17c4b1a findings (2026-10-06), each now a regression test ---


def test_later_review_request_does_not_erase_an_earlier_pass():
    later_request = [
        ev("claude-rco-1", "message", "requested", head=H, ts="2026-10-06T15:55:00Z",
           to="codex-lead-1,codex-tools-1,claude-rco-2", request_id="req-after-pass"),
        ev("codex-lead-1", "message", "requested", head=H, ts="2026-10-06T15:55:00Z",
           to="claude-rco-1", request_id="req-after-pass-2"),
    ]
    passes = [build_pass("codex-lead-1", ts="2026-10-06T15:50:00Z"),
              rco_pass("claude-rco-1", ts="2026-10-06T15:50:00Z"),
              rco_pass("claude-rco-2", ts="2026-10-06T15:50:00Z")]
    result = evaluate([*passes, *later_request], consultations=[grok()])
    assert result["slots"]["opposite_family"]["standing"]["codex-lead-1"] == "eligible"
    assert result["slots"]["rco"]["holders"] == ["claude-rco-1", "claude-rco-2"]
    assert result["grok_fallback"]["filled"] == []
    assert result["decision"] == "satisfied"


@pytest.mark.parametrize(
    "request_event",
    [
        ev("fable-5", "message", "requested", ts="2026-10-06T15:00:00Z", to="claude-rco-2",
           request_id="by-author"),
        ev("operator", "message", "requested", ts="2026-10-06T15:00:00Z", to="claude-rco-2",
           request_id="by-unknown"),
        ev("grok-scout-1", "message", "requested", ts="2026-10-06T15:00:00Z", to="claude-rco-2",
           request_id="by-grok"),
        ev("codex-lead-1", "message", "info", ts="2026-10-06T15:00:00Z", to="claude-rco-2",
           request_id="not-a-request"),
        ev("codex-lead-1", "message", "requested", ts="2026-10-06T15:00:00Z", to="claude-rco-2"),
    ],
)
def test_absence_cannot_be_forged_by_author_unknown_lane_or_non_request(request_event):
    result = evaluate([build_pass("codex-lead-1"), rco_pass("claude-rco-1"), request_event])
    assert result["slots"]["rco"]["standing"]["claude-rco-2"] == "eligible"
    assert result["decision"] == "not_satisfied"


def test_recusal_plus_pass_is_conflicting_counts_nothing_and_is_not_vacant():
    events = [build_pass("codex-lead-1"), rco_pass("claude-rco-1"), recused("claude-rco-1"),
              rco_pass("claude-rco-2")]
    result = evaluate(events)
    assert result["slots"]["rco"]["standing"]["claude-rco-1"] == "conflicting"
    assert result["slots"]["rco"]["missing_rco_pass"] == ["claude-rco-1"]
    assert result["decision"] == "not_satisfied"
    gpt = [build_pass("codex-lead-1"), *GPT_RECUSED, *BOTH_RCO]
    result = evaluate(gpt, consultations=[grok()])
    assert result["slots"]["opposite_family"]["state"] == "pending"
    assert result["grok_fallback"]["filled"] == []


def test_veto_order_is_read_from_timestamps_not_list_position():
    newer_block_listed_first = [
        build_pass("codex-lead-1"),
        ev("claude-rco-1", "finding", "changes_requested", ts="2026-10-06T16:55:00Z"),
        rco_pass("claude-rco-1", ts="2026-10-06T16:50:00Z"),
        rco_pass("claude-rco-2"),
    ]
    assert evaluate(newer_block_listed_first)["decision"] == "blocked"
    older_headless_block_listed_last = [
        build_pass("codex-lead-1"), *BOTH_RCO,
        ev("claude-rco-1", "finding", "changes_requested", head=None, ts="2026-10-06T16:40:00Z"),
    ]
    assert evaluate(older_headless_block_listed_last)["decision"] == "satisfied"


@pytest.mark.parametrize("pass_ts", ["bad", "2026-10-06T16:40:00Z"])
def test_pass_without_valid_or_later_timestamp_does_not_clear_a_block(pass_ts):
    events = [build_pass("codex-lead-1"), rco_pass("claude-rco-2"),
              ev("claude-rco-1", "finding", "changes_requested", ts="2026-10-06T16:40:00Z"),
              rco_pass("claude-rco-1", ts=pass_ts)]
    assert evaluate(events)["decision"] == "blocked"


def test_block_with_unparseable_timestamp_can_never_be_cleared():
    events = [build_pass("codex-lead-1"), *BOTH_RCO,
              ev("claude-rco-1", "finding", "changes_requested", ts="")]
    assert evaluate(events)["decision"] == "blocked"


def test_veto_at_another_head_sticks_through_new_head_recusal_or_absence():
    old_block = ev("claude-rco-2", "finding", "changes_requested", head=OLD,
                   ts="2026-10-06T15:00:00Z")
    events = [build_pass("codex-lead-1"), rco_pass("claude-rco-1"), old_block,
              recused("claude-rco-2")]
    assert evaluate(events)["decision"] == "blocked"
    cleared_at_old_head = [*events, rco_pass("claude-rco-2", head=OLD, ts="2026-10-06T15:30:00Z")]
    assert evaluate(cleared_at_old_head)["decision"] == "satisfied"


@pytest.mark.parametrize(
    "attempts",
    [
        [grok(slot="nope", request_id="5" * 32, started_utc="2026-10-06T16:41:00Z"),
         grok(verdict="reject", request_id="6" * 32, started_utc="2026-10-06T16:42:00Z")],
        [grok(request_id="7" * 32, started_utc="2026-10-06T16:43:00Z"),
         grok(verdict="reject", request_id="8" * 32, started_utc="2026-10-06T16:42:00Z")],
        [grok(request_id="9" * 32, started_utc="2026-10-06T16:42:00Z"),
         grok(verdict="unclear", request_id="a" * 32, started_utc="2026-10-06T16:42:00Z")],
        [grok(request_id="b" * 32), grok(slot="nope", request_id="c" * 32, started_utc="")],
    ],
)
def test_mistagged_out_of_order_tied_or_untimed_grok_attempts_cannot_count(attempts):
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=attempts)
    assert result["grok_fallback"]["filled"] == []
    assert result["decision"] == "not_satisfied"


def test_grok_attempt_tagged_for_the_other_slot_does_not_spend_this_slot():
    contributors = [*FABLE_AUTHOR]
    other_slot_first = grok(slot="rco", verdict="reject", request_id="d" * 32,
                            started_utc="2026-10-06T16:30:00Z")
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], contributors, [other_slot_first, grok()])
    assert result["decision"] == "satisfied"


@pytest.mark.parametrize("status", ["build_consensus_pass", "autonomous_merge_receipt",
                                    "rco_closed_postmerge"])
def test_rco_approval_or_tooling_record_status_is_not_a_veto(status):
    events = [build_pass("codex-lead-1"), *BOTH_RCO, ev("claude-rco-1", "decision", status,
                                                         ts="2026-10-06T16:55:00Z")]
    assert evaluate(events)["decision"] == "satisfied"


def test_rco_build_consensus_pass_never_doubles_as_opposite_family_approval():
    contributors = [{"agent": "codex-tools-1", "role": "author"}]
    events = [ev("claude-rco-1", "decision", "build_consensus_pass"), *BOTH_RCO]
    result = evaluate(events, contributors)
    assert result["decision"] == "not_satisfied"  # RCO1 F1: needs a distinct identity
    assert result["slots"]["opposite_family"]["holders"] == []

# --- Lead request v2 (2026-10-06 17:01Z): Fable before Grok, Grok advice is not
# implementation, whole-pool external review, pure relay, original answer ---

ALL_POOL_RECUSED = [*GPT_RECUSED, recused("claude-rco-1"), recused("claude-rco-2")]


def test_whole_pool_ineligible_grok_external_review_covers_the_review():
    result = evaluate(ALL_POOL_RECUSED, consultations=[grok(slot="external_review")])
    assert result["grok_fallback"]["whole_pool_ineligible"] is True
    assert result["decision"] == "satisfied"
    assert result["grok_fallback"]["filled"] == ["external_review"]
    record = result["grok_fallback"]["external_review"]
    assert record["reviewer"] == "grok-scout-1"
    assert record["nonce"] == NONCE
    for name in ("opposite_family", "rco"):
        assert result["slots"][name]["state"] == "held_by_grok_external_review"
        assert result["slots"][name]["holders"] == []  # no fabricated agents or rco_pass


def test_whole_pool_ineligible_by_implementation_and_recusal_mix():
    contributors = [*FABLE_AUTHOR, {"agent": "claude-rco-2", "role": "design"},
                    {"agent": "codex-tools-1", "role": "measurement"}]
    events = [recused("codex-lead-1"), recused("claude-rco-1")]
    result = evaluate(events, contributors, [grok(slot="external_review", requester="fable-5")])
    assert result["decision"] == "satisfied"
    assert result["grok_fallback"]["external_review"]["requester_relay"] == "fable-5"


@pytest.mark.parametrize(
    "overrides",
    [
        {"head": OLD},
        {"verdict": "REJECT"},
        {"answer": "APPROVE with conditions"},
        {"coverage": {"complete": True, "files_total": 3, "files_reviewed": 2}},
        {"nonce": "e" * 32},
        {"input_sha256": "e" * 64},
        {"answer_text": "APPROVE\nedited by the relay"},
        {"requester": "grok-scout-1"},
    ],
)
def test_whole_pool_external_review_rejects_wrong_binding_partial_or_negative(overrides):
    result = evaluate(ALL_POOL_RECUSED, consultations=[grok(slot="external_review", **overrides)])
    assert result["decision"] == "not_satisfied"
    assert result["grok_fallback"]["filled"] == []


def test_whole_pool_external_review_counts_only_the_first_attempt_at_the_head_of_any_tag():
    earlier = grok(slot="opposite_family", verdict="REJECT", request_id="1" * 32,
                   started_utc="2026-10-06T16:30:00Z")
    later = grok(slot="external_review", request_id="2" * 32)
    result = evaluate(ALL_POOL_RECUSED, consultations=[later, earlier])
    assert result["decision"] == "not_satisfied"


def test_grok_that_authored_the_change_cannot_review_even_when_the_pool_is_ineligible():
    contributors = [*FABLE_AUTHOR, {"agent": "grok-scout-1", "role": "author"}]
    result = evaluate(ALL_POOL_RECUSED, contributors, [grok(slot="external_review")])
    assert result["decision"] == "not_satisfied"
    assert result["grok_fallback"]["reasons"] == ["Grok is an implementer of this change"]


def test_rco_veto_still_outranks_a_whole_pool_external_review():
    events = [*ALL_POOL_RECUSED, ev("claude-rco-1", "finding", "changes_requested",
                                    ts="2026-10-06T16:55:00Z")]
    result = evaluate(events, consultations=[grok(slot="external_review")])
    assert result["decision"] == "blocked"


@pytest.mark.parametrize("role", ["concept", "design", "measurement"])
def test_grok_advice_or_choice_does_not_block_the_whole_pool_external_review(role):
    contributors = [*FABLE_AUTHOR, {"agent": "grok-scout-1", "role": role}]
    result = evaluate(ALL_POOL_RECUSED, contributors, [grok(slot="external_review")])
    assert result["decision"] == "satisfied"


def test_eligible_fable_comes_before_grok_for_gpt_authored_work():
    contributors = [{"agent": "codex-lead-1", "role": "author"},
                    {"agent": "codex-tools-1", "role": "design"}]
    events = [recused("claude-rco-1"), recused("claude-rco-2")]
    silent_fable = evaluate(events, contributors,
                            [grok(slot="opposite_family"), grok(slot="rco", request_id="f" * 32,
                                                                started_utc="2026-10-06T16:41:00Z")])
    assert silent_fable["slots"]["opposite_family"]["standing"]["fable-5"] == "eligible"
    assert silent_fable["slots"]["opposite_family"]["state"] == "pending"
    assert "opposite_family" not in silent_fable["grok_fallback"]["filled"]
    assert silent_fable["grok_fallback"]["whole_pool_ineligible"] is False
    assert silent_fable["decision"] == "not_satisfied"
    fable_reviews = evaluate([*events, build_pass("fable-5")], contributors,
                             [grok(slot="rco", request_id="f" * 32)])
    assert fable_reviews["slots"]["opposite_family"]["holders"] == ["fable-5"]
    assert fable_reviews["slots"]["rco"]["state"] == "held_by_grok_fallback"
    assert fable_reviews["decision"] == "satisfied"


def test_fable_is_not_a_recognized_rco():
    result = evaluate([build_pass("codex-lead-1"), rco_pass("claude-rco-1"), rco_pass("fable-5")])
    assert result["slots"]["rco"]["missing_rco_pass"] == ["claude-rco-2"]
    assert "fable-5" not in result["slots"]["rco"]["standing"]


@pytest.mark.parametrize("requester", ["fable-5", "codex-lead-1", "claude-rco-1"])
def test_ineligible_pure_relay_may_request_but_never_supplies_the_verdict(requester):
    ok = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[grok(requester=requester)])
    assert ok["decision"] == "satisfied"
    assert ok["slots"]["opposite_family"]["grok"]["requester_relay"] == requester
    relay_says_approve = grok(requester=requester, verdict="REJECT")
    relay_says_approve["verdict"] = "approve"
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[relay_says_approve])
    assert result["decision"] == "not_satisfied"


@pytest.mark.parametrize("answer", ["APPROVE", "**APPROVE**\nreasons", "# APPROVE.\n", "\n  APPROVE  \nok"])
def test_exact_approve_first_line_in_the_original_answer_counts(answer):
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[grok(answer=answer)])
    assert result["decision"] == "satisfied"

# --- RCO1 review 548FE5A5 on 0aabaaab: F1 one identity must never hold both slots ---


def test_rco_in_the_rco_slot_cannot_also_hold_the_opposite_family_slot():
    contributors = [{"agent": "codex-tools-1", "role": "author"}]
    events = [recused("claude-rco-2"), rco_pass("claude-rco-1")]
    result = evaluate(events, contributors)
    assert result["slots"]["rco"]["holders"] == ["claude-rco-1"]
    assert "claude-rco-1" not in result["slots"]["opposite_family"]["standing"]
    assert result["slots"]["opposite_family"]["state"] == "pending"  # fable-5 is still eligible
    assert result["decision"] == "not_satisfied"
    with_fable = evaluate([*events, build_pass("fable-5")], contributors)
    assert with_fable["slots"]["opposite_family"]["holders"] == ["fable-5"]
    assert with_fable["decision"] == "satisfied"


def test_rco_build_consensus_pass_does_not_double_as_opposite_family_approval():
    contributors = [{"agent": "codex-lead-1", "role": "author"}]
    events = [recused("claude-rco-2"), rco_pass("claude-rco-1"),
              ev("claude-rco-1", "decision", "build_consensus_pass"), recused("fable-5")]
    result = evaluate(events, contributors, [grok(request_id="f" * 32)])
    # With rco-1 in the RCO slot and fable-5 recused the opposite slot is vacant:
    # only a distinct identity (here the bound Grok fallback) can hold it.
    assert result["slots"]["opposite_family"]["state"] == "held_by_grok_fallback"
    assert result["decision"] == "satisfied"
    without_grok = evaluate(events, contributors)
    assert without_grok["decision"] == "not_satisfied"


def test_slot_holders_are_always_distinct_identities():
    contributors = [{"agent": "codex-tools-1", "role": "author"}]
    events = [rco_pass("claude-rco-1"), rco_pass("claude-rco-2"), build_pass("fable-5")]
    result = evaluate(events, contributors)
    assert result["decision"] == "satisfied"
    assert not set(result["slots"]["rco"]["holders"]) & set(
        result["slots"]["opposite_family"]["holders"])

# --- Tools T1 (17:22:21Z on 0aabaaab): positive evidence must be timed and not after the gate clock ---

FUTURE = "2026-10-07T17:00:00Z"
EQUAL = NOW


@pytest.mark.parametrize("ts,counts", [("bad", False), ("", False), (FUTURE, False),
                                       (EQUAL, True), ("2026-10-06T16:59:59Z", True)])
def test_approval_counts_only_with_a_valid_timestamp_not_after_the_gate_clock(ts, counts):
    events = [build_pass("codex-lead-1", ts=ts), rco_pass("claude-rco-1"), rco_pass("claude-rco-2")]
    result = evaluate(events)
    assert (result["decision"] == "satisfied") is counts
    events = [build_pass("codex-lead-1"), rco_pass("claude-rco-1", ts=ts), rco_pass("claude-rco-2")]
    result = evaluate(events)
    assert (result["decision"] == "satisfied") is counts


@pytest.mark.parametrize("pass_ts,cleared", [("bad", False), (FUTURE, False), ("2026-10-06T16:30:00Z", False),
                                             (EQUAL, True), ("2026-10-06T16:45:00Z", True)])
def test_future_invalid_or_not_later_pass_never_clears_an_active_rco_block(pass_ts, cleared):
    block = ev("claude-rco-1", "finding", "changes_requested", ts="2026-10-06T16:30:00Z")
    events = [build_pass("codex-lead-1"), rco_pass("claude-rco-2"), block,
              rco_pass("claude-rco-1", ts=pass_ts)]
    result = evaluate(events)
    assert result["decision"] == ("satisfied" if cleared else "blocked")


def test_future_dated_block_still_blocks():
    events = [build_pass("codex-lead-1"), *BOTH_RCO,
              ev("claude-rco-2", "finding", "changes_requested", ts=FUTURE)]
    assert evaluate(events)["decision"] == "blocked"


@pytest.mark.parametrize("ts,recused_ok", [("bad", False), (FUTURE, False), (EQUAL, True)])
def test_recusal_needs_a_valid_timestamp_not_after_the_gate_clock(ts, recused_ok):
    events = [recused("codex-lead-1", ts=ts), recused("codex-tools-1"), *BOTH_RCO]
    result = evaluate(events, consultations=[grok()])
    standing = result["slots"]["opposite_family"]["standing"]["codex-lead-1"]
    assert standing == ("recused" if recused_ok else "eligible")
    assert (result["decision"] == "satisfied") is recused_ok


@pytest.mark.parametrize("started,ok", [(FUTURE, False), (EQUAL, True)])
def test_grok_attempt_dated_after_the_gate_clock_never_qualifies(started, ok):
    result = evaluate([*GPT_RECUSED, *BOTH_RCO], consultations=[grok(started_utc=started)])
    assert (result["decision"] == "satisfied") is ok