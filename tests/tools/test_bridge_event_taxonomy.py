# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import pytest

from tools.bridge_event_taxonomy import classify, normalize_raw

TASK = "wd/some-task"
HEAD = "a" * 40
OLDHEAD = "b" * 40
RCOS = ("claude-rco-1", "claude-rco-2")


def ev(type, status="", *, head=HEAD, who="claude-rco-1", ts="2026-06-24T10:00:00Z",
       task=TASK) -> dict:
    return {"type": type, "decision_status": status, "head_sha": head,
            "author_identity": who, "task_id": task, "ts_utc": ts}


def verdict(events, head=HEAD):
    return classify(events, task_id=TASK, head_sha=head, recognized_rcos=RCOS)


# --- §1.1  negation fail-open (#1368) ----------------------------------------

def test_negated_resolution_token_does_not_clear_block():
    # changes_requested then a NEGATED "resolution" -> exact-enum miss -> UNKNOWN
    # -> still blocked (a substring-presence classifier would wrongly clear it).
    evs = [ev("decision", "changes_requested", ts="t1"),
           ev("decision", "changes_requested_not_resolved", ts="t2")]
    assert verdict(evs).clear_to_merge is False


@pytest.mark.parametrize("neg", [
    "block_not_resolved", "not_yet_cleared", "changes_requested_not_resolved",
    "not_no_changes_requested", "rco_pass_revoked", "unresolved",
])
def test_negated_forms_fail_closed(neg):
    assert verdict([ev("decision", neg)]).clear_to_merge is False


# --- §1.2  resolution token must actually clear -------------------------------

@pytest.mark.parametrize("clear", [
    "changes_requested_resolved", "changes_requested_cleared", "block_retracted",
])
def test_genuine_clear_clears_same_identity_block(clear):
    evs = [ev("decision", "changes_requested", ts="t1"),
           ev("decision", clear, ts="t2")]
    assert verdict(evs).clear_to_merge is True


# --- §1.3 / §1.4 / §1.5  non-authoritative types can never veto ---------------

@pytest.mark.parametrize("typ", ["message", "handoff", "wake_request", "heartbeat",
                                 "status", "intent", "claim"])
def test_non_authoritative_type_cannot_block(typ):
    # status NAME literally contains block vocabulary, but the TYPE is non-authoritative
    assert verdict([ev(typ, "changes_requested_blocked_concurrence")]).clear_to_merge is True


def test_message_cannot_approve_either():
    v = verdict([ev("message", "rco_pass")])
    assert v.clear_to_merge is True and v.approvals == []


# --- §1.6  stale block auto-expires on head advance --------------------------

def test_block_at_old_head_auto_expires():
    # RCO finding bound to OLDHEAD; current head advanced -> expired -> clear.
    assert verdict([ev("finding", "finding", head=OLDHEAD)], head=HEAD).clear_to_merge is True


def test_fresh_block_at_current_head_blocks():
    assert verdict([ev("finding", "finding", head=HEAD)], head=HEAD).clear_to_merge is False


# --- unknown status -> fail-closed -------------------------------------------

def test_unknown_authoritative_status_fails_closed():
    assert verdict([ev("decision", "frobnicate")]).clear_to_merge is False


# --- malformed block (no head) fails closed; approval needs head --------------

def test_block_with_no_head_fails_closed():
    assert verdict([ev("decision", "changes_requested", head="")]).clear_to_merge is False


def test_approval_with_no_head_does_not_count():
    v = verdict([ev("decision", "rco_pass", head="")])
    assert v.clear_to_merge is True and v.approvals == []  # no block, but no valid approval


def test_approval_at_old_head_does_not_count():
    v = verdict([ev("decision", "rco_pass", head=OLDHEAD)], head=HEAD)
    assert v.approvals == []


# --- veto outranks pass; latest-per-identity ---------------------------------

def test_veto_outranks_pass_across_identities():
    evs = [ev("decision", "rco_pass", who="claude-rco-2"),
           ev("finding", "finding", who="claude-rco-1")]
    assert verdict(evs).clear_to_merge is False


def test_latest_per_identity_clear_supersedes_earlier_block():
    evs = [ev("decision", "changes_requested", who="claude-rco-1", ts="t1"),
           ev("decision", "rco_pass", who="claude-rco-1", ts="t2")]
    assert verdict(evs).clear_to_merge is True


def test_latest_per_identity_block_supersedes_earlier_pass():
    evs = [ev("decision", "rco_pass", who="claude-rco-1", ts="t1"),
           ev("decision", "changes_requested", who="claude-rco-1", ts="t2")]
    assert verdict(evs).clear_to_merge is False


# --- finding from a NON-recognized identity is not authoritative --------------

def test_finding_from_unrecognized_identity_does_not_block():
    assert verdict([ev("finding", "finding", who="random-agent")]).clear_to_merge is True


# --- positive: clean approvals, no block -------------------------------------

def test_clean_dual_approval_clears():
    evs = [ev("decision", "build_consensus_pass", who="codex-lead-1"),
           ev("decision", "rco_pass", who="claude-rco-1")]
    v = verdict(evs)
    assert v.clear_to_merge is True and len(v.approvals) == 2


def test_empty_is_clear_no_approvals():
    v = verdict([])
    assert v.clear_to_merge is True and v.approvals == []


def test_other_task_events_ignored():
    assert verdict([ev("finding", "finding", task="other/task")]).clear_to_merge is True


# --- live-authority reconciliation (rco-1/rco-2 #1387 §2.1 + emit-audit) ------
# Evidence-based (rco-1 emit-audit over 65,440 events): blocked PRESERVED (38 active
# merge-vetoes); rco_review/test/done DROPPED as deliberate, documented tightenings
# (safe direction only — a dropped clear leaves blocks standing, never drops a veto).

def test_blocked_type_preserved_as_veto():
    # blocked = the live merge-veto type with 38 active instances -> MUST block
    # (the real loosening rco-1 caught; dropping it would lose live vetoes).
    assert verdict([ev("blocked", "merge_blocked_operator_or_driver",
                       who="codex-lead-1")]).clear_to_merge is False


def test_blocked_blocks_by_type_regardless_of_status():
    assert verdict([ev("blocked", "", who="codex-tools-1")]).clear_to_merge is False


@pytest.mark.parametrize("typ,status", [
    ("rco_review", "changes_requested"),     # 0 emits ever; RCO veto goes via finding
    ("test", "preflight_blocked"),           # CI noise must not be a bridge gate signal
    ("test", "pass_with_review_blocker"),
    ("done", "task_done"),                    # done dropped from CLEAR authority
])
def test_deliberate_tightening_types_non_authoritative(typ, status):
    # these live types are intentionally non-authoritative in V1 (documented,
    # safe-direction tightenings) -> a lone such event neither blocks nor approves.
    assert verdict([ev(typ, status, who="claude-rco-1")]).clear_to_merge is True


@pytest.mark.parametrize(
    "status",
    [
        "no_changes_requested",
        "no_changes_requested_approved",
        "changes_requested_resolved",
        "changes_requested_cleared",
    ],
)
def test_recognized_rco_finding_vetoes_by_type(status):
    # A recognized RCO finding is a veto by type; approval/clear-looking status
    # text cannot turn that authoritative finding into an enabling event.
    assert verdict([ev("finding", status, who="claude-rco-1")]).clear_to_merge is False


def test_done_cannot_clear_a_standing_decision_block():
    # done is no longer CLEAR-authoritative -> a 'done' leaves a real block STANDING
    # (safe tightening; a genuine clear must come via decision/changes_requested_resolved).
    evs = [ev("decision", "changes_requested", who="claude-rco-1", ts="t1"),
           ev("done", "changes_requested_resolved", who="claude-rco-1", ts="t2")]
    assert verdict(evs).clear_to_merge is False


def test_neutral_decision_does_not_clear_prior_block():
    # latest-VERDICT-BEARING-per-identity: a later NEUTRAL decision must NOT
    # supersede the same identity's standing block.
    evs = [ev("decision", "changes_requested", who="claude-rco-1", ts="t1"),
           ev("decision", "received", who="claude-rco-1", ts="t2")]
    assert verdict(evs).clear_to_merge is False


# --- normalize_raw reads structured fields only ------------------------------

def test_normalize_raw_prefers_payload_structured_fields():
    raw = {"type": "decision", "status": "rco_pass",
           "payload": {"decision_status": "rco_pass", "head_sha": HEAD,
                       "author_uuid": "uuid-1"},
           "task_id": TASK, "ts_utc": "t"}
    n = normalize_raw(raw)
    assert n["decision_status"] == "rco_pass" and n["head_sha"] == HEAD
    assert n["author_identity"] == "uuid-1"


def test_normalize_raw_then_classify_end_to_end():
    raw_block = {"type": "finding", "payload": {"head_sha": HEAD},
                 "agent": "claude-rco-1", "task_id": TASK, "ts_utc": "t"}
    n = normalize_raw(raw_block)
    assert classify([n], task_id=TASK, head_sha=HEAD,
                    recognized_rcos=RCOS).clear_to_merge is False
