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


def _claim(paths, expires, name):
    return {"write_scope": list(paths) if paths is not None else "UNKNOWN", "source_refs": [ref(f"claim:{name}")],
            "verification": "declared", "lease_expires_utc": expires,
            "expired": "UNKNOWN" if expires == "UNKNOWN" else _time(expires) <= _time(AS_OF)}


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
    assert rules["expired_claim"]["subject"] == "claim:codex-tools-1|" + UUIDS["codex-tools-1"]
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
    assert subjects == ["claim:fable-5|" + UUIDS["fable-5"], "request:grace-exact", "request:ttl-live"]


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
    undeclared = copy.deepcopy(pr)
    undeclared["votes"].append({**_vote("claude-rco-2", 10, HEAD), "agent_uuid": "UNKNOWN"})
    attention = mind.reflection(_snapshot(prs=[(key, undeclared)]))["attention"]
    assert [(item["kind"], item["actors"]) for item in attention] == [("last_missing_vote", ["claude-rco-2"])]
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
])
def test_a_claim_that_contradicts_itself_is_refused(change):
    claim = _claim(["a.py"], AS_OF, "self")
    change(claim)
    fields = mind.reflection(_snapshot(lanes=[_lane("fable-5", claim)]))
    assert [(tension["rule"], tension["subject"]) for tension in fields["tensions"]] == [("refused_input", "lanes[0].claims")]


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
    assert refused == ["facts[0]", "lanes[0].claims", "lanes[1].claims", "prs[0].votes[0]"]

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
# commit ac61455111bf07b3c05159ce16fde21ddf17c4e6 (blob da7543dfbc9d1341bced07681e69e93ae04863ab), from
# full_rebuild(CORE_TWIN_EVENTS as {ref: event_id(event), event}, source=CORE_TWIN_SOURCE, as_of_utc=CORE_TWIN_AS_OF)
# and snapshot(). The core ran from a git-archive copy of that commit; CORE_TWIN_SHA256 is the sha256 of its canonical
# bytes, and CORE_BLOCKED_SHA256 that of blocked_snapshot(state, offset=1500, reason="canonical_event_invalid") on the
# same state. Until the core lands on main the recorded bytes stand in for it; once it does, the twin test reruns it.
CORE_TWIN_SHA256 = "5c0f98288db4be9242c0a0d465d871bef3606a920e48f4d94635a1f0f18ea148"
CORE_BLOCKED_SHA256 = "d0c2c206ba91482053959cadeb2fb7837d2a0a1a20c0903df39928f763116d23"
CORE_TWIN_AS_OF = "2026-10-13T03:55:00Z"
CORE_TWIN_SOURCE = {"log_identity": "fixture-log", "generation": "g1", "cursor_start": 0, "cursor_end": 1500,
                    "coverage": "complete", "event_count": 17}
_TWIN_UUID = {"fable-5": "f5", "claude-rco-1": "c1", "claude-rco-2": "c2", "codex-lead-1": "a1", "codex-tools-1": "b1"}
H_OLD, H_NEW, H_1893, H_1890 = "0b" * 20, "1c" * 20, "2d" * 20, "3e" * 20


def _event(ts, agent, kind, status, task, payload):
    return {"ts_utc": f"2026-10-13T{ts}Z", "agent": agent, "agent_uuid": "00000000-0000-4000-8000-0000000000"
            + _TWIN_UUID[agent], "type": kind, "status": status, "task_id": task, "payload": payload}


CORE_TWIN_EVENTS = [
    _event("03:05:00", "codex-lead-1", "message", "ready", "codex-lead-1/train-1890",
           {"pr": 1890, "head": H_1890, "class": "b", "required_checks_green": True}),
    _event("03:06:00", "claude-rco-1", "finding", "changes_requested", "claude-rco-1/review-1890",
           {"pr": 1890, "head": H_1890}),
    _event("03:07:00", "claude-rco-2", "decision", "rco_pass", "claude-rco-2/review-1890", {"pr": 1890, "head": H_1890}),
    _event("03:10:00", "codex-lead-1", "message", "ready", "codex-lead-1/train-1893",
           {"pr": 1893, "head": H_1893, "class": "a", "required_checks_green": True}),
    _event("03:12:00", "codex-lead-1", "decision", "build_consensus_pass", "codex-lead-1/train-1893",
           {"pr": 1893, "head": H_1893}),
    _event("03:13:00", "codex-tools-1", "decision", "build_consensus_pass", "codex-tools-1/train-1893",
           {"pr": 1893, "head": H_1893}),
    _event("03:14:00", "claude-rco-1", "decision", "rco_pass", "claude-rco-1/review-1893", {"pr": 1893, "head": H_1893}),
    _event("03:15:00", "claude-rco-2", "decision", "rco_pass", "claude-rco-2/review-1893", {"pr": 1893, "head": H_1893}),
    _event("03:20:00", "codex-tools-1", "claim", "active", "codex-tools-1/mind-20261013",
           {"write_scope": ["tools/wd_bridge_mind.py", "tests/tools/test_wd_bridge_mind.py"],
            "claim_lease_expires_utc": "2026-10-13T03:50:00Z"}),
    _event("03:30:00", "fable-5", "message", "review_requested", "fable-5/bridge-mind-workspace-20261013",
           {"pr": 1899, "head": H_OLD, "class": "b"}),
    _event("03:35:00", "claude-rco-1", "decision", "rco_pass", "claude-rco-1/review-1899", {"pr": 1899, "head": H_OLD}),
    _event("03:40:00", "fable-5", "claim", "active", "fable-5/bridge-mind-workspace-20261013",
           {"write_scope": ["tools/bridge_mind_workspace.py", "tests/tools/test_bridge_mind_workspace.py"],
            "claim_lease_expires_utc": "2026-10-13T04:30:00Z"}),
    _event("03:41:00", "claude-rco-2", "claim", "active", "claude-rco-2/mind-probes",
           {"write_scope": ["tests/tools/test_bridge_mind_workspace.py"], "claim_lease_expires_utc": "2026-10-13T04:20:00Z"}),
    _event("03:45:00", "fable-5", "message", "head_updated", "fable-5/bridge-mind-workspace-20261013",
           {"pr": 1899, "head": H_NEW, "required_checks_green": True}),
    _event("03:50:00", "claude-rco-2", "decision", "rco_pass", "claude-rco-2/review-1899", {"pr": 1899, "head": H_NEW}),
    _event("03:51:00", "claude-rco-1", "decision", "rco_pass", "claude-rco-1/review-1899", {"pr": 1899, "head": H_NEW}),
    _event("03:52:00", "codex-tools-1", "decision", "build_consensus_pass", "codex-tools-1/review-1899",
           {"pr": 1899, "head": H_NEW}),
]
CORE_TWIN_JSON = r"""
{
 "attention": [],
 "authority": "none",
 "facts": [
  {
   "canonical_sha256": "85d2787d6e32271a3bb31c034fafc9db9da45ace1bca9e0b263ac1c323f7e3ef",
   "fact_id": "fbe79c19ff4c7450d31ab97a89e7c565c64aef9c6762f636450d2fca9ce29f9e",
   "kind": "pr_observation",
   "observed_at_utc": "2026-10-13T03:52:00Z",
   "salience": 80,
   "source_refs": [
    "342cab9f3d31b20a8af35d30bf08ac99c6828e4870a375f0f9136aad23180543",
    "85d2787d6e32271a3bb31c034fafc9db9da45ace1bca9e0b263ac1c323f7e3ef",
    "94731d6594ab48e4698a56fe9a79bcf4135a014b79bd2061f6495d7167ab1a69"
   ],
   "ttl_seconds": 300,
   "value": {
    "head": "1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c",
    "pr": 1899
   },
   "verification": "declared"
  },
  {
   "canonical_sha256": "791e590287062c286739e58a06050e376f9f080b18d2cede25cd324fd4ebab43",
   "fact_id": "f66bf932b5e74ef1067a8d5bafcd5aa9843bcaaefc0da6b4108cfadd2af29ee0",
   "kind": "lane_observation",
   "observed_at_utc": "2026-10-13T03:51:00Z",
   "salience": 50,
   "source_refs": [
    "791e590287062c286739e58a06050e376f9f080b18d2cede25cd324fd4ebab43"
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
   "canonical_sha256": "85d2787d6e32271a3bb31c034fafc9db9da45ace1bca9e0b263ac1c323f7e3ef",
   "fact_id": "9cdb159f675a8fd307d4e28e1b62b17ae6dc44477b25821d5831daaae648f98e",
   "kind": "lane_observation",
   "observed_at_utc": "2026-10-13T03:52:00Z",
   "salience": 50,
   "source_refs": [
    "85d2787d6e32271a3bb31c034fafc9db9da45ace1bca9e0b263ac1c323f7e3ef"
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
   "claims": "UNKNOWN",
   "current_task": "claude-rco-1/review-1899",
   "idle_status": "UNKNOWN",
   "last_event_ref": "791e590287062c286739e58a06050e376f9f080b18d2cede25cd324fd4ebab43",
   "last_event_utc": "2026-10-13T03:51:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 240.0,
   "run_id": "UNKNOWN",
   "session_id": "UNKNOWN",
   "source_refs": [
    "791e590287062c286739e58a06050e376f9f080b18d2cede25cd324fd4ebab43"
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
    "expired": false,
    "lease_expires_utc": "2026-10-13T04:20:00Z",
    "source_refs": [
     "e871ab9152092e382cc07582f4816e81e48e822e8ff88e84b2ab0cc9d13bc7f2"
    ],
    "verification": "declared",
    "write_scope": [
     "tests/tools/test_bridge_mind_workspace.py"
    ]
   },
   "current_task": "claude-rco-2/review-1899",
   "idle_status": "UNKNOWN",
   "last_event_ref": "b85f650f597bd9e25ce43a32d4f68bcddf947598a3653c438daa0ff608518b20",
   "last_event_utc": "2026-10-13T03:50:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 300.0,
   "run_id": "UNKNOWN",
   "session_id": "UNKNOWN",
   "source_refs": [
    "b85f650f597bd9e25ce43a32d4f68bcddf947598a3653c438daa0ff608518b20",
    "e871ab9152092e382cc07582f4816e81e48e822e8ff88e84b2ab0cc9d13bc7f2"
   ],
   "uncertainty": [
    "origin_not_authenticated",
    "native_readiness_UNKNOWN"
   ],
   "verification": "declared",
   "waits": "UNKNOWN"
  },
  "codex-lead-1|00000000-0000-4000-8000-0000000000a1": {
   "agent": "codex-lead-1",
   "agent_uuid": "00000000-0000-4000-8000-0000000000a1",
   "claims": "UNKNOWN",
   "current_task": "codex-lead-1/train-1893",
   "idle_status": "UNKNOWN",
   "last_event_ref": "f5d3b310396af8135c341735ecbb6a3409a1db68ce36da0e5af7c16372333115",
   "last_event_utc": "2026-10-13T03:12:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 2580.0,
   "run_id": "UNKNOWN",
   "session_id": "UNKNOWN",
   "source_refs": [
    "f5d3b310396af8135c341735ecbb6a3409a1db68ce36da0e5af7c16372333115"
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
    "expired": true,
    "lease_expires_utc": "2026-10-13T03:50:00Z",
    "source_refs": [
     "cfbd376f6cbe4fb3b9556ad42b75d7c025836ca9c919eac8f2536a9320b53e93"
    ],
    "verification": "declared",
    "write_scope": [
     "tools/wd_bridge_mind.py",
     "tests/tools/test_wd_bridge_mind.py"
    ]
   },
   "current_task": "codex-tools-1/review-1899",
   "idle_status": "UNKNOWN",
   "last_event_ref": "85d2787d6e32271a3bb31c034fafc9db9da45ace1bca9e0b263ac1c323f7e3ef",
   "last_event_utc": "2026-10-13T03:52:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 180.0,
   "run_id": "UNKNOWN",
   "session_id": "UNKNOWN",
   "source_refs": [
    "85d2787d6e32271a3bb31c034fafc9db9da45ace1bca9e0b263ac1c323f7e3ef",
    "cfbd376f6cbe4fb3b9556ad42b75d7c025836ca9c919eac8f2536a9320b53e93"
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
    "expired": false,
    "lease_expires_utc": "2026-10-13T04:30:00Z",
    "source_refs": [
     "0a5764b2c622f14057c2ec1b7aab3ca62c735b25f1591b416e52d204f8f82225"
    ],
    "verification": "declared",
    "write_scope": [
     "tools/bridge_mind_workspace.py",
     "tests/tools/test_bridge_mind_workspace.py"
    ]
   },
   "current_task": "fable-5/bridge-mind-workspace-20261013",
   "idle_status": "UNKNOWN",
   "last_event_ref": "94731d6594ab48e4698a56fe9a79bcf4135a014b79bd2061f6495d7167ab1a69",
   "last_event_utc": "2026-10-13T03:45:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 600.0,
   "run_id": "UNKNOWN",
   "session_id": "UNKNOWN",
   "source_refs": [
    "0a5764b2c622f14057c2ec1b7aab3ca62c735b25f1591b416e52d204f8f82225",
    "94731d6594ab48e4698a56fe9a79bcf4135a014b79bd2061f6495d7167ab1a69"
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
     "bfe2f194c57cc1d56db69aa9e65df7090265f9bec63aa8e22d72c63acb8618b0"
    ],
    "status": "reported_green",
    "verification": "declared"
   },
   "source_refs": [
    "aae0cc7ff0f8fdb9d91354ae7f05818346939dec6c2c51b6fa3617ed5f7393ea",
    "bfe2f194c57cc1d56db69aa9e65df7090265f9bec63aa8e22d72c63acb8618b0"
   ],
   "stale_votes": [],
   "veto_observations": [
    {
     "activity": "UNKNOWN",
     "agent": "claude-rco-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
     "head": "3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e",
     "source_refs": [
      "b957df27efe8dd10fd68864b272376a44959784bead746822262133dd6bc3a5d"
     ],
     "status": "changes_requested",
     "verification": "declared"
    }
   ],
   "votes": [
    {
     "agent": "claude-rco-2",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c2",
     "head": "3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e",
     "source_refs": [
      "aae0cc7ff0f8fdb9d91354ae7f05818346939dec6c2c51b6fa3617ed5f7393ea"
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
     "a1a9983c919085fb37b82c78e452c6d30611b748daf5b23700a860d1c16404cc"
    ],
    "status": "reported_green",
    "verification": "declared"
   },
   "source_refs": [
    "45e84bfa0345a316612ae19ad77b8f3b93ddb98ad22941b3f4f5cfcfc9349dd9",
    "a1a9983c919085fb37b82c78e452c6d30611b748daf5b23700a860d1c16404cc"
   ],
   "stale_votes": [],
   "veto_observations": [],
   "votes": [
    {
     "agent": "claude-rco-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
     "head": "2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d",
     "source_refs": [
      "518868632f7c3d30d4394b37d8c343aeb1d61fe097b5d1e6087448901ba37e0d"
     ],
     "status": "rco_pass",
     "verification": "declared"
    },
    {
     "agent": "claude-rco-2",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c2",
     "head": "2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d",
     "source_refs": [
      "45e84bfa0345a316612ae19ad77b8f3b93ddb98ad22941b3f4f5cfcfc9349dd9"
     ],
     "status": "rco_pass",
     "verification": "declared"
    },
    {
     "agent": "codex-lead-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000a1",
     "head": "2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d",
     "source_refs": [
      "f5d3b310396af8135c341735ecbb6a3409a1db68ce36da0e5af7c16372333115"
     ],
     "status": "build_consensus_pass",
     "verification": "declared"
    },
    {
     "agent": "codex-tools-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
     "head": "2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d",
     "source_refs": [
      "a763f6f1d8f691c9e3c603a1b6e7b1e901a823aed729e82c7a6c84bea06c048f"
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
     "94731d6594ab48e4698a56fe9a79bcf4135a014b79bd2061f6495d7167ab1a69"
    ],
    "status": "reported_green",
    "verification": "declared"
   },
   "source_refs": [
    "342cab9f3d31b20a8af35d30bf08ac99c6828e4870a375f0f9136aad23180543",
    "85d2787d6e32271a3bb31c034fafc9db9da45ace1bca9e0b263ac1c323f7e3ef",
    "94731d6594ab48e4698a56fe9a79bcf4135a014b79bd2061f6495d7167ab1a69"
   ],
   "stale_votes": [
    {
     "agent": "claude-rco-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
     "head": "0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b",
     "source_refs": [
      "3774d7c27a790ce591f2b4fb1393987d8af0328b435ae52c4fdd90e6f86aa720"
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
      "791e590287062c286739e58a06050e376f9f080b18d2cede25cd324fd4ebab43"
     ],
     "status": "rco_pass",
     "verification": "declared"
    },
    {
     "agent": "claude-rco-2",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c2",
     "head": "1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c",
     "source_refs": [
      "b85f650f597bd9e25ce43a32d4f68bcddf947598a3653c438daa0ff608518b20"
     ],
     "status": "rco_pass",
     "verification": "declared"
    },
    {
     "agent": "codex-tools-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
     "head": "1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c",
     "source_refs": [
      "85d2787d6e32271a3bb31c034fafc9db9da45ace1bca9e0b263ac1c323f7e3ef"
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
  "event_count": 17,
  "generation": "g1",
  "last_event_ref": "85d2787d6e32271a3bb31c034fafc9db9da45ace1bca9e0b263ac1c323f7e3ef",
  "log_identity": "fixture-log",
  "max_event_utc": "2026-10-13T03:52:00Z",
  "unique_event_count": 17
 },
 "tensions": []
}
"""


def _core_twin():
    return json.loads(CORE_TWIN_JSON)


def _core_blocked_twin():
    """What the core's blocked_snapshot adds to the same state: source.blocked and one core-shaped tension."""
    twin = _core_twin()
    twin["source"]["blocked"] = {"active": True, "offset": 1500, "reason": "canonical_event_invalid"}
    twin["tensions"] = [{"rule_id": "source_blocked", "offset": 1500, "reason": "canonical_event_invalid",
                         "refs": [twin["source"]["last_event_ref"]]}]
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
    reflected = mind.reflect(twin)
    attention = [(item["kind"], item["subject"], item["actors"]) for item in reflected["attention"]]
    by_rule = {tension["rule"]: tension for tension in reflected["tensions"]}
    assert attention == [
        ("last_missing_vote", "pr:1899", ["codex-lead-1"]),
        ("signature_only", "pr:1893", ["operator"]),
        ("tension_high", "tension:" + by_rule["claim_overlap"]["tension_id"], ["claude-rco-2", "fable-5"]),
        ("tension_high", "tension:" + by_rule["veto_observed"]["tension_id"], ["claude-rco-1"]),
        ("tension", "tension:" + by_rule["stale_vote"]["tension_id"], ["claude-rco-1"]),
        ("tension", "tension:" + by_rule["expired_claim"]["tension_id"], ["codex-tools-1"]),
    ]
    assert sorted(by_rule) == ["claim_overlap", "expired_claim", "stale_vote", "veto_observed"]
    assert by_rule["veto_observed"]["refs"] == sorted({refs["03:06:00"]} | set(twin["prs"]["1890"]["source_refs"]))
    assert by_rule["stale_vote"]["refs"] == sorted({refs["03:35:00"]} | set(twin["prs"]["1899"]["source_refs"]))
    assert by_rule["expired_claim"]["refs"] == [refs["03:20:00"]]
    assert by_rule["claim_overlap"]["refs"] == sorted([refs["03:40:00"], refs["03:41:00"]])
    assert reflected["attention"][0]["refs"] == sorted(set(twin["prs"]["1899"]["source_refs"]) | {
        refs["03:45:00"], refs["03:50:00"], refs["03:51:00"], refs["03:52:00"]})
    question = reflected["next_question"]
    assert question["global"]["question"] == ("PR1899 at 1c1c1c1c1c1c is reported CI-green; missing: build_lead. "
                                              "Can codex-lead-1 give it at this exact head?")
    assert sorted(question["lanes"]) == ["claude-rco-1", "claude-rco-2", "codex-lead-1", "codex-tools-1", "fable-5",
                                         "operator"]
    assert question["lanes"]["operator"]["attention_rank"] == 2
    # The core's own fields are unchanged, including its per-lane next_question placeholder.
    assert {key: value for key, value in reflected.items() if key not in ("tensions", "attention", "next_question")} \
        == {key: value for key, value in twin.items() if key not in ("tensions", "attention", "next_question")}


def test_an_actual_core_blocked_view_makes_no_head_bound_suggestion():
    """P6c on the core's own blocked view: the core tension becomes input_blocked with the same offset, reason and ref."""
    blocked = _core_blocked_twin()
    assert _sha256(blocked) == CORE_BLOCKED_SHA256
    reflected = mind.reflect(blocked)
    assert [item["kind"] for item in reflected["attention"]] == ["tension_high"] * 3 + ["tension"] * 2
    rules = _rules(reflected)
    assert sorted(rules) == ["claim_overlap", "expired_claim", "input_blocked", "stale_vote", "veto_observed"]
    assert rules["input_blocked"]["subject"] == "offset:1500" and "canonical_event_invalid" in rules["input_blocked"][
        "observation"]
    assert rules["input_blocked"]["refs"] == blocked["tensions"][0]["refs"] == [blocked["source"]["last_event_ref"]]
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
