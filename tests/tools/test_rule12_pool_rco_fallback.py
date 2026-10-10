# SPDX-License-Identifier: BUSL-1.1
"""Rule 12 RCO-slot pool fallback (operator 2026-10-10 06:56Z).

"RCO paikan voi täyttää tools, lead, fabel tai jos ei mikään niiistä niin grok":
with no recognized RCO present, the first eligible lane of Tools, Lead, Fable
with an exact-head ``rco_pass`` holds the RCO slot; only when none of them is
available may Grok fill it. Covers the evaluator, the gate lift under
``review_policy=rule12``, the receipt binding and the merge executor recheck.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import bridge_rule12_review_eligibility as r12  # noqa: E402
from tools.bridge_pr_author import github_pr_git_identity_evidence  # noqa: E402
from tools.idle_consensus_auto_merge import (  # noqa: E402
    REVIEW_POLICY_LEGACY,
    REVIEW_POLICY_RULE12,
    _pool_rco_slot_lift,
    evaluate_auto_merge_gate,
)
import tools.merge_with_bridge_receipt as merge_tool  # noqa: E402
from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from tools.write_bridge_consensus_merge_receipt import write_bridge_consensus_merge_receipt  # noqa: E402

# --- evaluator ---------------------------------------------------------------
TASK = "fable-5/example-change-20261010"
H = "a" * 40
DIFF = "d" * 64
NOW = "2026-10-06T17:00:00.0000000Z"
NONCE = "c" * 32
FABLE_AUTHOR = [{"agent": "fable-5", "role": "author"}]
RCOS_RECUSED_TS = "2026-10-06T16:45:00Z"


def ev(agent, type_, status, head=H, task=TASK, ts="2026-10-06T16:50:00Z", **extra):
    event = {"agent": agent, "type": type_, "status": status, "task_id": task, "ts_utc": ts}
    if head is not None:
        event["payload"] = {"head": head}
    event.update(extra)
    return event


def rco_pass(agent, **kw):
    return ev(agent, "decision", "rco_pass", **kw)


def build_pass(agent, **kw):
    return ev(agent, "decision", "build_consensus_pass", **kw)


def recused(agent, **kw):
    return ev(agent, "message", "rco_recused", **kw)


RCOS_RECUSED = [
    recused("claude-rco-1", ts=RCOS_RECUSED_TS),
    recused("claude-rco-2", ts=RCOS_RECUSED_TS),
]


def grok(**overrides):
    answer = "APPROVE\nFull review of 3 files."
    consultation = {
        "request_id": "0123456789abcdef0123456789abcdef",
        "requester": "codex-lead-1",
        "task_id": TASK,
        "head": H,
        "slot": "rco",
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


def evaluate(events, contributors=FABLE_AUTHOR, consultations=()):
    return r12.evaluate_rule12_review_eligibility(
        task_id=TASK,
        head=H,
        contributors=contributors,
        events=events,
        now_utc=NOW,
        grok_consultations=consultations,
        expected_diff_sha256=DIFF,
        expected_prompt_sha256="1" * 64,
        expected_files_total=3,
        expected_nonce=NONCE,
    )


def test_pool_order_is_the_operator_order_and_never_a_recognized_rco():
    assert r12.RCO_POOL_FALLBACK == ("codex-tools-1", "codex-lead-1", "fable-5")
    assert not set(r12.RCO_POOL_FALLBACK) & set(r12.RECOGNIZED_RCOS)
    assert set(r12.RCO_POOL_FALLBACK) <= set(r12.REVIEW_POOL)


def test_tools_holds_the_rco_slot_before_lead_and_lead_holds_the_opposite_slot():
    result = evaluate([*RCOS_RECUSED, rco_pass("codex-tools-1"), rco_pass("codex-lead-1")])
    assert result["decision"] == "satisfied", result["reasons"]
    rco = result["slots"]["rco"]
    assert rco["state"] == r12.POOL_FALLBACK_STATE
    assert rco["holders"] == ["codex-tools-1"]
    assert rco["pool_fallback"]["holder"] == "codex-tools-1"
    assert result["slots"]["opposite_family"]["holders"] == ["codex-lead-1"]
    assert result["grok_fallback"]["filled"] == []


def test_lead_holds_when_tools_only_approved_the_opposite_family_slot():
    result = evaluate([*RCOS_RECUSED, build_pass("codex-tools-1"), rco_pass("codex-lead-1")])
    assert result["decision"] == "satisfied", result["reasons"]
    assert result["slots"]["rco"]["holders"] == ["codex-lead-1"]
    assert result["slots"]["opposite_family"]["holders"] == ["codex-tools-1"]


def test_one_identity_never_holds_both_slots():
    # Tools alone: it holds the RCO slot, so the opposite slot waits for Lead.
    result = evaluate([*RCOS_RECUSED, rco_pass("codex-tools-1")])
    assert result["slots"]["rco"]["holders"] == ["codex-tools-1"]
    assert "codex-tools-1" not in result["slots"]["opposite_family"]["standing"]
    assert result["slots"]["opposite_family"]["state"] == "pending"
    assert result["decision"] == "not_satisfied"


def test_fable_holds_for_a_gpt_authored_change_when_tools_and_lead_are_out():
    contributors = [{"agent": "codex-lead-1", "role": "author"}]
    events = [*RCOS_RECUSED, recused("codex-tools-1"), rco_pass("fable-5")]
    result = evaluate(events, contributors)
    rco = result["slots"]["rco"]
    assert rco["pool_fallback"]["standing"] == {
        "codex-tools-1": "recused",
        "codex-lead-1": "implementer",
        "fable-5": "eligible",
    }
    assert rco["state"] == r12.POOL_FALLBACK_STATE
    assert rco["holders"] == ["fable-5"]
    # fable-5 holds the RCO slot, so it cannot also be the opposite-family holder.
    assert result["slots"]["opposite_family"]["state"] == "vacant"
    assert result["decision"] == "not_satisfied"


def test_an_implementer_pool_lane_is_skipped():
    contributors = [{"agent": "codex-tools-1", "role": "author"}]
    events = [*RCOS_RECUSED, rco_pass("codex-tools-1"), rco_pass("codex-lead-1"),
              build_pass("fable-5")]
    result = evaluate(events, contributors)
    assert result["decision"] == "satisfied", result["reasons"]
    assert result["slots"]["rco"]["holders"] == ["codex-lead-1"]
    assert result["slots"]["rco"]["pool_fallback"]["standing"]["codex-tools-1"] == "implementer"
    assert result["slots"]["opposite_family"]["holders"] == ["fable-5"]


def test_an_eligible_recognized_rco_keeps_the_pool_out():
    events = [recused("claude-rco-1", ts=RCOS_RECUSED_TS), rco_pass("codex-tools-1"),
              build_pass("codex-lead-1")]
    result = evaluate(events)
    rco = result["slots"]["rco"]
    assert rco["state"] == "pending"  # claude-rco-2 is present and has not passed
    assert rco["pool_fallback"]["holder"] == ""
    assert rco["pool_fallback"]["standing"] == {}
    assert result["decision"] == "not_satisfied"


def test_an_available_pool_lane_keeps_the_slot_pending_and_grok_out():
    # Lead holds the opposite slot; Tools is eligible but silent.
    events = [*RCOS_RECUSED, build_pass("codex-lead-1")]
    result = evaluate(events, consultations=[grok(requester="codex-lead-1")])
    rco = result["slots"]["rco"]
    assert rco["state"] == "pending"
    assert rco["pool_fallback"]["available"] == ["codex-tools-1"]
    assert result["grok_fallback"]["filled"] == []
    assert result["decision"] == "not_satisfied"


def test_grok_fills_only_when_no_pool_lane_is_available():
    events = [*RCOS_RECUSED, build_pass("codex-lead-1"), recused("codex-tools-1")]
    result = evaluate(events, consultations=[grok(requester="codex-tools-1")])
    rco = result["slots"]["rco"]
    assert rco["pool_fallback"]["available"] == []
    assert rco["state"] == "held_by_grok_fallback"
    assert rco["holders"] == []
    assert result["decision"] == "satisfied", result["reasons"]


def test_an_absent_pool_lane_is_not_available():
    request = ev("claude-rco-1", "message", "requested", ts="2026-10-06T15:00:00Z",
                 to="codex-tools-1", request_id="req-tools")
    events = [*RCOS_RECUSED, build_pass("codex-lead-1"), request]
    result = evaluate(events, consultations=[grok(requester="codex-lead-1")])
    rco = result["slots"]["rco"]
    assert rco["pool_fallback"]["standing"]["codex-tools-1"] == "absent"
    assert rco["state"] == "held_by_grok_fallback"
    assert result["decision"] == "satisfied", result["reasons"]


def test_a_conflicting_pool_lane_keeps_the_slot_pending():
    events = [*RCOS_RECUSED, build_pass("codex-lead-1"), recused("codex-tools-1"),
              rco_pass("codex-tools-1")]
    result = evaluate(events, consultations=[grok(requester="codex-lead-1")])
    rco = result["slots"]["rco"]
    assert rco["pool_fallback"]["standing"]["codex-tools-1"] == "conflicting"
    assert rco["pool_fallback"]["holder"] == ""
    assert rco["state"] == "pending"
    assert result["grok_fallback"]["filled"] == []


@pytest.mark.parametrize(
    "block",
    [
        ev("codex-tools-1", "finding", "changes_requested", ts="2026-10-06T16:55:00Z"),
        ev("codex-tools-1", "blocked", "blocked", ts="2026-10-06T16:55:00Z"),
        ev("codex-tools-1", "decision", "changes_requested", ts="2026-10-06T16:55:00Z"),
    ],
)
def test_an_available_pool_lane_block_outranks_another_lanes_pass(block):
    events = [*RCOS_RECUSED, rco_pass("codex-lead-1"), build_pass("fable-5"), block]
    contributors = [{"agent": "claude-rco-1", "role": "author"}]
    result = evaluate(events, contributors)
    rco = result["slots"]["rco"]
    assert rco["state"] == r12.POOL_BLOCKED_STATE
    assert rco["pool_fallback"]["blocking"] == ["codex-tools-1"]
    assert result["decision"] == "not_satisfied"
    assert any("pool fallback lane block" in reason for reason in result["reasons"])


def test_a_pool_block_on_the_holder_itself_is_not_held():
    # Tools' own earlier rco_pass also counts as an opposite-family approval, so it
    # sits in that slot; its uncleared block still blocks the RCO slot.
    events = [*RCOS_RECUSED, build_pass("codex-lead-1"),
              ev("codex-tools-1", "finding", "changes_requested", ts="2026-10-06T16:55:00Z"),
              rco_pass("codex-tools-1", ts="2026-10-06T16:50:00Z")]
    result = evaluate(events)
    assert result["slots"]["rco"]["pool_fallback"]["holder"] == ""
    assert result["slots"]["rco"]["state"] == r12.POOL_BLOCKED_STATE


def test_a_later_own_rco_pass_clears_a_pool_lane_block():
    events = [*RCOS_RECUSED, build_pass("codex-lead-1"),
              ev("codex-tools-1", "finding", "changes_requested", ts="2026-10-06T16:40:00Z"),
              rco_pass("codex-tools-1", ts="2026-10-06T16:50:00Z")]
    result = evaluate(events)
    assert result["slots"]["rco"]["state"] == r12.POOL_FALLBACK_STATE
    assert result["decision"] == "satisfied", result["reasons"]


def test_a_pool_lane_block_is_never_a_recognized_rco_veto():
    events = [*RCOS_RECUSED, build_pass("codex-lead-1"),
              ev("codex-tools-1", "finding", "changes_requested", ts="2026-10-06T16:55:00Z")]
    result = evaluate(events)
    assert result["blocking_rcos"] == []
    assert result["decision"] == "not_satisfied"


def test_an_opposite_family_holder_block_blocks_the_rco_slot():
    events = [*RCOS_RECUSED, build_pass("codex-lead-1"), rco_pass("codex-tools-1"),
              ev("codex-lead-1", "finding", "changes_requested", ts="2026-10-06T16:55:00Z")]
    result = evaluate(events)
    assert result["slots"]["rco"]["pool_fallback"]["available"] == ["codex-tools-1"]
    assert result["slots"]["rco"]["pool_fallback"]["blocking"] == ["codex-lead-1"]
    assert result["slots"]["rco"]["state"] == r12.POOL_BLOCKED_STATE
    assert result["decision"] == "not_satisfied"


def test_an_opposite_family_holder_message_does_not_block_the_rco_slot():
    # Lead holds the opposite slot, so it is not an RCO-slot candidate; Tools holds.
    events = [*RCOS_RECUSED, build_pass("codex-lead-1"), rco_pass("codex-tools-1"),
              ev("codex-lead-1", "message", "note", ts="2026-10-06T16:55:00Z")]
    result = evaluate(events)
    assert result["slots"]["rco"]["pool_fallback"]["available"] == ["codex-tools-1"]
    assert result["decision"] == "satisfied", result["reasons"]


def test_a_pool_rco_pass_at_another_head_does_not_hold():
    events = [*RCOS_RECUSED, build_pass("codex-lead-1"), rco_pass("codex-tools-1", head="b" * 40)]
    result = evaluate(events)
    assert result["slots"]["rco"]["state"] == "pending"


def test_a_future_dated_pool_rco_pass_does_not_hold():
    events = [*RCOS_RECUSED, build_pass("codex-lead-1"),
              rco_pass("codex-tools-1", ts="2026-10-06T17:30:00Z")]
    result = evaluate(events)
    assert result["slots"]["rco"]["state"] == "pending"


def test_a_grok_requester_may_not_be_an_implementer_when_grok_fills():
    events = [*RCOS_RECUSED, build_pass("codex-lead-1"), recused("codex-tools-1")]
    result = evaluate(events, consultations=[grok(requester="fable-5")])
    assert result["slots"]["rco"]["state"] == "vacant"
    assert result["decision"] == "not_satisfied"


def test_only_grok_advice_contributors_are_refused_c0402():
    result = evaluate([*RCOS_RECUSED], [{"agent": "grok-scout-1", "role": "concept"}])
    assert result["decision"] == "refused"
    assert any("no implementer remains" in reason for reason in result["reasons"])


def test_grok_advice_next_to_an_author_is_not_refused():
    contributors = [*FABLE_AUTHOR, {"agent": "grok-scout-1", "role": "design"}]
    result = evaluate([*RCOS_RECUSED, rco_pass("codex-tools-1"), rco_pass("codex-lead-1")],
                      contributors)
    assert result["decision"] == "satisfied", result["reasons"]
    assert result["implementers"] == ["fable-5"]


# --- gate --------------------------------------------------------------------
GHEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
GTASK = "fable-5/rule12-pool-gate-fixture"
GNOW = datetime(2026, 6, 7, 18, 0, tzinfo=timezone.utc)
DATE = "2026-06-07"
PATH = "tools/idle_daily_summary.py"
GDIFF = (
    f"diff --git a/{PATH} b/{PATH}\n"
    f"--- a/{PATH}\n+++ b/{PATH}\n@@ -1 +1,2 @@\n def helper():\n+    return 1\n"
)
MISSING_RCO = "missing exact-head RCO_PASS from recognized non-author RCO"
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}


def _identity_fields(author: str) -> dict:
    material = github_pr_git_identity_evidence(
        {
            "author": {"login": "Ahkeratmehilaiset", "name": "", "email": ""},
            "commits": [{"oid": GHEAD, "authors": [
                {"name": author, "email": f"{author}@users.noreply.github.com", "login": ""}
            ]}],
        },
        expected_head_sha=GHEAD,
    )
    identities = material.pop("identities")
    return {"git_identities": identities, "git_identity_evidence": material}


def _status(**overrides) -> dict:
    status = {
        "pr_number": 479,
        "head_sha": GHEAD,
        "head_ref": GTASK,
        "base_sha": BASE,
        "base_ref": "main",
        "base_tip_sha": BASE,
        "title": "Rule 12 pool gate fixture",
        "mergeable": "clean",
        "state": "OPEN",
        "is_draft": False,
        "updated_at": "2026-06-07T17:00:00Z",
        "author_agent": "fable-5",
        "operator_approved": False,
        "receipt_verified": True,
        "changed_paths": [PATH],
        "diff_text": GDIFF,
        "checks": [{"name": "unified", "state": "success", "status": "", "conclusion": ""}],
        **_identity_fields("fable-5"),
    }
    status.update(overrides)
    return status


def _event(agent: str, type_: str, status: str, ts: str, *, head: str | None = GHEAD,
           uuid: str | None = None) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": type_,
        "status": status,
        "task_id": GTASK,
        "message": f"{status} exact head {head}" if head is not None else "",
        "payload": {"pr": 479, "head": head} if head else {},
        "agent_uuid": AGENT_UUIDS.get(agent, "") if uuid is None else uuid,
    }


def _events(*extra: dict, tools_pass: bool = True) -> list[dict]:
    """Claude-authored; both RCOs recused; Lead approves; Tools holds the RCO slot."""
    claim = _event("fable-5", "claim", "active", "2026-06-07T16:00:00Z", head=None)
    claim["write_scope"] = ["*"]
    return [
        claim,
        _event("claude-rco-1", "message", "rco_recused", "2026-06-07T17:00:00Z"),
        _event("claude-rco-2", "message", "rco_recused", "2026-06-07T17:01:00Z"),
        _event("codex-lead-1", "decision", "build_consensus_pass", "2026-06-07T17:10:00Z"),
        *([_event("codex-tools-1", "decision", "rco_pass", "2026-06-07T17:12:00Z")]
          if tools_pass else []),
        *extra,
    ]


def _events_path(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e, sort_keys=True) for e in events), encoding="utf-8")
    return path


def _gate(tmp_path: Path, events: list[dict] | None = None, **overrides) -> dict:
    kwargs = dict(
        pr_status=_status(),
        expected_head=GHEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=GTASK,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _events() if events is None else events),
        bridge_task_id=GTASK,
        utc_date=DATE,
        require_bridge_consensus=True,
        review_policy=REVIEW_POLICY_RULE12,
        now_utc=GNOW,
    )
    kwargs.update(overrides)
    return evaluate_auto_merge_gate(**kwargs)


def test_gate_pool_holder_lifts_the_rco_pass_blocker(tmp_path):
    report = _gate(tmp_path)
    assert report["decision"] == "auto_merge_plan_ready", report["reasons"]
    assert MISSING_RCO not in report["reasons"]
    assert report["rco_pass_gate"]["ok"] is False  # no recognized RCO passed
    lift = report["pool_rco_slot_lift"]
    assert lift["lifted"] is True and lift["reasons"] == []
    assert lift["pool_holder"] == "codex-tools-1"
    assert lift["opposite_family_holders"] == ["codex-lead-1"]
    assert lift["veto_scan"]["decision"] == "no_recognized_rco_veto"
    assert report["bridge_consensus"]["identities"]["rco"]["agent"] == "codex-tools-1"
    evidence = report["pool_rco_fallback_evidence"]
    assert evidence == {
        "task_id": GTASK,
        "pr_number": 479,
        "base_sha": BASE,
        "head_sha": GHEAD,
        "slot": "rco",
        "holder": "codex-tools-1",
        "agent_uuid": AGENT_UUIDS["codex-tools-1"],
        "ts_utc": "2026-06-07T17:12:00Z",
        "status": "rco_pass",
        "rco_pass_task_id": GTASK,
        "pool_order": ["codex-tools-1", "codex-lead-1", "fable-5"],
        "pool_standing": {
            "codex-tools-1": "eligible",
            "codex-lead-1": "eligible",
            "fable-5": "implementer",
        },
        "opposite_family_holders": ["codex-lead-1"],
        "review_policy": REVIEW_POLICY_RULE12,
    }
    assert "grok_rco_slot_lift" not in report  # the Grok switch stays off


def test_gate_without_a_pool_pass_keeps_the_blocker(tmp_path):
    report = _gate(tmp_path, _events(tools_pass=False))
    assert report["ok"] is False
    assert MISSING_RCO in report["reasons"]
    assert report["pool_rco_slot_lift"]["lifted"] is False
    assert report["pool_rco_fallback_evidence"] is None


def test_gate_pool_pass_with_a_forged_identity_does_not_lift(tmp_path):
    forged = _event("codex-tools-1", "decision", "rco_pass", "2026-06-07T17:12:00Z",
                    uuid="00000000-0000-4000-8000-000000000000")
    report = _gate(tmp_path, _events(forged, tools_pass=False))
    assert report["ok"] is False
    assert MISSING_RCO in report["reasons"]
    assert report["pool_rco_slot_lift"]["lifted"] is False


def test_gate_recognized_rco_veto_blocks_the_pool_lift(tmp_path):
    veto = _event("claude-rco-1", "finding", "changes_requested", "2026-06-07T17:20:00Z")
    report = _gate(tmp_path, _events(veto))
    assert report["ok"] is False
    assert report["pool_rco_slot_lift"]["lifted"] is False
    assert report["pool_rco_fallback_evidence"] is None


def test_gate_legacy_policy_never_reads_the_pool(tmp_path):
    report = _gate(tmp_path, review_policy=REVIEW_POLICY_LEGACY, now_utc=None)
    assert report["ok"] is False
    assert MISSING_RCO in report["reasons"]
    assert "pool_rco_slot_lift" not in report
    assert "pool_rco_fallback_evidence" not in report


@pytest.mark.parametrize(
    ("status_overrides", "reason"),
    [
        ({"is_draft": True}, "PR must not be a draft"),
        ({"checks": [{"name": "unified", "state": "failure", "status": "", "conclusion": ""}]},
         "status checks not green"),
    ],
)
def test_gate_other_gates_still_block_a_pool_held_slot(tmp_path, status_overrides, reason):
    report = _gate(tmp_path, pr_status=_status(**status_overrides))
    assert report["ok"] is False
    assert any(reason in r for r in report["reasons"]), report["reasons"]


def _lift_input(**rco_overrides):
    rco = {
        "state": r12.POOL_FALLBACK_STATE,
        "holders": ["codex-tools-1"],
        "pool_fallback": {"holder": "codex-tools-1"},
    }
    rco.update(rco_overrides)
    return {
        "ok": True,
        "rco_pass_refs": [{"agent": "codex-tools-1"}],
        "rule12": {
            "decision": "satisfied",
            "slots": {"rco": rco, "opposite_family": {"state": "held", "holders": ["codex-lead-1"]}},
        },
    }


def _lift(consensus, **kw):
    kw.setdefault("bridge_peer_gate", {"clear_to_merge": True})
    return _pool_rco_slot_lift(bridge_consensus=consensus, events=[], task_id=GTASK,
                               author_agent="fable-5", checked=True, **kw)


def test_lift_control_state_lifts():
    assert _lift(_lift_input())["lifted"] is True


@pytest.mark.parametrize(
    "rco_overrides",
    [
        {"state": "held"},
        {"state": "held_by_grok_fallback"},
        {"holders": "codex-tools-1"},
        {"holders": ["codex-tools-1", "codex-lead-1"]},
        {"pool_fallback": {"holder": "claude-rco-1"}},
        {"pool_fallback": {"holder": "grok-scout-1"}},
        {"pool_fallback": {"holder": ["codex-tools-1"]}},
        {"pool_fallback": "codex-tools-1"},
    ],
)
def test_lift_refuses_any_other_slot_shape(rco_overrides):
    assert _lift(_lift_input(**rco_overrides))["lifted"] is False


def test_lift_refuses_the_holder_as_opposite_holder_or_grok():
    for holders in (["codex-tools-1"], ["grok-scout-1"], [], "codex-lead-1"):
        consensus = _lift_input()
        consensus["rule12"]["slots"]["opposite_family"]["holders"] = holders
        assert _lift(consensus)["lifted"] is False, holders


def test_lift_refuses_without_a_bound_pass_ref_or_with_a_peer_block():
    consensus = _lift_input()
    consensus["rco_pass_refs"] = []
    assert _lift(consensus)["lifted"] is False
    assert _lift(_lift_input(), bridge_peer_gate={"clear_to_merge": False})["lifted"] is False
    unchecked = _pool_rco_slot_lift(bridge_consensus=_lift_input(), bridge_peer_gate={
        "clear_to_merge": True}, events=[], task_id=GTASK, author_agent="fable-5", checked=False)
    assert unchecked["lifted"] is False


# --- receipt writer ----------------------------------------------------------
def test_receipt_binds_the_pool_holder_tuple(tmp_path):
    report = write_bridge_consensus_merge_receipt(
        pr_status=_status(),
        events_path=_events_path(tmp_path, _events()),
        out_dir=tmp_path / "receipt",
        expected_head=GHEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=GTASK,
        repo="example/repo",
        bridge_task_id=GTASK,
        now_utc=GNOW,
        review_policy=REVIEW_POLICY_RULE12,
    )
    manifest = Path(report["receipt_bundle_path"])
    assert verify_manifest(manifest)["ok"] is True
    payload = json.loads((manifest.parent / "payload-001-merge.json").read_text())
    evidence = payload["rule12_review"]["pool_rco_fallback"]
    assert evidence == report["gate_report"]["pool_rco_fallback_evidence"]
    assert evidence["holder"] == "codex-tools-1"
    assert "grok_fallback" not in payload["rule12_review"]
    bundle_text = "".join(p.read_text() for p in manifest.parent.glob("*.json"))
    assert "rco:pool_fallback" in bundle_text
    assert "rco:grok_fallback" not in bundle_text


# --- merge executor ----------------------------------------------------------
POOL_EVIDENCE = {
    "task_id": GTASK, "pr_number": 479, "base_sha": BASE, "head_sha": GHEAD, "slot": "rco",
    "holder": "codex-tools-1", "agent_uuid": AGENT_UUIDS["codex-tools-1"],
    "ts_utc": "2026-06-07T17:12:00Z", "status": "rco_pass", "rco_pass_task_id": GTASK,
    "pool_order": ["codex-tools-1", "codex-lead-1", "fable-5"],
    "pool_standing": {"codex-tools-1": "eligible"},
    "opposite_family_holders": ["codex-lead-1"], "review_policy": REVIEW_POLICY_RULE12,
}


def _execute(tmp_path, monkeypatch, receipt_evidence, fresh_evidence):
    seen: dict[str, list] = {"commands": []}
    monkeypatch.setattr(merge_tool, "build_pr_status_snapshot", lambda **_: _status())
    monkeypatch.setattr(
        merge_tool, "write_bridge_consensus_merge_receipt",
        lambda **_: {"receipt_bundle_path": str(tmp_path / "m.json"),
                     "gate_report": {"ok": True, "pool_rco_fallback_evidence": receipt_evidence}},
    )
    monkeypatch.setattr(
        merge_tool, "evaluate_auto_merge_gate",
        lambda **_: {"ok": True, "reasons": [], "pool_rco_fallback_evidence": fresh_evidence},
    )

    def runner(command):
        seen["commands"].append(list(command))
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    report = merge_tool.merge_with_bridge_receipt(
        pr_number=479, repo="example/repo", events_path=tmp_path / "events.jsonl",
        out_dir=tmp_path / "out", expected_head=GHEAD, expected_base_sha=BASE,
        consensus_proposal_id=GTASK, apply=True, review_policy=REVIEW_POLICY_RULE12,
        runner=runner,
    )
    return report, [c for c in seen["commands"] if c[:3] == ["gh", "pr", "merge"]]


@pytest.mark.parametrize("field", sorted(POOL_EVIDENCE))
def test_executor_rejects_any_swapped_pool_tuple_field_before_merge(tmp_path, monkeypatch, field):
    fresh = dict(POOL_EVIDENCE)
    fresh[field] = {"swapped": True}
    report, merge_calls = _execute(tmp_path, monkeypatch, POOL_EVIDENCE, fresh)
    assert report["decision"] == "apply_gate_recheck_failed"
    assert "pool RCO fallback evidence differs from the receipt" in report["errors"][0]
    assert merge_calls == []


@pytest.mark.parametrize(("receipt", "fresh"), [(POOL_EVIDENCE, None), (None, POOL_EVIDENCE)])
def test_executor_rejects_a_pool_tuple_that_appears_or_vanishes(tmp_path, monkeypatch, receipt,
                                                                fresh):
    report, merge_calls = _execute(tmp_path, monkeypatch, receipt, fresh)
    assert report["decision"] == "apply_gate_recheck_failed"
    assert merge_calls == []


def test_executor_merges_only_on_an_equal_pool_tuple(tmp_path, monkeypatch):
    report, merge_calls = _execute(tmp_path, monkeypatch, POOL_EVIDENCE, dict(POOL_EVIDENCE))
    assert len(merge_calls) == 1
    assert report["decision"] != "apply_gate_recheck_failed"
