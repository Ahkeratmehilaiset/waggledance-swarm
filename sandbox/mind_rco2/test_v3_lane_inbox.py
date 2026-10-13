# SPDX-License-Identifier: BUSL-1.1
"""Synthetic twins for the V3 lane inbox (sandbox only; no bridge, gate, network or live capture).

Lead 10-13: naming a request id is a routing match, never a valid binding; keep a wrong-id twin and a
mentioned-but-not-answered twin; an own vote at the requested head satisfies the content only.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sandbox.mind_rco2 import v3_lane_inbox as v3  # noqa: E402

LANE = "claude-rco-2"
HEAD = "a" * 40
NOW = "2026-10-13T12:00:00Z"


def ask(rid, *, to=LANE, status="requested", ts="2026-10-13T11:00:00Z", head=HEAD, agent="codex-lead-1"):
    return {"agent": agent, "type": "message", "status": status, "to": to, "task_id": "lead/task-100",
            "request_id": rid, "ts_utc": ts, "payload": {"pr": 100, "head": head, "result_contract": {"required": []}}}


def own(ts="2026-10-13T11:30:00Z", **fields):
    event = {"agent": LANE, "type": "message", "status": "info", "to": "codex-lead-1", "task_id": "lead/task-100",
             "ts_utc": ts, "message": "", "payload": {}}
    event.update(fields)
    return event


def inbox(events, votes=()):
    snapshot = {"prs": {"100": {"head": HEAD, "votes": list(votes), "stale_votes": [], "veto_observations": [],
                                "reported_ci": {"status": "UNKNOWN"}, "source_refs": []}},
                "lanes": {}, "tensions": []}
    context = {"charter_ok": True, "prs": {100: {"state": "OPEN", "head": HEAD, "branch": "lead/branch-100",
                                                "authors": ["codex-lead-1"], "path_class": None}}}
    return v3.build_inbox(snapshot, context, v3.Capture(events), LANE, NOW)


def kinds(result):
    return [item["kind"] for item in result["stale"]]


def test_pending_ask_waits_with_its_id():
    result = inbox([ask("ask-1")])
    assert [w["asks"] for w in result["wait"]] == [["ask-1"]]
    assert result["asks_actionable"] == 1


def test_bound_reply_answers_the_ask():
    result = inbox([ask("ask-1"), own(in_reply_to_request_id="ask-1")])
    assert result["wait"] == [] and result["asks_actionable"] == 0
    assert "request_reported_answer_unverified" not in kinds(result)


def test_mentioned_but_not_answered_is_unverified_not_answered():
    events = [ask("ask-1"), own(message="I will look at ask-1 after CI")]
    result = inbox(events)
    unverified = [s for s in result["stale"] if s["kind"] == "request_reported_answer_unverified"]
    assert len(unverified) == 1 and unverified[0]["request_id"] == "ask-1"
    assert unverified[0]["refs"] == [v3.event_id(events[0]), v3.event_id(events[1])]
    assert result["wait"] == []  # not work, but not a verified answer either: it is shown with both refs


def test_wrong_id_twin_does_not_end_the_ask():
    result = inbox([ask("ask-1"), own(in_reply_to_request_id="ask-1-v2", message="answer to ask-1-v2")])
    assert [w["asks"] for w in result["wait"]] == [["ask-1"]]
    assert "request_reported_answer_unverified" not in kinds(result)


def test_own_vote_at_the_head_satisfies_an_old_ask_before_the_age_rule():
    vote = {"agent": LANE, "status": "rco_pass", "head": HEAD, "source_refs": ["b" * 64]}
    result = inbox([ask("ask-old", ts="2026-10-12T08:00:00Z")], votes=[vote])
    assert "content_satisfied_by_vote" in kinds(result)
    assert "request_unanswered_over_12h" not in kinds(result)


def test_old_ask_without_a_vote_is_unanswered_over_12h():
    result = inbox([ask("ask-old", ts="2026-10-12T08:00:00Z")])
    assert "request_unanswered_over_12h" in kinds(result) and result["wait"] == []


def test_review_broadcast_asks_every_named_reviewer():
    result = inbox([ask("bc-1", to="codex-lead-1,codex-tools-1,claude-rco-2", status="review_requested",
                        agent="fable-5")])
    assert [w["asks"] for w in result["wait"]] == [["bc-1"]]


def test_plain_ask_to_another_first_addressee_is_only_a_copy():
    result = inbox([ask("tools-1", to="codex-tools-1,claude-rco-2")])
    assert result["wait"] == [] and result["asks_actionable"] == 0


def test_ask_for_another_head_is_dead_not_work():
    result = inbox([ask("ask-h", head="c" * 40)])
    assert result["wait"] == [] and "request_for_other_head" in kinds(result)


def test_punctuation_after_the_id_still_names_it():
    result = inbox([ask("ask-1"), own(message="answer for ask-1: done")])
    assert "request_reported_answer_unverified" in kinds(result) and result["wait"] == []


def test_a_named_ask_for_another_head_stays_dead():
    result = inbox([ask("ask-h", head="c" * 40), own(message="re ask-h")])
    assert "request_for_other_head" in kinds(result)
    assert "request_reported_answer_unverified" not in kinds(result)


def test_a_named_ask_for_a_closed_pr_stays_dead():
    closed_ask = ask("ask-c")
    closed_ask["payload"]["pr"] = 101
    snapshot = {"prs": {}, "lanes": {}, "tensions": []}
    context = {"charter_ok": True, "prs": {101: {"state": "CLOSED", "head": HEAD, "branch": "lead/branch-101",
                                                "authors": ["codex-lead-1"], "path_class": None}}}
    result = v3.build_inbox(snapshot, context, v3.Capture([closed_ask, own(message="re ask-c")]), LANE, NOW)
    assert "request_for_pr_not_open" in kinds(result)
    assert "request_reported_answer_unverified" not in kinds(result)


def test_an_id_with_a_dot_uses_the_same_boundary_rule():
    result = inbox([ask("lead.ask.7"), own(message="lead.ask.7: done")])
    assert "request_reported_answer_unverified" in kinds(result) and result["wait"] == []
    twin = inbox([ask("lead.ask.7"), own(message="lead.ask.7x is another one")])
    assert [w["asks"] for w in twin["wait"]] == [["lead.ask.7"]]


def test_another_lane_naming_the_id_does_not_end_my_ask():
    other = own(message="relaying ask-1 to the reviewers")
    other["agent"] = "codex-tools-1"
    result = inbox([ask("ask-1"), other])
    assert [w["asks"] for w in result["wait"]] == [["ask-1"]]


def test_grok_round_fixture_storage_order_tools_line():
    """Grok HIGH fixture relayed by RCO1 10:22Z: storage order, mention versus answer target, handoff ends a claim."""
    tools, gh = "codex-tools-1", "a" * 40
    claim_ref_holder = {}
    handoff = {"agent": tools, "type": "handoff", "status": "ready", "to": "codex-lead-1", "task_id": "tools/C9",
               "ts_utc": "2026-10-13T11:20:00Z", "message": "C9 done", "payload": {"pr": 42, "head": gh}}
    claim = {"agent": tools, "type": "claim", "status": "active", "to": "", "task_id": "tools/C9",
             "ts_utc": "2026-10-13T11:00:00Z", "message": "", "payload": {}, "write_scope": ["tools/x.py"]}
    r1 = {"agent": "codex-lead-1", "type": "message", "status": "requested", "to": tools, "task_id": "lead/T7",
          "request_id": "R1", "ts_utc": "2026-10-13T11:05:00Z", "payload": {"result_contract": {"required": []}}}
    r2 = dict(r1, request_id="R2", ts_utc="2026-10-13T11:06:00Z")
    lead_mention = {"agent": "codex-lead-1", "type": "message", "status": "info", "to": tools, "task_id": "lead/T7",
                    "ts_utc": "2026-10-13T11:07:00Z", "message": "about R1", "payload": {}}
    answer_r2 = {"agent": tools, "type": "message", "status": "answered", "to": "codex-lead-1", "task_id": "lead/T7",
                 "in_reply_to_request_id": "R2", "ts_utc": "2026-10-13T11:08:00Z", "message": "", "payload": {}}
    events = [handoff, claim, r1, r2, lead_mention, answer_r2]
    claim_ref = v3.event_id(claim)
    snapshot = {"prs": {"42": {"head": gh, "stale_votes": [], "veto_observations": [], "source_refs": [],
                               "reported_ci": {"status": "UNKNOWN"},
                               "votes": [{"agent": "claude-rco-1", "status": "rco_pass", "head": gh,
                                          "source_refs": ["d" * 64]}]}},
                "lanes": {f"{tools}|7a8af68d-20bc-4598-9953-23c5dd98b102": {
                    "agent": tools, "verification": "declared", "uncertainty": [],
                    "claims": {"by_task": [{"task_id": "tools/C9", "source_refs": [claim_ref]}]}}},
                "tensions": []}
    context = {"charter_ok": True, "prs": {42: {"state": "OPEN", "head": gh, "branch": "tools/pr42",
                                               "authors": [tools], "path_class": None}}}
    result = v3.build_inbox(snapshot, context, v3.Capture(events), tools, NOW)
    assert [(o["pr"], o["github_head"]) for o in result["own"]] == [(42, gh)]  # author line, no vote ask
    open_asks = [a["request_id"] for a in result["act"] if a.get("request_id")]
    assert open_asks == ["R1"]  # a mention by another lane is routing only; R2 has its answer target
    assert result["claims"] == []  # the handoff ends claim C9 whatever the storage order
    assert any(s["kind"] == "claim_ended_by_handoff" for s in result["stale"])


def test_a_pr_with_my_vote_in_stays_visible():
    vote = {"agent": LANE, "status": "rco_pass", "head": HEAD, "source_refs": ["b" * 64]}
    result = inbox([], votes=[vote])
    assert [(d["pr"], d["missing"]) for d in result["done"]] == [(100, ["build_lead", "build_tools", "claude-rco-1"])]
    text = v3.render_text(result)  # others and CI still missing: folded into one DONE line with the PR number
    assert any(line.startswith("DONE ") and "PR100" in line for line in text.splitlines())


def test_shared_fold_lens_equals_the_one_lane_path_for_every_lane():
    vote = {"agent": "claude-rco-1", "status": "rco_pass", "head": HEAD, "source_refs": ["b" * 64]}
    events = [ask("ask-1"), ask("bc-1", to="codex-lead-1,codex-tools-1,claude-rco-2", status="review_requested",
              agent="fable-5"), own(message="re ask-1"), ask("tools-1", to="codex-tools-1,claude-rco-2")]
    snapshot = {"prs": {"100": {"head": HEAD, "votes": [vote], "stale_votes": [], "veto_observations": [],
                                "reported_ci": {"status": "UNKNOWN"}, "source_refs": []}},
                "lanes": {"operator|x": {"agent": "operator", "claims": "UNKNOWN"}}, "tensions": []}
    context = {"charter_ok": True, "prs": {100: {"state": "OPEN", "head": HEAD, "branch": "lead/branch-100",
                                                "authors": ["codex-lead-1"], "path_class": None}}}
    capture = v3.Capture(events)
    fold = v3.build_fold(snapshot, context, capture, NOW)
    for lane in ("claude-rco-2", "claude-rco-1", "codex-lead-1", "codex-tools-1", "fable-5"):
        assert v3.lens(fold, lane) == v3.build_inbox_direct(snapshot, context, capture, lane, NOW), lane


def test_fleet_lens_grok_open_stale_and_answered():
    def grok(status, cid, ts, extra=None):
        payload = {"consultation_id": cid, "requested_by": "claude-rco-1"}
        payload.update(extra or {})
        return {"agent": "grok-scout-1", "type": "status", "status": status, "to": "", "task_id": "rco1/t",
                "ts_utc": ts, "message": "", "payload": payload}
    events = [grok("consultation_started", "old", "2026-10-11T02:00:00Z"),
              grok("consultation_started", "done", "2026-10-13T11:00:00Z"),
              grok("consultation_answered", "done", "2026-10-13T11:03:00Z", {"duration_seconds": 160, "report_sha256": "e" * 64}),
              grok("consultation_started", "live", "2026-10-13T11:50:00Z")]
    snapshot = {"prs": {}, "lanes": {}, "tensions": []}
    context = {"charter_ok": True, "prs": {}}
    text = v3.render_fleet(v3.build_fold(snapshot, context, v3.Capture(events), NOW))
    assert "GROK open live" in text and "GROK open old" not in text
    assert "GROK answered done" in text and "in 160 s" in text
    assert "3 consultations, 1 open, 1 stale starts" in text


def test_fleet_lens_shows_posted_usage_limits_and_never_guesses():
    limit = {"agent": "claude-rco-1", "type": "status", "status": "usage_limit", "to": "", "task_id": "limits",
             "ts_utc": "2026-10-13T11:50:00Z", "message": "", "payload": {"lane": "claude-rco-2", "provider": "claude",
             "window": "session", "reset_utc": "2026-10-13T13:00:00Z", "source": "/usage"}}
    unknown = dict(limit, payload={"provider": "codex", "window": "weekly"}, agent="codex-tools-1")
    text = v3.render_fleet(v3.build_fold({"prs": {}, "lanes": {}, "tensions": []}, {"charter_ok": True, "prs": {}},
                                         v3.Capture([limit, unknown]), NOW))
    assert "LIMITS claude-rco-2 claude session: resets in 60 min" in text
    assert "LIMITS codex-tools-1 codex weekly: UNKNOWN" in text


def test_two_windows_of_one_account_both_show():
    def limit(window, reset, used):
        return {"agent": "claude-rco-1", "type": "status", "status": "usage_limit", "to": "", "task_id": "x",
                "ts_utc": "2026-10-13T11:55:00Z", "message": "", "payload": {"provider": "claude", "window": window,
                "reset_utc": reset, "used_percentage": used, "scope": "shared account of the Claude lanes", "source": "statusline"}}
    text = v3.render_fleet(v3.build_fold({"prs": {}, "lanes": {}, "tensions": []}, {"charter_ok": True, "prs": {}},
                                         v3.Capture([limit("five_hour", "2026-10-13T14:50:00Z", 6),
                                                     limit("seven_day", "2026-10-14T18:00:00Z", 80)]), NOW))
    assert "claude five_hour: 6% used, resets in 170 min" in text
    assert "claude seven_day: 80% used, resets in 1800 min" in text


def test_github_observed_status_splits_ready_and_blocked_and_checks_the_head():
    vote = lambda agent, status: {"agent": agent, "status": status, "head": HEAD, "source_refs": ["b" * 64]}
    votes = [vote("claude-rco-1", "rco_pass"), vote("claude-rco-2", "rco_pass"), vote("codex-lead-1", "build_consensus_pass"),
             vote("codex-tools-1", "build_consensus_pass")]
    snapshot = {"prs": {"100": {"head": HEAD, "votes": votes, "stale_votes": [], "veto_observations": [],
                                "reported_ci": {"status": "UNKNOWN"}, "source_refs": []},
                        "101": {"head": "c" * 40, "votes": [dict(v, head="c" * 40) for v in votes], "stale_votes": [],
                                "veto_observations": [], "reported_ci": {"status": "UNKNOWN"}, "source_refs": []}},
                "lanes": {}, "tensions": []}
    context = {"charter_ok": True, "prs": {100: {"state": "OPEN", "head": HEAD, "branch": "b100", "authors": ["x-lane"], "path_class": None},
                                           101: {"state": "OPEN", "head": "c" * 40, "branch": "b101", "authors": ["x-lane"], "path_class": None}}}
    fold = v3.build_fold(snapshot, context, v3.Capture([]), NOW)
    observed = {"schema": "wd.bridge-mind-gh-status.v1", "authority": "none", "observed_utc": "2026-10-13T11:58:00Z",
                "prs": {"100": {"head": HEAD, "merge_state": "BLOCKED", "all_required_success": True,
                                "bot_threads": {"bot": {"open": {"P2": 2}}, "complete": True}},
                        "101": {"head": "d" * 40, "merge_state": "CLEAN", "all_required_success": True}}}
    text = v3.render_text(v3.lens(v3.attach_github(fold, observed), "claude-rco-2"))
    assert "BLOCKED on GitHub although every vote is in and CI is green: PR100 BLOCKED (2 open bot threads)" in text
    assert "PR101" in text and "READY" not in text  # 101: observed head differs, so its GitHub facts are not used
    stale = dict(observed, observed_utc="2026-10-13T10:00:00Z")
    assert "github" not in v3.attach_github(fold, stale)["prs"]["100"]
