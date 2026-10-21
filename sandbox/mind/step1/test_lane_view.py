"""Tests of hex-Mind step 1a (lane_view). Synthetic cases go through the real core (``tools.wd_bridge_mind``), so the
snapshot rows are what the core writes; the frozen-cycle checks run on a synthetic frozen root built in a
temporary directory (no data disk is read).

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/step1 -q -k lane_view
"""

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lane_view as lv  # noqa: E402
import lane_view_mutants as mut  # noqa: E402
from tools import wd_bridge_mind as core  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402

NUMBER = 1905
BRANCH = "codex-lead-1/operator-manual-ten-train-20261013"
OLD, NEW = hashlib.sha1(b"head A").hexdigest(), hashlib.sha1(b"head B").hexdigest()
SECOND, SECOND_BRANCH, SECOND_HEAD = 1906, "codex-tools-1/second-20261013", hashlib.sha1(b"head C").hexdigest()
UUIDS = {"codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101", "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
         "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103", "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
         "claude-rco-3": "00000000-0000-4000-8000-000000000003",  # synthetic declared identity, not a live session
         "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80"}
REQUIRED = ["unified", "test (3.11)", "test (3.12)", "test (3.13)", "security-scan", "lock-resolver-check"]
AUTHORS = ["claude-rco-2", "codex-lead-1", "codex-tools-1", "fable-5"]
AS_OF = "2026-10-13T18:00:00Z"
FORBIDDEN = ("ready", "merge eligible", "manual_execution")


def event(ts, agent, kind, status, payload, task=BRANCH, declared=True):
    row = {"ts_utc": ts, "agent": agent, "type": kind, "status": status, "task_id": task, "payload": payload}
    if declared:
        row["agent_uuid"] = UUIDS[agent]
    return row


def vote(ts, agent, head=NEW, task=BRANCH, number=NUMBER, kind="decision", **extra):
    status = "rco_pass" if agent.startswith("claude-rco") else "build_consensus_pass"
    return event(ts, agent, kind, status, {"pr": number, "head": head, **extra}, task=task)


def base_events():
    """Synthetic PR1905 shape: head A request, then head B votes with two eligible RCOs under the current quorum."""
    return [event("2026-10-13T13:26:48Z", "codex-lead-1", "message", "requested", {"pr": NUMBER, "head": OLD}),
            vote("2026-10-13T17:40:43.4458582Z", "claude-rco-1", task_id=BRANCH),
            vote("2026-10-13T17:40:44.4266226Z", "codex-lead-1", canonical_task_id=BRANCH, merge_owner="operator",
                 component_prs=[1885, 1878]),
            vote("2026-10-13T17:40:43.9458582Z", "claude-rco-3", task_id=BRANCH)]


def snapshot_of(events, as_of=AS_OF, *, log_identity="capture-window:" + "0" * 64, blocked=None):
    source = {"log_identity": log_identity, "generation": None, "cursor_start": 0,
              "cursor_end": len(events), "event_count": len(events), "coverage": "complete"}
    state = core.full_rebuild([{"ref": event_id(row), "event": row} for row in events], source=source,
                              as_of_utc=as_of)
    if blocked is not None:
        return core.blocked_snapshot(state, as_of_utc=as_of, offset=len(events), reason=blocked)
    return core.snapshot(state, as_of_utc=as_of)


def github_of(head=NEW, *, observed="2026-10-13T17:55:00Z", authors=AUTHORS, results=None, required=REQUIRED,
              state="OPEN", completed="2026-10-13T17:39:09Z", number=NUMBER, branch=BRANCH):
    results = {name: "SUCCESS" for name in required} if results is None else results
    rollup = [{"__typename": "CheckRun", "completedAt": completed, "conclusion": result, "name": name,
               "status": "COMPLETED"} for name, result in results.items()]
    derived = {"head": head, "merge_state": "CLEAN",
               "required_results": {name: [results[name]] for name in required if name in results},
               "missing_required": [name for name in required if name not in results],
               "all_required_success": bool(required) and all(results.get(name) == "SUCCESS" for name in required)}
    github = {
        "context": {"schema": "wd.bridge-mind-pr-context.v2", "authority": "none", "capture_sha256": "",
                    "observed_utc": observed,
                    "charter": {"loaded": True, "charter_blob": "1" * 40, "classifier_blob": "2" * 40},
                    "prs": {str(number): {"state": state, "head": head, "head_ref_name": branch, "path_class": None,
                                          "commit_authors": authors}}},
        "status": {"schema": "wd.bridge-mind-gh-status.v1", "authority": "none", "observed_utc": observed,
                   "required_contexts": list(required), "list_sha256": "", "mentions_captured": False,
                   "prs": {str(number): derived} if state == "OPEN" else {}},
        "pr_list": [{"headRefName": branch, "headRefOid": head, "number": number, "state": state}],
        "open_status": ([{"headRefOid": head, "mergeStateStatus": "CLEAN", "number": number,
                          "statusCheckRollup": rollup}] if state == "OPEN" else []),
    }
    mut.relink(github)
    return github


def merge_github(first, *others):
    """One capture of several PRs (same observation times and required set), linked again."""
    merged = copy.deepcopy(first)
    for other in others:
        merged["context"]["prs"].update(other["context"]["prs"])
        merged["status"]["prs"].update(other["status"]["prs"])
        merged["pr_list"] += other["pr_list"]
        merged["open_status"] += other["open_status"]
    mut.relink(merged)
    return merged


def make_case(events=None, github=None, as_of=AS_OF):
    events = base_events() if events is None else events
    return {"snapshot": snapshot_of(events, as_of), "github": github_of() if github is None else github,
            "bodies": {event_id(row): row for row in events}, "as_of_utc": as_of}


def view_of(case):
    return lv.lane_view(case["snapshot"], case["github"], case["bodies"], as_of_utc=case["as_of_utc"])


def lines_for(view, number=NUMBER):
    return {lane: [line for line in entry["actions"] if f"PR{number} " in line] for lane, entry in view["lanes"].items()}


def refs_of(events):
    return [event_id(row) for row in events]


def assert_abstains(view, reason):
    assert not any(lines_for(view).values())
    found = [item for item in view["abstained"] if item["pr"] == NUMBER]
    assert found and reason in found[0]["reasons"] and found[0]["detail"].startswith("UNKNOWN")


def quorum_events(rcos):
    """Synthetic current-head decisions only; no hidden RCO vote from the historical baseline."""
    return [vote("2026-10-13T17:40:00Z", agent) for agent in
            ("codex-lead-1", "codex-tools-1", *rcos)]


@pytest.mark.parametrize("rcos", [("claude-rco-1", "claude-rco-2"),
                                  ("claude-rco-1", "claude-rco-3"),
                                  ("claude-rco-2", "claude-rco-3")])
def test_quorum_any_two_distinct_recognized_rcos_complete(rcos):
    view = view_of(make_case(quorum_events(rcos), github_of(authors=["fable-5"])))
    record = view["prs"][str(NUMBER)]
    assert record["state"] == "votes_complete" and record["empty_slots"] == []
    assert {row["agent"] for row in record["credited"] if row["slot"] in lv.ws.RECOGNIZED_RCOS} == set(rcos)
    assert not any(lines_for(view).values())


def test_quorum_one_rco_leaves_exactly_one_eligible_advisory_slot():
    view = view_of(make_case(quorum_events(["claude-rco-1"]), github_of(authors=["fable-5"])))
    record = view["prs"][str(NUMBER)]
    assert record["state"] == "action" and record["empty_slots"] == ["claude-rco-2"]
    assert {lane for lane, lines in lines_for(view).items() if lines} == {"claude-rco-2"}
    assert "required RCO coverage is UNKNOWN" in view["lanes"]["claude-rco-2"]["actions"][0]


@pytest.mark.parametrize("exclusion", ["commit_author", "rco_recused"])
def test_quorum_excluded_rco_never_fills_or_is_asked(exclusion):
    events = quorum_events(lv.ws.RECOGNIZED_RCOS)
    authors = ["fable-5"]
    if exclusion == "commit_author":
        authors.append("claude-rco-1")
    else:
        events.append(event("2026-10-13T17:41:00Z", "claude-rco-1", "message", "rco_recused",
                            {"pr": NUMBER, "head": NEW}))
    view = view_of(make_case(events, github_of(authors=authors)))
    record = view["prs"][str(NUMBER)]
    assert record["state"] == "votes_complete" and record["empty_slots"] == []
    assert {row["agent"] for row in record["credited"] if row["slot"] in lv.ws.RECOGNIZED_RCOS} == {
        "claude-rco-2", "claude-rco-3"}
    assert [(row["agent"], row["reason"]) for row in record["not_credited"]] == [("claude-rco-1", exclusion)]
    assert not view["lanes"]["claude-rco-1"]["actions"]


@pytest.mark.parametrize("exclusion", ["commit_author", "rco_recused"])
def test_quorum_all_excused_abstains(exclusion):
    events = quorum_events(lv.ws.RECOGNIZED_RCOS)
    authors = list(lv.ws.RECOGNIZED_RCOS) if exclusion == "commit_author" else ["fable-5"]
    if exclusion == "rco_recused":
        events += [event("2026-10-13T17:41:00Z", rco, "message", "rco_recused", {"pr": NUMBER, "head": NEW})
                   for rco in lv.ws.RECOGNIZED_RCOS]
    view = view_of(make_case(events, github_of(authors=authors)))
    assert_abstains(view, "independent_rco_unavailable")
    assert view["counts"]["votes_complete"] == 0


def test_quorum_two_excused_never_make_builds_complete():
    view = view_of(make_case(quorum_events([]), github_of(authors=["claude-rco-1", "claude-rco-2"])))
    assert_abstains(view, "independent_rco_unavailable")


def test_quorum_duplicate_identity_does_not_fill_second_slot():
    events = quorum_events(["claude-rco-1"]) + [vote("2026-10-13T17:41:00Z", "claude-rco-1")]
    record = view_of(make_case(events, github_of(authors=["fable-5"])))["prs"][str(NUMBER)]
    assert record["state"] == "action" and record["empty_slots"] == ["claude-rco-2"]


def test_quorum_only_verified_current_head_votes_fill_slots():
    events = quorum_events(["claude-rco-1"]) + [vote("2026-10-13T17:41:00Z", "claude-rco-2", head=OLD)]
    record = view_of(make_case(events, github_of(authors=["fable-5"])))["prs"][str(NUMBER)]
    assert record["state"] == "action" and record["empty_slots"] == ["claude-rco-2"]
    assert [(row["agent"], row["reason"]) for row in record["not_credited"]] == [("claude-rco-2", "other_head")]


def test_lane_view_tools_line_at_the_github_head_not_the_bridge_head():
    events = base_events()
    view = view_of(make_case(events))
    record = view["prs"][str(NUMBER)]
    assert record["bridge_view_head"] == OLD and record["github_head"] == NEW and record["state"] == "action"
    assert record["empty_slots"] == ["build_tools"]
    assert [vote["ref"] for vote in record["credited"]] == [refs_of(events)[2], refs_of(events)[1], refs_of(events)[3]]
    line = view["lanes"]["codex-tools-1"]["actions"][0]
    assert f"build vote on PR{NUMBER} at {NEW[:12]} is the only empty slot" in line
    for cite in (refs_of(events)[2][:16], refs_of(events)[1][:16], f"GitHub 6/6 at {NEW[:12]}",
                 "waiting since 2026-10-13T17:40:44Z", "route operator manual train; the vote is still required",
                 "coverage UNKNOWN"):
        assert cite in line
    assert OLD[:12] not in line and refs_of(events)[0][:16] not in line
    assert {lane: found for lane, found in lines_for(view).items() if found} == {"codex-tools-1": [line]}


def test_lane_view_waiting_since_is_ci_completion_when_ci_finished_last():
    view = view_of(make_case(github=github_of(completed="2026-10-13T17:50:00Z")))
    assert "waiting since 2026-10-13T17:50:00Z" in view["lanes"]["codex-tools-1"]["actions"][0]


def test_lane_view_one_required_check_not_success_abstains():
    view = view_of(mut.check_result(make_case(), NUMBER, "unified", "FAILURE"))
    assert_abstains(view, "required_check_not_success")


def _duplicate_row(github):
    github["open_status"][0]["statusCheckRollup"].append(dict(github["open_status"][0]["statusCheckRollup"][0]))
    mut.relink(github)
    return github


def _derived_disagrees(github):
    github["status"]["prs"][str(NUMBER)]["required_results"]["unified"] = ["FAILURE"]
    return github


def _derived_not_all_success(github):
    github["status"]["prs"][str(NUMBER)]["all_required_success"] = False
    return github


@pytest.mark.parametrize("github, reason", [
    (github_of(results={name: "SUCCESS" for name in REQUIRED[1:]}), "required_check_missing"),
    (github_of(required=[]), "required_set_unknown"),
    (github_of(required=REQUIRED + ["unified"]), "required_set_duplicate"),
    (github_of(results=dict({name: "SUCCESS" for name in REQUIRED}, unified=None)), "required_check_not_success"),
    (_duplicate_row(github_of()), "required_check_duplicate"),
    (_derived_disagrees(github_of()), "required_checks_disagree"),
    (_derived_not_all_success(github_of()), "required_checks_disagree"),
], ids=["subset", "empty", "duplicate_name", "pending", "duplicate_row", "derived_disagrees", "derived_not_all"])
def test_lane_view_required_set_must_be_complete_and_all_success(github, reason):
    assert_abstains(view_of(make_case(github=github)), reason)


def test_lane_view_votes_on_another_task_are_not_credited():
    case = make_case()
    refs = refs_of(base_events())[1:]
    moved, renamed = mut.rebind_events(case, NUMBER, refs, task=BRANCH + "-moved")
    view = view_of(moved)
    record = view["prs"][str(NUMBER)]
    assert record["state"] == "no_credited_vote" and not record["credited"] and not any(lines_for(view).values())
    assert sorted(row["reason"] for row in record["not_credited"]) == ["other_task"] * 3


def test_lane_view_one_vote_on_another_task_leaves_two_empty_slots():
    moved, renamed = mut.rebind_events(make_case(), NUMBER, [refs_of(base_events())[2]], task=BRANCH + "-moved")
    view = view_of(moved)
    tools, lead = view["lanes"]["codex-tools-1"]["actions"][0], view["lanes"]["codex-lead-1"]["actions"][0]
    assert "is the only empty slot" not in tools and "also empty: build_lead" in tools
    assert "your vote row at this head is not credited (other_task" in lead


def test_lane_view_payload_task_field_that_disagrees_is_not_credited():
    events = base_events()
    events[2]["payload"]["canonical_task_id"] = BRANCH + "-other"
    record = view_of(make_case(events))["prs"][str(NUMBER)]
    assert [row["reason"] for row in record["not_credited"]] == ["task_fields_disagree"]
    assert record["empty_slots"] == ["build_lead", "build_tools"]


def test_lane_view_votes_rebound_to_the_bridge_head_are_not_credited():
    moved, renamed = mut.rebind_events(make_case(), NUMBER, refs_of(base_events())[1:], head=OLD)
    view = view_of(moved)
    record = view["prs"][str(NUMBER)]
    assert record["state"] == "no_credited_vote" and not any(lines_for(view).values())
    assert sorted(row["reason"] for row in record["not_credited"]) == ["other_head"] * 3


def test_lane_view_e4d970bb_head_a_head_b_late_vote_at_a():
    """Head A, then head B, then a late vote at A: B is kept, the A vote is stale (never ordered by arrival)."""
    events = [event("2026-10-13T10:00:00Z", "codex-lead-1", "message", "head_updated", {"pr": NUMBER, "head": OLD}),
              event("2026-10-13T11:00:00Z", "codex-lead-1", "message", "head_updated", {"pr": NUMBER, "head": NEW}),
              vote("2026-10-13T11:10:00Z", "claude-rco-1"), vote("2026-10-13T11:20:00Z", "codex-lead-1"),
              vote("2026-10-13T11:30:00Z", "codex-tools-1", head=OLD),
              vote("2026-10-13T11:15:00Z", "claude-rco-3")]
    view = view_of(make_case(events, github_of(observed="2026-10-13T17:55:00Z")))
    record = view["prs"][str(NUMBER)]
    assert record["bridge_view_head"] == NEW and record["github_head"] == NEW
    assert [(row["agent"], row["reason"]) for row in record["not_credited"]] == [("codex-tools-1", "other_head")]
    assert record["empty_slots"] == ["build_tools"]
    assert "is the only empty slot" in view["lanes"]["codex-tools-1"]["actions"][0]
    # The same late vote after the GitHub capture: GitHub may be stale, so the PR abstains.
    late = make_case(events, github_of(observed="2026-10-13T11:25:00Z"), as_of="2026-10-13T11:35:00Z")
    assert_abstains(view_of(late), "github_older_than_bridge_head")


def test_lane_view_late_vote_at_the_lagging_bridge_head_never_fills_the_slot():
    case, ref = mut.add_vote(make_case(), NUMBER, agent="codex-tools-1", agent_uuid=UUIDS["codex-tools-1"],
                             status="build_consensus_pass", head=OLD, task=BRANCH, ts_utc="2026-10-13T17:41:30Z")
    view = view_of(case)
    record = view["prs"][str(NUMBER)]
    assert [row["reason"] for row in record["not_credited"] if row["ref"] == ref] == ["other_head"]
    line = view["lanes"]["codex-tools-1"]["actions"][0]
    assert "is the only empty slot" in line and ref[:16] not in line


def _context_removed(github):
    github["context"] = None
    return github


def _status_unlinked(github):
    github["status"]["list_sha256"] = hashlib.sha256(b"another capture").hexdigest()  # derived from another list
    return github


def _context_unlinked(github):
    github["context"]["capture_sha256"] = hashlib.sha256(b"another capture").hexdigest()
    return github


def _status_stale(github):
    github["status"]["observed_utc"] = "2026-10-13T17:40:00Z"  # the PR context is fresh, the status file is not
    return github


def _list_head_differs(github):
    github["open_status"][0]["headRefOid"] = OLD
    mut.relink(github)
    return github


def _pr_list_head_differs(github):
    github["pr_list"][0]["headRefOid"] = OLD
    mut.relink(github)
    return github


@pytest.mark.parametrize("github, reason, refused", [
    (None, "github_capture_missing", "github_absent"),
    (_context_removed(github_of()), "github_capture_missing", "context_absent"),
    (github_of(observed="2026-10-13T17:40:00Z"), "github_capture_missing", "context_stale"),
    (_status_stale(github_of()), "github_capture_missing", "github_not_current"),
    (github_of(observed="2026-10-13T18:00:01Z"), "github_capture_missing", "github_not_current"),
    (_status_unlinked(github_of()), "github_capture_missing", "raw_lists_not_linked"),
    (_context_unlinked(github_of()), "github_capture_missing", "raw_lists_not_linked"),
    (_list_head_differs(github_of()), "head_ambiguous", None),
    (_pr_list_head_differs(github_of()), "head_ambiguous", None),
    (github_of(SECOND_HEAD, number=SECOND, branch=SECOND_BRANCH), "github_pr_missing", None),
], ids=["absent", "context_removed", "stale", "status_stale", "after_as_of", "status_unlinked", "context_unlinked",
        "status_head_disagrees", "list_head_disagrees", "pr_not_captured"])
def test_lane_view_github_missing_unusable_or_ambiguous_abstains(github, reason, refused):
    case = make_case()
    case["github"] = github
    view = view_of(case)
    assert_abstains(view, reason)
    assert view["github"] == ({"usable": False, "reason": refused} if refused else dict(view["github"], usable=True))


@pytest.mark.parametrize("verification", ["unverified", None])
def test_lane_view_valid_uuid_without_declared_verification_is_not_credited(verification):
    lead = refs_of(base_events())[2]
    view = view_of(mut.set_verification(make_case(), NUMBER, lead, verification))
    record = view["prs"][str(NUMBER)]
    assert lead not in {vote["ref"] for vote in record.get("credited", [])}
    assert_abstains(view, "vote_row_refused")


def test_lane_view_undeclared_identity_is_not_credited():
    events = base_events()
    events[2] = event(events[2]["ts_utc"], "codex-lead-1", "decision", "build_consensus_pass", events[2]["payload"],
                      declared=False)
    view = view_of(make_case(events))
    record = view["prs"][str(NUMBER)]
    assert [row["reason"] for row in record["not_credited"]] == ["undeclared_identity"]
    assert "also empty: build_lead" in view["lanes"]["codex-tools-1"]["actions"][0]


def test_lane_view_an_identity_votes_only_in_its_own_slot():
    events = base_events() + [event("2026-10-13T17:41:00Z", "fable-5", "decision", "build_consensus_pass",
                                    {"pr": NUMBER, "head": NEW}),
                              event("2026-10-13T17:41:10Z", "codex-lead-1", "decision", "rco_pass",
                                    {"pr": NUMBER, "head": NEW})]
    record = view_of(make_case(events))["prs"][str(NUMBER)]
    assert sorted((row["agent"], row["reason"]) for row in record["not_credited"]) == [
        ("codex-lead-1", "no_slot"), ("fable-5", "no_slot")]
    assert record["empty_slots"] == ["build_tools"]


def test_lane_view_author_rco_fills_no_slot_and_is_never_asked():
    events = base_events() + [vote("2026-10-13T17:41:00Z", "claude-rco-2")]
    view = view_of(make_case(events, github_of(authors=["claude-rco-1", "codex-lead-1"])))
    record = view["prs"][str(NUMBER)]
    assert [(row["agent"], row["reason"]) for row in record["not_credited"]] == [("claude-rco-1", "commit_author")]
    assert {"agent": "claude-rco-1", "why": "commit_author"} in record["excused"]
    assert record["empty_slots"] == ["build_tools"]
    assert not lines_for(view)["claude-rco-1"]
    line = view["lanes"]["codex-tools-1"]["actions"][0]
    assert refs_of(events)[1][:16] not in line and refs_of(events)[4][:16] in line


def test_pool_empty_regression_twin_both_rco_authors_cannot_mean_votes_complete():
    """REGRESSION_TWIN_EMPTY_RCO_POOL: builds alone cannot fill RCO coverage."""
    events = base_events() + [vote("2026-10-13T17:41:00Z", "codex-tools-1")]
    control = view_of(make_case(events, github_of(authors=["claude-rco-2"])))
    assert control["prs"][str(NUMBER)]["state"] == "votes_complete"
    view = view_of(make_case(events, github_of(authors=["claude-rco-1", "claude-rco-2"])))
    assert_abstains(view, "independent_rco_unavailable")
    assert view["counts"]["votes_complete"] == 0
    assert "operator" in view["abstained"][0]["detail"]


def test_both_declared_recusals_do_not_credit_an_existing_rco_vote():
    recusal1 = event("2026-10-13T17:42:00Z", "claude-rco-1", "message", "rco_recused",
                     {"pr": NUMBER, "head": NEW})
    recusal2 = event("2026-10-13T17:42:01Z", "claude-rco-2", "message", "rco_recused",
                     {"pr": NUMBER, "head": NEW})
    events = base_events() + [vote("2026-10-13T17:41:00Z", "codex-tools-1"), recusal1, recusal2]
    view = view_of(make_case(events, github_of(authors=["codex-lead-1"])))
    assert_abstains(view, "independent_rco_unavailable")
    record = view["prs"][str(NUMBER)]
    assert any(row["agent"] == "claude-rco-1" and row["reason"] == "rco_recused"
               for row in record["not_credited"])


def test_unfilled_rco_line_does_not_invent_required_policy_from_unknown_class():
    events = base_events()[:3] + [vote("2026-10-13T17:41:00Z", "codex-tools-1")]
    view = view_of(make_case(events, github_of(authors=["codex-lead-1"])))
    line, = view["lanes"]["claude-rco-2"]["actions"]
    assert "required RCO coverage is UNKNOWN" in line
    assert "the vote is still required" not in line


def test_lane_view_unknown_commit_authors_abstains():
    assert_abstains(view_of(make_case(github=github_of(authors=None))), "commit_authors_unknown")


def _recusal(head, task=BRANCH):
    return event("2026-10-13T17:30:00Z", "claude-rco-2", "message", "rco_recused", {"pr": NUMBER, "head": head},
                 task=task)


def test_lane_view_declared_recusal_at_the_github_head_excuses_the_rco():
    events = base_events() + [_recusal(NEW)]
    record = view_of(make_case(events, github_of(authors=["codex-lead-1"])))["prs"][str(NUMBER)]
    assert record["empty_slots"] == ["build_tools"] and {"agent": "claude-rco-2", "why": "rco_recused"} in record["excused"]
    # At another head the recusal does not count: the slot is empty and that RCO is asked.
    view = view_of(make_case(base_events()[:3] + [_recusal(OLD)], github_of(authors=["codex-lead-1"])))
    assert view["prs"][str(NUMBER)]["empty_slots"] == ["build_tools", "claude-rco-2"]
    assert f"RCO vote on PR{NUMBER} at {NEW[:12]} is empty (claude-rco-2" in view["lanes"]["claude-rco-2"]["actions"][0]


def test_lane_view_recusal_on_another_task_or_refused_abstains():
    other = make_case(base_events() + [_recusal(NEW, task=BRANCH + "-other")], github_of(authors=["codex-lead-1"]))
    assert_abstains(view_of(other), "recusal_unverified")
    case = make_case(base_events() + [_recusal(NEW)], github_of(authors=["codex-lead-1"]))
    case["snapshot"]["prs"][str(NUMBER)]["recusals"][0]["verification"] = "unverified"
    assert_abstains(view_of(case), "vote_row_refused")


def _recusal_row_case(change):
    """claude-rco-2's genuine recusal at the GitHub head, then its snapshot ROW made to disagree with what its event
    body says (Lead's W1, 10-16): ``change`` changes the row, or is another canonical event that the row then names."""
    case = make_case(base_events() + [_recusal(NEW)], github_of(authors=["codex-lead-1"]))
    [row] = case["snapshot"]["prs"][str(NUMBER)]["recusals"]
    if callable(change):
        change(row)
    else:
        case["bodies"][event_id(change)] = change
        row["source_refs"] = [event_id(change)]
    return case


def _rco2_body(kind="message", status="rco_recused", declared=True, **payload):
    return event("2026-10-13T17:31:00Z", "claude-rco-2", kind, status, {"pr": NUMBER, "head": NEW, **payload},
                 declared=declared)


def _row_names_rco1(row):
    row["agent"], row["agent_uuid"] = "claude-rco-1", UUIDS["claude-rco-1"]


@pytest.mark.parametrize("change", [
    _row_names_rco1,  # the row names another RCO than its body
    _rco2_body(status="acknowledged"),  # the body is no recusal
    _rco2_body(head=OLD),  # the body binds another head
    _rco2_body(pr=SECOND),  # the body is on another PR
    _rco2_body(kind="handoff"),  # not a kind the core reads as a recusal
    _rco2_body(recused=False),
    _rco2_body(canonical_task_id=BRANCH + "-other"),  # a declared task field disagrees with the task
    _rco2_body(declared=False),  # the row is declared, its body declares no identity
], ids=["agent", "status", "head", "pr", "type", "recused_false", "task_field", "undeclared_body"])
def test_lane_view_recusal_row_its_body_does_not_support_abstains(change):
    view = view_of(_recusal_row_case(change))
    assert_abstains(view, "recusal_unverified")
    assert view["prs"][str(NUMBER)]["excused"] == []


def test_lane_view_recusal_kinds_and_case_are_read_as_the_core_reads_them():
    recusal = event("2026-10-13T17:30:00Z", "claude-rco-2", "Message", "RCO_Recused", {"pr": NUMBER, "head": NEW})
    record = view_of(make_case(base_events() + [recusal], github_of(authors=["codex-lead-1"])))["prs"][str(NUMBER)]
    assert record["empty_slots"] == ["build_tools"] and {"agent": "claude-rco-2", "why": "rco_recused"} in record["excused"]
    # A decision is a recusal kind of the core too, so a row whose body is one is supported ...
    record = view_of(_recusal_row_case(_rco2_body(kind="decision")))["prs"][str(NUMBER)]
    assert record["empty_slots"] == ["build_tools"] and {"agent": "claude-rco-2", "why": "rco_recused"} in record["excused"]
    # ... but through the real core an RCO's decision rco_recused is also a veto observation (its conservative block
    # path), so that PR abstains anyway.
    decision = event("2026-10-13T17:30:00Z", "claude-rco-2", "decision", "rco_recused", {"pr": NUMBER, "head": NEW})
    assert_abstains(view_of(make_case(base_events() + [decision], github_of(authors=["codex-lead-1"]))), "veto_observed")


def test_lane_view_veto_observation_abstains():
    veto = event("2026-10-13T17:45:00Z", "claude-rco-1", "finding", "changes_requested", {"pr": NUMBER, "head": NEW})
    assert_abstains(view_of(make_case(base_events() + [veto])), "veto_observed")


def test_lane_view_event_body_missing_foreign_or_disagreeing_abstains():
    case = make_case()
    del case["bodies"][refs_of(base_events())[1]]
    assert_abstains(view_of(case), "event_body_missing")
    case = make_case()
    case["bodies"][refs_of(base_events())[1]] = dict(base_events()[1], ts_utc="2026-10-13T17:30:00Z")
    assert_abstains(view_of(case), "event_body_missing")  # a body that is not its ref's content is not available
    case = make_case()
    row = next(row for row in case["snapshot"]["prs"][str(NUMBER)]["stale_votes"] if row["agent"] == "codex-lead-1")
    row["agent"], row["agent_uuid"] = "codex-tools-1", UUIDS["codex-tools-1"]  # a row the body does not support
    assert_abstains(view_of(case), "vote_body_disagrees")


def test_lane_view_body_is_its_ref_content_and_not_after_the_as_of():
    row = base_events()[1]
    ref, now = event_id(row), lv.ws.parse_time(AS_OF)
    assert lv.body({ref: row}, ref, now)[0] is row
    assert lv.body({ref: dict(row, ts_utc="2026-10-13T18:00:01Z")}, ref, now) is None  # other content
    late = dict(row, ts_utc="2026-10-13T18:00:01Z")
    assert lv.body({event_id(late): late}, event_id(late), now) is None  # after the as-of
    assert lv.body({}, ref, now) is None


def test_lane_view_a_decision_type_is_required_for_a_vote():
    events = base_events()[:3] + [vote("2026-10-13T17:42:00Z", "claude-rco-2", kind="rco_review")]
    view = view_of(make_case(events, github_of(authors=["codex-lead-1"])))
    record = view["prs"][str(NUMBER)]
    assert [(row["agent"], row["reason"]) for row in record["not_credited"]] == [("claude-rco-2", "not_a_decision")]
    assert record["empty_slots"] == ["build_tools", "claude-rco-2"]
    line = view["lanes"]["claude-rco-2"]["actions"][0]
    assert "your vote row at this head is not credited (not_a_decision, ref " + refs_of(events)[3][:16] in line


def test_lane_view_the_only_empty_slot_comes_first_and_lines_are_limited(monkeypatch):
    events = base_events() + [vote("2026-10-13T16:30:00Z", "codex-lead-1", head=SECOND_HEAD, task=SECOND_BRANCH,
                                   number=SECOND)]
    github = merge_github(github_of(), github_of(SECOND_HEAD, number=SECOND, branch=SECOND_BRANCH,
                                                 authors=["codex-tools-1"], completed="2026-10-13T16:00:00Z"))
    view = view_of(make_case(events, github))
    tools = view["lanes"]["codex-tools-1"]["actions"]
    # PR1906 waits longer (16:30:00Z) but has three empty slots; the only empty slot (PR1905) comes first.
    assert [line.split(" at ")[0] for line in tools] == [f"build vote on PR{NUMBER}", f"build vote on PR{SECOND}"]
    assert "is the only empty slot" in tools[0] and "also empty: claude-rco-1, claude-rco-2" in tools[1]
    assert "waiting since 2026-10-13T16:30:00Z" in tools[1]
    for rco in ("claude-rco-1", "claude-rco-2"):
        assert [line.split(" at ")[0] for line in view["lanes"][rco]["actions"]] == [f"RCO vote on PR{SECOND}"]
    assert not view["lanes"]["codex-lead-1"]["actions"] and view["counts"]["action"] == 2
    monkeypatch.setattr(lv, "LINE_LIMIT", 1)
    limited = view_of(make_case(events, github))["lanes"]["codex-tools-1"]
    assert limited == {"actions": tools[:1], "more": 1}


def test_lane_view_blocked_bridge_input_abstains_every_pr():
    case = make_case()
    case["snapshot"] = snapshot_of(base_events(), blocked="log_truncated")
    view = view_of(case)
    assert_abstains(view, "bridge_input_blocked")
    assert view["coverage"] == "UNKNOWN"


def test_lane_view_coverage_is_named_only_when_bounded():
    case = make_case()
    case["snapshot"] = snapshot_of(base_events(), log_identity="bridge-log:" + "0" * 64)
    view = view_of(case)
    assert view["coverage"] == "complete"
    line = view["lanes"]["codex-tools-1"]["actions"][0]
    assert line.endswith("the vote is still required") and "coverage UNKNOWN" not in line


def test_lane_view_names_a_pr_row_the_workspace_refuses():
    case = make_case()
    case["snapshot"]["prs"][str(NUMBER)]["pr"] = NUMBER + 1  # a row whose number is not its key
    view = view_of(case)
    assert not view["prs"] and not view["abstained"] and not any(entry["actions"] for entry in view["lanes"].values())
    assert [row["where"] for row in view["refused_rows"]] == ["prs[0]"]
    assert "refused snapshot rows: 1" in lv.render_text(view, "codex-tools-1")


def test_lane_view_pr_that_is_not_open_has_no_line_and_no_abstention():
    view = view_of(make_case(github=github_of(state="MERGED")))
    assert view["prs"][str(NUMBER)]["state"] == "not_open" and not view["abstained"]
    assert not any(entry["actions"] for entry in view["lanes"].values())


def test_lane_view_route_is_unknown_without_a_declared_owner():
    events = base_events()
    del events[2]["payload"]["merge_owner"]
    line = view_of(make_case(events))["lanes"]["codex-tools-1"]["actions"][0]
    assert "route UNKNOWN; the vote is still required" in line


def test_lane_view_is_deterministic_and_free_of_input_order():
    first = lv.canonical(view_of(make_case()))
    case = make_case()
    for row in case["snapshot"]["prs"].values():
        for name in ("votes", "stale_votes", "source_refs"):
            row[name] = list(reversed(row[name]))
    case["bodies"] = dict(reversed(list(case["bodies"].items())))
    assert lv.canonical(view_of(case)) == first == lv.canonical(view_of(make_case()))


def test_lane_view_does_not_change_its_inputs():
    case = make_case()
    before = lv.canonical({key: case[key] for key in ("snapshot", "github", "bodies")})
    view_of(case)
    assert lv.canonical({key: case[key] for key in ("snapshot", "github", "bodies")}) == before


def test_lane_view_never_claims_merge_readiness():
    for case in (make_case(), mut.check_result(make_case(), NUMBER, "unified"), mut.drop_github(make_case())):
        view = view_of(case)
        texts = [lv.render_text(view, lane) for lane in lv.LANES] + [lv.canonical(view)]
        assert not any(word in text.lower() for word in FORBIDDEN for text in texts)


def test_lane_view_line_limit_keeps_a_few_lines(monkeypatch):
    monkeypatch.setattr(lv, "LINE_LIMIT", 0)
    entry = view_of(make_case())["lanes"]["codex-tools-1"]
    assert entry == {"actions": [], "more": 1}


def test_lane_view_guard_refuses_a_guarded_word_in_the_output(monkeypatch):
    monkeypatch.setitem(lv.REASON_TEXT, "veto_observed", "veto " + core._MARKERS[0].lower())
    veto = event("2026-10-13T17:45:00Z", "claude-rco-1", "finding", "changes_requested", {"pr": NUMBER, "head": NEW})
    with pytest.raises(lv.LaneViewError):
        view_of(make_case(base_events() + [veto]))


def test_lane_view_text_render_guards_its_own_lines():
    view = view_of(make_case())
    view["lanes"]["codex-tools-1"]["actions"][0] += " " + core._MARKERS[1].lower()
    with pytest.raises(lv.LaneViewError):
        lv.render_text(view, "codex-tools-1")
    assert lv.render_text(view, "codex-lead-1").splitlines()[1] == "no action line"


def test_lane_view_needs_an_explicit_as_of_not_older_than_the_snapshot():
    case = make_case()
    with pytest.raises(lv.LaneViewError):
        lv.lane_view(case["snapshot"], case["github"], case["bodies"], as_of_utc="now")
    with pytest.raises(lv.LaneViewError):
        lv.lane_view(case["snapshot"], case["github"], case["bodies"], as_of_utc="2026-10-13T17:59:59Z")


# --- Frozen cycles, synthetic (no data disk) ------------------------------------------------------------------------
# The reference tests here loaded the frozen P1 copies from the sandbox data disk when present. The release suite
# reads no real data: the same two checks run on a synthetic wd.mind-frozen-cycles.v1 root built in a temporary
# directory and read through the real loader (manifest verification, null reporting, the shared body store), with a
# synthetic labelled case of the PR1905 shape. This proves the code on synthetic input only; it claims nothing about
# the historical cycles.

def frozen_cycle_bytes(events, github, as_of):
    """The six files of one synthetic frozen cycle: objects canonical through the core, the two raw gh lists in the
    canonical form their links are computed from, the core snapshot in its publication envelope."""
    snapshot = snapshot_of(events, as_of)
    publication = {"schema": "wd.bridge-mind-publication.v1", "snapshot": snapshot,
                   "snapshot_sha256": hashlib.sha256(core.canonical_bytes(snapshot)).hexdigest()}
    return {"capture.json": core.canonical_bytes({"events": events}),
            "context.json": core.canonical_bytes(github["context"]),
            "observed-status.json": core.canonical_bytes(github["status"]),
            "gh-pr-list.json": lv.canonical(github["pr_list"]).encode("utf-8"),
            "gh-open-status.json": lv.canonical(github["open_status"]).encode("utf-8"),
            "run\\reflected.publication.json": core.canonical_bytes(publication)}


def write_frozen_root(root, cycles):
    """A synthetic frozen root at ``root``; ``cycles`` holds (name, events, github, as_of, keep_capture) tuples. A cycle
    without its capture gets a null manifest entry, as a capture pruned before the copy."""
    entries = []
    for name, events, github, as_of, keep_capture in cycles:
        files = {}
        for file_name, data in frozen_cycle_bytes(events, github, as_of).items():
            if file_name == "capture.json" and not keep_capture:
                files[file_name] = None
                continue
            path = root.joinpath(name, *file_name.split("\\"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            files[file_name] = hashlib.sha256(data).hexdigest()
        entries.append({"cycle": name, "files": files})
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_bytes(core.canonical_bytes({"schema": "wd.mind-frozen-cycles.v1",
                                                               "cycles": entries}))
    return root


def synthetic_cycles(events=None, count=8, captured=2):
    """``count`` cycles five minutes apart from 18:00Z, GitHub observed five minutes before each; only the last
    ``captured`` cycles keep their capture."""
    events = base_events() if events is None else events
    cycles = []
    for index in range(count):
        as_of = f"2026-10-13T18:{5 * index:02d}:00Z"
        observed = "2026-10-13T17:55:00Z" if index == 0 else f"2026-10-13T18:{5 * (index - 1):02d}:00Z"
        keep = index >= count - captured
        cycles.append((f"synthetic-{index}", events, github_of(observed=observed), as_of, keep))
    return cycles


def frozen_events():
    """The synthetic PR1905 events plus one ordinary Tools message on its own task, so the owning lane is a lane of
    the snapshot (the acceptance controls look its uuid up); the PR rows are those of ``base_events``."""
    return base_events() + [event("2026-10-13T17:30:00Z", "codex-tools-1", "message", "in_progress", {},
                                  task="codex-tools-1/lane-work-20261013")]


def synthetic_labels(events):
    """A labelled case of the PR1905 shape for the synthetic events: what acceptance 1a reads, nothing more."""
    refs = refs_of(events)
    return {"facts_in_input": {"github_head": NEW, "bridge_view_head": OLD, "older_request": {"ref": refs[0]},
                               "votes_at_github_head": [{"agent": "claude-rco-1", "ref": refs[1]},
                                                        {"agent": "codex-lead-1", "ref": refs[2]},
                                                        {"agent": "claude-rco-3", "ref": refs[3]}],
                               "commit_authors": list(AUTHORS)},
            "expected": {"top_action": f"build vote on PR{NUMBER} at {NEW[:12]} is the only empty slot",
                         "owning_lane": "codex-tools-1",
                         "must_cite": [refs[2][:16], refs[1][:16], f"GitHub 6/6 at {NEW[:12]}"],
                         "waiting_since_utc": "2026-10-13T17:40:44Z",
                         "coverage_wording_if_request_inventory_bounded": "coverage UNKNOWN"}}


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    import lane_view_frozen as fz
    import run_acceptance_1a as acc
    events = frozen_events()
    data = fz.load_frozen(write_frozen_root(tmp_path_factory.mktemp("frozen") / "cycles", synthetic_cycles(events)))
    labels = acc.Labels(synthetic_labels(events))
    return data, labels, acc


def test_lane_view_frozen_manifest_nulls_are_reported_and_bodies_resolve(frozen):
    data, labels, acc = frozen
    assert len(data["cycles"]) == 8
    assert [case["nulls"] for case in data["cycles"]] == [["capture.json"]] * 6 + [[], []]
    assert set(labels.votes.values()) | {labels.old_request} <= set(data["bodies"])


def test_lane_view_frozen_pr1905_line_must_not_and_controls_in_every_cycle(frozen):
    data, labels, acc = frozen
    for case in data["cycles"]:
        view = acc.run_view(case)
        result, view_a = acc.controls(case, labels)
        assert acc.positive(view, labels)["pass"], case["cycle"]
        assert all(acc.must_not(view, labels, view_a).values()), case["cycle"]
        # The frozen acceptance helper's b/c summaries hardcode two historical vote rows. This synthetic
        # quorum now contains three (Lead and two RCOs); retain the other controls and verify b/c's actual
        # unchanged binding properties with the current exact vote count, never count an excusal as a vote.
        assert all(item["pass"] for name, item in result.items() if name not in ("b", "c")), case["cycle"]
        refs = sorted(labels.votes.values())
        for field, value, reason in (("task", BRANCH + "-moved", "other_task"),
                                     ("head", labels.old_head, "other_head")):
            moved, renamed = mut.rebind_events(case, labels.number, refs, **{field: value})
            changed = acc.run_view(moved)
            record = changed["prs"][str(labels.number)]
            assert not any(acc.pr_lines(changed, labels.number).values()) and not record["credited"]
            assert sorted(row["reason"] for row in record["not_credited"] if row["ref"] in renamed.values()) == [
                reason] * len(refs)
        assert all(acc.case_facts(case, view, labels).values()), case["cycle"]
