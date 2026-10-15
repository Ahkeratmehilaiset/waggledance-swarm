"""Bridge mind reflective workspace v0 over wd.bridge-mind.v1: derived, referenced, deterministic, observe-only.

Probe ids: P1-P13 from the RCO2 adversarial probe plan, R1-R7 from the RCO1 additions (bridge mind spec v0.2),
P6b/P6c from the RCO2 currentness probes and L1-L5 from the RCO1 privacy probes of the PR1899 repair.
The fixtures have the exact shape of the projector core (``tools/wd_bridge_mind.py``, ``snapshot()``); the twin
test feeds an actual core output through the workspace.
"""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random

import pytest

from tools import bridge_mind_workspace as mind
from tools.bridge_compact_view import event_id

# Event time far from the wall clock: a derivation that read the clock would see every lease as ended.
AS_OF = "2026-01-01T00:30:00Z"
HEAD = "a" * 40
OLD = "b" * 40
# Built from parts, as the repo's other guard tests do, so this file never carries the literal tokens.
GUARD_WORDS = ("PRIVATE_" + "MARKER", "_DO_NOT_" + "LEAK")
UUIDS = {agent: f"00000000-0000-4000-8000-{index:012x}" for index, agent in enumerate(
    ("claude-rco-1", "claude-rco-2", "codex-lead-1", "codex-tools-1", "fable-5"), 1)}
RCOS = ("claude-rco-1", "claude-rco-2")
# Every slot but claude-rco-2's (the last missing vote), and every slot.
ALMOST = ["codex-lead-1", "codex-tools-1", "claude-rco-1"]
EVERY = ALMOST + ["claude-rco-2"]


def ref(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _expired(lease):
    return "UNKNOWN" if lease == "UNKNOWN" else _time(lease) <= _time(AS_OF)


def _task_claim(paths, expires, name, task=None):
    """One core ``claims.by_task`` row."""
    return {"task_id": task or f"task-{name}", "write_scope": list(paths) if paths is not None else "UNKNOWN",
            "source_refs": [ref(f"claim:{name}")], "verification": "declared", "lease_expires_utc": expires,
            "expired": _expired(expires), "owner_session_id": "UNKNOWN", "owner_run_id": "UNKNOWN"}


def _claims(*tasks):
    """The core's lane claims: the by_task rows sorted by task id, and the lane-level fields they give."""
    scopes = [task["write_scope"] for task in tasks]
    leases = [task["lease_expires_utc"] for task in tasks]
    lease = leases[0] if leases and all(value == leases[0] for value in leases) else "UNKNOWN"
    union = sorted({path for scope in scopes for path in scope}) if all(isinstance(s, list) for s in scopes) \
        else "UNKNOWN"
    return {"write_scope": union, "source_refs": sorted({r for task in tasks for r in task["source_refs"]})
            or [ref("claims:none")], "verification": "declared", "lease_expires_utc": lease, "expired": _expired(lease),
            "by_task": sorted(tasks, key=lambda task: task["task_id"])}


def _claim(paths, expires, name):
    """A lane holding one task claim."""
    return _claims(_task_claim(paths, expires, name))


def _lane(agent, claim=None, uuid=None):
    """One core lane record and its key (agent|uuid)."""
    uuid = uuid or UUIDS[agent]
    claims = claim if claim is not None else "UNKNOWN"
    refs = {ref(f"lane:{agent}:{uuid}")} | (set(claim["source_refs"]) if claim else set())
    return f"{agent}|{uuid}", {
        "agent": agent, "agent_uuid": uuid, "session_id": "UNKNOWN", "run_id": "UNKNOWN", "current_task": "UNKNOWN",
        "claims": claims, "waits": "UNKNOWN", "last_event_ref": ref(f"lane:{agent}:{uuid}"), "last_event_utc": AS_OF,
        "source_refs": sorted(refs), "verification": "declared", "idle_status": "UNKNOWN",
        "uncertainty": ["origin_not_authenticated", "native_readiness_UNKNOWN"], "observation_age_seconds": 0.0,
        "next_question": "UNKNOWN"}


def _vote(agent, number, head, status=None):
    status = status or ("rco_pass" if agent in RCOS else "build_consensus_pass")
    return {"agent": agent, "agent_uuid": UUIDS[agent], "head": head, "status": status,
            "source_refs": [ref(f"vote:{number}:{agent}:{status}:{head}")], "verification": "declared"}


def _veto(agent, number, head, status="changes_requested"):
    return {"agent": agent, "agent_uuid": UUIDS[agent], "head": head, "status": status, "activity": "UNKNOWN",
            "source_refs": [ref(f"veto:{number}:{agent}:{head}")], "verification": "declared"}


def _pr(number, *, head=HEAD, ci=True, ci_head=None, klass="b", votes=(), stale=(), vetoes=()):
    """One core PR record and its key. ``votes`` are agents voting at the head, ``stale`` (agent, head) pairs."""
    reported = {"status": "UNKNOWN"} if ci is None else {
        "status": "reported_green" if ci else "reported_not_green", "head": ci_head or head,
        "source_refs": [ref(f"ci:{number}")], "verification": "declared"}
    return str(number), {
        "pr": number, "head": head, "base": "UNKNOWN", "reported_ci": reported, "source_refs": [ref(f"pr:{number}")],
        "class": klass, "votes": [_vote(agent, number, head) for agent in votes],
        "stale_votes": [_vote(agent, number, vote_head) for agent, vote_head in stale],
        "veto_observations": [_veto(agent, number, head) for agent in vetoes], "missing": ["gate_eligibility_UNKNOWN"]}


def _command(request_id, observed, expected, answered=(), ttl=86400):
    return {"fact_id": ref(f"command:{request_id}:id"), "kind": "command",
            "value": {"request_id": request_id, "from": "claude-rco-1",
                                         "expected_responders": list(expected), "answered_by": list(answered)},
            "source_refs": [ref(f"command:{request_id}")], "canonical_sha256": ref(f"command:{request_id}"),
            "observed_at_utc": observed, "ttl_seconds": ttl, "salience": 50, "verification": "declared"}


def _snapshot(coverage="complete", blocked=None, prs=(), lanes=(), facts=()):
    return {"schema": "wd.bridge-mind.v1", "authority": "none", "scope": "bounded_projection_not_gate",
            "source": {"log_identity": "fixture-log", "generation": "g1", "cursor_start": 0, "cursor_end": 300,
                       "coverage": coverage, "event_count": 3, "as_of_utc": AS_OF, "unique_event_count": 3,
                       "duplicate_count": 0, "last_event_ref": ref("source"), "max_event_utc": AS_OF,
                       "blocked": blocked},
            "prs": dict(prs), "lanes": dict(lanes), "facts": list(facts), "tensions": [], "attention": [],
            "next_question": {"lanes": {}, "global": {"question": "UNKNOWN", "attention_rank": None,
                                                      "refs": ["UNKNOWN"]}}}


def _rules(fields):
    return {tension["rule"]: tension for tension in fields["tensions"]}


def test_each_tension_rule_is_derived_with_its_refs():
    """P6 stale vote, P7 expired claim is not done, claim overlap, veto observed, unacknowledged command, R5 blocked."""
    rco2_claim = _claim(["tools\\Board.py"], "2026-01-01T01:00:00Z", "rco2")
    fable_claim = _claim(["./tools/board.py", "tests/x.py"], "2026-01-01T01:00:00Z", "fable")
    lanes = [_lane("claude-rco-2", rco2_claim), _lane("fable-5", fable_claim),
             # One agent's other session overlapping its own claim is not a tension.
             _lane("fable-5", _claim(["tests"], "2026-01-01T01:00:00Z", "fable-old"),
                   uuid="00000000-0000-4000-8000-0000000000ff"),
             _lane("codex-tools-1", _claim(["docs/old.md"], AS_OF, "tools"))]
    prs = [_pr(7, votes=["codex-lead-1"], stale=[("claude-rco-2", OLD)], vetoes=["claude-rco-1"])]
    facts = [_command("req-old", "2026-01-01T00:15:00Z", ["fable-5", "codex-lead-1"], ["codex-lead-1"])]
    blocked = {"active": True, "reason": "invalid_json", "offset": 4096}
    fields = mind.reflection(_snapshot(blocked=blocked, lanes=lanes, prs=prs, facts=facts))
    rules = _rules(fields)
    assert set(rules) == {"claim_overlap", "expired_claim", "stale_vote", "veto_observed", "unacknowledged_command",
                          "input_blocked"}
    assert [tension["rule"] for tension in fields["tensions"]].count("claim_overlap") == 1
    overlap = rules["claim_overlap"]
    assert overlap["actors"] == ["claude-rco-2", "fable-5"] and overlap["severity"] == "high"
    assert overlap["refs"] == sorted([ref("claim:rco2"), ref("claim:fable")])
    assert overlap["observation"] == "overlapping paths: tools/board.py"
    assert rules["expired_claim"]["subject"] == "claim:codex-tools-1|" + UUIDS["codex-tools-1"] + ":task-tools"
    assert "not completed work" in rules["expired_claim"]["observation"]
    assert rules["stale_vote"]["actors"] == ["claude-rco-2"]
    assert rules["stale_vote"]["refs"] == sorted([ref(f"vote:7:claude-rco-2:rco_pass:{OLD}"), ref("pr:7")])
    veto = rules["veto_observed"]
    assert veto["actors"] == ["claude-rco-1"] and veto["severity"] == "high"
    assert veto["refs"] == sorted([ref(f"veto:7:claude-rco-1:{HEAD}"), ref("pr:7")])
    assert "UNKNOWN, never cleared" in veto["observation"]
    assert rules["unacknowledged_command"]["actors"] == ["fable-5"]
    assert rules["unacknowledged_command"]["refs"] == [ref("command:req-old")]
    assert rules["input_blocked"]["subject"] == "offset:4096" and rules["input_blocked"]["refs"] == [ref("source")]
    assert "invalid_json" in rules["input_blocked"]["observation"]
    assert all(tension["lifecycle"] == "new" and len(tension["tension_id"]) == 16 for tension in fields["tensions"])


def test_lease_ttl_and_grace_boundaries_are_pure_functions_of_as_of():
    """P2: the boundary is decided by as_of alone, on both sides."""
    lanes = [_lane("fable-5", _claim(["a.py"], AS_OF, "at")),
             _lane("codex-tools-1", _claim(["b.py"], "2026-01-01T00:30:00.000001Z", "after"))]
    facts = [
        _command("grace-exact", "2026-01-01T00:15:00Z", ["fable-5"]),
        _command("grace-short", "2026-01-01T00:15:01Z", ["fable-5"]),
        _command("ttl-ended", "2026-01-01T00:00:00Z", ["fable-5"], ttl=1800),
        _command("ttl-live", "2026-01-01T00:00:00Z", ["fable-5"], ttl=1801),
    ]
    tensions = mind.reflection(_snapshot(lanes=lanes, facts=facts))["tensions"]
    subjects = sorted(tension["subject"] for tension in tensions)
    assert subjects == ["claim:fable-5|" + UUIDS["fable-5"] + ":task-at", "request:grace-exact", "request:ttl-live"]


@pytest.mark.parametrize("as_of, lease, rules", [
    ("2026-01-01T00:30:00.0000001Z", "2026-01-01T00:30:00.0000009Z", ["expired_claim"]),
    ("2026-01-01T00:30:00.000000100Z", "2026-01-01T00:30:00.000000900Z", ["expired_claim"]),
    ("2026-01-01T00:30:00.0000001Z", "2026-01-01T00:30:00.0000019Z", []),
], ids=["7_digits_same_microsecond", "9_digits_same_microsecond", "7_digits_next_microsecond"])
def test_sub_microsecond_times_follow_the_core_time_rule_and_a_matching_claim_is_kept(as_of, lease, rules):
    """PR1901 Codex P2 thread PRRT_kwDORYf3RM6rxSH7, not reproduced against the core: the core decides ``expired``
    with ``datetime.fromisoformat``, which cuts every fraction digit after the sixth, and the workspace cuts the same
    way. Keeping finer digits only here would make the two disagree; this pins the agreement. A claim whose
    ``expired`` the core computed by that rule is kept: expired when both cut to the same microsecond, live (no
    tension) one microsecond later, and never refused as contradictory."""
    for text in (as_of, lease):
        assert mind.parse_time(text) == datetime.fromisoformat(text.replace("Z", "+00:00"))
    core_expired = datetime.fromisoformat(lease.replace("Z", "+00:00")) <= \
        datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    claims = _claim(["a.py"], lease, "sub")
    claims["expired"] = claims["by_task"][0]["expired"] = core_expired
    snapshot = _snapshot(lanes=[_lane("fable-5", claims)])
    snapshot["source"]["as_of_utc"] = snapshot["source"]["max_event_utc"] = as_of
    tensions = mind.reflection(snapshot)["tensions"]
    assert sorted(tension["rule"] for tension in tensions) == rules
    assert [tension["subject"] for tension in tensions] == \
        [f"claim:fable-5|{UUIDS['fable-5']}:task-sub"] * len(rules)


def test_attention_puts_the_last_missing_ci_green_vote_first_and_is_only_a_suggestion():
    prs = [
        _pr(30, votes=["codex-tools-1"]),
        _pr(20, klass="a", votes=EVERY),
        _pr(21, klass="b", votes=EVERY),  # every slot and not (a)-class: no suggestion, never "merge"
        _pr(40, votes=ALMOST, ci=False),
        _pr(42, votes=ALMOST, ci=None),
        _pr(45, votes=ALMOST, vetoes=["claude-rco-1"]),
        _pr(46, klass="a", votes=EVERY, vetoes=["claude-rco-1"]),  # a veto is never "only the signature"
        _pr(47, klass="a", votes=EVERY, ci=False),
        _pr(10, votes=ALMOST),
        _pr(50, head="UNKNOWN"),  # the core puts every vote of a PR with an unknown head into stale_votes
    ]
    fields = mind.reflection(_snapshot(prs=prs))
    order = [(item["kind"], item["subject"]) for item in fields["attention"]]
    assert order[:3] == [("last_missing_vote", "pr:10"), ("signature_only", "pr:20"), ("votes_missing", "pr:30")]
    assert [kind for kind, _ in order[3:]] == ["tension_high", "tension_high"]
    assert sorted(tension["subject"] for tension in fields["tensions"]) == [
        f"pr:45:veto:claude-rco-1:{HEAD[:12]}", f"pr:46:veto:claude-rco-1:{HEAD[:12]}"]
    assert [item["rank"] for item in fields["attention"]] == [1, 2, 3, 4, 5]
    first = fields["attention"][0]
    assert first["actors"] == ["claude-rco-2"]
    assert first["refs"] == sorted([ref("pr:10"), ref("ci:10"), ref(f"vote:10:claude-rco-1:rco_pass:{HEAD}")] + [
        ref(f"vote:10:{agent}:build_consensus_pass:{HEAD}") for agent in ("codex-lead-1", "codex-tools-1")])
    assert fields["attention"][2]["actors"] == ["claude-rco-1", "claude-rco-2", "codex-lead-1"]
    # P10: a suggestion never carries clearance.
    assert all(item["suggestion_only"] is True for item in fields["attention"])
    text = mind.canonical_json(fields).lower()
    assert "approved" not in text and "clear to merge" not in text and "cleared" not in text.replace("never cleared", "")


@pytest.mark.parametrize("reported, expected", [
    ({"status": "reported_green", "head": HEAD}, "last_missing_vote"),
    ({"status": "reported_green", "head": OLD}, None),
    ({"status": "reported_green"}, None),
    ({"status": "reported_not_green", "head": HEAD}, None),
    ({"status": "UNKNOWN"}, None),
    ({"status": "reported_green", "head": HEAD, "source_refs": []}, None),
])
def test_ci_counts_as_green_only_when_reported_green_at_the_exact_head(reported, expected):
    """P6b: a green report without a head, for another head, or without refs makes no vote suggestion."""
    key, pr = _pr(10, votes=ALMOST)
    pr["reported_ci"] = {"source_refs": [ref("ci:10")], "verification": "declared", **reported}
    kinds = [item["kind"] for item in mind.reflection(_snapshot(prs=[(key, pr)]))["attention"]]
    assert kinds == ([expected] if expected else [])


@pytest.mark.parametrize("blocked", [
    {"active": True, "reason": "invalid_json", "offset": 4096},
    {"active": True},
    {"active": False},
    "yes",
])
def test_a_blocked_source_makes_no_head_bound_suggestion(blocked):
    """P6c: blocked input (or a blocked field that is not the core shape) is not current, even with complete coverage."""
    prs = [_pr(10, votes=ALMOST), _pr(20, klass="a", votes=EVERY)]
    fields = mind.reflection(_snapshot(blocked=blocked, prs=prs))
    assert [item["kind"] for item in fields["attention"]] == ["tension_high"]
    assert [tension["rule"] for tension in fields["tensions"]] == ["input_blocked"]
    assert fields["next_question"]["global"]["question"].startswith("How can the fleet resolve input_blocked:")
    assert "exact head" not in mind.canonical_json(fields["next_question"])
    # The unblocked and the unknown-coverage controls keep their suggestions, and stay distinct.
    unblocked = mind.reflection(_snapshot(prs=prs))
    assert [item["kind"] for item in unblocked["attention"]] == ["last_missing_vote", "signature_only"]
    partial = mind.reflection(_snapshot(coverage="unknown", prs=prs))
    assert [item["kind"] for item in partial["attention"]][:2] == ["last_missing_vote", "signature_only"]
    assert partial["next_question"]["global"]["question"].endswith("check the exact head first.")


def test_a_veto_is_never_read_as_cleared_and_an_undeclared_identity_fills_no_slot():
    key, pr = _pr(10, votes=ALMOST)
    unreadable = copy.deepcopy(pr)
    unreadable["veto_observations"] = [{**_veto("claude-rco-1", 10, HEAD), "source_refs": ["not-a-ref"]}]
    fields = mind.reflection(_snapshot(prs=[(key, unreadable)]))
    assert fields["attention"] and all(item["kind"] != "last_missing_vote" for item in fields["attention"])
    assert [tension["subject"] for tension in fields["tensions"]] == ["prs[0].veto_observations[0]"]
    no_head = copy.deepcopy(pr)
    no_head["veto_observations"] = [_veto("claude-rco-1", 10, "UNKNOWN")]
    rules = _rules(mind.reflection(_snapshot(prs=[(key, no_head)])))
    assert set(rules) == {"veto_observed"} and rules["veto_observed"]["subject"] == "pr:10:veto:claude-rco-1:UNKNOWN"
    # An undeclared identity as the core emits it (agent_uuid UNKNOWN, verification unverified) fills no slot.
    undeclared = copy.deepcopy(pr)
    undeclared["votes"].append({**_vote("claude-rco-2", 10, HEAD), "agent_uuid": "UNKNOWN",
                                "verification": "unverified"})
    fields = mind.reflection(_snapshot(prs=[(key, undeclared)]))
    assert [(item["kind"], item["actors"]) for item in fields["attention"]] == [("last_missing_vote", ["claude-rco-2"])]
    assert fields["tensions"] == []
    # A row whose agent_uuid and verification disagree is refused by position and fills no slot either.
    for disagreeing in ({"agent_uuid": "UNKNOWN"}, {"verification": "unverified"}, {"verification": "UNKNOWN"}):
        refused = copy.deepcopy(pr)
        refused["votes"].append({**_vote("claude-rco-2", 10, HEAD), **disagreeing})
        fields = mind.reflection(_snapshot(prs=[(key, refused)]))
        assert (fields["attention"][0]["kind"], fields["attention"][0]["actors"]) == ("last_missing_vote", ["claude-rco-2"])
        assert [tension["subject"] for tension in fields["tensions"]] == ["prs[0].votes[3]"]
    declared = copy.deepcopy(pr)
    declared["votes"].append(_vote("claude-rco-2", 10, HEAD))
    assert mind.reflection(_snapshot(prs=[(key, declared)]))["attention"] == []
    # A vote at another head in the current list (a core inconsistency) never fills a slot; it is a stale vote.
    misplaced = copy.deepcopy(pr)
    misplaced["votes"].append(_vote("claude-rco-2", 10, OLD))
    fields = mind.reflection(_snapshot(prs=[(key, misplaced)]))
    assert [(item["kind"], item["actors"]) for item in fields["attention"]][0] == ("last_missing_vote", ["claude-rco-2"])
    assert [tension["rule"] for tension in fields["tensions"]] == ["stale_vote"]
    # The same veto seen in two rows (two tasks) is one tension with both refs, whatever the order.
    twice = copy.deepcopy(pr)
    twice["veto_observations"] = [_veto("claude-rco-1", 10, HEAD),
                                  {**_veto("claude-rco-1", 10, HEAD), "source_refs": [ref("veto:other-task")]}]
    for order in (twice["veto_observations"], twice["veto_observations"][::-1]):
        rules = _rules(mind.reflection(_snapshot(prs=[(key, {**twice, "veto_observations": order})])))
        assert rules["veto_observed"]["refs"] == sorted([ref(f"veto:10:claude-rco-1:{HEAD}"), ref("veto:other-task"),
                                                         ref("pr:10")])


@pytest.mark.parametrize("klass", ["a", "b"])
def test_one_rco_pass_never_completes_the_rco_requirement(klass):
    """RCO2 P10b: the rule-12 policy needs every present eligible RCO, and the snapshot has no presence or author."""
    for voters, missing in ((ALMOST, "claude-rco-2"), (["codex-lead-1", "codex-tools-1", "claude-rco-2"], "claude-rco-1")):
        attention = mind.reflection(_snapshot(prs=[_pr(10, klass=klass, votes=voters)]))["attention"]
        assert [(item["kind"], item["actors"]) for item in attention] == [("last_missing_vote", [missing])]
        assert attention[0]["detail"].endswith("missing: " + missing)
    every = mind.reflection(_snapshot(prs=[_pr(10, klass=klass, votes=EVERY)]))["attention"]
    assert [item["kind"] for item in every] == (["signature_only"] if klass == "a" else [])


def test_a_question_per_lane_plus_one_global_and_unknown_where_unsupported():
    """P7: a lane without an item is UNKNOWN, never idle."""
    prs = [_pr(10, votes=ALMOST), _pr(20, klass="a", votes=EVERY)]
    lanes = [_lane("claude-rco-2"), _lane("codex-tools-1")]
    question = mind.reflection(_snapshot(prs=prs, lanes=lanes))["next_question"]
    assert question["global"]["attention_rank"] == 1
    assert question["global"]["question"] == ("PR10 at aaaaaaaaaaaa is reported CI-green; missing: claude-rco-2. "
                                              "Can claude-rco-2 give it at this exact head?")
    assert sorted(question["lanes"]) == ["claude-rco-2", "codex-tools-1", "operator"]
    assert question["lanes"]["operator"]["attention_rank"] == 2
    assert question["lanes"]["codex-tools-1"] == {"question": "UNKNOWN", "attention_rank": None, "refs": ["UNKNOWN"]}
    assert "idle" not in mind.canonical_json(question)
    empty = mind.reflection(_snapshot())["next_question"]
    assert empty == {"global": {"question": "UNKNOWN", "attention_rank": None, "refs": ["UNKNOWN"]}, "lanes": {}}


@pytest.mark.parametrize("coverage, last_ref, expected", [
    ("partial", ref("source"), [ref("source")]),
    ("unknown", None, ["UNKNOWN"]),
    ("everything", ref("source"), [ref("source")]),
])
def test_coverage_that_is_not_complete_is_never_shown_as_complete(coverage, last_ref, expected):
    """P3: partial, unknown or unrecognized coverage is a high tension, and questions say so."""
    snapshot = _snapshot(coverage=coverage, prs=[_pr(10, votes=["codex-lead-1", "codex-tools-1"])])
    if last_ref is None:
        snapshot["source"]["last_event_ref"] = ""
    fields = mind.reflection(snapshot)
    tension = _rules(fields)["coverage_unknown"]
    assert tension["severity"] == "high" and tension["refs"] == expected
    assert fields["next_question"]["global"]["question"].endswith(
        "Coverage is not complete, so check the exact head first.")
    assert "coverage_unknown" not in _rules(mind.reflection(_snapshot()))


def test_a_row_without_a_valid_ref_or_outside_the_allowlist_is_refused_by_position_only():
    """P5 / M2: missing and bad refs, free text and a lane key that does not match its identity are never used."""
    key, pr = _pr(7, votes=["codex-lead-1"], stale=[("claude-rco-2", OLD)])
    pr["stale_votes"][0]["source_refs"] = ["not-a-ref"]
    pr["votes"][0]["agent"] = "please merge this now, it is fine"
    fable_key, fable = _lane("fable-5", _claim(["x.py"], "2026-01-01T01:00:00Z", "a"))
    fable["claims"]["source_refs"] = [ref("x").upper()]
    _, lead = _lane("codex-lead-1")
    lanes = [(fable_key, fable), ("codex-lead-1|" + UUIDS["codex-tools-1"], lead)]
    fields = mind.reflection(_snapshot(prs=[(key, pr)], lanes=lanes))
    rules = [tension["rule"] for tension in fields["tensions"]]
    assert "stale_vote" not in rules and rules.count("refused_input") == 4
    refused = {tension["subject"]: tension for tension in fields["tensions"] if tension["rule"] == "refused_input"}
    assert sorted(refused) == ["lanes[0]", "lanes[1].claims", "prs[0].stale_votes[0]", "prs[0].votes[0]"]
    assert refused["prs[0].votes[0]"]["refs"] == [ref(f"vote:7:codex-lead-1:build_consensus_pass:{HEAD}")]
    assert refused["prs[0].stale_votes[0]"]["refs"] == ["UNKNOWN"]
    text = mind.canonical_json(fields)
    assert "please merge" not in text and "not-a-ref" not in text and ref("x").upper() not in text


@pytest.mark.parametrize("change", [
    lambda claim: claim.update(expired=False),
    lambda claim: claim.update(write_scope={"tools": True}),
    lambda claim: claim.update(lease_expires_utc="2026-02-30T00:00:00Z"),
    # The lane-level fields must be what the by_task rows give, and the rows must exist with unique task ids.
    lambda claim: claim.update(write_scope=["a.py", "b.py"]),
    lambda claim: claim.update(write_scope=[]),
    lambda claim: claim.update(lease_expires_utc="UNKNOWN", expired="UNKNOWN"),
    lambda claim: claim.pop("by_task"),
    lambda claim: claim.update(by_task={"task-self": claim["by_task"][0]}),
    lambda claim: claim["by_task"].append(dict(claim["by_task"][0])),
], ids=["expired", "scope_shape", "lease_calendar", "scope_extra", "scope_missing", "lease_not_common",
        "no_by_task", "by_task_dict", "task_twice"])
def test_a_claim_that_contradicts_itself_is_refused(change):
    claim = _claim(["a.py"], AS_OF, "self")
    change(claim)
    fields = mind.reflection(_snapshot(lanes=[_lane("fable-5", claim)]))
    assert [(tension["rule"], tension["subject"]) for tension in fields["tensions"]] == [("refused_input", "lanes[0].claims")]


LIVE = "2026-01-01T01:00:00Z"


def test_every_task_claim_of_a_lane_counts_on_its_own():
    """Core 58a30453 by_task: one lane holds several task claims; each overlaps or expires on its own lease."""
    lanes = [_lane("codex-lead-1", _claims(_task_claim(["docs/plan.md"], LIVE, "plan"),
                                            _task_claim(["tools/old.py"], AS_OF, "old"))),
             _lane("fable-5", _claims(_task_claim(["docs"], LIVE, "docs"),
                                      _task_claim(["tools/old.py"], LIVE, "tools")))]
    rules = _rules(mind.reflection(_snapshot(lanes=lanes)))
    assert set(rules) == {"claim_overlap", "expired_claim"}
    # The expired task claim no longer counts for overlap; the live one of the same lane does.
    assert rules["claim_overlap"]["observation"] == "overlapping paths: docs ~ docs/plan.md"
    assert rules["claim_overlap"]["refs"] == sorted([ref("claim:plan"), ref("claim:docs")])
    assert rules["expired_claim"]["subject"] == f"claim:codex-lead-1|{UUIDS['codex-lead-1']}:task-old"
    assert rules["expired_claim"]["claim"] == "codex-lead-1 holds a write claim for task-old"


def test_a_claim_with_an_unknown_lease_still_counts_for_overlap():
    """Bridge claim events carry no lease, so the core gives UNKNOWN: such a claim is not expired and still counts."""
    lanes = [_lane("claude-rco-1", _claim(["tools/x.py"], "UNKNOWN", "rco1")),
             _lane("fable-5", _claim(["tools"], "UNKNOWN", "fable"))]
    rules = _rules(mind.reflection(_snapshot(lanes=lanes)))
    assert set(rules) == {"claim_overlap"}
    assert rules["claim_overlap"]["claim"] == "claude-rco-1 and fable-5 both hold write claims that are not expired"


@pytest.mark.parametrize("change", [
    lambda row: row.update(expired=True),
    lambda row: row.update(task_id="task-zbad with space"),
    lambda row: row.update(source_refs=["not-a-ref"]),
    lambda row: row.update(write_scope=["../outside.py"]),
], ids=["expired", "task_id", "refs", "path"])
def test_an_unusable_task_row_refuses_only_itself_and_the_other_task_claims_still_count(change):
    rows = [_task_claim(["docs/a.md"], LIVE, "keep"), _task_claim(["tools"], LIVE, "zbad")]
    change(rows[1])
    claims = _claims(*rows)
    # The lane-level fields stay what the rows give (the core's union), with only valid refs at lane level.
    claims["source_refs"] = [value for value in claims["source_refs"] if len(value) == 64]
    lanes = [_lane("claude-rco-1", _claim(["docs", "tools/x.py"], LIVE, "rco1")), _lane("fable-5", claims)]
    fields = mind.reflection(_snapshot(lanes=lanes))
    assert sorted((tension["rule"], tension["subject"]) for tension in fields["tensions"]) == [
        ("claim_overlap", f"claims:claude-rco-1|{UUIDS['claude-rco-1']}|fable-5|{UUIDS['fable-5']}"),
        ("refused_input", "lanes[1].claims.by_task[1]")]
    assert _rules(fields)["claim_overlap"]["observation"] == "overlapping paths: docs ~ docs/a.md"


@pytest.mark.parametrize("many, one", [("claude-rco-1", "fable-5"), ("fable-5", "claude-rco-1")],
                         ids=["first_lane", "second_lane"])
def test_a_later_task_claim_overlaps_whichever_lane_of_the_pair_holds_it(many, one):
    """Every live task claim of a lane is compared, not only its first one, on either side of the lane pair."""
    lanes = [_lane(many, _claims(_task_claim(["docs/a.md"], LIVE, "a"), _task_claim(["tools/x.py"], "UNKNOWN", "b"))),
             _lane(one, _claim(["tools"], LIVE, "one"))]
    rules = _rules(mind.reflection(_snapshot(lanes=lanes)))
    assert set(rules) == {"claim_overlap"}
    assert rules["claim_overlap"]["observation"] == "overlapping paths: tools ~ tools/x.py"
    assert rules["claim_overlap"]["refs"] == sorted([ref("claim:b"), ref("claim:one")])


def test_task_rows_are_read_in_task_id_order_whatever_the_input_order():
    """A refusal names a task row by its position in task_id order, the order the core emits."""
    claims = _claims(_task_claim(["docs/a.md"], LIVE, "a"), _task_claim(["../outside.py"], LIVE, "b"))
    claims["by_task"].reverse()
    fields = mind.reflection(_snapshot(lanes=[_lane("fable-5", claims)]))
    assert [(tension["rule"], tension["subject"]) for tension in fields["tensions"]] == [
        ("refused_input", "lanes[0].claims.by_task[1]")]


@pytest.mark.parametrize("lease, rules", [(LIVE, []), (AS_OF, ["expired_claim"])], ids=["live", "expired"])
def test_a_task_claim_with_an_unknown_scope_is_kept_and_can_expire_but_overlaps_nothing(lease, rules):
    """The core gives a task row the scope UNKNOWN when the claim event's scope cannot be read: not a refusal."""
    lanes = [_lane("claude-rco-1", _claim(["tools"], LIVE, "rco1")), _lane("fable-5", _claim(None, lease, "fable"))]
    assert [tension["rule"] for tension in mind.reflection(_snapshot(lanes=lanes))["tensions"]] == rules


def test_declared_paths_with_a_space_or_a_colon_are_live_claims_and_overlap():
    """Lead 04:52Z: the core declares paths with a space or a colon; the workspace keeps them as live claims."""
    lanes = [_lane("claude-rco-2", _claim(["docs\\My Notes\\", "resource:Grok-Lock"], LIVE, "rco2")),
             _lane("fable-5", _claim(["docs/my notes/x y.md", "resource:grok-lock"], LIVE, "fable"))]
    fields = mind.reflection(_snapshot(lanes=lanes))
    assert [tension["rule"] for tension in fields["tensions"]] == ["claim_overlap"]
    assert fields["tensions"][0]["observation"] == \
        "overlapping paths: docs/my notes ~ docs/my notes/x y.md, resource:grok-lock"


@pytest.mark.parametrize("path", ["../outside.py", "tools/../../outside.py", "tools/.../x.py", "tools/ ./x.py",
                                  "tools/a\tb.py", "tools/" + "x" * 2048, "docs/x " + GUARD_WORDS[0].lower() + "/a.md"],
                         ids=["parent", "climbs_out", "dots_only", "dot_space", "tab", "overlong", "guarded"])
def test_an_unsafe_declared_path_refuses_its_claim_by_position_without_echo_and_keeps_the_others(path):
    """Never opened or resolved; a path that climbs out, a dots-only segment, a control character, an overlong or
    guarded path refuses the claim it is in, named by position only, and every other live claim still counts."""
    lanes = [_lane("claude-rco-1", _claim(["tools/x.py"], LIVE, "rco1")),
             _lane("claude-rco-2", _claim(["tools", path], LIVE, "rco2")),
             _lane("fable-5", _claim(["tools/x.py", "docs/a.md"], LIVE, "fable"))]
    fields = mind.reflection(_snapshot(lanes=lanes))
    assert [(tension["rule"], tension["subject"]) for tension in fields["tensions"]] == [
        ("claim_overlap", f"claims:claude-rco-1|{UUIDS['claude-rco-1']}|fable-5|{UUIDS['fable-5']}"),
        ("refused_input", "lanes[1].claims.by_task[0]")]
    text = json.dumps(fields, ensure_ascii=False).casefold()
    assert path.casefold() not in text and "outside" not in text and "x" * 60 not in text
    with pytest.raises(mind.WorkspaceError):
        mind.scopes_overlap(["tools"], [path])


def test_a_released_claim_as_the_core_emits_it_is_an_empty_scope_not_a_refusal():
    """Core 58a30453: releasing the last task claim leaves no by_task row, an empty scope and an UNKNOWN lease."""
    released = {"write_scope": [], "source_refs": [ref("release:fable")], "verification": "declared",
                "lease_expires_utc": "UNKNOWN", "expired": "UNKNOWN", "by_task": []}
    lanes = [_lane("claude-rco-2", _claim(["tools"], LIVE, "rco2")), _lane("fable-5", released)]
    assert mind.reflection(_snapshot(lanes=lanes))["tensions"] == []


@pytest.mark.parametrize("status", ["UNKNOWN", "changes-requested", "hold:blocked", "rco_pass_withdrawn"])
def test_a_veto_with_any_safe_status_is_observed_and_blocks_every_suggestion(status):
    """Core 218e6547 keeps the status that was sent, as a token or UNKNOWN; any of them is a veto observation."""
    key, pr = _pr(10, votes=ALMOST)
    pr["veto_observations"] = [_veto("claude-rco-2", 10, HEAD, status)]
    fields = mind.reflection(_snapshot(prs=[(key, pr)]))
    assert [tension["rule"] for tension in fields["tensions"]] == ["veto_observed"]
    assert all(item["kind"] in ("tension", "tension_high") for item in fields["attention"])


def test_a_vote_the_core_retired_at_the_pr_head_is_stale_and_fills_no_slot():
    """Core 218e6547: a later blocking signal of the same identity moves its pass to stale_votes at the same head."""
    key, pr = _pr(10, votes=["codex-lead-1", "codex-tools-1", "claude-rco-1"], stale=[("claude-rco-2", HEAD)])
    fields = mind.reflection(_snapshot(prs=[(key, pr)]))
    assert [(item["kind"], item["actors"]) for item in fields["attention"]][0] == ("last_missing_vote", ["claude-rco-2"])
    stale = _rules(fields)["stale_vote"]
    assert stale["observation"] == "the core retired it at the PR head; a stale vote cannot approve it"
    assert stale["subject"] == f"pr:10:claude-rco-2:{HEAD[:12]}"


def test_an_invalid_calendar_time_is_a_fixed_refusal_not_a_traceback(tmp_path, capsys):
    snapshot = _snapshot()
    snapshot["source"]["as_of_utc"] = "2026-99-99T00:00:00Z"
    with pytest.raises(mind.WorkspaceError):
        mind.reflection(snapshot)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert mind.main(["--snapshot", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "bridge mind workspace refused: snapshot source needs an explicit as_of_utc\n"


def test_an_output_item_without_valid_refs_fails_closed_and_nothing_is_printed(tmp_path, monkeypatch, capsys):
    original = mind.derive_tensions
    for bad_refs in ([], ["UNKNOWN"], ["abc"]):
        def broken(inputs, bad_refs=bad_refs):
            return original(inputs) + [mind._tension("stale_vote", "pr:1", "a vote", "old head", [], bad_refs)]

        monkeypatch.setattr(mind, "derive_tensions", broken)
        with pytest.raises(mind.WorkspaceError):
            mind.reflection(_snapshot())
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(_snapshot()), encoding="utf-8")
    assert mind.main(["--snapshot", str(snapshot), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "no valid event ref" in captured.err


def _shuffled(snapshot, shuffler):
    """The same facts with every list and every dictionary in another order."""
    out = copy.deepcopy(snapshot)
    for table in ("prs", "lanes"):
        keys = list(out[table])
        shuffler.shuffle(keys)
        out[table] = {key: out[table][key] for key in keys}
    for pr in out["prs"].values():
        for name in ("votes", "stale_votes", "veto_observations"):
            shuffler.shuffle(pr[name])
    for lane in out["lanes"].values():
        if isinstance(lane["claims"], dict):
            shuffler.shuffle(lane["claims"]["write_scope"])
            shuffler.shuffle(lane["claims"]["by_task"])
            for task in lane["claims"]["by_task"]:
                shuffler.shuffle(task["write_scope"])
    shuffler.shuffle(out["facts"])
    for fact in out["facts"]:
        shuffler.shuffle(fact["value"]["expected_responders"])
    return out


def test_the_same_facts_in_any_order_give_identical_bytes():
    """P1 / M4: an incremental and a full projector that agree on the facts get the same bytes."""
    lanes = [_lane("claude-rco-2", _claim(["tools", "docs/a.md"], "2026-01-01T01:00:00Z", "rco2")),
             _lane("fable-5", _claim(["tools/board.py", "docs"], "2026-01-01T01:00:00Z", "fable")),
             _lane("codex-tools-1", _claim(["docs/old.md"], "2026-01-01T00:00:00Z", "old"))]
    prs = [_pr(10, votes=["codex-tools-1", "codex-lead-1"]),
           _pr(20, klass="a", votes=["claude-rco-2", "codex-lead-1", "codex-tools-1"]),
           _pr(7, votes=["codex-lead-1"], stale=[("claude-rco-2", OLD), ("claude-rco-1", OLD)],
               vetoes=["claude-rco-1", "claude-rco-2"])]
    facts = [_command("r1", "2026-01-01T00:00:00Z", ["fable-5", "codex-tools-1"]),
             _command("r2", "2026-01-01T00:01:00Z", ["claude-rco-2"])]
    snapshot = _snapshot(lanes=lanes, prs=prs, facts=facts)
    fields = mind.reflection(snapshot)
    # A two-agent tension and a parent/child overlap, whose order would show if anything depended on it.
    overlap = _rules(fields)["claim_overlap"]
    assert overlap["actors"] == ["claude-rco-2", "fable-5"]
    assert overlap["observation"] == "overlapping paths: docs ~ docs/a.md, tools ~ tools/board.py"
    assert any(len(tension["actors"]) == 2 for tension in fields["tensions"] if tension["rule"] == "unacknowledged_command")
    first = mind.canonical_json(fields)
    shuffler = random.Random(1813)
    for _ in range(8):
        assert mind.canonical_json(mind.reflection(_shuffled(snapshot, shuffler))) == first


def test_only_event_time_is_used():
    """P2 / M5: no wall clock anywhere in the derivation."""
    lane = _lane("fable-5", _claim(["a.py"], "2026-01-01T00:30:00.2Z", "live"))
    assert mind.reflection(_snapshot(lanes=[lane]))["tensions"] == []
    source = Path(mind.__file__).read_text(encoding="utf-8")
    assert ".now(" not in source and "time.time" not in source and "utcnow" not in source
    assert "import time" not in source and "from time" not in source


def test_tension_ids_are_structural_and_lifecycle_follows_the_previous_snapshot():
    lanes = [_lane("fable-5", _claim(["docs/old.md"], "2026-01-01T00:00:00Z", "old"))]
    first = mind.reflect(_snapshot(lanes=lanes))
    expired_id = first["tensions"][0]["tension_id"]
    # New evidence for the same contradiction (another ref) keeps the id.
    moved = copy.deepcopy(lanes)
    moved[0][1]["claims"]["source_refs"] = [ref("claim:old:refreshed")]
    moved[0][1]["claims"]["by_task"][0]["source_refs"] = [ref("claim:old:refreshed")]
    second = mind.reflect(_snapshot(lanes=moved, coverage="partial"), first)
    lifecycle = {tension["tension_id"]: (tension["rule"], tension["lifecycle"]) for tension in second["tensions"]}
    assert lifecycle[expired_id] == ("expired_claim", "persisting")
    assert sorted(lifecycle.values()) == [("coverage_unknown", "new"), ("expired_claim", "persisting")]


def _escaped(word):
    """The word with its first letter as a JSON backslash-u escape, so only a decoded check can see it."""
    return "\\u%04x" % ord(word[0]) + word[1:]


def _double_escaped(word):
    """The escape of the escape: a reader that decodes twice would reveal the word."""
    return "\\u005c" + _escaped(word)[1:]


def _assert_no_guard_word(text):
    folded = text.upper()
    assert not any(word in folded for word in GUARD_WORDS)


def test_guarded_words_in_input_rows_are_refused_by_position_and_never_derived():
    """P11 / M3: the decoded input guard keeps every guarded row out of the derived fields."""
    lanes = [_lane("fable-5", _claim(["a" + GUARD_WORDS[0] + ".py"], "2026-01-01T01:00:00Z", "a")),
             # A path may hold a backslash, so a literal backslash-u form passes the allowlist; only the
             # unescaped check refuses it.
             _lane("codex-tools-1", _claim(["docs\\" + _escaped(GUARD_WORDS[1])[1:]], "2026-01-01T01:00:00Z", "b"))]
    facts = [_command("req-" + _escaped(GUARD_WORDS[1]), "2026-01-01T00:00:00Z", ["fable-5"])]
    prs = [_pr(9, votes=["codex-lead-1"])]
    prs[0][1]["votes"][0]["agent"] = "x" + GUARD_WORDS[1].lower()
    fields = mind.reflection(_snapshot(lanes=lanes, facts=facts, prs=prs))
    _assert_no_guard_word(json.dumps(fields))
    refused = sorted(tension["subject"] for tension in fields["tensions"] if tension["rule"] == "refused_input")
    assert refused == ["facts[0]", "lanes[0].claims.by_task[0]", "lanes[1].claims.by_task[0]", "prs[0].votes[0]"]

    def leaking(inputs):
        return [mind._tension("stale_vote", "pr:1", "vote by x" + GUARD_WORDS[0], "old head", [], [ref("v")])]

    original = mind.derive_tensions
    try:
        mind.derive_tensions = leaking
        with pytest.raises(mind.WorkspaceError):
            mind.reflection(_snapshot())
    finally:
        mind.derive_tensions = original


def _put(snapshot, where, value):
    if where == "top_level_key":
        snapshot[value] = 1
    elif where == "source_extra":
        snapshot["source"]["note"] = value
    elif where == "source_coverage":  # printed by the text mode
        snapshot["source"]["coverage"] = value
    elif where == "pr_extra":
        snapshot["prs"]["10"]["note"] = value
    elif where == "lane_extra":
        snapshot["lanes"][next(iter(snapshot["lanes"]))]["note"] = value
    elif where == "fact_value":
        snapshot["facts"].append({**_command("r1", AS_OF, []), "kind": "pr_observation", "value": {"note": value}})


def _boundary_snapshot():
    return _snapshot(prs=[_pr(10, votes=ALMOST)], lanes=[_lane("fable-5")])


@pytest.mark.parametrize("where", ["top_level_key", "source_extra", "source_coverage", "pr_extra", "lane_extra",
                                   "fact_value"])
@pytest.mark.parametrize("form", ["plain", "lowercase", "escaped", "double_escaped", "fullwidth", "kelvin",
                                  "json_file_escape"])
@pytest.mark.parametrize("as_json", [True, False])
def test_a_guarded_word_anywhere_in_the_emitted_snapshot_refuses_the_whole_output(tmp_path, capsys, where, form,
                                                                                    as_json):
    """L1-L5 / Lead item 1: the complete object and the exact printed text are guarded; exit 2, nothing printed."""
    word = GUARD_WORDS[0] if where != "lane_extra" else GUARD_WORDS[1]
    value = {"plain": word, "lowercase": word.lower(), "escaped": _escaped(word), "double_escaped": _double_escaped(word),
             "fullwidth": chr(0xFF00 + ord(word[1]) - 0x20).join([word[0], word[2:]]),
             "kelvin": word.replace("K", "K"), "json_file_escape": word}[form]
    assert value != word or form in ("plain", "json_file_escape")
    snapshot = _boundary_snapshot()
    _put(snapshot, where, value)
    with pytest.raises(mind.WorkspaceError):
        mind.reflect(snapshot)
    raw = json.dumps(snapshot)
    if form == "json_file_escape":  # the file holds only the JSON escape; the decoded value is the plain word
        raw = raw.replace(word, _escaped(word))
        assert word not in raw
    path = tmp_path / "snapshot.json"
    path.write_text(raw, encoding="utf-8")
    assert mind.main(["--snapshot", str(path)] + (["--json"] if as_json else [])) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    # The canonical bridge JSON reader already refuses a non-ASCII key, before any output exists.
    reason = ("an input file is not one canonical JSON object" if where == "top_level_key" and not value.isascii()
              else "a guarded word is in the output; nothing is written")
    assert captured.err == f"bridge mind workspace refused: {reason}\n"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["snapshot.json"]


def test_the_serializer_guards_the_object_and_its_own_text_and_deep_escapes_fail_closed(monkeypatch):
    """Each layer of the output boundary holds on its own, not only behind reflect()."""
    reflected = mind.reflect(_boundary_snapshot())
    for as_json in (True, False):
        with pytest.raises(mind.WorkspaceError):
            mind.serialize({**reflected, "note": GUARD_WORDS[1].lower()}, as_json=as_json)
    monkeypatch.setattr(mind, "render_text", lambda snapshot: "as of " + GUARD_WORDS[0] + "\n")
    with pytest.raises(mind.WorkspaceError):
        mind.serialize(reflected, as_json=False)
    nested = GUARD_WORDS[0]
    for _ in range(mind.UNESCAPE_ROUNDS + 1):
        nested = "\\u%04x" % ord(nested[0]) + nested[1:]
    assert mind._has_marker(nested) and not mind._has_marker("docs\\u0041.md")


@pytest.mark.parametrize("as_json", [True, False])
def test_safe_extra_fields_are_kept_unchanged_in_the_output(tmp_path, capsys, as_json):
    """The positive control: nothing is stripped, rewritten or added outside the reflection fields."""
    snapshot = _boundary_snapshot()
    for where in ("source_extra", "pr_extra", "lane_extra"):
        _put(snapshot, where, "public note")
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert mind.main(["--snapshot", str(path)] + (["--json"] if as_json else [])) == 0
    out = capsys.readouterr().out
    if as_json:
        printed = json.loads(out)
        assert {key: value for key, value in printed.items() if key not in ("tensions", "attention", "next_question")} \
            == {key: value for key, value in snapshot.items() if key not in ("tensions", "attention", "next_question")}
        assert printed == mind.reflect(snapshot)
    else:
        assert out == mind.render_text(mind.reflect(snapshot)) and "last_missing_vote" in out


def test_authority_scope_and_schema_are_required_and_only_the_reflection_fields_change():
    """P10: no clearance; the workspace never touches source, prs, lanes, facts, authority or scope."""
    snapshot = _snapshot(prs=[_pr(10, votes=["codex-lead-1", "codex-tools-1"])], lanes=[_lane("claude-rco-2")])
    before = copy.deepcopy(snapshot)
    reflected = mind.reflect(snapshot)
    assert snapshot == before
    assert {key: value for key, value in reflected.items() if key not in ("tensions", "attention", "next_question")} == \
        {key: value for key, value in before.items() if key not in ("tensions", "attention", "next_question")}
    assert reflected["authority"] == "none" and reflected["scope"] == "bounded_projection_not_gate"
    for bad in ({"authority": "merge"}, {"scope": "gate"}, {"schema": "wd.bridge-mind.v0"},
                {"source": {"coverage": "complete"}}):
        with pytest.raises(mind.WorkspaceError):
            mind.reflection({**snapshot, **bad})


def test_main_prints_only_to_stdout_and_has_no_file_writer(tmp_path, capsys):
    """P12: no file is written; publication is left to the core's owned, atomic writer."""
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(_boundary_snapshot()), encoding="utf-8")
    assert mind.main(["--snapshot", str(snapshot), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == mind.reflect(json.loads(snapshot.read_text(encoding="utf-8")))
    with pytest.raises(SystemExit):
        mind.main(["--snapshot", str(snapshot), "--out", str(tmp_path / "never.json")])
    valid = json.dumps(_boundary_snapshot())
    # Not an object; a duplicated key a plain reader would resolve to the last value; a NaN the text mode never prints.
    for bad in ("[1, 2]", '{"authority": "merge", ' + valid[1:], valid.replace('"observation_age_seconds": 0.0',
                                                                               '"observation_age_seconds": NaN')):
        assert bad != valid
        (tmp_path / "bad.json").write_text(bad, encoding="utf-8")
        assert mind.main(["--snapshot", str(tmp_path / "bad.json")]) == 2
        assert "not one canonical JSON object" in capsys.readouterr().err
    assert sorted(path.name for path in tmp_path.iterdir()) == ["bad.json", "snapshot.json"]
    source = Path(mind.__file__).read_text(encoding="utf-8")
    assert "write_text" not in source and "open(" not in source


@pytest.mark.parametrize("left, right, hit", [
    (["tools\\Board.py"], ["./tools/board.py"], ["tools/board.py"]),
    (["tools"], ["tools/board.py"], ["tools ~ tools/board.py"]),
    (["tools/board.py"], ["tools"], ["tools ~ tools/board.py"]),
    (["tools/a"], ["tools/ab.py"], []),
    ([".codex-audit/x/"], [".codex-audit/x"], [".codex-audit/x"]),
    (["docs/My Notes/a.md"], ["docs/my notes"], ["docs/my notes ~ docs/my notes/a.md"]),
    (["resource:grok-lock"], ["Resource:Grok-Lock"], ["resource:grok-lock"]),
    (["tools/sub/../board.py"], ["tools/board.py"], ["tools/board.py"]),
    (["tools/board.py. "], ["tools/board.py"], ["tools/board.py"]),
    (["."], ["tools/board.py"], [". ~ tools/board.py"]),
    (["C:/x"], ["c:\\x\\y"], ["c:/x ~ c:/x/y"]),
    (["docs/a b.md"], ["docs/a  b.md"], []),
])
def test_claim_scopes_overlap_the_way_windows_compares_paths_whichever_side_is_which(left, right, hit):
    assert mind.scopes_overlap(left, right) == hit


def test_the_workspace_is_not_wired_into_any_gate_or_bridge_code():
    """M1 / P9: nothing in tools, the bridge bin or waggledance reads the workspace."""
    root = Path(mind.__file__).resolve().parents[1]
    readers = [path for path in (root / "tools").glob("*.py") if path.name != "bridge_mind_workspace.py"]
    readers += list((root / ".agent-bridge" / "bin").glob("*.ps1"))
    readers += list((root / "waggledance").rglob("*.py"))
    assert len(readers) > 100
    assert not [path.name for path in readers
                if "bridge_mind_workspace" in path.read_text(encoding="utf-8", errors="replace")]


# The positive twin: an actual output of the projector core, tools/wd_bridge_mind.py at codex-tools-1/mind-20261013
# commit d47f3ef9372c40855a21308147a19531f2fed989 (blob ecd3cde499e0d822188689ce202bdde4997f22c5), from
# full_rebuild(CORE_TWIN_EVENTS as {ref: event_id(event), event}, source=CORE_TWIN_SOURCE, as_of_utc=CORE_TWIN_AS_OF)
# and snapshot(). The core ran from a git-archive copy of that commit; CORE_TWIN_SHA256 is the sha256 of its canonical
# bytes, and CORE_BLOCKED_SHA256 that of blocked_snapshot(state, offset=1500, reason="canonical_event_invalid") on the
# same state. Until the core lands on main the recorded bytes stand in for it; once it does, the twin test reruns it.
# The events hold a withdrawn pass (PR1895), a pass from an undeclared identity (PR1896), a canonical non-RCO
# type=blocked observation (PR1890), two task claims of one lane with a space and a colon in their paths, one of them
# released (codex-lead-1), an expired claim and a stale vote.
CORE_TWIN_SHA256 = "ff80507152793116e6b5f0bac5d39cd5145a5ab879de763e3a0b17570cad64b7"
CORE_BLOCKED_SHA256 = "990030e082dee1bcafb9ff9d631f16fe0481b11b03be72d2fcdc66fc70f8c3ee"
CORE_TWIN_AS_OF = "2026-10-13T03:55:00Z"
CORE_TWIN_SOURCE = {"log_identity": "fixture-log", "generation": "g1", "cursor_start": 0, "cursor_end": 1500,
                    "coverage": "complete", "event_count": 33}
_TWIN_UUID = {"fable-5": "f5", "claude-rco-1": "c1", "claude-rco-2": "c2", "codex-lead-1": "a1", "codex-tools-1": "b1"}
H_OLD, H_NEW, H_1893, H_1890 = "0b" * 20, "1c" * 20, "2d" * 20, "3e" * 20
H_1895, H_1896 = "4f" * 20, "5a" * 20


def _event(ts, agent, kind, status, task, payload, uuid=None):
    """One synthetic bridge event of a declared identity, or of an undeclared one when ``uuid`` is given."""
    code = _TWIN_UUID[agent]
    return {"ts_utc": f"2026-10-13T{ts}Z", "agent": agent,
            "agent_uuid": uuid if uuid is not None else "00000000-0000-4000-8000-0000000000" + code,
            "session_id": f"s-{code}", "run_id": f"r-{code}", "type": kind, "status": status, "task_id": task,
            "payload": payload}


CORE_TWIN_EVENTS = [
    _event("03:05:00", "codex-lead-1", "message", "ready", "codex-lead-1/train-1890",
           {"pr": 1890, "head": H_1890, "class": "b", "required_checks_green": True}),
    _event("03:06:00", "claude-rco-1", "finding", "changes_requested", "claude-rco-1/review-1890",
           {"pr": 1890, "head": H_1890}),
    _event("03:07:00", "claude-rco-2", "decision", "rco_pass", "claude-rco-2/review-1890",
           {"pr": 1890, "head": H_1890}),
    _event("03:08:00", "codex-tools-1", "blocked", "blocked", "codex-tools-1/train-1890",
           {"pr": 1890, "head": H_1890}),
    _event("03:10:00", "codex-lead-1", "message", "ready", "codex-lead-1/train-1893",
           {"pr": 1893, "head": H_1893, "class": "a", "required_checks_green": True}),
    _event("03:12:00", "codex-lead-1", "decision", "build_consensus_pass", "codex-lead-1/train-1893",
           {"pr": 1893, "head": H_1893}),
    _event("03:13:00", "codex-tools-1", "decision", "build_consensus_pass", "codex-tools-1/train-1893",
           {"pr": 1893, "head": H_1893}),
    _event("03:14:00", "claude-rco-1", "decision", "rco_pass", "claude-rco-1/review-1893",
           {"pr": 1893, "head": H_1893}),
    _event("03:15:00", "claude-rco-2", "decision", "rco_pass", "claude-rco-2/review-1893",
           {"pr": 1893, "head": H_1893}),
    _event("03:20:00", "codex-tools-1", "claim", "active", "codex-tools-1/mind-20261013",
           {"write_scope": ["tools/wd_bridge_mind.py", "tests/tools/test_wd_bridge_mind.py"],
            "claim_lease_expires_utc": "2026-10-13T03:50:00Z"}),
    _event("03:21:00", "codex-lead-1", "message", "ready", "codex-lead-1/train-1895",
           {"pr": 1895, "head": H_1895, "class": "b", "required_checks_green": True}),
    _event("03:22:00", "codex-lead-1", "decision", "build_consensus_pass", "codex-lead-1/train-1895",
           {"pr": 1895, "head": H_1895}),
    _event("03:23:00", "codex-tools-1", "decision", "build_consensus_pass", "codex-tools-1/train-1895",
           {"pr": 1895, "head": H_1895}),
    _event("03:24:00", "claude-rco-1", "decision", "rco_pass", "claude-rco-1/review-1895",
           {"pr": 1895, "head": H_1895}),
    _event("03:25:00", "claude-rco-2", "decision", "rco_pass", "claude-rco-2/review-1895",
           {"pr": 1895, "head": H_1895}),
    _event("03:26:00", "claude-rco-2", "decision", "rco_pass_withdrawn", "claude-rco-2/review-1895",
           {"pr": 1895, "head": H_1895}),
    _event("03:27:00", "codex-lead-1", "message", "ready", "codex-lead-1/train-1896",
           {"pr": 1896, "head": H_1896, "class": "b", "required_checks_green": True}),
    _event("03:27:10", "codex-lead-1", "decision", "build_consensus_pass", "codex-lead-1/train-1896",
           {"pr": 1896, "head": H_1896}),
    _event("03:27:20", "codex-tools-1", "decision", "build_consensus_pass", "codex-tools-1/train-1896",
           {"pr": 1896, "head": H_1896}),
    _event("03:27:30", "claude-rco-1", "decision", "rco_pass", "claude-rco-1/review-1896",
           {"pr": 1896, "head": H_1896}),
    _event("03:28:00", "claude-rco-2", "decision", "rco_pass", "claude-rco-2/review-1896",
           {"pr": 1896, "head": H_1896}, uuid="not-a-uuid"),
    _event("03:30:00", "fable-5", "message", "review_requested", "fable-5/bridge-mind-workspace-20261013",
           {"pr": 1899, "head": H_OLD, "class": "b"}),
    _event("03:31:00", "codex-lead-1", "claim", "active", "codex-lead-1/plan-notes",
           {"write_scope": ["docs/My Notes/plan.md", "resource:grok-lock"],
            "claim_lease_expires_utc": "2026-10-13T04:30:00Z"}),
    _event("03:32:00", "claude-rco-1", "claim", "active", "claude-rco-1/notes-review",
           {"write_scope": ["docs/my notes", "Resource:Grok-Lock"],
            "claim_lease_expires_utc": "2026-10-13T04:30:00Z"}),
    _event("03:33:00", "codex-lead-1", "claim", "active", "codex-lead-1/root-notes",
           {"write_scope": ["docs/root.md"], "claim_lease_expires_utc": "2026-10-13T04:30:00Z"}),
    _event("03:34:00", "codex-lead-1", "release", "released", "codex-lead-1/root-notes", {}),
    _event("03:35:00", "claude-rco-1", "decision", "rco_pass", "claude-rco-1/review-1899",
           {"pr": 1899, "head": H_OLD}),
    _event("03:40:00", "fable-5", "claim", "active", "fable-5/bridge-mind-workspace-20261013",
           {"write_scope": ["tools/bridge_mind_workspace.py", "tests/tools/test_bridge_mind_workspace.py"],
            "claim_lease_expires_utc": "2026-10-13T04:30:00Z"}),
    _event("03:41:00", "claude-rco-2", "claim", "active", "claude-rco-2/mind-probes",
           {"write_scope": ["tests/tools/test_bridge_mind_workspace.py"],
            "claim_lease_expires_utc": "2026-10-13T04:20:00Z"}),
    _event("03:45:00", "fable-5", "message", "head_updated", "fable-5/bridge-mind-workspace-20261013",
           {"pr": 1899, "head": H_NEW, "required_checks_green": True}),
    _event("03:50:00", "claude-rco-2", "decision", "rco_pass", "claude-rco-2/review-1899",
           {"pr": 1899, "head": H_NEW}),
    _event("03:51:00", "claude-rco-1", "decision", "rco_pass", "claude-rco-1/review-1899",
           {"pr": 1899, "head": H_NEW}),
    _event("03:52:00", "codex-tools-1", "decision", "build_consensus_pass", "codex-tools-1/review-1899",
           {"pr": 1899, "head": H_NEW}),
]
CORE_TWIN_JSON = r"""
{
 "attention": [],
 "authority": "none",
 "facts": [
  {
   "canonical_sha256": "4f32f2df251a63c06f10dc88af79c2e1b22a906dcfcdee0601daaa225aeb5f06",
   "fact_id": "fbe79c19ff4c7450d31ab97a89e7c565c64aef9c6762f636450d2fca9ce29f9e",
   "kind": "pr_observation",
   "observed_at_utc": "2026-10-13T03:52:00Z",
   "salience": 80,
   "source_refs": [
    "0f39860f875a930842320055220ec0b0ba3160adcc0b4ef1a6b586a6badfee4f",
    "4f32f2df251a63c06f10dc88af79c2e1b22a906dcfcdee0601daaa225aeb5f06",
    "9e05f37b0a4f23f69563ed6466315fbc4435db46ed411178c551274b94883d49"
   ],
   "ttl_seconds": 300,
   "value": {
    "head": "1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c",
    "pr": 1899
   },
   "verification": "declared"
  },
  {
   "canonical_sha256": "8303eb5a7eb4556dcc42ac038f3bf13663e50d42cd607224aa280e959f3c89ea",
   "fact_id": "f66bf932b5e74ef1067a8d5bafcd5aa9843bcaaefc0da6b4108cfadd2af29ee0",
   "kind": "lane_observation",
   "observed_at_utc": "2026-10-13T03:51:00Z",
   "salience": 50,
   "source_refs": [
    "8303eb5a7eb4556dcc42ac038f3bf13663e50d42cd607224aa280e959f3c89ea"
   ],
   "ttl_seconds": 300,
   "value": {
    "agent": "claude-rco-1",
    "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
    "current_task": "claude-rco-1/review-1899"
   },
   "verification": "declared"
  },
  {
   "canonical_sha256": "4f32f2df251a63c06f10dc88af79c2e1b22a906dcfcdee0601daaa225aeb5f06",
   "fact_id": "9cdb159f675a8fd307d4e28e1b62b17ae6dc44477b25821d5831daaae648f98e",
   "kind": "lane_observation",
   "observed_at_utc": "2026-10-13T03:52:00Z",
   "salience": 50,
   "source_refs": [
    "4f32f2df251a63c06f10dc88af79c2e1b22a906dcfcdee0601daaa225aeb5f06"
   ],
   "ttl_seconds": 300,
   "value": {
    "agent": "codex-tools-1",
    "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
    "current_task": "codex-tools-1/review-1899"
   },
   "verification": "declared"
  }
 ],
 "lanes": {
  "claude-rco-1|00000000-0000-4000-8000-0000000000c1": {
   "agent": "claude-rco-1",
   "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
   "claims": {
    "by_task": [
     {
      "expired": false,
      "lease_expires_utc": "2026-10-13T04:30:00Z",
      "owner_run_id": "r-c1",
      "owner_session_id": "s-c1",
      "source_refs": [
       "646f3c22bb984bc6886f8ded3eb12b9f70ccb7454e777ccff2bbff0bcdfbe1f3"
      ],
      "task_id": "claude-rco-1/notes-review",
      "verification": "declared",
      "write_scope": [
       "docs/my notes",
       "Resource:Grok-Lock"
      ]
     }
    ],
    "expired": false,
    "lease_expires_utc": "2026-10-13T04:30:00Z",
    "source_refs": [
     "646f3c22bb984bc6886f8ded3eb12b9f70ccb7454e777ccff2bbff0bcdfbe1f3"
    ],
    "verification": "declared",
    "write_scope": [
     "Resource:Grok-Lock",
     "docs/my notes"
    ]
   },
   "current_task": "claude-rco-1/review-1899",
   "idle_status": "UNKNOWN",
   "last_event_ref": "8303eb5a7eb4556dcc42ac038f3bf13663e50d42cd607224aa280e959f3c89ea",
   "last_event_utc": "2026-10-13T03:51:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 240.0,
   "run_id": "r-c1",
   "session_id": "s-c1",
   "source_refs": [
    "646f3c22bb984bc6886f8ded3eb12b9f70ccb7454e777ccff2bbff0bcdfbe1f3",
    "8303eb5a7eb4556dcc42ac038f3bf13663e50d42cd607224aa280e959f3c89ea"
   ],
   "uncertainty": [
    "origin_not_authenticated",
    "native_readiness_UNKNOWN"
   ],
   "verification": "declared",
   "waits": "UNKNOWN"
  },
  "claude-rco-2|00000000-0000-4000-8000-0000000000c2": {
   "agent": "claude-rco-2",
   "agent_uuid": "00000000-0000-4000-8000-0000000000c2",
   "claims": {
    "by_task": [
     {
      "expired": false,
      "lease_expires_utc": "2026-10-13T04:20:00Z",
      "owner_run_id": "r-c2",
      "owner_session_id": "s-c2",
      "source_refs": [
       "9c707688210250fff0f1c44bcd47356b65690e022bdd07283cc2121fef23098a"
      ],
      "task_id": "claude-rco-2/mind-probes",
      "verification": "declared",
      "write_scope": [
       "tests/tools/test_bridge_mind_workspace.py"
      ]
     }
    ],
    "expired": false,
    "lease_expires_utc": "2026-10-13T04:20:00Z",
    "source_refs": [
     "9c707688210250fff0f1c44bcd47356b65690e022bdd07283cc2121fef23098a"
    ],
    "verification": "declared",
    "write_scope": [
     "tests/tools/test_bridge_mind_workspace.py"
    ]
   },
   "current_task": "claude-rco-2/review-1899",
   "idle_status": "UNKNOWN",
   "last_event_ref": "0b31c62369dde32aef0822301a81d22271bae1ce6d71e37380d521cbb2046fe5",
   "last_event_utc": "2026-10-13T03:50:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 300.0,
   "run_id": "r-c2",
   "session_id": "s-c2",
   "source_refs": [
    "0b31c62369dde32aef0822301a81d22271bae1ce6d71e37380d521cbb2046fe5",
    "9c707688210250fff0f1c44bcd47356b65690e022bdd07283cc2121fef23098a"
   ],
   "uncertainty": [
    "origin_not_authenticated",
    "native_readiness_UNKNOWN"
   ],
   "verification": "declared",
   "waits": "UNKNOWN"
  },
  "claude-rco-2|UNKNOWN|80343b3c2262b84af2dae409219162f87d4e17dbe935c4d7e1024da366ec71fa": {
   "agent": "claude-rco-2",
   "agent_uuid": "UNKNOWN",
   "claims": "UNKNOWN",
   "current_task": "claude-rco-2/review-1896",
   "idle_status": "UNKNOWN",
   "last_event_ref": "80343b3c2262b84af2dae409219162f87d4e17dbe935c4d7e1024da366ec71fa",
   "last_event_utc": "2026-10-13T03:28:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 1620.0,
   "run_id": "r-c2",
   "session_id": "s-c2",
   "source_refs": [
    "80343b3c2262b84af2dae409219162f87d4e17dbe935c4d7e1024da366ec71fa"
   ],
   "uncertainty": [
    "origin_not_authenticated",
    "native_readiness_UNKNOWN"
   ],
   "verification": "unverified",
   "waits": "UNKNOWN"
  },
  "codex-lead-1|00000000-0000-4000-8000-0000000000a1": {
   "agent": "codex-lead-1",
   "agent_uuid": "00000000-0000-4000-8000-0000000000a1",
   "claims": {
    "by_task": [
     {
      "expired": false,
      "lease_expires_utc": "2026-10-13T04:30:00Z",
      "owner_run_id": "r-a1",
      "owner_session_id": "s-a1",
      "source_refs": [
       "4723b6587032f3d3799247fc31bb0d2b50f00fed45e09ea0c599fcb34245b421"
      ],
      "task_id": "codex-lead-1/plan-notes",
      "verification": "declared",
      "write_scope": [
       "docs/My Notes/plan.md",
       "resource:grok-lock"
      ]
     }
    ],
    "expired": false,
    "lease_expires_utc": "2026-10-13T04:30:00Z",
    "source_refs": [
     "4723b6587032f3d3799247fc31bb0d2b50f00fed45e09ea0c599fcb34245b421"
    ],
    "verification": "declared",
    "write_scope": [
     "docs/My Notes/plan.md",
     "resource:grok-lock"
    ]
   },
   "current_task": "codex-lead-1/root-notes",
   "idle_status": "UNKNOWN",
   "last_event_ref": "202603a083b24a9bf4ab50aca7c908dbe8b81ec9fc1d0d81baa3d1818e3c8d86",
   "last_event_utc": "2026-10-13T03:34:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 1260.0,
   "run_id": "r-a1",
   "session_id": "s-a1",
   "source_refs": [
    "202603a083b24a9bf4ab50aca7c908dbe8b81ec9fc1d0d81baa3d1818e3c8d86",
    "4723b6587032f3d3799247fc31bb0d2b50f00fed45e09ea0c599fcb34245b421"
   ],
   "uncertainty": [
    "origin_not_authenticated",
    "native_readiness_UNKNOWN"
   ],
   "verification": "declared",
   "waits": "UNKNOWN"
  },
  "codex-tools-1|00000000-0000-4000-8000-0000000000b1": {
   "agent": "codex-tools-1",
   "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
   "claims": {
    "by_task": [
     {
      "expired": true,
      "lease_expires_utc": "2026-10-13T03:50:00Z",
      "owner_run_id": "r-b1",
      "owner_session_id": "s-b1",
      "source_refs": [
       "ba655a56acaf2116d030b701416cd85a6d26ef375a54e523a82b58fa662723df"
      ],
      "task_id": "codex-tools-1/mind-20261013",
      "verification": "declared",
      "write_scope": [
       "tools/wd_bridge_mind.py",
       "tests/tools/test_wd_bridge_mind.py"
      ]
     }
    ],
    "expired": true,
    "lease_expires_utc": "2026-10-13T03:50:00Z",
    "source_refs": [
     "ba655a56acaf2116d030b701416cd85a6d26ef375a54e523a82b58fa662723df"
    ],
    "verification": "declared",
    "write_scope": [
     "tests/tools/test_wd_bridge_mind.py",
     "tools/wd_bridge_mind.py"
    ]
   },
   "current_task": "codex-tools-1/review-1899",
   "idle_status": "UNKNOWN",
   "last_event_ref": "4f32f2df251a63c06f10dc88af79c2e1b22a906dcfcdee0601daaa225aeb5f06",
   "last_event_utc": "2026-10-13T03:52:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 180.0,
   "run_id": "r-b1",
   "session_id": "s-b1",
   "source_refs": [
    "4f32f2df251a63c06f10dc88af79c2e1b22a906dcfcdee0601daaa225aeb5f06",
    "ba655a56acaf2116d030b701416cd85a6d26ef375a54e523a82b58fa662723df"
   ],
   "uncertainty": [
    "origin_not_authenticated",
    "native_readiness_UNKNOWN"
   ],
   "verification": "declared",
   "waits": "UNKNOWN"
  },
  "fable-5|00000000-0000-4000-8000-0000000000f5": {
   "agent": "fable-5",
   "agent_uuid": "00000000-0000-4000-8000-0000000000f5",
   "claims": {
    "by_task": [
     {
      "expired": false,
      "lease_expires_utc": "2026-10-13T04:30:00Z",
      "owner_run_id": "r-f5",
      "owner_session_id": "s-f5",
      "source_refs": [
       "370fdc9bc95fedc9d0b685cf17fc379f6a0d2032cbd9b4b3d8f9c18c0353f9a1"
      ],
      "task_id": "fable-5/bridge-mind-workspace-20261013",
      "verification": "declared",
      "write_scope": [
       "tools/bridge_mind_workspace.py",
       "tests/tools/test_bridge_mind_workspace.py"
      ]
     }
    ],
    "expired": false,
    "lease_expires_utc": "2026-10-13T04:30:00Z",
    "source_refs": [
     "370fdc9bc95fedc9d0b685cf17fc379f6a0d2032cbd9b4b3d8f9c18c0353f9a1"
    ],
    "verification": "declared",
    "write_scope": [
     "tests/tools/test_bridge_mind_workspace.py",
     "tools/bridge_mind_workspace.py"
    ]
   },
   "current_task": "fable-5/bridge-mind-workspace-20261013",
   "idle_status": "UNKNOWN",
   "last_event_ref": "0f39860f875a930842320055220ec0b0ba3160adcc0b4ef1a6b586a6badfee4f",
   "last_event_utc": "2026-10-13T03:45:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 600.0,
   "run_id": "r-f5",
   "session_id": "s-f5",
   "source_refs": [
    "0f39860f875a930842320055220ec0b0ba3160adcc0b4ef1a6b586a6badfee4f",
    "370fdc9bc95fedc9d0b685cf17fc379f6a0d2032cbd9b4b3d8f9c18c0353f9a1"
   ],
   "uncertainty": [
    "origin_not_authenticated",
    "native_readiness_UNKNOWN"
   ],
   "verification": "declared",
   "waits": "UNKNOWN"
  }
 },
 "next_question": {
  "global": {
   "attention_rank": null,
   "question": "UNKNOWN",
   "refs": [
    "UNKNOWN"
   ]
  },
  "lanes": {}
 },
 "prs": {
  "1890": {
   "base": "UNKNOWN",
   "class": "b",
   "head": "3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e",
   "missing": [
    "gate_eligibility_UNKNOWN"
   ],
   "pr": 1890,
   "reported_ci": {
    "head": "3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e",
    "source_refs": [
     "60442daa4004644f1b764c69335845bbd82447aa65865afbfabbec201c8b6f9a"
    ],
    "status": "reported_green",
    "verification": "declared"
   },
   "source_refs": [
    "2885f94c40b6f75b95a1119fd09fd0e359b46251e15262f0a2364cb5f0845fd4",
    "60442daa4004644f1b764c69335845bbd82447aa65865afbfabbec201c8b6f9a"
   ],
   "stale_votes": [],
   "veto_observations": [
    {
     "activity": "UNKNOWN",
     "agent": "claude-rco-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
     "head": "3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e",
     "source_refs": [
      "88e2b130881d7adbd2b355ee962f3c017c063c3a6b348cc1d1b8197504731fd4"
     ],
     "status": "changes_requested",
     "verification": "declared"
    },
    {
     "activity": "UNKNOWN",
     "agent": "codex-tools-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
     "head": "3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e",
     "source_refs": [
      "2885f94c40b6f75b95a1119fd09fd0e359b46251e15262f0a2364cb5f0845fd4"
     ],
     "status": "blocked",
     "verification": "declared"
    }
   ],
   "votes": [
    {
     "agent": "claude-rco-2",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c2",
     "head": "3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e",
     "source_refs": [
      "99b5488aab5b2d6002fe0a2d69530c242c6e105b3a4ad1548596b860ffdf9dc9"
     ],
     "status": "rco_pass",
     "verification": "declared"
    }
   ]
  },
  "1893": {
   "base": "UNKNOWN",
   "class": "a",
   "head": "2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d",
   "missing": [
    "gate_eligibility_UNKNOWN"
   ],
   "pr": 1893,
   "reported_ci": {
    "head": "2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d",
    "source_refs": [
     "71d91ba1a0b9b4fc22c824ec5d675f81043db77ba2e6a9e94d165c3817a77bf6"
    ],
    "status": "reported_green",
    "verification": "declared"
   },
   "source_refs": [
    "71d91ba1a0b9b4fc22c824ec5d675f81043db77ba2e6a9e94d165c3817a77bf6",
    "7b129d10616700ef3dfd50491f128cebde93ba2288d306ef614d33d6797af422"
   ],
   "stale_votes": [],
   "veto_observations": [],
   "votes": [
    {
     "agent": "claude-rco-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
     "head": "2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d",
     "source_refs": [
      "fd0416f22bec030e2fc40f71ab7600492a2f36cac3545fb80c7d452cdd744a19"
     ],
     "status": "rco_pass",
     "verification": "declared"
    },
    {
     "agent": "claude-rco-2",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c2",
     "head": "2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d",
     "source_refs": [
      "7b129d10616700ef3dfd50491f128cebde93ba2288d306ef614d33d6797af422"
     ],
     "status": "rco_pass",
     "verification": "declared"
    },
    {
     "agent": "codex-lead-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000a1",
     "head": "2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d",
     "source_refs": [
      "a43f6a3d5559392e8fa0d8189c12aa4ece1662856189851df43bf7f9830227fd"
     ],
     "status": "build_consensus_pass",
     "verification": "declared"
    },
    {
     "agent": "codex-tools-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
     "head": "2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d",
     "source_refs": [
      "cf83940ed7c8a6fed87c34a0b3b952af2b143923f5b8f7ff53f2ccba12c15136"
     ],
     "status": "build_consensus_pass",
     "verification": "declared"
    }
   ]
  },
  "1895": {
   "base": "UNKNOWN",
   "class": "b",
   "head": "4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f",
   "missing": [
    "gate_eligibility_UNKNOWN"
   ],
   "pr": 1895,
   "reported_ci": {
    "head": "4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f",
    "source_refs": [
     "2560322ecedaa8fad747c664263ece5419afabe41c6a495219f31e115919d89e"
    ],
    "status": "reported_green",
    "verification": "declared"
   },
   "source_refs": [
    "2560322ecedaa8fad747c664263ece5419afabe41c6a495219f31e115919d89e",
    "557c98fac1ebf28936a40c149dda4d4d4c53db6d5bd448c0a9704bbac7925cc3"
   ],
   "stale_votes": [
    {
     "agent": "claude-rco-2",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c2",
     "head": "4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f",
     "source_refs": [
      "e83a319c0621bd53b7619292e4119cd19324043da1febed3b06c0043942c3fd9"
     ],
     "status": "rco_pass",
     "verification": "declared"
    }
   ],
   "veto_observations": [
    {
     "activity": "UNKNOWN",
     "agent": "claude-rco-2",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c2",
     "head": "4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f",
     "source_refs": [
      "557c98fac1ebf28936a40c149dda4d4d4c53db6d5bd448c0a9704bbac7925cc3"
     ],
     "status": "rco_pass_withdrawn",
     "verification": "declared"
    }
   ],
   "votes": [
    {
     "agent": "claude-rco-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
     "head": "4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f",
     "source_refs": [
      "2b9b353d5fbca0e99a7facce9ff30c18e795f8ae724289325d543ddf83a1caee"
     ],
     "status": "rco_pass",
     "verification": "declared"
    },
    {
     "agent": "codex-lead-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000a1",
     "head": "4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f",
     "source_refs": [
      "473c791e7c578bb0ca9d75959165c40bebdb5e634af96090a7815d07834b68bc"
     ],
     "status": "build_consensus_pass",
     "verification": "declared"
    },
    {
     "agent": "codex-tools-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
     "head": "4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f4f",
     "source_refs": [
      "e1ee7f20f12c1e8bedb906fb6c6e8dbc2c8d8de29f4a49bda5a1bebd5972a794"
     ],
     "status": "build_consensus_pass",
     "verification": "declared"
    }
   ]
  },
  "1896": {
   "base": "UNKNOWN",
   "class": "b",
   "head": "5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a",
   "missing": [
    "gate_eligibility_UNKNOWN"
   ],
   "pr": 1896,
   "reported_ci": {
    "head": "5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a",
    "source_refs": [
     "4a95fd4e879d66478cceab39481e89cf3e8f2485607249f511847a1755dc105d"
    ],
    "status": "reported_green",
    "verification": "declared"
   },
   "source_refs": [
    "4a95fd4e879d66478cceab39481e89cf3e8f2485607249f511847a1755dc105d",
    "80343b3c2262b84af2dae409219162f87d4e17dbe935c4d7e1024da366ec71fa"
   ],
   "stale_votes": [],
   "veto_observations": [],
   "votes": [
    {
     "agent": "claude-rco-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
     "head": "5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a",
     "source_refs": [
      "cf0dd4dff93267f6cf2fc6fc3cfef7130f2c566245105688f645b8d2608787b4"
     ],
     "status": "rco_pass",
     "verification": "declared"
    },
    {
     "agent": "claude-rco-2",
     "agent_uuid": "UNKNOWN",
     "head": "5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a",
     "source_refs": [
      "80343b3c2262b84af2dae409219162f87d4e17dbe935c4d7e1024da366ec71fa"
     ],
     "status": "rco_pass",
     "verification": "unverified"
    },
    {
     "agent": "codex-lead-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000a1",
     "head": "5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a",
     "source_refs": [
      "b395bcc080cf7594b172286ec35f71c219e4a85fa99c39350df2125a615ddee2"
     ],
     "status": "build_consensus_pass",
     "verification": "declared"
    },
    {
     "agent": "codex-tools-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
     "head": "5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a",
     "source_refs": [
      "085882e72e7fc16b2070de70eae3a8b8b51627abeeed274c03436e6e82c47509"
     ],
     "status": "build_consensus_pass",
     "verification": "declared"
    }
   ]
  },
  "1899": {
   "base": "UNKNOWN",
   "class": "b",
   "head": "1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c",
   "missing": [
    "gate_eligibility_UNKNOWN"
   ],
   "pr": 1899,
   "reported_ci": {
    "head": "1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c",
    "source_refs": [
     "0f39860f875a930842320055220ec0b0ba3160adcc0b4ef1a6b586a6badfee4f"
    ],
    "status": "reported_green",
    "verification": "declared"
   },
   "source_refs": [
    "0f39860f875a930842320055220ec0b0ba3160adcc0b4ef1a6b586a6badfee4f",
    "4f32f2df251a63c06f10dc88af79c2e1b22a906dcfcdee0601daaa225aeb5f06",
    "9e05f37b0a4f23f69563ed6466315fbc4435db46ed411178c551274b94883d49"
   ],
   "stale_votes": [
    {
     "agent": "claude-rco-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
     "head": "0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b",
     "source_refs": [
      "8a568778b9f01ceba403a38c958aba396afaa248941e78654f4781f2b3583811"
     ],
     "status": "rco_pass",
     "verification": "declared"
    }
   ],
   "veto_observations": [],
   "votes": [
    {
     "agent": "claude-rco-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
     "head": "1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c",
     "source_refs": [
      "8303eb5a7eb4556dcc42ac038f3bf13663e50d42cd607224aa280e959f3c89ea"
     ],
     "status": "rco_pass",
     "verification": "declared"
    },
    {
     "agent": "claude-rco-2",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c2",
     "head": "1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c",
     "source_refs": [
      "0b31c62369dde32aef0822301a81d22271bae1ce6d71e37380d521cbb2046fe5"
     ],
     "status": "rco_pass",
     "verification": "declared"
    },
    {
     "agent": "codex-tools-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
     "head": "1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c",
     "source_refs": [
      "4f32f2df251a63c06f10dc88af79c2e1b22a906dcfcdee0601daaa225aeb5f06"
     ],
     "status": "build_consensus_pass",
     "verification": "declared"
    }
   ]
  }
 },
 "schema": "wd.bridge-mind.v1",
 "scope": "bounded_projection_not_gate",
 "source": {
  "as_of_utc": "2026-10-13T03:55:00Z",
  "blocked": null,
  "coverage": "complete",
  "cursor_end": 1500,
  "cursor_start": 0,
  "duplicate_count": 0,
  "event_count": 33,
  "generation": "g1",
  "last_event_ref": "4f32f2df251a63c06f10dc88af79c2e1b22a906dcfcdee0601daaa225aeb5f06",
  "log_identity": "fixture-log",
  "max_event_utc": "2026-10-13T03:52:00Z",
  "unique_event_count": 33
 },
 "tensions": []
}
"""


def _core_twin():
    return json.loads(CORE_TWIN_JSON)


def _core_blocked_twin():
    """What the core's blocked_snapshot adds to the same state: source.blocked only, no reflection field."""
    twin = _core_twin()
    twin["source"]["blocked"] = {"active": True, "offset": 1500, "reason": "canonical_event_invalid"}
    return twin


def _sha256(value):
    return hashlib.sha256(mind.canonical_json(value).encode("utf-8")).hexdigest()


def test_an_actual_core_output_flows_through_the_workspace():
    """The positive twin: the recorded core bytes are intact, and reflect into the expected attention and questions."""
    twin = _core_twin()
    assert _sha256(twin) == CORE_TWIN_SHA256
    refs = {event["ts_utc"][11:19]: event_id(event) for event in CORE_TWIN_EVENTS}
    assert twin["prs"]["1899"]["reported_ci"]["head"] == H_NEW and twin["prs"]["1899"]["stale_votes"][0]["head"] == H_OLD
    # The core's own facts (pr and lane observations) are carried, not refused and not derived from.
    assert {fact["kind"] for fact in twin["facts"]} == {"lane_observation", "pr_observation"}
    # The core's d47f3ef9 shapes the workspace reads: an undeclared pass is unverified, a withdrawn pass is stale at
    # the same head, a non-RCO type=blocked is a veto observation, and releasing one task claim of a lane keeps
    # the other.
    assert ("claude-rco-2", "unverified") in [(vote["agent"], vote["verification"])
                                              for vote in twin["prs"]["1896"]["votes"]]
    assert [vote["head"] for vote in twin["prs"]["1895"]["stale_votes"]] == [H_1895]
    assert [(veto["agent"], veto["status"]) for veto in twin["prs"]["1890"]["veto_observations"]] == [
        ("claude-rco-1", "changes_requested"), ("codex-tools-1", "blocked")]
    lead = twin["lanes"]["codex-lead-1|" + _event("00:00:00", "codex-lead-1", "", "", "", {})["agent_uuid"]]
    assert [row["task_id"] for row in lead["claims"]["by_task"]] == ["codex-lead-1/plan-notes"]
    reflected = mind.reflect(twin)
    by_subject = {tension["subject"]: tension for tension in reflected["tensions"]}
    uuid = {agent: _event("00:00:00", agent, "", "", "", {})["agent_uuid"] for agent in _TWIN_UUID}
    overlap_new = f"claims:claude-rco-1|{uuid['claude-rco-1']}|codex-lead-1|{uuid['codex-lead-1']}"
    overlap_old = f"claims:claude-rco-2|{uuid['claude-rco-2']}|fable-5|{uuid['fable-5']}"
    expired = f"claim:codex-tools-1|{uuid['codex-tools-1']}:codex-tools-1/mind-20261013"
    veto_1895, veto_1890 = f"pr:1895:veto:claude-rco-2:{H_1895[:12]}", f"pr:1890:veto:claude-rco-1:{H_1890[:12]}"
    blocked_1890 = f"pr:1890:veto:codex-tools-1:{H_1890[:12]}"
    stale_1899, stale_1895 = f"pr:1899:claude-rco-1:{H_OLD[:12]}", f"pr:1895:claude-rco-2:{H_1895[:12]}"
    assert sorted(by_subject) == sorted([overlap_new, overlap_old, expired, veto_1895, veto_1890, blocked_1890,
                                         stale_1899, stale_1895])
    named = {"tension:" + tension["tension_id"]: subject for subject, tension in by_subject.items()}
    attention = [(item["kind"], named.get(item["subject"], item["subject"]), item["actors"])
                 for item in reflected["attention"]]
    assert attention[:3] == [("last_missing_vote", "pr:1896", ["claude-rco-2"]),
                             ("last_missing_vote", "pr:1899", ["codex-lead-1"]),
                             ("signature_only", "pr:1893", ["operator"])]
    assert sorted(attention[3:8]) == sorted([("tension_high", overlap_new, ["claude-rco-1", "codex-lead-1"]),
                                             ("tension_high", overlap_old, ["claude-rco-2", "fable-5"]),
                                             ("tension_high", veto_1895, ["claude-rco-2"]),
                                             ("tension_high", veto_1890, ["claude-rco-1"]),
                                             ("tension_high", blocked_1890, ["codex-tools-1"])])
    assert sorted(attention[8:]) == sorted([("tension", stale_1899, ["claude-rco-1"]),
                                            ("tension", stale_1895, ["claude-rco-2"]),
                                            ("tension", expired, ["codex-tools-1"])])
    assert by_subject[overlap_new]["observation"] == "overlapping paths: docs/my notes ~ docs/my notes/plan.md, " \
                                                     "resource:grok-lock"
    assert by_subject[overlap_new]["refs"] == sorted([refs["03:31:00"], refs["03:32:00"]])
    assert by_subject[overlap_old]["refs"] == sorted([refs["03:40:00"], refs["03:41:00"]])
    assert by_subject[stale_1895]["observation"] == "the core retired it at the PR head; a stale vote cannot approve it"
    assert by_subject[veto_1890]["refs"] == sorted({refs["03:06:00"]} | set(twin["prs"]["1890"]["source_refs"]))
    assert by_subject[blocked_1890]["refs"] == sorted({refs["03:08:00"]} | set(twin["prs"]["1890"]["source_refs"]))
    assert by_subject[blocked_1890]["observation"] == "the veto is observed; whether it is still active is UNKNOWN, " \
                                                      "never cleared here"
    assert by_subject[stale_1899]["refs"] == sorted({refs["03:35:00"]} | set(twin["prs"]["1899"]["source_refs"]))
    assert by_subject[expired]["refs"] == [refs["03:20:00"]]
    assert reflected["attention"][1]["refs"] == sorted(set(twin["prs"]["1899"]["source_refs"]) | {
        refs["03:45:00"], refs["03:50:00"], refs["03:51:00"], refs["03:52:00"]})
    question = reflected["next_question"]
    assert question["global"]["question"] == ("PR1896 at 5a5a5a5a5a5a is reported CI-green; missing: claude-rco-2. "
                                              "Can claude-rco-2 give it at this exact head?")
    assert sorted(question["lanes"]) == ["claude-rco-1", "claude-rco-2", "codex-lead-1", "codex-tools-1", "fable-5",
                                         "operator"]
    assert question["lanes"]["operator"]["attention_rank"] == 3
    # The core's own fields are unchanged, including its per-lane next_question placeholder.
    assert {key: value for key, value in reflected.items() if key not in ("tensions", "attention", "next_question")} \
        == {key: value for key, value in twin.items() if key not in ("tensions", "attention", "next_question")}


def test_an_actual_core_blocked_view_makes_no_head_bound_suggestion():
    """P6c on the core's own blocked view: no PR suggestion; input_blocked comes from source.blocked alone."""
    blocked = _core_blocked_twin()
    assert _sha256(blocked) == CORE_BLOCKED_SHA256
    assert (blocked["tensions"], blocked["attention"]) == ([], [])
    reflected = mind.reflect(blocked)
    assert [item["kind"] for item in reflected["attention"]] == ["tension_high"] * 6 + ["tension"] * 3
    rules = _rules(reflected)
    assert sorted(rules) == ["claim_overlap", "expired_claim", "input_blocked", "stale_vote", "veto_observed"]
    assert rules["input_blocked"]["subject"] == "offset:1500" and "canonical_event_invalid" in rules["input_blocked"][
        "observation"]
    assert rules["input_blocked"]["refs"] == [blocked["source"]["last_event_ref"]]
    assert reflected["next_question"]["global"]["question"].startswith("How can")
    assert "exact head" not in mind.canonical_json(reflected["next_question"])
    assert reflected["source"] == blocked["source"]


def test_the_fixture_builders_have_the_exact_core_shape():
    """The synthetic fixtures above are the core's shape, field for field, not an unrelated format."""
    twin = _core_twin()
    synthetic = _snapshot(prs=[_pr(10, votes=["codex-lead-1"], stale=[("claude-rco-2", OLD)], vetoes=["claude-rco-1"])],
                          lanes=[_lane("fable-5", _claim(["a.py"], AS_OF, "x"))])
    assert set(synthetic) == set(twin) and set(synthetic["source"]) == set(twin["source"])
    assert synthetic["next_question"] == twin["next_question"]
    pr, twin_pr = synthetic["prs"]["10"], twin["prs"]["1899"]
    assert set(pr) == set(twin_pr) and set(pr["reported_ci"]) == set(twin_pr["reported_ci"])
    assert set(pr["votes"][0]) == set(twin_pr["votes"][0]) == set(pr["stale_votes"][0])
    assert set(pr["veto_observations"][0]) == set(twin["prs"]["1890"]["veto_observations"][0])
    lane, twin_lane = next(iter(synthetic["lanes"].values())), twin["lanes"]["fable-5|" + _event(
        "00:00:00", "fable-5", "", "", "", {})["agent_uuid"]]
    assert set(lane) == set(twin_lane) and set(lane["claims"]) == set(twin_lane["claims"])
    assert set(lane["claims"]["by_task"][0]) == set(twin_lane["claims"]["by_task"][0])
    assert set(_command("r", AS_OF, [])) == set(twin["facts"][0])


def test_the_twin_is_what_the_core_emits_once_the_core_is_on_this_tree():
    core_path = Path(mind.__file__).resolve().parent / "wd_bridge_mind.py"
    if not core_path.exists():  # the core lands in its own PR; until then the recorded hashes bind the twins
        assert (_sha256(_core_twin()), _sha256(_core_blocked_twin())) == (CORE_TWIN_SHA256, CORE_BLOCKED_SHA256)
        return
    from tools import wd_bridge_mind as core

    envelopes = [{"ref": event_id(event), "event": event} for event in CORE_TWIN_EVENTS]
    state = core.full_rebuild(envelopes, source=CORE_TWIN_SOURCE, as_of_utc=CORE_TWIN_AS_OF)
    assert core.snapshot(state, as_of_utc=CORE_TWIN_AS_OF) == _core_twin()
    assert core.blocked_snapshot(state, as_of_utc=CORE_TWIN_AS_OF, offset=1500,
                                 reason="canonical_event_invalid") == _core_blocked_twin()


# G-K opt-in consistency, NOT origin authentication. These use only public synthetic values.
GK_ROLE_SLOT = {"codex-lead-1": ("lead-impl", "build_lead"),
                "codex-tools-1": ("tools-tests", "build_tools"),
                "claude-rco-1": ("rco-security", "claude-rco-1"),
                "claude-rco-2": ("rco-security", "claude-rco-2")}


def _gk_context(**changes):
    value = {"schema": "wd.bridge-mind.identity-context.v1", "coverage": "complete",
             "source_ref": ref("synthetic-registry-projection"),
             "identities": [{"agent": agent, "agent_uuid": UUIDS[agent], "role": role, "slot": slot}
                            for agent, (role, slot) in sorted(GK_ROLE_SLOT.items())]}
    value.update(changes)
    value["source_sha256"] = hashlib.sha256(mind.canonical_json(value).encode("utf-8")).hexdigest()
    return value


def _gk_snapshot():
    return _snapshot(prs=[_pr(17, klass="a", votes=EVERY)])


def _gk_pr_items(value):
    return [item for item in value["attention"] if item["subject"] == "pr:17"]


def test_gk00_matching_positive_twin_and_gk09_no_origin_authentication():
    """GK00 RejectMatchingPositiveTwin; GK09 RegistryMatchAuthenticatesOrigin."""
    original = _gk_snapshot()
    context = _gk_context()
    frozen = copy.deepcopy((original, context))
    out = mind.reflect(original, identity_context=context)
    item, = _gk_pr_items(out)
    assert item["kind"] == "signature_only"
    assert item["identity_basis"] == {
        "mode": "caller_supplied", "consistency": "registry_consistent_declarations",
        "source_ref": context["source_ref"], "source_sha256": context["source_sha256"],
        "origin": "origin_not_authenticated", "gate_eligibility": "UNKNOWN"}
    assert context["source_ref"] in item["refs"]
    assert out["authority"] == "none" and out["scope"] == "bounded_projection_not_gate"
    assert out["prs"] == original["prs"]  # raw UUIDs, verification and event refs are unchanged
    assert (original, context) == frozen


@pytest.mark.parametrize("mutant", ["GK01", "GK02", "GK03", "GK04", "GK06", "GK07", "GK10", "GK12"])
def test_gk_mismatched_tuple_does_not_fill_the_fourth_slot(mutant):
    """Named mutants match unchanged4D2590E8: syntax, borrowed UUID/role, stale, malformed, unknown label, bad4th."""
    snap, context = _gk_snapshot(), _gk_context()
    target = "claude-rco-1" if mutant == "GK03" else "codex-tools-1"
    vote = next(v for v in snap["prs"]["17"]["votes"] if v["agent"] == target)
    if mutant == "GK01":
        vote["agent_uuid"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    elif mutant in {"GK02", "GK12"}:
        vote["agent_uuid"] = UUIDS["claude-rco-1"]
    elif mutant == "GK03":
        vote["agent_uuid"] = UUIDS["claude-rco-2"]
    elif mutant == "GK04":
        vote["status"] = "rco_pass"
    elif mutant == "GK06":
        vote["head"] = OLD
    elif mutant == "GK07":
        vote["agent_uuid"] = "not-a-uuid"
    elif mutant == "GK10":
        vote["agent"] = "unknown-lane"
    original = copy.deepcopy(snap)
    out = mind.reflect(snap, identity_context=context)
    assert not any(item["kind"] == "signature_only" for item in _gk_pr_items(out))
    missing, = _gk_pr_items(out)
    assert missing["kind"] == "last_missing_vote" and missing["actors"] == [target]
    assert out["prs"] == original["prs"]  # no re-keying, dropping or rewriting raw observations
    if mutant == "GK06":
        assert "stale_vote" in _rules(out)
    if mutant == "GK07":
        assert "refused_input" in _rules(out)


@pytest.mark.parametrize("context_kind", [
    pytest.param("missing", id="GK05_MissingRegistryIsComplete"),
    pytest.param("partial", id="GK08_PartialRegistryAsComplete"),
    pytest.param("hash_mismatch", id="MUTANT_CONTEXT_DIGEST_MISMATCH"),
    pytest.param("duplicate", id="MUTANT_CONTEXT_DUPLICATE_LABEL"),
    pytest.param("role_mismatch", id="MUTANT_CONTEXT_ROLE_SLOT_MISMATCH"),
])
def test_gk05_gk08_missing_partial_or_ambiguous_context_stays_unknown(context_kind):
    """GK05 MissingRegistryIsComplete; GK08 PartialRegistryAsComplete; no digest-as-auth promotion."""
    context = _gk_context()
    if context_kind == "missing":
        context = None
    elif context_kind == "partial":
        context = _gk_context(coverage="partial", identities=context["identities"][:-1])
    elif context_kind == "hash_mismatch":
        context["source_sha256"] = "0" * 64
    elif context_kind == "duplicate":
        context = _gk_context(identities=context["identities"][:-1] + context["identities"][:1])
    elif context_kind == "role_mismatch":
        rows = copy.deepcopy(context["identities"])
        rows[0]["role"] = "tools-tests"
        context = _gk_context(identities=rows)
    item, = _gk_pr_items(mind.reflect(_gk_snapshot(), identity_context=context))
    assert item["kind"] == "votes_missing"
    assert item["actors"] == sorted(EVERY)
    assert item["identity_basis"]["consistency"] == "UNKNOWN"
    assert item["identity_basis"]["origin"] == "origin_not_authenticated"
    assert item["identity_basis"]["gate_eligibility"] == "UNKNOWN"


def test_gk11_duplicate_and_order_do_not_change_slots_or_raw_refs():
    """GK11 DuplicateOrOrderChangesSlots: vote order/duplicates change neither reflected fields nor slot count."""
    snap = _gk_snapshot()
    other = copy.deepcopy(snap)
    other["prs"]["17"]["votes"] = list(reversed(other["prs"]["17"]["votes"])) * 2
    context = _gk_context()
    assert mind.reflection(snap, identity_context=context) == mind.reflection(other, identity_context=context)
    assert mind.reflect(other, identity_context=context)["prs"] == other["prs"]


@pytest.mark.parametrize("control", ["veto", "source_block"])
def test_gk13_identity_consistency_never_clears_veto_or_source_block(control):
    """GK13 IdentityFixClearsVeto: existing veto/source controls outrank positive matching context."""
    snap = _gk_snapshot()
    if control == "veto":
        snap["prs"]["17"]["veto_observations"] = [_veto("claude-rco-1", 17, HEAD)]
    else:
        snap["source"]["blocked"] = {"active": True, "reason": "invalid_json", "offset": 17}
    out = mind.reflect(snap, identity_context=_gk_context())
    assert _gk_pr_items(out) == []
    assert ("veto_observed" if control == "veto" else "input_blocked") in _rules(out)
    assert out["prs"] == snap["prs"]


def test_gk_legacy_is_declared_only_not_silently_registry_checked():
    """MUTANT_LEGACY_CALLER_SILENT_CONTEXT: no opt-in retains the exact legacy reflection shape."""
    item, = _gk_pr_items(mind.reflect(_gk_snapshot()))
    assert item["kind"] == "signature_only" and "identity_basis" not in item
    assert "reported vote" in item["detail"]  # never authenticated or gate-approved


def test_gk_context_does_not_invent_event_role_or_mutate_input():
    """MUTANT_CONTEXT_INVENTS_EVENT_ROLE: core votes contain no role; context validates only its own static map."""
    snap = _gk_snapshot()
    out = mind.reflect(snap, identity_context=_gk_context())
    assert all("role" not in vote for vote in out["prs"]["17"]["votes"])
    assert out["prs"] == snap["prs"]


def test_gk_opt_in_cli_serializes_basis_and_rejects_private_context(tmp_path, capsys):
    """MUTANT_CLI_IGNORES_CONTEXT / MUTANT_CONTEXT_BYPASSES_GUARD: emitted JSON, not only an internal helper."""
    snapshot_path, context_path = tmp_path / "snapshot.json", tmp_path / "context.json"
    snapshot_path.write_text(mind.canonical_json(_gk_snapshot()), encoding="utf-8")
    context_path.write_text(mind.canonical_json(_gk_context()), encoding="utf-8")
    assert mind.main(["--snapshot", str(snapshot_path), "--identity-context", str(context_path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    item, = _gk_pr_items(out)
    assert item["identity_basis"]["consistency"] == "registry_consistent_declarations"
    assert item["identity_basis"]["origin"] == "origin_not_authenticated"
    bad = _gk_context()
    bad["identities"][0]["role"] = GUARD_WORDS[0]
    context_path.write_text(mind.canonical_json(bad), encoding="utf-8")
    assert mind.main(["--snapshot", str(snapshot_path), "--identity-context", str(context_path), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and GUARD_WORDS[0] not in captured.err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["context.json", "snapshot.json"]
